"""Owner -> seat messages: a durable hand-off into the real work loop.

WHY THIS EXISTS
---------------
The vendored Habblaud client has a message pane that POSTs
``/api/messages`` and then polls ``/api/messages/<id>`` for a delivery state.
Upstream that was a live Claude session; in Qubit there is no second agent
runtime to hand the text to. The honest Qubit equivalent is the work loop it
already owns: a message becomes an ordinary ``office_task`` assigned to the
seat, so the seat's next dispatch turn sees it. This module is the one durable
record of that hand-off — never a fabricated "delivered" with no work behind
it.

States are the client's own vocabulary:

* ``queued``    — the row exists, delivery has not run yet.
* ``sent``      — reserved for an in-flight delivery (not used synchronously).
* ``delivered`` — a real task was created and claimed for the seat.
* ``failed``    — delivery refused; ``error`` carries the typed reason.

Nothing here invents an agent: an unknown or terminated seat is a typed
``OFFICE_NOT_FOUND`` / ``OFFICE_BAD_STATE`` before any row is written.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from . import store
from .errors import BAD_INPUT_CODE, BAD_STATE_CODE, NOT_FOUND_CODE, OfficeError

#: The exit check for a message-backed task. ``true`` is the POSIX command that
#: always succeeds — the message itself has no external artifact to verify, so
#: the task still has a machine-checkable boundary (the check is runnable and
#: returns 0) rather than claiming completion on the model's word alone. A task
#: with no check at all is refused by ``work.create`` (C17).
MESSAGE_EXIT_CHECK = "true"


def _wire(row: Dict[str, Any]) -> Dict[str, Any]:
    """One row in the client's ``Message`` shape (epoch MILLISECONDS)."""
    def _ms(value: Any) -> int:
        try:
            return int(float(value) * 1000)
        except (TypeError, ValueError):
            return 0

    return {
        "id": str(row["id"]),
        "agentId": str(row["agent_id"]),
        "text": str(row.get("text") or ""),
        "status": str(row.get("status") or "queued"),
        "taskId": (str(row["task_id"])
                   if row.get("task_id") is not None else None),
        "error": str(row.get("error") or ""),
        "createdAt": _ms(row.get("created_at", row.get("ts"))),
        "updatedAt": _ms(row.get("updated_at", row.get("ts"))),
    }


def _seat(cfg: Any, agent_id: int) -> Dict[str, Any]:
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        row = conn.execute(
            "SELECT id, slug, name, department_id, status FROM office_agent"
            " WHERE id=?", (int(agent_id),)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise OfficeError(NOT_FOUND_CODE, f"no office seat #{agent_id}")
    seat = dict(row)
    if seat.get("status") == "terminated":
        raise OfficeError(BAD_STATE_CODE,
                          f"seat {seat.get('slug')!r} is terminated; it cannot "
                          "receive messages")
    return seat


def send(cfg: Any, agent_id: int, text: str, *,
         actor: str = "dashboard:office",
         confirmed: bool = False) -> Dict[str, Any]:
    """Persist a message and deliver it as a real task for the seat."""
    if not confirmed:
        raise OfficeError(store.CONFIRM_CODE, "office message send requires confirm")
    clean = str(text or "").strip()
    if not clean:
        raise OfficeError(BAD_INPUT_CODE, "a message needs text")
    if len(clean) > 4000:
        raise OfficeError(BAD_INPUT_CODE, "message is too long (max 4000 chars)")
    if store.estop_engaged(cfg):
        raise OfficeError("OFFICE_ESTOP",
                          "the emergency stop is latched; no message is sent")
    seat = _seat(cfg, int(agent_id))
    stamp = time.time()
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        cur = conn.execute(
            "INSERT INTO office_message(ts, agent_id, text, status, created_at,"
            " updated_at) VALUES(?,?,?,'queued',?,?)",
            (stamp, int(agent_id), clean, stamp, stamp))
        conn.commit()
        mid = int(cur.lastrowid or 0)
    finally:
        conn.close()

    status, error, task_id = "delivered", "", None
    try:
        from . import work as W

        title = clean.splitlines()[0][:120] or "Owner message"
        task = W.create(
            cfg, title=f"Message: {title}", brief=clean,
            exit_check=MESSAGE_EXIT_CHECK,
            department_id=seat.get("department_id"),
            created_by=int(agent_id), turn_budget=1, max_attempts=1,
            confirmed=True, requested_by=actor)
        task_id = int(task["id"])
        W.claim(cfg, task_id, agent_id=int(agent_id), confirmed=True,
                reason="owner message delivery", requested_by=actor)
    except Exception as exc:  # noqa: BLE001 - the row records the refusal
        status = "failed"
        error = str(getattr(exc, "detail", exc))[:300]

    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        conn.execute(
            "UPDATE office_message SET status=?, task_id=?, error=?,"
            " updated_at=? WHERE id=?",
            (status, task_id, error, time.time(), mid))
        conn.commit()
        row = dict(conn.execute("SELECT * FROM office_message WHERE id=?",
                                (mid,)).fetchone())
    finally:
        conn.close()

    # A delivered message is a real hand-off. Emit the remembered-event kind so
    # the tower can show the seat carrying something, but only when delivery
    # actually happened (C7: no motion without a fact behind it).
    if status == "delivered":
        try:
            from . import agent_memory as MEM

            MEM.emit_event(
                cfg, "note_written", agent_id=int(agent_id),
                department_id=seat.get("department_id"),
                payload={"message_id": mid, "task_id": task_id},
                visible="bubble")
        except Exception:  # noqa: BLE001 - the message row is the authority
            pass
    store.audit("message.send", actor,
                {"message_id": mid, "agent_id": int(agent_id),
                 "task_id": task_id, "status": status})
    out = _wire(row)
    out["ok"] = status == "delivered"
    return out


def get(cfg: Any, message_id: Any) -> Optional[Dict[str, Any]]:
    try:
        ref = int(str(message_id).strip())
    except (TypeError, ValueError):
        return None
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        row = conn.execute("SELECT * FROM office_message WHERE id=?",
                           (ref,)).fetchone()
    finally:
        conn.close()
    return _wire(dict(row)) if row is not None else None


def recent(cfg: Any, *, limit: int = 50,
           agent_id: Optional[int] = None) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit), 200))
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        if agent_id is None:
            rows = conn.execute(
                "SELECT * FROM office_message ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM office_message WHERE agent_id=?"
                " ORDER BY id DESC LIMIT ?", (int(agent_id), limit)).fetchall()
    finally:
        conn.close()
    return [_wire(dict(r)) for r in rows]
