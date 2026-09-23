"""Qubit task boards: a local task board with widget canvas.

Qubit implements boards and tasks on one canonical SQLite file
(``state/kanban.db``) and routes execution-shaped work to the authorities
that already own it:

* IMPLEMENTED here: ``init``, ``boards`` (create/list), ``create``,
  ``list``/``ls``, ``show``, ``claim``, ``assign``, ``reassign``,
  ``reclaim``, ``comment``, ``complete``, ``edit``, ``block``,
  ``unblock``, ``archive``, ``stats``, ``log``, ``export``, ``import``.
  Claims are atomic (``BEGIN IMMEDIATE``); every mutation needs explicit
  ``--confirm`` (top-level ``--dry-run`` previews), refuses while the
  emergency stop is latched, writes redacted audit events via
  :func:`log_event`, and honors title/body/comment/list caps.
  ``export``/``import`` (JSON) are the rollback mechanism.
* DELEGATED (typed ``KANBAN_DELEGATED``, nothing started): ``swarm``,
  ``dispatch``, ``daemon``, ``watch``, ``notify-subscribe``,
  ``notify-list``, ``notify-unsubscribe``, ``heartbeat``, ``runs`` — these
  would duplicate Qubit's scheduler/monitor (8D), supervised runs (8R), and
  notification paths. The refusal names the Qubit-native alternative.
* Anything else (``decompose``, ``attach``, ``promote``, …) is typed
  ``KANBAN_UNSUPPORTED`` — no subcommand is silently omitted; every
  invocation gets a typed answer.

Task assignees are free-text labels. Execution of a
claimed task happens through the existing supervised run paths, never in
this module.

The O-board widget canvas (``board_open``, ``move_task``, ``widget_*``,
``layout_*``, ``role_*``, ``snapshot_*`` below) is the visual surface
around the same store: per-board column layout, sandboxed widgets
(active markup stripped, never executed), per-board delegate roles,
and auto-snapshots for rollback. Same confirm/estop/audit/redaction
contracts; CLI, dashboard, and voice share every function.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = "qubit-kanban/1"
DB_NAME = "kanban.db"
MAX_TITLE_LEN = 200
MAX_BODY_LEN = 8000
MAX_COMMENT_LEN = 2000
MAX_BOARDS = 64
MAX_OPEN_TASKS_PER_BOARD = 2000
LIST_DEFAULT = 50
LIST_MAX = 200

CONFIRM_CODE = "KANBAN_CONFIRM_REQUIRED"
ESTOP_CODE = "KANBAN_ESTOP"
NOT_FOUND_CODE = "KANBAN_NOT_FOUND"
EXISTS_CODE = "KANBAN_EXISTS"
BAD_INPUT_CODE = "KANBAN_BAD_INPUT"
STATE_CODE = "KANBAN_BAD_STATE"
DELEGATED_CODE = "KANBAN_DELEGATED"
UNSUPPORTED_CODE = "KANBAN_UNSUPPORTED"
IO_CODE = "KANBAN_IO"

STATUSES = ("open", "claimed", "blocked", "done", "archived")
PRIORITIES = ("low", "normal", "high")
_BOARD_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# Subcommands that would duplicate an existing Qubit authority, mapped to
# the Qubit-native path named in the refusal.
DELEGATED = {
    "swarm": "bounded parallel reads + serialized writes through the Qubit "
             "broker (Packet 8 delegation); no worker swarm is started here",
    "dispatch": "supervised runs via `qubit opencode` (Packet 8R); "
                "no dispatch queue runs here",
    "daemon": "durable monitor jobs via `qubit monitor` (Packet 8D); "
              "no background daemon runs here",
    "watch": "monitor jobs + journal evidence via `qubit monitor` "
             "(Packet 8D); no watcher runs here",
    "notify-subscribe": "deduplicated notifications with quiet hours via "
                        "`qubit monitor` (Packet 8D)",
    "notify-list": "notification state via `qubit monitor` (Packet 8D)",
    "notify-unsubscribe": "notification state via `qubit monitor` "
                          "(Packet 8D)",
    "heartbeat": "run liveness via `qubit opencode` run state "
                 "(Packet 8R); no heartbeat loop runs here",
    "runs": "run history via `qubit opencode` (Packet 8R)",
}


class KanbanError(Exception):
    """Typed kanban failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _redact(text: str) -> str:
    from .redaction import redact_text

    return redact_text(text)


def redact_spec(text: str) -> str:
    """Redact user-supplied titles/bodies before echoing them."""
    return _redact(text)


def _db_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / DB_NAME


def _connect(cfg: Any) -> sqlite3.Connection:
    path = _db_path(cfg)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=30.0,
                               check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass
        return conn
    except sqlite3.Error as exc:
        raise KanbanError(IO_CODE, f"kanban store unavailable: {exc}") from exc


def _migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS boards("
        " slug TEXT PRIMARY KEY, name TEXT NOT NULL,"
        " project_slug TEXT, created_at REAL NOT NULL);"
        "CREATE TABLE IF NOT EXISTS tasks("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, board TEXT NOT NULL"
        " REFERENCES boards(slug), title TEXT NOT NULL,"
        " body TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open',"
        " assignee TEXT, priority TEXT NOT NULL DEFAULT 'normal',"
        " created_at REAL NOT NULL, updated_at REAL NOT NULL);"
        "CREATE INDEX IF NOT EXISTS idx_tasks_board ON tasks(board, status);"
        "CREATE TABLE IF NOT EXISTS comments("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL"
        " REFERENCES tasks(id), author TEXT NOT NULL DEFAULT '',"
        " body TEXT NOT NULL, created_at REAL NOT NULL);"
        "CREATE INDEX IF NOT EXISTS idx_comments_task ON comments(task_id);"
        "CREATE TABLE IF NOT EXISTS events("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL,"
        " kind TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',"
        " created_at REAL NOT NULL);"
        "CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id);")
    # Additive-only: remembers the status a task held before it was archived so
    # a later restore returns it there (Qubit workboard "Restore from
    # archive" parity) instead of silently resetting to ``open``.
    _ensure_column(conn, "tasks", "archived_from", "TEXT")


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    from .logging_setup import log_event

    try:
        log_event(f"kanban.{op}", actor=actor, **detail)
    except Exception:
        pass


def _now() -> float:
    return time.time()


def _check_board_slug(slug: str) -> str:
    text = (slug or "").strip().lower()
    if not _BOARD_RE.match(text):
        raise KanbanError(BAD_INPUT_CODE,
                          "board slug must match ^[a-z0-9][a-z0-9-]{0,63}$")
    return text


def _check_title(title: str) -> str:
    text = (title or "").strip()
    if not text or len(text) > MAX_TITLE_LEN:
        raise KanbanError(BAD_INPUT_CODE,
                          f"title must be 1..{MAX_TITLE_LEN} chars")
    return text


def _check_body(body: str, limit: int = MAX_BODY_LEN) -> str:
    if len(body or "") > limit:
        raise KanbanError(BAD_INPUT_CODE,
                          f"body must be <={limit} chars")
    return body or ""


def _task_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {"id": row["id"], "board": row["board"],
            "title": redact_spec(row["title"]),
            "body": redact_spec(row["body"]), "status": row["status"],
            "assignee": redact_spec(row["assignee"] or ""),
            "priority": row["priority"],
            "created_at": row["created_at"], "updated_at": row["updated_at"]}


def _event(conn: sqlite3.Connection, task_id: int, kind: str,
           detail: str = "") -> None:
    conn.execute("INSERT INTO events(task_id, kind, detail, created_at)"
                 " VALUES(?,?,?,?)", (task_id, kind, detail[:500], _now()))


def _get_task(conn: sqlite3.Connection, task_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM tasks WHERE id=?",
                       (task_id,)).fetchone()
    if row is None:
        raise KanbanError(NOT_FOUND_CODE, f"no task #{task_id}")
    return row


def _get_board(conn: sqlite3.Connection, slug: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM boards WHERE slug=?",
                       (_check_board_slug(slug),)).fetchone()
    if row is None:
        raise KanbanError(NOT_FOUND_CODE, f"no board {slug!r}")
    return row


# ------------------------------------------------------------------- boards
def init(cfg: Any) -> Dict[str, Any]:
    """Create the board store if missing (idempotent)."""
    conn = _connect(cfg)
    try:
        _migrate(conn)
        conn.commit()
        return {"state": "OK", "store": str(_db_path(cfg))}
    finally:
        conn.close()


def boards_create(cfg: Any, slug: str, name: str = "",
                  *, requested_by: str = "cli:user",
                  confirmed: bool = False) -> Dict[str, Any]:
    """Create one board (one board per project/workstream)."""
    if not confirmed:
        raise KanbanError(CONFIRM_CODE,
                          "kanban boards create requires explicit --confirm")
    clean = _check_board_slug(slug)
    label = (name or clean).strip()[:MAX_TITLE_LEN] or clean
    conn = _connect(cfg)
    try:
        _migrate(conn)
        if conn.execute("SELECT COUNT(*) c FROM boards").fetchone()["c"] \
                >= MAX_BOARDS:
            raise KanbanError(EXISTS_CODE,
                              f"board limit reached ({MAX_BOARDS})")
        try:
            conn.execute("INSERT INTO boards(slug, name, created_at)"
                         " VALUES(?,?,?)", (clean, label, _now()))
        except sqlite3.IntegrityError:
            raise KanbanError(EXISTS_CODE,
                              f"board {clean!r} exists") from None
        conn.commit()
        _audit("boards.create", requested_by, {"board": clean})
        return {"state": "OK", "board": clean, "name": redact_spec(label)}
    finally:
        conn.close()


def boards_list(cfg: Any) -> Dict[str, Any]:
    """List boards with per-status counts (read-only)."""
    conn = _connect(cfg)
    try:
        _migrate(conn)
        out = []
        for brow in conn.execute("SELECT * FROM boards ORDER BY slug"):
            counts = {s: 0 for s in STATUSES}
            for trow in conn.execute(
                    "SELECT status, COUNT(*) c FROM tasks WHERE board=? "
                    "GROUP BY status", (brow["slug"],)):
                counts[trow["status"]] = trow["c"]
            out.append({"board": brow["slug"],
                        "name": redact_spec(brow["name"]),
                        "project_slug": brow["project_slug"],
                        "counts": counts})
        return {"state": "OK", "boards": out}
    finally:
        conn.close()


def board_exists(cfg: Any, slug: str) -> bool:
    """Read-only board presence check (used by project bind-board)."""
    conn = _connect(cfg)
    try:
        _migrate(conn)
        return conn.execute("SELECT 1 FROM boards WHERE slug=?",
                            (_check_board_slug(slug),)).fetchone() is not None
    except KanbanError:
        return False
    finally:
        conn.close()


def set_board_project(cfg: Any, slug: str, project_slug: Optional[str],
                      *, requested_by: str = "cli:user") -> None:
    """Link/unlink a board to a project slug (bind-board plumbing)."""
    conn = _connect(cfg)
    try:
        _migrate(conn)
        _get_board(conn, slug)
        conn.execute("UPDATE boards SET project_slug=? WHERE slug=?",
                     (project_slug, _check_board_slug(slug)))
        conn.commit()
        _audit("boards.bind", requested_by,
               {"board": _check_board_slug(slug),
                "project_slug": project_slug or ""})
    finally:
        conn.close()


