"""Execute one recipe turn through the RuntimePort.

Deterministic collectors run first; the selected backend only writes the
brief from their facts. The native backend is the default. A non-native
engine that cannot run is a typed refusal (exit 2), never a silent fallback.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from agent.redact import redact_for_egress
from agent.runtime_port import TurnEvent, TurnRequest
from recipes.sections import SectionResult, collect_sections, render_brief, render_facts
from recipes.spec import RecipeSpec
from traces.recorder import try_record_trace


def _default_agent_factory(request: TurnRequest) -> Any:
    from qubit_cli.runtime_cmd import _native_factory

    return _native_factory(request)


class RecipeInputError(ValueError):
    """A recipe's declared inputs are missing or blank."""

    def __init__(self, recipe_id: str, missing: Sequence[str]) -> None:
        self.recipe_id = recipe_id
        self.missing = tuple(missing)
        super().__init__(
            f"recipe {recipe_id!r} requires input(s): {', '.join(self.missing)}")


def _redact(value: object) -> str:
    try:
        return redact_for_egress(str(value or ""))
    except Exception:
        return ""


def normalize_inputs(spec: RecipeSpec,
                     inputs: Mapping[str, Any] | None) -> dict[str, str]:
    """Return bounded input values, raising for any declared-but-blank input."""
    provided = {
        str(key): _redact(value).strip()
        for key, value in dict(inputs or {}).items()
    }
    missing = [name for name in spec.input_fields if not provided.get(name)]
    if missing:
        raise RecipeInputError(spec.id, missing)
    return provided


