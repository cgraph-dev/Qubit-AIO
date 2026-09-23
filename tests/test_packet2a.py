"""Packet 2A tests: append-only ordered journal, RFC 6902 snapshot/delta
subset, redaction boundary, fail-closed malformed input, and documented local
divergences from the pinned AG-UI contract (docs/archive/2026-09-21/UPSTREAM_REUSE.md R1)."""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from voicebridge.events import (PatchError, PatchUnsupportedError,
                                EventValidationError, EventType,
                                apply_patch, diff_state, encode_sse,
                                from_wire, make_event)
from voicebridge.events.journal import EventJournal


# ------------------------------------------------------------- journal ordering

def test_seq_is_monotonic_and_recovery_resumes_after_reopen(tmp_path):
    path = tmp_path / "state" / "events.jsonl"
    j1 = EventJournal(path)
    for i in range(3):
        j1.append(EventType.STEP_STARTED, step_name=f"s{i}")
    assert j1.last_seq == 3
    j2 = EventJournal(path)                    # reopen: recovery scans max seq
    rec = j2.append(EventType.STEP_FINISHED, step_name="s2")
    assert rec["seq"] == 4 and j2.last_seq == 4
    assert [r["seq"] for r in EventJournal(path).read_all()] == [1, 2, 3, 4]


def test_journal_records_carry_versioned_envelope_and_wire_strips_it(tmp_path):
    j = EventJournal(tmp_path / "events.jsonl")
    rec = j.append(EventType.TEXT_MESSAGE_CONTENT, message_id="m1", delta="hi",
                   correlation_run_id="run-9")
    assert rec["schema"] == "v1" and rec["redacted"] is True
    assert rec["run_id"] == "run-9"            # envelope correlation kept locally
    wire = json.loads(encode_sse(rec)[6:-2])
    assert "seq" not in wire and "schema" not in wire and "redacted" not in wire
    assert "run_id" not in wire                # non-RUN type: correlation is local
    assert wire["messageId"] == "m1"           # AG-UI shape preserved


def test_append_only_torn_tail_is_skipped_and_next_append_stays_valid(tmp_path):
    path = tmp_path / "events.jsonl"
    j = EventJournal(path)
    j.append(EventType.RAW, event={"n": 1})
    good_bytes = path.read_bytes()
    with path.open("a") as fh:                 # simulate crash mid-write
        fh.write('{"seq":2,"type":"RAW",')
    j2 = EventJournal(path)                    # recovery tolerates torn tail
    rec = j2.append(EventType.RAW, event={"n": 2})
    assert rec["seq"] == 2
    records = EventJournal(path).read_all()
    assert [r["event"]["n"] for r in records] == [1, 2]   # torn line skipped
    assert path.read_bytes().startswith(good_bytes)       # append-only honored


def test_concurrent_appends_serialize_without_gap_or_duplicate(tmp_path):
    j = EventJournal(tmp_path / "events.jsonl")
    def worker(n):
        for i in range(10):
            j.append(EventType.CUSTOM, name="tick", value={"w": n, "i": i})
    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    seqs = [r["seq"] for r in EventJournal(tmp_path / "events.jsonl").read_all()]
    assert len(seqs) == 40 and seqs == list(range(1, 41))


def test_redaction_applied_at_journal_boundary(tmp_path):
    j = EventJournal(tmp_path / "events.jsonl")
    rec = j.append(EventType.CUSTOM, name="leak-check",
                   value={"api_token": "sk-super-secret", "note": "fine"})
    stored_line = (tmp_path / "events.jsonl").read_text().splitlines()[0]
    assert "sk-super-secret" not in stored_line
    assert rec["value"]["api_token"] != "sk-super-secret"
    assert rec["value"]["note"] == "fine"


def test_read_run_correlates_envelope_and_payload_ids(tmp_path):
    j = EventJournal(tmp_path / "events.jsonl")
    j.append(EventType.RUN_STARTED, thread_id="t", run_id="r-42")
    j.append(EventType.CUSTOM, name="step", value=1,
             correlation_run_id="r-42", correlation_parent_id=None)
    j.append(EventType.RUN_CANCELLED, thread_id="t", run_id="r-42",
             reason="user-stop")
    run = j.read_run("r-42")
    assert [r["type"] for r in run] == ["RUN_STARTED", "CUSTOM", "RUN_CANCELLED"]


# ------------------------------------------------------------ state delta subset

def test_diff_state_nested_add_remove_replace_and_roundtrip():
    base = {"a": 1, "nested": {"x": True, "y": [1, 2]}, "gone": "bye"}
    new = {"a": 2, "nested": {"x": False, "y": [1, 2, 3], "z": "new"}, "add": 5}
    ops = diff_state(base, new)
    assert {"op": "remove", "path": "/gone"} in [{k: o[k] for k in ("op", "path")}
                                                 for o in ops]
    assert apply_patch(base, ops) == new       # ordered deltas reconstruct state
    assert base == {"a": 1, "nested": {"x": True, "y": [1, 2]}, "gone": "bye"}


