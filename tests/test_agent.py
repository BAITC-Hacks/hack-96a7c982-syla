"""Focused tests for planning limits; no hidden environment data is used."""

import unittest
from types import SimpleNamespace

from agent import _plan


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

    def test_does_not_force_negative_first_campaign(self):
        env = SimpleNamespace(
            channels=self.channels, remaining_contacts=200, remaining_budget=1000
        )
        campaigns = _plan(env, [candidate("tariff_1", 120, mean=-0.30)])
        self.assertEqual([], campaigns)

    def test_can_consider_digital_ads_when_economics_support_it(self):
        channels = {
            "push": {"cost_per_contact": 0, "conversion_multiplier": 0.50},
            "sms": {"cost_per_contact": 4, "conversion_multiplier": 0.65},
            "digital_ads": {"cost_per_contact": 22, "conversion_multiplier": 0.85},
            "call": {"cost_per_contact": 160, "conversion_multiplier": 1.20},
        }
        env = SimpleNamespace(
            channels=channels, remaining_contacts=120, remaining_budget=10000
        )
        campaigns = _plan(env, [candidate("tariff_1", 120, mean=0.30)])
        self.assertEqual(1, len(campaigns))
        self.assertIn(campaigns[0]["channel"], channels)


if __name__ == "__main__":
    unittest.main()
