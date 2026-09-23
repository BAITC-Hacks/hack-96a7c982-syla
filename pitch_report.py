"""Generate pitch-ready JSON and Markdown reports from the current agent."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
from agent import Agent
from local_eval import evaluate_agent
from mock_environment import make_mock_env

def build(seed=42):
    env, _ = make_mock_env(seed=seed)
    campaigns = Agent().act(env) or []
    # Re-evaluate independently for scorer metrics.
    result = evaluate_agent(Agent(), seed=seed, verbose=False) or {}
    rejected = max(0, 20 - int(getattr(env, "pilots_left", 20)))
    rows = []
    for c in campaigns:
        rows.append({
            "campaign": c.get("campaign_name"),
            "segment": {
                "current_tariff": c.get("filter_current_tariff"),
                "arpu": c.get("filter_arpu_segment"),
                "data": c.get("filter_data_segment"),
                "calls": c.get("filter_call_segment"),
            },
            "target_tariff": c.get("target_tariff"),
            "channel": c.get("channel"),
            "estimated_group_size": c.get("estimated_group_size"),
            "expected_net_effect": c.get("expected_net_effect"),
            "uncertainty": c.get("uncertainty"),
            "confidence_interval_95": c.get("confidence_interval_95"),
            "pilots_used": c.get("pilots_used"),
        })
    payload = {
        "seed": seed,
        "score": {
            "net_arpu_gain": result.get("net_arpu_gain"),
            "gross_arpu_lift": result.get("gross_arpu_lift"),
            "total_cost": result.get("total_cost"),
            "roi": result.get("roi"),
            "coverage_pct": result.get("coverage_pct"),
            "risk_score_pct": result.get("risk_score_pct"),
        },
        "exploration": {
            "pilots_executed": rejected,
            "pilots_remaining": int(getattr(env, "pilots_left", 0)),
            "contacts_remaining": int(getattr(env, "remaining_contacts", 0)),
            "budget_remaining": float(getattr(env, "remaining_budget", 0)),
        },
        "campaigns": rows,
    }
    return payload

def write_reports(seed=42):
    p = build(seed)
    Path("PITCH_REPORT.json").write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    s=p["score"]; e=p["exploration"]
    lines=[
        "# Pitch Summary", "",
        f"- Net ARPU gain: **{s.get('net_arpu_gain', 0):,.0f}**",
        f"- Gross lift: **{s.get('gross_arpu_lift', 0):,.0f}**",
        f"- Communication cost: **{s.get('total_cost', 0):,.0f}**",
        f"- ROI: **{s.get('roi', 0):.2f}**",
        f"- Coverage: **{s.get('coverage_pct', 0):.1f}%**",
        f"- Risk score: **{s.get('risk_score_pct', 0):.1f}%**",
        f"- Pilots executed: **{e['pilots_executed']}**", "",
        "## Recommended campaigns", "",
        "| Segment | Target | Channel | Size | Expected net | 95% CI | Pilots |",
        "|---|---|---:|---:|---:|---|---:|",
    ]
    for c in p["campaigns"]:
        seg=c["segment"]; ci=c.get("confidence_interval_95") or [None,None]
        ci_txt="n/a" if ci[0] is None else f"[{ci[0]:.3f}, {ci[1]:.3f}]"
        net=c.get("expected_net_effect")
        lines.append(f"| {seg['current_tariff']} / {seg['arpu']} | {c['target_tariff']} | {c['channel']} | {c.get('estimated_group_size') or 0} | {net or 0:,.0f} | {ci_txt} | {c.get('pilots_used') or 0} |")
    Path("SUMMARY.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    print("Generated PITCH_REPORT.json and SUMMARY.md")

if __name__ == "__main__":
    write_reports()
