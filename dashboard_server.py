"""Local, read-only demo dashboard for the tariff campaign agent.

Run ``python dashboard_server.py`` and open http://127.0.0.1:8765.
The dashboard uses the supplied mock environment. It never sends campaigns to
customers and does not change the submitted agent or submission.csv.
"""

from __future__ import annotations

import csv
import io
import json
import math
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pandas as pd

from agent import PILOT_STD_PER_CUSTOMER, PILOTED_RISK_PENALTY, _candidates, _explore, _plan, _posterior
from make_submission import CAMPAIGN_COLUMNS
from mock_environment import (
    MAX_TOTAL_CONTACTS, TOTAL_BUDGET, _mock_fallback, _mock_impact_model,
    make_mock_env,
)
from scoring_core import MAX_CAMPAIGNS, score_campaigns, sanitize_campaigns


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "dashboard"


@lru_cache(maxsize=8)
def build_dashboard(seed: int = 42) -> dict:
    """Run the exact baseline decision pipeline and return display diagnostics."""
    env, internals = make_mock_env(seed=seed)
    pilot_candidates, small_candidates = _candidates(env)
    _explore(env, pilot_candidates)
    candidates = pilot_candidates + small_candidates
    final = sanitize_campaigns(_plan(env, candidates), env.tariffs)[:MAX_CAMPAIGNS]
    pilots = internals.executed_pilot_campaigns()

    all_campaigns = pd.DataFrame(pilots + final)
    for column in (
        "filter_arpu_segment", "filter_data_segment", "filter_call_segment",
        "filter_current_tariff", "explicit_ids",
    ):
        if column not in all_campaigns:
            all_campaigns[column] = None
    mock_model = _mock_impact_model(pd.read_csv(ROOT / "data" / "change_tariff.csv"))
    score = score_campaigns(
        all_campaigns, env.customer_profile, mock_model, env.tariffs,
        float(env.customer_profile["predicted_arpu"].sum()), _mock_fallback,
        team_id="dashboard-local-mock",
    )
    scored_detail = {item["name"]: item for item in score["campaigns_detail"]}
    tariff_detail = env.tariffs.set_index("tariff_plan_code")
    by_key = {
        (item["filter_current_tariff"], item["filter_arpu_segment"], item["target_tariff"]): item
        for item in candidates
    }
    pilot_results: dict[tuple[str, str, str], list[dict]] = {}
    for campaign, result in zip(pilots, env.pilot_history):
        key = (
            campaign["filter_current_tariff"], campaign["filter_arpu_segment"],
            campaign["target_tariff"],
        )
        actual_n = int(result["n_customers"])
        pilot_results.setdefault(key, []).append({
            "n": actual_n,
            "observed_lift_pct": 100 * float(result["observed_lift_ratio"]),
            "standard_error_pct": 100 * PILOT_STD_PER_CUSTOMER / math.sqrt(actual_n),
            "cost": float(result["cost"]),
        })

    rows = []
    for index, campaign in enumerate(final, start=1):
        key = (
            campaign["filter_current_tariff"], campaign["filter_arpu_segment"],
            campaign["target_tariff"],
        )
        candidate = by_key[key]
        detail = scored_detail[campaign["campaign_name"]]
        n = int(detail["n_contacts"])
        mean_sms, std_sms = _posterior(candidate)
        factor = (
            env.channels[campaign["channel"]]["conversion_multiplier"]
            / env.channels["sms"]["conversion_multiplier"]
        )
        mean, std = mean_sms * factor, std_sms * factor
        arpu_sum = float(candidate["arpu_prefix"][n - 1]) if n else 0.0
        cost = float(detail["cost"])
        expected_net = arpu_sum * mean - cost
        cautious_net = arpu_sum * (mean - PILOTED_RISK_PENALTY * std) - cost
        members = env.customer_profile.loc[
            (env.customer_profile["current_tariff"] == key[0])
            & (env.customer_profile["arpu_segment"] == key[1])
        ]
        data_volume = pd.to_numeric(members["DATA_VOLUME"], errors="coerce")
        rows.append({
            "rank": index,
            "source": key[0], "segment": key[1], "target": key[2],
            "channel": campaign["channel"],
            "name": campaign["campaign_name"],
            "contacts": n, "audience": int(candidate["audience_size"]),
            "cost": cost, "cost_per_contact": float(env.channels[campaign["channel"]]["cost_per_contact"]),
            "expected_net": expected_net,
            "cautious_net": cautious_net,
            "posterior_lift_pct": 100 * mean,
            "posterior_std_pct": 100 * std,
            "prior_lift_pct": 100 * float(candidate["prior_lift_ratio"]) * factor,
            "tariff_context": {
                "current_price": float(tariff_detail.loc[key[0], "price_tariff"]),
                "target_price": float(tariff_detail.loc[key[2], "price_tariff"]),
                "current_data_gb": float(tariff_detail.loc[key[0], "Data_in_PKG"]) / 1024,
                "target_data_gb": float(tariff_detail.loc[key[2], "Data_in_PKG"]) / 1024,
                "data_user_pct": 100 * float((data_volume > 0).mean()),
                "heavy_user_pct": 100 * float((members["data_segment"] == "HEAVY").mean()),
            },
            "pilot_count": int(candidate["pilot_count"]),
            "pilots": pilot_results.get(key, []),
            "capped": bool(detail["capped_at_campaign_limit"] or detail["capped_at_reach_budget"] or detail["capped_at_money_budget"]),
        })

    selected = {row["name"] for row in rows}
    rejected = []
    for candidate in pilot_candidates:
        name = (
            f"main_{candidate['filter_current_tariff']}_{candidate['filter_arpu_segment']}_"
            f"{candidate['target_tariff']}"
        )
        if name in selected or not candidate.get("is_rejected"):
            continue
        mean, std = _posterior(candidate)
        n = min(int(candidate["audience_size"]), 5000)
        rejected.append({
            "source": candidate["filter_current_tariff"],
            "segment": candidate["filter_arpu_segment"],
            "target": candidate["target_tariff"],
            "posterior_lift_pct": 100 * mean,
            "posterior_std_pct": 100 * std,
            "pilots": pilot_results.get((
                candidate["filter_current_tariff"], candidate["filter_arpu_segment"],
                candidate["target_tariff"],
            ), []),
            "potential_sms_cost": n * float(env.channels["sms"]["cost_per_contact"]),
            "reason": "После первого пилота μ + 1σ < 0; повторный пилот не проводился.",
        })

    return {
        "mode": "Локальная симуляция · не прогноз результата на скрытом судействе",
        "seed": seed,
        "limits": {
            "campaigns": MAX_CAMPAIGNS, "contacts": MAX_TOTAL_CONTACTS,
            "budget": TOTAL_BUDGET, "pilots": 20,
        },
        "summary": {
            "campaigns": len(rows),
            "contacts": int(score["total_contacts"]),
            "budget_used": float(score["total_cost"]),
            "net_arpu_gain": float(score["net_arpu_gain"]),
            "pilots": len(pilots),
            "tested_hypotheses": sum(item["pilot_count"] > 0 for item in pilot_candidates),
            "unique_customers": int(score["unique_customers_targeted"]),
            "rejected": len(rejected),
            "potential_sms_cost_avoided": sum(row["potential_sms_cost"] for row in rejected),
        },
        "campaigns": rows,
        "rejected": rejected,
        "submission": final,
    }