def build_prompt(spec: RecipeSpec, sections: Sequence[SectionResult],
                 now: datetime | None = None,
                 inputs: Mapping[str, Any] | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    today = moment.astimezone().strftime("%A, %B %d, %Y")
    text = str(spec.prompt).replace("{facts}", render_facts(sections)).replace(
        "{today}", today)
    for key, value in dict(inputs or {}).items():
        text = text.replace("{" + str(key) + "}", _redact(value))
    return text


@dataclass(frozen=True)
class RecipeRun:
    recipe_id: str
    engine: str
    brief: str
    sections: tuple[SectionResult, ...]
    events: tuple[TurnEvent, ...] = field(default_factory=tuple)
    terminal: str = ""
    refusal: str = ""
    hint: str = ""
    failed: bool = False
    cancelled: bool = False

    @property
    def exit_code(self) -> int:
        if self.cancelled:
            return 130
        if self.refusal:
            return 2
        if self.failed:
            return 1
        return 0

    def as_dict(self) -> dict:
        return {
            "recipe": self.recipe_id,
            "engine": self.engine,
            "brief": self.brief,
            "sections": [section.as_dict() for section in self.sections],
            "terminal": self.terminal,
            "refusal": self.refusal,
            "hint": self.hint,
            "failed": self.failed,
            "cancelled": self.cancelled,
            "events": [
                {"kind": event.kind, "text": _redact(event.text), "data": event.data}
                for event in self.events
            ],
        }


def _sections_for(
    spec: RecipeSpec,
    collectors: Mapping[str, Callable[[datetime], SectionResult]] | None,
    fixture: Sequence[SectionResult] | None,
    now: datetime | None,
    inputs: Mapping[str, str] | None = None,
) -> tuple[SectionResult, ...]:
    if fixture is not None:
        return tuple(fixture)
    active = collectors
    if active is None and spec.collector_factory is not None:
        active = spec.collector_factory(dict(inputs or {}))
    return collect_sections(
        active, now=now, sections=spec.sections, titles=spec.section_titles,
        defaults=spec.collectors)


def _usage_value(events: Sequence[TurnEvent], key: str):
    """Read one usage metric from the last event that carries it; unknown stays None."""
    for event in reversed(events):
        usage = event.data.get("usage") if isinstance(event.data, dict) else None
        source = usage if isinstance(usage, dict) else (event.data if isinstance(event.data, dict) else {})
        if key in source and source[key] is not None:
            return source[key]
    return None


def run_recipe(
    spec: RecipeSpec,
    *,
    registry: Any | None = None,
    engine: str = "native",
    dry_run: bool = False,
    collectors: Mapping[str, Callable[[datetime], SectionResult]] | None = None,
    fixture: Sequence[SectionResult] | None = None,
    now: datetime | None = None,
    cancel: Any | None = None,
    inputs: Mapping[str, Any] | None = None,
) -> RecipeRun:
    """Run *spec* once. Raises ``UnknownRuntimeError`` for an unregistered engine
    and ``RecipeInputError`` when a declared input is missing."""
    resolved_inputs = normalize_inputs(spec, inputs)
    engine_name = str(engine or "native").strip() or "native"
    if registry is None:
        from agent.runtime_registry import build_registry

        registry = build_registry(_default_agent_factory, native_stream=True)
    backend = registry.get(engine_name)

    sections = _sections_for(spec, collectors, fixture, now, resolved_inputs)
    if dry_run:
        return RecipeRun(
            recipe_id=spec.id, engine=engine_name,
            brief=_redact(render_brief(spec, sections)),
            sections=sections, terminal="done")

    request = TurnRequest(
        prompt=build_prompt(spec, sections, now, resolved_inputs),
        session_id=f"recipe-{spec.id}-{uuid.uuid4().hex[:12]}",
    )
    started = time.perf_counter()
    events: list[TurnEvent] = []
    for event in backend.run_turn(request, cancel):
        events.append(event)
    latency_ms = max(0.0, (time.perf_counter() - started) * 1000.0)

    def record(response: str, *, success: bool, error_code: str | None = None) -> None:
        """Opt-in observability; disabled by default and never raises."""
        try_record_trace(
            prompt=request.prompt, response=response, session_id=request.session_id,
            engine=engine_name, tool_calls=tuple(
                event.data.get("name") or event.text for event in events if event.kind == "tool"),
            latency_ms=latency_ms, success=success, error_code=error_code,
            prompt_tokens=_usage_value(events, "prompt_tokens"),
            completion_tokens=_usage_value(events, "completion_tokens"),
            cost_usd=_usage_value(events, "cost_usd"),
            energy_joules=_usage_value(events, "energy_joules"),
            flops=_usage_value(events, "flops"))

    terminal = events[-1] if events else None
    if terminal is None or terminal.kind not in ("done", "error"):
        # A backend that ends on a delta (or yields nothing) did not complete a
        # turn; never treat partial text as a successful brief.
        record("", success=False, error_code="no_terminal_event")
        return RecipeRun(
            recipe_id=spec.id, engine=engine_name, brief="", sections=sections,
            events=tuple(events), terminal="", failed=True)

    if terminal.kind == "error":
        refusal = str(terminal.data.get("refusal") or "")
        cancelled = bool(terminal.data.get("cancelled")) or (
            cancel is not None and bool(getattr(cancel, "is_set", lambda: False)()))
        failed = not refusal and not cancelled
        if failed:
            record("", success=False, error_code=str(terminal.data.get("code") or "turn_failed"))
        return RecipeRun(
            recipe_id=spec.id, engine=engine_name, brief="", sections=sections,
            events=tuple(events), terminal="error", refusal=refusal,
            hint=str(terminal.data.get("hint") or ""),
            failed=failed, cancelled=cancelled)

    brief = _redact(terminal.text)
    if not brief.strip():
        record("", success=False, error_code="empty_response")
        return RecipeRun(
            recipe_id=spec.id, engine=engine_name, brief="", sections=sections,
            events=tuple(events), terminal="done", failed=True)
    record(brief, success=True)
    return RecipeRun(
        recipe_id=spec.id, engine=engine_name, brief=brief, sections=sections,
        events=tuple(events), terminal="done")
