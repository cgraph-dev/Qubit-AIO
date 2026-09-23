"""Qubit dashboard plugin catalog — the real, loadable plugin set.

Serves ``GET /api/dashboard/plugins/catalog`` and
``GET /api/dashboard/plugins/catalog/<id>``.

The upstream reference serves a curated index of GitHub-pinned repositories
(``hermes_cli/plugin_catalog.py`` + ``plugins_cmd_catalog.py``). Qubit policy
refuses remote plugin acquisition (``BLOCKED_REMOTE_PLUGIN``), so that index is
never fetched and is exposed only as a typed, operator-gated refusal that names
the prerequisite. What Qubit CAN load and manage is served instead, with real
provenance for every row:

* ``bundled``   — the vendored agent plugin fleet (``qubit_runtime/plugins``)
* ``dashboard`` — vendored dashboard plugins with a real manifest + backend
* ``overlay``   — Qubit dashboard extensions (``frontend/qubit-app/plugins``)
* ``builtin`` / ``registered`` / ``reviewed`` — the Qubit plugin-runtime store
* ``user``      — plugins installed under ``QUBIT_HOME/plugins``

Every row has a real on-disk manifest, a real loader, and a real registry
entry; rows that cannot load are not listed. No plugin code is imported or
executed while building the catalog.
"""
from __future__ import annotations

import datetime
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import resources as _resources

CATALOG_SCHEMA = "qubit-dashboard-plugin-catalog/1"
REMOTE_CODE = "BLOCKED_REMOTE_PLUGIN"
REMOTE_DETAIL = (
    "raw, unvetted remote fetch is refused by Qubit pinned-source policy: no "
    "arbitrary repository is fetched, cloned, or executed. The curated, pinned "
    "index is served separately under ``pinned_index``; an entry install is "
    "confirm/audit/e-stop gated and operator-verified at the pinned ref.")
REMOTE_PREREQUISITE = (
    "operator-reviewed local install: `qubit agent plugins install <dir|*.json>`; "
    "raw remote Git/GitHub install requires an owner policy amendment plus a "
    "hash-pinned allowlist")
OVERLAY_DIR = _resources.resource_path("frontend/qubit-app")

# ── Curated, pinned remote plugin index (Owner Decision #1, 2026-09-23) ───────
# A curated subset of the upstream pinned plugin index, ported from the
# vendored source ``qubit_runtime/qubit_cli/data/plugin_index.json``. Every
# entry is a real remote source pinned to a full 40-character commit SHA; no
# entry is fabricated. Raw unvetted remote fetch stays refused; an entry
# install is pinned/verified and routed through the canonical e-stop ->
# confirmation -> audit path before any repository could be fetched.
INDEX_SCHEMA = "qubit-pinned-plugin-index/1"
INDEX_CODE = "PLUGIN_INDEX_OPERATOR_GATED"
INDEX_NOT_FOUND_CODE = "PLUGIN_INDEX_NOT_FOUND"
INDEX_REF_MISMATCH_CODE = "PLUGIN_INDEX_REF_MISMATCH"
INDEX_PATH = Path(__file__).resolve().parent / "data" / "plugin_index_pinned.json"
INDEX_INSTALL_DETAIL = (
    "this entry is curated and pinned, but its remote source is not fetched by "
    "Qubit. Installing it requires an operator to verify the repository and "
    "license at the pinned ref, then install the verified source through the "
    "canonical local install path. Raw remote fetch remains refused.")
INDEX_INSTALL_PREREQUISITE = (
    "operator-verified pinned install: verify `repo`@`pinned_ref` (and its "
    "license), fetch it out-of-band, then `qubit agent plugins install "
    "<verified-dir>`; a direct Qubit remote fetch requires a hash-pinned "
    "allowlist")
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")


def _redact(text: Any) -> str:
    try:
        from .redaction import redact_text
        return redact_text(str(text))
    except Exception:
        return str(text)


def _bounded(text: Any, limit: int) -> str:
    return _redact(text)[:limit]


def _now_iso() -> str:
    return (datetime.datetime.now(datetime.timezone.utc)
            .isoformat(timespec="seconds").replace("+00:00", "Z"))


def _str_list(value: Any) -> List[str]:
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if isinstance(v, (str, int, float))]
    return []


def _prepare_runtime(cfg: Any) -> None:
    """Put the vendored fork on ``sys.path`` and point it at ``QUBIT_HOME``."""
    from . import qubit_plugins as _QP

    _QP._prepare_runtime(cfg)


