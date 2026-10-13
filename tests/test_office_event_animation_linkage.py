"""C7/L3 — every animated event kind has a real emitter, proven end to end.

The office looked dead for one reason: ``tower._ANIMATION`` (``tower.py:46-70``)
maps 15 event kinds to poses, and **seven of them were never emitted by
anything**. The animation existed; nothing fired it. So a real work turn could
not move anybody, and the vault/comms/shelf station walks were unreachable.

This file proves the fix in three directions, each capable of failing:

1. **Forward — each newly-wired kind appears in ``office_event`` after the
   action that causes it.** The action is the real production call (a turn, a
   money posting, an artifact, an escalation, a memory write) — never a
   hand-written INSERT — so a call site that stops emitting is caught.
2. **Through the tower — a row of each kind produces the animation AND the
   station ``_ANIMATION``/``_EVENT_STATION`` map it to**, asserted via the pure
   ``tower.walk_for`` / ``tower.activity_from_events`` folds.
3. **Weathering — no mapped kind is silently unreachable.** Every kind in
   ``_ANIMATION`` is either emitted by a real call site (this file exercised it)
   or explicitly listed as deliberately-unwired, so a new pose cannot be added
   without an emitter. ``meeting`` is the one deliberate exception (there is no
   boardroom yet, ``57-BOARDROOM.md``), and it is asserted AS an exception.

Hermetic: tmp XDG state only; no network, no provider call, no live service.
"""
from __future__ import annotations

import inspect

import pytest

#: The kinds this milestone wires, with the production module that must contain
#: the emitter. If the emitter moves out of the named module the test fails, so
#: "it is emitted somewhere" cannot hide a call site that drifted.
WIRED = {
    "memory_written": "agent_memory.py",
    "note_written": "runner.py",
    "skill_used": "agent_memory.py",
    "mcp_call": "agent_memory.py",
    "ledger_write": "receipts.py",
    "escalation": "escalation.py",
}

#: Mapped by ``_ANIMATION`` but deliberately NOT wired: a meeting is a real
#: boardroom event and there is no boardroom yet (``57-BOARDROOM.md``). It stays
#: unemitted AND unwired on purpose — the weathering rule, not a gap.
DELIBERATELY_UNWIRED = {"meeting"}


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _org(cfg):
    from voicebridge.office import ledger, org as O

    ledger.health(cfg)
    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    w = O.hire(cfg, "wk-1", seat_type="worker", supervisor_id=ceo,
               confirmed=True)["id"]
    ledger.record(cfg, "revenue", 100_000, agent_id=w)
    return {"ceo": ceo, "w": w}


def _kinds(cfg, agent_id=None):
    from voicebridge.office import store

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        if agent_id is None:
            rows = conn.execute("SELECT kind FROM office_event ORDER BY id")
        else:
            rows = conn.execute("SELECT kind FROM office_event WHERE agent_id=?"
                                " ORDER BY id", (int(agent_id),))
        return [r["kind"] for r in rows]
    finally:
        conn.close()


def _stub(tools=(), cost_usd=0.0042):
    def _call(cfg, agent_id, task_text):
        return {"state": "OK", "reply": "done",
                "receipt": {"model": "stub", "identity": "stub/model",
                            "tools": list(tools),
                            "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                      "cost_usd_est": cost_usd}}}
    return _call


# ================================================== 1. forward: each call site
def test_skill_used_and_mcp_call_appear_after_a_turn_that_really_used_them(
        tmp_path, monkeypatch):
    """A real turn whose receipt names a skill + an MCP tool emits both kinds."""
    from voicebridge.office import runner

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)

    assert "skill_used" not in _kinds(cfg, ids["w"])
    runner.run_turn(
        cfg, ids["w"], "do the thing",
        executor=_stub(tools=["skill_view",
                              "mcp__pixellab__create_character",
                              "read_file"]))
    kinds = _kinds(cfg, ids["w"])
    assert "skill_used" in kinds, "a turn used a skill and emitted no skill_used"
    assert "mcp_call" in kinds, "a turn called MCP and emitted no mcp_call"
    assert "read_file" not in kinds, "a plain tool was promoted into a walk"


def test_a_turn_that_used_no_skill_or_mcp_emits_neither(tmp_path, monkeypatch):
    """No event, no motion: a plain turn must not fire the capability poses."""
    from voicebridge.office import runner

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)

    runner.run_turn(cfg, ids["w"], "just think",
                    executor=_stub(tools=["read_file"]))
    kinds = _kinds(cfg, ids["w"])
    assert "skill_used" not in kinds and "mcp_call" not in kinds


