"""Framework-neutral agent adapter boundary (P2-03).

The adapter is the seam between an Office turn and *any* engine. It is
deliberately independent of the vendored runtime, of a model provider and of
the Office runner: an engine is injected as a backend object (or a callable),
and the registry is the only place that resolves an engine name.

Contract, enforced here rather than documented only:

* one turn in (:class:`TurnInput`), an ordered iterator of
  :class:`TurnEvent` out;
* **exactly one** terminal ``done``/``error``. A backend that ends without a
  terminal event is converted into a typed terminal error; an event after the
  terminal is never emitted because iteration stops there;
* a registered engine that is absent, unpinned or unsupported is a typed
  :class:`BackendUnavailable`, never a silent fallback to ``native``;
* every engine carries a pinned version/health/availability and an optional
  model route plus fallback chain, so the caller can record exactly what was
  asked for;
* the caller's :mod:`contextvars` are active while the backend produces each
  event, even when it hops threads;
* event text and serialized ``data`` are bounded. An oversized or malformed
  event becomes one typed terminal error instead of unbounded output.

Nothing in this module opens a socket, imports a model provider or touches
live state.
"""
from __future__ import annotations

import contextvars
import json
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Mapping, Protocol
from typing import runtime_checkable

from .errors import BAD_INPUT_CODE, BusinessError

# Stable refusal codes. Callers branch on these, so they are API.
BACKEND_UNKNOWN_CODE = "BUSINESS_AGENT_BACKEND_UNKNOWN"
BACKEND_UNSUPPORTED_CODE = "BUSINESS_AGENT_BACKEND_UNSUPPORTED"
BACKEND_UNAVAILABLE_CODE = "BUSINESS_AGENT_BACKEND_UNAVAILABLE"
BACKEND_DUPLICATE_CODE = "BUSINESS_AGENT_BACKEND_DUPLICATE"
TURN_INVALID_CODE = "BUSINESS_AGENT_TURN_INVALID"
EVENT_INVALID_CODE = "BUSINESS_AGENT_EVENT_INVALID"
EVENT_TOO_LARGE_CODE = "BUSINESS_AGENT_EVENT_TOO_LARGE"
TERMINAL_MISSING_CODE = "BUSINESS_AGENT_TERMINAL_MISSING"
TERMINAL_EXCESS_CODE = "BUSINESS_AGENT_TERMINAL_EXCESS"
BACKEND_FAILED_CODE = "BUSINESS_AGENT_BACKEND_FAILED"

#: Event kinds the adapter understands. ``done``/``error`` are terminal.
EVENT_KINDS = frozenset({"delta", "tool", "usage", "done", "error"})
TERMINAL_KINDS = frozenset({"done", "error"})

#: Backend availability, distinct from a per-capability health string.
BACKEND_AVAILABILITY = frozenset(
    {"available", "degraded", "unavailable", "unsupported"}
)
BACKEND_HEALTH = frozenset({"healthy", "degraded", "unavailable"})

DEFAULT_MAX_EVENT_BYTES = 256 * 1024
DEFAULT_MAX_EVENT_TEXT_CHARS = 64 * 1024
#: A hard ceiling on emitted events so a runaway backend cannot stream forever.
MAX_TURN_EVENTS = 10_000


class BackendUnavailable(BusinessError):
    """A registered engine cannot run here.

    A refusal is not a failed turn: no work was attempted. ``code`` is one of
    :data:`BACKEND_UNSUPPORTED_CODE` / :data:`BACKEND_UNAVAILABLE_CODE`, and
    ``hint`` is the actionable next step.
    """

    def __init__(self, code: str, detail: str, *, hint: str = "") -> None:
        super().__init__(code, detail)
        self.hint = hint


def _text(value: Any, label: str, *, max_len: int = 300) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BusinessError(BAD_INPUT_CODE, f"{label} must be a non-empty string")
    clean = value.strip()
    if len(clean) > max_len:
        raise BusinessError(
            BAD_INPUT_CODE, f"{label} is longer than {max_len} characters"
        )
    return clean


@dataclass(frozen=True, slots=True)
class ModelRoute:
    """The model/provider route pinned for a turn (and each fallback hop)."""

    provider: str
    model: str
    reasoning: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _text(self.provider, "provider"))
        object.__setattr__(self, "model", _text(self.model, "model"))
        object.__setattr__(
            self, "reasoning", str(self.reasoning or "").strip()[:80]
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "reasoning": self.reasoning,
        }


