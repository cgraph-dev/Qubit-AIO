"""Packet 8Q — OpenAI-compatible frontier HTTP client (stdlib only).

Covers direct providers, OpenCode Zen, and OpenRouter through one normalized
shape: POST {base_url}/chat/completions with bearer auth. No SDK, no new
dependency. Transport is injectable for hermetic tests; production uses
urllib with bounded timeouts. Cancellation stops local work; provider-side
limits are reported honestly.
"""
from __future__ import annotations

import contextlib
import http.client
import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from ..logging_setup import log_event
from .contracts import ProviderRequest, ProviderResult, UsageRecord

# Documented endpoint contracts (source: provider docs at implementation
# time; recorded in docs/archive/2026-09-21/UPSTREAM_REUSE.md R8Q; catalog snapshots carry
# source+timestamp and stale data fails closed). Only endpoints whose
# contract is verified are listed here — every other provider MUST set an
# explicit operator base_url or the gateway returns NOT_CONFIGURED.
WELL_KNOWN_BASES = {
    "openrouter": "https://openrouter.ai/api/v1",
}

Transport = Callable[[str, Dict[str, str], Dict[str, Any], float],
                      tuple[int, Dict[str, Any]]]


def urllib_transport(url: str, headers: Dict[str, str], payload: Dict[str, Any],
                     timeout_s: float) -> tuple[int, Dict[str, Any]]:
    # Cloudflare-fronted relays (opencode-zen returns 403/error 1010) refuse
    # the stdlib urllib signature; send the same browser User-Agent a real
    # client would. Caller-supplied headers still win on conflict.
    merged = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/126.0 Safari/537.36",
              "Accept": "application/json"}
    merged.update(headers or {})
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers=merged, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            status = getattr(resp, "status", 200) or 200
            body = json.loads(resp.read().decode() or "{}")
            return status, body if isinstance(body, dict) else {}
    except urllib.error.HTTPError as he:
        # urllib raises for non-2xx: preserve the real status so the caller
        # maps 401/403->AUTH_INVALID, 429->RATE_LIMITED, 400/422->typed
        # mismatch instead of collapsing everything to PROVIDER_OUTAGE.
        try:
            raw = he.read().decode() or "{}"
        except Exception:
            raw = "{}"
        finally:
            # HTTPError holds the response body fp; closing it is what stops every
            # non-2xx provider response from leaking a socket to the GC.
            he.close()
        try:
            body = json.loads(raw)
        except Exception:
            body = {"__http_error__": raw[:300]}
        return he.code or 0, body if isinstance(body, dict) else {}
    except Exception as exc:
        # Map stdlib errors to typed shapes via status 0 + error body.
        return 0, {"__transport_error__": str(exc)[:300]}


