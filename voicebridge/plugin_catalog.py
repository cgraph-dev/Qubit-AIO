"""Qubit dashboard plugin catalog — the real, loadable plugin set.

Serves ``GET /api/dashboard/plugins/catalog`` and
``GET /api/dashboard/plugins/catalog/<id>``.

The upstream reference serves a curated index of GitHub-pinned repositories
(``hermes_cli/plugin_catalog.py`` + ``plugins_cmd_catalog.py``). Qubit serves
the same universe: the 5 curated entries plus every upstream catalog YAML as
an installable row pinned at its YAML SHA (unreviewed rows are marked and
carry a warning). A confirmed install fetches the entry at exactly its pin
through the vendored catalog pipeline; arbitrary (non-index) remote
identifiers stay a typed ``BLOCKED_REMOTE_PLUGIN`` refusal. What Qubit CAN
load and manage is served instead, with real provenance for every row:

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
    "this entry is curated and pinned: confirming the install fetches its "
    "remote source at exactly the pinned ref and installs it through the "
    "vendored catalog pipeline (kill-list, platform check, exact-SHA clone, "
    "pre-publish malware scan, admission-gated enable). Raw (non-index) "
    "remote fetch remains refused.")
INDEX_INSTALL_PREREQUISITE = (
    "POST /api/dashboard/agent-plugins/install "
    '{"catalog_name": "<name>", "ref": "<pinned_ref>", "confirm": true} '
    "(or settle the staged approval); arbitrary remote URLs stay refused — "
    "only curated entries install, and only at their pinned ref")
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
#: Local memo of index rows proven uninstallable (e.g. the upstream YAML's
#: subdir does not exist at the pinned SHA — upstream data drift the reference
#: would hit identically). Keyed ``"<name>\0<pin>"`` -> reason. A failed install
#: teaches the catalog instead of failing the same way forever.
_KNOWN_BAD_FILENAME = "plugin-index-known-bad.json"
_SUBDIR_MISSING_MARK = "does not exist in the repository"


def _known_bad_path(cfg: Any) -> Path:
    try:
        base = Path(cfg.state_dir())
    except Exception:
        base = Path(".")
    return base / _KNOWN_BAD_FILENAME


def _installed_catalog_map() -> Dict[str, Dict[str, Any]]:
    """Installed plugins keyed by catalog name: ``name -> {plugin, revision}``.

    Reads the installer-owned metadata sidecar (the same record installs
    write): ``catalog.name`` when present, plus the plugin dir name as a
    fallback key. Empty on any failure — an unknown install state never
    blocks serving the index.
    """
    try:
        _prepare_runtime_no_home()
        from qubit_cli.plugins_cmd_git import (  # type: ignore
            _read_install_metadata)
        meta = _read_install_metadata()
    except Exception:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for plugin_name, rec in (meta or {}).items():
        if not isinstance(rec, dict):
            continue
        block = rec.get("catalog") or {}
        keys = {str(plugin_name)}
        if isinstance(block, dict) and block.get("name"):
            keys.add(str(block["name"]))
        for key in keys:
            out[key.lower()] = {
                "plugin": str(plugin_name),
                "revision": str(rec.get("revision") or "").lower(),
                "pinned": bool(rec.get("pinned")),
            }
    return out


def _prepare_runtime_no_home() -> None:
    from . import qubit_runtime as _QR
    _QR.ensure_path()


def _known_bad_map(cfg: Any = None) -> Dict[str, str]:
    if cfg is None:
        return {}
    try:
        raw = json.loads(_known_bad_path(cfg).read_text(encoding="utf-8"))
        return dict(raw) if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _record_known_bad(cfg: Any, name: str, pin: str, reason: str) -> None:
    try:
        path = _known_bad_path(cfg)
        memo = _known_bad_map(cfg)
        memo[f"{name}\0{pin}"] = reason
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(memo, indent=2, sort_keys=True))
        tmp.replace(path)
    except OSError:
        pass


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
    provenance = raw.get("provenance")
    provenance = provenance if isinstance(provenance, dict) else {}
    # B4-06: the served row is product surface and must carry no legacy brand,
    # so the real upstream URL now lives only in the internal ``provenance``
    # sub-field (rendered behind the plugin's Details disclosure). Admission
    # still requires a real https upstream repo + a full 40-hex pin — the same
    # rules the upstream loader enforces (``hermes_cli/plugin_catalog.py:125-171``).
    upstream_repo = str(
        provenance.get("upstream_repo")
        or provenance.get("source_repo_url")
        or repo or "").strip()
    if not name or not upstream_repo.startswith("https://") or \
            not _SHA40_RE.match(pin):
        return None
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
        "provenance": provenance,
    }


def _install_capability() -> str:
    try:
        from . import plugin_runtime as PR
        return PR.INSTALL_CAPABILITY
    except Exception:
        return "plugin.install"


def pinned_index_entries(query: str = "",
                         known_bad: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """The curated pinned index entries plus the upstream catalog, optionally
    filtered by *query*.

    Curated rows come first (``review: "curated"``); the remaining upstream
    catalog YAMLs follow as ``review: "unreviewed"`` installable rows pinned
    at their YAML SHA. Upstream rows duplicating a curated entry (same repo +
    subdir) are skipped — the curated row wins. Rows present in *known_bad*
    (a previous install proved them uninstallable at this pin) are flagged
    ``install_state: "BROKEN"`` with the reason instead of failing again.
    """
    bad = known_bad or {}
    try:
        installed = _installed_catalog_map()
    except Exception:
        installed = {}
    out: List[Dict[str, Any]] = []
    for raw in _index_doc().get("entries", []) or []:
        entry = _valid_index_entry(raw)
        if entry is None:
            continue
        _stamp_installed(entry, installed)
        if query and not _entry_matches(entry, query):
            continue
        out.append(entry)
    for row in _upstream_index_rows(known_bad=bad):
        _stamp_installed(row, installed)
        if query and not _entry_matches(row, query):
            continue
        out.append(row)
    return out


def _stamp_installed(entry: Dict[str, Any],
                     installed: Dict[str, Dict[str, Any]]) -> None:
    """Stamp a served index row with local install state (never raises).

    Matches the row's served id/name plus its upstream source name against
    the installer metadata. A row installed at its served pin reports
    ``installed: True`` with ``install_state: "INSTALLED"`` (unless a
    known-bad flag already explains it); anything else installs normally.
    """
    try:
        provenance = entry.get("provenance") or {}
        candidates = [entry.get("id"), entry.get("name"),
                      provenance.get("source_name")]
        hit = next((installed[str(c).lower()] for c in candidates
                    if c and str(c).lower() in installed), None)
        if hit is None:
            return
        entry["installed"] = True
        entry["installed_plugin"] = hit["plugin"]
        entry["installed_revision"] = hit["revision"]
        if entry.get("install_state") == "READY":
            entry["install_state"] = "INSTALLED"
    except Exception:
        pass


def _entry_matches(entry: Dict[str, Any], query: str) -> bool:
    q = query.strip().lower()
    provenance = entry.get("provenance") or {}
    hay = [entry.get("name"), entry.get("label"), entry.get("description"),
           entry.get("repo"), entry.get("maintainer"), entry.get("category"),
           entry.get("tier"), provenance.get("source_name"),
           provenance.get("upstream_repo"),
           *entry.get("tags", []), *entry.get("capabilities", [])]
    return any(q in str(item).lower() for item in hay)


def _repo_key(repo: str) -> str:
    """Normalize a repo URL/shorthand for dedupe comparisons."""
    r = (repo or "").strip().lower()
    r = re.sub(r"^https?://", "", r)
    if r.endswith(".git"):
        r = r[:-4]
    return r.strip("/")


def _upstream_index_rows(known_bad: Optional[Dict[str, str]] = None
                       ) -> List[Dict[str, Any]]:
    """Every upstream catalog YAML as an installable (unreviewed) index row.

    The vendored ``plugin-catalog/`` tree is the trust anchor: each row pins
    the YAML's own SHA, and installs re-verify it. Rows duplicating a curated
    entry resolve to the curated row instead (matched by repo + subdir).
    Never raises: YAML parse skips are warnings inside the loader, and a
    missing tree yields no rows.
    """
    try:
        # Fork root on sys.path (same ensure every catalog read performs;
        # the YAML tree is read-only data, no home state is touched).
        from . import qubit_runtime as _QR
        _QR.ensure_path()
        from qubit_cli import plugin_catalog as _VC  # type: ignore
        yaml_rows = _VC.load_catalog()
    except Exception:
        return []
    curated_repos = set()
    for raw in _index_doc().get("entries", []) or []:
        provenance = (raw.get("provenance") or {}) if isinstance(raw, dict) \
            else {}
        repo = str(provenance.get("upstream_repo")
                   or provenance.get("source_repo")
                   or raw.get("repo") or "").strip().lower()
        subdir = str(raw.get("subdir") or "").strip().lower()
        curated_repos.add((_repo_key(repo), subdir))
    bad = known_bad or {}
    out: List[Dict[str, Any]] = []
    for entry in yaml_rows:
        try:
            repo = str(entry.repo or "").strip()
            subdir = str(entry.subdir or "").strip()
            sha = str(entry.sha or "").strip().lower()
            key = (_repo_key(repo), subdir.lower())
            if not repo.startswith("https://") or not _SHA40_RE.match(sha):
                continue
            if key in curated_repos:
                continue
            caps = getattr(entry, "capabilities", None)
            # B4-06: served display rows carry no legacy brand outside
            # provenance and the install-critical subdir (same rule as
            # curated rows) — the real upstream URL and maintainer live
            # only in the internal ``provenance`` sub-field, rendered behind
            # the Details disclosure. ``subdir`` is exempt with provenance:
            # it is the third-party upstream path installs fetch verbatim,
            # not rebrandable prose.
            broken_reason = bad.get(f"{entry.name}\0{sha}", "")
            out.append({
                "id": entry.name, "name": entry.name,
                "label": str(entry.title or entry.name),
                "description": _bounded(entry.description or "", 400),
                "repo": "", "subdir": subdir,
                "pinned_ref": sha, "pinned_ref_short": sha[:8],
                "homepage": "", "maintainer": "",
                "license": "unverified",
                "tier": str(entry.tier or "community"),
                "category": str(entry.category or "general"),
                "capabilities": _str_list(getattr(caps, "provides_tools", [])
                                          + getattr(caps, "provides_hooks", [])),
                "api_version": None,
                "tags": ["unreviewed"],
                "curated": False, "pinned": True,
                "review": "unreviewed",
                "install_state": "BROKEN" if broken_reason else "READY",
                "install_code": ("PLUGIN_INDEX_KNOWN_BAD"
                                 if broken_reason
                                 else "PLUGIN_INDEX_INSTALLABLE"),
                "install_note": broken_reason,
                "install_capability": _install_capability(),
                "provenance": {
                    "source_name": entry.name,
                    "upstream_repo": repo,
                    "upstream_maintainer": str(entry.maintainer or ""),
                    "curation": "upstream-unreviewed",
                },
            })
        except Exception:
            continue
    return out


def pinned_index_block(query: str = "",
                       known_bad: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """The curated, pinned index block served alongside the local catalog.

    Clearly marked ``curated`` + ``pinned``. Every entry pins a full 40-char
    commit SHA. Confirming an install fetches the entry at exactly the pin
    through the canonical e-stop -> confirmation -> audit path and the
    vendored catalog pipeline; arbitrary (non-index) remote fetch stays
    refused.
    """
    doc = _index_doc()
    entries = pinned_index_entries(query, known_bad=known_bad)
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
            "state": "READY",
            "code": "PLUGIN_INDEX_INSTALLABLE",
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
    """Pinned/verified install of a curated index entry, fetched at the pin.

    Order (the canonical path, shared with every plugin mutation):

    1. resolve the entry in the curated pinned index — unknown name is a typed
       ``PLUGIN_INDEX_NOT_FOUND``;
    2. verify the pin: a caller-supplied ``ref`` must equal the curated
       ``pinned_ref`` (an off-pin ref is a typed ``PLUGIN_INDEX_REF_MISMATCH``);
    3. e-stop -> durable journal confirmation -> audit via the canonical
       ``plugin_runtime`` authority;
    4. fetch the entry's upstream repository at exactly the curated pin and
       install it through the vendored catalog pipeline
       (``install_catalog_entry``): kill-list, unsupported-platform refusal,
       exact-SHA clone, malware scan before publish, atomic publish with
       provenance recorded at the actually-checked-out SHA, admission-gated
       enable. The vendored YAML is cross-checked against the curated pin and
       any drift is a loud refusal, never a silent substitution.

    What stays refused: arbitrary (non-index) remote identifiers
    (``BLOCKED_REMOTE_PLUGIN``) — only curated entries with a full 40-hex pin
    are fetchable, and only at that pin.
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
    # Idempotent reinstall: already installed at exactly this pin is success
    # with no fetch — never an ALREADY_EXISTS error for clicking Install twice.
    try:
        installed_here = _installed_catalog_map().get(
            str(entry.get("name") or "").lower())
    except Exception:
        installed_here = None
    if installed_here and installed_here.get("revision") == pin:
        PR._audit("index_install_noop", requested_by,
                  {"catalog_name": entry["name"], "pinned_ref": pin,
                   "plugin_name": installed_here.get("plugin")})
        return {
            "ok": True, "state": "INSTALLED", "catalog_name": entry["name"],
            "plugin_name": installed_here.get("plugin"), "pinned_ref": pin,
            "revision": pin, "unchanged": True,
            "detail": "already installed at the pinned ref; nothing fetched",
            "review": "curated" if entry.get("curated") else "unreviewed",
            "install_capability": PR.INSTALL_CAPABILITY,
        }
    _prepare_runtime(cfg)
    from qubit_cli import plugin_catalog as _VC  # type: ignore
    from qubit_cli import plugins_cmd_catalog as _VCC  # type: ignore
    from qubit_cli.plugins_admission import AdmissionRefused  # type: ignore
    from qubit_cli.plugins_cmd import (  # type: ignore
        PluginOperationError, PluginScanBlocked, _set_plugin_enabled)
    provenance = entry.get("provenance") or {}
    upstream = str(provenance.get("source_name") or entry["name"])
    upstream_repo = str(provenance.get("upstream_repo")
                        or provenance.get("source_repo_url")
                        or entry.get("repo") or "")
    subdir = str(entry.get("subdir") or "")
    cat_entry = None
    try:
        candidate = _VC.get_catalog_entry(upstream)
    except Exception:
        candidate = None
    if candidate is not None:
        if str(candidate.sha or "").lower() != pin:
            raise PR.PluginRuntimeError(
                INDEX_REF_MISMATCH_CODE,
                f"curated pin {pin[:12]!r} does not match the vendored catalog "
                f"sha {str(candidate.sha or '')[:12]!r} for {upstream!r}; "
                f"refusing rather than installing drifted source")
        cat_entry = candidate
    else:
        if not upstream_repo.startswith("https://"):
            raise PR.PluginRuntimeError(
                INDEX_NOT_FOUND_CODE,
                f"no fetchable upstream source for {entry['name']!r}")
        cat_entry = _VC.PluginCatalogEntry(
            name=upstream, repo=upstream_repo, sha=pin,
            description=str(entry.get("description") or ""),
            maintainer=str(entry.get("maintainer") or ""),
            tier=str(entry.get("tier") or "community"),
            category=str(entry.get("category") or "general"),
            subdir=subdir)
    try:
        _target, _manifest, installed_name = _VCC.install_catalog_entry(
            cat_entry, force=False)
    except PluginScanBlocked as exc:
        raise PR.PluginRuntimeError(
            PR.REFUSED_CODE,
            f"plugin scan blocked install of {upstream!r}: {exc}") from exc
    except PluginOperationError as exc:
        # Upstream data drift (e.g. the YAML's subdir does not exist at the
        # pinned SHA — the reference fails the identical install the identical way):
        # teach the catalog so the row shows BROKEN with the reason instead
        # of failing the same way on every retry.
        if _SUBDIR_MISSING_MARK in str(exc):
            _record_known_bad(
                cfg, entry["name"], pin,
                f"subdir {subdir or upstream!r} missing at pin "
                f"{pin[:12]} (upstream data drift)")
        raise PR.PluginRuntimeError(PR.REFUSED_CODE, str(exc)) from exc
    try:
        _set_plugin_enabled(installed_name, enable=True)
        enabled = True
        enable_note: str = ""
    except AdmissionRefused as exc:
        enabled = False
        enable_note = f"installed but enable refused: {exc}"
    try:
        from qubit_cli.plugins_activation import (  # type: ignore
            activate_plugin_now)
        activation = activate_plugin_now(installed_name) if enabled else {}
    except Exception as exc:  # noqa: BLE001 - activation is best-effort
        activation = {"activation_error": str(exc)[:200]}
    PR._audit("index_install", requested_by,
              {"catalog_name": entry["name"], "pinned_ref": pin,
               "plugin_name": installed_name, "enabled": enabled})
    out: Dict[str, Any] = {
        "ok": True, "state": "INSTALLED", "catalog_name": entry["name"],
        "plugin_name": installed_name, "pinned_ref": pin,
        "revision": pin, "enabled": enabled,
        "review": "curated" if entry.get("curated") else "unreviewed",
        "install_capability": PR.INSTALL_CAPABILITY,
    }
    if not entry.get("curated"):
        out["warning"] = (
            "unreviewed upstream source: kill-list, platform check, "
            "exact-SHA clone, and pre-publish scan passed, but no human "
            "reviewed this plugin — check what it does before enabling it "
            "broadly")
    if enable_note:
        out["enable_note"] = enable_note
    if activation:
        out["activation"] = activation
    return out


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
        # Only a runtime entry with an on-disk source directory can be
        # enable/disable-able: ``plugin_runtime.enable_plugin``/``disable_plugin``
        # refuse a record whose ``source_kind`` is not ``directory``
        # (PLUGIN_REFUSED, "no loadable source directory"). A manifest-only
        # record therefore must not advertise Enable/Disable (a dead control).
        # Removal is the real action for any installed, non-builtin store record.
        installed = bool(r.get("installed", True))
        enabled = bool(r.get("enabled", True))
        builtin = bool(r.get("builtin", False))
        is_dir = str(r.get("source_kind") or "") == "directory"
        _add(_base_row(
            id=pid, name=pid,
            label=str(r.get("label") or pid),
            description=_bounded(r.get("description", ""), 400),
            version=str(r.get("version") or ""),
            source=src or "registered", kind=str(r.get("kind") or "tool"),
            path=str(r.get("install_dir") or ""),
            manifest_path="",
            installed=installed,
            enabled=enabled,
            runtime_status="enabled" if enabled else "disabled",
            loadable=installed,
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
            can_enable=installed and not enabled and is_dir,
            can_disable=installed and enabled and is_dir,
            can_remove=installed and not builtin,
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
        "pinned_index": pinned_index_block(
            query=query, known_bad=_known_bad_map(cfg)),
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
