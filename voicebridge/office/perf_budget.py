"""The byte budget for the tower poll: measure the payload, refuse it past budget.

The Office page polls ``GET /api/office/tower`` every 400 ms and redraws the
whole scene. The renderer's own contract forbids a client frame loop (C7: motion
comes from the payload, never from a client clock), so **the poll rate is the
performance lever** — nothing smooths the redraw on the client, and the only way
to make the page cheaper is to poll less often or move less data. Nobody had
measured what the poll actually costs, so "it's fine" was unfalsifiable. This
module turns it into a number and a verdict.

The measured facts (``tests/test_office_perf_budget.py`` builds real hermetic
orgs and reads real ``tower.state()`` payloads — those numbers, not these
comments, are the proof):

* At **11 seats** a poll is ~11.7 KB and the largest section is the ``activity``
  feed (60 rows, ~4.5 KB, ~38%). ``characters`` (one ~282-byte entry per seat)
  and ``history`` (a 30-day series, 2.4 KB) are close behind.
* At the **~71-seat design target** a poll is ~32.7 KB; ``characters`` dominates
  (~18.6 KB, ~56%) because it is the only per-seat section. The measured
  marginal cost of one seat is ~349 bytes (a single character entry is ~282
  bytes; the floorplan's per-seat slots add the rest).
* ``history`` is hard-capped at **30 rows** by ``state()`` — it calls
  ``history(cfg)`` with the default ``days=30`` — so a 180-day ledger does NOT
  grow the live poll. A forced ``history(days=180)`` produces 180 rows (~15.8 KB,
  ~55% of the payload at 11 seats) at ~88 bytes per day: that is the
  unbounded-array risk this budget exists to catch, and it is not live today.
* The **activity feed is the section that actually moves** with org activity (up
  to its 60-row cap, ~4.5-6.9 KB), and it is a per-poll cost that does not scale
  with seats.

Three design consequences:

* **The wire cost is the JSON, not the dict.** :func:`payload_bytes` measures
  ``json.dumps(..., separators=(",", ":"))`` — the compact form the HTTP layer
  emits — not ``sys.getsizeof`` of a Python object, which is meaningless for a
  server-side payload.
* **The projection is split fixed/variable and calibrated on measurement.**
  :func:`_project_bytes` separates the section that grows with seats
  (``characters``) from the fixed tax (HUD, history, grid, the activity cap,
  constants), then scales the variable part at the measured per-seat rate. It is
  an extrapolation from real bytes, not a guess.
* **A budget that is never checked is a comment.** :data:`BUDGET_BYTES_PER_POLL`
  is a declared ceiling and :func:`budget_for` returns ``ok=False`` with a
  ``reason`` when a projected org exceeds it, or when the per-minute ceiling is
  exceeded at the given interval. An over-budget projection is reported as over
  budget, never rounded down.

Read-only and pure: this module computes, compares, and returns. It never opens
the store, takes no clock of its own, and changes no payload.
"""
from __future__ import annotations

import json
import math
from typing import Any, Dict, Optional

# --------------------------------------------------------------- the budget
#: Declared ceiling for one poll's serialised payload, in bytes.
#:
#: Derivation, not taste. The poll runs at 400 ms = 150 polls/minute. The
#: measured design-target (71-seat) payload is ~32.7 KB and a real 11-seat org is
#: ~11.7 KB. The ceiling must admit the design target with headroom while
#: refusing a payload that has quietly grown an unbounded array. 64 KiB
#: (65 536 bytes) is chosen: about 2x the measured 71-seat payload, so normal
#: growth has room, but a payload that triples — e.g. a history window widened
#: from its capped 30 days to 180 (~+13 KB) on top of a full 60-row activity
#: feed — is refused. At 400 ms, 64 KiB is 64*150 = 9.4 MB/min, the point where
#: a poll stops being "a JSON response" and starts being "a file transfer per
#: second".
BUDGET_BYTES_PER_POLL = 64 * 1024

