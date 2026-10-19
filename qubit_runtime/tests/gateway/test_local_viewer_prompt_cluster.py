"""A local viewer's turn must run inside the sudo/secret/CLI-turn cluster.

Upstream's ``run_sync`` wraps ``_run_conversation_with_approval`` in
``sudo_prompt_scope(self, agent)`` + ``secret_prompt_scope(self, agent)`` via
``run_classic_cli_turn(self, lambda: ...)``, with a ``finally`` that restores the local-viewer
sinks and publishes turn-end usage / settled session info.

Without that wiring a local turn cannot ask for a sudo password (``sudo`` dies headless in 0.1 s),
cannot prompt for a skill secret, and publishes no ``session.usage`` / ``session.info`` at turn end.
This test drives a real ``run_sync`` against a session authority that owns the session, which is
what puts ``_approval_owner`` on the runner (``local_viewer_turn`` then returns True).
"""
from types import SimpleNamespace

import pytest

from gateway.config import Platform
from gateway.session import SessionSource
from gateway.turn_context import TurnContext


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """``run_sync`` reads ``QUBIT_HOME`` (``_current_max_iterations`` reloads the profile's
    ``.env``). Pin it per test so this file passes whatever ran before it in the same process."""
    home = tmp_path / ".qubit"
    home.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(home))
    return home


class _Agent:
    def __init__(self, **kwargs):
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
        return {"final_response": "ok", "messages": [], "api_calls": 1}


def _authority(session_id="c"):
    """An authority that owns *session_id*: ``active_authority`` finds it and its store answers
    ``get_session`` with a runtime generation."""
    from gateway.session_events import SessionEvents

    return SimpleNamespace(
        sessions={session_id: SimpleNamespace(event_stream=SessionEvents())},
        db=SimpleNamespace(get_session=lambda sid: {"runtime_generation": 7}),
        logical_owner=lambda sid: "canonical-" + sid,
        publish_execution=lambda *a, **k: True,
    )


def _gateway_runner(session_id="c"):
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
    gr._resolve_session_reasoning_config.return_value = None
    gr._resolve_session_service_tier.return_value = None
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
    # ``active_authority`` reads the registry first, then the legacy single-authority attribute.
    gr.session_authorities = None
    gr.session_authority = _authority(session_id)
    return gr


def _local_ctx(agent_cls=None):
    return TurnContext(
        source=SessionSource(platform=Platform.LOCAL, chat_id="c", user_id="u"),
        message="hi", history=[], session_id="c", session_key="c", user_config={},
        AIAgent=agent_cls or _Agent, resolve_display_setting=lambda *a: False,
        _run_still_current=lambda: True, _hooks_ref=SimpleNamespace(loaded_hooks=False),
    )


def test_local_turn_installs_the_sudo_password_callback():
    """FINDINGS row 9: without this the terminal tool takes the headless ``sudo: a terminal is
    required`` path and the user is never asked."""
    from gateway.run_turn_runner import TurnRunner
    from tools.terminal_tool import _get_sudo_password_callback

    seen = {}
    before = _get_sudo_password_callback()

    class _Probe(_Agent):
        def run_conversation(self, message, **kwargs):
            seen["during_turn"] = _get_sudo_password_callback()
            return super().run_conversation(message, **kwargs)

    TurnRunner(_gateway_runner(), _local_ctx(_Probe)).run_sync()

    assert seen.get("during_turn") is not None, "no sudo callback installed for a local viewer turn"
    assert _get_sudo_password_callback() is before, "the sudo callback was not restored"


def test_local_turn_binds_the_secret_capture():
    """Without this a skill's missing env var only yields the gateway "secret entry is not
    available" hint instead of the masked prompt."""
    from gateway.run_turn_runner import TurnRunner
    from tools.skills_tool import _turn_secret_capture

    seen = {}

    class _Probe(_Agent):
        def run_conversation(self, message, **kwargs):
            seen["capture"] = _turn_secret_capture.get()
            return super().run_conversation(message, **kwargs)

    TurnRunner(_gateway_runner(), _local_ctx(_Probe)).run_sync()

    assert seen.get("capture") is not None, "secret capture was not bound for a local viewer turn"


def test_local_turn_publishes_usage_and_settled_info_at_turn_end(monkeypatch):
    """Turn end is when the classic CLI repainted its status bar and first learned the agent's
    tool list / system prompt."""
    from gateway import session_local_inventory

    published = []
    monkeypatch.setattr(session_local_inventory, "publish_settled_info",
                        lambda *a, **k: published.append("settled_info"), raising=True)
    from gateway.run_turn_runner import TurnRunner
    TurnRunner(_gateway_runner(), _local_ctx()).run_sync()

    assert published == ["settled_info"], "publish_settled_info did not run at turn end"


def test_turn_end_publication_survives_a_failing_turn(monkeypatch):
    """The turn-end publication rides a ``finally``: a turn that raises must still publish, and
    the original exception must not be masked."""
    from gateway import session_local_inventory

    published = []
    monkeypatch.setattr(session_local_inventory, "publish_settled_info",
                        lambda *a, **k: published.append("settled_info"), raising=True)

    class _Boom(_Agent):
        def run_conversation(self, _message, **_kwargs):
            raise RuntimeError("boom")

    from gateway.run_turn_runner import TurnRunner

    with pytest.raises(RuntimeError, match="boom"):
        TurnRunner(_gateway_runner(), _local_ctx(_Boom)).run_sync()
    assert published == ["settled_info"], "turn-end publication was skipped on the error path"
