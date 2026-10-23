"""The turn body's scoped half: what ``api_policy_scope`` must be open for, and what it captures.

Upstream ``gateway/run_turn_runner.py`` (pin 43faacf) splits the turn in two:

* ``run_sync`` (:1115-1126) opens ``policy_scope`` + ``api_policy_scope``, runs the body, and merges
  the classic-CLI fields the body stashed on ``turn._cli_result_fields`` into
  ``execution_result['result']`` — the dict a settled receipt returns.
* ``_run_sync_scoped`` (:1128-1330) derives ``max_iterations`` / ``enabled_toolsets`` from the bound
  hosted-room policy (:1168-1172), calls ``prepare_api_runtime`` inside that scope (:1171-1172), and
  re-applies the /v1 Responses request's own ``reasoning`` / ``service_tier`` (:1221-1230).

Without the split all three are wrong in ways no other test catches: a room turn runs under the
gateway's iteration cap and full toolset, an admitted request silently keeps the session's reasoning
settings, and a classic CLI viewer's receipt carries no result at all.
"""
from types import SimpleNamespace

import pytest

from gateway.config import Platform
from gateway.session import SessionSource
from gateway.turn_context import TurnContext

SEEN = {}
ADOPTED = {}


class _Agent:
    # ``wire_api_observers`` reads these before re-assigning them.
    status_callback = None
    interim_assistant_callback = None

    def __init__(self, **kwargs):
        SEEN.update(kwargs)
        self.model = kwargs["model"]
        self.session_id = kwargs["session_id"]
        self.tools = []
        self.context_compressor = SimpleNamespace(
            last_prompt_tokens=0, context_length=200_000, compression_count=0,
        )
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.session_output_tokens = 0

    def run_conversation(self, _message, **_kwargs):
        # ``turn_exit_reason`` is a ``_RESULT_PASSTHROUGH`` key: the gateway's own result drops it,
        # the classic CLI receipt keeps it.
        return {"final_response": "ok", "messages": [], "api_calls": 1,
                "last_reasoning": "r", "turn_exit_reason": "complete"}


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """``_current_max_iterations`` reloads the profile's ``.env``; pin ``QUBIT_HOME`` per test."""
    home = tmp_path / ".qubit"
    home.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(home))
    SEEN.clear()
    ADOPTED.clear()


def _gateway_runner():
    from unittest.mock import MagicMock

    gr = MagicMock()
    gr.config = SimpleNamespace(streaming=None)
    gr._provider_routing = {}
    gr._agent_cache_lock = None
    gr._agent_cache = {}
    gr._session_db = None
    gr._prefill_messages = None
    gr._pending_model_notes = {}
    gr._pending_skills_reload_notes = {}
    gr.session_store._entries = {}
    gr._get_system_prompt_for_channel.return_value = None
    gr._resolve_session_agent_runtime.return_value = ("test-model", {})
    gr._resolve_session_reasoning_config.return_value = {"effort": "GATEWAY-DEFAULT"}
    gr._resolve_session_service_tier.return_value = "gateway-default-tier"
    gr._resolve_turn_agent_config.return_value = {"model": "test-model", "runtime": {}}
    gr._agent_config_signature.return_value = ("sig",)
    gr._extract_cache_busting_config.return_value = {}
    gr._refresh_fallback_model.return_value = None
    gr._consume_pending_native_image_paths.return_value = []
    gr._consume_pending_turn_sidecar_notes.return_value = []
    gr._is_telegram_topic_lane.return_value = False
    gr._is_discord_auto_thread_lane.return_value = False
    gr._is_relay_discord_channel_lane.return_value = False
    gr._delivery_adapter_for.return_value = None
    # ``prepare_api_runtime`` re-runs the admission's own route selection.
    gr._select_agent_runtime.return_value = ("test-model", None, None, None)
    gr._model_routes = {}
    gr.session_authorities = None
    gr.session_authority = _authority()
    return gr


def _authority(session_id="c"):
    from gateway.session_events import SessionEvents

    return SimpleNamespace(
        # Relative, like the gateway fixture: ``_read_prefill`` resolves it to ``QUBIT_HOME``.
        profile_id="default",
        runner=SimpleNamespace(_prefill_messages=None),
        sessions={session_id: SimpleNamespace(event_stream=SessionEvents())},
        db=SimpleNamespace(get_session=lambda sid: {"runtime_generation": 7}),
        logical_owner=lambda sid: "canonical-" + sid,
        # The turn installs its agent for the running claim; the fake records the exact
        # ``(session_id, generation, agent)`` contract the production path calls.
        adopt_agent=lambda session_id, generation, agent: ADOPTED.update(
            session_id=session_id, generation=generation, agent=agent),
        publish_execution=lambda *a, **k: True,
        check_approval_generation=lambda *a, **k: None,
        api_observers={},
    )


def _ctx():
    return TurnContext(
        source=SessionSource(platform=Platform.LOCAL, chat_id="c", user_id="u"),
        message="hi", history=[], session_id="c", session_key="c", user_config={},
        AIAgent=_Agent, resolve_display_setting=lambda *a: False,
        _run_still_current=lambda: True, _hooks_ref=SimpleNamespace(loaded_hooks=False),
    )


def _admission(**settings):
    from gateway.session_api_turn import api_execution

    return api_execution.set({"settings": settings, "adapter": _gateway_runner()})


