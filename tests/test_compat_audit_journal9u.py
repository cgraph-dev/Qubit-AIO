"""Regression: the compat audit path must actually write the journal.

``voicebridge.qubit_compat._audit`` called ``log_event(event, {dict})``, but
``log_event(event, **fields)`` accepts keyword fields only, so every compat
audit write raised ``TypeError`` and was swallowed by the bare ``except``.
This proves the writer now emits the event with keyword fields.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def test_compat_audit_writes_event_with_keyword_fields(monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import logging_setup as LS

    captured = []

    def _capture(event, **fields):
        captured.append((event, fields))

    monkeypatch.setattr(LS, "log_event", _capture)
    HC._audit("probe", "dashboard:user", {"thing": "value"})
    HC.audit_event("probe_public", "cli:user", {"n": 3})

    assert captured, "compat audit wrote nothing (positional-dict bug returned)"
    event, fields = captured[0]
    assert event == "qubit_compat.probe"
    assert fields["actor"] == "dashboard:user"
    assert fields["thing"] == "value"
    event2, fields2 = captured[1]
    assert event2 == "qubit_compat.probe_public" and fields2["n"] == "3"


def test_compat_audit_redacts_secret_shaped_values(monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import logging_setup as LS

    captured = []
    monkeypatch.setattr(LS, "log_event", lambda event, **f: captured.append((event, f)))
    HC._audit("probe", "dashboard:user", {"note": "api_key=sk-live-SECRET123"})
    assert captured
    blob = str(captured[0][1])
    assert "sk-live-SECRET123" not in blob


def test_estop_blocks_confirm_gated_mutation(monkeypatch):
    """A latched e-stop refuses a compat mutation even with confirm=true."""
    from voicebridge import qubit_compat as HC

    monkeypatch.setattr(HC, "_estop", lambda _cfg: True)
    for body in ({}, {"confirm": True}):
        out = HC._compat_confirm(object(), "mcp_server_create", "d", {}, body)
        assert out is not None, body
        code, payload = out
        assert code == 403 and payload["code"] == "EMERGENCY_STOP", (code, payload)
