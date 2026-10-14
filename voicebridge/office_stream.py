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
        agents.append({
            "id": agent_id,
            "kind": "main",
            "roomId": room_id,
            "name": ch.get("slug") or agent_id,
            "look": "f" if (ch.get("agent_id") or 0) % 2 == 0 else "m",
            "role": ch.get("seat_type") or "agent",
            "sessionId": agent_id,
            "account": "qubit",
            # The tower's own activity vocabulary, passed through unmodified so
            # the art maps it exactly as it does upstream.
            "activity": ch.get("animation"),
            "state": ch.get("animation"),
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
    """The tower's activity rows as the client's `FeedItem`s."""
    out: List[Dict[str, Any]] = []
    for row in tower.get("activity") or []:
        out.append({
            "id": str(row.get("id")),
            "ts": int((row.get("ts") or 0) * 1000),
            "kind": row.get("kind"),
            "agentId": (str(row.get("agent_id"))
                        if row.get("agent_id") is not None else None),
            # The tower's text is already human-readable; passing it through is
            # more honest than re-deriving a sentence from the kind.
            "text": row.get("text"),
        })
    return out


def sse(event: str, payload: Any) -> bytes:
    """One SSE frame.

    `json.dumps` never emits a bare newline inside a JSON string, so a single
    `data:` line is safe here and keeps the parser simple.
    """
    return (f"event: {event}\ndata: {json.dumps(payload)}\n\n").encode("utf-8")


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
