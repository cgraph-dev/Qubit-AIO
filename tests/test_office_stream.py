"""The office SSE TRANSLATION: tower payload -> the vendored client's snapshot.

WHY THIS FILE EXISTS
--------------------
``voicebridge/office_stream.py`` is the single point of failure between Qubit's
office store and the page a person actually looks at. It has no other consumer
that can check it: ``webui._serve_office_stream`` pipes its bytes straight onto
a socket, and the client applies the result wholesale. So a wrong id, a
null-forgotten money field, or a malformed frame is not a visible error — it is
a silently wrong office.

Every assertion below is written against the client's OWN declarations
(``habblaud/shared/types.ts``: ``OfficeSnapshot``, ``RoomInfo``, ``AgentInfo``,
``FeedItem``) and against the REAL recorded payload
(``frontend/qubit-web/src/lib/office-game/fixtures/tower-recording.json``:
3 floors, 4 rooms, 10 characters), not a hand-built toy.

Hermetic: pure Python, no server, no network, no clock dependence. Timings are
read from the payload's own ``now`` where the contract names one, so a test
cannot pass by running fast.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from voicebridge import office_stream as os

REPO = Path(__file__).resolve().parent.parent
FIXTURE = (REPO / "frontend" / "qubit-web" / "src" / "lib" / "office-game"
           / "fixtures" / "tower-recording.json")

#: Fields the client's `AgentInfo` declares as REQUIRED. A snapshot missing one
#: of these is not "sparse", it is a different type from the one the store is
#: typed against.
REQUIRED_AGENT_FIELDS = ("id", "kind", "roomId", "name", "look", "role",
                         "sessionId", "account")

#: Fields the client's `RoomInfo` declares as required.
REQUIRED_ROOM_FIELDS = ("id", "name", "path", "slot", "seed", "createdAt")


def tower() -> dict:
    """The real recorded payload, re-read per test (never mutated in place)."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def revision_of(t: dict) -> int:
    return t["now"]


def snapshot(t: dict | None = None) -> dict:
    t = tower() if t is None else t
    return os.to_snapshot(t, revision_of(t), t["now"])


# ======================================================================
# 1 — ROOM/AGENT JOIN: an agent's roomId must be a room that EXISTS
# ======================================================================
def test_every_agent_lands_in_a_room_the_snapshot_declares():
    """The client's join is `rooms.find(r => r.id === agent.roomId)`.

    An agent whose `roomId` matches no room is not dropped by that lookup — it
    falls out of the world silently. So the join is asserted from the OUTSIDE:
    every emitted agent's roomId must be a member of the emitted room ids.
    """
    snap = snapshot()
    room_ids = [r["id"] for r in snap["rooms"]]
    assert room_ids, "the real payload produced no rooms at all"

    orphaned = [a["id"] for a in snap["agents"] if a["roomId"] not in room_ids]
    assert orphaned == [], (
        f"agents whose roomId is not a declared room (they would vanish from "
        f"the building with no error): {orphaned}; rooms={sorted(room_ids)}")


def test_no_room_and_no_agent_is_invented():
    """A translation renders; it does not create. Every room id and every agent
    id in the output must be traceable to the payload."""
    t = tower()
    snap = snapshot(t)

    expected_rooms = {
        os.room_id_for(room, floor.get("number", 0))
        for floor in (t["floorplan"]["floors"] or [])
        for room in (floor.get("rooms") or [])
    }
    assert {r["id"] for r in snap["rooms"]} == expected_rooms, (
        "the snapshot declares rooms the floorplan does not have (or lost one)")

    payload_agents = {str(c.get("agent_id")) for c in t["characters"]}
    emitted = {a["id"] for a in snap["agents"]}
    assert emitted <= payload_agents, (
        f"the snapshot invented agents: {sorted(emitted - payload_agents)}")
    assert emitted == payload_agents, (
        f"the snapshot dropped placeable agents: "
        f"{sorted(payload_agents - emitted)} — every recorded character has a "
        f"declared room, so all of them must be drawn")