@dataclass(frozen=True, slots=True)
class BackendCapabilities:
    """What an engine can do, declared before the turn starts."""

    streaming: bool = True
    cancel: bool = True
    tools: bool = True
    images: bool = False
    max_event_bytes: int = DEFAULT_MAX_EVENT_BYTES
    max_event_text_chars: int = DEFAULT_MAX_EVENT_TEXT_CHARS

    def as_dict(self) -> dict[str, Any]:
        return {
            "streaming": self.streaming,
            "cancel": self.cancel,
            "tools": self.tools,
            "images": self.images,
            "max_event_bytes": self.max_event_bytes,
            "max_event_text_chars": self.max_event_text_chars,
        }


@dataclass(frozen=True, slots=True)
class TurnInput:
    """One framework-neutral turn request."""

    prompt: str
    session_id: str = ""
    tenant_id: str = ""
    subject_id: str = ""
    run_id: str = ""
    system: str | None = None
    history: tuple[Mapping[str, Any], ...] = ()
    model_route: ModelRoute | None = None
    fallback: tuple[ModelRoute, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "subject_id": self.subject_id,
            "run_id": self.run_id,
            "system": self.system,
            "history": [dict(row) for row in self.history],
            "model_route": None
            if self.model_route is None
            else self.model_route.as_dict(),
            "fallback": [route.as_dict() for route in self.fallback],
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class TurnEvent:
    """One ordered event from a turn. ``kind`` is in :data:`EVENT_KINDS`."""

    kind: str
    text: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "text": self.text,
            "data": dict(self.data),
        }


@runtime_checkable
class AgentBackend(Protocol):
    """The injected engine contract."""

    name: str
    engine: str

    def capabilities(self) -> BackendCapabilities:
        ...

    def start_turn(self, turn: TurnInput) -> Iterator[TurnEvent]:
        ...

    def cancel(self) -> None:
        ...


def _validate_turn(turn: Any) -> TurnInput:
    if not isinstance(turn, TurnInput):
        raise BusinessError(TURN_INVALID_CODE, "turn must be a TurnInput")
    if not isinstance(turn.prompt, str) or not turn.prompt.strip():
        raise BusinessError(
            TURN_INVALID_CODE, "turn.prompt must be a non-empty string"
        )
    if not isinstance(turn.history, tuple):
        raise BusinessError(TURN_INVALID_CODE, "turn.history must be a tuple")
    for label in ("session_id", "tenant_id", "subject_id", "run_id"):
        value = getattr(turn, label)
        if not isinstance(value, str):
            raise BusinessError(
                TURN_INVALID_CODE, f"turn.{label} must be a string"
            )
    if turn.model_route is not None and not isinstance(
        turn.model_route, ModelRoute
    ):
        raise BusinessError(
            TURN_INVALID_CODE, "turn.model_route must be a ModelRoute or None"
        )
    if not isinstance(turn.fallback, tuple) or any(
        not isinstance(route, ModelRoute) for route in turn.fallback
    ):
        raise BusinessError(
            TURN_INVALID_CODE, "turn.fallback must be a tuple of ModelRoute"
        )
    if not isinstance(turn.metadata, Mapping):
        raise BusinessError(TURN_INVALID_CODE, "turn.metadata must be a mapping")
    return turn


def _terminal_error(code: str, detail: str) -> TurnEvent:
    return TurnEvent(
        kind="error",
        text=detail,
        data={"code": code, "terminal": True},
    )


