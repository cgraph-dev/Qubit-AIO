"""Confirm-gated, tool-executing tasks for the voice surface.

Item 1: a spoken actionable request must actually *do* the thing, not just
narrate it. This module reuses the existing canonical pieces rather than
inventing a second executor:

* a deterministic fast path resolves a small set of local actions and bounded
  web intents (search/fetch) with no model round-trip;
* otherwise the typed L2 planner (:mod:`voicebridge.planner`) proposes one
  whitelisted tool (``local.*`` / ``web.*``) and
  :func:`voicebridge.agent.run_session` runs the bounded turn, calling back here
  for exactly that proposal;
* the callback dispatches the tool through the ONE capability broker
  (:func:`voicebridge.broker.build_host_broker` + ``register_web_tools``) — the
  same junction (registry/policy/e-stop/confirmation/audit) the orchestrator's
  ``_agent_tool`` uses.

Dispatch source: an unconfirmed task dispatches as ``source="model"`` (mutating/
destructive tools fail closed); a caller-confirmed task (the user explicitly
authorized THIS task) dispatches as ``source="user"`` and, for a spec whose
``needs_confirmation`` is true, mints a single-use, target-bound user
confirmation the broker consumes (required for the DESTRUCTIVE two-step).

The caller (``VoiceEditService.task``) owns the user-facing confirmation gate
and the audit; this module performs no egress beyond the broker-guarded tool.
"""
from __future__ import annotations

import json
import re
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


def _binding_for(broker: Any, name: str) -> Any:
    """The registered binding for ``name`` (public accessor, then fallback)."""
    getter = getattr(broker, "get", None)
    if callable(getter):
        try:
            binding = getter(name)
        except Exception:  # noqa: BLE001 - a broken probe must not mint
            binding = None
        if binding is not None:
            return binding
    bindings = getattr(broker, "_bindings", None)
    if isinstance(bindings, dict):
        return bindings.get(name)
    return None


def _needs_confirmation(broker: Any, name: str) -> bool:
    """True only for a KNOWN spec whose ``needs_confirmation`` is set.

    Conservative: an unknown/unreadable binding returns False so no
    confirmation is minted for a capability the broker does not declare.
    """
    binding = _binding_for(broker, name)
    spec = getattr(binding, "spec", None)
    return bool(getattr(spec, "needs_confirmation", False))


def execute_tool(cfg: Any, tool: str, args: Optional[dict], *,
                 session: str = "", confirmed: bool = False) -> str:
    """Dispatch one task tool behind the shared broker.

    Unconfirmed (``confirmed=False``): ``source="model"`` — a mutating tool is
    refused while reversible/read-only tools run. Confirmed
    (``confirmed=True``): the user authorized this task, so dispatch as
    ``source="user"`` and mint a user confirmation for a spec that requires one
    (the broker consumes exactly that capability+args grant).

    Unknown tools fail closed with ``ValueError``; a denied broker result
    raises ``PermissionError``.
    """
    name = str(tool or "")
    if name.startswith("local."):
        from ..local import actions as _la
        if _la.ACTIONS.get(name[len("local."):]) is None:
            raise ValueError(f"unknown task tool {tool!r}")
    elif not name.startswith("web."):
        raise ValueError(f"unknown task tool {tool!r}")
    broker = _broker(cfg)
    call_args = dict(args or {})
    source = "user" if confirmed else "model"
    confirmation_id: Optional[str] = None
    if confirmed and _needs_confirmation(broker, name):
        # A DESTRUCTIVE spec refuses the broker's implicit issuance, and even a
        # MUTATING one should carry an explicit, user-issued grant. The store
        # itself refuses issuer='model'; 'user' is the authenticated human
        # surface the caller already confirmed.
        issued = broker.issue_confirmation(name, call_args, issued_by="user")
        confirmation_id = getattr(issued, "id", None)
    kwargs: dict = {"source": source, "session": session or None}
    if confirmation_id:
        kwargs["confirmation_id"] = confirmation_id
    res = broker.execute(name, call_args, **kwargs)
    if getattr(res, "denied", ""):
        raise PermissionError(f"{res.denied}:{name}")
    if not getattr(res, "ok", False):
        raise RuntimeError(getattr(res, "error", "") or f"{name} failed")
    return str(getattr(res, "output", "") or "")


