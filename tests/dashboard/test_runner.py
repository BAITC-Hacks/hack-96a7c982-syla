import pandas as pd

from dashboard_ui import runner


def test_one_local_run_calls_agent_once(monkeypatch, tmp_path):
    calls = []

    class FakeAgent:
        def act(self, env):
            calls.append(env)
            return []

    class FakeEnv:
        tariffs = pd.DataFrame({"tariff_plan_code": []})
        pilot_history = []

    class Internals:
        @staticmethod
        def executed_pilot_campaigns():
            return []

    monkeypatch.setattr(runner, "Agent", FakeAgent)
    monkeypatch.setattr(runner, "make_mock_env", lambda seed: (FakeEnv(), Internals()))
    monkeypatch.chdir(tmp_path)
    report = runner.run_local(42)
    assert len(calls) == 1
    assert report["campaigns"] == []