def _coerce_event(
    event: Any,
    *,
    max_bytes: int,
    max_text_chars: int,
) -> TurnEvent:
    """Return a bounded, well-formed event or one typed terminal error."""
    if not isinstance(event, TurnEvent):
        return _terminal_error(
            EVENT_INVALID_CODE, "backend produced a non-TurnEvent object"
        )
    if event.kind not in EVENT_KINDS:
        return _terminal_error(
            EVENT_INVALID_CODE, f"unknown event kind {event.kind!r}"
        )
    if not isinstance(event.text, str):
        return _terminal_error(EVENT_INVALID_CODE, "event.text must be a string")
    if not isinstance(event.data, Mapping):
        return _terminal_error(EVENT_INVALID_CODE, "event.data must be a mapping")
    if len(event.text) > max_text_chars:
        return _terminal_error(
            EVENT_TOO_LARGE_CODE,
            f"event text exceeds {max_text_chars} characters",
        )
    data = dict(event.data)
    try:
        encoded = json.dumps(
            data, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        return _terminal_error(
            EVENT_INVALID_CODE, f"event.data is not serializable: {exc}"
        )
    if len(encoded) > max_bytes:
        return _terminal_error(
            EVENT_TOO_LARGE_CODE,
            f"event data exceeds {max_bytes} bytes",
        )
    return TurnEvent(kind=event.kind, text=event.text, data=data)


class FakeBackend:
    """A hermetic, deterministic backend for tests and dry runs.

    ``events`` wins when supplied (exact order preserved); otherwise one delta
    per ``chunks`` entry followed by ``done``. ``fail_with`` turns the terminal
    into an ``error`` carrying that code. A cancellation observed between
    events yields a single ``done`` with ``data['cancelled'] == True``.
    """

    def __init__(
        self,
        *,
        name: str = "fake",
        engine: str = "fake",
        response: str = "",
        chunks: Iterable[str] | None = None,
        events: Iterable[TurnEvent] | None = None,
        fail_with: str | None = None,
        capabilities: BackendCapabilities | None = None,
        version: str = "1.0.0",
        health: str = "healthy",
        availability: str = "available",
        model_route: ModelRoute | None = None,
        fallback: tuple[ModelRoute, ...] = (),
    ) -> None:
        self.name = _text(name, "name")
        self.engine = _text(engine, "engine")
        self._events = tuple(events) if events is not None else None
        if self._events is None:
            if chunks is not None:
                self._chunks = tuple(str(chunk) for chunk in chunks)
            else:
                self._chunks = (response,) if response else ()
        else:
            self._chunks = ()
        self._fail_with = str(fail_with or "").strip()
        self.capabilities_spec = capabilities or BackendCapabilities()
        self.version = str(version)
        self.health = health
        self.availability = availability
        self.model_route = model_route
        self.fallback = tuple(fallback)
        self._cancelled = threading.Event()
        self.turns: list[TurnInput] = []
        self.cancel_calls = 0

    def capabilities(self) -> BackendCapabilities:
        return self.capabilities_spec

    def cancel(self) -> None:
        self.cancel_calls += 1
        self._cancelled.set()

    def start_turn(self, turn: TurnInput) -> Iterator[TurnEvent]:
        self.turns.append(turn)
        try:
            for item in self._scripted():
                if (
                    self._cancelled.is_set()
                    and item.kind not in TERMINAL_KINDS
                ):
                    yield TurnEvent(kind="done", data={"cancelled": True})
                    return
                yield item
                if item.kind in TERMINAL_KINDS:
                    return
        finally:
            # A cancellation applies to one turn only; clearing at the end
            # (not the start) means a cancel requested before the first pull
            # is still honoured.
            self._cancelled.clear()

    def _scripted(self) -> Iterator[TurnEvent]:
        if self._events is not None:
            yield from self._events
            return
        for chunk in self._chunks:
            yield TurnEvent(kind="delta", text=chunk)
        if self._fail_with:
            yield _terminal_error(self._fail_with, "fake backend failure")
        else:
            yield TurnEvent(kind="done", text="".join(self._chunks))


class CallbackBackend:
    """Wraps an injected callable returning an iterable of :class:`TurnEvent`.

    The callable receives a :class:`TurnInput`. This is the seam the
    integration lead wires to ``runtime_port`` or the Office runner; the
    adapter never imports either.
    """

    def __init__(
        self,
        engine: str,
        run_turn: Callable[[TurnInput], Iterable[TurnEvent]],
        *,
        name: str | None = None,
        capabilities: BackendCapabilities | None = None,
        version: str = "1.0.0",
        health: str = "healthy",
        availability: str = "available",
        model_route: ModelRoute | None = None,
        fallback: tuple[ModelRoute, ...] = (),
    ) -> None:
        if not callable(run_turn):
            raise BusinessError(BAD_INPUT_CODE, "run_turn must be callable")
        self.engine = _text(engine, "engine")
        self.name = _text(name or engine, "name")
        self._run_turn = run_turn
        self.capabilities_spec = capabilities or BackendCapabilities()
        self.version = str(version)
        self.health = health
        self.availability = availability
        self.model_route = model_route
        self.fallback = tuple(fallback)
        self._cancelled = threading.Event()
        self.cancel_calls = 0

    def capabilities(self) -> BackendCapabilities:
        return self.capabilities_spec

    def cancel(self) -> None:
        self.cancel_calls += 1
        self._cancelled.set()

    def start_turn(self, turn: TurnInput) -> Iterator[TurnEvent]:
        try:
            for item in self._run_turn(turn):
                if (
                    self._cancelled.is_set()
                    and item.kind not in TERMINAL_KINDS
                ):
                    yield TurnEvent(kind="done", data={"cancelled": True})
                    return
                yield item
                if item.kind in TERMINAL_KINDS:
                    return
        finally:
            self._cancelled.clear()


class NativeBackend(CallbackBackend):
    """The native seam: a callback wrapping the vendored runtime port.

    With no injected callable this class deliberately does **not** fall back to
    a built-in engine. Use :meth:`from_runtime_backend` to adapt a
    ``runtime_port``-shaped object, or pass a callable directly.
    """

    @classmethod
    def from_runtime_backend(
        cls,
        engine: str,
        runtime_backend: Any,
        *,
        request_factory: Callable[[TurnInput], Any] | None = None,
        **kwargs: Any,
    ) -> "NativeBackend":
        run_turn = getattr(runtime_backend, "run_turn", None)
        if not callable(run_turn):
            raise BusinessError(
                BAD_INPUT_CODE,
                "runtime_backend must expose a callable run_turn",
            )
        factory = request_factory or (lambda turn: turn)

        def _run(turn: TurnInput) -> Iterable[TurnEvent]:
            return run_turn(factory(turn))

        return cls(engine, _run, **kwargs)


@dataclass(frozen=True, slots=True)
class BackendRegistration:
    """A pinned registry entry. The backend object is not serialized."""

    engine: str
    backend: AgentBackend
    version: str
    health: str
    availability: str
    model_route: ModelRoute | None = None
    fallback: tuple[ModelRoute, ...] = ()
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "version": self.version,
            "health": self.health,
            "availability": self.availability,
            "model_route": None
            if self.model_route is None
            else self.model_route.as_dict(),
            "fallback": [route.as_dict() for route in self.fallback],
            "reason": self.reason,
            "capabilities": self.backend.capabilities().as_dict()
            if callable(getattr(self.backend, "capabilities", None))
            else {},
        }


