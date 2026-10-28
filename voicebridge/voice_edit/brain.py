"""Optional model-based command router ("the Qubit brain decides").

The deterministic parser is the fast, predictable first pass. When it cannot
recognise an editing command, this router asks the ONE existing provider
gateway (local profile by default) to map the free-form utterance to a
supported action. It is advisory and monotonic-cautious:

* it can only choose an action from the fixed allowlist (never a new
  capability, never bypass the confirmation/preview);
* it never chooses verbatim ``dictate_text`` (that stays an explicit trigger);
* a refusal, timeout, or unparsable reply falls back to the deterministic
  clarification — never a silent rewrite.

This is the Qubit-owned seam the Jev routing plan will plug into later; it uses
the same turn text, the same gateway, the same audit, and adds no authority.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, Optional, Set

# Actions the router may choose (transform-only; no verbatim dictation).
ROUTER_ACTIONS: Set[str] = {
    "rewrite", "clarify", "humanize", "professionalize", "make_funny",
    "shorten", "expand", "summarize", "correct_grammar", "translate",
    "bullet_points", "email", "technical", "casual", "improve_prompt",
    "custom_transform",
}
ROUTER_STYLES: Set[str] = {
    "clear", "human", "professional", "funny", "casual", "concise",
    "persuasive", "technical", "friendly", "formal", "direct", "creative",
}

_SYSTEM = (
    "You are a strict routing classifier for a text-editing tool. The user "
    "selected text and spoke one command. Choose exactly ONE action from this "
    "list:\n" + ", ".join(sorted(ROUTER_ACTIONS)) + "\n"
    "Optionally add one style from: " + ", ".join(sorted(ROUTER_STYLES)) + "\n"
    'Reply with JSON only: {"action": "...", "style": "..."} (style may be '
    'empty). If no action fits, reply {"action": "unknown"}.'
)


def _runner_default(cfg: Any, *, system: str, user: str,
                    profile_id: Optional[str], timeout_s: float):
    from ..providers import gateway as GW
    return GW.run_role(
        user, cfg, role="classify", profile_id=profile_id or None,
        max_tokens=48, timeout_s=timeout_s, system_prompt=system,
        local_mode="generate", reasoning="low")


def _configured_model(cfg: Any) -> str:
    """Truthful configured model identity for the failure trace."""
    try:
        from ..providers import resolver
        return str(resolver.resolve(cfg, role=resolver.DEFAULT_ROLE).model)
    except Exception:
        return ""


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    t = (text or "").strip()
    if t.startswith("```"):
        m = re.match(r"^```[a-zA-Z0-9_+-]*\n(.*)\n```$", t, re.DOTALL)
        if m:
            t = m.group(1).strip()
    m = re.search(r"\{.*\}", t, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


class BrainRouter:
    def __init__(self, *, runner: Optional[Callable[..., Any]] = None) -> None:
        self._runner = runner

    def classify(self, cfg: Any, transcript: str, *, has_selection: bool,
                 timeout_s: float = 4.0,
                 cancel: Optional[Callable[[], bool]] = None
                 ) -> Optional[Dict[str, str]]:
        text = (transcript or "").strip()
        if not text:
            return None
        from ..logging_setup import log_event
        user = (
            f"Selected text present: {'yes' if has_selection else 'no'}.\n"
            f'Command: "{text[:500]}"')
        run = self._runner or _runner_default
        try:
            res = run(cfg, system=_SYSTEM, user=user,
                      profile_id=None, timeout_s=timeout_s)
        except Exception as exc:  # noqa: BLE001 - classifier is advisory
            # The cold-start limit must be visible: the classifier budget is
            # short by design (default 4 s) while a cold local load is much
            # longer. The budget is NOT changed here; this trace names the
            # timeout and model so the gap is diagnosable from the journal.
            log_event("voice_edit.brain.error",
                      code="BRAIN_CALL_FAILED", error=type(exc).__name__,
                      timeout_s=timeout_s, model=_configured_model(cfg))
            return None
        if not getattr(res, "ok", False):
            log_event("voice_edit.brain.error",
                      code=str(getattr(res, "code", "") or ""),
                      timeout_s=timeout_s,
                      model=str(getattr(res, "model", "") or
                                _configured_model(cfg)))
            return None
        data = _extract_json(str(getattr(res, "text", "") or ""))
        if not data:
            log_event("voice_edit.brain.unparsable")
            return None
        action = str(data.get("action", "") or "").strip().lower()
        style = str(data.get("style", "") or "").strip().lower()
        if action not in ROUTER_ACTIONS:
            log_event("voice_edit.brain.no_match", action=action[:40])
            return None
        if style not in ROUTER_STYLES:
            style = ""
        log_event("voice_edit.brain.decided", action=action, style=style,
                  model=str(getattr(res, "model", "") or ""),
                  provider=str(getattr(res, "provider", "") or ""))
        return {"action": action, "style": style}


__all__ = ["BrainRouter", "ROUTER_ACTIONS", "ROUTER_STYLES"]
