"""Uploads must use their own history and cannot leak into another plan/export."""

import csv
import io
import json
from http.server import ThreadingHTTPServer
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dashboard_data import DatasetError, DatasetStore, ROOT, UPLOADS, validate_upload
from dashboard_server import DashboardHandler, build_dashboard


def upload_payload(prefix="custom", next_arpu=4000, size=180):
    return {
        "profile": {"name": "customers.csv", "text":
            "ID_NUMBER,current_tariff,predicted_arpu,arpu_segment,DATA_VOLUME,data_segment,call_segment\n"
            + "".join(f"{i},{prefix}_a,2000,MID,1024,LITE,MEDIUM\n" for i in range(1, size + 1))},
        "tariffs": {"name": "tariffs.csv", "text":
            f"tariff_plan_code,price_tariff,Data_in_PKG\n{prefix}_a,1000,1024\n{prefix}_b,1000,2048\n"},
        "history": {"name": "history.csv", "text":
            "ID_NUMBER,tariff_plan_code_from,tariff_plan_code_to,AVG_ARPU_PREV_3M,AVG_ARPU_NEXT_3M\n"
            + "".join(f"{i},{prefix}_a,{prefix}_b,2000,{next_arpu}\n" for i in range(1, 201))},
    }


class UploadValidationTest(unittest.TestCase):
    def test_case_files_are_accepted_with_explicit_missing_data_warnings(self):
        paths = {"profile": ROOT / "customer_profile.csv", "tariffs": ROOT / "data/dict_tariff.csv",
                 "history": ROOT / "data/change_tariff.csv"}
        dataset = validate_upload({key: {"name": path.name, "text": path.read_text(encoding="utf-8")}
                                   for key, path in paths.items()})
        self.assertTrue(dataset.metadata["warnings"])
        self.assertFalse(dataset.profile["current_tariff"].eq("").any())
        self.assertTrue(dataset.profile["DATA_VOLUME"].isna().any())
        self.assertTrue((dataset.history["AVG_ARPU_PREV_3M"] < 0).any())

    def test_bom_and_semicolon(self):
        payload = upload_payload()
        for item in payload.values():
            item["text"] = "\ufeff" + item["text"].replace(",", ";")
        self.assertEqual(len(validate_upload(payload).profile), 180)

    def test_invalid_inputs_return_actionable_issues(self):
        cases = {
            "missing_column": lambda p: p["profile"].update(text=p["profile"]["text"].replace("predicted_arpu", "arpu")),
            "duplicate_id": lambda p: p["profile"].update(text=p["profile"]["text"] + "1,custom_a,2000,MID,0,NON_USER,LOW\n"),
            "unknown_tariff": lambda p: p["profile"].update(text=p["profile"]["text"].replace("custom_a", "missing")),
            "nan_arpu": lambda p: p["profile"].update(text=p["profile"]["text"].replace(",2000,MID", ",NaN,MID")),
            "unknown_segment": lambda p: p["profile"].update(text=p["profile"]["text"].replace(",MID,", ",MIDDLE,")),
            "ragged_csv": lambda p: p["profile"].update(text=p["profile"]["text"] + "9,extra\n"),
            "empty_history": lambda p: p["history"].update(text=""),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                payload = upload_payload()
                mutate(payload)
                with self.assertRaises(DatasetError) as caught:
                    validate_upload(payload)
                self.assertTrue(caught.exception.issues[0]["file"])
                self.assertTrue(caught.exception.issues[0]["message"])

    def test_no_candidates_is_a_valid_empty_report(self):
        dataset = validate_upload(upload_payload(size=5))
        key = UPLOADS.add(dataset)["id"]
        report = build_dashboard(42, key)
        self.assertEqual(report["campaigns"], [])
        self.assertEqual(report["summary"]["net_arpu_gain"], 0)
        json.dumps(report, allow_nan=False)

    def test_unknown_traffic_is_not_reported_as_zero_usage(self):
        payload = upload_payload()
        payload["profile"]["text"] = payload["profile"]["text"].replace(",1024,LITE,", ",,,")
        key = UPLOADS.add(validate_upload(payload))["id"]
        report = build_dashboard(42, key)
        context = report["campaigns"][0]["tariff_context"]
        self.assertIsNone(context["data_user_pct"])
        self.assertIsNone(context["heavy_user_pct"])
        self.assertEqual(context["data_known_count"], 0)
        json.dumps(report, allow_nan=False)

    def test_negative_uploaded_history_rejects_campaign(self):
        key = UPLOADS.add(validate_upload(upload_payload(next_arpu=0)))["id"]
        report = build_dashboard(42, key)
        self.assertTrue(report["rejected"])
        self.assertEqual(report["campaigns"], [])

    def test_store_expiry(self):
        store = DatasetStore(capacity=1)
        old = store.add(validate_upload(upload_payload()))["id"]
        store.add(validate_upload(upload_payload()))
        with self.assertRaises(KeyError):
            store.get(old)


class UploadHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
        cls.worker = Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join()

    def upload(self, payload):
        request = Request(self.base + "/api/datasets", data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json", "Origin": self.base})
        with urlopen(request) as response:
            self.assertEqual(response.status, 201)
            return json.load(response)

    def test_upload_plan_export_and_dataset_isolation(self):
        first = self.upload(upload_payload("first"))
        second = self.upload(upload_payload("second"))
        for metadata, prefix in ((first, "first"), (second, "second")):
            query = f"?seed=42&dataset={metadata['id']}"
            with urlopen(self.base + "/api/plan" + query) as response:
                report = json.load(response)
            self.assertEqual(report["dataset"]["id"], metadata["id"])
            self.assertEqual(report["campaigns"][0]["target"], prefix + "_b")
            # Equal tariff prices imply zero fallback: a positive prior proves
            # this request used the uploaded history, not the bundled history.
            self.assertGreater(report["campaigns"][0]["prior_lift_pct"], 0)
            with urlopen(self.base + "/api/submission.csv" + query) as response:
                rows = list(csv.DictReader(io.StringIO(response.read().decode("utf-8-sig"))))
            self.assertEqual(rows[0]["target_tariff"], prefix + "_b")
            self.assertEqual(rows[0]["campaign_name"], report["campaigns"][0]["name"])

    def test_validation_and_expired_upload_have_json_errors(self):
        with self.assertRaises(HTTPError) as caught:
            self.upload({})
        self.assertEqual(caught.exception.code, 422)
        self.assertEqual(len(json.load(caught.exception)["issues"]), 3)
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.base + "/api/plan?dataset=expired")
        self.assertEqual(caught.exception.code, 404)
        self.assertIn("error", json.load(caught.exception))


if __name__ == "__main__":
    unittest.main()
