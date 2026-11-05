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
    app.orch = SimpleNamespace(cfg=object())
    return app


def test_prewarm_reads_primes_every_slow_handler(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("QUBIT_WEBUI_PREWARM", "1")
    seen = []
    for name in (
        "h_skills",
        "h_status",
        "h_terminal_profiles",
        "h_model_options",
        "h_mcp_servers",
        "h_office_tower",
    ):
        monkeypatch.setattr(qubit_compat, name, lambda cfg, q, b, p, _n=name: seen.append(_n))
    _app()._prewarm_reads()
    assert _wait_for(lambda: len(seen) == 6)
    assert set(seen) == {
        "h_skills",
        "h_status",
        "h_terminal_profiles",
        "h_model_options",
        "h_mcp_servers",
        "h_office_tower",
    }


def test_prewarm_reads_can_be_disabled(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("QUBIT_WEBUI_PREWARM", "0")
    seen = []
    monkeypatch.setattr(qubit_compat, "h_skills", lambda *a, **k: seen.append("skills"))
    _app()._prewarm_reads()
    time.sleep(0.1)
    assert seen == []
