"""Opt-in trace recorder.

``record_trace`` is the strict API (typed errors). Product code calls
``try_record_trace``, which never raises: tracing is observability, never a
reason to fail a turn. Both are no-ops when tracing is disabled.

Integration point: ``recipes.runner.run_recipe`` records one trace after a
real (non-dry-run) turn, behind this opt-in. ``qubit -z`` is deliberately not
wired yet — its result envelope does not expose usage, so a trace there would
have unknown cost/tokens; the function below is ready for it when it does.
"""
from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from traces.errors import TraceInputError, TraceStoreError
from traces.redaction import redact_excerpt, redact_tool_name
from traces.settings import MAX_EXCERPT_CHARS, TraceSettings, load_settings
from traces.store import TraceRow, TraceStore

logger = logging.getLogger(__name__)

MAX_TOOL_CALLS = 200
MAX_TOOL_NAME_CHARS = 120
MAX_ID_CHARS = 200
MAX_MODEL_CHARS = 200


def new_trace_id() -> str:
    return uuid.uuid4().hex


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _number(value: Any, name: str, *, integer: bool = False) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TraceInputError(f"{name} must be a number, not a boolean")
    try:
        number = int(value) if integer else float(value)
    except (TypeError, ValueError) as exc:
        raise TraceInputError(f"{name} must be a number") from exc
    if integer and number < 0:
        raise TraceInputError(f"{name} must not be negative")
    if not integer and (not math.isfinite(number) or number < 0):
        raise TraceInputError(f"{name} must be a finite non-negative number")
    return number


def _tool_call_names(tool_calls: Iterable[Any]) -> tuple[str, ...]:
    names: list[str] = []
    for item in list(tool_calls)[:MAX_TOOL_CALLS]:
        if isinstance(item, Mapping):
            raw = item.get("name") or item.get("tool") or ""
        else:
            raw = item
        name = redact_tool_name(raw, MAX_TOOL_NAME_CHARS)
        names.append(name)
    return tuple(names)


def _build_row(
    *,
    settings: TraceSettings,
    trace_id: str | None,
    session_id: str | None,
    engine: str | None,
    model: str | None,
    prompt: str | None,
    response: str | None,
    tool_calls: Sequence[Any] | None,
    tool_call_count: int | None,
    latency_ms: Any,
    prompt_tokens: Any,
    completion_tokens: Any,
    cost_usd: Any,
    energy_joules: Any,
    flops: Any,
    success: bool,
    error_code: str | None,
    created_at: str | None,
) -> TraceRow:
    names = _tool_call_names(tool_calls or ())
    excerpt_limit = max(1, min(int(settings.max_excerpt_chars or 1), MAX_EXCERPT_CHARS))
    if tool_call_count is None:
        tool_call_count = len(names)
    else:
        tool_call_count = int(_number(tool_call_count, "tool_call_count", integer=True) or 0)
    return TraceRow(
        id=redact_excerpt(trace_id, MAX_ID_CHARS) if trace_id else new_trace_id(),
        created_at=created_at or _utc_now(),
        session_id=redact_excerpt(session_id, MAX_ID_CHARS),
        engine=redact_excerpt(engine, MAX_MODEL_CHARS),
        model=redact_excerpt(model, MAX_MODEL_CHARS),
        prompt_excerpt=redact_excerpt(prompt, excerpt_limit),
        response_excerpt=redact_excerpt(response, excerpt_limit),
        tool_calls=names,
        tool_call_count=tool_call_count,
        latency_ms=_number(latency_ms, "latency_ms"),
        prompt_tokens=_number(prompt_tokens, "prompt_tokens", integer=True),
        completion_tokens=_number(completion_tokens, "completion_tokens", integer=True),
        cost_usd=_number(cost_usd, "cost_usd"),
        energy_joules=_number(energy_joules, "energy_joules"),
        flops=_number(flops, "flops"),
        success=bool(success),
        error_code=redact_excerpt(error_code, MAX_ID_CHARS) if error_code else None,
    )


def record_trace(
    *,
    prompt: str | None = None,
    response: str | None = None,
    session_id: str | None = None,
    engine: str | None = None,
    model: str | None = None,
    tool_calls: Sequence[Any] | None = None,
    tool_call_count: int | None = None,
    latency_ms: float | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    cost_usd: float | None = None,
    energy_joules: float | None = None,
    flops: float | None = None,
    success: bool = True,
    error_code: str | None = None,
    trace_id: str | None = None,
    created_at: str | None = None,
    settings: TraceSettings | None = None,
    store: TraceStore | None = None,
    config: Mapping[str, Any] | None = None,
) -> str | None:
    """Record one trace and return its id, or ``None`` when tracing is disabled.

    Raises a typed :class:`TraceError` for bad input or a malformed store.
    """
    resolved = settings if settings is not None else load_settings()
    if not resolved.enabled:
        return None
    row = _build_row(
        settings=resolved, trace_id=trace_id, session_id=session_id, engine=engine,
        model=model, prompt=prompt, response=response, tool_calls=tool_calls,
        tool_call_count=tool_call_count, latency_ms=latency_ms,
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        cost_usd=cost_usd, energy_joules=energy_joules, flops=flops, success=success,
        error_code=error_code, created_at=created_at)
    own_store = store is None
    target = store or TraceStore(resolved.path)
    try:
        target.insert(row)
    finally:
        if own_store:
            target.close()
    return row.id


def try_record_trace(**kwargs: Any) -> str | None:
    """Best-effort recorder for product code: never raises, never changes a turn."""
    try:
        return record_trace(**kwargs)
    except (TraceStoreError, TraceInputError) as exc:
        logger.debug("trace not recorded: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001 - observability must not break the caller
        logger.debug("trace not recorded: unexpected %s", type(exc).__name__)
        return None


__all__ = ["MAX_TOOL_CALLS", "new_trace_id", "record_trace", "try_record_trace"]
