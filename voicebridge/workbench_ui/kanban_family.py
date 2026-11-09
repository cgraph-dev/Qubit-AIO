"""``/api/kanban/*`` — the vendored kanban page on Qubit's OWN boards authority.

WHAT THE PORTED CLIENT ACTUALLY CALLS (quoted from ``static/panels.js``)
---------------------------------------------------------------------------
``panels.js:3187``  ``GET  /api/kanban/config``     → the page reads
                   ``lane_by_profile``, ``include_archived_by_default``,
                   ``default_tenant`` and falls back to ``config.columns``.
``panels.js:2645``  ``PATCH /api/kanban/config``    ``{lane_by_profile}``
                   → the saved record's ``lane_by_profile``.
``panels.js:3189``  ``GET  /api/kanban/assignees``  → ``{assignees: [...]}``
``panels.js:3198``  ``GET  /api/kanban/board?assignee=&tenant=
                   include_archived=1&only_mine=1&board=`` → ``{columns:
                   [{name, tasks}], tenants, assignees, latest_event_id,
                   read_only}``; ``data.changed === false`` short-circuits.
``panels.js:3236``  ``GET  /api/kanban/stats``      → ``{by_status: {status: n}}``
``panels.js:3251``  ``GET  /api/kanban/events?since=N`` → ``{events:[{task_id,…}],
                   latest_event_id}`` (or ``cursor``)
``panels.js:3282``  ``GET  /api/kanban/events/stream?since=N`` — ``EventSource``;
                   listens for the named events ``hello`` and ``events`` and
                   parses ``events``' data as ``{events, cursor}``.
``panels.js:3370``  ``POST /api/kanban/dispatch?dry_run=1&max=8`` →
                   ``{spawned[], promoted, reclaimed, skipped_unassigned[],
                   skipped_nonspawnable[], auto_blocked[], timed_out[],
                   crashed[]}``
``panels.js:3460``  ``POST /api/kanban/tasks/bulk`` ``{ids, status}``
``panels.js:3622``  ``POST /api/kanban/tasks``      ``{title[, body, assignee,
                   status, priority]}`` → ``{task: {id}}``
``panels.js:4197``  ``POST /api/kanban/links``      ``{parent_id, child_id}``
``panels.js:4209``  ``POST /api/kanban/links/delete`` ``{parent_id, child_id}``
``panels.js:4387``  ``GET  /api/kanban/boards``     → ``{boards:[{slug,name,
                   counts, total, is_current}], current}``
``panels.js:4661``  ``POST /api/kanban/boards``     ``{slug, name, switch,
                   default_workdir}`` → ``{board: {slug}}``
``panels.js:4687``  ``PATCH /api/kanban/boards/<slug>`` ``{name, …}``
``panels.js:4721``  ``DELETE /api/kanban/boards/<slug>`` → falls back to default
``panels.js:4524``  ``POST /api/kanban/boards/<slug>/switch``

THE AUTHORITY — ONE STORE, NOT A THIRD ONE
---------------------------------------------------------------------------
``docs/WORKBENCH_UI_PORT.md`` listed all twelve as GAP ("no kanban REST family
at these paths"). The *paths* were missing; the capability was not. Qubit
already owns a boards store and its whole read/write surface:

``voicebridge.kanban``              the Qubit-native store (``<state>/kanban.db``,
                                    board ``main``) the CLI, voice, the ops
                                    catalog, the task ledger and ``/qubit-boards``
                                    read AND write.
``kanban_bridge.BOARD_AUTHORITY``   names it the only writable authority.
``qubit_plugins.h_plugin_kanban_*`` the vendored kanban plugin's READ surface,
                                    already synthesizing the vendored wire shape
                                    from that store (``_native_task_vendor``).
``qubit_compat.h_plugin_kanban_*``  the plugin's WRITE surface — create, patch,
                                    bulk, comment, link, board create — all on
                                    that same store behind the shared confirm
                                    contract.

This module delegates to those and adds no writer of its own: every mutation
ends in ``voicebridge.kanban``, and every board/task response is synthesized
from that one database. Writing to the fork's ``qubit_cli.kanban_db`` instead
would be exactly the competing store ``kanban_bridge`` exists to prevent, and
~28 in-tree tests (``test_store_consolidation``, ``test_kanban_native_write9u``,
``test_dashboard_plugin_fleet`` …) pin the single-authority contract.

WHAT A SERVED RESPONSE SAYS OUT LOUD, AND WHY
---------------------------------------------------------------------------
* **Lanes.** The native store has ONE column set — ``open, claimed, blocked,
  done, archived`` — where the vendored board has nine (``triage, todo,
  scheduled, ready, running, blocked, review, done``). The board is served in
  the native set with ``native_statuses: true`` and ``upstream_columns`` listed,
  so a card's status is never mistranslated and a lane a user creates into is a
  lane that exists. Status *requests* — create, bulk, ``tasks`` — still accept
  either vocabulary (``VENDOR_TO_NATIVE``) and translate.
* **Events are synthesized, not a second log.** ``voicebridge.kanban`` records
  ``board=main`` and an integer task id; the page's shapes want ``task_id`` and
  a cursor. Events are re-derived from the native timeline (``kanban.log``) so
  the drawer and the board agree; ``latest_event_id`` is that timeline's length,
  which is exactly what ``?since=`` pages against. ``synthetic: true`` says so.
* **Sub-boards are real in the native store** (``boards_create`` /
  ``boards_list``), so the switcher lists and creates them there. A card cannot
  be moved BETWEEN boards through this page (the page has no such call), so the
  only cross-board action it offers — archive — is served by
  ``kanban.archive_task`` on the native store's side.
* **Dependencies are refused, typed.** The native store has no ``task_links``
  table and its dispatcher gates on none, so ``POST /links`` answers
  ``WB_CAPABILITY_UNBOUND`` naming the missing primitive instead of a fabricated
  ``200``; ``links/delete`` is an idempotent no-op (``removed: false``) so a
  stale drawer never toasts a failure for an edge that cannot exist.
* **Tenant.** ``voicebridge.kanban`` owns no tenant column; the board reports
  ``tenants: []`` and the accepted ``tenant`` filter narrows nothing. A
  fabricated tenant list would offer a filter that silently matched everything.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple

#: The vendored column vocabulary -> the native store's status set. Board and
#: task responses map the other way (``qubit_plugins._NATIVE_TO_VENDOR``); a
#: client may still ASK for a vendored lane (``status: "todo"`` on create,
#: ``status: "running"`` on bulk), so requests accept both and translate here.
VENDOR_TO_NATIVE: Dict[str, str] = {
    "triage": "open", "todo": "open", "scheduled": "open", "ready": "open",
    "running": "claimed", "review": "claimed", "blocked": "blocked",
    "done": "done", "archived": "archived",
}

#: The upstream lanes the page knows about.
UPSTREAM_COLUMNS: Tuple[str, ...] = (
    "triage", "todo", "scheduled", "ready", "running", "blocked", "review",
    "done",
)

#: The native lanes the board renders, in the store's own order.
NATIVE_COLUMNS: Tuple[str, ...] = ("open", "claimed", "blocked", "done")

#: ``GET /events`` returns at most this many events per call (the page polls
#: with ``?since=`` and only needs the delta).
EVENTS_LIMIT = 100

#: Stream cadence. Short enough that a card move shows up while the user is
#: looking at it; an idle board still costs one bounded SQL read per second.
POLL_SECONDS = 1.0
#: A heartbeat at this cadence keeps a proxied connection from idling out. The
#: client's parser ignores comment-only frames.
HEARTBEAT_SECONDS = 15.0
#: Bound on one stream. The browser's ``EventSource`` reconnects on close with
#: ``?since=`` = the last cursor it saw, so a bounded stream ends cleanly
#: instead of pinning a listener thread forever.
STREAM_MAX_SECONDS = 300.0

#: One condition variable per board, so an open stream announces its cursor to
#: its peers (they re-poll at once instead of sleeping out the leg) rather than
#: every tab carrying an independent loop.
_WAKE: Dict[str, threading.Condition] = {}
_WAKE_LOCK = threading.Lock()


# ----------------------------------------------------------------- helpers
def _refuse(code: str, detail: str, *, endpoint: str,
            counterpart: str = "") -> Tuple[int, Dict[str, Any]]:
    from .handlers_extra import _refuse as _he_refuse

    return _he_refuse(code, detail, endpoint=endpoint, counterpart=counterpart)


def _body(b: Any) -> Dict[str, Any]:
    return b if isinstance(b, dict) else {}


def _qbool(q: Dict[str, Any], key: str, default: bool = False) -> bool:
    raw = (q or {}).get(key)
    if raw is None or raw == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _qint(q: Dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(str((q or {}).get(key) or default))
    except (TypeError, ValueError):
        return default


def _board_arg(q: Dict[str, Any]) -> str:
    """Board slug as the NATIVE store names it (``default`` -> ``main``).

    The native store's first board is ``main``; the page says ``default`` for
    "the board I have not switched away from". Both resolve to the same board,
    and every call below passes the native name straight through.
    """
    raw = str((q or {}).get("board", "") or "").strip()
    return "main" if raw in ("", "default") else raw


def _kanban() -> Any:
    from .. import kanban as N

    return N


def _plugins() -> Any:
    from .. import qubit_plugins as HP

    return HP


def _compat() -> Any:
    from .. import qubit_compat as HC

    return HC


def _http(exc: Exception) -> Tuple[int, Dict[str, Any]]:
    """A native ``KanbanError`` -> the plugin's typed HTTP envelope."""
    return _compat()._qubit_plugins._kanban_http(exc)