class AbortableHTTPTransport:
    """Default frontier transport over a socket the client can abort.

    Behaves like :func:`urllib_transport` (same browser User-Agent, status
    mapping and typed transport errors) but keeps the live connection so
    :meth:`abort` can ``shutdown`` it from another thread while a response is
    still being read. ``shutdown`` — not just ``close`` — is what wakes a
    thread blocked in ``recv`` on Linux. An abort before the connection
    exists is remembered and the next call fails typed at once; repeated
    aborts are safe.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._conn: Any = None
        self._abort_pending = False

    def abort(self) -> bool:
        """Shut down the live frontier socket; False when none was in flight."""
        with self._lock:
            self._abort_pending = True
            conn = self._conn
        if conn is None:
            return False
        try:
            sock = getattr(conn, "sock", None)
            if sock is not None:
                sock.shutdown(socket.SHUT_RDWR)
        except Exception:  # noqa: BLE001 - abort is best-effort
            pass
        try:
            conn.close()
        except Exception:  # noqa: BLE001 - abort is best-effort
            pass
        return True

    def __call__(self, url: str, headers: Dict[str, str],
                 payload: Dict[str, Any], timeout_s: float
                 ) -> tuple[int, Dict[str, Any]]:
        merged = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/126.0 Safari/537.36",
                  "Accept": "application/json"}
        merged.update(headers or {})
        with self._lock:
            if self._abort_pending:
                self._abort_pending = False
                return 0, {"__transport_error__": "request aborted"}
        parts = urllib.parse.urlsplit(url)
        cls = (http.client.HTTPSConnection if (parts.scheme or "http") == "https"
               else http.client.HTTPConnection)
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        conn = cls(parts.hostname, parts.port, timeout=float(timeout_s))
        with self._lock:
            self._conn = conn
        try:
            conn.request("POST", path, body=json.dumps(payload).encode(),
                         headers=merged)
            resp = conn.getresponse()
            status = int(getattr(resp, "status", 200) or 200)
            raw = resp.read()
            if status == 200:
                try:
                    body = json.loads(raw.decode() or "{}")
                except Exception as exc:  # noqa: BLE001 - same as urllib
                    return 0, {"__transport_error__": str(exc)[:300]}
            else:
                try:
                    body = json.loads(raw.decode() or "{}")
                except Exception:
                    body = {"__http_error__":
                            raw.decode("utf-8", "replace")[:300]}
            return status, body if isinstance(body, dict) else {}
        except Exception as exc:  # noqa: BLE001 - typed transport error
            return 0, {"__transport_error__": str(exc)[:300]}
        finally:
            with self._lock:
                self._conn = None
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - cleanup only
                pass


class AbortableChatStream:
    """One ``stream: true`` chat-completions POST over a socket this owns.

    The streaming sibling of :class:`AbortableHTTPTransport`: ``http.client``
    keeps the live connection, so :meth:`abort` can ``shutdown`` it (``shutdown``,
    not just ``close``, is what wakes a thread blocked in ``recv`` on Linux)
    while a chunk read is mid-flight. Idempotent, and safe before the socket
    exists.

    ``open`` returns a context manager yielding the decoded ``data:`` payload
    lines as *parsed JSON objects* — the caller never sees raw bytes, so an
    SSE comment, a keep-alive blank line, or a ``[DONE]`` sentinel is skipped
    here rather than in three call sites.
    """

    def __init__(self, url: str, headers: Dict[str, str],
                 payload: Dict[str, Any], timeout_s: float) -> None:
        parts = urllib.parse.urlsplit(url)
        self._scheme = (parts.scheme or "http").lower()
        self._host = parts.hostname or "127.0.0.1"
        self._port = parts.port
        self._path = parts.path or "/"
        if parts.query:
            self._path = f"{self._path}?{parts.query}"
        self._body = json.dumps(payload).encode()
        self._headers = dict(headers or {})
        self._timeout_s = float(timeout_s)
        self._lock = threading.RLock()
        self._conn: Any = None
        self._aborted = False

    def abort(self) -> bool:
        """Shut down the live socket; False when none was in flight."""
        with self._lock:
            self._aborted = True
            conn = self._conn
        if conn is None:
            return False
        try:
            sock = getattr(conn, "sock", None)
            if sock is not None:
                sock.shutdown(socket.SHUT_RDWR)
        except Exception:  # noqa: BLE001 - abort is best-effort
            pass
        try:
            conn.close()
        except Exception:  # noqa: BLE001 - abort is best-effort
            pass
        return True

    @property
    def aborted(self) -> bool:
        with self._lock:
            return self._aborted

    @contextlib.contextmanager
    def _connection(self) -> Any:
        """Own one connection for the life of the ``with`` block."""
        cls = (http.client.HTTPSConnection if self._scheme == "https"
               else http.client.HTTPConnection)
        conn = cls(self._host, self._port, timeout=self._timeout_s)
        with self._lock:
            if self._aborted:
                raise _StreamAborted("request aborted before send")
            self._conn = conn
        resp = None
        try:
            conn.request("POST", self._path, body=self._body,
                         headers={**self._headers,
                                  "Accept": "text/event-stream"})
            resp = conn.getresponse()
            if int(getattr(resp, "status", 200) or 200) != 200:
                # Surface the error body so the caller can map it with the
                # same status→code table the blocking client uses.
                try:
                    raw = resp.read().decode("utf-8", "replace")[:2000]
                except Exception:  # noqa: BLE001 - body already gone
                    raw = ""
                raise _StreamHTTPError(int(resp.status or 0), raw)
            yield resp
        finally:
            with self._lock:
                self._conn = None
            if resp is not None:
                try:
                    resp.close()
                except Exception:  # noqa: BLE001 - cleanup only
                    pass
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - cleanup only
                pass

    def frames(self) -> Any:
        """Yield each SSE ``data:`` payload as a parsed JSON object.

        A plain generator (NOT a context manager) so callers iterate it in a
        ``for`` loop exactly as they read a file: ``contextlib`` would collapse
        a generator to its first value only.
        """
        with self._connection() as resp:
            for line in iter(resp.readline, b""):
                if self.aborted:
                    raise _StreamAborted("request aborted mid-stream")
                text = line.decode("utf-8", "replace").strip()
                if not text or text.startswith(":"):
                    continue  # blank keep-alive / SSE comment
                if not text.startswith("data:"):
                    # A provider that ignored ``stream: true`` answers 200 with
                    # ONE plain JSON body, not SSE. Surface it as a single frame
                    # so the caller can accept it whole (and report honestly
                    # that nothing streamed) instead of seeing an empty turn.
                    if text.startswith("{"):
                        try:
                            obj = json.loads(text)
                        except Exception:  # noqa: BLE001 - not JSON either
                            continue
                        if isinstance(obj, dict):
                            yield obj
                            return
                    continue  # event:/id:/retry: frames carry no content
                data = text[len("data:"):].strip()
                if data == "[DONE]":
                    return
                try:
                    obj = json.loads(data)
                except Exception:  # noqa: BLE001 - one bad frame is skipped
                    continue
                if isinstance(obj, dict):
                    yield obj


class _StreamAborted(Exception):
    """Raised when the stream was aborted mid-flight (cancel / e-stop)."""


class _StreamHTTPError(Exception):
    """Non-200 response to a streaming request; carries status + body."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"http {status}: {body[:300]}")
        self.status = status
        self.body = body