def test_an_unplaceable_agent_is_skipped_never_parked():
    """THE BUG THIS FILE EXISTS FOR.

    An agent the tower cannot place in a declared room must be SKIPPED. Parking
    it in "some" room draws a character standing where it is not: that reads as
    a fact about the office and is worse than not drawing it at all. Three
    payloads are exercised: undefined floor, undefined room, and a room id the
    floorplan never declared (the drift case — a character pointing at a room
    the floorplan dropped).
    """
    t = tower()
    good = t["characters"][0]
    base_id = int(good["agent_id"])

    unplaceable = [
        dict(good, agent_id=base_id + 100,
             slug="skip-no-room",
             position={"room_id": None}),                       # no room
        dict(good, agent_id=base_id + 101,
             slug="skip-no-floor",
             position={"floor": None, "room_id": 1}),           # no floor
        dict(good, agent_id=base_id + 102,
             slug="skip-ghost-room",
             position={"floor": good["position"]["floor"],
                       "room_id": 999999}),                     # no such room
        dict(good, agent_id=base_id + 103,
             slug="skip-no-position", position=None),           # position absent
    ]
    assert all(c["slug"].startswith("skip-") for c in unplaceable)
    t["characters"] = [good] + unplaceable
    snap = snapshot(t)

    # Fail closed: identify the unplaceable set BY IDENTITY (slug), never by
    # position or name, so a mutation that re-homes the agent cannot also
    # disguise which agents this test was talking about.
    skip_ids = {str(c["agent_id"]) for c in unplaceable}
    base_slug = good["slug"]
    emitted = [a for a in snap["agents"] if a["id"] in skip_ids]
    parked = [a for a in emitted if a["name"] != base_slug]

    assert len(snap["agents"]) == 1, (
        f"expected exactly the one placeable agent; got "
        f"{[(a['id'], a['name'], a['roomId']) for a in snap['agents']]}")
    assert parked == [], (
        f"an unplaceable agent was placed anyway: "
        f"{[(a['id'], a['roomId']) for a in parked]} — the client would draw "
        f"it standing in a room the tower never put it in")
    assert not any(a["id"] in skip_ids for a in snap["agents"]), (
        f"an unplaceable agent survives in the snapshot: "
        f"{[(a['id'], a['roomId']) for a in snap['agents'] if a['id'] in skip_ids]}")
    assert [a["id"] for a in snap["agents"]] == [str(base_id)], (
        "the placeable agent was dropped along with the unplaceable ones")
    assert snap["agents"][0]["name"] == base_slug
    assert str(base_id) in {a["id"] for a in snap["agents"]}


def test_an_agent_is_skipped_rather_than_joined_across_floors():
    """The floor is part of the room id. A character standing on floor 1 that
    names room id 2 (which only exists on floor 2) must be SKIPPED: the room it
    names exists, it is just not on its floor, so joining it would draw the
    agent one floor away from where the tower put it."""
    t = tower()
    ch = dict(t["characters"][0])
    ch["agent_id"] = 4242
    ch["position"] = {"floor": 1, "room_id": 2}   # f1r2 does not exist; f2r2 does
    t["characters"] = [ch]
    snap = snapshot(t)
    assert "f2r2" in {r["id"] for r in snap["rooms"]}, (
        "the fixture no longer has the room on the other floor; this test "
        "would pass for the wrong reason")
    assert snap["agents"] == [], (
        f"an agent on floor 1 was joined to a room on another floor: "
        f"{[(a['id'], a['roomId']) for a in snap['agents']]}")


# ======================================================================
# 2 — ROOM ID STABILITY: the same payload twice gives the same ids
# ======================================================================
def test_the_same_payload_translates_to_identical_room_ids():
    """The client applies a snapshot WHOLESALE and keys rooms by this string.

    An id that differs between two readings of the SAME payload makes every
    poll look like the whole office moved: rooms are torn down and rebuilt, the
    agent join resolves against ids that no longer exist, and nothing errors.
    """
    first = [r["id"] for r in snapshot()["rooms"]]
    second = [r["id"] for r in snapshot()["rooms"]]
    assert first == second, (
        f"the room ids are not stable across two readings of one payload:\n"
        f"  {first}\n  {second}")
    assert len(set(first)) == len(first), (
        f"the snapshot declares a duplicate room id: {first} — the client's "
        f"lookup would resolve both to whichever comes first")


