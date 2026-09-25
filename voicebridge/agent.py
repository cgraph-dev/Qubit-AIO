"""Qubit-native agent turns.

One task in, one reply out, through Qubit's own local model path — no
supervised external runtime, no second agent process, no second memory.
Timeout, cancellation (via the caller's estop probe), redaction, audit,
and typed receipts are enforced here so every caller (voice, schedules,
orchestrator, CLI task path) shares one execution contract.
"""
from __future__ import annotations

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


def run_session(cfg: Any, task_text: str, *, session: str,
                execute_tool: Optional[Callable[[str, Dict[str, Any]],
                                                Any]] = None,
                max_steps: int = 4,
                timeout_s: Optional[float] = None,
                estop_engaged: Optional[Callable[[], bool]] = None,
                cancelled: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    """Run a bounded multi-step worker session natively.

    Plan once with the validated planner; when it yields a tool proposal
    and an ``execute_tool`` backend is provided, run exactly that tool
    through the caller's broker path and summarize the observation with
    the model. Otherwise answer directly. Every step honors the
    emergency stop and the caller's cancellation flag; sensitive tool
    work stays behind the caller's own approval pipeline (this function
    never confirms anything itself).

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
    steps = 0
    tool_used = ""
    observation = ""
    try:
        from .planner import plan as _plan

        proposal = _plan(clean)
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


def refuse_estop() -> Dict[str, Any]:
    """Typed refusal while the emergency stop is latched."""
    return {"state": "ESTOP", "code": "AGENT_ESTOP",
            "detail": "emergency stop latched; reset it before running "
                      "agent turns",
            "owner": "qubit-policy",
            "rollback": "nothing started; nothing to roll back"}