class _CancelWatch:
    """Watch a cancel hook for the duration of one blocking transport call.

    The watcher polls the hook and calls ``transport.abort()`` when it fires,
    which unblocks a response still being read. A transport without ``abort``
    (an injected fake / ``urllib_transport``) is left untouched: the existing
    pre-send and between-retry checks still apply.
    """

    def __init__(self, cancel: Optional[Callable[[], bool]],
                 transport: Any) -> None:
        self.cancel = cancel
        self.abort = getattr(transport, "abort", None)
        self._done = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def __enter__(self) -> "_CancelWatch":
        if self.cancel is None or not callable(self.abort):
            return self

        def _run() -> None:
            while not self._done.wait(0.01):
                try:
                    if self.cancel():
                        self.abort()
                except Exception:  # noqa: BLE001 - hook must not crash
                    return

        self._thread = threading.Thread(
            target=_run, daemon=True, name="frontier-cancel")
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> bool:
        self._done.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        return False


_REFUSAL_MARKERS = (
    "content_filter", "content-filter", "content_policy", "content-policy",
    "moderation", "refusal", "blocked_by_policy", "policy_violation",
    "safety_system", "responsible_ai",
)


def _http_error_result(provider: str, model: str, status: int,
                       body: Dict[str, Any]) -> "ProviderResult":
    """Typed refusal for a non-200 provider response.

    One table shared by :meth:`FrontierClient.complete` and
    :meth:`FrontierClient.complete_stream` so the streaming path can never map
    an error code differently from the blocking path. Returns a
    ``ProviderResult`` with ``ok=False``; the caller decides whether the code
    is retryable (``PROVIDER_OUTAGE`` / ``RATE_LIMITED``) before giving up.
    """
    ident = f"{provider}/{model}"
    base: Dict[str, Any] = {"ok": False, "provider": provider,
                            "model": model, "identity": ident}
    if _looks_like_refusal(body):
        return ProviderResult(code="CONTENT_REFUSED",
                              message=json.dumps(body)[:300], **base)
    if status == 404:
        if _looks_like_dead_model(body):
            return _dead_model_result(provider, model, body)
        return ProviderResult(code="NOT_CONFIGURED",
                              message="endpoint or model not found "
                                      f"(http 404 at {provider})", **base)
    if status in (401, 403):
        return ProviderResult(code="AUTH_INVALID", message=f"http {status}",
                              **base)
    if status == 400 and "tool" in json.dumps(body)[:500].lower():
        return ProviderResult(code="UNSUPPORTED_CAPABILITY",
                              message=json.dumps(body)[:300], **base)
    if status in (400, 422):
        if _looks_like_dead_model(body):
            return _dead_model_result(provider, model, body)
        return ProviderResult(code="MALFORMED_RESPONSE",
                              message=json.dumps(body)[:300], **base)
    if status == 429:
        return ProviderResult(code="RATE_LIMITED", message="http 429", **base)
    return ProviderResult(code="PROVIDER_OUTAGE", message=f"http {status}",
                          **base)


def _looks_like_refusal(body: Dict[str, Any]) -> bool:
    """True when an error body signals a provider content-policy refusal."""
    try:
        hay = json.dumps(body)[:2000].lower()
    except Exception:
        return False
    return any(m in hay for m in _REFUSAL_MARKERS)