#: Declared ceiling for sustained poll traffic, in bytes per minute.
#:
#: Derived from :data:`BUDGET_BYTES_PER_POLL` at the current 400 ms cadence so
#: the two ceilings cannot disagree: 65 536 bytes * (60 000 / 400) polls =
#: 65 536 * 150 = 9 830 400 bytes/min (~9.4 MB/min). This is the number to
#: compare against if the poll interval ever changes: a faster interval can keep
#: the per-poll budget satisfied while blowing this one — the failure mode a
#: single per-poll ceiling would hide.
BUDGET_BYTES_PER_MINUTE = BUDGET_BYTES_PER_POLL * 150

#: The interval the Office page actually polls at, in milliseconds.
POLL_INTERVAL_MS = 400

#: The org size this budget is designed against (the design target: ~71 seats).
DESIGN_TARGET_SEATS = 71

#: The measured marginal cost of one seat, in bytes of JSON, from the hermetic
#: growth curve: the ``characters`` section grows ~349 bytes/seat (a single
#: character entry serialises to ~282 bytes alone; the floorplan's per-seat slots
#: add the rest). Kept as a named constant so a reviewer can see the rate the
#: projections fall back to and check it against the test's measured curve.
MEASURED_BYTES_PER_SEAT = 349.0


def _seat_count(state: Dict[str, Any]) -> int:
    """Live seats in a payload: the ``characters`` list, by definition.

    ``state()`` builds one character per non-terminated ``office_agent`` row, so
    ``len(characters)`` is the seat count the payload itself carries. It is the
    right denominator for a marginal cost because it is the thing that grows
    when a seat is hired — not ``hud.headcount``, which counts the same rows via
    a different query and could disagree.
    """
    chars = state.get("characters")
    return len(chars) if isinstance(chars, list) else 0


def payload_bytes(state: Dict[str, Any]) -> int:
    """Serialised size of a real ``tower.state()`` payload.

    Compact separators and UTF-8, matching the HTTP layer, so the number is the
    bytes that would cross the wire for one poll.
    """
    blob = json.dumps(state, separators=(",", ":"), ensure_ascii=False,
                      default=str)
    return len(blob.encode("utf-8"))


def per_seat_bytes(state: Dict[str, Any]) -> float:
    """Payload bytes divided by seat count — the average cost of a seat.

    The marginal (next-seat) cost is *lower* than this average, because the
    fixed tax (the 60-row activity feed, the history series) is amortised over
    more seats as the org grows — the measured curve falls from ~1 500 B/seat at
    7 seats to ~488 B/seat at 67. A zero-seat payload has no per-seat cost to
    amortise, so this returns ``0.0`` rather than raising or returning ``inf``:
    a payload with no seats has no per-seat average, which is a fact, not an
    error.
    """
    seats = _seat_count(state)
    if seats <= 0:
        return 0.0
    return payload_bytes(state) / seats


def _section_bytes(state: Dict[str, Any]) -> Dict[str, int]:
    """Serialised size of each top-level section, for the dominance proof.

    Every top-level key is counted, because a section that exists on the wire is
    bytes on the wire.
    """
    out: Dict[str, int] = {}
    for name, value in state.items():
        out[name] = len(
            json.dumps(value, separators=(",", ":"), ensure_ascii=False,
                       default=str).encode("utf-8"))
    return out


def dominant_section(state: Dict[str, Any]) -> Dict[str, Any]:
    """The largest top-level section and its share of the payload.

    Returns ``{name, bytes, share, sections}`` where ``share`` is the section's
    fraction of the whole payload (0..1). This is the measurement behind the
    claim "X dominates": it is computed, not asserted.
    """
    total = payload_bytes(state)
    parts = _section_bytes(state)
    if not parts:
        return {"name": None, "bytes": 0, "share": 0.0, "sections": {}}
    name = max(parts, key=lambda k: parts[k])
    return {"name": name, "bytes": parts[name],
            "share": (parts[name] / total) if total else 0.0,
            "sections": parts}