# -------------------------------------------------------------------- tasks
def create(cfg: Any, title: str, *, board: str = "main", body: str = "",
           assignee: str = "", priority: str = "normal",
           requested_by: str = "cli:user",
           confirmed: bool = False) -> Dict[str, Any]:
    """Create a task (status open)."""
    if not confirmed:
        raise KanbanError(CONFIRM_CODE,
                          "kanban create requires explicit --confirm")
    clean_title = _check_title(title)
    clean_body = _check_body(body)
    clean_board = _check_board_slug(board)
    if priority not in PRIORITIES:
        raise KanbanError(BAD_INPUT_CODE,
                          f"priority must be one of {PRIORITIES}")
    conn = _connect(cfg)
    try:
        _migrate(conn)
        brow = conn.execute("SELECT 1 FROM boards WHERE slug=?",
                            (clean_board,)).fetchone()
        if brow is None:
            if clean_board == "main":
                conn.execute("INSERT INTO boards(slug, name, created_at)"
                             " VALUES('main','main',?)", (_now(),))
            else:
                raise KanbanError(NOT_FOUND_CODE,
                                  f"no board {clean_board!r}")
        nopen = conn.execute(
            "SELECT COUNT(*) c FROM tasks WHERE board=? AND status!='archived'",
            (clean_board,)).fetchone()["c"]
        if nopen >= MAX_OPEN_TASKS_PER_BOARD:
            raise KanbanError(EXISTS_CODE,
                              f"board {clean_board!r} is full")
        cur = conn.execute(
            "INSERT INTO tasks(board, title, body, status, assignee,"
            " priority, created_at, updated_at)"
            " VALUES(?,?,'', 'open',?,?,?,?)",
            (clean_board, clean_title, (assignee or "").strip()[:80],
             priority, _now(), _now()))
        task_id = cur.lastrowid
        if clean_body:
            conn.execute("UPDATE tasks SET body=? WHERE id=?",
                         (clean_body, task_id))
        _event(conn, task_id, "create", f"board={clean_board}")
        conn.commit()
        _audit("create", requested_by,
               {"board": clean_board, "task_id": task_id})
        return {"state": "OK",
                "task": _task_dict(_get_task(conn, task_id))}
    finally:
        conn.close()


def list_tasks(cfg: Any, *, board: str = "", status: str = "",
               limit: int = LIST_DEFAULT) -> Dict[str, Any]:
    """List tasks newest-first (read-only, bounded)."""
    limit = max(1, min(int(limit or LIST_DEFAULT), LIST_MAX))
    conn = _connect(cfg)
    try:
        _migrate(conn)
        query = "SELECT * FROM tasks"
        clauses, params = [], []
        if board:
            clauses.append("board=?")
            params.append(_check_board_slug(board))
        if status:
            if status not in STATUSES:
                raise KanbanError(BAD_INPUT_CODE,
                                  f"status must be one of {STATUSES}")
            clauses.append("status=?")
            params.append(status)
        else:
            clauses.append("status!='archived'")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return {"state": "OK",
                "tasks": [_task_dict(r) for r in rows], "limit": limit}
    finally:
        conn.close()


def show(cfg: Any, task_id: int) -> Dict[str, Any]:
    """Show a task with comments + events (read-only)."""
    conn = _connect(cfg)
    try:
        _migrate(conn)
        task = _task_dict(_get_task(conn, int(task_id)))
        comments = [
            {"id": r["id"], "author": redact_spec(r["author"]),
             "body": redact_spec(r["body"]), "created_at": r["created_at"]}
            for r in conn.execute(
                "SELECT * FROM comments WHERE task_id=? ORDER BY id",
                (task["id"],))]
        events = [
            {"kind": r["kind"], "detail": redact_spec(r["detail"]),
             "created_at": r["created_at"]}
            for r in conn.execute(
                "SELECT * FROM events WHERE task_id=? ORDER BY id",
                (task["id"],))]
        task["comments"] = comments
        task["events"] = events
        return {"state": "OK", "task": task}
    finally:
        conn.close()


def current_task(cfg: Any) -> Dict[str, Any]:
    """Resolve "the current task": the newest actionable (open/claimed/
    blocked) task across boards, with comments + events (read-only).

    Shared by `qubit kanban current`, the voice `kanban_current` entry
    (same backend as CLI/dashboard), and `task --current` resolution.
    Empty boards / nothing actionable return a typed EMPTY state —
    never a fabricated task."""
    conn = _connect(cfg)
    try:
        _migrate(conn)
        row = conn.execute(
            "SELECT id FROM tasks WHERE status IN ('open','claimed','blocked')"
            " ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return {"state": "EMPTY", "code": "NO_OPEN_TASKS",
                    "detail": "no open, claimed, or blocked tasks; "
                              "create one with `qubit kanban create`",
                    "owner": "qubit-policy",
                    "rollback": "board untouched; nothing to roll back"}
        task_id = int(row["id"])
    finally:
        conn.close()
    out = show(cfg, task_id)
    out["current"] = True
    return out


def _transition(cfg: Any, task_id: int, to: str, *,
                requested_by: str, confirmed: bool, op: str,
                assignee: str = "", event_detail: str = "") -> Dict[str, Any]:
    if not confirmed:
        raise KanbanError(CONFIRM_CODE,
                          f"kanban {op} requires explicit --confirm")
    conn = _connect(cfg)
    try:
        _migrate(conn)
        # Atomic claim/transition: one writer wins; concurrent claimants get
        # the loser's typed state error, never a double-claim.
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = _get_task(conn, int(task_id))
            task = _task_dict(row)
            if op == "claim" and task["status"] not in ("open", "blocked"):
                raise KanbanError(STATE_CODE,
                                  f"task #{task_id} is {task['status']}; "
                                  f"only open/blocked tasks can be claimed")
            if op == "complete" and task["status"] == "archived":
                raise KanbanError(STATE_CODE,
                                  f"task #{task_id} is archived")
            if op in ("block", "unblock") and task["status"] == "archived":
                raise KanbanError(STATE_CODE,
                                  f"task #{task_id} is archived")
            if op == "archive" and task["status"] == "archived":
                raise KanbanError(STATE_CODE,
                                  f"task #{task_id} is already archived")
            updates = "status=?, updated_at=?"
            params: List[Any] = [to, _now()]
            if op in ("claim", "assign", "reassign"):
                who = (assignee or requested_by).strip()[:80]
                updates += ", assignee=?"
                params.append(who)
            if op == "reclaim":
                updates += ", assignee=NULL"
            if op == "archive":
                # Remember the pre-archive column so ``restore`` can return the
                # task exactly where it was (never a fabricated default).
                updates += ", archived_from=?"
                params.append(task["status"])
            params.append(task["id"])
            conn.execute(f"UPDATE tasks SET {updates} WHERE id=?", params)
            _event(conn, task["id"], op,
                   event_detail or f"status={to}")
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            raise
        _audit(op, requested_by, {"task_id": task["id"]})
        return {"state": "OK", "task": _task_dict(_get_task(conn, task["id"]))}
    finally:
        conn.close()


def claim(cfg: Any, task_id: int, *, assignee: str = "",
          requested_by: str = "cli:user",
          confirmed: bool = False) -> Dict[str, Any]:
    """Atomically claim an open/blocked task (losers get a typed error)."""
    return _transition(cfg, task_id, "claimed", requested_by=requested_by,
                       confirmed=confirmed, op="claim", assignee=assignee)


def assign(cfg: Any, task_id: int, assignee: str, *,
           requested_by: str = "cli:user",
           confirmed: bool = False) -> Dict[str, Any]:
    """Set the assignee label without changing status."""
    if not (assignee or "").strip():
        raise KanbanError(BAD_INPUT_CODE, "assignee is required")
    conn = _connect(cfg)
    try:
        _migrate(conn)
        if not confirmed:
            raise KanbanError(CONFIRM_CODE,
                              "kanban assign requires explicit --confirm")
        row = _get_task(conn, int(task_id))
        if row["status"] == "archived":
            raise KanbanError(STATE_CODE, f"task #{task_id} is archived")
        conn.execute("UPDATE tasks SET assignee=?, updated_at=? WHERE id=?",
                     (assignee.strip()[:80], _now(), row["id"]))
        _event(conn, row["id"], "assign", f"assignee={assignee.strip()[:80]}")
        conn.commit()
        _audit("assign", requested_by, {"task_id": row["id"]})
        return {"state": "OK", "task": _task_dict(_get_task(conn, row["id"]))}
    finally:
        conn.close()


def reassign(cfg: Any, task_id: int, assignee: str, *,
             requested_by: str = "cli:user",
             confirmed: bool = False) -> Dict[str, Any]:
    """Move a claimed task to another assignee (stays claimed)."""
    return _transition(cfg, task_id, "claimed", requested_by=requested_by,
                       confirmed=confirmed, op="reassign", assignee=assignee,
                       event_detail=f"reassigned to {(assignee or requested_by).strip()[:80]}")


def reclaim(cfg: Any, task_id: int, *, requested_by: str = "cli:user",
            confirmed: bool = False) -> Dict[str, Any]:
    """Release a claim back to open (unassigns)."""
    return _transition(cfg, task_id, "open", requested_by=requested_by,
                       confirmed=confirmed, op="reclaim")


def complete(cfg: Any, task_id: int, *, requested_by: str = "cli:user",
             confirmed: bool = False) -> Dict[str, Any]:
    """Mark a task done (archived tasks cannot be completed)."""
    return _transition(cfg, task_id, "done", requested_by=requested_by,
                       confirmed=confirmed, op="complete")


def block(cfg: Any, task_id: int, *, requested_by: str = "cli:user",
          confirmed: bool = False) -> Dict[str, Any]:
    """Mark a task blocked."""
    return _transition(cfg, task_id, "blocked", requested_by=requested_by,
                       confirmed=confirmed, op="block")


def unblock(cfg: Any, task_id: int, *, requested_by: str = "cli:user",
            confirmed: bool = False) -> Dict[str, Any]:
    """Return a blocked task to open."""
    return _transition(cfg, task_id, "open", requested_by=requested_by,
                       confirmed=confirmed, op="unblock")


def archive_task(cfg: Any, task_id: int, *, requested_by: str = "cli:user",
                 confirmed: bool = False) -> Dict[str, Any]:
    """Archive a task (hidden from default lists)."""
    return _transition(cfg, task_id, "archived", requested_by=requested_by,
                       confirmed=confirmed, op="archive")


