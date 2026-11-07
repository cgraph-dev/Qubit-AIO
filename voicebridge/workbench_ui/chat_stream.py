"""Chat routes: upstream's ``/chat/start`` + ``/chat/stream`` on Qubit's one turn path.

UPSTREAM CONTRACT (read at ``UPSTREAM_PIN``, ``api/routes.py`` / ``api/streaming.py``)
---------------------------------------------------------------------------
``POST /api/chat/start``  ``routes.py:31125`` -> ``{stream_id, session_id,
                          pending_started_at, turn_id, title, effective_model?}``
                          The turn runs in the background; tokens arrive on SSE.
``GET  /api/chat/stream?stream_id=``  ``streaming.py`` -> ``text/event-stream``
                          with named events ``token`` (``{text}``), ``reasoning``,
                          ``tool``, ``tool_complete``, ``done`` (``{status, session}``),
                          ``error``.
``GET  /api/chat/stream/status?stream_id=`` -> ``{active: bool, replay_available: bool}``
                          The client's reconnect probe (``messages.js:7610``).
``POST /api/chat/steer``  -> GAP (no in-flight run to steer; see ``sessions.py``).

HOW QUBIT ANSWERS WITHOUT A SECOND SEND PATH
---------------------------------------------------------------------------
Qubit's ``chat_transport`` already streams a real turn:
``chat_transport.stream_turn`` yields typed events (``start``/``delta``/``reasoning``/
``progress``/``cancelled``/``done``/``error``) from the same agent turn
``/qubit-chat`` runs, and ``webui`` already serves it as SSE at
``POST /agent/api/qubit/chat/stream`` (``qubit_compat.h_chat_stream``).

This module adds the *split* upstream's client needs — start a turn, then read its
stream by id — by running that SAME generator on a background thread and buffering
its events here::

    run_stream_thread(cfg, ...)  ->  thread iterating chat_transport.stream_turn
                                 ->  _Run.events (buffer) + _Run.cond
    GET /chat/stream/<id>        ->  replays the buffer, then blocks for new events

So: one sender (``chat_transport.stream_turn``), one persistence path
(``ChatSessionStore`` inside it), one preflight (``stream_preflight``). Nothing here
appends a message, resolves a model, or touches a store.

THE TURN IS BOUND TO THE CALLER'S SESSION ID
---------------------------------------------------------------------------
Upstream's ``session_id`` in ``/chat/start`` is the sidebar row the user clicked.
Qubit's store treats an unknown id as "create it", so forwarding it is safe, and it
preserves the identity the client already rendered. ``chat_transport.store`` is the
declared ``CHAT_SESSION_AUTHORITY``; no other writer is opened.

DEATH AND RE-A TTACH ARE BOTH EXPLICIT
---------------------------------------------------------------------------
* A run that finished keeps its buffer for ``_RETAIN_S`` seconds so a client that
  reloaded can replay it (upstream's ``replay_available``). After that the run is
  forgotten and the client falls back to the persisted transcript, exactly as
  upstream does when its journal is pruned.
* A client that reconnects to a live run replays from the start of the buffer
  (``?after_seq=0``); the client sends its cursor otherwise and gets only the tail,
  so a reconnect never duplicates text.
* A run that raised yields a typed ``error`` event; the client renders it. No
  fabricated ``done``.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Any, Dict, Iterator, List, Optional, Tuple

from ..workbench_shim import UPSTREAM_PIN, e_stopped, str_param

#: How long a finished run stays addressable for a late ``/chat/stream`` (upstream's
#: replay window; the client's own reconnect ladder tops out at ~50 s).
#: Retention for a finished run (replay window).
_RETAIN_S = 120.0
#: Absolute ceiling on a run's life. A turn that has produced nothing by now is
#: never going to; expires it so it cannot answer 'active' forever.
_HARD_MAX_S = 600.0
#: How long a ``/chat/stream`` reader waits between liveness checks.
_POLL_S = 0.25
#: Hard cap on one SSE connection's lifetime. The client reconnects on close and
#: replays by cursor, so a long turn survives the cap; an idle socket does not.
_STREAM_MAX_S = 600.0
#: Buffered events kept per run. A delta is one model sentence, so this is a very
#: long turn; past it the OLDEST events are dropped and the reader is told
#: (``truncated``) rather than silently losing the head.
_MAX_EVENTS = 20000


class _Run:
    """One live (or recently finished) turn, addressable by ``stream_id``."""

    __slots__ = ("stream_id", "session_id", "started_at", "events", "seq",
                 "done", "error", "lock", "changed", "truncated", "thread",
                 "title", "model", "finished_at")

    def __init__(self, stream_id: str, session_id: str) -> None:
        self.stream_id = stream_id
        self.session_id = session_id
        self.started_at = time.time()
        self.events: List[Tuple[int, Dict[str, Any]]] = []
        self.seq = 0
        self.done = False
        self.error: Optional[str] = None
        self.truncated = False
        self.lock = threading.Lock()
        self.changed = threading.Condition(self.lock)
        self.thread: Optional[threading.Thread] = None
        self.title: Optional[str] = None
        self.finished_at: Optional[float] = None
        self.model: Optional[str] = None

    def push(self, event: Dict[str, Any]) -> None:
        with self.changed:
            self.seq += 1
            self.events.append((self.seq, event))
            if len(self.events) > _MAX_EVENTS:
                drop = len(self.events) - _MAX_EVENTS
                del self.events[:drop]
                self.truncated = True
            if str(event.get("event")) in ("done", "error", "cancelled"):
                self.done = True
                if str(event.get("event")) == "error":
                    self.error = str(event.get("code") or "STREAM_ERROR")
            self.changed.notify_all()

    def snapshot(self, after_seq: int = 0) -> Tuple[List[Tuple[int, Dict[str, Any]]],
                                                   bool, bool]:
        with self.lock:
            return ([(s, e) for (s, e) in self.events if s > after_seq],
                    self.done, self.truncated)


_RUNS: Dict[str, _Run] = {}
_RUNS_LOCK = threading.Lock()


def _as_body(value: Any) -> Dict[str, Any]:
    """A JSON body is a dict or nothing; the gateway guarantees the JSON half."""
    return value if isinstance(value, dict) else {}


def _gc() -> None:
    """Forget finished runs past their retention window (and any orphan)."""
    now = time.time()
    with _RUNS_LOCK:
        dead = []
        for sid, r in _RUNS.items():
            # Retention is measured from when the run FINISHED (falls back to
            # started_at for a run that never finished). Measuring from the
            # start meant a turn slower than _RETAIN_S was reaped the instant it
            # completed, before any client could read its result.
            _t0 = r.finished_at or r.started_at
            if r.done and now - _t0 > _RETAIN_S:
                dead.append(sid)
            elif (not r.done and r.thread is not None
                  and r.thread.ident is not None
                  and not r.thread.is_alive()):
                # ORPHAN: the pump thread died without ever marking the run
                # terminal. Leaving it in _RUNS makes live_run_for_session()
                # return it forever, so every later /chat/start for that session
                # is refused 409 SESSION_BUSY — a session permanently locked out
                # by one crashed turn. Observed exactly that before this fix.
                dead.append(sid)
            elif not r.done and now - r.started_at > _HARD_MAX_S:
                # ABSOLUTE BACKSTOP. The checks above miss two shapes: a run
                # whose pump never started (thread is None) and one whose thread
                # died before ident was assigned. Both write nothing and can
                # never settle, so the run answered 'active' forever — no
                # watchdog in the pump can fire, because there is no pump. Age
                # is the only fact every run has, so expire on it.
                dead.append(sid)
        for sid in dead:
            _RUNS.pop(sid, None)


def _get(stream_id: str) -> Optional[_Run]:
    _gc()
    with _RUNS_LOCK:
        return _RUNS.get(stream_id)


def _new_run(session_id: str) -> _Run:
    _gc()
    run = _Run(uuid.uuid4().hex, session_id)
    with _RUNS_LOCK:
        _RUNS[run.stream_id] = run
    return run


def live_run_for_session(session_id: str) -> Optional[_Run]:
    """The unfinished run for a session, if one is still producing events."""
    with _RUNS_LOCK:
        for run in _RUNS.values():
            if run.session_id == session_id and not run.done:
                return run
    return None


# --------------------------------------------------------------------------- #
# start
# --------------------------------------------------------------------------- #
def _upstream_events(gen: Iterator[Dict[str, Any]], sid: str) -> Iterator[Dict[str, Any]]:
    """Map ``chat_transport.stream_turn`` events onto upstream's named events.

    Only names and field names change; no event is invented or dropped. Qubit's
    ``progress`` (task worker) has no upstream equivalent and is carried on the
    ``tool`` channel as a status line, which is what the client renders for
    non-tool progress anyway.
    """
    for ev in gen:
        kind = str(ev.get("event") or "")
        if kind == "start":
            continue  # upstream signals start via the /chat/start response
        if kind == "delta":
            yield {"event": "token", "text": str(ev.get("text") or ""),
                   "session_id": sid}
        elif kind == "reasoning":
            yield {"event": "reasoning", "text": str(ev.get("text") or ""),
                   "session_id": sid}
        elif kind == "progress":
            yield {"event": "tool", "name": str(ev.get("task") or "task"),
                   "status": str(ev.get("status") or ""), "session_id": sid}
        elif kind == "cancelled":
            yield {"event": "error", "code": "CANCELLED",
                   "detail": "the turn was cancelled", "session_id": sid}
        elif kind == "error":
            yield {"event": "error", "code": str(ev.get("code") or "TURN_FAILED"),
                   "detail": str(ev.get("detail") or ""), "session_id": sid}
        elif kind == "done":
            yield {"event": "done", "status": "completed",
                   "session_id": sid,
                   "session": {"session_id": sid},
                   "reply": ev.get("reply"),
                   "elapsed_ms": ev.get("elapsed_ms"),
                   "usage": (ev.get("receipt") or {}).get("usage")
                   if isinstance(ev.get("receipt"), dict) else None}


def _pump(run: _Run, cfg: Any, text: str, *, model: Optional[str],
          reasoning: Optional[str], timeout_s: Optional[float]) -> None:
    """Thread body: stream ONE turn and buffer every event. Never invents one."""
    try:
        from .. import chat_transport as CT
    except Exception as exc:  # noqa: BLE001 - absence is a typed error event
        run.push({"event": "error", "code": "TURN_TRANSPORT_UNAVAILABLE",
                  "detail": f"chat_transport is not reachable: "
                            f"{type(exc).__name__}: {exc}"})
        return
    try:
        gen = CT.stream_turn(cfg, text, session=run.session_id, model=model,
                             reasoning=reasoning, timeout_s=timeout_s,
                             actor="dashboard:user")
        # Watchdog. chat_transport.stream_turn accepts timeout_s but never
        # applies it, so a stalled model call blocks this generator forever and
        # the run sits 'active' indefinitely — the pane spins and the server
        # holds a dead thread (observed: 140s of ': connected' on an active
        # run). Bound the wall clock here and emit a real terminal event
        # instead of hanging. The turn itself is not cancelled (the transport
        # owns that); this only stops the stream lying about being alive.
        deadline = time.monotonic() + float(os.environ.get("QU_WB_TURN_MAX_S", "300"))
        for ev in _upstream_events(gen, run.session_id):
            run.push(ev)
            if str(ev.get("event") or "") in ("done", "error"):
                break
            if time.monotonic() > deadline:
                run.push({"event": "error", "code": "TURN_TIMEOUT",
                          "detail": ("the turn produced no terminal event within "
                                     f"{int(deadline - run.started_at)}s; the "
                                     "transcript on disk is authoritative — "
                                     "reload the session to read it")})
                break
        # A generator that ended without a terminal event is a transport fault,
        # not a successful turn: say so instead of closing the stream silently.
        with run.lock:
            settled = run.done
        if not settled:
            run.push({"event": "error", "code": "STREAM_ENDED_EARLY",
                      "detail": "the turn stream ended without a done event"})
    except Exception as exc:  # noqa: BLE001 - a crash is an error, never silence
        run.push({"event": "error", "code": "TURN_FAILED",
                  "detail": f"{type(exc).__name__}: {exc}"[:400]})
    finally:
        # A run that ends before any reader attaches must still be READABLE.
        # Reaping it immediately left the client with STREAM_NOT_FOUND, which
        # the pane renders as a bare "Processed" row with no text and no error —
        # an instant refusal looked exactly like a successful silent turn.
        # Stamp the finish time so _gc() measures retention from the END, not
        # from the start, and a fast turn stays replayable for the full window.
        with run.changed:
            run.finished_at = time.time()
            if not run.done:
                run.done = True
            run.changed.notify_all()


def chat_start(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """POST /api/chat/start — begin one real turn, answer upstream's envelope."""
    body = _as_body(b)
    session_id = str(body.get("session_id") or "").strip()
    message = str(body.get("message") or body.get("text") or "")
    if not session_id:
        return 400, {"error": "session_id required", "ok": False}
    if not message.strip():
        return 400, {"error": "message is required", "ok": False}
    if e_stopped(cfg):
        return 403, {"ok": False, "error": "emergency stop latched; agent turn refused",
                     "code": "EMERGENCY_STOP"}
    # One turn per session at a time, exactly like upstream's 409 (the client has a
    # branch for this exact message and queues the user's text behind the run).
    live = live_run_for_session(session_id)
    if live is not None:
        return 409, {"ok": False, "error": "session already has an active stream",
                     "code": "SESSION_BUSY", "active_stream_id": live.stream_id}
    # Regeneration re-sends the last user turn server-side upstream. Qubit's
    # transport has no regenerate entry point, so this is refused by name rather
    # than silently sending an empty turn.
    if body.get("regenerate"):
        return 501, {"ok": False, "code": "REGENERATE_NOT_PORTED",
                     "detail": ("regeneration is not ported: chat_transport runs "
                                "turns from user text and has no regenerate entry "
                                "point. Send the instruction again instead.")}
    model = str(body.get("model") or "").strip() or None
    reasoning = body.get("reasoning")
    try:
        from .. import chat_transport as CT
        # Same preflight the streaming route runs, so a refusal is a typed status
        # BEFORE any stream exists instead of an error event after a 200.
        status, refusal, _resolved = CT.stream_preflight(
            cfg, message, model, session=session_id)
    except Exception as exc:  # noqa: BLE001
        return 503, {"ok": False, "code": "TURN_TRANSPORT_UNAVAILABLE",
                     "error": f"chat_transport is not reachable: "
                              f"{type(exc).__name__}: {exc}"}
    if refusal is not None:
        payload = {k: v for k, v in refusal.items() if k != "event"}
        payload["ok"] = False
        payload.setdefault("error", payload.get("detail") or payload.get("code"))
        return status, payload

    run = _new_run(session_id)
    run.model = model or _resolved
    run.thread = threading.Thread(
        target=_pump, name=f"wb-chat-{run.stream_id[:8]}", daemon=True,
        args=(run, cfg, message),
        kwargs={"model": model,
                "reasoning": reasoning if isinstance(reasoning, str) else None,
                "timeout_s": float(os.environ.get("QU_WB_TURN_MAX_S", "180"))})
    run.thread.start()
    return 200, {
        "ok": True,
        "stream_id": run.stream_id,
        "session_id": session_id,
        "pending_started_at": run.started_at,
        "turn_id": None,
        "title": None,
        "effective_model": run.model,
        "upstream": UPSTREAM_PIN,
        "via": "voicebridge.chat_transport.stream_turn",
    }


