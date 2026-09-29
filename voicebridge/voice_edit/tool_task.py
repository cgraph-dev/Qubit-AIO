"""Confirm-gated, tool-executing tasks for the voice surface.

Item 1: a spoken actionable request must actually *do* the thing, not just
narrate it. This module reuses the existing canonical pieces rather than
inventing a second executor:

* the typed L2 planner (:mod:`voicebridge.planner`) proposes one whitelisted
  tool (``local.*`` / ``web.*``) from the utterance;
* :func:`voicebridge.agent.run_session` runs the bounded turn and, for exactly
  that proposal, calls back here;
* the callback dispatches the tool through the ONE capability broker
  (:func:`voicebridge.broker.build_host_broker` + ``register_web_tools``) with
  ``source="model"`` — the same junction (registry/policy/e-stop/confirmation/
  audit) the orchestrator's ``_agent_tool`` uses, so mutating/destructive tools
  fail closed and read-only/reversible ones run.

The caller (``VoiceEditService.task``) owns the user-facing confirmation gate
and the audit; this module performs no confirmation of its own and no egress
beyond the broker-guarded tool.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Optional

_BROKER: Any = None
_LOCK = threading.Lock()


def reset_broker() -> None:
    """Test seam: drop the process-wide broker so the next call rebuilds it."""
    global _BROKER
    with _LOCK:
        _BROKER = None


def _broker(cfg: Any) -> Any:
    """The one process-wide host broker (local.* + read-only web.*)."""
    global _BROKER
    with _LOCK:
        if _BROKER is None:
            from ..broker import build_host_broker, register_web_tools
            broker = build_host_broker(cfg=cfg)
            register_web_tools(broker, cfg=cfg)
            _BROKER = broker
        return _BROKER


def execute_tool(cfg: Any, tool: str, args: Optional[dict], *,
                 session: str = "") -> str:
    """Dispatch one planner-proposed tool behind the shared broker.

    Mirrors the orchestrator's ``_agent_tool`` contract exactly: unknown tools
    fail closed; the broker's decision (deny/confirm) applies; a model-origin
    mutating tool is refused while reversible/read-only tools run.
    """
    name = str(tool or "")
    if name.startswith("local."):
        from ..local import actions as _la
        if _la.ACTIONS.get(name[len("local."):]) is None:
            raise ValueError(f"unknown task tool {tool!r}")
    elif not name.startswith("web."):
        raise ValueError(f"unknown task tool {tool!r}")
    broker = _broker(cfg)
    res = broker.execute(name, dict(args or {}), source="model",
                         session=session or None)
    if getattr(res, "denied", ""):
        raise PermissionError(f"{res.denied}:{name}")
    if not getattr(res, "ok", False):
        raise RuntimeError(getattr(res, "error", "") or f"{name} failed")
    return str(getattr(res, "output", "") or "")


def run_tool_task(cfg: Any, text: str, *, session: str = "",
                  estop_engaged: Optional[Callable[[], bool]] = None,
                  execute_tool_fn: Optional[Callable[[str, dict], Any]] = None
                  ) -> dict:
    """Plan + run one bounded, broker-guarded task turn.

    Local-first: a deterministic local action (the SAME
    :func:`voicebridge.local.actions.match` the shared intent classifier uses)
    runs without any model round-trip; a free-form request falls through to the
    typed planner + :func:`voicebridge.agent.run_session`. Either way the tool
    runs through the ONE broker and the turns persist to the one canonical
    session store.

    Returns a ``run_session``-shaped result dict (``state``/``reply``/
    ``tool_used``). Raises ``AgentDenied`` only from the free-form fallback.
    """
    from ..agent import resolve_session_id
    clean = (text or "").strip()
    sid = resolve_session_id(cfg, session or None)

    # Deterministic fast path: a recognized local action needs no model.
    if execute_tool_fn is None:
        try:
            from ..local import actions as _la
            match = _la.match(clean)
        except Exception:
            match = None
        if match is not None and _la_has_action(match.name):
            tool = f"local.{match.name}"
            try:
                output = execute_tool(cfg, tool, dict(match.params or {}),
                                      session=sid)
            except Exception as exc:
                return {"state": "FAILED", "code": _deny_code(exc),
                        "detail": str(exc)[:200], "tool_used": tool,
                        "session": sid, "session_id": sid}
            desc = str(getattr(_la_action(match.name), "desc", "") or "")
            reply = (str(output or "").strip()
                     or (f"Done: {desc}." if desc else f"Done: {tool}."))
            _persist(cfg, sid, clean, reply)
            return {"state": "OK", "reply": reply, "tool_used": tool,
                    "session": sid, "session_id": sid, "steps": 1}

    from ..agent import run_session
    fn = execute_tool_fn or (
        lambda tool, args: execute_tool(cfg, tool, args, session=sid))
    return run_session(cfg, clean, session=sid, execute_tool=fn,
                       estop_engaged=estop_engaged)


def _la_action(name: str) -> Any:
    from ..local import actions as _la
    return _la.ACTIONS.get(name)


def _la_has_action(name: str) -> bool:
    act = _la_action(name)
    return act is not None and getattr(act, "fn", None) is not None


def _deny_code(exc: BaseException) -> str:
    text = str(exc) or type(exc).__name__
    head = text.split(":", 1)[0].strip()
    return head[:64] or "VOICE_EDIT_TASK_FAILED"


def _persist(cfg: Any, sid: str, question: str, answer: str) -> None:
    """Best-effort persistence onto the one canonical session store."""
    try:
        from ..agent import _canonical_session_store, _persist_session_turn
        store = _canonical_session_store(cfg)
        _persist_session_turn(store, sid, "user", question)
        _persist_session_turn(store, sid, "assistant", answer)
    except Exception:
        pass


__all__ = ["run_tool_task", "execute_tool", "reset_broker"]
