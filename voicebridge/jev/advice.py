"""Advisory Jev verdict on the turn-classification seam.

The first real Jev seam (see :mod:`voicebridge.jev.seams`, id
``turn-classification``). Jev never decides anything here: the local
classifier stays authoritative, and a Jev verdict can only *re-rank* a turn the
local path already called ambiguous. Off by default, fail-open by construction.

Three properties this module guarantees, each of them a rule from the seam map:

1. **Opt-in.** Nothing calls Jev unless ``jev.enabled`` is true in config. An
   absent, false, or unreadable setting means "local only".
2. **Local stays authoritative.** Only kinds named in ``jev.rerank_kinds`` (by
   default ``clarify`` and ``answer_local``) may be changed at all, and a
   verdict with low confidence is discarded.
3. **Never a fabricated judgment.** Jev's client fails closed with a typed
   refusal (``ok: False`` + ``JEV_*``). A refusal here means "keep the local
   answer" — never a substituted one.

The verdict is returned, not applied: callers decide, and today the only
caller logs it. That keeps the blind-spot small while the live path is proved
with a real key.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

#: Kinds the local classifier may leave genuinely ambiguous. A confident local
#: routing (``route-word``, ``registry``, ``agent_task``, ``refuse``) is never
#: offered to Jev: those are decisions, not guesses.
DEFAULT_RERANK_KINDS: Tuple[str, ...] = ("clarify", "answer_local")

#: Below this, a Jev verdict is treated as "no opinion".
MIN_CONFIDENCE = 0.6

#: The actions Jev may choose between — the same set the seam map names.
RERANK_ACTIONS: Tuple[str, ...] = ("answer_local", "web_search", "local_action",
                                   "registry", "agent_task", "clarify", "refuse")

#: Environment switch for tests and no-key hosts (mirrors the client's own).
MOCK_ENV_VAR = "JEV_BACKEND"


def _cfg_get(cfg: Any, *path: str, default: Any = None) -> Any:
    """Read a dotted config path, tolerating dicts and config objects alike."""
    if cfg is None:
        return default
    getter = getattr(cfg, "get", None)
    if callable(getter):
        try:
            return getter(*path, default=default)
        except TypeError:
            pass
        try:
            node: Any = cfg
            for seg in path:
                node = node.get(seg) if isinstance(node, dict) else None
                if node is None:
                    return default
            return node
        except Exception:  # noqa: BLE001 - a bad cfg is "not configured"
            return default
    return default


def enabled(cfg: Any) -> bool:
    """True when the operator opted in to advisory Jev on this seam."""
    if os.environ.get(MOCK_ENV_VAR, "").strip().lower() == "mock":
        return True
    return bool(_cfg_get(cfg, "jev", "enabled", default=False))


def rerank_kinds(cfg: Any) -> Tuple[str, ...]:
    """Kinds Jev may re-rank. Defaults to the ambiguous pair."""
    configured = _cfg_get(cfg, "jev", "rerank_kinds", default=None)
    if isinstance(configured, (list, tuple)) and configured:
        allowed = tuple(str(k).strip() for k in configured if str(k).strip())
        # Never let config widen this to a kind the seam map forbids.
        return tuple(k for k in allowed if k in DEFAULT_RERANK_KINDS)
    return DEFAULT_RERANK_KINDS


def should_ask(decided: Dict[str, Any], cfg: Any) -> bool:
    """True when this turn is a candidate for an advisory verdict.

    A turn is a candidate only if Jev is enabled AND the local classifier
    landed on a kind it is allowed to re-rank. Everything else is final.
    """
    if not enabled(cfg):
        return False
    kind = str((decided or {}).get("kind") or "")
    return kind in rerank_kinds(cfg)


def question_for(text: str, decided: Dict[str, Any]) -> Dict[str, Any]:
    """The native-dialect ``pick`` question for this turn."""
    return {
        "id": "turn-action",
        "kind": "pick",
        "ask": "Which turn action for this utterance?",
        "options": list(RERANK_ACTIONS),
        "state": {
            "utterance": str(text or "")[:2000],
            "local_action": str((decided or {}).get("action") or ""),
            "local_kind": str((decided or {}).get("kind") or ""),
            "local_reason": str((decided or {}).get("reason") or "")[:400],
        },
    }


def advise(text: str, decided: Dict[str, Any], cfg: Any,
           *, client: Any = None) -> Dict[str, Any]:
    """Ask Jev for an advisory action; never raises, never overrides.

    Returns ``{"asked": bool, "applied": bool, "action": str, "confidence":
    float, "reason": str}``. ``applied`` is False whenever the verdict was
    unavailable, refused, low-confidence, or out of the allowed action set —
    in every one of those cases the caller keeps the local answer.
    """
    out: Dict[str, Any] = {"asked": False, "applied": False, "action": "",
                           "confidence": 0.0, "reason": ""}
    try:
        if not should_ask(decided, cfg):
            out["reason"] = "not a candidate"
            return out
        # Imported lazily: a host without the jev package still routes turns.
        from . import zen as zen_backend

        ask = zen_backend if client is None else client
        out["asked"] = True
        question = question_for(text, decided)
        # The client takes state and questions separately; the state lives on
        # the question record so the verdict can quote what it was asked about.
        #
        # Zen is the default door: it answers the same decision contract at
        # cost 0 with no credential (see voicebridge/jev/zen.py). The OpenRouter
        # alpha path in .client needs a paid key and is reached only when a
        # caller passes that client explicitly.
        result = ask.judge(question["state"]["utterance"], [question])
        if not dict(result or {}).get("ok"):
            # Typed refusal (JEV_KEY_ABSENT / JEV_TIMEOUT / ...): keep local.
            out["reason"] = str((result or {}).get("code")
                                or "JEV_UNAVAILABLE")
            return out
        answers = list((result or {}).get("answers") or [])
        pick = next((a for a in answers
                     if str(a.get("id")) == "turn-action"), None)
        if pick is None:
            out["reason"] = "no verdict for this question"
            return out
        confidence = float(pick.get("confidence") or 0.0)
        out["confidence"] = confidence
        action = str(pick.get("answer") or "")
        if bool(pick.get("escalate")) or confidence < MIN_CONFIDENCE:
            out["reason"] = "low confidence; local answer kept"
            return out
        if action not in RERANK_ACTIONS:
            out["reason"] = f"verdict {action!r} is not an allowed action"
            return out
        out["action"] = action
        out["applied"] = action != str((decided or {}).get("action") or "")
        out["reason"] = "verdict accepted" if out["applied"] else \
            "verdict agrees with the local answer"
        return out
    except Exception as exc:  # noqa: BLE001 - advisory means never fatal
        out["reason"] = f"{type(exc).__name__}: {exc}"[:200]
        return out
