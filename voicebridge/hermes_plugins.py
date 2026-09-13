"""Qubit-owned backend for the vendored Hermes dashboard plugin fleet.

The pinned Hermes tree vendors ``plugins/hermes-achievements`` and
``plugins/kanban`` with their real dashboard APIs and tests, but before this
module they had no Qubit backend route or ledger row. This adapter exposes
those plugin APIs through :mod:`voicebridge.hermes_compat` while keeping the
single-listener / single-policy contract:

* handlers run in-process on the existing Qubit web server (no second server);
* achievement scans reuse the vendored engine + ``hermes_state.SessionDB`` and
  read the REAL local session history (never fabricated badges);
* the snapshot/checkpoint/unlock state persists under the Qubit state dir
  (``<state>/hermes/plugin-data/<plugin>/``), hardened to 0600;
* kanban reads delegate to the same ``hermes_cli.kanban_db`` the vendored
  dashboard plugin (and the fork CLI/gateway ``/kanban``) use; kanban writes
  stay on the shared typed policy/confirmation path.

Every handler returns the ``(status, payload)`` tuple the compat dispatcher
expects, and typed error envelopes on failure.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import hermes_runtime

# Hard cap on sessions analyzed by a single scan. The vendored engine defaults
# to the full history (SQLite ``LIMIT -1``); Qubit keeps that bounded so a
# damaged/oversized history can never turn one dashboard request into an
# unbounded operation. Overridable via ``QUBIT_ACHIEVEMENTS_SCAN_LIMIT``.
_DEFAULT_SCAN_LIMIT = 20000

_ENGINES: Dict[str, Any] = {}
_ACTIVE_BOUND = _DEFAULT_SCAN_LIMIT
_BOUND_INSTALLED = False

# The complete vendored Hermes plugin fleet, in the order the direct source
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

def _hermes_home(cfg: Any) -> Path:
    """Qubit-owned Hermes root: the live ``HERMES_HOME`` when the fork set one,
    else ``<Qubit state dir>/hermes`` (matching ``hermes_asgi``'s default)."""
    env = os.environ.get("HERMES_HOME", "").strip()
    if env:
        return Path(env)
    try:
        base = Path(cfg.state_dir())
    except Exception:
        base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home()))) / \
            "qubit-voicebridge"
    return base / "hermes"


def _plugin_data_dir(cfg: Any, name: str) -> Path:
    return _hermes_home(cfg) / "plugin-data" / name


def _prepare_runtime(cfg: Any) -> Path:
    """Put the fork on ``sys.path`` and point it at the Qubit HERMES_HOME."""
    home = _hermes_home(cfg)
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HERMES_HOME"] = str(home)
    hermes_runtime.ensure_path()
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


def _audit(op: str, detail: Dict[str, Any]) -> None:
    try:
        from .logging_setup import log_event
        log_event(f"hermes_plugins.{op}",
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
    return hermes_runtime.runtime_root() / "plugins" / name / "dashboard" / \
        "plugin_api.py"


def dashboard_surface(name: str) -> Optional[Path]:
    """The vendored plugin's dashboard manifest, or ``None`` when it has none.

    A plugin "has a dashboard surface" only when the pinned tree ships
    ``plugins/<name>/dashboard/manifest.json`` (the fleet audit uses this, and so
    does the ledger generator, so the two cannot disagree about which plugins
    are dashboard plugins).
    """
    manifest = hermes_runtime.runtime_root() / "plugins" / name / "dashboard" / \
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
    return payload


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


def h_plugin_kanban_board(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Any]:
    return _kanban_read(
        cfg, "get_board", tenant=q.get("tenant"),
        include_archived=_qbool(q, "include_archived"),
        board=q.get("board") or None,
        workflow_template_id=q.get("workflow_template_id") or None,
        current_step_key=q.get("current_step_key") or None)


def h_plugin_kanban_task(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                         p: Dict[str, str]) -> Tuple[int, Any]:
    return _kanban_read(
        cfg, "get_task", task_id=str(p.get("task_id") or ""),
        board=q.get("board") or None,
        run_state_type=q.get("run_state_type") or None,
        run_state_name=q.get("run_state_name") or None)


def h_plugin_kanban_stats(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                          p: Dict[str, str]) -> Tuple[int, Any]:
    return _kanban_read(cfg, "get_stats", board=q.get("board") or None)


def h_plugin_kanban_assignees(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                              p: Dict[str, str]) -> Tuple[int, Any]:
    return _kanban_read(cfg, "get_assignees", board=q.get("board") or None)


def h_plugin_kanban_boards(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> Tuple[int, Any]:
    return _kanban_read(cfg, "list_boards",
                        include_archived=_qbool(q, "include_archived"))


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
            "label": data.get("label", name),
            "description": data.get("description", ""),
            "icon": data.get("icon", "Puzzle"),
            "version": data.get("version", "0.0.0"),
            "tab": data.get("tab", {}),
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
                hermes_runtime.runtime_root()))
            row["api"] = str(_plugin_api_path(name).relative_to(
                hermes_runtime.runtime_root()))
        audit.append(row)
    return audit


def h_plugin_fleet(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                   p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    try:
        return 200, {"plugins": plugin_fleet_manifests()}
    except Exception as exc:
        return _fail(exc, "fleet")


__all__ = [
    "h_plugin_achievements", "h_plugin_achievements_scan_status",
    "h_plugin_achievements_recent_unlocks",
    "h_plugin_achievements_session_badges", "h_plugin_achievements_rescan",
    "h_plugin_achievements_reset_state",
    "h_plugin_kanban_board", "h_plugin_kanban_task", "h_plugin_kanban_stats",
    "h_plugin_kanban_assignees", "h_plugin_kanban_boards",
    "h_plugin_kanban_config", "h_plugin_kanban_projects",
    "h_plugin_kanban_workers", "h_plugin_kanban_diagnostics",
    "h_plugin_kanban_model_options", "h_plugin_kanban_home_channels",
    "h_plugin_fleet", "plugin_fleet_manifests", "plugin_fleet_audit",
    "dashboard_surface", "VENDORED_PLUGIN_FLEET", "reset_plugin_caches",
]
