"""Compare trusted agent.py snapshots using paired seeds and the unchanged mock.

Example: python benchmark_agents.py --baseline-ref HEAD --runs 10
Only agent.py is taken from a Git ref; dependencies and public data come from
the current checkout for both agents. This is not a hidden-judge prediction.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass
import hashlib
import io
import json
import math
import os
from pathlib import Path
import random
import statistics
import subprocess
import sys
import time
import types

import numpy as np
import pandas as pd

from environment import MAX_PILOTS, MAX_PILOT_CUSTOMERS
from mock_environment import make_mock_env, _mock_fallback, _mock_impact_model
from scoring_core import (
    MAX_CAMPAIGNS, MAX_CUSTOMERS_PER_CAMPAIGN, MAX_TOTAL_CONTACTS,
    TOTAL_BUDGET, sanitize_campaigns, score_campaigns, validate_strategy,
)


ROOT = Path(__file__).resolve().parent
FILTER_COLUMNS = [
    "filter_arpu_segment", "filter_data_segment", "filter_call_segment",
    "filter_current_tariff", "explicit_ids",
]


@dataclass(frozen=True)
class Snapshot:
    source: str
    ref: str
    commit: str
    sha256: str

    def metadata(self):
        return {"ref": self.ref, "commit": self.commit, "agent_sha256": self.sha256}


def _git(*args):
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout


def load_snapshot(ref=None):
    """Read a trusted Git revision without changing branches or writing files."""
    commit = _git("rev-parse", "--verify", "--end-of-options", f"{ref or 'HEAD'}^{{commit}}")
    commit = commit.decode("ascii").strip()
    raw = (ROOT / "agent.py").read_bytes() if ref is None else _git("show", f"{commit}:agent.py")
    return Snapshot(raw.decode("utf-8-sig"), ref or "working-tree", commit,
                    hashlib.sha256(raw).hexdigest())


@contextmanager
def _execution_context(seed):
    """Keep relative data paths and non-env random generators identical."""
    previous_cwd = Path.cwd()
    python_state, numpy_state = random.getstate(), np.random.get_state()
    try:
        os.chdir(ROOT)
        random.seed(seed)
        np.random.seed(seed % (2 ** 32))
        yield
    finally:
        random.setstate(python_state)
        np.random.set_state(numpy_state)
        os.chdir(previous_cwd)


def evaluate_snapshot(snapshot, seed):
    """Keep failures in the report; already-executed pilots still earn a score."""
    row = {
        "seed": seed, "net_arpu_gain": None, "agent_error": None,
        "evaluation_error": None, "invalid_output": [], "limit_violations": [],
        "empty_final_plan": True, "pilots": 0, "final_campaigns": 0,
        "raw_final_campaigns": 0, "discarded_campaigns": 0,
        "total_contacts": 0, "total_cost": 0.0, "caps": [],
    }
    started = time.perf_counter()
    log = io.StringIO()
    module_name = "_benchmark_agent_snapshot"
    previous_module = sys.modules.get(module_name)
    try:
        with _execution_context(seed), redirect_stdout(log):
            env, internals = make_mock_env(
                seed=seed, data_dir=str(ROOT / "data"),
                profile_path=str(ROOT / "customer_profile.csv"),
            )
            raw = []
            try:
                # A fresh module and Agent each run avoid cross-seed state leakage.
                module = types.ModuleType(module_name)
                module.__file__ = str(ROOT / "agent.py")
                sys.modules[module_name] = module
                exec(compile(snapshot.source, module.__file__, "exec"), module.__dict__)
                raw = module.Agent().act(env)
            except Exception as exc:
                row["agent_error"] = f"{type(exc).__name__}: {exc}"

            if not isinstance(raw, list):
                row["invalid_output"].append(f"Expected list, got {type(raw).__name__}")
                raw = []
            row["raw_final_campaigns"] = len(raw)
            if len(raw) > MAX_CAMPAIGNS:
                row["limit_violations"].append("final_campaigns")
            valid = []
            for index, campaign in enumerate(raw):
                try:
                    clean = sanitize_campaigns([campaign], env.tariffs)
                except Exception as exc:
                    clean = []
                    row["invalid_output"].append(f"Campaign {index}: {type(exc).__name__}: {exc}")
                if not clean:
                    row["discarded_campaigns"] += 1
                    row["invalid_output"].append(f"Campaign {index}: rejected by sanitizer")
                    continue
                # Flag invalid filters even though the official sanitizer retains them.
                try:
                    validate_strategy(pd.DataFrame(clean), env.tariffs)
                except Exception as exc:
                    row["invalid_output"].append(f"Campaign {index}: {type(exc).__name__}: {exc}")
                valid.extend(clean)
            row["discarded_campaigns"] += max(0, len(valid) - MAX_CAMPAIGNS)
            valid = valid[:MAX_CAMPAIGNS]
            pilots = internals.executed_pilot_campaigns()
            row["pilots"], row["final_campaigns"] = len(pilots), len(valid)
            row["empty_final_plan"] = not valid
            if len(pilots) > MAX_PILOTS or env.pilots_left < 0:
                row["limit_violations"].append("pilots")
            if env.remaining_budget < -1e-9:
                row["limit_violations"].append("pilot_budget")
            if env.remaining_contacts < 0:
                row["limit_violations"].append("pilot_contacts")
            if any(len(pilot.get("explicit_ids", [])) > MAX_PILOT_CUSTOMERS for pilot in pilots):
                row["limit_violations"].append("pilot_size")

            all_campaigns = pd.DataFrame(pilots + valid)
            if all_campaigns.empty:
                row["net_arpu_gain"] = 0.0
            else:
                for column in FILTER_COLUMNS:
                    if column not in all_campaigns:
                        all_campaigns[column] = None
                model = _mock_impact_model(pd.read_csv(ROOT / "data" / "change_tariff.csv"))
                result = score_campaigns(
                    all_campaigns, env.customer_profile, model, env.tariffs,
                    env.customer_profile["predicted_arpu"].sum(), _mock_fallback,
                    team_id="paired-benchmark",
                )
                net = float(result["net_arpu_gain"])
                if not math.isfinite(net):
                    raise ValueError("Scorer produced a non-finite net gain")
                row["net_arpu_gain"] = net
                row["total_contacts"] = int(result["total_contacts"])
                row["total_cost"] = float(result["total_cost"])
                if row["total_contacts"] > MAX_TOTAL_CONTACTS:
                    row["limit_violations"].append("contacts")
                if row["total_cost"] > TOTAL_BUDGET + 1e-9:
                    row["limit_violations"].append("budget")
                for index, detail in enumerate(result["campaigns_detail"]):
                    if detail["n_contacts"] > MAX_CUSTOMERS_PER_CAMPAIGN:
                        row["limit_violations"].append(f"campaign_size:{index}")
                    caps = [key for key in (
                        "capped_at_campaign_limit", "capped_at_reach_budget", "capped_at_money_budget"
                    ) if detail.get(key)]
                    if caps:
                        row["caps"].append({"campaign_index": index, "limits": caps})
    except Exception as exc:
        row["evaluation_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if previous_module is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous_module
    row["elapsed_seconds"] = time.perf_counter() - started
    if row["elapsed_seconds"] > 600:
        row["limit_violations"].append("runtime")
    row["log"] = log.getvalue()
    return row


def _statistics(values):
    """Never silently compute metrics on only the successful subset."""
    complete = bool(values) and all(value is not None and math.isfinite(value) for value in values)
    return {
        "complete": complete,
        "min": min(values) if complete else None,
        "mean": statistics.mean(values) if complete else None,
        "median": statistics.median(values) if complete else None,
        "p05": float(np.percentile(values, 5)) if complete else None,
        "positive": sum(value > 0 for value in values) if complete else None,
    }


def summarize(rows):
    return {
        "runs": len(rows), **_statistics([row["net_arpu_gain"] for row in rows]),
        "agent_failures": sum(row["agent_error"] is not None for row in rows),
        "evaluation_failures": sum(row["evaluation_error"] is not None for row in rows),
        "empty_final_plans": sum(row["empty_final_plan"] for row in rows),
        "invalid_output_runs": sum(bool(row["invalid_output"]) for row in rows),
        "limit_violation_runs": sum(bool(row["limit_violations"]) for row in rows),
    }


def run_benchmark(baseline, candidate, runs=10, seed_start=0):
    if runs <= 0 or seed_start < 0:
        raise ValueError("runs must be positive and seed_start must be non-negative")
    rows = {"baseline": [], "candidate": []}
    deltas = []
    for seed in range(seed_start, seed_start + runs):
        for name, snapshot in (("baseline", baseline), ("candidate", candidate)):
            rows[name].append(evaluate_snapshot(snapshot, seed))
        old = rows["baseline"][-1]["net_arpu_gain"]
        new = rows["candidate"][-1]["net_arpu_gain"]
        deltas.append({"seed": seed, "delta": None if old is None or new is None else new - old})
    return {
        "schema_version": 1,
        "warning": "Synthetic mock effects, not hidden-judge performance. Only agent.py is snapshotted; dependencies are shared from the current checkout.",
        "seed_start": seed_start, "runs": runs,
        "snapshots": {"baseline": baseline.metadata(), "candidate": candidate.metadata()},
        "runtime": {"python": sys.version, "numpy": np.__version__, "pandas": pd.__version__},
        "shared_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ("environment.py", "mock_environment.py", "scoring_core.py",
                         "customer_profile.csv", "data/change_tariff.csv", "data/dict_tariff.csv")
        },
        "summary": {name: summarize(items) for name, items in rows.items()},
        "paired_delta": {"runs": runs, **_statistics([item["delta"] for item in deltas]), "by_seed": deltas},
        "rows": rows,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", required=True, help="Trusted Git ref containing baseline agent.py")
    parser.add_argument("--candidate-ref", help="Trusted Git ref; default: current working agent.py")
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument("--output", type=Path, help="Optional JSON report path (not written by default)")
    args = parser.parse_args(argv)
    if args.runs <= 0 or args.seed_start < 0:
        parser.error("--runs must be positive and --seed-start must be non-negative")
    try:
        baseline, candidate = load_snapshot(args.baseline_ref), load_snapshot(args.candidate_ref)
    except (OSError, subprocess.CalledProcessError, UnicodeError) as exc:
        parser.error(f"Cannot load agent snapshots: {exc}")
    report = run_benchmark(baseline, candidate, args.runs, args.seed_start)
    serialized = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
    print(report["warning"])
    for name, summary in report["summary"].items():
        print(f"{name}: " + json.dumps(summary, ensure_ascii=False))
    print("paired_delta: " + json.dumps({key: value for key, value in report["paired_delta"].items() if key != "by_seed"}))
    print("snapshots: " + json.dumps(report["snapshots"]))
    for name, rows in report["rows"].items():
        for row in rows:
            print(f"{name} seed={row['seed']}: " + json.dumps({key: value for key, value in row.items() if key != "log"}))
    return 1 if any(
        not summary["complete"] or summary["agent_failures"] or summary["invalid_output_runs"]
        or summary["limit_violation_runs"] or summary["empty_final_plans"]
        for summary in report["summary"].values()
    ) else 0


if __name__ == "__main__":
    raise SystemExit(main())
