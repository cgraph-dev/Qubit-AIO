"""``/api/commands*``, ``/api/btw`` — the composer's command registry and side question.

WHAT THE PORTED CLIENT ACTUALLY CALLS (quoted from ``static/``)
---------------------------------------------------------------------------
``commands.js:544``   ``GET  /api/commands``    → ``Array.isArray(data.commands)``
                      ; each row is read as ``cmd.name``, ``cmd.description``,
                      ``cmd.aliases`` (array), ``cmd.args_hint``,
                      ``cmd.category``, ``cmd.cli_only``
``commands.js:695``   ``POST /api/commands/exec`` ``{command:"/name args"}``
                      → returns ``data.output`` rendered as the assistant turn
``commands.js:710``   ``POST /api/commands/bundles/resolve``
                      ``{command, session_id?}`` → reads ``data.message``
``commands.js:2790``  ``GET  /api/commands/bundles`` → ``data.bundles[]``, each
                      turned into a row by ``_buildBundleCommandEntry`` (reads
                      ``bundle.name``)
``messages.js:1927``  ``GET  /api/commands/moa/resolve`` → ``data.usage``
``commands.js:2379``  ``POST /api/btw`` ``{session_id, question}`` →
                      ``r.stream_id``, ``r.parent_session_id``, then opens
                      ``EventSource`` on the ephemeral ``/api/btw/stream`` and
                      listens for ``done`` (``e.data`` = ``{answer}``) and
                      ``apperror`` (``{message}``)

THE AUTHORITY FOR EACH
---------------------------------------------------------------------------
* ``/api/commands`` re-uses :func:`voicebridge.workbench_shim.system._command_rows`
  — the same catalog ``GET /api/chat/slash-commands`` serves, i.e. the
  canonical ``qubit_cli.commands.COMMAND_REGISTRY`` plus the gateway mixin scan,
  the skill catalog, the plugin registry and the toolset list. The ported menu
  and the Qubit chat palette therefore cannot disagree about what exists.
* ``/api/commands/exec`` runs the command through
  :func:`voicebridge.chat_transport.run_qubitchat_slash` — the ONE slash
  executor ``/qubit-chat`` and voice use. Its contract is already
  "read-only verbs hit the real backend; terminal-session verbs answer honest
  guidance; unknown verbs answer honest help", so ``output`` is either a real
  answer or a named miss, never phantom success.
* ``/api/btw`` is a side question against the parent session's context. The
  answer is produced by :func:`voicebridge.chat_transport.stream_turn` in a
  worker thread and read back from the ``/api/btw/stream`` SSE route; it is
  NEVER appended to the parent transcript (upstream calls it "ephemeral", and
  the only transcript this module writes is the parent's user question with a
  ``kind: btw`` meta so the transcript records that a side question was asked).

WHAT HAS NO QUBIT AUTHORITY (typed refusals)
---------------------------------------------------------------------------
``/api/commands/bundles`` — Qubit's command bundles are session-scoped runtime
objects created by the agent itself (``/bundles`` in the terminal), not a
persisted registry a viewer can list. An empty list would hide the fact.
``/api/commands/bundles/resolve`` — resolving a bundle needs the live bundle
object; without the registry there is nothing to resolve against.
``/api/commands/moa/resolve`` — mixture-of-agents is not a Qubit routing
concept; ``chat_transport`` has one route ladder and no MoA resolver.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any, Dict, Iterator, List, Optional, Tuple

from ..workbench_shim import _EMPTY_CFG, UPSTREAM_PIN

_OWNER = "qubit-command-authority"

#: Bound on one side-question stream; the client's own SSE listener treats an
#: ``apperror`` frame as the failure it is.
BTW_TIMEOUT_S = 180.0

#: How many finished side questions stay readable (a late EventSource attach
#: must still find its frame). One entry is a few KB at most.
_BTW_KEEP = 32


def _refusal(code: str, detail: str, *, endpoint: str, status: int = 0,
             counterpart: str = "",
             rollback: str = "read-only; nothing to roll back",
             **extra: Any) -> Tuple[int, Dict[str, Any]]:
    return (status or _STATUS.get(code, 502)), {
        "ok": False,
        "state": "UNAVAILABLE",
        "code": code,
        "endpoint": endpoint,
        "detail": detail,
        "qubit_counterpart": counterpart or "Qubit Chat (/qubit-chat) — /help",
        "owner": _OWNER,
        "rollback": rollback,
        "upstream": UPSTREAM_PIN,
        **extra,
    }


_STATUS: Dict[str, int] = {
    "WB_COMMANDS_UNAVAILABLE": 503,
    "WB_COMMAND_REQUIRED": 400,
    "WB_SESSION_ID_REQUIRED": 400,
    "WB_SESSION_NOT_FOUND": 409,
    "WB_CAPABILITY_UNBOUND": 501,
    "COMMANDS_UNAVAILABLE": 503,
    "EMERGENCY_STOP": 403,
    "AGENT_DISABLED": 403,
    "MALFORMED": 400,
}


def _body(b: Any) -> Dict[str, Any]:
    return b if isinstance(b, dict) else {}


def _q(q: Any, *names: str) -> str:
    if not isinstance(q, dict):
        return ""
    for name in names:
        value = q.get(name)
        if value is not None and str(value).strip() != "":
            return str(value)
    return ""


# ------------------------------------------------------------------ catalog
def commands(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
             p: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """``GET /api/commands`` — Qubit's real slash catalog in upstream's shape."""
    try:
        from ..workbench_shim import system as SYS

        rows = SYS._command_rows(cfg)
    except Exception as exc:  # noqa: BLE001 - absence is a typed answer
        return _refusal(
            "COMMANDS_UNAVAILABLE",
            f"the slash catalog could not be built: {type(exc).__name__}: {exc}",
            endpoint="commands")
    if not rows:
        return _refusal(
            "COMMANDS_UNAVAILABLE",
            "the slash catalog is empty: no CLI-registry verb, gateway command, "
            "skill, plugin or toolset resolved, so the composer has nothing "
            "real to offer rather than a list it would have to invent",
            endpoint="commands")
    return 200, {"ok": True, "commands": rows, "total": len(rows),
                 "source": "GET /api/chat/slash-commands",
                 "upstream": UPSTREAM_PIN}


# --------------------------------------------------------------------- exec
def commands_exec(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                  p: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """``POST /api/commands/exec`` — run one slash command on Qubit's executor."""
    command = str(_body(b).get("command") or "").strip()
    if not command:
        return _refusal("WB_COMMAND_REQUIRED",
                        "POST /api/commands/exec needs `command`",
                        endpoint="commands/exec")
    try:
        from .. import chat_transport as CT

        parsed = CT.parse_qubitchat_slash(command)
        if parsed is None:
            # A slash-less body is the client misbehaving, not a command.
            return _refusal(
                "WB_COMMAND_REQUIRED",
                f"/api/commands/exec only runs a leading-slash command; "
                f"{command[:40]!r} is not one. Send a plain message through "
                f"POST /api/chat/start instead.",
                endpoint="commands/exec")
        out = CT.run_qubitchat_slash(cfg, command,
                                     session_id=str(_q(q, "session_id")))
    except Exception as exc:  # noqa: BLE001
        return _refusal(
            "COMMANDS_UNAVAILABLE",
            f"the slash executor raised: {type(exc).__name__}: {exc}",
            endpoint="commands/exec")
    if out.get("ok") is not True:
        return (_STATUS.get(str(out.get("code") or ""), 502), {
            "ok": False, "state": "FAILED", "code": out.get("code"),
            "endpoint": "commands/exec",
            "detail": str(out.get("detail") or "the command failed"),
            "command": command, "upstream": UPSTREAM_PIN})
    text = str(out.get("text") or out.get("reply") or "")
    return 200, {"ok": True, "output": text, "text": text,
                 "entry": out.get("entry"), "route": out.get("route"),
                 "upstream": UPSTREAM_PIN}


