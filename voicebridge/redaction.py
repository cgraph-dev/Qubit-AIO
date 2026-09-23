"""Centralized secret redaction (Packet 1A).

Pure, dependency-free functions used by every boundary that writes journals,
audit events, prompts, or reports. Conservative by design: known credential
shapes are masked; everything else passes through so logs stay useful.
"""
from __future__ import annotations

import re
from typing import Any

MAX_TEXT = 10_000
MAX_DEPTH = 6
MAX_ITEMS = 200
MASK = "[REDACTED]"

# The optional ``\"?`` on both sides of the separator lets quoted JSON/object
# keys redact too: ``{"password": "hunter2"}`` and ``"token":"abc"`` must not
# leak just because the key/separator are double-quoted. The replacement keeps
# groups 1 (key) + 2 (separator, quotes included) verbatim.
_SECRET_KEY = re.compile(
    r"(?i)(?:\b|(?<=_))(api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|"
    r"auth[_-]?token|token|secret|client[_-]?secret|password|passwd|pwd|"
    r"authorization|cookie|session[_-]?id|private[_-]?key|credentials?)"
    r"\b(\"?\s*[=:]\s*\"?)([^\s,&\"']+)")
# A scheme word ("bearer"/"basic") is credential material only when the token
# after it looks like one. Requiring at least one non-letter (a digit or one of
# ``._~+/=-``) stops ordinary prose such as ``"basic lifecycle"`` from being
# masked, while real base64/opaque credentials — which are never a plain
# alphabetic word run — still redact. Tradeoff: a purely alphabetic token is
# left alone, since it is indistinguishable from prose; JWT/``sk-``/URL
# credential shapes stay covered by their own matchers below.
_BEARER = re.compile(
    r"(?i)\b(bearer|basic)\s+"
    r"(?=[A-Za-z0-9._~+/=-]{8,}(?![A-Za-z0-9._~+/=-]))"
    r"(?=[A-Za-z0-9._~+/=-]*[0-9._~+/=-])"
    r"[A-Za-z0-9._~+/=-]{8,}")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")
_SK_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)([^/\s:@]+):([^@\s]+)@")
_URL_SECRET_QUERY = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|token|secret|password|sig|signature)=)"
    r"([^&\s]+)")


def redact_text(text: str) -> str:
    """Return `text` with credential material replaced by [REDACTED]."""
    if not isinstance(text, str):
        text = repr(text)
    out = text
    out = _BEARER.sub(lambda m: f"{m.group(1)} {MASK}", out)
    out = _JWT.sub(MASK, out)
    out = _SK_KEY.sub(MASK, out)
    out = _URL_USERINFO.sub(lambda m: f"{m.group(1)}{MASK}:{MASK}@", out)
    out = _URL_SECRET_QUERY.sub(lambda m: f"{m.group(1)}{MASK}", out)
    out = _SECRET_KEY.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", out)
    if len(out) > MAX_TEXT:
        out = out[:MAX_TEXT] + f"…[truncated {len(out) - MAX_TEXT} chars]"
    return out


def redact_obj(obj: Any, _depth: int = 0) -> Any:
    """Recursively redact strings inside mappings/sequences; scalars survive."""
    if _depth > MAX_DEPTH:
        return MASK
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        items = list(obj.items())[:MAX_ITEMS]
        return {
            (k if not _looks_secret_key(k) else redact_text(str(k))):
            (MASK if _looks_secret_key(k) else redact_obj(v, _depth + 1))
            for k, v in items}
    if isinstance(obj, (list, tuple, set)):
        items = list(obj)[:MAX_ITEMS]
        seq = [redact_obj(v, _depth + 1) for v in items]
        return type(obj)(seq) if isinstance(obj, (list, tuple)) else set(seq)
    return obj


def _looks_secret_key(key: Any) -> bool:
    return bool(isinstance(key, str) and
                re.search(r"(?i)\b(token|secret|password|passwd|api[_-]?key|"
                          r"authorization|cookie|credential)", key))
