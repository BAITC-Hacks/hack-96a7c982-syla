"""Validate CSV datasets and hold local dashboard uploads in bounded memory."""

from collections import OrderedDict
import csv
from dataclasses import dataclass
import io
from pathlib import Path
import re
from threading import Lock
from uuid import uuid4

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_UPLOAD_BYTES = 35 * 1024 * 1024
SCHEMAS = {
    "profile": {
        "label": "База абонентов", "filename": "customer_profile.csv", "max_rows": 100_000,
        "columns": ["ID_NUMBER", "current_tariff", "predicted_arpu", "arpu_segment",
                    "DATA_VOLUME", "data_segment", "call_segment"],
        "numeric": ["ID_NUMBER", "predicted_arpu", "DATA_VOLUME"],
        "codes": ["current_tariff"],
    },
    "tariffs": {
        "label": "Справочник тарифов", "filename": "dict_tariff.csv", "max_rows": 100,
        "columns": ["tariff_plan_code", "price_tariff", "Data_in_PKG"],
        "numeric": ["price_tariff", "Data_in_PKG"], "codes": ["tariff_plan_code"],
    },
    "history": {
        "label": "История переходов", "filename": "change_tariff.csv", "max_rows": 100_000,
        "columns": ["ID_NUMBER", "tariff_plan_code_from", "tariff_plan_code_to",
                    "AVG_ARPU_PREV_3M", "AVG_ARPU_NEXT_3M"],
        "numeric": ["ID_NUMBER", "AVG_ARPU_PREV_3M", "AVG_ARPU_NEXT_3M"],
        "codes": ["tariff_plan_code_from", "tariff_plan_code_to"],
    },
}


class DatasetError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__("Исправьте ошибки в файлах и повторите проверку.")


@dataclass
class Dataset:
    profile: pd.DataFrame
    tariffs: pd.DataFrame
    history: pd.DataFrame
    metadata: dict


