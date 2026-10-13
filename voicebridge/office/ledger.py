"""The ledger: recording money, and deriving every total from it.

Two rules shape this module, and both are enforced in
:mod:`voicebridge.office.store` rather than here:

* **Append-only** (C1). There is no ``update`` and no ``delete``. A mistake is
  corrected by a :func:`reverse`, which writes a *compensating row* and names
  the row it offsets. Nothing in the org can rewrite history.
* **Derived totals** (C2). :func:`pnl` never reads a stored total; it reads the
  ``office_v_*_pnl`` views, which are ``GROUP BY`` over the ledger. A stored
  rollup and the ledger therefore cannot disagree — there is only one of them.

Sign convention: ``revenue``, ``inference``, ``fees`` and ``capex`` are stored
as **positive magnitudes** and the view applies direction from ``kind``;
``transfer`` and ``adjustment`` are signed, because a compensating row has to
be able to point either way. The store enforces exactly that with a ``CHECK``.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List

from . import store
from .errors import (BAD_INPUT_CODE, BAD_STATE_CODE, NOT_FOUND_CODE,
                     OfficeError)

MAGNITUDE_KINDS = ("inference", "revenue", "fees", "capex")
SIGNED_KINDS = ("transfer", "adjustment")


def record(cfg: Any, kind: str, amount_cents: int, *,
           agent_id: int | None = None, department_id: int | None = None,
           business_id: int | None = None,
           run_id: str | None = None, note: str = "",
           recorded_by: str = "cli:user", offsets_id: int | None = None,
           ts: float | None = None, unmetered: bool = False,
           attested: bool = False, attested_as: str | None = None,
           tokens_in: int = 0, tokens_out: int = 0,
           provider_cost_micros: int = 0,
           source: str | None = None,
           external_ref: str | None = None) -> Dict[str, Any]:
    """Append one row. This is the only way money enters the org's record.

    ``unmetered`` (D16): the row is recorded, but **no metered aggregate will
    ever see it**. Agent balances, budget caps and P&L exclude it; run counts
    include it. That is what stops a subscription-backed turn from
    manufacturing phantom spend that later denies real work.

    ``source`` + ``external_ref`` (idempotency): the pair identifies a provider
    charge, so a retried booking returns the original row instead of
    double-counting it.
    """
    kind = store.check_enum(kind, store.KINDS, "kind")
    if not isinstance(amount_cents, int) or isinstance(amount_cents, bool):
        raise OfficeError(BAD_INPUT_CODE,
                          "amount_cents must be an integer number of cents "
                          "(money is never a float here)")
    if kind in MAGNITUDE_KINDS and amount_cents < 0:
        raise OfficeError(
            BAD_INPUT_CODE,
            f"kind={kind} is a magnitude and must be >= 0; direction comes "
            f"from the kind. Use kind='transfer' for a signed correction.")
    if kind in SIGNED_KINDS and amount_cents == 0:
        raise OfficeError(BAD_INPUT_CODE,
                          "a transfer/adjustment of 0 cents records nothing")
    if kind == "transfer" and offsets_id is None:
        raise OfficeError(
            BAD_STATE_CODE,
            "a transfer must name the row it offsets (offsets_id); that is "
            "what a compensating entry is for (C1)")
    if attested and attested_as not in ("entered", "limit"):
        raise OfficeError(
            BAD_INPUT_CODE,
            "an attested row must say how it was attested: 'entered' (typed "
            "from the provider's own figure) or 'limit' (counted at the run's "
            "cap). A limit booking must never claim to be a typed figure "
            "(D15)")
    if attested_as and not attested:
        raise OfficeError(BAD_INPUT_CODE,
                          "attested_as is only meaningful with attested=True")
    if note and len(note) > 500:
        raise OfficeError(BAD_INPUT_CODE, "note must be <=500 chars")

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        # A seat belongs to a department, and its department's envelope and P&L
        # are derived from the rows it books. So the department is resolved from
        # the seat here rather than trusted to every caller: a cost whose
        # department was forgotten is a cost no P&L owns, and it would silently
        # under-report the department that actually spent it.
        if department_id is None and agent_id is not None:
            row = conn.execute("SELECT department_id FROM office_agent"
                               " WHERE id=?", (agent_id,)).fetchone()
            if row is not None:
                department_id = row["department_id"]
        if offsets_id is not None:
            exists = conn.execute("SELECT 1 FROM office_ledger WHERE id=?",
                                  (offsets_id,)).fetchone()
            if exists is None:
                raise OfficeError(
                    NOT_FOUND_CODE,
                    f"cannot offset ledger row #{offsets_id}: no such row")
        # Idempotency (D15): the same provider charge booked twice is one row.
        if source and external_ref:
            seen = conn.execute(
                "SELECT * FROM office_ledger WHERE source=? AND external_ref=?",
                (source, external_ref)).fetchone()
            if seen is not None:
                return {"state": "OK", "id": int(seen["id"]),
                        "kind": seen["kind"],
                        "amount_cents": int(seen["amount_cents"]),
                        "idempotent": True,
                        "note": "already booked under this source/ref"}
        cur = conn.execute(
            "INSERT INTO office_ledger(ts, kind, amount_cents, agent_id,"
            " department_id, business_id, run_id, note, recorded_by, offsets_id,"
            " unmetered, attested, attested_as, tokens_in, tokens_out,"
            " provider_cost_micros, source, external_ref)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts if ts is not None else store.now(), kind, amount_cents,
             agent_id, department_id, business_id, run_id, note, recorded_by,
             offsets_id, 1 if unmetered else 0, 1 if attested else 0, attested_as,
             int(tokens_in or 0), int(tokens_out or 0),
             int(provider_cost_micros or 0), source, external_ref))
        conn.commit()
        row_id = int(cur.lastrowid or 0)
        store.audit("ledger.record", recorded_by,
                    {"id": row_id, "kind": kind, "cents": amount_cents,
                     "unmetered": bool(unmetered)})
        return {"state": "OK", "id": row_id, "kind": kind,
                "amount_cents": amount_cents,
                "unmetered": bool(unmetered)}
    except sqlite3.IntegrityError as exc:
        # A concurrent racer inserted the same source/ref first: that is the
        # idempotency key doing its job, not a failure.
        if source and external_ref:
            seen = conn.execute(
                "SELECT * FROM office_ledger WHERE source=? AND external_ref=?",
                (source, external_ref)).fetchone()
            if seen is not None:
                return {"state": "OK", "id": int(seen["id"]),
                        "kind": seen["kind"],
                        "amount_cents": int(seen["amount_cents"]),
                        "idempotent": True}
        raise OfficeError(BAD_STATE_CODE,
                          f"ledger write refused: {exc}") from exc
    finally:
        conn.close()


def reverse(cfg: Any, entry_id: int, *, note: str = "",
            recorded_by: str = "cli:user") -> Dict[str, Any]:
    """Correct a row by appending its compensating opposite (never an edit).

    C1 states the fix exactly: a reversal is a ``transfer`` whose note names
    the row it offsets. The delta is the *negation of the row's net effect*,
    which differs by kind because the directional kinds are stored as
    magnitudes:

    * ``revenue`` 100 → its net effect was ``+100`` → reverse with ``-100``
    * ``inference`` 100 → its net effect was ``-100`` → reverse with ``+100``
      (the cost is given back; gross revenue and gross cost stay as recorded,
      which is what a credit note looks like on a real ledger)
    * ``transfer``/``adjustment`` are already signed → negate them
    """
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        row = conn.execute("SELECT * FROM office_ledger WHERE id=?",
                           (entry_id,)).fetchone()
        if row is None:
            raise OfficeError(NOT_FOUND_CODE, f"no ledger row #{entry_id}")
        amount = int(row["amount_cents"])
        if row["kind"] == "revenue":
            delta = -amount
        elif row["kind"] in ("inference", "fees", "capex"):
            delta = amount
        else:
            delta = -amount
        if delta == 0:
            raise OfficeError(BAD_STATE_CODE,
                              f"ledger row #{entry_id} has a zero effect and "
                              f"cannot be reversed")
        original = dict(row)

    finally:
        conn.close()

    out = record(cfg, "transfer", delta,
                 agent_id=original["agent_id"],
                 department_id=original["department_id"],
                 note=(note or f"reversal of #{entry_id}"),
                 recorded_by=recorded_by,
                 offsets_id=entry_id)
    out["reversed"] = entry_id
    out["reversed_kind"] = original["kind"]
    return out


def rows(cfg: Any, *, limit: int = 50, agent_id: int | None = None,
         department_id: int | None = None,
         kind: str | None = None) -> Dict[str, Any]:
    """Read ledger rows, newest first (read-only)."""
    limit = max(1, min(int(limit or 50), 500))
    where: List[str] = []
    args: List[Any] = []
    if agent_id is not None:
        where.append("agent_id = ?")
        args.append(agent_id)
    if department_id is not None:
        where.append("department_id = ?")
        args.append(department_id)
    if kind:
        where.append("kind = ?")
        args.append(store.check_enum(kind, store.KINDS, "kind"))
    sql = "SELECT * FROM office_ledger"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        out = [dict(r) for r in conn.execute(sql, args)]
        return {"state": "OK", "count": len(out), "rows": out}
    finally:
        conn.close()


def pnl(cfg: Any, *, scope: str = "org", id: int | None = None) -> Dict[str, Any]:
    """Derive P&L from the ledger views. Never reads a stored total."""
    view = {"org": "office_v_org_pnl", "agent": "office_v_agent_pnl",
            "department": "office_v_department_pnl",
            "business": "office_v_business_pnl"}.get((scope or "").strip())
    if view is None:
        raise OfficeError(BAD_INPUT_CODE,
                          "scope must be org, agent, department, or "
                          "business")
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        if scope == "org":
            row = conn.execute(f"SELECT * FROM {view}").fetchone()
            return {"state": "OK", "scope": "org", **dict(row)}
        key = {"agent": "agent_id", "department": "department_id",
               "business": "business_id"}[scope]
        if id is None:
            out = [dict(r) for r in conn.execute(
                f"SELECT * FROM {view} ORDER BY net_cents ASC")]
            return {"state": "OK", "scope": scope, "count": len(out),
                    "rows": out}
        row = conn.execute(f"SELECT * FROM {view} WHERE {key}=?",
                           (id,)).fetchone()
        if row is None:
            raise OfficeError(NOT_FOUND_CODE, f"no {scope} #{id}")
        return {"state": "OK", "scope": scope, **dict(row)}
    finally:
        conn.close()


def sum_metered(cfg: Any, *, scope: str = "org",
                id: int | None = None) -> int:
    """**Metered spend.** The fold that feeds every cap (D16/C15).

    Returns the cost magnitude (inference + fees + capex) that actually cost
    dollars, excluding ``unmetered`` rows — so it is ``100`` for one 100-cent
    metered turn, whichever way that turn is later netted against revenue.

    This is deliberately *spend*, not net: a cap answers "how many dollars has
    this burned", and netting revenue into it would let a profitable seat spend
    without limit. Balance and P&L are the net folds (:func:`balance`,
    :func:`pnl`).
    """
    return int(pnl(cfg, scope=scope, id=id)["cost_cents"])


def sum_reported(cfg: Any, *, scope: str = "org",
                 id: int | None = None) -> int:
    """**What the provider saw.** The fold that includes unmetered rows (D16).

    **Never feed this to a cap** — that is the exact bug StarNet documented,
    where phantom spend denied a later real run with a cap-hit note naming
    money nobody spent.
    """
    return int(pnl(cfg, scope=scope, id=id)["reported_cost_cents"])


def balance(cfg: Any, agent_id: int) -> Dict[str, Any]:
    """One seat's balance. Metered only, always (D16)."""
    row = pnl(cfg, scope="agent", id=agent_id)
    return {"state": "OK", "agent_id": agent_id,
            "balance_cents": int(row["balance_cents"]),
            "runs_recorded": int(row["entries"]),
            "unmetered_runs": int(row["unmetered_entries"])}


