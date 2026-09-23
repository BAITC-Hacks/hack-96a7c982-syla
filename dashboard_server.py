"""Local demo dashboard for the tariff campaign agent.

Run ``python dashboard_server.py`` and open http://127.0.0.1:8765.
The dashboard uses the supplied mock environment. It never sends campaigns to
customers and does not change the submitted agent or submission.csv.
Aggregate simulation results can be saved in the local pilot journal.
"""

from __future__ import annotations

import csv
import io
import json
import math
import hashlib
import sqlite3
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pandas as pd

from agent import PILOT_STD_PER_CUSTOMER, PILOTED_RISK_PENALTY, PRIOR_STD, _candidates, _explore, _historical_priors, _plan, _posterior
from dashboard_data import MAX_UPLOAD_BYTES, SCHEMAS, UPLOADS, DatasetError, demo_dataset, validate_upload
from environment import make_environment
from make_submission import CAMPAIGN_COLUMNS
from mock_environment import (
    CHANNELS, MAX_TOTAL_CONTACTS, TOTAL_BUDGET, _mock_fallback, _mock_impact_model,
)
from scoring_core import MAX_CAMPAIGNS, score_campaigns, sanitize_campaigns
from pilot_journal import PilotJournal


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "dashboard"
JOURNAL = PilotJournal(ROOT / ".local" / "pilot_history.sqlite3")
MODEL_VERSION = hashlib.sha256(b"".join(
    (ROOT / name).read_bytes() for name in ("agent.py", "environment.py", "mock_environment.py", "scoring_core.py")
)).hexdigest()[:12]


def pilot_trace(candidates, pilots, history, final):
    """Replay Bayesian updates in time order without leaking later observations."""
    by_key = {(c["filter_current_tariff"], c["filter_arpu_segment"], c["target_tariff"]): c
              for c in candidates}
    selected = {(c["filter_current_tariff"], c["filter_arpu_segment"], c["target_tariff"]) for c in final}
    states, trace = {}, []
    for sequence, (campaign, result) in enumerate(zip(pilots, history), 1):
        key = (campaign["filter_current_tariff"], campaign["filter_arpu_segment"], campaign["target_tariff"])
        candidate = by_key[key]
        prior_precision = 1 / PRIOR_STD ** 2
        precision, weighted, round_number = states.get(
            key, (prior_precision, candidate["prior_lift_ratio"] * prior_precision, 0),
        )
        before, std_before = weighted / precision, math.sqrt(1 / precision)
        n, observed = int(result["n_customers"]), float(result["observed_lift_ratio"])
        added = n / PILOT_STD_PER_CUSTOMER ** 2
        precision += added
        weighted += observed * added
        after, std_after = weighted / precision, math.sqrt(1 / precision)
        states[key] = precision, weighted, round_number + 1
        trace.append({
            "sequence": sequence, "source": key[0], "segment": key[1], "target": key[2],
            "channel": campaign["channel"], "round": round_number + 1,
            "n": n, "cost": float(result["cost"]),
            "before_pct": 100 * before, "std_before_pct": 100 * std_before,
            "observed_pct": 100 * observed, "observation_se_pct": 100 * PILOT_STD_PER_CUSTOMER / math.sqrt(n),
            "after_pct": 100 * after, "std_after_pct": 100 * std_after,
            "error_pp": 100 * (observed - before),
            "decision": "selected" if key in selected else "rejected" if candidate.get("is_rejected") else "not_selected",
        })
    return trace


