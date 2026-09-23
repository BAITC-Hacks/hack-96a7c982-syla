"""One-pass local agent execution for the dashboard."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json
import pandas as pd

from agent import Agent
from local_eval import CAMPAIGN_FILTER_COLUMNS
from mock_environment import make_mock_env, _mock_fallback, _mock_impact_model
from scoring_core import MAX_CAMPAIGNS, sanitize_campaigns, score_campaigns
from dashboard_ui.data import normalize_report, export_json


def run_local(seed: int = 42) -> dict:
    """Run Agent.act exactly once, then score that same run including its pilots."""
    env, internals = make_mock_env(seed=seed)
    final = sanitize_campaigns(Agent().act(env) or [], env.tariffs)[:MAX_CAMPAIGNS]
    pilots = internals.executed_pilot_campaigns()
    all_rows = pd.DataFrame(pilots + final)
    if all_rows.empty:
        result = {}
    else:
        for column in CAMPAIGN_FILTER_COLUMNS + ["explicit_ids"]:
            if column not in all_rows: all_rows[column] = None
        model = _mock_impact_model(pd.read_csv("data/change_tariff.csv"))
        result = score_campaigns(all_rows, env.customer_profile, model, env.tariffs,
                                 env.customer_profile["predicted_arpu"].sum(), _mock_fallback, team_id="dashboard")
    details = {x["name"]: x for x in result.get("campaigns_detail", [])}
    rows = []
    for campaign in final:
        c = dict(campaign); detail = details.get(c.get("campaign_name"), {})
        c.update({k: detail.get(k) for k in ("cost", "n_contacts", "gross_lift")})
        rows.append(c)
    payload = {"seed": seed, "generated_at": datetime.now(timezone.utc).isoformat(), "score": result,
               "campaigns": rows, "pilot_history": list(env.pilot_history),
               "exploration": {"pilots_executed": len(env.pilot_history),
                               "pilot_contacts": sum(x.get("n_customers", 0) for x in env.pilot_history),
                               "pilot_cost": sum(x.get("cost", 0) for x in env.pilot_history)}}
    report = normalize_report(payload, "local")
    out = Path("artifacts/dashboard"); out.mkdir(parents=True, exist_ok=True)
    (out / "latest_report.json").write_bytes(export_json(report))
    return report
