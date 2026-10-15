"""Budget envelopes: the thing that stops one CEO being the org's throughput.

Every level holds a monthly envelope, allocated by the level above (D37):

```
owner  →  the org envelope (the only capital that enters from outside)
  CEO  →  per-portfolio envelopes + a reserve
   director  →  per-department envelopes
     manager  →  spends its department envelope
```

Two rules, and both are the point:

* **Inside the envelope, spending is free and is only *reported*.** No request,
  no approval, no wait. A manager rebranding a storefront must not need the
  CEO's desk.
* **Above the envelope, it is refused unless raised** — and a raise must name
  the four things the contract requires: the amount, the thesis, the expected
  return, and what it displaces (`expected_return` and `displaces` are not
  optional, because a raise without them is a wish).

Allocated figures **are** stored: they are decisions, made by a named seat. Spend
and remaining are **derived** from the ledger by `GROUP BY`, never stored — so a
displayed envelope balance cannot drift from the book (C2).
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from . import store
from .errors import (BAD_INPUT_CODE, BAD_STATE_CODE, NOT_FOUND_CODE,
                     OfficeError)

SCOPES = ("org", "portfolio", "department")
OPEN = "open"


def period_key(ts: Optional[float] = None) -> str:
    """The month a spend belongs to — a local-calendar key, not a rolling 30d."""
    t = time.localtime(ts if ts is not None else time.time())
    return f"{t.tm_year:04d}-{t.tm_mon:02d}"


def _seat(conn, agent_id: int):
    row = conn.execute("SELECT * FROM office_agent WHERE id=?",
                       (agent_id,)).fetchone()
    if row is None:
        raise OfficeError(NOT_FOUND_CODE, f"no seat #{agent_id}")
    return row


# ------------------------------------------------------------------ allocate
def allocate(cfg: Any, *, scope: str, scope_id: Optional[int], cents: int,
             by_agent: Optional[int], period: Optional[str] = None,
             reserve: bool = False, confirmed: bool = False,
             requested_by: str = "cli:office") -> Dict[str, Any]:
    """Set one level's envelope for a period. Supersedes, never edits."""
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE,
                          "office envelope-allocate requires --confirm")
    if scope not in SCOPES:
        raise OfficeError(BAD_INPUT_CODE,
                          f"scope must be one of {', '.join(SCOPES)}")
    if scope == "org" and scope_id is not None:
        raise OfficeError(BAD_INPUT_CODE,
                          "the org envelope has no scope_id")
    # A `portfolio` envelope with no scope_id is the CEO's unallocated reserve.
    # Every other scoped envelope must name what it covers.
    if scope != "org" and scope_id is None and not reserve:
        raise OfficeError(BAD_INPUT_CODE,
                          f"a {scope} envelope needs a scope_id")
    if not isinstance(cents, int) or isinstance(cents, bool) or cents < 0:
        raise OfficeError(BAD_INPUT_CODE,
                          "cents must be a non-negative integer")
    if scope == "org" and by_agent is not None:
        raise OfficeError(
            BAD_INPUT_CODE,
            "the org envelope is set by the owner, who is not an agent; "
            "pass no by_agent")
    if scope != "org" and by_agent is None:
        raise OfficeError(
            BAD_INPUT_CODE,
            f"a {scope} envelope must name the seat that allocated it")
    pk = period or period_key()

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        if by_agent is not None:
            _seat(conn, by_agent)
        # Explicit select/update-or-insert rather than ON CONFLICT: a reserve
        # has a NULL scope_id, and SQLite treats NULLs as distinct in a UNIQUE
        # index, so an upsert would quietly add a second reserve row instead of
        # replacing the first.
        existing = conn.execute(
            "SELECT id FROM office_envelope WHERE period=? AND scope=? AND"
            " ((scope_id IS NULL AND ? IS NULL) OR scope_id=?)",
            (pk, scope, scope_id, scope_id)).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO office_envelope(period, scope, scope_id,"
                " allocated_cents, allocated_by, reserve, created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (pk, scope, scope_id, int(cents), by_agent,
                 1 if reserve else 0, store.now()))
        else:
            conn.execute(
                "UPDATE office_envelope SET allocated_cents=?, allocated_by=?,"
                " reserve=? WHERE id=?",
                (int(cents), by_agent, 1 if reserve else 0, existing["id"]))
        conn.commit()
        store.audit("envelope.allocate", requested_by,
                    {"period": pk, "scope": scope, "scope_id": scope_id,
                     "cents": int(cents)})
        return {"state": "OK", "period": pk, "scope": scope,
                "scope_id": scope_id, "allocated_cents": int(cents),
                "allocated_by": by_agent, "reserve": bool(reserve)}
    finally:
        conn.close()


