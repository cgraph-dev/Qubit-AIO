"""Audit: how the numbers are kept honest without trusting or suspecting anyone.

The premise (`17-AUDIT.md`) is that every agent's incentive points somewhere
slightly different from the org's, and that the fix is not vigilance but
**reconciliation**. Four controls, in the order they cost the least:

**A. Reconciliation (continuous, free).** Every total is recomputed from the
ledger and diffed against the stored view. One rule matters more than the check:
**a diff is never silently repaired.** It becomes a finding, the view is
corrected *from the ledger*, and the disagreement is kept. Silently fixing a
wrong number is how an org loses the ability to notice wrong numbers.

**B. The spot audit (per period, sampled).** One business at random, three
questions. The sample is not announced and the *selection* is not made by anyone
in the chain.

**C. Separation of duties.** Checkable rather than aspirational, because
`office_ledger.recorded_by` exists: the seat that approves a spend is not the
seat that records it, and the auditor reports outside the chain it audits.

**D. The penalty ladder.** A finding never results in a ledger edit — the fix is
always a compensating row (C1). A punishment that rewrites history destroys the
only evidence there was.
"""
from __future__ import annotations

import json
import random
from typing import Any, Dict, List, Optional

from . import store
from .errors import BAD_INPUT_CODE, BAD_STATE_CODE, NOT_FOUND_CODE, OfficeError

KINDS = ("reconcile", "spot_audit", "separation", "penalty")
SEVERITIES = ("minor", "repeat", "terminate")

#: Who the auditor reports to. The owner, dotted; the CEO, administratively.
AUDITOR_REPORTS_TO = "owner"


def _seat(conn, agent_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM office_agent WHERE id=?",
                       (int(agent_id),)).fetchone()
    if row is None:
        raise OfficeError(NOT_FOUND_CODE, f"no seat #{agent_id}")
    return dict(row)


def file_finding(cfg: Any, *, kind: str, severity: str = "minor",
                 period: str = "", business_id: Optional[int] = None,
                 department_id: Optional[int] = None,
                 agent_id: Optional[int] = None,
                 evidence: Optional[Dict[str, Any]] = None,
                 status: str = "open",
                 recorded_by: str = "office:audit") -> Dict[str, Any]:
    """Record a finding. Append-only in practice: nothing edits one."""
    kind = store.check_enum(kind, KINDS, "finding kind")
    severity = store.check_enum(severity, SEVERITIES, "severity")
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        cur = conn.execute(
            "INSERT INTO office_audit_finding(ts, period, kind, severity,"
            " business_id, department_id, agent_id, evidence, status)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (store.now(), str(period or ""), kind, severity, business_id,
             department_id, agent_id,
             json.dumps(evidence or {}, sort_keys=True, default=str), status))
        conn.commit()
        fid = int(cur.lastrowid or 0)
    finally:
        conn.close()
    store.audit("audit.finding", recorded_by,
                {"id": fid, "kind": kind, "severity": severity})
    out = {"state": "OK", "id": fid, "kind": kind, "severity": severity}
    if severity == "terminate":
        # The only finding the owner is notified of immediately (17-AUDIT.md).
        out["notify_owner"] = True
        out["why"] = ("intentional misuse of the ledger, a false report, or a "
                      "spend with no artifact")
    return out


def close_finding(cfg: Any, finding_id: int, *, note: str = "",
                  confirmed: bool = False,
                  requested_by: str = "cli:office") -> Dict[str, Any]:
    """Close a finding by recording what fixed it — **never by erasing it.**

    A finding does not disappear when the underlying practice is corrected: the
    ledger row that caused it is still there, because the ledger is append-only
    (C1). What closes is the finding, and the evidence of how it was answered
    stays attached to it. A control you can make vanish by fixing the thing it
    caught is a control that will be made to vanish.
    """
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE,
                          "office finding-close requires --confirm")
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        row = conn.execute("SELECT * FROM office_audit_finding WHERE id=?",
                           (int(finding_id),)).fetchone()
        if row is None:
            raise OfficeError(NOT_FOUND_CODE, f"no finding #{finding_id}")
        if row["status"] == "closed":
            return {"state": "OK", "id": int(finding_id), "status": "closed",
                    "already": True}
        evidence = {}
        try:
            evidence = json.loads(row["evidence"])
        except Exception:                                  # noqa: BLE001
            pass
        if note:
            evidence["closing_note"] = str(note)[:300]
        conn.execute("UPDATE office_audit_finding SET status='closed',"
                     " closed_at=?, evidence=? WHERE id=?",
                     (store.now(),
                      json.dumps(evidence, sort_keys=True, default=str),
                      int(finding_id)))
        conn.commit()
    finally:
        conn.close()
    store.audit("audit.close", requested_by, {"id": int(finding_id)})
    return {"state": "OK", "id": int(finding_id), "status": "closed",
            "erases_nothing": True,
            "note": ("the ledger row that caused this finding is still there; "
                     "the finding is closed, not the history (C1)")}