@lru_cache(maxsize=8)
def build_dashboard(seed: int = 42, dataset_id: str = "demo") -> dict:
    """Run the exact baseline decision pipeline and return display diagnostics."""
    dataset = demo_dataset() if dataset_id == "demo" else UPLOADS.get(dataset_id)
    mock_model = _mock_impact_model(dataset.history)
    env, internals = make_environment(
        customer_profile=dataset.profile, impact_model=mock_model, dict_tariff=dataset.tariffs,
        channels=CHANNELS, total_budget=TOTAL_BUDGET, max_total_contacts=MAX_TOTAL_CONTACTS,
        fallback_predict=_mock_fallback, seed=seed,
    )
    pilot_candidates, small_candidates = _candidates(
        env, prior_provider=lambda tariffs: _historical_priors(tariffs, dataset.history),
    )
    _explore(env, pilot_candidates)
    candidates = pilot_candidates + small_candidates
    final = sanitize_campaigns(_plan(env, candidates), env.tariffs)[:MAX_CAMPAIGNS]
    pilots = internals.executed_pilot_campaigns()
    trace = pilot_trace(candidates, pilots, env.pilot_history, final)
    fingerprint = hashlib.sha256()
    for frame in (dataset.profile, dataset.tariffs, dataset.history):
        fingerprint.update(json.dumps(list(frame.columns)).encode())
        fingerprint.update(pd.util.hash_pandas_object(frame, index=False).values.tobytes())

    all_campaigns = pd.DataFrame(pilots + final)
    for column in (
        "filter_arpu_segment", "filter_data_segment", "filter_call_segment",
        "filter_current_tariff", "explicit_ids",
    ):
        if column not in all_campaigns:
            all_campaigns[column] = None
    score = score_campaigns(
        all_campaigns, env.customer_profile, mock_model, env.tariffs,
        float(env.customer_profile["predicted_arpu"].sum()), _mock_fallback,
        team_id="dashboard-local-mock",
    )
    scored_detail = {item["name"]: item for item in score["campaigns_detail"]}
    tariff_detail = env.tariffs.set_index("tariff_plan_code")
    by_key = {
        (item["filter_current_tariff"], item["filter_arpu_segment"], item["target_tariff"]): item
        for item in candidates
    }
    pilot_results: dict[tuple[str, str, str], list[dict]] = {}
    for campaign, result in zip(pilots, env.pilot_history):
        key = (
            campaign["filter_current_tariff"], campaign["filter_arpu_segment"],
            campaign["target_tariff"],
        )
        actual_n = int(result["n_customers"])
        pilot_results.setdefault(key, []).append({
            "n": actual_n,
            "observed_lift_pct": 100 * float(result["observed_lift_ratio"]),
            "standard_error_pct": 100 * PILOT_STD_PER_CUSTOMER / math.sqrt(actual_n),
            "cost": float(result["cost"]),
        })

    rows = []
    for index, campaign in enumerate(final, start=1):
        key = (
            campaign["filter_current_tariff"], campaign["filter_arpu_segment"],
            campaign["target_tariff"],
        )
        candidate = by_key[key]
        detail = scored_detail[campaign["campaign_name"]]
        n = int(detail["n_contacts"])
        mean_sms, std_sms = _posterior(candidate)
        factor = (
            env.channels[campaign["channel"]]["conversion_multiplier"]
            / env.channels["sms"]["conversion_multiplier"]
        )
        mean, std = mean_sms * factor, std_sms * factor
        arpu_sum = float(candidate["arpu_prefix"][n - 1]) if n else 0.0
        cost = float(detail["cost"])
        expected_net = arpu_sum * mean - cost
        cautious_net = arpu_sum * (mean - PILOTED_RISK_PENALTY * std) - cost
        members = env.customer_profile.loc[
            (env.customer_profile["current_tariff"] == key[0])
            & (env.customer_profile["arpu_segment"] == key[1])
        ]
        data_volume = pd.to_numeric(members["DATA_VOLUME"], errors="coerce").dropna()
        data_segments = members.loc[members["data_segment"].isin(["NON_USER", "LITE", "HEAVY"]), "data_segment"]
        rows.append({
            "rank": index,
            "source": key[0], "segment": key[1], "target": key[2],
            "channel": campaign["channel"],
            "name": campaign["campaign_name"],
            "contacts": n, "audience": int(candidate["audience_size"]),
            "cost": cost, "cost_per_contact": float(env.channels[campaign["channel"]]["cost_per_contact"]),
            "expected_net": expected_net,
            "cautious_net": cautious_net,
            "posterior_lift_pct": 100 * mean,
            "posterior_std_pct": 100 * std,
            "prior_lift_pct": 100 * float(candidate["prior_lift_ratio"]) * factor,
            "tariff_context": {
                "current_price": float(tariff_detail.loc[key[0], "price_tariff"]),
                "target_price": float(tariff_detail.loc[key[2], "price_tariff"]),
                "current_data_gb": float(tariff_detail.loc[key[0], "Data_in_PKG"]) / 1024,
                "target_data_gb": float(tariff_detail.loc[key[2], "Data_in_PKG"]) / 1024,
                "data_user_pct": 100 * float((data_volume > 0).mean()) if len(data_volume) else None,
                "heavy_user_pct": 100 * float((data_segments == "HEAVY").mean()) if len(data_segments) else None,
                "data_known_count": len(data_volume),
            },
            "pilot_count": int(candidate["pilot_count"]),
            "pilots": pilot_results.get(key, []),
            "capped": bool(detail["capped_at_campaign_limit"] or detail["capped_at_reach_budget"] or detail["capped_at_money_budget"]),
        })

    selected = {row["name"] for row in rows}
    rejected = []
    for candidate in pilot_candidates:
        name = (
            f"main_{candidate['filter_current_tariff']}_{candidate['filter_arpu_segment']}_"
            f"{candidate['target_tariff']}"
        )
        if name in selected or not candidate.get("is_rejected"):
            continue
        mean, std = _posterior(candidate)
        n = min(int(candidate["audience_size"]), 5000)
        rejected.append({
            "source": candidate["filter_current_tariff"],
            "segment": candidate["filter_arpu_segment"],
            "target": candidate["target_tariff"],
            "posterior_lift_pct": 100 * mean,
            "posterior_std_pct": 100 * std,
            "pilots": pilot_results.get((
                candidate["filter_current_tariff"], candidate["filter_arpu_segment"],
                candidate["target_tariff"],
            ), []),
            "potential_sms_cost": n * float(env.channels["sms"]["cost_per_contact"]),
            "reason": "После первого пилота μ + 1σ < 0; повторный пилот не проводился.",
        })

    return {
        "mode": "Локальная симуляция · не прогноз результата на скрытом судействе",
        "seed": seed,
        "model_version": MODEL_VERSION,
        "dataset_fingerprint": fingerprint.hexdigest(),
        "pilot_trace": trace,
        "dataset": dataset.metadata,
        "limits": {
            "campaigns": MAX_CAMPAIGNS, "contacts": MAX_TOTAL_CONTACTS,
            "budget": TOTAL_BUDGET, "pilots": 20,
        },
        "summary": {
            "campaigns": len(rows),
            "contacts": int(score["total_contacts"]),
            "budget_used": float(score["total_cost"]),
            "net_arpu_gain": float(score["net_arpu_gain"]),
            "pilots": len(pilots),
            "tested_hypotheses": sum(item["pilot_count"] > 0 for item in pilot_candidates),
            "unique_customers": int(score["unique_customers_targeted"]),
            "rejected": len(rejected),
            "potential_sms_cost_avoided": sum(row["potential_sms_cost"] for row in rejected),
        },
        "campaigns": rows,
        "rejected": rejected,
        "submission": final,
    }