def restore_task(cfg: Any, task_id: int, *, requested_by: str = "cli:user",
                 confirmed: bool = False) -> Dict[str, Any]:
    """Restore an archived task (Qubit workboard "Restore from archive").

    Returns the task to the exact status it held before archiving when the
    store recorded it, else to ``open``. Non-archived tasks are a typed state
    error, and every mutation is atomic, audited, and confirmation-gated —
    the same shared path the CLI, dashboard, and voice all reach.
    """
    if not confirmed:
        raise KanbanError(CONFIRM_CODE,
                          "kanban restore requires explicit --confirm")
    conn = _connect(cfg)
    try:
        _migrate(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = _get_task(conn, int(task_id))
            if row["status"] != "archived":
                raise KanbanError(STATE_CODE,
                                  f"task #{task_id} is not archived")
            keys = _row_keys(row)
            prior = str(row["archived_from"] or "") \
                if "archived_from" in keys else ""
            target = prior if prior in STATUSES \
                and prior != "archived" else "open"
            conn.execute(
                "UPDATE tasks SET status=?, archived_from=NULL, updated_at=?"
                " WHERE id=?", (target, _now(), row["id"]))
            _event(conn, row["id"], "restore", f"restored to status={target}")
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            raise
        _audit("restore", requested_by,
               {"task_id": row["id"], "status": target})
        out = _task_dict(_get_task(conn, row["id"]))
        out["restored_from"] = "archived"
        return {"state": "OK", "task": out}
    finally:
        conn.close()


def edit(cfg: Any, task_id: int, *, title: str = "", body: str = "",
         priority: str = "", requested_by: str = "cli:user",
         confirmed: bool = False) -> Dict[str, Any]:
    """Edit title/body/priority (archived tasks are immutable)."""
    if not confirmed:
        raise KanbanError(CONFIRM_CODE,
                          "kanban edit requires explicit --confirm")
    if priority and priority not in PRIORITIES:
        raise KanbanError(BAD_INPUT_CODE,
                          f"priority must be one of {PRIORITIES}")
    if title:
        _check_title(title)
    if body:
        _check_body(body)
    if not title and not body and not priority:
        raise KanbanError(BAD_INPUT_CODE, "nothing to edit")
    conn = _connect(cfg)
    try:
        _migrate(conn)
        row = _get_task(conn, int(task_id))
        if row["status"] == "archived":
            raise KanbanError(STATE_CODE, f"task #{task_id} is archived")
        sets, params = [], []
        if title:
            sets.append("title=?")
            params.append(title.strip())
        if body:
            sets.append("body=?")
            params.append(body)
        if priority:
            sets.append("priority=?")
            params.append(priority)
        sets.append("updated_at=?")
        params.extend([_now(), row["id"]])
        conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", params)
        _event(conn, row["id"], "edit", "fields updated")
        conn.commit()
        _audit("edit", requested_by, {"task_id": row["id"]})
        return {"state": "OK", "task": _task_dict(_get_task(conn, row["id"]))}
    finally:
        conn.close()


def comment(cfg: Any, task_id: int, body: str, *,
            requested_by: str = "cli:user",
            confirmed: bool = False) -> Dict[str, Any]:
    """Append a comment (bounded; archived tasks refuse)."""
    if not confirmed:
        raise KanbanError(CONFIRM_CODE,
                          "kanban comment requires explicit --confirm")
    text = (body or "").strip()
    if not text or len(text) > MAX_COMMENT_LEN:
        raise KanbanError(BAD_INPUT_CODE,
                          f"comment must be 1..{MAX_COMMENT_LEN} chars")
    conn = _connect(cfg)
    try:
        _migrate(conn)
        row = _get_task(conn, int(task_id))
        if row["status"] == "archived":
            raise KanbanError(STATE_CODE, f"task #{task_id} is archived")
        cur = conn.execute("INSERT INTO comments(task_id, author, body,"
                           " created_at) VALUES(?,?,?,?)",
                           (row["id"], requested_by[:80], text, _now()))
        _event(conn, row["id"], "comment", f"comment_id={cur.lastrowid}")
        conn.commit()
        _audit("comment", requested_by, {"task_id": row["id"]})
        return {"state": "OK", "comment_id": cur.lastrowid,
                "task_id": row["id"]}
    finally:
        conn.close()


def stats(cfg: Any, *, board: str = "") -> Dict[str, Any]:
    """Per-board status counts (read-only)."""
    conn = _connect(cfg)
    try:
        _migrate(conn)
        boards = [board] if board else [
            r["slug"] for r in conn.execute("SELECT slug FROM boards")]
        if board:
            _get_board(conn, board)
        out = {}
        for slug in boards:
            counts = {s: 0 for s in STATUSES}
            for trow in conn.execute(
                    "SELECT status, COUNT(*) c FROM tasks WHERE board=? "
                    "GROUP BY status", (slug,)):
                counts[trow["status"]] = trow["c"]
            out[slug] = counts
        return {"state": "OK", "stats": out}
    finally:
        conn.close()


def log(cfg: Any, *, board: str = "", limit: int = LIST_DEFAULT) -> Dict[str, Any]:
    """Recent board events newest-first (read-only, bounded)."""
    limit = max(1, min(int(limit or LIST_DEFAULT), LIST_MAX))
    conn = _connect(cfg)
    try:
        _migrate(conn)
        if board:
            _get_board(conn, board)
            rows = conn.execute(
                "SELECT e.kind, e.detail, e.created_at, e.task_id,"
                " t.title FROM events e JOIN tasks t ON t.id=e.task_id"
                " WHERE t.board=? ORDER BY e.id DESC LIMIT ?",
                (_check_board_slug(board), limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT e.kind, e.detail, e.created_at, e.task_id,"
                " t.title FROM events e JOIN tasks t ON t.id=e.task_id"
                " ORDER BY e.id DESC LIMIT ?", (limit,)).fetchall()
        return {"state": "OK", "events": [
            {"kind": r["kind"], "detail": redact_spec(r["detail"]),
             "task_id": r["task_id"], "title": redact_spec(r["title"]),
             "created_at": r["created_at"]} for r in rows], "limit": limit}
    finally:
        conn.close()


def export(cfg: Any) -> Dict[str, Any]:
    """Dump boards+tasks+comments+events as JSON (rollback snapshots)."""
    conn = _connect(cfg)
    try:
        _migrate(conn)
        data = {"schema": SCHEMA,
                "boards": [dict(r) for r in conn.execute("SELECT * FROM boards")],
                "tasks": [dict(r) for r in conn.execute("SELECT * FROM tasks")],
                "comments": [dict(r) for r in
                             conn.execute("SELECT * FROM comments")],
                "events": [dict(r) for r in conn.execute(
                    "SELECT * FROM events ORDER BY id DESC LIMIT 5000")]}
        return {"state": "OK", "snapshot": data}
    finally:
        conn.close()


def _validate_snapshot(snapshot: Dict[str, Any]) -> None:
    """Reject a malformed snapshot BEFORE any row is deleted or rewritten.

    Recovery contract: validation is all-or-nothing. Unknown schema, a slug
    that violates the canonical board form, a task status/priority outside
    the closed sets, an oversized title/body/comment, a task pointing at a
    board absent from the snapshot, or a comment pointing at an absent task
    raise a typed ``KANBAN_BAD_INPUT`` before the transaction starts, so a
    bad import can never leave the store partially restored."""
    if not isinstance(snapshot, dict) or snapshot.get("schema") != SCHEMA:
        raise KanbanError(BAD_INPUT_CODE, "snapshot has an unknown schema")
    boards = snapshot.get("boards", [])
    tasks = snapshot.get("tasks", [])
    comments = snapshot.get("comments", [])
    events = snapshot.get("events", [])
    if not isinstance(boards, list) or len(boards) > MAX_BOARDS:
        raise KanbanError(BAD_INPUT_CODE,
                          f"snapshot boards must be a list of <= {MAX_BOARDS}")
    slugs = set()
    for b in boards:
        if not isinstance(b, dict):
            raise KanbanError(BAD_INPUT_CODE,
                              "snapshot board must be an object")
        slug = str(b.get("slug", ""))
        if not _BOARD_RE.match(slug):
            raise KanbanError(BAD_INPUT_CODE,
                              f"bad board slug in snapshot: {slug!r}")
        if slug in slugs:
            raise KanbanError(BAD_INPUT_CODE,
                              f"duplicate board slug in snapshot: {slug!r}")
        slugs.add(slug)
        if len(str(b.get("name", "") or "")) > MAX_TITLE_LEN:
            raise KanbanError(BAD_INPUT_CODE, "snapshot board name too long")
    if not isinstance(tasks, list):
        raise KanbanError(BAD_INPUT_CODE, "snapshot tasks must be a list")
    if not isinstance(comments, list) or not isinstance(events, list):
        raise KanbanError(BAD_INPUT_CODE,
                          "snapshot comments/events must be lists")
    task_ids = set()
    for t in tasks:
        if not isinstance(t, dict):
            raise KanbanError(BAD_INPUT_CODE, "snapshot task must be an object")
        if str(t.get("board", "")) not in slugs:
            raise KanbanError(
                BAD_INPUT_CODE,
                f"snapshot task references unknown board {t.get('board')!r}")
        if t.get("status") not in STATUSES:
            raise KanbanError(
                BAD_INPUT_CODE, f"bad task status in snapshot: {t.get('status')!r}")
        if str(t.get("priority", "normal")) not in PRIORITIES:
            raise KanbanError(
                BAD_INPUT_CODE,
                f"bad task priority in snapshot: {t.get('priority')!r}")
        title = str(t.get("title", "") or "")
        if not title.strip() or len(title) > MAX_TITLE_LEN:
            raise KanbanError(BAD_INPUT_CODE,
                              "snapshot task title must be 1..200 chars")
        if len(str(t.get("body", "") or "")) > MAX_BODY_LEN:
            raise KanbanError(BAD_INPUT_CODE, "snapshot task body too long")
        if t.get("id") is not None:
            task_ids.add(t.get("id"))
    for c in comments:
        if not isinstance(c, dict) or c.get("task_id") not in task_ids:
            raise KanbanError(BAD_INPUT_CODE,
                              "snapshot comment references an unknown task")
        if len(str(c.get("body", "") or "")) > MAX_COMMENT_LEN:
            raise KanbanError(BAD_INPUT_CODE, "snapshot comment too long")
    for e in events:
        if not isinstance(e, dict) or not str(e.get("kind", "") or ""):
            raise KanbanError(BAD_INPUT_CODE, "snapshot event needs a kind")


def import_snapshot(cfg: Any, snapshot: Dict[str, Any], *,
                    requested_by: str = "cli:user",
                    confirmed: bool = False) -> Dict[str, Any]:
    """Restore a snapshot (auto-backup first; the rollback mechanism).

    The snapshot is fully validated before the first delete, then applied
    inside one ``BEGIN IMMEDIATE`` transaction: any failure rolls back to
    the pre-import store, so an import is always all-or-nothing."""
    if not confirmed:
        raise KanbanError(CONFIRM_CODE,
                          "kanban import requires explicit --confirm")
    _validate_snapshot(snapshot)
    conn = _connect(cfg)
    try:
        _migrate(conn)
        try:
            payload = json.dumps(snapshot)
        except (TypeError, ValueError) as exc:
            raise KanbanError(BAD_INPUT_CODE,
                              f"snapshot not JSON-safe: {exc}") from exc
        if len(payload) > 20_000_000:
            raise KanbanError(BAD_INPUT_CODE, "snapshot too large (20MB cap)")
        backup = export(cfg)["snapshot"]
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM events")
            conn.execute("DELETE FROM comments")
            conn.execute("DELETE FROM tasks")
            conn.execute("DELETE FROM boards")
            for b in snapshot.get("boards", []):
                conn.execute("INSERT INTO boards(slug, name, project_slug,"
                             " created_at) VALUES(?,?,?,?)",
                             (b["slug"], b["name"], b.get("project_slug"),
                              b.get("created_at", _now())))
            for t in snapshot.get("tasks", []):
                conn.execute("INSERT INTO tasks(id, board, title, body, status,"
                             " assignee, priority, created_at, updated_at)"
                             " VALUES(?,?,?,?,?,?,?,?,?)",
                             (t.get("id"), t["board"], t["title"],
                              t.get("body", ""), t["status"], t.get("assignee"),
                              t.get("priority", "normal"),
                              t.get("created_at", _now()),
                              t.get("updated_at", _now())))
            for c in snapshot.get("comments", []):
                conn.execute("INSERT INTO comments(id, task_id, author, body,"
                             " created_at) VALUES(?,?,?,?,?)",
                             (c.get("id"), c["task_id"], c.get("author", ""),
                              c["body"], c.get("created_at", _now())))
            for e in snapshot.get("events", []):
                conn.execute("INSERT INTO events(id, task_id, kind, detail,"
                             " created_at) VALUES(?,?,?,?,?)",
                             (e.get("id"), e["task_id"], e["kind"],
                              e.get("detail", ""), e.get("created_at", _now())))
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            raise
        _audit("import", requested_by,
               {"boards": len(snapshot.get("boards", [])),
                "tasks": len(snapshot.get("tasks", []))})
        return {"state": "OK",
                "restored_tasks": len(snapshot.get("tasks", [])),
                "auto_backup_tasks": len(backup.get("tasks", []))}
    except KanbanError:
        raise
    except (sqlite3.Error, KeyError, TypeError) as exc:
        raise KanbanError(BAD_INPUT_CODE,
                          f"snapshot restore failed: {exc}") from exc
    finally:
        conn.close()


#: Vendored Qubit kanban write surfaces (the upstream plugin's own verbs).
#: Any request naming one of these boards/tasks mutations is answered by the
#: single in-process board-write boundary ``kanban_bridge.vendored_write`` —
#: the production caller that keeps the vendored store read-only everywhere,
#: not just at the dashboard route layer.
VENDORED_WRITE_SUBCOMMANDS = frozenset({
    "tasks", "task", "boards", "board", "profiles", "profile",
    "orchestration",
})


def refuse_delegated(subcommand: str) -> Dict[str, Any]:
    """Typed delegation notice for execution-shaped subcommands.

    Nothing is ever started here: each known subcommand names the Qubit
    authority that owns it. An unknown subcommand returns the same typed
    ``KANBAN_DELEGATED`` state (never an untyped ``KeyError``) and lists the
    known set so CLI and dashboard dispatch stay equivalent.

    A vendored-store write verb (``tasks``/``boards``/``orchestration``/…) is
    answered by :func:`voicebridge.kanban_bridge.vendored_write` with the
    single ``KANBAN_WRITE_BROKERED`` refusal, so an in-process caller can never
    mutate the read-only legacy store by reaching it directly."""
    name = str(subcommand or "").strip()
    if name in VENDORED_WRITE_SUBCOMMANDS:
        from . import kanban_bridge as KBG

        refusal = KBG.vendored_write(name, board="")
        refusal["state"] = "REFUSED"
        return refusal
    reason = DELEGATED.get(name)
    if reason is None:
        return {"state": "DELEGATED", "code": DELEGATED_CODE,
                "subcommand": name,
                "explanation": "Qubit Agent cannot do this here: "
                               f"`kanban {name or '<subcommand>'}` is not a "
                               "Qubit board operation; execution-shaped work "
                               "goes through the Qubit broker/scheduler. "
                               f"Known delegated subcommands: "
                               f"{', '.join(sorted(DELEGATED))}.",
                "known_subcommands": sorted(DELEGATED),
                "owner": "qubit-policy",
                "rollback": "nothing started; nothing to roll back"}
    return {"state": "DELEGATED", "code": DELEGATED_CODE,
            "subcommand": name,
            "explanation": "Qubit Agent cannot do this here: " + reason,
            "owner": "qubit-policy",
            "rollback": "nothing started; nothing to roll back"}


def refuse_unsupported(subcommand: str) -> Dict[str, Any]:
    """Typed answer for removals without a Qubit construct (no silent omission)."""
    return {"state": "BLOCKED", "code": UNSUPPORTED_CODE,
            "subcommand": subcommand,
            "explanation": "Qubit Agent cannot do this: "
                           f"`kanban {subcommand}` has no Qubit construct yet; "
                           "task tracking lives in open/claimed/blocked/done "
                           "tasks on local boards.",
            "owner": "packet-8Y",
            "rollback": "nothing started; nothing to roll back"}


def estop_engaged(cfg: Any) -> bool:
    """Same durable latch probe as the plugins surface (fail closed)."""
    from .plugins import estop_engaged as _engaged

    return _engaged(cfg)


def refuse_estop(operation: str) -> Dict[str, Any]:
    """Typed refusal while the emergency stop is latched."""
    return {"state": "ESTOP", "code": ESTOP_CODE, "operation": operation,
            "explanation": "Qubit Agent cannot do this: the emergency stop "
                           "is latched; reset it before changing kanban.",
            "owner": "qubit-policy",
            "rollback": "board untouched; nothing to roll back"}


# --------------------------------------------- widget canvas (O-board canvas)
#
# The task board above is the Qubit task lifecycle. The
# board/widget canvas is the visual surface around it: per-board column
# layout, sandboxed widgets, per-board permission roles, and layout
# snapshots for rollback. Same SQLite store, same confirm/estop/audit/
# redaction contracts, same backend for CLI, dashboard, and voice.

WIDGET_KINDS = ("note", "text", "tasks", "stats", "links", "link",
                "html", "svg", "plugin")
# Markup kinds are stored verbatim as INERT content: the backend never
# renders or executes them; callers get an explicit trust marker and the
# dashboard must render them inside a sandbox (or not at all). The other
# kinds keep the existing active-markup stripping. ``plugin`` is a
# JSON-descriptor kind: validated against a closed key set, stored inert,
# and only accepted when the widget declares the review-only
# ``plugin:read`` grant (declaring it never grants execution).
WIDGET_MARKUP_KINDS = ("html", "svg")
WIDGET_PLUGIN_KIND = "plugin"
WIDGET_PLUGIN_KEYS = ("name", "version", "props")
_PLUGIN_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
WIDGET_TITLE_LEN = 200
WIDGET_CONTENT_LEN = 4000
WIDGET_MARKUP_LEN = 8000
WIDGET_URL_LEN = 2000
WIDGET_TRUST_SANITIZED = "sanitized"
WIDGET_TRUST_INERT = "inert"
WIDGET_MAX_PER_BOARD = 64
# Declared widget capability grants: a widget may only *declare* tokens
# from this closed allow-list. They are recorded for review, never granted
# at runtime, so declaring one cannot widen access.
WIDGET_GRANT_TOKENS = ("note:read", "tasks:read", "stats:read",
                       "links:read", "link:open", "plugin:read")
WIDGET_DECLARED_GRANTS_MAX = 16
LAYOUT_MAX_COLS = 6
SNAPSHOTS_KEPT = 20
DENIED_CODE = "KANBAN_DENIED"
CANVAS_ROLES = ("owner", "editor", "viewer")
_ROLE_RANK = {"viewer": 1, "editor": 2, "owner": 3}
_DEFAULT_COLUMNS = ["open", "claimed", "blocked", "done"]

# Board tabs: ordered per-board surfaces; every board without explicit
# tabs exposes a single virtual default tab so legacy boards keep working.
MAX_TABS_PER_BOARD = 32
TAB_NAME_LEN = 200
DEFAULT_TAB_SLUG = "main"
DEFAULT_TAB_NAME = "Main"

# Active markup that can never persist in a widget: scripts, frames,
# plug-ins, forms, inline handlers, and javascript: URLs. Matches are
# stripped and counted; the output names the sanitized flag so the
# dashboard can render the widget with plain text only.
_ACTIVE_PATTERNS = (
    re.compile(r"<\s*script\b.*?(?:<\s*/\s*script\s*>|$)", re.I | re.S),
    re.compile(r"<\s*(iframe|object|embed|form|meta|link)\b[^>]*>", re.I),
    re.compile(r"\son\w+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I),
    re.compile(r"javascript\s*:", re.I),
)


def _migrate_canvas(conn: sqlite3.Connection) -> None:
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS canvas_widgets("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, board TEXT NOT NULL,"
        " kind TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',"
        " content TEXT NOT NULL DEFAULT '', x INTEGER NOT NULL DEFAULT 0,"
        " y INTEGER NOT NULL DEFAULT 0, w INTEGER NOT NULL DEFAULT 4,"
        " h INTEGER NOT NULL DEFAULT 3,"
        " created_at REAL NOT NULL, updated_at REAL NOT NULL);"
        "CREATE INDEX IF NOT EXISTS idx_widgets_board ON canvas_widgets(board);"
        "CREATE TABLE IF NOT EXISTS canvas_layouts("
        " board TEXT PRIMARY KEY, columns TEXT NOT NULL,"
        " updated_at REAL NOT NULL);"
        "CREATE TABLE IF NOT EXISTS board_roles("
        " board TEXT NOT NULL, principal TEXT NOT NULL,"
        " role TEXT NOT NULL, updated_at REAL NOT NULL,"
        " PRIMARY KEY(board, principal));"
        "CREATE TABLE IF NOT EXISTS canvas_snapshots("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, board TEXT NOT NULL,"
        " payload TEXT NOT NULL, created_at REAL NOT NULL);"
        "CREATE INDEX IF NOT EXISTS idx_snapshots_board"
        " ON canvas_snapshots(board, id);"
        "CREATE TABLE IF NOT EXISTS board_tabs("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, board TEXT NOT NULL,"
        " slug TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',"
        " position INTEGER NOT NULL DEFAULT 0,"
        " created_at REAL NOT NULL, updated_at REAL NOT NULL,"
        " UNIQUE(board, slug));"
        "CREATE INDEX IF NOT EXISTS idx_tabs_board"
        " ON board_tabs(board, position);"
        "CREATE TABLE IF NOT EXISTS board_tab_roles("
        " tab_id INTEGER NOT NULL, principal TEXT NOT NULL,"
        " role TEXT NOT NULL, updated_at REAL NOT NULL,"
        " PRIMARY KEY(tab_id, principal));")
    _ensure_column(conn, "canvas_widgets", "tab", "TEXT")
    _ensure_column(conn, "canvas_widgets", "grants",
                   "TEXT NOT NULL DEFAULT '[]'")


def _ensure_column(conn: sqlite3.Connection, table: str, name: str,
                   decl: str) -> None:
    """Additive-only column migration (no-op once the column exists).

    Existing rows keep their stored data; nullable/defaulted columns mean
    every legacy read keeps working unchanged."""
    have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if name not in have:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def sanitize_widget_content(text: str) -> Dict[str, Any]:
    """Strip active markup from widget content (sandboxing).

    Returns the cleaned text plus how many active fragments were removed.
    Plain text, links as text, and task references survive; nothing
    executable survives. Never raises on hostile input."""
    clean = text or ""
    removed = 0
    for pat in _ACTIVE_PATTERNS:
        clean, n = pat.subn("", clean)
        removed += n
    return {"content": clean, "removed": removed,
            "sanitized": removed > 0}


def _row_keys(row: sqlite3.Row) -> set:
    try:
        return set(row.keys())
    except AttributeError:
        return set()


def _parse_grants(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return []
    if not isinstance(raw, list):
        return []
    return [str(t) for t in raw if isinstance(t, str)]


def _widget_dict(row: sqlite3.Row) -> Dict[str, Any]:
    keys = _row_keys(row)
    kind = row["kind"]
    tab = (row["tab"] if "tab" in keys else None) or DEFAULT_TAB_SLUG
    grants = _parse_grants(row["grants"] if "grants" in keys else None)
    inert = kind in WIDGET_MARKUP_KINDS or kind == WIDGET_PLUGIN_KIND
    out = {"id": row["id"], "board": row["board"], "kind": kind,
           "title": redact_spec(row["title"]),
           "content": redact_spec(row["content"]),
           "x": row["x"], "y": row["y"], "w": row["w"], "h": row["h"],
           "tab": tab, "grants": grants,
           "trust": WIDGET_TRUST_INERT if inert else WIDGET_TRUST_SANITIZED,
           "inert": inert, "executable": False,
           "created_at": row["created_at"],
           "updated_at": row["updated_at"]}
    if kind == "link":
        out["url"] = redact_spec(row["content"])
    return out


def _check_widget_kind(kind: str) -> str:
    clean = (kind or "").strip().lower()
    if clean not in WIDGET_KINDS:
        raise KanbanError(BAD_INPUT_CODE,
                          f"widget kind must be one of {WIDGET_KINDS}")
    return clean


def _widget_content_cap(kind: str) -> int:
    if kind in WIDGET_MARKUP_KINDS or kind == WIDGET_PLUGIN_KIND:
        return WIDGET_MARKUP_LEN
    if kind == "link":
        return WIDGET_URL_LEN
    return WIDGET_CONTENT_LEN


def _check_plugin_content(text: str) -> str:
    """Validate a plugin widget descriptor: inert JSON, closed key set.

    The descriptor is persisted verbatim and never executed or imported
    by the backend; only ``name`` (a safe slug), optional ``version``, and
    an optional ``props`` object are accepted so a hostile descriptor can
    never smuggle code, URLs, or unknown behavior contracts."""
    raw = (text or "").strip()
    if not raw:
        raise KanbanError(BAD_INPUT_CODE,
                          "plugin widget needs a JSON descriptor")
    if len(raw) > WIDGET_MARKUP_LEN:
        raise KanbanError(BAD_INPUT_CODE,
                          f"plugin descriptor must be <={WIDGET_MARKUP_LEN}"
                          " chars")
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        raise KanbanError(BAD_INPUT_CODE,
                          "plugin descriptor must be valid JSON") from None
    if not isinstance(obj, dict):
        raise KanbanError(BAD_INPUT_CODE,
                          "plugin descriptor must be a JSON object")
    extra = set(obj) - set(WIDGET_PLUGIN_KEYS)
    if extra:
        raise KanbanError(BAD_INPUT_CODE,
                          "plugin descriptor keys must be within "
                          f"{list(WIDGET_PLUGIN_KEYS)} (got {sorted(extra)})")
    name = str(obj.get("name", "") or "").strip().lower()
    if not _PLUGIN_NAME_RE.match(name):
        raise KanbanError(BAD_INPUT_CODE,
                          "plugin descriptor needs a safe 'name' slug")
    props = obj.get("props")
    if props is not None and not isinstance(props, dict):
        raise KanbanError(BAD_INPUT_CODE,
                          "plugin descriptor 'props' must be an object")
    clean = {"name": name}
    if "version" in obj:
        version = str(obj.get("version", "") or "").strip()
        if len(version) > 40:
            raise KanbanError(BAD_INPUT_CODE,
                              "plugin descriptor version too long")
        clean["version"] = version
    if props is not None:
        if len(json.dumps(props)) > WIDGET_CONTENT_LEN:
            raise KanbanError(BAD_INPUT_CODE,
                              "plugin descriptor props too large")
        clean["props"] = props
    return json.dumps(clean, sort_keys=True, separators=(",", ":"))


def _check_link_url(text: str) -> str:
    """Validate a link reference WITHOUT fetching it (no SSRF surface).

    http(s) only, with a host; stored verbatim as a reference and never
    dereferenced by this module or the server."""
    from urllib.parse import urlparse

    raw = (text or "").strip()
    if not raw:
        raise KanbanError(BAD_INPUT_CODE, "link widget needs a url")
    if len(raw) > WIDGET_URL_LEN:
        raise KanbanError(BAD_INPUT_CODE,
                          f"link url must be <={WIDGET_URL_LEN} chars")
    parsed = urlparse(raw)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        raise KanbanError(BAD_INPUT_CODE,
                          "link url must be an absolute http(s):// reference"
                          " (never fetched)")
    return raw


def _check_declared_grants(grants: Any) -> List[str]:
    """Validate declared-only widget capability tokens (never granted)."""
    if grants is None or grants == "":
        return []
    if isinstance(grants, str):
        grants = [g for g in grants.replace(",", " ").split() if g]
    if not isinstance(grants, (list, tuple)):
        raise KanbanError(BAD_INPUT_CODE,
                          "declared_grants must be a list of tokens")
    if len(grants) > WIDGET_DECLARED_GRANTS_MAX:
        raise KanbanError(BAD_INPUT_CODE,
                          "declared_grants has too many tokens"
                          f" (max {WIDGET_DECLARED_GRANTS_MAX})")
    clean: List[str] = []
    for token in grants:
        text = str(token or "").strip().lower()
        if text not in WIDGET_GRANT_TOKENS:
            raise KanbanError(BAD_INPUT_CODE,
                              f"unknown declared grant {text!r}; allowed: "
                              f"{WIDGET_GRANT_TOKENS}")
        if text not in clean:
            clean.append(text)
    return clean


def _check_grid(name: str, value: int, lo: int, hi: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise KanbanError(BAD_INPUT_CODE,
                          f"widget {name} must be an integer") from None
    if not lo <= number <= hi:
        raise KanbanError(BAD_INPUT_CODE,
                          f"widget {name} must be {lo}..{hi}")
    return number


def _check_columns(columns: List[str]) -> List[str]:
    if not isinstance(columns, list) or not 1 <= len(columns) <= LAYOUT_MAX_COLS:
        raise KanbanError(BAD_INPUT_CODE,
                          f"layout needs 1..{LAYOUT_MAX_COLS} columns")
    clean = []
    for col in columns:
        text = str(col or "").strip().lower()
        if text not in STATUSES:
            raise KanbanError(BAD_INPUT_CODE,
                              f"layout column must be one of {STATUSES}")
        if text not in clean:
            clean.append(text)
    return clean


def _canvas_roles(conn: sqlite3.Connection, board: str) -> Dict[str, str]:
    return {r["principal"]: r["role"] for r in conn.execute(
        "SELECT principal, role FROM board_roles WHERE board=?", (board,))}


def _require_canvas_role(conn: sqlite3.Connection, board: str,
                         principal: str, need: str) -> None:
    """Enforce per-board permission roles for delegate principals.

    Boards without any role rows are unrestricted (legacy behavior — the
    CLI, dashboard, and voice first-party surfaces already passed broker
    auth, confirmation, and the emergency-stop gate). Once roles exist,
    naming a principal outside the board's editor list for assignment or
    canvas work is denied with a typed error; viewer principals may look
    but never change state."""
    roles = _canvas_roles(conn, board)
    if not roles:
        return
    if not principal:
        return
    rank = _ROLE_RANK.get(roles.get(principal, ""), 0)
    if rank < _ROLE_RANK[need]:
        raise KanbanError(DENIED_CODE,
                          f"principal {principal!r} has "
                          f"{roles.get(principal, 'no')} access on board "
                          f"{board!r}; {need} or owner required")


# ------------------------------------------------------------------- tabs
def _check_tab_slug(slug: str) -> str:
    return _check_board_slug(slug)


def _default_tab() -> Dict[str, Any]:
    return {"id": None, "slug": DEFAULT_TAB_SLUG, "name": DEFAULT_TAB_NAME,
            "position": 0, "default": True, "widget_count": 0}


def _tab_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {"id": row["id"], "slug": row["slug"],
            "name": redact_spec(row["name"] or row["slug"]),
            "position": row["position"],
            "default": row["slug"] == DEFAULT_TAB_SLUG,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"]}


def _tabs(conn: sqlite3.Connection, board: str) -> List[sqlite3.Row]:
    return conn.execute("SELECT * FROM board_tabs WHERE board=?"
                        " ORDER BY position, id", (board,)).fetchall()


def _get_tab(conn: sqlite3.Connection, board: str,
             slug: str) -> sqlite3.Row:
    clean = _check_tab_slug(slug)
    row = conn.execute("SELECT * FROM board_tabs WHERE board=? AND slug=?",
                       (board, clean)).fetchone()
    if row is None:
        raise KanbanError(NOT_FOUND_CODE,
                          f"no tab {slug!r} on board {board!r}")
    return row


def _tab_widget_counts(conn: sqlite3.Connection,
                       board: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for r in conn.execute(
            "SELECT COALESCE(tab, ?) AS t, COUNT(*) AS c FROM canvas_widgets"
            " WHERE board=? GROUP BY COALESCE(tab, ?)",
            (DEFAULT_TAB_SLUG, board, DEFAULT_TAB_SLUG)):
        counts[r["t"]] = r["c"]
    return counts


def _tabs_payload(conn: sqlite3.Connection, board: str) -> List[Dict[str, Any]]:
    rows = _tabs(conn, board)
    counts = _tab_widget_counts(conn, board)
    if not rows:
        out = _default_tab()
        out["widget_count"] = counts.get(DEFAULT_TAB_SLUG, 0)
        return [out]
    payload = []
    for row in rows:
        item = _tab_dict(row)
        item["widget_count"] = counts.get(row["slug"], 0)
        payload.append(item)
    return payload


def _widget_tab_slug(conn: sqlite3.Connection, board: str,
                     tab: str) -> Optional[str]:
    """Resolve a widget's tab slug; the virtual default stores NULL."""
    text = (tab or "").strip().lower()
    if not text or text == DEFAULT_TAB_SLUG:
        row = conn.execute("SELECT slug FROM board_tabs WHERE board=?"
                           " AND slug=?", (board, DEFAULT_TAB_SLUG)).fetchone()
        return row["slug"] if row else None
    row = conn.execute("SELECT slug FROM board_tabs WHERE board=? AND slug=?",
                       (board, text)).fetchone()
    if row is None:
        raise KanbanError(NOT_FOUND_CODE,
                          f"no tab {tab!r} on board {board!r}")
    return row["slug"]


def _tab_roles(conn: sqlite3.Connection, tab_id: int) -> Dict[str, str]:
    return {r["principal"]: r["role"] for r in conn.execute(
        "SELECT principal, role FROM board_tab_roles WHERE tab_id=?",
        (int(tab_id),))}


def _require_tab_role(conn: sqlite3.Connection, board: str, tab_id: int,
                      principal: str, need: str) -> None:
    """Enforce a per-tab role without ever widening board access.

    Tab grants are a *ceiling* applied on top of the board role: a tab
    role can only lower a principal's rank (``min`` of board and tab),
    and a tab grant to a principal with no board role grants nothing.
    Unrestricted boards (no board role rows) ignore tab grants entirely,
    preserving the legacy behavior."""
    if not principal:
        return
    board_roles = _canvas_roles(conn, board)
    if not board_roles:
        return
    board_rank = _ROLE_RANK.get(board_roles.get(principal, ""), 0)
    tab_roles = _tab_roles(conn, tab_id) if tab_id else {}
    effective = board_rank
    if principal in tab_roles:
        effective = min(board_rank,
                        _ROLE_RANK.get(tab_roles[principal], 0))
    if effective < _ROLE_RANK[need]:
        raise KanbanError(DENIED_CODE,
                          f"principal {principal!r} has no {need} grant on"
                          f" tab {tab_id} (board role "
                          f"{board_roles.get(principal, 'none')})")
    return


def _snapshot_canvas(conn: sqlite3.Connection, board: str) -> int:
    """Auto-snapshot widgets+layout+roles+task positions (rollback)."""
    payload = json.dumps({
        "schema": SCHEMA,
        "board": board,
        "widgets": [dict(r) for r in conn.execute(
            "SELECT * FROM canvas_widgets WHERE board=?", (board,))],
        "layouts": [dict(r) for r in conn.execute(
            "SELECT * FROM canvas_layouts WHERE board=?", (board,))],
        "roles": [dict(r) for r in conn.execute(
            "SELECT * FROM board_roles WHERE board=?", (board,))],
        "tabs": [dict(r) for r in conn.execute(
            "SELECT * FROM board_tabs WHERE board=?", (board,))],
        "tab_roles": [dict(r) for r in conn.execute(
            "SELECT tr.* FROM board_tab_roles tr JOIN board_tabs t"
            " ON t.id=tr.tab_id WHERE t.board=?", (board,))],
        "task_positions": [dict(r) for r in conn.execute(
            "SELECT id, board, status, assignee FROM tasks WHERE board=?",
            (board,))],
        "taken_at": _now(),
    })
    cur = conn.execute("INSERT INTO canvas_snapshots(board, payload,"
                       " created_at) VALUES(?,?,?)",
                       (board, payload, _now()))
    snap_id = cur.lastrowid
    conn.execute("DELETE FROM canvas_snapshots WHERE board=? AND id NOT IN"
                 " (SELECT id FROM canvas_snapshots WHERE board=?"
                 " ORDER BY id DESC LIMIT ?)",
                 (board, board, SNAPSHOTS_KEPT))
    return snap_id


def _canvas_guard(cfg: Any, operation: str, confirmed: bool) -> None:
    if not confirmed:
        raise KanbanError(CONFIRM_CODE,
                          f"kanban {operation} requires explicit --confirm")
    if estop_engaged(cfg):
        raise KanbanError(ESTOP_CODE,
                          "the emergency stop is latched; reset it before"
                          " changing the board canvas")


def board_open(cfg: Any, board: str = "main") -> Dict[str, Any]:
    """Render the whole board canvas (read-only): tasks by column,
    widgets, layout, and role roster. This is what "open the board"
    speaks and what the dashboard canvas page shows."""
    slug = _check_board_slug(board or "main")
    conn = _connect(cfg)
    try:
        _migrate(conn)
        _migrate_canvas(conn)
        _get_board(conn, slug)
        layout_row = conn.execute("SELECT columns FROM canvas_layouts"
                                  " WHERE board=?", (slug,)).fetchone()
        columns = (json.loads(layout_row["columns"])
                   if layout_row else list(_DEFAULT_COLUMNS))
        by_column: Dict[str, List[Dict[str, Any]]] = {c: [] for c in columns}
        for trow in conn.execute("SELECT * FROM tasks WHERE board=?"
                                 " ORDER BY id DESC LIMIT 500", (slug,)):
            task = _task_dict(trow)
            by_column.setdefault(task["status"], []).append(task)
        widgets = [_widget_dict(r) for r in conn.execute(
            "SELECT * FROM canvas_widgets WHERE board=? ORDER BY y, x, id",
            (slug,))]
        roles = _canvas_roles(conn, slug)
        tabs = _tabs_payload(conn, slug)
        counts = {s: len(by_column.get(s, [])) for s in STATUSES}
        return {"state": "OK", "board": slug, "columns": columns,
                "by_column": by_column, "counts": counts,
                "widgets": widgets, "roles": roles, "tabs": tabs,
                "restricted": bool(roles),
                "owner": "qubit-policy",
                "rollback": "read-only; nothing changed"}
    finally:
        conn.close()


def move_task(cfg: Any, task_id: int, *, board: str = "",
              status: str = "", requested_by: str = "cli:user",
              confirmed: bool = False) -> Dict[str, Any]:
    """Move a task across boards and/or columns ("move this task").

    Either destination may be omitted (move board only, or re-column
    only). Archived tasks refuse; restricted boards refuse unlisted
    assignees; every move auto-snapshots first for rollback."""
    _canvas_guard(cfg, "move", confirmed)
    if status and status not in STATUSES:
        raise KanbanError(BAD_INPUT_CODE,
                          f"status must be one of {STATUSES}")
    if not board and not status:
        raise KanbanError(BAD_INPUT_CODE,
                          "move needs --board and/or --status")
    conn = _connect(cfg)
    try:
        _migrate(conn)
        _migrate_canvas(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = _get_task(conn, int(task_id))
            if row["status"] == "archived":
                raise KanbanError(STATE_CODE,
                                  f"task #{task_id} is archived")
            dest_board = _check_board_slug(board) if board else row["board"]
            _get_board(conn, dest_board)
            dest_status = status or row["status"]
            task = _task_dict(row)
            if task.get("assignee"):
                _require_canvas_role(conn, dest_board,
                                     task["assignee"], "editor")
            snap_id = _snapshot_canvas(conn, str(row["board"]))
            if dest_board != row["board"]:
                snap_id = _snapshot_canvas(conn, dest_board)
            conn.execute("UPDATE tasks SET board=?, status=?, updated_at=?"
                         " WHERE id=?",
                         (dest_board, dest_status, _now(), row["id"]))
            _event(conn, row["id"], "move",
                   f"board={dest_board} status={dest_status}")
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            raise
        _audit("move", requested_by,
               {"task_id": row["id"], "board": dest_board,
                "status": dest_status})
        out = _task_dict(_get_task(conn, row["id"]))
        out["snapshot_id"] = snap_id
        return {"state": "OK", "task": out,
                "rollback": f"canvas snapshot #{snap_id}; "
                            "`kanban snapshot-restore` reverts this move"}
    finally:
        conn.close()


def widget_add(cfg: Any, board: str = "main", kind: str = "",
               title: str = "", *,
               content: str = "", content_kind: str = "",
               tab: str = "", declared_grants: Optional[List[str]] = None,
               x: int = 0, y: int = 0,
               w: int = 4, h: int = 3,
               requested_by: str = "cli:user", principal: str = "",
               confirmed: bool = False) -> Dict[str, Any]:
    """Add a sandboxed widget to the board canvas (confirmed).

    ``kind`` (or its alias ``content_kind``) selects the content kind:
    ``note``/``text`` are sanitized plain text; ``link`` stores an
    http(s) URL as a reference only (never fetched, so no SSRF);
    ``html``/``svg`` are stored verbatim as INERT content and returned
    with an explicit ``trust: "inert"`` marker — the backend never
    renders or executes them. ``declared_grants`` records review-only
    capability tokens from a closed allow-list; they grant nothing."""
    _canvas_guard(cfg, "widget-add", confirmed)
    slug = _check_board_slug(board or "main")
    raw_kind = (content_kind or kind or "").strip().lower()
    if kind and content_kind and \
            kind.strip().lower() != content_kind.strip().lower():
        raise KanbanError(BAD_INPUT_CODE,
                          "widget kind and content_kind disagree")
    clean_kind = _check_widget_kind(raw_kind)
    clean_title = _check_title(title)
    body = content or ""
    cap = _widget_content_cap(clean_kind)
    if len(body) > cap:
        raise KanbanError(BAD_INPUT_CODE,
                          f"content must be <={cap} chars for {clean_kind!r}")
    grants = _check_declared_grants(declared_grants)
    if clean_kind == "link":
        body = _check_link_url(body)
        scrubbed = {"content": body, "removed": 0, "sanitized": False}
    elif clean_kind == WIDGET_PLUGIN_KIND:
        if "plugin:read" not in grants:
            raise KanbanError(DENIED_CODE,
                              "plugin widget requires the declared "
                              "'plugin:read' grant (review-only; adds no "
                              "execution capability)")
        body = _check_plugin_content(body)
        scrubbed = {"content": body, "removed": 0, "sanitized": False}
    elif clean_kind in WIDGET_MARKUP_KINDS:
        # Inert markup: persisted verbatim, never executed server-side.
        scrubbed = {"content": body, "removed": 0, "sanitized": False}
    else:
        scrubbed = sanitize_widget_content(body)
    conn = _connect(cfg)
    try:
        _migrate(conn)
        _migrate_canvas(conn)
        _get_board(conn, slug)
        tab_slug = _widget_tab_slug(conn, slug, tab)
        tab_id = None
        if tab_slug is not None:
            tab_id = _get_tab(conn, slug, tab_slug)["id"]
        if principal and tab_id:
            _require_tab_role(conn, slug, tab_id, principal, "editor")
        if conn.execute("SELECT COUNT(*) c FROM canvas_widgets WHERE board=?",
                        (slug,)).fetchone()["c"] >= WIDGET_MAX_PER_BOARD:
            raise KanbanError(EXISTS_CODE,
                              f"board {slug!r} widget limit reached"
                              f" ({WIDGET_MAX_PER_BOARD})")
        snap_id = _snapshot_canvas(conn, slug)
        cur = conn.execute("INSERT INTO canvas_widgets(board, kind, title,"
                           " content, x, y, w, h, tab, grants, created_at,"
                           " updated_at)"
                           " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                           (slug, clean_kind, clean_title,
                            scrubbed["content"],
                            _check_grid("x", x, 0, 48),
                            _check_grid("y", y, 0, 200),
                            _check_grid("w", w, 1, 48),
                            _check_grid("h", h, 1, 200),
                            tab_slug, json.dumps(grants),
                            _now(), _now()))
        conn.commit()
        _audit("canvas.widget_add", requested_by,
               {"board": slug, "widget_id": cur.lastrowid,
                "kind": clean_kind, "tab": tab_slug or DEFAULT_TAB_SLUG,
                "inert": clean_kind in WIDGET_MARKUP_KINDS
                or clean_kind == WIDGET_PLUGIN_KIND,
                "sanitized": scrubbed["sanitized"]})
        row = conn.execute("SELECT * FROM canvas_widgets WHERE id=?",
                           (cur.lastrowid,)).fetchone()
        out = _widget_dict(row)
        out["sanitized"] = scrubbed["sanitized"]
        out["removed_fragments"] = scrubbed["removed"]
        out["snapshot_id"] = snap_id
        return {"state": "OK", "widget": out,
                "rollback": f"canvas snapshot #{snap_id}"}
    finally:
        conn.close()


def widget_list(cfg: Any, board: str = "main",
                tab: str = "") -> Dict[str, Any]:
    """List canvas widgets newest-position-first (read-only).

    ``tab`` optionally filters to one tab (the virtual default when the
    board has no explicit tabs)."""
    slug = _check_board_slug(board or "main")
    conn = _connect(cfg)
    try:
        _migrate(conn)
        _migrate_canvas(conn)
        _get_board(conn, slug)
        clauses, params = ["board=?"], [slug]
        if tab:
            tab_slug = _widget_tab_slug(conn, slug, tab)
            if tab_slug is None:
                clauses.append("(tab IS NULL OR tab=?)")
                params.append(DEFAULT_TAB_SLUG)
            else:
                clauses.append("tab=?")
                params.append(tab_slug)
        rows = conn.execute(
            "SELECT * FROM canvas_widgets WHERE " + " AND ".join(clauses)
            + " ORDER BY y, x, id", params).fetchall()
        return {"state": "OK", "board": slug, "tab": tab or "",
                "widgets": [_widget_dict(r) for r in rows]}
    finally:
        conn.close()


def show_widget(cfg: Any, widget_id: int) -> Dict[str, Any]:
    """Return one canvas widget plus its inert render contract (read-only).

    This is the backend the ``show_widget`` agent/dashboard surface calls:
    it never renders, executes, fetches, or mutates anything — it returns
    the redacted widget, an explicit ``trust`` marker, and the sandbox the
    caller must use (``inert-html`` for markup/plugin widgets, else
    ``plain-text``). Reads stay available while the emergency stop is
    latched, matching every other read path on this store."""
    conn = _connect(cfg)
    try:
        _migrate(conn)
        _migrate_canvas(conn)
        row = conn.execute("SELECT * FROM canvas_widgets WHERE id=?",
                           (int(widget_id),)).fetchone()
        if row is None:
            raise KanbanError(NOT_FOUND_CODE, f"no widget #{widget_id}")
        out = _widget_dict(row)
        return {"state": "OK", "widget": out,
                "render": {"kind": out["kind"], "trust": out["trust"],
                           "executable": False,
                           "sandbox": ("inert-html" if out["inert"]
                                       else "plain-text")}}
    finally:
        conn.close()


def widget_move(cfg: Any, widget_id: int, *, x: Optional[int] = None,
                y: Optional[int] = None, w: Optional[int] = None,
                h: Optional[int] = None, requested_by: str = "cli:user",
                confirmed: bool = False) -> Dict[str, Any]:
    """Reposition/resize a widget (confirmed; auto-snapshots)."""
    _canvas_guard(cfg, "widget-move", confirmed)
    if x is None and y is None and w is None and h is None:
        raise KanbanError(BAD_INPUT_CODE,
                          "widget-move needs at least one of --x --y --w --h")
    conn = _connect(cfg)
    try:
        _migrate(conn)
        _migrate_canvas(conn)
        row = conn.execute("SELECT * FROM canvas_widgets WHERE id=?",
                           (int(widget_id),)).fetchone()
        if row is None:
            raise KanbanError(NOT_FOUND_CODE, f"no widget #{widget_id}")
        snap_id = _snapshot_canvas(conn, row["board"])
        sets, params = [], []
        for name, val, lo, hi in (("x", x, 0, 48), ("y", y, 0, 200),
                                  ("w", w, 1, 48), ("h", h, 1, 200)):
            if val is not None:
                sets.append(f"{name}=?")
                params.append(_check_grid(name, val, lo, hi))
        sets.append("updated_at=?")
        params.extend([_now(), row["id"]])
        conn.execute(f"UPDATE canvas_widgets SET {', '.join(sets)}"
                     " WHERE id=?", params)
        conn.commit()
        _audit("canvas.widget_move", requested_by,
               {"widget_id": row["id"], "board": row["board"]})
        fresh = conn.execute("SELECT * FROM canvas_widgets WHERE id=?",
                             (row["id"],)).fetchone()
        out = _widget_dict(fresh)
        out["snapshot_id"] = snap_id
        return {"state": "OK", "widget": out,
                "rollback": f"canvas snapshot #{snap_id}"}
    finally:
        conn.close()


def widget_remove(cfg: Any, widget_id: int, *,
                  requested_by: str = "cli:user",
                  confirmed: bool = False) -> Dict[str, Any]:
    """Remove a widget (confirmed; auto-snapshots first)."""
    _canvas_guard(cfg, "widget-remove", confirmed)
    conn = _connect(cfg)
    try:
        _migrate(conn)
        _migrate_canvas(conn)
        row = conn.execute("SELECT * FROM canvas_widgets WHERE id=?",
                           (int(widget_id),)).fetchone()
        if row is None:
            raise KanbanError(NOT_FOUND_CODE, f"no widget #{widget_id}")
        snap_id = _snapshot_canvas(conn, row["board"])
        conn.execute("DELETE FROM canvas_widgets WHERE id=?", (row["id"],))
        conn.commit()
        _audit("canvas.widget_remove", requested_by,
               {"widget_id": row["id"], "board": row["board"]})
        return {"state": "OK", "removed": row["id"], "board": row["board"],
                "rollback": f"canvas snapshot #{snap_id}; "
                            "`kanban snapshot-restore` brings it back"}
    finally:
        conn.close()


def tab_create(cfg: Any, board: str = "main", slug: str = "",
               name: str = "", *, requested_by: str = "cli:user",
               confirmed: bool = False) -> Dict[str, Any]:
    """Create an ordered tab on a board (confirmed; auto-snapshots).

    The virtual default tab (``main``) can be materialised explicitly;
    otherwise it stays virtual and existing boards keep working."""
    _canvas_guard(cfg, "tab-create", confirmed)
    board_slug = _check_board_slug(board or "main")
    tab_slug = _check_tab_slug(slug)
    label = (name or tab_slug).strip()[:TAB_NAME_LEN] or tab_slug
    conn = _connect(cfg)
    try:
        _migrate(conn)
        _migrate_canvas(conn)
        _get_board(conn, board_slug)
        if conn.execute("SELECT COUNT(*) c FROM board_tabs WHERE board=?",
                        (board_slug,)).fetchone()["c"] >= MAX_TABS_PER_BOARD:
            raise KanbanError(EXISTS_CODE,
                              f"board {board_slug!r} tab limit reached"
                              f" ({MAX_TABS_PER_BOARD})")
        nxt = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 AS p"
                           " FROM board_tabs WHERE board=?",
                           (board_slug,)).fetchone()["p"]
        snap_id = _snapshot_canvas(conn, board_slug)
        try:
            cur = conn.execute(
                "INSERT INTO board_tabs(board, slug, name, position,"
                " created_at, updated_at) VALUES(?,?,?,?,?,?)",
                (board_slug, tab_slug, label, int(nxt), _now(), _now()))
        except sqlite3.IntegrityError:
            raise KanbanError(EXISTS_CODE,
                              f"tab {tab_slug!r} exists on {board_slug!r}") \
                from None
        conn.commit()
        _audit("canvas.tab_create", requested_by,
               {"board": board_slug, "tab": tab_slug,
                "tab_id": cur.lastrowid})
        return {"state": "OK", "board": board_slug, "tab": tab_slug,
                "tab_id": cur.lastrowid, "snapshot_id": snap_id,
                "rollback": f"canvas snapshot #{snap_id}"}
    finally:
        conn.close()


def tab_list(cfg: Any, board: str = "main") -> Dict[str, Any]:
    """List ordered board tabs (read-only).

    A board without explicit tabs reports one virtual default tab so
    legacy boards and their widgets remain addressable."""
    board_slug = _check_board_slug(board or "main")
    conn = _connect(cfg)
    try:
        _migrate(conn)
        _migrate_canvas(conn)
        _get_board(conn, board_slug)
        has_rows = bool(_tabs(conn, board_slug))
        tabs = _tabs_payload(conn, board_slug)
        return {"state": "OK", "board": board_slug, "tabs": tabs,
                "virtual_default": not has_rows}
    finally:
        conn.close()


def tab_rename(cfg: Any, board: str, tab: str, name: str, *,
               requested_by: str = "cli:user",
               confirmed: bool = False) -> Dict[str, Any]:
    """Rename a tab's display label (slug stays stable; auto-snapshots)."""
    _canvas_guard(cfg, "tab-rename", confirmed)
    board_slug = _check_board_slug(board or "main")
    clean_name = (name or "").strip()
    if not clean_name or len(clean_name) > TAB_NAME_LEN:
        raise KanbanError(BAD_INPUT_CODE,
                          f"tab name must be 1..{TAB_NAME_LEN} chars")
    conn = _connect(cfg)
    try:
        _migrate(conn)
        _migrate_canvas(conn)
        _get_board(conn, board_slug)
        row = _get_tab(conn, board_slug, tab)
        snap_id = _snapshot_canvas(conn, board_slug)
        conn.execute("UPDATE board_tabs SET name=?, updated_at=?"
                     " WHERE id=?", (clean_name[:TAB_NAME_LEN], _now(),
                                     row["id"]))
        conn.commit()
        _audit("canvas.tab_rename", requested_by,
               {"board": board_slug, "tab": row["slug"],
                "name": clean_name[:TAB_NAME_LEN]})
        return {"state": "OK", "board": board_slug, "tab": row["slug"],
                "name": redact_spec(clean_name[:TAB_NAME_LEN]),
                "snapshot_id": snap_id,
                "rollback": f"canvas snapshot #{snap_id}"}
    finally:
        conn.close()


def tab_delete(cfg: Any, board: str, tab: str, *,
               requested_by: str = "cli:user",
               confirmed: bool = False) -> Dict[str, Any]:
    """Delete a tab (confirmed; re-homes its widgets and auto-snapshots)."""
    _canvas_guard(cfg, "tab-delete", confirmed)
    board_slug = _check_board_slug(board or "main")
    conn = _connect(cfg)
    try:
        _migrate(conn)
        _migrate_canvas(conn)
        _get_board(conn, board_slug)
        row = _get_tab(conn, board_slug, tab)
        snap_id = _snapshot_canvas(conn, board_slug)
        moved = conn.execute(
            "UPDATE canvas_widgets SET tab=NULL, updated_at=?"
            " WHERE board=? AND tab=?", (_now(), board_slug, row["slug"])).rowcount
        conn.execute("DELETE FROM board_tab_roles WHERE tab_id=?",
                     (row["id"],))
        conn.execute("DELETE FROM board_tabs WHERE id=?", (row["id"],))
        conn.commit()
        _audit("canvas.tab_delete", requested_by,
               {"board": board_slug, "tab": row["slug"],
                "widgets_rehomed": moved})
        return {"state": "OK", "board": board_slug, "removed": row["slug"],
                "widgets_rehomed": moved, "snapshot_id": snap_id,
                "rollback": f"canvas snapshot #{snap_id}; "
                            "`kanban snapshot-restore` brings it back"}
    finally:
        conn.close()


def tab_move(cfg: Any, board: str, tab: str, position: int, *,
             requested_by: str = "cli:user",
             confirmed: bool = False) -> Dict[str, Any]:
    """Reorder a tab to a 0-based position (confirmed; auto-snapshots)."""
    _canvas_guard(cfg, "tab-move", confirmed)
    board_slug = _check_board_slug(board or "main")
    conn = _connect(cfg)
    try:
        _migrate(conn)
        _migrate_canvas(conn)
        _get_board(conn, board_slug)
        row = _get_tab(conn, board_slug, tab)
        order = [r["id"] for r in _tabs(conn, board_slug)]
        order.remove(row["id"])
        try:
            pos = int(position)
        except (TypeError, ValueError):
            raise KanbanError(BAD_INPUT_CODE,
                              "tab position must be an integer") from None
        pos = max(0, min(pos, len(order)))
        order.insert(pos, row["id"])
        snap_id = _snapshot_canvas(conn, board_slug)
        for index, tab_id in enumerate(order):
            conn.execute("UPDATE board_tabs SET position=?, updated_at=?"
                         " WHERE id=?", (index, _now(), tab_id))
        conn.commit()
        _audit("canvas.tab_move", requested_by,
               {"board": board_slug, "tab": row["slug"], "position": pos})
        return {"state": "OK", "board": board_slug, "tab": row["slug"],
                "position": pos, "snapshot_id": snap_id,
                "rollback": f"canvas snapshot #{snap_id}"}
    finally:
        conn.close()


def tab_role_grant(cfg: Any, board: str, tab: str, principal: str,
                   role: str, *, requested_by: str = "cli:user",
                   confirmed: bool = False) -> Dict[str, Any]:
    """Grant a tab-scoped role (confirmed; never widens board access).

    A tab grant is a *ceiling* on top of the board role: it can only
    lower a principal's effective rank, and it grants nothing to a
    principal with no board role."""
    _canvas_guard(cfg, "tab-role-grant", confirmed)
    board_slug = _check_board_slug(board or "main")
    who = (principal or "").strip()[:80]
    if not who:
        raise KanbanError(BAD_INPUT_CODE, "principal is required")
    clean_role = (role or "").strip().lower()
    if clean_role not in CANVAS_ROLES:
        raise KanbanError(BAD_INPUT_CODE,
                          f"role must be one of {CANVAS_ROLES}")
    conn = _connect(cfg)
    try:
        _migrate(conn)
        _migrate_canvas(conn)
        _get_board(conn, board_slug)
        row = _get_tab(conn, board_slug, tab)
        snap_id = _snapshot_canvas(conn, board_slug)
        conn.execute("INSERT INTO board_tab_roles(tab_id, principal, role,"
                     " updated_at) VALUES(?,?,?,?)"
                     " ON CONFLICT(tab_id, principal) DO UPDATE SET"
                     " role=excluded.role, updated_at=excluded.updated_at",
                     (row["id"], who, clean_role, _now()))
        conn.commit()
        _audit("canvas.tab_role_grant", requested_by,
               {"board": board_slug, "tab": row["slug"], "principal": who,
                "role": clean_role})
        board_roles = _canvas_roles(conn, board_slug)
        if board_roles and who in board_roles:
            effective = min(_ROLE_RANK.get(board_roles[who], 0),
                            _ROLE_RANK.get(clean_role, 0))
            effective_role = next((r for r, rank in _ROLE_RANK.items()
                                   if rank == effective), "none")
        else:
            effective_role = "none"
        return {"state": "OK", "board": board_slug, "tab": row["slug"],
                "principal": who, "role": clean_role,
                "board_restricted": bool(board_roles),
                "effective_role": effective_role,
                "snapshot_id": snap_id,
                "rollback": f"canvas snapshot #{snap_id}"}
    finally:
        conn.close()


def tab_role_revoke(cfg: Any, board: str, tab: str, principal: str, *,
                    requested_by: str = "cli:user",
                    confirmed: bool = False) -> Dict[str, Any]:
    """Revoke a tab-scoped role (confirmed; auto-snapshots)."""
    _canvas_guard(cfg, "tab-role-revoke", confirmed)
    board_slug = _check_board_slug(board or "main")
    who = (principal or "").strip()[:80]
    if not who:
        raise KanbanError(BAD_INPUT_CODE, "principal is required")
    conn = _connect(cfg)
    try:
        _migrate(conn)
        _migrate_canvas(conn)
        _get_board(conn, board_slug)
        row = _get_tab(conn, board_slug, tab)
        snap_id = _snapshot_canvas(conn, board_slug)
        cur = conn.execute("DELETE FROM board_tab_roles WHERE tab_id=? AND"
                           " principal=?", (row["id"], who))
        conn.commit()
        _audit("canvas.tab_role_revoke", requested_by,
               {"board": board_slug, "tab": row["slug"], "principal": who})
        return {"state": "OK", "board": board_slug, "tab": row["slug"],
                "principal": who, "revoked": cur.rowcount > 0,
                "snapshot_id": snap_id,
                "rollback": f"canvas snapshot #{snap_id}"}
    finally:
        conn.close()


def tab_roles_list(cfg: Any, board: str, tab: str) -> Dict[str, Any]:
    """List tab-scoped roles (read-only)."""
    board_slug = _check_board_slug(board or "main")
    conn = _connect(cfg)
    try:
        _migrate(conn)
        _migrate_canvas(conn)
        _get_board(conn, board_slug)
        row = _get_tab(conn, board_slug, tab)
        return {"state": "OK", "board": board_slug, "tab": row["slug"],
                "roles": _tab_roles(conn, row["id"]),
                "board_roles": _canvas_roles(conn, board_slug)}
    finally:
        conn.close()


def layout_show(cfg: Any, board: str = "main") -> Dict[str, Any]:
    """Show the board column layout (read-only)."""
    slug = _check_board_slug(board or "main")
    conn = _connect(cfg)
    try:
        _migrate(conn)
        _migrate_canvas(conn)
        _get_board(conn, slug)
        row = conn.execute("SELECT columns FROM canvas_layouts WHERE board=?",
                           (slug,)).fetchone()
        columns = (json.loads(row["columns"]) if row
                   else list(_DEFAULT_COLUMNS))
        return {"state": "OK", "board": slug, "columns": columns,
                "custom": bool(row)}
    finally:
        conn.close()


def layout_set(cfg: Any, board: str, columns: List[str], *,
               requested_by: str = "cli:user",
               confirmed: bool = False) -> Dict[str, Any]:
    """Set the board column layout (confirmed; auto-snapshots)."""
    _canvas_guard(cfg, "layout-set", confirmed)
    slug = _check_board_slug(board or "main")
    clean = _check_columns(columns)
    conn = _connect(cfg)
    try:
        _migrate(conn)
        _migrate_canvas(conn)
        _get_board(conn, slug)
        snap_id = _snapshot_canvas(conn, slug)
        conn.execute("INSERT INTO canvas_layouts(board, columns, updated_at)"
                     " VALUES(?,?,?) ON CONFLICT(board) DO UPDATE SET"
                     " columns=excluded.columns,"
                     " updated_at=excluded.updated_at",
                     (slug, json.dumps(clean), _now()))
        conn.commit()
        _audit("canvas.layout_set", requested_by,
               {"board": slug, "columns": clean})
        return {"state": "OK", "board": slug, "columns": clean,
                "snapshot_id": snap_id,
                "rollback": f"canvas snapshot #{snap_id}"}
    finally:
        conn.close()


def role_grant(cfg: Any, board: str, principal: str, role: str, *,
               requested_by: str = "cli:user",
               confirmed: bool = False) -> Dict[str, Any]:
    """Grant a board role to a delegate principal (confirmed)."""
    _canvas_guard(cfg, "role-grant", confirmed)
    slug = _check_board_slug(board or "main")
    who = (principal or "").strip()[:80]
    if not who:
        raise KanbanError(BAD_INPUT_CODE, "principal is required")
    clean_role = (role or "").strip().lower()
    if clean_role not in CANVAS_ROLES:
        raise KanbanError(BAD_INPUT_CODE,
                          f"role must be one of {CANVAS_ROLES}")
    conn = _connect(cfg)
    try:
        _migrate(conn)
        _migrate_canvas(conn)
        _get_board(conn, slug)
        snap_id = _snapshot_canvas(conn, slug)
        conn.execute("INSERT INTO board_roles(board, principal, role,"
                     " updated_at) VALUES(?,?,?,?)"
                     " ON CONFLICT(board, principal) DO UPDATE SET"
                     " role=excluded.role, updated_at=excluded.updated_at",
                     (slug, who, clean_role, _now()))
        conn.commit()
        _audit("canvas.role_grant", requested_by,
               {"board": slug, "principal": who, "role": clean_role})
        return {"state": "OK", "board": slug, "principal": who,
                "role": clean_role, "restricted": True,
                "snapshot_id": snap_id,
                "rollback": f"canvas snapshot #{snap_id}"}
    finally:
        conn.close()


def role_revoke(cfg: Any, board: str, principal: str, *,
                requested_by: str = "cli:user",
                confirmed: bool = False) -> Dict[str, Any]:
    """Revoke a board role (confirmed; last role removal unrestricts)."""
    _canvas_guard(cfg, "role-revoke", confirmed)
    slug = _check_board_slug(board or "main")
    who = (principal or "").strip()[:80]
    if not who:
        raise KanbanError(BAD_INPUT_CODE, "principal is required")
    conn = _connect(cfg)
    try:
        _migrate(conn)
        _migrate_canvas(conn)
        _get_board(conn, slug)
        snap_id = _snapshot_canvas(conn, slug)
        cur = conn.execute("DELETE FROM board_roles WHERE board=? AND"
                           " principal=?", (slug, who))
        conn.commit()
        _audit("canvas.role_revoke", requested_by,
               {"board": slug, "principal": who})
        remaining = _canvas_roles(conn, slug)
        return {"state": "OK", "board": slug, "principal": who,
                "revoked": cur.rowcount > 0,
                "restricted": bool(remaining),
                "rollback": f"canvas snapshot #{snap_id}"}
    finally:
        conn.close()


def roles_list(cfg: Any, board: str = "main") -> Dict[str, Any]:
    """List board roles (read-only)."""
    slug = _check_board_slug(board or "main")
    conn = _connect(cfg)
    try:
        _migrate(conn)
        _migrate_canvas(conn)
        _get_board(conn, slug)
        roles = _canvas_roles(conn, slug)
        return {"state": "OK", "board": slug, "roles": roles,
                "restricted": bool(roles)}
    finally:
        conn.close()


def snapshot_list(cfg: Any, board: str = "main",
                  limit: int = LIST_DEFAULT) -> Dict[str, Any]:
    """List canvas snapshots newest-first (read-only, bounded)."""
    slug = _check_board_slug(board or "main")
    limit = max(1, min(int(limit or LIST_DEFAULT), LIST_MAX))
    conn = _connect(cfg)
    try:
        _migrate(conn)
        _migrate_canvas(conn)
        _get_board(conn, slug)
        return {"state": "OK", "board": slug, "snapshots": [
            {"id": r["id"], "created_at": r["created_at"]}
            for r in conn.execute("SELECT id, created_at FROM"
                                  " canvas_snapshots WHERE board=?"
                                  " ORDER BY id DESC LIMIT ?", (slug, limit))],
                "limit": limit}
    finally:
        conn.close()


def snapshot_restore(cfg: Any, snapshot_id: int, *,
                     requested_by: str = "cli:user",
                     confirmed: bool = False) -> Dict[str, Any]:
    """Restore a canvas snapshot (confirmed; pre-restore backup kept).

    Restores widgets, layout, roles, and task board/status positions
    captured in the snapshot. Tasks created after the snapshot are left
    in place (never deleted by a canvas rollback)."""
    _canvas_guard(cfg, "snapshot-restore", confirmed)
    conn = _connect(cfg)
    try:
        _migrate(conn)
        _migrate_canvas(conn)
        row = conn.execute("SELECT * FROM canvas_snapshots WHERE id=?",
                           (int(snapshot_id),)).fetchone()
        if row is None:
            raise KanbanError(NOT_FOUND_CODE, f"no snapshot #{snapshot_id}")
        try:
            snap = json.loads(row["payload"])
        except ValueError as exc:
            raise KanbanError(BAD_INPUT_CODE,
                              f"snapshot #{snapshot_id} is unreadable") \
                from exc
        if snap.get("schema") != SCHEMA:
            raise KanbanError(BAD_INPUT_CODE, "snapshot has unknown schema")
        board = snap["board"]
        _get_board(conn, board)
        pre_id = _snapshot_canvas(conn, board)
        conn.execute("DELETE FROM canvas_widgets WHERE board=?", (board,))
        for w in snap.get("widgets", []):
            raw_grants = w.get("grants", "[]")
            if not isinstance(raw_grants, str):
                raw_grants = json.dumps(raw_grants or [])
            conn.execute("INSERT INTO canvas_widgets(id, board, kind, title,"
                         " content, x, y, w, h, tab, grants, created_at,"
                         " updated_at)"
                         " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (w.get("id"), board, w.get("kind", "note"),
                          w.get("title", ""), w.get("content", ""),
                          w.get("x", 0), w.get("y", 0), w.get("w", 4),
                          w.get("h", 3), w.get("tab"), raw_grants,
                          w.get("created_at", _now()), _now()))
        conn.execute("DELETE FROM canvas_layouts WHERE board=?", (board,))
        for layout in snap.get("layouts", []):
            conn.execute("INSERT INTO canvas_layouts(board, columns,"
                         " updated_at) VALUES(?,?,?)",
                         (board, layout.get("columns",
                                            json.dumps(_DEFAULT_COLUMNS)),
                          _now()))
        conn.execute("DELETE FROM board_roles WHERE board=?", (board,))
        for role in snap.get("roles", []):
            conn.execute("INSERT INTO board_roles(board, principal, role,"
                         " updated_at) VALUES(?,?,?,?)",
                         (board, role.get("principal"),
                          role.get("role", "viewer"), _now()))
        conn.execute(
            "DELETE FROM board_tab_roles WHERE tab_id IN"
            " (SELECT id FROM board_tabs WHERE board=?)", (board,))
        conn.execute("DELETE FROM board_tabs WHERE board=?", (board,))
        for tab in snap.get("tabs", []):
            conn.execute("INSERT INTO board_tabs(id, board, slug, name,"
                         " position, created_at, updated_at)"
                         " VALUES(?,?,?,?,?,?,?)",
                         (tab.get("id"), board, tab["slug"],
                          tab.get("name", ""), tab.get("position", 0),
                          tab.get("created_at", _now()), _now()))
        for troupe in snap.get("tab_roles", []):
            conn.execute("INSERT INTO board_tab_roles(tab_id, principal,"
                         " role, updated_at) VALUES(?,?,?,?)",
                         (troupe.get("tab_id"), troupe.get("principal"),
                          troupe.get("role", "viewer"), _now()))
        moved = 0
        for pos in snap.get("task_positions", []):
            cur = conn.execute("SELECT id FROM tasks WHERE id=?",
                               (pos.get("id"),)).fetchone()
            if cur is None:
                continue
            conn.execute("UPDATE tasks SET board=?, status=?, updated_at=?"
                         " WHERE id=?",
                         (board, pos.get("status", "open"), _now(),
                          pos.get("id")))
            moved += 1
        conn.commit()
        _audit("canvas.snapshot_restore", requested_by,
               {"board": board, "snapshot_id": row["id"],
                "pre_restore_backup": pre_id})
        return {"state": "OK", "board": board,
                "restored_snapshot": row["id"], "tasks_repositioned": moved,
                "pre_restore_backup": pre_id,
                "rollback": f"canvas snapshot #{pre_id} restores the"
                            " pre-restore canvas"}
    finally:
        conn.close()
