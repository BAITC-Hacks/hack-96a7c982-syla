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
INITIAL_PILOTS = 12
FOLLOWUP_PILOTS = 8
INITIAL_PILOT_SIZE = 150
FOLLOWUP_PILOT_SIZE = 200
FINAL_CONTACT_RESERVE = 11_000
RISK_LAMBDA = 0.75  # risk-adjusted utility: mean - lambda * posterior std



def _affordable_contacts(budget: float, unit_cost: float, contact_cap: int) -> int:
    """Return a safe contact count for finite, infinite, NaN, or negative budgets."""
    if contact_cap <= 0:
        return 0
    if unit_cost <= 0:
        return contact_cap
    try:
        budget = float(budget)
    except (TypeError, ValueError, OverflowError):
        return 0
    if math.isnan(budget) or budget <= 0:
        return 0
    if math.isinf(budget):
        return contact_cap if budget > 0 else 0
    return max(0, min(contact_cap, int(budget // unit_cost)))


def _historical_priors(tariffs: pd.DataFrame) -> tuple[dict, float]:
    """Estimate coarse transition priors from the supplied *other* population."""
    path = Path(__file__).resolve().parent / "data" / "change_tariff.csv"
    if not path.exists():
        return {}, 0.10

    try:
        history = pd.read_csv(path)
        required = {"AVG_ARPU_PREV_3M", "AVG_ARPU_NEXT_3M",
                    "tariff_plan_code_from", "tariff_plan_code_to"}
        if not required.issubset(history.columns):
            return {}, 0.10
    except (OSError, ValueError, pd.errors.ParserError):
        return {}, 0.10
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
    ).copy()
    profile["predicted_arpu"] = pd.to_numeric(
        profile["predicted_arpu"], errors="coerce"
    )
    profile = profile[
        profile["predicted_arpu"].notna()
        & profile["predicted_arpu"].map(math.isfinite)
        & (profile["predicted_arpu"] >= 0)
    ]
    tariffs = env.tariffs.dropna(subset=["tariff_plan_code"])
    prices = dict(zip(tariffs["tariff_plan_code"], tariffs["price_tariff"]))
    scale = max(float(tariffs["price_tariff"].median()), 1.0)
    historical, fallback_conversion = _historical_priors(tariffs)
    sms_multiplier = env.channels["sms"]["conversion_multiplier"]
    sms_cost = env.channels["sms"]["cost_per_contact"]

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
            # A small exploration bonus keeps valuable uncertain groups in play.
            rank = arpu_sum * (max(prior_mean, 0.0) + 0.025) - contactable * sms_cost
            ranked.append({
                "filter_current_tariff": source,
                "filter_arpu_segment": segment,
                "target_tariff": target,
                "audience_size": n,
                "arpu_sum": arpu_sum,
                "arpu_prefix": arpu_prefix,
                "prior_lift_ratio": prior_mean,
                "historical_conversion": (
                    float(entry[1]) if entry is not None else float(fallback_conversion)
                ),
                "precision": 1.0 / (PRIOR_STD ** 2),
                "weighted_lift": prior_mean / (PRIOR_STD ** 2),
                "pilot_count": 0,
                "pilot_customers": 0,
                "is_rejected": False,
                "rank": rank,
            })

    ranked.sort(key=lambda c: c["rank"], reverse=True)

    # Explore both audience choice AND target-tariff choice. The old version kept
    # only one target per (current tariff, ARPU) cell, which made the historical
    # prior irreversible: pilots could confirm/reject that target but could never
    # discover that another target is better on the hidden audience.
    selected = []
    small = []
    first_by_cell = {}
    second_by_cell = {}
    small_seen = set()
    for candidate in ranked:
        cell = (candidate["filter_current_tariff"], candidate["filter_arpu_segment"])
        if candidate["audience_size"] < INITIAL_PILOT_SIZE:
            if cell not in small_seen:
                small.append(candidate)
                small_seen.add(cell)
            continue
        if cell not in first_by_cell:
            first_by_cell[cell] = candidate
        elif cell not in second_by_cell:
            second_by_cell[cell] = candidate

    # First buy breadth: one hypothesis from the highest-value distinct cells.
    first = sorted(first_by_cell.values(), key=lambda c: c["rank"], reverse=True)
    breadth = min(8, INITIAL_PILOTS)
    selected.extend(first[:breadth])

    # Then buy target discrimination inside those valuable cells. This is robust
    # to the explicit case requirement that hidden effects differ from history.
    selected_cells = {
        (c["filter_current_tariff"], c["filter_arpu_segment"]) for c in selected
    }
    alternatives = [
        c for cell, c in second_by_cell.items() if cell in selected_cells
    ]
    alternatives.sort(key=lambda c: c["rank"], reverse=True)
    selected.extend(alternatives[: max(0, INITIAL_PILOTS - len(selected))])

    # If too few second arms exist, fill with additional distinct cells.
    if len(selected) < INITIAL_PILOTS:
        used = {id(c) for c in selected}
        for candidate in first[breadth:]:
            if id(candidate) not in used:
                selected.append(candidate)
                used.add(id(candidate))
                if len(selected) >= INITIAL_PILOTS:
                    break
    return selected, small


def _posterior(candidate: dict) -> tuple[float, float]:
    return (
        candidate["weighted_lift"] / candidate["precision"],
        math.sqrt(1.0 / candidate["precision"]),
    )


def _run_pilot(env, candidate: dict, requested_size: int) -> bool:
    if env.pilots_left <= 0 or candidate.get("is_rejected", False):
        return False
    size = min(requested_size, candidate["audience_size"])
    pilot_channel = "sms" if "sms" in env.channels else min(
        env.channels, key=lambda ch: env.channels[ch]["cost_per_contact"]
    )
    cost = size * env.channels[pilot_channel]["cost_per_contact"]
    if size < 10 or env.remaining_contacts - size < FINAL_CONTACT_RESERVE:
        return False
    if env.remaining_budget < cost:
        return False
    try:
        result = env.run_pilot(
            target_tariff=candidate["target_tariff"],
            channel=pilot_channel,
            n_customers=size,
            filter_current_tariff=candidate["filter_current_tariff"],
            filter_arpu_segment=candidate["filter_arpu_segment"],
        )
    except (RuntimeError, ValueError):
        return False
    try:
        observed = float(result["observed_lift_ratio"])
        actual_size = int(result["n_customers"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    if not math.isfinite(observed) or actual_size <= 0:
        return False
    # observed_lift_ratio is measured after the selected channel multiplier.
    # The environment adds sampling noise with PER_CUSTOMER_STD/sqrt(n), so use
    # that exact observation scale here (do not rescale by tariff economics).
    precision = actual_size / (PILOT_STD_PER_CUSTOMER ** 2)
    candidate["precision"] += precision
    candidate["weighted_lift"] += observed * precision
    candidate["pilot_count"] += 1
    candidate["pilot_customers"] = candidate.get("pilot_customers", 0) + actual_size
    mean, std = _posterior(candidate)
    if mean + std < 0.0:
        candidate["is_rejected"] = True
    return True


def _explore(env, candidates: list[dict]) -> None:
    if env.pilots_left <= 0 or env.remaining_contacts <= 0 or env.remaining_budget < 0:
        return
    for candidate in candidates:
        if not _run_pilot(env, candidate, INITIAL_PILOT_SIZE):
            break

    for _ in range(FOLLOWUP_PILOTS):
        eligible = []
        for candidate in candidates:
            mean, std = _posterior(candidate)
            if candidate["pilot_count"] < 1 or mean < -std:
                continue
            # Sequential exploration: recompute after every result. Prefer uncertain
            # high-value cells, but discount repeatedly sampled cells so pilots spread
            # unless one decision is genuinely close and valuable.
            # Information matters most near the launch/no-launch boundary.
            # A clearly positive arm needs less confirmation than an ambiguous one.
            boundary_weight = math.exp(-abs(mean) / max(2.0 * std, 1e-9))
            value_of_information = (
                candidate["arpu_sum"] * std * (0.35 + 0.65 * boundary_weight)
                / math.sqrt(candidate["pilot_count"])
            )
            eligible.append((value_of_information, candidate))
        if not eligible:
            break
        candidate = max(eligible, key=lambda item: item[0])[1]
        if not _run_pilot(env, candidate, FOLLOWUP_PILOT_SIZE):
            break


def _plan(env, candidates: list[dict]) -> list[dict]:
    remaining_contacts = max(0, int(env.remaining_contacts))
    try:
        remaining_budget = float(env.remaining_budget)
    except (TypeError, ValueError, OverflowError):
        remaining_budget = 0.0
    if math.isnan(remaining_budget) or remaining_budget < 0:
        remaining_budget = 0.0
    if remaining_contacts <= 0:
        return []
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
            for channel in ("push", "sms", "digital_ads", "call"):
                if channel not in env.channels:
                    continue
                channel_info = env.channels[channel]
                unit_cost = channel_info["cost_per_contact"]
                affordable = _affordable_contacts(
                    remaining_budget, unit_cost, remaining_contacts
                )
                n = min(candidate["audience_size"], 5000, remaining_contacts, affordable)
                if n <= 0:
                    continue
                # The final campaign has no n_customers parameter. The scorer
                # takes this exact ID-sorted prefix when a limit truncates it.
                arpu_sum = float(candidate["arpu_prefix"][n - 1])
                # Conversion is capped at 1.0. A naive multiplier ratio can
                # overstate digital/call when the underlying conversion is already high.
                hist_conv = max(float(candidate.get("historical_conversion", 0.0)), 0.0)
                sms_eff = min(hist_conv * env.channels["sms"]["conversion_multiplier"], 1.0)
                ch_eff = min(hist_conv * channel_info["conversion_multiplier"], 1.0)
                if sms_eff > 1e-9:
                    factor = ch_eff / sms_eff
                else:
                    factor = channel_info["conversion_multiplier"] / env.channels["sms"]["conversion_multiplier"]
                mean = mean_sms * factor
                std = std_sms * factor
                cost = n * unit_cost
                expected_net = arpu_sum * mean - cost
                # Unpiloted small groups need stronger evidence than piloted cells.
                penalty = 1.0 if not candidate["pilot_count"] else RISK_LAMBDA
                risk_adjusted_lift = mean - penalty * std
                cautious_net = arpu_sum * risk_adjusted_lift - cost
                # Prefer value per scarce contact as a tie-breaker, while absolute
                # cautious net remains the primary objective.
                cautious_per_contact = cautious_net / max(n, 1)
                options.append((cautious_net, expected_net, cautious_per_contact,
                                candidate, channel, n, cost))
        if not options:
            break
        # Bounded-knapsack heuristic: early on favor total value; as contacts become
        # scarce, increasingly favor risk-adjusted value per contact to avoid a large
        # mediocre segment crowding out several small high-return cells.
        scarcity = 1.0 - min(1.0, remaining_contacts / max(float(env.remaining_contacts), 1.0))
        cautious_net, _, _, candidate, channel, n, cost = max(
            options,
            key=lambda item: (
                (1.0 - scarcity) * item[0]
                + scarcity * item[2] * min(remaining_contacts, 5000),
                item[2], item[1],
            ),
        )
        # Never launch a final campaign whose risk-adjusted value is non-positive.
        # Pilots already count in scoring, so forcing a first bad campaign only burns
        # contacts/budget and can reduce the final result.
        if cautious_net <= 0:
            break
        cell = (candidate["filter_current_tariff"], candidate["filter_arpu_segment"])
        campaigns.append({
            "campaign_name": f"main_{candidate['filter_current_tariff']}_{candidate['filter_arpu_segment']}_{candidate['target_tariff']}",
            "filter_current_tariff": candidate["filter_current_tariff"],
            "filter_arpu_segment": candidate["filter_arpu_segment"],
            "target_tariff": candidate["target_tariff"],
            "channel": channel,
            # UI/report contract. The scorer ignores these extra fields.
            "estimated_group_size": int(n),
            "expected_net_effect": float(expected_net),
            "uncertainty": float(std),
            "confidence_interval_95": [
                float(mean - 1.96 * std), float(mean + 1.96 * std)
            ],
            "risk_adjusted_lift": float(mean - RISK_LAMBDA * std),
            "pilots_used": int(candidate["pilot_count"]),
            "pilot_customers": int(candidate.get("pilot_customers", 0)),
        })
        chosen_cells.add(cell)
        remaining_contacts -= n
        remaining_budget -= cost
    return campaigns


def _fallback_agent(env) -> list[dict]:
    """Minimal legal strategy if model/prior construction fails unexpectedly."""
    try:
        profile = env.customer_profile.dropna(
            subset=["current_tariff", "arpu_segment", "predicted_arpu"]
        )
        tariffs = list(env.tariffs["tariff_plan_code"].dropna())
        if profile.empty or not tariffs:
            return []
        # Pick a large/high-value cell and a different target. One push pilot is
        # free in money terms; use its sign to avoid blindly launching.
        grouped = []
        for (source, segment), members in profile.groupby(
            ["current_tariff", "arpu_segment"], observed=True
        ):
            if len(members) >= 10:
                grouped.append((float(members["predicted_arpu"].sum()), source, segment, len(members)))
        if not grouped:
            return []
        _, source, segment, n = max(grouped)
        targets = [t for t in tariffs if t != source]
        if not targets:
            return []
        prices = dict(zip(env.tariffs["tariff_plan_code"], env.tariffs["price_tariff"]))
        target = max(targets, key=lambda t: prices.get(t, 0.0))
        pilot_n = min(200, n)
        result = env.run_pilot(
            target_tariff=target, channel="push", n_customers=pilot_n,
            filter_current_tariff=source, filter_arpu_segment=segment,
        )
        if float(result.get("observed_lift_ratio", 0.0)) <= 0:
            return []
        return [{
            "campaign_name": "fallback_positive_pilot",
            "filter_current_tariff": source,
            "filter_arpu_segment": segment,
            "target_tariff": target,
            "channel": "push",
            "estimated_group_size": int(min(n, 5000, env.remaining_contacts)),
            "expected_net_effect": None,
            "uncertainty": PILOT_STD_PER_CUSTOMER / math.sqrt(max(pilot_n, 1)),
            "pilots_used": 1,
        }]
    except Exception:
        return []


class Agent:
    def act(self, env) -> list[dict]:
        try:
            piloted_candidates, small_candidates = _candidates(env)
            if not piloted_candidates:
                return _fallback_agent(env)
            _explore(env, piloted_candidates)
            return _plan(env, piloted_candidates + small_candidates)
        except Exception:
            # Requirement: remain operational even if the prior/model cannot load.
            return _fallback_agent(env)
