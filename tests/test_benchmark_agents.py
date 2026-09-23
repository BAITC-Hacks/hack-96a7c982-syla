"""Fast contract tests for the paired benchmark, without multi-run simulation."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import benchmark_agents as benchmark


class BenchmarkTest(unittest.TestCase):
    def setUp(self):
        self.env = SimpleNamespace(
            tariffs=pd.DataFrame({"tariff_plan_code": ["tariff_1"]}),
            customer_profile=pd.DataFrame({"ID_NUMBER": [1], "predicted_arpu": [100.0]}),
            pilots_left=20, remaining_budget=100_000, remaining_contacts=15_000,
        )
        self.pilots = []
        self.internals = SimpleNamespace(executed_pilot_campaigns=lambda: self.pilots)
        self.factory = patch.object(benchmark, "make_mock_env", return_value=(self.env, self.internals))
        self.factory.start()
        self.addCleanup(self.factory.stop)

    @staticmethod
    def snapshot(body):
        return benchmark.Snapshot("class Agent:\n    def act(self, env):\n" + body, "test-ref", "a" * 40, "hash")

    def test_empty_plan_is_zero_not_dropped(self):
        row = benchmark.evaluate_snapshot(self.snapshot("        return []\n"), 0)
        self.assertEqual(0.0, row["net_arpu_gain"])
        self.assertTrue(row["empty_final_plan"])
        summary = benchmark.summarize([row])
        self.assertEqual(1, summary["runs"])
        self.assertEqual(0.0, summary["mean"])
        self.assertEqual(0, summary["positive"])

    def test_exception_retains_pilot_score(self):
        self.pilots.append({"target_tariff": "tariff_1", "channel": "sms", "explicit_ids": [1]})
        result = {"net_arpu_gain": -4.0, "total_cost": 4.0, "total_contacts": 1, "campaigns_detail": [{"n_contacts": 1}]}
        with patch.object(benchmark, "_mock_impact_model"), patch.object(benchmark.pd, "read_csv"), patch.object(benchmark, "score_campaigns", return_value=result) as scorer:
            row = benchmark.evaluate_snapshot(self.snapshot("        raise RuntimeError('broken')\n"), 2)
        self.assertEqual(-4.0, row["net_arpu_gain"])
        self.assertEqual(1, row["pilots"])
        self.assertEqual("RuntimeError: broken", row["agent_error"])
        self.assertEqual(1, len(scorer.call_args.args[0]))

    def test_invalid_container_and_unhashable_target_are_diagnostic(self):
        for body in ("        return None\n", "        return [{'target_tariff': [], 'channel': 'sms'}]\n"):
            with self.subTest(body=body):
                row = benchmark.evaluate_snapshot(self.snapshot(body), 0)
                self.assertTrue(row["invalid_output"])
                self.assertEqual(0.0, row["net_arpu_gain"])
                self.assertIsNone(row["evaluation_error"])

    def test_limit_violation_is_recorded_before_truncation(self):
        result = {"net_arpu_gain": 10.0, "total_cost": 0.0, "total_contacts": 1, "campaigns_detail": [{"n_contacts": 1}]}
        body = "        return [{'target_tariff': 'tariff_1', 'channel': 'push'}] * 11\n"
        with patch.object(benchmark, "_mock_impact_model"), patch.object(benchmark.pd, "read_csv"), patch.object(benchmark, "score_campaigns", return_value=result) as scorer:
            row = benchmark.evaluate_snapshot(self.snapshot(body), 0)
        self.assertEqual(10, row["final_campaigns"])
        self.assertEqual(11, row["raw_final_campaigns"])
        self.assertEqual(1, row["discarded_campaigns"])
        self.assertIn("final_campaigns", row["limit_violations"])
        self.assertEqual(10, len(scorer.call_args.args[0]))

    def test_fresh_module_with_identical_file_for_each_run(self):
        source = ("from pathlib import Path\ncalls = 0\nclass Agent:\n"
                  "    def act(self, env):\n        global calls\n        calls += 1\n"
                  "        assert calls == 1\n"
                  "        assert Path(__file__) == Path.cwd() / 'agent.py'\n"
                  "        return []\n")
        snapshot = benchmark.Snapshot(source, "ref", "commit", "hash")
        for _ in range(2):
            row = benchmark.evaluate_snapshot(snapshot, 4)
            self.assertIsNone(row["agent_error"])
            self.assertEqual(0.0, row["net_arpu_gain"])

    def test_scoring_failure_does_not_make_optimistic_partial_summary(self):
        good = benchmark.evaluate_snapshot(self.snapshot("        return []\n"), 0)
        failed = dict(good, net_arpu_gain=None, evaluation_error="bad scorer")
        summary = benchmark.summarize([good, failed])
        self.assertEqual(2, summary["runs"])
        self.assertFalse(summary["complete"])
        self.assertIsNone(summary["mean"])
        self.assertIsNone(summary["positive"])
        self.assertEqual(1, summary["evaluation_failures"])

    def test_paired_seeds_and_delta(self):
        base, candidate = self.snapshot("        return []\n"), self.snapshot("        return None\n")
        template = benchmark.evaluate_snapshot(base, 0)
        def fake_evaluate(snapshot, seed):
            return dict(template, seed=seed, net_arpu_gain=float(seed + (2 if snapshot is candidate else 0)))
        with patch.object(benchmark, "evaluate_snapshot", side_effect=fake_evaluate) as evaluate:
            report = benchmark.run_benchmark(base, candidate, runs=2, seed_start=7)
        self.assertEqual([7, 7, 8, 8], [call.args[1] for call in evaluate.call_args_list])
        self.assertEqual(2.0, report["paired_delta"]["mean"])
        self.assertEqual(2, report["paired_delta"]["positive"])
        self.assertEqual("test-ref", report["snapshots"]["baseline"]["ref"])
        self.assertEqual(64, len(report["shared_sha256"]["scoring_core.py"]))

    def test_load_git_ref_resolves_commit_before_reading_agent(self):
        source = b"class Agent: pass\n"
        with patch.object(benchmark, "_git", side_effect=[b"abc123\n", source]) as git:
            snapshot = benchmark.load_snapshot("origin/experiment")
        self.assertEqual("abc123", snapshot.commit)
        self.assertEqual("origin/experiment", snapshot.ref)
        self.assertEqual(64, len(snapshot.sha256))
        self.assertEqual(("show", "abc123:agent.py"), git.call_args.args)

    def test_rejects_invalid_run_range(self):
        for runs, seed_start in ((0, 0), (1, -1)):
            with self.assertRaises(ValueError):
                benchmark.run_benchmark(None, None, runs, seed_start)


if __name__ == "__main__":
    unittest.main()
