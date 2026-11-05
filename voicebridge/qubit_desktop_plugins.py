"""Read-only desktop-plugin surface for the Qubit dashboard listener.

Native Desktop reads ``<QUBIT_HOME>/desktop-plugins/<name>/plugin.js`` from its
own disk. The vendored runtime exposes that tree over HTTP for its Webapp
surface (``qubit_cli/web_routers/desktop_plugins.py``), but the Qubit listener
serves the ``dashboard`` surface, whose surface policy deliberately keeps those
routes off (``web_server_surface.POLICIES["dashboard"].desktop_plugins`` is
``False``). The proxy therefore answers 404 and the browser's plugin loader has
no door.

These compat handlers close only the READ half. They call the vendored module's
own audited helpers (``_inside_root``/``_list``/``_read``) instead of
reimplementing containment: one root, the same two-component limit, the same
sensitive-path guard, the same symlink rules and the same 16 MiB cap. No native
or remote capability is fabricated.

``GET /api/desktop-plugins/root`` intentionally does NOT run the Webapp's
reconcile pass (which copies unified packages' desktop halves into the root):
no plugin content is written. Resolving the root may create the empty app-level
directory, exactly as the canonical resolver documents. Reconcile stays a typed
refusal naming the two real owners (native Desktop and the Webapp surface).
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

from .qubit_plugins import _prepare_runtime, _redact

HandlerResult = Tuple[int, Dict[str, Any]]


def _modules(cfg: Any) -> Tuple[Any, Any]:
    """The vendored root resolver and its audited read routes.

    ``_prepare_runtime`` points ``QUBIT_HOME`` at the serving runtime home and
    puts the vendored tree on ``sys.path`` before importing, exactly as the
    kanban/achievements compat backends do.
    """
    _prepare_runtime(cfg)
    from qubit_cli import desktop_plugin_install as install
    from qubit_cli.web_routers import desktop_plugins as routes

    return install, routes


def _error(exc: Exception) -> HandlerResult:
    """Map the vendored handler's own refusal to a typed compat envelope."""
    status = getattr(exc, "status_code", None)
    detail = getattr(exc, "detail", None)
    if status is not None and detail is not None:
        code = {
            400: "DESKTOP_PLUGINS_BAD_PATH",
            404: "DESKTOP_PLUGINS_NOT_FOUND",
            413: "DESKTOP_PLUGINS_TOO_LARGE",
        }.get(int(status), "DESKTOP_PLUGINS_REFUSED")
        return int(status), {
            "ok": False,
            "state": "REFUSED",
            "code": code,
            "detail": _redact(detail)[:300],
            "qubit_counterpart": (
                "native Desktop plugin loader or the Webapp surface's "
                "/api/desktop-plugins/* routes"),
        }
    return 503, {
        "ok": False,
        "state": "UNAVAILABLE",
        "code": "DESKTOP_PLUGINS_UNAVAILABLE",
        "detail": "the desktop-plugin tree is not readable in this runtime: "
                  + _redact(exc)[:200],
        "qubit_counterpart": (
            "native Desktop plugin loader (reads <QUBIT_HOME>/desktop-plugins "
            "from disk)"),
    }


def _path(q: Dict[str, str]) -> str:
    return str((q or {}).get("path") or "").strip()


def h_desktop_plugins_root(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> HandlerResult:
    """GET /api/desktop-plugins/root -> the app-level ``desktop-plugins`` root.

    Same path the vendored loader and Electron resolve. The vendored ``/root``
    route also reconciles unified package halves first; this read-only surface
    does not write, so a half is visible only after native Desktop (or the
    Webapp surface) has reconciled it.
    """
    try:
        install, _routes = _modules(cfg)
        return 200, {"root": str(install.desktop_plugins_root())}
    except Exception as exc:  # noqa: BLE001 - typed refusal, never a 500
        return _error(exc)


def h_desktop_plugins_list(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> HandlerResult:
    """GET /api/desktop-plugins/list?path=... -> one folder's entries."""
    raw = _path(q)
    if not raw:
        return 400, {
            "ok": False,
            "state": "REFUSED",
            "code": "DESKTOP_PLUGINS_BAD_PATH",
            "detail": "the 'path' query parameter is required",
            "qubit_counterpart": (
                "native Desktop plugin loader or the Webapp surface's "
                "/api/desktop-plugins/list"),
        }
    try:
        _install, routes = _modules(cfg)
        return 200, routes._list(raw)
    except Exception as exc:  # noqa: BLE001 - typed refusal, never a 500
        return _error(exc)


def h_desktop_plugins_read(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                           p: Dict[str, str]) -> HandlerResult:
    """GET /api/desktop-plugins/read?path=... -> one plugin.js / marker file."""
    raw = _path(q)
    if not raw:
        return 400, {
            "ok": False,
            "state": "REFUSED",
            "code": "DESKTOP_PLUGINS_BAD_PATH",
            "detail": "the 'path' query parameter is required",
            "qubit_counterpart": (
                "native Desktop plugin loader or the Webapp surface's "
                "/api/desktop-plugins/read"),
        }
    try:
        _install, routes = _modules(cfg)
        return 200, routes._read(raw)
    except Exception as exc:  # noqa: BLE001 - typed refusal, never a 500
        return _error(exc)
