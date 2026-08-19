"""LLM-assisted intent classification via the local Ollama API.

Uses the fast fully-in-VRAM tier (qwen3-vl:8b, ~40-70 tok/s on this box) with
`think:false` and strict JSON output. Falls back gracefully (returns None) when
Ollama is down so the deterministic router still works.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict, Optional

from ..logging_setup import log_event

SYSTEM_PROMPT = """You are the intent router of a voice assistant that controls an
OpenClaw coding agent. Classify the user's spoken utterance into EXACTLY ONE kind:

- "system": control of the voice assistant itself (status, mute, stop, help, model switch)
- "confirm": a clear yes/affirmative answer to a pending confirmation
- "cancel": a clear no/negative, or a request to stop/abort current work
- "coding": a task for the coding agent (open/inspect/patch/test/fix/refactor/run/review/commit/deploy...)
- "conversation": casual chat, questions, small talk — NOT a coding task
- "clarify": the user asks for clarification or the request is too vague to act on

Rules:
- Commands that touch files, code, repos, terminals, browsers, or servers are "coding".
- Questions about code ("why does X fail?", "how does Y work?") are "coding" when
  they need inspecting a project; pure general knowledge questions are "conversation".
- "yes/yeah/sure/go ahead/apply it/do it" -> "confirm". "no/stop/cancel/never mind" -> "cancel".
- Respond ONLY with JSON: {"kind": "...", "task": "one-line actionable task text or ''",
  "confidence": 0.0-1.0, "reason": "short reason"}."""


def classify_with_ollama(text: str, url: str = "http://127.0.0.1:11434",
                         model: str = "qwen3-vl:8b",
                         timeout_s: float = 8.0) -> Optional[Dict[str, Any]]:
    """Returns {kind, task, confidence, reason} or None if Ollama is unavailable."""
    payload = {
        "model": model,
        "prompt": f"User said: \"{text[:600]}\"\n\nClassify it.",
        "system": SYSTEM_PROMPT,
        "stream": False,
        "format": "json",
        "options": {"think": False, "temperature": 0.0},
    }
    req = urllib.request.Request(
        f"{url}/api/generate",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode())
        raw = body.get("response", "")
        parsed = json.loads(raw)
        kind = parsed.get("kind")
        if kind not in ("system", "confirm", "cancel", "coding", "conversation", "clarify"):
            return None
        parsed.setdefault("task", "" if kind != "coding" else text)
        log_event("intent.llm", kind=kind, confidence=parsed.get("confidence"),
                  reason=parsed.get("reason", "")[:200])
        return parsed
    except Exception as exc:
        log_event("intent.llm.unavailable", error=str(exc)[:160])
        return None
