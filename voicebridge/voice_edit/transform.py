"""Transformation engine: selected text + typed command -> rewritten text.

It uses the ONE existing model authority (`voicebridge.providers.gateway
.run_role` via the shared resolver). There is no second router and no second
credential path: the resolved profile's locality/egress/budget policy applies
exactly as it does for chat.

Privacy:
* no full selected text is ever logged; only a char count and a sha256 digest;
* ``routing='local'`` never reaches the network -- a missing local profile is a
  typed refusal, never a silent API substitution;
* ``allow_api=False`` disables external transformations entirely;
* a fallback to the API is only possible under ``routing='auto'`` +
  ``allow_api=True`` and is *recorded* in the result, never silent.
"""
from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Callable, Dict, List, Optional

from .prompts import render_prompt, scan_injection
from .schema import TransformResult

# Patterns whose content must survive a transformation unless the user asked
# for it to change. Detection is conservative and only reports; it never
# blocks the user's explicit request.
_URL_RE = re.compile(r"https?://[^\s)\]]+")
_NUM_RE = re.compile(r"(?<![\w.])\d+(?:[.,:]\d+)*(?![\w])")
_IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:[./:_-][A-Za-z0-9_]+)+\b")
_FLAG_RE = re.compile(r"(?<!\w)--?[A-Za-z][A-Za-z0-9-]+")
_BACKTICK_RE = re.compile(r"`[^`]+`")

MAX_INPUT_CHARS = 20000
DEFAULT_TIMEOUT_S = 60.0


def digest_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Deterministic case transforms (no model, no egress)
# ---------------------------------------------------------------------------
_CASE_ACTIONS = frozenset({"uppercase", "lowercase", "title_case"})
_CASE_DETAIL = {
    "uppercase": "the text is already uppercase",
    "lowercase": "the text is already lowercase",
    "title_case": "the text is already title case",
}


def _title_segment(word: str) -> str:
    for i, ch in enumerate(word):
        if ch.isalpha():
            return word[:i] + ch.upper() + word[i + 1:].lower()
    return word


def _title_word(word: str) -> str:
    """Title-case one whitespace-delimited token.

    Hyphen-separated compounds capitalize each part (``well-known`` ->
    ``Well-Known``) while apostrophes stay inside the word so ``don't`` ->
    ``Don't`` (``str.title`` would produce ``Don'T``). Leading punctuation and
    quotes are preserved.
    """
    return "-".join(_title_segment(part) for part in word.split("-"))


def title_case_text(text: str) -> str:
    """Title-case every whitespace-delimited word, preserving whitespace."""
    return re.sub(r"\S+", lambda m: _title_word(m.group(0)), text or "")


def _case_text(action: str, text: str) -> str:
    if action == "uppercase":
        return text.upper()
    if action == "lowercase":
        return text.lower()
    return title_case_text(text)


def _apply_case(action: str, text: str, started: float) -> TransformResult:
    out = _case_text(action, text)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if out == text:
        return TransformResult(
            ok=False, code="VOICE_EDIT_NO_CHANGE",
            detail=_CASE_DETAIL[action], action=action,
            model="deterministic", provider="", locality="local",
            privacy_mode="local", elapsed_ms=elapsed_ms,
            prompt_version="case")
    return TransformResult(
        ok=True, text=out, action=action, model="deterministic",
        provider="", locality="local", privacy_mode="local",
        elapsed_ms=elapsed_ms, prompt_version="case")


def _preserved_tokens(text: str) -> List[str]:
    out: List[str] = []
    for rx in (_URL_RE, _FLAG_RE, _BACKTICK_RE, _IDENT_RE):
        out.extend(rx.findall(text or ""))
    return [t for t in out if len(t) >= 2]


def _dropped_tokens(source: str, target: str) -> List[str]:
    want = _preserved_tokens(source)
    low = target or ""
    dropped = []
    for tok in want:
        if tok not in low and tok not in dropped:
            dropped.append(tok)
    # numeric preservation
    for n in set(_NUM_RE.findall(source or "")):
        if n not in low and n not in dropped and len(n) >= 2:
            dropped.append(n)
    return dropped[:20]


_COMMENTARY_HEAD_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*"
    r"(changes?|change log|explanations?|notes?|reasoning|why|"
    r"here(?:'s| is) (?:what|why|the)|key changes?|summary of changes)"
    r"\b[^.!?\n]{0,32}\s*(?:\*\*)?\s*:?\s*$", re.IGNORECASE)
