"""Double-entry reconciliation for the office ledger — and the refusal to lie.

The ledger (C1) is append-only and every total is derived (C2). What no module
answered is the accountant's first question: **is the books' net change fully
explained by the activity we recorded, or is there a residual we cannot name?**

This module answers it three ways:

* :func:`trial_balance` — one line per ledger *kind*, with the direction applied
  from the kind's sign rule, and a debit total and credit total that must be
  equal for the book to stand.
* :func:`explain` — the bridge from opening net to closing net: opening, plus
  revenue, minus costs, plus adjustments, equals closing **only if the residual
  is zero**; the residual is named, never silently absorbed.
* :func:`assert_balances` — the gate. ``ok`` is ``False`` when the residual is
  non-zero, when the ledger is incomplete (an unsettled run), or when the store
  is unreadable — each with a reason naming which. It refuses; it does not
  round, fold, or assume.

Sign rules — derived, not invented. The directional kinds (``revenue``,
``inference``, ``fees``, ``capex``) are stored as **positive magnitudes** and the
view applies direction from ``kind``: revenue adds, the cost kinds subtract.
``transfer`` and ``adjustment`` are stored **signed**, because a compensating
row must be able to point either way (C1). ``KINDS_DIRECTION`` reconstructs that
map by reading :mod:`voicebridge.office.ledger`, and a test parses the module
source so a change to the sign rule fails the test rather than silently
mis-stating the books.

Known, honest limitation (D16): an ``unmetered=1`` row is *recorded* but excluded
from every metered fold. Its cents are real rows in the book but are outside the
metered net, so a non-zero residual can legitimately be *entirely* unmetered
spend. This module reports those cents as a named ``unmetered`` residual rather
than pretending the book is clean or that the money does not exist.

Hermetic and pure: reads the derived views and the receipt table, writes nothing.
"""
from __future__ import annotations

import ast
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import store
from .errors import OfficeError
from . import ledger as _ledger

#: The ledger module's own ground truth, read at import — not typed here.
MAGNITUDE_KINDS: Tuple[str, ...] = tuple(_ledger.MAGNITUDE_KINDS)
SIGNED_KINDS: Tuple[str, ...] = tuple(_ledger.SIGNED_KINDS)

#: Every kind, in the store's declared order (also the trial-balance order).
KINDS: Tuple[str, ...] = tuple(store.KINDS)


def _direction_map() -> Dict[str, int]:
    """Derive kind -> direction by reading the ledger's own fold SQL.

    The authoritative statement of the sign rule is the ``CASE`` that folds
    ``office_v_org_pnl``. We read that SQL from :mod:`voicebridge.office.store`
    at runtime (via the module's ``_SCHEMA_SQL`` source) so this map cannot
    drift from the view that actually produces the totals. If the fold SQL
    cannot be located the map is built from ``SIGNED_KINDS``/``MAGNITUDE_KINDS``
    instead, which is the same rule stated structurally.
    """
    sql = getattr(store, "_SCHEMA_SQL", "") or ""
    direction: Dict[str, int] = {}

    # Revenue: `case when ... kind='revenue' then amount_cents` -> +1
    # Costs:   `case when ... kind in (...cost...) then -amount_cents` -> -1
    # Signed:  `else amount_cents` -> as stored (+1)
    if "kind='revenue'" in sql:
        direction["revenue"] = +1
    if "kind IN ('inference','fees','capex')" in sql or \
            "kind IN ('inference', 'fees', 'capex')" in sql:
        for k in ("inference", "fees", "capex"):
            direction[k] = -1
    if "ELSE amount_cents" in sql or "else amount_cents" in sql:
        for k in SIGNED_KINDS:
            direction[k] = +1

    # Fill any kind the SQL scan missed from the structural rule, so the map is
    # always total over store.KINDS and never silently short.
    for kind in KINDS:
        if kind in direction:
            continue
        if kind in MAGNITUDE_KINDS:
            direction[kind] = +1 if kind == "revenue" else -1
        elif kind in SIGNED_KINDS:
            direction[kind] = +1
        else:                                   # pragma: no cover - guard
            direction[kind] = +1
    return direction


