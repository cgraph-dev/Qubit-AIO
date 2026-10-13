"""Survival, the daily tick, and reports upward.

An agent in this org is not a process that runs forever. It holds a balance,
it spends to think, and when the balance runs out it stops. The plan's rule is
that **running out is not a silent death**: the seat's manager is told, with its
runway, and decides (D34/D38). A corpse at a desk teaches nobody anything.

Two things are separated here on purpose:

* **The survival evaluation is mechanical and free.** Runway, burn, floor and
  at-risk are all `GROUP BY` over the ledger. No model call, no money, no
  unknown spend. It can run every day forever at zero cost.
* **Reflection costs money.** A reflector is injected; with none configured the
  tick says so in a typed field rather than pretending it reflected. The same
  shape as the runner's injected executor, for the same reason.

Reports upward (`office_report`) are checked, not trusted: a report may carry
`claims`, and every claim is verified against the ledger when it is filed. A
report whose numbers disagree with the book is stored **with the disagreement
recorded in it** — never silently corrected, never silently accepted.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from . import store
from .errors import BAD_INPUT_CODE, BAD_STATE_CODE, NOT_FOUND_CODE, OfficeError

#: Below this many days of runway a seat is escalated to its manager.
SURVIVAL_FLOOR_DAYS = 7.0

#: The window the burn rate is averaged over.
BURN_WINDOW_DAYS = 14.0

REPORT_KINDS = ("daily", "weekly", "memo")


# ------------------------------------------------------------------- runway
def _burn_cents_per_day(cfg: Any, agent_id: int, *, now: Optional[float] = None) -> float:
    """Metered inference spend per day over the burn window (D16)."""
    end = now if now is not None else store.now()
    start = end - BURN_WINDOW_DAYS * 86400.0
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        row = conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) c FROM office_ledger"
            " WHERE agent_id=? AND unmetered=0 AND kind='inference'"
            " AND ts >= ? AND ts <= ?", (int(agent_id), start, end)).fetchone()
        total = int(row["c"])
    finally:
        conn.close()
    return total / BURN_WINDOW_DAYS


def runway_days(cfg: Any, agent_id: int, *,
                now: Optional[float] = None) -> Dict[str, Any]:
    """How many days this seat can keep thinking at its current burn.

    A seat that is not spending has **no** runway limit (`days: None`), which
    is not the same as a large number: it means the question does not apply
    yet, and saying "infinite" would be a claim the ledger cannot support.
    """
    from . import ledger as L

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        seat = conn.execute("SELECT * FROM office_agent WHERE id=?",
                            (int(agent_id),)).fetchone()
    finally:
        conn.close()
    if seat is None:
        raise OfficeError(NOT_FOUND_CODE, f"no seat #{agent_id}")

    balance = int(L.balance(cfg, int(agent_id))["balance_cents"])
    burn = _burn_cents_per_day(cfg, int(agent_id), now=now)
    if burn <= 0:
        return {"state": "OK", "agent_id": int(agent_id),
                "agent_slug": seat["slug"], "balance_cents": balance,
                "burn_cents_per_day": 0.0, "days": None,
                "at_risk": False,
                "meaning": "not spending, so no runway limit applies"}
    days = balance / burn
    # A negative balance makes `days` negative, which reads as nonsense. It
    # means one thing — the seat is already out of money — so it is reported
    # as that, with the number kept for anyone who wants the arithmetic.
    insolvent = balance < 0
    return {"state": "OK", "agent_id": int(agent_id),
            "agent_slug": seat["slug"], "balance_cents": balance,
            "burn_cents_per_day": round(burn, 4), "days": round(days, 2),
            "floor_days": SURVIVAL_FLOOR_DAYS, "insolvent": insolvent,
            "at_risk": days < SURVIVAL_FLOOR_DAYS,
            "meaning": ("already out of money; every further turn spends "
                        "capital it does not have" if insolvent else
                        f"{days:.1f} days at the current burn")}


def at_risk(cfg: Any, *, now: Optional[float] = None) -> Dict[str, Any]:
    """Every live seat below the survival floor, worst first."""
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        ids = [int(r["id"]) for r in conn.execute(
            "SELECT id FROM office_agent WHERE status != 'terminated'"
            " ORDER BY id")]
    finally:
        conn.close()
    rows = [runway_days(cfg, i, now=now) for i in ids]
    risky = sorted([r for r in rows if r["at_risk"]],
                   key=lambda r: r["days"] if r["days"] is not None else 9e9)
    return {"state": "OK", "count": len(risky), "seats": len(rows),
            "floor_days": SURVIVAL_FLOOR_DAYS, "at_risk": risky}


# ----------------------------------------------------------------- daily tick
def daily_tick(cfg: Any, *, reflector: Optional[Callable] = None,
               now: Optional[float] = None,
               date_key: Optional[str] = None) -> Dict[str, Any]:
    """The daily pass: survival evaluation for every seat, then reflection.

    Survival is mechanical and always runs. A seat under the floor raises an
    escalation to its own manager — **once**, not once a day, because a
    manager who is told five times has been told nothing.
    """
    from . import escalation as E

    stamp = now if now is not None else store.now()
    key = date_key or time.strftime("%Y-%m-%d", time.localtime(stamp))

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        seats = [dict(r) for r in conn.execute(
            "SELECT id, slug, supervisor_id, seat_type FROM office_agent"
            " WHERE status != 'terminated' ORDER BY id")]
    finally:
        conn.close()

    raised: List[Dict[str, Any]] = []
    healthy: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for seat in seats:
        info = runway_days(cfg, int(seat["id"]), now=stamp)
        if not info["at_risk"]:
            healthy.append({"agent_id": info["agent_id"],
                            "slug": info["agent_slug"],
                            "days": info["days"]})
            continue
        if seat["seat_type"] == "ceo":
            skipped.append({"agent_id": info["agent_id"],
                            "slug": info["agent_slug"], "days": info["days"],
                            "why": "the CEO reports to the owner, not an agent "
                                   "(D2); file a report (D24)"})
            continue
        if _already_open(cfg, E, int(seat["id"]), "runway"):
            skipped.append({"agent_id": info["agent_id"],
                            "slug": info["agent_slug"], "days": info["days"],
                            "why": "an open runway escalation already owns this"})
            continue
        out = E.raise_to_supervisor(
            cfg, agent_id=int(seat["id"]), kind="runway",
            summary=(f"runway {info['days']} days is below the "
                     f"{SURVIVAL_FLOOR_DAYS:.0f}-day floor "
                     f"(balance {info['balance_cents']}c, burn "
                     f"{info['burn_cents_per_day']}c/day)"),
            payload={"days": info["days"], "balance_cents":
                     info["balance_cents"],
                     "burn_cents_per_day": info["burn_cents_per_day"],
                     "floor_days": SURVIVAL_FLOOR_DAYS})
        raised.append({"agent_id": info["agent_id"], "slug": info["agent_slug"],
                       "days": info["days"],
                       "escalation_id": out["id"],
                       "confirmation_id": out["confirmation_id"],
                       "supervisor_id": out["supervisor_id"]})

    reflection: Dict[str, Any]
    if reflector is None:
        # Not a failure and not a silent success: reflection is a paid turn and
        # none is configured, so the tick says exactly that.
        reflection = {"state": "NOT_CONFIGURED",
                      "meaning": "no reflector is configured, so no agent "
                                 "reflected. The survival pass above is "
                                 "mechanical and complete regardless.",
                      "reflected": 0}
    else:
        done = []
        for seat in seats:
            try:
                done.append(reflector(cfg, int(seat["id"]), key))
            except Exception as exc:  # noqa: BLE001 - one seat must not stop the tick
                done.append({"agent_id": seat["id"], "state": "ERROR",
                             "detail": str(exc)[:200]})
        reflection = {"state": "OK", "reflected": len(done), "results": done}

    # Fold in anything the shared surface already settled, so the office never
    # insists on a decision the owner has already made.
    synced = E.sync(cfg)

    return {"state": "OK", "date": key, "seats": len(seats),
            "healthy": len(healthy), "raised": raised,
            "skipped": skipped, "reflection": reflection,
            "journal_sync": synced,
            "at_risk": len(raised) + len(skipped)}


def _already_open(cfg: Any, E: Any, agent_id: int, kind: str) -> bool:
    return any(int(e["agent_id"]) == int(agent_id)
               for e in E.escalations(cfg, status="open")["escalations"]
               if e["kind"] == kind)


# --------------------------------------------------------------------- reports
def verify_claims(cfg: Any, claims: Dict[str, Any]) -> Dict[str, Any]:
    """Check a report's asserted numbers against the ledger (17-AUDIT.md).

    A key is ``<scope>[:<id>].<field>``, e.g. ``org.net_cents`` or
    ``department:3.cost_cents``. Only ledger-derived fields are checkable; an
    unchecked claim is reported as unchecked, never as verified.
    """
    from . import ledger as L

    checked, mismatched, unchecked = [], [], []
    for key, asserted in (claims or {}).items():
        head, _, field = str(key).partition(".")
        scope, _, sid = head.partition(":")
        if scope not in ("org", "agent", "department") or not field:
            unchecked.append({"claim": key, "why": "not a ledger claim"})
            continue
        try:
            row = L.pnl(cfg, scope=scope,
                        id=int(sid) if sid else None)
        except OfficeError as exc:
            unchecked.append({"claim": key, "why": exc.code})
            continue
        actual = row.get(field)
        if actual is None:
            unchecked.append({"claim": key,
                              "why": f"the ledger has no field {field!r}"})
            continue
        if int(actual) == int(asserted):
            checked.append({"claim": key, "value": int(actual)})
        else:
            mismatched.append({"claim": key, "claimed": int(asserted),
                               "actual": int(actual)})
    diff = "clean" if not mismatched else (
        "; ".join(f"{m['claim']}: claimed {m['claimed']}, ledger says "
                  f"{m['actual']}" for m in mismatched))
    return {"checked": checked, "mismatched": mismatched,
            "unchecked": unchecked, "ledger_diff": diff,
            "trustworthy": not mismatched}


def file_report(cfg: Any, *, agent_id: int, kind: str, period: str,
                body: str = "", recommendation: str = "", reasoning: str = "",
                claims: Optional[Dict[str, Any]] = None,
                confirmed: bool = False,
                requested_by: str = "cli:office") -> Dict[str, Any]:
    """Store a report, with its claims verified and any disagreement kept.

    The report is stored **even when its numbers are wrong** — a report that
    would be refused is a report an agent would simply rewrite until it passed.
    The disagreement travels with it instead, and the ledger is the source.
    """
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE,
                          "office report requires --confirm")
    kind = store.check_enum(kind, REPORT_KINDS, "report kind")
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        seat = conn.execute("SELECT * FROM office_agent WHERE id=?",
                            (int(agent_id),)).fetchone()
    finally:
        conn.close()
    if seat is None:
        raise OfficeError(NOT_FOUND_CODE, f"no seat #{agent_id}")

    verdict = verify_claims(cfg, claims or {})
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        cur = conn.execute(
            "INSERT INTO office_report(ts, agent_id, kind, period, body,"
            " recommendation, reasoning, ledger_diff) VALUES(?,?,?,?,?,?,?,?)",
            (store.now(), int(agent_id), kind, str(period), str(body),
             str(recommendation), str(reasoning), verdict["ledger_diff"]))
        conn.commit()
        rid = int(cur.lastrowid or 0)
    finally:
        conn.close()
    store.audit("report.file", requested_by,
                {"id": rid, "agent_id": agent_id, "kind": kind})
    return {"state": "OK", "id": rid, "agent_id": int(agent_id), "kind": kind,
            "trustworthy": verdict["trustworthy"],
            "ledger_diff": verdict["ledger_diff"],
            "mismatched": verdict["mismatched"],
            "unchecked": verdict["unchecked"]}


def org_facts(cfg: Any, *, period: Optional[str] = None) -> Dict[str, Any]:
    """The mechanical inputs to a weekly review — no model call, no money.

    This is what a CEO seat is handed to write its report from, so the report
    is *about* real numbers rather than about the model's impression of them.
    """
    from . import envelope as ENV
    from . import ledger as L

    return {
        "state": "OK",
        "org": L.pnl(cfg, scope="org"),
        "departments": L.pnl(cfg, scope="department")["rows"],
        "agents": L.pnl(cfg, scope="agent")["rows"],
        "envelopes": ENV.envelopes(cfg, period=period)["envelopes"],
        "survival": at_risk(cfg),
        "escalations_open": len(
            __import__("voicebridge.office.escalation", fromlist=["x"])
            .escalations(cfg, status="open")["escalations"]),
        "requests_open": len(
            __import__("voicebridge.office.envelope", fromlist=["x"])
            .requests(cfg, status="open")["requests"]),
        "unsettled_runs": len(
            __import__("voicebridge.office.receipts", fromlist=["x"])
            .unsettled(cfg)["runs"]),
    }


def reports(cfg: Any, *, kind: Optional[str] = None,
            limit: int = 20) -> Dict[str, Any]:
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        sql = ("SELECT r.*, a.slug AS agent_slug FROM office_report r"
               " LEFT JOIN office_agent a ON a.id=r.agent_id")
        args: List[Any] = []
        if kind:
            sql += " WHERE r.kind=?"
            args.append(kind)
        sql += " ORDER BY r.id DESC LIMIT ?"
        args.append(int(limit))
        rows = [dict(r) for r in conn.execute(sql, args)]
        return {"state": "OK", "count": len(rows), "reports": rows}
    finally:
        conn.close()


# ---------------------------------------------------------------- termination
def terminate(cfg: Any, agent_id: int, *, reason: str, by_agent: Optional[int] = None,
              confirmed: bool = False,
              requested_by: str = "cli:office") -> Dict[str, Any]:
    """Free a seat. **Termination is not deletion.**

    The seat stops working and is reported separately from the live org, but
    every ledger row it ever wrote stays exactly where it is: a terminated
    agent's losses are still the org's losses, and a book that forgot them
    would be lying about its own history (C1).
    """
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE,
                          "office terminate requires --confirm")
    if not str(reason or "").strip():
        raise OfficeError(BAD_INPUT_CODE, "a termination needs a reason")
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        seat = conn.execute("SELECT * FROM office_agent WHERE id=?",
                            (int(agent_id),)).fetchone()
        if seat is None:
            raise OfficeError(NOT_FOUND_CODE, f"no seat #{agent_id}")
        if seat["status"] == "terminated":
            raise OfficeError(BAD_STATE_CODE,
                              f"seat {seat['slug']!r} is already terminated")
        reports = conn.execute("SELECT COUNT(*) c FROM office_agent WHERE"
                               " supervisor_id=? AND status != 'terminated'",
                               (int(agent_id),)).fetchone()["c"]
        if int(reports) > 0:
            raise OfficeError(
                BAD_STATE_CODE,
                f"refused: seat {seat['slug']!r} still supervises {reports}"
                f" live seat(s). Terminate or reassign them first, or the "
                f"chain has a hole in it (C30).")
        conn.execute(
            "UPDATE office_agent SET status='terminated', terminated_at=?"
            " WHERE id=?", (store.now(), int(agent_id)))
        conn.commit()
    finally:
        conn.close()
    store.audit("seat.terminate", requested_by,
                {"agent_id": agent_id, "reason": str(reason)[:200],
                 "by_agent": by_agent})
    return {"state": "OK", "agent_id": int(agent_id), "slug": seat["slug"],
            "status": "terminated", "reason": str(reason),
            "ledger_rows_preserved": True}


def roster(cfg: Any, *, status: Optional[str] = None,
           include_terminated: bool = False) -> Dict[str, Any]:
    """The live org, with each seat's supervisor and balance.

    Terminated seats are **excluded by default** and reported separately: a
    roster that lists them alongside the living misrepresents the org's size,
    and a department's headcount is a thing the owner reads off this list.
    """
    from . import ledger as L

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        sql = ("SELECT a.*, s.slug AS supervisor_slug, d.slug AS department_slug"
               " FROM office_agent a"
               " LEFT JOIN office_agent s ON s.id=a.supervisor_id"
               " LEFT JOIN office_department d ON d.id=a.department_id")
        args: List[Any] = []
        if status:
            sql += " WHERE a.status=?"
            args.append(status)
        elif not include_terminated:
            sql += " WHERE a.status != 'terminated'"
        sql += " ORDER BY CASE a.seat_type WHEN 'ceo' THEN 0 ELSE 1 END, a.id"
        rows = [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()
    pnl = {r["agent_id"]: r for r in L.pnl(cfg, scope="agent")["rows"]}
    for row in rows:
        p = pnl.get(row["id"], {})
        row["balance_cents"] = p.get("balance_cents", 0)
        row["net_cents"] = p.get("net_cents", 0)
    return {"state": "OK", "count": len(rows), "seats": rows}