# Turn-time dead-model markers (G1 residual): a model the registry lists but
# upstream rejects at turn time (e.g. muse-spark resolves statically, then
# ModelProtocolUnsupported on use). These must surface as a typed
# MODEL_UNAVAILABLE_TURN, never as generic MALFORMED_RESPONSE. Matching is
# separator-insensitive (camelCase / snake_case / kebab-case all compact to
# the same run of letters) and limited to the three dead-model classes —
# protocol-unsupported, unavailable, not-found — so ordinary 400s stay
# MALFORMED_RESPONSE.
_DEAD_MODEL_COMPACT_MARKERS = (
    "modelprotocolunsupported",
    "modelunavailable",
    "modelnotfound",
    "nosuchmodel",
    "modeldoesnotexist",
    "modelisunavailable",
)

_DEAD_MODEL_LOOSE_MARKERS = (
    "unknown model",
    "model not found",
    "no such model",
    "model does not exist",
    "unsupported model",
    "model is unavailable",
)


def _looks_like_dead_model(body: Dict[str, Any]) -> bool:
    """True when an error body names a dead/retired/unknown model."""
    import re as _re

    try:
        hay = json.dumps(body)[:2000].lower()
    except Exception:
        return False
    if any(m in hay for m in _DEAD_MODEL_LOOSE_MARKERS):
        return True
    compact = _re.sub(r"[^a-z]", "", hay)
    return any(m in compact for m in _DEAD_MODEL_COMPACT_MARKERS)


def _dead_model_result(provider: str, model: str,
                       body: Dict[str, Any]) -> "ProviderResult":
    """Typed turn-time refusal for a dead model (slot never auto-moved).

    Names the dead provider/model identity; the caller (gateway) appends the
    still-working slot. No fallback, no retry: failover policy is an owner
    call, so this is a typed error only.
    """
    return ProviderResult(ok=False, code="MODEL_UNAVAILABLE_TURN",
                          message=(f"model {provider}/{model} is unavailable "
                                   f"at turn time ({json.dumps(body)[:200]}); "
                                   "the configured slot is unchanged — pick "
                                   "a working model explicitly"),
                          provider=provider, model=model,
                          identity=f"{provider}/{model}")


def _extract_text(body: Dict[str, Any]) -> tuple[str, int, int, str, bool]:
    """Returns (text, prompt_tokens, completion_tokens, finish_reason,
    has_reasoning) or raises ValueError.

    ``finish_reason`` and whether the provider emitted chain-of-thought
    (``reasoning_content``/``reasoning``) are needed to tell a genuinely
    malformed empty completion apart from a reasoning model that spent the
    whole output budget on its reasoning and stopped (``finish_reason ==
    "length"``) before writing any answer.
    """
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("missing choices")
    choice = choices[0] if isinstance(choices[0], dict) else {}
    msg = choice.get("message") or {}
    text = str(msg.get("content") or "").strip()
    reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
    finish_reason = str(choice.get("finish_reason") or "")
    usage = body.get("usage") or {}
    pt = int(usage.get("prompt_tokens") or 0)
    ct = int(usage.get("completion_tokens") or 0)
    return text, pt, ct, finish_reason, bool(str(reasoning).strip())


def _bumped_max_tokens(current: Any, *, ceiling: int = 8192) -> int:
    """Next output budget for a length-truncated completion.

    The vendored agent retries a ``finish_reason="length"`` turn with a larger
    ``max_tokens`` (``agent.turn_iteration_prep`` length-continue); this is the
    same bounded escalation. Doubling from a floor of 1024 keeps the repair
    modest and finite; ``ceiling`` prevents an unbounded spend.
    """
    try:
        cur = int(current or 0)
    except (TypeError, ValueError):
        cur = 0
    return min(max(cur * 2, 1024), ceiling)


