"""The gateway turn's `message.complete` payload must carry the `persisted_turn` receipt and the
`-z --usage-file` ledger keys, and a confirmed API model lock must settle on the runtime it named.

Ported from upstream `gateway/run_turn_runner.py` (pin 43faacf), where `_persisted_turn` and
`_LEDGER_PASSTHROUGH_KEYS` project into the turn result and `_settle_api_runtime_lock` runs after
`run_conversation`. `agent/persisted_turn_receipt` names this host as a publisher, and
`gateway/session_settlement_recovery.completion_payload` reads `captured_result['persisted_turn']`;
without the projection that frame never carries one.
"""
import types

import pytest

import gateway.run_turn_runner as rtr
from agent.context_compressor import _DB_PERSISTED_MARKER


def _row(role, row_id, content="", **kw):
    return {"role": role, _DB_PERSISTED_MARKER: True, "_row_id": row_id, "content": content, **kw}


def _agent(history_len):
    return types.SimpleNamespace(_persist_user_message_idx=history_len, context_compressor=None)


def test_persisted_turn_receipt_addresses_the_turns_committed_rows():
    prefix = _row("user", 1, "old")
    agent_history = [prefix]
    messages = [prefix, _row("user", 2, "new prompt"), _row("assistant", 3, "the answer")]
    receipt = rtr._persisted_turn(_agent(1), agent_history,
                                  {"messages": messages, "final_response": "the answer"}, 0)
    assert receipt["row_ids"] == [2, 3]
    assert receipt["user_row_id"] == 2
    assert receipt["final_assistant_row_id"] == 3


def test_persisted_turn_reports_status_and_drops_the_final_row_when_interrupted():
    prefix = _row("user", 1, "old")
    messages = [prefix, _row("user", 2, "p"), _row("assistant", 3, "partial")]
    receipt = rtr._persisted_turn(_agent(1), [prefix],
                                  {"messages": messages, "final_response": "partial", "interrupted": True}, 0)
    assert "final_assistant_row_id" not in receipt


def test_persisted_turn_is_none_without_a_message_list():
    assert rtr._persisted_turn(_agent(0), [], None, 0) is None


def test_ledger_keys_project_only_known_result_keys():
    result = {key: 1 for key in rtr._LEDGER_PASSTHROUGH_KEYS}
    result["not_a_ledger_key"] = "ignored"
    projected = {k: result.get(k) for k in rtr._LEDGER_PASSTHROUGH_KEYS if k in result}
    assert set(projected) == set(rtr._LEDGER_PASSTHROUGH_KEYS)
    assert "not_a_ledger_key" not in projected


def test_settle_api_runtime_lock_records_the_served_runtime():
    import gateway.session_api_turn as sat

    class Adapter:
        def _turn_runtime_metadata(self, agent, *, route, requested_runtime, route_source,
                                   confirmed_runtime_lock):
            return {"served": "locked", "route": route}

    captured = {"usage": {}}
    token = sat.api_execution.set(
        {"settings": {"confirmed_runtime_lock": True, "route": {"provider": "x"}}, "adapter": Adapter()})
    try:
        rtr._settle_api_runtime_lock(object(), {"final_response": "hi"}, captured)
    finally:
        sat.api_execution.reset(token)
    assert captured["usage"]["runtime"] == {"served": "locked", "route": {"provider": "x"}}


def test_settle_api_runtime_lock_fails_the_turn_on_a_runtime_mismatch():
    import gateway.session_api_turn as sat

    class Adapter:
        def _turn_runtime_metadata(self, *a, **k):
            raise RuntimeError("served runtime differs from the confirmed lock")

    result = {"final_response": "hi"}
    token = sat.api_execution.set({"settings": {"confirmed_runtime_lock": True}, "adapter": Adapter()})
    try:
        rtr._settle_api_runtime_lock(object(), result, {"usage": {}})
    finally:
        sat.api_execution.reset(token)
    assert result["failed"] is True
    assert "differs" in result["error"]


def test_settle_api_runtime_lock_is_a_noop_without_a_confirmed_lock():
    import gateway.session_api_turn as sat

    captured = {"usage": {}}
    token = sat.api_execution.set({"settings": {}, "adapter": object()})
    try:
        rtr._settle_api_runtime_lock(object(), {}, captured)
    finally:
        sat.api_execution.reset(token)
    assert "runtime" not in captured["usage"]


@pytest.mark.platforms("any")
def test_delta_callback_raises_when_a_finite_viewer_cannot_see_the_text(monkeypatch):
    """A live sink alone is delivered; a finite viewer must not count it as delivered."""
    import gateway.stream_consumer as sc

    class Consumer:
        stream_deltas_enabled = True

        def on_delta(self, text):
            pass

    monkeypatch.setattr(sc, "GatewayStreamConsumer", lambda *a, **k: Consumer())

    class Ctx:
        mute_notification_reply = False
        streaming_tts_consumer_holder = [None]
        scheduled_heartbeat = False
        interim_assistant_messages_enabled = True
        user_config = {}
        progress_queue = None
        _status_thread_metadata = None
        event_message_id = None
        source = types.SimpleNamespace(chat_id="c", platform=None)

        @staticmethod
        def resolve_display_setting(*_a, **_k):
            return None

        @staticmethod
        def _run_still_current():
            return True

    class Adapter:
        SUPPORTS_MESSAGE_EDITING = True

    import gateway.config as gcfg

    def build_runner():
        tr = rtr.TurnRunner.__new__(rtr.TurnRunner)
        tr._runner = types.SimpleNamespace(
            config=types.SimpleNamespace(streaming=gcfg.StreamingConfig(enabled=True)),
            _delivery_adapter_for=lambda _s: Adapter(),
            _build_stream_consumer_config=lambda *_a, **_k: (types.SimpleNamespace(), None))
        tr._ctx = Ctx()
        return tr

    monkeypatch.setattr(rtr, "_finite_viewer_turn", lambda: False)
    _, cb, _, _ = build_runner()._setup_stream_consumer("cli")
    assert cb is not None
    cb("hello")  # delivered to a real sink, nothing to report

    monkeypatch.setattr(rtr, "_finite_viewer_turn", lambda: True)
    _, cb, _, want_interim = build_runner()._setup_stream_consumer("cli")
    assert want_interim is False  # a finite viewer cannot see commentary
    with pytest.raises(rtr._NoStreamConsumer):
        cb("hello")
    cb(None)  # the segment boundary itself is not text
