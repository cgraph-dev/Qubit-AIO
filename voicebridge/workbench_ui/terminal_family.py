"""The terminal family: an AGENT-backed session behind upstream's terminal wire.

THE HONEST ANSWER
---------------------------------------------------------------------------
Upstream's ``terminal.js`` drives xterm against a real shell over
``/api/terminal/{start,input,resize,close}`` plus an output EventSource. Qubit's
owner policy forbids a raw shell/PTY on this listener (``PTY_NO_SHELL``,
``voicebridge/webui.py:1251``) and that refusal STAYS: nothing here spawns a
process, opens a pty or runs a ``sh -c``.

What Qubit already has is :class:`voicebridge.chat_transport.AgentPtySession` —
"a PTY-shaped session with no PTY": keystrokes in, an agent turn out. It is the
session the Qubit Chat terminal mounts, and it is the one this family wires to
upstream's wire contract, so the ported pane renders real output instead of a
blank refusal. Two facts ride on every payload so the pane can never imply it is
a shell:

* ``backend: "agent"`` — not ``"local"``; the page reads
  ``terminal_remote_backend`` from ``/api/workspaces`` and keeps its own
  "embedded terminal only for local backends" path unchanged.
* ``shell: false`` and a ``banner`` naming what it actually is.

REQUEST/RESPONSE (quoted from the ported client)
---------------------------------------------------------------------------
``terminal.js:633``  ``POST /api/terminal/start  {session_id, rows, cols, restart}``
``terminal.js:132``  ``POST /api/terminal/input  {session_id, data}``
``terminal.js:832``  ``POST /api/terminal/resize {session_id, rows, cols}``
``terminal.js:752``  ``POST /api/terminal/close  {session_id}``
``terminal.js:559``  ``GET  api/terminal/output?session_id=<sid>`` (SSE:
                     ``event: output`` with ``{text}``, ``terminal_closed``,
                     ``terminal_error`` with ``{error}``). NOTE: this one path is
                     NOT in the 165-endpoint list because it is an
                     ``EventSource`` opened from ``terminal.js`` rather than a
                     ``fetch`` — the pane opens it the moment ``start`` succeeds,
                     so serving ``start`` without it would leave a dead pane.

The pane opens one EventSource per started session and expects frames to keep
arriving; the stream therefore stays open with keep-alives until the session is
closed, the pane goes away, or the idle cap (upstream's own 30 min) is reached.

SESSION SCOPE
---------------------------------------------------------------------------
A session is identified by ``session_id`` (the ported client passes its chat
session id). Qubit's ``AgentPtySession`` keeps its own conversation key, which is
qualified with the chat session id so two panes never share one buffer. A
session is NOT confined to a workspace path because it runs no command in one;
it only needs the ``session_id`` to be a plain bounded identifier.
"""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

#: Upstream's own idle cap for a terminal (:data:`voicebridge.pty_ws.IDLE_TIMEOUT_S`).
IDLE_TIMEOUT_S = 30 * 60.0
#: Concurrent agent terminals. Each is a conversation buffer, not a process, but
#: each turn costs a model call, so it stays bounded like the PTY server's cap.
MAX_SESSIONS = 12
#: Upstream's per-input cap is enforced client-side; this is the server's own.
MAX_INPUT_CHARS = 8192
MIN_COLS, MIN_ROWS, MAX_COLS, MAX_ROWS = 10, 3, 500, 200
#: How long an output stream waits for something to happen before a keep-alive.
POLL_S = 0.25
#: A read returns at most this much per frame (xterm writes are cheap; a huge
#: frame is a sign of a runaway reply).
MAX_FRAME_CHARS = 65536

_SID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class TerminalRefusal(Exception):
    """A typed terminal refusal (the pane renders ``detail``)."""

    def __init__(self, code: str, detail: str, *, status: int = 400,
                 counterpart: str = "") -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status
        self.counterpart = counterpart