def allocate_portfolios(cfg: Any, *, by_agent: int, total_cents: int,
                        reserve_pct: int = 20, period: Optional[str] = None,
                        confirmed: bool = False,
                        requested_by: str = "cli:office") -> Dict[str, Any]:
    """Split the org envelope across the portfolios, keeping the CEO's reserve.

    The reserve is what makes the fast-track case possible (a time-limited
    market, a regulatory deadline) without re-opening every envelope.
    """
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE,
                          "office envelope-split requires --confirm")
    if not 0 <= int(reserve_pct) <= 90:
        raise OfficeError(BAD_INPUT_CODE, "reserve_pct must be 0..90")

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        _seat(conn, by_agent)
        directors = [dict(r) for r in conn.execute(
            "SELECT id, slug, portfolio FROM office_agent WHERE"
            " seat_type='director_portfolio' AND status != 'terminated'"
            " ORDER BY id")]
    finally:
        conn.close()

    if not directors:
        raise OfficeError(
            BAD_STATE_CODE,
            "no director_portfolio seat exists, so there is nothing to split "
            "the org envelope across. Hire the directorate first (D35).")

    pk = period or period_key()
    reserve_cents = int(round(int(total_cents) * int(reserve_pct) / 100.0))
    pool = int(total_cents) - reserve_cents
    each = pool // len(directors)
    remainder = pool - each * len(directors)

    out = []
    allocate(cfg, scope="org", scope_id=None, cents=int(total_cents),
             by_agent=None, period=pk, reserve=False, confirmed=True,
             requested_by=requested_by)
    allocate(cfg, scope="portfolio", scope_id=None, cents=reserve_cents,
             by_agent=by_agent, period=pk, reserve=True, confirmed=True,
             requested_by=requested_by)
    for i, d in enumerate(directors):
        share = each + (remainder if i == 0 else 0)   # remainder to the first
        allocate(cfg, scope="portfolio", scope_id=d["id"], cents=share,
                 by_agent=by_agent, period=pk, confirmed=True,
                 requested_by=requested_by)
        out.append({"director_id": d["id"], "slug": d["slug"],
                    "portfolio": d["portfolio"], "cents": share})
    return {"state": "OK", "period": pk, "total_cents": int(total_cents),
            "reserve_cents": reserve_cents, "split_cents": pool,
            "directors": out}


# ---------------------------------------------------------------------- read
def envelopes(cfg: Any, *, period: Optional[str] = None) -> Dict[str, Any]:
    """Every envelope with its *derived* spend and remaining (C2).

    `spent_cents` is computed from the ledger for the period, never stored.
    """
    pk = period or period_key()
    t0 = _period_start(pk)
    t1 = _period_end(pk)
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM office_envelope WHERE period=? ORDER BY"
            " CASE scope WHEN 'org' THEN 0 WHEN 'portfolio' THEN 1 ELSE 2 END,"
            " scope_id", (pk,))]
        for row in rows:
            spent = _spent_for(cfg, conn, row, t0, t1)
            row["spent_cents"] = spent
            allowed = int(row["allocated_cents"]) + int(row["carried_cents"])
            row["remaining_cents"] = allowed - spent
            row["overspent"] = spent > allowed
            row["utilisation_pct"] = (
                round(100.0 * spent / allowed, 1) if allowed else None)
        return {"state": "OK", "period": pk, "count": len(rows),
                "envelopes": rows}
    finally:
        conn.close()


def _spent_for(cfg: Any, conn, envelope: Dict[str, Any], t0: float,
               t1: float) -> int:
    """Metered spend in the period for whatever this envelope covers (D16)."""
    base = ("SELECT COALESCE(SUM(amount_cents),0) c FROM office_ledger"
            " WHERE unmetered=0 AND kind IN ('inference','fees','capex')"
            " AND ts >= ? AND ts < ?")
    scope = envelope["scope"]
    if scope == "org":
        return int(conn.execute(base, (t0, t1)).fetchone()["c"])
    if scope == "portfolio":
        if envelope["reserve"]:
            return 0            # the reserve is unspent by construction
        return int(conn.execute(
            base + " AND department_id IN (SELECT id FROM office_department"
                   " WHERE director_id=?)",
            (t0, t1, envelope["scope_id"])).fetchone()["c"])
    return int(conn.execute(base + " AND department_id=?",
                            (t0, t1, envelope["scope_id"])).fetchone()["c"])