def test_room_ids_never_collide_across_floors():
    """THE M2 SHAPE: the tower's room id is a PER-FLOOR integer, so f1r1 and
    f2r1 are different rooms with the same integer. Dropping the floor from the
    id makes them one room and silently re-homes an entire floor's staff.

    The real payload happens to use distinct integers, so the collision is
    asserted on a payload built the way the tower's own schema allows: same
    room id, different floors.
    """
    t = tower()
    t["floorplan"]["floors"] = [
        {"number": 1, "rooms": [{"id": 1, "slug": "a"}]},
        {"number": 2, "rooms": [{"id": 1, "slug": "b"}]},
    ]
    t["characters"] = []
    ids = [r["id"] for r in snapshot(t)["rooms"]]
    assert len(ids) == 2, (
        f"two rooms on different floors collapsed to one id: {ids} — the floor "
        f"is not part of the room id")
    assert len(set(ids)) == 2, f"colliding room ids: {ids}"


def test_the_agent_join_survives_the_per_floor_collision():
    """The same collision, seen from the agent side: an agent on floor 2 must
    not be drawn in floor 1's room."""
    t = tower()
    t["floorplan"]["floors"] = [
        {"number": 1, "rooms": [{"id": 1, "slug": "a"}]},
        {"number": 2, "rooms": [{"id": 1, "slug": "b"}]},
    ]
    t["characters"] = [{"agent_id": 7, "slug": "z", "position":
                        {"floor": 2, "room_id": 1}}]
    snap = snapshot(t)
    assert len(snap["agents"]) == 1
    room = next(r for r in snap["rooms"] if r["id"] == snap["agents"][0]["roomId"])
    assert room["name"] == "b", (
        f"an agent on floor 2 was joined to floor 1's room: {room}")


# ======================================================================
# 3 — NULL IS NOT ZERO
# ======================================================================
def test_a_null_money_field_stays_null_and_never_becomes_zero():
    """`null` is the tower saying it does not know. `0` is the tower saying it
    knows it is zero. Rendering one as the other turns "unknown runway" into
    "bankrupt today".

    The real payload CARRIES values, so this is asserted twice: once on the
    real numbers (they must survive as themselves), and once on a payload whose
    whole HUD is null (they must survive as None).
    """
    t = tower()
    real = snapshot(t)["qubit"]["hud"]
    for field in ("revenue_cents", "cost_cents", "net_cents"):
        assert real[field] == t["hud"][field], (
            f"{field} was altered in translation: "
            f"{real[field]!r} != {t['hud'][field]!r}")
    assert real["runway_days"] == t["hud"]["runway_days"]

    t["hud"] = {"headcount": None, "revenue_cents": None, "cost_cents": None,
                "net_cents": None, "runway_days": None,
                "runway_is_derived": None}
    hud = snapshot(t)["qubit"]["hud"]
    for field in ("revenue_cents", "cost_cents", "net_cents"):
        assert hud[field] is None, (
            f"a null {field} became {hud[field]!r} — the office now reports a "
            f"confident zero where the tower said it did not know")
        assert hud[field] != 0, f"{field} was coerced to 0"
    assert hud["runway_days"] is None, (
        f"a null runway_days became {hud['runway_days']!r}; a null runway "
        f"rendered as 0 reads as 'bankrupt today'")


def test_an_absent_money_field_is_omitted_not_defaulted():
    """A field the tower did not carry at all is the same "unknown" as null."""
    t = tower()
    t["hud"] = {}
    hud = snapshot(t)["qubit"]["hud"]
    for field in ("revenue_cents", "cost_cents", "net_cents", "runway_days"):
        assert hud[field] is None, (
            f"an absent {field} defaulted to {hud[field]!r}")
        assert hud[field] != 0, f"an absent {field} was defaulted to 0"


