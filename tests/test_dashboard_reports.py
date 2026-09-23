"""Reports preserve the displayed simulation and reconcile all scored resources."""

import copy
from http.server import ThreadingHTTPServer
import json
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.request import urlopen

from dashboard_data import UPLOADS, validate_upload
from dashboard_server import (
    CAP_LABELS, DashboardHandler, aggregate_report, build_dashboard, markdown_report,
    resource_breakdown,
)
from scoring_core import score_campaigns
from mock_environment import CHANNELS
from test_dashboard_upload import upload_payload


class ResourceReportTest(unittest.TestCase):
    def test_phases_and_all_used_channels_including_free_push(self):
        rows = [
            {"channel": "sms", "n_contacts": 150, "cost": 600},
            {"channel": "sms", "n_contacts": 150, "cost": 600},
            {"channel": "push", "n_contacts": 800, "cost": 0},
            {"channel": "digital_ads", "n_contacts": 20, "cost": 440},
            {"channel": "call", "n_contacts": 0, "cost": 0},
        ]
        report = resource_breakdown(rows, 2)
        self.assertEqual(report["pilots"], {"contacts": 300, "cost": 1200})
        self.assertEqual(report["final"], {"contacts": 820, "cost": 440})
        self.assertEqual(report["total"], {"contacts": 1120, "cost": 1640})
        channels = {row["channel"]: row for row in report["channels"]}
        self.assertEqual(set(channels), {"sms", "push", "digital_ads"})
        self.assertEqual(channels["push"]["total"], {"contacts": 800, "cost": 0})
        self.assertEqual(sum(row["total"]["contacts"] for row in channels.values()), 1120)
        self.assertEqual(resource_breakdown([], 0)["total"], {"contacts": 0, "cost": 0})

    def test_actual_breakdown_reconciles_with_scorer(self):
        report = build_dashboard(42)
        resources = report["resources"]
        self.assertEqual(resources["total"]["contacts"], report["summary"]["contacts"])
        self.assertAlmostEqual(resources["total"]["cost"], report["summary"]["budget_used"])
        self.assertEqual(resources["pilots"]["contacts"], sum(row["n"] for row in report["pilot_trace"]))
        self.assertAlmostEqual(resources["pilots"]["cost"], sum(row["cost"] for row in report["pilot_trace"]))
        self.assertEqual(resources["final"]["contacts"], sum(row["contacts"] for row in report["campaigns"]))
        self.assertAlmostEqual(resources["final"]["cost"], sum(row["cost"] for row in report["campaigns"]))
        for field in ("contacts", "cost"):
            self.assertAlmostEqual(resources["pilots"][field] + resources["final"][field], resources["total"][field])
            self.assertAlmostEqual(sum(row["total"][field] for row in resources["channels"]), resources["total"][field])

    def test_channel_transfer_is_explicit_and_matches_sms_posterior(self):
        report = build_dashboard(42)
        for row in report["campaigns"]:
            factor = CHANNELS[row["channel"]]["conversion_multiplier"] / CHANNELS["sms"]["conversion_multiplier"]
            self.assertAlmostEqual(row["channel_scale_from_sms"], factor)
            trace = [pilot for pilot in report["pilot_trace"] if
                     (pilot["source"], pilot["segment"], pilot["target"]) ==
                     (row["source"], row["segment"], row["target"])]
            if trace:
                self.assertAlmostEqual(row["posterior_lift_pct"], trace[-1]["after_pct"] * factor)
                self.assertAlmostEqual(row["posterior_std_pct"], trace[-1]["std_after_pct"] * factor)
        self.assertEqual([row["channel_scale_from_sms"] for row in report["campaigns"]],
                         [row["channel_scale_from_sms"] for row in report["exports"]["json"]["campaigns"]])

    def test_exact_cap_reasons_are_taken_from_scorer(self):
        def capped(*args, **kwargs):
            score = score_campaigns(*args, **kwargs)
            for flag in CAP_LABELS:
                score["campaigns_detail"][-1][flag] = True
            return score

        build_dashboard.cache_clear()
        try:
            with patch("dashboard_server.score_campaigns", side_effect=capped):
                report = build_dashboard(42)
            self.assertEqual(report["campaigns"][-1]["cap_reasons"], list(CAP_LABELS.values()))
            self.assertEqual(report["exports"]["json"]["campaigns"][-1]["cap_reasons"], list(CAP_LABELS.values()))
        finally:
            build_dashboard.cache_clear()


class AggregateExportTest(unittest.TestCase):
    def test_upload_export_preserves_snapshot_without_private_metadata(self):
        payload = upload_payload("export")
        payload["profile"]["name"] = "private-customer-list.csv"
        key = UPLOADS.add(validate_upload(payload))["id"]
        report = build_dashboard(84, key)
        snapshot = report["exports"]["json"]
        encoded = json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
        self.assertEqual(snapshot["seed"], 84)
        self.assertEqual(snapshot["provenance"], "simulation")
        self.assertEqual(snapshot["model_version"], report["model_version"])
        self.assertEqual(snapshot["dataset_fingerprint"], report["dataset_fingerprint"])
        self.assertEqual(snapshot["summary"], report["summary"])
        self.assertEqual(snapshot["resources"], report["resources"])
        self.assertEqual(snapshot["campaigns"][0]["target"], "export_b")
        for private in (key, "private-customer-list.csv", "ID_NUMBER", "explicit_ids", "files"):
            self.assertNotIn(private, encoded)
            self.assertNotIn(private, report["exports"]["markdown"])
        self.assertIn("provenance: simulation", report["exports"]["markdown"])
        self.assertIn("Локальная симуляция", report["exports"]["markdown"])
        self.assertIn("Сценарий: 84", report["exports"]["markdown"])
        self.assertEqual(report["exports"]["markdown"], markdown_report(snapshot))

    def test_allowlist_excludes_unexpected_row_fields_and_escapes_markdown(self):
        report = copy.deepcopy(build_dashboard(42))
        report["campaigns"][0].update(explicit_ids=["private-id"], filename="secret.csv", source="[label](bad)|row\n# title")
        report["pilot_trace"][0]["ID_NUMBER"] = "private-id"
        snapshot = aggregate_report(report)
        encoded = json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
        self.assertNotIn("private-id", encoded)
        self.assertNotIn("secret.csv", encoded)
        markdown = markdown_report(snapshot)
        self.assertNotIn("[label](bad)", markdown)
        self.assertNotIn("\n# title", markdown)
        self.assertIn("\\|row", markdown)

    def test_plan_http_includes_exports_for_same_seed_and_resources(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with urlopen(f"http://127.0.0.1:{server.server_port}/api/plan?seed=73") as response:
                report = json.load(response)
            self.assertEqual(report["exports"]["json"]["seed"], report["seed"])
            self.assertEqual(report["exports"]["json"]["summary"], report["summary"])
            self.assertEqual(report["exports"]["json"]["resources"], report["resources"])
            self.assertIn("Сценарий: 73", report["exports"]["markdown"])
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == "__main__":
    unittest.main()
