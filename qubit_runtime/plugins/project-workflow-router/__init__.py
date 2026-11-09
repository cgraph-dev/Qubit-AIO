"""project-workflow-router — make the app/project workflow unavoidable, not remembered.

``qubit-project-workflow`` is a router skill: it maps "create an app", "update the
app", "create a project" and their variants onto an ordered chain of ported
workflow skills (ask-matt -> grill-me -> implement-spec -> domain-modeling ->
implement, and the update/refactor chains). A skill is only followed if the model
thinks to load it, and a memory entry is only a hint — neither is a gate.

This plugin is the gate. On every turn it reads the user's message, and when the
message is asking to start/change/scope an app or project it returns a
``pre_llm_call`` context block naming the skill chain for that intent. The host
appends that block to the user message (never the system prompt, so the cached
prefix is untouched), which makes "load the router before writing code" the first
thing in front of the model rather than something it has to recall.

Design notes that matter:

* **Narrow trigger, not a mood.** Only a request to create/build/change/fix/refactor
  an app or project fires. A bare "fix this test" or "why is the build red" does not
  — those are ordinary debugging, and dragging a five-stage workflow into them would
  train the model to ignore this block.
* **Named chain, not a slogan.** The injection names the intent it matched, the
  exact skill to load first, and the order. Vague advice ("consider your process")
  is noise the model learns to skip.
* **Never blocks the turn.** Returning context cannot fail a request: a missing
  skill, a broken import or an unreadable message all degrade to no injection.
  ``QU_WORKFLOW_ROUTER_DISABLE=1`` is the kill switch.
* **One injection per turn.** The block is at most a few hundred bytes so it cannot
  meaningfully inflate a long conversation.

Why not a ``file_exists`` gate on the skill directory: the router must still fire
when the port is partially installed, because naming the gap is more useful than
silently falling back to no process at all.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional, Tuple

#: Kill switch. Any value present disables the router.
_DISABLE_ENV = "QU_WORKFLOW_ROUTER_DISABLE"

#: The skill that owns the mapping. The plugin does not duplicate the routing table;
#: it names this skill and the chain, so the two can never disagree about order.
ROUTER_SKILL = "qubit-project-workflow"

#: Intent -> (first skill to load, ordered chain, one-line scope note).
#:
#: Order mirrors ``ROUTES`` in qubit-project-workflow/SKILL.md. Kept as data here
#: because the hook must answer without loading the skill file on every turn.
CHAIN_BY_INTENT: Dict[str, Tuple[str, Tuple[str, ...], str]] = {
    "create_app": (
        "ask-matt",
        ("ask-matt", "grill-me", "implement-spec", "domain-modeling", "implement"),
        "build something that does not exist yet",
    ),
    "create_project": (
        "ask-matt",
        ("ask-matt", "domain-modeling", "implement-spec", "implement"),
        "start a fresh repo/project from nothing",
    ),
    "update_app": (
        "diagnosing-bugs",
        ("diagnosing-bugs", "implement-spec", "implement"),
        "something in a working app is broken",
    ),
    "change_app": (
        "improve-codebase-architecture",
        ("improve-codebase-architecture", "ask-matt", "implement-spec", "implement"),
        "add or change a feature in a working app",
    ),
    "refactor_project": (
        "improve-codebase-architecture",
        ("improve-codebase-architecture", "implement-spec", "implement"),
        "restructure or rework an existing project",
    ),
}

#: Words that make a message a create/change request, not ordinary conversation.
#: Deliberately verbs about the PRODUCT ("build", "scaffold", "ship"), not about a
#: repository's health ("failing test", "red build"), which is debugging.
_NOUN = r"(?:app|application|project|service|daemon|cli|bot|plugin)"

#: (intent, pattern). First match wins, so the more specific intents come first.
#: "broken/fix" is classified before "add/change" because a fix request that also
#: says "add" is a diagnosis first.
_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = tuple(
    (intent, re.compile(pattern, re.IGNORECASE))
    for intent, pattern in (
        # "the app is broken", "fix the X in my app", "my app stopped working" -> diagnose.
        (
            "update_app",
            rf"\b(?:fix|broken|crash(?:es|ing)?|not working|doesn'?t work|stopped working|bug)\b"
            rf"[\s\S]{{0,40}}\b{_NOUN}s?\b"
            rf"|\b{_NOUN}s?\b[\s\S]{{0,40}}\b(?:is |are |keeps? |stopped |started )?"
            rf"(?:broken|crashing|not working|failing|stopped working)\b",
        ),
        # "refactor my project", "restructure the app"
        (
            "refactor_project",
            rf"\b(?:refactor|restructure|re-?architect|clean up|rewrite)\b[\s\S]{{0,40}}\b{_NOUN}s?\b",
        ),
        # "create a project", "start a new project", "scaffold a repo"
        (
            "create_project",
            r"\b(?:create|start|begin|scaffold|bootstrap|set up|setup|init|initialise|initialize)\b"
            r"[\s\S]{0,30}\b(?:new\s+)?(?:project|repo|repository|codebase)\b",
        ),
        # "build me an app", "make me a X app", "new app", "I want to create an app"
        (
            "create_app",
            rf"\b(?:create|build|make|start|write|develop|ship|prototype|spin up|scaffold)\b"
            rf"[\s\S]{{0,30}}\b(?:an?|the|my|another)\s+(?:\w+\s+){{0,2}}{_NOUN}\b"
            rf"|\bnew\s+{_NOUN}\b"
            rf"|\bI\s+(?:want|need|'?d like)\s+to\s+(?:create|build|make|start)\b"
            rf"[\s\S]{{0,30}}\b{_NOUN}\b",
        ),
        # "add X to the app", "change the app", "update my project"
        (
            "change_app",
            rf"\b(?:add|change|update|extend|modify|improve|enhance|upgrade|rework)\b"
            rf"[\s\S]{{0,40}}\b{_NOUN}s?\b",
        ),
    )
)


def classify(message: str) -> Optional[str]:
    """The intent key for a user message, or None when it is not app/project work.

    Public so a test can pin the classification without going through the hook.
    """
    text = (message or "").strip()
    if not text:
        return None
    for intent, pattern in _PATTERNS:
        if pattern.search(text):
            return intent
    return None


def build_context(intent: str) -> str:
    """The injected block for one intent. Names the skill, the order, and the rule."""
    first, chain, scope = CHAIN_BY_INTENT[intent]
    steps = " → ".join(chain)
    return (
        "[Qubit project workflow — routed automatically]\n"
        f"This request is {scope} (intent: {intent}).\n"
        f"Before writing any code, load the router and follow its chain:\n"
        f"  skill_view(name='{ROUTER_SKILL}')  then  skill_view(name='{first}')\n"
        f"Order for this intent: {steps}\n"
        "Rule: understand → scope → spec → implement → verify. Do not open an editor, "
        "create a directory or write a file until the first skill in the chain has been "
        "loaded. If a skill in the chain is missing, say so and name the gap — do not "
        "silently skip the stage or invent its content."
    )


def _text_of(user_message: Any) -> str:
    """The user's words, whatever shape the host handed us.

    The hook receives the raw message, which is a string on most platforms but a
    list of content blocks or a dict when the client sent structured content. An
    unreadable message is "no match", never an exception.
    """
    if isinstance(user_message, str):
        return user_message
    if isinstance(user_message, dict):
        for key in ("text", "content", "message"):
            value = user_message.get(key)
            if isinstance(value, str):
                return value
        return ""
    if isinstance(user_message, (list, tuple)):
        parts = []
        for item in user_message:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return " ".join(parts)
    return ""


def _on_pre_llm_call(user_message: Any = None, **_: Any) -> Optional[Dict[str, str]]:
    """Inject the routed workflow when the turn is app/project work; None otherwise."""
    if os.environ.get(_DISABLE_ENV):
        return None
    try:
        intent = classify(_text_of(user_message))
    except Exception:  # health: allow BLE001 -- a classifier failure must not fail the turn
        return None
    if intent is None:
        return None
    try:
        return {"context": build_context(intent)}
    except Exception:  # health: allow BLE001 -- same: never break the request
        return None


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