def _bad_input(detail: str, *, counterpart: str = "") -> Tuple[int, Dict[str, Any]]:
    out: Dict[str, Any] = {"ok": False, "state": "FAILED",
                           "code": "KANBAN_BAD_INPUT", "detail": detail}
    if counterpart:
        out["qubit_counterpart"] = counterpart
    return 400, out


def _not_found(what: str) -> Tuple[int, Dict[str, Any]]:
    return 404, {"ok": False, "state": "FAILED", "code": "PLUGIN_HTTP",
                 "detail": f"{what} not found"}


def _task_id(raw: Any) -> Optional[int]:
    text = str(raw if raw is not None else "").strip()
    return int(text) if text.isdigit() else None


def _user_config() -> Dict[str, Any]:
    """The RAW user config.yaml — what ``atomic_config_write`` persists.

    Not ``cfg.data``: the settings module already learned that a value written
    to config.yaml was invisible to the merged/override read, so the panel wrote
    a setting and immediately re-read the old one.
    """
    from qubit_cli.config import get_config_path, read_user_config_raw  # type: ignore

    path = get_config_path()
    if path is None:
        return {}
    data = read_user_config_raw(path)
    return data if isinstance(data, dict) else {}


def _write_user_config(data: Dict[str, Any]) -> None:
    """Persist through ``atomic_config_write`` — the ONE atomic config writer."""
    from qubit_cli.config import atomic_config_write, get_config_path  # type: ignore

    path = get_config_path()
    if path is None:
        raise RuntimeError("qubit_cli.config.get_config_path is unavailable")
    atomic_config_write(path, data)