# ------------------------------------------------------------------ bundles
def commands_bundles(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                     p: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """``GET /api/commands/bundles`` — no persisted bundle registry exists."""
    # Upstream: a persisted list of composite commands the server loads from
    # disk. Qubit ships none: `loadBundleCommands` would cache the rows and the
    # composer would offer names that resolve to nothing.
    return _refusal(
        "WB_CAPABILITY_UNBOUND",
        "Qubit has no listable command-bundle registry: bundles are "
        "session-scoped objects the agent creates at runtime (`/bundles` in "
        "the terminal lists the ones in THIS session), not a catalog the "
        "dashboard can enumerate. An empty list would read as 'you have "
        "none', so this refuses instead.",
        endpoint="commands/bundles", status=501,
        counterpart="Qubit Chat: /bundles (in-session)")


def commands_bundles_resolve(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                             p: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """``POST /api/commands/bundles/resolve`` — nothing to resolve against."""
    command = str(_body(b).get("command") or "").strip()
    return _refusal(
        "WB_CAPABILITY_UNBOUND",
        f"Qubit cannot expand {command[:60]!r}: bundle expansion needs the live "
        f"in-session bundle object, and there is no bundle registry to resolve "
        f"a name against (see /api/commands/bundles). Nothing was invented and "
        f"no turn was started.",
        endpoint="commands/bundles/resolve", status=501,
        counterpart="Qubit Chat: /bundles, then send the expanded prompt")


def commands_moa_resolve(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
                         p: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """``GET /api/commands/moa/resolve`` — mixture-of-agents is not a Qubit route."""
    return _refusal(
        "WB_CAPABILITY_UNBOUND",
        "Qubit has no mixture-of-agents resolver: chat_transport has one "
        "route ladder (local brain / configured provider / worker) and no "
        "fan-out across several models per turn, so there is no usage string "
        "or config to return.",
        endpoint="commands/moa/resolve", status=501,
        counterpart="Qubit Settings → model routing (one route per turn)")


# ---------------------------------------------------------------------- btw
class _BtwRuns:
    """Finished side questions, by stream id (bounded, in-process).

    A run is startable and answerable within one listener process; the client
    opens the EventSource immediately after the POST, so this only has to hold
    the frame until the attach plus the retention window. Nothing is written to
    the parent transcript beyond the user's own question.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: Dict[str, Dict[str, Any]] = {}
        self._order: List[str] = []

    def start(self, parent: str, question: str, run: Dict[str, Any]) -> str:
        sid = "btw-" + uuid.uuid4().hex[:16]
        with self._lock:
            self._runs[sid] = {"parent_session_id": parent,
                               "question": question, "run": run,
                               "created_at": time.time()}
            self._order.append(sid)
            while len(self._order) > _BTW_KEEP:
                self._runs.pop(self._order.pop(0), None)
        return sid

    def get(self, sid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._runs.get(str(sid or ""))


BTW_RUNS = _BtwRuns()


def _btw_preflight(cfg: Any, question: str) -> Optional[Tuple[int, Dict[str, Any]]]:
    """The same preflight a normal turn passes, before any stream exists."""
    try:
        from .. import chat_transport as CT

        status, refusal, _model = CT.stream_preflight(
            cfg if cfg is not None else _EMPTY_CFG, question, None,
            session=None)
    except Exception as exc:  # noqa: BLE001
        return _refusal("WB_CAPABILITY_UNBOUND",
                        f"chat_transport is not reachable: "
                        f"{type(exc).__name__}: {exc}", endpoint="btw")
    if refusal is not None:
        return int(status or 502), {
            "ok": False, "state": "FAILED",
            "code": str(refusal.get("code") or "TURN_REFUSED"),
            "endpoint": "btw",
            "detail": str(refusal.get("detail") or ""),
            "upstream": UPSTREAM_PIN}
    return None


def _btw_pump(cfg: Any, run: Dict[str, Any], question: str,
              parent: str) -> None:
    """Drive one side-question turn and record its typed outcome."""
    answer = ""
    error: Optional[Dict[str, Any]] = None
    try:
        from .. import chat_transport as CT

        pass_cfg = cfg if cfg is not None else _EMPTY_CFG
        for event in CT.stream_turn(pass_cfg, question, session=None,
                                    timeout_s=BTW_TIMEOUT_S,
                                    role="btw", actor="workbench:btw"):
            kind = str(event.get("event") or "")
            if kind == "delta":
                answer += str(event.get("text") or "")
            elif kind == "done":
                reply = str(event.get("reply") or "")
                if reply:
                    answer = reply
            elif kind in ("error", "cancelled"):
                error = {"code": str(event.get("code") or kind.upper()),
                         "message": str(event.get("detail")
                                        or "the side question failed")}
                break
    except Exception as exc:  # noqa: BLE001 - a fault is a typed frame
        error = {"code": "BTW_FAULT",
                 "message": f"{type(exc).__name__}: {exc}"[:400]}
    with run["lock"]:
        run["answer"] = answer
        run["error"] = error
        run["done"] = True
    _btw_audit(parent, question, bool(error))


def _btw_audit(parent: str, question: str, failed: bool) -> None:
    try:
        from ..logging_setup import log_event

        log_event("workbench.btw", actor="workbench:user",
                  parent_session=str(parent)[:80], failed=str(failed),
                  chars=str(len(question)))
    except Exception:
        pass


def btw(cfg: Any, q: Dict[str, Any], b: Dict[str, Any],
        p: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """``POST /api/btw`` — start one ephemeral side question (``/api/btw/stream``).

    The parent session must exist (the question is about ITS context), and the
    turn is recorded on the parent as a ``kind: btw`` user message so the
    transcript shows that a side question was asked — the ANSWER is never
    appended, which is what "ephemeral" means on upstream too.
    """
    body = _body(b)
    sid = _q(body, "session_id", "parent_session_id") or _q(q, "session_id")
    question = str(body.get("question") or body.get("prompt") or "").strip()
    if not sid:
        return _refusal("WB_SESSION_ID_REQUIRED",
                        "POST /api/btw needs session_id",
                        endpoint="btw")
    if not question:
        return _refusal("WB_COMMAND_REQUIRED",
                        "POST /api/btw needs a non-empty `question`",
                        endpoint="btw")
    try:
        from ..chat_transport import store as _store

        chat_store = _store(cfg)
    except Exception as exc:  # noqa: BLE001
        return _refusal("WB_CAPABILITY_UNBOUND",
                        f"the chat-session store is not reachable: "
                        f"{type(exc).__name__}: {exc}", endpoint="btw")
    if chat_store.show(sid).get("state") != "OK":
        return _refusal("WB_SESSION_NOT_FOUND",
                        f"no chat session {sid!r}; /btw answers about an "
                        f"existing session's context", endpoint="btw")
    refused = _btw_preflight(cfg, question)
    if refused is not None:
        return refused
    run: Dict[str, Any] = {"lock": threading.Lock(), "done": False,
                           "answer": "", "error": None}
    stream_id = BTW_RUNS.start(sid, question, run)
    # Record the question on the parent (real, visible), never the answer.
    # ``display_metadata`` is the ONE sidecar the session store persists for a
    # message beyond its tool fields (``kind``/``stream_id`` alone are dropped by
    # ``QubitSessionStore.append`` — measured, the row came back with only an
    # id), so the marker rides there under a namespaced key.
    try:
        chat_store.append(sid, "user", question,
                          meta={"display_metadata": {"btw": {
                              "stream_id": stream_id, "ephemeral": True}}})
    except Exception:  # noqa: BLE001 - the answer still runs; a lost echo is not a failure
        pass
    threading.Thread(target=_btw_pump, name="wb-btw", daemon=True,
                     args=(cfg, run, question, sid)).start()
    return 200, {
        "ok": True,
        "stream_id": stream_id,
        "parent_session_id": sid,
        "question": question,
        "stream": "/api/btw/stream?stream_id=" + stream_id,
        "ephemeral": True,
        "upstream": UPSTREAM_PIN,
    }


def btw_stream_frames(stream_id: str) -> Iterator[str]:
    """The SSE frames for one side question: wait, then ``done`` or ``apperror``.

    Bounded by the turn's own timeout, and an unknown id is an ``apperror`` —
    never an empty stream the client would sit on.
    """
    entry = BTW_RUNS.get(stream_id)
    if entry is None:
        yield ('event: apperror\n'
               'data: {"message": "no such side question stream; it expired or '
               'was started by another process"}\n\n')
        return
    run = entry["run"]
    deadline = time.time() + BTW_TIMEOUT_S + 5.0
    yield 'event: start\ndata: {"event": "start"}\n\n'
    while time.time() < deadline:
        with run["lock"]:
            if run["done"]:
                break
        time.sleep(0.1)
    with run["lock"]:
        done = bool(run["done"])
        answer = str(run["answer"] or "")
        error = run["error"]
    if not done:
        yield ('event: apperror\n'
               'data: {"message": "the side question did not finish in time"}\n\n')
        return
    if error:
        yield ("event: apperror\ndata: "
               + json.dumps({"code": error.get("code"),
                             "message": error.get("message")}) + "\n\n")
        return
    yield ("event: done\ndata: " + json.dumps({"answer": answer}) + "\n\n")


# ------------------------------------------------------------- the routes
HANDLERS: Dict[str, Any] = {
    "commands": commands,
    "commands_exec": commands_exec,
    "commands_bundles": commands_bundles,
    "commands_bundles_resolve": commands_bundles_resolve,
    "commands_moa_resolve": commands_moa_resolve,
    "btw": btw,
}
