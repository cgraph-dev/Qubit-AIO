"""Qubit-native agent turns.

One task in, one reply out, through Qubit's own local model path — no
supervised external runtime, no second agent process, no second memory.
Timeout, cancellation (via the caller's estop probe), redaction, audit,
and typed receipts are enforced here so every caller (voice, schedules,
orchestrator, CLI task path) shares one execution contract.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional

DEFAULT_TIMEOUT_S = 300.0
MAX_TEXT_CHARS = 8000


class AgentDenied(Exception):
    """Refused before execution (disabled or emergency stop)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _redact(text: str) -> str:
    from .redaction import redact_text

    return redact_text(text)


def _audit(op: str, **fields: Any) -> None:
    from .logging_setup import log_event

    try:
        log_event(f"agent.{op}", **fields)
    except Exception:
        pass


def _agent_enabled(cfg: Any) -> bool:
    try:
        return bool(cfg.get("agent", "enabled", default=True))
    except Exception:
        return True


def _explicit_model(cfg: Any) -> str:
    """The operator's explicit ``agent.model`` override, or ``""``.

    Distinct from :func:`_model`, which falls back to the voicebridge resolver
    default (a local model). The vendored tool-capable worker must only adopt
    an override the operator actually set; otherwise the vendored config's own
    tool-capable ``model`` slot decides (matching /chat).
    """
    try:
        return str(cfg.get("agent", "model", default="") or "").strip() \
            if cfg is not None else ""
    except Exception:
        return ""


def _model(cfg: Any) -> str:
    """Legacy ``agent.model`` override, else the canonical resolver default.

    Never a hard-coded alias: when no explicit model is configured the one
    resolver answers which model is effective, so a replaced/selected default
    profile is honored (PV-02).
    """
    try:
        configured = (str(cfg.get("agent", "model", default="") or "").strip()
                      if cfg is not None else "")
        if configured:
            return configured
    except Exception:
        pass
    try:
        from .providers import resolver

        return resolver.resolve(cfg, role=resolver.DEFAULT_ROLE).model
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Canonical conversation id (single session/transcript authority)
# ---------------------------------------------------------------------------
#: Surface-local session ids that mean "the default voice/PTT conversation".
#: They are aliases of the canonical conversation id, so a desktop/voice turn
#: and a ``/qubit-chat`` turn on the default conversation read and write ONE
#: transcript. An explicit, non-alias id is never rewritten.
VOICE_SESSION_ALIASES = frozenset({"", "voice", "qubit:voice", "qubit-voice"})


def canonical_session_id(cfg: Any) -> str:
    """The canonical default conversation id for voice/PTT/chat surfaces.

    Configurable via ``agent.session_id``; when unset it is the dashboard chat
    default (:data:`voicebridge.chat_transport.DEFAULT_SESSION`), so the
    desktop/voice path and ``/qubit-chat`` converge on one conversation by
    default. Typed and total: a Config-less caller gets the default, never an
    exception.
    """
    try:
        configured = str(
            cfg.get("agent", "session_id", default="") or "").strip()
    except Exception:  # noqa: BLE001 - Config-less callers use the default
        configured = ""
    if configured:
        return configured[:80]
    try:
        from .chat_transport import DEFAULT_SESSION

        return DEFAULT_SESSION
    except Exception:  # noqa: BLE001 - never let the default raise
        return "dashboard-chat"


def resolve_session_id(cfg: Any, requested: Optional[str] = None, *,
                       surface: str = "voice") -> str:
    """Map a surface-local session id onto the canonical conversation id.

    * empty / a known voice-default alias -> :func:`canonical_session_id`
      (so the desktop/voice/PTT default converges with ``/qubit-chat``)
    * any other explicit id -> returned unchanged, so a caller that names a
      session keeps it exactly (explicit ids always win)

    The result is bounded to the store's 80-char session-id limit. ``surface``
    is accepted for audit/telemetry and future per-surface policy; it never
    changes an explicit id. Never raises.
    """
    req = str(requested or "").strip()
    if req in VOICE_SESSION_ALIASES:
        return canonical_session_id(cfg)
    return req[:80]


def _canonical_session_store(cfg: Any) -> Any:
    """The ONE canonical chat store for voice/worker turns.

    Returns exactly ``voicebridge.chat_transport.store(cfg)`` —
    :class:`~voicebridge.qubit_session_store.QubitSessionStore`, a strict
    adapter over the vendored ``qubit_state.SessionDB`` (``state.db``) — so a
    voice/orchestrator turn and a ``/qubit-chat``/terminal turn on the same
    session id read and write ONE transcript. Never a second store.

    Best-effort: an unavailable store is audited and the caller's turn result
    is unchanged (persistence must never fabricate or mask a turn).
    """
    try:
        from . import chat_transport

        return chat_transport.store(cfg)
    except Exception as exc:  # noqa: BLE001 - persist failure is non-fatal
        _audit("session_store_unavailable", error=str(exc)[:160])
        return None


