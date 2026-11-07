"""Approval and clarify routes on Qubit's one durable approval journal.

UPSTREAM CONTRACT (read at UPSTREAM_PIN)
---------------------------------------------------------------------------
``GET  /api/approval/pending``   ``routes.py:26673-26698`` →
                                 ``{pending: entry|null, pending_count: int}``
``POST /api/approval/respond``   ``routes.py:34054-34110`` + ``:21401`` →
                                 body ``{session_id, choice: once|session|always|
                                 deny, approval_id, yolo?}``
``GET  /api/clarify/pending``    ``:26791-26796`` → ``{pending: entry|null}``
``POST /api/clarify/respond``    ``:34363-34397`` → ``{ok:true, response}`` /
                                 409 ``{ok:false, error, stale:true}``

The approval card renders ``pending.description``, ``pending.command`` and the
``pattern_key``/``pattern_keys`` list, and keys the response on
``pending.approval_id`` (``static/messages.js:8723-8741, 8991-9001``). Those are
the fields the translation must produce.

WHERE THE DELEGATION LANDS
---------------------------------------------------------------------------
:class:`voicebridge.approvals.ApprovalJournal` — the one append-only,
settle-once journal the CLI ``qubit approvals``, voice yes/no, the dashboard
pending-approvals banner and ``/api/ops/approvals`` all share. Qubit records
``{id, capability, summary, created_at, expires_at, state, meta}``.

TWO HONEST CONTRACT DIFFERENCES
---------------------------------------------------------------------------
1. **One global queue.** Upstream keeps a per-session queue (``_pending[sid]``);
   Qubit's journal is global — an approval is a system-wide confirmation, not a
   chat-session artifact. So ``pending`` is the live head of the journal, and the
   shim reports the record's own ``session_id`` from ``meta`` when a producer
   recorded one, instead of pretending an approval is bound to the polling
   session. ``pending_count`` is the real live count.
2. **Clarify is a GAP.** Qubit's ``chat_transport`` has a ``clarify`` routing
   decision, not a parked-question queue an agent blocks on (upstream's
   ``api/clarify.py``). Answering ``{pending: null}`` would be a lie that reads
   as "the agent is not waiting"; the typed 501 says what is missing.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import (UPSTREAM_PIN, body_of, gap, redact, str_param)

#: Choices upstream's card can send, mapped onto the journal's decision
#: vocabulary. ``approve`` is the journal's word for "the user said yes"; the
#: three upstream scopes differ only in how long the grant lasts, and Qubit's
#: journal settles this ONE record either way.
_APPROVE_CHOICES = ("once", "session", "always")
_DECISIONS = {"deny": "deny"}
for _c in _APPROVE_CHOICES:
    _DECISIONS[_c] = "approve"

#: The journal is the dashboard's own file, read with the same TTL the dashboard
#: uses so an approval staged by the CLI is visible to a polling card.
JOURNAL_TTL_S = 120.0


def _journal(cfg: Any) -> Any:
    """The SHARED journal. Raises → the caller types the refusal."""
    from ..approvals import ApprovalJournal

    return ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl",
                           default_ttl_s=JOURNAL_TTL_S)


def _entry(record: Any) -> Dict[str, Any]:
    """One journal record → upstream's approval card payload."""
    meta = getattr(record, "meta", None)
    meta = meta if isinstance(meta, dict) else {}
    capability = str(getattr(record, "capability", "") or "")
    summary = redact(getattr(record, "summary", "") or "")[:400]
    # Upstream renders ``description`` (what is being asked) and ``command``
    # (what would run). Qubit's record names the capability and, when the
    # producer recorded them, the tool/command argv — so both are real facts,
    # never a synthesized command line.
    command = ""
    for key in ("command", "argv", "tool", "path"):
        value = meta.get(key)
        if isinstance(value, (list, tuple)):
            value = " ".join(str(v) for v in value)
        if value:
            command = str(value)[:400]
            break
    pattern_key = str(meta.get("pattern_key") or "") or None
    out: Dict[str, Any] = {
        "approval_id": str(getattr(record, "id", "") or ""),
        "description": summary or capability,
        "command": command,
        "capability": capability,
        "created_at": getattr(record, "created_at", None),
        "expires_at": getattr(record, "expires_at", None),
        "state": str(getattr(record, "state", "") or ""),
        "session_id": str(meta.get("session_id") or meta.get("session") or "") or None,
        "pattern_key": pattern_key,
        "pattern_keys": list(meta.get("pattern_keys") or
                             ([pattern_key] if pattern_key else [])),
    }
    return out


