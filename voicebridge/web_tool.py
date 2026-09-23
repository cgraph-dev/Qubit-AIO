"""Brokered, SSRF-safe web fetch + optional provider-backed search.

This is the smallest honest ``web_fetch`` / ``web_search`` agent tool for
Qubit. It reuses the single canonical URL/SSRF policy already enforced by
:mod:`voicebridge.websites` (``normalize_origin`` / ``blocked_host_reason`` /
``content_type_supported``) instead of inventing a second blocklist.

Guarantees:

* **Scheme allowlist** — only ``http``/``https``.
* **No credentials / query-token URLs** — userinfo and secret-named query
  keys (``token``, ``api_key``, ``sig`` …) are refused.
* **SSRF block, every hop** — the target host is resolved and rejected when it
  is private/loopback/link-local/metadata/reserved; redirects are followed
  manually (auto-follow is disabled) and each hop is re-validated. A redirect
  to a blocked host is refused, never followed.
* **Bounded** — hard caps on body bytes, wall/socket time, redirect count and
  accepted content type.
* **Redacted** — returned text and every receipt pass through the centralized
  redaction layer; secrets never reach the journal or result.
* **E-stop aware** — the durable emergency-stop / global-disable latch refuses
  the call before any network work.
* **Read-only, single path** — GET only, no cookies/authorization, no shell,
  no listener, no browser, no page script execution.

``search`` executes against an operator-configured provider only. When no
provider is configured it returns a typed ``WEB_SEARCH_UNAVAILABLE`` with an
empty result set — it never fabricates a result.
"""
from __future__ import annotations

import hashlib
import os
import re
import socket
import time
import urllib.parse as _urlparse
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .logging_setup import log_event
from .redaction import redact_obj, redact_text
from .websites import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_REDIRECTS,
    DEFAULT_TIMEOUT_S,
    WebsiteValidationError,
    blocked_host_reason,
    content_type_supported,
    normalize_origin,
)

# --------------------------------------------------------------- typed codes
ESTOP_CODE = "WEB_ESTOP"
BAD_INPUT_CODE = "WEB_BAD_INPUT"
SCHEME_CODE = "WEB_INVALID_SCHEME"
CREDENTIALS_CODE = "WEB_CREDENTIALS"
QUERY_TOKEN_CODE = "WEB_QUERY_TOKEN"
BLOCKED_HOST_CODE = "WEB_BLOCKED_HOST"
REDIRECT_BLOCKED_CODE = "WEB_REDIRECT_BLOCKED"
TOO_MANY_REDIRECTS_CODE = "WEB_TOO_MANY_REDIRECTS"
OVERSIZED_CODE = "WEB_OVERSIZED"
TIMEOUT_CODE = "WEB_TIMEOUT"
CONTENT_TYPE_CODE = "WEB_CONTENT_TYPE_UNSUPPORTED"
HTTP_STATUS_CODE = "WEB_HTTP_ERROR"
AUTH_CODE = "WEB_AUTH_REQUIRED"
UNREACHABLE_CODE = "WEB_UNREACHABLE"
TLS_CODE = "WEB_TLS_ERROR"
SEARCH_UNAVAILABLE_CODE = "WEB_SEARCH_UNAVAILABLE"
SEARCH_PROVIDER_CODE = "WEB_SEARCH_PROVIDER_ERROR"
X_SEARCH_UNAVAILABLE_CODE = "WEB_X_SEARCH_UNAVAILABLE"
READABILITY_EMPTY_CODE = "WEB_READABILITY_EMPTY"

UNAVAILABLE = "UNAVAILABLE"
OK = "OK"
READABILITY_SCHEMA = "qubit-web-readability/1"

# ------------------------------------------------------------------- bounds
MAX_URL_LEN = 2048
MAX_QUERY_CHARS = 400
MAX_RESULT_CHARS = 8000
MAX_FETCH_BYTES = 1_000_000
MIN_TIMEOUT_S = 0.1
MAX_TIMEOUT_S = 60.0
MAX_REDIRECT_LIMIT = 10
DEFAULT_SEARCH_LIMIT = 5
MAX_SEARCH_LIMIT = 20
MAX_SEARCH_RESULT_URL = 1024
MAX_SEARCH_SNIPPET = 600
DEFAULT_MAX_READABLE_CHARS = 20000
MAX_MAX_READABLE_CHARS = 200000

