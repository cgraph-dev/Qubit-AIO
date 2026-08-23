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
    "CRITICAL LANGUAGE RULE: Reply in the EXACT SAME language as the CURRENT "
    "User message (the last User: line). If the current User message is in English, "
    "reply in English. If the current User message is in Romanian, reply in Romanian. "
    "Ignore the language of previous turns — only the current User message matters. "
    "Never mix languages. Never respond in Romanian to an English question."
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


def chat_local(text: str, conversation_prompt: str = "",
               model: str = "qwen3:4b", url: str = "http://127.0.0.1:11434",
               timeout_s: float = 4.0, max_tokens: int = 100,
               context: str = "") -> str:
    """Return a short local reply, or "" on any failure (caller falls back).

    `context` = personal knowledge (MEMORY.md, personal-knowledge.md) the
    model should use when answering about the user's world.
    """
    user_content = f"Recent conversation:\n{conversation_prompt}\n\n"
    if context:
        user_content += (f"<about-the-user>\n{context[:2500]}\n</about-the-user>\n\n")
    # Explicit per-turn language lock (defense against Romanian context bleed):
    # Detect the CURRENT message language and force it — history/personal-knowledge
    # may contain Romanian, but the reply must match THIS message only.
    _ro_markers = ("ă","â","ș","ş","ț","ţ","î","ă")
    # High-signal Romanian tokens (not substrings of common English words).
    # Ensures "care este fusul meu orar" etc. are classified RO.
    _ro_words = {
        "salut","buna","bună","sal","hei","ce","faci","fac","cum","esti","ești","sunt",
        "bine","multumesc","mulțumesc","mersi","azi","astazi","astăzi","da","nu","te",
        "ma","mă","vreau","vrei","pot","ajuta","ajutor","vorbesti","vorbești","romana",
        "română","românește","româneşte","care","este","eşti","esti","meu","mea","mos",
        "fost","unde","cand","când","dece","de ce","cine","ceva","nimic","astfel","prin",
        "pentru","despre","între","acum","azi","ieri","mâine","main","merge","gust","vorbesc",
        "frumos","dragut","drăguț","copil","mama","mamă","tata","tata","să","sa","ca",
        "ca","cu","la","pe","din","în","in","sunt","esti","ești","e","sînt","nu","da",
    }
    _low = text.lower()
    _tokens = [w for w in _low.split() if w.isalpha()]
    # Score: RO words present (weighted), minus over-generic single/double-letter ones.
    _strong = {t for t in _tokens if t in _ro_words and len(t) > 2}
    _is_ro = bool(any(c in _low for c in _ro_markers)) or len(_strong) >= 2 or \
        ("care" in _tokens and any(t in _tokens for t in ("este","e","ești","fus","ora","orar")))
    _lang_directive = "Reply in Romanian." if _is_ro else "Reply in English. Do NOT reply in Romanian."
    user_content += f"User: {text}\n[{_lang_directive} The user's current message is in {'Romanian' if _is_ro else 'English'} — ignore previous turns' language.]"
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
    log_event("conversation.local", model=model, chars=len(reply),
              elapsed_s=round(elapsed, 3))
    return reply