def _dig(data: Any, *path: str) -> Any:
    node: Any = data
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


# ------------------------------------------------------------- the config
def _config_record() -> Dict[str, Any]:
    saved = _dig(_user_config(), "dashboard", "kanban") or {}
    return {
        "default_tenant": saved.get("default_tenant") or "",
        "lane_by_profile": bool(saved.get("lane_by_profile", True)),
        "include_archived_by_default": bool(
            saved.get("include_archived_by_default", False)),
        "render_markdown": bool(saved.get("render_markdown", True)),
        # The lanes this board really has. The page falls back to
        # ``config.columns`` when a board read comes back empty; without this it
        # painted upstream's nine lanes over a store that has five statuses.
        "columns": list(NATIVE_COLUMNS),
        "native_statuses": True,
        "upstream_columns": list(UPSTREAM_COLUMNS),
    }


def config(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
           p: Dict[str, str]) -> Tuple[int, Any]:
    """``GET|PATCH /api/kanban/config`` — the board's display preferences.

    Read from ``dashboard.kanban`` (the section the vendored plugin's own
    ``GET /config`` reads). ``PATCH`` persists exactly ``lane_by_profile`` — the
    single key the ported page writes (``panels.js:2645``) — through
    ``atomic_config_write``, so the CLI, the SPA and this page cannot disagree.
    """
    method = str((p or {}).get("method") or "GET").upper()
    record = _config_record()
    if method != "PATCH":
        return 200, {"ok": True, **record}

    patch = _body(b)
    if "lane_by_profile" not in patch:
        return _bad_input(
            "PATCH /api/kanban/config accepts lane_by_profile only; no other "
            "key in this record is writable through the workbench.",
            counterpart="config.yaml `dashboard.kanban`")
    data = _user_config()
    dashboard = data.get("dashboard")
    if not isinstance(dashboard, dict):
        dashboard = data["dashboard"] = {}
    section = dashboard.get("kanban")
    if not isinstance(section, dict):
        section = dashboard["kanban"] = {}
    section["lane_by_profile"] = bool(patch["lane_by_profile"])
    try:
        _write_user_config(data)
    except Exception as exc:  # noqa: BLE001 - a write failure is a typed refusal
        return _refuse("WB_BACKEND_UNAVAILABLE",
                       f"dashboard.kanban could not be written: "
                       f"{type(exc).__name__}: {exc}", endpoint="kanban/config")
    return 200, {"ok": True, **_config_record()}


# --------------------------------------------------------------- the board
def board(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
          p: Dict[str, str]) -> Tuple[int, Any]:
    """``GET /api/kanban/board`` — the board, in the native store's lanes."""
    plan = _board_plan(cfg, q)
    if "refusal" in plan:
        return plan["refusal"]
    columns = plan["columns"]
    return 200, {
        "ok": True,
        "columns": [{"name": name, "tasks": columns.get(name, [])}
                    for name in NATIVE_COLUMNS],
        # No tenant column exists on the native store; an invented list would
        # offer a filter that silently matched nothing.
        "tenants": [],
        "assignees": plan["assignees"],
        "latest_event_id": plan["latest"],
        "now": int(time.time()),
        # The page paints its "read-only view" banner from this flag; it is TRUE
        # only when the write authority itself says the store is not writable.
        "read_only": False,
        "native_statuses": True,
        "upstream_columns": list(UPSTREAM_COLUMNS),
        "store": {"authoritative": "qubit-native",
                  "db_path": plan.get("db_path")},
    }


def _board_plan(cfg: Any, q: Dict[str, Any]) -> Dict[str, Any]:
    """The board's lanes plus its cursor, or ``{"refusal": (status, payload)}``."""
    N = _kanban()
    board_slug = _board_arg(q)
    assignee = str((q or {}).get("assignee") or "").strip()
    include_archived = _qbool(q, "include_archived")
    status = "archived" if include_archived else ""
    try:
        # A board the store has no row for yet is an EMPTY board, not a missing
        # one: the vendored reader answers a fresh store the same way, so the
        # panel renders its create affordance instead of an error card.
        if board_slug != "main" and not N.board_exists(cfg, board_slug):
            return {"refusal": _not_found(f"board {board_slug!r}")}
        try:
            out = N.list_tasks(cfg, board=board_slug, status=status)
            events = N.log(cfg, board=board_slug, limit=EVENTS_LIMIT)
        except N.KanbanError as exc:
            # A store with no ``main`` row yet is an EMPTY board, not a missing
            # one — the vendored reader answers a fresh store the same way, so
            # the panel renders its create affordance instead of an error card.
            if "NOT_FOUND" not in str(getattr(exc, "code", "")) \
                    or board_slug != "main":
                raise
            out = {"tasks": []}
            events = {"events": []}
    except Exception as exc:  # noqa: BLE001
        return {"refusal": _http(exc)}
    tasks = [t for t in (out.get("tasks") or [])
             if not assignee or str(t.get("assignee") or "") == assignee]
    columns: Dict[str, List[Dict[str, Any]]] = {c: [] for c in NATIVE_COLUMNS}
    for task in tasks:
        columns.setdefault(str(task.get("status") or "open"), []).append(
            _card(task))
    # The picker's value set: the full roster (profiles on disk included, so a
    # fresh profile is filterable before it owns a card), not just this page of
    # tasks. A complete board is one query; a paged one is not, and the page
    # asks for the whole board.
    try:
        names = sorted({str(entry.get("name") or "") for entry in
                        (_plugins().h_plugin_kanban_assignees(
                            cfg, q, {}, {})[1].get("assignees") or [])} - {""})
    except Exception:  # noqa: BLE001 - a roster is best-effort, never a 500
        names = sorted({str(t.get("assignee") or "") for t in tasks} - {""})
    return {"columns": columns, "assignees": names,
            "latest": _cursor(events),
            "db_path": str(N._db_path(cfg))}


