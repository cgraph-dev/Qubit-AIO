"""Incremental polling for the tower: send only what changed since last poll.

The problem this module exists to solve is shape, not volume. ``state()`` in
``tower.py`` returns the whole renderable office — the HUD, the floorplan, every
character's position and animation, the activity feed, the history series. The
plugin polls it on a 400 ms timer. For a modest org that is ~10 KB per poll and
~25 polls per second across users, and the payload only grows as the org does.
Worse: the response carries no way to tell "nothing happened since you last
asked" from "the state actually changed", so the renderer redraws the identical
picture every time — the cost of the world, for no visible difference.

This module is the additive half of the fix. It computes a **fingerprint** of
the parts of the payload that matter for rendering, and a **delta** between two
states that names only the top-level sections that differ. A poller holds the
previous state and, on an unchanged poll, receives `{"changed": False}` instead
of the world; on a changed poll it receives the sections that moved, plus a list
of seats that were REMOVED — so a dropped seat becomes an explicit instruction
to the client to drop it, rather than a ghost that lingers because the client
never heard it was gone.

Three rules, and they are the whole design:

* **Mirror, never invent.** The section names in :data:`SECTIONS` are exactly the
  top-level keys ``state()`` returns (:data:`~voicebridge.office.tower.SCHEMA`).
  A delta is a same-shape fragment of that payload, so a client that can render
  ``state()`` can render a delta with the same code.
* **Nothing leaves the full-payload contract behind.** This module is read-only
  and separate: ``h_office_tower`` and ``state()`` are untouched, so the
  existing full-payload assertions (``tests/test_office_tower_plugin.py``,
  ``tests/test_office_tower_no_phantom_motion.py``) still hold by construction.
* **Pure.** No timers, no I/O, no clock of its own. Given the same inputs these
  functions return the same output, in the same process or another — the
  fingerprint is a ``hashlib`` digest, not ``hash()``, because a
  ``PYTHONHASHSEED`` that differs between the server process and a client would
  make a salted hash churn on every single poll.

The clock is a fact in the payload, not a render input. ``state()`` includes
``now``, which advances on every poll by definition; if the fingerprint folded
``now`` in, an idle office would fingerprint differently 25 times a second and
this module would be pointless. So the fingerprint is computed over the render
inputs only — positions, animations, HUD figures, floorplan, the newest activity
id — and the delta compares ``now`` separately, under its own section, so a
client that cares about the clock still hears about it.

**The clock/exactness trade-off, stated plainly.** Two sections carry a number
derived from ``now``: each character's ``last_event.age_s`` (``now - ts``), and
the history window's ``day``/``date`` (``history()`` anchors its window to
``now``). The fingerprint ignores both, so a poll where only the clock moved is
not a change. But :func:`apply_delta` must leave a client *exactly* at the
server's state, and the fingerprint only decides *whether to redraw* — so when
``now`` moves, the delta carries those two sections along anyway, as fresh
content. A poll with genuinely nothing new therefore reports ``changed: False``
with an empty ``sections`` list; a poll where only the clock moved reports the
clock (and the two clock-derived sections, for exactness) but no render section.
Note the deliberate exception: a character *mid-walk* moves in real space as the
clock advances (``walk_for`` interpolates its position from ``now``), so its
``position`` genuinely changes and the fingerprint moves — that is a redraw the
client must hear about, and it does.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

#: The top-level payload sections that matter for the incremental poll, in the
#: order a delta should report them. These mirror ``tower.state()``'s keys one
#: for one; a section added to ``state()`` and not here is simply absent from
#: deltas (and never *wrong* — the client falls back to a full seed for it).
SECTIONS = (
    "empty",
    "empty_reason",
    "hud",
    "floorplan",
    "characters",
    "activity",
    "history",
    "history_totals",
    "grid",
    "preview",
    "preview_legend",
)

#: Sections folded into the content fingerprint. ``now`` is deliberately NOT a
#: render input: it changes on every poll, and a fingerprint that changed when
#: nothing happened would defeat the whole point of the module. The clock is
#: reported to the client as a delta under its own section instead.
#: ``motion_rule``/``read_only``/``schema`` are constants — identical in every
#: payload by construction — so they carry no information about *change*.
_FINGERPRINT_SECTIONS = (
    "empty",
    "empty_reason",
    "hud",
    "floorplan",
    "characters",
    "activity",
    "history",
    "history_totals",
    "grid",
    "preview",
    "preview_legend",
)

#: Fields of a character that are NOT render inputs for a change. ``agent_id``
#: is the identity (carried in the keyed map, see ``_canon``); ``slug``,
#: ``seat_type``, ``department_id`` and ``supervisor_id`` describe a seat that
#: already exists and do not change between polls without a section change the
#: fingerprint already catches elsewhere.
_CHARACTER_ANIM_FIELDS = ("animation", "facing", "walking", "at", "stalled",
                         "seated")
_CHARACTER_POS_FIELDS = ("x", "y", "floor", "room", "room_id", "slot")

#: The newest activity id in a payload only ever *grows*; the feed is ordered by
#: ``id DESC``. Signed so its initial state (``None``) can never collide with a
#: real id (>= 1).
_NO_ACTIVITY = -1


def _digest(value: Any) -> str:
    """SHA-256 of a canonically-serialised JSON value, hex-truncated to 32 chars.

    A stable content hash is only stable if it has a stable serialisation, so
    serialisation happens *here* and never by re-encoding the caller's blob:
    ``sort_keys=True`` makes key insertion order irrelevant (requirement 2 — a
    dict built with shuffled keys is the same dict), and only atomic values ever
    reach the encoder, so no float-vs-int or tuple-vs-list surprise survives to
    change the digest. ``default=str`` is a last-resort guard so an unexpected
    value degrades to a stable string rather than raising inside a poll.
    """
    blob = json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _canon(value: Any) -> Any:
    """Canonicalise a value so dict key order cannot affect the fingerprint.

    Two transformations, both required:

    * **Lists of identified dicts become keyed maps.** ``characters`` is a list
      ordered by seat id, and that order is *meaningful* — so it is preserved by
      turning the list into ``{agent_id: canonical(character)}``, a map whose
      keys are the identity rather than the position. Reordering the list
      therefore still changes the digest (unlike a naive sort of the whole
      list), while key insertion order within each character stops mattering.
    * **Everything else recurses.** Plain dicts get their keys walked (with
      ``sort_keys=True`` sorting them at serialisation), lists of scalars are
      preserved in order (a list of positions is ordered by definition).
    """
    if isinstance(value, dict):
        return {str(k): _canon(v) for k, v in value.items()}
    if isinstance(value, list):
        canon_list = [_canon(v) for v in value]
        if canon_list and all(isinstance(v, dict) and "agent_id" in v
                              for v in canon_list):
            return {str(v["agent_id"]): v for v in canon_list}
        return canon_list
    return value


def _latest_activity_id(state: Dict[str, Any]) -> int:
    """The newest ``office_event`` id in the feed, or :data:`_NO_ACTIVITY`.

    "A new activity row" is a change the client must hear about even if nothing
    else moved, so it is fingerprinted explicitly rather than left to fall out
    of the full activity list — which truncates at ``limit`` and would otherwise
    let a burst of new rows push old ones off the end without the *newest* id
    moving.
    """
    rows = state.get("activity")
    if not isinstance(rows, list):
        return _NO_ACTIVITY
    ids = [int(r["id"]) for r in rows
           if isinstance(r, dict) and isinstance(r.get("id"), (int, float))]
    return max(ids) if ids else _NO_ACTIVITY


def _character_key(c: Dict[str, Any]) -> Dict[str, Any]:
    """One character's clock-stable render identity.

    ``last_event`` is folded to its ``kind`` only: its ``age_s`` is
    ``now - ts``, so it changes on every poll whether or not anything happened.
    ``age_s`` is a real render input (the renderer may show "did a tool call 3s
    ago"), but hashing it would make an idle office churn 25 times a second —
    exactly the failure this module exists to remove. The *identity* of the
    last event is what a redraw depends on; the age is a clock derivative the
    client recomputes from the ``now`` section.
    """
    raw_pos = c.get("position")
    pos: Dict[str, Any] = raw_pos if isinstance(raw_pos, dict) else {}
    le = c.get("last_event")
    return {
        "anim": {k: c.get(k) for k in _CHARACTER_ANIM_FIELDS},
        "pos": {k: pos.get(k) for k in _CHARACTER_POS_FIELDS},
        "last_event_kind": (le.get("kind") if isinstance(le, dict) else None),
    }


def _history_key(rows: Any) -> Any:
    """The history series, canonicalised so the sliding day window is stable.

    ``history()`` derives its window from ``now`` (``start = now - days*86400``),
    so advancing the clock by a day renumbers every ``day``/``date`` even when
    the money is identical. The fingerprint keys each row on its **offset from
    the newest day** and its content, so a poll that only slid the window is
    not a change — but a new day with new money is.
    """
    if not isinstance(rows, list):
        return _canon(rows)
    if not rows:
        return []
    days = [r.get("day") for r in rows if isinstance(r, dict)]
    newest = max((d for d in days if isinstance(d, (int, float))),
                 default=0)
    return [{str(k): r.get(k) for k in
             ("revenue_cents", "cost_cents", "net_cents")}
            | {"offset": int(newest) - int(r.get("day", newest))}
            for r in rows if isinstance(r, dict)]


def _section_key(name: str, value: Any) -> Any:
    """A section's change-comparison value, minus clock-derived noise.

    Two sections carry a number that moves with ``now`` and with nothing else:
    ``characters`` (each ``last_event.age_s``) and ``history`` (the sliding day
    window). Those must NOT make ``delta`` re-send the section on every poll —
    that would be the whole-payload cost under a different name. Everything
    else compares byte-for-byte, so a genuine change to any section is never
    missed.
    """
    if name == "characters":
        return _anim_face({"characters": value})
    if name == "history":
        return _history_key(value)
    return _canon(value)


def _anim_face(state: Dict[str, Any]) -> Dict[str, Any]:
    """The render state of every character: its animation and where it is.

    Keyed on ``agent_id`` and clock-stable (see :func:`_character_key`), so a
    same-content reorder of ``characters`` is the same office and an advancing
    clock alone never moves it.
    """
    chars = state.get("characters")
    if not isinstance(chars, list):
        return {}
    return {str(c["agent_id"]): _character_key(c)
            for c in chars if isinstance(c, dict) and "agent_id" in c}


def _fingerprint_source(state: Dict[str, Any]) -> Dict[str, Any]:
    """The exact value the fingerprint is taken over. Kept separate so the
    strategy is inspectable in a test rather than hidden inside a digest."""
    source: Dict[str, Any] = {
        name: _canon(state.get(name)) for name in _FINGERPRINT_SECTIONS
    }
    # clock-stable overrides: these two sections carry ``now``-derived numbers
    # (``age_s``, the sliding history window) that must not churn a poll
    source["characters"] = _anim_face(state)
    source["history"] = _history_key(state.get("history"))
    source["latest_activity_id"] = _latest_activity_id(state)
    # `characters` in the source is the canonical keyed map, so the animation
    # and position halves are already covered; nothing here re-adds list order
    # that the keyed map deliberately dropped.
    return source


def fingerprint(state: Dict[str, Any]) -> str:
    """A stable content hash of the parts of ``state`` that decide the render.

    Covers, and nothing else:

    * every character's ``animation``/``facing``/``walking``/``at``/``stalled``
      and its ``position`` (``x``, ``y``, ``floor``, ``room``, ``slot``);
    * the HUD figures (``hud`` in full — headcount, net_cents, burn, runway);
    * the floorplan shape (``floorplan`` in full — floors, rooms, seats, grid);
    * the newest activity event id, and the activity/history/grid sections;
    * the empty/preview flags.

    Stable across two calls on equal states, and order-independent for dict key
    insertion (see :func:`_canon`). Deliberately blind to ``now``: the clock is
    not a render input, and hashing it would make an idle office churn.
    """
    return _digest(_fingerprint_source(state))


def delta(prev_state: Optional[Dict[str, Any]],
          next_state: Dict[str, Any]) -> Dict[str, Any]:
    """The difference between two tower states, as a same-shape fragment.

    Returns::

        {"changed": bool,
         "fingerprint": <next fingerprint>,
         "sections": [name, ...],          # names of the sections that differ
         "values": {name: value, ...},     # their new contents, same shape
         "removed": {"characters": [agent_id, ...]},  # seats to drop
         "reason": "..."}

    ``sections`` is a **list of names** (the thing a caller logs, diffs or
    routes on) and ``values`` is the matching content map (what a cache
    actually merges). Splitting them is what makes requirement 4 exact: an
    uneventful poll returns ``sections == []`` — an empty list, not a dict that
    happens to be empty and not the world that happens to be unchanged.

    A client merges ``values`` over its cache and drops everything in
    ``removed``; :func:`apply_delta` does exactly that, so the round-trip is
    provable (requirement 3) rather than merely intended. When nothing changed,
    ``changed`` is False and ``sections`` is empty — the poll then costs a flag.

    ``prev_state`` may be ``None`` (no cache yet), in which case every section
    present in ``next_state`` is reported as changed.
    """
    next_fp = fingerprint(next_state)
    prev: Dict[str, Any] = prev_state if isinstance(prev_state, dict) else {}

    values: Dict[str, Any] = {}
    for name in SECTIONS:
        if name not in next_state:
            continue
        if name not in prev or _section_key(name, prev.get(name)) != \
                _section_key(name, next_state.get(name)):
            values[name] = next_state[name]

    # the newest activity id is part of the fingerprint but not a section; make
    # the change legible to the client by reporting the feed its id came from
    if ("activity" not in values
            and _latest_activity_id(prev) != _latest_activity_id(next_state)):
        values["activity"] = next_state.get("activity", [])

    # the clock is not a render input, but a client that displays it must still
    # be told it moved — so it is a delta under its own key, never a fingerprint
    clock_moved = prev.get("now") != next_state.get("now") or "now" not in prev
    if clock_moved:
        values["now"] = next_state.get("now")
        # ``characters`` and ``history`` carry clock-derived content the
        # fingerprint deliberately ignores to stay churn-free: each character's
        # ``last_event.age_s`` and the history window's sliding ``day``. A
        # client that rebuilds from the delta alone must still end up *exactly*
        # at the server's state (requirement 3), so when the clock moved, these
        # two sections ride along — the delta stays a delta for the render
        # inputs, but the clock derivative stays exact.
        for derived in ("characters", "history"):
            if derived in next_state:
                values[derived] = next_state[derived]

    removed = _removed(prev, next_state)
    sections = sorted(values)
    changed = bool(values) or bool(removed)
    return {"changed": changed,
            "fingerprint": next_fp,
            "sections": sections,
            "values": values,
            "removed": removed,
            "reason": _reason(prev, next_state, values, removed, changed)}


def _removed(prev: Dict[str, Any],
             next_state: Dict[str, Any]) -> Dict[str, List[Any]]:
    """Seats present in ``prev`` and absent from ``next_state``.

    A client caching the tower would otherwise keep drawing a seat the server no
    longer has — a terminated agent rendered as a ghost. The delta must say so,
    explicitly, which is why this is its own field and not an inference from
    ``characters``.
    """
    prev_chars = prev.get("characters")
    next_chars = next_state.get("characters")
    if not isinstance(prev_chars, list) or not isinstance(next_chars, list):
        return {}
    next_ids = {c.get("agent_id") for c in next_chars if isinstance(c, dict)}
    gone = [c.get("agent_id") for c in prev_chars
            if isinstance(c, dict) and c.get("agent_id") not in next_ids]
    return {"characters": gone} if gone else {}


def _reason(prev: Dict[str, Any], next_state: Dict[str, Any],
            values: Dict[str, Any], removed: Dict[str, List[Any]],
            changed: bool) -> str:
    """A short, human-readable why — the thing the renderer never had before."""
    if not changed:
        return "no change since the last poll"
    parts = [f"{name} changed" for name in values if name != "now"]
    if removed.get("characters"):
        parts.append(f"{len(removed['characters'])} seat(s) removed")
    if not parts:
        return "only the clock advanced"
    return "; ".join(parts)


def seed_payload(state: Dict[str, Any]) -> Dict[str, Any]:
    """The full payload plus its fingerprint, for a client's first poll.

    The full payload is handed over byte-for-byte as ``state`` — so the
    incremental path cannot break the full-payload contract, it can only add to
    it — with the fingerprint alongside.
    """
    return {"changed": True,
            "fingerprint": fingerprint(state),
            "state": state,
            "reason": "seed: the full payload, for the client's first poll"}


def apply_delta(cached: Dict[str, Any],
                delta_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Rebuild the full state from a client's cache plus a delta payload.

    Takes either shape a poller holds — a seed from :func:`seed_payload` or a
    plain state — and folds a :func:`delta` result over it. The result is
    *identical* to the server's full ``state()`` (deep equality), which is the
    round-trip the incremental path is only correct by: a cache the client
    rebuilds must not drift from the server it is standing in for.

    ``removed`` is applied first, so a seat the delta drops cannot survive a
    later ``values`` entry that would have re-added it — the drop wins.
    """
    base: Dict[str, Any] = dict(cached or {})
    if isinstance(base.get("state"), dict) and "fingerprint" in base:
        # a seed_payload(): the payload IS the state, plus our envelope
        base = dict(base["state"])

    for section, ids in (delta_payload.get("removed") or {}).items():
        rows = base.get(section)
        if isinstance(rows, list):
            drop = set(ids)
            base[section] = [r for r in rows
                             if not (isinstance(r, dict)
                                     and r.get("agent_id") in drop)]

    for name, value in (delta_payload.get("values") or {}).items():
        base[name] = value
    return base
