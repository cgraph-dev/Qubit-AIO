"""Qubit-owned Hermes backend launcher.

Step 1 of the owner directive: the actual vendored Hermes web backend (the
FastAPI/uvicorn server assembled by ``qubit_hermes/hermes_cli/web_server.py``)
must run from the Qubit tree — not as an external supervised process and not
only as a CLI passthrough.

This module is the single programmatic entry point:

* :func:`app` returns the vendored FastAPI application object.
* :func:`status` is a read-only probe (importable, title, route count, pin).
* :func:`serve` runs the real server (headless backend or browser dashboard).

``qubit agent serve`` / ``qubit agent dashboard`` already dispatch to the same
server through :mod:`voicebridge.hermes_runtime`; this module exists so the
Qubit service, dashboard, and tests can start it in-process as the runtime
backend.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict

from . import hermes_runtime

_REPO_ROOT = Path(__file__).resolve().parents[1]


def app():
    """Return the vendored Hermes FastAPI application (imported from the fork).

    The fork root is left on ``sys.path`` for the lifetime of the process: the
    server lazily imports its routers/modules while serving.
    """
    hermes_runtime.ensure_path()
    from hermes_cli import web_server  # type: ignore

    return web_server.app


def _web_server_module():
    hermes_runtime.ensure_path()
    from hermes_cli import web_server  # type: ignore

    return web_server


def status() -> Dict[str, Any]:
    """Read-only probe of the vendored backend (never starts a server)."""
    root = hermes_runtime.runtime_root()
    out: Dict[str, Any] = {
        "runtime": "qubit-hermes-backend",
        "root": str(root),
        "present": root.is_dir(),
        "importable": False,
        "title": None,
        "routes": 0,
        "upstream_revision": None,
    }
    if not root.is_dir():
        return out
    saved_path = list(sys.path)
    saved_env = dict(os.environ)
    try:
        ws = _web_server_module()
        out["importable"] = True
        out["title"] = getattr(ws.app, "title", None)
        paths = {getattr(r, "path", None) for r in ws.app.routes}
        out["routes"] = len([p for p in paths if p])
        out["start_server"] = callable(getattr(ws, "start_server", None))
    except Exception as exc:  # pragma: no cover - degraded env only
        out["import_error"] = f"{type(exc).__name__}: {exc}"[:200]
    finally:
        sys.path[:] = saved_path
        os.environ.clear()
        os.environ.update(saved_env)
    mf = hermes_runtime.manifest_path()
    if mf.is_file():
        try:
            import json
            out["upstream_revision"] = json.loads(
                mf.read_text(encoding="utf-8")).get("upstream_revision")
        except Exception:
            pass
    return out


def serve(host: str = "127.0.0.1", port: int = 9119, *,
          headless: bool = True, open_browser: bool = False,
          allow_public: bool = False, initial_profile: str = "") -> None:
    """Run the vendored Hermes backend server in this process.

    ``headless=True`` is the JSON-RPC/WS backend (no SPA); ``headless=False``
    is the full browser dashboard. Blocks until the server stops.
    """
    ws = _web_server_module()
    ws.start_server(host=host, port=int(port), open_browser=bool(open_browser),
                    allow_public=bool(allow_public),
                    initial_profile=initial_profile, headless=bool(headless))


__all__ = ["app", "status", "serve"]
