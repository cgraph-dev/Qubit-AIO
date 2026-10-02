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
        transport_retries = 0
        length_retries = 0
        for attempt in range(self.max_retries + 1 + self.max_length_retries):
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
