"""The `hermes-agent` skill is what a running Hermes knows about itself.

`website/` is never packaged, so an installed Hermes has no local copy of the
user guide; skills ARE synced into `$QUBIT_HOME/skills/`. The skill therefore
does not try to restate the product — it routes to the published `llms.txt`,
which is generated from the docs tree on every build and so can never be behind
the feature set. These tests keep that routing honest: the index has to be where
the skill says it is, and every reference has to be reachable, otherwise a
shipped feature is invisible and the agent answers "Hermes can't do that."
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO / "skills" / "autonomous-ai-agents" / "qubit-agent"
SKILL_MD = SKILL_DIR / "SKILL.md"


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_MD.read_text(encoding="utf-8")


def test_every_referenced_file_exists(skill_text):
    """Routing a question to a file that isn't there is a dead end."""
    targets = set(re.findall(r"`((?:references|templates)/[^`]+)`", skill_text))

    assert targets, "the skill's routing table no longer references any files"
    for target in sorted(targets):
        assert (SKILL_DIR / target).exists(), f"SKILL.md routes to missing {target}"


def test_every_reference_is_reachable_from_the_skill(skill_text):
    """An unrouted reference is one the agent will never think to open.

    This is the failure that produced the original complaint: content can exist
    and still be invisible because nothing points at it.
    """
    on_disk = {f"references/{path.name}" for path in (SKILL_DIR / "references").glob("*.md")}
    routed = set(re.findall(r"`(references/[^`]+)`", skill_text))

    assert not (on_disk - routed), (
        f"reference files no reader will ever reach: {sorted(on_disk - routed)} — "
        "add a routing-table row in SKILL.md"
    )


def test_unknown_features_route_to_the_published_index(skill_text):
    """The catch-all is what makes coverage of the whole product possible.

    The routing target is the Qubit-owned docs endpoint. The de-brand pipeline
    (``agent.skill_utils``) collapses the upstream docs host to this endpoint,
    so it is what a shipped skill can actually point at.
    """
    assert "/agent/openapi.json" in skill_text
    # web_extract can be disabled; terminal never is.
    assert "curl" in skill_text, "no way to reach the index without web tools"


def test_the_index_is_published_where_the_skill_says_it_is(skill_text):
    """A skill pointing at an endpoint nobody serves is worse than no routing.

    The upstream docs host must be rewritten by the de-brand pipeline to the
    endpoint the skill names; this ties the skill's routing target to what the
    pipeline actually emits.
    """
    from agent.skill_utils import _DEBRAND_DOCS_ENDPOINT, debrand_skill_text

    assert _DEBRAND_DOCS_ENDPOINT in skill_text
    rendered = debrand_skill_text(
        "See https://hermes-agent.nousresearch.com/docs/llms.txt", is_doc=True
    )
    assert _DEBRAND_DOCS_ENDPOINT in rendered