def validate_upload(payload):
    """Parse all three UTF-8 CSVs, returning no partial dataset on failure."""
    issues, frames, files, warnings = [], {}, [], []

    def issue(kind, message):
        issues.append({"file": kind, "message": message})

    if not isinstance(payload, dict):
        raise DatasetError([{"file": "all", "message": "Ожидаются три CSV-файла."}])
    total_bytes = sum(len(item.get("text", "").encode("utf-8")) for key, item in payload.items()
                      if key in SCHEMAS and isinstance(item, dict) and isinstance(item.get("text"), str))
    if total_bytes > 30 * 1024 * 1024:
        raise DatasetError([{"file": "all", "message": "Общий размер файлов превышает 30 МБ."}])
    for kind, spec in SCHEMAS.items():
        item = payload.get(kind)
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            issue(kind, "Выберите CSV-файл.")
            continue
        name = item.get("name", spec["filename"])
        if not isinstance(name, str) or not name.lower().endswith(".csv"):
            issue(kind, "Поддерживается только формат .csv.")
            continue
        content = item["text"].lstrip("\ufeff")
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            issue(kind, "Файл больше 15 МБ. Уменьшите его размер.")
            continue
        if not content.strip():
            issue(kind, "Файл пуст. Добавьте заголовки и строки данных.")
            continue
        if "\ufffd" in content or "\x00" in content:
            issue(kind, "Сохраните файл как CSV в кодировке UTF-8.")
            continue
        try:
            first_line = content.splitlines()[0]
            delimiter = ";" if first_line.count(";") > first_line.count(",") else ","
            headers = next(csv.reader(io.StringIO(content), delimiter=delimiter))
            headers = [header.strip() for header in headers]
            if len(headers) != len(set(headers)):
                issue(kind, "Названия колонок повторяются. Сделайте их уникальными.")
                continue
            missing = [column for column in spec["columns"] if column not in headers]
            if missing:
                issue(kind, "Не хватает колонок: " + ", ".join(missing) + ".")
                continue
            reader = csv.reader(io.StringIO(content), delimiter=delimiter)
            next(reader)
            malformed = next((line for line, row in enumerate(reader, 2)
                              if row and len(row) != len(headers)), None)
            if malformed:
                issue(kind, f"В строке {malformed} число полей не совпадает с заголовком. Проверьте разделители.")
                continue
            frame = pd.read_csv(io.StringIO(content), sep=delimiter, dtype=str,
                                nrows=spec["max_rows"] + 1, keep_default_na=False)
            frame.columns = headers
            frame = frame[spec["columns"]].copy()
        except (ValueError, pd.errors.ParserError, pd.errors.EmptyDataError, csv.Error):
            issue(kind, "Не удалось прочитать CSV. Проверьте разделители и кавычки.")
            continue
        if frame.empty or len(frame) > spec["max_rows"]:
            issue(kind, f"Нужно от 1 до {spec['max_rows']:,} строк данных.")
            continue
        for column in frame:
            frame[column] = frame[column].str.strip()
        original_rows = len(frame)
        if kind == "profile":
            incomplete = frame["current_tariff"].eq("") | frame["arpu_segment"].eq("")
            if incomplete.any():
                warnings.append(f"База: {int(incomplete.sum())} строк без тарифа или ARPU-сегмента исключены из планирования.")
                frame = frame.loc[~incomplete].copy()
            if frame.empty:
                issue(kind, "Нет абонентов с заполненными current_tariff и arpu_segment.")
                continue
        for column in spec["numeric"]:
            values = pd.to_numeric(frame[column], errors="coerce")
            allow_negative = column in {"AVG_ARPU_PREV_3M", "AVG_ARPU_NEXT_3M"}
            bad = ~np.isfinite(values)
            if not allow_negative:
                bad |= values < 0
            if column != "ID_NUMBER":
                bad |= values.abs() > 1e12
            if column == "DATA_VOLUME":
                missing_volume = frame[column].eq("")
                bad &= ~missing_volume
                if missing_volume.any():
                    warnings.append(f"У {int(missing_volume.sum())} абонентов нет DATA_VOLUME: доля пользователей интернета считается по заполненным значениям.")
            if column == "ID_NUMBER":
                bad |= (values % 1 != 0) | (values > 2 ** 53 - 1)
            if bad.any():
                rows = ", ".join(str(int(index) + 2) for index in frame.index[bad][:5])
                issue(kind, f"{column}: нужны конечные " + ("числа" if allow_negative else "неотрицательные числа")
                      + (" (целые для ID)" if column == "ID_NUMBER" else "")
                      + (" (не больше 2⁵³ − 1)" if column == "ID_NUMBER" else " (по модулю не больше 10¹²)")
                      + f". Проверьте строки: {rows}.")
            frame[column] = values
        for column in spec["codes"]:
            bad = ~frame[column].str.fullmatch(r"\w[\w .-]{0,79}")
            if bad.any():
                issue(kind, f"{column}: укажите коды тарифов из букв, цифр, пробелов, _, - или точки; до 80 символов.")
        frames[kind] = frame
        files.append({"kind": kind, "name": re.split(r"[/\\]", name)[-1][:160],
                      "rows": original_rows, "usable_rows": len(frame), "columns": len(headers)})

    if issues:
        raise DatasetError(issues)
    profile, tariffs, history = (frames[key] for key in ("profile", "tariffs", "history"))
    for kind, column in (("profile", "ID_NUMBER"), ("tariffs", "tariff_plan_code")):
        if frames[kind][column].duplicated().any():
            issue(kind, f"{column}: обнаружены повторы. Оставьте одну строку на каждый ID или тариф.")
    for column, allowed in {
        "arpu_segment": {"LOW", "MID", "HIGH"},
        "data_segment": {"NON_USER", "LITE", "HEAVY"},
        "call_segment": {"LOW", "MEDIUM", "HIGH"},
    }.items():
        if column == "data_segment":
            if profile[column].eq("").any():
                warnings.append("Пустые data_segment не участвуют в расчёте доли HEAVY.")
            allowed = allowed | {""}
        if not profile[column].isin(allowed).all():
            issue("profile", f"{column}: допустимы только {', '.join(sorted(allowed))}.")
    if profile["predicted_arpu"].sum() <= 0:
        issue("profile", "Сумма predicted_arpu должна быть больше нуля.")
    known = set(tariffs["tariff_plan_code"])
    if len(known) < 2:
        issue("tariffs", "Нужны как минимум два разных тарифа для выбора перехода.")
    for kind, column in (("profile", "current_tariff"), ("history", "tariff_plan_code_from"),
                         ("history", "tariff_plan_code_to")):
        if not frames[kind][column].isin(known).all():
            issue(kind, f"{column}: есть тарифы, которых нет в справочнике. Добавьте их в файл тарифов.")
    if not (history["AVG_ARPU_PREV_3M"] >= 100).any():
        issue("history", "Для расчёта нужна хотя бы одна запись с AVG_ARPU_PREV_3M ≥ 100.")
    if issues:
        raise DatasetError(issues)
    profile["ID_NUMBER"] = profile["ID_NUMBER"].astype("int64")
    history["ID_NUMBER"] = history["ID_NUMBER"].astype("int64")
    excluded = int((history["AVG_ARPU_PREV_3M"] < 100).sum())
    if excluded:
        warnings.append(f"В истории {excluded} строк с ARPU до перехода < 100: они не участвуют в оценке эффекта.")
    if profile.groupby(["current_tariff", "arpu_segment"]).size().max() < 150:
        warnings.append("Все группы меньше 150 человек: отдельные пилоты не проводятся, оценка опирается на историю.")
    metadata = {"id": "", "kind": "upload", "label": "Загруженные данные", "files": files,
                "customers": len(profile), "tariffs": len(tariffs), "transitions": len(history),
                "warnings": warnings}
    return Dataset(profile, tariffs, history, metadata)


class DatasetStore:
    def __init__(self, capacity=4):
        self.capacity = capacity
        self._items = OrderedDict()
        self._lock = Lock()

    def add(self, dataset):
        key = uuid4().hex
        dataset.metadata["id"] = key
        with self._lock:
            self._items[key] = dataset
            while len(self._items) > self.capacity:
                self._items.popitem(last=False)
        return dataset.metadata

    def get(self, key):
        with self._lock:
            if key not in self._items:
                raise KeyError("Данные больше не доступны. Загрузите файлы и проверьте их заново.")
            self._items.move_to_end(key)
            return self._items[key]


UPLOADS = DatasetStore()


def demo_dataset():
    profile = pd.read_csv(ROOT / "customer_profile.csv")
    tariffs = pd.read_csv(ROOT / "data" / "dict_tariff.csv")
    history = pd.read_csv(ROOT / "data" / "change_tariff.csv")
    metadata = {"id": "demo", "kind": "demo", "label": "Данные кейса Beeline",
                "customers": len(profile), "tariffs": len(tariffs), "transitions": len(history),
                "warnings": [], "files": []}
    return Dataset(profile, tariffs, history, metadata)