class _Term:
    """One agent-backed session and its output buffer for one SSE reader."""

    __slots__ = ("session_id", "session", "cols", "rows", "started", "last_used",
                 "closed", "buffered", "lock", "error", "lines")

    def __init__(self, session_id: str, session: Any, cols: int, rows: int) -> None:
        self.session_id = session_id
        self.session = session
        self.cols = cols
        self.rows = rows
        self.started = time.time()
        self.last_used = self.started
        self.closed = False
        self.buffered: List[str] = []
        self.lock = threading.Lock()
        self.error = ""
        self.lines = 0


_TERMS: Dict[str, _Term] = {}
_TERMS_GUARD = threading.Lock()


def _terminals() -> Dict[str, _Term]:
    """The live sessions, evicting idle ones (upstream's own 30-minute cap)."""
    now = time.time()
    with _TERMS_GUARD:
        for sid, term in list(_TERMS.items()):
            if now - term.last_used > IDLE_TIMEOUT_S and not term.buffered:
                term.closed = True
                _TERMS.pop(sid, None)
        return _TERMS


def refuses(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Turn a :class:`TerminalRefusal` into its wire answer.

    The handler contract is ``(status, payload)`` like every other family; a
    raised refusal would reach the gateway as a 500, which the pane paints as a
    crash instead of the reason. Applied to the four POST handlers below.
    """
    import functools

    @functools.wraps(fn)
    def _wrapped(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                 p: Dict[str, Any]) -> Tuple[int, dict]:
        try:
            return fn(cfg, q, b, p)
        except TerminalRefusal as exc:
            return exc.status, {
                "ok": False,
                "state": "REFUSED",
                "code": exc.code,
                "endpoint": "terminal/" + getattr(fn, "__name__", ""),
                "detail": exc.detail,
                "backend": "agent",
                "shell": False,
                "owner": "qubit-policy",
                "rollback": "nothing started; nothing to roll back",
                "qubit_counterpart": exc.counterpart or (
                    "Qubit Chat (/chat) — the same agent transport"),
            }

    return _wrapped


def _body_key(value: Any) -> Dict[str, Any]:
    """The compat table's dict argument, or ``{}`` (never a crash on garbage)."""
    return value if isinstance(value, dict) else {}


def session_id_of(body: Dict[str, Any], query: Optional[Dict[str, str]] = None
                  ) -> str:
    """The client's session id, validated as a plain bounded token."""
    raw = str((body or {}).get("session_id")
              or (query or {}).get("session_id") or "").strip()
    if not raw:
        raise TerminalRefusal(
            "TERMINAL_SESSION_REQUIRED",
            "a session_id is required; the terminal is scoped to a chat "
            "session so its buffer is not shared with another pane")
    if not _SID_RE.match(raw):
        raise TerminalRefusal(
            "TERMINAL_BAD_SESSION_ID",
            "session_id must be 1-128 characters of [A-Za-z0-9._:-]")
    return raw


def _dims(body: Dict[str, Any], *, default_cols: int = 80,
          default_rows: int = 24) -> Tuple[int, int]:
    def _int(name: str, fallback: int, low: int, high: int) -> int:
        try:
            return max(low, min(int(str((body or {}).get(name))), high))
        except (TypeError, ValueError):
            return fallback

    return _int("cols", default_cols, MIN_COLS, MAX_COLS), \
        _int("rows", default_rows, MIN_ROWS, MAX_ROWS)


def agent_session_for(sid: str, cols: int, rows: int) -> Any:
    """Build the AgentPtySession for a chat session (never a shell).

    The class is constructed directly rather than through
    ``chat_transport.pty_session`` because that registry is keyed only by
    session id and is shared with the Qubit Chat terminal: a COMPUTER session and
    an agent session on the same id must not append into one buffer. Here the
    agent session's own conversation key is qualified with a prefix, so two
    panes never share one.
    """
    from ..chat_transport import AgentPtySession

    return AgentPtySession(None, f"workbench-terminal:{sid}",
                           cols=cols, rows=rows)


@refuses
def start(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
          p: Dict[str, Any]) -> Tuple[int, dict]:
    """``POST /api/terminal/start`` — open (or restart) an agent session."""
    body, query = _body_key(b), _body_key(q)
    sid = session_id_of(body, query)
    cols, rows = _dims(body)
    restart = bool((body or {}).get("restart"))
    terms = _terminals()
    with _TERMS_GUARD:
        existing = terms.get(sid)
        if existing is not None and not restart and not existing.closed:
            existing.cols, existing.rows = cols, rows
            existing.last_used = time.time()
            return 200, _started(existing, reused=True)
    try:
        session = agent_session_for(sid, cols, rows)
    except Exception as exc:  # noqa: BLE001 - typed, redacted by the caller
        raise TerminalRefusal(
            "TERMINAL_BACKEND_UNAVAILABLE",
            f"the agent-backed terminal could not be constructed "
            f"({type(exc).__name__}); no shell is ever spawned here",
            status=503) from None
    term = _Term(sid, session, cols, rows)
    with _TERMS_GUARD:
        if len(terms) >= MAX_SESSIONS:
            oldest = min(terms.values(), key=lambda t: t.last_used)
            oldest.closed = True
            terms.pop(oldest.session_id, None)
        terms[sid] = term
    with term.lock:
        term.buffered.append(_banner(term))
    return 200, _started(term, reused=False)


def _started(term: _Term, *, reused: bool) -> Dict[str, Any]:
    return {
        "ok": True,
        "session_id": term.session_id,
        "backend": "agent",
        "shell": False,
        "workspace": None,
        "cols": term.cols,
        "rows": term.rows,
        "reused": reused,
        "started_at": term.started,
        "banner": _banner(term),
        "detail": ("agent-backed terminal: keystrokes are buffered into a line "
                   "and run as a Qubit agent turn on Enter. No shell, no PTY."),
        "qubit_counterpart": "Qubit Chat (/chat) — same agent transport",
    }


def _banner(term: _Term) -> str:
    return ("\x1b[2mQubit agent terminal — no shell. Type a message and press "
            "Enter; the agent replies here. Ctrl-C cancels, Ctrl-D closes.\x1b[0m"
            "\r\n\r\n")


def _write(term: _Term, text: str) -> None:
    with term.lock:
        term.buffered.append(text)
        term.last_used = time.time()


@refuses
def input_text(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
               p: Dict[str, Any]) -> Tuple[int, dict]:
    """``POST /api/terminal/input`` — feed one keystroke batch to the session."""
    body, query = _body_key(b), _body_key(q)
    sid = session_id_of(body, query)
    data = (body or {}).get("data")
    if not isinstance(data, str):
        raise TerminalRefusal("TERMINAL_BAD_INPUT",
                              "data must be a string of keystrokes")
    if len(data) > MAX_INPUT_CHARS:
        raise TerminalRefusal(
            "TERMINAL_INPUT_TOO_LARGE",
            f"data exceeds {MAX_INPUT_CHARS} characters per request")
    terms = _terminals()
    with _TERMS_GUARD:
        term = terms.get(sid)
    if term is None or term.closed:
        raise TerminalRefusal(
            "TERMINAL_NOT_RUNNING",
            "this session has no running terminal; POST /api/terminal/start "
            "first", status=409)
    chunks: List[bytes] = []
    try:
        for piece in term.session.feed(data.encode("utf-8", "replace")):
            if isinstance(piece, (bytes, bytearray)):
                chunks.append(bytes(piece))
    except Exception as exc:  # noqa: BLE001
        _write(term, f"\r\n\x1b[31m[terminal error] {type(exc).__name__}\x1b[0m"
                     "\r\n")
        raise TerminalRefusal(
            "TERMINAL_TURN_FAILED",
            f"the agent turn failed ({type(exc).__name__}); the session is "
            f"still open", status=502) from None
    text = b"".join(chunks).decode("utf-8", "replace")
    if text:
        _write(term, text)
    if not getattr(term.session, "alive", True):
        term.closed = True
        _write(term, "")
    with term.lock:
        term.lines += data.count("\n")
        term.last_used = time.time()
    return 200, {"ok": True, "session_id": sid, "accepted": len(data),
                 "closed": term.closed,
                 "backend": "agent", "shell": False}


@refuses
def resize(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
           p: Dict[str, Any]) -> Tuple[int, dict]:
    """``POST /api/terminal/resize`` — record the pane's geometry."""
    body, query = _body_key(b), _body_key(q)
    sid = session_id_of(body, query)
    cols, rows = _dims(body)
    terms = _terminals()
    with _TERMS_GUARD:
        term = terms.get(sid)
    if term is None:
        raise TerminalRefusal(
            "TERMINAL_NOT_RUNNING",
            "this session has no running terminal; POST /api/terminal/start "
            "first", status=409)
    term.cols, term.rows = cols, rows
    for attr, value in (("cols", cols), ("rows", rows)):
        try:
            setattr(term.session, attr, value)
        except Exception:  # noqa: BLE001 - geometry is advisory
            pass
    term.last_used = time.time()
    return 200, {"ok": True, "session_id": sid, "cols": cols, "rows": rows,
                 "backend": "agent", "shell": False}


@refuses
def close(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
          p: Dict[str, Any]) -> Tuple[int, dict]:
    """``POST /api/terminal/close`` — end the session (idempotent)."""
    body, query = _body_key(b), _body_key(q)
    sid = session_id_of(body, query)
    with _TERMS_GUARD:
        term = _TERMS.pop(sid, None)
    existed = term is not None
    if term is not None:
        term.closed = True
        try:
            term.session.alive = False
        except Exception:  # noqa: BLE001
            pass
        with term.lock:
            term.buffered.append("")
    return 200, {"ok": True, "session_id": sid, "closed": True,
                 "existed": existed, "backend": "agent", "shell": False}


# ----------------------------------------------------------------- the stream
def output_frames(cfg: Any, query: Dict[str, str],
                  *, should_stop: Optional[Any] = None) -> Iterable[str]:
    """SSE frames for ``GET /api/terminal/output?session_id=``.

    Frames match what ``terminal.js:563-585`` listens for: ``output`` with
    ``{"text"}``, ``terminal_closed`` when the session ended, ``terminal_error``
    with ``{"error"}`` when the backend failed. The stream stays open with
    keep-alives (the pane treats a close as a reconnect) until the session is
    closed, an error occurs, the idle cap passes, or ``should_stop()`` says the
    client is gone.
    """
    try:
        sid = session_id_of({}, query)
    except TerminalRefusal as exc:
        yield _error_frame(exc.code, exc.detail)
        return
    terms = _terminals()
    with _TERMS_GUARD:
        term = terms.get(sid)
    if term is None:
        yield _error_frame(
            "TERMINAL_NOT_RUNNING",
            "this session has no running terminal; POST /api/terminal/start "
            "first")
        return
    idle = 0.0
    while True:
        if should_stop is not None:
            try:
                if should_stop():
                    return
            except Exception:  # noqa: BLE001 - a dead socket is a stop
                return
        with _TERMS_GUARD:
            gone = terms.get(sid) is not term
        with term.lock:
            pending = "".join(term.buffered)
            term.buffered.clear()
        if not gone and pending:
            for chunk in _chunks(pending):
                yield _frame("output", {"text": chunk})
        elif gone or term.closed:
            if pending:
                for chunk in _chunks(pending):
                    yield _frame("output", {"text": chunk})
            yield _frame("terminal_closed", {"session_id": sid})
            return
        if term.error:
            yield _error_frame("TERMINAL_BACKEND_ERROR", term.error)
            return
        if gone or term.closed:
            yield _frame("terminal_closed", {"session_id": sid})
            return
        if time.time() - term.last_used > IDLE_TIMEOUT_S:
            yield _frame("terminal_closed", {"session_id": sid,
                                            "reason": "idle"})
            with _TERMS_GUARD:
                terms.pop(sid, None)
            return
        time.sleep(POLL_S)
        idle += POLL_S
        if idle >= 15.0:
            idle = 0.0
            yield ": keepalive\n\n"


def _chunks(text: str) -> List[str]:
    return [text[i:i + MAX_FRAME_CHARS] for i in range(0, len(text),
                                                      MAX_FRAME_CHARS)]


def _frame(name: str, payload: Dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _error_frame(code: str, detail: str) -> str:
    return _frame("terminal_error", {"code": code, "error": detail})


def reset_for_tests() -> None:
    """Drop every live session (tests only)."""
    with _TERMS_GUARD:
        _TERMS.clear()
