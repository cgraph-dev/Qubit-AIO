"""Typed contract for Qubit's native agent roles.

A role is a Qubit-owned preset for the ONE agent loop: a system prompt, an
allowed toolset set, a model tier and a confirmation posture. Roles are not a
second runtime: they run through the same ``RuntimePort`` / ``AIAgent`` and the
same authority chain as every other turn.

OpenJarvis's eight agents are the reference set (``docs/superpowers/plans/
2026-10-27-openjarvis-agent-absorption.md``); the role content here is authored
Qubit-native and never imports or shells out to OpenJarvis.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

MODEL_TIERS = ("local", "detail", "api", "")
CONFIRMATION_MODES = ("preview", "confirm", "auto")


@dataclass(frozen=True)
class AgentRole:
    """One selectable Qubit agent posture."""

    id: str
    title: str
    description: str
    system_prompt: str = ""
    toolsets: tuple[str, ...] = ()
    model_tier: str = "local"
    confirmation: str = "preview"
    voice_aliases: tuple[str, ...] = ()
    recipe_id: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        if not str(self.id or "").strip():
            raise ValueError("AgentRole.id is required")
        if not str(self.title or "").strip():
            raise ValueError(f"AgentRole({self.id}) title is required")
        if not str(self.description or "").strip():
            raise ValueError(f"AgentRole({self.id}) description is required")
        if self.model_tier not in MODEL_TIERS:
            raise ValueError(
                f"AgentRole({self.id}) model_tier must be one of {MODEL_TIERS}")
        if self.confirmation not in CONFIRMATION_MODES:
            raise ValueError(
                f"AgentRole({self.id}) confirmation must be one of "
                f"{CONFIRMATION_MODES}")
        if not self.recipe_id and not str(self.system_prompt or "").strip():
            raise ValueError(
                f"AgentRole({self.id}) needs a system_prompt or a recipe_id")
        if not self.voice_aliases:
            raise ValueError(f"AgentRole({self.id}) needs at least one voice alias")

    def to_wire(self) -> dict[str, Any]:
        """JSON-safe view for CLI/voice/tests (tuples become lists)."""
        data = asdict(self)
        data["toolsets"] = list(self.toolsets)
        data["voice_aliases"] = list(self.voice_aliases)
        return data


__all__ = ["AgentRole", "MODEL_TIERS", "CONFIRMATION_MODES"]
