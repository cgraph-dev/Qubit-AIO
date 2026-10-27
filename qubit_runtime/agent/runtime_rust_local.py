"""Optional Rust-local RuntimePort backend.

OpenJarvis at the reviewed pin exposes exactly one Rust-facing interface: the
optional PyO3 extension module ``openjarvis_rust``. It is not a standalone
binary and it is not imported by Qubit's default runtime. The extension builds
an engine wrapper in Rust over a local inference server:

    openjarvis_rust.Engine(engine_key="ollama", host="http://localhost:11434")
    openjarvis_rust.Message(role, content)
    engine.generate(messages, model, temperature=0.7, max_tokens=1024) -> JSON

``generate`` is synchronous and returns ``GenerateResult`` as JSON (``content``,
``usage``, ``model``, ``finish_reason``, optional ``tool_calls``). There is no
token stream and no tool executor in this interface, so the adapter advertises
that honestly rather than pretending otherwise.

The extension is optional and never imported at module import time. When it (or
its ``Engine``/``Message`` pieces) is absent, every turn yields exactly one
terminal ``error`` event with ``data["refusal"] == "rust_engine_unavailable"``
and an actionable hint. There is deliberately no fallback to the native engine:
a configured-but-unavailable Rust engine must not silently change backends.

The engine does not do network work until ``generate`` is called; construction
and ``resolve`` only touch the extension module.
"""
from __future__ import annotations

import importlib
import json
import os
import threading
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Mapping

from agent.runtime_port import (
    RuntimeCapabilities,
    RuntimeRefusalError,
    TurnEvent,
    TurnRequest,
)

NAME = "rust_local"
EXTENSION_MODULE = "openjarvis_rust"

ENGINE_ENV = "QUBIT_RUST_LOCAL_ENGINE"
HOST_ENV = "QUBIT_RUST_LOCAL_HOST"
MODEL_ENV = "QUBIT_RUST_LOCAL_MODEL"
DEFAULT_ENGINE_KEY = "ollama"

_INSTALL_HINT = (
    "Install the optional `openjarvis_rust` PyO3 extension (build it from the "
    "pinned OpenJarvis checkout with `maturin develop --manifest-path "
    "rust/crates/openjarvis-python/Cargo.toml`, or install its wheel), then set "
    "QUBIT_RUST_LOCAL_MODEL and optionally QUBIT_RUST_LOCAL_ENGINE/HOST."
)
_MODEL_HINT = (
    "Set QUBIT_RUST_LOCAL_MODEL (or pass model=) to a model served by the local "
    "Rust engine, and make sure that engine is running."
)
_USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")
_TERMINAL = ("done", "error")
_MAX_ERROR_CHARS = 2_000
_MAX_TEXT_CHARS = 1_000_000
_MAX_NAME_CHARS = 200


