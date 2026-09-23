import json
import math
from pathlib import Path
import pytest

from dashboard_ui.data import (ReportError, campaign_rows, confidence_label, export_csv, export_json, load_report_bytes,
                               load_report_file, normalize_report, retain_last_success, selected_campaign,
                               spend_breakdown)


def base(**score):
    return {"seed": 7, "score": {"net_arpu_gain": 10, "total_cost": 5, "roi": 2, "total_contacts": 3, **score},
            "campaigns": [{"campaign_name": "one", "filter_current_tariff": "a", "target_tariff": "b", "channel": "sms", "posterior_lift": .12}]}


def test_correct_report_and_current_campaign_metadata():
    raw = base(); raw["campaigns"].append({"campaign_name":"two","target_tariff":"z","channel":"push","posterior_lift":.44})
    report = normalize_report(raw)
    assert report["campaigns"][0]["posterior_lift"] == .12
    assert report["campaigns"][1]["posterior_lift"] == .44


def test_missing_file():
    with pytest.raises(ReportError): load_report_file("not-here.json")


def test_broken_json():
    with pytest.raises(ReportError): load_report_bytes(b"{")


def test_wrong_types():
    with pytest.raises(ReportError): normalize_report({"campaigns": "wrong"})
    assert normalize_report({"campaigns":[42], "score":{"total_cost":"no"}})["campaigns"] == []


def test_empty_campaigns_are_safe_not_valid_submission():
    report = normalize_report({"campaigns": []})
    assert report["metrics"]["campaigns"] == 0


def test_zero_cost_roi_is_unknown():
    assert normalize_report(base(total_cost=0, roi=math.inf))["metrics"]["roi"] is None


def test_nan_infinity_become_null_json():
    report = normalize_report(base(net_arpu_gain=math.nan, total_contacts=math.inf))
    encoded = export_json(report)
    assert b"NaN" not in encoded and b"Infinity" not in encoded


def test_negative_net_lift_preserved():
    assert normalize_report(base(net_arpu_gain=-99))["metrics"]["net_lift"] == -99


def test_missing_ci_and_history():
    report = normalize_report(base())
    assert report["campaigns"][0]["ci95"] == [None, None] and report["pilots"] == []


def test_long_names_are_bounded():
    raw=base(); raw["campaigns"][0]["campaign_name"]="x"*1000
    assert len(normalize_report(raw)["campaigns"][0]["name"]) == 240


def test_demo_totals_are_consistent():
    raw=json.loads(Path("demo/dashboard_report.json").read_text())
    report=normalize_report(raw,"demo")
    assert sum(c["cost"] for c in report["campaigns"]) + raw["exploration"]["pilot_cost"] == report["metrics"]["cost"]
    assert sum(c["contacts"] for c in report["campaigns"]) + raw["exploration"]["pilot_contacts"] == report["metrics"]["contacts"]
    assert report["metrics"]["net_lift"] == report["metrics"]["gross_lift"] - report["metrics"]["cost"]


def test_retain_last_success_on_failure():
    old=normalize_report(base())
    kept,error,stale=retain_last_success(old,lambda: load_report_bytes(b"bad"))
    assert kept is old and error and stale


def test_exports_preserve_source_and_seed():
    report=normalize_report(base(),"local")
    assert b"local" in export_csv(report) and b'"seed": 7' in export_json(report)


def test_extended_optional_metadata():
    raw = base(); raw.update({"schema_version": 2, "source": "candidate-final", "agent_commit": "abc123", "generated_at": "2026-09-23T12:00:00Z"})
    report = normalize_report(raw, "upload")
    assert report["agent_commit"] == "abc123"
    assert report["generated_at"] == "2026-09-23T12:00:00Z"
    assert report["source"] == "candidate-final"
    assert report["schema_version"] == 2


def test_filter_empty_and_selection_uses_filtered_rows():
    raw = base(); raw["campaigns"].append({"campaign_name": "push", "target_tariff": "c", "channel": "push"})
    report = normalize_report(raw)
    filtered = campaign_rows(report, ["push"])
    assert selected_campaign(filtered, [0])["name"] == "push"
    assert campaign_rows(report, []) == []
    assert selected_campaign([], [0]) is None


def test_campaigns_are_ranked_without_hiding_negative_or_unknown():
    raw = base(); raw["campaigns"][0]["expected_net_effect"] = -5
    raw["campaigns"] += [{"campaign_name":"best", "channel":"sms", "expected_net_effect":20},
                          {"campaign_name":"unknown", "channel":"sms"}]
    assert [c["name"] for c in campaign_rows(normalize_report(raw))] == ["best", "one", "unknown"]


def test_confidence_is_interval_ui_not_probability():
    assert confidence_label({"ci95": [0.02, 0.08]}) == "Высокая"
    assert confidence_label({"ci95": [0.02, 0.30]}) == "Средняя"
    assert confidence_label({"ci95": [-0.02, 0.08]}) == "Низкая"
    assert confidence_label({"ci95": [None, None]}) == "Нет данных"


def test_negative_effect_remains_visible():
    raw = base(); raw["campaigns"][0]["expected_net_effect"] = -12
    report = normalize_report(raw)
    assert campaign_rows(report)[0]["expected_net_effect"] == -12


def test_unknown_metrics_remain_unknown():
    report = normalize_report({"campaigns": []})
    assert report["metrics"]["net_lift"] is None
    assert report["metrics"]["cost"] is None


def test_pilot_final_total_breakdown():
    report = normalize_report({"score": {"total_cost": 120, "total_contacts": 30},
        "exploration": {"pilot_cost": 20, "pilot_contacts": 10},
        "campaigns": [{"campaign_name": "x", "channel": "sms", "cost": 100, "planned_contacts": 20}]})
    assert spend_breakdown(report) == {"final_cost": 100.0, "final_contacts": 20,
        "pilot_cost": 20.0, "pilot_contacts": 10, "total_cost": 120.0, "total_contacts": 30}


def test_export_is_exactly_displayed_report():
    shown = normalize_report(base(), "demo")
    other = normalize_report({"seed": 99, "campaigns": []}, "upload")
    assert b'"source": "demo"' in export_json(shown)
    assert b'"seed": 99' not in export_json(shown)
    assert export_json(shown) != export_json(other)


def test_csv_preserves_extended_metadata():
    raw = base(); raw.update({"source":"candidate-final", "agent_commit":"deadbeef", "generated_at":"2026-09-23T12:00:00Z"})
    exported = export_csv(normalize_report(raw, "upload")).decode("utf-8-sig")
    assert "candidate-final" in exported and "deadbeef" in exported and "2026-09-23" in exported