def test_ledger_write_appears_after_money_is_booked(tmp_path, monkeypatch):
    from voicebridge.office import runner

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)

    before = _kinds(cfg, ids["w"]).count("ledger_write")
    runner.run_turn(cfg, ids["w"], "spend a little",
                    executor=_stub(cost_usd=0.0731), estimated_cents=50)
    after = _kinds(cfg, ids["w"]).count("ledger_write")
    assert after == before + 1, "a booked ledger row emitted no ledger_write"


def test_note_written_appears_after_an_artifact_is_attributed(
        tmp_path, monkeypatch):
    from voicebridge.office import runner

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)

    out = runner.run_turn(cfg, ids["w"], "write out.txt",
                          executor=_stub(tools=[]))
    assert "note_written" not in _kinds(cfg, ids["w"])
    runner.attribute_artifact(cfg, out["run_id"], agent_id=ids["w"],
                              ref="out.txt", kind="file", sha256="ab12",
                              size=42, confirmed=True)
    assert "note_written" in _kinds(cfg, ids["w"]), (
        "an artifact reached the shelf and emitted no note_written")


def test_escalation_appears_after_an_escalation_is_raised(
        tmp_path, monkeypatch):
    from voicebridge.office import escalation as E

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)

    assert "escalation" not in _kinds(cfg, ids["w"])
    E.raise_to_supervisor(cfg, agent_id=ids["w"], kind="blocked",
                          summary="need a decision on the envelope")
    assert "escalation" in _kinds(cfg, ids["w"]), (
        "an escalation was raised and emitted no escalation event")


def test_memory_written_appears_after_a_memory_is_written(
        tmp_path, monkeypatch):
    from voicebridge.office import agent_memory as M

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)

    assert "memory_written" not in _kinds(cfg, ids["w"])
    M.remember(cfg, ids["w"], kind="tick", text="learned something",
               importance=6)
    assert "memory_written" in _kinds(cfg, ids["w"])


def test_every_wired_kind_has_an_emitter_in_the_named_production_module():
    """A real call site exists in the module the wiring table names.

    This is the guard that survives a refactor: the kind must be emitted by
    production code (not a test, not a comment), in the named file.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "voicebridge" / "office"
    for kind, module in WIRED.items():
        txt = (root / module).read_text()
        assert f'"{kind}"' in txt or f"'{kind}'" in txt, (
            f"{kind!r} is not emitted anywhere in {module}")


# ============================================ 2. through the tower, end to end
def test_each_wired_row_produces_the_animation_and_station_the_tower_maps(
        tmp_path, monkeypatch):
    """The durability half: a row of each kind animates AND walks where mapped.

    Both folds are pure (``activity_from_events`` / ``walk_for``), so this is
    asserted on a hand-built floorplan — no clock, no store, no timer.
    """
    from voicebridge.office import tower as T

    cfg = _cfg(tmp_path, monkeypatch)
    plan = {"floors": [{"number": 0, "rooms": [
        {"id": 1, "x": 0, "y": 0, "seats": [
            {"agent_id": 7, "x": 5, "y": 4}]}]}]}
    now = 1_000.0

    for kind in list(WIRED) + sorted(DELIBERATELY_UNWIRED):
        assert kind in T._ANIMATION, f"{kind} has no animation mapping"
        act = T.activity_from_events(
            [{"agent_id": 7, "kind": kind, "ts": now}], now=now)
        assert act[7]["animation"] == T._ANIMATION[kind]

    # the four station walks the milestone unblocks
    for kind, station in (("skill_used", "workbench"),
                          ("ledger_write", "vault"),
                          ("mcp_call", "comms"),
                          ("note_written", "shelf"),
                          ("memory_written", "shelf")):
        assert T._EVENT_STATION[kind] == station
        walk = T.walk_for(7, plan=plan, room_id=1, event_kind=kind,
                          ts=now, now=now + T.WALK_S)
        assert walk["at"] == "station", (kind, walk)
        expected = T._station_coords(plan["floors"][0]["rooms"][0])[station]
        assert (walk["x"], walk["y"]) == (expected["x"], expected["y"])


def test_escalation_walks_to_the_manager_not_a_station():
    """`escalation` is the fifth walk: to the supervisor's desk."""
    from voicebridge.office import tower as T

    now = 1_000.0
    plan = {"floors": [{"number": 0, "rooms": [
        {"id": 1, "x": 0, "y": 0, "seats": [
            {"agent_id": 7, "x": 5, "y": 4},
            {"agent_id": 8, "x": 9, "y": 4}]}]}]}
    walk = T.walk_for(7, plan=plan, room_id=1, event_kind="escalation",
                      ts=now, now=now + T.WALK_S,
                      escalation_target={"x": 9, "y": 4})
    assert (walk["x"], walk["y"]) == (9, 4)
    assert walk["at"] == "station"
    assert T._ANIMATION["escalation"] == "walking_to_manager"


