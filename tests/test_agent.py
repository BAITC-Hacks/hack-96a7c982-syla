"""Focused tests for planning limits; no hidden environment data is used."""

import unittest
import math
from types import SimpleNamespace

from agent import PILOT_STD_PER_CUSTOMER, _followup_value, _plan, _posterior, _run_pilot


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