_REDIRECT_CODES = (301, 302, 303, 307, 308)

_USER_AGENT = "QubitVoiceAgent-WebTool/1.0 (bounded, read-only)"
_ACCEPT = "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5"

_SCRIPT_RE = re.compile(r"(?is)<(script|style|noscript|template)\b.*?</\1>")
_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_RE = re.compile(r"\n[ \t]*\n[ \t]*\n+")
_SPACE_RE = re.compile(r"[ \t\f\v]+")
_ARTICLE_RE = re.compile(r"(?is)<article\b[^>]*>(.*?)</article>")
_MAIN_RE = re.compile(r"(?is)<main\b[^>]*>(.*?)</main>")
_READABILITY_DROP_RE = re.compile(
    r"(?is)<(nav|header|footer|aside|form|script|style|noscript|template)"
    r"\b.*?</\1>")


class WebToolError(Exception):
    """Typed web-tool refusal (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


# ------------------------------------------------------------- HTTP boundary
class _NoRedirectHandler(HTTPRedirectHandler):
    """Refuse transparent redirects so every hop is policy-checked here."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        raise HTTPError(req.full_url, code, msg, headers, fp)


def _open(req: Request, timeout: float):
    """One bounded GET through the no-auto-redirect opener.

    Kept as a module function so tests can monkeypatch the HTTP layer with no
    live network access.
    """
    opener = build_opener(_NoRedirectHandler())
    return opener.open(req, timeout=timeout)


# ----------------------------------------------------------------- estop
def estop_engaged(cfg: Any) -> bool:
    """Same durable latch the broker/dashboard/voice observe (fail closed)."""
    try:
        from .plugins import estop_engaged as _engaged

        return bool(_engaged(cfg))
    except Exception:
        return True


def refuse_estop(operation: str) -> Dict[str, Any]:
    return {"state": "DENIED", "code": ESTOP_CODE, "operation": operation,
            "detail": "emergency stop latched; web tool refused",
            "owner": "qubit-policy",
            "rollback": "nothing fetched or queried; nothing to roll back"}


def _audit(op: str, **fields: Any) -> None:
    try:
        log_event(f"web_tool.{op}", **fields)
    except Exception:
        pass


# ------------------------------------------------------------- URL policy
def _map_validation_code(exc: Exception) -> str:
    msg = str(exc)
    if "credential-bearing" in msg:
        return CREDENTIALS_CODE
    if "query-token" in msg:
        return QUERY_TOKEN_CODE
    if "private/out-of-scope" in msg:
        return BLOCKED_HOST_CODE
    if "invalid scheme" in msg:
        return SCHEME_CODE
    return BAD_INPUT_CODE


def _validate_target(url: Any) -> str:
    """Validate the request URL through the canonical policy; return origin."""
    text = str(url or "").strip()
    if not text or len(text) > MAX_URL_LEN:
        raise WebToolError(BAD_INPUT_CODE, "URL is required (<=2048 chars)")
    try:
        origin, _clean, _name = normalize_origin(text, resolve=True)
    except WebsiteValidationError as exc:
        raise WebToolError(_map_validation_code(exc), str(exc)) from None
    return origin


def _validate_redirect(next_url: str) -> None:
    """Re-check a redirect target; any refusal is a blocked redirect."""
    try:
        normalize_origin(next_url, resolve=True)
    except WebsiteValidationError as exc:
        raise WebToolError(
            REDIRECT_BLOCKED_CODE,
            f"redirect target refused: {exc}") from None


# --------------------------------------------------------------- helpers
def _header(resp: Any, name: str) -> str:
    hdrs = getattr(resp, "headers", None)
    if hdrs is None:
        return ""
    try:
        return str(hdrs.get(name, "") or "")
    except Exception:
        return ""