def _persist_session_turn(store: Any, session: str, role: str, content: str,
                          *, model: Optional[str] = None) -> None:
    """Append one turn to the canonical store (best-effort, never raises)."""
    if store is None or not str(content or "").strip():
        return
    try:
        store.ensure(session, model=model)
        store.append(session, role, content, model=model)
    except Exception as exc:  # noqa: BLE001 - persist failure is non-fatal
        _audit("session_persist_error", session=session, role=role,
               error=str(exc)[:160])


#: How many transcript rows are read to build/compact prior context. Larger
#: than the fed window so older turns can still contribute a deterministic
#: summary (matches ``chat_transport._CONTEXT_READ_LIMIT``).
_CONTEXT_READ_LIMIT = 200


def _session_context(store: Any, session_id: str, cfg: Any,
                     pending_text: str) -> "tuple[str, Dict[str, Any]]":
    """Bounded prior-transcript context from the canonical store.

    Uses the same :mod:`voicebridge.context_policy` as ``/qubit-chat`` so a
    voice/worker turn and a chat turn on the same canonical session see the
    same bounded history (fast window for a simple turn, deterministic summary
    of older turns, instructions always retained). ``pending_text`` is the
    in-flight turn: it is counted for window sizing but not rendered (the
    caller appends it). Best-effort: an unavailable store yields empty context
    and the turn still runs.
    """
    if store is None or not str(session_id or "").strip():
        return "", {}
    try:
        from . import context_policy as CTX

        msgs = store.messages(session_id, limit=_CONTEXT_READ_LIMIT).get(
            "messages", [])
        if not msgs:
            return "", {}
        decision_msgs = list(msgs)
        if pending_text:
            decision_msgs = decision_msgs + [
                {"role": "user", "content": pending_text}]
        decision = CTX.decide_context(cfg, decision_msgs)
        text, meta = CTX.build_context_prompt(msgs, decision)
        return text, meta
    except Exception as exc:  # noqa: BLE001 - context is additive, non-fatal
        _audit("session_context_error", session=session_id,
               error=str(exc)[:160])
        return "", {}


def _provider_receipt(res: Any, *, runtime: str, path: str) -> Dict[str, Any]:
    """Build a truthful receipt carrying the resolved provider/model/role.

    The resolved identity (PV-04) makes CLI/dashboard/voice receipts agree on
    which provider/model/role/profile actually produced the text.
    """
    receipt: Dict[str, Any] = {
        "runtime": runtime, "model": res.model, "path": path,
        "provider": res.provider, "identity": res.identity,
    }
    details = dict(getattr(res, "details", None) or {})
    for key in ("profile", "role", "source", "route", "locality",
                "reasoning_applied"):
        value = details.get(key)
        if value not in (None, ""):
            receipt[key] = value
    usage = getattr(res, "usage", None)
    if usage is not None:
        receipt["usage"] = {
            "provider": usage.provider, "model": usage.model,
            "identity": usage.identity,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "latency_ms": usage.latency_ms,
            "cost_usd_est": usage.cost_usd_est,
            "fallback": usage.fallback,
            "fallback_reason": usage.fallback_reason,
        }
    return receipt


