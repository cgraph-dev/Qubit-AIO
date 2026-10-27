"""Registry/CLI wiring for the optional ``rust_local`` engine.

The engine is registered but never the default; when its extension is absent the
listing annotates it and a run refuses with a typed terminal event and hint. An
injected available engine still maps the port events end to end.
"""
from __future__ import annotations

import argparse
import io
import json
from contextlib import redirect_stdout

from agent import runtime_rust_local as rust_local
from agent.runtime_port import RuntimeCapabilities
from agent.runtime_registry import RuntimeRegistry, build_registry
from agent.runtime_rust_local import RustLocalRuntime
from qubit_cli.runtime_cmd import cmd_runtime


class _FakeMessage:
    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content


class _FakeEngine:
    def __init__(self, engine_key: str = "ollama", host: str | None = None) -> None:
        self.engine_key = engine_key

    def variant_key(self) -> str:
        return "fake"

    def generate(self, messages, model, temperature=0.7, max_tokens=1024):
        return json.dumps({
            "content": "RUST_OK",
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            "finish_reason": "stop",
        })


class _FakeModule:
    Engine = _FakeEngine
    Message = _FakeMessage


def _missing_extension():
    raise ModuleNotFoundError("No module named 'openjarvis_rust'")


def _run_args(**kw):
    base = dict(
        runtime_action="run", engine="rust_local", prompt="hello",
        query_file=None, json=True,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def test_build_registry_registers_rust_local_without_changing_the_default():
    # Registration never probes the extension; the default loader is lazy.
    registry = build_registry(lambda request: object())

    assert registry.available() == ["native", "openjarvis", "rust_local"]
    assert registry.get("native").name == "native"

    from qubit_cli.subcommands.runtime import build_runtime_parser

    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="command")
    build_runtime_parser(subs, cmd_runtime=lambda args: 0)
    assert getattr(parser.parse_args(["runtime", "run", "--prompt", "x"]), "engine", "native") == "native"


def test_engines_annotates_an_unavailable_rust_engine(monkeypatch):
    monkeypatch.setattr(rust_local, "_import_extension", _missing_extension)
    registry = build_registry(lambda request: object())
    out = io.StringIO()

    with redirect_stdout(out):
        code = cmd_runtime(argparse.Namespace(runtime_action="engines"), registry=registry)

    assert code == 0
    lines = out.getvalue().splitlines()
    assert "rust_local (unavailable: rust_engine_unavailable)" in lines
    assert "native" in lines


def test_run_refuses_typed_with_the_install_hint_and_exit_two(monkeypatch, capsys):
    monkeypatch.setattr(rust_local, "_import_extension", _missing_extension)
    registry = build_registry(lambda request: object())

    with redirect_stdout(io.StringIO()):
        code = cmd_runtime(_run_args(json=False), registry=registry)

    assert code == 2
    assert "openjarvis_rust" in capsys.readouterr().err


def test_refusal_is_still_the_engine_envelope_in_json(monkeypatch):
    monkeypatch.setattr(rust_local, "_import_extension", _missing_extension)
    registry = build_registry(lambda request: object())
    out = io.StringIO()

    with redirect_stdout(out):
        code = cmd_runtime(_run_args(), registry=registry)

    assert code == 2
    payload = json.loads(out.getvalue())
    assert payload["engine"] == "rust_local"
    assert len(payload["events"]) == 1
    terminal = payload["events"][0]
    assert terminal["kind"] == "error"
    assert terminal["data"]["engine"] == "rust_local"
    assert terminal["data"]["refusal"] == "rust_engine_unavailable"
    assert terminal["data"]["hint"]


def test_injected_available_engine_maps_events_through_the_cli():
    backend = RustLocalRuntime(module_loader=lambda: _FakeModule(), model="fake-model")
    assert backend.capabilities == RuntimeCapabilities(False, False, False, False)
    registry = RuntimeRegistry()
    registry.register(backend)
    out = io.StringIO()

    with redirect_stdout(out):
        code = cmd_runtime(_run_args(), registry=registry)

    assert code == 0
    payload = json.loads(out.getvalue())
    assert payload["engine"] == "rust_local"
    assert [event["kind"] for event in payload["events"]] == ["delta", "usage", "done"]
    assert payload["events"][0]["text"] == "RUST_OK"
    assert payload["events"][1]["data"] == {
        "prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5,
    }
    assert payload["events"][-1]["data"]["engine"] == "rust_local"
    assert payload["events"][-1]["data"]["model"] == "fake-model"
    assert not any(event["kind"] == "error" for event in payload["events"])


def test_unknown_engine_refuses_and_lists_the_registered_engines(capsys):
    backend = RustLocalRuntime(module_loader=lambda: _FakeModule(), model="fake-model")
    registry = RuntimeRegistry()
    registry.register(backend)

    with redirect_stdout(io.StringIO()):
        code = cmd_runtime(_run_args(engine="nope"), registry=registry)

    assert code == 2
    assert "rust_local" in capsys.readouterr().err
