"""Validated report adapter. Unknown values stay unknown; exports can be reloaded."""
from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path
from typing import Any

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
SOURCE_LABELS = {"demo": "Демонстрационные данные", "upload": "Загруженный отчёт", "local": "Локальная симуляция"}
CHANNEL_LABELS = {"sms": "SMS", "push": "Push", "digital_ads": "Цифровая реклама", "call": "Звонок"}


class ReportError(ValueError):
    """A recoverable report validation error, safe to show to the analyst."""


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
    number = _finite(value)
    return int(number) if number is not None and number >= 0 and number.is_integer() else None


def _nonnegative(value: Any) -> float | None:
    number = _finite(value)
    return number if number is not None and number >= 0 else None


def _text(value: Any, limit: int = 240) -> str | None:
    if value is None or isinstance(value, (dict, list, tuple, bool)):
        return None
    value = str(value).strip()
    return value[:limit] if value else None


def _pick(raw: dict, *keys: str) -> Any:
    return next((raw[k] for k in keys if raw.get(k) is not None), None)


def _interval(value: Any) -> list[float | None]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return [None, None]
    lo, hi = map(_finite, value)
    if lo is None or hi is None or lo > hi:
        return [None, None]
    return [lo, hi]


def _complete_sum(values: list) -> float | int | None:
    """A partial subtotal is not a total. An empty complete list sums to zero."""
    if any(v is None for v in values):
        return None
    total = sum(values)
    return total if _finite(total) is not None else None


