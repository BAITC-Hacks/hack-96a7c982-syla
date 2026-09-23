"""Focused tests for planning limits; no hidden environment data is used."""

import unittest
import math
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from agent import (Agent, PILOT_STD_PER_CUSTOMER, _affordable_contacts, _candidates,
                   _explore, _followup_value, _historical_priors, _plan, _posterior, _run_pilot)


def candidate(source, audience, mean=0.30):
    return {
        "filter_current_tariff": source,
        "filter_arpu_segment": "MID",
        "target_tariff": "tariff_8",
        "audience_size": audience,
        "arpu_prefix": [1000.0 * (i + 1) for i in range(audience)],
        "precision": 100.0,
        "weighted_lift": 100.0 * mean,
        "pilot_count": 1,
    }


class PlanningLimitsTest(unittest.TestCase):
    def setUp(self):
        self.channels = {
            "push": {"cost_per_contact": 0, "conversion_multiplier": 0.50},
            "sms": {"cost_per_contact": 4, "conversion_multiplier": 0.65},
        }

    def test_keeps_profitable_partial_second_campaign(self):
        env = SimpleNamespace(
            channels=self.channels, remaining_contacts=150, remaining_budget=1000
        )
        campaigns = _plan(env, [candidate("tariff_1", 120), candidate("tariff_2", 50)])
        self.assertEqual(2, len(campaigns))
        self.assertEqual("tariff_1", campaigns[0]["filter_current_tariff"])
        self.assertEqual("tariff_2", campaigns[1]["filter_current_tariff"])

    def test_budget_can_partially_limit_paid_campaign(self):
        channels = {
            "push": {"cost_per_contact": 0, "conversion_multiplier": 0.01},
            "sms": {"cost_per_contact": 4, "conversion_multiplier": 0.65},
        }
        env = SimpleNamespace(
            channels=channels, remaining_contacts=200, remaining_budget=40
        )
        campaigns = _plan(env, [candidate("tariff_1", 120)])
        self.assertEqual(1, len(campaigns))
        self.assertEqual("sms", campaigns[0]["channel"])

    def test_repeated_pilots_accumulate_precision_and_weight(self):
        observations = iter([(0.20, 150), (0.10, 200)])
        env = SimpleNamespace(
            channels=self.channels, remaining_contacts=15_000,
            remaining_budget=100_000, pilots_left=20,
        )

        def run_pilot(**_kwargs):
            observed, size = next(observations)
            env.remaining_contacts -= size
            env.remaining_budget -= size * 4
            env.pilots_left -= 1
            return {"observed_lift_ratio": observed, "n_customers": size}

        env.run_pilot = run_pilot
        item = candidate("tariff_1", 300)
        item["pilot_count"] = 0
        self.assertTrue(_run_pilot(env, item, 150))
        self.assertTrue(_run_pilot(env, item, 200))
        added_precision = (150 + 200) / (PILOT_STD_PER_CUSTOMER ** 2)
        self.assertAlmostEqual(100.0 + added_precision, item["precision"])
        self.assertAlmostEqual(
            30.0 + (150 * 0.20 + 200 * 0.10) / (PILOT_STD_PER_CUSTOMER ** 2),
            item["weighted_lift"],
        )
        mean, std = _posterior(item)
        self.assertTrue(math.isfinite(mean) and math.isfinite(std) and std > 0)
        self.assertEqual(2, item["pilot_count"])

    def test_does_not_force_a_loss_making_first_campaign(self):
        env = SimpleNamespace(channels=self.channels, remaining_contacts=200,
                              remaining_budget=1000)
        for mean in (-0.20, 0.05):
            with self.subTest(mean=mean):
                self.assertEqual([], _plan(env, [candidate("tariff_1", 120, mean)]))

    def test_nonfinite_budget_keeps_free_push_but_does_not_crash(self):
        channels = {
            "push": {"cost_per_contact": 0, "conversion_multiplier": 0.01},
            "sms": {"cost_per_contact": 4, "conversion_multiplier": 0.65},
        }
        for budget, expected in ((float("inf"), "sms"), (float("nan"), "push"),
                                 (-5, "push"), (0, "push"), ("1000", "sms"),
                                 ("invalid", "push")):
            with self.subTest(budget=budget):
                env = SimpleNamespace(channels=channels, remaining_contacts=200,
                                      remaining_budget=budget)
                plan = _plan(env, [candidate("tariff_1", 120)])
                self.assertEqual(expected, plan[0]["channel"])

    def test_affordability_rejects_invalid_channel_costs(self):
        for cost in (-1, float("inf"), float("nan")):
            self.assertEqual(0, _affordable_contacts(1000, cost, 100))
        self.assertEqual(100, _affordable_contacts(0, 0, 100))
        self.assertEqual(25, _affordable_contacts(100, 4, 100))
        self.assertEqual(0, _affordable_contacts("invalid", 4, 100))

    def test_small_only_pool_is_not_discarded(self):
        item = candidate("tariff_1", 20)
        item["pilot_count"] = 0
        with patch("agent._candidates", return_value=([], [item])):
            env = SimpleNamespace(channels=self.channels, remaining_contacts=200,
                                  remaining_budget=1000)
            self.assertEqual(1, len(Agent().act(env)))

    def test_invalid_arpu_skips_whole_cell_to_preserve_scorer_prefix(self):
        for bad in ("bad", float("inf"), float("nan"), -5):
            with self.subTest(bad=bad):
                profile = pd.DataFrame({
                    "ID_NUMBER": range(21),
                    "current_tariff": ["tariff_1"] * 11 + ["tariff_2"] * 10,
                    "arpu_segment": ["MID"] * 21,
                    "predicted_arpu": [bad] + [1000] * 20,
                })
                tariffs = pd.DataFrame({
                    "tariff_plan_code": ["tariff_1", "tariff_2", "tariff_8"],
                    "price_tariff": [1000, 1500, 2000],
                })
                env = SimpleNamespace(customer_profile=profile, tariffs=tariffs,
                                      channels=self.channels)
                large, small = _candidates(env, prior_provider=lambda _: ({}, 0.1))
                self.assertEqual([], large)
                self.assertEqual(["tariff_2"], [c["filter_current_tariff"] for c in small])
                self.assertEqual(10, small[0]["audience_size"])

    def test_bad_history_falls_back_without_mutating_inputs(self):
        self.assertEqual(({}, 0.1), _historical_priors(pd.DataFrame(), pd.DataFrame()))
        history = pd.DataFrame({
            "tariff_plan_code_from": ["tariff_1"] * 3,
            "tariff_plan_code_to": ["tariff_8"] * 3,
            "AVG_ARPU_PREV_3M": [1200, "bad", 1000],
            "AVG_ARPU_NEXT_3M": [1800, 2000, float("inf")],
        })
        original = history.copy(deep=True)
        prior, _ = _historical_priors(pd.DataFrame(), history)
        self.assertEqual((0.5, 1.0, 1), prior[("tariff_1", "MID", "tariff_8")])
        pd.testing.assert_frame_equal(original, history)

    def test_malformed_pilot_does_not_pollute_posterior(self):
        item = candidate("tariff_1", 300)
        item["pilot_count"] = 0
        original = dict(item)
        env = SimpleNamespace(channels=self.channels, remaining_contacts=15_000,
                              remaining_budget=100_000, pilots_left=20)
        cases = [{}, None, {"observed_lift_ratio": "bad", "n_customers": 150}]
        cases += [{"observed_lift_ratio": 0.1, "n_customers": n}
                  for n in (0, -1, 151, 100.5, float("nan"), float("inf"))]
        cases += [{"observed_lift_ratio": value, "n_customers": 150}
                  for value in (float("nan"), float("inf"), 1e308)]
        for result in cases:
            with self.subTest(result=result):
                env.run_pilot = lambda **kwargs: result
                self.assertFalse(_run_pilot(env, item, 150))
                self.assertEqual(original, item)

    def test_invalid_budget_does_not_spend_on_pilot(self):
        env = SimpleNamespace(channels=self.channels, remaining_contacts=15_000,
                              remaining_budget=float("nan"), pilots_left=20)
        env.run_pilot = lambda **kwargs: self.fail("Invalid budget must not launch pilot")
        self.assertFalse(_run_pilot(env, candidate("tariff_1", 300), 150))
        self.assertEqual(-math.inf, _followup_value(env, candidate("tariff_1", 300)))

    def test_free_sms_followup_does_not_divide_by_zero(self):
        channels = dict(self.channels, sms={"cost_per_contact": 0, "conversion_multiplier": 0.65})
        env = SimpleNamespace(channels=channels, remaining_contacts=15_000,
                              remaining_budget=0, pilots_left=20)
        self.assertTrue(math.isfinite(_followup_value(env, candidate("tariff_1", 300, 0.004))))

    def test_posterior_rejects_invalid_precision(self):
        for precision in (0, -1, float("nan"), float("inf")):
            item = candidate("tariff_1", 300)
            item["precision"] = precision
            with self.subTest(precision=precision), self.assertRaises(ValueError):
                _posterior(item)

    def test_exploration_continues_after_one_malformed_pilot(self):
        env = SimpleNamespace(channels=self.channels, remaining_contacts=15_000,
                              remaining_budget=100_000, pilots_left=20)
        results = iter([None, {"observed_lift_ratio": 0.3, "n_customers": 150}])
        env.run_pilot = lambda **kwargs: next(results)
        items = [candidate("tariff_1", 300), candidate("tariff_2", 300)]
        for item in items:
            item["pilot_count"] = 0
            item["arpu_sum"] = item["arpu_prefix"][-1]
        with patch("agent._followup_value", return_value=-math.inf):
            _explore(env, items)
        self.assertEqual([0, 1], [c["pilot_count"] for c in items])

    def test_ads_is_selected_only_when_incremental_lift_pays_for_it(self):
        channels = dict(self.channels,
                        digital_ads={"cost_per_contact": 22, "conversion_multiplier": 0.85},
                        call={"cost_per_contact": 0, "conversion_multiplier": 1.20})
        env = SimpleNamespace(channels=channels, remaining_contacts=200,
                              remaining_budget=100_000)
        # No direct call evidence: even a free call is not extrapolated past cap.
        high = _plan(env, [candidate("tariff_1", 120, mean=0.30)])
        low = _plan(env, [candidate("tariff_1", 120, mean=0.12)])
        self.assertEqual("digital_ads", high[0]["channel"])
        self.assertEqual("sms", low[0]["channel"])

    def test_missing_optional_channels_does_not_break_sms(self):
        env = SimpleNamespace(channels={"sms": self.channels["sms"]},
                              remaining_contacts=200, remaining_budget=1000)
        plan = _plan(env, [candidate("tariff_1", 120)])
        self.assertEqual("sms", plan[0]["channel"])

    def test_rejected_candidate_is_not_planned(self):
        env = SimpleNamespace(
            channels=self.channels, remaining_contacts=200, remaining_budget=1000
        )
        item = candidate("tariff_1", 120)
        item["is_rejected"] = True
        self.assertEqual([], _plan(env, [item]))

    def test_unpiloted_large_candidate_is_not_planned(self):
        env = SimpleNamespace(
            channels=self.channels, remaining_contacts=500, remaining_budget=1000
        )
        item = candidate("tariff_1", 300)
        item["pilot_count"] = 0
        self.assertEqual([], _plan(env, [item]))

    def test_negative_first_pilot_is_rejected_and_not_retested(self):
        calls = []
        env = SimpleNamespace(
            channels=self.channels, remaining_contacts=15_000,
            remaining_budget=100_000, pilots_left=20,
        )

        def run_pilot(**_kwargs):
            calls.append(1)
            return {"observed_lift_ratio": -1.0, "n_customers": 150}

        env.run_pilot = run_pilot
        item = candidate("tariff_1", 300)
        item["pilot_count"] = 0
        self.assertTrue(_run_pilot(env, item, 150))
        self.assertTrue(item["is_rejected"])
        self.assertFalse(_run_pilot(env, item, 200))
        self.assertEqual(1, len(calls))
        self.assertEqual([], _plan(env, [item]))

    def test_followup_favors_decision_boundary_over_obvious_winner(self):
        env = SimpleNamespace(
            channels=self.channels, remaining_contacts=15_000,
            remaining_budget=100_000, pilots_left=10,
        )
        near_boundary = candidate("tariff_1", 300, mean=0.004)
        obvious_winner = candidate("tariff_2", 300, mean=0.30)
        self.assertGreater(_followup_value(env, near_boundary), 0)
        self.assertGreater(
            _followup_value(env, near_boundary),
            _followup_value(env, obvious_winner),
        )


if __name__ == "__main__":
    unittest.main()