def _card(task: Dict[str, Any]) -> Dict[str, Any]:
    """One native task in the vendored renderer's row shape.

    ``id``/``title``/``body``/``assignee``/``status``/``priority`` are what the
    card and its filter read; ``age`` is computed so a stale card can be
    coloured without the client doing date math on a missing field.
    """
    created = float(task.get("created_at") or 0)
    return {
        "id": str(task.get("id")),
        "board": task.get("board"),
        "title": task.get("title") or "",
        "body": task.get("body") or "",
        "status": str(task.get("status") or "open"),
        "assignee": task.get("assignee") or None,
        "tenant": None,
        "priority": _priority_int(task.get("priority")),
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
        "age": {"created_age_seconds": max(0.0, time.time() - created)
                if created else None,
                "started_age_seconds": None,
                "time_to_complete_seconds": None},
        "comment_count": 0,
        "link_counts": {"parents": 0, "children": 0},
        "progress": None,
        "workspace_kind": "scratch",
        "workspace_path": None,
        "skills": [],
        "goal_mode": False,
        "runs": [],
        "diagnostics": [],
        "latest_summary": None,
    }


_PRIORITY_INT = {"low": 0, "normal": 1, "high": 2}


def _priority_int(value: Any) -> int:
    """Native priority name -> the vendored ``0/1/2`` scale the page renders."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value if value in (0, 1, 2) else 1
    return _PRIORITY_INT.get(str(value or "normal").strip().lower(), 1)


def _priority_name(value: Any) -> Optional[str]:
    """The vendored ``0/1/2`` (or a native name) -> a native priority name."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return {0: "low", 1: "normal", 2: "high"}.get(value)
    text = str(value).strip().lower()
    return text if text in ("low", "normal", "high") else None


def stats(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
          p: Dict[str, str]) -> Tuple[int, Any]:
    """``GET /api/kanban/stats`` — per-status counts, straight from the store."""
    N = _kanban()
    board = _board_arg(q)
    try:
        _ensure_board(cfg, board)
        out = N.stats(cfg, board=board)
    except Exception as exc:  # noqa: BLE001
        return _http(exc)
    by_status: Dict[str, int] = {}
    for counts in (out.get("stats") or {}).values():
        for key, value in (counts or {}).items():
            by_status[str(key)] = by_status.get(str(key), 0) + int(value or 0)
    return 200, {"ok": True, "by_status": by_status,
                 "by_assignee": {}, "oldest_ready_age_seconds": None,
                 "now": int(time.time()), "native_statuses": True}


def assignees(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
              p: Dict[str, str]) -> Tuple[int, Any]:
    """``GET /api/kanban/assignees`` — the assignee picker's roster."""
    return _plugins().h_plugin_kanban_assignees(cfg, q, b, p)


# ------------------------------------------------------------------ boards
def boards(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
           p: Dict[str, str]) -> Tuple[int, Any]:
    """``GET|POST /api/kanban/boards`` — the switcher's list, and create."""
    if str((p or {}).get("method") or "GET").upper() == "POST":
        return _boards_create(cfg, _body(b))
    N = _kanban()
    try:
        _ensure_board(cfg, "main")
        out = N.boards_list(cfg)
    except Exception as exc:  # noqa: BLE001
        return _http(exc)
    rows = [_board_row(entry) for entry in out.get("boards") or []]
    current = _current(cfg)
    for row in rows:
        row["is_current"] = row["slug"] == current
    return 200, {"ok": True, "boards": rows, "current": current}


def _board_row(entry: Dict[str, Any]) -> Dict[str, Any]:
    counts = entry.get("counts") or {}
    return {
        "slug": entry.get("board") or entry.get("slug"),
        "name": entry.get("name"),
        "counts": counts,
        # Live cards only: archived tasks are hidden from every default board
        # view, so counting them in a badge would visibly disagree with it.
        "total": sum(int(v or 0) for k, v in counts.items() if k != "archived"),
        "is_current": True,
        "default_workspace_kind": "scratch",
        "default_workdir": entry.get("default_workdir"),
        "project_id": entry.get("project_slug"),
        "project_name": entry.get("project_slug"),
        "archived": False,
    }