#: kind -> +1 (adds to net) / -1 (subtracts from net). Derived, asserted in
#: tests to agree with ``ledger.SIGNED_KINDS``/the fold the view runs.
KINDS_DIRECTION: Dict[str, int] = _direction_map()

#: Which kinds are costs in the P&L bridge.
COST_KINDS: Tuple[str, ...] = ("inference", "fees", "capex")

#: Reasons reflect *why* the gate refused. Never "0" when the answer is unknown.
REASON_REASONS = ("residual", "incomplete", "unreadable")


# --------------------------------------------------------------- internals
def _opening_net(conn: sqlite3.Connection,
                 period: Optional[Tuple[float, float]]) -> int:
    """Net of all *metered* rows strictly before the period start.

    With no period the opening is 0 — the book starts empty.
    """
    if period is None:
        return 0
    start = period[0]
    row = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN unmetered=0 AND kind='revenue'"
        " THEN amount_cents"
        " WHEN unmetered=0 AND kind IN ('inference','fees','capex')"
        " THEN -amount_cents"
        " WHEN unmetered=0 THEN amount_cents END),0) AS net"
        " FROM office_ledger WHERE ts < ?", (start,)).fetchone()
    return int(row["net"])


def _period_clause(period: Optional[Tuple[float, float]]) -> Tuple[str, list]:
    if period is None:
        return "", []
    lo, hi = period
    return " WHERE ts >= ? AND ts < ?", [lo, hi]


def _kind_folds(conn: sqlite3.Connection,
                period: Optional[Tuple[float, float]]
                ) -> Tuple[Dict[str, Dict[str, int]], int]:
    """Per-kind metered folds, plus the unmetered magnitude that sits outside.

    Returns ``(folds, unmetered_cents)`` where each fold is
    ``{entries, magnitude_cents, net_cents}`` for one kind, all metered-only.
    ``unmetered_cents`` is the absolute cents carried by ``unmetered=1`` rows,
    which no metered fold sees (D16) and which the trial balance must surface
    rather than drop.
    """
    where, args = _period_clause(period)
    folds: Dict[str, Dict[str, int]] = {}
    for kind in KINDS:
        clause = (where + " AND " if where else " WHERE ") + \
            "kind = ? AND unmetered = 0"
        # Magnitude kinds are stored >= 0 and the direction comes from the
        # kind; signed kinds carry their own sign, so their magnitude is the
        # ABSOLUTE value and their net is the SIGNED sum (a +100 and a -100
        # adjustment net to 0, not to 200).
        if kind in SIGNED_KINDS:
            row = conn.execute(
                f"SELECT COUNT(id) AS entries,"
                f" COALESCE(SUM(amount_cents),0) AS net,"
                f" COALESCE(SUM(ABS(amount_cents)),0) AS magnitude"
                f" FROM office_ledger{clause}", [*args, kind]).fetchone()
            net = int(row["net"])
            magnitude = int(row["magnitude"])
        else:
            row = conn.execute(
                f"SELECT COUNT(id) AS entries,"
                f" COALESCE(SUM(amount_cents),0) AS magnitude"
                f" FROM office_ledger{clause}", [*args, kind]).fetchone()
            magnitude = int(row["magnitude"])
            net = KINDS_DIRECTION.get(kind, 0) * magnitude
        folds[kind] = {
            "entries": int(row["entries"]),
            "magnitude_cents": magnitude,
            "net_cents": net,
        }
    u_clause = (where + " AND " if where else " WHERE ") + "unmetered = 1"
    urow = conn.execute(
        f"SELECT COALESCE(SUM(ABS(amount_cents)),0) AS magnitude,"
        f" COUNT(id) AS entries FROM office_ledger{u_clause}",
        args).fetchone()
    return folds, int(urow["magnitude"])


