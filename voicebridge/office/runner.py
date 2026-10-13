"""The runner: one agent, one real turn, fully attributed (M1).

The ordering *is* the feature. This is the sequence from `40-RUNTIME.md`,
and every step exists because skipping it produced a specific failure:

```
e-stop  →  consent  →  admission  →  begin_run  →  the turn  →  finish_run
                                                       ↓
                                          ledger row, events, artifacts
```

* **e-stop first** — a deliberate stop is not a crash, and it must not spend.
* **consent before spending** (D17) — the four-tier ladder, hardline first.
* **admission before the call** (D15/C8) — an unsettled run or a breached cap
  refuses here, *before* a provider is touched.
* **receipt before the first model call** (D15) — so a process death mid-turn
  leaves *unknown spend*, never free work.
* **"done" is never the model's own verdict** (C17) — a turn completes because
  the runtime can check an exit condition against real state, not because the
  model said it did.

Money: the provider's own `cost_usd_est` is converted to integer cents for the
booking (the unit every cap and total uses) **and** stored exactly, in
micro-dollars, as evidence. A turn that genuinely cost $0.0004 books 0 cents
and 400 micros, and `trace` shows both — a rounding difference is visible
rather than hidden.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from . import consent, ledger, receipts, store
from .errors import BAD_INPUT_CODE, OfficeError

#: The default seat budget for one turn, in cents. Deliberately small: M1's
#: whole point is that a turn is bounded before it runs.
DEFAULT_TURN_CAP_CENTS = 50

RUN_REFUSED_CODE = "OFFICE_RUN_REFUSED"
DONE_CODE = "OFFICE_NOT_DONE"


def usd_to_cents(cost_usd: Any) -> int:
    """Book a provider cost as integer cents. Never a float in the ledger."""
    try:
        return int(round(float(cost_usd or 0.0) * 100))
    except (TypeError, ValueError):
        return 0


def usd_to_micros(cost_usd: Any) -> int:
    """The same cost, exactly, in micro-dollars — the evidence column."""
    try:
        return int(round(float(cost_usd or 0.0) * 1_000_000))
    except (TypeError, ValueError):
        return 0


def _event(cfg: Any, kind: str, *, agent_id: Optional[int] = None,
           department_id: Optional[int] = None, payload: Optional[Dict] = None,
           visible: str = "badge") -> None:
    """One renderable row. The tower draws only from these (D14/C7)."""
    import json as _json

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        conn.execute(
            "INSERT INTO office_event(ts, kind, agent_id, department_id,"
            " payload, visible) VALUES(?,?,?,?,?,?)",
            (store.now(), kind, agent_id, department_id,
             _json.dumps(payload or {}, sort_keys=True), visible))
        conn.commit()
    finally:
        conn.close()


def attribute_artifact(cfg: Any, run_id: str, *, agent_id: Optional[int],
                       ref: str, kind: str = "file",
                       department_id: Optional[int] = None,
                       sha256: str = "", size: Optional[int] = None,
                       confirmed: bool = False) -> Dict[str, Any]:
    """Bind a produced artifact to the run that produced it (C6).

    A run id that has no receipt is refused: an artifact with no run is an
    artifact with no cost, and that is how unattributed work hides.
    """
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE,
                          "office attribute requires --confirm")
    if not (ref or "").strip():
        raise OfficeError(BAD_INPUT_CODE, "an artifact needs a ref")
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        if conn.execute("SELECT 1 FROM office_run_receipts WHERE run_id=?",
                        (run_id,)).fetchone() is None:
            raise OfficeError(
                BAD_INPUT_CODE,
                f"run {run_id!r} has no receipt, so an artifact cannot be "
                f"attributed to it (C6)")
        cur = conn.execute(
            "INSERT INTO office_artifact(run_id, agent_id, department_id,"
            " kind, ref, sha256, bytes, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (run_id, agent_id, department_id, kind, ref.strip(), sha256,
             size, store.now()))
        conn.commit()
        return {"state": "OK", "id": int(cur.lastrowid or 0),
                "run_id": run_id, "ref": ref.strip()}
    finally:
        conn.close()


def contract_check_attribution(cfg: Any) -> Dict[str, Any]:
    """C6's proof: no artifact, no attribution, nothing scored on a story."""
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        orphans = [dict(r) for r in conn.execute(
            "SELECT a.id, a.run_id, a.ref FROM office_artifact a"
            " LEFT JOIN office_run_receipts r ON r.run_id = a.run_id"
            " WHERE r.run_id IS NULL")]
        unreceipted_rows = [dict(r) for r in conn.execute(
            "SELECT DISTINCT l.run_id FROM office_ledger l"
            " WHERE l.run_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM"
            " office_run_receipts r WHERE r.run_id = l.run_id)")]
        return {"state": "OK",
                "orphan_artifacts": len(orphans),
                "ledger_rows_without_a_receipt": len(unreceipted_rows),
                "unattributed_entries": len(orphans) + len(unreceipted_rows),
                "job": "unattributed_entries"}
    finally:
        conn.close()