def poll_cost(state: Dict[str, Any], interval_ms: int = POLL_INTERVAL_MS
              ) -> Dict[str, Any]:
    """What polling this payload at ``interval_ms`` costs, per minute and hour.

    ``verdict`` is ``"within_budget"`` or ``"over_budget"`` and is decided
    against BOTH ceilings — a fast interval can blow the per-minute ceiling with
    a payload that satisfies the per-poll one, and saying only "within budget"
    there would be false.
    """
    if interval_ms <= 0:
        raise ValueError("interval_ms must be positive")
    n = payload_bytes(state)
    polls_per_minute = 60_000.0 / float(interval_ms)
    per_minute = n * polls_per_minute
    per_hour = per_minute * 60.0
    seats = _seat_count(state)
    over_poll = n > BUDGET_BYTES_PER_POLL
    over_minute = per_minute > BUDGET_BYTES_PER_MINUTE
    return {
        "bytes_per_poll": n,
        "bytes_per_minute": int(round(per_minute)),
        "bytes_per_hour": int(round(per_hour)),
        "polls_per_minute": polls_per_minute,
        "interval_ms": int(interval_ms),
        "seats": seats,
        "bytes_per_seat": (n / seats) if seats else None,
        "verdict": ("over_budget" if (over_poll or over_minute)
                    else "within_budget"),
        "over_per_poll": over_poll,
        "over_per_minute": over_minute,
        "budget_bytes_per_poll": BUDGET_BYTES_PER_POLL,
        "budget_bytes_per_minute": BUDGET_BYTES_PER_MINUTE,
    }


def _variable_bytes_per_seat(state: Dict[str, Any]) -> float:
    """Measured marginal bytes for one seat in this payload.

    The ``characters``, ``floorplan`` and ``grid`` overlap on seat geometry, but
    ``characters`` is the section that grows one-for-one with seats and is the
    only reliable per-seat slice, so it is the rate used. When the payload has
    no seats there is nothing to measure, so the module falls back to
    :data:`MEASURED_BYTES_PER_SEAT`, the rate from the hermetic growth curve.
    """
    seats = _seat_count(state)
    if seats <= 0:
        return MEASURED_BYTES_PER_SEAT
    return _section_bytes(state).get("characters", 0) / seats


def _project_bytes(state: Dict[str, Any], headcount: int) -> int:
    """Project the payload size to ``headcount`` seats, honestly.

    Splits the measured payload into the part that does NOT grow with seats
    (the fixed tax: HUD, history series, history totals, grid, the activity feed
    up to its cap, and every constant field) and the part that does
    (``characters``, the one per-seat section), then scales the variable part at
    the measured per-seat rate. It is a marginal-cost extrapolation from real
    bytes, not a re-render: for an org the size of the design target it is a
    faithful estimate, and for 0 seats it degenerates to the fixed tax exactly —
    the cost of polling an office with nobody in it.
    """
    n = payload_bytes(state)
    seats = _seat_count(state)
    if seats <= 0:
        # no seats to measure the variable part from: the whole payload is the
        # fixed tax, and asking for seats we cannot meter is answered with the
        # measured payload rather than a fabricated rate
        return n
    if headcount == seats:
        return n
    per_seat = _variable_bytes_per_seat(state)
    variable = _section_bytes(state).get("characters", 0)
    fixed = n - variable
    return int(round(fixed + per_seat * headcount))


