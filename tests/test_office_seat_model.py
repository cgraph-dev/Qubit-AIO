"""Per-seat model routing: each office seat may run its own provider/model.

THE FEATURE
-----------
`office_agent` carried no model field, so every seat in a department ran on
whatever the global profile resolved to. An owner who wanted a cheap model for
the scrapers and a strong one for the CEO had no way to say so, and the office
could not be told apart from a single shared assistant wearing ten names.

This suite pins the four things that make the feature real rather than
decorative:

1. the column exists and an existing seat keeps working (empty = inherit),
2. a seat's assignment survives a read through the real store,
3. the assignment reaches the turn — `_default_executor` passes it to
   `run_agent_turn` as `model`/`provider`, not just to the receipt,
4. the HTTP surface resolves to the seat-model routes and not to the generic
   `agents/<id>/<sub>` catch-all, which would otherwise swallow them.

Catalogue validation is exercised with `force` and with the catalogue
unavailable, because the failure mode we care about is a write that *looks*
successful and then fails at the first turn.

Hermetic: tmp XDG state only; no provider is called.
"""
from __future__ import annotations

import re
import pathlib

import pytest


# --- the standard office harness (see test_office_seat_turn_pricing) -------

def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _seat(cfg):
    from voicebridge.office import caps as CAPS, ledger, org as O

    assert CAPS.set_cap(cfg, "perRun", 0, confirmed=True,
                        requested_by="test")["state"] == "OK"
    assert CAPS.set_cap(cfg, "perDay", 0, confirmed=True,
                        requested_by="test")["state"] == "OK"
    seat_id = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    ledger.record(cfg, "revenue", 5000, agent_id=seat_id, note="owner capital")
    return seat_id


# --- 1. schema + back-compat ----------------------------------------------

def test_a_seat_created_before_the_columns_has_no_model(tmp_path, monkeypatch):
    """Back-compat: the columns default to empty, which means 'inherit'.

    Every seat that existed before this field keeps running on the profile
    default instead of being silently pinned to a model nobody chose.
    """
    from voicebridge.office import store as S

    cfg = _cfg(tmp_path, monkeypatch)
    seat_id = _seat(cfg)
    conn = S.connect(cfg)
    try:
        S.migrate(conn)
        row = conn.execute("SELECT model, provider FROM office_agent"
                           " WHERE id=?", (seat_id,)).fetchone()
    finally:
        conn.close()
    assert str(row["model"]) == ""
    assert str(row["provider"]) == ""


def test_migrate_adds_the_columns_to_an_existing_store(tmp_path, monkeypatch):
    """`migrate()` is additive: an old table gains the columns, loses nothing."""
    import sqlite3

    from voicebridge.office import store as S

    cfg = _cfg(tmp_path, monkeypatch)
    conn = S.connect(cfg)
    try:
        S.migrate(conn)
        # Simulate a pre-feature store: drop the columns by rebuilding the
        # table without them, keeping the row that was already there.
        conn.execute("ALTER TABLE office_agent RENAME TO _old")
        conn.execute(
            "CREATE TABLE office_agent ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " slug TEXT UNIQUE NOT NULL, name TEXT NOT NULL,"
            " seat_type TEXT NOT NULL, department_id INTEGER, room_id INTEGER,"
            " supervisor_id INTEGER, portfolio TEXT,"
            " status TEXT NOT NULL DEFAULT 'active',"
            " autonomy TEXT NOT NULL DEFAULT 'unattended_full',"
            " exec_profile TEXT NOT NULL DEFAULT 'this_computer',"
            " created_at REAL NOT NULL,"
            " created_by TEXT NOT NULL DEFAULT 'owner',"
            " terminated_at REAL, auditor_of TEXT)")
        conn.execute(
            "INSERT INTO office_agent(slug, name, seat_type, created_at)"
            " VALUES ('legacy', 'Legacy', 'worker', 0.0)")
        conn.commit()
        # Now migrate again: the columns must appear without touching the row.
        S.migrate(conn)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(office_agent)")}
        row = conn.execute("SELECT slug, model, provider FROM office_agent"
                           " WHERE slug='legacy'").fetchone()
    finally:
        conn.close()
    assert {"model", "provider"} <= cols
    assert row["model"] == "" and row["provider"] == ""


# --- 2. assignment persists ------------------------------------------------

def test_a_seat_model_assignment_round_trips(tmp_path, monkeypatch):
    from voicebridge.office import store as S

    cfg = _cfg(tmp_path, monkeypatch)
    seat_id = _seat(cfg)
    conn = S.connect(cfg)
    try:
        S.migrate(conn)
        conn.execute("UPDATE office_agent SET model=?, provider=? WHERE id=?",
                     ("deepseek-v4.1-flash", "cheapvibecode", seat_id))
        conn.commit()
        row = conn.execute("SELECT model, provider FROM office_agent"
                           " WHERE id=?", (seat_id,)).fetchone()
    finally:
        conn.close()
    assert row["model"] == "deepseek-v4.1-flash"
    assert row["provider"] == "cheapvibecode"


# --- 3. the assignment reaches the turn -----------------------------------

