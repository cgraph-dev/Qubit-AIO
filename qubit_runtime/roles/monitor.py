"""The operative-monitor role: an alias to Qubit's existing recipe.

The prompt, collectors, schedule and approval policy live in
``recipes/monitor_operative.py``. This role only makes that recipe reachable as
an agent posture, so the text is never duplicated here.
"""
from __future__ import annotations

from roles.base import AgentRole

ROLE = AgentRole(
    id="monitor",
    title="Operative monitor",
    description=(
        "Read-only health report over cron, kanban, memory and compression, "
        "dispatched through the existing monitor_operative recipe."),
    recipe_id="monitor_operative",
    model_tier="local",
    confirmation="auto",
    voice_aliases=(
        "operative status",
        "monitor my work",
        "status of my work",
        "how is my work doing",
        "monitor operative",
    ),
    source="openjarvis:monitor_operative (recipe)",
)

__all__ = ["ROLE"]
