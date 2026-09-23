"""Out-of-sample experiment for historical tariff-change priors.

This predicts the ARPU change of people who *already changed* tariffs. It does
not estimate campaign acceptance or the hidden judge's treatment effect.
Run with the same Python environment used for local_eval.py; no ML package is
required beyond the repository's NumPy/Pandas dependencies.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
SEGMENTS = ("LOW", "MID", "HIGH")


def load_data() -> tuple[pd.DataFrame, dict[str, float], float]:
    history = pd.read_csv(ROOT / "data" / "change_tariff.csv")
    history = history.loc[history["AVG_ARPU_PREV_3M"] >= 100].copy()
    history["segment"] = pd.cut(
        history["AVG_ARPU_PREV_3M"],
        bins=[-np.inf, 1000, 5000, np.inf],
        labels=SEGMENTS,
    ).astype(str)
    history["change_ratio"] = (
        (history["AVG_ARPU_NEXT_3M"] - history["AVG_ARPU_PREV_3M"])
        / history["AVG_ARPU_PREV_3M"]
    ).clip(-1.0, 3.0)
    tariffs = pd.read_csv(ROOT / "data" / "dict_tariff.csv")
    prices = dict(zip(tariffs["tariff_plan_code"], tariffs["price_tariff"]))
    scale = max(float(tariffs["price_tariff"].median()), 1.0)
    return history.reset_index(drop=True), prices, scale


def design_matrix(history: pd.DataFrame, prices: dict[str, float], scale: float) -> np.ndarray:
    """Fixed feature map, including pooled tariff and transition effects."""
    sources = sorted(prices)
    targets = sorted(prices)
    source = history["tariff_plan_code_from"].to_numpy()
    target = history["tariff_plan_code_to"].to_numpy()
    segment = history["segment"].to_numpy()
    source_price = np.array([prices.get(value, 0.0) for value in source]) / scale
    target_price = np.array([prices.get(value, 0.0) for value in target]) / scale
    log_arpu = np.log1p(history["AVG_ARPU_PREV_3M"].to_numpy())
    numeric = np.column_stack((
        np.ones(len(history)),
        (log_arpu - np.log1p(scale)) / 2.0,
        source_price,
        target_price,
        target_price - source_price,
    ))
    blocks = [numeric]
    for values, categories in ((source, sources), (target, targets), (segment, SEGMENTS)):
        blocks.append(np.column_stack([values == category for category in categories]).astype(float))
    # Pair effects can learn well-supported transitions; ridge shrinks rare ones.
    blocks.append(np.column_stack([
        (source == from_tariff) & (target == to_tariff)
        for from_tariff in sources for to_tariff in targets
    ]).astype(float))
    return np.column_stack(blocks)


def fit_ridge(x: np.ndarray, y: np.ndarray, alpha: float) -> np.ndarray:
    gram = x.T @ x
    penalty = np.eye(x.shape[1]) * alpha
    penalty[0, 0] = 0.0  # Do not shrink the intercept.
    return np.linalg.solve(gram + penalty, x.T @ y)


def historical_baseline(train: pd.DataFrame, test: pd.DataFrame,
                        prices: dict[str, float], scale: float) -> np.ndarray:
    """Existing agent's smoothed change estimate, without conversion rate."""
    key = ["tariff_plan_code_from", "segment", "tariff_plan_code_to"]
    means = train.groupby(key)["change_ratio"].agg(["mean", "size"])
    predicted = np.empty(len(test))
    for i, (source, segment, target) in enumerate(test[key].itertuples(index=False, name=None)):
        fallback = np.clip((prices[target] - prices[source]) / scale * 0.4, -1.0, 3.0)
        if (source, segment, target) in means.index:
            row = means.loc[(source, segment, target)]
            weight = row["size"] / (row["size"] + 30.0)
            predicted[i] = weight * row["mean"] + (1.0 - weight) * fallback
        else:
            predicted[i] = fallback
    return predicted


def evaluate() -> None:
    history, prices, scale = load_data()
    x = design_matrix(history, prices, scale)
    y = history["change_ratio"].to_numpy()
    # Keep repeat records of the same person in one fold.
    unique_ids = history["ID_NUMBER"].unique()
    rng = np.random.default_rng(2026)
    rng.shuffle(unique_ids)
    fold_by_id = {customer_id: i % 5 for i, customer_id in enumerate(unique_ids)}
    folds = history["ID_NUMBER"].map(fold_by_id).to_numpy()
    predictions = {"current_prior": np.empty(len(y))}
    for alpha in (10.0, 100.0, 1000.0):
        predictions[f"ridge_{alpha:g}"] = np.empty(len(y))

    for fold in range(5):
        train = folds != fold
        test = ~train
        predictions["current_prior"][test] = historical_baseline(
            history.loc[train], history.loc[test], prices, scale
        )
        for alpha in (10.0, 100.0, 1000.0):
            weights = fit_ridge(x[train], y[train], alpha)
            predictions[f"ridge_{alpha:g}"][test] = x[test] @ weights

    print(f"Historical switchers: {len(y):,}; held-out evaluation: 5 ID-grouped folds")
    print("This metric predicts change after switching, NOT campaign response or hidden score.")
    print("model             MAE       RMSE       bias")
    for name, predicted in predictions.items():
        error = predicted - y
        print(f"{name:15} {np.abs(error).mean():.4f}    {np.sqrt(np.mean(error ** 2)):.4f}    {error.mean():+.4f}")


if __name__ == "__main__":
    evaluate()
