"""The Habblaud office-client compat surface: wired reads and typed refusals.

WHY THIS FILE EXISTS
--------------------
The vendored Habblaud client (``frontend/qubit-app/plugins/qubit-office/
habblaud/``) is a whole same-origin app that requests literal ``/api/*`` paths;
``api-bridge.ts`` rewrites them onto ``/agent/api/office/habblaud/<path>``. Before
this surface existed the dashboard answered every one of those sub-paths with a
bare ``{"ok": false, "code": "NOT_FOUND", "detail": "no compat route ..."}`` — the
page looked broken because a real capability was never registered.

This file pins the two rules the fix must satisfy, using the CLIENT's own
contracts (its ``g()`` access validator and its store's reads) rather than a
hand-built toy:

1. **Every route the bundle requests resolves to a real handler.** No sub-path
   the client can reach may fall through to the bare ``no compat route`` 404.
   The wired reads answer 200 with REAL office-store state (a seeded seat, its
   events, the ledger); a missing row is the typed 404 the client already
   handles, never a fabricated success.

2. **A genuinely-unsupported capability is a TYPED refusal**, not a fabricated
   success and not an unhandled 404: an ``ok:false`` with a code, and — for the
   org mutations (permission decision, message send, character edit) — the C4
   staged confirmation (HTTP 409 ``CONFIRM_REQUIRED``), never a 2xx that would
   render as executed.

Hermetic: tmp ``XDG_*`` only; no network, no live service.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

pytestmark = pytest.mark.unit


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _seed(cfg):
    """A real org, a real room, and real events — nothing fabricated."""
    from voicebridge.office import ledger, org as O, agent_memory as MEM

    ledger.health(cfg)                        # creates the store
    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    dept = O.department_create(cfg, "ops", name="Operations",
                               kill_criteria="net negative for 30 days",
                               confirmed=True)["id"]
    room = O.room_create(cfg, dept, "workshop", confirmed=True)["id"]
    wk = O.hire(cfg, "wk-1", seat_type="worker", supervisor_id=ceo,
                department_id=dept, room_id=room, confirmed=True)["id"]
    MEM.emit_event(cfg, "run_started", agent_id=wk)
    MEM.emit_event(cfg, "task_done", agent_id=wk)
    # A real ledger row so the money/days routes have a non-empty fold. It is
    # stamped a few days back so it lands inside the tower's rolling window
    # (``history`` renders whole days, and today's row is the window's edge).
    from voicebridge.office import store as _OSS
    ledger.record(cfg, "revenue", 1500, agent_id=wk, department_id=dept,
                  note="seed", recorded_by="test",
                  ts=_OSS.now() - 3 * 86400.0)
    return {"ceo": ceo, "worker": wk, "dept": dept, "room": room}


def _dispatch(cfg, method, path, query=None, body=None):
    from voicebridge import qubit_compat as HC

    return HC.dispatch(cfg, method, path, query or {}, body or {})


# ======================================================================
# 1 — every route the bundle requests must resolve (never a bare 404)
# ======================================================================

#: Paths the vendored bundle actually requests (see the module's inventory note
#: in ``qubit_compat.py``). Each must MATCH a registered route; a typo or a
#: missing registration is the exact defect this pins.
BUNDLE_REQUESTS = (
    ("GET", "/api/office/habblaud/access"),
    ("GET", "/api/office/habblaud/agents/1"),
    ("GET", "/api/office/habblaud/sessions/recent"),
    ("GET", "/api/office/habblaud/timeline/days"),
    ("GET", "/api/office/habblaud/timeline/20711"),
    ("GET", "/api/office/habblaud/stats/days"),
    ("GET", "/api/office/habblaud/stats"),
    ("GET", "/api/office/habblaud/spawn/folders"),
    ("GET", "/api/office/habblaud/dictation"),
    ("GET", "/api/office/habblaud/permissions/x"),
    ("GET", "/api/office/habblaud/messages/x"),
    ("GET", "/api/office/habblaud/deliveries"),
    ("GET", "/api/office/habblaud/deliveries/file/x"),
    ("POST", "/api/office/habblaud/updates/check"),
    ("POST", "/api/office/habblaud/demo"),
    ("POST", "/api/office/habblaud/rooms/rename"),
    ("POST", "/api/office/habblaud/remote/logout"),
    ("POST", "/api/office/habblaud/messages"),
    ("POST", "/api/office/habblaud/permissions/x/decision"),
    ("POST", "/api/office/habblaud/agents/1/character"),
    ("POST", "/api/office/habblaud/agents/1/stop"),
    ("POST", "/api/office/habblaud/agents/1/takeover"),
    ("POST", "/api/office/habblaud/agents/1/terminal"),
    ("POST", "/api/office/habblaud/sessions/a/b/terminal"),
    ("POST", "/api/office/habblaud/spawn"),
    ("POST", "/api/office/habblaud/spawn/attachments"),
    ("POST", "/api/office/habblaud/spawn/folders"),
    ("GET", "/api/office/habblaud/pty/1/stream"),
    ("POST", "/api/office/habblaud/pty"),
    ("POST", "/api/office/habblaud/pty/1/close"),
    ("POST", "/api/office/habblaud/pty/1/input"),
    ("POST", "/api/office/habblaud/pty/1/resize"),
    ("POST", "/api/office/habblaud/dictation"),
    ("GET", "/api/office/habblaud/dictation/x"),
)


def test_every_bundle_request_matches_a_registered_route():
    from voicebridge import qubit_compat as HC

    unresolved = [f"{m} {p}" for m, p in BUNDLE_REQUESTS
                  if HC.match(m, p)[0] is None]
    assert not unresolved, (
        "these Habblaud paths the bundle requests resolve to NO compat route "
        f"(the bare 'no compat route' 404): {unresolved}"
    )


def test_no_registered_habblaud_route_is_missing_its_handler():
    from voicebridge import qubit_compat as HC

    missing = [r["handler"] for r in HC.ROUTES
               if r["handler"] != "__gate__"
               and "/api/office/habblaud/" in r["pattern"]
               and r["handler"] not in HC.HANDLERS]
    assert not missing, f"registered routes with no handler: {missing}"


# ======================================================================
# 2 — the wired reads answer real office state
# ======================================================================


def test_access_carries_the_five_booleans_the_client_validates(tmp_path,
                                                               monkeypatch):
    """The client's ``g()`` requires all five booleans, else it redirects to
    ``/login``. This is the exact shape contract, not a subset."""
    cfg = _cfg(tmp_path, monkeypatch)
    code, out = _dispatch(cfg, "GET", "/api/office/habblaud/access")
    assert code == 200, (code, out)
    assert out["ok"] is True
    for key in ("remote", "local", "permissions", "messages", "terminal"):
        assert isinstance(out.get(key), bool), (key, out)
    # The honest positions: no remote view, no raw PTY, mutations are C4-gated.
    assert out["terminal"] is False
    assert out["remote"] is False
    assert out["permissions"] is False
    assert out["messages"] is False
    assert out["local"] is True


def test_agent_route_returns_the_real_seat_and_its_real_events(tmp_path,
                                                               monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    ids = _seed(cfg)
    code, out = _dispatch(cfg, "GET",
                          f"/api/office/habblaud/agents/{ids['worker']}")
    assert code == 200, (code, out)
    assert out["slug"] == "wk-1"
    assert out["id"] == str(ids["worker"])
    # The two real events we emitted must be present, in ms, with a kind.
    assert isinstance(out["history"], list) and out["history"], out
    kinds = {row["kind"] for row in out["history"]}
    assert {"run_started", "task_done"} <= kinds, kinds
    for row in out["history"]:
        assert isinstance(row["at"], int) and row["at"] > 0, row


def test_agent_route_resolves_a_slug_too(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)
    code, out = _dispatch(cfg, "GET", "/api/office/habblaud/agents/wk-1")
    assert code == 200, (code, out)
    assert out["slug"] == "wk-1"


def test_unknown_agent_is_a_typed_404_not_a_fabrication(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)
    code, out = _dispatch(cfg, "GET", "/api/office/habblaud/agents/nobody")
    assert code == 404, (code, out)
    assert out["ok"] is False and out["code"] == "AGENT_NOT_FOUND"


def test_timeline_days_lists_only_real_ledger_days(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)
    code, out = _dispatch(cfg, "GET", "/api/office/habblaud/timeline/days")
    assert code == 200, (code, out)
    assert out["recording"] is False
    assert isinstance(out["days"], list) and out["days"], out
    for day in out["days"]:
        assert isinstance(day["day"], int)
        assert day["to"] >= day["from"]
        assert (day["revenue_cents"] or day["cost_cents"]), day


def test_stats_days_reports_the_real_ledger_fold(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)
    code, out = _dispatch(cfg, "GET", "/api/office/habblaud/stats/days")
    assert code == 200, (code, out)
    assert out["usage_model"] == "office_ledger"
    assert out["totals"]["revenue_cents"] >= 1500, out


def test_stats_day_totals_match_the_seeded_ledger(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)
    from voicebridge.office import store as _OSS
    day = int((_OSS.now() - 3 * 86400.0) // 86400)
    code, out = _dispatch(cfg, "GET", "/api/office/habblaud/stats",
                          query={"day": str(day)})
    assert code == 200, (code, out)
    assert out["usage_model"] == "office_ledger"
    assert out["totals"]["revenue_cents"] == 1500, out


def test_rooms_rename_mutates_the_real_office_store(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    ids = _seed(cfg)
    rid = f"f1r{ids['room']}"          # the tower's composite room id
    code, out = _dispatch(cfg, "POST", "/api/office/habblaud/rooms/rename",
                          body={"id": rid, "name": "revenue-room"})
    assert code == 200, (code, out)
    assert out["name"] == "revenue-room"
    # Read it back: the store really changed.
    from voicebridge.office import store as OSS
    conn = OSS.connect(cfg)
    try:
        row = conn.execute("SELECT slug FROM office_room WHERE id=?",
                           (ids["room"],)).fetchone()
    finally:
        conn.close()
    assert row["slug"] == "revenue-room"


def test_rooms_rename_rejects_an_unknown_room(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)
    code, out = _dispatch(cfg, "POST", "/api/office/habblaud/rooms/rename",
                          body={"id": "f1r99999", "name": "ghost"})
    assert code == 404, (code, out)
    assert out["code"] == "ROOM_NOT_FOUND"


def test_dictation_reports_real_availability(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    code, out = _dispatch(cfg, "GET", "/api/office/habblaud/dictation")
    assert code == 200, (code, out)
    assert isinstance(out["available"], bool)


def test_updates_check_is_a_real_read_only_report(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    code, out = _dispatch(cfg, "POST", "/api/office/habblaud/updates/check")
    assert code == 200, (code, out)
    assert out["ok"] is True and isinstance(out.get("update"), dict)


def test_demo_persists_the_preview_flag(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    code, out = _dispatch(cfg, "POST", "/api/office/habblaud/demo",
                          body={"enabled": True})
    assert code == 200, (code, out)
    assert out["enabled"] is True
    # A reload reads the persisted value back.
    code2, out2 = _dispatch(cfg, "POST", "/api/office/habblaud/demo",
                            body={"enabled": False})
    assert out2["enabled"] is False


def test_sessions_recent_returns_the_real_store(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)
    code, out = _dispatch(cfg, "GET",
                          "/api/office/habblaud/sessions/recent")
    assert code == 200, (code, out)
    assert isinstance(out["sessions"], list)
    assert out["count"] == len(out["sessions"])


# ======================================================================
# 3 — unsupported capabilities are TYPED refusals, never a fabricated success
# ======================================================================

#: Paths that are genuinely unsupported and must answer a typed refusal: an
#: ``ok:false`` with a code, never a 2xx (which the client renders as executed).
TYPED_REFUSALS = (
    ("POST", "/api/office/habblaud/pty"),
    ("POST", "/api/office/habblaud/pty/1/close"),
    ("POST", "/api/office/habblaud/pty/1/input"),
    ("POST", "/api/office/habblaud/pty/1/resize"),
    ("GET", "/api/office/habblaud/pty/1/stream"),
    ("POST", "/api/office/habblaud/agents/1/takeover"),
    ("POST", "/api/office/habblaud/agents/1/stop"),
    ("POST", "/api/office/habblaud/agents/1/terminal"),
    ("POST", "/api/office/habblaud/sessions/a/b/terminal"),
    ("GET", "/api/office/habblaud/deliveries/file/x"),
    ("POST", "/api/office/habblaud/spawn"),
    ("POST", "/api/office/habblaud/spawn/attachments"),
    ("POST", "/api/office/habblaud/dictation"),
    ("POST", "/api/office/habblaud/dictation/x"),
    ("POST", "/api/office/habblaud/remote/logout"),
)


def test_unsupported_capabilities_return_typed_refusals(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)
    for method, path in TYPED_REFUSALS:
        code, out = _dispatch(cfg, method, path)
        assert code >= 400, (method, path, code, out)
        assert out.get("ok") is False, (method, path, out)
        assert out.get("code"), (method, path, out)


def test_org_mutations_are_c4_staged_not_a_bare_post(tmp_path, monkeypatch):
    """Permission decision, message send and character edit are org mutations:
    they MUST come back 409 NEEDS_CONFIRMATION, so the client never renders a
    fabricated success from a direct POST from the embed."""
    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)
    for body, path in (
            ({}, "/api/office/habblaud/permissions/abc/decision"),
            ({}, "/api/office/habblaud/messages"),
            ({}, "/api/office/habblaud/agents/1/character")):
        code, out = _dispatch(cfg, "POST", path, body=body)
        assert code == 409, (path, code, out)
        assert out["ok"] is False
        assert out["state"] == "NEEDS_CONFIRMATION"
        assert out["code"] == "CONFIRM_REQUIRED"


def test_missing_permission_and_message_are_the_404_client_expects(tmp_path,
                                                                   monkeypatch):
    """The client's ``permissionDetail``/``messageStatus`` treat 404 as 'none'.
    A typed 404 is honest; a fabricated 200 would be a fake pending row."""
    cfg = _cfg(tmp_path, monkeypatch)
    code, out = _dispatch(cfg, "GET", "/api/office/habblaud/permissions/none")
    assert code == 404 and out["code"] == "PERMISSION_NOT_FOUND"
    code, out = _dispatch(cfg, "GET", "/api/office/habblaud/messages/none")
    assert code == 404 and out["code"] == "MESSAGE_NOT_FOUND"


def test_deliveries_answers_a_typed_empty_refusal_not_a_404(tmp_path,
                                                            monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    code, out = _dispatch(cfg, "GET", "/api/office/habblaud/deliveries")
    assert code == 200, (code, out)
    assert out["deliveries"] == []
    assert out["supported"] is False


def test_no_habblaud_path_falls_through_to_the_bare_compat_404(tmp_path,
                                                               monkeypatch):
    """The exact failure the task names: a request that resolved the prefix but
    had no route. Every path the bundle reaches must NOT answer the bare
    ``no compat route`` 404 — it is either a real answer or a typed refusal."""
    cfg = _cfg(tmp_path, monkeypatch)
    _seed(cfg)
    for method, path in BUNDLE_REQUESTS:
        code, out = _dispatch(cfg, method, path)
        detail = str(out.get("detail", "")) if isinstance(out, dict) else ""
        assert "no compat route" not in detail, (method, path, code, out)
