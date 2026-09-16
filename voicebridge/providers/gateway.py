"""Packet 8Q — unified gateway: local default, opt-in frontier/auto.

One normalized contract for every route. Local performs no network request.
Frontier requires explicit enable + allowlist + catalog + credential + budgets.
Auto never escalates on untrusted text; unknown/stale/over-budget/unsupported
fail closed or use a clearly typed local fallback.
"""
from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Mapping
from typing import Any, Callable, Optional

from ..logging_setup import log_event
from ..redaction import redact_text
from . import frontier as _frontier
from .catalog import ModelEntry, ProviderCatalog
from .contracts import ProviderRequest, ProviderResult, UsageRecord
from .credentials import (SecretRef, looks_like_browser_secret,
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


def _local_complete(prompt: str, cfg: Any, max_tokens: int, *,
                    conversation_prompt: str = "", context: str = "",
                    timeout_s: Optional[float] = None,
                    on_sentence: Optional[Callable[[str], None]] = None,
                    should_stop: Optional[Callable[[], bool]] = None,
                    reasoning: Optional[str] = None,
                    num_ctx: Optional[int] = None,
                    model: Optional[str] = None) -> ProviderResult:
    """Local path: existing runner contract, no network beyond Ollama.

    ``on_sentence`` selects the streaming adapter (TTS overlap); otherwise the
    blocking ``chat_local`` adapter is used. Both are the sanctioned shared
    local adapters — no caller performs its own Ollama request.
    """
    try:
        from ..local_llm import chat_local, chat_local_stream
        from ..runner_contract import resolve
        rc = resolve(cfg)
        alias = (model or "").strip() or rc.alias
        timeout = 8.0 if timeout_s is None else float(timeout_s)
        t0 = time.time()
        if on_sentence is not None:
            text = chat_local_stream(
                prompt, conversation_prompt, model=alias, url=rc.url,
                timeout_s=timeout, max_tokens=max_tokens, context=context,
                num_ctx=num_ctx or rc.num_ctx, on_sentence=on_sentence,
                should_stop=should_stop)
        else:
            text = chat_local(prompt[:2000], conversation_prompt,
                              model=alias, url=rc.url, timeout_s=timeout,
                              max_tokens=max_tokens, context=context,
                              num_ctx=num_ctx or rc.num_ctx,
                              reasoning=reasoning)
        ms = int((time.time() - t0) * 1000)
        if not text:
            return ProviderResult(ok=False, code="PROVIDER_OUTAGE",
                                  message="local model unavailable",
                                  provider="local", model=alias,
                                  identity=f"local/{alias}")
        usage = UsageRecord(provider="local", model=alias,
                            identity=f"local/{alias}", latency_ms=ms)
        return ProviderResult(ok=True, text=text, provider="local",
                              model=alias, identity=f"local/{alias}",
                              usage=usage)
    except Exception as exc:
        _m = (model or "").strip() or "qubit-brain4"
        return ProviderResult(ok=False, code="PROVIDER_OUTAGE",
                              message=redact_text(str(exc))[:200],
                              provider="local", model=_m,
                              identity=f"local/{_m}")


def _local_generate(prompt: str, cfg: Any, max_tokens: int, *,
                    system_prompt: str = "",
                    structured_schema: Optional[Any] = None,
                    options: Optional[dict] = None,
                    timeout_s: Optional[float] = None,
                    keep_alive: Optional[int] = None,
                    model: Optional[str] = None) -> ProviderResult:
    """Raw local ``/api/generate`` adapter (classifier/summarizer shape).

    Preserves the Packet 4B canonical runner contract (model alias, num_ctx,
    keep_alive) so the resident runner never reloads. Per-caller options are
    bounded to temperature/num_predict policy only.
    """
    from ..runner_contract import resolve

    try:
        rc = resolve(cfg)
        alias = (model or "").strip() or rc.alias
        timeout = 8.0 if timeout_s is None else float(timeout_s)
        opts = {"temperature": 0.0, "num_ctx": rc.num_ctx,
                "num_predict": int(max_tokens)}
        if options:
            opts.update(options)
        payload: dict = {
            "model": alias, "prompt": prompt, "stream": False,
            "keep_alive": rc.keep_alive if keep_alive is None else keep_alive,
            "options": opts}
        if system_prompt:
            payload["system"] = system_prompt
        if structured_schema is not None:
            payload["format"] = structured_schema
        req = urllib.request.Request(
            f"{rc.url}/api/generate",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
        text = str(body.get("response") or "")
        ms = int((time.time() - t0) * 1000)
        if not text:
            return ProviderResult(ok=False, code="PROVIDER_OUTAGE",
                                  message="local model returned no content",
                                  provider="local", model=alias,
                                  identity=f"local/{alias}")
        return ProviderResult(
            ok=True, text=text, provider="local", model=alias,
            identity=f"local/{alias}",
            usage=UsageRecord(provider="local", model=alias,
                              identity=f"local/{alias}", latency_ms=ms))
    except Exception as exc:
        _m = (model or "").strip() or "qubit-brain4"
        return ProviderResult(ok=False, code="PROVIDER_OUTAGE",
                              message=redact_text(str(exc))[:200],
                              provider="local", model=_m,
                              identity=f"local/{_m}")


def _local_chat_raw(messages: Any, cfg: Any, max_tokens: int, *,
                    structured_schema: Optional[Any] = None,
                    options: Optional[dict] = None,
                    timeout_s: Optional[float] = None,
                    keep_alive: Optional[int] = None,
                    model: Optional[str] = None) -> ProviderResult:
    """Raw local ``/api/chat`` adapter (planner/vision message shape).

    ``messages`` may carry role/content and, for vision, an ``images`` list.
    ``keep_alive`` is explicit so the vision deep-tier cycle can expire the
    resident model; planner leaves it at the canonical -1.
    """
    from ..local_llm import _extract_reply
    from ..runner_contract import resolve

    try:
        rc = resolve(cfg)
        alias = (model or "").strip() or rc.alias
        timeout = 8.0 if timeout_s is None else float(timeout_s)
        msgs = list(messages or [])
        opts = {"temperature": 0.0, "num_ctx": rc.num_ctx,
                "num_predict": int(max_tokens)}
        if options:
            opts.update(options)
        payload: dict = {
            "model": alias, "messages": msgs, "stream": False,
            "keep_alive": rc.keep_alive if keep_alive is None else keep_alive,
            "options": opts}
        if structured_schema is not None:
            payload["format"] = structured_schema
        req = urllib.request.Request(
            f"{rc.url}/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode())
        text = _extract_reply(body)
        ms = int((time.time() - t0) * 1000)
        if not text:
            return ProviderResult(ok=False, code="PROVIDER_OUTAGE",
                                  message="local model returned no content",
                                  provider="local", model=alias,
                                  identity=f"local/{alias}")
        return ProviderResult(
            ok=True, text=text, provider="local", model=alias,
            identity=f"local/{alias}",
            usage=UsageRecord(provider="local", model=alias,
                              identity=f"local/{alias}", latency_ms=ms))
    except Exception as exc:
        _m = (model or "").strip() or "qubit-brain4"
        return ProviderResult(ok=False, code="PROVIDER_OUTAGE",
                              message=redact_text(str(exc))[:200],
                              provider="local", model=_m,
                              identity=f"local/{_m}")


def _local_dispatch(prompt: str, cfg: Any, max_tokens: int, *,
                    local_mode: str = "chat", **kw: Any) -> ProviderResult:
    """Route one local request to the matching shared local adapter."""
    mode = (local_mode or "chat").lower()
    if mode == "generate":
        return _local_generate(
            prompt, cfg, max_tokens,
            system_prompt=kw.get("system_prompt", ""),
            structured_schema=kw.get("structured_schema"),
            options=kw.get("options"), timeout_s=kw.get("timeout_s"),
            keep_alive=kw.get("keep_alive"), model=kw.get("model"))
    if mode == "chat_raw":
        msgs = kw.get("messages")
        if msgs is None:
            msgs = [{"role": "user", "content": prompt}]
        return _local_chat_raw(
            msgs, cfg, max_tokens,
            structured_schema=kw.get("structured_schema"),
            options=kw.get("options"), timeout_s=kw.get("timeout_s"),
            keep_alive=kw.get("keep_alive"), model=kw.get("model"))
    return _local_complete(
        prompt, cfg, max_tokens,
        conversation_prompt=kw.get("conversation_prompt", ""),
        context=kw.get("context", ""), timeout_s=kw.get("timeout_s"),
        on_sentence=kw.get("on_sentence"), should_stop=kw.get("should_stop"),
        reasoning=kw.get("reasoning"), num_ctx=kw.get("num_ctx"),
        model=kw.get("model"))


def resolve_role(cfg: Any, role: str = "chat", *,
                 profile_id: Optional[str] = None,
                 session: Optional[str] = None):
    """Resolve the effective model profile for a task role (canonical).

    Thin re-export of :func:`voicebridge.providers.resolver.resolve` so every
    production caller has one import surface. Raises ``ResolverError``.
    """
    from . import resolver
    return resolver.resolve(cfg, role=role, profile_id=profile_id,
                            session=session)


def run_role(prompt: str, cfg: Any, *, role: str = "chat",
             profile_id: Optional[str] = None,
             session: Optional[str] = None,
             max_tokens: int = 100,
             timeout_s: Optional[float] = None,
             provider: str = "", model: str = "",
             base_url: str = "", api_key: str = "",
             local_mode: str = "chat",
             conversation_prompt: str = "", context: str = "",
             system_prompt: str = "",
             structured_schema: Optional[Any] = None,
             messages: Any = None, options: Optional[dict] = None,
             keep_alive: Optional[int] = None,
             on_sentence: Optional[Callable[[str], None]] = None,
             should_stop: Optional[Callable[[], bool]] = None,
             reasoning: Optional[str] = None,
             num_ctx: Optional[int] = None,
             require_tools: bool = False,
             require_streaming: bool = False,
             context_chars: int = 0,
             allow_private: bool = False,
             cancel: Optional[Callable[[], bool]] = None) -> ProviderResult:
    """Resolve a task role's effective profile, then execute via ``run``.

    Local profiles execute on the local route only (no frontier/network
    request); a non-executable external profile is a typed refusal, never a
    silent local substitution. ``prompt`` is used by the chat/generate
    adapters; ``messages``/``structured_schema`` drive the raw chat adapter.

    The resolved profile's model is always forwarded: for a local profile the
    selected ``eff.model`` reaches the local adapter instead of being discarded
    in favor of a hard-coded alias. ``base_url``/``api_key`` are optional
    gateway overrides for a canonical caller that holds a pre-resolved
    credential (never logged); the frontier policy still gates enablement,
    allowlist, egress, and budgets, so the shipped local-only default never
    reaches the network.
    """
    from . import resolver

    try:
        eff = resolver.resolve(cfg, role=role, profile_id=profile_id,
                               session=session)
    except resolver.ResolverError as exc:
        return ProviderResult(ok=False, code=exc.code, message=exc.detail,
                              provider="", model="", identity="resolver")
    if not eff.executable:
        code = ("EGRESS_DENIED" if eff.egress_required
                and not eff.egress_allowed else "PROFILE_NOT_EXECUTABLE")
        return ProviderResult(
            ok=False, code=code,
            message=(f"profile {eff.profile_id!r} is not executable: "
                     f"policy_enabled={eff.policy_enabled} "
                     f"allowlisted={eff.allowlisted} "
                     f"egress_allowed={eff.egress_allowed}"),
            provider=eff.provider, model=eff.model,
            identity=f"{eff.provider}/{eff.model}")
    route = "local" if eff.is_local else eff.route
    if route not in ("local", "frontier", "auto"):
        # A realtime/duplex kind is a speech adapter, not a text runner: never
        # silently substitute local text for it.
        return ProviderResult(
            ok=False, code="PROFILE_NOT_EXECUTABLE",
            message=f"profile {eff.profile_id!r} kind {eff.kind!r} has no "
                    f"text-runner route",
            provider=eff.provider, model=eff.model,
            identity=f"{eff.provider}/{eff.model}")
    req = ProviderRequest(
        prompt=prompt, max_tokens=int(max_tokens),
        timeout_s=float(timeout_s) if timeout_s is not None else 20.0,
        route=route,
        provider=provider or ("" if eff.is_local else eff.provider),
        model=model or eff.model,
        structured_schema=structured_schema,
        require_tools=require_tools, require_streaming=require_streaming,
        context_chars=context_chars or len(prompt or ""))
    return run(req, cfg, cancel=cancel, allow_private=allow_private,
               base_url=base_url or ("" if eff.is_local else eff.base_url),
               api_key=api_key,
               key_ref="" if eff.is_local else eff.key_ref,
               local_mode=local_mode, conversation_prompt=conversation_prompt,
               context=context, timeout_s=timeout_s,
               system_prompt=system_prompt, messages=messages, options=options,
               keep_alive=keep_alive, on_sentence=on_sentence,
               should_stop=should_stop, reasoning=reasoning, num_ctx=num_ctx)


def run(req: ProviderRequest, cfg: Any,
        catalog: Optional[ProviderCatalog] = None,
        transport: Optional[_frontier.Transport] = None,
        cancel: Optional[Callable[[], bool]] = None,
        allow_private: bool = False, *,
        base_url: str = "",
        api_key: str = "",
        key_ref: str = "",
        local_mode: str = "chat",
        conversation_prompt: str = "",
        context: str = "",
        timeout_s: Optional[float] = None,
        system_prompt: str = "",
        messages: Any = None,
        options: Optional[dict] = None,
        keep_alive: Optional[int] = None,
        on_sentence: Optional[Callable[[str], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
        reasoning: Optional[str] = None,
        num_ctx: Optional[int] = None) -> ProviderResult:
    """Run one request through the 8Q policy. Never raises; typed results.

    The local route uses one of the shared local adapters: ``chat`` (default,
    ``chat_local``/``chat_local_stream``), ``generate`` (raw ``/api/generate``)
    or ``chat_raw`` (raw ``/api/chat`` with explicit messages/format/images).
    All three honor the canonical runner contract; none is a caller-owned
    Ollama request.
    """
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
        return _local_dispatch(
            req.prompt, cfg, min(req.max_tokens, policy.max_tokens),
            local_mode=local_mode, conversation_prompt=conversation_prompt,
            context=context, timeout_s=timeout_s, system_prompt=system_prompt,
            structured_schema=req.structured_schema, messages=messages,
            options=options, keep_alive=keep_alive, on_sentence=on_sentence,
            should_stop=should_stop, reasoning=reasoning, num_ctx=num_ctx,
            model=(req.model or None))
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

    # Capability requirements fail closed even when no catalog is supplied:
    # a caller that requires streaming/tools/structured output must never be
    # silently handed a capability we could not verify.
    if catalog is None and (req.require_streaming or req.require_tools
                            or req.structured_schema is not None):
        return _deny("UNSUPPORTED_CAPABILITY",
                     "capability requirements need a validated catalog; "
                     "none was supplied")

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
    # key_ref also accepts a server-side secret reference; it is normalized
    # to the same string contract below, so every refusal and redaction
    # below applies unchanged.
    pcfg = policy.provider_cfg(provider)
    # A resolved model profile may declare its own credential reference;
    # prefer it over the policy provider default so two profiles of one
    # provider never share a credential by accident. Values are never logged.
    raw_ref = key_ref or pcfg.get("key_ref", "")
    if isinstance(raw_ref, SecretRef):
        ref = f"{raw_ref.source}:{raw_ref.name}"
    elif isinstance(raw_ref, Mapping):
        ref = f"{raw_ref.get('source', '')}:{raw_ref.get('name', '')}"
    else:
        ref = str(raw_ref or "")
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
    if api_key:
        # A canonical caller (e.g. the voice fallback whose credential lives in
        # the Qubit env file rather than provider config) may supply an
        # already-resolved credential. It is used on the wire only, is never
        # logged or stored, and does not relax the enablement/allowlist/egress
        # gates above.
        key = str(api_key)
    else:
        key, kcode = resolve_key(provider, ref)
        if kcode:
            return ProviderResult(ok=False, code="AUTH_MISSING",
                                  message=f"no credential ({redact_ref(ref)})",
                                  provider=provider, model=model,
                                  identity=f"{provider}/{model}")

    base_url = (str(base_url or "").strip()
                or str(pcfg.get("base_url", "") or
                       _frontier.WELL_KNOWN_BASES.get(provider, "") or ""))
    if looks_like_opencode_private_db(base_url):
        return ProviderResult(ok=False, code="AUTH_MISSING",
                              message="undocumented OpenCode credential use refused",
                              provider=provider, model=model,
                              identity=f"{provider}/{model}")
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
