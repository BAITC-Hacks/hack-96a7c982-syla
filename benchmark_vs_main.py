"""Compare checked-out experiment against a baseline agent file.

Usage:
  cp agent.py /tmp/experiment_agent.py
  git show main:agent.py > /tmp/main_agent.py
  python benchmark_vs_main.py /tmp/main_agent.py /tmp/experiment_agent.py

Runs identical mock seeds and reports stability plus failure rate.
"""
from __future__ import annotations
import importlib.util
import statistics
import math
import sys
from pathlib import Path
from local_eval import evaluate_agent

def load_agent(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Agent()

def run(path, name, seeds=range(20)):
    agent = load_agent(path, name)
    nets, rois, failures = [], [], 0
    for seed in seeds:
        try:
            result = evaluate_agent(agent, seed=seed, verbose=False)
            if result is None:
                failures += 1
                continue
            nets.append(float(result["net_arpu_gain"]))
            roi = float(result.get("roi", 0.0))
            if math.isfinite(roi):
                rois.append(roi)
        except Exception:
            failures += 1
    return {
        "runs": len(list(seeds)), "failures": failures,
        "positive": sum(x > 0 for x in nets),
        "median_net": statistics.median(nets) if nets else float("nan"),
        "min_net": min(nets) if nets else float("nan"),
        "max_net": max(nets) if nets else float("nan"),
        "stdev_net": statistics.pstdev(nets) if len(nets) > 1 else 0.0,
        "mean_roi": statistics.mean(rois) if rois else float("nan"),
    }

def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: benchmark_vs_main.py MAIN_AGENT.py EXPERIMENT_AGENT.py")
    rows = [("main", run(Path(sys.argv[1]), "baseline_agent")),
            ("experiment", run(Path(sys.argv[2]), "experiment_agent"))]
    print("version      runs failures positive median_net      min_net         stdev       mean_roi")
    for name, x in rows:
        print(f"{name:<11} {x['runs']:>4} {x['failures']:>8} {x['positive']:>8} "
              f"{x['median_net']:>14,.0f} {x['min_net']:>14,.0f} {x['stdev_net']:>14,.0f} {x['mean_roi']:>10.2f}")
    if all(r[1]["failures"] == 0 for r in rows):
        delta = rows[1][1]["median_net"] - rows[0][1]["median_net"]
        print(f"\nmedian delta experiment-main: {delta:+,.0f}")

if __name__ == "__main__":
    main()
