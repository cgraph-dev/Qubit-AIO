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
    return t


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
            system_prompt=system, local_mode="chat",
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

        rendered = render_prompt(cmd, text)
        started = time.monotonic()
        injection = scan_injection(text)

        profile_id: Optional[str] = None
        fallback_note = ""
        if mode == "local":
            profile_id = settings["local_profile"]
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

        dropped: List[str] = []
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


__all__ = ["TransformEngine", "digest_text", "MAX_INPUT_CHARS"]
