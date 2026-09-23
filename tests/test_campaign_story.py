"""Campaign copy and presentation diagnostics must not change agent decisions."""

import copy
import json
import unittest
from unittest.mock import patch

from campaign_creatives import generate_creatives
from dashboard_data import UPLOADS, demo_dataset, validate_upload
from dashboard_server import (
    aggregate_report, build_dashboard, campaign_comparison, decision_funnel,
)
from scoring_core import score_campaigns
from test_dashboard_upload import upload_payload


def hypothesis(source, target="target", segment="MID", pilots=0, rejected=False):
    return {
        "filter_current_tariff": source, "filter_arpu_segment": segment,
        "target_tariff": target, "pilot_count": pilots, "is_rejected": rejected,
    }


class DecisionFunnelTest(unittest.TestCase):
    def test_distinct_hypotheses_separate_rejections_unselected_and_unpiloted(self):
        chosen = hypothesis("chosen", pilots=3)
        rejected = hypothesis("rejected", pilots=1, rejected=True)
        unselected = hypothesis("unselected", pilots=2)
        small = hypothesis("small")
        untouched = hypothesis("untouched")
        result = decision_funnel(
            [chosen, chosen.copy(), rejected, unselected, small, untouched],
            [chosen, small, dict(chosen, channel="push")],
        )
        self.assertEqual(result, {
            "tested": 3, "rejected": 1, "selected_piloted": 1,
            "not_selected_piloted": 1, "selected_unpiloted": 1,
            "final_campaigns": 2, "unpiloted_pool": 2,
        })
        self.assertEqual(result["tested"], result["rejected"] + result["selected_piloted"]
                         + result["not_selected_piloted"])
        self.assertEqual(result["final_campaigns"], result["selected_piloted"] + result["selected_unpiloted"])

    def test_key_includes_target_and_segment_and_empty_pool_is_valid(self):
        variants = [hypothesis("same", target="a", pilots=1),
                    hypothesis("same", target="b", pilots=1),
                    hypothesis("same", target="a", segment="HIGH", pilots=1)]
        self.assertEqual(decision_funnel(variants, [])["tested"], 3)
        self.assertEqual(decision_funnel(variants, [])["not_selected_piloted"], 3)
        self.assertTrue(all(value == 0 for value in decision_funnel([], []).values()))


class ComparisonTest(unittest.TestCase):
    def test_positive_negative_and_zero_baseline(self):
        for baseline, net, growth in ((1000, 100, 10), (1000, -200, -20), (0, 0, None)):
            with self.subTest(baseline=baseline, net=net):
                result = campaign_comparison({
                    "baseline_total_arpu": baseline, "net_arpu_gain": net,
                    "total_arpu_after": baseline + net,
                    "growth_vs_baseline_pct": float("nan"),
                })
                self.assertEqual(result["baseline_total_arpu"], baseline)
                self.assertEqual(result["total_arpu_after"], baseline + net)
                self.assertEqual(result["growth_pct"], growth)
                json.dumps(result, allow_nan=False)


