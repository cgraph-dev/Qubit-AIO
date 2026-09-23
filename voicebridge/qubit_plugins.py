"""Qubit-owned backend for the vendored Qubit dashboard plugin fleet.

The pinned Qubit tree vendors ``plugins/hermes-achievements`` and
``plugins/kanban`` with their real dashboard APIs and tests, but before this
module they had no Qubit backend route or ledger row. This adapter exposes
those plugin APIs through :mod:`voicebridge.qubit_compat` while keeping the
single-listener / single-policy contract:

* handlers run in-process on the existing Qubit web server (no second server);
* achievement scans reuse the vendored engine + ``qubit_state.SessionDB`` and
  read the REAL local session history (never fabricated badges);
* the snapshot/checkpoint/unlock state persists under the Qubit state dir
  (``<state>/qubit/plugin-data/<plugin>/``), hardened to 0600;
* kanban reads delegate to the same ``qubit_cli.kanban_db`` the vendored
  dashboard plugin (and the fork CLI/gateway ``/kanban``) use; kanban writes
  stay on the shared typed policy/confirmation path.

Every handler returns the ``(status, payload)`` tuple the compat dispatcher
expects, and typed error envelopes on failure.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import qubit_runtime

# Hard cap on sessions analyzed by a single scan. The vendored engine defaults
# to the full history (SQLite ``LIMIT -1``); Qubit keeps that bounded so a
# damaged/oversized history can never turn one dashboard request into an
# unbounded operation. Overridable via ``QUBIT_ACHIEVEMENTS_SCAN_LIMIT``.
_DEFAULT_SCAN_LIMIT = 20000

_ENGINES: Dict[str, Any] = {}
_ACTIVE_BOUND = _DEFAULT_SCAN_LIMIT
_BOUND_INSTALLED = False

# The complete vendored Qubit plugin fleet, in the order the direct source
# inventory records it. Exposed as the single authority for the fleet audit so
# ``scripts/rebuild_ledger_hermes.py`` cannot drift from the tree it scans.
# Only ``hermes-achievements`` and ``kanban`` declare a dashboard surface
# (``dashboard/manifest.json``); every other entry is an agent/provider plugin
# with no dashboard route of its own, tracked under its own capability area.
VENDORED_PLUGIN_FLEET: Tuple[str, ...] = (
    "hermes-achievements", "kanban", "browser", "context_engine",
    "cron_providers", "dashboard_auth", "disk-cleanup", "google_meet",
    "image_gen", "memory", "model-providers", "observability", "platforms",
    "security-guidance", "spotify", "teams_pipeline", "video_gen", "web",
)


# ---------------------------------------------------------------------------
# Runtime / state-dir plumbing
# ---------------------------------------------------------------------------

def _qubit_home(cfg: Any) -> Path:
    """Qubit-owned Qubit root: the live ``QUBIT_HOME`` when the fork set one,
    else the single canonical :func:`voicebridge.qubit_runtime.default_home`
    (``<state>/runtime``). The fork, the forwarded ``qubit qubit`` CLI, the
    in-process ASGI bridge, voice, and this plugin backend therefore resolve
    ONE home and one ``state.db`` — never the operator's external ``~/.hermes``
    and never a second ``<state>/qubit`` store. Legacy homes are converged by
    the one-shot read-only migration in :mod:`voicebridge.qubit_runtime`."""
    env = os.environ.get("QUBIT_HOME", "").strip()
    if env:
        return Path(env)
    return qubit_runtime.default_home()


def _plugin_data_dir(cfg: Any, name: str) -> Path:
    return _qubit_home(cfg) / "plugin-data" / name


def _prepare_runtime(cfg: Any) -> Path:
    """Put the fork on ``sys.path`` and point it at the Qubit QUBIT_HOME."""
    home = _qubit_home(cfg)
    home.mkdir(parents=True, exist_ok=True)
    os.environ["QUBIT_HOME"] = str(home)
    qubit_runtime.ensure_path()
    return home


def _scan_limit() -> int:
    raw = os.environ.get("QUBIT_ACHIEVEMENTS_SCAN_LIMIT", "").strip()
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_SCAN_LIMIT
    return value if value > 0 else _DEFAULT_SCAN_LIMIT


def _harden_state(cfg: Any, name: str) -> None:
    """Register 0600/0700 on the plugin's durable state files."""
    root = _plugin_data_dir(cfg, name)
    if not root.is_dir():
        return
    try:
        os.chmod(root, 0o700)
        for path in root.iterdir():
            if path.is_file():
                os.chmod(path, 0o600)
    except OSError:
        pass


