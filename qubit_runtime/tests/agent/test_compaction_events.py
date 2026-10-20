"""``hermes.compaction`` v1 records: one content-free record per history rewrite, on every path.

Codex-route section of the upstream ``tests/agent/test_compaction_events.py`` (pin
``43faacfb8de7b29548547a7fbdedad0cc6e7391c``). The other sections of that file exercise
compaction features that are not ported here yet (attempt ``method`` on the
summarizer path, ``overflow_reason``, micro-compaction publishing, empty-transcript
publishing); they are intentionally not copied rather than pinned red.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.conversation_compression import CompressionCheckpointUnavailable, compress_context
from agent.transports.codex_app_server_session import TurnResult

SECRET = "TOPSECRET_TRANSCRIPT_TEXT"


@pytest.fixture
def published(monkeypatch):
    """Every (session_id, mark name, payload) handed to the Relay emitter."""
    marks = []
    monkeypatch.setattr("agent.relay_compaction.compaction_target_available", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "agent.relay_compaction.emit_compaction_mark",
        lambda session_id, name, data, **_kwargs: marks.append((session_id, name, data)) or True,
    )
    return marks


def _codex_agent(result, auto_mode):
    agent = SimpleNamespace(
        api_mode="codex_app_server", codex_app_server_auto_compaction=auto_mode, session_id="codex-session",
        platform="cli", model="gpt-test", provider="openai-codex", _cached_system_prompt="cached prompt",
        _codex_session=SimpleNamespace(compact_thread=lambda: result, close=lambda: None),
        _compression_activity_heartbeat_interval=0.1,
        context_compressor=SimpleNamespace(
            compression_count=0, last_compression_rough_tokens=0, last_prompt_tokens=1, last_completion_tokens=0,
            awaiting_real_usage_after_compression=False,
        ),
    )
    agent._touch_activity = lambda *_args, **_kwargs: None
    agent._emit_status = agent._emit_warning = lambda _message: None
    return agent


@pytest.mark.parametrize(
    ("auto_mode", "force", "result", "name", "outcome"),
    [
        ("native", False, TurnResult(thread_id="t1"), "compaction.attempt", "skipped"),
        ("hermes", True, TurnResult(thread_id="t1", turn_id="c1"), "compaction", "committed"),
        ("hermes", True, TurnResult(thread_id="t1", error="boom"), "compaction.attempt", "failed"),
    ],
)
def test_codex_route_publishes_one_provider_record_per_exit(published, auto_mode, force, result, name, outcome):
    compress_context(_codex_agent(result, auto_mode), [{"role": "user", "content": SECRET}], "system", force=force)

    [(session_id, mark, payload)] = published
    assert (session_id, mark, payload["outcome"]) == ("codex-session", name, outcome)
    assert (payload["kind"], payload["scope"], payload["official"]) == ("provider_native", "provider", True)
    assert "boom" not in json.dumps(payload)


def _raising_codex_agent():
    def _compact_thread():
        raise RuntimeError(f"boom {SECRET}")

    agent = _codex_agent(None, "hermes")
    agent._codex_session = SimpleNamespace(compact_thread=_compact_thread, close=lambda: None)
    return agent


def _checkpoint_required_codex_agent():
    agent = _codex_agent(TurnResult(thread_id="t1"), "hermes")
    agent.compression_checkpoint_required = True
    return agent


@pytest.mark.parametrize(
    ("make_agent", "error"),
    [
        pytest.param(_raising_codex_agent, RuntimeError, id="compact-thread-raises"),
        pytest.param(_checkpoint_required_codex_agent, CompressionCheckpointUnavailable, id="checkpoint-required"),
    ],
)
def test_codex_route_that_raises_publishes_one_failed_attempt_mark(published, make_agent, error):
    with pytest.raises(error):
        compress_context(make_agent(), [{"role": "user", "content": SECRET}], "system", force=True)

    [(session_id, mark, payload)] = published
    assert (session_id, mark) == ("codex-session", "compaction.attempt")
    assert (payload["kind"], payload["outcome"], payload["failure_class"]) == ("provider_native", "failed", "exception")
    assert SECRET not in json.dumps(payload) and "boom" not in json.dumps(payload)


def test_codex_native_compaction_observed_by_qubit_is_an_unofficial_provider_compaction(published):
    from agent.codex_runtime import _record_codex_app_server_compaction

    agent = _codex_agent(None, "native")
    agent.event_callback = None
    _record_codex_app_server_compaction(agent, TurnResult(thread_id="t1", turn_id="n1", compacted=True))

    [(_sid, name, payload)] = published
    assert name == "compaction"
    assert (payload["kind"], payload["official"], payload["trigger"]) == ("provider_native", False, "provider")
    assert payload["trigger_class"] == "provider" and payload["compression_count"] == 1
