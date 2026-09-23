"""Optional learned prior for a challenger agent; baseline remains default."""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from model_experiment import design_matrix, fit_ridge, load_data


@lru_cache(maxsize=1)
def _trained_prior() -> tuple[dict, float]:
    history, prices, scale = load_data()
    x = design_matrix(history, prices, scale)
    y = history["change_ratio"].to_numpy()
    weights = fit_ridge(x, y, alpha=100.0)
    history["predicted_change"] = np.clip(x @ weights, -1.0, 3.0)
    keys = ["tariff_plan_code_from", "segment", "tariff_plan_code_to"]
    grouped = history.groupby(keys)["predicted_change"].agg(["mean", "size"])
    totals = history.groupby(["tariff_plan_code_from", "segment"]).size()
    prior = {}
    conversion_rates = []
    for (source, segment, target), row in grouped.iterrows():
        count = int(row["size"])
        conversion = count / int(totals.loc[(source, segment)])
        conversion_rates.append(conversion)
        # The model already shrinks rare transitions; avoid second shrinkage in
        # agent._candidates. Its prior uncertainty is still kept deliberately wide.
        prior[(source, segment, target)] = (float(row["mean"]), conversion, 100_000)
    fallback_conversion = float(np.median(conversion_rates)) if conversion_rates else 0.10
    return prior, fallback_conversion


def learned_priors(_tariffs: pd.DataFrame) -> tuple[dict, float]:
    return _trained_prior()
