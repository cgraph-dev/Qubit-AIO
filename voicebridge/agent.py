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
    try:
        return str(cfg.get("agent", "model", default="") or
                   "qubit-brain4")
    except Exception:
        return "qubit-brain4"


def turn(cfg: Any, text: str, *, session: str = "qubit",
         timeout_s: Optional[float] = None,
         estop_engaged: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    """Run one agent turn natively and return the reply.

    Raises AgentDenied before execution on emergency stop / disabled
    agent. Model failure, timeout, and empty replies are typed
    ``ok=false`` results — never an exception, never fabricated text.
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
    budget = timeout_s or DEFAULT_TIMEOUT_S
    started = time.monotonic()
    try:
        from .local_llm import chat_local

        reply = chat_local(clean, model=_model(cfg),
                           timeout_s=min(budget, 300.0), max_tokens=1024)
    except Exception as exc:
        _audit("turn_error", session=session, error=str(exc)[:160])
        return {"ok": False, "code": "MODEL_ERROR",
                "error": _redact(str(exc))[:300], "session": session}
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if check():
        return {"ok": False, "code": "CANCELLED",
                "error": "cancelled during execution", "session": session,
                "elapsed_ms": elapsed_ms}
    if not (reply or "").strip():
        _audit("turn_empty", session=session, elapsed_ms=elapsed_ms)
        return {"ok": False, "code": "MODEL_UNAVAILABLE",
                "error": "local model returned no text; it may be "
                         "unreachable — nothing was fabricated",
                "session": session, "elapsed_ms": elapsed_ms}
    _audit("turn", session=session, elapsed_ms=elapsed_ms, ok=True)
    from .textutil import sanitize

    return {"ok": True, "reply": _redact(sanitize(reply.strip())),
            "session": session, "elapsed_ms": elapsed_ms,
            "receipt": {"runtime": "qubit-native",
                        "model": _model(cfg),
                        "path": "voicebridge.agent.turn"}}


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
    """
    check = estop_engaged or (lambda: False)
    stop = cancelled or (lambda: False)
    budget = timeout_s or DEFAULT_TIMEOUT_S
    clean = (task_text or "").strip()[:MAX_TEXT_CHARS]
    if not clean:
        return {"state": "FAILED", "code": "MALFORMED",
                "detail": "session needs non-empty task text",
                "session": session}
    if check():
        raise AgentDenied("EMERGENCY_STOP",
                          "emergency stop latched; session refused")
    if not _agent_enabled(cfg):
        raise AgentDenied("AGENT_DISABLED",
                          "agent sessions are off (agent.enabled=false)")
    started = time.monotonic()
    steps = 0
    tool_used = ""
    observation = ""
    try:
        from .planner import plan as _plan

        proposal = _plan(clean)
    except Exception as exc:
        _audit("session_plan_error", session=session,
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
            _audit("session_tool_error", session=session, tool=tool_used,
                   error=str(exc)[:160])
    if check():
        raise AgentDenied("EMERGENCY_STOP",
                          "emergency stop latched mid-session")
    if stop():
        return {"state": "CANCELLED", "code": "CANCELLED",
                "detail": "session cancelled", "session": session,
                "steps": steps, "tool_used": tool_used}
    prompt = clean
    if observation:
        prompt = (f"{clean}\n\n---\nTool `{tool_used}` returned:\n"
                  f"{observation}\n---\nSummarize what was done directly "
                  "and concisely.")
    try:
        from .local_llm import chat_local

        reply = chat_local(prompt, model=_model(cfg),
                           timeout_s=min(budget, 300.0), max_tokens=1024)
    except Exception as exc:
        return {"state": "FAILED", "code": "MODEL_ERROR",
                "detail": _redact(str(exc))[:300], "session": session,
                "steps": steps, "tool_used": tool_used}
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if not (reply or "").strip():
        return {"state": "FAILED", "code": "MODEL_UNAVAILABLE",
                "detail": "local model returned no text; nothing was "
                          "fabricated",
                "session": session, "steps": steps, "tool_used": tool_used,
                "elapsed_ms": elapsed_ms}
    _audit("session", session=session, steps=steps, tool=tool_used,
           elapsed_ms=elapsed_ms)
    from .textutil import sanitize

    return {"state": "OK", "reply": _redact(sanitize(reply.strip())),
            "session": session, "steps": steps, "tool_used": tool_used,
            "elapsed_ms": elapsed_ms,
            "receipt": {"runtime": "qubit-native", "model": _model(cfg),
                        "path": "voicebridge.agent.run_session"}}


def refuse_estop() -> Dict[str, Any]:
    """Typed refusal while the emergency stop is latched."""
    return {"state": "ESTOP", "code": "AGENT_ESTOP",
            "detail": "emergency stop latched; reset it before running "
                      "agent turns",
            "owner": "qubit-policy",
            "rollback": "nothing started; nothing to roll back"}