def budget_for(headcount: int, interval_ms: int = POLL_INTERVAL_MS
               ) -> Dict[str, Any]:
    """Whether an org of ``headcount`` seats stays within the poll budget.

    Returns ``{ok, budget_bytes, projected_bytes, reason}`` (+ the traffic
    figures behind the verdict). ``ok`` is False and ``reason`` names the
    overrun when the projected payload exceeds :data:`BUDGET_BYTES_PER_POLL`, or
    when the per-minute traffic at ``interval_ms`` exceeds
    :data:`BUDGET_BYTES_PER_MINUTE`. An honest reason, never a rounded-down
    pass.

    ``headcount <= 0`` does not crash: a negative headcount has no seats to
    project from and is refused with a reason that says so; a zero-seat org
    still polls (the history window and HUD exist with nobody in them) and is
    reported as the honest floor with a reason that says which floor it is.
    """
    if interval_ms <= 0:
        raise ValueError("interval_ms must be positive")

    if headcount < 0:
        return {
            "ok": False,
            "headcount": int(headcount),
            "budget_bytes": BUDGET_BYTES_PER_POLL,
            "projected_bytes": 0,
            "projected_bytes_per_minute": 0,
            "interval_ms": int(interval_ms),
            "seats": 0,
            "reason": (f"headcount {headcount} is not a real org: an office "
                       "cannot have negative seats, so there is nothing to "
                       "project. No payload is within budget for a negative "
                       "headcount."),
        }

    ref = _REFERENCE_STATE.get("state")

    if headcount == 0:
        # an unstarted org still polls: the documented empty state is a real
        # payload (history window, HUD, grid, motion rule). Measure it when a
        # zero-seat reference is recorded; otherwise report the honest floor of
        # 0 bytes and say which it is.
        if ref is not None and _seat_count(ref) == 0:
            projected = payload_bytes(ref)
            reason = (f"an office with no seats still polls, and its empty "
                      f"state is measured at {projected} bytes/poll "
                      f"({int(round(projected * 60_000.0 / interval_ms))} "
                      f"bytes/min at {interval_ms} ms). It is within budget "
                      "because it has no seats to draw.")
        else:
            projected = 0
            reason = ("an office with no seats has no per-seat cost and no "
                      "measured empty payload in this process, so the floor is "
                      "0 bytes of variable content: the poll is within budget. "
                      "Record an empty reference to measure the empty state's "
                      "fixed tax (the history window still costs bytes with "
                      "nobody hired).")
        per_minute = int(round(projected * (60_000.0 / float(interval_ms))))
        return {
            "ok": True,
            "headcount": 0,
            "budget_bytes": BUDGET_BYTES_PER_POLL,
            "projected_bytes": projected,
            "projected_bytes_per_minute": per_minute,
            "interval_ms": int(interval_ms),
            "seats": 0,
            "reason": reason,
        }

    if ref is None or _seat_count(ref) == 0:
        # no measured baseline: refuse to invent a projection, and say so
        return {
            "ok": False,
            "headcount": int(headcount),
            "budget_bytes": BUDGET_BYTES_PER_POLL,
            "projected_bytes": 0,
            "projected_bytes_per_minute": 0,
            "interval_ms": int(interval_ms),
            "seats": 0,
            "reason": ("no reference payload has been recorded in this process, "
                       "so there are no measured bytes to project from. Call "
                       "record_reference(state) with a real tower.state() "
                       "payload first; a budget computed from a guess is not a "
                       "budget."),
        }

    projected = int(_project_bytes(ref, headcount))
    per_minute = int(round(projected * (60_000.0 / float(interval_ms))))

    if projected > BUDGET_BYTES_PER_POLL:
        reason = (f"projected {projected} bytes/poll at {headcount} seats "
                  f"exceeds the {BUDGET_BYTES_PER_POLL}-byte budget by "
                  f"{projected - BUDGET_BYTES_PER_POLL} bytes "
                  f"({projected / BUDGET_BYTES_PER_POLL:.2f}x). At "
                  f"{interval_ms} ms that is {per_minute} bytes/min, over the "
                  f"{BUDGET_BYTES_PER_MINUTE}-byte/min budget. Poll slower or "
                  "send less.")
        return {"ok": False, "headcount": int(headcount),
                "budget_bytes": BUDGET_BYTES_PER_POLL,
                "projected_bytes": projected,
                "projected_bytes_per_minute": per_minute,
                "interval_ms": int(interval_ms), "seats": int(headcount),
                "reason": reason}
    if per_minute > BUDGET_BYTES_PER_MINUTE:
        reason = (f"{projected} bytes/poll is within the per-poll budget, but "
                  f"at {interval_ms} ms it is {per_minute} bytes/min, over the "
                  f"{BUDGET_BYTES_PER_MINUTE}-byte/min budget. Poll slower.")
        return {"ok": False, "headcount": int(headcount),
                "budget_bytes": BUDGET_BYTES_PER_POLL,
                "projected_bytes": projected,
                "projected_bytes_per_minute": per_minute,
                "interval_ms": int(interval_ms), "seats": int(headcount),
                "reason": reason}
    reason = (f"projected {projected} bytes/poll at {headcount} seats is "
              f"{BUDGET_BYTES_PER_POLL - projected} bytes under the "
              f"{BUDGET_BYTES_PER_POLL}-byte budget; {per_minute} bytes/min at "
              f"{interval_ms} ms is within the {BUDGET_BYTES_PER_MINUTE}-byte/min "
              "budget.")
    return {"ok": True, "headcount": int(headcount),
            "budget_bytes": BUDGET_BYTES_PER_POLL,
            "projected_bytes": projected,
            "projected_bytes_per_minute": per_minute,
            "interval_ms": int(interval_ms), "seats": int(headcount),
            "reason": reason}


