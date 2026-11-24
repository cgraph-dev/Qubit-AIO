"""Bridge dashboard text turns to the gateway that owns a named profile."""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_WAIT_SECONDS = 300
_RECEIPT_INTERVAL = 2
_HEARTBEAT_INTERVAL = 10


@dataclass
class _Turn:
    target: str = ""
    admission: str = ""
    stop_requested: bool = False


_lock = threading.Lock()
_active: dict[tuple[Path, str], _Turn] = {}


def _error(code: str, detail: str, session: str) -> dict:
    return {"event": "error", "ok": False, "code": code, "detail": detail, "session_id": session}


def _result(outcome: str | None, reply: Any, session: str) -> dict:
    if outcome in ("cancelled", "interrupted"):
        return {"event": "cancelled", "ok": False, "code": "CANCELLED", "session_id": session}
    if outcome in ("complete", "completed") and isinstance(reply, str) and reply.strip():
        from .qubit_compat import _rebrand_chat_result

        return _rebrand_chat_result({"event": "done", "ok": True, "reply": reply,
                                    "session_id": session, "session": session, "route": "gateway"})
    return _error("CHAT_GATEWAY_TURN_FAILED", "The profile gateway did not complete the turn.", session)


async def _events(home: Path, endpoint, session: str, text: str, turn: _Turn, stopped):
    from qubit_cli.gateway_client import GatewayRPCError
    from tui_gateway.owner_forward import _REFUSED_BEFORE_COMMIT, connect_owner

    submitted = False
    try:
        async with connect_owner(home, endpoint) as client:
            attached = await client.rpc("session.resume", session_id=session, omit_messages=True)
            target = attached.get("session_id")
            if not target or attached.get("stored_session_id") != session:
                yield _error("CHAT_SESSION_MOVED", "Reopen the chat to use its current session.", session)
                return
            if turn.stop_requested or stopped():
                yield _result("cancelled", None, session)
                return
            submitted = True
            admitted = await client.rpc("prompt.submit", session_id=target, text=text,
                                        submission_id=uuid.uuid4().hex)
            admission = admitted.get("admission_id")
            if not isinstance(admission, str) or not admission:
                if admitted.get("voice_stopped") is True:
                    yield _result("cancelled", None, session)
                else:
                    yield _error("CHAT_ADMISSION_UNCONFIRMED", "Reload the chat before sending again.", session)
                return
            with _lock:
                turn.target, turn.admission = target, admission
                stop_pending = turn.stop_requested
            if stop_pending or stopped():
                await client.rpc("prompt.cancel", session_id=target, admission_id=admission)
            yield {"event": "start", "session_id": session, "route": "gateway"}
            started_at = time.monotonic()
            deadline = started_at + _WAIT_SECONDS
            next_heartbeat = started_at + _HEARTBEAT_INTERVAL
            status = admitted.get("status")
            while time.monotonic() < deadline:
                frame = None
                if status != "terminal":
                    try:
                        frame = await asyncio.wait_for(client.events.get(), _RECEIPT_INTERVAL)
                    except TimeoutError:
                        pass
                if isinstance(frame, BaseException):
                    raise frame
                if frame is None:
                    saved = await client.rpc("prompt.receipt", session_id=target,
                                             admission_id=admission, include_result=True)
                    status = saved.get("status")
                    if status == "terminal":
                        result = saved.get("result") or {}
                        yield _result(saved.get("outcome"), result.get("final_response"), session)
                        return
                    if status == "unknown":
                        break
                    now = time.monotonic()
                    if now >= next_heartbeat:
                        yield {"event": "heartbeat"}
                        next_heartbeat = now + _HEARTBEAT_INTERVAL
                    continue
                params = frame.get("params") or {}
                if params.get("admission_id") != admission:
                    continue
                payload = params.get("payload") or {}
                if params.get("type") == "message.delta" and isinstance(payload.get("text"), str):
                    yield {"event": "delta", "text": payload["text"], "session_id": session}
                elif params.get("type") == "message.complete":
                    status = "terminal"
        yield _error("CHAT_ADMISSION_UNCONFIRMED", "Reload the chat before sending again.", session)
    except GatewayRPCError as exc:
        reason = str(exc.data.get("reason") or exc)
        if reason == "session_read_only" and not turn.admission:
            yield _error("CHAT_GATEWAY_REFUSED", "This history is read-only. Start a new chat to send your draft.",
                         session)
        elif not submitted or (not turn.admission and reason in _REFUSED_BEFORE_COMMIT):
            yield _error("CHAT_GATEWAY_REFUSED", f"The profile gateway refused the turn ({reason}).", session)
        else:
            yield _error("CHAT_ADMISSION_UNCONFIRMED", "Reload the chat before sending again.", session)
    except Exception:  # Owner connection and protocol failures cannot safely be retried after submission.
        code = "CHAT_ADMISSION_UNCONFIRMED" if submitted else "CHAT_PROFILE_WORKER_UNAVAILABLE"
        detail = "Reload the chat before sending again." if submitted else "The selected profile gateway is unavailable."
        yield _error(code, detail, session)


