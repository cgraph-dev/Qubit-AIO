"""Businesses: the unit that actually earns (D1), and the gates on going live.

A department holds many businesses. A business is where a real dollar lands, so
this module carries the two gates that stop the org from accumulating things
that look like businesses and are not:

* **`kill_criteria` before `live`.** A business that cannot name the condition
  under which it dies is not a business, it is a hobby with a P&L. Enforced at
  the transition, not requested in a prompt.
* **`exposure` before `live`.** Everything the org can sell carries regulatory
  or legal exposure — tax registration, refund law, platform terms, IP, data
  protection. A business that has not named its exposure cannot go live, for the
  same reason `C22` refuses a trading desk without a drawdown limit: the schema
  is where the containment lives, because a prompt is a suggestion.

And one gate that is the owner's and only the owner's:

* **`funded`.** Test/paper is the default (`C23`). **No agent turn and no code
  path sets `funded=1`** — the parameter does not exist on any agent-facing
  function. Real money is enabled by `owner_fund()`, which demands the owner's
  own words. A suspended account is a permanent loss; a business on a real
  account with an unproven thesis loses it before the org learns anything.

Revenue is ingested with a `(source, external_ref)` pair from the payment
processor, which is the idempotency guard: a replayed webhook is the same row,
not a second sale. The P&L is a `GROUP BY` over the ledger (C2) — there is no
stored revenue figure anywhere for a business to be proud of.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from . import store
from .errors import (BAD_INPUT_CODE, BAD_STATE_CODE, EXISTS_CODE, NOT_FOUND_CODE,
                     OfficeError)

STATUSES = ("proposed", "building", "live", "profitable", "paused", "dead")
#: The order a business moves through. `paused` and `dead` are reachable from
#: anywhere past `proposed`; nothing goes back to `proposed`.
_FORWARD = {"proposed": ("building", "dead"),
            "building": ("live", "paused", "dead"),
            "live": ("profitable", "paused", "dead"),
            "profitable": ("paused", "dead"),
            "paused": ("live", "dead"),
            "dead": ()}

#: Channels the org may sell through. A closed set, so "where does this sell"
#: cannot be answered with a paragraph.
CHANNELS = ("direct", "marketplace", "api", "service", "affiliate")


def _slug(value: str) -> str:
    clean = "".join(c if (c.isalnum() or c in "-_") else "-"
                    for c in str(value or "").strip().lower())
    clean = "-".join(p for p in clean.split("-") if p)
    if not clean:
        raise OfficeError(BAD_INPUT_CODE, "a business needs a slug")
    return clean[:64]


def _business(conn, business_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM office_business WHERE id=?",
                       (int(business_id),)).fetchone()
    if row is None:
        raise OfficeError(NOT_FOUND_CODE, f"no business #{business_id}")
    return dict(row)


def propose(cfg: Any, slug: str, *, department_id: int, thesis: str,
            offer: str, channel: str = "direct", price_cents: Optional[int] = None,
            currency: str = "USD", provenance: Optional[Dict[str, Any]] = None,
            confirmed: bool = False,
            requested_by: str = "cli:office") -> Dict[str, Any]:
    """Open a business at `proposed`. No money moves here, and none can."""
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE,
                          "office business-propose requires --confirm")
    clean = _slug(slug)
    if channel not in CHANNELS:
        raise OfficeError(BAD_INPUT_CODE,
                          f"channel must be one of {', '.join(CHANNELS)}")
    for field, value in (("thesis", thesis), ("offer", offer)):
        if not str(value or "").strip():
            raise OfficeError(
                BAD_INPUT_CODE,
                f"a business must state its {field} before it exists. A "
                f"business with no {field} is not a hypothesis, it is a hope.")
    if price_cents is not None and (not isinstance(price_cents, int)
                                    or price_cents < 0):
        raise OfficeError(BAD_INPUT_CODE, "price_cents must be >= 0")

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        dept = conn.execute("SELECT * FROM office_department WHERE id=?",
                            (int(department_id),)).fetchone()
        if dept is None:
            raise OfficeError(NOT_FOUND_CODE,
                              f"no department #{department_id}")
        try:
            cur = conn.execute(
                "INSERT INTO office_business(slug, name, department_id, thesis,"
                " offer, channel, status, price_cents, currency, provenance,"
                " created_at) VALUES(?,?,?,?,?,?,'proposed',?,?,?,?)",
                (clean, clean, int(department_id), str(thesis).strip(),
                 str(offer).strip(), channel, price_cents, currency,
                 json.dumps(provenance or {}, sort_keys=True, default=str),
                 store.now()))
        except Exception as exc:
            raise OfficeError(EXISTS_CODE,
                              f"business {clean!r} already exists") from exc
        conn.commit()
        bid = int(cur.lastrowid or 0)
    finally:
        conn.close()
    store.audit("business.propose", requested_by,
                {"id": bid, "slug": clean, "department_id": department_id})
    return {"state": "OK", "id": bid, "slug": clean, "status": "proposed",
            "department_id": int(department_id), "funded": False,
            "next": "set kill_criteria and exposure, then advance to building"}


def set_terms(cfg: Any, business_id: int, *, kill_criteria: str = "",
              exposure: str = "", confirmed: bool = False,
              requested_by: str = "cli:office") -> Dict[str, Any]:
    """Record the two things a business owes before it may go live."""
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE,
                          "office business-terms requires --confirm")
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        biz = _business(conn, business_id)
        if biz["status"] == "dead":
            raise OfficeError(BAD_STATE_CODE,
                              f"business {biz['slug']!r} is dead and its terms "
                              f"are part of its post-mortem; they are not "
                              f"edited afterwards")
        conn.execute("UPDATE office_business SET kill_criteria=?, exposure=?"
                     " WHERE id=?",
                     (str(kill_criteria or biz["kill_criteria"]),
                      str(exposure or biz["exposure"]), int(business_id)))
        conn.commit()
    finally:
        conn.close()
    store.audit("business.terms", requested_by, {"id": int(business_id)})
    return {"state": "OK", "id": int(business_id),
            "kill_criteria": bool(str(kill_criteria or biz["kill_criteria"]).strip()),
            "exposure": bool(str(exposure or biz["exposure"]).strip())}


def advance(cfg: Any, business_id: int, status: str, *,
            reason: str = "", confirmed: bool = False,
            requested_by: str = "cli:office") -> Dict[str, Any]:
    """Move a business one step. The gates live here, on the transition."""
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE,
                          "office business-advance requires --confirm")
    status = store.check_enum(status, STATUSES, "business status")
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        biz = _business(conn, business_id)
        current = biz["status"]
        if status == current:
            raise OfficeError(BAD_STATE_CODE,
                              f"business {biz['slug']!r} is already {status}")
        allowed = _FORWARD.get(current, ())
        if status not in allowed:
            raise OfficeError(
                BAD_STATE_CODE,
                f"refused: {biz['slug']!r} is {current} and cannot become "
                f"{status}. Allowed from {current}: "
                f"{', '.join(allowed) or 'nothing — it is dead'}.")

        # The two gates. Both are checked on the way to `live`, every time.
        if status == "live":
            missing = [name for name, value in
                       (("kill_criteria", biz["kill_criteria"]),
                        ("exposure", biz["exposure"]))
                       if not str(value or "").strip()]
            if missing:
                raise OfficeError(
                    "OFFICE_BUSINESS_GATE",
                    f"refused: {biz['slug']!r} cannot go live without "
                    f"{' and '.join(missing)}. "
                    + ("A business with no declared way to die is not a "
                       "business (D33). " if "kill_criteria" in missing else "")
                    + ("A business that has not named what it puts at legal "
                       "risk cannot be launched (M3/C22)."
                       if "exposure" in missing else ""))
        if status == "dead" and not str(reason or "").strip():
            raise OfficeError(BAD_INPUT_CODE,
                              "a kill needs a reason; no kill without one "
                              "(C27)")

        sets, args = ["status=?"], [status]
        if status == "live":
            sets.append("launched_at=?"); args.append(store.now())
        if status == "dead":
            sets.append("killed_at=?"); args.append(store.now())
            sets.append("killed_reason=?"); args.append(str(reason))
        args.append(int(business_id))
        conn.execute(f"UPDATE office_business SET {', '.join(sets)}"
                     f" WHERE id=?", args)
        conn.commit()
    finally:
        conn.close()
    store.audit("business.advance", requested_by,
                {"id": int(business_id), "from": current, "to": status})
    return {"state": "OK", "id": int(business_id), "slug": biz["slug"],
            "from": current, "status": status,
            "funded": bool(biz["funded"])}


def owner_fund(cfg: Any, business_id: int, *, owner_words: str,
               confirmed: bool = False,
               requested_by: str = "cli:owner") -> Dict[str, Any]:
    """The **owner's** act, and the only way `funded` becomes 1 (C23).

    No agent-facing function takes this parameter. That is the whole point: the
    promotion from paper to real money is not a decision the org can make about
    itself.
    """
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE,
                          "office business-fund requires --confirm")
    words = " ".join(str(owner_words or "").split())
    if len(words) < 12:
        raise OfficeError(
            BAD_INPUT_CODE,
            "the owner must state the funding in their own words (at least a "
            "sentence). 'real money' is a switch; the sentence is the record "
            "of who threw it (C23).")
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        biz = _business(conn, business_id)
        if biz["funded"]:
            return {"state": "OK", "id": int(business_id), "funded": True,
                    "already": True}
        conn.execute("UPDATE office_business SET funded=1, funded_by=?"
                     " WHERE id=?", (words[:300], int(business_id)))
        conn.commit()
    finally:
        conn.close()
    store.audit("business.fund", requested_by,
                {"id": int(business_id), "words": words[:120]})
    return {"state": "OK", "id": int(business_id), "slug": biz["slug"],
            "funded": True, "funded_by": words[:300],
            "meaning": "this business may now take real money"}


def record_revenue(cfg: Any, business_id: int, cents: int, *,
                   source: str, external_ref: str, agent_id: Optional[int] = None,
                   currency: str = "USD", note: str = "",
                   occurred_at: Optional[float] = None,
                   recorded_by: str = "office:revenue") -> Dict[str, Any]:
    """Book a real sale, idempotently, against a live and funded business.

    The `(source, external_ref)` pair is the payment processor's own id. Replaying
    a webhook returns the original row rather than booking a second sale —
    without that, a retry storm inflates revenue, and revenue is the one number
    an org is most motivated to inflate.
    """
    if not isinstance(cents, int) or isinstance(cents, bool) or cents <= 0:
        raise OfficeError(BAD_INPUT_CODE,
                          "revenue must be a positive integer number of cents")
    for field, value in (("source", source), ("external_ref", external_ref)):
        if not str(value or "").strip():
            raise OfficeError(
                BAD_INPUT_CODE,
                f"revenue needs a {field}. A payment with no processor "
                f"reference cannot be checked against the processor, so it is "
                f"an assertion rather than a sale.")
    from . import ledger as L

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        biz = _business(conn, business_id)
    finally:
        conn.close()

    if biz["status"] not in ("live", "profitable"):
        raise OfficeError(
            "OFFICE_BUSINESS_GATE",
            f"refused: {biz['slug']!r} is {biz['status']} and cannot book "
            f"revenue. Only a live business sells (D33).")
    if not biz["funded"]:
        # Paper mode is the default (C23). Money arriving is not proof the
        # business was authorised to take it.
        raise OfficeError(
            "OFFICE_BUSINESS_GATE",
            f"refused: {biz['slug']!r} is not owner-funded, so it is on its "
            f"test path. An agent turn cannot fund it; only the owner can "
            f"(C23).")

    row = L.record(cfg, "revenue", int(cents), agent_id=agent_id,
                   department_id=biz["department_id"], business_id=int(business_id),
                   source=str(source).strip(), external_ref=str(external_ref).strip(),
                   note=note, ts=occurred_at, recorded_by=recorded_by)
    return {"state": "OK", "ledger_id": row["id"], "business_id": int(business_id),
            "business_slug": biz["slug"], "cents": int(cents),
            "idempotent": bool(row.get("idempotent")),
            "source": str(source).strip(),
            "external_ref": str(external_ref).strip()}


def pnl(cfg: Any, business_id: int) -> Dict[str, Any]:
    """A business's P&L — a GROUP BY, never a stored number (C2)."""
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        row = conn.execute("SELECT * FROM office_v_business_pnl WHERE"
                           " business_id=?", (int(business_id),)).fetchone()
        if row is None:
            raise OfficeError(NOT_FOUND_CODE, f"no business #{business_id}")
        out = {"state": "OK", "scope": "business", **dict(row)}
        chain = conn.execute(
            "SELECT d.id AS department_id, d.slug AS department_slug,"
            "       a.id AS director_id, a.slug AS director_slug"
            "  FROM office_business b"
            "  JOIN office_department d ON d.id = b.department_id"
            "  LEFT JOIN office_agent a ON a.id = d.director_id"
            " WHERE b.id=?", (int(business_id),)).fetchone()
        out["chain"] = dict(chain) if chain is not None else {}
        biz = conn.execute("SELECT * FROM office_business WHERE id=?",
                           (int(business_id),)).fetchone()
    finally:
        conn.close()
    out["thesis"] = biz["thesis"]
    out["status"] = biz["status"]
    out["funded"] = bool(biz["funded"])
    out["kill_criteria"] = biz["kill_criteria"]
    out["exposure"] = biz["exposure"]
    # "Break-even" is a claim about the ledger, so it is derived from it.
    out["break_even"] = int(out["cost_cents"]) <= int(out["revenue_cents"])
    return out


def businesses(cfg: Any, *, department_id: Optional[int] = None,
               status: Optional[str] = None) -> Dict[str, Any]:
    """Every business with its derived P&L and how far along it is."""
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        sql = ("SELECT b.*, v.revenue_cents, v.cost_cents, v.net_cents,"
               " v.entries, v.revenue_rows, d.slug AS department_slug"
               "  FROM office_business b"
               "  LEFT JOIN office_v_business_pnl v ON v.business_id=b.id"
               "  LEFT JOIN office_department d ON d.id=b.department_id")
        where, args = [], []
        if department_id is not None:
            where.append("b.department_id=?"); args.append(int(department_id))
        if status:
            where.append("b.status=?"); args.append(status)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY b.id"
        rows = [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()
    for row in rows:
        row["missing_gates"] = [
            name for name, value in (("kill_criteria", row["kill_criteria"]),
                                     ("exposure", row["exposure"]))
            if not str(value or "").strip()]
    return {"state": "OK", "count": len(rows), "businesses": rows}
