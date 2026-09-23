import copy
import csv
import io
import json
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from urllib.request import Request, urlopen

from agent import _candidates, _explore, _posterior
from dashboard_server import DashboardHandler, build_dashboard
from mock_environment import make_mock_env
from pilot_journal import PilotJournal


class PilotTraceTest(unittest.TestCase):
    def test_trace_matches_actual_updates_and_uses_pre_observation_forecast(self):
        report = build_dashboard(42)
        trace = report["pilot_trace"]
        self.assertEqual(len(trace), report["summary"]["pilots"])
        self.assertTrue(any(row["round"] > 1 for row in trace))
        previous = {}
        for row in trace:
            key = row["source"], row["segment"], row["target"]
            if key in previous:
                self.assertAlmostEqual(row["before_pct"], previous[key]["after_pct"])
                self.assertAlmostEqual(row["std_before_pct"], previous[key]["std_after_pct"])
            self.assertAlmostEqual(row["error_pp"], row["observed_pct"] - row["before_pct"])
            self.assertLess(row["std_after_pct"], row["std_before_pct"])
            previous[key] = row
        env, _ = make_mock_env(seed=42)
        candidates, _ = _candidates(env)
        _explore(env, candidates)
        for candidate in candidates:
            key = candidate["filter_current_tariff"], candidate["filter_arpu_segment"], candidate["target_tariff"]
            if key in previous:
                mean, std = _posterior(candidate)
                self.assertAlmostEqual(previous[key]["after_pct"], 100 * mean)
                self.assertAlmostEqual(previous[key]["std_after_pct"], 100 * std)


class JournalPersistenceTest(unittest.TestCase):
    def test_restart_deduplication_provenance_and_versioning(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            journal = PilotJournal(path)
            report = build_dashboard(42)
            with ThreadPoolExecutor(max_workers=3) as executor:
                saved = list(executor.map(journal.save, [report] * 3))
            self.assertEqual(sum(row["new_record"] for row in saved), 1)
            restored = PilotJournal(path)
            self.assertEqual(restored.list()["total"], 1)
            entry = restored.get(saved[0]["id"])
            self.assertEqual(entry["pilots"], report["pilot_trace"])
            self.assertEqual(entry["provenance"], "simulation")
            self.assertNotIn("ID_NUMBER", json.dumps(entry))
            self.assertNotIn("explicit_ids", json.dumps(entry))
            self.assertNotIn("files", entry)
            updated = copy.deepcopy(report)
            updated["model_version"] = "new-version"
            self.assertNotEqual(restored.save(updated)["id"], entry["id"])
            self.assertEqual(restored.list()["total"], 2)


class JournalHttpTest(unittest.TestCase):
    def test_save_list_detail_and_csv_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
            server.journal = PilotJournal(Path(directory) / "journal.sqlite3")
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(base + "/api/runs") as response:
                    self.assertEqual(json.load(response)["runs"], [])
                request = Request(base + "/api/runs", data=b'{"seed":42,"dataset":"demo"}',
                                  headers={"Content-Type": "application/json", "Origin": base})
                with urlopen(request) as response:
                    saved = json.load(response)
                with urlopen(request) as response:
                    self.assertFalse(json.load(response)["new_record"])
                with urlopen(base + "/api/runs?id=" + saved["id"]) as response:
                    detail = json.load(response)
                with urlopen(base + "/api/pilots.csv?id=" + saved["id"]) as response:
                    rows = list(csv.DictReader(io.StringIO(response.read().decode("utf-8-sig"))))
                self.assertEqual(len(rows), len(detail["pilots"]))
                self.assertTrue(all(row["provenance"] == "simulation" for row in rows))
                self.assertAlmostEqual(float(rows[0]["before_pct"]), detail["pilots"][0]["before_pct"])
                with urlopen(base + "/api/runs") as response:
                    self.assertEqual(json.load(response)["total"], 1)
            finally:
                server.shutdown()
                server.server_close()
                worker.join()


if __name__ == "__main__":
    unittest.main()