def _check_bounds(max_bytes: Any, timeout: Any, max_redirects: Any) -> None:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) \
            or not (1 <= max_bytes <= MAX_FETCH_BYTES):
        raise WebToolError(
            BAD_INPUT_CODE, f"max_bytes must be an int in [1, {MAX_FETCH_BYTES}]")
    try:
        timeout_f = float(timeout)
    except (TypeError, ValueError):
        raise WebToolError(BAD_INPUT_CODE, "timeout must be a number") from None
    if not (MIN_TIMEOUT_S <= timeout_f <= MAX_TIMEOUT_S):
        raise WebToolError(
            BAD_INPUT_CODE,
            f"timeout must be in [{MIN_TIMEOUT_S:g}, {MAX_TIMEOUT_S:g}]s")
    if isinstance(max_redirects, bool) or not isinstance(max_redirects, int) \
            or not (0 <= max_redirects <= MAX_REDIRECT_LIMIT):
        raise WebToolError(
            BAD_INPUT_CODE,
            f"max_redirects must be an int in [0, {MAX_REDIRECT_LIMIT}]")


def _visible_text(body: bytes, ctype: str) -> str:
    if not body:
        return ""
    try:
        text = body.decode("utf-8", "replace")
    except Exception:
        return ""
    low = (ctype or "").lower()
    if "html" in low or "<html" in text[:2000].lower():
        text = _SCRIPT_RE.sub(" ", text)
        text = _TAG_RE.sub(" ", text)
        try:
            import html as _html

            text = _html.unescape(text)
        except Exception:
            pass
    text = _SPACE_RE.sub(" ", text)
    text = _BLANK_RE.sub("\n\n", text)
    return text.strip()


def readable_text(body: Any, *,
                  max_chars: int = DEFAULT_MAX_READABLE_CHARS) -> Dict[str, Any]:
    """Extract bounded readable article text from fetched HTML.

    Qubit-owned equivalent of the Qubit ``web-readability`` extension. It is
    pure and offline: it prefers an ``<article>``/``<main>`` region, drops page
    chrome, then applies the same whitespace normalization as
    :func:`_visible_text`. It never executes markup, never egresses, and never
    fabricates content — empty input is a typed ``WEB_READABILITY_EMPTY``.
    """
    if isinstance(body, (bytes, bytearray)):
        raw = bytes(body).decode("utf-8", "replace")
    else:
        raw = str(body or "")
    try:
        limit = int(max_chars)
    except (TypeError, ValueError):
        raise WebToolError(BAD_INPUT_CODE, "max_chars must be an int")
    if limit < 1 or limit > MAX_MAX_READABLE_CHARS:
        raise WebToolError(
            BAD_INPUT_CODE,
            f"max_chars must be in [1, {MAX_MAX_READABLE_CHARS}]")
    if not raw.strip():
        return {"state": UNAVAILABLE, "code": READABILITY_EMPTY_CODE,
                "schema": READABILITY_SCHEMA, "text": "", "chars": 0,
                "truncated": False,
                "detail": "no readable input was provided"}
    text = _READABILITY_DROP_RE.sub(" ", raw)
    region = None
    for pattern in (_ARTICLE_RE, _MAIN_RE):
        match = pattern.search(text)
        if match and match.group(1).strip():
            region = match.group(1)
            break
    if region is not None:
        text = region
    text = _SCRIPT_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    try:
        import html as _html

        text = _html.unescape(text)
    except Exception:
        pass
    text = _SPACE_RE.sub(" ", text)
    text = _BLANK_RE.sub("\n\n", text).strip()
    truncated = len(text) > limit
    if truncated:
        text = text[:limit].rstrip()
    return {"state": OK, "code": "WEB_READABILITY_OK",
            "schema": READABILITY_SCHEMA, "text": text, "chars": len(text),
            "truncated": truncated,
            "detail": "readable text extracted" if text else
                      "no readable text remained after chrome removal"}