def _state_base(cfg: Any) -> Path:
    try:
        return Path(cfg.state_dir())
    except Exception:
        return Path(".")


def _hidden_set(cfg: Any) -> set:
    """Per-user hidden plugin names (canonical store, then the legacy name)."""
    base = _state_base(cfg)
    for name in ("qubit_hidden.json", "hermes_hidden.json"):
        try:
            raw = json.loads((base / name).read_text())
            return set(raw.get("hidden", []) or [])
        except (OSError, ValueError):
            continue
    return set()


def _overlay_manifests() -> List[Dict[str, Any]]:
    manifests: List[Dict[str, Any]] = []
    plugdir = OVERLAY_DIR / "plugins"
    if plugdir.exists():
        for mf in sorted(plugdir.glob("*/manifest.json")):
            try:
                manifests.append(json.loads(mf.read_text()))
            except (OSError, ValueError):
                continue
    return manifests


def _read_dashboard_manifest(dir_path: Path) -> Optional[Dict[str, Any]]:
    path = Path(dir_path) / "dashboard" / "manifest.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _manifest_file(dir_path: Path) -> Optional[Path]:
    for name in ("plugin.yaml", "plugin.yml", "plugin.json"):
        candidate = Path(dir_path) / name
        if candidate.is_file():
            return candidate
    return None


def _read_overlay_manifest(dir_path: Path) -> Optional[Dict[str, Any]]:
    """Qubit overlay plugins carry a root ``manifest.json`` (tab + entry)."""
    path = Path(dir_path) / "manifest.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("entry"):
        return None
    return data


def _provides(manifest: Dict[str, Any]) -> Dict[str, List[str]]:
    m = manifest if isinstance(manifest, dict) else {}
    return {
        "tools": _str_list(m.get("provides_tools")) or _str_list(m.get("tools")),
        "hooks": _str_list(m.get("hooks")) or _str_list(m.get("provides_hooks")),
        "skills": _str_list(m.get("skills")) or _str_list(m.get("provides_skills")),
        "web_providers": _str_list(m.get("provides_web_providers")),
        "browser_providers": _str_list(m.get("provides_browser_providers")),
        "requires_env": _str_list(m.get("requires_env")),
        "optional_env": _str_list(m.get("optional_env")),
    }


def _base_row(**kw: Any) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "id": "", "name": "", "label": "", "description": "", "version": "",
        "source": "", "kind": "", "path": "", "manifest_path": "",
        "installed": False, "enabled": False, "runtime_status": "inactive",
        "loadable": False, "load_state": "", "has_dashboard_manifest": False,
        "dashboard": None,
        "provides": {"tools": [], "hooks": [], "skills": [],
                     "web_providers": [], "browser_providers": [],
                     "requires_env": [], "optional_env": []},
        "provenance": {},
        "can_enable": False, "can_disable": False, "can_remove": False,
        "can_update": False, "can_hide": False, "can_install": False,
        "user_hidden": False, "declared_dependencies": [],
        "install_capability": "", "error": None,
    }
    row.update(kw)
    return row


def _vendored_agent_entries(cfg: Any) -> List[Dict[str, Any]]:
    """The vendored agent plugin fleet, with the reference activation verdict."""
    _prepare_runtime(cfg)
    from qubit_cli.plugins_cmd import (
        _bundled_default_on,
        _discover_all_plugins,
        _get_current_memory_provider,
        _get_disabled_set,
        _get_enabled_set,
        _read_manifest,
    )

    enabled = _get_enabled_set()
    disabled = _get_disabled_set()
    try:
        active = {n for n in (_get_current_memory_provider(),) if n}
    except Exception:
        active = set()
    out: List[Dict[str, Any]] = []
    for name, version, description, source, dir_str, key in _discover_all_plugins():
        dir_path = Path(dir_str)
        manifest = _read_manifest(dir_path) or {}
        aliases = {name, key} - {""}
        if aliases & disabled:
            status = "disabled"
        elif aliases & enabled or aliases & active:
            status = "enabled"
        elif source == "bundled" and _bundled_default_on(dir_path):
            status = "enabled"
        else:
            status = "inactive"
        out.append({
            "name": name, "key": key, "version": version,
            "description": description, "source": source,
            "dir_path": dir_path, "manifest": manifest, "status": status,
        })
    return out