class DashboardHandler(BaseHTTPRequestHandler):
    def _reply(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        route = urlsplit(self.path)
        if route.path in ("/", "/index.html", "/styles.css", "/app.js"):
            name = "index.html" if route.path == "/" else route.path.lstrip("/")
            mime = {"index.html": "text/html", "styles.css": "text/css", "app.js": "text/javascript"}[name]
            self._reply((STATIC / name).read_bytes(), f"{mime}; charset=utf-8")
            return
        if route.path not in ("/api/plan", "/api/submission.csv"):
            self._reply(b"Not found", "text/plain", 404)
            return
        try:
            seed = int(parse_qs(route.query).get("seed", ["42"])[0])
            if not 0 <= seed <= 10000:
                raise ValueError("seed out of range")
            report = build_dashboard(seed)
        except (ValueError, TypeError) as exc:
            self._reply(str(exc).encode(), "text/plain; charset=utf-8", 400)
            return
        if route.path == "/api/plan":
            self._reply(json.dumps(report, ensure_ascii=False, allow_nan=False).encode(), "application/json; charset=utf-8")
        else:
            stream = io.StringIO()
            writer = csv.DictWriter(stream, fieldnames=CAMPAIGN_COLUMNS)
            writer.writeheader()
            for campaign in report["submission"]:
                writer.writerow({key: campaign.get(key, "") for key in CAMPAIGN_COLUMNS})
            self._reply(stream.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8")


def main() -> None:
    host, port = "127.0.0.1", 8765
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"Beeline demo dashboard: http://{host}:{port}")
    print("Локальная мок-симуляция; Ctrl+C для остановки.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