# --------------------------------------------------------------- fetch
def fetch(cfg: Any, url: str, *, max_bytes: int = DEFAULT_MAX_BYTES,
          timeout: float = DEFAULT_TIMEOUT_S,
          max_redirects: int = DEFAULT_MAX_REDIRECTS) -> Dict[str, Any]:
    """Bounded, redacted, SSRF-safe GET of a public URL.

    Raises :class:`WebToolError` with a typed ``.code`` for every refusal
    (estop, scheme, credentials, query-token, blocked host, blocked redirect,
    too many redirects, oversized, timeout, unsupported content type, HTTP
    error, unreachable/TLS). No live network is required when the HTTP layer
    (``_open``) is monkeypatched.
    """
    if estop_engaged(cfg):
        _audit("estop_refused", tool="web.fetch")
        raise WebToolError(ESTOP_CODE,
                           "emergency stop latched; web fetch refused")
    _check_bounds(max_bytes, timeout, max_redirects)
    origin = _validate_target(url)

    headers = {"User-Agent": _USER_AGENT, "Accept": _ACCEPT}
    current = str(url or "").strip()
    redirects = 0
    started = time.monotonic()

    while True:
        if time.monotonic() - started > float(timeout):
            raise WebToolError(TIMEOUT_CODE,
                               f"web fetch exceeded {float(timeout):g}s")
        try:
            resp = _open(Request(current, headers=headers, method="GET"),
                         float(timeout))
        except HTTPError as exc:
            code = int(getattr(exc, "code", 0) or 0)
            loc = _header(exc, "Location")
            try:
                exc.close()
            except Exception:
                pass
            if code in _REDIRECT_CODES:
                if not loc:
                    raise WebToolError(
                        REDIRECT_BLOCKED_CODE,
                        "redirect response without a Location header") from None
                if redirects >= max_redirects:
                    raise WebToolError(
                        TOO_MANY_REDIRECTS_CODE,
                        f"redirect limit ({max_redirects}) exceeded") from None
                nxt = urljoin(current, loc)
                _validate_redirect(nxt)
                current = nxt
                redirects += 1
                continue
            if code in (401, 403):
                raise WebToolError(AUTH_CODE,
                                   f"target returned HTTP {code}") from None
            raise WebToolError(HTTP_STATUS_CODE,
                               f"target returned HTTP {code}") from None
        except (TimeoutError, socket.timeout):
            raise WebToolError(
                TIMEOUT_CODE,
                f"web fetch timed out after {float(timeout):g}s") from None
        except URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                raise WebToolError(
                    TIMEOUT_CODE,
                    f"web fetch timed out after {float(timeout):g}s") from None
            low = str(reason).lower()
            if "ssl" in low or "certificate" in low:
                raise WebToolError(TLS_CODE,
                                   "secure connection failed (TLS)") from None
            raise WebToolError(UNREACHABLE_CODE,
                               "target is unreachable") from None
        except WebToolError:
            raise
        except OSError:
            raise WebToolError(UNREACHABLE_CODE,
                               "target is unreachable") from None
        except Exception as exc:  # never leak an untyped traceback
            raise WebToolError(
                UNREACHABLE_CODE,
                redact_text(str(exc))[:200]) from None

        with resp:
            status = int(resp.getcode() or 0)
            final_url = str(resp.geturl() or current)
            ctype = _header(resp, "Content-Type")
            if status in _REDIRECT_CODES:
                loc = _header(resp, "Location")
                if not loc:
                    raise WebToolError(
                        REDIRECT_BLOCKED_CODE,
                        "redirect response without a Location header")
                if redirects >= max_redirects:
                    raise WebToolError(
                        TOO_MANY_REDIRECTS_CODE,
                        f"redirect limit ({max_redirects}) exceeded")
                nxt = urljoin(current, loc)
                _validate_redirect(nxt)
                current = nxt
                redirects += 1
                continue
            body = resp.read(max_bytes + 1)
            if body is None:
                body = b""
            if time.monotonic() - started > float(timeout):
                raise WebToolError(TIMEOUT_CODE,
                                   f"web fetch exceeded {float(timeout):g}s")
            if len(body) > max_bytes:
                raise WebToolError(
                    OVERSIZED_CODE,
                    f"response exceeds the {max_bytes}-byte fetch cap")
            if status in (401, 403):
                raise WebToolError(AUTH_CODE, f"target returned HTTP {status}")
            if status >= 400:
                raise WebToolError(HTTP_STATUS_CODE,
                                   f"target returned HTTP {status}")
            if not content_type_supported(ctype):
                raise WebToolError(
                    CONTENT_TYPE_CODE,
                    f"unsupported content type {ctype[:80]!r}")

            elapsed = time.monotonic() - started
            text = redact_text(_visible_text(body, ctype))[:MAX_RESULT_CHARS]
            safe_url = redact_text(final_url)
            receipt = redact_obj({
                "url": final_url,
                "origin": origin,
                "status": status,
                "content_type": ctype,
                "bytes_read": len(body),
                "redirects": redirects,
                "duration_s": round(elapsed, 4),
                "sha256": hashlib.sha256(body).hexdigest(),
                "limits": {"max_bytes": max_bytes,
                           "timeout_s": float(timeout),
                           "max_redirects": max_redirects},
            })
            _audit("fetch", origin=origin, status=status,
                   bytes_read=len(body), redirects=redirects,
                   duration_s=round(elapsed, 4))
            return {"state": OK, "ok": True, "code": "WEB_FETCH_OK",
                    "url": safe_url, "origin": origin, "content_type": ctype,
                    "text": text, "bytes_read": len(body),
                    "redirects": redirects, "duration_s": round(elapsed, 4),
                    "limits": {"max_bytes": max_bytes,
                               "timeout_s": float(timeout),
                               "max_redirects": max_redirects},
                    "receipt": receipt}


