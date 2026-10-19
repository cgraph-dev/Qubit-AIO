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


def _default_local_model(cfg: Any) -> str:
    """Canonical local model alias for truthful error identities.

    Uses the one resolver instead of a hard-coded alias so a replaced default
    profile is reported instead of a stale literal. The runner-contract alias
    is only a last-resort fallback when resolution itself is unavailable.
    """
    try:
        from . import resolver
        return resolver.resolve(cfg, role=resolver.DEFAULT_ROLE).model
    except Exception:
        from ..runner_contract import DEFAULT_ALIAS
        return DEFAULT_ALIAS


def _opencode_session_transport(base: Any, provider: str, base_url: str,
                                session_id: str, fallback_key: str,
                                profile_id: str = "") -> Any:
    """Wrap a frontier transport to send OpenCode's required affinity header.

    The vendored runtime adds ``x-opencode-session`` to every OpenCode request
    (``agent.opencode_affinity``); the generic frontier transport did not, so an
    otherwise-authorized opencode-go/zen turn was rejected by the relay with
    ``MissingSessionID``. The value only has to be opaque and consistent per
    conversation: the resolved session id first, then the request idempotency
    key, then the profile id. Non-OpenCode targets are returned untouched.
    """
    extra: dict = {}
    try:
        from .. import qubit_runtime
        qubit_runtime.ensure_path()
        from agent.opencode_affinity import (
            is_opencode_target, opencode_session_headers)
        if not is_opencode_target(provider, base_url):
            return base
        sid = (str(session_id or "").strip()
               or str(fallback_key or "").strip()
               or str(profile_id or "").strip())
        if not sid:
            import uuid as _uuid
            sid = _uuid.uuid4().hex
        extra = opencode_session_headers(provider, base_url, sid)
    except Exception:  # noqa: BLE001 - dependency-free fallback below
        low = f"{provider} {base_url}".lower()
        if "opencode" not in low:
            return base
        import uuid as _uuid
        sid = (str(session_id or "").strip()
               or str(fallback_key or "").strip()
               or str(profile_id or "").strip() or _uuid.uuid4().hex)
        extra = {"x-opencode-session": sid}
    if not extra:
        return base

    def _wrapped(url: str, headers: Any, payload: Any,
                 timeout_s: float) -> Any:
        merged = dict(headers or {})
        for key, value in extra.items():
            merged.setdefault(key, value)
        return base(url, merged, payload, timeout_s)

    # Keep the abort seam reachable through the header wrapper, so a STOP can
    # still shut down the live OpenCode relay socket mid-response.
    abort = getattr(base, "abort", None)
    if callable(abort):
        try:
            _wrapped.abort = abort  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - abort is best-effort
            pass
    return _wrapped


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
                    model: Optional[str] = None,
                    cancel: Optional[Callable[[], bool]] = None) -> ProviderResult:
    """Local path: existing runner contract, no network beyond Ollama.

    ``on_sentence`` selects the streaming adapter (TTS overlap); otherwise the
    blocking ``chat_local`` adapter is used. Both are the sanctioned shared
    local adapters — no caller performs its own Ollama request.
    """
    try:
        from ..local_llm import chat_local, chat_local_stream
        from ..runner_contract import DEFAULT_URL as _RUNNER_DEFAULT_URL
        from ..runner_contract import resolve
        rc = resolve(cfg)
        alias = (model or "").strip() or rc.alias
        timeout = 8.0 if timeout_s is None else float(timeout_s)
        # Only pass an explicit endpoint when it differs from the adapter's own
        # default; the local adapter already defaults to the resident runner
        # URL, and redundant defaults must not change the adapter call contract.
        extra = {"url": rc.url} if rc.url and rc.url != _RUNNER_DEFAULT_URL else {}
        t0 = time.time()
        if on_sentence is not None:
            text = chat_local_stream(
                prompt, conversation_prompt, model=alias,
                timeout_s=timeout, max_tokens=max_tokens, context=context,
                num_ctx=num_ctx or rc.num_ctx, on_sentence=on_sentence,
                should_stop=should_stop, reasoning=reasoning, **extra)
        else:
            # Lane 2 (2026-10-03, journal test-2 09:52:38): the non-streaming
            # local path silently truncated the prompt at 2000 chars while the
            # streaming branch above passed it whole — a "2000 context" the
            # owner could see as lost trailing text. The runner contract owns
            # context limits (num_ctx); never truncate here.
            text = chat_local(prompt, conversation_prompt,
                              model=alias, timeout_s=timeout,
                              max_tokens=max_tokens, context=context,
                              num_ctx=num_ctx or rc.num_ctx,
                              reasoning=reasoning, cancel=cancel, **extra)
        ms = int((time.time() - t0) * 1000)
        if not text:
            # Distinct from a transport exception so callers can map an empty
            # local reply to MODEL_UNAVAILABLE and an outage to MODEL_ERROR.
            return ProviderResult(ok=False, code="MODEL_UNAVAILABLE",
                                  message="local model returned no content",
                                  provider="local", model=alias,
                                  identity=f"local/{alias}")
        from ..local_llm import (  # noqa: E402 - keep the adapter lazy
            normalize_reasoning_level,
            resolve_reasoning_budget,
        )

        details: dict = {}
        if reasoning is not None:
            level = normalize_reasoning_level(reasoning)
            details["reasoning_applied"] = {
                "level": level,
                "max_tokens": resolve_reasoning_budget(level)}
        usage = UsageRecord(provider="local", model=alias,
                            identity=f"local/{alias}", latency_ms=ms)
        return ProviderResult(ok=True, text=text, provider="local",
                              model=alias, identity=f"local/{alias}",
                              usage=usage, details=details)
    except Exception as exc:
        _m = (model or "").strip() or _default_local_model(cfg)
        from ..local_llm import LocalLlmCancelled  # noqa: E402 - lazy adapter

        if isinstance(exc, LocalLlmCancelled):
            return ProviderResult(ok=False, code="CANCELLED",
                                  message="cancelled during local model call",
                                  provider="local", model=_m,
                                  identity=f"local/{_m}")
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
                    model: Optional[str] = None,
                    cancel: Optional[Callable[[], bool]] = None
                    ) -> ProviderResult:
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
        from ..local_llm import post_json
        t0 = time.time()
        body = json.loads(post_json(
            f"{rc.url}/api/generate", payload, timeout,
            cancel=cancel).decode())
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
        from ..local_llm import LocalLlmCancelled
        code = ("CANCELLED" if isinstance(exc, LocalLlmCancelled)
                else "PROVIDER_OUTAGE")
        _m = (model or "").strip() or _default_local_model(cfg)
        return ProviderResult(ok=False, code=code,
                              message=redact_text(str(exc))[:200],
                              provider="local", model=_m,
                              identity=f"local/{_m}")


