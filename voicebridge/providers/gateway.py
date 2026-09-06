"""Packet 8Q — unified gateway: local default, opt-in frontier/auto.

One normalized contract for every route. Local performs no network request.
Frontier requires explicit enable + allowlist + catalog + credential + budgets.
Auto never escalates on untrusted text; unknown/stale/over-budget/unsupported
fail closed or use a clearly typed local fallback.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

from ..logging_setup import log_event
from ..redaction import redact_text
from . import frontier as _frontier
from .catalog import ModelEntry, ProviderCatalog
from .contracts import ProviderRequest, ProviderResult, UsageRecord
from .credentials import (looks_like_browser_secret,
                           looks_like_opencode_private_db, redact_ref,
                           resolve_key)
from .egress import classify_and_sanitize
from .policy import FrontierPolicy, disclosure_block, get_governor


def _estimate_cost(entry: Optional[ModelEntry], pcfg: dict,
                   prompt_chars: int, max_tokens: int) -> float:
    """Cost estimate honoring catalog prices, else operator price overrides.

    Operator ``price_in_per_1k``/``price_out_per_1k`` in the provider config
    keep spend budgets enforceable for allowlist-catalog runs whose entries
    carry no pricing. Unknown prices estimate 0.0 and are disclosed as such.
    """
    pin = entry.price_in_per_1k if entry is not None else 0.0
    pout = entry.price_out_per_1k if entry is not None else 0.0
    try:
        if not pin:
            pin = float((pcfg or {}).get("price_in_per_1k", 0.0) or 0.0)
        if not pout:
            pout = float((pcfg or {}).get("price_out_per_1k", 0.0) or 0.0)
    except (TypeError, ValueError):
        pin, pout = 0.0, 0.0
    if not pin and not pout:
        return 0.0
    pt_est = max(1, prompt_chars // 4)
    return (pt_est / 1000.0) * pin + (max_tokens / 1000.0) * pout


def _local_complete(prompt: str, cfg: Any, max_tokens: int) -> ProviderResult:
    """Local path: existing runner contract, no network beyond Ollama."""
    try:
        from ..local_llm import chat_local
        from ..runner_contract import resolve
        rc = resolve(cfg)
        t0 = time.time()
        text = chat_local(prompt[:2000], "", model=rc.alias, url=rc.url,
                          timeout_s=8.0, max_tokens=max_tokens)
        ms = int((time.time() - t0) * 1000)
        if not text:
            return ProviderResult(ok=False, code="PROVIDER_OUTAGE",
                                  message="local model unavailable",
                                  provider="local", model=rc.alias,
                                  identity=f"local/{rc.alias}")
        usage = UsageRecord(provider="local", model=rc.alias,
                            identity=f"local/{rc.alias}", latency_ms=ms)
        return ProviderResult(ok=True, text=text, provider="local",
                              model=rc.alias, identity=f"local/{rc.alias}",
                              usage=usage)
    except Exception as exc:
        return ProviderResult(ok=False, code="PROVIDER_OUTAGE",
                              message=redact_text(str(exc))[:200],
                              provider="local", model="qubit-brain4",
                              identity="local/qubit-brain4")


def run(req: ProviderRequest, cfg: Any,
        catalog: Optional[ProviderCatalog] = None,
        transport: Optional[_frontier.Transport] = None,
        cancel: Optional[Callable[[], bool]] = None,
        allow_private: bool = False) -> ProviderResult:
    """Run one request through the 8Q policy. Never raises; typed results."""
    policy = FrontierPolicy.from_config(cfg)
    requested = (req.route or policy.route or "local").lower()
    if requested not in ("local", "frontier", "auto"):
        requested = "local"

    provider = (req.provider or "").strip()
    model = (req.model or "").strip()
    if not provider or not model:
        if policy.allowlist:
            first = policy.allowlist[0]
            if "/" in first:
                provider, model = first.split("/", 1)
    ident = f"{provider}/{model}" if provider and model else "frontier/unknown"

    def _fallback(reason: str, msg: str) -> ProviderResult:
        fb = _local_complete(req.prompt, cfg,
                             min(req.max_tokens, policy.max_tokens))
        if fb.ok and fb.usage is not None:
            fb.usage = UsageRecord(provider=fb.usage.provider,
                                   model=fb.usage.model,
                                   identity=fb.usage.identity,
                                   latency_ms=fb.usage.latency_ms,
                                   fallback=True, fallback_reason=reason)
            fb.identity = fb.usage.identity
            fb.code = ""
        else:
            fb.code = reason
            fb.message = msg or fb.message
        return fb

    def _deny(code: str, msg: str) -> ProviderResult:
        # Explicit frontier fails closed (no fake cloud text); auto uses a
        # clearly typed local fallback.
        if requested == "auto":
            return _fallback(code, msg)
        log_event("frontier.denied", code=code)
        return ProviderResult(ok=False, code=code, message=msg,
                              provider=provider, model=model, identity=ident)

    # Local is always available and performs no frontier network request.
    if requested == "local":
        return _local_complete(req.prompt, cfg,
                               min(req.max_tokens, policy.max_tokens))
    if not policy.enabled:
        if requested == "auto":
            return _fallback("CLOUD_DISABLED",
                             "cloud routing disabled by default")
        log_event("frontier.denied", code="CLOUD_DISABLED")
        return ProviderResult(ok=False, code="CLOUD_DISABLED",
                              message="cloud routing disabled by default",
                              provider=provider, model=model, identity=ident)

    if not provider or not model:
        return _deny("NOT_CONFIGURED",
                     "frontier route has no provider/model target")

    if not policy.is_allowed(provider, model):
        return _deny("UNKNOWN_MODEL", "model not in owner allowlist")

    # Catalog validation (stale/unknown fail closed).
    entry = catalog.lookup(provider, model) if catalog else None
    if catalog is None:
        entry = None
    else:
        ok, code = catalog.validate(provider, model)
        if not ok:
            return _deny(code, "catalog validation failed")

    # Capability mismatch fails closed before spend.
    if entry is not None:
        if req.require_tools and not entry.supports_tools:
            return ProviderResult(ok=False, code="UNSUPPORTED_CAPABILITY",
                                  message="model lacks tool calling",
                                  provider=provider, model=model,
                                  identity=f"{provider}/{model}")
        if req.structured_schema is not None and not entry.supports_structured:
            return ProviderResult(ok=False, code="UNSUPPORTED_CAPABILITY",
                                  message="model lacks structured output",
                                  provider=provider, model=model,
                                  identity=f"{provider}/{model}")
        if req.require_streaming and not entry.supports_streaming:
            return ProviderResult(ok=False, code="UNSUPPORTED_CAPABILITY",
                                  message="model lacks streaming",
                                  provider=provider, model=model,
                                  identity=f"{provider}/{model}")
        if req.context_chars > 0 and req.context_chars > entry.context_tokens * 4:
            return ProviderResult(ok=False, code="UNSUPPORTED_CAPABILITY",
                                  message="context exceeds model budget",
                                  provider=provider, model=model,
                                  identity=f"{provider}/{model}")

    # Egress: minimum-necessary, redacted, bounded.
    eg = classify_and_sanitize(req.prompt, allow_private=allow_private,
                               max_chars=policy.max_context_chars)
    if not eg.allowed:
        return ProviderResult(ok=False, code="EGRESS_DENIED",
                              message="egress policy denied",
                              provider=provider, model=model,
                              identity=f"{provider}/{model}")

    # Credentials: env/file reference only; never OpenCode private DB.
    pcfg = policy.provider_cfg(provider)
    ref = str(pcfg.get("key_ref", "") or "")
    if looks_like_opencode_private_db(ref) or looks_like_opencode_private_db(
            str(pcfg.get("base_url", ""))):
        return ProviderResult(ok=False, code="AUTH_MISSING",
                              message="undocumented OpenCode credential use refused",
                              provider=provider, model=model,
                              identity=f"{provider}/{model}")
    ref_path = ref[len("file:"):] if ref.startswith("file:") else ""
    if ref_path and looks_like_browser_secret(ref_path):
        return ProviderResult(ok=False, code="AUTH_MISSING",
                              message="browser cookie/session stores are never "
                                      "valid credential references",
                              provider=provider, model=model,
                              identity=f"{provider}/{model}")
    key, kcode = resolve_key(provider, ref)
    if kcode:
        return ProviderResult(ok=False, code="AUTH_MISSING",
                              message=f"no credential ({redact_ref(ref)})",
                              provider=provider, model=model,
                              identity=f"{provider}/{model}")

    base_url = str(pcfg.get("base_url", "") or
                   _frontier.WELL_KNOWN_BASES.get(provider, "") or "")
    if not base_url:
        return ProviderResult(ok=False, code="NOT_CONFIGURED",
                              message="no base_url for provider",
                              provider=provider, model=model,
                              identity=f"{provider}/{model}")

    max_tokens = min(int(req.max_tokens or policy.max_tokens), policy.max_tokens)
    est = _estimate_cost(entry, pcfg, len(eg.sanitized), max_tokens)

    gov = get_governor()
    if req.idempotency_key and gov.replay_seen(req.idempotency_key):
        return ProviderResult(ok=False, code="RATE_LIMITED",
                              message="duplicate idempotency key: retry suppressed",
                              provider=provider, model=model,
                              identity=f"{provider}/{model}")
    ok, code = gov.check(policy, provider, est_cost=est)
    if not ok:
        if code in ("OVER_BUDGET", "CIRCUIT_OPEN", "RATE_LIMITED"):
            # Typed failure; do NOT silently bill or duplicate work.
            return ProviderResult(ok=False, code=code,
                                  message=disclosure_block(policy, provider, model, est),
                                  provider=provider, model=model,
                                  identity=f"{provider}/{model}")
        return ProviderResult(ok=False, code=code, message="governor refused",
                              provider=provider, model=model,
                              identity=f"{provider}/{model}")

    client = _frontier.FrontierClient(base_url, key, provider, model,
                                     timeout_s=min(req.timeout_s, policy.timeout_s),
                                     max_retries=policy.max_retries,
                                     transport=transport, cancel=cancel,
                                     max_response_chars=policy.max_response_chars)
    gov.acquire()
    try:
        res = client.complete(req, eg.sanitized, est_cost=est)
    except Exception as exc:
        gov.release(provider, False)
        return ProviderResult(ok=False, code="PROVIDER_OUTAGE",
                              message=redact_text(str(exc))[:200],
                              provider=provider, model=model,
                              identity=f"{provider}/{model}")
    gov.release(provider, res.ok,
                cost=((res.usage.cost_usd_est if res.usage else 0.0) or est)
                if res.ok else 0.0)
    if res.ok and res.usage is not None:
        # Spend already recorded by gov.release above; attach disclosure.
        res.details["disclosure"] = disclosure_block(policy, provider, model,
                                                    res.usage.cost_usd_est or est)
        res.details["privacy_tier"] = policy.privacy_tier
        res.details["egress_warning"] = eg.warning
        log_event("frontier.used", provider=provider, model=model,
                  cost_usd=res.usage.cost_usd_est or est)
        return res
    if not res.ok:
        # Auto route falls back to local with truthful identity; explicit
        # frontier returns the typed failure (no fake cloud text).
        if requested == "auto":
            fb = _local_complete(req.prompt, cfg, min(req.max_tokens, policy.max_tokens))
            # Preserve local text but mark fallback truthfully.
            reason = res.code or "PROVIDER_OUTAGE"
            if fb.ok and fb.usage is not None:
                fb.usage = UsageRecord(provider=fb.usage.provider, model=fb.usage.model,
                                       identity=fb.usage.identity,
                                       latency_ms=fb.usage.latency_ms, fallback=True,
                                       fallback_reason=reason)
                fb.identity = fb.usage.identity
            else:
                fb.code = reason
                fb.message = f"auto fallback also failed: {res.message[:200]}"
            log_event("frontier.auto_fallback", reason=reason)
            return fb
        return res
    return res
