"""RuntimePort engine selection on the ``qubit chat -q`` single-query path.

The classic ``-q``/``-Q`` path keeps its agent, output and exit contract; the
turn itself crosses the RuntimePort with the native backend. A non-native engine
never builds ``AIAgent``: it runs the bounded P2 turn through the port, refuses
native-only flags, and reports a typed refusal instead of falling back to native.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

import cli
from agent.runtime_port import RuntimeCapabilities, TurnEvent, TurnRequest


class _FakeBackend:
    name = "fake"
    capabilities = RuntimeCapabilities(streaming=True, cancel=False, tools=False, images=False)

    def __init__(self, events, *, name="openjarvis"):
        self.events = list(events)
        self.requests = []
        self.name = name

    def run_turn(self, request, cancel=None):
        self.requests.append(request)
        yield from self.events


def _patch_registry(monkeypatch, backend):
    import agent.runtime_registry as registry_mod
    from agent.runtime_registry import RuntimeRegistry

    def fake_build_registry(factory, *, native_stream=True):
        registry = RuntimeRegistry()
        registry.register(backend)
        return registry

    monkeypatch.setattr(registry_mod, "build_registry", fake_build_registry)


def _chat_frame(mode: str):
    stop = {"called": False}

    class _Agent:
        session_id = "s-1"

        def run_conversation(self, *args, **kwargs):
            stop["called"] = True
            raise AssertionError("the fake backend must own the turn")

    self = SimpleNamespace(
        agent=_Agent(),
        _single_query_mode=True,
        conversation_history=[{"role": "user", "content": "hi"}],
        session_id="s-1",
        _sudo_password_callback=None,
        _approval_callback=None,
        _secret_capture_callback=None,
        _vault_unlock_callback=None,
        _vault_save_login_callback=None,
        _vault_code_callback=None,
        _pending_moa_config=None,
        _flush_credit_notices=lambda: None,
    )
    turn = SimpleNamespace(voice_prefix="", mute_notification_reply=False,
                           stream_callback=(lambda text: None) if mode == "stream" else None)
    return self, turn, stop


def test_chat_parser_defaults_to_the_native_engine():
    from qubit_cli._parser import build_top_level_parser

    parser, _subparsers, _chat = build_top_level_parser()

    assert parser.parse_args(["chat", "-q", "hi"]).engine == "native"
    assert parser.parse_args(["chat", "-q", "hi", "--engine", "native"]).engine == "native"
    assert parser.parse_args(["chat", "-q", "hi", "--engine", "openjarvis"]).engine == "openjarvis"


def test_native_chat_single_query_runs_its_turn_through_the_port(monkeypatch):
    import qubit_cli.oneshot as oneshot

    seen = {}

    def fake_run(agent, prompt, **kwargs):
        seen.update(agent=agent, prompt=prompt, **kwargs)
        return "ok", {"final_response": "ok", "completed": True}

    monkeypatch.setattr(oneshot, "run_agent_through_port", fake_run)
    self, turn, _ = _chat_frame("stream")

    from qubit_cli.cli_chat_turn_mixin import CLIChatTurnMixin

    CLIChatTurnMixin._chat_run_agent(self, turn, "hi")

    assert seen["prompt"] == "hi"
    assert seen["task_id"] == "s-1"
    assert seen["history"] == []
    assert seen["stream"] is turn.stream_callback
    assert seen["extra_kwargs"] == {"persist_user_message": None, "moa_config": None}
    assert turn.result["final_response"] == "ok"


def test_quiet_single_query_runs_the_native_agent_through_the_port(monkeypatch):
    backend = _FakeBackend(
        [TurnEvent("done", text="ported", data={"completed": True})], name="native")
    _patch_registry(monkeypatch, backend)
    monkeypatch.setattr("tools.process_registry.process_registry.wait_for_pending_completions",
                        lambda *a, **k: {"waited": [], "completed": [], "timed_out": []})

    class _Agent:
        session_id = "s-1"

        def run_conversation(self, *args, **kwargs):
            raise AssertionError("the port must own the turn")

    stub = SimpleNamespace(agent=_Agent(), conversation_history=[], session_id="s-1",
                           _quiet_notify_linger_done=False)

    with pytest.raises(SystemExit) as exc:
        cli._run_quiet_single_query(stub, "hello")

    assert exc.value.code == 0
    assert backend.requests[0].prompt == "hello"


def test_native_port_helper_forwards_deltas_and_returns_the_raw_result(monkeypatch):
    import qubit_cli.oneshot as oneshot

    backend = _FakeBackend([
        TurnEvent("delta", text="Hel"),
        TurnEvent("delta", text="lo"),
        TurnEvent("done", text="Hello", data={"completed": True}),
    ], name="native")
    _patch_registry(monkeypatch, backend)
    sink: list[str] = []
    agent = SimpleNamespace(interrupt=lambda *a, **k: None)

    text, result = oneshot.run_agent_through_port(agent, "hi", session_id="s-1", stream=sink.append)

    assert text == "Hello"
    assert result["final_response"] == "Hello"
    assert sink == ["Hel", "lo"]
    assert backend.requests[0].prompt == "hi"


def test_native_port_helper_interrupts_the_agent_on_ctrl_c(monkeypatch):
    import qubit_cli.oneshot as oneshot

    class _InterruptingBackend:
        name = "native"
        capabilities = RuntimeCapabilities(streaming=True, cancel=True, tools=True, images=False)

        def run_turn(self, request, cancel=None):
            raise KeyboardInterrupt
            yield  # pragma: no cover - generator shape

    _patch_registry(monkeypatch, _InterruptingBackend())
    monkeypatch.setattr(oneshot, "_PORT_CANCEL_GRACE_SECONDS", 0.01)
    seen = []
    agent = SimpleNamespace(interrupt=lambda *a, **k: seen.append((a, k)))

    with pytest.raises(KeyboardInterrupt):
        oneshot.run_agent_through_port(agent, "hi", session_id="s-1")

    assert seen, "the worker agent is asked to stop before the caller unwinds"


def test_quiet_single_query_maps_a_failed_port_turn_to_one(monkeypatch):
    backend = _FakeBackend(
        [TurnEvent("error", text="provider exploded", data={"failed": True})], name="native")
    _patch_registry(monkeypatch, backend)
    monkeypatch.setattr("tools.process_registry.process_registry.wait_for_pending_completions",
                        lambda *a, **k: {"waited": [], "completed": [], "timed_out": []})

    class _Agent:
        session_id = "s-1"

        def run_conversation(self, *args, **kwargs):
            raise AssertionError("the port must own the turn")

    stub = SimpleNamespace(agent=_Agent(), conversation_history=[], session_id="s-1",
                           _quiet_notify_linger_done=False)

    with pytest.raises(SystemExit) as exc:
        cli._run_quiet_single_query(stub, "hello")

    assert exc.value.code == 1


def test_non_native_chat_deltas_and_final_reach_output(monkeypatch, capsys):
    backend = _FakeBackend([
        TurnEvent("delta", text="Hel"),
        TurnEvent("delta", text="lo"),
        TurnEvent("done", text="Hello", data={"completed": True}),
    ])
    _patch_registry(monkeypatch, backend)

    from qubit_cli.single_query_engine import run_non_native_single_query

    assert run_non_native_single_query("hi", engine="openjarvis") == 0

    out = capsys.readouterr()
    assert out.out == "Hello\n"
    assert out.err == ""
    assert backend.requests[0].prompt == "hi"


def test_non_native_chat_failed_turn_exits_one_with_its_error(monkeypatch, capsys):
    backend = _FakeBackend([TurnEvent("error", text="OpenJarvis exploded", data={"failed": True})])
    _patch_registry(monkeypatch, backend)

    from qubit_cli.single_query_engine import run_non_native_single_query

    assert run_non_native_single_query("hi", engine="openjarvis", quiet=True) == 1

    err = capsys.readouterr().err
    assert "OpenJarvis exploded" in err


def test_non_native_chat_interrupted_turn_exits_130(monkeypatch, capsys):
    backend = _FakeBackend([
        TurnEvent("error", text="cancelled", data={"interrupted": True, "cancelled": True}),
    ])
    _patch_registry(monkeypatch, backend)

    from qubit_cli.single_query_engine import run_non_native_single_query

    assert run_non_native_single_query("hi", engine="openjarvis", quiet=True) == 130


def test_non_native_chat_unknown_engine_exits_two(capsys):
    from qubit_cli.single_query_engine import run_non_native_single_query

    assert run_non_native_single_query("hi", engine="does-not-exist") == 2

    err = capsys.readouterr().err
    assert "Unknown runtime engine" in err
    assert "native" in err


def test_non_native_chat_refuses_native_only_flags(capsys):
    from qubit_cli.single_query_engine import run_non_native_single_query

    assert run_non_native_single_query(
        "hi", engine="openjarvis", flags=["--model", "--toolsets"]) == 2

    err = capsys.readouterr().err
    assert "--engine openjarvis does not accept --model, --toolsets" in err
    assert "QUBIT_OPENJARVIS_MODEL" in err


def test_non_native_chat_openjarvis_refuses_without_building_native(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("QUBIT_OPENJARVIS_BIN", str(tmp_path / "missing-jarvis"))
    monkeypatch.setenv("QUBIT_OPENJARVIS_SOURCE", str(tmp_path / "missing-source"))
    monkeypatch.setenv("QUBIT_OPENJARVIS_HOME", str(tmp_path / "openjarvis-home"))

    def _no_native_fallback(**_kwargs):
        raise AssertionError("native AIAgent must not be constructed on an engine refusal")

    monkeypatch.setattr("run_agent.AIAgent", _no_native_fallback)

    from qubit_cli.single_query_engine import run_non_native_single_query

    assert run_non_native_single_query("hi", engine="openjarvis") == 2

    err = capsys.readouterr().err
    assert "openjarvis_unavailable" in err
    assert "QUBIT_OPENJARVIS_BIN" in err


def test_non_native_chat_stream_json_emits_deltas_and_result(monkeypatch, capsys):
    backend = _FakeBackend([
        TurnEvent("delta", text="Hello"),
        TurnEvent("done", text="Hello", data={"completed": True}),
    ])
    _patch_registry(monkeypatch, backend)

    from qubit_cli.single_query_engine import run_non_native_single_query

    assert run_non_native_single_query("hi", engine="openjarvis", stream_json=True) == 0

    import json

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert [line["type"] for line in lines] == ["system", "text", "result"]
    assert lines[1]["text"] == "Hello"
    assert lines[2]["text"] == "Hello"


def test_native_only_flags_lists_only_explicit_values():
    from qubit_cli.single_query_engine import native_only_flags

    assert native_only_flags(SimpleNamespace()) == ()
    args = SimpleNamespace(model="m", provider="p", reasoning="high", toolsets="web",
                           base_url=None, api_key=None, max_turns=None, run_budget=None,
                           checkpoints=False, ignore_rules=False, safe_mode=False,
                           ignore_user_config=False, yolo=False, accept_hooks=False)
    assert native_only_flags(args) == ("--model", "--provider", "--reasoning", "--toolsets")


def _cmd_chat_args(**overrides):
    values = dict(engine="openjarvis", query="hi", query_file=None, image=None,
                  quiet=False, oneshot_exit=True, output_format="text", resume=None,
                  skills=None, tui=False, model=None, toolsets=None)
    values.update(overrides)
    return SimpleNamespace(**values)


def _patch_cmd_chat_prereqs(monkeypatch, main_mod):
    monkeypatch.setattr(main_mod, "_apply_safe_mode", lambda args: None)
    monkeypatch.setattr(main_mod, "_apply_user_config_bypass", lambda args: None)
    monkeypatch.setattr(main_mod, "_guard_noninteractive_user_config", lambda args: None)
    monkeypatch.setattr("qubit_cli.stream_json.stream_json_requested", lambda args: False)
    monkeypatch.setattr(main_mod, "_bypass_chat_launch", lambda args: False)
    monkeypatch.setattr(main_mod, "_resolve_use_tui", lambda args: False)
    monkeypatch.setattr(main_mod, "_resolve_chat_session_args", lambda args, use_tui: None)
    monkeypatch.setattr(main_mod, "_read_query_file", lambda args: None)
    monkeypatch.setattr(cli, "_should_seed_interactive", lambda *a, **k: False)
    monkeypatch.setattr(main_mod, "_warn_retired_xai_models", lambda: None)
    monkeypatch.setattr("qubit_cli.free_tier_bootstrap.run_bootstrap", lambda **k: None)
    monkeypatch.setattr(main_mod, "_has_any_provider_configured", lambda: True)
    monkeypatch.setattr(main_mod, "_start_chat_background_prefetch", lambda: None)
    monkeypatch.setattr(main_mod, "_pin_kanban_board_env", lambda: None)
    monkeypatch.setattr(
        "qubit_cli.observability.shared_metrics_consent.offer_consent_before_chat",
        lambda args: None)
    monkeypatch.setattr(main_mod, "_confirm_startup_expensive_model_override", lambda args: None)


def test_cmd_chat_dispatches_a_non_native_single_query_to_the_port(monkeypatch):
    import qubit_cli.main as main_mod
    import qubit_cli.single_query_engine as engine_mod

    _patch_cmd_chat_prereqs(monkeypatch, main_mod)
    seen = {}
    monkeypatch.setattr(
        engine_mod, "run_non_native_single_query",
        lambda prompt, **kwargs: seen.update(prompt=prompt, **kwargs) or 0,
    )

    with pytest.raises(SystemExit) as exc:
        main_mod.cmd_chat(_cmd_chat_args(model="m", toolsets="web"))

    assert exc.value.code == 0
    assert seen["prompt"] == "hi"
    assert seen["engine"] == "openjarvis"
    assert seen["stream_json"] is False
    assert "--model" in seen["flags"] and "--toolsets" in seen["flags"]


def test_cmd_chat_native_keeps_the_classic_cli_path(monkeypatch):
    import qubit_cli.main as main_mod
    import qubit_cli.single_query_engine as engine_mod

    _patch_cmd_chat_prereqs(monkeypatch, main_mod)
    monkeypatch.setattr(
        engine_mod, "run_non_native_single_query",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("native must not take the port runner")),
    )
    seen = {}
    monkeypatch.setattr("cli.main", lambda **kwargs: seen.update(kwargs))
    monkeypatch.setattr(
        "qubit_cli.observability.shared_metrics_process.begin_process", lambda *a, **k: None)

    main_mod.cmd_chat(_cmd_chat_args(engine="native", quiet=True))

    assert seen["query"] == "hi"