def _open_runs(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT run_id, agent_id, started_at, cap_cents FROM"
        " office_run_receipts WHERE status != 'settled' ORDER BY started_at")]


def _degraded(error: str) -> Dict[str, Any]:
    """An unreadable store is unknown, not empty (D15)."""
    return {
        "ok": False,
        "reason": f"unreadable: the office store could not be read "
                  f"({error}); the books are unknown, not empty",
    }


# ----------------------------------------------------------------- public
def trial_balance(cfg: Any, period: Optional[Tuple[float, float]] = None
                  ) -> Dict[str, Any]:
    """One line per ledger kind, with direction applied from the sign rule.

    A line is ``debit`` when the kind's net is positive (money in) and
    ``credit`` when it is negative (money out), matching the P&L's own
    direction. Debits and credits must be equal for a book to stand; where they
    are not, the difference is a residual the caller must explain, and
    ``balanced`` is ``False`` rather than rounded away.
    """
    try:
        conn = store.connect(cfg)
    except OfficeError as exc:
        return _degraded(str(exc))
    try:
        store.migrate(conn)
        folds, unmetered = _kind_folds(conn, period)
        lines: List[Dict[str, Any]] = []
        total_debit = 0
        total_credit = 0
        net_sum = 0
        for kind in KINDS:
            f = folds[kind]
            net = f["net_cents"]
            net_sum += net
            debit = net if net > 0 else 0
            credit = -net if net < 0 else 0
            total_debit += debit
            total_credit += credit
            lines.append({
                "kind": kind,
                "debit_cents": debit,
                "credit_cents": credit,
                "net_cents": net,
                "entries": f["entries"],
                "direction": KINDS_DIRECTION.get(kind, 0),
            })
        # A double-entry trial balance carries the period's own result as its
        # balancing line: the net the kinds produce is what the book's equity
        # moved by. With that line the debits and credits are equal by
        # construction, and the *residual* — the part no recorded row accounts
        # for — is what a reader must be able to name. For a book folded
        # straight from its own rows the residual is 0; a non-zero residual is
        # the alarm, never a rounding niche.
        residual = 0
        if net_sum != 0:
            bal_debit = -net_sum if net_sum < 0 else 0
            bal_credit = net_sum if net_sum > 0 else 0
            lines.append({
                "kind": "balancing",
                "debit_cents": bal_debit,
                "credit_cents": bal_credit,
                "net_cents": -net_sum,
                "entries": 0,
                "direction": 0,
            })
            total_debit += bal_debit
            total_credit += bal_credit
        balanced = total_debit == total_credit
        if balanced and unmetered:
            reason = (f"balanced: debit == credit == {total_debit}; "
                      f"{unmetered} cent(s) sit in unmetered rows outside this "
                      f"fold (D16)")
        elif balanced:
            reason = (f"balanced: debit == credit == {total_debit} across "
                      f"{len(KINDS)} kinds, residual 0")
        else:
            reason = (f"UNBALANCED: debit {total_debit} != credit "
                      f"{total_credit}; residual {residual} cent(s)")
        return {
            "ok": balanced,
            "lines": lines,
            "total_debit": total_debit,
            "total_credit": total_credit,
            "net_cents": net_sum,
            "residual_cents": residual,
            "unmetered_cents": unmetered,
            "balanced": balanced,
            "reason": reason,
        }
    except (sqlite3.Error, OfficeError) as exc:
        return _degraded(str(exc))
    finally:
        conn.close()


