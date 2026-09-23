"""Dashboard data must match the baseline agent and the case limits."""

import unittest

from agent import Agent
from dashboard_server import build_dashboard
from make_submission import build_submission


class DashboardTest(unittest.TestCase):
    def test_demo_plan_matches_submission_for_same_seed(self):
        report = build_dashboard(42)
        expected = build_submission(Agent(), seed=42)
        self.assertEqual(expected["campaign_name"].tolist(), [
            campaign["name"] for campaign in report["campaigns"]
        ])
        self.assertEqual(len(expected), report["summary"]["campaigns"])
        self.assertLessEqual(report["summary"]["contacts"], report["limits"]["contacts"])
        self.assertLessEqual(report["summary"]["budget_used"], report["limits"]["budget"])
        self.assertLessEqual(report["summary"]["pilots"], report["limits"]["pilots"])
        self.assertTrue(all(row["pilots"] or row["pilot_count"] == 0 for row in report["campaigns"]))
        for campaign in report["campaigns"]:
            context = campaign["tariff_context"]
            self.assertGreaterEqual(context["current_price"], 0)
            self.assertGreaterEqual(context["target_price"], 0)
            self.assertGreaterEqual(context["current_data_gb"], 0)
            self.assertGreaterEqual(context["target_data_gb"], 0)
            self.assertGreaterEqual(context["data_user_pct"], 0)
            self.assertLessEqual(context["data_user_pct"], 100)
            self.assertGreaterEqual(context["heavy_user_pct"], 0)
            self.assertLessEqual(context["heavy_user_pct"], 100)


if __name__ == "__main__":
    unittest.main()