def _vendored_dashboard_entries(cfg: Any) -> List[Dict[str, Any]]:
    _prepare_runtime(cfg)
    from . import qubit_plugins as _QP

    out: List[Dict[str, Any]] = []
    for m in _QP.plugin_fleet_manifests():
        name = str(m.get("name") or "")
        surface = _QP.dashboard_surface(name)
        # ``surface`` is ``<plugin>/dashboard/manifest.json``; the plugin root
        # (what ``_read_dashboard_manifest`` and validation resolve from) is two
        # levels up.
        plugin_root = surface.parent.parent if surface else None
        out.append({"manifest": m, "path": plugin_root,
                    "manifest_path": surface})
    return out


def _runtime_entries(cfg: Any) -> List[Dict[str, Any]]:
    from . import plugin_runtime as PR

    catalog = PR.plugin_catalog(cfg)
    return list(catalog.get("plugins", []) or [])


def _matches(row: Dict[str, Any], query: str) -> bool:
    if not query:
        return True
    q = query.strip().lower()
    hay = [row.get("id", ""), row.get("name", ""), row.get("label", ""),
           row.get("description", ""), row.get("source", ""),
           row.get("kind", "")]
    for values in (row.get("provides") or {}).values():
        if isinstance(values, list):
            hay.extend(str(v) for v in values)
    return any(q in str(item).lower() for item in hay)


def _index_doc() -> Dict[str, Any]:
    """Read the curated pinned index document (read-only, never fetched)."""
    try:
        raw = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def load_pinned_index() -> Dict[str, Any]:
    """The parsed curated pinned index document (read-only; never fetched)."""
    return _index_doc()


def _valid_index_entry(raw: Any) -> Optional[Dict[str, Any]]:
    """Validate one curated entry; ``None`` (skipped) on a malformed row.

    Never raises: a malformed curated row is dropped rather than fabricating a
    replacement. Name, https repo and a full 40-hex pinned ref are mandatory —
    the same admission rules the upstream loader enforces
    (``hermes_cli/plugin_catalog.py:125-171``).
    """
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    repo = str(raw.get("repo") or "").strip()
    pin = str(raw.get("pinned_ref") or raw.get("sha") or "").strip().lower()
    if not name or not repo.startswith("https://") or not _SHA40_RE.match(pin):
        return None
    provenance = raw.get("provenance")
    return {
        "id": name,
        "name": name,
        "label": str(raw.get("label") or name),
        "description": _bounded(raw.get("description", ""), 400),
        "repo": repo,
        "subdir": str(raw.get("subdir") or ""),
        "pinned_ref": pin,
        "pinned_ref_short": pin[:8],
        "homepage": str(raw.get("homepage") or ""),
        "maintainer": str(raw.get("maintainer") or ""),
        "license": str(raw.get("license") or "unverified"),
        "tier": str(raw.get("tier") or "community"),
        "category": str(raw.get("category") or "general"),
        "capabilities": _str_list(raw.get("capabilities")),
        "api_version": raw.get("api_version"),
        "tags": _str_list(raw.get("tags")),
        "curated": True,
        "pinned": True,
        "install_state": "OPERATOR_GATED",
        "install_code": INDEX_CODE,
        "install_capability": _install_capability(),
        "provenance": provenance if isinstance(provenance, dict) else {},
    }


def _install_capability() -> str:
    try:
        from . import plugin_runtime as PR
        return PR.INSTALL_CAPABILITY
    except Exception:
        return "plugin.install"


def pinned_index_entries(query: str = "") -> List[Dict[str, Any]]:
    """The curated pinned index entries, optionally filtered by *query*."""
    out: List[Dict[str, Any]] = []
    for raw in _index_doc().get("entries", []) or []:
        entry = _valid_index_entry(raw)
        if entry is None:
            continue
        if query:
            q = query.strip().lower()
            hay = [entry["name"], entry["label"], entry["description"],
                   entry["repo"], entry["maintainer"], entry["category"],
                   entry["tier"], *entry["tags"], *entry["capabilities"]]
            if not any(q in str(item).lower() for item in hay):
                continue
        out.append(entry)
    return out