def _local_chat_raw(messages: Any, cfg: Any, max_tokens: int, *,
                    structured_schema: Optional[Any] = None,
                    options: Optional[dict] = None,
                    timeout_s: Optional[float] = None,
                    keep_alive: Optional[int] = None,
                    model: Optional[str] = None,
                    cancel: Optional[Callable[[], bool]] = None
                    ) -> ProviderResult:
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
        # Thinking models (e.g. qubit-brain4 = Qwen3-abliterated) default to
        # reasoning ON for a raw /api/chat call. Without an explicit ``think``
        # flag the whole ``num_predict`` budget is spent on the chain-of-thought
        # and the response returns content='' with done_reason=length. Mirror
        # the canonical voice path (``local_llm._chat_payload``): pass the
        # configured ``intent.local_llm.think`` (default False). A caller that
        # wants reasoning can override via ``options["think"]``.
        think = False
        try:
            think = bool(cfg.get("intent", "local_llm", "think",
                                 default=False))
        except Exception:  # noqa: BLE001 - absent config keeps no-think default
            think = False
        if options and "think" in options:
            think = bool(options["think"])
        opts.pop("think", None)
        payload: dict = {
            "model": alias, "messages": msgs, "stream": False,
            "think": think,
            "keep_alive": rc.keep_alive if keep_alive is None else keep_alive,
            "options": opts}
        if structured_schema is not None:
            payload["format"] = structured_schema
        from ..local_llm import post_json
        t0 = time.time()
        body = json.loads(post_json(
            f"{rc.url}/api/chat", payload, timeout,
            cancel=cancel).decode())
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
        from ..local_llm import LocalLlmCancelled
        code = ("CANCELLED" if isinstance(exc, LocalLlmCancelled)
                else "PROVIDER_OUTAGE")
        _m = (model or "").strip() or _default_local_model(cfg)
        return ProviderResult(ok=False, code=code,
                              message=redact_text(str(exc))[:200],
                              provider="local", model=_m,
                              identity=f"local/{_m}")