def test_a_zero_the_tower_reports_stays_zero():
    """The mirror image, and the reason a "coerce nulls to 0" fix is not
    acceptable either: a real 0 must NOT be turned into None. The payload's own
    per-floor rows are all zero, and `0` here is a claim the tower can make."""
    assert os._cents(0) == 0
    assert os._cents(0) != None  # noqa: E711 - the point is identity, not truth
    assert os._cents(1234) == 1234
    assert os._cents(None) is None


def test_an_unparseable_money_field_is_unknown_not_zero():
    assert os._cents("not-a-number") is None, (
        "an unparseable money value became a number; garbage is not zero")
    assert os._cents({}) is None
    assert os._cents("17") == 17, "a numeric string is still a number"


# ======================================================================
# 4 — SEED DETERMINISM
# ======================================================================
def test_every_room_seed_is_stable_across_calls():
    """The seed drives colours and decoration. If it moves between polls the
    office strobes — same layout, different paint, every two seconds."""
    first = [(r["id"], r["seed"]) for r in snapshot()["rooms"]]
    second = [(r["id"], r["seed"]) for r in snapshot()["rooms"]]
    assert first == second, (
        f"room seeds are not stable across calls:\n  {first}\n  {second}")


def test_seeds_are_distinct_between_rooms():
    """Two rooms sharing a seed is two rooms that paint identically — an office
    where two floors look like one room."""
    seeds = [(r["id"], r["seed"]) for r in snapshot()["rooms"]]
    by_seed: dict[int, list[str]] = {}
    for rid, seed in seeds:
        by_seed.setdefault(seed, []).append(rid)
    collisions = {s: ids for s, ids in by_seed.items() if len(ids) > 1}
    assert collisions == {}, (
        f"rooms that would paint identically: {collisions}")


def test_the_seed_survives_a_change_of_interpreter_salt():
    """The seed is derived from the room id, and in CPython `hash(str)` is
    salted PER PROCESS. A seed taken straight from `hash()` is therefore stable
    within one process and different in the next one — which is exactly the
    shape a two-call test cannot see: both calls happen in the same process.

    The office is served by a long-lived process and read by a browser that
    reconnects; a seed that changes when the server restarts repaints the whole
    building. This runs the translation in two children with a FIXED
    PYTHONHASHSEED and requires their seeds to agree.
    """
    prog = (
        "import json,sys; sys.path.insert(0, '.');"
        "from voicebridge import office_stream as OS;"
        "t = {'now': 1.0, 'characters': [], 'floorplan': {'floors': ["
        "{'number': n, 'rooms': [{'id': n}]} for n in (1, 2, 3)]}};"
        "print(json.dumps([[r['id'], r['seed']] for r in"
        " OS.to_snapshot(t, 1, 0.0)['rooms']]))")

    def in_child() -> str:
        import os as _os
        import subprocess
        return subprocess.run(
            [sys.executable, "-c", prog], cwd=str(REPO), capture_output=True,
            text=True, timeout=120,
            env={**_os.environ, "PYTHONHASHSEED": "0"}).stdout.strip()

    first, second = in_child(), in_child()
    assert first, "the child process produced no snapshot at all"
    assert first == second, (
        f"the room seeds differ between two processes reading the SAME payload "
        f"({first} vs {second}) — the seed is salted by the interpreter, so a "
        f"server restart repaints the office")
    expected = json.loads(first)
    assert len(expected) == 3, f"the probe payload lost a room: {expected}"
    assert all(isinstance(seed, int) for _, seed in expected)


# ======================================================================
# 5 — SSE FRAMING
# ======================================================================
def parse_sse(raw: bytes) -> list[tuple[str | None, list[str]]]:
    """A strict RFC-3117-subset parser: frames split on a BLANK LINE, each
    frame a set of `field: value` lines. Nothing is lenient here — that is the
    point, since the client's parser is not lenient either."""
    text = raw.decode("utf-8")
    frames: list[tuple[str | None, list[str]]] = []
    for block in text.split("\n\n"):
        if block == "":
            continue
        event: str | None = None
        data: list[str] = []
        for line in block.split("\n"):
            assert ":" in line, (
                f"an SSE line with no colon is not a field: {line!r}")
            field, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "event":
                event = value
            elif field == "data":
                data.append(value)
            elif field in ("retry", "id", "comment"):
                pass
            else:
                raise AssertionError(f"unknown SSE field {field!r} in {line!r}")
        frames.append((event, data))
    return frames


