# Phase 1 — RuntimePort + Native Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a typed `RuntimePort` and a native adapter that runs one turn through today's `AIAgent` unchanged, plus a diagnostic `qubit runtime` CLI. No product caller changes.

**Architecture:** `agent/runtime_port.py` defines request/event/capability types and the backend protocol. `agent/runtime_native.py` wraps an injected agent factory: it maps `stream_callback` deltas, the result envelope and `agent.interrupt(...)` cancellation into ordered port events. `agent/runtime_registry.py` selects a backend by name. `qubit runtime` is a thin diagnostic caller.

**Tech Stack:** Python 3.12, existing `AIAgent`/`run_conversation`, `stream_callback`, `agent.interrupt`, argparse subcommands, pytest.

**Spec:** `docs/superpowers/specs/2026-10-26-runtime-boundary-and-openjarvis-design.md`

**Program plan:** `docs/superpowers/plans/2026-10-26-runtime-boundary-and-openjarvis-program.md`

## Global Constraints

- Start only after the program's P0 sign-off gate is recorded in `docs/STATUS.md`.
- Default engine stays `native`; no change to chat/oneshot/gateway behaviour in this phase.
- Do not import any OpenJarvis code; this phase is native only.
- Exactly one terminal event per turn: `done` or `error`, never both.
- Event payloads are bounded plain data; never include API keys, tokens or full request/response bodies.
- Focused checks only while building; one focused matrix at the end of the phase.
- Commit each task; one `git worktree` for the implementer.

## Review Focus

- **Blocked turn:** a fake agent that never returns must not hang the caller forever; the cancel path must emit a terminal `error` and the adapter must not leak a live thread (daemon is acceptable, but exactly one terminal event is mandatory).
- **Double terminal:** a result that is both `completed=False` and carries an `error` must emit one `error`, never `done` + `error`.
- **Redaction:** a result containing a credential-shaped string must not put it in `TurnEvent.data`; only bounded identity fields are copied.
- **Unknown engine:** `qubit runtime run --engine nope` exits non-zero with the registered list, not a traceback.
- **Missing prompt/query file:** the CLI refuses with a typed message before building an agent.

---

### Task 1: Port types + the backend contract

**Files:**
- Create: `qubit_runtime/agent/runtime_port.py`
- Test: `qubit_runtime/tests/runtime/test_runtime_port.py`

**Interfaces:**
- Produces: `TurnRequest(prompt, session_id, history=(), system=None, task_id=None)`,
  `TurnEvent(kind, text="", data={})`, `RuntimeCapabilities(streaming, cancel, tools, images)`,
  `RuntimeBackend` protocol, `UnknownRuntimeError`.

- [ ] **Step 1: Write the failing test**

```python
# qubit_runtime/tests/runtime/test_runtime_port.py
from agent.runtime_port import (
    RuntimeBackend, RuntimeCapabilities, TurnEvent, TurnRequest, UnknownRuntimeError,
)


def test_request_defaults_are_immutable():
    req = TurnRequest(prompt="hi", session_id="s1")
    assert req.history == ()
    assert req.system is None
    with pytest.raises(Exception):
        req.prompt = "changed"  # frozen dataclass


def test_event_payload_is_plain_and_bounded():
    ev = TurnEvent("done", text="ok", data={"completed": True})
    assert ev.kind == "done" and ev.data["completed"] is True


def test_protocol_is_satisfied_by_a_fake_backend():
    class Fake:
        name = "fake"
        capabilities = RuntimeCapabilities(streaming=False, cancel=False, tools=False, images=False)
        def run_turn(self, request, cancel=None):
            yield TurnEvent("delta", text="a")
            yield TurnEvent("done", text="a")

    assert isinstance(Fake(), RuntimeBackend)


def test_unknown_runtime_error_is_a_value_error():
    assert issubclass(UnknownRuntimeError, ValueError)
```