def _completion_is_machine_checkable(result: Dict[str, Any],
                                     checker: Optional[Callable]) -> bool:
    """C17: a run is complete only if the runtime can *check* it.

    **With no checker, nothing is done.** A non-empty reply is not a completion
    signal — that is precisely the StarNet failure (98 passes of "verified the
    files exist", each auto-approved), and a first draft of this function made
    it, by treating "the model produced text" as success. A caller that wants a
    completion verdict must supply an exit condition it can evaluate against
    real state.
    """
    if checker is None:
        return False
    return bool(checker(result))


def run_turn(cfg: Any, agent_id: int, task_text: str, *,
             executor: Optional[Callable[[Any, int, str], Dict[str, Any]]] = None,
             checker: Optional[Callable[[Dict[str, Any]], bool]] = None,
             cap_cents: int = DEFAULT_TURN_CAP_CENTS,
             estimated_cents: int = 1,
             session: Optional[str] = None,
             model: Optional[str] = None,
             department_id: Optional[int] = None,
             unmetered: bool = False,
             now: Optional[float] = None) -> Dict[str, Any]:
    """Run exactly one attributed turn for one seat.

    ``executor`` is the seam: production passes nothing and the seat's real
    turn runs through :func:`voicebridge.agent.run_agent_turn`; a test injects
    a stub, so the whole accounting path is provable **without spending a
    cent**.
    """
    if not (task_text or "").strip():
        raise OfficeError(BAD_INPUT_CODE, "a turn needs a task")

    # 1 — the owner's off-switch is the first gate, not the last.
    if store.estop_engaged(cfg):
        raise OfficeError(RUN_REFUSED_CODE,
                          "the emergency stop is latched; no turn starts")

    # 2 — consent. Spending on a provider is a network mutation, so it is
    #     resolved through the ladder like any other action (D17).
    seat = _seat(cfg, agent_id)
    verdict = consent.check(
        cfg, agent_id=agent_id, capability="spend", scope="provider",
        posture=seat.get("autonomy") or "unattended_full",
        tags=["spend:provider"])
    if not verdict["allowed"]:
        _event(cfg, "consent_denied", agent_id=agent_id,
               payload={"danger_key": verdict["danger_key"],
                        "tier": verdict["tier"]}, visible="bubble")
        raise OfficeError(RUN_REFUSED_CODE, verdict["message"])

    # 3 — admission: completeness, then the caps, then the balance (C8/D15).
    admitted = receipts.admit(cfg, agent_id=agent_id,
                              estimated_cents=estimated_cents)

    # 4 — the receipt, before the first model call (D15).
    run = receipts.begin_run(cfg, agent_id, cap_cents=cap_cents,
                             model=model or "")
    run_id = run["run_id"]
    _event(cfg, "run_started", agent_id=agent_id,
           department_id=department_id,
           payload={"run_id": run_id, "task": task_text[:200]})

    # THE WALK-DRIVING EVENT. `tower.py` animates a figure from six event kinds
    # (`tool_call`, `skill_used`, `ledger_write`, `mcp_call`, `note_written`,
    # `memory_written`) and chooses a destination station for each. NOTHING wrote
    # any of them: the runner emitted only `run_started`/`run_settled`/
    # `run_failed`/`task_done`/`task_unverified`/`run_unpriced`, whose overlap
    # with those six is the empty set. So a real work turn could never move
    # anybody — the office was permanently idle no matter how much work it did,
    # and the owner's report ("nothing is moving, only boxes and squares") was
    # exactly correct.
    #
    # A turn that is about to call the model IS a tool call, so that is the
    # honest kind — this is not a decorative event bolted on to make the tower
    # look busy (D14: the tower never asserts a state it cannot prove). It is
    # emitted only when `executor` will actually run, i.e. after admission and
    # the receipt, so the movement tracks real spend rather than intent.
    _event(cfg, "tool_call", agent_id=agent_id, department_id=department_id,
           payload={"run_id": run_id, "task": (task_text or "")[:120]})

    # 5 — the turn itself.
    call = executor or _default_executor
    try:
        result = call(cfg, agent_id, task_text) or {}
    except Exception as exc:  # noqa: BLE001 — the receipt must survive it
        # We do NOT settle here: we cannot know what the provider charged, and
        # guessing is exactly what D15 forbids. The run stays open as unknown
        # spend and admission blocks until the owner attests it.
        _event(cfg, "run_failed", agent_id=agent_id,
               payload={"run_id": run_id, "error": str(exc)[:300]},
               visible="bubble")
        return {"state": "FAILED", "run_id": run_id, "unknown_spend": True,
                "error": str(exc)[:300],
                "next": ("settle it with an owner-stated amount — the receipt "
                         "stays open and admission refuses until you do")}

    usage = ((result.get("receipt") or {}).get("usage"))
    state = str(result.get("state") or "").upper()

    # --- a subscription-backed turn has no dollar cost to be unknown (D16) ---
    # `unmetered` means the provider is flat-rate: the turn happened and is
    # RECORDED, but there is no marginal charge to measure. So an absent usage
    # block is not a missing measurement here — it is the expected shape — and
    # leaving the receipt open would block admission forever on a cost that does
    # not exist. The row is booked with unmetered=1, which every metered
    # aggregate and every dollar cap excludes by construction.
    if unmetered and state in ("", "OK") and not usage:
        settled = receipts.finish_run(
            cfg, run_id, settled_cents=0, unmetered=True, tokens_in=0,
            tokens_out=0, model=str(model or ""), agent_id=agent_id,
            department_id=department_id,
            note=("subscription-backed turn: recorded, not counted as dollars "
                  "(D16)"))
        _event(cfg, "run_settled", agent_id=agent_id,
               department_id=department_id,
               payload={"run_id": run_id, "cents": 0, "unmetered": True},
               visible="bubble")
        done = _completion_is_machine_checkable(result, checker)
        _event(cfg, "task_done" if done else "task_unverified",
               agent_id=agent_id,
               payload={"run_id": run_id, "reason":
                        "exit condition satisfied" if done else
                        "no machine-checkable exit condition (C17)"},
               visible="bubble")
        return {"state": "OK", "run_id": run_id, "cents": 0, "unmetered": True,
                "ledger_id": settled["ledger_id"],
                "tokens_in": 0, "tokens_out": 0, "done": bool(done),
                "reply": str(result.get("reply") or "")[:2000],
                "model": ((result.get("receipt") or {}).get("model") or ""),
                "meaning": ("flat-rate turn: on the record, costing no dollars; "
                            "the turn budget is what bounds it (M5)")}

    # --- unknown spend, not free work (D15) --------------------------------
    # The call happened. If the provider reported no usage block, or the turn
    # failed, its cost is UNKNOWN — and unknown is not zero. Settling here with
    # a 0-cent row is the exact failure D15 exists to prevent: a real charge
    # booked as free, and an agent that then survives on money it already
    # burned. So the receipt stays OPEN, admission refuses, and only the owner
    # can close it by stating what was charged.
    #
    # Note the discriminator: a usage block that says 0 is the provider's own
    # figure and is settled as such (a local, genuinely free model). *No* usage
    # block is a missing measurement, and that is different.
    if not usage or state not in ("", "OK"):
        why = ("the provider reported no usage for this turn"
               if not usage else f"the turn did not succeed (state={state})")
        _event(cfg, "run_unpriced", agent_id=agent_id,
               department_id=department_id,
               payload={"run_id": run_id, "why": why}, visible="bubble")
        return {"state": "UNKNOWN_SPEND", "run_id": run_id,
                "unknown_spend": True, "done": False,
                "reason": why,
                "next": (f"settle it with the owner's own figure: "
                         f"qubit office settle {run_id} --cents N "
                         f"--attested-as entered --confirm "
                         f"(admission refuses until you do)")}

    cost_usd = usage.get("cost_usd_est", 0.0)
    cents = usd_to_cents(cost_usd)
    micros = usd_to_micros(cost_usd)

    # 6 — real usage into the ledger, then settle the receipt.
    settled = receipts.finish_run(
        cfg, run_id, settled_cents=cents, unmetered=unmetered,
        tokens_in=int(usage.get("prompt_tokens") or 0),
        tokens_out=int(usage.get("completion_tokens") or 0),
        model=str(usage.get("model") or model or ""),
        provider_cost_micros=micros,
        agent_id=agent_id, department_id=department_id,
        note=f"{usage.get('identity') or 'unknown identity'}")

    done = _completion_is_machine_checkable(result, checker)
    # Two separate facts, two separate rows (D14: the renderer must be able to
    # tell a settled turn from a delivered task).
    _event(cfg, "run_settled", agent_id=agent_id, department_id=department_id,
           payload={"run_id": run_id, "cents": cents, "micros": micros,
                    "tokens_in": usage.get("prompt_tokens") or 0,
                    "tokens_out": usage.get("completion_tokens") or 0})
    _event(cfg, "task_done" if done else "task_unverified",
           agent_id=agent_id, department_id=department_id,
           payload={"run_id": run_id, "done": done,
                    "why": ("machine-checkable exit condition satisfied"
                            if done else
                            "no exit condition was checked, so completion is "
                            "unverified (C17)")})

    return {"state": "OK", "run_id": run_id, "done": done,
            "ledger_id": settled["ledger_id"],
            "cents": cents, "provider_cost_micros": micros,
            "tokens_in": usage.get("prompt_tokens") or 0,
            "tokens_out": usage.get("completion_tokens") or 0,
            "model": usage.get("model") or model or "",
            "identity": usage.get("identity") or "",
            "balance_cents": admitted["balance_cents"],
            "reply": str(result.get("reply") or "")[:500]}


