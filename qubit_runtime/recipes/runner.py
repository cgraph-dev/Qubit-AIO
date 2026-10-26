"""Execute one recipe turn through the RuntimePort.

Deterministic collectors run first; the selected backend only writes the
brief from their facts. The native backend is the default. A non-native
engine that cannot run is a typed refusal (exit 2), never a silent fallback.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from agent.redact import redact_for_egress
from agent.runtime_port import TurnEvent, TurnRequest
from recipes.morning_digest import collect_sections, render_brief, render_facts
from recipes.sections import SectionResult
from recipes.spec import RecipeSpec


def _default_agent_factory(request: TurnRequest) -> Any:
    from qubit_cli.runtime_cmd import _native_factory

    return _native_factory(request)


def _redact(value: object) -> str:
    try:
        return redact_for_egress(str(value or ""))
    except Exception:
        return ""


def build_prompt(spec: RecipeSpec, sections: Sequence[SectionResult],
                 now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    today = moment.astimezone().strftime("%A, %B %d, %Y")
    return str(spec.prompt).replace("{facts}", render_facts(sections)).replace(
        "{today}", today)


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
) -> tuple[SectionResult, ...]:
    if fixture is not None:
        return tuple(fixture)
    return collect_sections(collectors, now=now, sections=spec.sections)


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
) -> RecipeRun:
    """Run *spec* once. Raises ``UnknownRuntimeError`` for an unregistered engine."""
    engine_name = str(engine or "native").strip() or "native"
    if registry is None:
        from agent.runtime_registry import build_registry

        registry = build_registry(_default_agent_factory, native_stream=True)
    backend = registry.get(engine_name)

    sections = _sections_for(spec, collectors, fixture, now)
    if dry_run:
        return RecipeRun(
            recipe_id=spec.id, engine=engine_name,
            brief=_redact(render_brief(spec, sections)),
            sections=sections, terminal="done")

    request = TurnRequest(
        prompt=build_prompt(spec, sections, now),
        session_id=f"recipe-{spec.id}-{uuid.uuid4().hex[:12]}",
    )
    events: list[TurnEvent] = []
    for event in backend.run_turn(request, cancel):
        events.append(event)

    terminal = events[-1] if events else None
    if terminal is None:
        return RecipeRun(
            recipe_id=spec.id, engine=engine_name, brief="", sections=sections,
            failed=True)

    if terminal.kind == "error":
        refusal = str(terminal.data.get("refusal") or "")
        cancelled = bool(terminal.data.get("cancelled")) or (
            cancel is not None and bool(getattr(cancel, "is_set", lambda: False)()))
        return RecipeRun(
            recipe_id=spec.id, engine=engine_name, brief="", sections=sections,
            events=tuple(events), terminal="error", refusal=refusal,
            hint=str(terminal.data.get("hint") or ""),
            failed=not refusal and not cancelled, cancelled=cancelled)

    brief = _redact(terminal.text)
    if not brief.strip():
        return RecipeRun(
            recipe_id=spec.id, engine=engine_name, brief="", sections=sections,
            events=tuple(events), terminal="done", failed=True)
    return RecipeRun(
        recipe_id=spec.id, engine=engine_name, brief=brief, sections=sections,
        events=tuple(events), terminal="done")
