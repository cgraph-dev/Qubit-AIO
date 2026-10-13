"""Run receipts and the admission gate (D15).

The plan's most important correctness rule lives here.

**A receipt is written before the first model call.** A receipt with no
matching ledger row is *spend-unknown*. Unknown spend is not zero, and it is
not headroom: if a process dies mid-turn, the provider still charged, and a
ledger that reads healthier than reality lets an agent survive on money it
already burned.

So:

* :func:`begin_run` refuses when the book is incomplete — it will not start a
  new run on top of an unsettled one.
* :func:`finish_run` writes the ledger row **first**, then settles the receipt.
  A crash between the two leaves the receipt open, which is the honest state.
* :func:`settle` closes an unknown spend **only** with the owner's attestation
  — ``entered`` (typed from the provider's own figure) or ``limit`` (counted at
  the run's cap). The two are never conflated, so a limit booking can never
  claim to be a real number.
* :func:`halt` marks a run stopped by the emergency stop, so a deliberate stop
  is distinguishable from a crash.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from . import ledger, store
from .errors import (BAD_INPUT_CODE, BAD_STATE_CODE, NOT_FOUND_CODE,
                     OfficeError)

ADMISSION_CODE = "OFFICE_ADMISSION_REFUSED"


def _unknown_spend_detail(health: Dict[str, Any]) -> str:
    """The refusal sentence for an incomplete book (D15).

    ``complete`` is false in **two** different situations, and they need
    different sentences: an unsettled run (name it), or a ledger that could not
    be read at all (say so). Assuming a run exists is how this crashed with an
    ``IndexError`` on a corrupt store.
    """
    runs = health.get("open_runs") or []
    if runs:
        first = runs[0]
        return (f"unknown spend: run {first.get('run_id')!r} (agent "
                f"{first.get('agent_id')}) has a receipt and no settled ledger "
                f"row. Unknown spend is not headroom — settle it before "
                f"admitting new work (D15).")
    return ("unknown spend: the ledger could not be read "
            f"({health.get('error') or 'unreadable'}). Every admission refuses "
            "while the book is unreadable, because unknown spend is not zero "
            "(D15).")


def begin_run(cfg: Any, agent_id: int, *, cap_cents: int,
              model: str = "", run_id: Optional[str] = None) -> Dict[str, Any]:
    """Write the dispatch receipt. Returns the run id.

    Refuses — typed, naming the unsettled run — while the book is incomplete.
    """
    if not isinstance(cap_cents, int) or isinstance(cap_cents, bool):
        raise OfficeError(BAD_INPUT_CODE, "cap_cents must be an integer")
    if cap_cents < 0:
        raise OfficeError(BAD_INPUT_CODE, "cap_cents must be >= 0")

    health = ledger.health(cfg)
    if not health["complete"]:
        raise OfficeError(ADMISSION_CODE, _unknown_spend_detail(health))

    rid = run_id or f"run_{uuid.uuid4().hex[:16]}"
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        if conn.execute("SELECT 1 FROM office_run_receipts WHERE run_id=?",
                        (rid,)).fetchone() is not None:
            raise OfficeError(BAD_STATE_CODE, f"run {rid!r} already receipted")
        conn.execute(
            "INSERT INTO office_run_receipts(run_id, agent_id, started_at,"
            " model, status, cap_cents) VALUES(?,?,?,?,'open',?)",
            (rid, agent_id, store.now(), model, cap_cents))
        conn.commit()
        store.audit("run.begin", "office:runner",
                    {"run_id": rid, "agent_id": agent_id,
                     "cap_cents": cap_cents})
        return {"state": "OK", "run_id": rid, "agent_id": agent_id,
                "cap_cents": cap_cents, "status": "open"}
    finally:
        conn.close()


def finish_run(cfg: Any, run_id: str, *, settled_cents: int,
               unmetered: bool = False, attested: bool = False,
               attested_as: Optional[str] = None, tokens_in: int = 0,
               tokens_out: int = 0, provider_cost_micros: int = 0,
               model: str = "",
               source: Optional[str] = None,
               external_ref: Optional[str] = None,
               agent_id: Optional[int] = None,
               department_id: Optional[int] = None,
               note: str = "") -> Dict[str, Any]:
    """Record the real usage, **then** settle the receipt.

    Order matters: the ledger row must be durable before the receipt closes.
    If the row write fails the receipt stays open, which reports the run as
    unknown spend rather than as free.
    """
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        row = conn.execute("SELECT * FROM office_run_receipts WHERE run_id=?",
                           (run_id,)).fetchone()
        if row is None:
            raise OfficeError(NOT_FOUND_CODE, f"no run receipt {run_id!r}")
        if row["status"] == "settled":
            raise OfficeError(BAD_STATE_CODE,
                              f"run {run_id!r} is already settled")
        rid_agent = int(row["agent_id"]) if row["agent_id"] else agent_id
        rid_cap = row["cap_cents"]
    finally:
        conn.close()

    entry = ledger.record(
        cfg, "inference", int(settled_cents), agent_id=rid_agent,
        department_id=department_id, run_id=run_id, note=note,
        recorded_by="office:runner", unmetered=unmetered,
        attested=attested, attested_as=attested_as, tokens_in=tokens_in,
        tokens_out=tokens_out, provider_cost_micros=provider_cost_micros,
        source=source, external_ref=external_ref)

    # A row reached the ledger: that IS a `ledger_write` (the tower's `vault`
    # walk / `settling` pose). Emitted here, by the code that actually booked
    # the money, so the vault animation can only fire when money really moved
    # (C7). A5 owns the emission; the payload carries the row id and cents so
    # the event is the same evidence the ledger row is. It deliberately does
    # NOT carry `run_id`: the ledger row itself is run-scoped, and `runner.trace`
    # joins a run's events by `payload LIKE '"run_id": …'` — adding the key here
    # would silently widen that pinned surface for a value the ledger row already
    # holds.
    try:
        from . import agent_memory as _mem

        _mem.emit_event(cfg, "ledger_write", agent_id=rid_agent,
                        department_id=department_id,
                        payload={"ledger_id": entry.get("id"),
                                 "cents": int(settled_cents),
                                 "kind": "inference",
                                 "unmetered": bool(unmetered)},
                        visible="badge")
    except Exception:                                   # noqa: BLE001
        pass

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        conn.execute(
            "UPDATE office_run_receipts SET status='settled', settled_at=?,"
            " settled_cents=?, attested_as=?, model=COALESCE(NULLIF(?,''),"
            " model) WHERE run_id=?",
            (store.now(), int(settled_cents), attested_as, model, run_id))
        conn.commit()
        store.audit("run.finish", "office:runner",
                    {"run_id": run_id, "cents": int(settled_cents),
                     "cap_cents": rid_cap, "unmetered": bool(unmetered)})
        return {"state": "OK", "run_id": run_id, "settled": True,
                "ledger_id": entry["id"], "settled_cents": int(settled_cents),
                "unmetered": bool(unmetered), "cap_cents": rid_cap}
    finally:
        conn.close()


def settle(cfg: Any, run_id: str, *, cents: int, attested_as: str,
           model: str = "", note: str = "") -> Dict[str, Any]:
    """Close an *unknown* spend with the owner's attestation (D15).

    ``attested_as='entered'`` — the owner typed the provider's real figure.
    ``attested_as='limit'`` — the charge is booked at the run's own cap, and
    says so, because it is not a measurement.
    """
    if attested_as not in ("entered", "limit"):
        raise OfficeError(
            BAD_INPUT_CODE,
            "settlement must say how it was attested: 'entered' (the "
            "provider's own figure) or 'limit' (counted at the run's cap)")
    if not isinstance(cents, int) or isinstance(cents, bool) or cents < 0:
        raise OfficeError(BAD_INPUT_CODE, "cents must be a non-negative int")

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        row = conn.execute("SELECT * FROM office_run_receipts WHERE run_id=?",
                           (run_id,)).fetchone()
        if row is None:
            raise OfficeError(NOT_FOUND_CODE, f"no run receipt {run_id!r}")
        if row["status"] == "settled":
            raise OfficeError(BAD_STATE_CODE,
                              f"run {run_id!r} is already settled")
        cap = row["cap_cents"]
        agent_id = int(row["agent_id"]) if row["agent_id"] else None
    finally:
        conn.close()

    if attested_as == "limit" and cap is not None and int(cents) != int(cap):
        raise OfficeError(
            BAD_INPUT_CODE,
            f"a 'limit' attestation books the run at its own cap "
            f"({cap} cents), not {cents}. Use 'entered' with the real figure, "
            f"or book the cap (D15).")

    return finish_run(cfg, run_id, settled_cents=int(cents), attested=True,
                      attested_as=attested_as, agent_id=agent_id, model=model,
                      note=note or f"owner-attested ({attested_as})")


def halt(cfg: Any, run_id: str, *, reason: str = "emergency stop"
         ) -> Dict[str, Any]:
    """Mark a run stopped by the e-stop, so a stop is not read as a crash."""
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        row = conn.execute("SELECT status FROM office_run_receipts WHERE"
                           " run_id=?", (run_id,)).fetchone()
        if row is None:
            raise OfficeError(NOT_FOUND_CODE, f"no run receipt {run_id!r}")
        if row["status"] == "settled":
            raise OfficeError(BAD_STATE_CODE,
                              f"run {run_id!r} is settled; it cannot be halted")
        conn.execute("UPDATE office_run_receipts SET status='halted',"
                     " end_reason=? WHERE run_id=?", (reason, run_id))
        conn.commit()
        store.audit("run.halt", "office:runner",
                    {"run_id": run_id, "reason": reason})
        return {"state": "OK", "run_id": run_id, "status": "halted",
                "reason": reason}
    finally:
        conn.close()


def unsettled(cfg: Any) -> Dict[str, Any]:
    """Every receipt that is not settled: the org's known-unknown spend."""
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        rows: List[Dict[str, Any]] = [
            dict(r) for r in conn.execute(
                "SELECT r.*, a.slug AS agent_slug FROM office_run_receipts r"
                " LEFT JOIN office_agent a ON a.id = r.agent_id"
                " WHERE r.status != 'settled' ORDER BY r.started_at")]
        return {"state": "OK", "count": len(rows), "runs": rows,
                "complete": not rows}
    finally:
        conn.close()