def _campaign(raw: Any, index: int, detail: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    segment = raw.get("segment") if isinstance(raw.get("segment"), dict) else {}
    name = _text(_pick(raw, "campaign", "campaign_name", "name")) or f"campaign_{index + 1}"
    ci_input = _pick(raw, "confidence_interval_95", "ci95")
    ci = _interval(ci_input)
    warnings = raw.get("warnings", [])
    warnings = warnings if isinstance(warnings, list) else [warnings]
    warnings = [text for x in warnings if (text := _text(x))]
    if ci_input is not None and ci == [None, None]:
        warnings.append("Некорректный или неполный интервал: оценка уверенности недоступна.")
    std = _nonnegative(_pick(raw, "uncertainty", "posterior_std", "std"))
    contacts = _integer(_pick(raw, "planned_contacts", "n_contacts", "contacts"))
    if contacts is None:
        contacts = _integer(detail.get("n_contacts"))
    cost = _nonnegative(raw.get("cost"))
    if cost is None:
        cost = _nonnegative(detail.get("cost"))
    channel = _text(raw.get("channel"))
    if channel not in CHANNEL_LABELS:
        warnings.append("Канал не указан или отсутствует в справочнике кейса.")
    return {
        "id": _text(_pick(raw, "campaign_id", "id")) or f"{index}:{name}", "name": name,
        "current_tariff": _text(_pick(segment, "current_tariff") or _pick(raw, "filter_current_tariff", "current_tariff")),
        "arpu_segment": _text(_pick(segment, "arpu") or _pick(raw, "filter_arpu_segment", "arpu_segment")),
        "data_segment": _text(_pick(segment, "data") or _pick(raw, "filter_data_segment", "data_segment")),
        "call_segment": _text(_pick(segment, "calls") or _pick(raw, "filter_call_segment", "call_segment")),
        "target_tariff": _text(raw.get("target_tariff")), "channel": channel,
        "group_size": _integer(_pick(raw, "audience_size", "group_size", "estimated_group_size")),
        "contacts": contacts, "cost": cost,
        "expected_net_effect": _finite(raw.get("expected_net_effect")),
        "gross_lift": _finite(_pick(raw, "gross_lift") if raw.get("gross_lift") is not None else detail.get("gross_lift")),
        "posterior_lift": _finite(raw.get("posterior_lift")), "std": std, "ci95": ci,
        "risk_adjusted_lift": _finite(raw.get("risk_adjusted_lift")),
        "pilots_used": _integer(raw.get("pilots_used")), "pilot_customers": _integer(raw.get("pilot_customers")),
        "warnings": list(dict.fromkeys(warnings)),
    }


def normalize_report(raw: Any, source: str = "upload") -> dict:
    if not isinstance(raw, dict):
        raise ReportError("Корень JSON должен быть объектом.")
    # Support both raw PITCH_REPORT and this adapter's exported representation.
    score = raw.get("score") if isinstance(raw.get("score"), dict) else raw.get("metrics")
    if not isinstance(score, dict):
        score = raw
    exploration = raw.get("exploration") if isinstance(raw.get("exploration"), dict) else {}
    rows = raw.get("campaigns")
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise ReportError("Поле campaigns должно быть массивом.")
    if len(rows) > 5000:
        raise ReportError("Слишком много строк кампаний: максимум 5000 для просмотра.")
    details = score.get("campaigns_detail", raw.get("campaigns_detail", []))
    detail_map = {str(x.get("name")): x for x in details if isinstance(x, dict)} if isinstance(details, list) else {}
    campaigns = []
    for i, row in enumerate(rows):
        name = _pick(row, "campaign", "campaign_name", "name") if isinstance(row, dict) else None
        item = _campaign(row, i, detail_map.get(str(name), {}))
        if item is not None:
            campaigns.append(item)
    declared_source = _text(raw.get("source")) or source
    is_demo = source == "demo" or raw.get("is_demo") is True or declared_source == "demo"
    source_kind = "demo" if is_demo else source if source in SOURCE_LABELS else "upload"
    history = _pick(raw, "pilot_history", "pilots")
    if history is None:
        history = exploration.get("pilot_history")
    pilots = []
    if isinstance(history, list):
        for i, row in enumerate(history):
            if not isinstance(row, dict):
                continue
            segment = row.get("segment") if isinstance(row.get("segment"), dict) else {}
            pilots.append({
                "pilot": _text(row.get("pilot")) or f"pilot_{i+1}",
                "target_tariff": _text(row.get("target_tariff")),
                "segment": _text(segment.get("arpu") or _pick(row, "filter_arpu_segment", "segment_name", "segment")),
                "channel": _text(row.get("channel")), "contacts": _integer(_pick(row, "n_customers", "contacts")),
                "cost": _nonnegative(row.get("cost")), "observed_lift_ratio": _finite(row.get("observed_lift_ratio")),
                "observed_lift_total": _finite(row.get("observed_lift_total")), "status": _text(row.get("status")),
            })
    count = _integer(exploration.get("pilots_executed"))
    if count is None and isinstance(history, list):
        count = len(pilots)
    full_history = isinstance(history, list) and len(pilots) == len(history) and count == len(pilots)
    pilot_contacts = _integer(exploration.get("pilot_contacts"))
    pilot_cost = _nonnegative(exploration.get("pilot_cost"))
    if full_history:
        if pilot_contacts is None:
            pilot_contacts = _complete_sum([p["contacts"] for p in pilots])
        if pilot_cost is None:
            pilot_cost = _complete_sum([p["cost"] for p in pilots])
    cost = _nonnegative(_pick(score, "total_cost", "cost"))
    roi = _finite(score.get("roi")) if cost is not None and cost > 0 else None
    report = {
        "schema_version": _integer(raw.get("schema_version")) or 1,
        "source": "demo" if is_demo else declared_source,
        "source_kind": source_kind, "source_label": SOURCE_LABELS[source_kind],
        "seed": _integer(raw.get("seed")), "agent_commit": _text(raw.get("agent_commit")),
        "agent_sha256": _text(raw.get("agent_sha256")),
        "generated_at": _text(_pick(raw, "generated_at", "report_time")), "is_demo": is_demo,
        "metrics": {"net_lift": _finite(_pick(score, "net_arpu_gain", "net_lift")),
                    "gross_lift": _finite(_pick(score, "gross_arpu_lift", "gross_lift")),
                    "cost": cost, "roi": roi, "contacts": _integer(_pick(score, "total_contacts", "contacts")),
                    "campaigns": len(campaigns)},
        "campaigns": campaigns, "pilots": pilots,
        "exploration": {"pilots_executed": count, "pilot_contacts": pilot_contacts, "pilot_cost": pilot_cost},
    }
    report["warnings"] = report_warnings(report)
    if len(campaigns) != len(rows):
        report["warnings"].append("Некорректные строки кампаний пропущены; отчёт неполный.")
    return report


def channel_label(channel: str | None) -> str:
    return CHANNEL_LABELS.get(channel, channel or "Не указан")


def campaign_rows(report: dict, channels: list | None = None) -> list[dict]:
    allowed = set(channels) if channels is not None else None
    rows = [c for c in report.get("campaigns", []) if allowed is None or c.get("channel") in allowed]
    return sorted(rows, key=lambda c: (c.get("expected_net_effect") is None, -(c.get("expected_net_effect") or 0)))


def confidence_label(campaign: dict) -> str:
    lo, hi = _interval(campaign.get("ci95"))
    if lo is None:
        return "Нет данных"
    if hi < 0:
        return "Ниже нуля"
    if lo <= 0 <= hi:
        return "Включает ноль"
    return "Выше нуля"


def selected_campaign(rows: list[dict], selected_indices: list[int]) -> dict | None:
    if not rows:
        return None
    index = selected_indices[0] if selected_indices else 0
    return rows[index] if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(rows) else rows[0]


def spend_breakdown(report: dict) -> dict:
    campaigns = report.get("campaigns", [])
    e, m = report.get("exploration", {}), report.get("metrics", {})
    return {"final_cost": _complete_sum([c.get("cost") for c in campaigns]),
            "final_contacts": _complete_sum([c.get("contacts") for c in campaigns]),
            "pilot_cost": e.get("pilot_cost"), "pilot_contacts": e.get("pilot_contacts"),
            "total_cost": m.get("cost"), "total_contacts": m.get("contacts")}


def channel_totals(report: dict) -> dict:
    groups = {}
    for c in report.get("campaigns", []):
        groups.setdefault(c.get("channel"), []).append(c)
    return {key: {"cost": _complete_sum([c.get("cost") for c in rows]),
                  "contacts": _complete_sum([c.get("contacts") for c in rows])} for key, rows in groups.items()}


def report_warnings(report: dict) -> list[str]:
    m, e = report.get("metrics", {}), report.get("exploration", {})
    warnings = []
    for value, limit, text in [(m.get("cost"), 100000, "Расходы превышают 100 000 у.е."),
                               (m.get("contacts"), 15000, "Контакты превышают лимит 15 000."),
                               (m.get("campaigns"), 10, "Больше 10 финальных кампаний."),
                               (e.get("pilots_executed"), 20, "Превышен лимит 20 пилотов.")]:
        if value is not None and value > limit:
            warnings.append(text)
    if m.get("campaigns") == 0:
        warnings.append("Финальных кампаний нет: такой план не готов к официальной сдаче.")
    if m.get("net_lift") is not None and m["net_lift"] < 0:
        warnings.append("Итоговый чистый прирост отрицательный.")
    if any(c.get("expected_net_effect") is not None and c["expected_net_effect"] < 0 for c in report.get("campaigns", [])):
        warnings.append("В плане есть кампании с отрицательной ожидаемой отдачей.")
    if any(c.get("contacts") is not None and c["contacts"] > 5000 for c in report.get("campaigns", [])):
        warnings.append("Охват одной из кампаний превышает 5 000 контактов.")
    b = spend_breakdown(report)
    for suffix, name in [("cost", "расходов"), ("contacts", "контактов")]:
        vals = [b.get(f"{part}_{suffix}") for part in ("final", "pilot", "total")]
        if all(x is not None for x in vals) and not math.isclose(vals[0] + vals[1], vals[2], rel_tol=1e-9, abs_tol=0.01):
            warnings.append(f"Не сходится сумма {name}: финал + пилоты не равны итогу score.")
    return warnings


def load_report_bytes(data: bytes, source: str = "upload") -> dict:
    if not isinstance(data, (bytes, bytearray)):
        raise ReportError("Ожидался файл JSON.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ReportError("Файл больше допустимых 5 МБ.")
    try:
        return normalize_report(json.loads(data.decode("utf-8-sig")), source)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        if isinstance(exc, ReportError):
            raise
        raise ReportError("Не удалось прочитать JSON. Проверьте формат файла.") from exc


def load_report_file(path: str | Path, source: str = "upload") -> dict:
    try:
        with Path(path).open("rb") as stream:
            data = stream.read(MAX_UPLOAD_BYTES + 1)
        return load_report_bytes(data, source)
    except OSError as exc:
        raise ReportError("Отчёт недоступен. Проверьте путь и права доступа.") from exc


def sanitize_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): sanitize_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_json(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def export_json(report: dict) -> bytes:
    return json.dumps(sanitize_json(report), ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")


def _csv_cell(value: Any) -> Any:
    # Keep numeric losses numeric; neutralize spreadsheet formulas only in text.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def export_csv(report: dict) -> bytes:
    output = io.StringIO()
    fields = ["source", "seed", "agent_commit", "generated_at", "campaign", "segment", "current_tariff", "target_tariff", "channel", "contacts", "cost", "expected_net_effect", "posterior_lift", "std", "ci95_low", "ci95_high", "pilots_used"]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for c in report["campaigns"]:
        row = {k: report.get(k) for k in ("source", "seed", "agent_commit", "generated_at")}
        row.update({"campaign": c["name"], "segment": c.get("arpu_segment"),
                    **{k: c.get(k) for k in ("current_tariff", "target_tariff", "channel", "contacts", "cost", "expected_net_effect", "posterior_lift", "std", "pilots_used")},
                    "ci95_low": c["ci95"][0], "ci95_high": c["ci95"][1]})
        writer.writerow({k: _csv_cell(v) for k, v in row.items()})
    return output.getvalue().encode("utf-8-sig")


def export_markdown(report: dict) -> bytes:
    def safe(value: Any) -> str:
        text = str(value) if value is not None else "нет данных"
        for symbol in ("\\", "*", "_", "[", "]", "<", ">", "`", "|"):
            text = text.replace(symbol, "\\" + symbol)
        return text.replace("\n", " ")
    m = report["metrics"]
    lines = ["# Beeline Campaign AI — краткое резюме", "",
             f"- Источник: {safe(report['source_label'])} ({safe(report['source'])})",
             f"- Seed: {safe(report.get('seed'))}", f"- Agent commit: {safe(report.get('agent_commit'))}",
             f"- Сформирован: {safe(report.get('generated_at'))}",
             f"- Чистый прирост score: {safe(m['net_lift'])} у.е.",
             f"- Финальных кампаний: {len(report['campaigns'])}", "",
             "> Синтетические данные, не реальные показатели Beeline.", "", "## Кампании"]
    for c in report["campaigns"]:
        lines.append(f"- {safe(c['name'])}: {safe(c.get('current_tariff'))} → {safe(c.get('target_tariff'))}; {safe(c.get('channel'))}; контактов: {safe(c.get('contacts'))}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def retain_last_success(current: dict | None, operation):
    try:
        return operation(), None, False
    except (ReportError, OSError, ValueError) as exc:
        return current, str(exc), current is not None
