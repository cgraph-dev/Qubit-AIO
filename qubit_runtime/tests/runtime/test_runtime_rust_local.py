"""Rust-local backend: lazy optional extension, typed refusal, event mapping."""
from __future__ import annotations

import ast
import json
import socket
from pathlib import Path
from typing import Any

import pytest

import agent.runtime_rust_local as rust_local
from agent.runtime_port import RuntimeRefusalError, TurnRequest
from agent.runtime_rust_local import (
    ENGINE_ENV,
    EXTENSION_MODULE,
    HOST_ENV,
    MODEL_ENV,
    RustLocalRuntime,
    build_rust_local_runtime,
)


class _FakeMessage:
    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content


class _FakeEngine:
    instances: list["_FakeEngine"] = []
    error: Exception | None = None
    raw: Any = None

    def __init__(self, engine_key: str = "ollama", host: str | None = None) -> None:
        self.engine_key = engine_key
        self.host = host
        self.calls: list[dict[str, Any]] = []
        _FakeEngine.instances.append(self)

    def variant_key(self) -> str:
        return "fake"

    def generate(
        self,
        messages: list[_FakeMessage],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> Any:
        self.calls.append({
            "messages": [(m.role, m.content) for m in messages],
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
        })
        if _FakeEngine.error is not None:
            raise _FakeEngine.error
        if _FakeEngine.raw is not None:
            return _FakeEngine.raw
        return json.dumps({
            "content": "RUST_OK",
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            "model": model,
            "finish_reason": "stop",
        })


class _FakeModule:
    Engine = _FakeEngine
    Message = _FakeMessage


class _LegacyModule:
    OllamaEngine = _FakeEngine
    Message = _FakeMessage


def _request(prompt: str = "hello") -> TurnRequest:
    return TurnRequest(prompt=prompt, session_id="s-rust")


def _run(backend: RustLocalRuntime, prompt: str = "hello"):
    return list(backend.run_turn(_request(prompt)))


def _assert_single_refusal(events: list[Any]) -> None:
    assert len(events) == 1
    assert events[0].kind == "error"
    assert events[0].data["refusal"] == "rust_engine_unavailable"
    assert events[0].data["hint"]
    assert events[0].data["engine"] == "rust_local"
    assert not any(event.kind in ("done", "delta") for event in events)


@pytest.fixture(autouse=True)
def _reset_fake_engine():
    _FakeEngine.instances.clear()
    _FakeEngine.error = None
    _FakeEngine.raw = None
    yield
    _FakeEngine.instances.clear()
    _FakeEngine.error = None
    _FakeEngine.raw = None


# ------------------------------------------------------------------- refusals


def test_missing_extension_is_exactly_one_typed_terminal_refusal(monkeypatch):
    def _missing(*args, **kwargs):
        raise ModuleNotFoundError("No module named 'openjarvis_rust'")

    monkeypatch.setattr(rust_local.importlib, "import_module", _missing)
    backend = RustLocalRuntime(model="fake-model")

    events = _run(backend)

    _assert_single_refusal(events)
    assert EXTENSION_MODULE in events[0].text
    assert "QUBIT_RUST_LOCAL_MODEL" in events[0].data["hint"]


def test_resolve_raises_the_same_typed_refusal(monkeypatch):
    monkeypatch.setattr(
        rust_local.importlib, "import_module",
        lambda *a, **k: (_ for _ in ()).throw(ModuleNotFoundError("missing")),
    )

    with pytest.raises(RuntimeRefusalError) as exc:
        RustLocalRuntime(model="fake-model").resolve()

    assert exc.value.code == "rust_engine_unavailable"
    assert exc.value.hint


def test_extension_without_engine_is_refused():
    class _NoEngine:
        Message = _FakeMessage

    backend = RustLocalRuntime(module_loader=lambda: _NoEngine(), model="fake-model")

    _assert_single_refusal(_run(backend))


def test_loader_failure_is_a_typed_refusal():
    def _broken():
        raise ImportError("libopenjarvis_rust.so is missing")

    backend = RustLocalRuntime(module_loader=_broken, model="fake-model")

    _assert_single_refusal(_run(backend))


def test_extension_without_message_is_refused():
    class _NoMessage:
        Engine = _FakeEngine

    backend = RustLocalRuntime(module_loader=lambda: _NoMessage(), model="fake-model")

    _assert_single_refusal(_run(backend))


def test_engine_construction_failure_is_refused():
    class _BrokenEngine:
        def __init__(self, engine_key: str = "ollama", host: str | None = None) -> None:
            raise ValueError("unknown engine: nope")

    class _Module:
        Engine = _BrokenEngine
        Message = _FakeMessage

    backend = RustLocalRuntime(
        module_loader=lambda: _Module(), model="fake-model", engine_key="nope",
    )

    _assert_single_refusal(_run(backend))


def test_missing_model_is_a_typed_refusal(monkeypatch):
    for key in (MODEL_ENV, ENGINE_ENV, HOST_ENV):
        monkeypatch.delenv(key, raising=False)
    backend = RustLocalRuntime(module_loader=lambda: _FakeModule())

    events = _run(backend)

    _assert_single_refusal(events)
    assert "model" in events[0].data["hint"].lower()


# --------------------------------------------------------------- capabilities


def test_name_capabilities_and_factory():
    backend = build_rust_local_runtime(module_loader=lambda: _FakeModule())

    assert backend.name == "rust_local"
    assert backend.capabilities.streaming is False
    assert backend.capabilities.cancel is False
    assert backend.capabilities.tools is False
    assert backend.capabilities.images is False


def test_import_is_lazy_and_resolution_is_cached():
    calls = {"n": 0}

    def _loader():
        calls["n"] += 1
        return _FakeModule()

    backend = RustLocalRuntime(module_loader=_loader, model="fake-model")

    assert calls["n"] == 0
    first = backend.resolve()
    second = backend.resolve()
    assert first is second
    assert calls["n"] == 1


def test_no_in_process_openjarvis_import_at_module_import_time():
    tree = ast.parse(Path(rust_local.__file__).read_text(encoding="utf-8"))
    top_level_imports: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level_imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level_imports.append(node.module)

    assert not any(name.startswith("openjarvis") for name in top_level_imports)
    assert "runtime_native" not in top_level_imports


# ------------------------------------------------------------------ happy path


def test_fake_available_engine_maps_events_and_messages(monkeypatch):
    monkeypatch.delenv(MODEL_ENV, raising=False)
    backend = RustLocalRuntime(
        module_loader=lambda: _FakeModule(),
        engine_key="ollama",
        host="http://127.0.0.1:1",
        model="fake-model",
        temperature=0.2,
        max_tokens=64,
    )
    request = TurnRequest(
        prompt="hello now",
        session_id="s-rust",
        system="be terse",
        history=(
            {"role": "user", "content": "earlier"},
            {"role": "assistant", "content": "earlier answer"},
        ),
    )

    events = list(backend.run_turn(request))

    assert [event.kind for event in events] == ["delta", "usage", "done"]
    assert events[0].text == "RUST_OK"
    assert events[1].data == {
        "prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5,
    }
    assert events[-1].text == "RUST_OK"
    assert events[-1].data == {
        "completed": True,
        "engine": "rust_local",
        "backend": "rust",
        "engine_key": "ollama",
        "model": "fake-model",
        "variant": "fake",
        "finish_reason": "stop",
    }
    assert len(_FakeEngine.instances) == 1
    engine = _FakeEngine.instances[0]
    assert engine.engine_key == "ollama"
    assert engine.host == "http://127.0.0.1:1"
    assert engine.calls == [{
        "messages": [
            ("system", "be terse"),
            ("user", "earlier"),
            ("assistant", "earlier answer"),
            ("user", "hello now"),
        ],
        "model": "fake-model",
        "temperature": 0.2,
        "max_tokens": 64,
    }]


def test_legacy_ollama_engine_module_is_usable():
    backend = RustLocalRuntime(
        module_loader=lambda: _LegacyModule(), model="fake-model", host="http://127.0.0.1:2",
    )

    events = _run(backend)

    assert [event.kind for event in events] == ["delta", "usage", "done"]
    assert events[-1].data["engine_key"] == "ollama"
    assert _FakeEngine.instances[0].host == "http://127.0.0.1:2"


def test_error_payload_is_terminal_and_does_not_claim_completion():
    _FakeEngine.raw = json.dumps({"error": "upstream failed", "content": "partial"})
    backend = RustLocalRuntime(module_loader=lambda: _FakeModule(), model="fake-model")

    events = _run(backend)

    assert [event.kind for event in events] == ["delta", "error"]
    assert events[0].text == "partial"
    assert events[-1].data["engine"] == "rust_local"
    assert "refusal" not in events[-1].data


def test_malformed_result_is_an_error_not_a_done():
    _FakeEngine.raw = "not json"
    backend = RustLocalRuntime(module_loader=lambda: _FakeModule(), model="fake-model")

    events = _run(backend)

    assert [event.kind for event in events] == ["error"]
    assert "malformed" in events[0].text.lower()


# --------------------------------------------------------------- safety checks


def test_adapter_makes_no_network_calls(monkeypatch):
    def _no_network(*args, **kwargs):
        raise AssertionError("the adapter must not open sockets")

    monkeypatch.setattr(socket, "socket", _no_network)
    backend = RustLocalRuntime(module_loader=lambda: _FakeModule(), model="fake-model")

    events = _run(backend)

    assert [event.kind for event in events] == ["delta", "usage", "done"]


def test_engine_errors_do_not_leak_secrets():
    secret = "sk-" + "a" * 32
    _FakeEngine.error = RuntimeError(f"provider rejected key {secret}")
    backend = RustLocalRuntime(module_loader=lambda: _FakeModule(), model="fake-model")

    events = _run(backend)

    assert [event.kind for event in events] == ["error"]
    rendered = json.dumps(
        {"text": events[0].text, "data": events[0].data}, ensure_ascii=False, default=str,
    )
    assert secret not in rendered
    assert "a" * 32 not in rendered
