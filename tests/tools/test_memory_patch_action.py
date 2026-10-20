"""Regression for #124582: the memory tool had no find-and-splice edit, so a
partial edit of one clause of a packed multi-fact entry could only be expressed
as a whole-entry ``replace`` — silently discarding the sibling facts in the same
entry. The ``patch`` action splices ``old_text`` -> ``new_text`` inside the
matched entry, preserving the rest of the entry verbatim.

Adapted from upstream ``tests/tools/test_memory_patch_action.py`` at pin
``43faacfb8de7b29548547a7fbdedad0cc6e7391c`` (hermes -> qubit names, QUBIT_HOME).

Upstream's batch-apply cases (``apply_batch`` with a ``patch`` op) are not
reproduced here: on this base every ``MemoryStore.apply_batch`` call raises
``TypeError`` because ``memory_tool_store._batch`` calls the pinned 3-argument
``_memory_target_error(store, target, operations)`` while this tree still has the
older 2-argument definition. That is an independent, pre-existing port gap
unrelated to the ``patch`` action and outside this change's scope.
"""

import json

import pytest

from tools.memory_tool import memory_tool
from tools.memory_tool_store import MemoryStore
from qubit_cli.write_approval_commands import _changed_entries

PACKED = "todo: item A; item B; item C"


@pytest.fixture
def packed_store(tmp_path, monkeypatch):
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / ".qubit"))
    s = MemoryStore(memory_char_limit=500, user_char_limit=300)
    s.add("memory", PACKED)
    return s


class TestPatchSingleOp:
    def test_single_patch_previous_entry_is_reported(self):
        assert _changed_entries({
            "patched_entry_before": PACKED,
            "patched_entry_after": "todo: item A; item B done; item C",
        }, "patched") == [PACKED]

    def test_patch_splices_only_the_matched_span(self, packed_store):
        result = json.loads(memory_tool(
            action="patch", old_text="item B", new_text="item B done", store=packed_store))
        assert result["success"] is True
        assert packed_store._entries_for("memory") == ["todo: item A; item B done; item C"]

    def test_patch_returns_before_and_after(self, packed_store):
        result = json.loads(memory_tool(
            action="patch", old_text="item B", new_text="item B done", store=packed_store))
        assert result["patched_entry_before"] == PACKED
        assert result["patched_entry_after"] == "todo: item A; item B done; item C"

    def test_patch_requires_old_text(self, packed_store):
        result = json.loads(memory_tool(action="patch", new_text="x", store=packed_store))
        assert result["success"] is False
        assert "old_text" in result["error"]

    def test_patch_requires_new_text(self, packed_store):
        result = json.loads(memory_tool(action="patch", old_text="item B", store=packed_store))
        assert result["success"] is False
        # Deleting the whole entry would drop the sibling facts; point at a longer span instead.
        assert "patch a longer span" in result["error"]

    def test_patch_unmatched_old_text_fails_without_write(self, packed_store):
        result = json.loads(memory_tool(
            action="patch", old_text="item Z", new_text="whatever", store=packed_store))
        assert result["success"] is False
        assert packed_store._entries_for("memory") == [PACKED]

    def test_patch_multi_entry_match_is_rejected(self, packed_store):
        packed_store.add("memory", "also mentions item B elsewhere")
        result = json.loads(memory_tool(
            action="patch", old_text="item B", new_text="x", store=packed_store))
        assert result["success"] is False
        assert packed_store._entries_for("memory") == [PACKED, "also mentions item B elsewhere"]


class TestPatchStoreLevel:
    def test_store_patch_method(self, packed_store):
        result = packed_store.patch("memory", "item B", "item B done")
        assert result["success"] is True
        assert packed_store._entries_for("memory") == ["todo: item A; item B done; item C"]

    def test_patch_rejects_a_span_repeated_within_the_entry(self, tmp_path, monkeypatch):
        # Replacing every occurrence rewrote sibling facts that shared the span.
        monkeypatch.setenv("QUBIT_HOME", str(tmp_path / ".qubit"))
        s = MemoryStore(memory_char_limit=500)
        entry = "Jeff drinks tea in the morning; his wife drinks tea at night"
        s.add("memory", entry)
        result = s.patch("memory", "tea", "coffee")
        assert result["success"] is False
        assert "appears 2 times" in result["error"]
        assert s._entries_for("memory") == [entry]
        assert s.patch("memory", "drinks tea in the morning", "drinks coffee in the morning")["success"] is True
        assert s._entries_for("memory") == ["Jeff drinks coffee in the morning; his wife drinks tea at night"]


def _store_with(tmp_path, monkeypatch, entry):
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / ".qubit"))
    s = MemoryStore(memory_char_limit=500)
    s.add("memory", entry)
    return s


def test_patch_rejects_an_overlapping_repeat(tmp_path, monkeypatch):
    # str.count misses overlaps: '00' is twice in '1000', and the edit saved 'Timeout 150 ms'.
    s = _store_with(tmp_path, monkeypatch, "Timeout 1000 ms")
    result = s.patch("memory", "00", "5")
    assert result["success"] is False and "appears 2 times" in result["error"]
    assert s._entries_for("memory") == ["Timeout 1000 ms"]


@pytest.mark.parametrize("entry,old_text,content,expected", [
    ("Jeff drinks tea tonight", "tea ", "coffee ", "Jeff drinks coffee tonight"),
    ("Lives in NewYork", "New", "New ", "Lives in New York"),
])
def test_patch_spans_keep_their_whitespace(tmp_path, monkeypatch, entry, old_text, content, expected):
    # The single call stripped old_text only (double space); the batch stripped both (a no-op).
    s = _store_with(tmp_path, monkeypatch, entry)
    assert s.patch("memory", old_text, content)["success"] is True
    assert s._entries_for("memory") == [expected]


def test_patch_op_counts_as_destructive_for_staging(packed_store):
    # patch rewrites part of an entry: it must be staged (matched_entry pinned)
    # exactly like replace, never replayed against a newer entry.
    from tools.memory_tool import destructive_ops
    ops = destructive_ops({"action": "batch", "operations": [
        {"action": "patch", "old_text": "item B", "new_text": "x"}]})
    assert [op["action"] for op in ops] == ["patch"]