def turn(cfg: Any, text: str, *, session: str = "qubit",
         timeout_s: Optional[float] = None,
         estop_engaged: Optional[Callable[[], bool]] = None,
         model: Optional[str] = None, role: str = "chat",
         profile_id: Optional[str] = None,
         reasoning: Optional[str] = None) -> Dict[str, Any]:
    """Run one agent turn through the canonical provider gateway.

    Raises AgentDenied before execution on emergency stop / disabled
    agent. Model failure, timeout, and empty replies are typed
    ``ok=false`` results — never an exception, never fabricated text.

    The turn executes via :func:`voicebridge.providers.gateway.run_role`, so
    the resolved profile's role/locality/egress/budget policy applies and the
    local adapter stays behind the gateway. ``model``/``profile_id`` come from
    the canonical resolver when supplied; the receipt records the resolved
    provider/model/role identity that actually ran (PV-04).
    """
    check = estop_engaged or (lambda: False)
    if check():
        raise AgentDenied("EMERGENCY_STOP",
                          "emergency stop latched; agent turn refused")
    if not _agent_enabled(cfg):
        raise AgentDenied("AGENT_DISABLED",
                          "agent turns are off (agent.enabled=false)")
    clean = (text or "").strip()[:MAX_TEXT_CHARS]
    if not clean:
        return {"ok": False, "code": "MALFORMED",
                "error": "turn needs non-empty text", "session": session}
    try:
        from .local_llm import normalize_reasoning_level

        reasoning_level = normalize_reasoning_level(reasoning)
    except Exception as exc:
        return {"ok": False, "code": "REASONING_INVALID",
                "error": str(exc)[:200], "session": session}
    budget = timeout_s or DEFAULT_TIMEOUT_S
    started = time.monotonic()
    try:
        from .providers import gateway as _gateway

        res = _gateway.run_role(
            clean, cfg, role=role, profile_id=profile_id,
            model=str(model or "").strip() or _model(cfg),
            max_tokens=1024, timeout_s=min(budget, 300.0),
            local_mode="chat", should_stop=check, reasoning=reasoning_level)
    except Exception as exc:  # noqa: BLE001 - typed, redacted, no fabrication
        _audit("turn_error", session=session, error=str(exc)[:160])
        return {"ok": False, "code": "MODEL_ERROR",
                "error": _redact(str(exc))[:300], "session": session}
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if check():
        return {"ok": False, "code": "CANCELLED",
                "error": "cancelled during execution", "session": session,
                "elapsed_ms": elapsed_ms}
    if not res.ok:
        if res.code in ("MODEL_UNAVAILABLE", "EMPTY_EMBEDDING") or (
                res.code == "PROVIDER_OUTAGE"
                and not str(res.message or "").strip()):
            _audit("turn_empty", session=session, elapsed_ms=elapsed_ms)
            return {"ok": False, "code": "MODEL_UNAVAILABLE",
                    "error": "local model returned no text; it may be "
                             "unreachable — nothing was fabricated",
                    "session": session, "elapsed_ms": elapsed_ms}
        _audit("turn_failed", session=session, code=res.code)
        return {"ok": False, "code": "MODEL_ERROR",
                "error": _redact(res.message or "model provider refused")[:300],
                "session": session, "elapsed_ms": elapsed_ms}
    reply = str(res.text or "")
    if not reply.strip():
        _audit("turn_empty", session=session, elapsed_ms=elapsed_ms)
        return {"ok": False, "code": "MODEL_UNAVAILABLE",
                "error": "local model returned no text; it may be "
                         "unreachable — nothing was fabricated",
                "session": session, "elapsed_ms": elapsed_ms}
    _audit("turn", session=session, elapsed_ms=elapsed_ms, ok=True)
    from .textutil import sanitize

    return {"ok": True, "reply": _redact(sanitize(reply.strip())),
            "session": session, "elapsed_ms": elapsed_ms,
            "receipt": _provider_receipt(
                res, runtime="qubit-native",
                path="voicebridge.agent.turn")}