Add `import pytest` at the top.

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd qubit_runtime && TMPDIR=/tmp/opencode/p1-tmp <venv-python> -m pytest tests/runtime/test_runtime_port.py -q --basetemp=/tmp/opencode/p1-base`
Expected: `ModuleNotFoundError: agent.runtime_port`.

- [ ] **Step 3: Implement the types**

```python
# qubit_runtime/agent/runtime_port.py
"""Engine-neutral runtime boundary: one turn in, an ordered event stream out.

Product surfaces depend on this module, never on a concrete engine. The native
adapter wraps today's ``AIAgent``; later phases add out-of-process engines.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Literal, Protocol, runtime_checkable

EventKind = Literal["delta", "tool", "usage", "done", "error"]


class UnknownRuntimeError(ValueError):
    """No backend is registered under this name."""

    def __init__(self, name: str, available: list[str]) -> None:
        self.name = name
        self.available = available
        super().__init__(
            f"Unknown runtime engine {name!r}; registered: {', '.join(available) or '(none)'}"
        )


@dataclass(frozen=True)
class RuntimeCapabilities:
    streaming: bool = True
    cancel: bool = True
    tools: bool = True
    images: bool = False


@dataclass(frozen=True)
class TurnRequest:
    prompt: str
    session_id: str
    history: tuple[dict[str, Any], ...] = ()
    system: str | None = None
    task_id: str | None = None


@dataclass(frozen=True)
class TurnEvent:
    kind: EventKind
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class RuntimeBackend(Protocol):
    name: str
    capabilities: RuntimeCapabilities

    def run_turn(self, request: TurnRequest, cancel: Any | None = None) -> Iterator[TurnEvent]:
        """Yield ordered events with exactly one terminal ``done`` or ``error``."""
        ...
```

- [ ] **Step 4: Run it and confirm it passes**

Run: `cd qubit_runtime && TMPDIR=/tmp/opencode/p1-tmp <venv-python> -m pytest tests/runtime/test_runtime_port.py -q --basetemp=/tmp/opencode/p1-base`
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add qubit_runtime/agent/runtime_port.py qubit_runtime/tests/runtime/test_runtime_port.py
git commit -m "feat(runtime): add the engine-neutral RuntimePort types"
```

### Task 2: Native adapter — deltas, done and error mapping

**Files:**
- Create: `qubit_runtime/agent/runtime_native.py`
- Test: `qubit_runtime/tests/runtime/test_runtime_native.py`

**Interfaces:**
- Consumes: `TurnRequest`, `TurnEvent`, `RuntimeCapabilities` from Task 1.
- Produces: `NativeRuntime(agent_factory)` with `name == "native"` and
  `run_turn(request, cancel=None)` yielding `delta*` then exactly one `done|error`.

- [ ] **Step 1: Write the failing test**

```python
# qubit_runtime/tests/runtime/test_runtime_native.py
import threading

from agent.runtime_native import NativeRuntime
from agent.runtime_port import TurnEvent, TurnRequest


class _FakeAgent:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def run_conversation(self, message, **kwargs):
        self.calls.append((message, kwargs))
        callback = kwargs.get("stream_callback")
        if callback:
            callback("Hel")
            callback("lo")
        return self.result


def _collect(backend, request, cancel=None):
    return list(backend.run_turn(request, cancel))


def test_completed_turn_emits_deltas_then_one_done():
    agent = _FakeAgent({"completed": True, "final_response": "Hello", "turn_id": "t1"})
    backend = NativeRuntime(lambda request: agent)

    events = _collect(backend, TurnRequest(prompt="hi", session_id="s1"))

    assert [e.kind for e in events] == ["delta", "delta", "done"]
    assert "".join(e.text for e in events if e.kind == "delta") == "Hello"
    assert events[-1].text == "Hello"
    assert events[-1].data["turn_id"] == "t1"
    assert agent.calls[0][0] == "hi"


def test_failed_turn_emits_one_error():
    agent = _FakeAgent({"completed": False, "error": "provider rejected the request"})
    events = _collect(NativeRuntime(lambda request: agent), TurnRequest(prompt="hi", session_id="s1"))

    assert [e.kind for e in events] == ["delta", "delta", "error"]
    assert "provider rejected" in events[-1].text


def test_exception_emits_one_error():
    class _Boom:
        def run_conversation(self, message, **kwargs):
            raise RuntimeError("boom")

    events = _collect(NativeRuntime(lambda request: _Boom()), TurnRequest(prompt="hi", session_id="s1"))
    assert [e.kind for e in events] == ["error"]
    assert "boom" in events[-1].text


def test_result_payload_is_not_copied_wholesale():
    agent = _FakeAgent({"completed": True, "final_response": "ok", "api_key": "sk-secret"})
    events = _collect(NativeRuntime(lambda request: agent), TurnRequest(prompt="hi", session_id="s1"))
    assert "sk-secret" not in repr(events[-1].data)
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd qubit_runtime && TMPDIR=/tmp/opencode/p1-tmp <venv-python> -m pytest tests/runtime/test_runtime_native.py -q --basetemp=/tmp/opencode/p1-base`
Expected: `ModuleNotFoundError: agent.runtime_native`.

- [ ] **Step 3: Implement the adapter**

```python
# qubit_runtime/agent/runtime_native.py
"""Native (vendored AIAgent) RuntimePort adapter.