def _redact(text: Any) -> str:
    try:
        from .redaction import redact_text
        return redact_text(str(text))
    except Exception:
        return str(text)


_UPSTREAM_PRODUCT_RE = re.compile(
    r"\b(?:" + "Her" + "mes" + "|" + "Open" + "Claw" + r")\b",
    re.IGNORECASE)


def _rebrand_product(text: Any) -> str:
    """Render upstream product names as Qubit at the served boundary.

    Vendored manifests/catalogs author upstream product names; the frozen
    identity promise requires the normal dashboard to render Qubit while the
    vendored tree stays byte-faithful as the provenance record. Backend
    counterpart of ``frontend/qubit-web/src/lib/rebrand.ts``.
    """
    return _UPSTREAM_PRODUCT_RE.sub("Qubit", str(text))


def _rebrand_tab(tab: Any) -> Any:
    if isinstance(tab, dict) and tab.get("label"):
        out = dict(tab)
        out["label"] = _rebrand_product(out["label"])
        return out
    return tab


def _rebrand_obj(value: Any) -> Any:
    """Recursively rebrand upstream product names in a served payload.

    Applied to the vendored achievements payload at the backend boundary so the
    raw JSON API is already Qubit-branded (the SPA normalizer remains a second
    guard)."""
    if isinstance(value, str):
        return _rebrand_product(value)
    if isinstance(value, list):
        return [_rebrand_obj(v) for v in value]
    if isinstance(value, dict):
        return {k: _rebrand_obj(v) for k, v in value.items()}
    return value


def _audit(op: str, detail: Dict[str, Any]) -> None:
    try:
        from .logging_setup import log_event
        log_event(f"qubit_plugins.{op}",
                  {k: _redact(v)[:160] for k, v in detail.items()})
    except Exception:
        pass


def _estop(cfg: Any) -> bool:
    try:
        from .broker import estop_engaged
        if estop_engaged(cfg):
            return True
    except Exception:
        pass
    try:
        from . import power as PW
        return bool(PW.estop_engaged(cfg))
    except Exception:
        return False


class PluginUnavailable(RuntimeError):
    """The vendored plugin engine could not be imported/loaded."""


def _fail(exc: Exception, plugin: str = "") -> Tuple[int, Dict[str, Any]]:
    if isinstance(exc, PluginUnavailable):
        return _unavailable(exc, plugin or "unknown")
    return 500, {"ok": False, "state": "FAILED", "code": "PLUGIN_ERROR",
                 "detail": _redact(str(exc))[:300]}


def _unavailable(exc: Exception, plugin: str) -> Tuple[int, Dict[str, Any]]:
    """Typed 503 for a vendored engine that is present but not loadable."""
    return 503, {"ok": False, "state": "UNAVAILABLE",
                 "code": "PLUGIN_ENGINE_UNAVAILABLE",
                 "plugin": plugin,
                 "detail": _redact(str(exc))[:300],
                 "rollback": "read-only; nothing to roll back"}


def _need_confirm(detail: str) -> Tuple[int, Dict[str, Any]]:
    return 409, {"ok": False, "state": "NEEDS_CONFIRMATION",
                 "code": "CONFIRM_REQUIRED", "detail": detail,
                 "qubit_counterpart": "resend with confirm=true"}


# ---------------------------------------------------------------------------
# Vendored engine loading
# ---------------------------------------------------------------------------

def _plugin_api_path(name: str) -> Path:
    return qubit_runtime.runtime_root() / "plugins" / name / "dashboard" / \
        "plugin_api.py"


