"""Real Streamlit smoke/interaction tests. Skip explicitly when unavailable."""
from unittest.mock import patch
from pathlib import Path

import pytest

pytest.importorskip("streamlit", reason="Streamlit is not installed; this is not a browser pass")
from streamlit.testing.v1 import AppTest
from dashboard_ui.data import normalize_report

ROOT = Path(__file__).resolve().parents[2]


def app():
    return AppTest.from_file(str(ROOT / 'dashboard.py')).run(timeout=20)


def test_demo_loads_without_exception():
    at=app()
    assert not at.exception
    assert any("ДЕМОНСТРАЦИОННЫЕ" in x.value for x in at.markdown)


def test_mode_and_presentation_do_not_start_agent():
    with patch('dashboard_ui.process.run_bounded') as run:
        at=app()
        at.toggle[0].set_value(True).run()
        at.radio[0].set_value('Отчёт').run()
        assert not at.exception
        run.assert_not_called()


def test_one_click_runs_once_and_uploaded_demo_report_does_not_loop():
    payload=normalize_report({'source':'demo','score':{'total_cost':0},'campaigns':[]},'upload')
    with patch('dashboard_ui.process.run_bounded', return_value=payload) as run:
        at=app()
        next(x for x in at.button if x.label == 'Запустить расчёт').click().run(timeout=20)
        assert not at.exception
        at.toggle[0].set_value(True).run()
        assert not at.exception
        run.assert_called_once_with(42)