def health(cfg: Any) -> Dict[str, Any]:
    """Ledger health: totals, the two invariants, and **completeness** (D15).

    This is the operator-facing answer to "is the book still a book?":

    * are the append-only triggers still in the schema (C1)?
    * do the rows and the derived views still agree (C2)?
    * **is any run unsettled?** A receipt with no matching ledger row is
      spend-unknown, and unknown spend is not zero and not headroom — every
      admission refuses while ``complete`` is false.

    An **unreadable** store is itself unknown spend (D15: "or the ledger file
    was unreadable, every admission refuses"). It returns a typed DEGRADED
    result with ``complete: False`` rather than raising, so a corrupt file
    blocks the org instead of crashing the runner.

    Honesty note: a SQLite ``BEFORE`` trigger fires **per affected row**, so on
    an empty ledger the forbidden writes succeed trivially and prove nothing.
    That case reports ``null`` ("unverified — no rows") rather than ``False``,
    because the tower must never assert a state it cannot prove (D14).
    """
    try:
        conn = store.connect(cfg)
    except OfficeError as exc:
        return _degraded(cfg, str(exc))
    try:
        store.migrate(conn)
        totals = dict(conn.execute("SELECT * FROM office_v_org_pnl").fetchone())

        # C1(a) — the triggers must still be present in the schema.
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND"
            " tbl_name='office_ledger'")}
        triggers_present = {"office_ledger_no_update",
                            "office_ledger_no_delete"} <= names

        # C1(b) — prove enforcement live, but only where there is a row for
        # the trigger to fire on.
        rows = conn.execute("SELECT COUNT(*) c FROM office_ledger"
                            ).fetchone()["c"]
        enforced: Dict[str, Any] = {"update": None, "delete": None}
        if rows:
            for label, sql in (("update", "UPDATE office_ledger SET"
                                          " amount_cents = amount_cents + 1"),
                               ("delete", "DELETE FROM office_ledger")):
                conn.execute("SAVEPOINT office_health")
                try:
                    conn.execute(sql)
                    enforced[label] = False
                except sqlite3.Error:
                    enforced[label] = True
                finally:
                    conn.execute("ROLLBACK TO office_health")
                    conn.execute("RELEASE office_health")

        # D15 — completeness. An open (or halted) receipt is unknown spend.
        open_runs = [dict(r) for r in conn.execute(
            "SELECT run_id, agent_id, started_at, cap_cents FROM"
            " office_run_receipts WHERE status != 'settled' ORDER BY"
            " started_at")]
        complete = not open_runs

        # C2 — recompute the org total from the rows and compare to the view.
        raw = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN unmetered=0 AND kind='revenue'"
            " THEN amount_cents"
            " WHEN unmetered=0 AND kind IN ('inference','fees','capex')"
            " THEN -amount_cents"
            " WHEN unmetered=0 THEN amount_cents END),0) AS net"
            " FROM office_ledger").fetchone()
        drift = int(raw["net"]) - int(totals["net_cents"])

        proof_ok = all(v is not False for v in enforced.values())
        return {"state": "OK", "schema": store.SCHEMA,
                "store": str(store.db_path(cfg)),
                "entries": totals["entries"],
                "metered_entries": totals["metered_entries"],
                "unmetered_entries": totals["unmetered_entries"],
                "revenue_cents": totals["revenue_cents"],
                "cost_cents": totals["cost_cents"],
                "net_cents": totals["net_cents"],
                "reported_net_cents": totals["reported_net_cents"],
                "tokens_in": totals["tokens_in"],
                "tokens_out": totals["tokens_out"],
                "complete": complete,
                "open_runs": open_runs,
                "triggers_present": triggers_present,
                "append_only_enforced": enforced,
                "append_only_proof": ("live" if rows else
                                      "unverified: no rows for the trigger "
                                      "to fire on"),
                "rollup_drift_cents": drift,
                "healthy": bool(triggers_present and proof_ok and drift == 0
                                and complete)}
    except (sqlite3.Error, OfficeError) as exc:
        # A store that cannot be read is not a store that is empty.
        return _degraded(cfg, str(exc))
    finally:
        conn.close()


def _degraded(cfg: Any, error: str) -> Dict[str, Any]:
    """An unreadable ledger: typed, and refusing — never an empty book.

    D15 is explicit that an unreadable ledger is unknown spend. Reporting it as
    zero totals would be the worst possible answer: the org would read as
    debt-free exactly when it cannot see its own books.
    """
    return {"state": "DEGRADED", "schema": store.SCHEMA,
            "store": str(store.db_path(cfg)),
            "error": error,
            "complete": False,
            "healthy": False,
            "entries": None,
            "net_cents": None,
            "reported_net_cents": None,
            "open_runs": [],
            "append_only_proof": "unknown: the store could not be read",
            "advice": ("the ledger is unreadable, so all spend is unknown. "
                       "Every admission refuses until it can be read again "
                       "(D15).")}