_PREAMBLE_RE = re.compile(
    r"^\s*(?:sure|totally|absolutely|certainly|of course|alright|all right|"
    r"okay|ok|great|no problem|happy to help|i'?d love to help|i can help|"
    r"i'?ll help|here(?:'s| is)|below)\b[^\n]{0,240}\n"
    r"(?:[ \t]*\n)?", re.IGNORECASE)
# A model that answers with a clarifying question instead of a rewrite.
_ASKING_RE = re.compile(
    r"\b(could you|can you|would you|i need (?:a bit )?more|please provide|"
    r"please share|provide more|more detail|what (?:is|are) the)\b",
    re.IGNORECASE)

# Trailing conversational offer-tails the small local model appends to
# shorten/funny outputs ("Let me know if you need further details.",
# "Is there anything else I can assist with?"). Mirrors _PREAMBLE_RE
# (leading-only precedent): an allowlist of EXACT tail shapes, anchored at
# the end, stripped only when real content precedes the tail. Never fuzzy —
# a genuine closing ("Call me Ishmael.", "Yours truly, Qubit.", "Let me
# know, Ishmael.") never matches a shape, and a tail with nothing before
# it is kept verbatim (never strip to empty).
_TAIL_RE = re.compile(
    r"\s*(?:let me know if you need (?:anything else|anything|"
    r"further details|any further assistance|more help|any help)"
    r"|is there anything else i can (?:help with|assist with|"
    r"assist you with|do for you)"
    r"|how can i (?:assist|help) you(?: further)?)"
    r"\s*[.!?]?\s*$",
    re.IGNORECASE)

_LABEL_PREFIX_RE = re.compile(
    r"^\s*(?:\*\*)?(corrected sentence|corrected text|corrected version|"
    r"rewritten text|rewritten version|improved (?:text|prompt|version)|"
    r"concise version|transformed text|result|translation|summary|email|"
    r"output)\s*(?:\*\*)?\s*:\s*",
    re.IGNORECASE)


def _strip_model_wrapper(text: str) -> str:
    """Remove a whole-output code fence, 'Here is ...' prefix, a leading label,
    and a trailing 'Changes:'/'Explanation:' commentary block.

    Models occasionally ignore 'return only the text'; this keeps the preview
    showing just the transformation. It only ever removes clearly-labelled
    commentary, never ordinary prose.
    """
    t = (text or "").strip()
    if t.startswith("```"):
        m = re.match(r"^```[a-zA-Z0-9_+-]*\n(.*)\n```$", t, re.DOTALL)
        if m:
            t = m.group(1).strip()
    lines = t.splitlines()
    if len(lines) > 1 and len(lines[0]) < 160:
        head = lines[0].strip().lower()
        if (head.endswith(":") and (
                head.startswith("here") or head.startswith("sure")
                or head.startswith("certainly") or head.startswith("below"))):
            t = "\n".join(lines[1:]).strip()
    # Drop a trailing labelled commentary/change-log block.
    block = t.splitlines()
    cut: Optional[int] = None
    for i in range(1, len(block)):
        prev_blank = block[i - 1].strip() == ""
        if _COMMENTARY_HEAD_RE.match(block[i]) and (prev_blank or i == 1):
            cut = i - 1 if prev_blank else i
            break
    if cut is not None:
        t = "\n".join(block[:cut]).strip()
    # Strip a single leading "Corrected sentence:" style label (and the blank
    # line it may leave behind).
    m = _LABEL_PREFIX_RE.match(t)
    if m:
        t = t[m.end():].lstrip("\n").strip()
    # Drop a short conversational preamble sentence ("Totally! ...",
    # "Sure, here you go.") when real content follows. The policy asks models
    # not to preface; this enforces it without eating genuine first lines.
    m = _PREAMBLE_RE.match(t)
    if m and (t[m.end():].strip()):
        t = t[m.end():].lstrip("\n").strip()
    # Drop a trailing conversational offer-tail ("... Let me know if you
    # need further details.") when real content precedes it. The policy
    # asks models not to append offers; this enforces it without eating
    # genuine closings (exact allowlist shapes only, and never when
    # nothing precedes the tail).
    m = _TAIL_RE.search(t)
    if m and (t[:m.start()].strip()):
        t = t[:m.start()].rstrip()
    return t