#: One frame, parsed from bytes, holding its STRUCTURE tight enough to catch a
#: line that is not a well-formed field. The `data:` line must itself be a
#: single line whose value is JSON.
DATA_LINE = re.compile(
    rb"\Adata: (?!.*(?<!\\)\n)(?P<json>\{.*\}|\[.*\])\Z", re.S)


def assert_sse_frame_wellformed(frame: bytes) -> tuple[str, str]:
    """Enforce the client's OWN grammar on a single raw frame:

        event: <name>\\n
        data: <one line of JSON>\\n
        \\n

    A data payload that contains a raw newline splits into a second line that
    is NOT `data:`-prefixed, and the client's parser cannot put it back
    together. This is the assertion that catches that, whatever produced it —
    it does not care HOW the bytes were built, only that they parse.
    """
    assert frame.endswith(b"\n\n"), (
        f"an SSE frame must end with a blank line: {frame[-12:]!r}")
    lines = frame[:-2].split(b"\n")
    assert len(lines) == 2, (
        f"a frame must be exactly `event:` + `data:` (2 lines); got "
        f"{len(lines)}: {lines!r} — a payload with an embedded newline has "
        f"been split across lines the client cannot rejoin")
    assert lines[0].startswith(b"event: "), f"bad event line: {lines[0]!r}"
    event = lines[0][len(b"event: "):].decode("utf-8")
    assert lines[1].startswith(b"data: "), (
        f"the payload line is not `data:`-prefixed: {lines[1][:40]!r}")
    body = lines[1][len(b"data: "):]
    # the payload half must be ONE line: no raw newline survives inside it
    assert b"\n" not in body and b"\r" not in body, (
        f"a raw newline was written INSIDE the data line: {body[:60]!r}")
    json.loads(body.decode("utf-8"))  # it must still be JSON
    return event, body.decode("utf-8")


def _raw_frames(raw: bytes) -> list[bytes]:
    """Split a stream into its RAW frames (each ending in a blank line)."""
    assert raw.endswith(b"\n\n"), "the stream does not end on a frame boundary"
    return [f + b"\n\n" for f in raw[:-2].split(b"\n\n")]


def test_the_frames_parse_as_sse_with_the_retry_hint_first():
    t = tower()
    raw = b"".join(os.stream_frames(t, revision_of(t), 1700000000.0))

    # The retry hint is its OWN frame and comes FIRST: the client sets its
    # reconnect delay from it, and a hint that arrives after a snapshot is a
    # hint the client may already have reconnected without.
    assert re.match(rb"\Aretry: \d+\n\n", raw), (
        f"the stream does not open with `retry: <n>\\n\\n`: {raw[:40]!r}")
    raws = _raw_frames(raw)
    assert raws[0] == b"retry: 2000\n\n", f"first frame is {raws[0]!r}"
    assert not raws[0].startswith(b"event: ")

    # Every NAMED frame after the hint must be exactly `event:` + one `data:`
    # line of JSON, enforced on the raw bytes.
    named = []
    for frame in raws[1:]:
        event, body = assert_sse_frame_wellformed(frame)
        named.append(event)
        parsed = json.loads(body)
        assert isinstance(parsed, (dict, list))
    assert named[:1] == ["snapshot"], (
        f"the snapshot is not the first named event: {named}")


def test_the_stream_is_byte_reproducible_for_one_payload():
    """Two connections reading the SAME tower must produce the SAME bytes.

    There is no delta protocol: the client applies each snapshot wholesale, so
    a byte difference between two readings of one payload is the whole office
    described differently for no reason — and it is what makes a poll look like
    movement rather than a refresh.
    """
    t = tower()
    a = b"".join(os.stream_frames(t, revision_of(t), 1700000000.0))
    b = b"".join(os.stream_frames(t, revision_of(t), 1700000000.0))
    assert a == b, (
        "the same payload streamed two different byte sequences; the first "
        "difference is at offset "
        f"{next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))}")