def _period_start(pk: str) -> float:
    y, m = (int(x) for x in pk.split("-"))
    return time.mktime((y, m, 1, 0, 0, 0, 0, 0, -1))


def _period_end(pk: str) -> float:
    y, m = (int(x) for x in pk.split("-"))
    return time.mktime((y + (m // 12), (m % 12) + 1, 1, 0, 0, 0, 0, 0, -1))


def headroom_for(cfg: Any, *, agent_id: int,
                 period: Optional[str] = None) -> Dict[str, Any]:
    """What this seat may spend before it has to raise.

    Resolution: a department envelope if the seat is in a department; the
    portfolio envelope of that department's director otherwise; the org
    envelope as the last resort.
    """
    pk = period or period_key()
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        seat = _seat(conn, agent_id)
        dept_id = seat["department_id"]
        chain: List[Dict[str, Any]] = []
        if dept_id:
            row = conn.execute("SELECT * FROM office_envelope WHERE period=?"
                               " AND scope='department' AND scope_id=?",
                               (pk, dept_id)).fetchone()
            if row is not None:
                chain.append(dict(row))
            d = conn.execute("SELECT director_id FROM office_department"
                             " WHERE id=?", (dept_id,)).fetchone()
            if d is not None and d["director_id"] is not None:
                row = conn.execute(
                    "SELECT * FROM office_envelope WHERE period=? AND"
                    " scope='portfolio' AND scope_id=?",
                    (pk, d["director_id"])).fetchone()
                if row is not None:
                    chain.append(dict(row))
        row = conn.execute("SELECT * FROM office_envelope WHERE period=? AND"
                           " scope='org'", (pk,)).fetchone()
        if row is not None:
            chain.append(dict(row))
    finally:
        conn.close()

    if not chain:
        # No envelope anywhere: uncapped, and the caller should say so plainly.
        return {"state": "OK", "agent_id": agent_id, "period": pk,
                "envelope": None, "headroom_cents": None,
                "meaning": "no envelope is set for this period"}
    env = chain[0]
    t0, t1 = _period_start(pk), _period_end(pk)
    conn = store.connect(cfg)
    try:
        spent = _spent_for(cfg, conn, env, t0, t1)
    finally:
        conn.close()
    allowed = int(env["allocated_cents"]) + int(env["carried_cents"])
    return {"state": "OK", "agent_id": agent_id, "period": pk,
            "envelope": {"scope": env["scope"], "scope_id": env["scope_id"],
                         "allocated_cents": env["allocated_cents"],
                         "reserve": bool(env["reserve"])},
            "spent_cents": spent, "headroom_cents": allowed - spent,
            "chain": [{"scope": e["scope"], "scope_id": e["scope_id"]}
                      for e in chain]}


# ------------------------------------------------------------- spend / raise
def check_spend(cfg: Any, *, agent_id: int, cents: int,
                period: Optional[str] = None) -> Dict[str, Any]:
    """May this seat spend this much right now, and why not if it may not?

    Two independent bounds, and both must pass:

    * **The envelope** — the policy bound. Above it, the seat must raise.
    * **The balance** — the solvency bound. A seat cannot spend money it does
      not have; that is what makes runway mean something. Without this check an
      agent runs to a large negative balance and its runway goes negative too,
      so "at risk" fires only after it is already dead.

    Returns rather than raises, because the caller needs to *report* the
    outcome either way; `spend()` is the enforcing wrapper.
    """
    info = headroom_for(cfg, agent_id=agent_id, period=period)
    if info["envelope"] is None:
        policy_ok = True
        policy_reason = "no envelope is set, so the spend is uncapped"
    else:
        head = int(info["headroom_cents"])
        policy_ok = int(cents) <= head
        policy_reason = (
            "inside the envelope" if policy_ok else
            f"this spend is {cents} cents and the "
            f"{info['envelope']['scope']} envelope has {head} cents left; "
            f"raise it to the level above")

    from . import ledger as L

    balance = int(L.balance(cfg, int(agent_id))["balance_cents"])
    solvent = balance >= int(cents)
    funds_reason = (f"this spend is {cents} cents and the seat holds "
                    f"{balance} cents. A seat cannot spend money it does not "
                    f"have, or its runway stops meaning anything")

    # Both bounds are always evaluated. Returning early on the policy check
    # would leave `needs_funds` unset, and a seat that had its raise approved
    # would then be allowed to spend a balance it does not have — which is the
    # one thing the envelope is not allowed to paper over.
    if not policy_ok and not solvent:
        reason = f"{policy_reason} (and {funds_reason})"
    elif not policy_ok:
        reason = policy_reason
    elif not solvent:
        reason = funds_reason
    else:
        reason = policy_reason

    return {**info, "allowed": policy_ok and solvent,
            "needs_raise": not policy_ok, "needs_funds": not solvent,
            "balance_cents": balance, "reason": reason}


def spend(cfg: Any, *, agent_id: int, cents: int, kind: str = "inference",
          run_id: Optional[str] = None, note: str = "",
          department_id: Optional[int] = None,
          unmetered: bool = False,
          recorded_by: str = "office:runner") -> Dict[str, Any]:
    """Book a spend, refusing anything above the envelope with no raise.

    Inside → a plain ledger row. Above → refused, unless an approved raise
    exists for this seat and period.
    """
    from . import ledger as L

    check = check_spend(cfg, agent_id=agent_id, cents=cents)
    if check["allowed"]:
        row = L.record(cfg, kind, int(cents), agent_id=agent_id,
                       department_id=department_id, run_id=run_id, note=note,
                       unmetered=unmetered, recorded_by=recorded_by)
        return {"state": "OK", "booked": True, "ledger_id": row["id"],
                "envelope": check["envelope"],
                "within_envelope": True}

    # The two refusals are distinct and the codes must not be confused: a
    # solvency problem is not fixable by a raise, and a policy problem is not
    # fixable by funding.
    #
    # The approval must COVER this spend (FINDING #1). `_approved_raise_headroom`
    # returns the cents of *approved* raise the seat holds for this period that
    # it has not already drawn on; a 1-cent approval therefore clears exactly one
    # cent above the envelope, not any amount. A stale approval (a different
    # period) or a denied one contributes nothing.
    approved = _approved_raise_headroom(cfg, agent_id=agent_id, check=check)
    if check.get("needs_raise") and not _approval_covers(
            check, cents=int(cents), approved_cents=approved):
        raise OfficeError(
            "OFFICE_ENVELOPE_EXCEEDED",
            f"refused: {check['reason']}. A raise must name the amount, the "
            f"thesis, the expected return, and what it displaces (D37/C31); "
            f"an approved raise must cover THIS spend, and the seat holds "
            f"{approved} approved cent(s) above the envelope this period.")
    if check.get("needs_funds"):
        raise OfficeError(
            "OFFICE_INSUFFICIENT_BALANCE",
            f"refused: {check['reason']}. A raise cannot fix this; the seat "
            f"needs revenue or an allocation of capital.")

    row = L.record(cfg, kind, int(cents), agent_id=agent_id,
                   department_id=department_id, run_id=run_id, note=note,
                   unmetered=unmetered, recorded_by=recorded_by)
    return {"state": "OK", "booked": True, "ledger_id": row["id"],
            "envelope": check["envelope"], "within_envelope": False,
            "raised": True}


def _approval_covers(check: Dict[str, Any], *, cents: int,
                     approved_cents: int) -> bool:
    """Does the approved raise cover the part of this spend above the envelope?

    The envelope has already paid for whatever headroom remains; an approval only
    ever authorises the *overage*. A negative headroom means the seat is already
    above the envelope, so every new cent is overage. The approval covers the
    spend iff the overage is not more than the approved cents available.
    """
    if check.get("envelope") is None:
        return True                       # no envelope → nothing to cover
    head = max(0, int(check.get("headroom_cents") or 0))
    overage = max(0, int(cents) - head)
    return overage <= int(approved_cents)


def _approved_raise_headroom(cfg: Any, *, agent_id: int,
                             check: Dict[str, Any]) -> int:
    """Approved raise cents this seat holds for THIS period and has not drawn.

    The amount and period are the whole point (FINDING #1): an approval is only
    an authorisation for the amount the level above actually approved, in the
    period it approved it for. Sum every `approved` request in the period, then
    subtract the overage the seat has already drawn this period (read from the
    caller's own `check`, so no nested connection) — so two spends cannot both
    draw the same approval, and a stale approval from another period contributes
    nothing.
    """
    pk = check.get("period") or period_key()
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        row = conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) c FROM office_request"
            " WHERE agent_id=? AND status='approved' AND period=?",
            (agent_id, pk)).fetchone()
        approved = int(row["c"])
        if approved <= 0:
            return 0
        if check.get("envelope") is None:
            return approved
        head = int(check.get("headroom_cents") or 0)
        if head >= 0:
            return approved              # nothing above the envelope yet
        # head<0 means the seat has already drawn |head| above the envelope;
        # that portion of the approval is spent and cannot be drawn again.
        return max(0, approved + head)
    finally:
        conn.close()


def raise_above(cfg: Any, *, agent_id: int, amount_cents: int, thesis: str,
                expected_return: str, displaces: str,
                period: Optional[str] = None,
                confirmed: bool = False,
                requested_by: str = "cli:office") -> Dict[str, Any]:
    """Ask the level above for more than the envelope holds (D37/C31).

    The four required fields are not decoration: `thesis` says why,
    `expected_return` says what the money buys, and `displaces` says what stops
    — which is the question the level above actually has to answer.
    """
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE,
                          "office raise requires --confirm")
    for field, value in (("thesis", thesis),
                         ("expected_return", expected_return),
                         ("displaces", displaces)):
        if not str(value or "").strip():
            raise OfficeError(
                BAD_INPUT_CODE,
                f"a raise must name its {field}; a raise without it is a "
                f"wish, and the level above cannot decide it (C31)")
    if not isinstance(amount_cents, int) or amount_cents <= 0:
        raise OfficeError(BAD_INPUT_CODE, "amount_cents must be > 0")
    pk = period or period_key()

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        seat = _seat(conn, agent_id)
        if seat["seat_type"] == "ceo":
            raise OfficeError(
                BAD_STATE_CODE,
                "the CEO raises to the owner, who is not an agent (D2); file "
                "a memo instead (D24)")
        supervisor = seat["supervisor_id"]
        if supervisor is None:
            raise OfficeError(
                BAD_STATE_CODE,
                f"seat {seat['slug']!r} has no supervisor to raise to (C30)")
        cur = conn.execute(
            "INSERT INTO office_request(ts, agent_id, supervisor_id, scope,"
            " scope_id, amount_cents, thesis, expected_return, displaces,"
            " period) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (store.now(), agent_id, supervisor, "above_envelope",
             seat["department_id"], int(amount_cents), str(thesis).strip(),
             str(expected_return).strip(), str(displaces).strip(), pk))
        conn.commit()
        rid = int(cur.lastrowid or 0)
        store.audit("request.raise", requested_by,
                    {"id": rid, "agent_id": agent_id, "cents": amount_cents})
        return {"state": "OK", "id": rid, "agent_id": agent_id,
                "supervisor_id": supervisor, "period": pk,
                "amount_cents": int(amount_cents)}
    finally:
        conn.close()