class FrontierClient:
    def __init__(self, base_url: str, api_key: str, provider: str, model: str,
                 timeout_s: float = 20.0, max_retries: int = 1,
                 transport: Optional[Transport] = None,
                 cancel: Optional[Callable[[], bool]] = None,
                 max_response_chars: int = 4000,
                 max_length_retries: int = 2) -> None:
        self.base_url = base_url.rstrip("/")
        self._key = api_key  # never logged
        self.provider = provider
        self.model = model
        self.timeout_s = timeout_s
        self.max_retries = max(0, max_retries)
        self.transport = transport or urllib_transport
        self.cancel = cancel
        self.max_response_chars = max_response_chars
        # Bounded output-budget repairs for a length-truncated empty completion
        # (reasoning models count reasoning tokens against ``max_tokens``).
        self.max_length_retries = max(0, max_length_retries)

    def complete(self, req: ProviderRequest, prompt_text: str,
                 est_cost: float = 0.0,
                 messages: Optional[List[Dict[str, Any]]] = None
                 ) -> ProviderResult:
        if self.cancel is not None and self.cancel():
            return ProviderResult(ok=False, code="CANCELLED",
                                  message="cancelled before send",
                                  provider=self.provider, model=self.model,
                                  identity=f"{self.provider}/{self.model}")
        # ``messages`` (when supplied) is a pre-built, already-translated
        # conversation — the multimodal vision path hands over the OpenAI
        # content-block shape here. It is passed through verbatim; the default
        # single-text-message body is used otherwise. NEVER rebuild the caller's
        # messages: an image carried in a block list would be dropped.
        convo = messages if messages else [{"role": "user",
                                            "content": prompt_text}]
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": convo,
            "max_tokens": req.max_tokens,
            "stream": False,
        }
        if req.structured_schema is not None:
            payload["response_format"] = {"type": "json_object"}
        if req.tools:
            payload["tools"] = req.tools
        # NOTE: the Authorization header below carries the live key on the
        # wire only. It is never logged, never stored, and never included in
        # results, usage records, or public dicts.
        real_headers = {"Content-Type": "application/json",
                        "Authorization": f"Bearer {self._key}"}
        url = f"{self.base_url}/chat/completions"
        last_code, last_msg = "PROVIDER_OUTAGE", "no attempts"
        t0 = time.time()
        transport_retries = 0
        length_retries = 0
        for attempt in range(self.max_retries + 1 + self.max_length_retries):
            if self.cancel is not None and self.cancel():
                return ProviderResult(ok=False, code="CANCELLED",
                                      message="cancelled during retry",
                                      provider=self.provider, model=self.model,
                                      identity=f"{self.provider}/{self.model}")
            # Abort the live socket when the turn's cancel fires mid-response;
            # a transport without ``abort`` keeps the unchanged checks below.
            with _CancelWatch(self.cancel, self.transport):
                status, body = self.transport(url, real_headers, payload,
                                              self.timeout_s)
            ms = int((time.time() - t0) * 1000)
            if self.cancel is not None and self.cancel():
                return ProviderResult(ok=False, code="CANCELLED",
                                      message="cancelled during provider call",
                                      provider=self.provider, model=self.model,
                                      identity=f"{self.provider}/{self.model}")
            if status == 0:
                err = str(body.get("__transport_error__", ""))[:300]
                low = err.lower()
                if "timed out" in low or "timeout" in low:
                    last_code, last_msg = "TIMEOUT", err
                else:
                    last_code, last_msg = "PROVIDER_OUTAGE", err
                log_event("frontier.attempt", provider=self.provider,
                          attempt=attempt, code=last_code)
                transport_retries += 1
                if transport_retries > self.max_retries:
                    break
                continue  # retry (idempotent: no external tool action here)
            if _looks_like_refusal(body):
                return ProviderResult(ok=False, code="CONTENT_REFUSED",
                                      message=json.dumps(body)[:300],
                                      provider=self.provider, model=self.model,
                                      identity=f"{self.provider}/{self.model}")
            if status == 404:
                if _looks_like_dead_model(body):
                    return _dead_model_result(self.provider, self.model,
                                              body)
                return ProviderResult(ok=False, code="NOT_CONFIGURED",
                                      message="endpoint or model not found "
                                              f"(http 404 at {self.provider})",
                                      provider=self.provider, model=self.model,
                                      identity=f"{self.provider}/{self.model}")
            if status == 401 or status == 403:
                return ProviderResult(ok=False, code="AUTH_INVALID",
                                      message=f"http {status}",
                                      provider=self.provider, model=self.model,
                                      identity=f"{self.provider}/{self.model}")
            if status == 429:
                last_code, last_msg = "RATE_LIMITED", f"http 429"
                transport_retries += 1
                if transport_retries > self.max_retries:
                    break
                continue
            if status == 400 and "tool" in json.dumps(body)[:500].lower():
                return ProviderResult(ok=False, code="UNSUPPORTED_CAPABILITY",
                                      message=json.dumps(body)[:300],
                                      provider=self.provider, model=self.model,
                                      identity=f"{self.provider}/{self.model}")
            if status in (400, 422):
                if _looks_like_dead_model(body):
                    return _dead_model_result(self.provider, self.model,
                                              body)
                return ProviderResult(ok=False, code="MALFORMED_RESPONSE",
                                      message=json.dumps(body)[:300],
                                      provider=self.provider, model=self.model,
                                      identity=f"{self.provider}/{self.model}")
            if status != 200:
                last_code, last_msg = "PROVIDER_OUTAGE", f"http {status}"
                transport_retries += 1
                if transport_retries > self.max_retries:
                    break
                continue
            try:
                text, pt, ct, finish_reason, has_reasoning = _extract_text(body)
            except Exception as exc:
                return ProviderResult(ok=False, code="MALFORMED_RESPONSE",
                                      message=str(exc)[:200],
                                      provider=self.provider, model=self.model,
                                      identity=f"{self.provider}/{self.model}")
            if not text:
                # A reasoning model can spend the entire output budget on its
                # chain-of-thought and stop at the cap with no visible answer
                # (finish_reason == "length"). That is output truncation, not a
                # malformed response: repair it once with a larger budget
                # before surfacing a truthful, actionable failure.
                if finish_reason == "length" \
                        and length_retries < self.max_length_retries:
                    bumped = _bumped_max_tokens(payload.get("max_tokens"))
                    if bumped > int(payload.get("max_tokens") or 0):
                        payload["max_tokens"] = bumped
                        length_retries += 1
                        log_event("frontier.length_retry",
                                  provider=self.provider, max_tokens=bumped,
                                  reasoned=has_reasoning,
                                  completion_tokens=ct)
                        continue
                if finish_reason == "length":
                    return ProviderResult(
                        ok=False, code="OUTPUT_TRUNCATED",
                        message=(
                            f"provider stopped at max_tokens="
                            f"{int(payload.get('max_tokens') or 0)} with no "
                            f"visible content after {ct} completion tokens "
                            f"({'reasoning consumed the output budget'
                              if has_reasoning else 'output cap reached'}); "
                            "lower the reasoning level or raise the output "
                            "budget"),
                        provider=self.provider, model=self.model,
                        identity=f"{self.provider}/{self.model}")
                return ProviderResult(ok=False, code="MALFORMED_RESPONSE",
                                      message="empty content",
                                      provider=self.provider, model=self.model,
                                      identity=f"{self.provider}/{self.model}")
            if len(text) > self.max_response_chars:
                return ProviderResult(ok=False, code="RESPONSE_TOO_LARGE",
                                      message=f"{len(text)} chars",
                                      provider=self.provider, model=self.model,
                                      identity=f"{self.provider}/{self.model}")
            if req.structured_schema is not None:
                try:
                    json.loads(text)
                except Exception:
                    return ProviderResult(ok=False, code="MALFORMED_RESPONSE",
                                          message="schema output not JSON",
                                          provider=self.provider,
                                          model=self.model,
                                          identity=f"{self.provider}/{self.model}")
            usage = UsageRecord(provider=self.provider, model=self.model,
                                identity=f"{self.provider}/{self.model}",
                                prompt_tokens=pt, completion_tokens=ct,
                                latency_ms=ms, cost_usd_est=est_cost)
            log_event("frontier.ok", provider=self.provider, model=self.model,
                      ms=ms)
            details: Dict[str, Any] = {}
            if length_retries:
                # Truthful audit trail: the answer needed a repaired (larger)
                # output budget because reasoning exhausted the requested one.
                details["output_budget_repairs"] = length_retries
                details["effective_max_tokens"] = int(
                    payload.get("max_tokens") or 0)
            return ProviderResult(ok=True, text=text,
                                  provider=self.provider, model=self.model,
                                  identity=f"{self.provider}/{self.model}",
                                  usage=usage, details=details)
        return ProviderResult(ok=False, code=last_code, message=last_msg,
                              provider=self.provider, model=self.model,
                              identity=f"{self.provider}/{self.model}")

    def complete_stream(self, req: ProviderRequest, prompt_text: str,
                        est_cost: float = 0.0,
                        messages: Optional[List[Dict[str, Any]]] = None,
                        on_delta: Optional[Callable[[str], None]] = None,
                        extra_headers: Optional[Dict[str, str]] = None
                        ) -> ProviderResult:
        """Stream one chat completion, calling ``on_delta`` per real chunk.

        ``on_delta(text)`` receives ONLY the incremental content of one SSE
        chunk, as the provider sent it — never a fragment split out of a
        finished reply. The returned :class:`ProviderResult` carries the same
        fields as :meth:`complete` (``text`` = the concatenation of every
        chunk, ``usage``, ``details``) so a caller can persist or receipt it
        identically.

        ``details["streaming"]`` is the honest verdict: ``True`` only when
        chunks really arrived over SSE. A provider/transport that ignores
        ``stream: true`` and answers with a single JSON body is accepted as
        one delta and marked ``streaming: False`` — the caller then says so
        rather than claiming token streaming.

        There is NO retry once a delta was emitted: a retry would duplicate
        text the client already displayed. Retries apply only to failures
        before the first chunk (the blocking path's exact table).
        """
        if self.cancel is not None and self.cancel():
            return ProviderResult(ok=False, code="CANCELLED",
                                  message="cancelled before send",
                                  provider=self.provider, model=self.model,
                                  identity=f"{self.provider}/{self.model}")
        convo = messages if messages else [{"role": "user",
                                            "content": prompt_text}]
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": convo,
            "max_tokens": req.max_tokens,
            "stream": True,
            # Ask for usage on the final chunk; a provider that does not
            # support it ignores the field.
            "stream_options": {"include_usage": True},
        }
        if req.structured_schema is not None:
            payload["response_format"] = {"type": "json_object"}
        if req.tools:
            payload["tools"] = req.tools
        real_headers = {"Content-Type": "application/json",
                        "Authorization": f"Bearer {self._key}"}
        # Relay-required affinity headers (OpenCode) arrive from the gateway so
        # a streamed turn sends exactly what the blocking transport sends.
        for _k, _v in (extra_headers or {}).items():
            real_headers.setdefault(str(_k), str(_v))
        url = f"{self.base_url}/chat/completions"
        last_code, last_msg = "PROVIDER_OUTAGE", "no attempts"
        t0 = time.time()
        transport_retries = 0
        for attempt in range(self.max_retries + 1):
            if self.cancel is not None and self.cancel():
                return ProviderResult(ok=False, code="CANCELLED",
                                      message="cancelled during retry",
                                      provider=self.provider, model=self.model,
                                      identity=f"{self.provider}/{self.model}")
            stream = AbortableChatStream(url, real_headers, payload,
                                         self.timeout_s)
            acc: List[str] = []
            pt = ct = 0
            finish_reason = ""
            sent = 0
            first_ms: Optional[int] = None
            single_body: Dict[str, Any] = {}
            try:
                with _CancelWatch(self.cancel, stream):
                    for frame in stream.frames():
                        if self.cancel is not None and self.cancel():
                            raise _StreamAborted("cancelled mid-stream")
                        if isinstance(frame.get("usage"), dict):
                            usage = frame["usage"]
                            pt = int(usage.get("prompt_tokens") or pt)
                            ct = int(usage.get("completion_tokens") or ct)
                        choices = frame.get("choices")
                        if not isinstance(choices, list) or not choices:
                            # Not a chunk envelope. A provider that ignored
                            # stream:true answers with one whole JSON body —
                            # remember it and accept it below, NEVER split it
                            # into fake token deltas.
                            if "choices" not in frame and "usage" not in frame:
                                single_body = frame
                                break
                            continue
                        choice = choices[0] if isinstance(choices[0], dict) else {}
                        finish_reason = (str(choice.get("finish_reason") or "")
                                         or finish_reason)
                        if "delta" not in choice:
                            # A whole (non-streamed) completion body: accept it
                            # as ONE delta below and report streaming False —
                            # never read ``message`` as if it were a chunk.
                            single_body = frame
                            break
                        delta = choice.get("delta") or {}
                        chunk = (delta.get("content")
                                 if isinstance(delta, dict) else None)
                        if not chunk:
                            continue
                        if sent == 0:
                            first_ms = int((time.time() - t0) * 1000)
                        acc.append(str(chunk))
                        sent += 1
                        if on_delta is not None:
                            on_delta(str(chunk))
            except _StreamHTTPError as exc:
                try:
                    body = json.loads(exc.body or "{}")
                except Exception:  # noqa: BLE001 - keep the raw text
                    body = {"__http_error__": exc.body[:300]}
                res = _http_error_result(self.provider, self.model,
                                         exc.status,
                                         body if isinstance(body, dict) else {})
                if res.code in ("RATE_LIMITED", "PROVIDER_OUTAGE") \
                        and sent == 0:
                    last_code, last_msg = res.code, res.message
                    transport_retries += 1
                    if transport_retries > self.max_retries:
                        break
                    continue
                return res
            except _StreamAborted:
                # A stopped turn returns what really arrived; the caller
                # decides (the stream envelope reports ``cancelled``).
                return ProviderResult(ok=False, code="CANCELLED",
                                      message="cancelled during provider call",
                                      provider=self.provider, model=self.model,
                                      identity=f"{self.provider}/{self.model}",
                                      details={"streaming": sent > 0,
                                               "chunks": sent,
                                               "partial_text": "".join(acc)})
            except Exception as exc:  # noqa: BLE001 - typed transport error
                err = str(exc)[:300]
                low = err.lower()
                last_code = ("TIMEOUT" if "timed out" in low or "timeout" in low
                             else "PROVIDER_OUTAGE")
                last_msg = err
                if sent:
                    # Text already reached the client: never retry (it would
                    # duplicate the visible answer), surface it typed instead.
                    return ProviderResult(ok=False, code=last_code,
                                          message=err,
                                          provider=self.provider, model=self.model,
                                          identity=f"{self.provider}/{self.model}",
                                          details={"streaming": True,
                                                   "chunks": sent,
                                                   "partial_text": "".join(acc)})
                log_event("frontier.attempt", provider=self.provider,
                          attempt=attempt, code=last_code)
                transport_retries += 1
                if transport_retries > self.max_retries:
                    break
                continue
            ms = int((time.time() - t0) * 1000)
            text = "".join(acc).strip()
            if not text and single_body:
                # Non-streaming body from a provider that ignored stream:true.
                try:
                    whole, pt, ct, finish_reason, _ = _extract_text(single_body)
                except Exception as exc:  # noqa: BLE001 - typed refusal
                    return ProviderResult(ok=False, code="MALFORMED_RESPONSE",
                                          message=str(exc)[:200],
                                          provider=self.provider,
                                          model=self.model,
                                          identity=f"{self.provider}/{self.model}")
                if whole:
                    if on_delta is not None:
                        on_delta(whole)
                    return self._streamed_result(whole, pt, ct, ms, est_cost,
                                                 streamed=False)
            if not text:
                if finish_reason == "length":
                    return ProviderResult(
                        ok=False, code="OUTPUT_TRUNCATED",
                        message=(
                            f"provider stopped at max_tokens="
                            f"{int(payload.get('max_tokens') or 0)} with no "
                            f"visible content ({ct} completion tokens); lower "
                            "the reasoning level or raise the output budget"),
                        provider=self.provider, model=self.model,
                        identity=f"{self.provider}/{self.model}")
                return ProviderResult(ok=False, code="MALFORMED_RESPONSE",
                                      message="empty content",
                                      provider=self.provider, model=self.model,
                                      identity=f"{self.provider}/{self.model}")
            if len(text) > self.max_response_chars:
                return ProviderResult(ok=False, code="RESPONSE_TOO_LARGE",
                                      message=f"{len(text)} chars",
                                      provider=self.provider, model=self.model,
                                      identity=f"{self.provider}/{self.model}")
            if req.structured_schema is not None:
                try:
                    json.loads(text)
                except Exception:  # noqa: BLE001 - typed refusal
                    return ProviderResult(ok=False, code="MALFORMED_RESPONSE",
                                          message="schema output not JSON",
                                          provider=self.provider,
                                          model=self.model,
                                          identity=f"{self.provider}/{self.model}")
            return self._streamed_result(text, pt, ct, ms, est_cost,
                                         streamed=sent > 0, chunks=sent,
                                         first_delta_ms=first_ms)
        return ProviderResult(ok=False, code=last_code, message=last_msg,
                              provider=self.provider, model=self.model,
                              identity=f"{self.provider}/{self.model}")

    def _streamed_result(self, text: str, pt: int, ct: int, ms: int,
                         est_cost: float, *, streamed: bool,
                         chunks: int = 0,
                         first_delta_ms: Optional[int] = None) -> ProviderResult:
        """Build the success result shared by the streaming and whole-body paths."""
        # Providers that omit per-chunk usage: charge the tokens we can count
        # rather than reporting a zero-token turn.
        if ct <= 0 and streamed:
            ct = max(1, len(text) // 4) if text else 0
        usage = UsageRecord(provider=self.provider, model=self.model,
                            identity=f"{self.provider}/{self.model}",
                            prompt_tokens=pt, completion_tokens=ct,
                            # Declared latency is the measured time to the FIRST
                            # chunk (what the operator waited before seeing
                            # text), never a guess derived from the total.
                            latency_ms=int(first_delta_ms or ms),
                            cost_usd_est=est_cost)
        log_event("frontier.ok", provider=self.provider, model=self.model,
                  ms=ms, streamed=streamed, chunks=chunks)
        return ProviderResult(ok=True, text=text, provider=self.provider,
                              model=self.model,
                              identity=f"{self.provider}/{self.model}",
                              usage=usage,
                              details={"streaming": bool(streamed),
                                       "chunks": int(chunks)})
