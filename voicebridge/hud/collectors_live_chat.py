"""HUD live-chat collector — Qubit native; no upstream code is vendored here.

Upstream's Live Chat tab opens a chat WebSocket and streams turns. That
transport does not exist in this tree and must not be built a second time, so
the decision is a split one:

DECISION: GAP (live turn stream) + MAP (in-flight activity snapshot)
-------------------------------------------------------------------
GAP, stated on screen: there is no chat WebSocket to subscribe to.
``voicebridge/webui.py`` records the refusal in ``QUBIT_WS_GATE_CODES`` —
``/api/ws``, ``/api/pub``, ``/api/audio/speak-stream`` and
``/api/plugins/kanban/events`` all answer a typed ``WS_SINGLE_PATH``
("Qubit keeps one event path (SSE feed + ops)"). Building a chat WS inside a
HUD tab would be exactly the second transport that gate refuses.

MAP, what a viewer can honestly show instead: the ONE event path Qubit owns is
already durable — :class:`voicebridge.events.journal.EventJournal` over
``<state_dir>/typed-events.jsonl``, redacted at the write boundary by contract
("redaction applied at the write boundary"). Reading it gives the in-flight
picture a watcher actually wants:

* active runs (``RUN_STARTED`` without ``RUN_FINISHED``/``RUN_CANCELLED``);
* the last ``STATE_SNAPSHOT`` per run (stage + description), which is the
  agent's own progress line — not a chat bubble reconstructed from prose;
* the recent run/TEXT/REASONING event tail, verbatim and redacted.

It is a SNAPSHOT, not a stream: it is polled like every other tab, and the
panel says so. Composition of a turn (model deltas, tool args) only exists on
the SSE/WS the dashboard owns, so no delta is rendered here.

Read-only: ``EventJournal`` is opened for reading (its constructor only scans
for the last ``seq``, then this module calls ``read_all``). ``append`` is never
called.

Pure function, JSON-ready dict, never raises.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

#: Event tail shown. The journal is append-only and unbounded; the panel is not.
TAIL = 60
#: Runs tracked. Bounded so the snapshot stays one pass over the tail.
MAX_RUNS = 20

FINISH_TYPES = ("RUN_FINISHED", "RUN_CANCELLED", "RUN_ERROR")


def _journal_path(cfg: Any) -> Any:
    from pathlib import Path

    return Path(cfg.state_dir()) / "typed-events.jsonl"


def _read(cfg: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"available": False, "detail": "", "records": 0,
                           "path_present": False}
    try:
        path = _journal_path(cfg)
    except Exception as exc:  # noqa: BLE001 - absence is a typed answer
        out["detail"] = f"{type(exc).__name__}: {exc}"
        return out
    out["path_present"] = path.is_file()
    if not out["path_present"]:
        out.update(available=True,
                   detail="the typed event journal has not been written yet")
        return out
    try:
        from ..events.journal import EventJournal

        records = EventJournal(path, fsync=False).read_all()
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"event journal unreadable ({type(exc).__name__}: {exc})"
        return out
    out.update(available=True, records=len(records),
               detail=f"{len(records)} event(s) in the journal")
    return {"available": True, "detail": out["detail"], "records": len(records),
            "path_present": True, "items": records}


def collect_live_chat(home: Optional[Any] = None, cfg: Any = None, *,
                      tail: int = TAIL) -> Dict[str, Any]:
    """In-flight runs and the redacted event tail — a snapshot, not a stream."""
    out: Dict[str, Any] = {
        "available": False,
        "detail": "",
        "transport": {
            "available": False,
            "code": "WS_SINGLE_PATH",
            "detail": ("No chat WebSocket is served by this build. "
                       "voicebridge.webui refuses /api/ws, /api/pub and the "
                       "plugin event sockets with WS_SINGLE_PATH: Qubit keeps "
                       "one event path (SSE feed + ops). A HUD tab will not "
                       "open a second transport."),
            "counterpart": "GET /api/events/stream (SSE) + the Qubit Chat tab",
        },
        "path_present": False,
        "records": 0,
        "active_runs": [],
        "last_snapshot": {},
        "tail": [],
        "tail_count": 0,
        "authority": "voicebridge.events.journal.EventJournal "
                     "(typed-events.jsonl, redacted at write)",
    }
    read = _read(cfg)
    if not read.get("available"):
        out["detail"] = str(read.get("detail") or "event journal unavailable")
        return out
    out["path_present"] = bool(read.get("path_present"))
    out["records"] = int(read.get("records") or 0)
    items: List[Dict[str, Any]] = [r for r in (read.get("items") or [])
                                   if isinstance(r, dict)]
    if not items:
        out.update(available=True, detail=str(read.get("detail") or ""))
        return out

    started: Dict[str, Dict[str, Any]] = {}
    finished: set = set()
    snapshots: Dict[str, Dict[str, Any]] = {}
    for rec in items:
        etype = str(rec.get("type") or "")
        run_id = str(rec.get("run_id") or "")
        if etype == "RUN_STARTED" and run_id:
            started[run_id] = {
                "run_id": run_id,
                "thread_id": str(rec.get("thread_id") or ""),
                "started_at": rec.get("timestamp"),
            }
        elif etype in FINISH_TYPES and run_id:
            finished.add(run_id)
        elif etype == "STATE_SNAPSHOT" and run_id:
            snap = rec.get("snapshot")
            snapshots[run_id] = {
                "stage": str((snap or {}).get("stage", "")
                             if isinstance(snap, dict) else ""),
                "description": str((snap or {}).get("description", "")
                                   if isinstance(snap, dict) else "")[:300],
                "at": rec.get("timestamp"),
            }

    active = []
    for run_id, info in started.items():
        if run_id in finished:
            continue
        snap = snapshots.get(run_id) or {}
        active.append({**info,
                       "stage": snap.get("stage", ""),
                       "description": snap.get("description", "")})
    active.reverse()
    out["active_runs"] = active[:MAX_RUNS]

    recent_snap = None
    for rec in reversed(items):
        if str(rec.get("type") or "") == "STATE_SNAPSHOT":
            recent_snap = rec
            break
    if isinstance(recent_snap, dict):
        snap = recent_snap.get("snapshot")
        out["last_snapshot"] = {
            "run_id": str(recent_snap.get("run_id") or ""),
            "stage": str((snap or {}).get("stage", "")
                         if isinstance(snap, dict) else ""),
            "description": str((snap or {}).get("description", "")
                               if isinstance(snap, dict) else "")[:300],
            "at": recent_snap.get("timestamp"),
        }

    tail_rows = []
    for rec in items[-max(1, int(tail)):]:
        row = {
            "seq": rec.get("seq"),
            "type": str(rec.get("type") or ""),
            "at": rec.get("timestamp"),
            "run_id": str(rec.get("run_id") or ""),
        }
        detail = rec.get("delta") or rec.get("snapshot") or rec.get("message")
        if isinstance(detail, str):
            row["detail"] = detail[:300]
        elif isinstance(detail, dict):
            row["detail"] = str(detail.get("description")
                                or detail.get("text") or "")[:300]
        tail_rows.append(row)
    out.update(
        available=True,
        detail=(f"{len(out['active_runs'])} run(s) started without a matching "
                f"finish event, {len(items)} event(s) journaled "
                f"({len(started)} run(s) recorded)"),
        tail=tail_rows,
        tail_count=len(tail_rows),
        stream_note=("This is a polled SNAPSHOT of the durable event journal, "
                     "not a live stream: no token delta or tool-call "
                     "argument is delivered to this tab."),
    )
    return out


__all__ = ["collect_live_chat"]