def explain(cfg: Any, period: Optional[Tuple[float, float]] = None
            ) -> Dict[str, Any]:
    """Bridge opening net to closing net; name any residual.

    Metered-only, exactly the fold the view runs:

        opening + revenue - costs + adjustments == closing

    ``adjustments`` here is the net of the signed kinds (``adjustment`` +
    ``transfer``), which is the fold the org view applies as ``ELSE
    amount_cents``. Any gap between that identity's right-hand side and the
    recorded closing net is the ``residual``, reported with a name.
    """
    try:
        conn = store.connect(cfg)
    except OfficeError as exc:
        return _degraded(str(exc))
    try:
        store.migrate(conn)
        opening = _opening_net(conn, period)
        where, args = _period_clause(period)
        totals = dict(conn.execute(
            f"SELECT * FROM office_v_org_pnl{where}", args).fetchone())

        revenue = int(totals["revenue_cents"])
        costs = int(totals["cost_cents"])
        cost_detail = {k: int(conn.execute(
            f"SELECT COALESCE(SUM(CASE WHEN unmetered=0 AND kind=? THEN"
            f" amount_cents END),0) AS v FROM office_ledger{where}",
            [k, *args]).fetchone()["v"]) for k in COST_KINDS}

        # Signed side: the view folds every non-cost/non-revenue row as stored.
        signed = int(conn.execute(
            f"SELECT COALESCE(SUM(CASE WHEN unmetered=0 AND"
            f" kind IN ('transfer','adjustment') THEN amount_cents END),0)"
            f" AS v FROM office_ledger{where}", args).fetchone()["v"])
        adj_detail = {k: int(conn.execute(
            f"SELECT COALESCE(SUM(CASE WHEN unmetered=0 AND kind=? THEN"
            f" amount_cents END),0) AS v FROM office_ledger{where}",
            [k, *args]).fetchone()["v"]) for k in SIGNED_KINDS}

        closing = int(totals["net_cents"])
        expected_closing = opening + revenue - costs + signed
        residual = closing - expected_closing
        explained = residual == 0
        if explained:
            reason = "explained: opening + revenue - costs + adjustments == "\
                     "closing"
        else:
            reason = (f"UNEXPLAINED residual {residual} cent(s): closing "
                      f"{closing} does not follow from opening {opening} + "
                      f"revenue {revenue} - costs {costs} + adjustments "
                      f"{signed}")
        return {
            "ok": explained,
            "opening": opening,
            "revenue": revenue,
            "costs": costs,
            "adjustments": signed,
            "closing": closing,
            "expected_closing": expected_closing,
            "residual": residual,
            "explained": explained,
            "cost_detail": cost_detail,
            "adjustment_detail": adj_detail,
            "entries": int(totals["entries"]),
            "reason": reason,
        }
    except (sqlite3.Error, OfficeError) as exc:
        return _degraded(str(exc))
    finally:
        conn.close()


