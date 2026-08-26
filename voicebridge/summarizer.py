"""Spoken summaries: long tool outputs -> 2-3 short spoken sentences.

Two providers:
  * ollama   — the resident baked brain (qubit-brain4) via the raw local API
  * extractive — deterministic fallback (first + last sentences, key numbers)

Always strips markdown/code-fence noise before speaking.
"""
from __future__ import annotations

import json
import re
import urllib.request
from typing import Optional

from .logging_setup import log_event

SUMMARY_PROMPT = """You are summarizing a coding agent's output for a voice
interface. Produce a short spoken summary of 2-3 sentences (max 40 words).
Include: what was changed, key file names, and any action the user must take
(such as "review before commit"). Use plain conversational language, no
markdown, no bullet points. Output JSON: {"summary": "..."}"""


def _strip_markdown(text: str) -> str:
    text = re.sub(r"```[a-zA-Z]*\n?.*?```", " ", text, flags=re.S)   # code fences
    text = re.sub(r"`([^`]*)`", r"\1", text)                          # inline code
    text = re.sub(r"[*_#>{}\[\]]+", " ", text)                        # markers
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def summarize_ollama(text: str, url: Optional[str] = None,
                     model: Optional[str] = None, timeout_s: float = 5.0,
                     max_chars: int = 900) -> Optional[str]:
    from .runner_contract import resolve
    _rc = resolve()
    url = url or _rc.url
    model = model or _rc.alias
    text = text[:max_chars]
    payload = {"model": model,
               "prompt": f"Agent output:\n{text} /no_think",
               "system": SUMMARY_PROMPT, "stream": False, "format": "json",
               "keep_alive": _rc.keep_alive,   # canonical contract (4B)
               "options": {"temperature": 0.2,
                           "num_ctx": _rc.num_ctx,   # single runner contract
                           "num_predict": 90}}    # cap generation (<=40w summary + json)
    req = urllib.request.Request(f"{url}/api/generate",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode())
        summary = json.loads(body.get("response", "{}")).get("summary", "")
        if summary.strip():
            return _strip_markdown(summary)
    except Exception as exc:
        log_event("summarizer.ollama.unavailable", error=str(exc)[:160])
    return None


def summarize_extractive(text: str, max_chars: int = 900) -> str:
    clean = _strip_markdown(text)
    if not clean:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", clean)
    # first 2 sentences + last sentence (if different)
    parts = sentences[:2]
    if len(sentences) > 3 and sentences[-1] not in parts:
        parts.append(sentences[-1])
    summary = " ".join(parts)
    if len(summary) > max_chars:
        summary = summary[:max_chars].rsplit(" ", 1)[0] + "."
    return summary


def clamp_words(text: str, max_words: int = 40) -> str:
    """Hard cap for SPOKEN text — 40 words ≈ ~12s of speech (save time).

    Truncates at a word boundary, strips trailing punctuation, and appends
    a period so the voice never cuts mid-word.
    """
    if not text:
        return text
    words = text.split()
    if len(words) <= max_words:
        return text
    cut = " ".join(words[:max_words]).rstrip(" ,;:.-")
    return cut + "."


def summarize(text: str, cfg: Optional[dict] = None,
              ollama_url: Optional[str] = None,
              ollama_model: Optional[str] = None) -> str:
    """Never crash, always return <=max_words. Raw full report is kept separately."""
    try:
        cfg = cfg or {}
        provider = cfg.get("provider", "extractive")
        url = ollama_url or "http://127.0.0.1:11434"
        model = ollama_model or cfg.get("ollama_model", "qubit-brain4")
        max_chars = int(cfg.get("max_chars", 900))
        max_words = int(cfg.get("max_words", 40))
        if not text or not text.strip():
            return ""
        # truncate for LLM but keep deterministic fallback on full stripped text
        if provider == "ollama":
            try:
                s = summarize_ollama(text, url, model, max_chars=max_chars)
                if s and s.strip():
                    return clamp_words(s, max_words)
            except Exception:
                pass
        # extractive fallback is guaranteed
        try:
            return clamp_words(summarize_extractive(text, max_chars), max_words)
        except Exception:
            return clamp_words(_strip_markdown(text or ""), max_words)
    except Exception:
        # ultimate fallback: first 40 words
        try:
            return clamp_words(text or "", int((cfg or {}).get("max_words", 40)))
        except Exception:
            return ""


def trim_spoken(text: str, max_chars: int = 700,
                max_words: int = 40) -> str:
    """Shorten any text for speaking (summary fallback), capped in words."""
    clean = _strip_markdown(text)
    if len(clean) <= max_chars:
        return clamp_words(clean, max_words)
    return clamp_words(summarize_extractive(clean, max_chars), max_words)
