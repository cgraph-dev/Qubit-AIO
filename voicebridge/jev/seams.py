"""Jev integration-point map (READ-ONLY analysis — no wiring).

Each entry names the exact hook location (function:line), the Jev question
we would ask there, and the fallback while Jev is unreachable (today's
local-heuristic behavior, which stays the default until the key-unlock
step lands). Nothing in this module imports heavy machinery or mutates
shared state — it is a written map for the wiring lane.

Question kinds: ``check`` (boolean), ``pick`` (one of options), ``rate``
(0-1). Verdicts are advisory to the existing authority path (broker /
policy / audit / confirmation / e-stop); Jev never executes.
"""
from __future__ import annotations

from typing import Any, Dict, List

SEAMS: List[Dict[str, Any]] = [
    {
        "id": "turn-classification",
        "hook": "voicebridge/chat_transport.py:835 "
                "decide_turn_action(text, cfg=..., intent=...)",
        "today": "Pure local classification via "
                 "voicebridge.intent.classifier.Classifier projected onto "
                 "answer_local / web_search / local_action / registry / "
                 "agent_task / clarify / refuse (no side effects).",
        "jev_question": {
            "kind": "pick",
            "ask": "Which turn action for this utterance?",
            "options": ["answer_local", "web_search", "local_action",
                        "registry", "agent_task", "clarify", "refuse"],
        },
        "fallback": "Keep decide_turn_action exactly as-is (local "
                    "heuristics stay authoritative; Jev verdict, when "
                    "available, re-ranks only ambiguous kinds).",
    },
    {
        "id": "destructive-tool-risk-gate",
        "hook": "voicebridge/broker.py:535 "
                "CapabilityBroker.execute(...) "
                "(destructive screen :579-590; DESTRUCTIVE two-step "
                "confirm :624-631) + voicebridge/dispatch_guard.py:98 "
                "WorkerGuard.authorize(...)",
        "today": "Packet 4D destructive-intent tripwire fails closed; "
                 "DESTRUCTIVE-tier capabilities need a user-issued "
                 "confirmation id (two-step spoken flow); scoped-autonomy "
                 "profiles may pre-authorize.",
        "jev_question": {
            "kind": "rate",
            "ask": "How risky is this capability+args in this turn "
                   "context (0-1)?",
        },
        "fallback": "Keep the tripwire + two-step confirm exactly as-is; "
                    "a Jev risk score, when available, would only raise "
                    "the confirm bar (never lower it below today's "
                    "policy).",
    },
    {
        "id": "tier-effort-pick",
        "hook": "voicebridge/providers/endpoints.py:769 select_model(...) "
                "+ :425 _gate_remote_liveness(...) + "
                "voicebridge/providers/resolver.py:881 _pick(...) / :936 "
                "resolve(...) / :954 resolve_model(...)",
        "today": "Explicit picker/CLI/voice picks via select_model with "
                 "the bounded liveness probe gate (MODEL_UNAVAILABLE "
                 "refusal, slot unchanged); silent paths resolve the "
                 "local/detail/API tiers from config.",
        "jev_question": {
            "kind": "pick",
            "ask": "Which effort tier serves this turn?",
            "options": ["fast-local", "detail-local", "api-escalate"],
        },
        "fallback": "Keep today's resolver + liveness gate; a Jev tier "
                    "pick, when available, would only choose AMONG "
                    "reachable tiers (dead-record models stay refused).",
    },
    {
        "id": "context-compaction-verdict",
        "hook": "voicebridge/session_compaction.py:174 compact(...) + "
                "voicebridge/context_policy.py:174 decide_context(...)",
        "today": "decide_context picks the fast/expanded/escalated "
                 "window from token estimates; compact() checkpoints "
                 "then trims or runs the vendored summariser "
                 "(UNAVAILABLE when the compressor cannot run).",
        "jev_question": {
            "kind": "check",
            "ask": "Is this transcript safe to compact now (no open "
                   "task/confirm pending)?",
        },
        "fallback": "Keep the watermark + checkpoint + manual-trim path; "
                    "a Jev verdict, when available, would only gate the "
                    "summariser branch (never skip the checkpoint).",
    },
]


def describe() -> List[Dict[str, Any]]:
    """Return a JSON-safe copy of the seam map (no imports, no I/O)."""
    return [dict(s) for s in SEAMS]
