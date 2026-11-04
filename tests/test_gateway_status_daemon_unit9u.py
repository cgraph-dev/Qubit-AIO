"""``qubit gateway status`` and ``qubit agent gateway status`` both target the real bare
``qubit-gateway.service`` (the shipped ``voicebridge.gateway_daemon`` unit), never a
hash-suffixed ``qubit-gateway-1ebdcf4f`` target.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def test_agent_gateway_status_forwards_to_the_runtime(monkeypatch):
    """``qubit agent gateway status`` is the historical passthrough to runtime ``gateway status``."""
    from voicebridge import cli as CLI
    from voicebridge import qubit_runtime

    calls: list[list[str]] = []

    def fake_run(argv=None):
        calls.append(list(argv or []))
        return 0

    monkeypatch.setattr(qubit_runtime, "run", fake_run)

    assert CLI.main(["agent", "gateway", "status"]) == 0
    assert calls == [["gateway", "status"]]


def test_gateway_supervisor_status_uses_the_bare_daemon_unit():
    from voicebridge import gateway_supervisor as GS

    class _Cfg:
        def get(self, *path, default=None):
            return default

    runner_calls: list[list[str]] = []

    def runner(argv, timeout):
        runner_calls.append(list(argv))
        return (
            0,
            "ActiveState=active\nSubState=running\nMainPID=4242\n"
            "ExecMainStatus=0\nNRestarts=0\n",
            "",
        )

    status = GS.status(
        _Cfg(),
        runner=runner,
        probe=lambda host, port: True,
        daemon_reader=lambda: None,
        control_query=lambda: None,
    )

    assert status["service_unit"] == "qubit-gateway.service"
    assert "qubit-gateway-1ebdcf4f" not in " ".join(" ".join(call) for call in runner_calls)


def test_gateway_status_reports_the_daemon_unit_not_a_hash(monkeypatch, capsys):
    from voicebridge import cli as CLI
    from voicebridge import gateway_supervisor as GS

    def fake_status(cfg):
        return {
            "state": "OK",
            "running": True,
            "service_unit": "qubit-gateway.service",
            "scope": "user",
            "active_state": "active",
            "sub_state": "running",
        }

    monkeypatch.setattr(GS, "status", fake_status)

    assert CLI.main(["gateway", "status"]) == 0

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["service_unit"] == "qubit-gateway.service"
    assert "qubit-gateway-1ebdcf4f" not in out
