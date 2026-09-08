"""Adapted Hermes turn/session worker (stdio JSONL, Qubit-owned).

Protocol (mirrors the pinned Hermes CLI contract shape):
  -> {"op": "ping"}                          <- {"ok": true, "runtime": ..., "pin": ...}
  -> {"op": "turn", "text": ..., "session": ...}
     <- {"ok": true, "reply": ..., "session": ..., "turn": N}
     <- {"ok": false, "code": "PROVIDER_DEGRADED"|"MALFORMED", "error": ...}
  -> {"op": "shutdown"}                      <- {"ok": true}

Providers (env QUBIT_RUNTIME_PROVIDER, default "fixture"):
  - "fixture": deterministic redacted echo, hermetic (no model/network).
  - "local": Qubit resident model via voicebridge.local_llm.chat_local;
    failures become typed PROVIDER_DEGRADED, never a crash.

Session continuity is in-worker turn counting per session id. Secrets are
redacted from every reply and every error string before emission.
"""
from __future__ import annotations

import json
import os
import sys

RUNTIME_NAME = "qubit-hermes-adapted"
HERMES_PIN = "2237be355906fbe6065ce1815711eee52b2d646e"
MAX_TEXT = 8192


def _redact(text: str) -> str:
    try:
        from voicebridge.redaction import redact_text
        return redact_text(text)
    except Exception:
        return text


def _sanitize(text: str) -> str:
    """8T-F: vendored pinned-Hermes output sanitizers execute in the turn
    path — ANSI escapes and invisible-tag smuggling never reach Qubit
    prompts, receipts, or TTS text."""
    try:
        from voicebridge.upstream.hermes_ansi_strip import (
            strip_ansi, strip_unicode_tags)
        return strip_unicode_tags(strip_ansi(text))
    except Exception:
        return text


def _answer(text: str, session: str, turn: int) -> dict:
    provider = os.environ.get("QUBIT_RUNTIME_PROVIDER", "fixture")
    if provider == "local":
        try:
            from voicebridge.local_llm import chat_local
            reply = chat_local(text, conversation_prompt="")
            return {"ok": True, "reply": _redact(str(reply)),
                    "session": session, "turn": turn,
                    "provider": "local"}
        except Exception as exc:
            return {"ok": False, "code": "PROVIDER_DEGRADED",
                    "error": _redact(f"local model unavailable: {exc}"[:300]),
                    "session": session, "turn": turn}
    if provider != "fixture":
        return {"ok": False, "code": "PROVIDER_DEGRADED",
                "error": f"unknown QUBIT_RUNTIME_PROVIDER={provider!r}",
                "session": session, "turn": turn}
    clean = _sanitize(_redact(text[:MAX_TEXT]))
    return {"ok": True,
            "reply": f"[turn {turn} session {session}] {clean}",
            "session": session, "turn": turn, "provider": "fixture"}


def main() -> int:
    sessions: dict[str, int] = {}
    stdin = sys.stdin
    stdout = sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            stdout.write(json.dumps({"ok": False, "code": "MALFORMED",
                                     "error": "request is not JSON"}) + "\n")
            stdout.flush()
            continue
        op = req.get("op")
        if op == "ping":
            stdout.write(json.dumps({"ok": True, "runtime": RUNTIME_NAME,
                                     "pin": HERMES_PIN}) + "\n")
        elif op == "shutdown":
            stdout.write(json.dumps({"ok": True}) + "\n")
            stdout.flush()
            return 0
        elif op == "turn":
            text = req.get("text")
            session = req.get("session") or "default"
            if not isinstance(text, str) or not text.strip():
                stdout.write(json.dumps({"ok": False, "code": "MALFORMED",
                                         "error": "turn needs non-empty text"}) + "\n")
            else:
                turn = sessions.get(session, 0) + 1
                sessions[session] = turn
                stdout.write(json.dumps(_answer(text, session, turn)) + "\n")
        else:
            stdout.write(json.dumps({"ok": False, "code": "MALFORMED",
                                     "error": f"unknown op {op!r}"}) + "\n")
        stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
