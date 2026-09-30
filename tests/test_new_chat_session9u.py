"""9U — explicit New Chat mints a NEW canonical session id.

Parity defect SESS-01/D1: `/chat` "New chat" and `/qubit-chat` "start fresh"
both converged on the fixed canonical id ``dashboard-chat``, so the SAME
transcript reopened and no new session was ever minted. SESS-04/SESS-05: the
canonical list hardcoded ``source="qubit-chat"`` for every row (masking the real
DB source) and included archived rows by default, while the reference dashboard
defaults ``archived="exclude"``.

This module pins the fixed contract on the canonical store
(:class:`voicebridge.qubit_session_store.QubitSessionStore`) and the terminal
session mapping (:mod:`voicebridge.terminal_bridge`): an explicit New Chat mints
a genuinely NEW id (via the store ``create`` call the
``POST /api/qubit/chat/sessions`` route makes), an implicit first load is
FRESH (Hermes parity — no resume env, the TUI forges a new session),
refresh/re-ensure preserves the current session (no duplicate), the real DB ``source`` is passed through, and archived
rows are excluded unless explicitly requested.

Hermetic: isolated XDG dirs + QUBIT_HOME, no network, no live services.
"""
from __future__ import annotations

import re

from voicebridge import chat_transport as CT
from voicebridge import qubit_session_store as QSS
from voicebridge import terminal_bridge as TB
from voicebridge.agent import canonical_session_id, resolve_session_id

_NEW_ID_RE = re.compile(r"^dashboard-[0-9a-f]{12}$")


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    home = tmp_path / "home" / ".hermes"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QUBIT_HOME", str(home))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


# ------------------------------------------------------- explicit New Chat mints
def test_explicit_new_chat_mints_a_new_canonical_session_id(tmp_path,
                                                            monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    store = CT.store(cfg)
    assert store.list()["sessions"] == []

    # Explicit New Chat mints through the canonical store — the exact call the
    # dashboard ``POST /api/qubit/chat/sessions`` route makes — and NEVER the
    # fixed canonical default the old client fell back to.
    first = store.create("")
    assert first["state"] == "OK"
    sid = first["session"]["id"]
    assert _NEW_ID_RE.match(sid), sid
    assert sid != CT.DEFAULT_SESSION

    second = store.create("")
    assert second["session"]["id"] != sid

    listed = {s["id"] for s in store.list()["sessions"]}
    assert {sid, second["session"]["id"]} <= listed
    # The fixed default is an implicit-load alias, not a minted session.
    assert CT.DEFAULT_SESSION not in listed


# ------------------------------------------- implicit load is fresh (Hermes)
def test_implicit_load_is_fresh_not_canonical_default(tmp_path,
                                                      monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    # Hermes parity (hermes_cli/web_routers/chat_ws.py): an empty resume means
    # a FRESH chat — the TUI forges a new session via session.create. Only an
    # explicit, non-empty id is kept verbatim. A resume env is emitted only
    # for a non-empty explicit id.
    assert resolve_session_id(cfg, "") == canonical_session_id(cfg)
    assert resolve_session_id(cfg, None) == CT.DEFAULT_SESSION
    assert resolve_session_id(cfg, "voice") == CT.DEFAULT_SESSION

    explicit = "dashboard-abcdef012345"
    assert TB.canonical_terminal_session(cfg, explicit)["session_id"] == explicit
    assert TB.canonical_terminal_session(cfg, "")["session_id"] == ""
    assert TB.canonical_terminal_session(cfg, None)["session_id"] == ""

    assert TB.terminal_resume_env("") == {}
    assert TB.terminal_resume_env(explicit) == {
        "QUBIT_TUI_RESUME": explicit, "HERMES_TUI_RESUME": explicit}


# ------------------------------------------------------- refresh preserves
def test_refresh_reensures_the_same_session_without_a_duplicate(tmp_path,
                                                                monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    store = CT.store(cfg)
    sid = store.create("Keep me")["session"]["id"]

    # A refresh / PTY respawn re-ensures the SAME id; it must not mint a new row.
    mapping = TB.canonical_terminal_session(cfg, sid)
    assert mapping["session_id"] == sid
    assert mapping["ensured"] is True
    assert [s["id"] for s in store.list()["sessions"]] == [sid]

    # An implicit (empty) load is FRESH: no row is ensured and no resume id
    # is returned — the TUI forges a new session instead of reopening the
    # old canonical default.
    implicit = TB.canonical_terminal_session(cfg, "")
    assert implicit["session_id"] == ""
    assert implicit["ensured"] is False
    assert {s["id"] for s in store.list()["sessions"]} == {sid}


# ------------------------------------------------------- real source passthrough
def test_list_returns_the_real_db_source_not_a_hardcoded_label(tmp_path,
                                                               monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    store = CT.store(cfg)
    sid = store.create("Source")["session"]["id"]

    # The native create path records the real source; list() must pass it
    # through rather than reporting the fixed "qubit-chat" label.
    assert QSS.SOURCE == "dashboard"
    assert store.list()["sessions"][0]["source"] == "dashboard"

    # A row written with a different real source (the terminal writes "cli") is
    # surfaced verbatim — the label is never overwritten.
    store._db.create_session("cli-sess-1", source="cli", display_name=None)
    by_id = {s["id"]: s["source"] for s in store.list()["sessions"]}
    assert by_id[sid] == "dashboard"
    assert by_id["cli-sess-1"] == "cli"


# ------------------------------------------------------- archived excluded
def test_list_excludes_archived_by_default_and_keeps_an_opt_in(tmp_path,
                                                               monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    store = CT.store(cfg)
    keep = store.create("Keep")["session"]["id"]
    gone = store.create("Gone")["session"]["id"]
    assert store.archive(gone, True)["state"] == "OK"

    # Default list excludes archived rows (reference: archived="exclude").
    listed = store.list()
    ids = {s["id"] for s in listed["sessions"]}
    assert keep in ids
    assert gone not in ids
    assert listed["total"] == 1

    # Explicit opt-in is the recovery surface.
    recovered = store.list(include_archived=True)
    rids = {s["id"] for s in recovered["sessions"]}
    assert {keep, gone} <= rids
    assert recovered["total"] == 2
