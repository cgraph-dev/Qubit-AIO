"""Resume fast path: one lineage read and no TUI-only dead work on replayed history."""

import json

from tui_gateway import server


def _history_with_a_bridge_tool():
    args = {"calls": [{"name": "read_file", "arguments": {"path": "README.md"}}]}
    return [
        {"role": "user", "content": "inspect the readme", "timestamp": 100.0},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call-1", "type": "function",
             "function": {"name": "tool_call", "arguments": json.dumps(args)}}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "done", "timestamp": 101.0},
    ]


def test_tui_history_omits_labels_without_changing_the_rendered_fields():
    history = _history_with_a_bridge_tool()
    with_labels = server._history_to_messages(history, tool_labels=True)
    without_labels = server._history_to_messages(history, tool_labels=False)

    assert any(m.get("labels") for m in with_labels), "fixture must exercise a labelled row"
    assert all("labels" not in m for m in without_labels)
    strip = lambda rows: [{k: v for k, v in m.items() if k != "labels"} for m in rows]
    assert strip(with_labels) == strip(without_labels)

    tool_row = next(m for m in without_labels if m["role"] == "tool")
    assert tool_row["name"] == "tool_call"
    assert "context" in tool_row
    assert tool_row.get("args")


def test_resume_marks_only_non_tui_callers_for_tool_labels():
    assert server._Resume("r", {"source": "tui"}, "sid").tool_labels is False
    assert server._Resume("r", {"source": "desktop"}, "sid").tool_labels is True


class _BundleOnlyDB:
    def __init__(self):
        self.bundle_reads = 0

    def get_resume_bundle(self, _sid):
        self.bundle_reads += 1
        return ([{"role": "user", "content": "hello"}],
                [{"role": "user", "content": "hello", "_row_id": 1}],
                [])

    def get_resume_conversations(self, _sid):
        raise AssertionError("restore_bundle must not take the two-read path")

    def get_ancestor_display_prefix(self, _sid):
        raise AssertionError("restore_bundle must not take the two-read path")


def test_restore_bundle_takes_one_read():
    ctx = server._Resume("r", {}, "sid")
    db = _BundleOnlyDB()
    ctx.db = db
    history, display, prefix, raw = ctx.restore_bundle()
    assert db.bundle_reads == 1
    assert history == [{"role": "user", "content": "hello"}]
    assert display == [{"role": "user", "content": "hello", "_row_id": 1}]
    assert prefix == []
    assert raw == [{"role": "user", "content": "hello"}]


class _OmitDB:
    def get_resume_bundle(self, _sid):
        raise AssertionError("omit_messages resumes materialize the tip segment only")

    def get_messages_as_conversation(self, _sid, **_kwargs):
        return [{"role": "user", "content": "tip only"}]


def test_omit_messages_keeps_the_tip_only_path():
    ctx = server._Resume("r", {"omit_messages": True}, "sid")
    ctx.db = _OmitDB()
    history, display, prefix, raw = ctx.restore_bundle()
    assert raw == [{"role": "user", "content": "tip only"}]
    assert display == []
    assert prefix == []
