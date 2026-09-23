"""Real child-process smoke tests, independent of Streamlit installation."""
import shutil
import subprocess
from pathlib import Path

import pytest
from dashboard_ui import process
from dashboard_ui.data import ReportError


def worker_tree(tmp_path, monkeypatch, runner):
    pkg=tmp_path/'dashboard_ui';pkg.mkdir()
    for file in ['__init__.py','data.py','process.py']:
        shutil.copy(Path(process.__file__).parent/file,pkg/file)
    (pkg/'runner.py').write_text(runner)
    monkeypatch.setattr(process,'ROOT',tmp_path)


def test_real_worker_runs_once_and_returns_reimportable_report(tmp_path,monkeypatch):
    worker_tree(tmp_path,monkeypatch,'''from pathlib import Path
from dashboard_ui.data import normalize_report
def run_local(seed):
 p=Path('counter'); n=int(p.read_text()) if p.exists() else 0; p.write_text(str(n+1))
 return normalize_report({'source':'local_mock','seed':seed,'score':{'total_cost':0},'campaigns':[]},'local')
''')
    report=process.run_bounded(7,timeout=5)
    assert report['seed'] == 7 and report['source_kind'] == 'local'
    assert (tmp_path/'counter').read_text() == '1'


def test_real_worker_failure_is_recoverable(tmp_path,monkeypatch):
    worker_tree(tmp_path,monkeypatch,"def run_local(seed):\n raise RuntimeError('bad input')\n")
    with pytest.raises(ReportError,match='ошибкой'):process.run_bounded(timeout=5)


def test_real_timeout_kills_worker(tmp_path,monkeypatch):
    worker_tree(tmp_path,monkeypatch,"import time\ndef run_local(seed):\n time.sleep(30)\n")
    with pytest.raises(ReportError,match='таймауту'):process.run_bounded(timeout=.25)


@pytest.mark.parametrize('seed',[-1,'42',True])
def test_invalid_seed_cannot_start_process(seed):
    with pytest.raises(ReportError):process.run_bounded(seed)


@pytest.mark.parametrize('limit',[0,-1,541,float('inf'),float('nan')])
def test_invalid_timeout_cannot_start_process(limit):
    with pytest.raises(ReportError):process.run_bounded(timeout=limit)