def _local_dispatch(prompt: str, cfg: Any, max_tokens: int, *,
                    local_mode: str = "chat",
                    cancel: Optional[Callable[[], bool]] = None,
                    **kw: Any) -> ProviderResult:
    """Route one local request to the matching shared local adapter."""
    mode = (local_mode or "chat").lower()
    if mode == "generate":
        return _local_generate(
            prompt, cfg, max_tokens,
            system_prompt=kw.get("system_prompt", ""),
            structured_schema=kw.get("structured_schema"),
            options=kw.get("options"), timeout_s=kw.get("timeout_s"),
            keep_alive=kw.get("keep_alive"), model=kw.get("model"),
            cancel=cancel)
    if mode == "chat_raw":
        msgs = kw.get("messages")
        if msgs is None:
            msgs = [{"role": "user", "content": prompt}]
        return _local_chat_raw(
            msgs, cfg, max_tokens,
            structured_schema=kw.get("structured_schema"),
            options=kw.get("options"), timeout_s=kw.get("timeout_s"),
            keep_alive=kw.get("keep_alive"), model=kw.get("model"),
            cancel=cancel)
    return _local_complete(
        prompt, cfg, max_tokens,
        conversation_prompt=kw.get("conversation_prompt", ""),
        context=kw.get("context", ""), timeout_s=kw.get("timeout_s"),
        on_sentence=kw.get("on_sentence"), should_stop=kw.get("should_stop"),
        reasoning=kw.get("reasoning"), num_ctx=kw.get("num_ctx"),
        model=kw.get("model"), cancel=cancel)


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


def _resolved_identity(eff: Any, role: str, route: str = "") -> dict:
    """Resolved provider/model/role identity for receipt propagation (PV-04)."""
    return {
        "profile": eff.profile_id, "role": role, "source": eff.source,
        "provider": eff.provider, "model": eff.model, "kind": eff.kind,
        "route": route or eff.route, "locality": eff.locality,
        "executable": bool(eff.executable),
    }


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
                              provider="", model="", identity="resolver",
                              details={"role": role})
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
            identity=f"{eff.provider}/{eff.model}",
            details=_resolved_identity(eff, role))
    route = "local" if eff.is_local else eff.route
    if route not in ("local", "frontier", "auto"):
        # A realtime/duplex kind is a speech adapter, not a text runner: never
        # silently substitute local text for it.
        return ProviderResult(
            ok=False, code="PROFILE_NOT_EXECUTABLE",
            message=f"profile {eff.profile_id!r} kind {eff.kind!r} has no "
                    f"text-runner route",
            provider=eff.provider, model=eff.model,
            identity=f"{eff.provider}/{eff.model}",
            details=_resolved_identity(eff, role))
    req = ProviderRequest(
        prompt=prompt, max_tokens=int(max_tokens),
        timeout_s=float(timeout_s) if timeout_s is not None else 20.0,
        route=route,
        provider=provider or ("" if eff.is_local else eff.provider),
        model=model or eff.model,
        structured_schema=structured_schema,
        require_tools=require_tools, require_streaming=require_streaming,
        context_chars=context_chars or len(prompt or ""))
    res = run(req, cfg, cancel=cancel, allow_private=allow_private,
              base_url=base_url or ("" if eff.is_local else eff.base_url),
              api_key=api_key,
              key_ref="" if eff.is_local else eff.key_ref,
              local_mode=local_mode, conversation_prompt=conversation_prompt,
              context=context, timeout_s=timeout_s,
              system_prompt=system_prompt, messages=messages, options=options,
              keep_alive=keep_alive, on_sentence=on_sentence,
              should_stop=should_stop, reasoning=reasoning, num_ctx=num_ctx,
              profile_id=eff.profile_id, session_id=session or "")
    # PV-04: carry the resolved provider/model/role identity so CLI/dashboard/
    # voice receipts report the same authority that actually executed.
    res.details.update(_resolved_identity(eff, role, route))
    return res


