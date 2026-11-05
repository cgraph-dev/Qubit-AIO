"""Resumed-transcript projection: cached tool previews, identical messages, full history retained."""

import json

import pytest

from agent import display
from tui_gateway import server


def _tool_history(n_rows: int = 3):
    rows = [{"role": "user", "content": "start", "timestamp": 1.0}]
    for i in range(n_rows):
        call = {"id": f"call-{i}", "type": "function",
                "function": {"name": "terminal", "arguments": json.dumps({"command": f"cd /repo && make build_{i} 2>&1 | tail -5"})}}
        rows.append({"role": "assistant", "content": "", "tool_calls": [call], "timestamp": 2.0 + i})
        rows.append({"role": "tool", "tool_call_id": f"call-{i}", "content": "ok", "timestamp": 2.5 + i})
    rows.append({"role": "assistant", "content": "done", "timestamp": 9.0})
    return rows


@pytest.fixture(autouse=True)
def _clear_caches():
    server._tool_ctx_cache.clear()
    display._cached_split_shell_words.cache_clear()
    display._cached_split_shell_compound.cache_clear()
    yield
    server._tool_ctx_cache.clear()


def test_tool_ctx_caches_and_matches_the_direct_preview(monkeypatch):
    args = {"command": "cd /repo && pytest -q tests/unit 2>&1 | tail -20"}
    expected = display.build_tool_preview("terminal", args, max_len=80) or ""
    assert server._tool_ctx("terminal", args) == expected

    calls = []
    real = display.build_tool_preview

    def counting(*a, **kw):
        calls.append(a)
        return real(*a, **kw)

    monkeypatch.setattr(display, "build_tool_preview", counting)
    assert server._tool_ctx("terminal", args) == expected
    assert calls == [], "a warm identity must not rebuild the preview"


def test_tool_ctx_cache_is_scoped_by_display_state(monkeypatch):
    args = {"command": "cd /repo && pytest -q 2>&1 | tail -5"}
    calls = []
    real = display.build_tool_preview

    def counting(*a, **kw):
        calls.append(a)
        return real(*a, **kw)

    monkeypatch.setattr(display, "build_tool_preview", counting)
    server._tool_ctx("terminal", args, display_token=("en", True))
    server._tool_ctx("terminal", args, display_token=("en", True))
    assert len(calls) == 1, "same display state must reuse the preview"
    server._tool_ctx("terminal", args, display_token=("fr", True))
    assert len(calls) == 2, "a changed display state must not serve the previous preview"


def test_long_argument_blobs_are_not_retained():
    args = {"command": "echo " + ("x" * (server._TOOL_CTX_CACHE_MAX_KEY_CHARS + 32))}
    server._tool_ctx("terminal", args)
    assert server._tool_ctx_cache == {}, "an oversized preview key must not pin memory"


def test_projection_is_identical_cold_and_warm():
    history = _tool_history()
    cold = server._history_to_messages(history, tool_labels=False)
    server._tool_ctx_cache.clear()
    display._cached_split_shell_words.cache_clear()
    display._cached_split_shell_compound.cache_clear()
    cold_again = server._history_to_messages(history, tool_labels=False)
    warm = server._history_to_messages(history, tool_labels=False)
    assert cold == cold_again == warm


def test_projection_keeps_the_full_history_without_truncation():
    history = []
    for i in range(1200):
        history.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"message {i}", "timestamp": float(i)})
    messages = server._history_to_messages(history, tool_labels=False)
    assert len(messages) == 1200
    assert messages[0]["text"] == "message 0"
    assert messages[-1]["text"] == "message 1199"
