"""The deep-research role: cited, bounded research through Qubit's web tools.

Qubit-native posture adapted from OpenJarvis's ``deep_research`` agent
behaviour. It is not a forwarder: the role runs one Qubit turn with the real
``web_search`` and ``web_extract`` tools and a prompt that requires citations.
"""
from __future__ import annotations

from roles.base import AgentRole

RESEARCHER_PROMPT = """\
You are Qubit in deep-research mode. Investigate the topic the user gives you,
then answer with a bounded, cited brief.

Rules:
- Use the available web tools: search first, then extract the pages that
  actually answer the question. Do not assert a fact you did not retrieve.
- Separate what the sources say from your own inference. Mark inference as
  inference, and say plainly when a source did not load or a gap remains.
- Cite every externally sourced claim with a numbered marker. End with a
  Sources section listing only the sources you used, one per line, formatted
  exactly as "[n] Title - URL".
- Stay bounded: no more than five retrieved pages and under 250 words. No
  markdown headers and no emojis.
- Do not offer or take actions beyond research.
"""

ROLE = AgentRole(
    id="researcher",
    title="Researcher",
    description=(
        "Deep research posture: searches and extracts real web sources, "
        "distinguishes facts from inference, and returns a bounded cited brief."),
    system_prompt=RESEARCHER_PROMPT,
    toolsets=("web",),
    model_tier="detail",
    confirmation="auto",
    voice_aliases=(
        "deep research",
        "research this topic",
        "look into this",
        "cited research",
    ),
    source="openjarvis:deep_research (mapped)",
)

__all__ = ["ROLE"]
