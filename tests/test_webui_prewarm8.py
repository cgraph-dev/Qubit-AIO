"""The dashboard prewarms its slow read inventories once, off the request path."""
from __future__ import annotations

import time
from types import SimpleNamespace

from voicebridge import qubit_compat
from voicebridge.webui import WebUI


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _app():
    app = object.__new__(WebUI)
    app.orch = SimpleNamespace(cfg=SimpleNamespace(
        get=lambda *a, **k: False,
        state_dir=lambda: "/tmp/qubit-prewarm-test"))
    return app


def test_prewarm_reads_primes_every_slow_handler(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("QUBIT_WEBUI_PREWARM", "1")
    # One pass then stop: the test asserts the prime, not the cadence.
    monkeypatch.setenv("QUBIT_WEBUI_PREWARM_INTERVAL_S", "0")
    monkeypatch.setenv("QUBIT_WEBUI_PREWARM_LIVE_INTERVAL_S", "0")
    names = (
        "h_skills",
        "h_status",
        "h_terminal_profiles",
        "h_model_options",
        "h_mcp_servers",
        "h_office_tower",
        "h_gateway",
        "h_approvals_list",
        "h_model_info",
        "h_toolsets",
        "h_qubit_update_check",
        "h_config",
    )
    seen = []
    for name in names:
        monkeypatch.setattr(qubit_compat, name, lambda cfg, q, b, p, _n=name: seen.append(_n))
    _app()._prewarm_reads()
    assert _wait_for(lambda: len(seen) == len(names))
    assert set(seen) == set(names)


def test_prewarm_reads_can_be_disabled(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("QUBIT_WEBUI_PREWARM", "0")
    seen = []
    monkeypatch.setattr(qubit_compat, "h_skills", lambda *a, **k: seen.append("skills"))
    _app()._prewarm_reads()
    time.sleep(0.1)
    assert seen == []