def pinned_index_block(query: str = "") -> Dict[str, Any]:
    """The curated, pinned index block served alongside the local catalog.

    Clearly marked ``curated`` + ``pinned``. Every entry pins a full 40-char
    commit SHA. The install affordance is operator-gated: the entry is verified
    against the curated pin and routed through the canonical e-stop ->
    confirmation -> audit path, but no repository is fetched (raw remote fetch
    stays refused).
    """
    doc = _index_doc()
    entries = pinned_index_entries(query)
    return {
        "ok": True,
        "state": "CURATED_PINNED",
        "schema": INDEX_SCHEMA,
        "curated": True,
        "pinned": True,
        "source": str(doc.get("source") or "qubit_runtime/qubit_cli/data/plugin_index.json"),
        "curation": str(doc.get("curation") or ""),
        "license_note": str(doc.get("license_note") or ""),
        "install_policy": str(doc.get("install_policy") or ""),
        "query": query,
        "count": len(entries),
        "install_capability": _install_capability(),
        "entries": entries,
        "install": {
            "state": "OPERATOR_GATED",
            "code": INDEX_CODE,
            "detail": INDEX_INSTALL_DETAIL,
            "prerequisite": INDEX_INSTALL_PREREQUISITE,
            "endpoint": ("POST /api/dashboard/agent-plugins/install "
                         '{"catalog_name": "<name>", "ref": "<pinned_ref>", '
                         '"confirm": true}'),
        },
        "raw_remote_fetch": {
            "state": "REFUSED", "code": REMOTE_CODE,
            "detail": REMOTE_DETAIL, "prerequisite": REMOTE_PREREQUISITE,
        },
    }


def _index_entry(catalog_name: str) -> Optional[Dict[str, Any]]:
    wanted = str(catalog_name or "").strip().lower()
    if not wanted:
        return None
    for entry in pinned_index_entries():
        if wanted in (entry["name"].lower(), entry["id"].lower()):
            return entry
    return None


def install_from_index(cfg: Any, catalog_name: str, *, ref: str = "",
                       confirmed: bool = False, confirmation_id: str = "",
                       requested_by: str = "dashboard:user") -> Dict[str, Any]:
    """Pinned/verified install request for a curated index entry.

    Order (the canonical path, shared with every plugin mutation):

    1. resolve the entry in the curated pinned index — unknown name is a typed
       ``PLUGIN_INDEX_NOT_FOUND``;
    2. verify the pin: a caller-supplied ``ref`` must equal the curated
       ``pinned_ref`` (an off-pin ref is a typed ``PLUGIN_INDEX_REF_MISMATCH``);
    3. e-stop -> durable journal confirmation -> audit via the canonical
       ``plugin_runtime`` authority; then
    4. return the honest operator-gated gate: the pinned remote source is not
       fetched, so nothing is installed until an operator verifies the repo and
       license at the pinned ref.

    Never fetches, clones, or executes anything.
    """
    from . import plugin_runtime as PR

    entry = _index_entry(catalog_name)
    if entry is None:
        raise PR.PluginRuntimeError(
            INDEX_NOT_FOUND_CODE,
            f"no curated pinned-index entry {str(catalog_name)[:80]!r}")
    wanted = str(ref or "").strip().lower()
    pin = str(entry.get("pinned_ref") or "").lower()
    if wanted and wanted != pin:
        raise PR.PluginRuntimeError(
            INDEX_REF_MISMATCH_CODE,
            f"requested ref {wanted[:12]!r} does not match the curated pin "
            f"{pin[:12]!r}; only the pinned ref is installable")
    # Canonical gate: e-stop -> durable journal confirmation -> audit.
    PR._authorize_mutation(cfg, PR.INSTALL_CAPABILITY, confirmed=confirmed,
                           confirmation_id=confirmation_id,
                           requested_by=requested_by)
    PR._audit("index_install_request", requested_by,
              {"catalog_name": entry["name"], "pinned_ref": pin,
               "repo": entry.get("repo", "")})
    return {
        "ok": False, "state": "OPERATOR_GATED", "code": INDEX_CODE,
        "catalog_name": entry["name"], "pinned_ref": pin,
        "repo": entry.get("repo", ""),
        "detail": INDEX_INSTALL_DETAIL,
        "prerequisite": INDEX_INSTALL_PREREQUISITE,
        "install_capability": PR.INSTALL_CAPABILITY,
        "qubit_counterpart": ("CLI `qubit agent plugins install "
                              "<operator-verified-dir>`"),
    }


