# Dashboard recheck — isolated changes

Base: `dashboard` / `dashboard-polish` at `4f9c1a3194c7f48235a2cf2886f6574d9eb835b5`.
No changes to agent.py, environment.py, scoring_core.py, the participant data,
submission.csv, or benchmark logic. No new third-party dependencies.

## What changed

- Round-trip JSON export/import preserves KPI, campaigns, pilots, source and version.
- Demo provenance survives upload. Display mode is separate from provenance;
  opening an exported demo cannot cause a Demo/Report rerun loop.
- A half-missing, reversed or non-finite interval becomes unavailable as a whole.
- Invalid counts are unavailable, not a fabricated zero. Unknown channel expenses
  are omitted with an explicit incomplete-data note, not plotted as zero.
- Final + pilot costs/contacts are reconciled against score. The score is never
  overwritten with a subtotal. Resource violations and losses stay visible.
- Confidence labels describe an interval's position around zero rather than
  asserting an arbitrary probability of success.
- Selection resets when the report or filtered rows change. Upload identity uses
  contents, not just filename and byte count. Cached reports are bounded to 8.
- Russian labels, readable yellow button, KPI limits, negative-value styling,
  projector mode and detailed campaign facts; no external fonts/components.
- The existing runner is invoked in a separate process with a 540-second ceiling.
  A timeout kills/waits for the worker. It does not leave a live pilot thread.
- CSV text cells are protected from formula interpretation. JSON is finite, BOM
  input is supported, file reads are bounded and deep malformed JSON is rejected.

## Executed checks

```
python -m py_compile dashboard.py dashboard_ui/*.py
python -m pytest tests/dashboard/test_quality.py tests/dashboard/test_process.py tests/dashboard/test_app_quality.py -q
```

Result in the editing environment: **59 passed, 1 skipped**.
The child-process success, failure and real-timeout tests actually executed Python
workers. These are not benchmark results and not evidence of agent profitability.
The 59 tests are this recheck's data/process regressions, not the entire repository.

**Unverified here:** real Streamlit rendering, AppTest, desktop/mobile appearance.
Streamlit was absent and installation was unavailable from the package source.
The AppTest module was explicitly skipped; it was not counted as a pass.
No screenshots or performance improvements are claimed.

## Ubuntu verification (existing project virtual environment)

Use a separate Git worktree to leave the working dashboard checkout intact:

```
git fetch origin
git worktree add --detach ../beeline-dashboard-check origin/dashboard-polish
cd ../beeline-dashboard-check
../hack-96a7c982-syla/.venv/bin/python -m pytest tests/dashboard -q
../hack-96a7c982-syla/.venv/bin/python -m streamlit run dashboard.py --server.address 127.0.0.1 --server.port 8502
```

The sibling paths assume the original checkout is named `hack-96a7c982-syla`.
Port 8502 keeps the previously running dashboard on 8501 available for comparison.
If the worktree path already exists, use another new directory; do not delete or
reset an existing checkout. Inspect desktop and narrow layouts, round-trip JSON,
empty channel filter, negative campaign warning, presentation toggle, and one
explicit optimization run. Do not merge until this local check passes.

## Important upstream findings — not changed in this UI patch

1. Latest inspected agent blob `3b542d79b66d35b109a250a661b4b93c60e80f56`
   stores `std` and `mean` after selecting an option from a loop, but does not
   recover those fields from the selected option. Its exported uncertainty/CI can
   therefore belong to the last evaluated alternative. `risk_adjusted_lift` also
   does not reflect the extra alternative-target penalty. This is a reporting bug,
   not a reason to invent new intervals in the UI. Fix and test it in an isolated
   agent branch before treating its uncertainty metadata as authoritative.

2. The holdout report at `95698376b594844d03011c21cf39c9d3152e31db`
   uses fixed baseline `0b89100` and candidate `85916db`. Its advantage cannot be
   assigned to today's main just because the branch name is the same. Reported
   holdout medians were 2,691,533 vs 3,413,236 (200 seeds 1000–1199).
   These figures were read from a repository report, not rerun in this session.

3. That same report explicitly records 0/20 positive diagnostic runs for
   `historical_target_not_best` and 3/20 for `all_weak_or_negative`. Do not call the
   agent universally robust or claim a hidden-test victory.

4. Inspected main at `09c9bb2aafd4ca1c097ffc8b920e0c1c953bb59c` and absolute at
   `553ecdcc2ea2861841898c15010febb55b6cdefb` shared agent blob
   `4ba466b76d89b64419ac1055954355e509d44c14`. Equality of this file does not prove
   identical complete applications/dependencies or validate the old benchmark.
