"""Pilot-driven tariff campaign planner for the HackAlem Beeline case.

Only the public environment interface and the supplied historical data are used.
Historical transitions provide a broad prior; pilots decide the final plan.
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd


PILOT_STD_PER_CUSTOMER = 0.804
PRIOR_STD = 0.18  # Keep the historical prior weak: the target audience differs.
INITIAL_PILOTS = 16
FOLLOWUP_PILOTS = 4
INITIAL_PILOT_SIZE = 150
FOLLOWUP_PILOT_SIZE = 200
FINAL_CONTACT_RESERVE = 11_000
TARGET_PROMISING_CANDIDATES = 11


def _historical_priors(tariffs: pd.DataFrame) -> tuple[dict, float]:
    """Estimate coarse transition priors from the supplied *other* population."""
    path = Path(__file__).resolve().parent / "data" / "change_tariff.csv"
    if not path.exists():
        return {}, 0.10

    history = pd.read_csv(path)
    history = history.loc[history["AVG_ARPU_PREV_3M"] >= 100].copy()
    history["arpu_segment"] = pd.cut(
        history["AVG_ARPU_PREV_3M"],
        bins=[-math.inf, 1000, 5000, math.inf],
        labels=["LOW", "MID", "HIGH"],
    ).astype(str)
    history["change_ratio"] = (
        (history["AVG_ARPU_NEXT_3M"] - history["AVG_ARPU_PREV_3M"])
        / history["AVG_ARPU_PREV_3M"]
    ).clip(-1.0, 3.0)

    grouped = history.groupby(
        ["tariff_plan_code_from", "tariff_plan_code_to", "arpu_segment"],
        observed=True,
    )["change_ratio"].agg(["mean", "size"])
    totals = history.groupby(
        ["tariff_plan_code_from", "arpu_segment"], observed=True
    ).size()
    prior = {}
    conversion_rates = []
    for (source, target, segment), row in grouped.iterrows():
        count = int(row["size"])
        total = int(totals.loc[(source, segment)])
        conversion = count / total
        conversion_rates.append(conversion)
        prior[(source, segment, target)] = (
            float(row["mean"]), conversion, count
        )
    fallback_conversion = float(pd.Series(conversion_rates).median()) if conversion_rates else 0.10
    return prior, fallback_conversion


def _candidates(env) -> tuple[list[dict], list[dict]]:
    profile = env.customer_profile.dropna(
        subset=["current_tariff", "arpu_segment", "predicted_arpu"]
    )
    tariffs = env.tariffs.dropna(subset=["tariff_plan_code"])
    prices = dict(zip(tariffs["tariff_plan_code"], tariffs["price_tariff"]))
    scale = max(float(tariffs["price_tariff"].median()), 1.0)
    historical, fallback_conversion = _historical_priors(tariffs)
    sms_multiplier = env.channels["sms"]["conversion_multiplier"]

    cells = profile.groupby(["current_tariff", "arpu_segment"], observed=True)
    ranked = []
    for (source, segment), members in cells:
        n = len(members)
        if n < 10:
            continue
        # The scorer contacts the lowest IDs first and caps each campaign at 5000.
        arpu_prefix = members.sort_values("ID_NUMBER")["predicted_arpu"].cumsum().to_numpy()
        contactable = min(n, 5000)
        arpu_sum = float(arpu_prefix[contactable - 1])
        for target in prices:
            if source == target:
                continue
            fallback_change = max(-1.0, min(3.0, (prices[target] - prices[source]) / scale * 0.4))
            fallback_ratio = fallback_change * fallback_conversion * sms_multiplier
            entry = historical.get((source, segment, target))
            if entry is None:
                prior_mean = fallback_ratio
            else:
                change, conversion, count = entry
                history_ratio = change * min(conversion * sms_multiplier, 1.0)
                weight = count / (count + 30.0)
                prior_mean = weight * history_ratio + (1.0 - weight) * fallback_ratio
            prior_mean = max(-0.5, min(0.75, prior_mean))
            rank = arpu_sum * prior_mean
            ranked.append({
                "filter_current_tariff": source,
                "filter_arpu_segment": segment,
                "target_tariff": target,
                "audience_size": n,
                "arpu_sum": arpu_sum,
                "arpu_prefix": arpu_prefix,
                "prior_lift_ratio": prior_mean,
                "precision": 1.0 / (PRIOR_STD ** 2),
                "weighted_lift": prior_mean / (PRIOR_STD ** 2),
                "pilot_count": 0,
                "is_rejected": False,
                "rank": rank,
            })

    ranked.sort(key=lambda c: c["rank"], reverse=True)
    # Distinct cells make the final campaigns disjoint and spread exploration.
    selected = []
    small = []
    seen_cells = set()
    for candidate in ranked:
        cell = (candidate["filter_current_tariff"], candidate["filter_arpu_segment"])
        if cell in seen_cells:
            continue
        seen_cells.add(cell)
        if candidate["audience_size"] < INITIAL_PILOT_SIZE:
            small.append(candidate)
        elif len(selected) < INITIAL_PILOTS:
            selected.append(candidate)
    return selected, small


def _posterior(candidate: dict) -> tuple[float, float]:
    precision = candidate["precision"]
    if precision <= 0:
        raise ValueError("Posterior precision must be positive")
    return (
        candidate["weighted_lift"] / precision,
        math.sqrt(1.0 / precision),
    )


def _run_pilot(env, candidate: dict, requested_size: int) -> bool:
    if env.pilots_left <= 0 or candidate.get("is_rejected", False):
        return False
    size = min(requested_size, candidate["audience_size"])
    cost = size * env.channels["sms"]["cost_per_contact"]
    if size < 10 or env.remaining_contacts - size < FINAL_CONTACT_RESERVE:
        return False
    if env.remaining_budget < cost:
        return False
    try:
        result = env.run_pilot(
            target_tariff=candidate["target_tariff"],
            channel="sms",
            n_customers=size,
            filter_current_tariff=candidate["filter_current_tariff"],
            filter_arpu_segment=candidate["filter_arpu_segment"],
        )
    except (RuntimeError, ValueError):
        return False
    observed = float(result["observed_lift_ratio"])
    actual_size = int(result["n_customers"])
    if not math.isfinite(observed) or actual_size <= 0:
        return False
    precision = actual_size / (PILOT_STD_PER_CUSTOMER ** 2)
    candidate["precision"] += precision
    candidate["weighted_lift"] += observed * precision
    candidate["pilot_count"] += 1
    if candidate["pilot_count"] == 1:
        mean, std = _posterior(candidate)
        candidate["is_rejected"] = mean + std < 0.0
    return True


def _followup_value(env, candidate: dict) -> float:
    """Approximate value of a new observation for the launch/no-launch choice."""
    size = min(FOLLOWUP_PILOT_SIZE, candidate["audience_size"])
    unit_cost = env.channels["sms"]["cost_per_contact"]
    pilot_cost = size * unit_cost
    if (size < 10 or env.pilots_left <= 0
            or env.remaining_contacts - size < FINAL_CONTACT_RESERVE
            or env.remaining_budget < pilot_cost):
        return -math.inf

    final_n = min(
        candidate["audience_size"], 5000, env.remaining_contacts - size,
        int((env.remaining_budget - pilot_cost) // unit_cost),
    )
    if final_n <= 0:
        return -math.inf
    arpu_sum = float(candidate["arpu_prefix"][final_n - 1])
    mean, std = _posterior(candidate)
    new_precision = candidate["precision"] + size / (PILOT_STD_PER_CUSTOMER ** 2)
    new_std = math.sqrt(1.0 / new_precision)
    # Across possible pilot results, the posterior mean itself varies by this SD.
    decision_sd = arpu_sum * math.sqrt(max(0.0, std * std - new_std * new_std))
    if decision_sd <= 0:
        return -math.inf
    margin = arpu_sum * mean - final_n * unit_cost
    z = margin / decision_sd
    normal_pdf = math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)
    normal_cdf = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    value_of_information = (
        decision_sd * normal_pdf + margin * normal_cdf - max(0.0, margin)
    )
    return value_of_information - pilot_cost


def _explore(env, candidates: list[dict]) -> None:
    promising = 0
    for candidate in candidates:
        if (promising >= TARGET_PROMISING_CANDIDATES
                or env.pilots_left <= FOLLOWUP_PILOTS):
            break
        if not _run_pilot(env, candidate, INITIAL_PILOT_SIZE):
            break
        if candidate.get("is_rejected", False):
            continue
        mean, std = _posterior(candidate)
        n = min(candidate["audience_size"], 5000)
        cost = n * env.channels["sms"]["cost_per_contact"]
        if (mean - 0.5 * std) * candidate["arpu_sum"] > cost:
            promising += 1

    for _ in range(FOLLOWUP_PILOTS):
        eligible = (
            candidate for candidate in candidates
            if candidate["pilot_count"] >= 1 and not candidate.get("is_rejected", False)
        )
        best = max(
            ((_followup_value(env, candidate), candidate) for candidate in eligible),
            key=lambda item: item[0], default=None,
        )
        if best is None or best[0] <= 0:
            break
        if not _run_pilot(env, best[1], FOLLOWUP_PILOT_SIZE):
            break


def _plan(env, candidates: list[dict]) -> list[dict]:
    remaining_contacts = env.remaining_contacts
    remaining_budget = env.remaining_budget
    chosen_cells = set()
    campaigns = []
    while len(campaigns) < 10 and remaining_contacts > 0:
        options = []
        for candidate in candidates:
            if (candidate.get("is_rejected", False)
                    or (candidate["pilot_count"] == 0
                        and candidate["audience_size"] >= INITIAL_PILOT_SIZE)):
                continue
            cell = (candidate["filter_current_tariff"], candidate["filter_arpu_segment"])
            if cell in chosen_cells:
                continue
            mean_sms, std_sms = _posterior(candidate)
            for channel in ("push", "sms"):
                channel_info = env.channels[channel]
                unit_cost = channel_info["cost_per_contact"]
                affordable = remaining_contacts if unit_cost == 0 else int(remaining_budget // unit_cost)
                n = min(candidate["audience_size"], 5000, remaining_contacts, affordable)
                if n <= 0:
                    continue
                # The final campaign has no n_customers parameter. The scorer
                # takes this exact ID-sorted prefix when a limit truncates it.
                arpu_sum = float(candidate["arpu_prefix"][n - 1])
                factor = channel_info["conversion_multiplier"] / env.channels["sms"]["conversion_multiplier"]
                mean = mean_sms * factor
                std = std_sms * factor
                cost = n * unit_cost
                expected_net = arpu_sum * mean - cost
                # Unpiloted small groups need stronger evidence than piloted cells.
                penalty = 1.0 if not candidate["pilot_count"] else 0.5
                cautious_net = arpu_sum * (mean - penalty * std) - cost
                options.append((cautious_net, expected_net, candidate, channel, n, cost))
        if not options:
            break
        cautious_net, _, candidate, channel, n, cost = max(options, key=lambda item: (item[0], item[1]))
        if cautious_net <= 0 and campaigns:
            break
        cell = (candidate["filter_current_tariff"], candidate["filter_arpu_segment"])
        campaigns.append({
            "campaign_name": f"main_{candidate['filter_current_tariff']}_{candidate['filter_arpu_segment']}_{candidate['target_tariff']}",
            "filter_current_tariff": candidate["filter_current_tariff"],
            "filter_arpu_segment": candidate["filter_arpu_segment"],
            "target_tariff": candidate["target_tariff"],
            "channel": channel,
        })
        chosen_cells.add(cell)
        remaining_contacts -= n
        remaining_budget -= cost
    return campaigns


class Agent:
    def act(self, env) -> list[dict]:
        piloted_candidates, small_candidates = _candidates(env)
        if not piloted_candidates:
            return []
        _explore(env, piloted_candidates)
        return _plan(env, piloted_candidates + small_candidates)
