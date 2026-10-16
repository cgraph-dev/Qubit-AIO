"""The office stream: Qubit's tower payload -> the vendored client's snapshot.

THE GAP THIS CLOSES
-------------------
The vendored office client (`frontend/qubit-app/plugins/qubit-office/habblaud/`)
was written against a server that streams SSE from `/api/stream`, sending
`snapshot` and `feed` events. Qubit serves exactly one office route —
`GET /api/office/tower`, a read-only snapshot of the office store.

So this module is a TRANSLATION, not a new source of truth: it renders the tower
payload into the `OfficeSnapshot` shape the client's store expects, and streams
it. Nothing here invents an agent, a room or an event — every field comes from
the tower report, and a field the tower does not carry is omitted rather than
defaulted (the client has its own unknown-handling and a fabricated `0` would
render as a confident zero).

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
The client also wants `/api/agents/:id`, `/api/permissions/:id` and
`/api/messages/:id`, and can POST to them. Those are org MUTATIONS and, per
contract C4, must go through the confirmation authority rather than a direct
POST from a view. They are not bound here, and the client's own binding status
reports them as missing.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Iterable, List, Optional

#: How long one SSE connection is held open before the client is asked to
#: reconnect. The client already reconnects, so this is a ceiling on a single
#: response, not a poll interval.
STREAM_MAX_SECONDS = 300

#: How often a fresh snapshot is sent. The tower report is a fold over the
#: office store, so this is the resolution of "live" and should stay modest.
SNAPSHOT_INTERVAL_SECONDS = 2.0


def _cents(value: Any) -> Optional[int]:
    """Coerce a money field, PRESERVING null.

    null is not zero: the tower reports an unknown fold as null, and rendering
    that as 0 reads as "spent nothing", which is a different and confident claim.
    """
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ms(value: Any, now: Any) -> Optional[int]:
    """A seconds-since-event value as epoch MILLISECONDS, PRESERVING null.

    Two different time encodings reach this function and both are handled,
    because getting it wrong silently shifts a timestamp by a factor of 1000:

      * an AGE in seconds (`last_event.age_s`) -> converted through the
        payload's own `now`, so the client's age arithmetic measures against
        the tower's clock rather than whenever the page happened to load.
      * a `now`-relative value that is already an age -> same conversion.

    An ABSOLUTE epoch-seconds stamp (an activity row's `ts`) must NOT come here
    — use `_epoch_ms` for that, since this would subtract `now` from it and
    produce a nonsense negative.

    null in, null out: the tower omits a timestamp when it has no event row,
    and a fabricated `now` would claim "this just happened" for an agent we
    know nothing about.
    """
    if value is None:
        return None
    try:
        base = float(now) if now is not None else time.time()
        return int((base - float(value)) * 1000)
    except (TypeError, ValueError):
        return None


def _epoch_ms(value: Any) -> int:
    """An ABSOLUTE epoch-seconds stamp as epoch milliseconds.

    The tower's activity `ts` is epoch seconds (e.g. 1791861986.937), NOT an
    age — so it must not go through `_ms`, which would subtract `now` from it.

    A row with no timestamp gets 0 rather than null: the client's `Activity.at`
    is a required `number` and it stamps `<time datetime=...>` from it, so null
    throws inside its own render path. 0 is the honest "epoch, i.e. unknown"
    here — unlike money, where 0 is a *different claim* and must stay null.
    """
    if value is None:
        return 0
    try:
        return int(float(value) * 1000)
    except (TypeError, ValueError):
        return 0


def room_id_for(room: Dict[str, Any], floor_number: int) -> str:
    """A stable room id.

    The client keys rooms by STRING id and uses it for the agent->room join, so
    it must be stable across snapshots or every poll looks like the whole office
    moved. The tower's room id is a per-floor integer, so the floor is folded in.
    """
    return f"f{floor_number}r{room.get('id')}"


def to_snapshot(tower: Dict[str, Any], revision: int,
                start_time: float) -> Dict[str, Any]:
    """Render the tower payload into the client's `OfficeSnapshot`.

    Every field is either taken from the tower or omitted. There is no default
    for a value the tower did not carry.
    """
    floorplan = tower.get("floorplan") or {}
    floors = floorplan.get("floors") or []
    characters = tower.get("characters") or []
    hud = tower.get("hud") or {}
    now = tower.get("now")

    # ---- rooms: one per room the floorplan declares ----------------------
    rooms: List[Dict[str, Any]] = []
    seen_rooms: set[str] = set()
    for floor in floors:
        number = floor.get("number", 0)
        for room in floor.get("rooms") or []:
            rid = room_id_for(room, number)
            if rid in seen_rooms:
                continue
            seen_rooms.add(rid)
            rooms.append({
                "id": rid,
                "name": room.get("slug") or room.get("kind") or rid,
                "path": "",                      # the tower carries no cwd
                "slot": len(rooms),
                # A seed from the room id, so colours and decoration are stable
                # for a given room rather than reshuffling every poll.
                "seed": abs(hash(rid)) % 100000,
                "createdAt": int((now or time.time()) * 1000),
            })

    # ---- agents: one per character, joined to its room -------------------
    agents: List[Dict[str, Any]] = []
    for ch in characters:
        pos = ch.get("position") or {}
        floor_number = pos.get("floor")
        room_id = None
        if floor_number is not None and pos.get("room_id") is not None:
            candidate = f"f{floor_number}r{pos.get('room_id')}"
            if candidate in seen_rooms:
                room_id = candidate
        if room_id is None:
            # An agent the tower cannot place in a declared room is SKIPPED, not
            # parked in an arbitrary room: a character standing in the wrong
            # place is worse than one not drawn, because it looks like a fact.
            continue
        agent_id = str(ch.get("agent_id"))
        # ---- the fields the client's AgentInfo declares as REQUIRED --------
        # The client is not defensive about these: `payTasks` iterates
        # `e.tasks`, and a pane reads `info.status` / `info.at`. Omitting them
        # threw "Cannot read properties of undefined" inside the vendored
        # bundle's own render path, which surfaced as console errors while the
        # frame still said "Connected" — a silent half-failure.
        #
        # Each value is still DERIVED, not invented:
        #   tasks    -- one per open task the tower attributes to this agent;
        #               no task rows means an empty array (honest: "no tasks"),
        #               never a synthesised placeholder task.
        #   status   -- the tower's animation vocabulary, which is what the art
        #               already maps; the client treats an unknown status as
        #               idle, so passing it through is exact rather than lossy.
        #   startedAt / lastEventAt / statusSince -- the tower's own clock, so
        #               the client's age arithmetic measures against the payload
        #               rather than the browser clock.
        #   stats    -- zeroed counters. A counter with no data IS zero (unlike
        #               money, where a missing value must stay null because 0
        #               means something different). `subagents` is the one
        #               honest count available.
        #   seed     -- derived from the agent id so a character's appearance is
        #               stable across polls, exactly as rooms already do.
        # `at` IS A PLACE, NOT A TIME. On a character, `at` is a location name
        # ("desk") and the only real timestamp is `last_event.age_s`. The first
        # version of this passed `ch["at"]` through the seconds->ms converter,
        # where float("desk") threw, the except returned None, and every agent
        # silently got a null clock. Derive from `last_event.age_s` instead.
        last_event = ch.get("last_event") or {}
        at_ms = _ms(last_event.get("age_s"), now)
        open_tasks = [
            t for t in (ch.get("tasks") or [])
            if isinstance(t, dict) and t.get("status") != "completed"
        ]
        tasks = [
            {
                "id": str(t.get("id") or t.get("slug") or f"{agent_id}-{i}"),
                "title": str(t.get("title") or t.get("slug") or t.get("id") or ""),
                "status": str(t.get("status") or "pending"),
                **({"activeForm": t["active_form"]} if t.get("active_form") else {}),
            }
            for i, t in enumerate(open_tasks)
        ]
        subagents = sum(
            1 for other in characters
            if str(other.get("supervisor_id")) == agent_id
            and other.get("supervisor_id") is not None
        )
        agents.append({
            "id": agent_id,
            "kind": "main",
            "roomId": room_id,
            "name": ch.get("slug") or agent_id,
            "look": "f" if (ch.get("agent_id") or 0) % 2 == 0 else "m",
            "role": ch.get("seat_type") or "agent",
            "sessionId": agent_id,
            "account": "qubit",
            # Habblaud AgentInfo's status is the activity vocabulary; it is
            # distinct from `at` (the physical place string).
            "status": ch.get("animation"),
            # The tower's activity vocabulary, passed through unmodified.
            "activity": ch.get("animation"),
            "state": ch.get("animation"),
            # required by the client's type
            "tasks": tasks,
            "seed": abs(hash(agent_id)) % 100000,
            "startedAt": at_ms,
            "lastEventAt": at_ms,
            "statusSince": at_ms,
            # The raw age, in seconds, of this agent's most recent event, or
            # null when the agent has NEVER produced one. Sent alongside the
            # derived `lastEventAt` because the two disagree in a way that
            # matters to the reader:
            #
            #   * `lastEventAt: null` means "no event row at all" -- an agent
            #     that has never run. The client renders that as an absurd age
            #     because it does `now - undefined`; the flag below lets a
            #     future client render "no activity yet" instead.
            #   * `lastEventAgeS: <seconds>` means "ran, then went quiet", which
            #     after ACTIVE_WINDOW_S is ALSO reported as `idle` by the tower.
            #     Without this the two states are indistinguishable on screen:
            #     an agent that ran ten minutes ago looks exactly like one that
            #     has never run, which reads as "the office is dead".
            "lastEventAgeS": (float(last_event["age_s"])
                              if last_event.get("age_s") is not None else None),
            "hasHistory": last_event.get("age_s") is not None,
            "stats": {
                "toolCalls": 0,
                "tokensIn": 0,
                "tokensOut": 0,
                "subagents": subagents,
            },
            "recent": [],
            "stalled": bool(ch.get("stalled")),
            "walking": bool(ch.get("walking")),
            "facing": ch.get("facing"),
            "at": ch.get("at"),
            "supervisorId": (str(ch.get("supervisor_id"))
                             if ch.get("supervisor_id") is not None else None),
            "departmentId": ch.get("department_id"),
        })

    return {
        "rev": revision,
        # The tower's own clock, so the client's age arithmetic is measured
        # against the payload rather than against the browser's clock.
        "serverTime": int((now or time.time()) * 1000),
        "rooms": rooms,
        "agents": agents,
        "accounts": [],
        "meta": {
            # demo=false is the honest answer: this is a real office store.
            "demo": False,
            "sources": [],
            "startedAt": int(start_time * 1000),
            "version": "qubit-office",
            "terminal": False,
            "messages": False,
        },
        # NOT part of the client's type, carried because the office's own HUD
        # reads them and a reader should be able to see the fold it is looking
        # at. The client ignores unknown keys.
        "qubit": {
            "hud": {
                "headcount": hud.get("headcount"),
                "revenue_cents": _cents(hud.get("revenue_cents")),
                "cost_cents": _cents(hud.get("cost_cents")),
                "net_cents": _cents(hud.get("net_cents")),
                "runway_days": hud.get("runway_days"),
                "runway_is_derived": hud.get("runway_is_derived"),
            },
            "read_only": tower.get("read_only"),
            "schema": tower.get("schema"),
        },
    }


def to_feed(tower: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The tower's activity rows as the client's `FeedItem`s.

    `activity` IS AN OBJECT, NOT A STRING. The client's feed row reads
    `item.activity.at` to stamp the row's `<time datetime=...>` and
    `item.activity.kind`/`icon` to pick its glyph. Emitting the bare text (the
    first version of this function did) threw "Cannot read properties of
    undefined (reading 'at')" inside the vendored bundle's own `updateRow` and
    aborted every other row on the feed — so an honest-looking string was a
    worse failure than a missing row.

    Fields the tower does not carry are OMITTED rather than defaulted, on the
    same rule the rest of this module follows: `icon` is absent from the tower,
    so the client falls back to its own per-kind glyph instead of being handed
    an invented emoji.
    """
    characters = tower.get("characters") or []
    rooms = {
        room_id_for(room, floor.get("number", 0)): (room.get("slug") or "")
        for floor in ((tower.get("floorplan") or {}).get("floors") or [])
        for room in (floor.get("rooms") or [])
    }
    by_agent = {str(c.get("agent_id")): c for c in characters}

    out: List[Dict[str, Any]] = []
    # The SSE endpoint re-sends the current snapshot on every poll. Habblaud
    # treats a `feed` event as new rows, not as a replacement, so include only
    # a bounded recent window here; the client itself deduplicates by id.
    for row in (tower.get("activity") or [])[:40]:
        agent_id = (str(row.get("agent_id"))
                    if row.get("agent_id") is not None else None)
        character = by_agent.get(agent_id or "") or {}
        pos = character.get("position") or {}
        room_id = None
        if pos.get("floor") is not None and pos.get("room_id") is not None:
            candidate = f"f{pos.get('floor')}r{pos.get('room_id')}"
            if candidate in rooms:
                room_id = candidate
        at_ms = _epoch_ms(row.get("ts"))
        activity = {
            "id": str(row.get("id")),
            # the tower's kind vocabulary is the client's ActivityKind
            "kind": row.get("kind") or "other",
            # `icon` deliberately absent — see the docstring
            # The tower's text is already human-readable; passing it through is
            # more honest than re-deriving a sentence from the kind.
            "text": row.get("text") or "",
            "at": at_ms,
        }
        out.append({
            "id": str(row.get("id")),
            "agentId": agent_id,
            "roomId": room_id,
            "agentName": character.get("slug") or agent_id or "",
            "roomName": rooms.get(room_id or "", ""),
            "activity": activity,
            # kept for the office's own readers; the client ignores unknown keys
            "ts": at_ms,
            "kind": row.get("kind"),
            "text": row.get("text"),
            # D28's visible class, passed through from the tower's activity row
            # (which now reads it off office_event). The client ignores keys it
            # does not know, so this is additive; it is the surface an event was
            # written for, and until now nothing downstream could see it.
            "visible": row.get("visible"),
        })
    return out


def sse(event: str, payload: Any) -> bytes:
    """One SSE frame.

    `json.dumps` never emits a bare newline inside a JSON string, so a single
    `data:` line is safe here and keeps the parser simple.
    """
    return (f"event: {event}\ndata: {json.dumps(payload)}\n\n").encode("utf-8")


#: The toast levels the vendored client's `push()` understands. It looks up an
#: auto-dismiss duration as `$C[e.level]` and arms a timer only when the result
#: is a finite number; a `level` outside this set (or absent) sets NO timer, so
#: the toast never dismisses. This is the client's own vocabulary, taken from
#: its bundle (`$C={info:5e3,success:6e3,warn:9e3,alert:1/0}`), not invented here.
NOTICE_LEVELS = frozenset({"info", "success", "warn", "alert"})


def notice(text: str, *, level: str = "info", kind: str = "",
           at_ms: Optional[int] = None) -> Dict[str, Any]:
    """A `notice` frame in the shape the vendored client actually consumes.

    THE DEFECT THIS CLOSES
    ----------------------
    The only server-side `notice` was `{"kind": "error", "text": ...}`. The
    client's toast handler reads `e.level` (to pick an auto-dismiss lifetime),
    `e.text` (the body) and `e.at` (the dedup clock):

        push(e){ ... key = `${e.level}|${e.agentId ?? ""}|${e.text}`;
        n = this.toasts.find(n => n.key === key && e.at - n.notice.at < QC);
        ... this.arm(r, $C[e.level]) }

    `arm(e, t)` sets a `setTimeout` only when `Number.isFinite(t)`. The emitted
    frame had no `level`, so `$C[undefined]` was `undefined`, no timer was armed,
    and the error toast -- the one that says the office store could not be read
    -- was **immortal**: it never dismissed itself.

    `at` also matters for the dedup window (`e.at - n.notice.at`): absent, that
    subtraction is `NaN`, so the "already showing, bump it" branch never fires
    and identical notices stack instead of collapsing to a repeat counter.

    Both fields are now always present. `level` is coerced into the client's
    vocabulary rather than passed through raw, so an unknown level cannot
    silently produce an immortal toast again. `kind` is kept for Qubit-side
    readers and the client ignores unknown keys.
    """
    lvl = level if level in NOTICE_LEVELS else "info"
    out: Dict[str, Any] = {
        "kind": kind or lvl,
        "level": lvl,
        "text": text,
        "at": int(time.time() * 1000) if at_ms is None else int(at_ms),
    }
    return out


def stream_frames(tower: Dict[str, Any], revision: int, start_time: float,
                  retry_ms: int = 2000) -> Iterable[bytes]:
    """The opening frames of a stream: the retry hint, then a snapshot + feed.

    One snapshot per connection is what the client's `snapshot` handler expects;
    a fresh connection re-reads the tower, so the caller passes the CURRENT
    report each time rather than caching one.
    """
    yield f"retry: {retry_ms}\n\n".encode("utf-8")
    yield sse("snapshot", to_snapshot(tower, revision, start_time))
    feed = to_feed(tower)
    if feed:
        yield sse("feed", feed)