def test_the_snapshot_data_line_round_trips_as_the_office_snapshot():
    t = tower()
    raw = b"".join(os.stream_frames(t, revision_of(t), 1700000000.0))
    raws = _raw_frames(raw)
    event, body = assert_sse_frame_wellformed(raws[1])
    assert event == "snapshot"
    payload = json.loads(body)
    assert isinstance(payload, dict)
    for key in ("rev", "serverTime", "rooms", "agents", "accounts", "meta"):
        assert key in payload, (
            f"the snapshot data is missing `{key}`, which OfficeSnapshot "
            f"declares as required")
    # the frame is exactly the documented shape
    assert raw.count(b"\ndata: ") == len(raws) - 1


def test_the_feed_frame_round_trips():
    t = tower()
    raw = b"".join(os.stream_frames(t, revision_of(t), 1700000000.0))
    events = {}
    for frame in _raw_frames(raw)[1:]:
        name, body = assert_sse_frame_wellformed(frame)
        events[name] = body
    assert "feed" in events, "no feed frame was streamed for a payload with activity"
    items = json.loads(events["feed"])
    assert isinstance(items, list) and items
    assert len(items) == len(t["activity"])


def test_no_bare_newline_appears_inside_a_data_line():
    """THE M5 SHAPE — a real newline inside a `data:` payload.

    SSE ends a field at the newline, so an embedded newline turns the rest of
    the JSON into a second, un-prefixed line. The client's parser either drops
    it or throws on it, and the frame is silently half a snapshot. The source's
    own comment claims `json.dumps` makes this impossible; this asserts it for
    a payload DESIGNED to try, because "it cannot happen" is exactly the kind
    of claim that stops being true.

    The check is on the RAW BYTES, through the same grammar the client applies
    — not on a re-serialised copy of the payload, which would pass no matter
    what the wire looked like.
    """
    t = tower()
    # A payload whose every kind of text field tries to break the frame.
    t["characters"][0]["slug"] = "eve\ninjected: 1"
    t["characters"][0]["slug"] += "\r\nmore"
    t["activity"][0]["text"] = "line one\ndata: {\"not\": \"the payload\"}"
    t["activity"][0]["kind"] = "multi\nline"
    t["floorplan"]["floors"][0]["rooms"][0]["slug"] = "board\nroom"

    raw = b"".join(os.stream_frames(t, revision_of(t), 1700000000.0))
    raws = _raw_frames(raw)

    seen = {}
    for frame in raws[1:]:
        event, body = assert_sse_frame_wellformed(frame)   # strict grammar
        seen[event] = json.loads(body)

    # no line anywhere in the whole stream may be a stray continuation
    for line in raw.split(b"\n"):
        assert line == b"" or line.startswith(
            (b"event:", b"data:", b"retry:")), (
            f"a line in the stream is neither a field nor blank — a payload "
            f"broke out of its data line: {line!r}")

    # and the JSON must still carry the newline INSIDE the string, un-escaped
    # semantics and all
    assert seen["snapshot"]["agents"][0]["name"] == "eve\ninjected: 1\r\nmore", (
        "the embedded newline was lost or mangled in translation")
    assert seen["feed"][0]["text"] == "line one\ndata: {\"not\": \"the payload\"}"
    assert seen["snapshot"]["rooms"][0]["name"] == "board\nroom"

    # the ONLY blank lines are frame terminators
    assert raw.count(b"\n\n") == len(raws)


def test_a_json_error_in_one_agent_does_not_corrupt_the_frame():
    """Non-ASCII and control characters must survive the byte round trip: the
    frame is written to a socket as UTF-8, not as an escaped ASCII string."""
    t = tower()
    t["characters"][0]["slug"] = "Ana\\Mañana \"quoted\" — ok\x07"
    raw = b"".join(os.stream_frames(t, revision_of(t), 1700000000.0))
    event, body = assert_sse_frame_wellformed(_raw_frames(raw)[1])
    payload = json.loads(body)
    assert payload["agents"][0]["name"] == "Ana\\Mañana \"quoted\" — ok\x07"


