"""Paired mock evaluation of baseline and optional learned-prior challenger."""

from __future__ import annotations

import numpy as np
import pandas as pd

from agent import Agent
from local_eval import evaluate_agent
from ml_prior import learned_priors
from mock_environment import _mock_impact_model
from stress_eval import ROOT, evaluate as stress_evaluate


def report(scores: dict[str, list[float]], title: str) -> None:
    print(title)
    for name, values in scores.items():
        values = np.array(values)
        print(f"{name:14} min={values.min():,.0f} mean={values.mean():,.0f} "
              f"median={np.median(values):,.0f} positive={(values > 0).sum()}/{len(values)}")
    difference = np.array(scores["learned_prior"]) - np.array(scores["baseline"])
    print(f"paired_delta   mean={difference.mean():+,.0f} wins={(difference > 0).sum()}/{len(difference)}")


def main() -> None:
    scores = {"baseline": [], "learned_prior": []}
    for seed in range(10):
        for name, agent in (
            ("baseline", Agent()),
            ("learned_prior", Agent(prior_provider=learned_priors)),
        ):
            result = evaluate_agent(agent, seed=seed, verbose=False)
            if result is None:
                raise RuntimeError(f"{name} failed on seed {seed}")
            scores[name].append(float(result["net_arpu_gain"]))
    report(scores, "Paired local mock evaluation (not hidden judge performance)")

    base = _mock_impact_model(pd.read_csv(ROOT / "data" / "change_tariff.csv"))
    profile = pd.read_csv(ROOT / "customer_profile.csv")
    tariffs = pd.read_csv(ROOT / "data" / "dict_tariff.csv")
    for bias in (0.0, -0.3, -0.6):
        shifted = {"baseline": [], "learned_prior": []}
        for seed in range(20):
            for name, agent in (
                ("baseline", Agent()),
                ("learned_prior", Agent(prior_provider=learned_priors)),
            ):
                result = stress_evaluate(seed, base, profile, tariffs, 0.30, bias, agent)
                shifted[name].append(float(result["net_arpu_gain"]))
        report(shifted, f"Shifted synthetic effects: SD=0.30, bias={bias:+.2f}")


if __name__ == "__main__":
    main()