def _default_executor(cfg: Any, agent_id: int,
                      task_text: str) -> Dict[str, Any]:
    """The seat's real turn: the existing tool-capable agent (D9).

    No new loop, no new provider path. This calls the same
    ``run_agent.AIAgent`` the terminal drives, so the office can never drift
    from the bridge it is drawn in.
    """
    from .. import agent as _agent

    seat = _seat(cfg, agent_id)
    session = seat.get("slug") or f"office-{agent_id}"
    result = _agent.run_agent_turn(
        cfg, task_text, session=session,
        estop_engaged=lambda: store.estop_engaged(cfg))
    if result is None:
        raise OfficeError(
            RUN_REFUSED_CODE,
            "no tool-capable runtime/model is configured, so no real turn "
            "could run. Configure a model before running an office turn.")
    return result


def _seat(cfg: Any, agent_id: int) -> Dict[str, Any]:
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        row = conn.execute("SELECT * FROM office_agent WHERE id=?",
                           (agent_id,)).fetchone()
        if row is None:
            raise OfficeError("OFFICE_NOT_FOUND", f"no seat #{agent_id}")
        return dict(row)
    finally:
        conn.close()


def trace(cfg: Any, agent_id: int, *, limit: int = 10) -> Dict[str, Any]:
    """Everything one seat's turns produced — M1's proof surface.

    Shows, per run: the receipt written before the call, the model that
    actually ran, the provider's own tokens and cents, the artifacts, and the
    event rows the renderer would consume. Nothing here is inferred from a
    transcript.

    Note on ordering: this reads everything first and **closes the connection**
    before asking for health. ``health()`` runs a write (its savepoint probe),
    and issuing that write while a read cursor is still open on the same
    database is how a read-only surface deadlocks the writer.
    """
    seat = _seat(cfg, agent_id)
    runs: List[Dict[str, Any]] = []
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        # Materialise the outer result set BEFORE issuing per-run queries:
        # iterating a cursor while running more statements on the same
        # connection is the hazard this avoids.
        receipt_rows = [dict(r) for r in conn.execute(
            "SELECT * FROM office_run_receipts WHERE agent_id=?"
            " ORDER BY started_at DESC LIMIT ?", (agent_id, limit))]
        for row in receipt_rows:
            rid = row["run_id"]
            row["ledger"] = [dict(x) for x in conn.execute(
                "SELECT id, kind, amount_cents, provider_cost_micros,"
                " tokens_in, tokens_out, unmetered, attested, attested_as,"
                " note, ts FROM office_ledger WHERE run_id=?", (rid,))]
            row["artifacts"] = [dict(x) for x in conn.execute(
                "SELECT id, kind, ref, sha256, bytes FROM office_artifact"
                " WHERE run_id=?", (rid,))]
            row["events"] = [dict(x) for x in conn.execute(
                "SELECT id, kind, visible, payload FROM office_event"
                " WHERE agent_id=? AND payload LIKE ? ORDER BY id",
                (agent_id, f'%"run_id": "{rid}"%'))]
            row["attributed"] = bool(row["ledger"])
            runs.append(row)
    finally:
        conn.close()

    # Safe now: no cursor is open, so health's probe cannot contend with us.
    return {"state": "OK",
            "agent": {"id": seat["id"], "slug": seat["slug"],
                      "seat_type": seat["seat_type"]},
            "runs": runs, "count": len(runs),
            "complete": ledger.health(cfg)["complete"],
            "attribution": contract_check_attribution(cfg)}