# ---------------------------------------------------------------------------
# Deterministic intent resolution (bounded, no model)
# ---------------------------------------------------------------------------
_FILLER_RE = re.compile(
    r"^(?:hey\s+qubit[,\s]+|qubit[,\s]+|please\s+|can you\s+|could you\s+|"
    r"would you\s+|just\s+)+", re.I)

_WEB_SEARCH_RES = (
    re.compile(r"^search\s+(?:the\s+)?(?:web|internet|online)\s+for\s+(?P<q>.+)$",
               re.I),
    re.compile(r"^search\s+for\s+(?P<q>.+?)\s+"
               r"(?:online|on\s+the\s+(?:web|internet))$", re.I),
    re.compile(r"^look\s+up\s+(?P<q>.+?)\s+"
               r"(?:online|on\s+the\s+(?:web|internet))$", re.I),
    re.compile(r"^google\s+(?P<q>.+)$", re.I),
)

_WEB_FETCH_RE = re.compile(
    r"^(?:fetch|get|read|open)\s+(?:the\s+)?(?:url|page|link)\s+"
    r"(?P<u>https?://\S+)$", re.I)
_BARE_URL_RE = re.compile(r"^(?P<u>https?://\S+)$", re.I)


def _clean_query(raw: str) -> str:
    q = (raw or "").strip()
    q = re.sub(r"^(?:for|about|on)\s+", "", q, flags=re.I)
    q = re.sub(r"\s+(?:for me|please|now)\s*$", "", q, flags=re.I)
    return q.strip(" .,!?;:\"'")


def _clean_url(raw: str) -> str:
    u = (raw or "").strip().strip("\"'")
    u = u.rstrip(".,;:!?)")
    if not re.match(r"^https?://", u, re.I):
        return ""
    return u


def _match_web(text: str) -> Optional[dict]:
    s = _FILLER_RE.sub("", text or "").strip()
    if not s:
        return None
    m = _WEB_FETCH_RE.match(s) or _BARE_URL_RE.match(s)
    if m is not None:
        url = _clean_url(m.group("u"))
        if url:
            return {"tool": "web.fetch", "args": {"url": url}}
    for rx in _WEB_SEARCH_RES:
        m = rx.match(s)
        if m is not None:
            query = _clean_query(m.group("q"))
            if query:
                return {"tool": "web.search", "args": {"query": query}}
    return None


def propose_tool(cfg: Any, text: str) -> Optional[dict]:
    """Resolve the deterministic tool a task will run, or None.

    The local-action match (the SAME :func:`voicebridge.local.actions.match`
    the shared classifier uses) takes priority; a bounded web intent
    (``web.search`` / ``web.fetch``) is the fallback. Returns
    ``{"tool", "args"}`` so a caller can preview exactly what will run; the
    model planner is never consulted here.
    """
    clean = (text or "").strip()
    if not clean:
        return None
    try:
        from .custom_commands import match_alias
        hit = match_alias(clean, cfg)
    except Exception:  # noqa: BLE001 - aliases never break built-ins
        hit = None
    if hit is not None and _la_has_action(str(hit.get("name") or "")):
        return {"tool": f"local.{hit['name']}",
                "args": dict(hit.get("params") or {})}
    try:
        from ..local import actions as _la
        match = _la.match(clean)
    except Exception:  # noqa: BLE001 - a failed matcher never guesses
        match = None
    if match is not None and _la_has_action(match.name):
        return {"tool": f"local.{match.name}", "args": dict(match.params or {})}
    return _match_web(clean)