class CampaignStoryIntegrationTest(unittest.TestCase):
    def tearDown(self):
        build_dashboard.cache_clear()

    def test_seed42_comparison_uses_actual_score_and_funnel_reconciles(self):
        build_dashboard.cache_clear()
        captured = []

        def capture(*args, **kwargs):
            result = score_campaigns(*args, **kwargs)
            captured.append(result)
            return result

        with patch("dashboard_server.score_campaigns", side_effect=capture):
            report = build_dashboard(42)
        self.assertEqual(len(captured), 1)
        self.assertEqual(report["comparison"], campaign_comparison(captured[0]))
        self.assertAlmostEqual(report["comparison"]["baseline_total_arpu"],
                               demo_dataset().profile["predicted_arpu"].sum())
        self.assertEqual(report["comparison"]["net_arpu_gain"], report["summary"]["net_arpu_gain"])
        funnel = report["funnel"]
        self.assertEqual(funnel["tested"], report["summary"]["tested_hypotheses"])
        self.assertLess(funnel["tested"], len(report["pilot_trace"]))
        self.assertEqual(funnel["rejected"], len(report["rejected"]))
        self.assertEqual(funnel["tested"], funnel["rejected"] + funnel["selected_piloted"]
                         + funnel["not_selected_piloted"])
        self.assertEqual(funnel["final_campaigns"], len(report["campaigns"]))
        self.assertEqual(funnel["final_campaigns"], funnel["selected_piloted"] + funnel["selected_unpiloted"])
        json.dumps(report, allow_nan=False)

    def test_uploaded_comparison_and_copy_use_uploaded_tariffs(self):
        for prefix, size, price, data in (("first_story", 180, 2500, 6144),
                                           ("second_story", 210, 3500, 12288)):
            with self.subTest(prefix=prefix):
                payload = upload_payload(prefix, size=size)
                payload["tariffs"]["text"] = payload["tariffs"]["text"].replace(
                    f"{prefix}_b,1000,2048", f"{prefix}_b,{price},{data}",
                )
                dataset = validate_upload(payload)
                key = UPLOADS.add(dataset)["id"]
                report = build_dashboard(42, key)
                comparison = report["comparison"]
                self.assertEqual(comparison["baseline_total_arpu"], size * 2000)
                self.assertEqual(comparison["total_arpu_after"],
                                 comparison["baseline_total_arpu"] + report["summary"]["net_arpu_gain"])
                row = report["campaigns"][0]
                self.assertEqual(row["target"], prefix + "_b")
                self.assertEqual(row["tariff_context"]["target_price"], price)
                self.assertEqual(row["tariff_context"]["target_data_gb"], data / 1024)
                self.assertEqual(row["creative"], generate_creatives(row["target"], row["channel"], row["tariff_context"]))
                json.dumps(report, allow_nan=False)

    def test_copy_generation_makes_no_network_calls_and_does_not_change_plan(self):
        build_dashboard.cache_clear()
        with patch("socket.create_connection", side_effect=AssertionError("No external service required")), \
                patch("dashboard_server.generate_creatives", wraps=generate_creatives) as generator:
            original = build_dashboard(42)
        self.assertEqual(generator.call_count, len(original["campaigns"]))
        for row in original["campaigns"]:
            self.assertTrue(row["creative"]["requires_review"])
        build_dashboard.cache_clear()
        with patch("dashboard_server.generate_creatives", return_value={"test_only": "different copy"}):
            changed_copy = build_dashboard(42)
        for field in ("summary", "submission", "pilot_trace", "resources", "comparison", "funnel",
                      "model_version", "dataset_fingerprint"):
            self.assertEqual(original[field], changed_copy[field], field)
        self.assertEqual(original["exports"], changed_copy["exports"])

    def test_exports_include_diagnostics_but_not_creative_drafts_or_private_fields(self):
        report = copy.deepcopy(build_dashboard(42))
        report["comparison"]["private"] = "secret-comparison-token"
        report["funnel"]["private"] = "secret-funnel-token"
        snapshot = aggregate_report(report)
        self.assertEqual(snapshot["comparison"], report["exports"]["json"]["comparison"])
        self.assertEqual(snapshot["funnel"], report["exports"]["json"]["funnel"])
        self.assertEqual(snapshot["summary"], report["summary"])
        self.assertNotIn("comparison", snapshot["summary"])
        encoded = json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
        for forbidden in ("creative", "copy_text", "secret-comparison-token", "secret-funnel-token"):
            self.assertNotIn(forbidden, encoded)
        markdown = report["exports"]["markdown"]
        self.assertIn("Baseline без кампаний", markdown)
        self.assertIn("после затрат на коммуникацию", markdown)
        self.assertIn("не прибыль компании", markdown)
        self.assertIn("не доказанный убыток", markdown)

    def test_empty_and_all_rejected_plans_have_honest_funnels(self):
        small_key = UPLOADS.add(validate_upload(upload_payload("empty_story", size=5)))["id"]
        empty = build_dashboard(42, small_key)
        self.assertTrue(all(value == 0 for value in empty["funnel"].values()))
        self.assertEqual(empty["comparison"]["net_arpu_gain"], 0)
        self.assertEqual(empty["comparison"]["total_arpu_after"], 10000)
        negative_key = UPLOADS.add(validate_upload(upload_payload("negative_story", next_arpu=0)))["id"]
        rejected = build_dashboard(42, negative_key)
        self.assertEqual(rejected["funnel"]["tested"], 1)
        self.assertEqual(rejected["funnel"]["rejected"], 1)
        self.assertEqual(rejected["funnel"]["final_campaigns"], 0)
        self.assertLess(rejected["comparison"]["net_arpu_gain"], 0)
        for report in (empty, rejected):
            json.dumps(report, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