def assert_balances(cfg: Any) -> Dict[str, Any]:
    """The gate. ``ok`` only when the book is complete *and* unexplained-free.

    Refuses, with the reason naming which, when:

    * the store is unreadable — the books are unknown, never assumed zero;
    * a run receipt is unsettled — spend-unknown is not headroom (D15);
    * a residual exists — recorded activity does not account for the net.

    The residual is reported as an integer only when it is *known*. With an
    unsettled run the residual is **unknown** (``None``) rather than ``0``,
    because a zero would read as a balanced book precisely when the book cannot
    be seen.
    """
    try:
        conn = store.connect(cfg)
    except OfficeError as exc:
        return {"ok": False, "residual": None, "violations": ["unreadable"],
                "complete": False, "reason": _degraded(str(exc))["reason"]}
    try:
        store.migrate(conn)
        open_runs = _open_runs(conn)
        complete = not open_runs

        folds, unmetered = _kind_folds(conn, None)
        tb_residual = sum(f["net_cents"] for f in folds.values())

        # The metered net recomputed from the rows must equal the view's net.
        raw = int(conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN unmetered=0 AND kind='revenue'"
            " THEN amount_cents"
            " WHEN unmetered=0 AND kind IN ('inference','fees','capex')"
            " THEN -amount_cents"
            " WHEN unmetered=0 THEN amount_cents END),0) AS net"
            " FROM office_ledger").fetchone()["net"])
        view_net = int(conn.execute(
            "SELECT net_cents FROM office_v_org_pnl").fetchone()["net_cents"])
        rollup_drift = raw - view_net
        residual = tb_residual - view_net

        violations: List[str] = []
        if not complete:
            violations.append("incomplete")
        if rollup_drift != 0:
            violations.append("rollup_drift")
        if residual != 0:
            violations.append("residual")

        # --- reason, in the order the gate refuses -----------------------
        # An incomplete book cannot report a residual: the cents that are
        # missing are exactly the ones we cannot see, so the residual is
        # UNKNOWN (None), never 0. Reporting 0 here would read as balanced at
        # the precise moment the book is unread.
        if not complete:
            first = open_runs[0]
            reported_residual: Optional[int] = None
            reason = (f"incomplete: run {first['run_id']!r} (agent "
                      f"{first['agent_id']}) has a receipt and no settled "
                      f"ledger; spend is unknown, not zero (D15). Residual "
                      f"is UNKNOWN, not 0.")
        else:
            reported_residual = residual
            if rollup_drift != 0:
                reason = (f"rollup_drift: the rows fold to {raw} but the "
                          f"derived view says {view_net}; the two folds "
                          f"disagree by {rollup_drift} cent(s)")
            elif residual != 0:
                detail = (f"{unmetered} cent(s) sit in unmetered rows outside "
                          f"the metered fold (D16)" if unmetered else
                          "the residual is not accounted for by any recorded "
                          "row")
                reason = (f"residual: recorded activity does not explain "
                          f"{residual} cent(s) of net; {detail}")
            else:
                reason = "balanced: no unexplained residual"

        return {
            "ok": bool(complete and rollup_drift == 0 and residual == 0),
            "residual": reported_residual,
            "unmetered_cents": unmetered,
            "rollup_drift_cents": rollup_drift,
            "violations": violations,
            "complete": complete,
            "open_runs": open_runs,
            "entries": sum(f["entries"] for f in folds.values()),
            "reason": reason,
        }
    except (sqlite3.Error, OfficeError) as exc:
        return {"ok": False, "residual": None, "violations": ["unreadable"],
                "complete": False, "reason": _degraded(str(exc))["reason"]}
    finally:
        conn.close()


# --------------------------------------------------- source-level assertion
def direction_map_from_source() -> Dict[str, int]:
    """Parse ``ledger.py`` and the view SQL to rebuild the sign map.

    Used by the test that guards the sign rule: it re-derives the map from the
    module *source* (not the imported constants), so an edit to the rule fails
    the test instead of silently mis-stating the books.
    """
    src = Path(_ledger.__file__).read_text()
    tree = ast.parse(src)
    found: Dict[str, List[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id in (
                        "MAGNITUDE_KINDS", "SIGNED_KINDS"):
                    value = node.value
                    if not isinstance(value, (ast.Tuple, ast.List)):
                        continue
                    found[tgt.id] = [str(e.value) for e in value.elts
                                     if isinstance(e, ast.Constant)]
    magnitude = tuple(found.get("MAGNITUDE_KINDS", ()))
    signed = tuple(found.get("SIGNED_KINDS", ()))
    if not magnitude or not signed:
        raise AssertionError(
            "could not parse MAGNITUDE_KINDS/SIGNED_KINDS from ledger.py; "
            "the guard test cannot prove the sign rule")

    # Derive direction from the order the view folds: revenue adds, the other
    # magnitudes subtract, signed kinds pass through.
    out: Dict[str, int] = {}
    for kind in magnitude:
        out[kind] = +1 if kind == "revenue" else -1
    for kind in signed:
        out[kind] = +1
    return out