def test_empty_patch_is_noop_and_bool_int_identity_is_not_hidden():
    state = {"flag": True, "n": 0}
    assert diff_state(state, {"flag": True, "n": 0}) == []
    ops = diff_state(state, {"flag": 1, "n": 0})          # True == 1 in python
    assert ops and ops[0]["op"] == "replace"              # type change surfaced
    assert apply_patch(state, ops) == {"flag": 1, "n": 0}


def test_json_pointer_escaping_per_rfc6901():
    base = {"weird/key": 1, "til~de": 1}
    new = {"weird/key": 2, "til~de": 2}
    ops = diff_state(base, new)
    paths = {o["path"] for o in ops}
    assert "/weird~1key" in paths and "/til~0de" in paths
    assert apply_patch(base, ops) == new


def test_unsupported_ops_fail_closed_toward_snapshot_recovery():
    with pytest.raises(PatchUnsupportedError):
        apply_patch({"a": 1}, [{"op": "move", "from": "/a", "path": "/b"}])
    with pytest.raises(PatchUnsupportedError):
        apply_patch({"a": 1}, [{"op": "test", "path": "/a", "value": 1}])
    with pytest.raises(PatchError):
        apply_patch({"a": 1}, [{"op": "replace", "path": "no-slash", "value": 2}])


def test_state_delta_event_carries_ordered_ops_against_snapshot_baseline():
    snap = make_event(EventType.STATE_SNAPSHOT, timestamp=1,
                      snapshot={"progress": 0})
    delta = make_event(EventType.STATE_DELTA, timestamp=2,
                       delta=diff_state({"progress": 0}, {"progress": 50}))
    assert isinstance(snap["snapshot"], dict)
    assert delta["delta"][0]["op"] == "replace"
    assert apply_patch(snap["snapshot"], delta["delta"]) == {"progress": 50}


# ------------------------------------------------------- fail-closed malformed

def test_from_wire_rejects_unknown_type_missing_fields_and_duplicates():
    with pytest.raises(EventValidationError):
        from_wire('{"type":"NOT_A_REAL_EVENT"}')
    with pytest.raises(EventValidationError):
        from_wire('{"type":"TEXT_MESSAGE_END"}')               # missing messageId
    with pytest.raises(EventValidationError):
        from_wire('{"type":"TEXT_MESSAGE_CONTENT","messageId":"m",'
                  '"message_id":"m","delta":"d"}')              # ambiguous pair
    with pytest.raises(EventValidationError):
        make_event("TEXT_MESSAGE_START", message_id="m",
                   role="tool")                                 # invalid role


def test_make_event_rejects_unknown_and_type_mismatched_fields():
    with pytest.raises(EventValidationError):
        make_event(EventType.RAW, event="x", bogus_field=1)
    with pytest.raises(EventValidationError):
        make_event(EventType.STATE_DELTA, delta={"op": "add"})  # must be a list
    with pytest.raises(EventValidationError):
        make_event(EventType.STATE_SNAPSHOT, snapshot=[1, 2])   # must be a dict


# --------------------------------------------------- documented divergences

def test_divergence_omitted_upstream_families_are_absent():
    """Chunk/thinking/reasoning families are intentionally omitted (R1);
    RUN_CANCELLED is a documented LOCAL addition (plan Phase 2)."""
    names = {et.value for et in EventType}
    for absent in ("TEXT_MESSAGE_CHUNK", "TOOL_CALL_CHUNK",
                   "THINKING_START", "THINKING_TEXT_MESSAGE_CONTENT",
                   "REASONING_START", "REASONING_ENCRYPTED_VALUE"):
        assert absent not in names
    assert "RUN_CANCELLED" in names
    from voicebridge.events import TERMINAL_EVENT_TYPES
    assert EventType.RUN_CANCELLED in TERMINAL_EVENT_TYPES


def test_terminal_events_are_typed_contract_markers():
    from voicebridge.events import TERMINAL_EVENT_TYPES
    assert TERMINAL_EVENT_TYPES == {EventType.RUN_FINISHED, EventType.RUN_ERROR,
                                    EventType.RUN_CANCELLED}


def test_no_undeclared_dependencies_in_events_package():
    """Production imports must be stdlib or this package (plan 6.4/6.8)."""
    import ast
    root = REPO / "voicebridge" / "events"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods = [node.module.split(".")[0]]
            for mod in mods:
                assert mod in sys.stdlib_module_names | {"voicebridge"}, \
                    f"{path.name} imports undeclared {mod}"
