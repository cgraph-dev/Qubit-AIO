"""Packet 8Q — OpenAI-compatible frontier HTTP client (stdlib only).

Covers direct providers, OpenCode Zen, and OpenRouter through one normalized
shape: POST {base_url}/chat/completions with bearer auth. No SDK, no new
dependency. Transport is injectable for hermetic tests; production uses
urllib with bounded timeouts. Cancellation stops local work; provider-side
limits are reported honestly.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional

from ..logging_setup import log_event
from .contracts import ProviderRequest, ProviderResult, UsageRecord

# Documented endpoint contracts (source: provider docs at implementation
# time; recorded in docs/UPSTREAM_REUSE.md R8Q; catalog snapshots carry
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
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers=headers, method="POST")
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
        try:
            body = json.loads(raw)
        except Exception:
            body = {"__http_error__": raw[:300]}
        return he.code or 0, body if isinstance(body, dict) else {}
    except Exception as exc:
        # Map stdlib errors to typed shapes via status 0 + error body.
        return 0, {"__transport_error__": str(exc)[:300]}


_REFUSAL_MARKERS = (
    "content_filter", "content-filter", "content_policy", "content-policy",
    "moderation", "refusal", "blocked_by_policy", "policy_violation",
    "safety_system", "responsible_ai",
)


def _looks_like_refusal(body: Dict[str, Any]) -> bool:
    """True when an error body signals a provider content-policy refusal."""
    try:
        hay = json.dumps(body)[:2000].lower()
    except Exception:
        return False
    return any(m in hay for m in _REFUSAL_MARKERS)


def _extract_text(body: Dict[str, Any]) -> tuple[str, int, int]:
    """Returns (text, prompt_tokens, completion_tokens) or raises ValueError."""
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("missing choices")
    msg = (choices[0].get("message") or {}) if isinstance(choices[0], dict) else {}
    text = str(msg.get("content") or "").strip()
    usage = body.get("usage") or {}
    pt = int(usage.get("prompt_tokens") or 0)
    ct = int(usage.get("completion_tokens") or 0)
    return text, pt, ct


class FrontierClient:
    def __init__(self, base_url: str, api_key: str, provider: str, model: str,
                 timeout_s: float = 20.0, max_retries: int = 1,
                 transport: Optional[Transport] = None,
                 cancel: Optional[Callable[[], bool]] = None,
                 max_response_chars: int = 4000) -> None:
        self.base_url = base_url.rstrip("/")
        self._key = api_key  # never logged
        self.provider = provider
        self.model = model
        self.timeout_s = timeout_s
        self.max_retries = max(0, max_retries)
        self.transport = transport or urllib_transport
        self.cancel = cancel
        self.max_response_chars = max_response_chars

    def complete(self, req: ProviderRequest, prompt_text: str,
                 est_cost: float = 0.0) -> ProviderResult:
        if self.cancel is not None and self.cancel():
            return ProviderResult(ok=False, code="CANCELLED",
                                  message="cancelled before send",
                                  provider=self.provider, model=self.model,
                                  identity=f"{self.provider}/{self.model}")
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt_text}],
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
        for attempt in range(self.max_retries + 1):
            if self.cancel is not None and self.cancel():
                return ProviderResult(ok=False, code="CANCELLED",
                                      message="cancelled during retry",
                                      provider=self.provider, model=self.model,
                                      identity=f"{self.provider}/{self.model}")
            status, body = self.transport(url, real_headers, payload,
                                          self.timeout_s)
            ms = int((time.time() - t0) * 1000)
            if status == 0:
                err = str(body.get("__transport_error__", ""))[:300]
                low = err.lower()
                if "timed out" in low or "timeout" in low:
                    last_code, last_msg = "TIMEOUT", err
                else:
                    last_code, last_msg = "PROVIDER_OUTAGE", err
                log_event("frontier.attempt", provider=self.provider,
                          attempt=attempt, code=last_code)
                continue  # retry (idempotent: no external tool action here)
            if _looks_like_refusal(body):
                return ProviderResult(ok=False, code="CONTENT_REFUSED",
                                      message=json.dumps(body)[:300],
                                      provider=self.provider, model=self.model,
                                      identity=f"{self.provider}/{self.model}")
            if status == 404:
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
                continue
            if status == 400 and "tool" in json.dumps(body)[:500].lower():
                return ProviderResult(ok=False, code="UNSUPPORTED_CAPABILITY",
                                      message=json.dumps(body)[:300],
                                      provider=self.provider, model=self.model,
                                      identity=f"{self.provider}/{self.model}")
            if status in (400, 422):
                return ProviderResult(ok=False, code="MALFORMED_RESPONSE",
                                      message=json.dumps(body)[:300],
                                      provider=self.provider, model=self.model,
                                      identity=f"{self.provider}/{self.model}")
            if status != 200:
                last_code, last_msg = "PROVIDER_OUTAGE", f"http {status}"
                continue
            try:
                text, pt, ct = _extract_text(body)
            except Exception as exc:
                return ProviderResult(ok=False, code="MALFORMED_RESPONSE",
                                      message=str(exc)[:200],
                                      provider=self.provider, model=self.model,
                                      identity=f"{self.provider}/{self.model}")
            if not text:
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
            return ProviderResult(ok=True, text=text,
                                  provider=self.provider, model=self.model,
                                  identity=f"{self.provider}/{self.model}",
                                  usage=usage)
        return ProviderResult(ok=False, code=last_code, message=last_msg,
                              provider=self.provider, model=self.model,
                              identity=f"{self.provider}/{self.model}")