# ------------------------------------------------------- the measured reference
#: The payload the projections are calibrated against. :func:`record_reference`
#: stores a real ``state()`` payload here so :func:`budget_for` extrapolates from
#: measured bytes instead of a guess. Empty until recorded — the module does not
#: open the store on import, and a projection with no measurement refuses with a
#: reason rather than inventing a rate.
_REFERENCE_STATE: Dict[str, Any] = {}


def record_reference(state: Dict[str, Any]) -> None:
    """Remember a real payload as the projection baseline.

    Called once with a hermetic reference org (see the tests) so ``budget_for``
    extrapolates from measurement. An explicit call rather than an import-time
    side effect: a module that reaches into a store on import cannot be imported
    where there is none.
    """
    _REFERENCE_STATE["state"] = state


def min_interval_ms(state: Optional[Dict[str, Any]] = None) -> int:
    """The fastest poll interval, in ms, that this budget allows for ``state``.

    Computed, never guessed: the interval at which the payload's traffic exactly
    equals :data:`BUDGET_BYTES_PER_MINUTE` —
    ``60_000 * bytes / BUDGET_BYTES_PER_MINUTE`` — so the number moves with the
    measured payload rather than with a hardcoded constant. Rounded *up* to the
    next millisecond, because rounding down would pick an interval that is over
    budget by a fraction of a millisecond.

    Uses the recorded reference payload when ``state`` is not given. With
    nothing measured, the current interval is the only honest answer.
    """
    target = state if state is not None else _REFERENCE_STATE.get("state")
    if target is None:
        return POLL_INTERVAL_MS
    n = payload_bytes(target)
    if n <= 0:
        return 1
    # interval such that n * (60_000 / interval) <= BUDGET_BYTES_PER_MINUTE
    return max(1, int(math.ceil(60_000.0 * n / BUDGET_BYTES_PER_MINUTE)))


#: The fastest interval the budget allows for the recorded reference payload,
#: computed at import time from whatever has been measured. With no reference
#: recorded this is :data:`POLL_INTERVAL_MS` (the current cadence). The value
#: that matters for the design target is ``min_interval_ms(projected_71_seat)``,
#: which the tests compute explicitly. Kept as a module constant because a
#: computed MIN_INTERVAL_MS is required, and a constant that disagrees with the
#: function is worse than no constant.
MIN_INTERVAL_MS = min_interval_ms()