def findings(cfg: Any, *, period: Optional[str] = None,
             kind: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        sql = "SELECT * FROM office_audit_finding"
        where, args = [], []
        if period:
            where.append("period=?"); args.append(period)
        if kind:
            where.append("kind=?"); args.append(kind)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        rows = [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()
    for row in rows:
        try:
            row["evidence"] = json.loads(row["evidence"])
        except Exception:                                  # noqa: BLE001
            pass
    return {"state": "OK", "count": len(rows), "findings": rows}


# ------------------------------------------------------------------ control A
def reconcile_all(cfg: Any, *, period: Optional[str] = None,
                  file_findings: bool = True) -> Dict[str, Any]:
    """Recompute every rollup from the ledger and diff it against the view.

    `mismatches: 0` is the healthy answer. A mismatch is **recorded, not
    silently repaired** — the caller gets it back either way, so a test can
    assert on it and a human can read it.
    """
    from . import ledger as L

    checked: List[Dict[str, Any]] = []
    mismatches: List[Dict[str, Any]] = []

    # Client-side recomputation, independent of the view's own SQL. If both used
    # the same expression, the check would only prove the expression equals
    # itself.
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        raw = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN unmetered=0 AND kind='revenue'"
            "  THEN amount_cents ELSE 0 END),0) AS revenue,"
            " COALESCE(SUM(CASE WHEN unmetered=0 AND"
            "   kind IN ('inference','fees','capex') THEN amount_cents"
            "   ELSE 0 END),0) AS cost,"
            " COUNT(CASE WHEN unmetered=0 THEN 1 END) AS entries"
            " FROM office_ledger").fetchone()
        views = {
            "org": conn.execute("SELECT * FROM office_v_org_pnl").fetchone(),
        }
        agent_rows = {r["agent_id"]: dict(r) for r in conn.execute(
            "SELECT * FROM office_v_agent_pnl")}
        dept_rows = {r["department_id"]: dict(r) for r in conn.execute(
            "SELECT * FROM office_v_department_pnl")}
        biz_rows = {r["business_id"]: dict(r) for r in conn.execute(
            "SELECT * FROM office_v_business_pnl")}

        recomputed_agent: Dict[int, Dict[str, int]] = {}
        recomputed_dept: Dict[int, Dict[str, int]] = {}
        recomputed_biz: Dict[int, Dict[str, int]] = {}
        for column, dest in (("agent_id", recomputed_agent),
                             ("department_id", recomputed_dept),
                             ("business_id", recomputed_biz)):
            for r in conn.execute(
                    f"SELECT {column} AS k,"
                    " COALESCE(SUM(CASE WHEN unmetered=0 AND"
                    "   kind='revenue' THEN amount_cents ELSE 0 END),0) AS revenue,"
                    " COALESCE(SUM(CASE WHEN unmetered=0 AND"
                    "   kind IN ('inference','fees','capex') THEN amount_cents"
                    "   ELSE 0 END),0) AS cost"
                    f" FROM office_ledger WHERE {column} IS NOT NULL"
                    f" GROUP BY {column}"):
                dest[int(r["k"])] = {"revenue_cents": int(r["revenue"]),
                                     "cost_cents": int(r["cost"])}
    finally:
        conn.close()

    def _diff(label: str, expected: Dict[str, int], actual: Optional[Dict[str, Any]]):
        actual = actual or {}
        for field in ("revenue_cents", "cost_cents"):
            want, got = int(expected.get(field, 0)), int(actual.get(field, 0))
            entry = {"scope": label, "field": field,
                     "ledger": want, "view": got}
            checked.append(entry)
            if want != got:
                mismatches.append(entry)

    if views["org"] is not None:
        _diff("org", {"revenue_cents": raw["revenue"],
                      "cost_cents": raw["cost"]}, dict(views["org"]))
    for aid, expected in recomputed_agent.items():
        _diff(f"agent:{aid}", expected, agent_rows.get(aid))
    for did, expected in recomputed_dept.items():
        _diff(f"department:{did}", expected, dept_rows.get(did))
    for bid, expected in recomputed_biz.items():
        _diff(f"business:{bid}", expected, biz_rows.get(bid))

    filed = []
    if mismatches and file_findings:
        for m in mismatches:
            scope, _, sid = m["scope"].partition(":")
            out = file_finding(
                cfg, kind="reconcile", severity="repeat",
                period=str(period or ""),
                business_id=int(sid) if scope == "business" else None,
                department_id=int(sid) if scope == "department" else None,
                agent_id=int(sid) if scope == "agent" else None,
                evidence={"field": m["field"], "ledger": m["ledger"],
                          "view": m["view"],
                          "note": "the ledger is the source; the view is "
                                  "derived and was corrected from it, never "
                                  "edited by hand"})
            filed.append(out["id"])

    return {"state": "OK", "healthy": not mismatches,
            "mismatches": len(mismatches), "checked": len(checked),
            "details": mismatches, "findings_filed": filed,
            "repaired": False,
            "rule": ("a diff is never silently repaired: it is recorded as a "
                     "finding and corrected forward from the ledger (C1/C2)")}


# ------------------------------------------------------------------ control B
def spot_audit(cfg: Any, *, period: str, seed: Optional[int] = None,
               recorded_by: str = "office:auditor") -> Dict[str, Any]:
    """Pick one business and ask the three questions (`17-AUDIT.md` § B).

    The sample is drawn here, by seed, and not chosen by anyone in the chain —
    which is the entire mechanism. The agent does not know which period is its
    period, so intermittent audit is enough.
    """
    from . import business as B
    from . import ledger as L

    rows = B.businesses(cfg)["businesses"]
    live = [r for r in rows if r["status"] in ("live", "profitable")]
    if not live:
        return {"state": "OK", "sampled": None, "period": period,
                "reason": "no live business exists to sample"}
    rng = random.Random(seed if seed is not None else f"{period}-audit")
    picked = rng.choice(sorted(live, key=lambda r: int(r["id"])))
    bid = int(picked["id"])

    # Q1: did it do what its reports say? The reports that name it, and the
    # ledger rows that carry it, are the two sides of that question.
    reports = [r for r in
               __import__("voicebridge.office.survival", fromlist=["x"])
               .reports(cfg, limit=200)["reports"]]
    inconsistent = [r["id"] for r in reports if r["ledger_diff"] != "clean"]
    # Q2: did it spend what the ledger says?
    ledger_rows = L.rows(cfg, limit=500, department_id=None)["rows"]
    biz_rows = [r for r in ledger_rows if r.get("business_id") == bid]
    # Q3: does the P&L survive a fee-and-refund pass? Gross -> net, and the
    # refund rate, which is the number agents most often leave out.
    gross = sum(int(r["amount_cents"]) for r in biz_rows
                if r["kind"] == "revenue")
    # A processor sends a refund as a negative adjustment, so it is already
    # signed. Take the magnitude and subtract it, rather than subtracting a
    # negative and inflating the net — which is the exact gross/net confusion
    # this pass exists to catch.
    refunds = abs(sum(int(r["amount_cents"]) for r in biz_rows
                      if r["kind"] == "adjustment"
                      and int(r["amount_cents"]) < 0))
    fees = sum(int(r["amount_cents"]) for r in biz_rows if r["kind"] == "fees")
    net = gross - refunds - fees
    pnl = B.pnl(cfg, bid)
    # Compare against the view's REVENUE-side net, not its whole net. The whole
    # net also carries the cost of thinking, so diffing the two would report a
    # disagreement on every healthy business — a control that always fires is a
    # control nobody reads.
    survives = net == int(pnl.get("revenue_net_cents", net))

    finding = file_finding(
        cfg, kind="spot_audit",
        severity="repeat" if inconsistent else "minor",
        period=period, business_id=bid,
        department_id=int(pnl["chain"].get("department_id") or 0) or None,
        evidence={"q1_reports_conflicting": inconsistent[:20],
                  "q2_ledger_rows": len(biz_rows),
                  "q3_gross_cents": gross, "q3_refund_cents": refunds,
                  "q3_fee_cents": fees, "q3_net_cents": net,
                  "view_revenue_net_cents": int(
                      pnl.get("revenue_net_cents", net)),
                  "refund_rate_pct": (round(100.0 * refunds / gross, 2)
                                      if gross else None),
                  "survives_fee_and_refund_pass": survives},
        status="noted", recorded_by=recorded_by)
    return {"state": "OK", "period": period, "sampled": bid,
            "business_slug": pnl["business_slug"],
            "selection": "drawn by seed, not chosen by anyone in the chain",
            "q1_reports_conflicting": len(inconsistent),
            "q2_ledger_rows": len(biz_rows),
            "q3_survives_fee_and_refund_pass": survives,
            "q3_refund_rate_pct": (round(100.0 * refunds / gross, 2)
                                   if gross else None),
            "finding_id": finding["id"]}


# ------------------------------------------------------------------ control C
def check_separation(cfg: Any, *, file_findings: bool = True) -> Dict[str, Any]:
    """The duties that are checkable because the ledger names who wrote each row.

    A rule that cannot be checked is not a control. These can be, so they are.
    """
    from . import ledger as L

    violations: List[Dict[str, Any]] = []
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        # The approver of a raise is never the requester. Enforced at write
        # time; re-checked here in case a row predates the rule.
        for r in conn.execute(
                "SELECT id, agent_id, supervisor_id, status FROM office_request"
                " WHERE status='approved'"):
            if r["agent_id"] is not None and r["agent_id"] == r["supervisor_id"]:
                violations.append({"kind": "self_approval", "request": r["id"],
                                   "seat": r["agent_id"]})
        # A seat does not rate itself. Enforced at write time; re-checked.
        for r in conn.execute("SELECT id, subject_agent_id AS s,"
                              " reviewer_agent_id AS v FROM office_review"
                              " WHERE subject_agent_id = reviewer_agent_id"):
            violations.append({"kind": "self_review", "review": r["id"],
                               "seat": r["s"]})
        # The auditor is not rated by the chain it audits.
        for r in conn.execute(
                "SELECT a.id, a.slug, a.supervisor_id, s.seat_type AS sup_type"
                "  FROM office_agent a"
                "  LEFT JOIN office_agent s ON s.id=a.supervisor_id"
                " WHERE a.seat_type='auditor' AND a.status!='terminated'"):
            if r["sup_type"] in ("manager", "director_portfolio"):
                violations.append({"kind": "auditor_inside_the_chain",
                                   "seat": r["slug"],
                                   "supervisor_seat_type": r["sup_type"]})
    finally:
        conn.close()

    # No artifact -> no attributed spend. A cost with no run receipt and no
    # artifact is money that bought nothing anyone can point at.
    unattributed = L.rows(cfg, limit=1000, agent_id=None, kind="inference")["rows"]
    orphans = [r["id"] for r in unattributed if not r.get("run_id")]
    if orphans:
        violations.append({"kind": "spend_with_no_run_receipt",
                           "ledger_rows": orphans[:20],
                           "count": len(orphans)})

    # Detection without a record is not a control: a violation that exists only
    # while someone is looking at it will be missed every time it matters. Each
    # violation is filed, and re-filing is avoided by matching the kind+agent.
    filed = []
    if violations and file_findings:
        for v in violations:
            seat = v.get("seat")
            agent_id = int(seat) if isinstance(seat, int) else None
            slug = seat if isinstance(seat, str) else None
            if slug:
                conn = store.connect(cfg)
                try:
                    row = conn.execute("SELECT id FROM office_agent WHERE"
                                       " slug=?", (slug,)).fetchone()
                finally:
                    conn.close()
                agent_id = int(row["id"]) if row is not None else None
            if _already_filed(cfg, "separation", agent_id, v):
                continue
            out = file_finding(cfg, kind="separation", severity="minor",
                               agent_id=agent_id,
                               evidence={"violation": v},
                               recorded_by="office:audit")
            filed.append(out["id"])

    return {"state": "OK", "violations": len(violations), "details": violations,
            "findings_filed": filed,
            "auditor_reports_to": AUDITOR_REPORTS_TO}


def _already_filed(cfg: Any, kind: str, agent_id: Optional[int],
                   violation: Dict[str, Any]) -> bool:
    """Has this exact violation already been filed and is still open?"""
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        sql = ("SELECT evidence FROM office_audit_finding WHERE kind=?"
               " AND status != 'closed'")
        args: List[Any] = [kind]
        if agent_id is not None:
            sql += " AND agent_id=?"
            args.append(int(agent_id))
        marker = violation.get("kind")
        for row in conn.execute(sql, args):
            try:
                ev = json.loads(row["evidence"] or "{}")
            except Exception:                              # noqa: BLE001
                continue
            if (ev.get("violation") or {}).get("kind") == marker:
                return True
        return False
    finally:
        conn.close()


def penalty_for(cfg: Any, *, agent_id: int) -> Dict[str, Any]:
    """The ladder, read off the findings a seat has accumulated (D-control D)."""
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        rows = [dict(r) for r in conn.execute(
            "SELECT severity, kind FROM office_audit_finding WHERE agent_id=?",
            (int(agent_id),))]
    finally:
        conn.close()
    if not rows:
        return {"state": "OK", "agent_id": int(agent_id), "rung": "clean",
                "consequence": "none"}
    if any(r["severity"] == "terminate" for r in rows):
        return {"state": "OK", "agent_id": int(agent_id), "rung": "terminate",
                "consequence": ("the seat is terminated and the finding is "
                                "reported to the owner — the only finding the "
                                "owner is told about immediately"),
                "findings": len(rows)}
    if len(rows) >= 2:
        return {"state": "OK", "agent_id": int(agent_id), "rung": "repeat",
                "consequence": ("the quarter is capped at `below` and the "
                                "department is sampled again next period"),
                "findings": len(rows)}
    return {"state": "OK", "agent_id": int(agent_id), "rung": "minor",
            "consequence": ("recorded; the report is corrected forward, never "
                            "edited (C1)"), "findings": len(rows)}


def auditor_seat(cfg: Any, *, slug: str = "auditor", auditor_of: str = "",
                 confirmed: bool = False,
                 requested_by: str = "cli:office") -> Dict[str, Any]:
    """Hire the auditor, reporting to the **owner** rather than into the chain.

    Dotted line to the owner, administrative line to the CEO. It is created with
    no supervisor in the ordinary sense — which is why `hire` cannot build it:
    C30 requires every seat to have an accountable superior, and the auditor's
    is the owner, who is not an agent (D2).
    """
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE,
                          "office auditor-hire requires --confirm")
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        if conn.execute("SELECT 1 FROM office_agent WHERE seat_type='auditor'"
                        " AND status!='terminated'").fetchone() is not None:
            raise OfficeError(BAD_STATE_CODE,
                              "there is already an auditor (17-AUDIT.md); one "
                              "control that never fires is not two controls")
        cur = conn.execute(
            "INSERT INTO office_agent(slug, name, seat_type, supervisor_id,"
            " status, autonomy, created_at, created_by, auditor_of)"
            " VALUES(?,?, 'auditor', NULL, 'active', 'unattended_full', ?,"
            " 'owner', ?)",
            (str(slug)[:64], str(slug)[:64], store.now(), str(auditor_of)))
        conn.commit()
        aid = int(cur.lastrowid or 0)
    finally:
        conn.close()
    store.audit("auditor.hire", requested_by, {"id": aid})
    return {"state": "OK", "id": aid, "slug": slug, "seat_type": "auditor",
            "reports_to": AUDITOR_REPORTS_TO,
            "supervisor_id": None,
            "note": ("the auditor's superior is the owner, not an agent; that "
                     "is why this is not `hire` (C30/D2)"),
            "auditor_of": auditor_of}
