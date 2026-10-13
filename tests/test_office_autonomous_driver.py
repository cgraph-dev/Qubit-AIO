"""M7/L1 — the autonomous driver: the org notices work for itself.

The gap this closes: every turn the office ever ran was a human typing
``qubit office run``/``dispatch``. ``task_proposal.propose`` had no production
consumer, so the org could execute anything asked of it and notice nothing on its
own. This module proves the tick that fixes that, and — more importantly — proves
its limits, because autonomy that is not bounded is not a feature.

The proofs, one test each, in the order they matter:

  1. a tick produces a real ``proposed`` row with NO owner CLI invocation;
  2. with the flag OFF a tick changes nothing (not even a read of the store);
  3. a seat cannot exceed its turn budget through the loop
     (``OFFICE_TURN_BUDGET_EXHAUSTED``, reported in ``skipped``);
  4. the cadence is honoured against a frozen clock (the MIN_GAP rule);
  5. the driver is cron-shaped, not a second loop — no thread, no ``while True``;
  6. a frozen posture seat is never woken;
  7. a quiet tick writes no event (no event, no motion).

Hermetic: tmp XDG state only; the executor is stubbed everywhere, so not one
cent is spent and no provider is touched. No wall-clock waits: ``now`` is passed
in.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _org(cfg, *, workers=2, managers=1, supervisors=True):
    """A CEO above M managers above N workers — the chain C30/C31 needs."""
    from voicebridge.office import org as O

    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    mgrs = [O.hire(cfg, f"mgr-{i}", seat_type="manager",
                   supervisor_id=ceo, confirmed=True)["id"]
            for i in range(managers)]
    boss = mgrs or [ceo]
    wks = [O.hire(cfg, f"wk-{i}", seat_type="worker",
                  supervisor_id=(boss[i % len(boss)] if supervisors else None),
                  confirmed=True)["id"]
           for i in range(workers)]
    return {"ceo": ceo, "managers": mgrs, "workers": wks}


def _pay(cfg, agent_id, cents=100_000):
    from voicebridge.office import ledger
    ledger.record(cfg, "revenue", cents, agent_id=agent_id)


def _stub(reply="did the thing"):
    """A stand-in for a real turn: reports usage so nothing is unknown spend."""
    def _call(cfg, agent_id, task_text):
        return {"state": "OK", "reply": reply,
                "receipt": {"model": "stub", "identity": "stub/model",
                            "usage": {"prompt_tokens": 10,
                                      "completion_tokens": 5,
                                      "cost_usd_est": 0.0}}}
    return _call


def _pay_all(cfg, ids):
    for a in ids["managers"] + ids["workers"] + [ids["ceo"]]:
        _pay(cfg, a)


def _event_rows(cfg):
    from voicebridge.office import store
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        return [dict(r) for r in conn.execute(
            "SELECT id, kind, agent_id, payload FROM office_event ORDER BY id")]
    finally:
        conn.close()


def _proposal_rows(cfg):
    from voicebridge.office import store, task_proposal as TP
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        TP.ensure(conn)
        return [dict(r) for r in conn.execute(
            "SELECT * FROM office_task_proposal ORDER BY id")]
    finally:
        conn.close()


# ===========================================================================
# 1 — A TICK PROPOSES, WITH NO OWNER INVOCATION.
# ===========================================================================
def test_a_tick_produces_a_real_proposed_row_with_no_owner_invocation(
        tmp_path, monkeypatch):
    """The whole point: the org floats an idea nothing the owner typed asked for.

    Nothing here calls a CLI verb, ``task_proposal.propose``, or
    ``work.create``. The only entry point is ``autowork.tick`` with the flag on,
    and the row it produces is a genuine ``office_task_proposal`` row whose
    proposer is a real seat.
    """
    from voicebridge.office import autowork as AW

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1, managers=1)

    out = AW.tick(cfg, executor=_stub(), now=1_000_000.0, enabled=True)

    assert out["state"] == "OK" and out["enabled"] is True
    assert out["proposed"], f"the tick proposed nothing: {out}"
    # every seat floats an idea from its own context — including the CEO, whose
    # proposal has no seat above it and is left for the owner (D2).
    assert {p["agent_id"] for p in out["proposed"]} == set(
        ids["workers"] + ids["managers"] + [ids["ceo"]])

    rows = _proposal_rows(cfg)
    assert len(rows) == len(out["proposed"])
    assert all(r["status"] in ("proposed", "approved") for r in rows)
    assert all(r["rationale"].strip() for r in rows), (
        "a proposal with no rationale is a wish the level above cannot judge")
    # the grounding travels with the proposal (L1's whole point)
    for p in out["proposed"]:
        assert "grounding" in p and "open_tasks" in p["grounding"]


def test_a_tick_runs_a_real_attributed_turn_and_emits_real_events(
        tmp_path, monkeypatch):
    """A zero-cost turn, on the record, from the existing emitter — no new path.

    The manager seat runs its own self-directed work (the leaf seats are left to
    the org → ``dispatch``), and the turn goes through the same
    ``runner.run_turn`` a human command uses.
    """
    from voicebridge.office import autowork as AW

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1, managers=1)
    _pay(cfg, ids["managers"][0])

    out = AW.tick(cfg, executor=_stub(), now=1_000_000.0, enabled=True)
    assert out["ran"], f"no seat ran: {out}"
    assert ids["managers"][0] in [r["agent_id"] for r in out["ran"]]

    r = out["ran"][0]
    assert r["run_id"], "the turn left no run id"
    assert r["done"] is False, "with no checker, nothing is done (C17)"

    kinds = [e["kind"] for e in _event_rows(cfg)]
    # run_started / tool_call / run_settled / task_unverified — all from
    # runner.run_turn, which is the same path a human command takes (C7).
    assert "run_started" in kinds
    assert "tool_call" in kinds, "the walk-driving event did not fire"
    assert "run_settled" in kinds
    assert _event_rows(cfg), "the tick produced no rows to animate"


# ===========================================================================
# 2 — OFF BY DEFAULT: A TICK CHANGES NOTHING.
# ===========================================================================
def test_with_the_flag_off_a_tick_is_a_genuine_no_op(tmp_path, monkeypatch):
    """Off by default (O3) means no store, no proposal, no event, no turn.

    The tick must not even *create* the office database — that is what makes
    "off" a real statement about the deployment, not a promise about behaviour.
    """
    from voicebridge.office import autowork as AW, store

    cfg = _cfg(tmp_path, monkeypatch)
    db = store.db_path(cfg)
    assert not db.exists(), "fixture must start with no office store"

    calls = []

    def stub(cfg_, agent_id, task_text):
        calls.append(agent_id)
        return {"state": "OK", "reply": "x", "receipt": {}}

    out = AW.tick(cfg, executor=stub, now=1_000_000.0)  # enabled=None -> config
    assert out["enabled"] is False
    assert out["proposed"] == [] and out["ran"] == []
    assert calls == [], "a disabled tick reached the executor"
    assert not db.exists(), "a disabled tick touched the store"
    assert out["meaning"]

    # and status agrees without writing anything either
    st = AW.status(cfg)
    assert st["enabled"] is False
    assert not db.exists()


def test_the_config_flag_turns_it_on():
    """The flag is a real config key, not a hardcoded constant."""
    from voicebridge import config as C
    from voicebridge.office import autowork as AW

    assert AW.CONFIG_KEY == ("office", "autowork", "enabled")
    cfg = C.Config(data={"office": {"autowork": {"enabled": True}}})
    assert AW._flag(cfg) is True
    cfg_off = C.Config(data={"office": {"autowork": {"enabled": False}}})
    assert AW._flag(cfg_off) is False
    assert AW._flag(C.Config(data={})) is False


def test_enable_persists_and_the_gate_reads_what_it_wrote(tmp_path, monkeypatch):
    """`enable` must actually flip the gate — status, and the next tick.

    Found by running the CLI: `enable` persisted the flag but `_flag` read only
    the config, so the two disagreed and `enable` was a silent no-op. The gate a
    tick consults and the value `status` reports are now the same read.
    """
    from voicebridge.office import autowork as AW

    cfg = _cfg(tmp_path, monkeypatch)
    assert AW.status(cfg)["enabled"] is False

    AW.enable(cfg, enabled=True)
    assert AW.status(cfg)["enabled"] is True
    assert AW._flag(cfg) is True

    # and the persisted flip survives a fresh status read (a restart)
    assert AW.status(cfg)["enabled"] is True

    AW.enable(cfg, enabled=False)
    assert AW.status(cfg)["enabled"] is False
    assert AW._flag(cfg) is False


# ===========================================================================
# 3 — THE BUDGET IS THE BOUND, AND NOTHING ELSE IS.
# ===========================================================================
def test_a_seat_at_its_turn_cap_is_skipped_not_run(tmp_path, monkeypatch):
    """The daily turn cap stops the loop, via the EXISTING budget.

    No parallel budget is invented: ``work.set_turn_cap`` is what the owner
    already uses, and the tick honours it with the existing code.
    """
    from voicebridge.office import autowork as AW, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1, managers=1)
    mgr = ids["managers"][0]
    _pay(cfg, mgr)
    W.set_turn_cap(cfg, agent_id=mgr, turns=0, confirmed=True)

    out = AW.tick(cfg, executor=_stub(), now=1_000_000.0, enabled=True)
    assert mgr not in [r["agent_id"] for r in out["ran"]]
    skipped = [s for s in out["skipped_seats"] if s.get("agent_id") == mgr]
    assert skipped and skipped[0]["outcome"] == AW.SKIP_TURN_BUDGET
    assert W.turn_budget(cfg, agent_id=mgr)["turns_used"] == 0


def test_a_seat_cannot_exceed_its_cap_across_many_ticks(tmp_path, monkeypatch):
    """The loop cannot spend a second turn past the cap, however often it ticks.

    This is the "≤1 turn per seat per tick, bounded by the turn budget" rule
    (O3) tested at its limit: two ticks with a cap of one must produce exactly
    one turn, and the second must be refused with the existing code reported.
    """
    from voicebridge.office import autowork as AW, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1, managers=1)
    mgr = ids["managers"][0]
    _pay(cfg, mgr)
    W.set_turn_cap(cfg, agent_id=mgr, turns=1, confirmed=True)

    first = AW.tick(cfg, executor=_stub(), now=1_000_000.0, enabled=True)
    assert [r["agent_id"] for r in first["ran"]] == [mgr]
    assert W.turn_budget(cfg, agent_id=mgr)["turns_used"] == 1

    second = AW.tick(cfg, executor=_stub(), now=1_000_000.0 + 3600.0,
                     enabled=True)
    assert mgr not in [r["agent_id"] for r in second["ran"]], (
        "a seat ran a second turn past its cap")
    assert W.turn_budget(cfg, agent_id=mgr)["turns_used"] == 1
    assert any(s.get("agent_id") == mgr and s["outcome"] == AW.SKIP_TURN_BUDGET
               for s in second["skipped_seats"])


def test_a_tick_runs_at_most_one_turn_per_seat_per_tick(tmp_path, monkeypatch):
    """O3: at most one turn per seat per tick — the loop runs a seat once.

    ``dispatch`` was the one place that could run several rounds for one seat;
    the tick never calls it for its own action, it calls ``run_turn`` once.
    """
    from voicebridge.office import autowork as AW

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=3, managers=2)
    for a in ids["workers"] + ids["managers"]:
        _pay(cfg, a)

    out = AW.tick(cfg, executor=_stub(), now=1_000_000.0, enabled=True)
    ran_ids = [r["agent_id"] for r in out["ran"]]
    assert len(ran_ids) == len(set(ran_ids)), f"a seat ran twice: {ran_ids}"
    # the seats that ran are exactly the supervising seats (managers); leaves
    # are left to the org
    assert set(ran_ids) == set(ids["managers"])


# ===========================================================================
# 4 — THE CADENCE (frozen clock, no wall-clock wait)
# ===========================================================================
def test_the_cadence_suppresses_a_second_tick_inside_the_interval(
        tmp_path, monkeypatch):
    """The persisted last-tick stamp is what makes the cadence real (O3)."""
    from voicebridge.office import autowork as AW

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=2, managers=1)
    _pay(cfg, ids["managers"][0])

    t0 = 1_000_000.0
    first = AW.tick(cfg, executor=_stub(), now=t0, enabled=True)
    assert first.get("skipped") != "not_due"
    assert first["ran"], "the first tick should have acted"

    # one second later: inside the interval, so nothing happens again
    second = AW.tick(cfg, executor=_stub(), now=t0 + 1.0, enabled=True)
    assert second["skipped"] == "not_due"
    assert second["ran"] == [] and second["proposed"] == []
    assert second["enabled"] is True

    # past the interval: it acts again
    third = AW.tick(cfg, executor=_stub(),
                    now=t0 + AW.DEFAULT_EVERY_S + 1.0, enabled=True)
    assert third.get("skipped") != "not_due"


def test_the_last_tick_survives_a_restart(tmp_path, monkeypatch):
    """Cadence state is persisted, so a restart cannot replay a tick (O3)."""
    from voicebridge.office import autowork as AW

    cfg = _cfg(tmp_path, monkeypatch)
    _org(cfg, workers=1, managers=1)
    t0 = 1_000_000.0
    AW.tick(cfg, executor=_stub(), now=t0, enabled=True)

    # a fresh status read (a new "process") sees the same stamp
    st = AW.status(cfg)
    assert st["last_tick_at"] == t0
    assert st["every_seconds"] == AW.DEFAULT_EVERY_S == 900


# ===========================================================================
# 5 — CRON-SHAPED, NOT A SECOND LOOP (C3/O3)
# ===========================================================================
def test_the_driver_is_cron_shaped_never_a_thread_or_a_busy_loop():
    """``grep 'while True|threading.Thread|asyncio.create_task'`` must stay empty.

    The tick must be a *job* — runs, returns — so it survives restarts the way
    every other scheduled job does and can be paused from the cron surface.

    Comments and docstrings are stripped first, exactly as
    ``tests/test_office_bundle.py:252-254`` does for the served JS: a *mention*
    of the banned construct in prose is not the construct.
    """
    import ast

    def code_only(path):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef, ast.Module)):
                body = getattr(node, "body", [])
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    body.pop(0)
        return ast.unparse(tree)

    driver = code_only(REPO / "voicebridge" / "office" / "autowork.py")
    for banned in ("threading.Thread", "asyncio.create_task", "while True",
                   "daemon=", "setInterval", "requestAnimationFrame"):
        assert banned not in driver, f"the driver is not cron-shaped: {banned}"

    # every office module, not just this one (L1's acceptance check #6)
    for path in (REPO / "voicebridge" / "office").glob("*.py"):
        text = code_only(path)
        for banned in ("threading.Thread", "asyncio.create_task", "while True"):
            assert banned not in text, f"{path.name} is not cron-shaped: {banned}"


# ===========================================================================
# 6 — A FROZEN SEAT IS NEVER WOKEN
# ===========================================================================
def test_a_frozen_posture_seat_is_skipped_and_writes_nothing(
        tmp_path, monkeypatch):
    """``frozen`` refuses and ``supervised`` stages — neither is a wake (C5)."""
    from voicebridge.office import autowork as AW, store

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1, managers=1)
    mgr = ids["managers"][0]
    _pay(cfg, mgr)
    conn = store.connect(cfg)
    try:
        conn.execute("UPDATE office_agent SET autonomy='frozen' WHERE id=?",
                     (mgr,))
        conn.commit()
    finally:
        conn.close()

    out = AW.tick(cfg, executor=_stub(), now=1_000_000.0, enabled=True)
    assert mgr not in [r["agent_id"] for r in out["ran"]]
    assert mgr not in [p["agent_id"] for p in out["proposed"]]
    skipped = [s for s in out["skipped_seats"] if s.get("agent_id") == mgr]
    assert skipped and skipped[0]["outcome"] == AW.SKIP_FROZEN


# ===========================================================================
# 7 — NO EVENT, NO MOTION (C7): A QUIET TICK WRITES NOTHING.
# ===========================================================================
def test_a_tick_that_can_do_nothing_writes_no_event(tmp_path, monkeypatch):
    """Every seat frozen => the tick animates nothing, because nothing happened.

    Fabricating a heartbeat event is exactly what ``90-CORRECTIONS.md`` §C7
    forbids, so the tick has no fallback that writes one.
    """
    from voicebridge.office import autowork as AW, store

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=2, managers=1)
    conn = store.connect(cfg)
    try:
        conn.execute("UPDATE office_agent SET autonomy='frozen'")
        conn.commit()
    finally:
        conn.close()

    out = AW.tick(cfg, executor=_stub(), now=1_000_000.0, enabled=True)
    assert out["ran"] == [] and out["proposed"] == []
    assert _event_rows(cfg) == [], "a quiet tick wrote an event (C7 violated)"
    assert out.get("quiet") is True
    # the reason it was quiet is reported rather than left to be inferred
    assert len(out["skipped_seats"]) == 4  # ceo + 1 manager + 2 workers


# ===========================================================================
# The ratchet: approval drives the EXISTING path, and the chain never bends.
# ===========================================================================
def test_a_managers_proposal_is_approved_by_the_ceo_and_becomes_a_task(
        tmp_path, monkeypatch):
    """The tick approves one level up, through ``task_proposal.approve``.

    A manager's proposal has the CEO above it, so the tick drives that approval;
    the resulting task is an ordinary one, indistinguishable from an owner's.
    """
    from voicebridge.office import autowork as AW, work as W

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=1, managers=1)
    _pay_all(cfg, ids)

    out = AW.tick(cfg, executor=_stub(), now=1_000_000.0, enabled=True)
    approved = out["approvals"]["approved"]
    assert approved, f"nothing was approved: {out['approvals']}"
    for a in approved:
        assert a["approver_id"] in (ids["ceo"],) + tuple(ids["managers"])

    tasks = W.tasks(cfg)["tasks"]
    assert tasks, "an approved proposal did not become a task"
    # every approved task has a machine-checkable exit condition (C17)
    assert all(t["exit_check"].strip() for t in tasks)


def test_a_ceos_proposal_is_left_for_the_owner_not_auto_approved(
        tmp_path, monkeypatch):
    """D2: the seat above the CEO is the owner, who is not an agent.

    The tick must NOT invent a seat approver for the CEO — it leaves the
    proposal pending and names why, which is the honest outcome.
    """
    from voicebridge.office import autowork as AW, task_proposal as P

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=0, managers=0)
    _pay(cfg, ids["ceo"])
    # the CEO floats an idea directly (as its own turn's proposal would)
    pid = P.propose(cfg, ids["ceo"], "org-wide restructure", exit_check="true",
                    rationale="the org should look different")["id"]

    out = AW.tick(cfg, executor=_stub(), now=1_000_000.0, enabled=True)
    left = [x for x in out["approvals"]["left"]
            if x["proposal_id"] == pid]
    assert left, f"the CEO's proposal was not left for the owner: {out['approvals']}"
    assert left[0]["outcome"] == "awaits_the_owner"
    assert P.proposals(cfg)["proposals"][0]["status"] == "proposed"


def test_assess_is_a_read_only_preview_of_what_a_tick_would_do(
        tmp_path, monkeypatch):
    """The owner can see the driver's intent before enabling it — no writes."""
    from voicebridge.office import autowork as AW, store

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg, workers=2, managers=1)
    _pay(cfg, ids["managers"][0])
    db = store.db_path(cfg)

    out = AW.assess(cfg, now=1_000_000.0)
    assert out["state"] == "OK"
    assert out["enabled"] is False        # the fixture never enabled it
    assert out["due"] is True             # no tick has run
    assert isinstance(out["would_act"], list)
    # `assess` reads; it must not have proposed or run anything
    assert _proposal_rows(cfg) == []
    assert out["would_act"], "the funded manager should be an acting seat"
    assert db.exists()  # the org was already hired, so the store exists
