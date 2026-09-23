"""Local robustness check with synthetic effect shifts (not part of submission).

The submitted agent still sees only the public ``env`` interface. This harness
plays the same organizer role as ``local_eval.py`` and never uses hidden data.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import agent
from agent import Agent
from environment import make_environment
from mock_environment import (
    CHANNELS, MAX_TOTAL_CONTACTS, TOTAL_BUDGET, _mock_fallback,
    _mock_impact_model,
)
from scoring_core import score_campaigns, sanitize_campaigns


ROOT = Path(__file__).resolve().parent


def shifted_model(base: pd.DataFrame, seed: int, change_sd: float,
                  change_bias: float) -> pd.DataFrame:
    """Vary transition effects and conversion rates, not just pilot noise."""
    rng = np.random.default_rng(seed)
    model = base.copy()
    pair_keys = list(zip(model["tariff_plan_code_from"], model["tariff_plan_code_to"]))
    pair_shocks = {
        key: rng.normal(0.0, change_sd * 0.7)
        for key in sorted(set(pair_keys))
    }
    row_shocks = rng.normal(0.0, change_sd * 0.3, len(model))
    model["arpu_change_pct"] = np.clip(
        model["arpu_change_pct"].to_numpy()
        + np.array([pair_shocks[key] for key in pair_keys]) + row_shocks
        + change_bias,
        -1.0, 3.0,
    )
    conversion_scale = rng.lognormal(0.0, 0.35, len(model))
    model["conversion_rate"] = np.clip(
        model["conversion_rate"].to_numpy() * conversion_scale, 0.001, 1.0
    )
    return model


def evaluate(seed: int, base: pd.DataFrame, profile: pd.DataFrame,
             tariffs: pd.DataFrame, change_sd: float,
             change_bias: float, agent_instance=None) -> dict:
    model = shifted_model(base, seed, change_sd, change_bias)
    env, scoring_state = make_environment(
        customer_profile=profile, impact_model=model, dict_tariff=tariffs,
        channels=CHANNELS, total_budget=TOTAL_BUDGET,
        max_total_contacts=MAX_TOTAL_CONTACTS, fallback_predict=_mock_fallback,
        seed=seed,
    )
    final = sanitize_campaigns((agent_instance or Agent()).act(env), tariffs)
    pilots = scoring_state.executed_pilot_campaigns()
    campaigns = pd.DataFrame(pilots + final)
    for column in (
        "filter_arpu_segment", "filter_data_segment", "filter_call_segment",
        "filter_current_tariff", "explicit_ids",
    ):
        if column not in campaigns:
            campaigns[column] = None
    result = score_campaigns(
        campaigns, profile, model, tariffs, profile["predicted_arpu"].sum(),
        _mock_fallback, team_id="stress",
    )
    result["n_pilots"] = len(pilots)
    result["n_final"] = len(final)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenarios", type=int, default=20)
    parser.add_argument("--change-sd", type=float, default=0.30)
    parser.add_argument("--change-bias", type=float, default=0.0)
    parser.add_argument("--risk-penalty", type=float, default=1.0)
    args = parser.parse_args()
    if args.scenarios < 1 or args.change_sd < 0 or args.risk_penalty < 0:
        parser.error("scenarios must be positive; SD and penalty nonnegative")
    agent.PILOTED_RISK_PENALTY = args.risk_penalty

    base = _mock_impact_model(pd.read_csv(ROOT / "data" / "change_tariff.csv"))
    profile = pd.read_csv(ROOT / "customer_profile.csv")
    tariffs = pd.read_csv(ROOT / "data" / "dict_tariff.csv")
    results = [
        evaluate(seed, base, profile, tariffs, args.change_sd, args.change_bias)
        for seed in range(args.scenarios)
    ]
    net = pd.Series([result["net_arpu_gain"] for result in results])
    print(
        f"Scenarios: {len(net)} | change SD: {args.change_sd:.2f}"
        f" | change bias: {args.change_bias:+.2f}"
        f" | risk penalty: {args.risk_penalty:.1f}"
    )
    print(f"Net ARPU min/mean/median: {net.min():,.0f} / {net.mean():,.0f} / {net.median():,.0f}")
    print(f"Positive scenarios: {(net > 0).sum()} / {len(net)}")
    print(f"Pilots min/max: {min(r['n_pilots'] for r in results)} / {max(r['n_pilots'] for r in results)}")
    print(f"Final campaigns min/max: {min(r['n_final'] for r in results)} / {max(r['n_final'] for r in results)}")


if __name__ == "__main__":
    main()
