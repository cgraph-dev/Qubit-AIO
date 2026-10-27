"""The typed contract for a Qubit recipe.

A recipe is a *product* concept: a named prompt, the deterministic collectors
that gather its facts, the capabilities/tools it needs, its default schedule,
and the policy it runs under. Recipes execute through the RuntimePort, so the
same recipe can run on the native engine or on an optional out-of-process
engine without changing its contract.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Mapping

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


@dataclass(frozen=True)
class RecipeSpec:
    """One bounded recipe.

    ``prompt`` is a template. It may use ``{facts}`` (the rendered, redacted
    section facts) and ``{today}`` (the local date). The template keeps recipe
    text inspectable and testable instead of buried in the runner.
    """

    id: str
    title: str
    description: str
    prompt: str
    sections: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    required_tools: tuple[str, ...]
    default_schedule: str
    approval_policy: str
    output_format: str
    voice_aliases: tuple[str, ...] = field(default_factory=tuple)
    # Human section labels, used when a section degrades before its collector
    # returns (otherwise an unavailable section would render as a raw id).
    section_titles: Mapping[str, str] = field(default_factory=dict)
    # Inputs the caller must supply (e.g. ``topic`` for deep_research). Input
    # values are substituted into the prompt as ``{name}`` and are never
    # serialized to callers.
    input_fields: tuple[str, ...] = field(default_factory=tuple)
    # Deterministic collectors keyed by section id. Runtime data, not serialized:
    # the runner calls them before any model turn and passes their facts to the prompt.
    collectors: Mapping[str, Callable] = field(
        default_factory=dict, repr=False, compare=False, hash=False)
    # Optional per-run collector factory for recipes whose collectors need the
    # caller's inputs (it receives the normalized input mapping). Runtime data,
    # never serialized; ``collectors`` still wins when a caller passes one.
    collector_factory: Callable[[Mapping[str, str]], Mapping[str, Callable]] | None = field(
        default=None, repr=False, compare=False, hash=False)

    def __post_init__(self) -> None:
        if not _ID_RE.fullmatch(self.id or ""):
            raise ValueError(f"invalid recipe id {self.id!r}: use [a-z][a-z0-9_]{{1,63}}")
        for name in ("title", "description", "prompt", "default_schedule",
                     "approval_policy", "output_format"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"recipe {self.id!r}: {name} must not be empty")
        if not self.sections:
            raise ValueError(f"recipe {self.id!r}: at least one section is required")
        if len(set(self.sections)) != len(self.sections):
            raise ValueError(f"recipe {self.id!r}: sections must be unique")
        reserved = {"facts", "today"}
        for name in self.input_fields:
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", str(name or "")):
                raise ValueError(f"recipe {self.id!r}: invalid input field {name!r}")
            if name in reserved:
                raise ValueError(f"recipe {self.id!r}: input field {name!r} is reserved")
        if len(set(self.input_fields)) != len(self.input_fields):
            raise ValueError(f"recipe {self.id!r}: input fields must be unique")

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "sections": list(self.sections),
            "required_capabilities": list(self.required_capabilities),
            "required_tools": list(self.required_tools),
            "default_schedule": self.default_schedule,
            "approval_policy": self.approval_policy,
            "output_format": self.output_format,
            "voice_aliases": list(self.voice_aliases),
            "section_titles": dict(self.section_titles),
            "inputs": list(self.input_fields),
        }