# Meta-commentary from small local models that narrate ("I'm processing...")
# instead of rewriting, despite the output-only policy. The existing
# _strip_model_wrapper handles labelled heads ("Here is ...:" on its own
# line, "Corrected version:"); this guard handles the unlabelled narration
# class: a leading first-person preamble when the rewritten text follows
# (strip it), or a narration-only output with no rewrite at all (fail so the
# API-fallback chain engages instead of returning narration as the rewrite).
_NARRATION_MARKERS = (
    "i'm processing", "i am processing",
    "i'll respond", "i will respond",
    "i'll provide", "i will provide",
    "i'm working", "i am working",
    "let me ",
    "one moment", "just a moment", "give me a moment",
    "here is", "here's", "sure,",
    # Instruction-echo class (small local models narrating the system
    # instruction instead of rewriting): "We are asked to reply with
    # exactly: ...". Kept reply/response-scoped so genuine content that
    # happens to start similarly is never stripped.
    "we are asked to reply", "we're asked to reply",
    "we are asked to respond", "we're asked to respond",
    "rewrote your text", "rewrote your request",
    "reply with exactly",
)


def _has_narration_marker(head: str) -> bool:
    low = (head or "").lower()
    return any(m in low for m in _NARRATION_MARKERS)


# A whole output that is one narration statement and nothing else (single
# sentence so a "marker + real rewrite" output never matches: the sentence
# break before the rewrite defeats the full-string anchor). The "here is" /
# "sure" branches only match announcement/offer shapes ("Here is the rewritten
# version.", "Sure, I can help with that.") so genuine content that happens
# to start the same way ("Here is my idea for the launch.", "Sure thing,
# let's meet at noon.") still passes through as the rewrite.
_NARRATION_WHOLE_RE = re.compile(
    r"^\s*(?:"
    r"i(?:'m| am)\s+(?:processing|working on|getting|preparing|rewriting|"
    r"handling|looking at)\b"
    r"|i(?:'ll| will)\s+(?:respond|provide|rewrite|reply|get back to you|"
    r"take care of)\b"
    r"|let me\s+(?:process|rewrite|work on|take|just)\b"
    r"|(?:just\s+)?(?:one|a)\s+moment\b|give me a (?:moment|second)\b"
    r"|here(?:'s| is| are)\s+(?:the\s+)?(?:rewrit\w*|correct\w*|improv\w*|"
    r"concise|clearer|better|updat\w*|summar\w*|translat\w*|result|"
    r"version|email|draft|bullet)\b"
    r"|here\s+(?:you go|it is|we go|you are)\b"
     r"|sure,?\s+(?:i can help|here(?:'s| is| are)|i(?:'ll| will)|let me|"
     r"(?:just\s+)?(?:one|a)\s+moment|give me a (?:moment|second)|"
     r"absolutely|of course|no problem|happy to help)\b"
     r"|i\s+(?:just\s+)?rewrote\s+(?:your|the)\s+(?:text|selection|"
     r"passage|message|request)\b"
     r")[^.!?\n]{0,160}[.!?]?\s*$",
    re.IGNORECASE)


# First-person future promise paired with a confirm/request clause or a
# question (2026-10-04 owner turn: "I will handle the task immediately.
# Please confirm the details to proceed with the app update." shipped as an
# ok:true rewrite). The promise alone is genuine content ("I will move the
# meeting to Thursday." must pass); the confirm/request clause or question
# is what marks it as the model stalling for instructions instead of
# rewriting. Scans the whole output (not single-sentence anchored) because
# the shape is promise-sentence + confirm-sentence.
_PROMISE_RE = re.compile(r"\bi(?:'ll|\s+will)\s+[a-z]+", re.IGNORECASE)
_CONFIRM_REQUEST_RE = re.compile(
    r"please confirm|do you want me to|shall\s+i\b|let me know\s+to proceed",
    re.IGNORECASE)


def _is_future_promise_request(text: str) -> bool:
    t = text or ""
    if not _PROMISE_RE.search(t):
        return False
    if "?" in t:
        return True
    return bool(_CONFIRM_REQUEST_RE.search(t))