def _ensure_board(cfg: Any, slug: str, name: str = "") -> None:
    """Make sure ``main`` has its row before it is listed or written to.

    ``voicebridge.kanban.boards_list`` lists the ``boards`` table as it is, and
    a store that has never had a task created gets no ``main`` row (``create``
    inserts it lazily). The vendored reader treats a fresh store's default board
    as PRESENT-but-empty, and a switcher that shows no board at all while the
    board below it renders is the kind of disagreement this family exists to
    prevent — so the default row is written here, once, through the store's own
    connection.
    """
    conn = _kanban()._connect(cfg)
    try:
        _kanban()._migrate(conn)
        if conn.execute("SELECT 1 FROM boards WHERE slug=?",
                        (slug,)).fetchone() is None:
            conn.execute("INSERT INTO boards(slug, name, created_at)"
                         " VALUES(?,?,?)", (slug, name or slug, time.time()))
            conn.commit()
    finally:
        conn.close()


def _current(cfg: Any) -> str:
    """The active board. This store keeps ONE board, so it is always ``main``."""
    rows = _kanban().boards_list(cfg).get("boards") or []
    return str(rows[0].get("board")) if rows else "main"


def _boards_create(cfg: Any, payload: Dict[str, Any]) -> Tuple[int, Any]:
    """Create a board on the native store, then bind its project/display scope."""
    from .. import kanban as N

    slug = str(payload.get("slug") or "").strip()
    if not slug:
        return _bad_input("slug is required",
                          counterpart="CLI `qubit kanban boards-create`")
    workdir = str(payload.get("default_workdir") or "").strip()
    if workdir:
        from .workspace_scope import ScopeRefusal, confine

        try:
            confine(cfg, workdir)
        except ScopeRefusal as exc:
            return 400, {"ok": False, "state": "FAILED", "code": exc.code,
                         "detail": exc.detail,
                         "qubit_counterpart": "CLI `qubit project add-folder`"}
    try:
        made = N.boards_create(cfg, slug, str(payload.get("name") or ""),
                               requested_by="workbench:user", confirmed=True)
    except Exception as exc:  # noqa: BLE001
        return _http(exc)
    created = str(made.get("board") or slug)
    if workdir:
        try:
            N.set_board_project(cfg, created, workdir, requested_by="workbench:user")
        except Exception as exc:  # noqa: BLE001 - the board exists; say so
            return 200, {"ok": True, "board": _board_row(
                {"board": created, "name": made.get("name"), "counts": {}}),
                "current": _current(cfg),
                "warning": f"board created but its project scope was not "
                           f"applied: {type(exc).__name__}: {exc}"}
    rows = {r.get("board"): r for r in
            (N.boards_list(cfg).get("boards") or [])}
    row = _board_row(rows.get(created) or {"board": created,
                                           "name": made.get("name"),
                                           "counts": {}})
    total = sum(int(v or 0) for k, v in (row.get("counts") or {}).items()
                if k != "archived")
    row["total"] = total
    row["is_current"] = created == _current(cfg)
    return 200, {"ok": True, "board": row, "current": _current(cfg),
                 "qubit_counterpart": "CLI `qubit kanban boards-create`"}