def receipts(cfg: Any, *, limit: int = 50) -> Dict[str, Any]:
    limit = max(1, min(int(limit or 50), 500))
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM office_run_receipts ORDER BY started_at DESC"
            " LIMIT ?", (limit,))]
        return {"state": "OK", "count": len(rows), "receipts": rows}
    finally:
        conn.close()


def admit(cfg: Any, *, agent_id: int, estimated_cents: int,
          caps: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """The admission gate: completeness first, then the caps (D15/D16/C8).

    Refuses rather than truncating. Every refusal is typed and names the
    reason, so the runner can record *why* a turn did not start.
    """
    from . import caps as CAPS

    health = ledger.health(cfg)
    if not health["complete"]:
        raise OfficeError(ADMISSION_CODE, _unknown_spend_detail(health))

    eff = caps or CAPS.resolve(cfg)
    remaining_agent = ledger.balance(cfg, agent_id)["balance_cents"]
    if remaining_agent <= 0:
        raise OfficeError(
            ADMISSION_CODE,
            f"agent {agent_id} has a non-positive balance "
            f"({remaining_agent} cents); the seat idles until it earns "
            f"(D1)")

    for key in ("perRun", "perAgent", "perDay", "global"):
        cap = eff.get(key)
        if cap is None or cap == 0:          # 0 is a real value: "no cap"
            continue
        if key == "perRun" and estimated_cents > cap:
            raise OfficeError(
                ADMISSION_CODE,
                f"perRun cap: this turn estimates {estimated_cents} cents and "
                f"the ceiling is {cap}")
        if key in ("perDay", "global"):
            spent = _spent(cfg, key)
            if spent + estimated_cents > cap:
                raise OfficeError(
                    ADMISSION_CODE,
                    f"{key} cap: {spent} cents already spent, "
                    f"{estimated_cents} estimated, ceiling {cap}")
    return {"state": "OK", "agent_id": agent_id,
            "estimated_cents": estimated_cents, "caps": eff,
            "balance_cents": remaining_agent}


def _spent(cfg: Any, key: str) -> int:
    """Metered spend only, and only over the window the key names (D16)."""
    import time as _time

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        if key == "global":
            row = conn.execute(
                "SELECT COALESCE(SUM(amount_cents),0) c FROM office_ledger"
                " WHERE unmetered=0 AND kind IN"
                " ('inference','fees','capex')").fetchone()
        else:  # perDay: the local day, not a rolling 24h window
            midnight = _time.mktime(_time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
            row = conn.execute(
                "SELECT COALESCE(SUM(amount_cents),0) c FROM office_ledger"
                " WHERE unmetered=0 AND kind IN ('inference','fees','capex')"
                " AND ts >= ?", (midnight,)).fetchone()
        return int(row["c"])
    finally:
        conn.close()