# Instruction echo: the model repeats the system instruction ("reply with
# exactly") instead of producing the rewrite. Anchored at the start (after
# the wrapper strip) so genuine content that merely contains the phrase
# mid-sentence ("Please reply with exactly the amount due.") never matches;
# the pipeline strips a recoverable rewrite first and only fails on the
# remainder.
_INSTRUCTION_ECHO_RE = re.compile(
    r"^\s*we(?:'re| are)\s+asked\s+to\s+(?:reply|respond|output|return|"
    r"repeat|produce|write|say)\b"
    r"|^\s*(?:reply|respond|output|return|repeat|echo)\s+with\s+exactly\b",
    re.IGNORECASE)


# Verbatim echo: the model returns the input unchanged (modulo case,
# punctuation, or whitespace) instead of rewriting it. Small local models do
# this on short/fragment inputs that look "already fine" ("the quick brown
# fox." -> "the quick brown fox"). Returning it as ok:true lets the owner
# hear their own words back as the result, so an echo is never a rewrite:
# the engine retries once with an explicit reword instruction, then fails
# honestly (VOICE_EDIT_NO_CHANGE) instead of returning input-as-output.
_ECHO_RETRY_INSTRUCTION = (
    "The previous output was identical to the input. Reword it now using "
    "different words with the same meaning; never return the input "
    "unchanged.")


def _normalize_for_echo(text: str) -> str:
    low = (text or "").lower()
    collapsed = re.sub(r"[^\w]+", " ", low, flags=re.UNICODE)
    return re.sub(r"\s+", " ", collapsed).strip()


def is_echo_output(source: str, target: str) -> bool:
    """True when the rewrite is the input back verbatim (case/punct/
    whitespace-insensitive). Empty normalizations never count as echo."""
    norm_src = _normalize_for_echo(source)
    if not norm_src:
        return False
    return norm_src == _normalize_for_echo(target)


def strip_narration_preamble(text: str) -> str:
    """Remove leading meta-commentary when the rewritten text follows it.

    Two forms: a narration head ending in ':' ("I will provide the rewrite
    below:\\n<rewrite>", "Sure, here is the clearer version: <rewrite>") and
    a leading narration sentence ("I'm processing your text. <rewrite>").
    Returns the input unchanged when there is no narration lead, or when
    nothing follows the preamble (that is the narration-only case, handled
    by is_narration_only). Genuine first-person rewrites without a narration
    marker are never touched.
    """
    t = text or ""
    while True:
        # Form A: narration head up to the first colon, rewrite after it.
        m = re.match(r"^\s*(.{1,160}?):\s*(.+)$", t, re.DOTALL)
        if m and _has_narration_marker(m.group(1)):
            t = m.group(2).strip()
            continue
        # Form B: leading narration sentence, rewrite after it.
        m = re.match(r"^\s*([^.!?\n]{1,200}[.!?])\s+(.+)$", t, re.DOTALL)
        if m and _has_narration_marker(m.group(1)):
            t = m.group(2).strip()
            continue
        return t


def is_narration_only(text: str) -> bool:
    """True when the whole output is meta-commentary with no rewrite in it."""
    t = (text or "").strip()
    if not t:
        return False
    if _INSTRUCTION_ECHO_RE.search(t):
        return True
    rest = strip_narration_preamble(t).strip()
    if not rest:
        return True
    if _is_future_promise_request(rest):
        return True
    return bool(_NARRATION_WHOLE_RE.match(rest))


def _read_cfg(cfg: Any, *path: str, default: Any = None) -> Any:
    try:
        return cfg.get(*path, default=default)
    except Exception:
        return default


def _routing_settings(cfg: Any) -> Dict[str, Any]:
    return {
        "routing": str(_read_cfg(cfg, "voice_edit", "routing",
                                 default="auto") or "auto").lower(),
        "local_profile": str(_read_cfg(cfg, "voice_edit", "local_profile",
                                       default="qubit-brain4") or
                             "qubit-brain4"),
        # Rewriting is careful work, not speed work: the detail tier
        # (usually a bigger local model) handles transforms when
        # configured, the local tier otherwise. Chat answers keep the
        # fast local tier.
        "detail_model": str(_read_cfg(cfg, "voice_edit", "detail_model",
                                      default="") or ""),
        "api_profile": str(_read_cfg(cfg, "voice_edit", "api_profile",
                                     default="") or ""),
        "allow_api": bool(_read_cfg(cfg, "voice_edit", "allow_api",
                                    default=True)),
        "timeout_s": float(_read_cfg(cfg, "voice_edit", "timeout_s",
                                     default=DEFAULT_TIMEOUT_S) or
                           DEFAULT_TIMEOUT_S),
        "max_input_chars": int(_read_cfg(cfg, "voice_edit", "max_input_chars",
                                         default=MAX_INPUT_CHARS) or
                               MAX_INPUT_CHARS),
    }