def test_a_stale_or_absent_row_produces_no_motion(tmp_path, monkeypatch):
    """C7's other half: the fold cannot animate without a FRESH row."""
    from voicebridge.office import tower as T

    now = 1_000.0
    act = T.activity_from_events(
        [{"agent_id": 7, "kind": "skill_used",
          "ts": now - (T.ACTIVE_WINDOW_S + 1)}], now=now)
    assert act[7]["animation"] == "idle"
    assert T.activity_from_events([], now=now) == {}


# ================================================== 3. weathering, no dead pose
def test_no_mapped_animation_kind_is_silently_unreachable():
    """Every ``_ANIMATION`` kind is either wired here or a named exception.

    A new pose added to ``_ANIMATION`` without an emitter fails this test — the
    rule ``50-ARCHITECTURE.md`` §5.3 makes checkable.
    """
    from voicebridge.office import tower as T

    mapped = set(T._ANIMATION)
    # every mapped kind is one of: wired by this milestone, one of the kinds the
    # runner already emitted before it, or a named deliberate exception.
    already_emitted = {"run_started", "run_settled", "run_unpriced",
                       "run_failed", "task_done", "task_unverified",
                       "consent_denied", "tool_call"}
    accounted = set(WIRED) | already_emitted | DELIBERATELY_UNWIRED
    assert mapped <= accounted, (
        f"these animations are unreachable (no emitter): {mapped - accounted}")


def test_the_deliberate_exception_is_really_unemitted():
    """`meeting` is asserted UNREACHABLE on purpose, not left quietly dead."""
    from pathlib import Path
    import re

    from voicebridge.office import tower as T

    assert "meeting" in T._ANIMATION
    assert "meeting" not in WIRED
    # the real assertion: no production emit call names "meeting" (there is no
    # boardroom yet, so there must be no meeting row pretending there is one).
    root = Path(__file__).resolve().parent.parent / "voicebridge" / "office"
    for py in root.glob("*.py"):
        src = py.read_text()
        for m in re.finditer(r'emit_event\(\s*cfg,\s*"(\w+)"', src):
            assert m.group(1) != "meeting", f"{py.name} emits meeting"
        for m in re.finditer(r'_event\(\s*cfg,\s*"(\w+)"', src):
            assert m.group(1) != "meeting", f"{py.name} emits meeting"


# ==================================================== 4. the emit seam is one
def test_agent_memory_is_the_single_emission_seam():
    """A5 owns "what an office event is": the call sites route through it."""
    from voicebridge.office import agent_memory as M

    assert callable(M.emit_event)
    assert callable(M.classify_tool)
    sig = inspect.signature(M.emit_event)
    assert {"agent_id", "payload", "visible"} <= set(sig.parameters)


def test_classify_tool_is_honest_about_what_is_not_a_skill_or_mcp():
    from voicebridge.office import agent_memory as M

    assert M.classify_tool("mcp__server__tool") == "mcp_call"
    assert M.classify_tool("skill_view") == "skill_used"
    assert M.classify_tool("skill_manage") == "skill_used"
    assert M.classify_tool("read_file") == ""
    assert M.classify_tool("") == ""
    assert M.classify_tool(None) == ""