# --------------------------------------------------------------- search
def _cfg_search(cfg: Any, key: str, default: Any = "") -> Any:
    try:
        return cfg.get("web", "search", key, default=default)
    except Exception:
        return default


def _provider_timeout(cfg: Any) -> float:
    try:
        value = float(_cfg_search(cfg, "timeout", default=DEFAULT_TIMEOUT_S))
    except (TypeError, ValueError):
        value = DEFAULT_TIMEOUT_S
    return min(max(value, MIN_TIMEOUT_S), MAX_TIMEOUT_S)


def _build_search_url(provider: str, endpoint: str, query: str) -> str:
    if provider == "json" and "{query}" in endpoint:
        return endpoint.replace("{query}", quote(query, safe=""))
    sep = "&" if "?" in endpoint else "?"
    url = f"{endpoint}{sep}q={quote(query, safe='')}"
    if provider == "searxng" and "format=" not in endpoint:
        url += "&format=json"
    return url


def _dig(payload: Any, path: str) -> Any:
    node = payload
    for part in (path or "").split("."):
        if not part:
            continue
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


def _parse_results(cfg: Any, provider: str, payload: Any,
                   limit: int) -> List[Dict[str, Any]]:
    if provider == "searxng":
        items = _dig(payload, "results")
    else:
        path = str(_cfg_search(cfg, "results_path", default="results") or
                   "results")
        items = _dig(payload, path)
        if items is None and isinstance(payload, list):
            items = payload
    if not isinstance(items, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_url = str(item.get("url") or item.get("link") or "").strip()
        host = ""
        try:
            host = (_urlparse.urlsplit(raw_url).hostname or "").lower()
        except Exception:
            host = ""
        if host and blocked_host_reason(host, resolve=False):
            continue
        out.append({
            "title": redact_text(
                str(item.get("title") or item.get("name") or ""))[:200],
            "url": redact_text(raw_url)[:MAX_SEARCH_RESULT_URL],
            "snippet": redact_text(str(
                item.get("content") or item.get("snippet")
                or item.get("description") or ""))[:MAX_SEARCH_SNIPPET],
        })
        if len(out) >= limit:
            break
    return out


def _search_via_transport(cfg: Any, provider: str, text: str, url: str,
                          limit: int, transport: Any) -> Dict[str, Any]:
    """Run a configured search over an operator-injected request boundary."""
    import json as _json

    try:
        raw = transport({"provider": provider, "method": "GET", "url": url,
                         "query": text, "limit": limit,
                         "headers": {"Accept": "application/json"}})
    except Exception as exc:
        return {"state": "FAILED", "code": SEARCH_PROVIDER_CODE,
                "provider": provider, "query": redact_text(text),
                "results": [], "count": 0,
                "detail": redact_text(str(exc))[:200]}
    if isinstance(raw, (bytes, bytearray)):
        try:
            payload = _json.loads(bytes(raw).decode("utf-8", "replace"))
        except Exception:
            return {"state": "FAILED", "code": SEARCH_PROVIDER_CODE,
                    "provider": provider, "query": redact_text(text),
                    "results": [], "count": 0,
                    "detail": "search provider returned unparseable JSON"}
    elif isinstance(raw, (dict, list)):
        payload = raw
    else:
        return {"state": "FAILED", "code": SEARCH_PROVIDER_CODE,
                "provider": provider, "query": redact_text(text),
                "results": [], "count": 0,
                "detail": "search transport returned an unsupported payload"}
    results = _parse_results(cfg, provider, payload, limit)
    _audit("search", provider=provider, results=len(results))
    return {"state": OK, "ok": True, "code": "WEB_SEARCH_OK",
            "provider": provider, "query": redact_text(text),
            "results": results, "count": len(results),
            "receipt": redact_obj({"provider": provider,
                                   "results": len(results),
                                   "transport": "injected",
                                   "limits": {"limit": limit}})}


def search(cfg: Any, query: str, *, limit: int = DEFAULT_SEARCH_LIMIT,
           timeout: Optional[float] = None,
           transport: Any = None) -> Dict[str, Any]:
    """Search via an operator-configured provider, or typed UNAVAILABLE.

    No provider configured (or no endpoint) returns
    ``{"state": "UNAVAILABLE", "code": "WEB_SEARCH_UNAVAILABLE", ...}`` with an
    empty result set — a result is never fabricated. Provider/network failures
    return typed ``WEB_SEARCH_PROVIDER_ERROR``; the e-stop raises
    ``WEB_ESTOP`` before any work.
    """
    if estop_engaged(cfg):
        _audit("estop_refused", tool="web.search")
        raise WebToolError(ESTOP_CODE,
                           "emergency stop latched; web search refused")
    text = str(query or "").strip()[:MAX_QUERY_CHARS]
    if not text:
        raise WebToolError(BAD_INPUT_CODE, "search query is required")
    try:
        limit_i = int(limit)
    except (TypeError, ValueError):
        limit_i = DEFAULT_SEARCH_LIMIT
    limit_i = max(1, min(limit_i, MAX_SEARCH_LIMIT))

    provider = str(_cfg_search(cfg, "provider", default="") or "").strip().lower()
    endpoint = str(_cfg_search(cfg, "endpoint", default="") or "").strip()
    if not provider or not endpoint:
        return {"state": UNAVAILABLE, "code": SEARCH_UNAVAILABLE_CODE,
                "provider": provider, "query": redact_text(text),
                "results": [], "count": 0,
                "detail": "no search provider is configured; set "
                          "web.search.provider and web.search.endpoint"}
    if provider not in ("searxng", "json"):
        return {"state": UNAVAILABLE, "code": SEARCH_UNAVAILABLE_CODE,
                "provider": provider, "query": redact_text(text),
                "results": [], "count": 0,
                "detail": f"search provider {provider!r} is not supported"}

    timeout_s = float(timeout) if timeout is not None else _provider_timeout(cfg)
    timeout_s = min(max(timeout_s, MIN_TIMEOUT_S), MAX_TIMEOUT_S)
    url = _build_search_url(provider, endpoint, text)
    if transport is not None:
        return _search_via_transport(cfg, provider, text, url, limit_i,
                                     transport)
    try:
        normalize_origin(url, resolve=True)
    except WebsiteValidationError as exc:
        return {"state": UNAVAILABLE, "code": SEARCH_UNAVAILABLE_CODE,
                "provider": provider, "query": redact_text(text),
                "results": [], "count": 0,
                "detail": f"search endpoint refused: {exc}"}

    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    api_key_env = str(_cfg_search(cfg, "api_key_env", default="") or "")
    if api_key_env:
        key = os.environ.get(api_key_env, "")
        if key:
            header = str(_cfg_search(cfg, "api_key_header",
                                     default="Authorization") or "Authorization")
            prefix = str(_cfg_search(cfg, "api_key_prefix", default="") or "")
            headers[header] = (prefix + key) if prefix else key

    started = time.monotonic()
    try:
        resp = _open(Request(url, headers=headers, method="GET"), timeout_s)
        with resp:
            raw = resp.read(MAX_FETCH_BYTES + 1) or b""
            if len(raw) > MAX_FETCH_BYTES:
                return {"state": "FAILED", "code": SEARCH_PROVIDER_CODE,
                        "provider": provider, "query": redact_text(text),
                        "results": [], "count": 0,
                        "detail": "search response exceeds the fetch cap"}
            try:
                import json as _json

                payload = _json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                return {"state": "FAILED", "code": SEARCH_PROVIDER_CODE,
                        "provider": provider, "query": redact_text(text),
                        "results": [], "count": 0,
                        "detail": "search provider returned unparseable JSON"}
    except (TimeoutError, socket.timeout):
        return {"state": "FAILED", "code": SEARCH_PROVIDER_CODE,
                "provider": provider, "query": redact_text(text),
                "results": [], "count": 0,
                "detail": "search provider timed out"}
    except Exception as exc:
        return {"state": "FAILED", "code": SEARCH_PROVIDER_CODE,
                "provider": provider, "query": redact_text(text),
                "results": [], "count": 0,
                "detail": redact_text(str(exc))[:200]}

    results = _parse_results(cfg, provider, payload, limit_i)
    elapsed = time.monotonic() - started
    _audit("search", provider=provider, results=len(results),
           duration_s=round(elapsed, 4))
    return {"state": OK, "ok": True, "code": "WEB_SEARCH_OK",
            "provider": provider, "query": redact_text(text),
            "results": results, "count": len(results),
            "duration_s": round(elapsed, 4),
            "receipt": redact_obj({
                "provider": provider, "results": len(results),
                "duration_s": round(elapsed, 4),
                "limits": {"limit": limit_i, "timeout_s": timeout_s}}),
            }


# --------------------------------------------------------------- agent tool
def x_search(cfg: Any, query: str, *,
             limit: int = DEFAULT_SEARCH_LIMIT,
             transport: Any = None) -> Dict[str, Any]:
    """X/Twitter search — typed UNAVAILABLE until a transport is injected.

    The credential/reference gate is real; Qubit ships no X API transport, so
    without an injected ``transport`` the call returns a typed
    ``WEB_X_SEARCH_UNAVAILABLE`` (never a fabricated result). With an injected
    boundary the bounded search request is shaped, dispatched, and parsed into
    redacted results.
    """
    q = str(query or "").strip()
    if estop_engaged(cfg):
        return refuse_estop("x_search")
    if not q or len(q) > MAX_QUERY_CHARS:
        raise WebToolError(BAD_INPUT_CODE,
                           f"query must be 1..{MAX_QUERY_CHARS} chars")
    try:
        limit_i = max(1, min(int(limit or DEFAULT_SEARCH_LIMIT),
                             MAX_SEARCH_LIMIT))
    except (TypeError, ValueError):
        limit_i = DEFAULT_SEARCH_LIMIT
    provider = ""
    try:
        provider = str(cfg.get("web", "x_search", "provider",
                               default="") or "")
    except Exception:
        provider = ""
    if transport is not None:
        return _x_search_via_transport(cfg, provider, q, limit_i, transport)
    _audit("x_search", query=q, provider=provider,
           state=X_SEARCH_UNAVAILABLE_CODE)
    return {
        "state": UNAVAILABLE,
        "code": X_SEARCH_UNAVAILABLE_CODE,
        "query": q,
        "provider": provider[:40],
        "limit": limit_i,
        "results": [],
        "explanation": "Qubit Agent cannot do this: no X/Twitter search "
                       "transport adapter is shipped; configure one before "
                       "use. No request was attempted.",
    }


def _x_search_via_transport(cfg: Any, provider: str, query: str, limit: int,
                            transport: Any) -> Dict[str, Any]:
    """Shape, dispatch and parse a bounded X/Twitter search request."""
    request = {"provider": provider or "xai", "method": "GET",
               "url": "https://api.x.com/2/tweets/search/recent",
               "query": query, "limit": limit,
               "headers": {"Accept": "application/json"}}
    try:
        raw = transport(request)
    except Exception as exc:
        return {"state": "FAILED", "code": SEARCH_PROVIDER_CODE,
                "provider": provider[:40], "query": redact_text(query),
                "results": [], "count": 0,
                "detail": redact_text(str(exc))[:200]}
    if isinstance(raw, (bytes, bytearray)):
        try:
            import json as _json

            raw = _json.loads(bytes(raw).decode("utf-8", "replace"))
        except Exception:
            return {"state": "FAILED", "code": SEARCH_PROVIDER_CODE,
                    "provider": provider[:40], "query": redact_text(query),
                    "results": [], "count": 0,
                    "detail": "X search returned unparseable JSON"}
    items = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return {"state": "FAILED", "code": SEARCH_PROVIDER_CODE,
                "provider": provider[:40], "query": redact_text(query),
                "results": [], "count": 0,
                "detail": "X search transport returned no data array"}
    results: List[Dict[str, Any]] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        results.append({
            "id": redact_text(str(item.get("id", "")))[:64],
            "text": redact_text(str(item.get("text", "")))[:MAX_SEARCH_SNIPPET],
        })
    _audit("x_search", provider=provider or "xai", results=len(results))
    return {"state": OK, "ok": True, "code": "WEB_X_SEARCH_OK",
            "provider": provider[:40], "query": redact_text(query),
            "results": results, "count": len(results),
            "transport": "injected"}


TOOL_SPECS: Dict[str, Dict[str, Any]] = {
    "web.fetch": {
        "args": {"url": {"type": "string"}},
        "required": ["url"],
        "summary": "fetch a public web page as bounded, redacted text",
    },
    "web.search": {
        "args": {"query": {"type": "string"}},
        "required": ["query"],
        "summary": "search the web via the configured provider",
    },
    "web.x_search": {
        "args": {"query": {"type": "string"}},
        "required": ["query"],
        "summary": "search X/Twitter (typed UNAVAILABLE until an adapter "
                   "is configured)",
    },
    "web.read": {
        "args": {"url": {"type": "string"}},
        "required": ["url"],
        "summary": "fetch a public page and return bounded readable text",
    },
}


def run(cfg: Any, tool: str, arguments: Optional[Dict[str, Any]] = None,
        transport: Any = None,
        ) -> Dict[str, Any]:
    """Dispatch a planner-proposed ``web.*`` tool through this module.

    Wiring point for the shared agent path (orchestrator ``_agent_tool`` /
    local action registry); unknown tools fail closed. ``transport`` is
    forwarded to the injected search boundaries.
    """
    name = str(tool or "")
    if name.startswith("web."):
        name = name[len("web."):]
    args = dict(arguments or {})
    if name in ("fetch", "web_fetch"):
        return fetch(cfg, str(args.get("url") or ""))
    if name in ("search", "web_search"):
        return search(cfg, str(args.get("query") or ""), transport=transport)
    if name in ("x_search", "xsearch"):
        return x_search(cfg, str(args.get("query") or ""), transport=transport)
    if name in ("read", "readable", "web_readability"):
        fetched = fetch(cfg, str(args.get("url") or ""))
        if fetched.get("state") != OK:
            return fetched
        readable = readable_text(fetched.get("text", ""))
        readable["url"] = fetched.get("url", "")
        return readable
    raise WebToolError(BAD_INPUT_CODE, f"unknown web tool {tool!r}")