def run_tool_task(cfg: Any, text: str, *, session: str = "",
                  confirmed: bool = False,
                  estop_engaged: Optional[Callable[[], bool]] = None,
                  execute_tool_fn: Optional[Callable[[str, dict], Any]] = None
                  ) -> dict:
    """Plan + run one bounded, broker-guarded task turn.

    Deterministic-first: a local action or a bounded web intent (websites
    search/fetch) runs with no model round-trip; a free-form request falls
    through to the typed planner + :func:`voicebridge.agent.run_session`.
    Either way the tool runs through the ONE broker (and the turns persist to
    the one canonical session store).

    ``confirmed=True`` (the user explicitly authorized this task) flows down to
    the broker dispatch as ``source="user"`` with a user-issued confirmation
    for specs that require one.

    Returns a ``run_session``-shaped result dict (``state``/``reply``/
    ``tool_used``). Raises ``AgentDenied`` only from the free-form fallback.
    """
    from ..agent import resolve_session_id
    clean = (text or "").strip()
    sid = resolve_session_id(cfg, session or None)

    # Deterministic fast path: a recognized local action or bounded web intent
    # needs no model.
    if execute_tool_fn is None:
        proposal = propose_tool(cfg, clean)
        if proposal is not None:
            tool = str(proposal.get("tool") or "")
            args = dict(proposal.get("args") or {})
            try:
                output = execute_tool(cfg, tool, args, session=sid,
                                      confirmed=confirmed)
            except Exception as exc:
                return {"state": "FAILED", "code": _deny_code(exc),
                        "detail": str(exc)[:200], "tool_used": tool,
                        "session": sid, "session_id": sid}
            if tool.startswith("web."):
                outcome = _web_outcome(tool, output)
                outcome.update({"tool_used": tool, "session": sid,
                                "session_id": sid, "steps": 1})
                _persist(cfg, sid, clean, str(outcome.get("reply", "")))
                return outcome
            desc = str(getattr(_la_action(tool[len("local."):]), "desc", "") or "")
            reply = (str(output or "").strip()
                     or (f"Done: {desc}." if desc else f"Done: {tool}."))
            _persist(cfg, sid, clean, reply)
            return {"state": "OK", "reply": reply, "tool_used": tool,
                    "session": sid, "session_id": sid, "steps": 1}

    from ..agent import run_session
    fn = execute_tool_fn or (
        lambda tool, args: execute_tool(cfg, tool, args, session=sid,
                                        confirmed=confirmed))
    return run_session(cfg, clean, session=sid, execute_tool=fn,
                       estop_engaged=estop_engaged)


# ---------------------------------------------------------------------------
# Web tool result shaping (honest: a typed UNAVAILABLE is a FAILED, never a
# fabricated result)
# ---------------------------------------------------------------------------
def _web_outcome(tool: str, output: Any) -> dict:
    raw = str(output or "")
    payload: Optional[dict] = None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, dict):
        payload = parsed
    if payload is None:
        return {"state": "OK", "reply": raw.strip()[:500] or f"Done: {tool}."}
    state = str(payload.get("state") or "").upper()
    code = str(payload.get("code") or "")
    if state and state != "OK":
        return {"state": "FAILED", "code": code or "WEB_TOOL_UNAVAILABLE",
                "detail": str(payload.get("detail") or "")[:200],
                "reply": _web_failure_reply(tool, code)}
    return {"state": "OK", "reply": _web_ok_reply(tool, payload)}


def _web_failure_reply(tool: str, code: str) -> str:
    if code == "WEB_SEARCH_UNAVAILABLE":
        return "Web search is unavailable: no search provider is configured."
    label = "Web search" if tool == "web.search" else "Web fetch"
    return f"{label} failed ({code or 'unavailable'})."


def _web_ok_reply(tool: str, payload: dict) -> str:
    if tool == "web.search":
        count = payload.get("count")
        if count is None and isinstance(payload.get("results"), list):
            count = len(payload["results"])
        if isinstance(count, int) and not isinstance(count, bool):
            return f"Found {count} result{'s' if count != 1 else ''}."
        return "Search complete."
    if tool == "web.fetch":
        title = str(payload.get("title") or "").strip()
        url = str(payload.get("url") or "").strip()
        text = str(payload.get("text") or "").strip()
        lead = title or url
        if lead and text:
            return f"{lead}: {text[:280]}"
        return lead or text[:300] or "Page fetched."
    return f"Done: {tool}."


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


__all__ = ["run_tool_task", "execute_tool", "propose_tool", "reset_broker"]
