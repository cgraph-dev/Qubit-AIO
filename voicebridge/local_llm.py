"""Tiny local LLM chat for short conversational turns (hybrid mode).

Used when `intent.local_llm.enabled` — a small Qwen3-4B (or Llama-3.2-3B)
answers brief utterances locally in ~0.3s with zero network; DeepSeek still
handles long/complex/web asks. Falls back cleanly (returns "") when ollama is
down or the model isn't pulled, so the caller goes to the cloud path.
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Optional

from .logging_setup import log_event

SYSTEM_PROMPT = (
    "You are Qubit, Tricky (cgraph-dev)'s warm voice assistant. Answer briefly in 1-3 short "
    "sentences unless asked for detail. Be direct: no emojis, no pleasantries, "
    "no filler words - say only what\u0027s needed. Remember: you are Qubit, your human is Tricky, main project is CGraph. "
    "You understand broken English, heavy accents, and typos — infer the intended meaning and respond helpfully (never mock). "
    "LANGUAGE: You speak ENGLISH ONLY. Always reply in plain English, regardless of "
    "what language the user used. Never reply in Romanian or any other language."
)

# Few-shot dialog examples (style teaching). Loaded from config/dialogs.md —
# part of the SYSTEM prompt so it costs tokens once per turn, not per request.
def _load_dialogs() -> str:
    candidates = [
        Path(os.path.expanduser("~/.config/openclaw-voice-bridge/dialogs.md")),
        Path(__file__).resolve().parent.parent / "config" / "dialogs.md",
    ]
    for p in candidates:
        if p.exists():
            try:
                text = p.read_text(errors="replace")
                start = text.find("DIALOG_EXAMPLES = ")
                if start >= 0:
                    text = text[start:].split('"""')[1]
                return text.strip()
            except Exception:
                return ""
    return ""


_DIALOGS = _load_dialogs()

if _DIALOGS:
    SYSTEM_PROMPT = SYSTEM_PROMPT + "\n\n# Example dialogs (match this style)\n" + _DIALOGS

# --- language guard (ENGLISH ONLY) -------------------------------------------
# Common high-frequency English words — a real English reply is full of them.
_EN_COMMON = {"the","be","to","of","and","a","in","that","have","i","it","for","not",
              "on","with","he","as","you","do","at","this","but","his","by","from",
              "they","we","say","her","she","or","an","will","my","one","all","would",
              "there","their","what","so","up","out","if","about","who","get","which",
              "go","me","when","make","can","like","time","no","just","him","know",
              "take","people","into","year","your","good","some","could","them","see",
              "other","than","then","now","look","only","come","its","over","think",
              "also","back","after","use","two","how","our","work","first","well",
              "way","even","new","want","because","any","these","give","day","most",
              "is","are","yes"}
def _reply_is_other_language(text: str) -> bool:
    """True when the reply does NOT look like plain English (ENGLISH ONLY rule)."""
    if not text:
        return False
    # Non-Latin / Romanian-diacritic scripts => not English.
    for ch in text[:400]:
        o = ord(ch)
        if o > 0x24F:   # beyond Latin Extended-A (covers ă â ș ț î, Cyrillic, CJK…)
            return True
    low = text.lower()
    tokens = [w.strip(".,!?;:'\"()[]") for w in low.split()]
    tokens = [t for t in tokens if t.isalpha()]
    if not tokens:
        return False
    en = sum(1 for t in tokens if t in _EN_COMMON)
    # A genuine English reply contains common English words; foreign text almost
    # none. Short replies (<4 tokens) can't be judged reliably -> allow.
    if len(tokens) >= 4 and en == 0:
        return True
    if len(tokens) >= 6 and en <= 1:
        return True
    return False


def chat_local(text: str, conversation_prompt: str = "",
               model: str = "qwen3:4b", url: str = "http://127.0.0.1:11434",
               timeout_s: float = 4.0, max_tokens: int = 100,
               context: str = "", language: Optional[str] = None) -> str:
    """Return a short local reply, or "" on any failure (caller falls back).

    `context` = personal knowledge (MEMORY.md, personal-knowledge.md) the
    model should use when answering about the user's world.
    `language` = explicit ASR-detected hint ("ro"/"en"); overrides text
    heuristics when provided (the recogniser knows better than a word list).
    """
    user_content = f"Recent conversation:\n{conversation_prompt}\n\n"
    if context:
        user_content += (f"<about-the-user>\n{context[:2500]}\n</about-the-user>\n\n")
    # ENGLISH ONLY (user decision): no Romanian, no other language. The guard
    # below retries if the model drifts out of English.
    user_content += (
        "Reply ONLY in plain English. Do NOT reply in any other language.\n"
        "FACTS OVERRIDE HISTORY: the <about-the-user> facts above are ALWAYS true. "
        "If older conversation turns contain a different or refusing answer "
        "(e.g. \"I'm not capable\"), correct it now with the real fact and do not repeat the old mistake."
    )
    user_content += f"\nUser: {text}"
    payload = {
        "model": model,
        "stream": False,
        "keep_alive": -1,          # -1 = always resident (24/7); cold reload = seconds of latency
        "options": {"num_predict": max_tokens, "temperature": 0.4,
                    "num_ctx": 4096,          # fits system prompt + dialogs + context + instruction (avoids truncation)
                    "top_p": 0.9},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    }
    req = urllib.request.Request(
        f"{url}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = __import__("time").time()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode())
        reply = (body.get("message", {}) or {}).get("content", "").strip()
    except Exception as exc:
        log_event("conversation.local.failed", error=str(exc)[:160], model=model)
        return ""
    elapsed = __import__("time").time() - t0
    # HARD LANGUAGE GUARD: the agent speaks ONLY English and Romanian. If the
    # reply drifted into another language, retry ONCE with an explicit lock;
    # if it still drifts, keep the retry (best effort) but log it.
    if reply and _reply_is_other_language(reply):
        log_event("conversation.local.lang_drift", lang="non-english", chars=len(reply),
                  preview=reply[:80])
        payload["messages"] = payload["messages"][:-1] + [
            {"role": "user", "content": user_content +
             "\n[STRICT: answer in plain ENGLISH only. No other language.]"},
        ]
        try:
            req2 = urllib.request.Request(
                f"{url}/api/chat",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req2, timeout=timeout_s) as resp2:
                body2 = json.loads(resp2.read().decode())
            fixed = (body2.get("message", {}) or {}).get("content", "").strip()
            if fixed and not _reply_is_other_language(fixed):
                reply = fixed
        except Exception as exc:
            log_event("conversation.local.retry_failed", error=str(exc)[:120])
    log_event("conversation.local", model=model, chars=len(reply),
              elapsed_s=round(elapsed, 3))
    return reply