def _bounded(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else str(value or "")
    return text if len(text) <= limit else text[:limit] + "…"


def _safe(value: Any, limit: int) -> str:
    """Redact before bounding; a broken redactor fails closed, never raw."""
    try:
        from agent.monitoring.redaction import redact_bounded

        return redact_bounded(value, limit=limit, empty="")
    except Exception:  # noqa: BLE001 - a diagnostic must never leak by crashing
        return "[redacted]"


def _bounded_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    return None


def _import_extension() -> Any:
    """Import only when a turn or an availability probe needs it."""
    try:
        return importlib.import_module(EXTENSION_MODULE)
    except Exception as exc:  # noqa: BLE001 - any load failure is a refusal
        raise RuntimeRefusalError(
            "rust_engine_unavailable",
            _safe(
                f"the optional OpenJarvis Rust extension {EXTENSION_MODULE!r} is not "
                f"importable: {type(exc).__name__}: {exc}",
                _MAX_ERROR_CHARS,
            ),
            _INSTALL_HINT,
        ) from exc


@dataclass(frozen=True)
class ResolvedRustLocal:
    module: Any
    engine: Any
    engine_key: str
    variant: str


class RustLocalRuntime:
    """Run one turn through an already-installed ``openjarvis_rust`` engine."""

    name = NAME
    # ``generate`` returns the whole answer and has no cancel/tool surface.
    capabilities = RuntimeCapabilities(streaming=False, cancel=False, tools=False, images=False)

    def __init__(
        self,
        *,
        module_loader: Callable[[], Any] | None = None,
        engine_key: str | None = None,
        host: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> None:
        self._module_loader = module_loader or _import_extension
        self._engine_key = (
            engine_key or os.environ.get(ENGINE_ENV) or DEFAULT_ENGINE_KEY
        ).strip() or DEFAULT_ENGINE_KEY
        self._host = host if host is not None else os.environ.get(HOST_ENV)
        self._model = model if model is not None else os.environ.get(MODEL_ENV)
        self._temperature = float(temperature)
        self._max_tokens = int(max_tokens)
        self._resolved: ResolvedRustLocal | None = None
        self._resolve_lock = threading.Lock()

    # ------------------------------------------------------------- capability

    def resolve(self) -> ResolvedRustLocal:
        """Resolve the extension + engine object, or raise a typed refusal."""
        with self._resolve_lock:
            if self._resolved is not None:
                return self._resolved
            self._resolved = self._resolve_uncached()
            return self._resolved

    def _resolve_uncached(self) -> ResolvedRustLocal:
        try:
            module = self._module_loader()
        except RuntimeRefusalError:
            raise
        except Exception as exc:  # noqa: BLE001 - any load failure is a refusal
            raise RuntimeRefusalError(
                "rust_engine_unavailable",
                _safe(
                    f"the OpenJarvis Rust extension loader failed: "
                    f"{type(exc).__name__}: {exc}",
                    _MAX_ERROR_CHARS,
                ),
                _INSTALL_HINT,
            ) from exc
        engine_cls = getattr(module, "Engine", None)
        legacy = False
        if not callable(engine_cls):
            engine_cls = getattr(module, "OllamaEngine", None)
            legacy = True
        if not callable(engine_cls):
            raise RuntimeRefusalError(
                "rust_engine_unavailable",
                f"the {EXTENSION_MODULE!r} extension does not expose an Engine class",
                _INSTALL_HINT,
            )
        try:
            engine = self._make_engine(engine_cls, legacy)
        except RuntimeRefusalError:
            raise
        except Exception as exc:  # noqa: BLE001 - construction failure is a refusal
            raise RuntimeRefusalError(
                "rust_engine_unavailable",
                _safe(
                    f"cannot construct the Rust engine {self._engine_key!r}: "
                    f"{type(exc).__name__}: {exc}",
                    _MAX_ERROR_CHARS,
                ),
                _INSTALL_HINT,
            ) from exc
        variant = ""
        variant_fn = getattr(engine, "variant_key", None)
        if callable(variant_fn):
            try:
                variant = _bounded(variant_fn(), _MAX_NAME_CHARS)
            except Exception:  # noqa: BLE001 - metadata only
                variant = ""
        return ResolvedRustLocal(module, engine, self._engine_key, variant)

    def _make_engine(self, engine_cls: Callable[..., Any], legacy: bool) -> Any:
        if legacy:
            return engine_cls(host=self._host) if self._host else engine_cls()
        if self._host:
            return engine_cls(self._engine_key, host=self._host)
        return engine_cls(self._engine_key)

    # ------------------------------------------------------------------ turns

    def run_turn(self, request: TurnRequest, cancel: Any | None = None) -> Iterator[TurnEvent]:
        """Yield ordered events with exactly one terminal ``done`` or ``error``."""
        terminal_seen = False
        try:
            for event in self._run_turn(request, cancel):
                if event.kind in _TERMINAL:
                    if terminal_seen:
                        continue
                    terminal_seen = True
                yield event
        except RuntimeRefusalError as exc:
            if not terminal_seen:
                yield self._refusal_event(exc)
        except Exception as exc:  # noqa: BLE001 - the port owns the terminal event
            if not terminal_seen:
                yield TurnEvent(
                    "error",
                    text=_safe(f"Rust local adapter failure: {exc}", _MAX_ERROR_CHARS),
                    data={"engine": self.name},
                )

    def _run_turn(
        self, request: TurnRequest, cancel: Any | None = None
    ) -> Iterator[TurnEvent]:
        is_set = getattr(cancel, "is_set", None)
        if callable(is_set) and is_set():
            yield TurnEvent(
                "error",
                text="rust local turn cancelled before it started",
                data={"engine": self.name, "cancelled": True, "interrupted": True},
            )
            return

        try:
            resolved = self.resolve()
        except RuntimeRefusalError as exc:
            yield self._refusal_event(exc)
            return

        model = _bounded(
            self._model.strip() if isinstance(self._model, str) else "", _MAX_NAME_CHARS,
        )
        if not model:
            yield self._refusal_event(RuntimeRefusalError(
                "rust_engine_unavailable",
                "no model is configured for the Rust local engine",
                _MODEL_HINT,
            ))
            return

        try:
            messages = self._build_messages(request, resolved.module)
        except RuntimeRefusalError as exc:
            yield self._refusal_event(exc)
            return

        try:
            raw = resolved.engine.generate(
                messages,
                model,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
        except Exception as exc:  # noqa: BLE001 - a turn failure is an event
            yield TurnEvent(
                "error",
                text=_safe(
                    f"Rust local engine call failed: {type(exc).__name__}: {exc}",
                    _MAX_ERROR_CHARS,
                ),
                data={"engine": self.name},
            )
            return

        payload = self._parse_payload(raw)
        if payload is None:
            yield TurnEvent(
                "error",
                text=_safe("malformed Rust local result", _MAX_ERROR_CHARS),
                data={"engine": self.name},
            )
            return

        content = _bounded(payload.get("content", ""), _MAX_TEXT_CHARS)
        error_text = payload.get("error") or payload.get("detail")
        if error_text:
            if content:
                yield TurnEvent("delta", text=content)
            yield TurnEvent(
                "error",
                text=_safe(error_text, _MAX_ERROR_CHARS),
                data={"engine": self.name},
            )
            return
        if payload.get("completed") is False:
            if content:
                yield TurnEvent("delta", text=content)
            yield TurnEvent(
                "error",
                text="the Rust local engine reported an incomplete turn",
                data={"engine": self.name},
            )
            return

        if content:
            # No token stream in this interface: the whole answer is one delta so
            # delta-only consumers still see text. ``capabilities.streaming`` stays
            # False to advertise the limitation.
            yield TurnEvent("delta", text=content)

        usage = payload.get("usage")
        if isinstance(usage, Mapping):
            bounded_usage = {
                key: value for key in _USAGE_KEYS
                if (value := _bounded_int(usage.get(key))) is not None
            }
            if bounded_usage:
                yield TurnEvent("usage", data=bounded_usage)

        done_data: dict[str, Any] = {
            "completed": True,
            "engine": self.name,
            "backend": "rust",
            "engine_key": _bounded(resolved.engine_key, _MAX_NAME_CHARS),
            "model": model,
        }
        if resolved.variant:
            done_data["variant"] = _bounded(resolved.variant, _MAX_NAME_CHARS)
        finish_reason = payload.get("finish_reason")
        if isinstance(finish_reason, str) and finish_reason:
            done_data["finish_reason"] = _bounded(finish_reason, _MAX_NAME_CHARS)
        yield TurnEvent("done", text=content, data=done_data)

    # -------------------------------------------------------------- message IO

    def _build_messages(self, request: TurnRequest, module: Any) -> list[Any]:
        message_cls = getattr(module, "Message", None)
        if not callable(message_cls):
            raise RuntimeRefusalError(
                "rust_engine_unavailable",
                f"the {EXTENSION_MODULE!r} extension does not expose a Message constructor",
                _INSTALL_HINT,
            )
        rows: list[tuple[str, str]] = []
        if request.system:
            rows.append(("system", request.system))
        for item in request.history:
            if not isinstance(item, Mapping):
                continue
            content = item.get("content")
            if content is None:
                continue
            role = str(item.get("role") or "user").strip().lower() or "user"
            rows.append((role, str(content)))
        rows.append(("user", request.prompt))
        try:
            return [message_cls(role, content) for role, content in rows]
        except Exception as exc:  # noqa: BLE001 - malformed history is a refusal
            raise RuntimeRefusalError(
                "rust_engine_unavailable",
                _safe(
                    f"cannot build Rust engine messages: {type(exc).__name__}: {exc}",
                    _MAX_ERROR_CHARS,
                ),
                _INSTALL_HINT,
            ) from exc

    @staticmethod
    def _parse_payload(raw: Any) -> dict[str, Any] | None:
        if isinstance(raw, Mapping):
            return dict(raw)
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", "replace")
        if not isinstance(raw, str):
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def _refusal_event(self, exc: RuntimeRefusalError) -> TurnEvent:
        return TurnEvent(
            "error",
            text=_safe(exc.message, _MAX_ERROR_CHARS),
            data={
                "engine": self.name,
                "refusal": exc.code,
                "hint": _safe(exc.hint, _MAX_ERROR_CHARS),
            },
        )


def build_rust_local_runtime(**kwargs: Any) -> RustLocalRuntime:
    """Construct the optional backend without changing any default engine."""
    return RustLocalRuntime(**kwargs)


__all__ = [
    "DEFAULT_ENGINE_KEY",
    "ENGINE_ENV",
    "EXTENSION_MODULE",
    "HOST_ENV",
    "MODEL_ENV",
    "NAME",
    "ResolvedRustLocal",
    "RustLocalRuntime",
    "build_rust_local_runtime",
]