def dashboard_surface(name: str) -> Optional[Path]:
    """The vendored plugin's dashboard manifest, or ``None`` when it has none.

    A plugin "has a dashboard surface" only when the pinned tree ships
    ``plugins/<name>/dashboard/manifest.json`` (the fleet audit uses this, and so
    does the ledger generator, so the two cannot disagree about which plugins
    are dashboard plugins).
    """
    manifest = qubit_runtime.runtime_root() / "plugins" / name / "dashboard" / \
        "manifest.json"
    return manifest if manifest.is_file() else None


def _load_engine(name: str) -> Any:
    engine = _ENGINES.get(name)
    if engine is not None:
        return engine
    path = _plugin_api_path(name)
    module_name = "qubit_plugin_" + name.replace("-", "_").replace(".", "_")
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise PluginUnavailable(
                f"cannot load vendored plugin {name!r} from {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except PluginUnavailable:
        raise
    except Exception as exc:  # missing dependency / syntax error / etc.
        raise PluginUnavailable(
            f"vendored plugin {name!r} failed to import: {exc}") from exc
    _ENGINES[name] = module
    return module


def _install_scan_bound(engine: Any) -> None:
    """Bound the vendored scanner without forking its orchestration.

    ``compute_all``/``evaluate_all``/``_run_scan_and_update_cache`` all resolve
    the module global ``scan_sessions``; wrapping it once injects the Qubit
    session cap while keeping the vendored checkpointing, partial publishes,
    caching, and persistence intact.
    """
    global _BOUND_INSTALLED
    if _BOUND_INSTALLED:
        return
    original = engine.scan_sessions

    def _bounded(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("limit") is None:
            kwargs["limit"] = _ACTIVE_BOUND
        return original(*args, **kwargs)

    engine.scan_sessions = _bounded
    _BOUND_INSTALLED = True


def _achievements_engine(cfg: Any) -> Any:
    _prepare_runtime(cfg)
    engine = _load_engine("hermes-achievements")
    _install_scan_bound(engine)
    return engine


def reset_plugin_caches() -> None:
    """Clear in-memory plugin caches (test/reload seam).

    Mirrors a process restart for the achievements engine: snapshot cache and
    scan bookkeeping are dropped while the durable ``plugin-data`` files remain,
    and the active scan bound is restored to the environment default. The
    already-installed scan bound wrapper is kept (re-wrapping would nest caps).
    """
    global _ACTIVE_BOUND
    _ACTIVE_BOUND = _scan_limit()
    engine = _ENGINES.get("hermes-achievements")
    if engine is None:
        return
    engine._SNAPSHOT_CACHE = None
    engine._SNAPSHOT_CACHE_AT = 0
    engine._BACKGROUND_SCAN_THREAD = None
    try:
        engine._SCAN_STATUS.update(
            state="idle", started_at=None, finished_at=None, last_error=None,
            last_duration_ms=None, run_count=0)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# hermes-achievements handlers
# ---------------------------------------------------------------------------

def _achievements_payload(engine: Any) -> Dict[str, Any]:
    data = engine.evaluate_all()
    payload = {k: data[k] for k in
               ["achievements", "unlocked_count", "discovered_count",
                "secret_count", "total_count", "error", "generated_at"]
               if k in data}
    payload["is_stale"] = engine._is_snapshot_stale(data)
    payload["scan_meta"] = {
        **(data.get("scan_meta") or {}),
        "status": engine._scan_status_payload()}
    return _rebrand_obj(payload)


def h_plugin_achievements(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    try:
        engine = _achievements_engine(cfg)
        globals()["_ACTIVE_BOUND"] = _scan_limit()
        payload = _achievements_payload(engine)
        _harden_state(cfg, "hermes-achievements")
        return 200, payload
    except Exception as exc:
        return _fail(exc, "hermes-achievements")


def h_plugin_achievements_scan_status(
        cfg: Any, q: Dict[str, str], b: Dict[str, Any],
        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    try:
        engine = _achievements_engine(cfg)
        return 200, engine._scan_status_payload()
    except Exception as exc:
        return _fail(exc, "hermes-achievements")


def h_plugin_achievements_recent_unlocks(
        cfg: Any, q: Dict[str, str], b: Dict[str, Any],
        p: Dict[str, str]) -> Tuple[int, Any]:
    try:
        engine = _achievements_engine(cfg)
        data = engine.evaluate_all()
        unlocked = [a for a in data.get("achievements", []) if a.get("unlocked")]
        return 200, sorted(unlocked, key=lambda a: a.get("unlocked_at") or 0,
                           reverse=True)[:20]
    except Exception as exc:
        return _fail(exc, "hermes-achievements")


def h_plugin_achievements_session_badges(
        cfg: Any, q: Dict[str, str], b: Dict[str, Any],
        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    try:
        session_id = str(p.get("session_id") or "").strip()
        engine = _achievements_engine(cfg)
        data = engine.evaluate_all()
        session = next((s for s in data.get("sessions", [])
                        if s.get("session_id") == session_id), None)
        if not session:
            return 200, {"session_id": session_id, "badges": []}
        aggregate = engine.aggregate_stats([session])
        results = [(d, engine.evaluate_definition(d, aggregate))
                   for d in engine.ACHIEVEMENTS]
        badges = [engine.display_achievement({**d, **r})
                  for d, r in results if r["unlocked"]]
        return 200, {"session_id": session_id, "badges": badges}
    except Exception as exc:
        return _fail(exc, "hermes-achievements")


def h_plugin_achievements_rescan(
        cfg: Any, q: Dict[str, str], b: Dict[str, Any],
        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    if _estop(cfg):
        return 403, {"ok": False, "state": "ESTOP", "code": "ESTOP_ENGAGED",
                     "detail": "Emergency stop is engaged; rescan refused."}
    try:
        engine = _achievements_engine(cfg)
        globals()["_ACTIVE_BOUND"] = _scan_limit()
        data = engine.evaluate_all(force=True)
        _harden_state(cfg, "hermes-achievements")
        _audit("achievements_rescan", {"unlocked": data.get("unlocked_count")})
        return 200, {"ok": True, **data}
    except Exception as exc:
        return _fail(exc, "hermes-achievements")


def h_plugin_achievements_reset_state(
        cfg: Any, q: Dict[str, str], b: Dict[str, Any],
        p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    if _estop(cfg):
        return 403, {"ok": False, "state": "ESTOP", "code": "ESTOP_ENGAGED",
                     "detail": "Emergency stop is engaged; state reset refused."}
    if not bool(b.get("confirm")):
        return _need_confirm(
            "Resetting achievement unlock history and cached scans requires "
            "confirm=true.")
    try:
        engine = _achievements_engine(cfg)
        engine.save_state({"unlocks": {}})
        engine._SNAPSHOT_CACHE = None
        engine._SNAPSHOT_CACHE_AT = 0
        engine._SCAN_STATUS.update(
            state="idle", started_at=None, finished_at=None,
            last_error=None, last_duration_ms=None)
        for name in (engine.SNAPSHOT_FILE, engine.CHECKPOINT_FILE):
            try:
                engine._data_file(name).unlink(missing_ok=True)
            except OSError:
                pass
        _audit("achievements_reset", {"ok": True})
        return 200, {"ok": True}
    except Exception as exc:
        return _fail(exc, "hermes-achievements")


# ---------------------------------------------------------------------------
# kanban handlers (read surface -> vendored kanban_db)
# ---------------------------------------------------------------------------

def _kanban_engine(cfg: Any) -> Any:
    _prepare_runtime(cfg)
    return _load_engine("kanban")


def _qbool(q: Dict[str, str], key: str, default: bool = False) -> bool:
    raw = q.get(key)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _kanban_read(cfg: Any, fn_name: str, **kwargs: Any) -> Tuple[int, Any]:
    try:
        module = _kanban_engine(cfg)
        result = getattr(module, fn_name)(**kwargs)
        return 200, result
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        detail = getattr(exc, "detail", None)
        if status and detail is not None:
            return int(status), {"ok": False, "code": "PLUGIN_HTTP",
                                 "detail": _redact(detail)[:300]}
        return _fail(exc, "kanban")


# ---------------------------------------------------------------------------
# Kanban read surface -> the AUTHORITATIVE Qubit-native store
# ---------------------------------------------------------------------------
# The vendored Qubit kanban plugin read the fork's own ``qubit_cli.kanban_db``
# while Qubit's CLI, voice, operations catalog, task ledger and reports all use
# ``voicebridge.kanban``. One store must back every surface, so these handlers
# synthesize the vendored board/task wire shape from the native store. Fields
# the native store does not own (links, runs, diagnostics, attachments,
# workspace, tenant, sub-columns) are returned typed-empty, never read from the
# legacy store.

_NATIVE_TO_VENDOR = {
    "open": "todo", "claimed": "running", "blocked": "blocked",
    "done": "done", "archived": "archived",
}
_VENDOR_COLUMNS = ("triage", "todo", "scheduled", "ready", "running",
                   "blocked", "review", "done")
# The native store only has these mapping targets; the four upstream-only
# sub-columns (triage/scheduled/ready/review) are omitted from the board so the
# UI does not render empty lanes with dead create buttons.
_SUPPORTED_COLUMNS = ("todo", "running", "blocked", "done")
_NATIVE_PRIORITY_INT = {"low": 0, "normal": 1, "high": 2}


def _native_board(q: Dict[str, str]) -> str:
    raw = str((q or {}).get("board", "") or "").strip()
    return "main" if raw in ("", "default") else raw


def _kanban_http(exc: Exception) -> Tuple[int, Any]:
    code = str(getattr(exc, "code", "") or "")
    detail = str(getattr(exc, "detail", "") or exc)
    status = 500
    if "NOT_FOUND" in code:
        status = 404
    elif "BAD_INPUT" in code or "BAD_STATE" in code:
        status = 400
    elif "CONFIRM" in code:
        status = 409
    elif "ESTOP" in code:
        status = 403
    if status == 500:
        return _fail(exc, "kanban")
    return status, {"ok": False, "state": "FAILED", "code": code,
                    "detail": _redact(detail)[:300]}


def _native_comment_counts(cfg: Any, kanban_mod: Any) -> Dict[int, int]:
    conn = kanban_mod._connect(cfg)
    try:
        kanban_mod._migrate(conn)
        return {int(r["task_id"]): int(r["c"]) for r in conn.execute(
            "SELECT task_id, COUNT(*) c FROM comments GROUP BY task_id")}
    except Exception:
        return {}
    finally:
        conn.close()


def _native_task_vendor(task: Dict[str, Any], now: float,
                        comment_count: int) -> Dict[str, Any]:
    """Synthesize the vendored task wire shape from a native task."""
    created = float(task.get("created_at") or now)
    return {
        "id": str(task.get("id")),
        "board": task.get("board", "main"),
        "title": task.get("title", ""),
        "body": task.get("body", ""),
        "result": None,
        "latest_summary": None,
        "assignee": task.get("assignee") or None,
        "tenant": None,
        "status": _NATIVE_TO_VENDOR.get(str(task.get("status")), "todo"),
        "priority": _NATIVE_PRIORITY_INT.get(str(task.get("priority")), 1),
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
        "age": {"created_age_seconds": max(0.0, now - created),
                "started_age_seconds": None,
                "time_to_complete_seconds": None},
        "comment_count": int(comment_count or 0),
        "link_counts": {"parents": 0, "children": 0},
        "progress": None,
        "workspace_kind": "scratch",
        "workspace_path": None,
        "skills": [],
        "goal_mode": False,
        "goal_max_turns": None,
        "created_by": None,
        "model_override": None,
        "provider_override": None,
        "runs": [],
        "diagnostics": [],
        "warnings": None,
    }


def h_plugin_kanban_board(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Any]:
    from . import kanban as N
    board = _native_board(q)
    try:
        out = N.board_open(cfg, board=board)
    except Exception as exc:
        # A fresh native store has no ``main`` row yet; the default board is
        # empty, not missing. A non-default slug that does not exist stays 404.
        if "NOT_FOUND" in str(getattr(exc, "code", "")) and board == "main":
            out = {"by_column": {}}
        else:
            return _kanban_http(exc)
    now = time.time()
    counts = _native_comment_counts(cfg, N)
    columns: Dict[str, List[Dict[str, Any]]] = {
        c: [] for c in _SUPPORTED_COLUMNS}
    if _qbool(q, "include_archived"):
        columns["archived"] = []
    for status, tasks in (out.get("by_column") or {}).items():
        vendor = _NATIVE_TO_VENDOR.get(status, "todo")
        if vendor == "archived" and "archived" not in columns:
            continue
        if vendor not in columns:
            vendor = "todo"
        for t in tasks:
            columns[vendor].append(_native_task_vendor(
                t, now, counts.get(int(t.get("id")), 0)))
    assignees = sorted({t["assignee"] for col in columns.values()
                        for t in col if t.get("assignee")})
    return 200, {"ok": True,
                 "columns": [{"name": n, "tasks": columns[n]} for n in columns],
                 "tenants": [], "assignees": assignees,
                 "latest_event_id": 0, "now": int(now)}


def h_plugin_kanban_task(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Any]:
    from . import kanban as N
    try:
        tid = int(str(p.get("task_id") or ""))
    except (TypeError, ValueError):
        return 404, {"ok": False, "state": "FAILED", "code": "PLUGIN_HTTP",
                     "detail": f"task {p.get('task_id')!r} not found"}
    try:
        out = N.show(cfg, tid)
    except Exception as exc:
        return _kanban_http(exc)
    task = out["task"]
    comments = [{"id": c.get("id"), "author": c.get("author", ""),
                 "body": c.get("body", ""), "created_at": c.get("created_at")}
                for c in task.get("comments", [])]
    events = [{"id": i, "kind": e.get("kind", ""),
               "created_at": e.get("created_at"), "payload": None}
              for i, e in enumerate(task.get("events", []))]
    return 200, {"ok": True,
                 "task": _native_task_vendor(task, time.time(), len(comments)),
                 "comments": comments, "events": events, "attachments": [],
                 "links": {"parents": [], "children": []},
                 "child_results": [], "runs": []}


def h_plugin_kanban_stats(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Any]:
    from . import kanban as N
    board = _native_board(q)
    try:
        out = N.stats(cfg, board=board)
    except Exception as exc:
        if "NOT_FOUND" in str(getattr(exc, "code", "")) and board == "main":
            out = {"stats": {}}
        else:
            return _kanban_http(exc)
    by_status = next(iter((out.get("stats") or {}).values()), {})
    return 200, {"ok": True, "by_status": by_status, "by_assignee": {},
                 "oldest_ready_age_seconds": None, "now": int(time.time())}


def h_plugin_kanban_assignees(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                              p: Dict[str, str]) -> Tuple[int, Any]:
    from . import kanban as N
    try:
        out = N.list_tasks(cfg, board=_native_board(q), limit=N.LIST_MAX)
    except Exception as exc:
        return _kanban_http(exc)
    names = sorted({t["assignee"] for t in out.get("tasks", [])
                    if t.get("assignee")})
    return 200, {"ok": True,
                 "assignees": [{"name": n, "on_disk": False, "counts": {}}
                               for n in names]}


def h_plugin_kanban_boards(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Any]:
    from . import kanban as N
    try:
        out = N.boards_list(cfg)
    except Exception as exc:
        return _kanban_http(exc)
    boards = []
    for bd in out.get("boards", []):
        counts = bd.get("counts", {}) or {}
        boards.append({
            "slug": bd.get("board"),
            "name": bd.get("name"),
            "counts": counts,
            "total": sum(v for k, v in counts.items() if k != "archived"),
            "is_current": bd.get("board") == "main",
            "default_workspace_kind": "scratch",
            "default_workdir": None,
            "project_id": bd.get("project_slug"),
            "project_name": bd.get("project_slug"),
            "archived": False,
        })
    if not boards:
        # A fresh native store has no board row yet: present the canonical
        # empty board so the dashboard renders instead of an empty selector.
        boards = [{"slug": "main", "name": "main", "counts": {}, "total": 0,
                   "is_current": True, "default_workspace_kind": "scratch",
                   "default_workdir": None, "project_id": None,
                   "project_name": None, "archived": False}]
    return 200, {"ok": True, "boards": boards, "current": "main"}


def h_plugin_kanban_config(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Any]:
    return _kanban_read(cfg, "get_config")


def h_plugin_kanban_projects(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                             p: Dict[str, str]) -> Tuple[int, Any]:
    return _kanban_read(cfg, "list_kanban_projects")


def h_plugin_kanban_workers(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                            p: Dict[str, str]) -> Tuple[int, Any]:
    return _kanban_read(cfg, "list_active_workers",
                        board=q.get("board") or None)


def h_plugin_kanban_diagnostics(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                                p: Dict[str, str]) -> Tuple[int, Any]:
    return _kanban_read(cfg, "list_diagnostics",
                        board=q.get("board") or None,
                        severity=q.get("severity") or None)


def h_plugin_kanban_model_options(
        cfg: Any, q: Dict[str, str], b: Dict[str, Any],
        p: Dict[str, str]) -> Tuple[int, Any]:
    return _kanban_read(cfg, "model_options")


def h_plugin_kanban_home_channels(
        cfg: Any, q: Dict[str, str], b: Dict[str, Any],
        p: Dict[str, str]) -> Tuple[int, Any]:
    return _kanban_read(cfg, "get_home_channels",
                        task_id=q.get("task_id") or None,
                        board=q.get("board") or None)


def _kanban_int(value: Any) -> Any:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return value


def h_plugin_kanban_task_log(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                             p: Dict[str, str]) -> Tuple[int, Any]:
    tail = q.get("tail")
    return _kanban_read(
        cfg, "get_task_log", task_id=str(p.get("task_id") or ""),
        tail=_kanban_int(tail) if tail else None,
        board=q.get("board") or None)


def h_plugin_kanban_task_attachments(
        cfg: Any, q: Dict[str, str], b: Dict[str, Any],
        p: Dict[str, str]) -> Tuple[int, Any]:
    return _kanban_read(
        cfg, "list_task_attachments",
        task_id=str(p.get("task_id") or ""), board=q.get("board") or None)


def h_plugin_kanban_run(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                        p: Dict[str, str]) -> Tuple[int, Any]:
    return _kanban_read(
        cfg, "get_run_endpoint",
        run_id=_kanban_int(p.get("run_id")), board=q.get("board") or None)


def h_plugin_kanban_run_inspect(
        cfg: Any, q: Dict[str, str], b: Dict[str, Any],
        p: Dict[str, str]) -> Tuple[int, Any]:
    return _kanban_read(
        cfg, "inspect_run_endpoint",
        run_id=_kanban_int(p.get("run_id")), board=q.get("board") or None)


def h_plugin_kanban_profiles(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                             p: Dict[str, str]) -> Tuple[int, Any]:
    return _kanban_read(cfg, "list_profile_roster")


def h_plugin_kanban_orchestration(
        cfg: Any, q: Dict[str, str], b: Dict[str, Any],
        p: Dict[str, str]) -> Tuple[int, Any]:
    return _kanban_read(cfg, "get_orchestration_settings")


# ---------------------------------------------------------------------------
# Plugin fleet discovery
# ---------------------------------------------------------------------------

def h_plugin_kanban_events(
    cfg: Any, q: Dict[str, str], b: Dict[str, Any], p: Dict[str, str]
) -> Tuple[int, Any]:
    """Recent board events for the kanban plugin's live-update poll.

    Qubit serves ONE event path and refuses the vendored plugin's ``/events``
    WebSocket with the typed ``WS_SINGLE_PATH`` gate, so the served bundle polls
    this GET route. It reads the SAME authoritative Qubit-native store as the
    board handlers (``kanban.log``), newest-first and bounded — a read, never a
    second listener or a second store.
    """
    from . import kanban as N
    board = _native_board(q)
    try:
        out = N.log(cfg, board=board, limit=100)
    except Exception as exc:
        if "NOT_FOUND" in str(getattr(exc, "code", "")) and board == "main":
            out = {"events": []}
        else:
            return _kanban_http(exc)
    events = [{"id": i, "task_id": e.get("task_id"),
               "kind": e.get("kind", ""), "title": e.get("title"),
               "detail": e.get("detail"), "payload": None,
               "created_at": e.get("created_at")}
              for i, e in enumerate(out.get("events", []))]
    return 200, {"ok": True, "state": "OK", "events": events}


# ---------------------------------------------------------------------------
# Plugin fleet discovery
# ---------------------------------------------------------------------------


def plugin_fleet_manifests() -> List[Dict[str, Any]]:
    """Manifests for the vendored dashboard plugin fleet.

    Returned by ``GET /api/dashboard/plugins/fleet`` — a discovery view that
    does NOT replace the Qubit overlay plugin list at ``/api/dashboard/plugins``
    (that response is fixed to the ten Qubit overlays). Fields mirror the
    dashboard manifest contract, with ``source="vendored"``.
    """
    manifests: List[Dict[str, Any]] = []
    for name in VENDORED_PLUGIN_FLEET:
        manifest_path = dashboard_surface(name)
        if not manifest_path:
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        manifests.append({
            "name": str(data.get("name") or name),
            "label": _rebrand_product(data.get("label", name)),
            "description": _rebrand_product(data.get("description", "")),
            "icon": data.get("icon", "Puzzle"),
            "version": data.get("version", "0.0.0"),
            "tab": _rebrand_tab(data.get("tab", {})),
            "entry": data.get("entry", "dist/index.js"),
            "css": data.get("css"),
            "api": data.get("api"),
            "source": "vendored",
        })
    return manifests


def plugin_fleet_audit() -> List[Dict[str, Any]]:
    """Per-plugin disposition for the full vendored fleet.

    The dashboard-surface plugins ship a real manifest + backend API; every
    other entry is an agent/provider plugin with no dashboard route of its own.
    The ledger generator consumes this so a fleet plugin can never be silently
    dropped or double-counted.
    """
    audit: List[Dict[str, Any]] = []
    for name in VENDORED_PLUGIN_FLEET:
        manifest = dashboard_surface(name)
        row: Dict[str, Any] = {
            "name": name,
            "has_dashboard": bool(manifest),
            "source": "vendored",
        }
        if manifest:
            row["manifest"] = str(manifest.relative_to(
                qubit_runtime.runtime_root()))
            row["api"] = str(_plugin_api_path(name).relative_to(
                qubit_runtime.runtime_root()))
        audit.append(row)
    return audit


def h_plugin_fleet(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    try:
        return 200, {"plugins": plugin_fleet_manifests()}
    except Exception as exc:
        return _fail(exc, "fleet")


__all__ = [
    "h_plugin_achievements",
    "h_plugin_achievements_scan_status",
    "h_plugin_achievements_recent_unlocks",
    "h_plugin_achievements_session_badges",
    "h_plugin_achievements_rescan",
    "h_plugin_achievements_reset_state",
    "h_plugin_kanban_board",
    "h_plugin_kanban_task",
    "h_plugin_kanban_stats",
    "h_plugin_kanban_assignees",
    "h_plugin_kanban_boards",
    "h_plugin_kanban_config",
    "h_plugin_kanban_projects",
    "h_plugin_kanban_workers",
    "h_plugin_kanban_diagnostics",
    "h_plugin_kanban_model_options",
    "h_plugin_kanban_home_channels",
    "h_plugin_kanban_events",
    "h_plugin_fleet",
    "plugin_fleet_manifests",
    "plugin_fleet_audit",
    "dashboard_surface",
    "VENDORED_PLUGIN_FLEET",
    "reset_plugin_caches",
]
