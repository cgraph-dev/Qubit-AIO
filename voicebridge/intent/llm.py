"""LLM-assisted intent classification via the local Ollama API.

Uses the resident baked tier (qubit-brain, no-think template) with strict
JSON output. Falls back gracefully (returns None) when Ollama is down so the
deterministic router still works.
"""
from __future__ import annotations

import json
import re
import urllib.request  # noqa: F401 - re-exported for callers/tests that patch this seam
from typing import Any, Dict, Optional

from ..logging_setup import log_event

SYSTEM_PROMPT = """You are the intent router of a voice assistant that controls an
Qubit worker. Classify the user's spoken utterance into EXACTLY ONE kind:
"""

# CopilotKit-style tool schema (STEAL_LIST item 3): each route is defined as a
# structured tool with name, description, and example triggers so the model's
# classification is grounded in explicit contracts -> fewer routing mistakes,
# fewer wasted turns, fewer model calls.
TOOL_SCHEMA = """
<tool name="system">
  Control of the voice assistant itself: status, mute, unmute, stop, help, model switch.
  Triggers: "are you there", "mute", "stop", "switch to pro".
</tool>
<tool name="confirm">
  A clear yes/affirmative answer to a pending confirmation.
  Triggers: "yes", "yeah", "go ahead", "apply it", "do it".
</tool>
<tool name="cancel">
  A clear no/negative, or a request to stop/abort current work.
  Triggers: "no", "cancel", "never mind", "stop it".
</tool>
<tool name="coding">
  A task for the Qubit worker.
  Triggers: open/inspect/patch/test/fix/refactor/run/review/commit/deploy,
  anything touching files, code, repos, terminals, browsers, or servers;
  questions about code that need inspecting a project ("why does X fail?").
</tool>
<tool name="local">
  A direct PC/local action from the whitelist — never file/system surgery.
  Triggers: volume up/down, mute mic, open an app, screenshot, media play/pause,
  clipboard, lock screen.
</tool>
<tool name="agent">
  The user explicitly asks Qubit to do something.
  Triggers: "ask Qubit to ...", "Qubit, ...", "hand this to Qubit".
</tool>
<tool name="conversation">
  Casual chat, general knowledge questions, small talk — NOT a task.
  Triggers: "how are you", "what is the capital of France".
</tool>
<tool name="clarify">
  The user asks for clarification or the request is too vague to act on.
  Triggers: "what do you mean", "hmm... maybe... you know...".
</tool>

Rules:
- Volume/sound/mute/app-launch/screenshot/media/clipboard/lock phrases are "local".
- Commands that touch files, code, repos, terminals, browsers, or servers are "coding".
- Questions about code ("why does X fail?", "how does Y work?") are "coding" when
  they need inspecting a project; pure general knowledge questions are "conversation".
- If the utterance is garbled or NOT understandable English, kind is "clarify"
  — NEVER guess "coding" for text you cannot understand.
- Respond ONLY with compact JSON matching the provided schema:
  {"kind": "<one kind>", "confidence": 0.0-1.0}. No task text, no reason,
  no extra fields."""

SYSTEM_PROMPT = SYSTEM_PROMPT + TOOL_SCHEMA

VALID_KINDS = ("system", "confirm", "cancel", "coding", "conversation",
               "clarify", "local", "agent")

# Compact typed decision schema (Packet 4B): full JSON Schema for Ollama
# structured outputs — enum-locked kind, bounded confidence, nothing else.
# Free-text task/reason fields cost eval tokens on every hot-path call and
# are synthesized locally after validation instead (plan Phase 4 task 7).
DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": list(VALID_KINDS)},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["kind"],
    "additionalProperties": False,
}


def _parse_json_loose(raw: str) -> Optional[Dict[str, Any]]:
    """Parse the model's JSON reply, tolerating ``` fences / stray prose."""
    raw = (raw or "").strip()
    if not raw:
        return None
    # strip markdown fences if present
    m = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if m:
        raw = m.group(1).strip()
    try:
        return json.loads(raw)
    except Exception:
        pass
    # last resort: first {...} span
    start, end = raw.find("{"), raw.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(raw[start:end + 1])
        except Exception:
            return None
    return None


def classify_local_llm(text: str, cfg: Any = None,
                       url: Optional[str] = None,
                       model: Optional[str] = None,
                       timeout_s: float = 8.0) -> Optional[Dict[str, Any]]:
    """Returns {kind, task, confidence, reason} or None if the local model is
    unavailable or the decision fails schema validation (typed no-decision).

    Execution goes through the canonical provider authority: the effective
    ``classify`` profile is resolved by ``providers.resolver`` and the call runs
    through ``providers.gateway.run`` (local route, no frontier network). The
    legacy ``url``/``model`` hints are accepted for call-site compatibility;
    the resolver decides the actual runner.
    """
    from ..providers import gateway as _gateway

    prompt = f"User said: \"{text[:600]}\"\n\nClassify it. /no_think"
    res = _gateway.run_role(
        prompt, cfg, role="classify", max_tokens=64, timeout_s=timeout_s,
        local_mode="generate", system_prompt=SYSTEM_PROMPT,
        structured_schema=DECISION_SCHEMA)
    if not res.ok:
        log_event("intent.llm.unavailable", code=res.code,
                  error=res.message[:160])
        return None
    raw = res.text
    parsed = _parse_json_loose(raw)
    if not isinstance(parsed, dict):
        return None
    kind = parsed.get("kind")
    if kind not in VALID_KINDS:
        return None
    confidence = parsed.get("confidence")
    if not isinstance(confidence, (int, float)) or not (
            0.0 <= float(confidence) <= 1.0):
        confidence = 0.6
    # Compact typed decision -> downstream shape preserved without
    # asking the model for free text (hot-path eval tokens saved).
    parsed.setdefault("task", "" if kind != "coding" else text)
    parsed["confidence"] = round(float(confidence), 3)
    parsed.setdefault("reason", f"{kind}:schema")
    # Packet 4D structural mitigation: destructive-intent screen.
    # A model claiming "local" for destruction text is overridden to the
    # confirmation-gated coding path - independent of model behavior.
    if kind == "local":
        from ..safety.destructive_screen import screen_destructive
        scr = screen_destructive(text)
        if scr.matched:
            log_event("intent.destructive_screen",
                      claimed="local", route="coding",
                      reason=scr.reason)
            kind = "coding"
            parsed["kind"] = "coding"   # returned dict must reflect it
            parsed["task"] = text
            parsed["confidence"] = 0.99
            parsed["reason"] = f"destructive-screen:{scr.reason}"
    log_event("intent.llm", kind=kind, confidence=parsed.get("confidence"),
              reason=parsed.get("reason", "")[:200],
              provider=res.provider, model=res.model)
    return parsed


def classify_with_ollama(text: str, url: Optional[str] = None,
                         model: Optional[str] = None,
                         timeout_s: float = 8.0) -> Optional[Dict[str, Any]]:
    """Backward-compatible alias for :func:`classify_local_llm`."""
    return classify_local_llm(text, None, url, model, timeout_s)
