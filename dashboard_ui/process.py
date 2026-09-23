"""Run the existing UI runner in an isolated, time-bounded Python process."""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from dashboard_ui.data import ReportError, load_report_file

ROOT = Path(__file__).resolve().parents[1]


def run_bounded(seed: int = 42, timeout: float = 540.0) -> dict:
    """A timeout kills/waits for the worker; no background thread can spend later."""
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ReportError("Seed должен быть целым неотрицательным числом.")
    if not 0 < timeout <= 540:
        raise ReportError("Допустимый таймаут: от 0 до 540 секунд.")
    with tempfile.TemporaryDirectory(prefix="beeline-ui-") as tmp:
        output = Path(tmp) / "report.json"
        with (Path(tmp) / "worker.log").open("wb") as log:
            try:
                completed = subprocess.run(
                    [sys.executable, "-m", "dashboard_ui.process", "--worker", "--seed", str(seed), "--output", str(output)],
                    cwd=ROOT, stdout=log, stderr=log, timeout=timeout, check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ReportError("Расчёт остановлен по таймауту. Предыдущий отчёт сохранён.") from exc
            except OSError as exc:
                raise ReportError("Не удалось запустить Python-процесс расчёта.") from exc
        if completed.returncode:
            raise ReportError("Локальный расчёт завершился с ошибкой. Проверьте зависимости и файлы данных.")
        return load_report_file(output, "local")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true", required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from dashboard_ui.runner import run_local
    from dashboard_ui.data import export_json
    # Exactly one call to the existing runner; it owns one Agent.act invocation.
    report = run_local(args.seed)
    args.output.write_bytes(export_json(report))


if __name__ == "__main__":
    main()