def board_verb(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /boards/<slug>/switch``, ``PATCH|DELETE /boards/<slug>``."""
    from .. import kanban as N

    sub = str((p or {}).get("sub") or "").strip("/")
    tail = sub.split("/", 2)[2] if sub.startswith("kanban/boards/") else ""
    parts = [seg for seg in tail.split("/") if seg]
    slug = str(parts[0]).strip().lower() if parts else ""
    action = "/".join(parts[1:])
    method = str((p or {}).get("method") or "").upper()
    if not slug:
        return _not_found("board")
    native = "main" if slug == "default" else slug
    try:
        # The default board always exists (it is the store's own default), so a
        # store that has never held a task still answers its slug-scoped verbs
        # instead of claiming the one board it has is missing.
        _ensure_board(cfg, native)
        known = {str(r.get("board")) for r in
                 (N.boards_list(cfg).get("boards") or [])}
    except Exception as exc:  # noqa: BLE001
        return _http(exc)
    if native not in known:
        return _not_found(f"board {slug!r}")
    try:
        if action == "switch" and method == "POST":
            return 200, {"ok": True, "current": native,
                         "qubit_counterpart": "CLI `qubit kanban boards`"}
        if method == "PATCH":
            name = str(_body(b).get("name") or "").strip()
            if not name:
                return _bad_input(
                    "PATCH /api/kanban/boards/<slug> renames a board; send "
                    "`name`. Description/icon/color have no native column.",
                    counterpart="CLI `qubit kanban boards edit --name`")
            rows = {r.get("board"): r for r in
                    (N.boards_list(cfg).get("boards") or [])}
            target = dict(rows.get(native) or {})
            # ``voicebridge.kanban`` has no board-rename verb; the name is a
            # column, so this one UPDATE goes through the module's own
            # connection + validation, not a new store.
            _rename_board(cfg, native, name)
            target["name"] = name
            return 200, {"ok": True, "board": _board_row(target)}
        if method == "DELETE":
            if native == "main":
                return _refuse(
                    "WB_READ_ONLY_SURFACE",
                    "the main board cannot be archived; archive its tasks "
                    "instead (`qubit kanban archive <id>`)",
                    endpoint="kanban/boards",
                    counterpart="CLI `qubit kanban archive <task-id>`")
            # The native store archives a BOARD by archiving its tasks: there is
            # no board-lifecycle verb, and inventing a directory delete would
            # bypass the store's only writer.
            out = N.list_tasks(cfg, board=native, limit=N.LIST_MAX)
            archived = 0
            for task in out.get("tasks") or []:
                N.archive_task(cfg, int(task["id"]), requested_by="workbench:user",
                               confirmed=True)
                archived += 1
            return 200, {"ok": True, "result": {"slug": native,
                                                "action": "archived",
                                                "archived_tasks": archived},
                         "current": _current(cfg),
                         "detail": ("the native store keeps boards as rows, so "
                                    "archiving a board archives its tasks; "
                                    "`qubit kanban restore` brings each back"),
                         "qubit_counterpart": "CLI `qubit kanban restore`"}
    except Exception as exc:  # noqa: BLE001
        return _http(exc)
    return _refuse("WB_CAPABILITY_UNBOUND",
                   f"{method or '?'} /api/kanban/boards/{slug}"
                   f"{'/' + action if action else ''} has no Qubit authority",
                   endpoint="kanban/boards")


def _rename_board(cfg: Any, slug: str, name: str) -> None:
    N = _kanban()

    conn = N._connect(cfg)
    try:
        N._migrate(conn)
        N._get_board(conn, slug)
        conn.execute("UPDATE boards SET name=? WHERE slug=?",
                     (N.redact_spec(N._check_title(name)[:80]),
                      N._check_board_slug(slug)))
        conn.commit()
        N._audit("boards.rename", "workbench:user", {"board": slug})
    finally:
        conn.close()


# ------------------------------------------------------------------ tasks
def tasks(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
          p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /api/kanban/tasks`` — create on the native store.

    The page sends ``title`` first (``panels.js:3622``) and later the modal's
    full set (``title, body, assignee, status, priority``). Everything else the
    native ``create`` accepts is NOT accepted here: the page never sends it, and
    accepting it would let an unvetted body choose a workspace path.
    """
    from .. import kanban as N

    payload = _body(b)
    title = str(payload.get("title") or "").strip()
    if not title:
        return _bad_input("title is required",
                          counterpart="CLI `qubit kanban create`")
    priority = _priority_name(payload.get("priority")) or "normal"
    raw_status = str(payload.get("status") or "").strip().lower()
    if raw_status and raw_status not in VENDOR_TO_NATIVE:
        return _bad_input(
            f"unknown status {raw_status!r}; this board has "
            f"{', '.join(NATIVE_COLUMNS)}",
            counterpart="CLI `qubit kanban move <id> --status`")
    native_status = VENDOR_TO_NATIVE.get(raw_status, "") if raw_status else ""
    if native_status == "claimed":
        return _refuse(
            "WB_CAPABILITY_UNBOUND",
            "a card enters `claimed` only by a worker claiming it (that is the "
            "dispatcher's write, not the board's); created nothing.",
            endpoint="kanban/tasks",
            counterpart="CLI `qubit kanban claim <task-id>`")
    try:
        out = N.create(cfg, title, board=_board_arg(q),
                       body=str(payload.get("body") or ""),
                       assignee=str(payload.get("assignee") or ""),
                       priority=priority, requested_by="workbench:user",
                       confirmed=True)
    except Exception as exc:  # noqa: BLE001
        return _http(exc)
    task = out.get("task") or {}
    moved = _apply_status(cfg, N, int(task["id"]), raw_status) \
        if raw_status and VENDOR_TO_NATIVE[raw_status] != "open" else None
    if isinstance(moved, tuple):
        return moved
    if moved is not None:
        task = moved
    return 200, {"ok": True, "task": _card(task),
                 "qubit_counterpart": "CLI `qubit kanban create`"}


def _apply_status(cfg: Any, N: Any, task_id: int, status: str):
    """Run one vendored status through the native transition verbs."""
    native = VENDOR_TO_NATIVE.get(status)
    if native is None:
        return _bad_input(f"unknown status {status!r}; this board has "
                          f"{', '.join(NATIVE_COLUMNS)}",
                          counterpart="CLI `qubit kanban move <id> --status`")
    verbs = {
        "open": N.move_task,
        "claimed": N.claim,
        "blocked": N.block,
        "done": N.complete,
        "archived": N.archive_task,
    }
    verb = verbs.get(native)
    if verb is None:  # pragma: no cover - every native status is mapped above
        return _bad_input(f"cannot move a task to {native!r}")
    try:
        if native == "open":
            out = verb(cfg, task_id, status="open",
                       requested_by="workbench:user", confirmed=True)
        else:
            out = verb(cfg, task_id, requested_by="workbench:user",
                       confirmed=True)
    except Exception as exc:  # noqa: BLE001
        return _http(exc)
    return out.get("task") if isinstance(out, dict) else None


def tasks_bulk(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
               p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /api/kanban/tasks/bulk`` ``{ids, status}`` — per-id, reported each."""
    from .. import kanban as N

    payload = _body(b)
    ids = payload.get("ids")
    if not isinstance(ids, list) or not ids:
        return _bad_input("ids is required",
                          counterpart="CLI `qubit kanban move <id> --status`")
    raw_status = str(payload.get("status") or "").strip().lower()
    if not raw_status:
        return _bad_input("status is required",
                          counterpart="CLI `qubit kanban move <id> --status`")
    results: List[Dict[str, Any]] = []
    for raw in ids:
        tid = _task_id(raw)
        if tid is None:
            results.append({"id": str(raw), "ok": False,
                            "error": "task ids are integers on this store"})
            continue
        moved = _apply_status(cfg, N, tid, raw_status)
        if isinstance(moved, tuple):
            results.append({"id": str(raw), "ok": False,
                            "error": moved[1].get("detail") or moved[1].get("code")})
        else:
            results.append({"id": str(raw), "ok": True})
    ok = sum(1 for r in results if r["ok"])
    return 200, {"ok": ok > 0, "results": results, "updated": ok,
                 "failed": len(results) - ok, "status": raw_status,
                 "qubit_counterpart": "CLI `qubit kanban move <id> --status`"}


# ------------------------------------------------------------------ links
def links(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
          p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /api/kanban/links`` — REFUSED, typed: the native store has no edges.

    ``voicebridge.kanban`` has no dependency table and its dispatcher gates on
    none, so an accepted link would either be dropped on the next read or would
    have to be kept in a second store — precisely the split this family exists
    to avoid. The refusal names the missing primitive so the drawer shows a
    reason instead of a toast claiming the dependency was added.
    """
    payload = _body(b)
    parent = str(payload.get("parent_id") or "").strip()
    child = str(payload.get("child_id") or "").strip()
    return _refuse(
        "WB_CAPABILITY_UNBOUND",
        "task dependencies are not a capability of Qubit's boards store "
        "(no task_links table); no edge was recorded between "
        f"{parent or '<parent>'} and {child or '<child>'}.",
        endpoint="kanban/links",
        counterpart="Qubit boards have no dependency edges")


def links_delete(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                 p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /api/kanban/links/delete`` — idempotent: there is nothing to remove.

    Answered ``200`` with ``removed: false`` on purpose: no edge can exist on
    this store, so the request's intent is already satisfied. A 5xx here would
    make a drawer's "remove dependency" button toast a failure for a state the
    server cannot be in.
    """
    payload = _body(b)
    return 200, {"ok": True, "removed": False,
                 "parent_id": str(payload.get("parent_id") or ""),
                 "child_id": str(payload.get("child_id") or ""),
                 "detail": ("the native boards store has no dependency edges, "
                            "so there is nothing to unlink"),
                 "qubit_counterpart": "Qubit boards have no dependency edges"}


# --------------------------------------------------------------- dispatch
def dispatch(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
             p: Dict[str, str]) -> Tuple[int, Any]:
    """``POST /api/kanban/dispatch`` — one dispatcher tick on the native store.

    There is no ``dispatch_once`` in ``voicebridge.kanban``, and a real
    dispatcher tick SPAWNS worker subprocesses (``qubit -p <assignee>`` per
    claimed task) — budget-consuming work this page's button already confirms
    before it asks. So:

    * ``?dry_run=1`` (the Nudge button, ``panels.js:3370``) answers a REAL
      report: which tasks are queued, which are claimed, which are blocked. It
      changes nothing, so it is served here from the same ``list_tasks`` read
      the board itself uses.
    * a real run is a typed ``WB_CAPABILITY_UNBOUND`` naming the authority that
      does spawn workers (``qubit kanban run``), because this listener must not
      start agent processes as a side effect of a dashboard click.
    """
    N = _kanban()
    board_slug = _board_arg(q)
    dry_run = _qbool(q, "dry_run")
    try:
        out = N.list_tasks(cfg, board=board_slug, limit=N.LIST_MAX)
    except Exception as exc:  # noqa: BLE001
        return _http(exc)
    queued = [t for t in out.get("tasks") or [] if t.get("status") == "open"]
    claimed = [t for t in out.get("tasks") or [] if t.get("status") == "claimed"]
    blocked = [t for t in out.get("tasks") or [] if t.get("status") == "blocked"]
    limit = max(1, min(_qint(q, "max", 8) or 8, 64))
    spawnable = [t for t in queued if t.get("assignee")][:limit]
    report = {
        "ok": True,
        "dry_run": dry_run,
        # The page reads ``.length`` on these lists and counts on the ints.
        "spawned": ([{"task_id": str(t["id"]), "assignee": t.get("assignee")}
                     for t in spawnable] if dry_run else []),
        "promoted": 0,
        "reclaimed": 0,
        "skipped_unassigned": [str(t["id"]) for t in queued
                               if not t.get("assignee")],
        "skipped_nonspawnable": [],
        "auto_blocked": [],
        "timed_out": [],
        "crashed": [],
        "queued": len(queued),
        "claimed": len(claimed),
        "blocked": len(blocked),
        "board": board_slug,
        "qubit_counterpart": "CLI `qubit kanban run` / `qubit kanban claim`",
    }
    if dry_run:
        return 200, report
    code, refusal = _refuse(
        "WB_CAPABILITY_UNBOUND",
        "a real dispatcher tick spawns one worker per claimed task; this "
        "listener does not start agent processes. Nothing was spawned.",
        endpoint="kanban/dispatch",
        counterpart="CLI `qubit kanban run --max N`")
    return code, {**refusal, "dry_run": False, **report}


# ----------------------------------------------------------------- events
def _cursor(events: Dict[str, Any]) -> int:
    """The page's event cursor: the number of events read from the timeline.

    The reads are bounded (``kanban.log`` pages ``EVENTS_LIMIT`` newest-first),
    so the cursor is a position in that page: the newest event's. ``?since=``
    pages against it and every event is re-derived from the same timeline, which
    is the contract upstream's ``latest_event_id`` has.
    """
    return len((events or {}).get("events") or [])


def _stamp(events: List[Dict[str, Any]], cursor: int) -> List[Dict[str, Any]]:
    """Stamp timeline positions on a newest-first page (oldest = ``cursor``)."""
    out: List[Dict[str, Any]] = []
    for offset, ev in enumerate(events):
        out.append({
            "id": max(0, cursor - offset),
            "task_id": ev.get("task_id"),
            "kind": ev.get("kind") or "",
            "title": ev.get("title"),
            "detail": ev.get("detail"),
            "payload": None,
            "created_at": ev.get("created_at"),
        })
    return out


def _timeline(cfg: Any, board_slug: str, since: int) -> Tuple[int, Any]:
    """``(http_status, payload)`` — the delta after ``since``, oldest-first."""
    N = _kanban()
    try:
        _ensure_board(cfg, board_slug)
        out = N.log(cfg, board=board_slug, limit=EVENTS_LIMIT)
    except Exception as exc:  # noqa: BLE001
        return _http(exc)
    events = list(out.get("events") or [])
    cursor = len(events)
    # ``log`` is newest-first with position ``cursor`` for the newest; the delta
    # is everything newer than the caller's cursor, delivered oldest-first so a
    # reader applying them in order ends where a fresh board read would.
    fresh = events[:max(0, cursor - max(0, since))][::-1]
    stamped = _stamp(fresh, cursor)
    return 200, {"ok": True,
                 "events": list(reversed(stamped)),
                 "latest_event_id": cursor,
                 "cursor": cursor,
                 "synthetic": True,
                 "now": int(time.time())}


def _events_payload(cfg: Any, q: Dict[str, Any]) -> Tuple[int, Any]:
    return _timeline(cfg, _board_arg(q), _qint(q, "since", 0))


def events(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
           p: Dict[str, str]) -> Tuple[int, Any]:
    """``GET /api/kanban/events?since=N`` — the board timeline's delta."""
    return _events_payload(cfg, q)


def _wake_for(board_slug: str) -> threading.Condition:
    with _WAKE_LOCK:
        cond = _WAKE.get(board_slug)
        if cond is None:
            cond = _WAKE[board_slug] = threading.Condition()
        return cond


def stream(sub: str, cfg: Any, q: Optional[Dict[str, Any]] = None) -> Iterator[str]:
    """SSE frames for ``kanban/events/stream`` — the page's live channel.

    Framing is :func:`voicebridge.workbench_ui_routes.stream_sse`'s: this
    iterator only produces frames. The named events are the two the client
    listens for (``panels.js:3289``): ``hello`` once on connect (its handshake
    resets the reconnect ladder) and ``events`` for every delta, whose data is
    ``{events, cursor}``.

    Bounded on purpose (``STREAM_MAX_SECONDS``): the client's ``EventSource``
    reconnects with its last cursor, so a closed stream resumes with no
    duplicates and the listener never accumulates one thread per open tab
    forever.
    """
    raw_q: Dict[str, Any] = dict(q or {})
    board_slug = _board_arg(raw_q)
    since = max(0, _qint(raw_q, "since", 0))
    cond = _wake_for(board_slug)
    deadline = time.time() + STREAM_MAX_SECONDS
    yield ("event: hello\ndata: " + json.dumps(
        {"event": "hello", "suite": "kanban", "board": board_slug}) + "\n\n")
    last_beat = time.time()
    while time.time() < deadline:
        code, payload = _timeline(cfg, board_slug, since)
        if code != 200:
            yield ("event: error\ndata: " + json.dumps(
                {"event": "error", "code": payload.get("code"),
                 "detail": payload.get("detail")}) + "\n\n")
            return
        cursor = int(payload.get("latest_event_id") or 0)
        if cursor != since:
            yield ("event: events\ndata: " + json.dumps(
                {"events": payload.get("events") or [], "cursor": cursor})
                + "\n\n")
            since = cursor
            last_beat = time.time()
            continue
        # Nothing new. Announce this cursor to the board's other streams (they
        # re-poll at once instead of sleeping out the leg), then wait for an
        # announcement or the next poll. A write from ANOTHER process cannot be
        # signalled — the poll is what makes that visible, which is why it stays
        # on every stream rather than being replaced by a signal.
        with cond:
            cond.notify_all()
            cond.wait(POLL_SECONDS)
        if time.time() - last_beat >= HEARTBEAT_SECONDS:
            # Comment-only frame: the client's parser ignores it, a proxy does
            # not idle the connection out.
            yield ": keep-alive\n\n"
            last_beat = time.time()


#: Paths below ``kanban/`` whose tail is a slug, not a verb (``boards/<slug>``,
#: ``boards/<slug>/switch``). The routes demand the slug, so these are answered
#: by the same PORT_SET row through ``_resolve_handler``'s prefix test; the tuple
#: exists so the port table can name them in one place.
BOARD_VERB_PREFIXES: Tuple[str, ...] = ("kanban/boards/",)

__all__ = [
    "VENDOR_TO_NATIVE", "UPSTREAM_COLUMNS", "NATIVE_COLUMNS",
    "BOARD_VERB_PREFIXES", "EVENTS_LIMIT", "POLL_SECONDS",
    "HEARTBEAT_SECONDS", "STREAM_MAX_SECONDS",
    "assignees", "board", "board_verb", "boards", "config", "dispatch",
    "events", "links", "links_delete", "stats", "stream", "tasks",
    "tasks_bulk",
]