# --------------------------------------------------------------------------- #
# read
# --------------------------------------------------------------------------- #
def chat_stream_frames(stream_id: str, after_seq: int = 0) -> Iterator[str]:
    """SSE frames for one run: replay the buffer, then follow it to the end.

    Yields bare frame text (``event: ...\\ndata: ...\\n\\n``); the transport's own
    framing (chunked writes) belongs to the caller.
    """
    run = _get(stream_id)
    if run is None:
        yield _frame("error", {"event": "error", "code": "STREAM_NOT_FOUND",
                               "detail": (f"no stream {stream_id!r}: it finished "
                                          f"more than {int(_RETAIN_S)}s ago or was "
                                          f"never started. Reload the session to "
                                          f"read the persisted transcript.")})
        return
    cursor = max(0, int(after_seq or 0))
    deadline = time.monotonic() + _STREAM_MAX_S
    while True:
        batch, done, truncated = run.snapshot(cursor)
        if truncated and cursor == 0:
            yield _frame("notice", {"event": "notice", "kind": "truncated",
                                    "detail": "earlier events were dropped from "
                                              "the buffer; the transcript is "
                                              "complete"})
        for seq, ev in batch:
            cursor = seq
            name = str(ev.get("event") or "message")
            body = dict(ev)
            body["stream_id"] = stream_id
            body["seq"] = seq
            yield _frame(name, body)
            if name in ("done", "error"):
                return
        if done:
            return
        if time.monotonic() > deadline:
            yield _frame("error", {"event": "error", "code": "STREAM_TIMEOUT",
                                   "detail": ("the stream exceeded its "
                                              f"{int(_STREAM_MAX_S)}s connection "
                                              "window; reconnect with after_seq "
                                              "to resume")})
            return
        with run.changed:
            if not run.snapshot(cursor)[0] and not run.done:
                run.changed.wait(_POLL_S)


def _frame(name: str, payload: Dict[str, Any]) -> str:
    return (f"event: {name}\n"
            f"data: {json.dumps(payload, ensure_ascii=False)}\n\n")


def chat_stream_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                       p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/chat/stream/status — the client's reconnect probe."""
    stream_id = str_param(q, "stream_id").strip()
    if not stream_id:
        return 400, {"error": "stream_id required", "ok": False}
    run = _get(stream_id)
    if run is None:
        return 200, {"ok": True, "active": False, "replay_available": False}
    return 200, {"ok": True, "active": not run.done,
                 "replay_available": bool(run.events)}


def cancel_stream(session_id: str) -> bool:
    """Cancel a live run's turn through the transport's ONE cancel authority."""
    run = live_run_for_session(session_id)
    if run is None:
        return False
    try:
        from .. import chat_transport as CT
        CT.cancel(run.session_id)
        return True
    except Exception:  # noqa: BLE001 - no cancel is a false, typed by the caller
        return False


def session_live_info(session_id: str) -> Dict[str, Any]:
    """``{active_stream_id, is_streaming}`` for a session, from the run table."""
    run = live_run_for_session(session_id)
    return {"active_stream_id": run.stream_id if run else None,
            "is_streaming": run is not None}
