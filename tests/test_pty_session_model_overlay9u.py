"""Resumed /chat grants report the session's stored model + provider.

Live 2026-10-05: the MODEL card said `deepseek` while the terminal ran
`qubit-brain`, and the picker showed a mismatched pair
(`opencode-go / qubit-brain`). Two causes, both fixed:

1. The frontend never threaded `?resume=` into the ws-ticket mint, so the
   server could not overlay `sessions.model` (fixed in `api.wsTicket` +
   `TerminalChatPage`; asserted in the vitest suite).
2. `_session_stored_model` left the provider empty for local models (no
   billing row), so the overlay kept the API default provider. It now infers
   the provider from the profile registry (`_infer_provider_for_model`).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def test_infer_provider_maps_local_default_to_ollama():
    from voicebridge import pty_ws

    assert pty_ws._infer_provider_for_model("qubit-brain") == "ollama"


def test_infer_provider_unknown_is_empty_never_fabricated():
    from voicebridge import pty_ws

    assert pty_ws._infer_provider_for_model("no-such-model-xyz") == ""
    assert pty_ws._infer_provider_for_model("") == ""
    assert pty_ws._infer_provider_for_model(None) == ""


def test_infer_provider_maps_api_profile_from_registry():
    from voicebridge import pty_ws
    from voicebridge.providers import resolver as R

    # Every shipped default profile whose model is unique resolves to its
    # provider — so a resumed API session names the right provider too.
    for p in R.default_profiles():
        got = pty_ws._infer_provider_for_model(p.model)
        if got:  # ambiguous models ('' in registry) intentionally skip
            assert got == p.provider or got == "ollama", (p.id, got)


def test_ws_ticket_threads_resume_field():
    # Server contract: the mint reads `session`/`resume` (webui.py). The
    # frontend now sends `resume` (asserted in wsTicket.test.ts); this pins
    # the server-side field name so the two cannot drift.
    webui = (REPO_ROOT / "voicebridge" / "webui.py").read_text()
    assert 'body.get("session", "") or body.get("resume", "")' in webui


def test_stored_model_prefers_explicit_pair_route(tmp_path, monkeypatch):
    # An explicit provider+model pick writes its route into the row's
    # model_config (set_model). The grant must report THAT provider — not
    # the billing-history provider, and not the registry first-match guess
    # (grok-4.7 picked under cheapvibecode must not report opencode-go).
    import json
    import sqlite3

    from voicebridge import pty_ws
    from voicebridge import qubit_runtime as _QR

    db = tmp_path / "state.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, model TEXT, model_config TEXT)")
    con.execute(
        "CREATE TABLE session_model_usage (session_id TEXT, model TEXT, billing_provider TEXT, last_seen REAL)")
    con.execute(
        "INSERT INTO sessions (id, model, model_config) VALUES (?, ?, ?)",
        ("sess-pair", "grok-4.7", json.dumps({
            "provider": "cheapvibecode",
            "base_url": "https://cheapvibecode.ru/v1",
            "api_mode": "chat_completions"})))
    # A stale billing row naming another provider must NOT win.
    con.execute(
        "INSERT INTO session_model_usage (session_id, model, billing_provider, last_seen)"
        " VALUES (?, ?, ?, 1)",
        ("sess-pair", "grok-4.7", "opencode-go"))
    con.commit()
    con.close()
    monkeypatch.setattr(_QR, "use_qubit_home", lambda cfg=None: tmp_path)
    provider, model = pty_ws._session_stored_model("sess-pair")
    assert (provider, model) == ("cheapvibecode", "grok-4.7")


def test_poke_redraw_issues_resize_and_never_raises():
    from voicebridge import pty_ws

    calls = []

    class Bridge:
        def resize(self, cols=80, rows=24):
            calls.append((cols, rows))

    class Session:
        bridge = Bridge()

    assert pty_ws._poke_redraw(Session(), 100, 30) is True
    assert calls == [(100, 30)]

    class DeadBridge:
        def resize(self, **kw):
            raise OSError("gone")

    class DeadSession:
        bridge = DeadBridge()

    assert pty_ws._poke_redraw(DeadSession(), 80, 24) is False
    assert pty_ws._poke_redraw(object(), 80, 24) is False


def test_guard_overlays_provider_when_model_matches(tmp_path, monkeypatch):
    # Same model on two bills: the row's explicit provider must still win.
    # Without this the grant (and the spawn env) kept the default provider
    # and the terminal booted into an empty account while the funded route
    # sat in the row.
    import json
    import sqlite3

    from voicebridge import pty_ws
    from voicebridge import qubit_runtime as _QR

    db = tmp_path / "state.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, model TEXT, model_config TEXT)")
    con.execute(
        "CREATE TABLE session_model_usage (session_id TEXT, model TEXT, billing_provider TEXT, last_seen REAL)")
    con.execute(
        "INSERT INTO sessions (id, model, model_config) VALUES (?, ?, ?)",
        ("sess-same", "deepseek-v4.1-flash", json.dumps({
            "provider": "cheapvibecode",
            "base_url": "https://cheapvibecode.ru/v1",
            "api_mode": "chat_completions"})))
    con.commit()
    con.close()
    monkeypatch.setattr(_QR, "use_qubit_home", lambda cfg=None: tmp_path)

    server = object.__new__(pty_ws.PtyWsServer)
    server.cfg = object()
    server._context_guard = lambda cfg, profile_id=None: {
        "ok": True, "provider": "opencode-go", "model": "deepseek-v4.1-flash"}
    server._spawn = pty_ws.PtyWsServer._production_spawn.__get__(server)
    guard = server._resolve_guard("", session_id="sess-same")
    assert guard["provider"] == "cheapvibecode", guard
    assert guard["model"] == "deepseek-v4.1-flash"
    assert guard["session_pinned"] is True
    assert guard["default_provider"] == "opencode-go"