def _room_policy(max_iterations=7, toolsets=("bot_room", "safe")):
    """A signed ``RoomExecutionPolicy`` mapping; ``api_policy_scope`` re-verifies the digest."""
    from gateway.hosted_room_execution_policy import (POLICY_VERSION, RoomExecutionPolicy,
                                                      _policy_digest)

    unsigned = {"version": POLICY_VERSION, "target_profile": "default",
                "enabled_toolsets": sorted(toolsets), "approval_mode": "manual",
                "max_iterations": max_iterations}
    return RoomExecutionPolicy.from_mapping(
        {**unsigned, "policy_digest": _policy_digest(unsigned)}).as_mapping()


def test_request_reasoning_and_service_tier_outrank_the_sessions_settings():
    """A /v1 Responses request that names ``reasoning`` and ``service_tier`` must run with them."""
    from gateway.session_api_turn import api_execution
    from gateway.run_turn_runner import TurnRunner

    token = _admission(model_options={"reasoning": {"enabled": True, "effort": "high"},
                                      "service_tier": "REQUEST-TIER"})
    try:
        TurnRunner(_gateway_runner(), _ctx()).run_sync()
    finally:
        api_execution.reset(token)

    assert SEEN["reasoning_config"] == {"enabled": True, "effort": "high"}
    assert SEEN["service_tier"] == "REQUEST-TIER"


def test_request_without_model_options_keeps_the_sessions_settings():
    """The override is per-request: a request that names neither field changes neither."""
    from gateway.session_api_turn import api_execution
    from gateway.run_turn_runner import TurnRunner

    token = _admission()
    try:
        TurnRunner(_gateway_runner(), _ctx()).run_sync()
    finally:
        api_execution.reset(token)

    assert SEEN["reasoning_config"] == {"effort": "GATEWAY-DEFAULT"}
    assert SEEN["service_tier"] == "gateway-default-tier"


def test_classic_cli_turn_fields_reach_the_settled_receipt(tmp_path):
    """``run_classic_cli_turn`` stashes the receipt-only fields; the outer turn half must merge them.

    ``local_viewer_turn`` / ``policy_for_source`` are stubbed to *classify* this turn as a classic
    CLI turn — ``run_classic_cli_turn`` itself, and the merge under test, run for real.
    """
    import gateway.run_turn_progress as progress
    import gateway.session_policy as policy_mod
    from gateway.run_turn_runner import TurnRunner
    from gateway.session_api_turn import api_execution
    from gateway.session_results import execution_result

    progress_local_viewer, policy_for_source = progress.local_viewer_turn, policy_mod.policy_for_source
    progress.local_viewer_turn = lambda runner: True
    # A real policy, not a bare namespace: ``policy_scope`` reads its frozen terminal,
    # follows-profile record and client-env scope, so the turn exercises the live contract.
    cli_policy = policy_mod.LocalSessionPolicy(
        source="cli", platform="cli", cwd=str(tmp_path), model=None, toolsets=(),
        config_json="{}", request_json="{}", terminal_json="{}")
    policy_mod.policy_for_source = lambda runner, source: cli_policy
    captured = {}
    etok, atok = execution_result.set(captured), api_execution.set(None)
    try:
        TurnRunner(_gateway_runner(), _ctx()).run_sync()
    finally:
        execution_result.reset(etok)
        api_execution.reset(atok)
        progress.local_viewer_turn = progress_local_viewer
        policy_mod.policy_for_source = policy_for_source

    receipt = captured["result"]
    # Receipt-only fields the gateway's own delivered result never carries.
    assert receipt["turn_exit_reason"] == "complete"
    assert receipt["last_reasoning"] == "r"
    # ``pending_steer`` is popped off the raw result and carried (as None) on the receipt.
    assert receipt["pending_steer"] is None
    assert receipt["final_response"] == "ok"
    # The gateway's own projection still wins for what it computed itself.
    assert receipt["api_calls"] == 1


def test_admitted_turn_receipt_carries_the_result():
    """Every executing admission settles from ``captured['result']``, not only a failed one."""
    from gateway.run_turn_runner import TurnRunner
    from gateway.session_api_turn import api_execution
    from gateway.session_results import execution_result

    captured = {}
    etok = execution_result.set(captured)
    atok = _admission()
    try:
        result = TurnRunner(_gateway_runner(), _ctx()).run_sync()
    finally:
        execution_result.reset(etok)
        api_execution.reset(atok)

    assert captured["result"] is result
    assert captured["result"]["final_response"] == "ok"


def test_hosted_room_turn_runs_under_the_rooms_cap_and_toolsets():
    """The room's policy is read while its scope is bound — before the agent is resolved."""
    from gateway.hosted_room_execution_policy import current_room_execution_policy
    from gateway.session_api_turn import api_execution
    from gateway.run_turn_runner import TurnRunner

    policy = _room_policy()
    ctx = _ctx()
    token = _admission(room_execution_policy=policy)
    try:
        TurnRunner(_gateway_runner(), ctx).run_sync()
        # The scope closes with the turn: no policy leaks into the next one.
        assert current_room_execution_policy() is None
    finally:
        api_execution.reset(token)

    assert SEEN["max_iterations"] == policy["max_iterations"]
    assert list(SEEN["enabled_toolsets"]) == policy["enabled_toolsets"]
    assert list(ctx.enabled_toolsets) == policy["enabled_toolsets"]