_DEGENERATE_JUNK = {
    "ok", "okay", "sure", "done", "yes", "no", "fine", "great",
    "certainly", "of course", "got it", "understood",
}


def _degenerate_output(text: str, out: str, action: str) -> bool:
    """True when a model answer cannot be a real rewrite of *text*.

    Tiny local models sometimes answer the instruction with "OK." or a short
    prefix of the input instead of transforming it. Treating that as a
    successful rewrite put the junk into the user's field, so it must be
    retried on the stronger tier (auto mode) or surfaced as a typed error.
    Shorten/summarize may legitimately be much shorter, so they use a lower
    floor and are never escalated for length alone.
    """
    source = (text or "").strip()
    answer = (out or "").strip()
    if not answer:
        return True
    core = answer.lower().strip(" \t\r\n.!?;:,'\"")
    if core in _DEGENERATE_JUNK:
        return True
    action = str(action or "").lower()
    if action in ("summarize", "shorten", "bullet_points"):
        # A short answer is the point of these actions; only reject junk or
        # an effectively empty result.
        return len(answer) < 2
    floor = max(4, int(len(source) * 0.2))
    if len(answer) < floor:
        return True
    # A truncated prefix ("The quick brown fox" for a 120-char sentence) is
    # not a rewrite, even when it is longer than the junk floor.
    if source and answer.lower() != source.lower() and \
            source.lower().startswith(answer.lower()) and \
            len(answer) < int(len(source) * 0.6):
        return True
    return False


def _fallback_target(settings: Dict[str, Any]) -> Optional[str]:
    """The API tier for a one-shot retry: configured profile, else active."""
    return settings["api_profile"] or None

