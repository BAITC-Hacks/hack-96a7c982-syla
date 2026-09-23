"""Stress tests for the participant agent's public-interface contract."""
import unittest
from types import SimpleNamespace
import pandas as pd

from agent import Agent, _fallback_agent


CHANNELS = {
    "push": {"cost_per_contact": 0, "conversion_multiplier": 0.50},
    "sms": {"cost_per_contact": 4, "conversion_multiplier": 0.65},
    "digital_ads": {"cost_per_contact": 22, "conversion_multiplier": 0.85},
    "call": {"cost_per_contact": 160, "conversion_multiplier": 1.20},
}


def env_with(pilot):
    profile = pd.DataFrame({
        "ID_NUMBER": range(1, 31),
        "current_tariff": ["tariff_1"] * 30,
        "arpu_segment": ["MID"] * 30,
        "data_segment": ["LITE"] * 30,
        "call_segment": ["MEDIUM"] * 30,
        "predicted_arpu": [3000.0] * 30,
    })
    tariffs = pd.DataFrame({
        "tariff_plan_code": ["tariff_1", "tariff_2"],
        "price_tariff": [1000.0, 5000.0],
    })
    return SimpleNamespace(
        customer_profile=profile, tariffs=tariffs, channels=CHANNELS,
        remaining_budget=100000, remaining_contacts=15000, pilots_left=20,
        pilot_history=[], run_pilot=pilot,
    )


class StressTest(unittest.TestCase):
    def test_fallback_survives_pilot_server_failure(self):
        def fail(**kwargs):
            raise RuntimeError("simulated 500/timeout")
        self.assertEqual([], _fallback_agent(env_with(fail)))

    def test_fallback_rejects_corrupt_pilot_payload(self):
        def corrupt(**kwargs):
            return {"observed_lift_ratio": "not-a-number", "n_customers": 20}
        self.assertEqual([], _fallback_agent(env_with(corrupt)))

    def test_fallback_does_not_launch_on_negative_signal(self):
        def negative(**kwargs):
            return {"observed_lift_ratio": -0.2, "n_customers": 20}
        self.assertEqual([], _fallback_agent(env_with(negative)))

    def test_agent_survives_missing_history_file_via_public_data(self):
        # This primarily asserts that the public-data path itself is enough;
        # historical CSV is an optional prior, not a hard runtime dependency.
        def positive(**kwargs):
            return {"observed_lift_ratio": 0.2, "n_customers": 20}
        result = Agent().act(env_with(positive))
        self.assertIsInstance(result, list)
        self.assertLessEqual(len(result), 10)

    def test_empty_profile_is_safe(self):
        def fail(**kwargs):
            raise AssertionError("pilot must not run")
        env = env_with(fail)
        env.customer_profile = env.customer_profile.iloc[:0]
        self.assertEqual([], Agent().act(env))


if __name__ == "__main__":
    unittest.main()