def requests(cfg: Any, *, status: Optional[str] = None) -> Dict[str, Any]:
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        sql = ("SELECT r.*, a.slug AS agent_slug, s.slug AS supervisor_slug"
               " FROM office_request r LEFT JOIN office_agent a ON a.id=r.agent_id"
               " LEFT JOIN office_agent s ON s.id=r.supervisor_id")
        args: List[Any] = []
        if status:
            sql += " WHERE r.status=?"
            args.append(status)
        sql += " ORDER BY r.id DESC"
        rows = [dict(r) for r in conn.execute(sql, args)]
        return {"state": "OK", "count": len(rows), "requests": rows}
    finally:
        conn.close()


def decide_request(cfg: Any, request_id: int, decision: str, *,
                   by_agent: int, note: str = "", confirmed: bool = False,
                   requested_by: str = "cli:office") -> Dict[str, Any]:
    """Approve or deny a raise. **Only the seat it was raised to may decide.**

    That is the one-level rule (C31/D36): a peer cannot approve another peer's
    overspend, or the envelope stops meaning anything.
    """
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE,
                          "office request-decide requires --confirm")
    if decision not in ("approve", "deny"):
        raise OfficeError(BAD_INPUT_CODE, "decision must be approve or deny")
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        row = conn.execute("SELECT * FROM office_request WHERE id=?",
                           (request_id,)).fetchone()
        if row is None:
            raise OfficeError(NOT_FOUND_CODE, f"no request #{request_id}")
        if row["status"] != OPEN:
            raise OfficeError(BAD_STATE_CODE,
                              f"request #{request_id} is already"
                              f" {row['status']}")
        if int(by_agent) != int(row["supervisor_id"]):
            raise OfficeError(
                "OFFICE_REQUEST_WRONG_LEVEL",
                f"refused: request #{request_id} was raised to seat "
                f"#{row['supervisor_id']}, and this seat is #{by_agent}. Only "
                f"the level it was raised to may decide it (C31/D36).")
        conn.execute("UPDATE office_request SET status=?, decided_by=?,"
                     " decided_at=?, note=? WHERE id=?",
                     ("approved" if decision == "approve" else "denied",
                      int(by_agent), store.now(), note, request_id))
        conn.commit()
        store.audit("request.decide", requested_by,
                    {"id": request_id, "decision": decision})
        return {"state": "OK", "id": request_id, "status": decision + "d",
                "decided_by": int(by_agent)}
    finally:
        conn.close()
