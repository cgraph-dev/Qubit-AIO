"""HUD plugins collector — Qubit-native port of ``backend/collectors/plugins.py``.

Vendored from ``joeynyc/hermes-hudui`` @ be83208f48b7a1c67b7816837c8df71a61ef7edf
Copyright (c) 2026 Hermes HUD Contributors. MIT licensed; the file-by-file
record is ``PROVENANCE.json`` beside this package. Rebranded and re-pointed at
QUBIT state.

What changed from upstream, and why
-----------------------------------
* **Runtime status** — upstream decided a plugin was ``enabled``/``disabled`` by
  reading an ``enabled:`` key out of the plugin's own ``plugin.yaml``. Qubit
  gates plugins through ``plugins.enabled`` / ``plugins.disabled`` in its
  config, and its ``PluginManager`` already resolves that gate (including
  bundled opt-ins, kind/backends and admission errors). This collector reports
  the manager's verdict, so the tab cannot claim a plugin is enabled when the
  loader skipped it (or vice versa).
* **Sources** — upstream scanned ``<home>/plugins`` (user), a bundled dir it
  *guessed* by walking its own parents, and an untrusted project plugins dir
  gated on an env var. Qubit's plugin loader is the authority for the source
  set (bundled, user, entry-point); :func:`_discover_all_plugins` adds the
  on-disk directory per plugin so the panel can open it.
* **Dashboard capability** — upstream read ``<plugin>/dashboard/manifest.json``
  itself. Here the dashboard half comes from Qubit's own dashboard discovery
  (``qubit_cli.web_server_dashboard``) plus the frontend overlay manifests that
  Qubit serves as ``GET /api/dashboard/plugins``, so "has a tab" means what the
  dashboard actually mounts.
* **Mutations dropped** — upstream's ``install_plugin``/``update_plugin`` shelled
  ``git`` into the plugins dir and ``set_plugin_enabled`` rewrote a plugin's
  YAML manifest. Neither is a read; both belong to Qubit's plugin CLI
  (``qubit plugins install/enable/disable``), which owns admission and the
  env/config transaction. A HUD tab must not be a second writer of plugin
  state, so this module is read-only and those functions are not ported.
* **Descriptions** — served through :func:`voicebridge.hud.branding.rebrand_text`
  at this boundary, so an upstream vendor name in a manifest description never
  reaches the panel (the on-disk manifest stays byte-faithful as provenance).

Pure function, JSON-ready dict, never raises.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .branding import rebrand_text

#: Plugin names that come from the frontend overlay (``frontend/qubit-app/plugins``)
#: rather than an on-disk plugin directory.
_OVERLAY_SOURCE = "qubit-overlay"


def _ensure_vendored() -> None:
    """Put the vendored tree on ``sys.path`` so Qubit's plugin loader imports."""
    from .. import qubit_runtime

    qubit_runtime.ensure_path()


def _directory_map() -> Dict[str, str]:
    """``name`` -> plugin directory, from Qubit's own plugin discovery.

    :func:`qubit_cli.plugins_cmd._discover_all_plugins` is the CLI's listing
    authority: it returns what the loader sees, with the directory each plugin
    was found in. A loader without that helper yields an empty map, which only
    costs the panel a ``path`` field — it never changes the plugin list.
    """
    try:
        from qubit_cli.plugins_cmd import _discover_all_plugins  # type: ignore
    except Exception:  # noqa: BLE001
        return {}
    try:
        rows = _discover_all_plugins() or []
    except Exception:  # noqa: BLE001
        return {}
    out: Dict[str, str] = {}
    for row in rows:
        try:
            _name, _version, _desc, _source, dir_path, key = row[:6]
        except (TypeError, ValueError):
            continue
        for alias in (str(key or ""), str(_name or "")):
            if alias and dir_path:
                out.setdefault(alias, str(dir_path))
    return out


def _dashboard_by_name() -> Dict[str, Dict[str, Any]]:
    """Dashboard-half metadata for every mounted plugin tab, keyed by name."""
    try:
        from qubit_cli.web_server_dashboard import (  # type: ignore
            _discover_dashboard_plugins, _strip_dashboard_manifest)
    except Exception:  # noqa: BLE001
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    try:
        for row in _discover_dashboard_plugins() or []:
            clean = _strip_dashboard_manifest(dict(row))
            name = str(clean.get("name") or "")
            if name:
                out[name] = clean
    except Exception:  # noqa: BLE001
        return out
    return out


def _overlay_rows() -> List[Dict[str, Any]]:
    """Frontend overlay plugin manifests (the Qubit-owned dashboard plugins).

    Read straight from ``frontend/qubit-app/plugins/*/manifest.json`` — the
    same directory the dashboard serves as ``GET /api/dashboard/plugins``.
    """
    try:
        from .. import resources  # type: ignore

        root = Path(resources.resource_path("frontend/qubit-app")) / "plugins"
    except Exception:  # noqa: BLE001
        return []
    rows: List[Dict[str, Any]] = []
    if not root.is_dir():
        return rows
    for manifest in sorted(root.glob("*/manifest.json")):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            rows.append(data)
    return rows


