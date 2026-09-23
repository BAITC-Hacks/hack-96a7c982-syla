"""Regression checks for report reload, provenance, intervals and truthful totals."""
import csv
import io
import json
import math
from copy import deepcopy

import pytest

from dashboard_ui.data import (
    MAX_UPLOAD_BYTES, ReportError, campaign_rows, channel_totals, confidence_label,
    export_csv, export_json, export_markdown, load_report_bytes, load_report_file,
    normalize_report, report_warnings, retain_last_success, selected_campaign, spend_breakdown,
)


def sample():
    return {
        "source": "local_mock", "seed": 42, "agent_commit": "abc123", "generated_at": "2026-09-23T10:00:00+05:00",
        "score": {"net_arpu_gain": 30, "gross_arpu_lift": 42, "total_cost": 12, "roi": 3.5, "total_contacts": 4},
        "exploration": {"pilots_executed": 1, "pilot_contacts": 1, "pilot_cost": 4},
        "campaigns": [
            {"campaign_id": "first", "campaign_name": "one", "filter_current_tariff": "a", "target_tariff": "b", "channel": "sms",
             "planned_contacts": 2, "cost": 8, "expected_net_effect": 31, "posterior_lift": .1, "uncertainty": .02,
             "confidence_interval_95": [.06,.14], "pilot_customers": 1, "pilots_used": 1},
            {"campaign_name": "two", "filter_current_tariff": "b", "target_tariff": "c", "channel": "push",
             "planned_contacts": 1, "cost": 0, "expected_net_effect": -1, "posterior_lift": -.01, "uncertainty": .03,
             "confidence_interval_95": [-.068,.048], "pilots_used": 0},
        ],
        "pilot_history": [{"pilot": "pilot_1", "channel": "sms", "n_customers": 1, "cost": 4, "observed_lift_ratio": .1}],
    }


def test_json_roundtrip_preserves_metrics_campaigns_pilots_and_provenance():
    original = normalize_report(sample(), "local")
    reloaded = load_report_bytes(export_json(original), "upload")
    for field in ("metrics", "campaigns", "pilots", "exploration", "source", "agent_commit", "seed", "generated_at"):
        assert reloaded[field] == original[field], field


@pytest.mark.parametrize("source,is_demo", [("demo",False), ("local_mock",True)])
def test_uploaded_demo_is_always_marked(source, is_demo):
    raw=sample();raw.update(source=source,is_demo=is_demo)
    report=normalize_report(raw, "upload")
    assert report["is_demo"] and report["source"] == "demo"
    assert "Демонстрационные" in report["source_label"]
    assert load_report_bytes(export_json(report))["is_demo"]


@pytest.mark.parametrize("ci", [[.1,None], [None,.3], [math.nan,.3], [.2,.1], "broken", [1], [0,math.inf]])
def test_bad_interval_is_atomic_unknown(ci):
    raw=sample();raw["campaigns"][0]["confidence_interval_95"]=ci
    campaign=normalize_report(raw)["campaigns"][0]
    assert campaign["ci95"] == [None,None]
    assert confidence_label(campaign) == "Нет данных"
    assert campaign["warnings"]


@pytest.mark.parametrize("ci,label", [([-.1,.2],"Включает ноль"),([.1,.2],"Выше нуля"),([-.3,-.1],"Ниже нуля")])
def test_interval_label_is_not_a_success_probability(ci,label):
    assert confidence_label({"ci95":ci}) == label


@pytest.mark.parametrize("bad", [-1, math.inf, math.nan, "broken", True, 2.5])
def test_bad_contact_counts_are_not_silently_zero(bad):
    raw=sample();raw["campaigns"][0]["planned_contacts"]=bad
    report=normalize_report(raw)
    assert report["campaigns"][0]["contacts"] is None
    assert spend_breakdown(report)["final_contacts"] is None


def test_partial_cost_is_not_a_total_or_zero():
    raw=sample(); raw["campaigns"][1]["cost"]=None
    report=normalize_report(raw)
    assert spend_breakdown(report)["final_cost"] is None
    assert channel_totals(report)["push"]["cost"] is None


def test_real_zero_cost_push_is_kept():
    report=normalize_report(sample())
    assert channel_totals(report)["push"] == {"cost":0.0, "contacts":1}