def local_catalog(cfg: Any, *, query: str = "", source: str = "",
                  installed_only: bool = False, enabled_only: bool = False,
                  loadable_only: bool = True) -> Dict[str, Any]:
    """The real local plugin catalog, filtered and counted (read-only)."""
    from . import plugin_runtime as PR

    estop = PR.estop_engaged(cfg)
    hidden = _hidden_set(cfg)
    rows: List[Dict[str, Any]] = []
    seen: set = set()

    def _add(row: Dict[str, Any]) -> None:
        pid = str(row.get("id") or "")
        if not pid or pid in seen:
            return
        seen.add(pid)
        rows.append(row)

    for entry in _vendored_agent_entries(cfg):
        dir_path: Path = entry["dir_path"]
        manifest: Dict[str, Any] = entry["manifest"]
        name = str(entry["name"])
        key = str(entry["key"] or name)
        dm = _read_dashboard_manifest(dir_path)
        status = entry["status"]
        src = entry["source"]
        if src == "bundled":
            source_label = "bundled"
        elif src in ("user", "git"):
            source_label = "user"
        elif src == "entrypoint":
            source_label = "entrypoint"
        else:
            source_label = src or "bundled"
        mf = _manifest_file(dir_path)
        provides = _provides(manifest)
        kind = str(manifest.get("kind") or "")
        if not kind:
            if provides["tools"]:
                kind = "tool"
            elif provides["hooks"]:
                kind = "hook"
            elif provides["web_providers"] or provides["browser_providers"]:
                kind = "provider"
            else:
                kind = "plugin"
        _add(_base_row(
            id=key, name=name,
            label=str(manifest.get("label") or manifest.get("name") or name),
            description=_bounded(manifest.get("description")
                                 or entry["description"] or "", 400),
            version=str(manifest.get("version") or entry["version"] or ""),
            source=source_label,
            kind=kind,
            path=str(dir_path),
            manifest_path=str(mf) if mf else "",
            installed=True, enabled=status == "enabled",
            runtime_status=status, loadable=True,
            load_state="",
            has_dashboard_manifest=bool(dm), dashboard=dm,
            provides=provides,
            provenance={
                "loader": "qubit_cli.plugins_cmd._discover_all_plugins",
                "registry": "plugins.enabled / plugins.disabled",
                "manifest": str(mf) if mf else "",
            },
            can_enable=status != "enabled", can_disable=status == "enabled",
            can_remove=src in ("user", "git"),
            can_update=False, can_hide=bool(dm),
            user_hidden=(name in hidden or key in hidden),
            declared_dependencies=_str_list(manifest.get("python_dependencies")),
            install_capability=PR.INSTALL_CAPABILITY,
        ))

    for entry in _vendored_dashboard_entries(cfg):
        m = entry["manifest"]
        name = str(m.get("name") or "")
        _add(_base_row(
            id=name, name=name, label=str(m.get("label") or name),
            description=_bounded(m.get("description", ""), 400),
            version=str(m.get("version") or ""),
            source="dashboard", kind="dashboard",
            path=str(entry["path"] or ""),
            manifest_path=str(entry["manifest_path"] or ""),
            installed=True, enabled=True, runtime_status="enabled",
            loadable=True, has_dashboard_manifest=True,
            dashboard={"name": name, "label": m.get("label", name),
                       "description": m.get("description", ""),
                       "icon": m.get("icon", ""), "version": m.get("version", ""),
                       "tab": m.get("tab", {}), "entry": m.get("entry"),
                       "css": m.get("css"), "has_api": bool(m.get("api")),
                       "source": "vendored"},
            provenance={
                "loader": "qubit_cli.web_server_dashboard._discover_dashboard_plugins",
                "registry": "vendored dashboard plugin fleet",
                "manifest": str(entry["manifest_path"] or ""),
            },
            can_hide=True, user_hidden=name in hidden,
            install_capability=PR.INSTALL_CAPABILITY,
        ))

    for m in _overlay_manifests():
        name = str(m.get("name") or "")
        if not name:
            continue
        plug_dir = OVERLAY_DIR / "plugins" / name
        _add(_base_row(
            id=name, name=name, label=str(m.get("label") or name),
            description=_bounded(m.get("description", ""), 400),
            version=str(m.get("version") or ""),
            source="overlay", kind="dashboard",
            path=str(plug_dir),
            manifest_path=str(plug_dir / "manifest.json"),
            installed=True, enabled=True, runtime_status="enabled",
            loadable=True, has_dashboard_manifest=True,
            dashboard={"name": name, "label": m.get("label", name),
                       "description": m.get("description", ""),
                       "icon": m.get("icon", ""), "version": m.get("version", ""),
                       "tab": m.get("tab", {}), "entry": m.get("entry"),
                       "css": m.get("css"), "has_api": bool(m.get("has_api")),
                       "source": m.get("source", "qubit-overlay")},
            provides=_provides(m),
            provenance={
                "loader": "voicebridge.qubit_compat._overlay_manifests",
                "registry": "Qubit dashboard overlay plugin set",
                "manifest": str(plug_dir / "manifest.json"),
            },
            can_hide=True, user_hidden=name in hidden,
            install_capability=PR.INSTALL_CAPABILITY,
        ))

    for r in _runtime_entries(cfg):
        pid = str(r.get("id") or "")
        if not pid:
            continue
        src = str(r.get("source") or "")
        _add(_base_row(
            id=pid, name=pid,
            label=str(r.get("label") or pid),
            description=_bounded(r.get("description", ""), 400),
            version=str(r.get("version") or ""),
            source=src or "registered", kind=str(r.get("kind") or "tool"),
            path=str(r.get("install_dir") or ""),
            manifest_path="",
            installed=bool(r.get("installed", True)),
            enabled=bool(r.get("enabled", True)),
            runtime_status="enabled" if r.get("enabled", True) else "disabled",
            loadable=bool(r.get("installed", True)),
            load_state=str(r.get("load_state") or ""),
            provides={"tools": list((r.get("provides") or {}).get("tools", [])),
                      "hooks": list((r.get("provides") or {}).get("hooks", [])),
                      "skills": list((r.get("provides") or {}).get("skills", [])),
                      "web_providers": [], "browser_providers": [],
                      "requires_env": [], "optional_env": []},
            provenance={
                "loader": "voicebridge.plugin_runtime.plugin_catalog",
                "registry": "QUBIT_HOME plugin store",
                "manifest": "",
            },
            can_enable=bool(r.get("installed")) and not r.get("enabled", True),
            can_disable=bool(r.get("installed")) and bool(r.get("enabled", True)),
            can_install=bool(r.get("can_install")),
            install_capability=PR.INSTALL_CAPABILITY,
        ))

    if source:
        rows = [r for r in rows if r["source"] == source]
    if installed_only:
        rows = [r for r in rows if r["installed"]]
    if enabled_only:
        rows = [r for r in rows if r["enabled"]]
    if loadable_only:
        rows = [r for r in rows if r["loadable"]]
    if query:
        rows = [r for r in rows if _matches(r, query)]
    rows.sort(key=lambda r: (r["source"], r["id"]))

    counts: Dict[str, int] = {"total": len(rows), "enabled": 0,
                              "installed": 0, "loadable": 0}
    for r in rows:
        counts[r["source"]] = counts.get(r["source"], 0) + 1
        counts["enabled"] += 1 if r["enabled"] else 0
        counts["installed"] += 1 if r["installed"] else 0
        counts["loadable"] += 1 if r["loadable"] else 0

    return {
        "ok": True, "state": "ESTOP" if estop else "OK",
        "schema": CATALOG_SCHEMA,
        "generated_at": _now_iso(),
        "query": query, "source": source,
        "count": len(rows), "counts": counts,
        "estop": estop,
        "remote": {"state": "REFUSED", "code": REMOTE_CODE,
                   "detail": REMOTE_DETAIL,
                   "prerequisite": REMOTE_PREREQUISITE},
        "pinned_index": pinned_index_block(query=query),
        "install_capability": PR.INSTALL_CAPABILITY,
        "entries": rows,
    }


