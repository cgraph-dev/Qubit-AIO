"""Tiny local LLM chat for short conversational turns.

The resident brain is `qubit-brain4` (Qwen3-4B abliterated, /no_think
switch applied by the shared payload builders; Packet 4B sanitized artifact). It answers brief utterances locally in ~0.3s with zero
network; DeepSeek still handles long/complex/web asks. Falls back cleanly
(returns "") when ollama is down or the model isn't pulled, so the caller
goes to the cloud path.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

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


_THINK_BLOCK_RE = re.compile(r"\s* thinking\s*\n.*?\n\s* response|\s*thinking\s*\n.*?\n\s* response", re.S)
_THINK_TAG_RE = re.compile(r"<{1,2}/?think>?", re.I)
_THINK_TOKEN_RE = re.compile(r"/no_think|/think|\bno_think\b", re.I)
_THINK_LEAD_RE = re.compile(r"^\s*(?:response|thinking|/think|no_think|<think)\b[^\w]*", re.I)


def _strip_think(text: str) -> str:
    """Remove reasoning artifacts from replies.

    The abliterated quant sometimes ignores /no_think and emits a leading
    ` thinking` / ` thinking` / ` response` / `/think` marker (and its
    matching close tag); nothing here may reach TTS/dashboard.
    """
    import re as _re
    text = text or ""
    text = _re.sub(_THINK_BLOCK_RE, " ", text)
    text = _re.sub(_THINK_TAG_RE, "", text)
    text = _re.sub(_THINK_TOKEN_RE, "", text)
    text = _re.sub(_THINK_LEAD_RE, "", text, count=1)
    text = _re.sub(r"\s*\bresponse\b\s*$", "", text)
    return text.strip()


def _extract_reply(body: Dict[str, Any]) -> str:
    """Pull the answer out of an /api/chat response — CONTENT ONLY.

    Packet 4B reversal: under the /no_think template any text in
    `message.thinking` is genuine chain-of-thought, never a speakable
    answer. The old thinking-fallback would have read reasoning junk aloud,
    so it is gone; an empty content is a typed failure for the caller's
    bounded retry instead."""
    msg = body.get("message", {}) or {}
    reply = (msg.get("content") or "").strip()
    if not reply and (msg.get("thinking") or "").strip():
        log_event("conversation.local.thinking_only",
                  chars=len(msg.get("thinking") or ""))
    return _strip_think(reply)


_COMPILER_CACHE: Dict[str, Any] = {}


def _reset_compiler_cache() -> None:
    """Test/diagnostic hook: rebuild the configured compiler next call."""
    _COMPILER_CACHE.clear()


def _evidence_provider(names: List[str]):
    """Bounded parallel evidence provider over the 5A read-only registry."""
    from .evidence import build_evidence_registry
    from .context import parallel_evidence
    reg = build_evidence_registry()

    def provider(query: str, max_workers: int, deadline_s: float):
        def one(name: str, q: str):
            return reg.fetch(name, q)
        return parallel_evidence(one, names, lambda n: query[:200],
                                 max_workers, deadline_s)

    return provider


def _configured_compiler() -> tuple:
    """Resolve the chat-path compiler once per process.

    intent.compiler.mode = "5c" (default) uses the Packet 5C bounded
    compiler wired to the FTS5 memory store + bounded evidence;
    "4b" restores the Packet 4B compiler (rollback flag).
    Env overrides (tests/diagnostics):
      VOICE_BRIDGE_MEMORY_DB, VOICE_BRIDGE_COMPILER_MODE.
    """
    if _COMPILER_CACHE:
        return (_COMPILER_CACHE["mode"], _COMPILER_CACHE["compiler"],
                _COMPILER_CACHE["observe"])
    mode = os.environ.get("VOICE_BRIDGE_COMPILER_MODE") or "5c"
    observe = True
    sources: List[str] = []
    try:
        from .config import Config
        cfg = Config.load()
        mode = os.environ.get("VOICE_BRIDGE_COMPILER_MODE") or str(
            cfg.get("intent", "compiler", "mode", default="5c"))
        sources = list(cfg.get("intent", "compiler", "evidence_sources",
                               default=[]) or [])
        observe = bool(cfg.get("intent", "compiler", "observe",
                               default=True))
    except Exception:
        pass
    compiler = None
    if mode == "5c":
        try:
            db_env = os.environ.get("VOICE_BRIDGE_MEMORY_DB")
            db_path = Path(db_env) if db_env else None
            if db_path is None:
                from .config import Config as _C2
                mem_dir = _C2.load().state_dir() / "memory"
                mem_dir.mkdir(parents=True, exist_ok=True)
                try:
                    os.chmod(mem_dir, 0o700)
                except OSError:
                    pass
                db_path = mem_dir / "memory.db"
            from .memory import MemoryStore
            from .context import ContextCompiler
            store = MemoryStore(db_path)
            provider = _evidence_provider(sources) if sources else None
            compiler = ContextCompiler(store=store,
                                       evidence_provider=provider,
                                       max_tokens=4096,
                                       output_reserve_tokens=150)
        except Exception as exc:
            from .logging_setup import log_event
            log_event("compiler.5c.build.failed", error=str(exc)[:160])
            compiler = None
    _COMPILER_CACHE.update({"mode": mode, "compiler": compiler,
                            "observe": observe})
    from .logging_setup import log_event
    log_event("compiler.mode", mode=mode,
              active_compiler=("5c" if compiler is not None else "4b"))
    return mode, compiler, observe


def _compile_4b(text: str, conversation_prompt: str, context: str,
                num_ctx: int, max_tokens: int,
                include_core_facts: bool = True) -> str:
    """Packet 4B compiler path (rollback + diagnostics, unchanged)."""
    from .context_compiler import (ACTIVE_MAX_CHARS, CORE_FACTS_MAX_CHARS,
                                   RECALL_MAX_CHARS, compile_context,
                                   fits_budget, load_core_facts)
    if include_core_facts and not (context or "").strip():
        context = load_core_facts()
    compiled = compile_context(
        persona="", active=conversation_prompt or "",
        core_facts=context or "", recall="",
        char_budget=max(ACTIVE_MAX_CHARS + CORE_FACTS_MAX_CHARS, 2000))
    if not fits_budget(num_ctx, max_tokens, compiled):
        from .logging_setup import log_event
        log_event("runner.context.overflow",
                  est_tokens=compiled.est_tokens, num_ctx=num_ctx)
    parts = []
    if compiled.sections_included and "active" in compiled.sections_included \
            and compiled.text.startswith("[active]"):
        blocks = compiled.text.split("\n\n")
        for b in blocks:
            if b.startswith("[active]\n"):
                parts.append(f"Recent conversation:\n{b[len('[active]\n'):]}")
            elif b.startswith("<about-the-user>"):
                parts.append(b)
            elif b.startswith("[reference]\n"):
                parts.append(b)
    else:
        if conversation_prompt:
            parts.append(f"Recent conversation:\n"
                         f"{conversation_prompt[:ACTIVE_MAX_CHARS]}")
        if context:
            parts.append(f"<about-the-user>\n"
                         f"{context[:RECALL_MAX_CHARS]}\n</about-the-user>")
    return "\n\n".join(parts)


def _compile_5c(text: str, conversation_prompt: str, context: str,
                compiler, observe: bool) -> str:
    """Packet 5C path: FTS5 recall + core facts + bounded evidence inside
    the LIVE chat user turn. Falls back to 4B on any compiler error."""
    from .context_compiler import CORE_FACTS_MAX_CHARS
    from .logging_setup import log_event
    try:
        turn = compiler.compile(
            text, scope="default", persona=SYSTEM_PROMPT,
            active=conversation_prompt or "", tools="",
            render_persona_tools=False)
    except Exception as exc:
        log_event("compiler.5c.compile.failed", error=str(exc)[:120])
        return _compile_4b(text, conversation_prompt, context, 4096, 100)
    body = turn.text
    if context and context.strip():
        body = (f"[instructions]\n{context[:CORE_FACTS_MAX_CHARS]}\n\n"
                + body)
    from .logging_setup import log_event as _le
    _le("compiler.5c.turn", sections=turn.report.sections_included,
        ms=turn.report.retrieval_ms, est=turn.est_tokens,
        failures=len(turn.report.failures),
        omitted=len(turn.report.omitted))
    if observe and (turn.report.failures or turn.report.omitted):
        trailer = (f"\n[ctx: ms={turn.report.retrieval_ms} "
                   f"omitted={len(turn.report.omitted)} "
                   f"ev_fail={len(turn.report.failures)} "
                   f"~{turn.est_tokens}tok]")
        log_event("compiler.5c.observability",
                  omitted=len(turn.report.omitted),
                  failures=len(turn.report.failures),
                  ms=turn.report.retrieval_ms, est=turn.est_tokens)
        body += trailer
    return body


def _user_content(text: str, conversation_prompt: str, context: str,
                  num_ctx: int, max_tokens: int,
                  include_core_facts: bool = True) -> str:
    """Compiled user turn for the live chat path (Packet 5C).

    mode "5c" (default): bounded compiler with FTS5 recall + core facts +
    bounded evidence + citations; persona/tools stay in the separate system
    message (immutable); retrieval only appends quoted reference blocks, so
    injected text cannot change policy, tools, persona or routing.
    mode "4b": exact Packet 4B behavior (rollback flag).
    include_core_facts=False requests TRUE context absence (diagnostics).
    """
    mode, compiler, observe = _configured_compiler()
    if compiler is not None and include_core_facts:
        user_content = _compile_5c(text, conversation_prompt, context,
                                   compiler, observe)
    else:
        user_content = _compile_4b(text, conversation_prompt, context,
                                   num_ctx, max_tokens,
                                   include_core_facts=include_core_facts)
    user_content += "\n\n" if user_content else ""
    # ENGLISH ONLY (user decision): no Romanian, no other language. The guard
    # below retries if the model drifts out of English.
    user_content += (
        "Reply ONLY in plain English. Do NOT reply in any other language.\n"
        "FACTS OVERRIDE HISTORY: the <about-the-user> facts above are ALWAYS true. "
        "If older conversation turns contain a different or refusing answer "
        "(e.g. \"I'm not capable\"), correct it now with the real fact and do not repeat the old mistake."
    )
    # Packet 4B repair round: Qwen3 soft no-think switch — this quant honors
    # it ONLY as a literal in the user turn text, so the shared payload
    # builder appends it (template/system-side insertion proved unreliable).
    return user_content + f"\nUser: {text} /no_think"


def _chat_payload(text: str, conversation_prompt: str, model: str,
                  max_tokens: int, context: str, stream: bool,
                  num_ctx: Optional[int] = None) -> Dict[str, Any]:
    # Packet 4B: ONE canonical runner contract. num_ctx resolves from the
    # contract (never a per-caller literal); keep_alive is always -1 so no
    # caller can expire the resident runner.
    if num_ctx is None:
        from .runner_contract import resolve
        num_ctx = resolve().num_ctx
    return {
        "model": model,
        "stream": stream,
        "keep_alive": -1,          # canonical contract: always resident
        "options": {"num_predict": max_tokens, "temperature": 0.4,
                    "num_ctx": num_ctx,
                    "top_p": 0.9},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_content(
                text, conversation_prompt, context, num_ctx, max_tokens)},
        ],
    }


def chat_local(text: str, conversation_prompt: str = "",
               model: str = "qubit-brain4", url: str = "http://127.0.0.1:11434",
               timeout_s: float = 4.0, max_tokens: int = 100,
               context: str = "", num_ctx: Optional[int] = None) -> str:
    """Return a short local reply, or "" on any failure (caller falls back).

    `context` = personal knowledge (MEMORY.md, personal-knowledge.md) the
    model should use when answering about the user's world.
    """
    payload = _chat_payload(text, conversation_prompt, model, max_tokens,
                            context, stream=False, num_ctx=num_ctx)
    req = urllib.request.Request(
        f"{url}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = __import__("time").time()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode())
        reply = _extract_reply(body)
    except Exception as exc:
        log_event("conversation.local.failed", error=str(exc)[:160], model=model)
        return ""
    # Packet 4B: the abliterated quant samples an instant-EOS / think-exhausted
    # empty turn occasionally even under /no_think. One BOUNDED retry — never
    # a loop; a second consecutive empty returns "" for the caller's fallback.
    if not reply:
        log_event("conversation.local.empty_retry", model=model)
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp2:
                body2 = json.loads(resp2.read().decode())
            reply = _extract_reply(body2)
        except Exception as exc:
            log_event("conversation.local.retry_failed", error=str(exc)[:120])
    elapsed = __import__("time").time() - t0
    # HARD LANGUAGE GUARD: the agent speaks ONLY English and Romanian. If the
    # reply drifted into another language, retry ONCE with an explicit lock;
    # if it still drifts, keep the retry (best effort) but log it.
    if reply and _reply_is_other_language(reply):
        log_event("conversation.local.lang_drift", lang="non-english", chars=len(reply),
                  preview=reply[:80])
        payload["messages"] = payload["messages"][:-1] + [
            {"role": "user", "content": payload["messages"][-1]["content"] +
             "\n[STRICT: answer in plain ENGLISH only. No other language.]"},
        ]
        try:
            req2 = urllib.request.Request(
                f"{url}/api/chat",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req2, timeout=timeout_s) as resp2:
                body2 = json.loads(resp2.read().decode())
            fixed = _extract_reply(body2)
            if fixed and not _reply_is_other_language(fixed):
                reply = fixed
        except Exception as exc:
            log_event("conversation.local.retry_failed", error=str(exc)[:120])
    log_event("conversation.local", model=model, chars=len(reply),
              elapsed_s=round(elapsed, 3))
    return reply


def chat_local_stream(text: str, conversation_prompt: str = "",
                      model: str = "qubit-brain4", url: str = "http://127.0.0.1:11434",
                      timeout_s: float = 4.0, max_tokens: int = 100,
                      context: str = "", on_sentence=None,
                      should_stop=None, num_ctx: Optional[int] = None) -> str:
    """Stream a local reply; emit completed sentences via on_sentence(str)
    as they arrive so TTS can start synthesizing while the model is still
    generating (STEAL_LIST item 6 — pipeline overlap, ~0.3-0.5s faster turns).

    Returns the full reply, or "" on any failure (caller falls back to the
    non-streaming path / cloud).
    `should_stop` = callable -> True aborts the stream mid-generation.
    """
    if on_sentence is None:
        return chat_local(text, conversation_prompt, model=model, url=url,
                          timeout_s=timeout_s, max_tokens=max_tokens,
                          context=context)
    payload = _chat_payload(text, conversation_prompt, model, max_tokens,
                            context, stream=True, num_ctx=num_ctx)
    req = urllib.request.Request(
        f"{url}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = __import__("time").time()
    full = ""
    buf = ""
    emitted: List[str] = []

    def _flush(sentence: str) -> None:
        sentence = _strip_think(sentence)
        if sentence:
            emitted.append(sentence)
            on_sentence(sentence)

    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            for raw in resp:
                if should_stop is not None and should_stop():
                    resp.close()
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    chunk = (json.loads(line).get("message", {}) or {})
                except Exception:
                    continue
                delta = chunk.get("content") or ""
                if not delta and not chunk.get("done"):
                    # thinking models stream <think> text first: skip it, never speak it
                    continue
                buf += delta
                full += delta
                while True:
                    m = re.search(r"[.!?…]\s*", buf)
                    if not m:
                        break
                    sentence = buf[:m.end()].strip()
                    buf = buf[m.end():]
                    _flush(sentence)
            _flush(buf.strip())
    except Exception as exc:
        log_event("conversation.local.stream_failed", error=str(exc)[:160],
                  model=model)
        # Speak-consistent partial: return ONLY what was actually emitted as
        # sentences, never the un-spoken trailing fragment (caller falls back
        # to the cloud path when nothing completed).
        return " ".join(emitted).strip()
    if not emitted and not full.strip() and not (should_stop and should_stop()):
        # Packet 4B: instant-EOS / think-exhausted empty stream. One BOUNDED
        # retry — safe here because nothing was emitted yet, so no partial
        # speech can duplicate; a second consecutive empty returns "" for the
        # caller's cloud fallback.
        log_event("conversation.local.empty_retry", model=model, stream=True)
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp2:
                for raw in resp2:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line:
                        continue
                    try:
                        chunk = (json.loads(line).get("message", {}) or {})
                    except Exception:
                        continue
                    delta = chunk.get("content") or ""
                    if not delta and not chunk.get("done"):
                        continue
                    buf += delta
                    full += delta
                    while True:
                        m = re.search(r"[.!?…]\s*", buf)
                        if not m:
                            break
                        sentence = buf[:m.end()].strip()
                        buf = buf[m.end():]
                        _flush(sentence)
                    _flush(buf.strip())
        except Exception as exc:
            log_event("conversation.local.retry_failed", error=str(exc)[:120])
    log_event("conversation.local.stream", model=model, chars=len(full),
              elapsed_s=round(__import__("time").time() - t0, 3))
    return " ".join(emitted).strip()
