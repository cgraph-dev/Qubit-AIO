"""The ``jev_decide`` tool: typed decisions from a decision model, or a clear "unavailable".

Derived from hermes-jev-skills (MIT, Copyright (c) 2026 Steve Darlow); see ``NOTICE``.

One tool, one call, no hooks: Jev is an optional capability, so a turn that never needs a
typed decision must be identical to a Qubit turn today. When no decision-model key resolves,
``check_fn`` hides the tool and the handler reports ``unavailable`` with the reason — it never
guesses, never falls back to asking a chat model, and never invents a confidence.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from plugins.jev import client
from tools.registry import tool_error, tool_result

SCHEMA: Dict[str, Any] = {
    "name": "jev_decide",
    "description": (
        "Ask a decision model typed questions about one state and get calibrated answers back "
        "(a yes/no probability, a distribution over a closed set, or an expected level on a "
        "rubric). It never writes prose. Use it for cheap, repeatable judgements — triage, "
        "ranking, routing, a gate — not for open-ended reasoning. Optional: when no "
        "decision-model key is configured the tool reports 'unavailable' and you proceed "
        "without it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "state": {
                "type": ["string", "object", "array"],
                "description": "The material to judge. Keep it to the fields the questions need; a long state makes the answers worse.",
            },
            "questions": {
                "type": "object",
                "description": (
                    "Mapping of {name: question}. Each question is "
                    '{"type": "noul"|"choice"|"score", "instructions": str, "criteria": …} — '
                    'noul criteria are {"true": "what a yes means", "false": "what a no means"} (optional); '
                    'choice criteria are {"option": "what it means"} (2-255); '
                    "score criteria are a list of 2-10 levels, lowest first. Instructions are the "
                    "question; the name only labels it."
                ),
                "additionalProperties": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": list(client.QUESTION_TYPES)},
                        "instructions": {"type": ["string", "object", "array"]},
                        "criteria": {"type": ["object", "array"]},
                    },
                    "required": ["type", "instructions"],
                },
            },
            "provider": {
                "type": "string",
                "enum": [client.PROVIDER_ZEN, client.PROVIDER_TYPESAFE],
                "description": "Pin a decision provider. Omit to use whichever one is configured.",
            },
            "model": {"type": "string", "description": "Override the decision model id."},
        },
        "required": ["state", "questions"],
    },
}


def _preferred(ctx: Any) -> str:
    try:
        return str(ctx.get_config("provider", "") or "")
    except Exception:  # noqa: BLE001 - missing config means "no pin"
        return ""


# Set by ``register_tools``. The tool dispatcher only forwards signature-declared kwargs, so
# the manifest-bound context is captured here rather than injected per call.
_PLUGIN_CTX: Any = None


def check_available(ctx: Any = None) -> bool:
    """True only when a decision-model key actually resolves — the tool's gate."""
    return client.resolve_provider(_preferred(ctx) if ctx is not None else None) is not None


def _handler(args: Dict[str, Any], **kwargs: Any) -> str:
    ctx = _PLUGIN_CTX
    state = args.get("state")
    questions = args.get("questions")
    if state is None or not questions:
        return tool_error("jev_decide needs both 'state' and 'questions'")
    try:
        result = client.ask(
            state,
            questions,
            model=(args.get("model") or None),
            preferred_provider=(args.get("provider") or (_preferred(ctx) if ctx is not None else None) or None),
        )
    except ValueError as bad_question:  # a caller bug: say exactly what to fix
        return tool_error(f"invalid question: {bad_question}")
    except client.JevUnavailable as unavailable:
        return tool_result({
            "available": False,
            "code": unavailable.code,
            "reason": unavailable.detail or unavailable.code,
            "answers": {},
            "guidance": (
                "No decision model is available. Proceed without it — do not substitute a guess "
                "for a calibrated answer."
            ),
        })
    except Exception as error:  # noqa: BLE001 - a decision failure is never a turn failure
        return tool_result({"available": False, "code": "error", "reason": type(error).__name__,
                            "answers": {}, "guidance": "Proceed without the decision model."})
    return tool_result({"available": True, **result})


def register_tools(ctx: Any) -> None:
    global _PLUGIN_CTX
    _PLUGIN_CTX = ctx
    ctx.register_tool(
        name="jev_decide",
        toolset="jev",
        schema=SCHEMA,
        handler=_handler,
        check_fn=lambda: check_available(ctx),
        description="Typed decisions (yes/no probability, closed-set distribution, rubric level) — optional.",
        emoji="⚖️",
    )


def summarize(result: str) -> str:  # pragma: no cover - debugging aid
    return json.dumps(json.loads(result), indent=2)
