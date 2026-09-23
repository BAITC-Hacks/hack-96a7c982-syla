"""Defensive report loading, normalization and export helpers."""
from __future__ import annotations

import csv
import io
import json
import math
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
SOURCE_LABELS = {"demo": "Демонстрационные данные", "upload": "Загруженный отчёт", "local": "Локальная симуляция"}


class ReportError(ValueError):
    pass


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
    return max(0, int(number)) if number is not None else None


def _text(value: Any, limit: int = 240) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    value = str(value).strip()
    return value[:limit] if value else None


def _campaign(raw: Any, index: int, detail: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    segment = raw.get("segment") if isinstance(raw.get("segment"), dict) else {}
    ci = raw.get("confidence_interval_95", raw.get("ci95"))
    ci = [_finite(ci[0]), _finite(ci[1])] if isinstance(ci, (list, tuple)) and len(ci) == 2 else [None, None]
    name = _text(raw.get("campaign", raw.get("campaign_name", raw.get("name")))) or f"campaign_{index + 1}"
    warnings = raw.get("warnings", [])
    if not isinstance(warnings, list):
        warnings = [warnings]
    return {
        "id": f"{index}:{name}", "name": name,
        "current_tariff": _text(segment.get("current_tariff", raw.get("filter_current_tariff"))),
        "arpu_segment": _text(segment.get("arpu", raw.get("filter_arpu_segment"))),
        "data_segment": _text(segment.get("data", raw.get("filter_data_segment"))),
        "call_segment": _text(segment.get("calls", raw.get("filter_call_segment"))),
        "target_tariff": _text(raw.get("target_tariff")), "channel": _text(raw.get("channel")),
        "group_size": _integer(raw.get("estimated_group_size", raw.get("audience_size"))),
        "contacts": _integer(raw.get("planned_contacts", raw.get("n_contacts", detail.get("n_contacts")))),
        "cost": _finite(raw.get("cost", detail.get("cost"))),
        "expected_net_effect": _finite(raw.get("expected_net_effect")),
        "gross_lift": _finite(raw.get("gross_lift", detail.get("gross_lift"))),
        "posterior_lift": _finite(raw.get("posterior_lift")),
        "std": _finite(raw.get("uncertainty", raw.get("posterior_std"))),
        "ci95": ci, "risk_adjusted_lift": _finite(raw.get("risk_adjusted_lift")),
        "pilots_used": _integer(raw.get("pilots_used")), "pilot_customers": _integer(raw.get("pilot_customers")),
        "warnings": [text for x in warnings if (text := _text(x))],
    }


def normalize_report(raw: Any, source: str = "upload") -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ReportError("Корень JSON должен быть объектом.")
    score = raw.get("score") if isinstance(raw.get("score"), dict) else raw
    exploration = raw.get("exploration") if isinstance(raw.get("exploration"), dict) else {}
    rows = raw.get("campaigns", [])
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise ReportError("Поле campaigns должно быть массивом.")
    details = score.get("campaigns_detail", raw.get("campaigns_detail", []))
    detail_map = {str(x.get("name")): x for x in details if isinstance(x, dict)} if isinstance(details, list) else {}
    campaigns = []
    for i, item in enumerate(rows):
        name = item.get("campaign", item.get("campaign_name", item.get("name"))) if isinstance(item, dict) else None
        normalized = _campaign(item, i, detail_map.get(str(name), {}))
        if normalized:
            campaigns.append(normalized)
    history = raw.get("pilot_history", exploration.get("pilot_history"))
    pilots = []
    if isinstance(history, list):
        for i, p in enumerate(history):
            if isinstance(p, dict):
                pilots.append({
                    "pilot": _text(p.get("pilot")) or f"pilot_{i + 1}", "target_tariff": _text(p.get("target_tariff")),
                    "channel": _text(p.get("channel")), "contacts": _integer(p.get("n_customers", p.get("contacts"))),
                    "cost": _finite(p.get("cost")), "observed_lift_ratio": _finite(p.get("observed_lift_ratio")),
                    "observed_lift_total": _finite(p.get("observed_lift_total")), "status": _text(p.get("status")),
                })
    cost = _finite(score.get("total_cost"))
    roi = _finite(score.get("roi")) if cost not in (None, 0) else None
    return {
        "schema_version": 1, "source": source if source in SOURCE_LABELS else "upload",
        "source_label": SOURCE_LABELS.get(source, SOURCE_LABELS["upload"]), "seed": _integer(raw.get("seed")),
        "generated_at": _text(raw.get("generated_at", raw.get("report_time"))),
        "is_demo": source == "demo" or raw.get("is_demo") is True,
        "metrics": {"net_lift": _finite(score.get("net_arpu_gain", score.get("net_lift"))),
                    "gross_lift": _finite(score.get("gross_arpu_lift", score.get("gross_lift"))),
                    "cost": cost, "roi": roi, "contacts": _integer(score.get("total_contacts")),
                    "campaigns": len(campaigns)},
        "campaigns": campaigns, "pilots": pilots,
        "exploration": {"pilots_executed": _integer(exploration.get("pilots_executed")),
                        "pilot_contacts": _integer(exploration.get("pilot_contacts")),
                        "pilot_cost": _finite(exploration.get("pilot_cost"))},
    }


def load_report_bytes(data: bytes, source: str = "upload") -> dict[str, Any]:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ReportError("Файл больше допустимых 5 МБ.")
    try:
        return normalize_report(json.loads(data.decode("utf-8")), source)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReportError(f"Не удалось прочитать JSON: {exc}") from exc


def load_report_file(path: str | Path, source: str = "upload") -> dict[str, Any]:
    try:
        return load_report_bytes(Path(path).read_bytes(), source)
    except OSError as exc:
        raise ReportError(f"Отчёт недоступен: {exc}") from exc


def sanitize_json(value: Any) -> Any:
    if isinstance(value, dict): return {str(k): sanitize_json(v) for k, v in value.items()}
    if isinstance(value, list): return [sanitize_json(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value): return None
    return value


def export_json(report: dict[str, Any]) -> bytes:
    return json.dumps(sanitize_json(report), ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")


def export_csv(report: dict[str, Any]) -> bytes:
    output = io.StringIO()
    fields = ["source", "seed", "campaign", "segment", "current_tariff", "target_tariff", "channel", "contacts", "cost", "expected_net_effect", "posterior_lift", "std", "ci95_low", "ci95_high", "pilots_used"]
    writer = csv.DictWriter(output, fieldnames=fields); writer.writeheader()
    for c in report["campaigns"]:
        writer.writerow({"source": report["source"], "seed": report.get("seed"), "campaign": c["name"],
            "segment": c.get("arpu_segment"), "current_tariff": c.get("current_tariff"), "target_tariff": c.get("target_tariff"),
            "channel": c.get("channel"), "contacts": c.get("contacts"), "cost": c.get("cost"),
            "expected_net_effect": c.get("expected_net_effect"), "posterior_lift": c.get("posterior_lift"), "std": c.get("std"),
            "ci95_low": c["ci95"][0], "ci95_high": c["ci95"][1], "pilots_used": c.get("pilots_used")})
    return output.getvalue().encode("utf-8-sig")


def export_markdown(report: dict[str, Any]) -> bytes:
    m = report["metrics"]
    lines = ["# Beeline Campaign AI — краткое резюме", "", f"- Источник: **{report['source_label']}**",
             f"- Seed: **{report.get('seed') if report.get('seed') is not None else 'не указан'}**",
             f"- Чистый прирост по локальному скореру: **{m['net_lift'] if m['net_lift'] is not None else 'нет данных'} у.е.**",
             f"- Кампаний: **{len(report['campaigns'])}**", "", "> Синтетические данные. Результаты симуляции не являются показателями Beeline.", "", "## Кампании"]
    for c in report["campaigns"]:
        lines.append(f"- **{c['name']}**: {c.get('current_tariff') or '—'} → {c.get('target_tariff') or '—'}, {c.get('channel') or '—'}, контактов: {c.get('contacts') if c.get('contacts') is not None else 'нет данных'}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def retain_last_success(current: dict[str, Any] | None, operation):
    try:
        return operation(), None, False
    except (ReportError, OSError, ValueError) as exc:
        return current, str(exc), current is not None