class DashboardHandler(BaseHTTPRequestHandler):
    @property
    def journal(self):
        return getattr(self.server, "journal", JOURNAL)

    def _json(self, value, status=200):
        self._reply(json.dumps(value, ensure_ascii=False, allow_nan=False).encode(),
                    "application/json; charset=utf-8", status)

    def _reply(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        route = urlsplit(self.path)
        if route.path in ("/api/runs", "/api/pilots.csv"):
            try:
                run_id = parse_qs(route.query).get("id", [None])[0]
                if route.path == "/api/pilots.csv":
                    saved = self.journal.get(run_id or "")
                    stream = io.StringIO()
                    fields = ["sequence", "source", "segment", "target", "channel", "round", "n", "cost",
                              "before_pct", "std_before_pct", "observed_pct", "observation_se_pct",
                              "after_pct", "std_after_pct", "error_pp", "decision", "provenance",
                              "run_id", "seed", "model_version"]
                    writer = csv.DictWriter(stream, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(dict(row, provenance="simulation", run_id=saved["id"],
                                          seed=saved["seed"], model_version=saved["model_version"])
                                     for row in saved["pilots"])
                    self._reply(stream.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8")
                else:
                    self._json(self.journal.get(run_id) if run_id else self.journal.list())
            except KeyError:
                self._json({"error": "Запись журнала не найдена."}, 404)
            except (sqlite3.Error, OSError):
                self._json({"error": "Не удалось прочитать локальный журнал."}, 500)
            return
        if route.path == "/api/data-schema":
            self._json(SCHEMAS)
            return
        if route.path == "/api/template.csv":
            kind = parse_qs(route.query).get("kind", [""])[0]
            if kind not in SCHEMAS:
                self._json({"error": "Неизвестный тип файла."}, 400)
                return
            stream = io.StringIO()
            csv.writer(stream).writerow(SCHEMAS[kind]["columns"])
            self._reply(stream.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8")
            return
        if route.path in ("/", "/index.html", "/styles.css", "/app.js", "/history.js"):
            name = "index.html" if route.path == "/" else route.path.lstrip("/")
            mime = {"index.html": "text/html", "styles.css": "text/css", "app.js": "text/javascript", "history.js": "text/javascript"}[name]
            self._reply((STATIC / name).read_bytes(), f"{mime}; charset=utf-8")
            return
        if route.path not in ("/api/plan", "/api/submission.csv"):
            self._reply(b"Not found", "text/plain", 404)
            return
        try:
            query = parse_qs(route.query)
            seed = int(query.get("seed", ["42"])[0])
            if not 0 <= seed <= 10000:
                raise ValueError("Номер сценария должен быть от 0 до 10 000.")
            dataset_id = query.get("dataset", ["demo"])[0]
            if dataset_id != "demo":
                UPLOADS.get(dataset_id)  # Check expiry even when a report is cached.
            report = build_dashboard(seed, dataset_id)
        except KeyError:
            self._json({"error": "Данные больше не доступны. Загрузите файлы заново."}, 404)
            return
        except (ValueError, TypeError) as exc:
            self._json({"error": str(exc)}, 400)
            return
        if route.path == "/api/plan":
            self._json(report)
        else:
            stream = io.StringIO()
            writer = csv.DictWriter(stream, fieldnames=CAMPAIGN_COLUMNS)
            writer.writeheader()
            for campaign in report["submission"]:
                writer.writerow({key: campaign.get(key, "") for key in CAMPAIGN_COLUMNS})
            self._reply(stream.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8")

    def do_POST(self) -> None:
        route = urlsplit(self.path).path
        if route not in ("/api/datasets", "/api/runs"):
            self._json({"error": "Страница не найдена."}, 404)
            return
        origin = self.headers.get("Origin")
        port = self.server.server_address[1]
        if origin and origin not in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}:
            self._json({"error": "Откройте загрузку в локальном приложении."}, 403)
            return
        if self.headers.get_content_type() != "application/json":
            self._json({"error": "Ожидается JSON с CSV-файлами."}, 415)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_UPLOAD_BYTES:
                self._json({"error": "Общий размер загрузки превышен. Используйте файлы до 15 МБ каждый и до 30 МБ вместе."}, 413)
                return
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if route == "/api/runs":
                if not isinstance(payload, dict) or type(payload.get("seed")) is not int:
                    raise ValueError("Укажите целый номер сценария.")
                seed, dataset_id = payload["seed"], payload.get("dataset", "demo")
                if not 0 <= seed <= 10000 or not isinstance(dataset_id, str):
                    raise ValueError("Некорректный сценарий или набор данных.")
                if dataset_id != "demo":
                    UPLOADS.get(dataset_id)
                self._json(self.journal.save(build_dashboard(seed, dataset_id)), 201)
                return
            dataset = validate_upload(payload)
        except KeyError:
            self._json({"error": "Набор данных больше не доступен. Загрузите его заново."}, 404)
            return
        except (sqlite3.Error, OSError):
            self._json({"error": "Не удалось сохранить журнал на диске. План остаётся доступен."}, 500)
            return
        except DatasetError as exc:
            self._json({"error": str(exc), "issues": exc.issues}, 422)
            return
        except (UnicodeDecodeError, ValueError):
            self._json({"error": "Не удалось прочитать загрузку. Выберите CSV в UTF-8 и повторите."}, 400)
            return
        self._json(UPLOADS.add(dataset), 201)


def main() -> None:
    host, port = "127.0.0.1", 8765
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"Beeline demo dashboard: http://{host}:{port}")
    print("Локальная мок-симуляция; Ctrl+C для остановки.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
