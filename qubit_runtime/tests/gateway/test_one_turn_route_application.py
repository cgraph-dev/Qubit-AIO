"""The classic CLI's per-turn ``prompt.submit`` route lands exactly once (CALLMAP W2).

Main's QubitCLI runs ``/model --once`` and ``/moa`` as a one-turn runtime swap: the target is
committed with the admission row as ``cli_turn_v1.model_override`` and applied to that admission's
resolved route only. The session's own route was already resolved (including
``_apply_session_model_override``); ``one_turn_route`` layers the committed override on top exactly
once, and the executing model never becomes the session's stored model.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import Platform


class _RecordingAgent:
    built: list = []

    def __init__(self, **kwargs):
        type(self).built.append(kwargs)
        self.model = kwargs["model"]
        self.session_id = kwargs.get("session_id")
        self.tools = []
        self.context_compressor = SimpleNamespace(last_prompt_tokens=0, context_length=200_000)
        self.session_prompt_tokens = self.session_completion_tokens = 0
        self.calls = []

    def run_conversation(self, message, **kwargs):
        self.calls.append((message, kwargs))
        return {"final_response": "ok", "messages": []}


def _gateway_runner():
    runner = MagicMock()
    runner.config = SimpleNamespace(streaming=None)
    runner._provider_routing = {}
    runner._agent_cache_lock = None
    runner._agent_cache = {}
    runner._session_db = runner._prefill_messages = None
    runner._pending_model_notes = runner._pending_skills_reload_notes = {}
    runner.session_store._entries = {}
    runner._pre_agent_fallback_notice = None
    runner._get_system_prompt_for_channel.return_value = None
    runner._resolve_session_agent_runtime.return_value = (
        "session-model", {"provider": "custom", "api_key": "k1", "base_url": "http://session"})
    runner._resolve_session_reasoning_config.return_value = None
    runner._resolve_session_service_tier.return_value = None
    runner._resolve_turn_agent_config.side_effect = lambda _msg, model, rt: {
        "model": model, "runtime": {k: v for k, v in rt.items() if k != "request_overrides"}}
    runner._agent_config_signature.return_value = ("sig",)
    runner._extract_cache_busting_config.return_value = {}
    runner._refresh_fallback_model.return_value = None
    runner._consume_pending_native_image_paths.return_value = []
    runner._consume_pending_turn_sidecar_notes.return_value = []
    for lane in ("_is_telegram_topic_lane", "_is_discord_auto_thread_lane", "_is_relay_discord_channel_lane"):
        getattr(runner, lane).return_value = False
    return runner


def _run_turn(runner, admission_id="adm-1"):
    from gateway.run_turn_runner import TurnRunner
    from gateway.session import SessionSource
    from gateway.turn_context import TurnContext
    ctx = TurnContext(
        source=SessionSource(platform=Platform.LOCAL, chat_id="c", user_id="u"),
        message="clean text", history=[], session_id="sid", session_key="key", user_config={},
        inbound_message_id=admission_id, AIAgent=_RecordingAgent, resolve_display_setting=lambda *_a: False,
        _run_still_current=lambda: True, _hooks_ref=SimpleNamespace(loaded_hooks=False))
    result = TurnRunner(runner, ctx).run_sync()
    return result, ctx.agent_holder[0]


def test_the_turn_runs_the_one_turn_model_with_exactly_one_application():
    from gateway.session_cli_turn import cli_turn_scope
    from qubit_cli.model_switch import ModelSwitchResult

    switched = ModelSwitchResult(success=True, new_model="gpt-x", target_provider="openrouter",
                                 api_key="k2", base_url="http://or", api_mode="chat_completions")
    _RecordingAgent.built = []
    runner = _gateway_runner()
    committed = {"model_override": {"model": "gpt-x", "provider": "openrouter"}}
    with patch("qubit_cli.model_switch.switch_model", return_value=switched) as switch, \
            cli_turn_scope("adm-1", committed):
        result, agent = _run_turn(runner)
    assert result["final_response"] == "ok"
    # One application: the committed override is resolved once, from the session's own route.
    switch.assert_called_once()
    assert switch.call_args.kwargs["raw_input"] == "gpt-x"
    assert switch.call_args.kwargs["explicit_provider"] == "openrouter"
    assert switch.call_args.kwargs["current_model"] == "session-model"
    built = _RecordingAgent.built[-1]
    assert (built["model"], built["provider"], built["api_key"], built["base_url"]) == (
        "gpt-x", "openrouter", "k2", "http://or")

    # The next turn (and any turn whose event is not that admission) runs the session's own route
    # and never applies the override a second time.
    with patch("qubit_cli.model_switch.switch_model") as switch, cli_turn_scope("adm-1", committed):
        _result, agent = _run_turn(runner, admission_id="adm-2")
    switch.assert_not_called()
    assert _RecordingAgent.built[-1]["model"] == "session-model"


def test_an_unresolvable_one_turn_model_fails_the_turn_without_running_it():
    from gateway.session_cli_turn import cli_turn_scope
    from qubit_cli.model_switch import ModelSwitchResult

    _RecordingAgent.built = []
    failed = ModelSwitchResult(success=False, error_message="Unknown model 'nope'")
    with patch("qubit_cli.model_switch.switch_model", return_value=failed), \
            cli_turn_scope("adm-1", {"model_override": {"model": "nope"}}):
        result, agent = _run_turn(_gateway_runner())
    assert result["failed"] is True and "Unknown model 'nope'" in result["final_response"]
    assert agent is None and _RecordingAgent.built == []


def test_a_one_turn_model_never_becomes_the_sessions_stored_model():
    """``/moa`` and ``/model --once`` run one admission on another route. The turn's post-run sync
    wrote the executing agent's model to the session row (``default``, the MoA preset), so a /resume
    right after restored onto the preset. Only a turn on the session's own route records its model."""
    from gateway.run_turn_prepare import GatewayTurnPrepareMixin
    from gateway.session_cli_turn import cli_turn_scope
    from qubit_cli.model_switch import ModelSwitchResult

    written = []
    db = SimpleNamespace(get_session=lambda sid: {"model": "session-model", "model_config": "{}"},
                         update_session_meta=lambda sid, config, model=None: written.append((sid, model)))
    runner = _gateway_runner()
    runner._session_db = SimpleNamespace(_db=db)
    runner._sync_session_model_from_agent = (
        lambda sid, agent: GatewayTurnPrepareMixin._sync_session_model_from_agent(runner, sid, agent))
    moa = ModelSwitchResult(success=True, new_model="default", target_provider="moa", api_key="",
                            base_url="", api_mode="chat_completions")
    committed = {"model_override": {"model": "default", "provider": "moa"}}
    with patch("qubit_cli.model_switch.switch_model", return_value=moa), cli_turn_scope("adm-1", committed):
        _result, agent = _run_turn(runner)
    assert agent.model == "default" and written == []
    with cli_turn_scope("adm-1", committed):
        _run_turn(runner, admission_id="adm-2")
    assert [model for _sid, model in written] == ["session-model"]