def embed_texts(texts: Any, cfg: Any = None, *, role: str = "embedding",
                profile_id: Optional[str] = None,
                session: Optional[str] = None, url: str = "", model: str = "",
                timeout_s: Optional[float] = None,
                num_gpu: int = 0) -> list:
    """Embed texts through the canonical embedding role + shared adapter.

    Resolves ``embedding`` via the one resolver (local synthetic default unless
    a profile/session overrides it), refuses a non-executable external profile
    with a typed :class:`~voicebridge.providers.embedding.EmbeddingError`, and
    performs the Ollama call only in
    :mod:`voicebridge.providers.embedding`. This is the single embedding entry
    point the L1 router and semantic memory share.
    """
    from . import embedding as _embedding
    from . import resolver

    eff = None
    try:
        eff = resolver.resolve(cfg, role=role, profile_id=profile_id,
                               session=session)
    except resolver.ResolverError as exc:
        raise _embedding.EmbeddingError(exc.code, exc.detail) from exc
    if not eff.executable:
        raise _embedding.EmbeddingError(
            "EGRESS_DENIED" if eff.egress_required and not eff.egress_allowed
            else "PROFILE_NOT_EXECUTABLE",
            f"embedding profile {eff.profile_id!r} is not executable")
    if not eff.is_local:
        raise _embedding.EmbeddingError(
            "PROFILE_NOT_EXECUTABLE",
            f"external embedding profile {eff.provider}/{eff.model} has no "
            "configured adapter")
    base = str(url or "").strip() or _embedding.runner_url(cfg)
    return _embedding.embed_local(
        list(texts), url=base, model=str(model or "").strip() or eff.model,
        timeout_s=10.0 if timeout_s is None else float(timeout_s),
        num_gpu=num_gpu)