class TransformEngine:
    """Runs one transformation through the canonical provider gateway."""

    def __init__(self, *, runner: Optional[Callable[..., Any]] = None) -> None:
        # ``runner`` is the test seam and defaults to the real gateway.
        self._runner = runner

    def _invoke(self, cfg: Any, *, system: str, user: str,
                profile_id: Optional[str], timeout_s: float,
                cancel: Optional[Callable[[], bool]]):
        if self._runner is not None:
            return self._runner(cfg, system=system, user=user,
                                profile_id=profile_id, timeout_s=timeout_s,
                                cancel=cancel)
        from ..providers import gateway as GW

        return GW.run_role(
            user, cfg, role="chat", profile_id=profile_id,
            max_tokens=4096, timeout_s=timeout_s,
            system_prompt=system, local_mode="generate",
            should_stop=cancel or (lambda: False),
            reasoning="low")

    def transform(self, cfg: Any, cmd: Any, *, selected_text: str = "",
                  cancel: Optional[Callable[[], bool]] = None,
                  privacy_override: str = "") -> TransformResult:
        # Verbatim dictation ("Qubit, type this for me: ..."): no model, no
        # egress, no rewriting. The payload is inserted exactly as spoken.
        if getattr(cmd, "action", "") == "dictate_text":
            payload = str(getattr(cmd, "dictated_text", "") or "").strip()
            if not payload:
                return TransformResult(
                    ok=False, code="VOICE_EDIT_NOTHING_TO_TYPE",
                    detail="no text was spoken after the dictation command",
                    action="dictate_text", privacy_mode="local")
            return TransformResult(
                ok=True, text=payload, action="dictate_text",
                model="verbatim", provider="", locality="local",
                privacy_mode="local", prompt_version="dictation",
                detail="verbatim dictation (no model)")

        text = selected_text if selected_text else getattr(cmd, "selected_text", "")
        text = str(text or "")
        if not text.strip():
            return TransformResult(ok=False, code="VOICE_EDIT_NO_SELECTION",
                                   detail="no selected text to transform")
        settings = _routing_settings(cfg)
        if len(text) > settings["max_input_chars"]:
            return TransformResult(
                ok=False, code="VOICE_EDIT_TOO_LONG",
                detail=f"selection is {len(text)} chars; cap is "
                       f"{settings['max_input_chars']}",
                action=getattr(cmd, "action", ""))
        mode = (privacy_override or settings["routing"]).lower()
        if mode not in ("local", "api", "auto"):
            mode = "auto"

        # Case transforms are deterministic and local: handled before prompt
        # rendering and before any provider/profile resolution, so they work
        # cold, offline, and in api-only configurations.
        case_action = str(getattr(cmd, "action", "") or "")
        if case_action in _CASE_ACTIONS:
            return _apply_case(case_action, text, time.monotonic())

        rendered = render_prompt(cmd, text)
        started = time.monotonic()
        injection = scan_injection(text)

        profile_id: Optional[str] = None
        fallback_note = ""
        if mode in ("local", "auto"):
            profile_id = settings["detail_model"] or settings["local_profile"]
        elif mode == "api":
            if not settings["allow_api"]:
                return TransformResult(
                    ok=False, code="VOICE_EDIT_API_DISABLED",
                    detail="API transformations are disabled in settings",
                    action=getattr(cmd, "action", ""), privacy_mode="api")
            if not settings["api_profile"]:
                return TransformResult(
                    ok=False, code="VOICE_EDIT_API_PROFILE_REQUIRED",
                    detail="no API profile configured for voice editing",
                    action=getattr(cmd, "action", ""), privacy_mode="api")
            profile_id = settings["api_profile"]

        res = self._invoke(cfg, system=rendered["system"],
                           user=rendered["user"], profile_id=profile_id,
                           timeout_s=settings["timeout_s"], cancel=cancel)
        elapsed_ms = int((time.monotonic() - started) * 1000)

        locality = ""
        details = getattr(res, "details", None) or {}
        if isinstance(details, dict):
            locality = str(details.get("locality", "") or "")

        # Auto fallback to the API is explicit, gated, and recorded.
        if (not getattr(res, "ok", False) and mode == "auto"
                and settings["allow_api"] and settings["api_profile"]
                and locality != "external"):
            code = str(getattr(res, "code", "") or "")
            if code in ("MODEL_UNAVAILABLE", "PROVIDER_OUTAGE"):
                fallback_note = f"local {code}; retried on configured API"
                res = self._invoke(cfg, system=rendered["system"],
                                   user=rendered["user"],
                                   profile_id=settings["api_profile"],
                                   timeout_s=settings["timeout_s"],
                                   cancel=cancel)
                elapsed_ms = int((time.monotonic() - started) * 1000)
                details = getattr(res, "details", None) or {}
                if isinstance(details, dict):
                    locality = str(details.get("locality", "") or "")

        if not getattr(res, "ok", False):
            return TransformResult(
                ok=False, code=str(getattr(res, "code", "MODEL_ERROR") or
                                   "MODEL_ERROR"),
                detail=str(getattr(res, "message", "") or "model refused")[:200],
                action=getattr(cmd, "action", ""),
                style=getattr(cmd, "style", ""),
                provider=str(getattr(res, "provider", "") or ""),
                model=str(getattr(res, "model", "") or ""),
                elapsed_ms=elapsed_ms, privacy_mode=mode,
                prompt_version=rendered["prompt_version"],
                injection_suspected=injection)

        out = _strip_model_wrapper(str(getattr(res, "text", "") or ""))
        if not out.strip():
            return TransformResult(
                ok=False, code="VOICE_EDIT_EMPTY_RESULT",
                detail="model returned no text; nothing was fabricated",
                action=getattr(cmd, "action", ""), model=str(
                    getattr(res, "model", "") or ""),
                provider=str(getattr(res, "provider", "") or ""),
                elapsed_ms=elapsed_ms, privacy_mode=mode,
                prompt_version=rendered["prompt_version"],
                injection_suspected=injection)

        # A narration preamble with the rewrite after it: keep the rewrite
        # BEFORE the narration-only verdict, so "echo: REAL" recovers the
        # rewrite and only a pure echo fails below.
        out = strip_narration_preamble(out)
        if not out.strip():
            return TransformResult(
                ok=False, code="VOICE_EDIT_EMPTY_RESULT",
                detail="model returned no text; nothing was fabricated",
                action=getattr(cmd, "action", ""), model=str(
                    getattr(res, "model", "") or ""),
                provider=str(getattr(res, "provider", "") or ""),
                elapsed_ms=elapsed_ms, privacy_mode=mode,
                prompt_version=rendered["prompt_version"],
                injection_suspected=injection)

        # Narration-only output: the model talked about rewriting instead of
        # rewriting. In auto mode retry once on the configured API (recorded,
        # never silent); otherwise fail loudly so narration is never
        # returned as the rewrite.
        if is_narration_only(out):
            if (mode == "auto" and settings["allow_api"]):
                fallback_note = ("local model returned narration only; "
                                 "retried on the API tier")
                res = self._invoke(cfg, system=rendered["system"],
                                   user=rendered["user"],
                                   profile_id=_fallback_target(settings),
                                   timeout_s=settings["timeout_s"],
                                   cancel=cancel)
                elapsed_ms = int((time.monotonic() - started) * 1000)
                details = getattr(res, "details", None) or {}
                if isinstance(details, dict):
                    locality = str(details.get("locality", "") or "")
                if not getattr(res, "ok", False):
                    return TransformResult(
                        ok=False, code=str(getattr(res, "code", "MODEL_ERROR")
                                           or "MODEL_ERROR"),
                        detail=str(getattr(res, "message", "")
                                   or "model refused")[:200],
                        action=getattr(cmd, "action", ""),
                        style=getattr(cmd, "style", ""),
                        provider=str(getattr(res, "provider", "") or ""),
                        model=str(getattr(res, "model", "") or ""),
                        elapsed_ms=elapsed_ms, privacy_mode=mode,
                        prompt_version=rendered["prompt_version"],
                        injection_suspected=injection)
                out = _strip_model_wrapper(
                    str(getattr(res, "text", "") or ""))
                out = strip_narration_preamble(out)
                if not out.strip() or is_narration_only(out):
                    return TransformResult(
                        ok=False, code="VOICE_EDIT_NARRATION_ONLY",
                        detail="model returned narration instead of a "
                               "rewrite, including on the API retry",
                        action=getattr(cmd, "action", ""),
                        style=getattr(cmd, "style", ""),
                        provider=str(getattr(res, "provider", "") or ""),
                        model=str(getattr(res, "model", "") or ""),
                        elapsed_ms=elapsed_ms, privacy_mode=mode,
                        prompt_version=rendered["prompt_version"],
                        injection_suspected=injection)
            else:
                return TransformResult(
                    ok=False, code="VOICE_EDIT_NARRATION_ONLY",
                    detail="model returned narration instead of a rewrite; "
                           "nothing was fabricated",
                    action=getattr(cmd, "action", ""),
                    style=getattr(cmd, "style", ""),
                    provider=str(getattr(res, "provider", "") or ""),
                    model=str(getattr(res, "model", "") or ""),
                    elapsed_ms=elapsed_ms, privacy_mode=mode,
                    prompt_version=rendered["prompt_version"],
                    injection_suspected=injection)

        # Degenerate answer (a tiny model replying "OK." or a truncated
        # prefix). In auto mode retry once on the API tier; otherwise fail
        # loudly so junk is never returned as a rewrite.
        if _degenerate_output(text, out, getattr(cmd, "action", "")):
            if mode == "auto" and settings["allow_api"]:
                fallback_note = ("local model returned a fragment; retried "
                                 "on the API tier")
                res = self._invoke(cfg, system=rendered["system"],
                                   user=rendered["user"],
                                   profile_id=_fallback_target(settings),
                                   timeout_s=settings["timeout_s"],
                                   cancel=cancel)
                elapsed_ms = int((time.monotonic() - started) * 1000)
                details = getattr(res, "details", None) or {}
                if isinstance(details, dict):
                    locality = str(details.get("locality", "") or "")
                if not getattr(res, "ok", False):
                    return TransformResult(
                        ok=False, code=str(getattr(res, "code", "MODEL_ERROR")
                                           or "MODEL_ERROR"),
                        detail=str(getattr(res, "message", "")
                                   or "model refused")[:200],
                        action=getattr(cmd, "action", ""),
                        style=getattr(cmd, "style", ""),
                        provider=str(getattr(res, "provider", "") or ""),
                        model=str(getattr(res, "model", "") or ""),
                        elapsed_ms=elapsed_ms, privacy_mode=mode,
                        prompt_version=rendered["prompt_version"],
                        injection_suspected=injection)
                out = strip_narration_preamble(_strip_model_wrapper(
                    str(getattr(res, "text", "") or "")))
            if _degenerate_output(text, out, getattr(cmd, "action", "")):
                return TransformResult(
                    ok=False, code="VOICE_EDIT_DEGENERATE_OUTPUT",
                    detail="the model returned a fragment instead of a "
                           "rewrite; nothing was inserted. Configure a "
                           "stronger detail model or allow the API tier.",
                    action=getattr(cmd, "action", ""),
                    style=getattr(cmd, "style", ""),
                    provider=str(getattr(res, "provider", "") or ""),
                    model=str(getattr(res, "model", "") or ""),
                    elapsed_ms=elapsed_ms, privacy_mode=mode,
                    prompt_version=rendered["prompt_version"],
                    injection_suspected=injection)

        dropped: List[str] = []
        # Verbatim echo: the model returned the input unchanged. Retry ONCE
        # on the same backend with an explicit reword instruction (bounded:
        # no loops, no API escalation); a second echo is an honest
        # no-change result, never input-as-output.
        if is_echo_output(text, out):
            res = self._invoke(cfg, system=rendered["system"] + "\n\n"
                               + _ECHO_RETRY_INSTRUCTION,
                               user=rendered["user"],
                               profile_id=profile_id,
                               timeout_s=settings["timeout_s"],
                               cancel=cancel)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            details = getattr(res, "details", None) or {}
            if isinstance(details, dict):
                locality = str(details.get("locality", "") or "")
            if not getattr(res, "ok", False):
                return TransformResult(
                    ok=False, code=str(getattr(res, "code", "MODEL_ERROR")
                                       or "MODEL_ERROR"),
                    detail=str(getattr(res, "message", "")
                               or "model refused")[:200],
                    action=getattr(cmd, "action", ""),
                    style=getattr(cmd, "style", ""),
                    provider=str(getattr(res, "provider", "") or ""),
                    model=str(getattr(res, "model", "") or ""),
                    elapsed_ms=elapsed_ms, privacy_mode=mode,
                    prompt_version=rendered["prompt_version"],
                    injection_suspected=injection)
            out = _strip_model_wrapper(
                str(getattr(res, "text", "") or ""))
            out = strip_narration_preamble(out)
            if not out.strip() or is_narration_only(out):
                return TransformResult(
                    ok=False, code="VOICE_EDIT_NARRATION_ONLY",
                    detail="model returned narration instead of a "
                           "rewrite, including on the echo retry",
                    action=getattr(cmd, "action", ""),
                    style=getattr(cmd, "style", ""),
                    provider=str(getattr(res, "provider", "") or ""),
                    model=str(getattr(res, "model", "") or ""),
                    elapsed_ms=elapsed_ms, privacy_mode=mode,
                    prompt_version=rendered["prompt_version"],
                    injection_suspected=injection)
            if is_echo_output(text, out):
                return TransformResult(
                    ok=False, code="VOICE_EDIT_NO_CHANGE",
                    detail="the text is already clear as-is; the model "
                           "returned it unchanged, so nothing was replaced",
                    action=getattr(cmd, "action", ""),
                    style=getattr(cmd, "style", ""),
                    provider=str(getattr(res, "provider", "") or ""),
                    model=str(getattr(res, "model", "") or ""),
                    elapsed_ms=elapsed_ms, privacy_mode=mode,
                    prompt_version=rendered["prompt_version"],
                    injection_suspected=injection)
        if getattr(cmd, "preserve_facts", True) and \
                getattr(cmd, "action", "") != "translate":
            dropped = _dropped_tokens(text, out)
        clarified = bool(_ASKING_RE.search(out)) and out.rstrip().endswith("?")

        return TransformResult(
            ok=True, text=out, action=getattr(cmd, "action", ""),
            style=getattr(cmd, "style", ""),
            provider=str(getattr(res, "provider", "") or ""),
            model=str(getattr(res, "model", "") or ""),
            locality=locality or ("local" if mode == "local" else "external"),
            privacy_mode=mode, elapsed_ms=elapsed_ms,
            prompt_version=rendered["prompt_version"],
            injection_suspected=injection,
            clarification_returned=clarified, facts_dropped=dropped,
            detail=fallback_note)


__all__ = ["TransformEngine", "digest_text", "MAX_INPUT_CHARS",
           "strip_narration_preamble", "is_narration_only",
           "is_echo_output"]