def _normalise_fallback(
    fallback: Iterable[ModelRoute],
) -> tuple[ModelRoute, ...]:
    rows = tuple(fallback)
    if any(not isinstance(route, ModelRoute) for route in rows):
        raise BusinessError(
            BAD_INPUT_CODE, "fallback must be a sequence of ModelRoute"
        )
    return rows


class AgentAdapterRegistry:
    """Resolve engine names to pinned backends; unknown is a typed refusal."""

    def __init__(self) -> None:
        self._entries: dict[str, BackendRegistration] = {}

    def register(
        self,
        backend: AgentBackend,
        *,
        engine: str | None = None,
        version: str = "1.0.0",
        health: str = "healthy",
        availability: str = "available",
        model_route: ModelRoute | None = None,
        fallback: Iterable[ModelRoute] = (),
        reason: str = "",
    ) -> BackendRegistration:
        engine_name = engine or getattr(backend, "engine", None) or getattr(
            backend, "name", None
        )
        clean = _text(engine_name, "engine")
        if clean in self._entries:
            raise BusinessError(
                BACKEND_DUPLICATE_CODE,
                f"engine {clean!r} is already registered",
            )
        if health not in BACKEND_HEALTH:
            raise BusinessError(
                BAD_INPUT_CODE,
                f"health must be one of {', '.join(sorted(BACKEND_HEALTH))}",
            )
        if availability not in BACKEND_AVAILABILITY:
            raise BusinessError(
                BAD_INPUT_CODE,
                "availability must be one of"
                f" {', '.join(sorted(BACKEND_AVAILABILITY))}",
            )
        registration = BackendRegistration(
            engine=clean,
            backend=backend,
            version=str(version),
            health=health,
            availability=availability,
            model_route=model_route,
            fallback=_normalise_fallback(fallback),
            reason=str(reason or "").strip(),
        )
        self._entries[clean] = registration
        return registration

    def describe(self, engine: str) -> BackendRegistration:
        """Inspect a registration even when it is unavailable."""
        clean = _text(engine, "engine")
        registration = self._entries.get(clean)
        if registration is None:
            raise BusinessError(
                BACKEND_UNKNOWN_CODE,
                f"agent engine {clean!r} is not registered"
                f" (registered: {', '.join(self.engines()) or '(none)'})",
            )
        return registration

    def get(self, engine: str) -> BackendRegistration:
        """Resolve a runnable engine; unavailable/unsupported is typed."""
        registration = self.describe(engine)
        if registration.availability == "unsupported":
            raise BackendUnavailable(
                BACKEND_UNSUPPORTED_CODE,
                f"engine {registration.engine!r} is unsupported on this host"
                + (f": {registration.reason}" if registration.reason else ""),
                hint=registration.reason,
            )
        if registration.availability == "unavailable":
            raise BackendUnavailable(
                BACKEND_UNAVAILABLE_CODE,
                f"engine {registration.engine!r} is unavailable"
                + (f": {registration.reason}" if registration.reason else ""),
                hint=registration.reason,
            )
        return registration

    def engines(self) -> tuple[str, ...]:
        return tuple(sorted(self._entries))

    def list(self) -> tuple[BackendRegistration, ...]:
        return tuple(self._entries[name] for name in self.engines())

    def descriptors(self) -> tuple[dict[str, Any], ...]:
        return tuple(row.as_dict() for row in self.list())

    def capabilities(self, engine: str) -> BackendCapabilities:
        return self.get(engine).backend.capabilities()

    def cancel(self, engine: str) -> None:
        # Cancellation must work for a backend that has since been marked
        # unavailable: use the descriptor, not the runnable resolve.
        self.describe(engine).backend.cancel()

    def start_turn(self, engine: str, turn: TurnInput) -> Iterator[TurnEvent]:
        registration = self.get(engine)
        clean_turn = _validate_turn(turn)
        caps = registration.backend.capabilities()
        max_bytes = int(caps.max_event_bytes)
        max_text = int(caps.max_event_text_chars)
        if max_bytes <= 0 or max_text <= 0:
            raise BusinessError(
                BAD_INPUT_CODE, "backend event bounds must be positive"
            )
        # Capture the caller's context now; the backend may hop threads while
        # producing events, and each pull runs under this snapshot.
        caller_context = contextvars.copy_context()
        inner = registration.backend.start_turn(clean_turn)

        def _iterate() -> Iterator[TurnEvent]:
            terminal_seen = False
            emitted = 0
            try:
                while True:
                    try:
                        raw = caller_context.run(next, inner)
                    except StopIteration:
                        break
                    except BusinessError as exc:
                        yield _terminal_error(exc.code, exc.detail)
                        return
                    except Exception as exc:  # noqa: BLE001 - typed terminal
                        yield _terminal_error(
                            BACKEND_FAILED_CODE,
                            f"backend raised {type(exc).__name__}:"
                            f" {str(exc)[:300]}",
                        )
                        return
                    emitted += 1
                    if emitted > MAX_TURN_EVENTS:
                        yield _terminal_error(
                            EVENT_TOO_LARGE_CODE,
                            f"turn exceeded {MAX_TURN_EVENTS} events",
                        )
                        return
                    event = _coerce_event(
                        raw,
                        max_bytes=max_bytes,
                        max_text_chars=max_text,
                    )
                    terminal_seen = event.kind in TERMINAL_KINDS
                    yield event
                    if terminal_seen:
                        return
            finally:
                close = getattr(inner, "close", None)
                if callable(close):
                    close()
            if not terminal_seen:
                yield _terminal_error(
                    TERMINAL_MISSING_CODE,
                    "backend ended without a terminal done/error event",
                )

        return _iterate()

    def collect(self, engine: str, turn: TurnInput) -> tuple[TurnEvent, ...]:
        """Drain one turn and assert exactly one terminal event at the end."""
        events = tuple(self.start_turn(engine, turn))
        terminals = [event for event in events if event.kind in TERMINAL_KINDS]
        if len(terminals) != 1 or events[-1].kind not in TERMINAL_KINDS:
            raise BusinessError(
                TERMINAL_EXCESS_CODE if terminals else TERMINAL_MISSING_CODE,
                "a turn must end with exactly one terminal done/error event",
            )
        return events

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self._entries)


def drain_events(events: Iterable[TurnEvent]) -> tuple[TurnEvent, ...]:
    """Collect an event iterator, requiring one final terminal event."""
    rows = tuple(events)
    terminals = [row for row in rows if row.kind in TERMINAL_KINDS]
    if len(terminals) != 1 or not rows or rows[-1].kind not in TERMINAL_KINDS:
        raise BusinessError(
            TERMINAL_EXCESS_CODE if terminals else TERMINAL_MISSING_CODE,
            "a turn must end with exactly one terminal done/error event",
        )
    return rows
