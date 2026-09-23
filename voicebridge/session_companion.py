"""Canonical Qubit session companion (session side chat).

Upstream keeps a session-scoped companion service that answers side questions
over a live session and reports its state. Qubit implements the same real
capability over its ONE session authority: a companion is a real session in the
canonical ``state.db`` linked to its parent by ``companion_of`` metadata, and
``ask`` runs a real canonical agent turn
(:func:`voicebridge.chat_transport.turn`) in that companion session — the same
provider/policy/audit/e-stop path as ``/qubit-chat``. No second runtime, no
second store, no fabricated answer.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

COMPANION_SUFFIX = "-companion"
COMPANION_SESSION_NOT_FOUND = "COMPANION_SESSION_NOT_FOUND"
COMPANION_TURN_FAILED = "COMPANION_TURN_FAILED"


def companion_session_id(session_id: str) -> str:
    sid = str(session_id or "").strip()[:60]
    return f"{sid}{COMPANION_SUFFIX}"


def _store(cfg: Any) -> Any:
    from . import chat_transport as CT

    return CT.store(cfg)


def ensure(store: Any, session_id: str) -> Dict[str, Any]:
    """Ensure the companion session exists and is linked to its parent."""
    cid = companion_session_id(session_id)
    if store._session(cid) is None:
        created = store.create(f"Companion of {session_id}"[:80],
                               session_id=cid)
        if str(created.get("state")) not in ("OK", "EXISTS"):
            return created
        store.meta_patch(cid, {"companion_of": session_id})
    return {"state": "OK", "companionSessionId": cid}


def state(store: Any, session_id: str) -> Dict[str, Any]:
    """Real companion state from the canonical store."""
    cid = companion_session_id(session_id)
    rec = store._session(cid)
    if rec is None:
        return {"state": "OK", "active": False,
                "companionSessionId": cid, "messageCount": 0,
                "lastAt": None, "lastAnswer": None}
    messages = store._messages(cid)
    last_answer = None
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and msg.get("content"):
            last_answer = str(msg["content"])[:280]
            break
    last_at = messages[-1]["timestamp"] if messages else None
    return {"state": "OK", "active": True, "companionSessionId": cid,
            "messageCount": len(messages), "lastAt": last_at,
            "lastAnswer": last_answer,
            "authority": "voicebridge.session_companion"}


def reset(store: Any, session_id: str, *, confirm: bool = False
          ) -> Dict[str, Any]:
    """Clear the companion transcript (recoverable: rows are deleted only for
    the companion session; the parent session is untouched)."""
    cid = companion_session_id(session_id)
    rec = store._session(cid)
    if rec is None:
        return {"state": "OK", "reset": False, "companionSessionId": cid,
                "detail": "no companion session to reset"}
    if not confirm:
        return {"state": "CONFIRM_REQUIRED", "code": "CONFIRM_REQUIRED",
                "detail": f"resetting companion for {session_id!r} needs "
                          "confirm=true",
                "companionSessionId": cid}
    before = int(store._db.message_count(cid))
    store._db.clear_messages(cid)
    store.meta_patch(cid, {"companion_of": None})
    return {"state": "OK", "reset": True, "companionSessionId": cid,
            "cleared": before, "authority": "voicebridge.session_companion"}


def ask(store: Any, cfg: Any, session_id: str, question: str
        ) -> Dict[str, Any]:
    """Run a real canonical agent turn in the companion session."""
    from . import chat_transport as CT

    ready = ensure(store, session_id)
    if str(ready.get("state")) != "OK":
        return ready
    cid = ready["companionSessionId"]
    try:
        out = CT.turn(cfg, question, session=cid, role="chat")
    except Exception as exc:  # noqa: BLE001 - typed at the boundary
        return {"state": "FAILED", "code": COMPANION_TURN_FAILED,
                "detail": str(exc)[:300], "companionSessionId": cid}
    if not out.get("ok", out.get("state") == "OK"):
        return {"state": out.get("state", "FAILED"),
                "code": out.get("code", COMPANION_TURN_FAILED),
                "detail": out.get("detail", ""),
                "companionSessionId": cid}
    answer = out.get("reply") or out.get("answer") or out.get("text")
    return {"state": "OK", "answer": answer,
            "companionSessionId": cid, "sessionId": session_id,
            "messageCount": out.get("message_count"),
            "authority": "voicebridge.session_companion "
                         "(chat_transport.turn)"}