The caller supplies the agent factory; this module owns only the turn/event
mapping so later engines share one contract.
"""
from __future__ import annotations

import queue
import threading
from typing import Any, Callable, Iterator

from agent.runtime_port import RuntimeCapabilities, TurnEvent, TurnRequest

_TERMINAL = ("done", "error")


class NativeRuntime:
    name = "native"
    capabilities = RuntimeCapabilities(streaming=True, cancel=True, tools=True, images=True)

    def __init__(self, agent_factory: Callable[[TurnRequest], Any]) -> None:
        self._agent_factory = agent_factory

    def run_turn(self, request: TurnRequest, cancel: Any | None = None) -> Iterator[TurnEvent]:
        events: "queue.Queue[TurnEvent | None]" = queue.Queue()
        agent = self._agent_factory(request)

        def _emit(event: TurnEvent) -> None:
            events.put(event)

        def _watch_cancel() -> None:
            if cancel is None:
                return
            cancel.wait()
            try:
                agent.interrupt("runtime port cancel", hard_cancel=True, tool_reason="runtime_port.cancel")
            except Exception:
                pass

        if cancel is not None:
            threading.Thread(target=_watch_cancel, daemon=True, name="runtime-port-cancel").start()

        def _run() -> None:
            try:
                result = agent.run_conversation(
                    request.prompt,
                    system_message=request.system,
                    conversation_history=list(request.history) or None,
                    task_id=request.task_id,
                    stream_callback=lambda chunk: _emit(TurnEvent("delta", text=str(chunk))),
                )
                result = result if isinstance(result, dict) else {}
                if result.get("completed") is False:
                    _emit(TurnEvent(
                        "error",
                        text=str(result.get("error") or "turn did not complete"),
                        data={"interrupted": bool(result.get("interrupted"))},
                    ))
                else:
                    _emit(TurnEvent(
                        "done",
                        text=str(result.get("final_response") or ""),
                        data={
                            "completed": True,
                            "turn_id": result.get("turn_id"),
                            "interrupted": bool(result.get("interrupted")),
                        },
                    ))
            except Exception as exc:  # noqa: BLE001 - a turn failure is an event, not a crash
                _emit(TurnEvent("error", text=f"{type(exc).__name__}: {exc}"))
            finally:
                events.put(None)

        threading.Thread(target=_run, daemon=True, name="runtime-port-turn").start()
        while True:
            event = events.get()
            if event is None:
                return
            yield event
```

- [ ] **Step 4: Run it and confirm it passes**

Run: `cd qubit_runtime && TMPDIR=/tmp/opencode/p1-tmp <venv-python> -m pytest tests/runtime/test_runtime_native.py -q --basetemp=/tmp/opencode/p1-base`
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add qubit_runtime/agent/runtime_native.py qubit_runtime/tests/runtime/test_runtime_native.py
git commit -m "feat(runtime): add the native RuntimePort adapter"
```

### Task 3: Native cancellation

**Files:**
- Modify: `qubit_runtime/agent/runtime_native.py`
- Test: `qubit_runtime/tests/runtime/test_runtime_native.py`

**Interfaces:**
- Consumes: `NativeRuntime` from Task 2.
- Produces: on `cancel.set()`, `agent.interrupt(...)` is called and the turn
  emits exactly one terminal `error` with `data["interrupted"] is True`.

- [ ] **Step 1: Write the failing test**

```python
def test_cancel_interrupts_the_agent_and_emits_one_error():
    started = threading.Event()
    release = threading.Event()

    class _BlockingAgent:
        def __init__(self):
            self.interrupted = False

        def interrupt(self, message=None, **kwargs):
            self.interrupted = True
            release.set()
            return True

        def run_conversation(self, message, **kwargs):
            started.set()
            release.wait(5)
            return {"completed": False, "interrupted": self.interrupted, "error": "cancelled"}

    agent = _BlockingAgent()
    cancel = threading.Event()
    events = []

    backend = NativeRuntime(lambda request: agent)
    consumer = threading.Thread(
        target=lambda: events.extend(backend.run_turn(
            TurnRequest(prompt="hi", session_id="s1"), cancel=cancel)),
        daemon=True,
    )
    consumer.start()
    assert started.wait(2)
    cancel.set()
    consumer.join(5)

    assert agent.interrupted is True
    terminals = [e for e in events if e.kind in ("done", "error")]
    assert len(terminals) == 1 and terminals[0].kind == "error"
    assert terminals[0].data["interrupted"] is True
```

- [ ] **Step 2: Run it and confirm it fails or reveals the gap**

Run: `cd qubit_runtime && TMPDIR=/tmp/opencode/p1-tmp <venv-python> -m pytest tests/runtime/test_runtime_native.py::test_cancel_interrupts_the_agent_and_emits_one_error -q --basetemp=/tmp/opencode/p1-base`
Expected before the fix: `error` text is present but `data["interrupted"]` may be `False` if the fake returns early; the assertion pins the contract.

- [ ] **Step 3: Make the terminal event report the cancellation**

In `_run`, when `result.get("completed") is False`, set
`data={"interrupted": bool(result.get("interrupted") or (cancel is not None and cancel.is_set()))}`.
No other change.

- [ ] **Step 4: Run the whole file**

Run: `cd qubit_runtime && TMPDIR=/tmp/opencode/p1-tmp <venv-python> -m pytest tests/runtime/test_runtime_native.py -q --basetemp=/tmp/opencode/p1-base`
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add qubit_runtime/agent/runtime_native.py qubit_runtime/tests/runtime/test_runtime_native.py
git commit -m "feat(runtime): report cancellation on the native terminal event"
```

### Task 4: Registry + typed unknown-engine refusal

**Files:**
- Create: `qubit_runtime/agent/runtime_registry.py`
- Test: `qubit_runtime/tests/runtime/test_runtime_registry.py`

**Interfaces:**
- Consumes: `RuntimeBackend`, `UnknownRuntimeError` from Task 1.
- Produces: `RuntimeRegistry.register(backend)`, `get(name)`, `available()`;
  `build_registry(agent_factory)` returning a registry with `native` registered.

- [ ] **Step 1: Write the failing test**

```python
# qubit_runtime/tests/runtime/test_runtime_registry.py
import pytest

from agent.runtime_registry import RuntimeRegistry, build_registry
from agent.runtime_port import RuntimeCapabilities, TurnEvent, UnknownRuntimeError


class _Fake:
    name = "fake"
    capabilities = RuntimeCapabilities(False, False, False, False)
    def run_turn(self, request, cancel=None):
        yield TurnEvent("done", text="ok")


def test_register_get_and_available():
    registry = RuntimeRegistry()
    registry.register(_Fake())
    assert registry.available() == ["fake"]
    assert registry.get("fake").name == "fake"


def test_unknown_engine_is_typed_and_lists_the_registered_ones():
    registry = RuntimeRegistry()
    registry.register(_Fake())
    with pytest.raises(UnknownRuntimeError) as exc:
        registry.get("openjarvis")
    assert "fake" in str(exc.value)


def test_build_registry_registers_native():
    registry = build_registry(lambda request: object())
    assert "native" in registry.available()
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd qubit_runtime && TMPDIR=/tmp/opencode/p1-tmp <venv-python> -m pytest tests/runtime/test_runtime_registry.py -q --basetemp=/tmp/opencode/p1-base`
Expected: `ModuleNotFoundError: agent.runtime_registry`.

- [ ] **Step 3: Implement the registry**

```python
# qubit_runtime/agent/runtime_registry.py
"""Named RuntimePort backends. Phase 1 registers only ``native``."""
from __future__ import annotations

from typing import Any, Callable

from agent.runtime_native import NativeRuntime
from agent.runtime_port import RuntimeBackend, UnknownRuntimeError


class RuntimeRegistry:
    def __init__(self) -> None:
        self._backends: dict[str, RuntimeBackend] = {}

    def register(self, backend: RuntimeBackend) -> None:
        self._backends[backend.name] = backend

    def get(self, name: str) -> RuntimeBackend:
        if name not in self._backends:
            raise UnknownRuntimeError(name, self.available())
        return self._backends[name]

    def available(self) -> list[str]:
        return sorted(self._backends)


def build_registry(agent_factory: Callable[[Any], Any]) -> RuntimeRegistry:
    registry = RuntimeRegistry()
    registry.register(NativeRuntime(agent_factory))
    return registry
```

- [ ] **Step 4: Run it and confirm it passes**

Run: `cd qubit_runtime && TMPDIR=/tmp/opencode/p1-tmp <venv-python> -m pytest tests/runtime/test_runtime_registry.py -q --basetemp=/tmp/opencode/p1-base`
Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add qubit_runtime/agent/runtime_registry.py qubit_runtime/tests/runtime/test_runtime_registry.py
git commit -m "feat(runtime): add the backend registry with a typed unknown-engine refusal"
```

### Task 5: Diagnostic `qubit runtime` command

**Files:**
- Create: `qubit_runtime/qubit_cli/subcommands/runtime.py`
- Create: `qubit_runtime/qubit_cli/runtime_cmd.py`
- Modify: `qubit_runtime/qubit_cli/main.py` (import + one `build_runtime_parser` call in `_build_cli_parser`)
- Test: `qubit_runtime/tests/runtime/test_runtime_cmd.py`

**Interfaces:**
- Consumes: `build_registry`, `TurnRequest`, `UnknownRuntimeError`.
- Produces: `qubit runtime engines`, `qubit runtime run --engine --prompt|--query-file [--json]`;
  `cmd_runtime(args, *, registry=None) -> int`.

- [ ] **Step 1: Write the failing test**

```python
# qubit_runtime/tests/runtime/test_runtime_cmd.py
import argparse
import io
import json
from contextlib import redirect_stdout

from agent.runtime_port import RuntimeCapabilities, TurnEvent
from qubit_cli.runtime_cmd import cmd_runtime


class _Fake:
    name = "fake"
    capabilities = RuntimeCapabilities(False, False, False, False)
    def run_turn(self, request, cancel=None):
        yield TurnEvent("done", text="PORT_OK", data={"completed": True})


class _Registry:
    def __init__(self):
        self._b = {"fake": _Fake()}
    def get(self, name):
        from agent.runtime_port import UnknownRuntimeError
        if name not in self._b:
            raise UnknownRuntimeError(name, sorted(self._b))
        return self._b[name]
    def available(self):
        return sorted(self._b)


def _args(**kw):
    base = dict(runtime_action="run", engine="fake", prompt="hi", query_file=None, json=True)
    base.update(kw)
    return argparse.Namespace(**base)


def test_engines_lists_registered_backends():
    out = io.StringIO()
    with redirect_stdout(out):
        assert cmd_runtime(argparse.Namespace(runtime_action="engines"), registry=_Registry()) == 0
    assert "fake" in out.getvalue()


def test_run_emits_json_events():
    out = io.StringIO()
    with redirect_stdout(out):
        assert cmd_runtime(_args(), registry=_Registry()) == 0
    payload = json.loads(out.getvalue())
    assert payload["engine"] == "fake"
    assert payload["events"][-1]["kind"] == "done"
    assert payload["events"][-1]["text"] == "PORT_OK"


def test_unknown_engine_exits_nonzero_with_the_list(capsys):
    assert cmd_runtime(_args(engine="nope"), registry=_Registry()) == 2
    err = capsys.readouterr().err
    assert "nope" in err and "fake" in err


def test_missing_prompt_is_refused(capsys):
    assert cmd_runtime(_args(prompt=None), registry=_Registry()) == 2
    assert "prompt" in capsys.readouterr().err.lower()
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd qubit_runtime && TMPDIR=/tmp/opencode/p1-tmp <venv-python> -m pytest tests/runtime/test_runtime_cmd.py -q --basetemp=/tmp/opencode/p1-base`
Expected: `ModuleNotFoundError: qubit_cli.runtime_cmd`.

- [ ] **Step 3: Implement the parser and handler**

```python
# qubit_runtime/qubit_cli/subcommands/runtime.py
"""``qubit runtime`` subcommand parser."""
from __future__ import annotations

from typing import Callable


def build_runtime_parser(subparsers, *, cmd_runtime: Callable) -> None:
    parser = subparsers.add_parser(
        "runtime", help="Inspect and run agent engines",
        description="Inspect the registered RuntimePort engines or run one diagnostic turn.")
    runtime_sub = parser.add_subparsers(dest="runtime_action", required=True)
    runtime_sub.add_parser("engines", help="List registered engines")
    run = runtime_sub.add_parser("run", help="Run one turn through an engine")
    run.add_argument("--engine", default="native", help="Engine name (default: native)")
    run.add_argument("--prompt", default=None, help="Prompt text")
    run.add_argument("--query-file", default=None, help="Read the prompt from a UTF-8 file")
    run.add_argument("--json", action="store_true", help="Print the event stream as JSON")
    parser.set_defaults(func=cmd_runtime)
```

```python
# qubit_runtime/qubit_cli/runtime_cmd.py
"""``qubit runtime`` handler: diagnostics only, no product turn path."""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any

from agent.runtime_port import TurnRequest, UnknownRuntimeError


def _prompt(args) -> str | None:
    if args.prompt:
        return args.prompt
    if args.query_file:
        try:
            return Path(args.query_file).read_text(encoding="utf-8-sig").strip() or None
        except OSError:
            return None
    return None


def _native_factory(request: TurnRequest) -> Any:
    from qubit_cli.runtime_provider import resolve_runtime_provider
    from run_agent import AIAgent

    runtime = resolve_runtime_provider(requested=None, target_model=None)
    return AIAgent(
        api_key=runtime.get("api_key"),
        base_url=runtime.get("base_url"),
        provider=runtime.get("provider"),
        requested_provider=runtime.get("requested_provider"),
        api_mode=runtime.get("api_mode"),
        model=runtime.get("model"),
        enabled_toolsets=[],
        quiet_mode=True,
        platform="cli",
        skip_context_files=True,
        skip_memory=True,
        max_iterations=1,
    )


def cmd_runtime(args, *, registry=None) -> int:
    if registry is None:
        from agent.runtime_registry import build_registry
        registry = build_registry(_native_factory)

    if args.runtime_action == "engines":
        print("\n".join(registry.available()))
        return 0

    prompt = _prompt(args)
    if not prompt:
        print("runtime run: --prompt or --query-file is required", file=sys.stderr)
        return 2
    try:
        backend = registry.get(args.engine)
    except UnknownRuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    request = TurnRequest(prompt=prompt, session_id=f"runtime-port-{uuid.uuid4().hex[:12]}")
    events = [
        {"kind": event.kind, "text": event.text, "data": event.data}
        for event in backend.run_turn(request)
    ]
    if args.json:
        print(json.dumps({"engine": backend.name, "events": events}, ensure_ascii=False))
    else:
        for event in events:
            if event["text"]:
                print(f"[{event['kind']}] {event['text']}")
    return 0 if events and events[-1]["kind"] == "done" else 1
```

Wire it in `qubit_cli/main.py` next to `build_doctor_parser` in `_build_cli_parser`:

```python
from qubit_cli.subcommands.runtime import build_runtime_parser
from qubit_cli.runtime_cmd import cmd_runtime
build_runtime_parser(subparsers, cmd_runtime=cmd_runtime)
```

- [ ] **Step 4: Run it and confirm it passes**

Run: `cd qubit_runtime && TMPDIR=/tmp/opencode/p1-tmp <venv-python> -m pytest tests/runtime/test_runtime_cmd.py -q --basetemp=/tmp/opencode/p1-base`
Expected: `4 passed`.

- [ ] **Step 5: Prove the diagnostic command on the real CLI (no live model)**

Run: `cd qubit_runtime && <venv-python> -m qubit_cli.main runtime engines`
Expected: `native`.
Run: `cd qubit_runtime && <venv-python> -m qubit_cli.main runtime run --engine nope --prompt hi`
Expected: exit 2 and `Unknown runtime engine 'nope'; registered: native`.

- [ ] **Step 6: Commit**

```bash
git add qubit_runtime/qubit_cli/subcommands/runtime.py qubit_runtime/qubit_cli/runtime_cmd.py \
        qubit_runtime/qubit_cli/main.py qubit_runtime/tests/runtime/test_runtime_cmd.py
git commit -m "feat(cli): add the diagnostic qubit runtime command"
```

### Task 6: Focused verification + docs

**Files:**
- Modify: `docs/ARCHITECTURE.md` (one paragraph under "The runtime")
- Modify: `docs/STATUS.md` (new session entry, evidence only)
- Test: none new

- [ ] **Step 1: Run the whole runtime test dir + the no-behaviour-change check**

Run: `cd qubit_runtime && TMPDIR=/tmp/opencode/p1-tmp <venv-python> -m pytest tests/runtime -q --basetemp=/tmp/opencode/p1-base`
Expected: all green.
Run: `cd qubit_runtime && <venv-python> -m pytest tests/qubit_cli/test_cli_codex_context_reference.py tests/qubit_cli/test_finite_chat_delegation.py -q --basetemp=/tmp/opencode/p1-base2`
Expected: unchanged from the sign-off baseline (the native path is untouched).

- [ ] **Step 2: Document the boundary**

In `docs/ARCHITECTURE.md`, under "The runtime", add:

```markdown
`agent/runtime_port.py` is the engine boundary: `TurnRequest` in, ordered
`TurnEvent`s out. `agent/runtime_native.py` wraps the vendored `AIAgent`
(default). Out-of-process engines register in `agent/runtime_registry.py`.
`qubit runtime engines` / `qubit runtime run` are diagnostics; they do not
change the product turn path.
```

In `docs/STATUS.md`, record the phase, the commands above, and their observed
output. No completion or VERIFIED claim.

- [ ] **Step 3: Commit**

```bash
git add docs/ARCHITECTURE.md docs/STATUS.md
git commit -m "docs(runtime): record the Phase 1 boundary and its evidence"
```

---

## Phase 1 exit criteria

- `tests/runtime` green; the focused chat/oneshot checks unchanged.
- `qubit runtime engines` lists `native`; unknown engine is a typed refusal.
- `qubit runtime run --engine native --prompt "Reply with exactly: PORT_OK" --json`
  emits one terminal `done` with `PORT_OK` on a live model.
- No product caller changed; no OpenJarvis code imported.
- Program Phase 2 can now pin the OpenJarvis interface against a real port.