def _pending(cfg: Any) -> Tuple[Optional[List[Dict[str, Any]]], Dict[str, Any]]:
    """(entries, fault). ``entries is None`` means the journal was unreadable."""
    try:
        journal = _journal(cfg)
        records = list(journal.pending())
    except Exception as exc:  # noqa: BLE001 - a read surface must not 500 bare
        return None, {
            "ok": False, "state": "UNAVAILABLE",
            "code": "APPROVAL_JOURNAL_UNAVAILABLE",
            "detail": f"the shared approval journal could not be read: "
                      f"{type(exc).__name__}: {exc}",
            "pending": None, "pending_count": 0,
            "upstream": UPSTREAM_PIN,
        }
    return [_entry(r) for r in records], {}


def approval_pending(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/wb/approval/pending — the head of the shared journal."""
    entries, fault = _pending(cfg)
    if entries is None:
        return 503, fault
    session_id = str_param(q, "session_id")
    # The journal is global. When the caller names a session, entries a producer
    # bound to it are preferred for the head, but nothing is hidden: a Qubit
    # approval with no session binding still needs a human.
    bound = [e for e in entries if session_id and e.get("session_id") == session_id]
    head = (bound or entries or [None])[0]
    return 200, {
        "pending": head,
        "pending_count": len(entries),
        # Additive honesty for the card's "1 of N" counter and for a UI that
        # wants to show session-matched entries first.
        "all_pending": entries,
        "session_bound_count": len(bound),
        "scope": "global-approval-journal",
        "upstream": UPSTREAM_PIN,
    }


def approval_respond(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                     p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/wb/approval/respond — settle one approval, exactly once."""
    body = body_of(b)
    session_id = str(body.get("session_id") or "").strip()
    if not session_id:
        return 400, {"error": "session_id is required"}
    choice = str(body.get("choice") or "deny").strip()
    if choice not in _DECISIONS:
        return 400, {"error": f"Invalid choice: {choice}"}
    if body.get("yolo") is True:
        return gap("SESSION_YOLO",
                   detail=("approve or deny this one request; a standing "
                           "auto-approve is a policy change made by the "
                           "operator."),
                   session_id=session_id)
    approval_id = str(body.get("approval_id") or "").strip()
    if not approval_id:
        # Upstream settles the queue HEAD when no id is sent. Resolve the head
        # explicitly so the journal call is by id (settle-once semantics).
        entries, fault = _pending(cfg)
        if entries is None:
            return 503, fault
        if not entries:
            return 409, {"ok": False, "stale": True,
                         "error": "No pending approval to respond to."}
        approval_id = str(entries[0].get("approval_id") or "")
    decision = _DECISIONS[choice]
    try:
        journal = _journal(cfg)
        record = journal.respond(approval_id, decision)
    except KeyError:
        return 409, {"ok": False, "stale": True,
                     "error": "Approval not found — it may have expired or "
                              "already been answered."}
    except ValueError as exc:
        return 400, {"error": redact(str(exc))[:300]}
    except Exception as exc:  # noqa: BLE001
        return 503, {"ok": False, "state": "UNAVAILABLE",
                     "code": "APPROVAL_JOURNAL_UNAVAILABLE",
                     "detail": f"the shared approval journal could not settle "
                               f"the response: {type(exc).__name__}: {exc}"}
    state = str(getattr(record, "state", "") or "")
    # The journal settles once: a second response returns the already-settled
    # record. Upstream treats that as success for the same id, so it is one here
    # too — with the real terminal state reported, not a claimed transition.
    return 200, {
        "ok": True,
        "approval_id": approval_id,
        "choice": choice,
        "decision": str(getattr(record, "decision", "") or decision),
        "state": state,
        "session_id": session_id,
        "upstream": UPSTREAM_PIN,
    }


def clarify_pending(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/wb/clarify/pending — GAP: no parked-question queue."""
    if not str_param(q, "session_id"):
        return 400, {"error": "session_id is required"}
    return gap("CLARIFY_QUEUE",
               detail=("Qubit's clarify is a route decision taken inside a "
                       "turn; there is no question waiting on a human."),
               pending=None)


def clarify_respond(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                    p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/wb/clarify/respond — GAP: nothing to respond to."""
    body = body_of(b)
    if not str(body.get("session_id") or "").strip():
        return 400, {"error": "session_id is required"}
    return gap("CLARIFY_QUEUE",
               detail=("no agent is blocked on a clarification, so there is "
                       "nothing to answer."),
               ok=False, stale=True)


HANDLERS = {
    "h_wb_approval_pending": approval_pending,
    "h_wb_approval_respond": approval_respond,
    "h_wb_clarify_pending": clarify_pending,
    "h_wb_clarify_respond": clarify_respond,
}


def routes() -> list:
    return [
        ("GET", r"/api/wb/approval/pending", "h_wb_approval_pending"),
        ("POST", r"/api/wb/approval/respond", "h_wb_approval_respond"),
        ("GET", r"/api/wb/clarify/pending", "h_wb_clarify_pending"),
        ("POST", r"/api/wb/clarify/respond", "h_wb_clarify_respond"),
    ]