def _vendor_tool_names(result: Any) -> "list[str]":
    """Tool names the vendored agent actually invoked, from its message log.

    Reads assistant ``tool_calls`` and ``role == "tool"`` rows so the turn
    receipt reports the real tools that ran (the same evidence
    ``chat_transport._worker_acted`` needs to accept a build). Order-stable,
    de-duplicated.
    """
    names: "list[str]" = []
    for msg in (result or {}).get("messages") or []:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "tool" and msg.get("name"):
            names.append(str(msg["name"]))
        for call in msg.get("tool_calls") or []:
            try:
                fn = (call.get("function") or {}).get("name") or call.get("name")
            except AttributeError:
                fn = None
            if fn:
                names.append(str(fn))
    seen: "set[str]" = set()
    out: "list[str]" = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _local_model_runtime(cfg: Any, model: str) -> "Optional[Dict[str, Any]]":
    """Runtime kwargs for a LOCAL pinned model, or ``None`` when it is not local.

    A ``/qubit-chat`` session can pin a local model (``qubit-spark17``,
    ``qubit-brain``, ...). The vendored provider resolver only knows the
    configured GLOBAL provider, so handing it a bare local model name lands the
    turn on the global API route under the wrong model. The voicebridge
    resolver is the one authority that maps a model id onto a local Ollama
    profile (``kind == "local"``), so it answers "is this local?" and the
    Ollama OpenAI-compatible endpoint supplies provider/base_url — the SAME
    runtime the reference local pick uses. Fail-safe: any error yields ``None``
    so the caller keeps the untouched vendored path.
    """
    name = str(model or "").strip()
    if not name:
        return None
    try:
        from .chat_transport import resolve_route

        routed = resolve_route(cfg, name)
        if not routed.get("ok") or str(routed.get("kind") or "") != "local":
            return None
        provider = str(routed.get("provider") or "ollama").strip() or "ollama"
        from . import qubit_runtime as _qr  # type: ignore

        _qr.use_qubit_home(cfg)
        _qr.ensure_path()
        from qubit_cli.models_local import (  # type: ignore
            _get_ollama_base_url as _ollama_base)
    except Exception:  # noqa: BLE001 - not a local pin: keep the vendored path
        return None
    try:
        base = str(_ollama_base() or "").strip().rstrip("/")
        for suffix in ("/api/tags", "/v1/models", "/api", "/v1"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
    except Exception:  # noqa: BLE001
        base = "http://127.0.0.1:11434"
    if base and "://" not in base:
        base = "http://" + base
    if not base:
        base = "http://127.0.0.1:11434"
    return {
        "provider": provider,
        "base_url": base + "/v1",
        "api_key": "no-key-required",
        "api_mode": "chat_completions",
        "model": str(routed.get("model") or name),
    }


def _run_vendored_session(cfg: Any, text: str, sid: str, *,
                          check: Callable[[], bool], store: Any,
                          reasoning: Optional[str],
                          persist_user: bool = False,
                          event_sink: Optional[Callable[[str, dict], None]] = None,
                          progress_sink: Optional[Callable[..., None]] = None,
                          session_model: Optional[str] = None
                          ) -> "Optional[Dict[str, Any]]":
    """Run one tool-capable agent turn through the vendored ``AIAgent``.

    This is the terminal /chat agent (``run_agent.AIAgent``): it resolves the
    same tool set (skills, plugins, MCP), sends the tool schema to the model,
    and executes tool calls. Owner directive docs/DECISIONS.md #26 requires the
    /qubit-chat worker to be this agent, never a tool-less shim.

    ``event_sink`` is the typed-progress bridge: the vendored ``AIAgent``
    already emits ``(name, payload)`` pairs through its ``event_callback``
    (``run_agent.py``), and a caller that owns the SSE hub forwards them as
    ``CUSTOM`` frames so ``/qubit-chat``'s work panel sees real todos and
    subagent activity. It is optional and purely additive — without it the
    vendored event hooks fall back to their no-op defaults.

    ``progress_sink`` is the *tool-progress* bridge and the one that actually
    carries the work-panel events: the vendored runtime fires
    ``tool_progress_callback`` with the child-agent lifecycle
    (``subagent.*``, relayed by ``tools/delegate_tool_progress``) and with the
    ``tool.completed`` result from which the todo snapshot is projected. It is
    wired into ``AIAgent(tool_progress_callback=...)``; see
    ``voicebridge/events/chat_progress.py``. Also optional and additive.


    Returns the ``run_session``-shaped outcome on a real run, or ``None`` when
    the vendored runtime is not importable (hermetic tests, an install that
    never vendored the fork). ``None`` lets the caller fall back to the planner
    path, so this is purely additive.

    The agent's own broker/policy/confirmation/e-stop chain governs each tool
    (the terminal path's authority), so this never invents a parallel grant:
    sensitive tools stay confirm-gated exactly as they are in /chat.
    """
    try:
        from . import qubit_runtime as _qr  # type: ignore

        _qr.use_qubit_home(cfg)
        _qr.ensure_path()
        from run_agent import AIAgent  # type: ignore
        from qubit_cli.config import load_config  # type: ignore
        from qubit_cli.oneshot import (  # type: ignore
            _resolve_model_and_provider)
        from qubit_cli.runtime_provider import (  # type: ignore
            resolve_runtime_with_fallback)
        from qubit_cli.tools_config import _get_platform_tools  # type: ignore
    except Exception as exc:  # noqa: BLE001 - no vendored runtime: use planner
        _audit("session_vendored_unavailable", session=sid,
               error=str(exc)[:160])
        return None
    if check():
        raise AgentDenied("EMERGENCY_STOP",
                          "emergency stop latched before the agent turn")
    vcfg = load_config()
    # Model selection: a session PIN (the operator's explicit pick for THIS
    # conversation) wins over the voicebridge ``agent.model`` override, which in
    # turn wins over the vendored config's ``model`` slot. Resolution follows
    # the same helper the CLI one-shot uses so ``/chat`` behaviour is unchanged
    # when no pin exists.
    requested = str(session_model or "").strip() or _explicit_model(cfg) or ""
    runtime: Optional[Dict[str, Any]] = None
    model = requested
    if requested:
        # A LOCAL pin (qubit-spark17, qubit-brain, ...) must run on the local
        # Ollama endpoint. The vendored provider resolver only knows the
        # configured GLOBAL provider, so handing it a bare local name routed
        # the turn to the global API under the wrong model — the exact bug this
        # fixes. Resolve the pin through the one voicebridge resolver first.
        runtime = _local_model_runtime(cfg, requested)
    if runtime is not None:
        model = str(runtime.get("model") or requested)
    else:
        try:
            choice = _resolve_model_and_provider(
                vcfg, requested or None, None)
        except Exception as exc:  # noqa: BLE001 - fail closed to planner path
            _audit("session_vendored_runtime_error", session=sid,
                   error=str(exc)[:160])
            return None
        model = str(getattr(choice, "model", "") or "")
        # Fast bail when the vendored config resolves no model at all (an
        # unconfigured/home-less runtime): there is no tool-capable agent to
        # run, so return None for the caller's legacy fallback instead of
        # paying the provider-resolution failure. A configured runtime always
        # names a model.
        if not str(getattr(choice, "provider", "") or "").strip() \
                and not model.strip():
            _audit("session_vendored_unconfigured", session=sid)
            return None
        try:
            runtime, _fallback = resolve_runtime_with_fallback(
                vcfg, requested=str(getattr(choice, "provider", "") or ""),
                target_model=model or None)
        except Exception as exc:  # noqa: BLE001 - fail closed to planner path
            _audit("session_vendored_runtime_error", session=sid,
                   error=str(exc)[:160])
            return None
    try:
        toolsets = sorted(_get_platform_tools(vcfg, "cli"))
        # Name-level platform filtering cannot subtract a denied toolset from
        # composite defaults. Pass the denylist into canonical schema selection
        # as well, including deferred MCP discovery and delegated children.
        from agent.skill_utils import parse_config_string_list
        disabled_toolsets = parse_config_string_list(
            (vcfg.get("agent") or {}).get("disabled_toolsets"))
    except Exception as exc:  # noqa: BLE001 - never widen permissions on failure
        _audit("session_toolsets_error", session=sid, error=str(exc)[:160])
        return {"state": "FAILED", "code": "TOOLSET_CONFIG_ERROR",
                "detail": "could not resolve configured tool permissions",
                "session": sid, "session_id": sid}
    # The vendored runtime is resolvable, so the turn will really run: persist
    # the user row now (the caller that asked for ``persist_user`` owns neither
    # side). This keeps the transcript ordered user-then-assistant and leaves a
    # retryable user row even when the agent later fails.
    if persist_user:
        _persist_session_turn(store, sid, "user", text,
                              model=model or None)
    # MCP discovery is deliberately NOT a module-level side effect of
    # ``model_tools``: every entry point runs it at startup itself (cli.py,
    # tui_gateway/entry.py, gateway/run.py, acp_adapter/entry.py). This turn is
    # another such entry point. Without the call the AIAgent built below
    # snapshots its tool schema before the configured MCP servers register, so
    # /qubit-chat runs with zero ``mcp__*`` tools while /chat has them.
    # Idempotent (a no-op once this home's discovery thread has run) and
    # fail-open: a missing helper or a slow server never blocks past the bound.
    try:
        from qubit_cli.mcp_startup import (  # type: ignore
            ensure_mcp_discovery_before_agent_build)
        ensure_mcp_discovery_before_agent_build(
            logger=logging.getLogger(__name__),
            thread_name="qubit-chat-mcp-discovery")
    except Exception:  # noqa: BLE001 - discovery is best-effort, never fatal
        logging.getLogger(__name__).debug(
            "MCP discovery readiness check failed before agent build",
            exc_info=True)
    started = time.monotonic()
    agent = None
    # Turn-time provider fallback (billing/rate-limit → next chain entry):
    # hand the vendored agent the same chain the terminal /chat uses, read
    # from the already-loaded vendored config. Fail-open None (today's
    # behavior) when the config names no chain, so an unconfigured install
    # is identical to before. Clearing ``fallback_providers`` in config
    # restores the old behavior with no code change.
    try:
        from qubit_cli.fallback_config import get_fallback_chain  # type: ignore
        _fb_chain = get_fallback_chain(vcfg) or None
    except Exception:  # noqa: BLE001 - chain is advisory, never fatal
        _fb_chain = None
    try:
        agent = AIAgent(
            api_key=runtime.get("api_key"),
            base_url=runtime.get("base_url"),
            provider=runtime.get("provider"),
            requested_provider=runtime.get("requested_provider"),
            api_mode=runtime.get("api_mode"),
            model=model or runtime.get("model"),
            enabled_toolsets=toolsets,
            disabled_toolsets=disabled_toolsets,
            quiet_mode=True,
            verbose_logging=False,
            platform="cli",
            session_id=sid,
            credential_pool=runtime.get("credential_pool"),
            fallback_model=_fb_chain,
            reasoning_config=_vendor_reasoning_config(reasoning),
            clarify_callback=_vendor_clarify_callback,
            event_callback=event_sink,
            tool_progress_callback=progress_sink,
        )
        result = agent.run_conversation(text) or {}
    except AgentDenied:
        raise
    except Exception as exc:  # noqa: BLE001 - typed, redacted, no fabrication
        _audit("session_vendored_error", session=sid, error=str(exc)[:160])
        return {"state": "FAILED", "code": "MODEL_ERROR",
                "detail": _redact(str(exc))[:300], "session": sid,
                "session_id": sid}
    finally:
        if agent is not None:
            try:
                agent.close()
            except Exception:  # noqa: BLE001 - teardown is best-effort
                pass
    if check():
        raise AgentDenied("EMERGENCY_STOP",
                          "emergency stop latched mid-session")
    reply = str(result.get("final_response") or "").strip()
    tool_names = _vendor_tool_names(result)
    model_used = str(result.get("model") or model or "")
    provider_used = str(result.get("provider") or runtime.get("provider") or "")
    if not reply:
        return {"state": "FAILED", "code": "MODEL_UNAVAILABLE",
                "detail": "agent returned no text; nothing was fabricated",
                "session": sid, "session_id": sid,
                "steps": len(tool_names),
                "tool_used": tool_names[0] if tool_names else "",
                "elapsed_ms": int((time.monotonic() - started) * 1000)}
    from .textutil import sanitize

    reply_out = _redact(sanitize(reply))
    _persist_session_turn(store, sid, "assistant", reply_out,
                          model=model_used or None)
    _audit("session_vendored", session=sid, tools=len(tool_names),
           elapsed_ms=int((time.monotonic() - started) * 1000))
    receipt = {
        "runtime": "qubit-vendored-agent",
        "path": "voicebridge.agent.run_session",
        "provider": provider_used, "model": model_used,
        "tools": tool_names, "session_id": sid,
    }
    return {"state": "OK", "reply": reply_out, "session": sid,
            "session_id": sid, "steps": len(tool_names) or 1,
            "tool_used": tool_names[0] if tool_names else "",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "receipt": receipt}


def _vendor_reasoning_config(reasoning: Optional[str]) -> Any:
    """Map the /qubit-chat reasoning selector onto the vendored config.

    ``None``/invalid degrades to the configured default (never raises), matching
    the /chat path so a resumed-session preference cannot brick a worker turn.
    """
    try:
        from qubit_constants import (parse_reasoning_effort,
                                     resolve_reasoning_config)  # type: ignore

        if reasoning is not None and str(reasoning).strip():
            parsed = parse_reasoning_effort(str(reasoning))
            if parsed is not None:
                return parsed
        return resolve_reasoning_config({}, "")
    except Exception:  # noqa: BLE001 - preference is advisory
        return None


def _vendor_clarify_callback(question: str, choices=None,
                             multi_select: bool = False) -> str:
    """Headless clarify: no user at the console, so pick and continue.

    Mirrors the vendored one-shot callback — a worker turn must never stall on
    interactive input.
    """
    if choices:
        what = "subset" if multi_select else "option"
        return (f"[headless worker: no user available. Pick the best {what} "
                f"from {choices} using your own judgment and continue.]")
    return ("[headless worker: no user available. Make the most reasonable "
            "assumption you can and continue.]")


def run_session(cfg: Any, task_text: str, *, session: str,
                execute_tool: Optional[Callable[[str, Dict[str, Any]],
                                                Any]] = None,
                max_steps: int = 4,
                timeout_s: Optional[float] = None,
                estop_engaged: Optional[Callable[[], bool]] = None,
                cancelled: Optional[Callable[[], bool]] = None,
                progress_sink: Optional[Callable[..., None]] = None,
                model: Optional[str] = None
                ) -> Dict[str, Any]:
    """Run a bounded multi-step worker session natively.

    Prefers the vendored tool-capable ``AIAgent`` (the terminal /chat agent:
    full tool schema — skills, plugins, MCP) so a /qubit-chat build turn runs
    real tools, per docs/DECISIONS.md #26. When the vendored runtime is not
    importable it falls back to the planner path: plan once, and when the
    planner yields a tool proposal and an ``execute_tool`` backend is provided,
    run exactly that tool through the caller's broker path and summarize the
    observation with the model. Every step honors the emergency stop and the
    caller's cancellation flag; sensitive tool work stays behind the caller's
    own approval pipeline (this function never confirms anything itself).

    Session continuity: the session id is mapped through
    :func:`resolve_session_id` (an empty/voice-default alias converges on the
    canonical conversation, an explicit id is kept verbatim), the prior
    transcript is folded into the prompt through the canonical store and
    :mod:`voicebridge.context_policy`, and both turns are persisted back to
    that same store. A voice/PTT turn therefore continues a ``/qubit-chat``
    conversation and vice versa.
    """
    check = estop_engaged or (lambda: False)
    stop = cancelled or (lambda: False)
    budget = timeout_s or DEFAULT_TIMEOUT_S
    clean = (task_text or "").strip()[:MAX_TEXT_CHARS]
    # Canonical session id: an empty/voice-default alias maps to the one
    # canonical conversation; an explicit id is preserved exactly.
    sid = resolve_session_id(cfg, session)
    # Honor the session's persisted reasoning preference (the /qubit-chat
    # selector): a voice/PTT/worker turn must apply the same output budget the
    # text turn does. Unreadable/invalid preference degrades to None so a
    # corrupt value can never brick a voice turn (per-request callers still win
    # on agent.turn).
    try:
        from .chat_transport import session_reasoning

        reasoning = session_reasoning(cfg, sid)
    except Exception:  # noqa: BLE001 - preference is advisory
        reasoning = None
    if not clean:
        return {"state": "FAILED", "code": "MALFORMED",
                "detail": "session needs non-empty task text",
                "session": sid}
    if check():
        raise AgentDenied("EMERGENCY_STOP",
                          "emergency stop latched; session refused")
    if not _agent_enabled(cfg):
        raise AgentDenied("AGENT_DISABLED",
                          "agent sessions are off (agent.enabled=false)")
    # Single session/store authority (voice contract section 3): a voice /
    # orchestrator worker turn reads and writes the SAME canonical transcript
    # ``/qubit-chat``/terminal/CLI use, so the same session id is continuous
    # across surfaces. Both reads and writes are additive and non-fatal: a
    # store failure is audited and the session still returns its typed result.
    store = _canonical_session_store(cfg)
    prior_context, context_meta = _session_context(store, sid, cfg, clean)
    _persist_session_turn(store, sid, "user", clean,
                          model=_model(cfg) or None)
    started = time.monotonic()
    # Owner directive docs/DECISIONS.md #26: a /qubit-chat agent_task turn must
    # run the SAME tool-capable agent the terminal /chat path uses, not a
    # planner shim. The vendored AIAgent sends the full tool schema (skills,
    # plugins, MCP) and actually calls tools; the planner below is the
    # fallback for environments without the vendored runtime.
    # Model selection: the caller may pass the session's pin explicitly
    # (``launch_task(model=...)``); otherwise read it from the session store so
    # a voice/orchestrator turn on a pinned ``/qubit-chat`` session runs the
    # same model the panel shows. A missing/unreadable store leaves it ``None``
    # and the worker keeps its default resolution.
    if not (model or "").strip():
        try:
            from .chat_transport import _pinned_session_model

            model = _pinned_session_model(cfg, sid)
        except Exception:  # noqa: BLE001 - a preference, never fatal
            model = None
    vendored = _run_vendored_session(cfg, clean, sid, check=check,
                                     store=store, reasoning=reasoning,
                                     progress_sink=progress_sink,
                                     session_model=model)
    if vendored is not None:
        vendored.setdefault("session_id", sid)
        return vendored
    steps = 0
    tool_used = ""
    observation = ""
    try:
        from .planner import plan as _plan

        proposal = _plan(clean, cfg=cfg)
    except Exception as exc:
        _audit("session_plan_error", session=sid,
               error=str(exc)[:160])
        proposal = None
    if proposal is not None and execute_tool is not None \
            and not check() and not stop():
        steps += 1
        tool_used = str(proposal.tool)
        try:
            raw = execute_tool(proposal.tool, dict(proposal.arguments))
            observation = _redact(
                raw if isinstance(raw, str) else str(raw))[:4000]
        except Exception as exc:
            observation = f"tool {tool_used} failed: {exc}"[:500]
            _audit("session_tool_error", session=sid, tool=tool_used,
                   error=str(exc)[:160])
    if check():
        raise AgentDenied("EMERGENCY_STOP",
                          "emergency stop latched mid-session")
    if stop():
        return {"state": "CANCELLED", "code": "CANCELLED",
                "detail": "session cancelled", "session": sid,
                "steps": steps, "tool_used": tool_used}
    # Fold bounded prior context from the canonical transcript so a voice/PTT
    # turn continues the same conversation a ``/qubit-chat`` turn wrote.
    prompt = clean
    if prior_context:
        prompt = f"Recent conversation:\n{prior_context}\n\nUser: {clean}"
    if observation:
        prompt = (f"{prompt}\n\n---\nTool `{tool_used}` returned:\n"
                  f"{observation}\n---\nSummarize what was done directly "
                  "and concisely.")
    try:
        from .providers import gateway as _gateway

        res = _gateway.run_role(
            prompt, cfg, role="chat", model=_model(cfg),
            max_tokens=1024, timeout_s=min(budget, 300.0),
            local_mode="chat", should_stop=check, reasoning=reasoning)
    except Exception as exc:  # noqa: BLE001 - typed, redacted
        return {"state": "FAILED", "code": "MODEL_ERROR",
                "detail": _redact(str(exc))[:300], "session": sid,
                "steps": steps, "tool_used": tool_used}
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if not res.ok:
        if res.code in ("MODEL_UNAVAILABLE", "EMPTY_EMBEDDING") or (
                res.code == "PROVIDER_OUTAGE"
                and not str(res.message or "").strip()):
            return {"state": "FAILED", "code": "MODEL_UNAVAILABLE",
                    "detail": "local model returned no text; nothing was "
                              "fabricated",
                    "session": sid, "steps": steps,
                    "tool_used": tool_used, "elapsed_ms": elapsed_ms}
        return {"state": "FAILED", "code": "MODEL_ERROR",
                "detail": _redact(res.message or "model provider refused")[:300],
                "session": sid, "steps": steps,
                "tool_used": tool_used, "elapsed_ms": elapsed_ms}
    reply = str(res.text or "")
    if not reply.strip():
        return {"state": "FAILED", "code": "MODEL_UNAVAILABLE",
                "detail": "local model returned no text; nothing was "
                          "fabricated",
                "session": sid, "steps": steps, "tool_used": tool_used,
                "elapsed_ms": elapsed_ms}
    _audit("session", session=sid, steps=steps, tool=tool_used,
           elapsed_ms=elapsed_ms)
    from .textutil import sanitize

    reply_out = _redact(sanitize(reply.strip()))
    _persist_session_turn(store, sid, "assistant", reply_out,
                          model=_model(cfg) or None)
    receipt = _provider_receipt(res, runtime="qubit-native",
                               path="voicebridge.agent.run_session")
    receipt.setdefault("session_id", sid)
    if context_meta:
        receipt["context"] = dict(context_meta)
    return {"state": "OK", "reply": reply_out,
            "session": sid, "session_id": sid, "steps": steps,
            "tool_used": tool_used,
            "elapsed_ms": elapsed_ms,
            "receipt": receipt}


def run_agent_turn(cfg: Any, text: str, *, session: str,
                   reasoning: Optional[str] = None,
                   estop_engaged: Optional[Callable[[], bool]] = None,
                   event_sink: Optional[Callable[[str, dict], None]] = None,
                   progress_sink: Optional[Callable[..., None]] = None
                   ) -> "Optional[Dict[str, Any]]":
    """Run one general turn through the tool-capable vendored agent.

    Owner directive docs/DECISIONS.md #26: a ``/qubit-chat`` turn that falls
    through to the model must run the SAME ``run_agent.AIAgent`` the ``/chat``
    terminal drives — the model receives the full tool schema (skills, plugins,
    MCP) and can call tools — never a bare ``providers.gateway`` text call.
    This is the seam :mod:`voicebridge.chat_transport` uses for general turns;
    the deterministic fast paths (slash/facts/affirmatives/local/registry/web/
    build task) keep their own branches and never reach here.

    Returns the ``run_session``-shaped outcome (``state``/``reply``/``code``/
    ``receipt``) on a real run, or ``None`` when no tool-capable vendored
    runtime/model is available (no vendored import, no configured model) so the
    caller can fall back to the legacy model path verbatim. The user row and
    the assistant reply are persisted here via the vendored ``persist_user``
    hook; the caller owns neither when this returns a dict.

    The agent's own broker/policy/confirmation/e-stop chain governs each tool
    (the terminal path's authority), so sensitive tools stay confirm-gated
    exactly as in /chat; ``estop_engaged`` is honored before and after the run.
    """
    check = estop_engaged or (lambda: False)
    if check():
        raise AgentDenied("EMERGENCY_STOP",
                          "emergency stop latched; agent turn refused")
    if not _agent_enabled(cfg):
        raise AgentDenied("AGENT_DISABLED",
                          "agent turns are off (agent.enabled=false)")
    clean = (text or "").strip()[:MAX_TEXT_CHARS]
    if not clean:
        return None
    sid = resolve_session_id(cfg, session)
    if reasoning is None:
        try:
            from .chat_transport import session_reasoning

            reasoning = session_reasoning(cfg, sid)
        except Exception:  # noqa: BLE001 - preference is advisory
            reasoning = None
    store = _canonical_session_store(cfg)
    # The session's stored model is the operator's explicit pick for THIS
    # conversation (the ``/qubit-chat`` panel write). Pass it through so the
    # worker actually runs the pinned model instead of the global default.
    session_model: Optional[str] = None
    try:
        from .chat_transport import _pinned_session_model

        session_model = _pinned_session_model(cfg, sid)
    except Exception:  # noqa: BLE001 - a preference, never fatal
        session_model = None
    return _run_vendored_session(cfg, clean, sid, check=check, store=store,
                                 reasoning=reasoning, persist_user=True,
                                 event_sink=event_sink,
                                 progress_sink=progress_sink,
                                 session_model=session_model)


def refuse_estop() -> Dict[str, Any]:
    """Typed refusal while the emergency stop is latched."""
    return {"state": "ESTOP", "code": "AGENT_ESTOP",
            "detail": "emergency stop latched; reset it before running "
                      "agent turns",
            "owner": "qubit-policy",
            "rollback": "nothing started; nothing to roll back"}
