"""The morning-digest role: an alias to Qubit's existing recipe.

The prompt, collectors, schedule and approval policy live in
``recipes/morning_digest.py``. This role only makes that recipe reachable as an
agent posture, so the text is never duplicated here.
"""
from __future__ import annotations

from roles.base import AgentRole

ROLE = AgentRole(
    id="digest",
    title="Morning digest",
    description=(
        "Start-of-day brief from weather, calendar, unread mail and health, "
        "dispatched through the existing morning_digest recipe."),
    recipe_id="morning_digest",
    model_tier="local",
    confirmation="auto",
    voice_aliases=(
        "start my day",
        "morning digest",
        "daily digest",
        "give me my morning digest",
    ),
    source="openjarvis:morning_digest (recipe)",
)

__all__ = ["ROLE"]