def events(home: Path, endpoint, session: str, text: str, *, cfg=None):
    """Run the gateway relay on the calling HTTP thread and close its socket on exit."""
    from .plugins import estop_engaged
    from .power import power_control

    control = power_control(cfg) if cfg is not None else None
    correlation = uuid.uuid4().hex

    def stopped():
        return cfg is not None and estop_engaged(cfg)

    def stop_turn():
        with _lock:
            turn.stop_requested = True
            target, admission = turn.target, turn.admission
        if admission:
            _cancel_admission(home, target, admission)

    key = (home, session)
    turn = _Turn()
    with _lock:
        busy = key in _active
        if not busy:
            _active[key] = turn
    if busy:
        yield _error("CHAT_SESSION_BUSY", "This chat already has an active dashboard turn.", session)
        return
    try:
        if control is not None:
            control.track(correlation, stop_turn)
        with asyncio.Runner() as runner:
            stream = _events(home, endpoint, session, text, turn, stopped)
            try:
                while True:
                    yield runner.run(anext(stream))
            except StopAsyncIteration:
                pass
            finally:
                runner.run(stream.aclose())
    finally:
        if control is not None:
            control.untrack(correlation)
        with _lock:
            _active.pop(key, None)


def cancel(home: Path, session: str) -> tuple[int, dict]:
    with _lock:
        turn = _active.get((home, session))
        target, admission = (turn.target, turn.admission) if turn else ("", "")
    if not turn:
        return 404, {"ok": False, "code": "CHAT_NO_ACTIVE_TURN"}
    if not admission:
        return 409, {"ok": False, "code": "CHAT_ADMISSION_PENDING"}

    try:
        _cancel_admission(home, target, admission)
    except Exception:
        return 409, {"ok": False, "code": "CHAT_CANCELLATION_UNCONFIRMED"}
    return 200, {"ok": True, "session_id": session}


def _cancel_admission(home: Path, target: str, admission: str) -> None:
    from tui_gateway.owner_forward import connect_owner, discover_owner

    async def stop(endpoint):
        async with connect_owner(home, endpoint) as client:
            await client.rpc("prompt.cancel", session_id=target, admission_id=admission)

    asyncio.run(stop(discover_owner(home)))


def approval(home: Path, session: str, response: dict | None) -> dict:
    """Read or settle the exact pending command using a profile-scoped owner ticket."""
    from qubit_cli.gateway_client import GatewayRPCError
    from tui_gateway.owner_forward import connect_owner, discover_owner

    async def use_owner(endpoint):
        async with connect_owner(home, endpoint) as client:
            if response is None:
                # An idle poll needs no subscription or transcript read.
                try:
                    pending = await client.rpc("approval.pending", session_id=session)
                except GatewayRPCError as exc:
                    if exc.data.get("reason") == "not_found":
                        return {"approval": None}
                    raise
                if not pending.get("approvals"):
                    return {"approval": None}

            snapshot = await client.rpc("session.resume", session_id=session, omit_messages=True)
            if snapshot.get("stored_session_id") != session or not snapshot.get("session_id"):
                return {"approval": None}

            for prompt in snapshot.get("prompts") or ():
                if prompt.get("kind") != "approval" or "edit" in prompt:
                    continue
                choices = [choice for choice in ("once", "deny") if choice in prompt.get("choices", ())]
                if not choices or not prompt.get("command"):
                    continue
                pending = {
                    "prompt_id": prompt["prompt_id"],
                    "execution_generation": prompt["execution_generation"],
                    "command": prompt["command"],
                    "description": prompt.get("description") or "",
                    "choices": choices,
                }
                if response is None:
                    return {"approval": pending}
                if (response["prompt_id"] == pending["prompt_id"]
                        and response["execution_generation"] == pending["execution_generation"]
                        and response["choice"] in choices):
                    return await client.rpc("approval.respond", session_id=snapshot["session_id"], **response)
            return {"approval": None}

    return asyncio.run(use_owner(discover_owner(home)))


def create(home: Path, name: str) -> str | None:
    """Mint a native session when the owner is ready, otherwise allow offline drafts."""
    from tui_gateway.owner_forward import OwnerNotRunning, connect_owner, discover_owner

    try:
        endpoint = discover_owner(home)
    except OwnerNotRunning:
        return None

    async def mint():
        async with connect_owner(home, endpoint) as client:
            snapshot = await client.rpc("session.create", source="gui", title=name or None,
                                        request_id=uuid.uuid4().hex)
            sid = snapshot.get("stored_session_id")
            if not isinstance(sid, str) or not sid:
                raise ValueError("The gateway did not return a stored session ID.")
            return sid

    return asyncio.run(mint())