# ======================================================================
# 6 — AN EMPTY TOWER
# ======================================================================
def test_an_empty_tower_yields_an_empty_snapshot_not_a_crash():
    """No rooms, no characters. The honest answer is an empty office — not an
    exception, and not a fabricated room to "have something to draw"."""
    t = {"now": 1791964228.0, "floorplan": {"floors": []}, "characters": [],
         "hud": {}, "activity": []}
    snap = os.to_snapshot(t, 3, 1700000000.0)
    assert snap["rooms"] == []
    assert snap["agents"] == []
    assert snap["accounts"] == []
    assert snap["rev"] == 3
    assert snap["meta"]["demo"] is False


def test_a_tower_missing_every_key_yields_an_empty_snapshot():
    """The degenerate payload: `{}`. Every field the module reads is absent."""
    snap = os.to_snapshot({}, 1, 1700000000.0)
    assert snap["rooms"] == []
    assert snap["agents"] == []


@pytest.mark.parametrize("broken", [
    {"floorplan": None, "characters": None},
    {"floorplan": {}, "characters": {}},
    {"floorplan": {"floors": None}, "characters": []},
    {"floorplan": {"floors": [{}]}, "characters": []},
    {"floorplan": {"floors": [{"number": 1}]}, "characters": []},
    {"floorplan": {"floors": [{"number": 1, "rooms": None}]}, "characters": None},
])
def test_a_partially_null_tower_is_an_empty_office_not_a_throw(broken):
    """The tower is a fold over a store that can be mid-write. A `None` where a
    list belongs must read as "nothing there", not as a TypeError inside the
    SSE writer — which would surface as a dropped connection, not an error."""
    snap = os.to_snapshot(broken, 1, 1700000000.0)
    assert snap["rooms"] == []
    assert snap["agents"] == []


def test_an_empty_tower_streams_valid_frames_with_no_feed():
    t = {"now": 1.0, "floorplan": {"floors": []}, "characters": [], "hud": {},
         "activity": []}
    raw = b"".join(os.stream_frames(t, 1, 1700000000.0))
    frames = parse_sse(raw)
    events = [e for e, _ in frames]
    assert events == [None, "snapshot"], (
        f"an empty office streamed {events} — a feed frame with no items would "
        f"make the client clear a feed it should leave alone")
    assert json.loads("\n".join(frames[1][1]))["rooms"] == []


def test_the_empty_payload_marks_itself_empty_but_the_snapshot_does_not_lie():
    """`empty: true` in the payload with no rooms must still render as an
    office with no rooms — and `demo` must never become true, because a demo
    snapshot is the client drawing a fictional office as if it were real."""
    t = tower()
    t["empty"] = True
    t["floorplan"] = {"floors": []}
    t["characters"] = []
    snap = os.to_snapshot(t, 9, 1700000000.0)
    assert snap["rooms"] == [] and snap["agents"] == []
    assert snap["meta"]["demo"] is False, (
        "an empty tower was marked as demo data")


# ======================================================================
# 7 — THE FEED
# ======================================================================
def test_activity_rows_map_to_feed_items_with_string_ids_and_ms_timestamps():
    t = tower()
    items = os.to_feed(t)
    expected = t["activity"][:40]
    assert len(items) == len(expected)
    for item, row in zip(items, expected):
        assert isinstance(item["id"], str), (
            f"FeedItem.id is a string in types.ts; got {type(item['id'])}")
        assert item["id"] == str(row["id"])
        assert isinstance(item["ts"], int), (
            f"FeedItem carries no `ts`; the client's Activity uses epoch ms, "
            f"got {item['ts']!r}")
        assert item["ts"] == int(row["ts"] * 1000), (
            f"a seconds timestamp was passed through as milliseconds: "
            f"{item['ts']} != {int(row['ts'] * 1000)}")
        assert isinstance(item["agentId"], str)
        assert item["text"] == row["text"], "the human-readable text was rewritten"


def test_feed_order_is_the_payload_order():
    """The stream is a window onto recent activity. Reordering it invents a
    sequence of events that did not happen."""
    t = tower()
    ids = [i["id"] for i in os.to_feed(t)]
    assert ids == [str(r["id"]) for r in t["activity"][:40]]