def _overlay_plugin(data: Dict[str, Any]) -> Dict[str, Any]:
    raw_tab = data.get("tab") if isinstance(data.get("tab"), dict) else {}
    name = str(data.get("name") or "")
    return {
        "name": name,
        "label": rebrand_text(data.get("label") or name),
        "description": rebrand_text(data.get("description") or ""),
        "version": str(data.get("version") or ""),
        # An overlay plugin ships with the app and is always mounted; it has no
        # plugins.enabled gate of its own.
        "source": _OVERLAY_SOURCE,
        "kind": "dashboard",
        "runtime_status": "enabled",
        "enabled": True,
        "error": None,
        "path": "",
        "has_dashboard_manifest": True,
        "has_api": bool(data.get("has_api") or data.get("api")),
        "hidden": bool(raw_tab.get("hidden")),
        "entry": str(data.get("entry") or ""),
        "css": data.get("css") if isinstance(data.get("css"), str) else None,
        "icon": str(data.get("icon") or "Puzzle"),
        "tab_path": str(raw_tab.get("path") or f"/{name}"),
        "tab_position": str(raw_tab.get("position") or "end"),
        "slots": [s for s in (data.get("slots") or []) if isinstance(s, str) and s],
        "provides_tools": [],
        "counts": {"tools": 0, "hooks": 0, "middleware": 0, "commands": 0},
    }


def _agent_plugin(row: Dict[str, Any], dirs: Dict[str, str],
                  dashboard: Dict[str, Any]) -> Dict[str, Any]:
    name = str(row.get("name") or "")
    key = str(row.get("key") or name)
    raw_tab = dashboard.get("tab")
    tab: Dict[str, Any] = raw_tab if isinstance(raw_tab, dict) else {}
    enabled = bool(row.get("enabled"))
    error = row.get("error")
    return {
        "name": name,
        "key": key,
        "label": rebrand_text(dashboard.get("label") or name),
        "description": rebrand_text(row.get("description")
                                    or dashboard.get("description") or ""),
        "version": str(row.get("version") or ""),
        "source": str(row.get("source") or ""),
        "kind": str(row.get("kind") or ""),
        # The loader's own verdict: enabled with an error means it was admitted
        # but failed to load, which is neither "enabled" nor "disabled".
        "runtime_status": ("error" if error else
                           ("enabled" if enabled else "disabled")),
        "enabled": enabled,
        "error": error,
        "path": dirs.get(key) or dirs.get(name) or "",
        "has_dashboard_manifest": bool(dashboard),
        "has_api": bool(dashboard.get("has_api")),
        "hidden": bool(tab.get("hidden")),
        "entry": str(dashboard.get("entry") or ""),
        "css": dashboard.get("css") if isinstance(dashboard.get("css"), str) else None,
        "icon": str(dashboard.get("icon") or "Puzzle"),
        "tab_path": str(tab.get("path") or f"/{name}"),
        "tab_position": str(tab.get("position") or "end"),
        "slots": list(dashboard.get("slots") or []),
        "provides_tools": [],
        "counts": {
            "tools": int(row.get("tools") or 0),
            "hooks": int(row.get("hooks") or 0),
            "middleware": int(row.get("middleware") or 0),
            "commands": int(row.get("commands") or 0),
        },
    }


def collect_plugins(home: Optional[Path] = None, cfg: Any = None,
                    *, include_overlay: bool = True) -> Dict[str, Any]:
    """Discover installed plugins from Qubit's own plugin sources.

    ``cfg`` is the VoiceBridge config (published into ``QUBIT_HOME`` by the
    vendored layer, so a profile lists its own plugins). Discovery is
    idempotent and joins an in-flight scan, so repeated HUD refreshes do not
    re-scan in parallel.
    """
    out: Dict[str, Any] = {
        "available": False,
        "detail": "",
        "plugins": [],
        "total": 0,
        "dashboard_count": 0,
        "agent_count": 0,
        "hidden_count": 0,
        "error_count": 0,
        "by_source": {},
        "by_kind": {},
    }
    try:
        _ensure_vendored()
        from .. import qubit_runtime

        qubit_runtime.use_qubit_home(cfg)
        from qubit_cli.plugins import get_plugin_manager  # type: ignore

        manager = get_plugin_manager()
        manager.discover_and_load()
        rows = manager.list_plugins() or []
    except Exception as exc:  # noqa: BLE001 - an unreadable loader is a report
        out["detail"] = f"plugin loader unavailable ({exc})"
        return out

    dirs = _directory_map()
    dashboard = _dashboard_by_name()
    seen: set = set()
    plugins: List[Dict[str, Any]] = []
    for row in rows:
        name = str(row.get("name") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        plugins.append(_agent_plugin(row, dirs, dashboard.get(name) or {}))

    if include_overlay:
        for data in _overlay_rows():
            name = str(data.get("name") or "")
            if not name or name in seen:
                continue
            seen.add(name)
            plugins.append(_overlay_plugin(data))

    by_source: Dict[str, int] = {}
    by_kind: Dict[str, int] = {}
    for plugin in plugins:
        by_source[plugin["source"]] = by_source.get(plugin["source"], 0) + 1
        by_kind[plugin["kind"]] = by_kind.get(plugin["kind"], 0) + 1

    out.update(
        available=True,
        detail=f"{len(plugins)} plugin(s) discovered",
        plugins=plugins,
        total=len(plugins),
        dashboard_count=sum(1 for p in plugins if p["has_dashboard_manifest"]),
        agent_count=sum(1 for p in plugins
                        if p["runtime_status"] == "enabled"),
        hidden_count=sum(1 for p in plugins if p["hidden"]),
        error_count=sum(1 for p in plugins if p["runtime_status"] == "error"),
        by_source=by_source,
        by_kind=by_kind,
    )
    return out


__all__ = ["collect_plugins"]