def _working_slot_summary(cfg: Any) -> str:
    """Human-readable still-working slot for a dead-model refusal (read-only).

    Best-effort resolver read only: never raises, never writes, never moves
    the slot. Failover policy is an owner call, so the typed error names the
    working slot without switching to it.
    """
    try:
        from . import resolver as _resolver
        eff = _resolver.resolve(cfg, role=_resolver.DEFAULT_ROLE)
        return (f"{eff.provider}/{eff.model} "
                f"(profile {eff.profile_id!r})")
    except Exception:  # noqa: BLE001 - summary is best-effort
        pass
    return "the shipped local default"


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
        num_ctx: Optional[int] = None,
        profile_id: str = "",
        session_id: str = "") -> ProviderResult:
    """Run one request through the 8Q policy. Never raises; typed results.

    The local route uses one of the shared local adapters: ``chat`` (default,
    ``chat_local``/``chat_local_stream``), ``generate`` (raw ``/api/generate``)
    or ``chat_raw`` (raw ``/api/chat`` with explicit messages/format/images).
    All three honor the canonical runner contract; none is a caller-owned
    Ollama request.

    ``profile_id`` names the resolved model profile this request executes. The
    one resolver may enable/allowlist that specific profile through
    ``intent.frontier.profile_overrides`` while the global frontier switch stays
    off; threading it here keeps a ``run_role``/remote-chat call consistent with
    the profile the resolver already reported executable (and supplies the
    profile's own ``base_url``/``key_ref`` when no explicit override is given).
    It does not widen policy: a profile whose override is absent/denied still
    fails closed on the global switch/allowlist. ``session_id`` names the
    conversation so an OpenCode relay gets its required ``x-opencode-session``
    affinity header; it is otherwise unused.
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

    # Honor the resolved profile's effective policy/profile overrides. Without
    # this, a profile the resolver enabled via ``profile_overrides`` would be
    # reported executable and then denied here with CLOUD_DISABLED — the exact
    # split-brain the resolver exists to prevent.
    profile = None
    pid = str(profile_id or "").strip()
    if pid:
        try:
            from . import resolver as _resolver
            profile = _resolver.get_profile(cfg, pid)
        except Exception:  # noqa: BLE001 - a missing profile is fail-closed
            profile = None
        try:
            from ..config import model_profile_overrides as _overrides
            ov = _overrides(cfg, pid)
        except Exception:  # noqa: BLE001 - absent override keeps global policy
            ov = {}
        if "policy_enabled" in ov:
            policy.enabled = bool(ov["policy_enabled"])
        if ov.get("allowlisted") and provider and model:
            pair = f"{provider}/{model}"
            if pair not in policy.allowlist:
                policy.allowlist = [*policy.allowlist, pair]

    def _fallback(reason: str, msg: str) -> ProviderResult:
        fb = _local_complete(req.prompt, cfg,
                             min(req.max_tokens, policy.max_tokens),
                             cancel=cancel)
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
            model=(req.model or None), cancel=cancel)
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
    raw_ref = (key_ref or (profile.key_ref if profile else "")
               or pcfg.get("key_ref", ""))
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
                or str((profile.base_url if profile else "") or "")
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
    reasoning_applied: Optional[dict] = None
    if reasoning is not None:
        # A reasoning level is a real per-request output budget on the
        # frontier path too (sent as the request's max_tokens, still capped
        # by policy) — never silently dropped. Levels are validated by the
        # caller; resolve here is defense in depth with the same mapping.
        from ..local_llm import (  # noqa: E402 - heavy adapter, keep lazy
            normalize_reasoning_level,
            resolve_reasoning_budget,
        )

        level = normalize_reasoning_level(reasoning)
        budget = resolve_reasoning_budget(level)
        max_tokens = min(int(budget), policy.max_tokens)
        reasoning_applied = {"level": level, "max_tokens": max_tokens}
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

    if reasoning_applied is not None:
        import dataclasses as _dc

        req = _dc.replace(req, max_tokens=int(reasoning_applied["max_tokens"]))
    # OpenCode relays reject a request with no session-affinity header
    # (``MissingSessionID``); wrap the transport only for OpenCode targets and
    # only when the caller did not already supply a transport.
    if transport is None:
        transport = _opencode_session_transport(
            _frontier.AbortableHTTPTransport(), provider, base_url, session_id,
            str(req.idempotency_key or ""), profile_id)
    client = _frontier.FrontierClient(base_url, key, provider, model,
                                     timeout_s=min(req.timeout_s, policy.timeout_s),
                                     max_retries=policy.max_retries,
                                     transport=transport, cancel=cancel,
                                     max_response_chars=policy.max_response_chars)
    gov.acquire()
    try:
        # ``messages`` carries a pre-built multimodal conversation for the
        # vision path (OpenAI content-block shape with the image data URL); it
        # is passed through verbatim so the image is never dropped. Text-only
        # callers pass ``None`` and the client builds its default body.
        res = client.complete(req, eg.sanitized, est_cost=est, messages=messages)
    except Exception as exc:
        gov.release(provider, False)
        return ProviderResult(ok=False, code="PROVIDER_OUTAGE",
                              message=redact_text(str(exc))[:200],
                              provider=provider, model=model,
                              identity=f"{provider}/{model}")
    if reasoning_applied is not None:
        res.details["reasoning_applied"] = dict(reasoning_applied)
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
        try:
            from . import dead_models as _DM

            _DM.clear_dead(cfg, provider, model)
        except Exception:
            pass
        return res
    if not res.ok:
        # Auto route falls back to local with truthful identity; explicit
        # frontier returns the typed failure (no fake cloud text).
        if res.code == "MODEL_UNAVAILABLE_TURN" and requested != "auto":
            # Turn-time dead model: typed error only. Name the dead model
            # (already in res.message) plus the still-working slot, without
            # moving the slot and without a silent fallback. Record it so
            # the activation gate refuses to (re-)persist this model into
            # the slots until a later successful turn clears the record.
            try:
                from . import dead_models as _DM

                _DM.record_dead(cfg, provider, model, res.code or "unavailable")
            except Exception:
                pass
            res.message = (f"{res.message} — still serving default turns: "
                           f"{_working_slot_summary(cfg)}")
            return res
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