def _validate_directory(dir_path: Path,
                        manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Static manifest validation for a directory plugin (no code executed).

    Native agent plugins validate their ``plugin.yaml`` + ``__init__.py``;
    vendored dashboard plugins (no native manifest) validate their
    ``dashboard/manifest.json`` + declared entry bundle + backend ``api``.
    """
    checks: List[Dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    dir_path = Path(dir_path)
    manifest = manifest if isinstance(manifest, dict) else {}
    native = bool(manifest)
    if native:
        add("manifest present", True)
        add("name", bool(str(manifest.get("name") or "").strip()),
            str(manifest.get("name") or ""))
        add("version", bool(str(manifest.get("version") or "").strip()),
            str(manifest.get("version") or ""))
        entry = dir_path / "__init__.py"
        add("entrypoint", entry.is_file(), str(entry))
    dm = _read_dashboard_manifest(dir_path)
    if dm is not None:
        add("dashboard manifest", bool(dm.get("name")) and bool(dm.get("entry")),
            f"entry={dm.get('entry')}")
        entry_rel = str(dm.get("entry") or "")
        if entry_rel:
            add("dashboard entry bundle",
                (dir_path / "dashboard" / entry_rel).is_file(),
                entry_rel)
        api_rel = str(dm.get("api") or "")
        if api_rel:
            add("dashboard api",
                (dir_path / "dashboard" / api_rel).is_file(), api_rel)
    overlay = _read_overlay_manifest(dir_path)
    if overlay is not None:
        add("overlay manifest", bool(overlay.get("name")),
            str(overlay.get("name") or ""))
        add("overlay entry bundle",
            (dir_path / str(overlay.get("entry"))).is_file(),
            str(overlay.get("entry") or ""))
    if not native and dm is None and overlay is None:
        add("manifest present", False, "no plugin.yaml and no dashboard manifest")
    return {"ok": all(c["ok"] for c in checks), "checks": checks}


def _find_row(cfg: Any, plugin_id: str) -> Optional[Dict[str, Any]]:
    pid = str(plugin_id or "").strip()
    if not pid:
        return None
    catalog = local_catalog(cfg, loadable_only=False)
    for row in catalog.get("entries", []):
        if pid in (row.get("id"), row.get("name")):
            return row
    return None


def plugin_detail(cfg: Any, plugin_id: str) -> Dict[str, Any]:
    """One plugin's real provenance + manifest validation + dependency verdict."""
    from . import plugin_runtime as PR

    row = _find_row(cfg, plugin_id)
    if row is None:
        return {"ok": False, "state": "UNAVAILABLE", "code": "PLUGIN_NOT_FOUND",
                "detail": f"no plugin {plugin_id!r} in the Qubit local catalog",
                "qubit_counterpart": "GET /api/dashboard/plugins/catalog"}
    detail = dict(row)

    manifest: Dict[str, Any] = {}
    dir_path = Path(str(row.get("path") or "")) if row.get("path") else None
    if dir_path and dir_path.is_dir():
        try:
            _prepare_runtime(cfg)
            from qubit_cli.plugins_cmd import _read_manifest
            manifest = _read_manifest(dir_path) or {}
        except Exception:
            manifest = {}
        detail["validation"] = _validate_directory(dir_path, manifest)
        effective = manifest or (_read_dashboard_manifest(dir_path) or {})
        try:
            detail["dependencies"] = PR.check_plugin_requirements(cfg, effective)
        except Exception as exc:
            detail["dependencies"] = {
                "ok": False, "missing_dependencies": [], "missing_plugins": [],
                "incompatible": [], "warnings": [_bounded(exc, 200)],
                "install_hint": ""}
        if not manifest:
            manifest = effective
    else:
        detail["validation"] = {"ok": True, "checks": [
            {"name": "runtime manifest", "ok": True,
             "detail": "registered in the Qubit plugin-runtime store"}]}
        detail["dependencies"] = {
            "ok": True, "missing_dependencies": [], "missing_plugins": [],
            "incompatible": [], "warnings": [], "install_hint": ""}

    provides = detail.get("provides") or {}
    detail["configuration"] = {
        "declared_env": list(provides.get("requires_env", [])),
        "optional_env": list(provides.get("optional_env", [])),
        "python_dependencies": list(detail.get("declared_dependencies", [])),
        "config_surface": "env" if provides.get("requires_env") else "none",
        "writable_via": ("POST /api/env (secret values) + PUT /api/config "
                         "(non-secret settings)") if provides.get("requires_env") else "",
    }
    detail["manifest"] = manifest
    return {"ok": True, "state": "OK", "schema": CATALOG_SCHEMA,
            "estop": PR.estop_engaged(cfg), "plugin": detail}


__all__ = [
    "CATALOG_SCHEMA",
    "REMOTE_CODE",
    "INDEX_SCHEMA",
    "INDEX_CODE",
    "INDEX_NOT_FOUND_CODE",
    "INDEX_REF_MISMATCH_CODE",
    "local_catalog",
    "plugin_detail",
    "load_pinned_index",
    "pinned_index_entries",
    "pinned_index_block",
    "install_from_index",
]