def test_cost_mismatch_is_reported_without_changing_score():
    raw=sample();raw["score"]["total_cost"]=1000
    report=normalize_report(raw)
    assert report["metrics"]["cost"] == 1000
    assert any("Не сходится" in x for x in report["warnings"])


def test_zero_expense_roi_not_infinite():
    raw=sample();raw["score"].update(total_cost=0,roi=math.inf)
    assert normalize_report(raw)["metrics"]["roi"] is None


def test_negative_net_preserved_and_warned():
    raw=sample();raw["score"]["net_arpu_gain"]=-100
    report=normalize_report(raw)
    assert report["metrics"]["net_lift"] == -100
    assert any("отрицательный" in x for x in report["warnings"])


def test_unknown_metrics_remain_unknown():
    report=normalize_report({"campaigns":[]})
    assert report["metrics"]["cost"] is None
    assert report["metrics"]["contacts"] is None


def test_empty_filter_is_not_called_an_empty_plan():
    report=normalize_report(sample())
    assert campaign_rows(report,[]) == []
    assert report["metrics"]["campaigns"] == 2


@pytest.mark.parametrize("selection", [[99], [-1], ["0"], [True], [], [None]])
def test_stale_selection_is_safe(selection):
    rows=normalize_report(sample())["campaigns"]
    assert selected_campaign(rows,selection) is rows[0]
    assert selected_campaign([],selection) is None


def test_ordering_keeps_losses_and_sorts_by_effect():
    report=normalize_report(sample())
    rows=campaign_rows(report)
    assert [r["expected_net_effect"] for r in rows] == [31,-1]


def test_bom_json_and_limits(tmp_path):
    report=load_report_bytes(b'\xef\xbb\xbf'+json.dumps(sample()).encode())
    assert report["seed"] == 42
    with pytest.raises(ReportError):load_report_bytes(b" "*(MAX_UPLOAD_BYTES+1))
    with pytest.raises(ReportError):load_report_file(tmp_path/"absent")


@pytest.mark.parametrize("payload", [b"{", b"[]", b"null", b'{"campaigns":42}', b'\xff'])
def test_corrupt_payloads_become_report_errors(payload):
    with pytest.raises(ReportError): load_report_bytes(payload)


def test_deep_json_is_controlled():
    with pytest.raises(ReportError):load_report_bytes(b"["*4000 + b"0" + b"]"*4000)


def test_csv_formula_neutralized_but_negative_number_kept():
    raw=sample();raw["campaigns"][0]["campaign_name"]="=1+1"
    report=normalize_report(raw)
    rows=list(csv.DictReader(io.StringIO(export_csv(report).decode("utf-8-sig"))))
    assert rows[0]["campaign"] == "'=1+1"
    assert rows[1]["expected_net_effect"] == "-1.0"


def test_json_export_is_standard_and_is_pure():
    raw=sample();raw["campaigns"][0]["uncertainty"]=math.nan
    report=normalize_report(raw);original=deepcopy(report)
    encoded=export_json(report)
    assert b"NaN" not in encoded and b"Infinity" not in encoded
    assert report == original


def test_markdown_export_retains_origin_and_version():
    text=export_markdown(normalize_report(sample())).decode()
    assert "abc123" in text and "local\\_mock" in text and "42" in text


def test_failure_retains_previous_report():
    old=normalize_report(sample())
    result,error,stale=retain_last_success(old,lambda:load_report_bytes(b"!"))
    assert result is old and error and stale


def test_partial_history_does_not_invent_pilot_totals():
    raw=sample();raw["exploration"]={"pilots_executed":20}
    report=normalize_report(raw)
    assert report["exploration"]["pilot_contacts"] is None


def test_complete_history_can_fill_missing_totals():
    raw=sample();raw["exploration"]={}
    report=normalize_report(raw)
    assert report["exploration"] == {"pilots_executed":1,"pilot_contacts":1,"pilot_cost":4}


def test_invariants_detect_all_resource_limits():
    raw=sample();raw["score"].update(total_cost=100001,total_contacts=15001)
    raw["exploration"]["pilots_executed"]=21
    raw["campaigns"]=raw["campaigns"]*6
    report=normalize_report(raw)
    assert len(report["warnings"]) >= 4


def test_normalization_does_not_mutate_source():
    raw=sample(); old=deepcopy(raw); normalize_report(raw)
    assert raw == old
