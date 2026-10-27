"""The fast-chat role: short, direct answers with minimal tooling.

Qubit-native posture adapted from OpenJarvis's ``simple`` agent behaviour. It
adds no runtime of its own: the role only narrows the one Qubit loop to a
read-only, low-latency shape.
"""
from __future__ import annotations

from roles.base import AgentRole

SIMPLE_PROMPT = """\
You are Qubit in fast-chat mode. Answer the user's question directly and
briefly. Prefer one or two short paragraphs; use a list only when the answer is
genuinely enumerable.

Rules:
- Do not fabricate facts, sources, names, numbers, or actions you did not take.
  If you do not know, say so plainly and name what would settle it.
- Do not claim to have run a tool when you did not. This posture exposes only a
  read-only web search; use it when the question needs current information and
  say what you searched otherwise.
- Do not propose multi-step plans or offer capabilities this posture does not
  have. Stay in the answer.
- No filler, no restating the question, no markdown headers or emojis.
"""

ROLE = AgentRole(
    id="simple",
    title="Simple",
    description=(
        "Fast chat posture: direct, short answers with a read-only web search "
        "and no fabrication."),
    system_prompt=SIMPLE_PROMPT,
    toolsets=("search",),
    model_tier="local",
    confirmation="auto",
    voice_aliases=(
        "simple mode",
        "quick answer",
        "fast chat",
        "just answer me",
    ),
    source="openjarvis:simple (mapped)",
)

__all__ = ["ROLE"]