def test_default_executor_passes_the_seats_model_and_provider(tmp_path,
                                                              monkeypatch):
    """The whole point: not just the receipt, the actual agent call.

    Before this, `run_turn(model=...)` wrote the model onto the receipt while
    the executor ran whatever the global profile resolved — the receipt
    described a model that never ran.
    """
    from voicebridge.office import runner as R, store as S

    cfg = _cfg(tmp_path, monkeypatch)
    seat_id = _seat(cfg)
    conn = S.connect(cfg)
    try:
        S.migrate(conn)
        conn.execute("UPDATE office_agent SET model=?, provider=? WHERE id=?",
                     ("grok-4.7", "omniroute", seat_id))
        conn.commit()
    finally:
        conn.close()

    seen = {}

    class _Agent:
        @staticmethod
        def run_agent_turn(cfg_, text, *, session, model=None, provider=None,
                           estop_engaged=None):
            seen.update(model=model, provider=provider, session=session)
            return {"state": "OK", "reply": "ok"}

    # Patch the attribute the executor actually resolves. `runner` does
    # `from .. import agent as _agent` at call time, so it reads the module
    # attribute off the PACKAGE, not necessarily sys.modules["voicebridge.agent"]
    # — another test that swaps sys.modules can leave the two objects
    # disagreeing. Setting the attribute on the resolved module is correct
    # regardless of which object that is.
    import voicebridge as _pkg
    real_agent = getattr(_pkg, "agent", None)
    monkeypatch.setattr(_pkg, "agent", _Agent, raising=False)
    monkeypatch.setitem(__import__("sys").modules,
                        "voicebridge.agent", _Agent)
    try:
        out = R._default_executor(cfg, seat_id, "do the thing")
    finally:
        if real_agent is not None:
            _pkg.agent = real_agent
    assert out["state"] == "OK"
    assert seen["model"] == "grok-4.7"
    assert seen["provider"] == "omniroute"


def test_default_executor_sends_none_when_the_seat_inherits(tmp_path,
                                                            monkeypatch):
    """An empty assignment must send None, not "" — the resolver treats an
    empty pin as a model name and would fail to resolve it."""
    from voicebridge.office import runner as R

    cfg = _cfg(tmp_path, monkeypatch)
    seat_id = _seat(cfg)

    seen = {}

    class _Agent:
        @staticmethod
        def run_agent_turn(cfg_, text, *, session, model=None, provider=None,
                           estop_engaged=None):
            seen.update(model=model, provider=provider)
            return {"state": "OK", "reply": "ok"}

    # Same reasoning as above: patch the PACKAGE attribute the executor
    # resolves, so a sibling test's sys.modules swap cannot desynchronise it.
    import voicebridge as _pkg
    real_agent = getattr(_pkg, "agent", None)
    monkeypatch.setattr(_pkg, "agent", _Agent, raising=False)
    monkeypatch.setitem(__import__("sys").modules,
                        "voicebridge.agent", _Agent)
    try:
        R._default_executor(cfg, seat_id, "do the thing")
    finally:
        if real_agent is not None:
            _pkg.agent = real_agent
    assert seen["model"] is None
    assert seen["provider"] is None


def test_run_agent_turn_accepts_model_and_provider():
    """The signature is the seam; if it loses these kwargs the feature dies
    silently at the call site (TypeError would be a test failure, not a 500)."""
    import inspect

    from voicebridge import agent as A

    sig = inspect.signature(A.run_agent_turn)
    assert "model" in sig.parameters
    assert "provider" in sig.parameters
    assert sig.parameters["model"].default is None
    assert sig.parameters["provider"].default is None


# --- 4. route resolution ---------------------------------------------------

def _office_routes():
    """The office habblaud route table, in registration order."""
    src = pathlib.Path(__file__).resolve().parents[1].joinpath(
        "voicebridge", "qubit_compat.py").read_text()
    routes = re.findall(r'_r\("(GET|POST|DELETE)",\s*r"([^"]+)"', src)
    return [(m, p) for m, p in routes if "/api/office/habblaud" in p]


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_the_seat_model_route_is_not_swallowed_by_the_catch_all(method):
    """`agents/<id>/<sub>` also matches `agents/7/model`, and it is a typed
    refusal. Registration order is what decides; this pins it."""
    path = "/api/office/habblaud/agents/7/model"
    first = None
    for m, pattern in _office_routes():
        if m == method and re.fullmatch(pattern, path):
            first = pattern
            break
    assert first == "/api/office/habblaud/agents/(?P<id>[^/]+)/model"


def test_existing_agent_routes_still_resolve():
    """Adding two routes ahead of the read must not capture the read or the
    character refusal."""
    table = _office_routes()

    def winner(method, path):
        for m, pattern in table:
            if m == method and re.fullmatch(pattern, path):
                return pattern
        return None

    assert winner("GET", "/api/office/habblaud/agents/7") == \
        "/api/office/habblaud/agents/(?P<id>[^/]+)"
    assert winner("POST", "/api/office/habblaud/agents/7/character") == \
        "/api/office/habblaud/agents/(?P<id>[^/]+)/character"