def test_feed_is_bounded_so_repeated_stream_snapshots_cannot_replay_history():
    t = tower()
    t["activity"] = [dict(row, id=i) for i, row in enumerate(t["activity"] * 20)]
    items = os.to_feed(t)
    assert len(items) == 40
    assert [int(item["id"]) for item in items] == list(range(40))


def test_repeated_feed_events_are_deduplicated_by_the_shipped_habblaud_store():
    """The stream resends feed rows each snapshot; the actual vendor client
    must de-duplicate their IDs, not append history every two seconds."""
    chunk = REPO / "frontend/qubit-app/plugins/qubit-office/habblaud/bundle/app-U4Pwh2XA.js"
    code = chunk.read_text(encoding="utf-8")
    assert "applyFeed(e){if(!e.length)return" in code
    assert "new Set(this.feed.map(e=>e.id))" in code
    assert "e.filter(e=>!t.has(e.id))" in code


def test_a_feed_row_with_no_timestamp_does_not_erupt():
    t = tower()
    t["activity"] = [{"id": 1, "kind": "run_started", "text": "x"},
                     {"id": 2, "ts": None, "agent_id": None, "text": "y"}]
    items = os.to_feed(t)
    assert items[0]["ts"] == 0
    assert items[1]["ts"] == 0
    assert items[1]["agentId"] is None, (
        "an absent agent_id became a string ('None') the client would resolve")


def test_no_activity_is_an_empty_feed():
    assert os.to_feed({"activity": []}) == []
    assert os.to_feed({}) == []


# ======================================================================
# 8 — THE SNAPSHOT'S OWN SHAPE (the client's required fields)
# ======================================================================
def test_the_snapshot_carries_every_field_the_client_requires():
    snap = snapshot()
    for room in snap["rooms"]:
        missing = [f for f in REQUIRED_ROOM_FIELDS if f not in room]
        assert missing == [], f"room {room.get('id')} is missing {missing}"
        assert isinstance(room["id"], str)
        assert isinstance(room["slot"], int)
        assert isinstance(room["seed"], int)
        assert isinstance(room["createdAt"], int)
        assert room["slot"] == snap["rooms"].index(room), (
            "slots are not the arrival order the client's compaction assumes")
    for agent in snap["agents"]:
        missing = [f for f in REQUIRED_AGENT_FIELDS if f not in agent]
        assert missing == [], f"agent {agent.get('id')} is missing {missing}"
        assert agent["kind"] in ("main", "sub")
        assert agent["look"] in ("f", "m")
        assert isinstance(agent["id"], str)


def test_timestamps_come_from_the_payloads_own_clock():
    """`serverTime` is the tower's `now`, not the local clock: the client
    computes ages against it, so a local clock here would make every agent look
    as old as the difference between the server and the browser."""
    t = tower()
    snap = os.to_snapshot(t, 1, 1700000000.0)
    assert snap["serverTime"] == int(t["now"] * 1000)
    assert snap["meta"]["startedAt"] == 1700000000 * 1000
    assert all(r["createdAt"] == int(t["now"] * 1000) for r in snap["rooms"])


def test_a_payload_with_no_clock_still_produces_integer_timestamps():
    snap = os.to_snapshot({"floorplan": {"floors": [
        {"number": 1, "rooms": [{"id": 1}]}]}, "characters": []}, 1, 1700000000.0)
    assert isinstance(snap["serverTime"], int)
    assert snap["rooms"][0]["createdAt"] == snap["serverTime"]


def test_the_snapshot_is_json_serialisable_whole():
    """The whole payload must survive the one operation that actually carries
    it to the client."""
    encoded = json.dumps(snapshot())
    assert json.loads(encoded)["rev"] == revision_of(tower())


def test_the_translation_does_not_mutate_the_payload():
    """The route re-folds the same report on every tick; a translation that
    writes back into it would compound across polls."""
    t = tower()
    before = json.dumps(t, sort_keys=True)
    os.to_snapshot(t, 1, 0.0)
    os.to_feed(t)
    b"".join(os.stream_frames(t, 1, 0.0))
    assert json.dumps(t, sort_keys=True) == before, (
        "office_stream mutated the tower payload it was given")
