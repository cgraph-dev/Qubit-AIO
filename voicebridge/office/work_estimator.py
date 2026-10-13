"""What a turn actually costs — measured, ranged, and honest about its basis.

``office admit --estimate 5`` takes a number the *caller* guessed. Nothing
measures what a turn really cost and feeds it back, so the guess never
improves. This module closes that loop from the one place real money is
recorded: the ledger rows and run receipts that :mod:`receipts` already writes.

It answers three questions, and it refuses to answer one of them:

``observe(cfg)``
    Read the real receipts/ledger and count what happened: how many runs,
    how many of them carried a *measurement*, how many did not, the tokens,
    and the metered dollars.

``estimate(cfg, ...)``
    Turn that measured history into a **range**, stating how strong the basis
    is (``measured``/``weak``/``none``). **With no history it refuses** —
    ``cents: None`` — because a default number that looks authoritative is
    worse than no answer (the ``CHAR_FLOOR_ZOOM`` failure: a value that is
    believed precisely because it has a plausible shape).

``provider_class(cfg)``
    Decide whether the configured provider is flat-rate or metered **from the
    measured receipts**, not from a hardcoded provider name. This is the D16
    distinction, and it costs in both directions: calling a metered provider
    flat-rate leaves real spend unrecorded; calling a flat-rate provider
    metered manufactures phantom spend that later denies real work.

Design rules carried from the rest of ``voicebridge.office``:

* **Money is integer cents.** Never a float in a returned total.
* **A measurement and a guess are never conflated.** A provider's own figure
  (a usage block that reported tokens or micros) is a measurement; a
  ``limit``-attested booking is the run's *cap*, which is not a measurement,
  and it is excluded from the basis and reported as excluded.
* **Unknown spend is reported, not assumed away.** A receipt that never
  settled (D15) has an unknown cost; including it would make the estimate a
  lie, so it is excluded and named.
* **A refusal is typed and says what is missing**, never a number.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import store
from .errors import OfficeError

#: How much measured history a range needs before it may call itself
#: ``measured`` rather than ``weak``.
#:
#: Five, and the number is a judgement stated openly rather than a law: with
#: fewer than five turns the spread of a single expensive or cheap run moves
#: the whole range, so a range built from three turns is a range about three
#: turns, not about this org's turns. At five the widest and narrowest observed
#: runs begin to describe a distribution instead of an accident. It is a floor,
#: not a target — the range is still literally the observed min/max, so more
#: history narrows it by giving a truer min and max, never by smoothing.
MIN_OBSERVATIONS = 5

#: The minimum number of measured runs that is "some" history rather than none.
#: One measured run is a fact about one run; the estimand exists, so the answer
#: is no longer a refusal — but it is ``weak``.
MIN_WEAK_OBSERVATIONS = 1

_NO_HISTORY_REASON = (
    "no measured history: no settled run has reported a cost, so there is "
    "nothing to estimate from. This is a refusal, not an estimate — a number "
    "invented here would look authoritative and be worthless.")

_FLAT_RATE_REASON = (
    "the provider is flat-rate: every settled run reports no usage and books "
    "no dollars (the subscription case, D16). A dollar estimate is not "
    "meaningful on this provider — the turn count is the real bound.")


def _runs(cfg: Any) -> List[Dict[str, Any]]:
    """Every settled run with its ledger rows, joined by ``run_id``.

    One query pair, materialised before iterating, because iterating a cursor
    while running more statements on the same connection is the hazard
    :func:`runner.trace` already documents.
    """
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        receipts = [dict(r) for r in conn.execute(
            "SELECT run_id, agent_id, model, status, settled_cents, cap_cents,"
            " attested_as FROM office_run_receipts ORDER BY started_at")]
        ledger = [dict(r) for r in conn.execute(
            "SELECT run_id, agent_id, kind, amount_cents, unmetered, attested,"
            " attested_as, tokens_in, tokens_out, provider_cost_micros"
            " FROM office_ledger WHERE kind='inference'")]
    finally:
        conn.close()

    by_run: Dict[str, List[Dict[str, Any]]] = {}
    for row in ledger:
        rid = row.get("run_id")
        if rid:
            by_run.setdefault(rid, []).append(row)

    out: List[Dict[str, Any]] = []
    for receipt in receipts:
        rid = receipt["run_id"]
        rows = by_run.get(rid, [])
        out.append({"receipt": receipt, "rows": rows})
    return out


def _classify(run: Dict[str, Any]) -> Dict[str, Any]:
    """One run's measured shape: metered, flat-rate, or unknown.

    * **unknown** — the receipt never settled (open/halted). Its cost is the
      D15 unknown-spend case: it may have been charged for, and guessing is
      exactly what D15 forbids.
    * **flat-rate** — settled with an ``unmetered`` row and no usage: the
      provider reported nothing to measure (D16).
    * **metered** — a row carrying the provider's own figure: real tokens or
      real micro-dollars. A ``limit``-attested row is *not* a measurement (it
      books the run's own cap), so a run whose only row is limit-attested is
      excluded too and says so.
    """
    receipt = run["receipt"]
    rows = run["rows"]
    rid = receipt["run_id"]
    status = receipt.get("status")

    if status != "settled":
        return {"run_id": rid, "kind": "unknown", "cents": None,
                "tokens_in": 0, "tokens_out": 0, "micros": 0, "model": None,
                "why": (f"the receipt is {status!r}, so this turn's cost is "
                        f"unknown, not zero (D15)")}

    # A row may not exist at all on a settled receipt; that is itself unknown.
    if not rows:
        return {"run_id": rid, "kind": "unknown", "cents": None,
                "tokens_in": 0, "tokens_out": 0, "micros": 0,
                "model": receipt.get("model"),
                "why": ("the receipt is settled but no inference row was ever "
                        "booked, so the cost of this turn is unrecorded")}

    # A `limit` booking is the run's cap, not the provider's figure (D15).
    # This is checked BEFORE the generic attested branch: an `entered` figure
    # is a measurement, a `limit` booking is not, and the two must not be
    # conflated.
    limit_only = all(int(r.get("attested") or 0) == 1 and
                     r.get("attested_as") == "limit" for r in rows)
    measured = [r for r in rows
                if int(r.get("tokens_in") or 0) or int(r.get("tokens_out") or 0)
                or int(r.get("provider_cost_micros") or 0)]
    unmetered = all(int(r.get("unmetered") or 0) == 1 for r in rows)

    cents = int(sum(int(r.get("amount_cents") or 0) for r in rows))
    tok_in = int(sum(int(r.get("tokens_in") or 0) for r in rows))
    tok_out = int(sum(int(r.get("tokens_out") or 0) for r in rows))
    micros = int(sum(int(r.get("provider_cost_micros") or 0) for r in rows))

    if unmetered and not measured:
        return {"run_id": rid, "kind": "flat_rate", "cents": 0,
                "tokens_in": 0, "tokens_out": 0, "micros": 0,
                "model": receipt.get("model"),
                "why": ("subscription-backed: recorded, no usage to measure "
                        "(D16)")}

    if measured:
        return {"run_id": rid, "kind": "metered", "cents": cents,
                "tokens_in": tok_in, "tokens_out": tok_out, "micros": micros,
                "model": receipt.get("model"),
                "why": ("the provider reported tokens/micros: a real "
                        "measurement")}

    if limit_only:
        return {"run_id": rid, "kind": "unknown", "cents": None,
                "tokens_in": 0, "tokens_out": 0, "micros": 0,
                "model": receipt.get("model"),
                "why": ("booked at the run's cap (attested_as='limit'), which "
                        "is not a measurement (D15)")}

    # Settled, nothing to measure, but an owner-typed `entered` figure: that
    # IS a measurement of cost, even without a usage block.
    if any(int(r.get("attested") or 0) == 1 for r in rows):
        return {"run_id": rid, "kind": "metered", "cents": cents,
                "tokens_in": tok_in, "tokens_out": tok_out, "micros": micros,
                "model": receipt.get("model"),
                "why": ("owner-attested figure (entered): a measurement of "
                        "cost")}

    return {"run_id": rid, "kind": "unknown", "cents": None,
            "tokens_in": 0, "tokens_out": 0, "micros": 0,
            "model": receipt.get("model"),
            "why": "settled with neither usage nor an attested figure"}


def observe(cfg: Any, *, seat_type: Optional[str] = None,
            model: Optional[str] = None) -> Dict[str, Any]:
    """Read the real receipts and ledger; count what actually happened.

    Returns the raw measurement the estimator stands on. Nothing here is
    inferred from a transcript, and nothing is filled in by default.
    """
    runs = _runs(cfg)

    # Optional narrowing, read from the join, never assumed.
    wanted_agents: Optional[set] = None
    if seat_type is not None:
        wanted_agents = _agents_by_seat_type(cfg, seat_type)

    classified: List[Dict[str, Any]] = []
    for run in runs:
        if wanted_agents is not None:
            aid = run["receipt"].get("agent_id")
            if aid is None or int(aid) not in wanted_agents:
                continue
        shape = _classify(run)
        if model is not None and shape.get("model") != model:
            continue
        classified.append(shape)

    metered = [c for c in classified if c["kind"] == "metered"]
    flat = [c for c in classified if c["kind"] == "flat_rate"]
    unknown = [c for c in classified if c["kind"] == "unknown"]

    provider_mix: Dict[str, int] = {}
    for c in metered:
        key = c.get("model") or "unknown"
        provider_mix[key] = provider_mix.get(key, 0) + 1

    return {
        "state": "OK",
        "runs": len(classified),
        "metered_runs": len(metered),
        "unmetered_runs": len(flat),
        "unknown_runs": len(unknown),
        "tokens_in": int(sum(c["tokens_in"] for c in classified)),
        "tokens_out": int(sum(c["tokens_out"] for c in classified)),
        "cents_total": int(sum(int(c["cents"] or 0) for c in metered)),
        "provider_mix": provider_mix,
        # the honest exclusions, named so a caller can show them
        "excluded": [{"run_id": c["run_id"], "kind": c["kind"], "why": c["why"]}
                     for c in unknown],
        "measured": [{"run_id": c["run_id"], "cents": int(c["cents"] or 0),
                      "tokens_in": c["tokens_in"], "tokens_out": c["tokens_out"],
                      "model": c.get("model")} for c in metered],
    }


def _agents_by_seat_type(cfg: Any, seat_type: str) -> set:
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        return {int(r["id"]) for r in conn.execute(
            "SELECT id FROM office_agent WHERE seat_type=?", (seat_type,))}
    finally:
        conn.close()


def provider_class(cfg: Any) -> Dict[str, Any]:
    """Flat-rate or metered — **from the measured receipts**, not the name.

    The evidence is the shape of settled turns, not a hardcoded provider:

    * every settled run reports no usage and books no dollars
      (``unmetered=1``, zero tokens, zero micros) → **flat-rate**. That is the
      subscription case, and the dollar figure is not a real bound (D16).
    * at least one settled run carries the provider's own usage → **metered**,
      and the evidence (which runs, how many tokens) is named.

    With no settled history the class is ``unknown`` rather than a guess:
    neither label is asserted from nothing, because getting it wrong either
    way costs (unrecorded spend, or phantom spend that denies real work).
    """
    obs = observe(cfg)
    metered = obs["metered_runs"]
    flat = obs["unmetered_runs"]
    settled = metered + flat

    if settled == 0:
        return {
            "state": "OK", "kind": "unknown", "flat_rate": None,
            "reason": ("no settled run exists yet, so the provider's billing "
                       "shape cannot be measured. The class is asserted from "
                       "evidence or not at all — a guess here either hides "
                       "real spend or manufactures phantom spend (D16)."),
            "evidence": {"settled_runs": 0, "metered_runs": 0,
                         "unmetered_runs": 0}}

    if metered == 0:
        return {
            "state": "OK", "kind": "flat_rate", "flat_rate": True,
            "reason": _FLAT_RATE_REASON,
            "evidence": {"settled_runs": settled, "metered_runs": 0,
                         "unmetered_runs": flat,
                         "tokens_reported": obs["tokens_in"] + obs["tokens_out"],
                         "cents_total": obs["cents_total"]}}

    if flat == 0:
        return {
            "state": "OK", "kind": "metered", "flat_rate": False,
            "reason": (f"the provider is metered: {metered} settled run(s) "
                       f"reported real usage "
                       f"({obs['tokens_in']}+{obs['tokens_out']} tokens, "
                       f"{obs['cents_total']} cents booked)."),
            "evidence": {"settled_runs": settled, "metered_runs": metered,
                         "unmetered_runs": 0,
                         "tokens_reported": obs["tokens_in"] + obs["tokens_out"],
                         "cents_total": obs["cents_total"]}}

    # A mix: some turns measured, some subscription-backed. The provider is
    # not flat-rate (it CAN bill), so a cap is meaningful for the metered
    # share — but the dollar estimate must say it covers only that share.
    return {
        "state": "OK", "kind": "mixed", "flat_rate": False,
        "reason": (f"the provider is metered but mixed: {metered} run(s) "
                   f"reported usage and {flat} did not. A dollar estimate "
                   f"covers only the metered share; the rest are bounded by "
                   f"turns (D16)."),
        "evidence": {"settled_runs": settled, "metered_runs": metered,
                     "unmetered_runs": flat,
                     "tokens_reported": obs["tokens_in"] + obs["tokens_out"],
                     "cents_total": obs["cents_total"]}}


def estimate(cfg: Any, *, seat_type: Optional[str] = None,
             model: Optional[str] = None) -> Dict[str, Any]:
    """A **range** for one turn's cost, with the basis stated honestly.

    ``confidence``:
      * ``measured`` — at least :data:`MIN_OBSERVATIONS` metered runs
      * ``weak`` — some metered runs, below that floor
      * ``none`` — nothing measured, and then ``cents`` is ``None``: a refusal
        in a range's clothing rather than a default that looks authoritative.

    ``low``/``high`` are the literal observed min and max of per-turn cost.
    They are not a confidence interval and are not smoothed — the honest
    statement is "the turns we have seen cost between these", nothing more.
    """
    pc = provider_class(cfg)
    obs = observe(cfg, seat_type=seat_type, model=model)
    measured = obs["measured"]
    n = len(measured)

    if obs["runs"] == 0:
        return {"state": "OK", "basis": "no runs recorded", "cents": None,
                "low": None, "high": None, "confidence": "none",
                "reason": _NO_HISTORY_REASON,
                "provider": pc["kind"], "runs_used": 0,
                "excluded": obs["excluded"]}

    if n == 0:
        # History exists but none of it is a measurement: either a flat-rate
        # provider (no dollars to measure) or unmeasurable runs. Both refuse.
        if pc["flat_rate"] is True:
            reason = (
                f"{_FLAT_RATE_REASON} No dollar estimate is produced; "
                f"{obs['runs']} turn(s) are on the record and the turn count "
                f"is what bounds them (M5).")
        else:
            reason = (
                "no measured cost among the recorded turns: "
                f"{obs['runs']} run(s) exist but none reports usage or an "
                "attested figure, so there is no basis for a dollar number. "
                "This is a refusal, not an estimate.")
        return {"state": "OK", "basis": f"0 measured of {obs['runs']} runs",
                "cents": None, "low": None, "high": None, "confidence": "none",
                "reason": reason, "provider": pc["kind"], "runs_used": 0,
                "excluded": obs["excluded"]}

    costs = sorted(int(m["cents"]) for m in measured)
    low, high = costs[0], costs[-1]
    # The point is the observed mean; the range is the observed spread. Never
    # a point alone: a single number from a handful of turns reads as settled.
    cents = round(sum(costs) / len(costs))

    if n >= MIN_OBSERVATIONS:
        confidence = "measured"
        why = (f"measured from {n} metered run(s) "
               f"(>= MIN_OBSERVATIONS={MIN_OBSERVATIONS}); "
               f"range is the observed min/max")
    else:
        confidence = "weak"
        why = (f"weak: only {n} metered run(s), below the floor of "
               f"{MIN_OBSERVATIONS}; the range describes those runs, not this "
               f"org's turns")

    reason = why
    if pc["flat_rate"] is True or pc["kind"] == "mixed":
        reason += (f". Note: the provider is {pc['kind']}, so this dollar "
                   f"figure covers only the metered share; on the flat-rate "
                   f"portion the turn count is the real bound (D16/M5)")
    if obs["excluded"]:
        reason += (f". {len(obs['excluded'])} run(s) excluded as unknown spend "
                   f"(never assumed zero, D15)")

    return {
        "state": "OK",
        "basis": (f"{n} measured run(s) of {obs['runs']} recorded"
                  + (f", seat_type={seat_type}" if seat_type else "")
                  + (f", model={model}" if model else "")),
        "cents": cents, "low": low, "high": high,
        "confidence": confidence, "reason": reason,
        "provider": pc["kind"], "runs_used": n,
        "mean_cents": cents,
        "excluded": obs["excluded"],
    }


__all__ = ["observe", "estimate", "provider_class", "MIN_OBSERVATIONS"]
