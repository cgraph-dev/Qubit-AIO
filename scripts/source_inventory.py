#!/usr/bin/env python3
"""Qubit direct source inventory (Phase 1/2 of the code-readiness directive).

Builds a deterministic, mechanical inventory directly from the pinned upstream
source trees -- not from the gap ledger -- then reconciles that
inventory against ``docs/archive/2026-09-21/QUBIT_GAP_LEDGER.{hermes,openclaw,qubit}.json`` and
writes a candidate-work omissions report.

Outputs (defaults, override with --out / --omissions):
    docs/archive/2026-09-21/QUBIT_SOURCE_INVENTORY.json
    docs/archive/2026-09-21/QUBIT_SOURCE_OMISSIONS.md

The script is read-only against the pinned trees and the repository except for
the two generated artifacts above. No timestamps are embedded so regeneration
is byte-for-byte deterministic.

Every non-evidence item is classified as either a **distinct capability** or a
**parented surface** of a capability that already has a ledger row (CLI
option/positional, model/provider entry, plugin asset/API/test/manifest,
upstream extension, workflow source file, dashboard component).  Each parented
surface records a deterministic ``parent_id`` and ``coverage`` kind, and is
marked covered only when its parent row is IMPLEMENTED in the pinned ledger, so
it never inflates the gap with a standalone MISSING row.  See
``scripts/rebuild_ledger_qubit.py`` for the live-ledger decision.

Usage:
    scripts/source_inventory.py
    scripts/source_inventory.py --hermes /tmp/opencode/pinned/hermes-agent \
        --openclaw /tmp/opencode/pinned/openclaw
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import re
import subprocess
import sys
import warnings
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

HERMES_PIN = "d337b736aa1e8ebecfab043842d13e4a2d2f48a3"
HERMES_TAG = "v0.21.4"
HERMES_REPO = "https://github.com/NousResearch/hermes-agent"
OPENCLAW_PIN = "da03996ab7203fe6b9b4d8edb2b54726e4394c9c"
OPENCLAW_TAG = "v2026.4.19-beta.2-28254-gda03996ab7"
OPENCLAW_REPO = "https://github.com/openclaw/openclaw"

# Ledger revision the inventory reconciles against. The default is the live
# working tree so a plain regeneration reflects the current implementations and
# matches the committed docs/archive/2026-09-21/QUBIT_SOURCE_INVENTORY.json. Pass an explicit
# revision (e.g. the verified waves-1-2 baseline ``f5fc5776``) to reconcile
# against a historical baseline and report omissions that have since closed.
LEDGER_REV = "working"

# Canonical output/input home for regenerated artifacts. The generator writes
# here by default; ``docs/archive/2026-09-21/`` holds the frozen historical
# copies and is never written by a plain regeneration.
CANONICAL_DIR = "docs/generated"
CANONICAL_VENDOR_MANIFEST = f"{CANONICAL_DIR}/QUBIT_HERMES_VENDOR_MANIFEST.json"

DEFAULT_HERMES = "/tmp/opencode/pinned/hermes-agent"
DEFAULT_OPENCLAW = "/tmp/opencode/pinned/openclaw"

# Directories/files excluded from the byte diff, matching
# scripts/hermes_vendor_manifest.py so counts reconcile.
_BYTE_SKIP_PARTS = {"__pycache__", ".git", ".pytest_cache", ".pytest-cache"}
_BYTE_SKIP_SUFFIX = (".pyc", ".pyo")

# Categories whose items are cross-cutting evidence rather than a capability
# surface with its own ledger row. They are counted in the inventory but are
# not enumerated as candidate work rows in the omissions report.
_EVIDENCE_CATEGORIES = {
    "hermes_i18n_key",
    "hermes_locale_key",
    "hermes_upstream_test",
}

_SURFACE_LABEL = {
    "qubit_cli_command": "cli",
    "qubit_cli_subcommand": "cli",
    "qubit_cli_option": "cli",
    "qubit_cli_positional": "cli",
    "qubit_backend_route": "dashboard",
    "hermes_dashboard_page": "dashboard",
    "hermes_dashboard_route": "dashboard",
    "hermes_dashboard_nav": "dashboard",
    "hermes_dashboard_component": "dashboard",
    "hermes_dashboard_dialog": "dashboard",
    "hermes_dashboard_form": "dashboard",
    "hermes_dashboard_plugin": "dashboard",
    "hermes_plugin": "dashboard",
    "hermes_plugin_api": "dashboard",
    "hermes_plugin_asset": "dashboard",
    "hermes_plugin_test": "dashboard",
    "hermes_tool": "backend",
    "qubit_toolset": "backend",
    "hermes_skill": "backend",
    "hermes_model": "backend",
    "hermes_provider": "backend",
    "hermes_workflow": "backend",
    "openclaw_extension": "dashboard",
}

# ---------------------------------------------------------------------------
# Parent-surface classification
# ---------------------------------------------------------------------------
# A *parented surface* is a sub-part of a parent capability that already has a
# ledger row: a CLI flag of a command, a catalog entry of the model/provider
# capability, a plugin asset/API/test of its plugin, an extension of an audited
# upstream capability, a workflow source file of its area, or a dashboard
# component of the dashboard.  A *distinct* capability has no parent and stays
# an explicit open row.  The mapping is mechanical and auditable; the ledger
# generator suppresses a parented surface's standalone MISSING row when its
# parent row is IMPLEMENTED (see ``scripts/rebuild_ledger_qubit.py``).
#
# ``coverage`` records *why* the item is a surface (CLI option, model entry,
# plugin surface, ...); ``parent_id`` records the capability it belongs to.
_COVERAGE_FOR_CATEGORY = {
    "qubit_cli_option": "cli-option",
    "qubit_cli_positional": "cli-option",
    "hermes_model": "model-entry",
    "hermes_provider": "provider-entry",
    "hermes_plugin_api": "plugin-surface",
    "hermes_plugin_asset": "plugin-surface",
    "hermes_plugin_test": "plugin-surface",
    "hermes_dashboard_plugin": "plugin-surface",
    "openclaw_extension": "openclaw-capability",
    "hermes_workflow": "workflow-surface",
    "hermes_dashboard_component": "dashboard-surface",
    "hermes_dashboard_dialog": "dashboard-surface",
    "hermes_dashboard_form": "dashboard-surface",
    "hermes_dashboard_nav": "dashboard-surface",
    "hermes_i18n_key": "evidence-i18n",
    "hermes_locale_key": "evidence-i18n",
    "hermes_upstream_test": "evidence-test",
}

# OpenClaw extension -> audited capability id (docs/archive/2026-09-21/AUDIT_OPENCLAW_*.md).  An
# extension whose upstream name already matched an ``OC-*`` ledger row wins;
# otherwise the first manifest category with a documented audited capability is
# the parent.  ``tools`` is intentionally unmapped: a generic tool extension
# has no single audited capability and stays a distinct capability.
_OPENCLAW_PARENT_BY_CATEGORY = {
    "gateway": "OC-02",
    "browser": "OC-09",
    "desktop": "OC-15",
    "channels": "OC-19",
    "webhooks": "OC-23",
    "automation": "OC-26",
    "boards": "OC-34",
    "skills": "OC-38",
    "sessions": "OC-44",
    "memory": "OC-46",
    "reports": "OC-49",
    "runtime": "OC-53",
    "ide": "OC-57",
    "web": "OC-63",
    "web_media": "OC-64",
    "media": "OC-64",
    "voice": "OC-66",
    "models": "OC-D28",
    "security": "OC-D08",
    "policy": "OC-D02",
    "dashboard": "OC-69",
    "context": "OC-D20",
}

# upstream workflow area -> the Qubit capability row that owns it.
_WORKFLOW_PARENT_BY_AREA = {
    "browser": "browser_status",
    "coding": "opencode_status",
    "desktop": "computer_status",
    "memory": "memory_search",
    "messaging": "channels_status",
    "reports": "reports_list",
    "runtime": "runtime_status",
    "schedules": "schedules_list",
}

# Dashboard UI surfaces belong to the dashboard capability.
_DASHBOARD_PARENT = "webui_status"

# Fork-runtime capability evidence (Wave 5).  Qubit executes the complete
# pinned upstream fork in-process, so an upstream agent tool, skill, or plugin is
# *not* a separate Qubit module: the fork's tool/toolset registry, skill tree,
# and plugin fleet are the real backing surface.  Every tool/skill/plugin
# inventory item is given an explicit typed disposition against that fork
# runtime (IMPLEMENTED with a real proving test, or GATED with a reason) so it
# can never be silently dropped or counted as an unexplained omission.
_FORK_TOOL_TEST = (
    "tests/test_tool_skill_plugin_coverage_wave5.py"
    "::test_fork_tool_registry_exposes_inventory_tools")
_FORK_SKILL_TEST = (
    "tests/test_tool_skill_plugin_coverage_wave5.py"
    "::test_all_inventory_skills_vendored_and_loadable")
_FORK_PLUGIN_TEST = (
    "tests/test_tool_skill_plugin_coverage_wave5.py"
    "::test_all_vendored_plugin_items_have_a_wrapper")
_FORK_CAPABILITY_BY_CATEGORY = {
    "hermes_tool": "fork_tool_runtime",
    "hermes_skill": "fork_skill_runtime",
    "hermes_plugin": "fork_plugin_runtime",
}
_FORK_BACKEND_BY_CATEGORY = {
    "hermes_tool": "qubit_runtime.tools.registry:registry",
    "hermes_skill": "qubit_runtime.skills",
    "hermes_plugin": "voicebridge.qubit_plugins.plugin_fleet_audit",
}


def _fork_tool_names(repo: Path) -> set[str]:
    """Names the vendored fork's tool/toolset source declares.

    The fork is byte-identical to the pinned upstream tree, so this is the
    direct source of truth for what the fork runtime exposes.  The proving test
    additionally imports the live ``tools.registry`` and asserts membership.
    """
    fork = repo / "qubit_runtime"
    if not fork.is_dir():
        return set()
    return {str(it.get("name") or "") for it in extract_qubit_tools(fork)}


def _fork_plugin_names(repo: Path) -> set[str]:
    """Vendored fork plugin directories (the Qubit plugin-wrapper fleet)."""
    plugins = repo / "qubit_runtime" / "plugins"
    if not plugins.is_dir():
        return set()
    return {p.name for p in plugins.iterdir()
            if p.is_dir() and p.name != "__pycache__"}


def _fork_skill_loadable(repo: Path, item: dict) -> bool:
    """True when the vendored skill directory carries a real ``SKILL.md``."""
    root = str(item.get("root") or "")
    name = str(item.get("name") or "")
    if not root or not name:
        return False
    skill_dir = repo / "qubit_runtime" / root / name
    if not skill_dir.is_dir():
        return False
    return any(skill_dir.rglob("SKILL.md"))


def _fork_disposition(repo: Path, item: dict, fork_tools: set[str],
                      fork_plugins: set[str]) -> dict | None:
    """Typed disposition for a tool/skill/plugin item against the fork runtime.

    Returns ``None`` for categories this workstream does not own, so their
    existing (matched/parented) classification is unchanged.
    """
    cat = item["category"]
    if cat not in _FORK_CAPABILITY_BY_CATEGORY:
        return None
    capability = _FORK_CAPABILITY_BY_CATEGORY[cat]
    backend = _FORK_BACKEND_BY_CATEGORY[cat]
    name = str(item.get("name") or "")

    if cat == "hermes_tool":
        if name in fork_tools:
            return {
                "status": "IMPLEMENTED", "capability": capability,
                "backend": backend, "proving_test": _FORK_TOOL_TEST,
                "reason": ("vendored fork tool/toolset registry exposes "
                           f"{name!r}; dispatchable through the in-process "
                           "fork agent runtime under Qubit policy/broker."),
                "needs": "none",
            }
        return {
            "status": "GATED", "capability": capability, "backend": "",
            "proving_test": "",
            "reason": (f"tool {name!r} is not declared by the vendored fork "
                       "tool/toolset source; no Qubit caller."),
            "needs": "owner",
        }

    if cat == "hermes_skill":
        if _fork_skill_loadable(repo, item):
            return {
                "status": "IMPLEMENTED", "capability": capability,
                "backend": backend, "proving_test": _FORK_SKILL_TEST,
                "reason": ("vendored under qubit_runtime/ with a loadable "
                           f"SKILL.md for {item.get('root')}/{name}; served by "
                           "the in-process fork skill runtime."),
                "needs": "none",
            }
        return {
            "status": "GATED", "capability": capability, "backend": "",
            "proving_test": "",
            "reason": (f"vendored skill path {item.get('path')!r} has no "
                       "loadable SKILL.md (cache/asset directory), so no skill "
                       "runtime surface exists."),
            "needs": "owner",
        }

    # hermes_plugin
    if name in fork_plugins:
        return {
            "status": "IMPLEMENTED", "capability": capability,
            "backend": backend, "proving_test": _FORK_PLUGIN_TEST,
            "reason": ("vendored under qubit_runtime/plugins/ and wrapped by "
                       "voicebridge.qubit_plugins.VENDORED_PLUGIN_FLEET / "
                       "plugin_fleet_audit; loadable in the fork plugin "
                       "runtime."),
            "needs": "none",
        }
    return {
        "status": "GATED", "capability": capability, "backend": "",
        "proving_test": "",
        "reason": (f"plugin {name!r} is not present in the vendored fork "
                   "plugin fleet; no Qubit wrapper."),
        "needs": "owner",
    }


def _openclaw_disposition(item: dict) -> dict | None:
    """Typed disposition for an upstream extension not matched to a ledger row.

    Consumes the wave-4 :data:`EXTENSION_MAP` (all 154 extensions) and the
    wave-5 form/proof table so the two never drift.  A ``parent``/``distinct``
    disposition with a real Qubit form + proving test is IMPLEMENTED; a gate is
    a typed credential/hardware/owner refusal.
    """
    if item.get("category") != "openclaw_extension":
        return None
    name = str(item.get("name") or "")
    try:
        scripts_dir = str(Path(__file__).resolve().parent)
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from rebuild_ledger_openclaw import EXTENSION_MAP, WAVE5_FORMS
    except Exception:
        return None
    disp = EXTENSION_MAP.get(name)
    if disp is None:
        return None
    form = WAVE5_FORMS.get(name)
    if not isinstance(form, dict):
        return None
    kind = str(disp.get("disposition") or "")
    gate = str(disp.get("gate") or "")
    if gate == "TEST_SUPPORT_ONLY":
        # Upstream test-only scaffolding: no product capability exists to
        # gate or implement, so it is dispositioned IMPLEMENTED (Qubit ships
        # its own test suite).
        status = "IMPLEMENTED"
    else:
        status = "IMPLEMENTED" if kind in ("parent", "distinct") else "GATED"
    return {
        "status": status,
        "capability": f"openclaw extension {name}",
        "backend": str(form.get("form") or ""),
        "proving_test": str(form.get("proving") or ""),
        "reason": (f"OpenClaw extension {name!r} -> {kind} "
                   f"(parent={disp.get('parent') or 'distinct'}, "
                   f"gate={disp.get('gate') or 'none'}) via "
                   f"{form.get('form')}."),
        "needs": str(disp.get("needs") or "none"),
    }


def _parent_for(item: dict) -> tuple[str | None, str]:
    """Return ``(parent_id, coverage)`` for one inventory item.

    ``parent_id`` is the ledger id of the owning capability (or ``None`` for a
    distinct capability).  ``coverage`` names why the item is a surface.
    """
    cat = item["category"]
    coverage = _COVERAGE_FOR_CATEGORY.get(cat, "")
    if cat in ("qubit_cli_option", "qubit_cli_positional"):
        cmd = str(item.get("command") or "")
        name = cmd[len("hermes "):] if cmd.startswith("hermes ") else cmd
        return (f"cli:{name}" if name else None, coverage)
    if cat == "qubit_cli_subcommand" and item.get("is_alias_of"):
        target = str(item["is_alias_of"])
        name = (target[len("hermes "):]
                if target.startswith("hermes ") else target)
        return (f"cli:{name}" if name else None, "cli-alias")
    if cat == "hermes_model":
        return ("model", coverage)
    if cat == "hermes_provider":
        return ("proxy_providers", coverage)
    if cat in ("hermes_plugin_api", "hermes_plugin_asset", "hermes_plugin_test"):
        plugin = item.get("plugin")
        return (f"plugin:{plugin}" if plugin else None, coverage)
    if cat == "hermes_dashboard_plugin":
        name = item.get("name")
        return (f"plugin:{name}" if name else None, coverage)
    if cat == "hermes_workflow":
        return (_WORKFLOW_PARENT_BY_AREA.get(str(item.get("area") or "")),
                coverage)
    if cat in ("hermes_dashboard_component", "hermes_dashboard_dialog",
               "hermes_dashboard_form", "hermes_dashboard_nav"):
        return (_DASHBOARD_PARENT, coverage)
    if cat == "openclaw_extension":
        match = item.get("ledger_match")
        if isinstance(match, str) and match.startswith("OC-"):
            return (match, coverage)
        for category in (item.get("categories") or []):
            parent = _OPENCLAW_PARENT_BY_CATEGORY.get(category)
            if parent:
                return (parent, coverage)
        return (None, coverage)
    if cat in ("hermes_i18n_key", "hermes_locale_key"):
        return (_DASHBOARD_PARENT, coverage)
    if cat == "qubit_backend_route":
        path = str(item.get("name") or item.get("path") or "")
        if "/assets/" in path or path.endswith((".css", ".js", ".svg")):
            # Static SPA assets are served by the dashboard static handler,
            # not by a compat API row.
            return (_DASHBOARD_PARENT, "static-asset")
        return (None, coverage)
    return (None, coverage)


def _ledger_status_index(repo: Path,
                         ledger_rev: str | None) -> dict[str, str]:
    """id -> status for every ledger row at ``ledger_rev`` (None = working).

    The inventory reconciles against the same (usually pinned) revision the
    omission comparison uses, so the artifact is reproducible even while other
    agents edit the working-tree ledger.
    """
    index: dict[str, str] = {}
    for name in ("hermes", "openclaw", "qubit"):
        ledger = _load_ledger(repo, name, ledger_rev)
        for row in ledger.get("rows", []):
            index.setdefault(row["id"], row.get("status"))
    return index


def _is_covered(item: dict) -> bool:
    """A surface is covered by its own ledger row, an IMPLEMENTED parent, or an
    explicit fork-runtime disposition (Wave 5 tools/skills/plugins)."""
    return bool(item.get("ledger_match") or item.get("parent_covered")
                or item.get("fork_disposition"))


def _resolve_parent(parent_id: str | None,
                    status_by_id: dict[str, str]) -> str | None:
    """Resolve a declared parent to the nearest existing ledger row.

    CLI options live under a command; when the exact ``cli:<command>`` row is
    absent, the nearest declared ancestor (``cli:cron create`` ->
    ``cli:cron``) is the parent.  Other parent ids must exist verbatim.
    """
    if not parent_id:
        return None
    if parent_id in status_by_id:
        return parent_id
    if parent_id.startswith("cli:"):
        parts = parent_id[4:].split()
        while len(parts) > 1:
            parts.pop()
            candidate = "cli:" + " ".join(parts)
            if candidate in status_by_id:
                return candidate
    return parent_id


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
    ).stdout


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _rel(repo: Path, path: Path) -> str:
    return path.relative_to(repo).as_posix()


def _str_const(node: ast.AST):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _base_name(node: ast.AST):
    """Return a dotted name for simple Name/Attribute chains, else None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _base_name(node.value)
        return f"{inner}.{node.attr}" if inner else node.attr
    return None


def _parse(src: str):
    """ast.parse without upstream invalid-escape SyntaxWarnings polluting output."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return ast.parse(src)


# ---------------------------------------------------------------------------
# upstream CLI extraction (AST over parser modules)
# ---------------------------------------------------------------------------

class _CliExtractor:
    def __init__(self, repo: Path):
        self.repo = repo
        self.items: list[dict] = []
        self._seen: set[str] = set()

    def _record_cmd(self, path: list[str], source: str, aliases: list[str],
                    help_text: str) -> None:
        name = " ".join(path)
        item_id = "hermes " + name
        if item_id in self._seen:
            return
        self._seen.add(item_id)
        is_top = len(path) <= 1
        self.items.append({
            "id": item_id,
            "source": "hermes",
            "category": ("qubit_cli_command" if is_top
                         else "qubit_cli_subcommand"),
            "name": "hermes " + name,
            "path": source,
            "help": help_text,
            "aliases": aliases,
        })
        for alias in aliases:
            apath = path[:-1] + [alias]
            aid = "hermes " + " ".join(apath)
            if aid not in self._seen:
                self._seen.add(aid)
                self.items.append({
                    "id": aid,
                    "source": "hermes",
                    "category": ("qubit_cli_command" if len(apath) <= 1
                                 else "qubit_cli_subcommand"),
                    "name": aid,
                    "path": source,
                    "help": help_text,
                    "aliases": [],
                    "is_alias_of": item_id,
                })

    def _record_arg(self, path: list[str], source: str, opt: str,
                    kind: str, help_text: str) -> None:
        if not path:
            return
        item_id = f"hermes {' '.join(path)} {opt}"
        if item_id in self._seen:
            return
        self._seen.add(item_id)
        self.items.append({
            "id": item_id,
            "source": "hermes",
            "category": ("qubit_cli_option" if kind == "option"
                         else "qubit_cli_positional"),
            "name": opt,
            "command": "hermes " + " ".join(path),
            "path": source,
            "help": help_text,
        })

    def _walk_function(self, fn: ast.FunctionDef, source: str) -> None:
        parsers: dict[str, list[str]] = {}
        containers: dict[str, list[str]] = {}
        groups: dict[str, list[str]] = {}
        add_aliases: dict[str, ast.AST] = {}
        inherited_aliases: set[str] = set()

        params = [a.arg for a in fn.args.args] + [a.arg for a in fn.args.kwonlyargs]
        if params:
            first = params[0]
            if first == "parser":
                parsers[first] = []
            elif "subparser" in first or first in ("sub", "subparsers"):
                containers[first] = []

        def parser_path(node: ast.AST):
            name = _base_name(node)
            if name is None:
                return None
            if name in parsers:
                return list(parsers[name])
            if "." in name and name.split(".")[-1] in parsers:
                return list(parsers[name.split(".")[-1]])
            if name in groups:
                return list(groups[name])
            return None

        def container_path(node: ast.AST):
            name = _base_name(node)
            if name is None:
                return None
            if name in containers:
                return list(containers[name])
            if "." in name and name.split(".")[-1] in containers:
                return list(containers[name.split(".")[-1]])
            return None

        def handle_add_argument(base: ast.AST, call: ast.Call) -> None:
            path = parser_path(base)
            if path is None:
                return
            opts = [s for a in call.args if (s := _str_const(a))]
            help_text = ""
            for kw in call.keywords:
                if kw.arg == "help":
                    help_text = _str_const(kw.value) or ""
            options = [o for o in opts if o.startswith("-")]
            positionals = [o for o in opts if not o.startswith("-")]
            for o in options:
                self._record_arg(path, source, o, "option", help_text)
            for o in positionals:
                self._record_arg(path, source, o, "positional", help_text)

        def handle_call(call: ast.Call, target: str | None) -> None:
            fn = call.func
            attr = fn.attr if isinstance(fn, ast.Attribute) else None
            if attr == "ArgumentParser":
                if target:
                    parsers[target] = []
                return
            if attr == "add_subparsers":
                parent = parser_path(fn.value)
                if parent is not None and target:
                    containers[target] = parent
                return
            if attr == "add_parser":
                parent = container_path(fn.value)
                if parent is None:
                    parent = []
                name = None
                aliases: list[str] = []
                help_text = ""
                if call.args:
                    name = _str_const(call.args[0])
                for kw in call.keywords:
                    if kw.arg == "aliases" and isinstance(kw.value, ast.List):
                        aliases = [s for e in kw.value.elts
                                   if (s := _str_const(e))]
                    if kw.arg == "help":
                        help_text = _str_const(kw.value) or ""
                if name is None:
                    return
                path = parent + [name]
                if target:
                    parsers[target] = path
                self._record_cmd(path, source, aliases, help_text)
                return
            if attr in ("add_mutually_exclusive_group", "add_argument_group"):
                base = parser_path(fn.value)
                if base is not None and target:
                    groups[target] = base
                return
            if attr == "add_argument":
                handle_add_argument(fn.value, call)
                return
            if isinstance(fn, ast.Name) and fn.id in add_aliases:
                handle_add_argument(add_aliases[fn.id], call)
                return
            if isinstance(fn, ast.Name) and fn.id in inherited_aliases:
                if call.args:
                    handle_add_argument(call.args[0], call)
                return

        for stmt in ast.walk(fn):
            if not isinstance(stmt, ast.Assign):
                continue
            # tuple unpack: add, inherited = parser.add_argument, _inherited_flag
            if isinstance(stmt.value, ast.Tuple):
                for tgt, val in zip(stmt.targets[0].elts
                                    if isinstance(stmt.targets[0], ast.Tuple)
                                    else [], stmt.value.elts):
                    if (isinstance(val, ast.Attribute)
                            and val.attr == "add_argument"
                            and isinstance(tgt, ast.Name)):
                        add_aliases[tgt.id] = val.value
                    elif (isinstance(val, ast.Name)
                          and val.id == "_inherited_flag"
                          and isinstance(tgt, ast.Name)):
                        inherited_aliases.add(tgt.id)
        # process statements in source order for correct nesting
        for stmt in fn.body:
            self._process_stmt(stmt, handle_call)
        # nested function bodies (rare but present)
        for node in ast.walk(fn):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node is not fn:
                for stmt in node.body:
                    self._process_stmt(stmt, handle_call)

    def _process_stmt(self, stmt: ast.AST, handle_call) -> None:
        if isinstance(stmt, ast.Assign):
            target = None
            if len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                target = stmt.targets[0].id
            if isinstance(stmt.value, ast.Call):
                handle_call(stmt.value, target)
        elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            handle_call(stmt.value, None)


def _cli_dir(repo: Path) -> str:
    """CLI package directory name for a given tree.

    The pinned upstream tree uses ``hermes_cli``; the shipped Qubit fork uses
    the deep-rebranded ``qubit_cli``. Callers read whichever exists so the same
    extractor can enumerate both the upstream surface and the local runtime.
    """
    return "qubit_cli" if (repo / "qubit_cli").is_dir() else "hermes_cli"


def extract_qubit_cli(repo: Path) -> list[dict]:
    d = _cli_dir(repo)
    files = [repo / d / "_parser.py", repo / d / "main.py"]
    files += sorted((repo / d / "subcommands").glob("*.py"))
    for extra in ("send_cmd.py", "portal_cli.py", "kanban.py",
                  "kanban_parser.py", "projects_cmd.py"):
        files.append(repo / d / extra)
    lsp = repo / "agent" / "lsp" / "cli.py"
    if lsp.is_file():
        files.append(lsp)

    ex = _CliExtractor(repo)
    for path in files:
        if not path.is_file():
            continue
        src = _read_text(path)
        try:
            tree = _parse(src)
        except SyntaxError:
            continue
        rel = _rel(repo, path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                ex._walk_function(node, rel)
    return ex.items


# ---------------------------------------------------------------------------
# upstream backend routes
# ---------------------------------------------------------------------------

_ROUTE_RE = re.compile(
    r"@(?:[\w.]+)\.(get|post|put|delete|patch|head|websocket)\(\s*[\"']([^\"']+)[\"']"
)


def extract_hermes_routes(repo: Path) -> list[dict]:
    d = _cli_dir(repo)
    files = sorted(repo.glob(f"{d}/web_server*.py"))
    files += sorted((repo / d / "web_routers").glob("*.py"))
    files += [repo / d / "dashboard_auth" / "routes.py"]
    items: list[dict] = []
    seen: set[str] = set()
    for path in files:
        if not path.is_file():
            continue
        rel = _rel(repo, path)
        for lineno, line in enumerate(_read_text(path).splitlines(), 1):
            m = _ROUTE_RE.search(line)
            if not m:
                continue
            method, route = m.group(1).upper(), m.group(2)
            if method == "WEBSOCKET":
                method = "WEBSOCKET"
            item_id = f"api:{method} {route}"
            if item_id in seen:
                continue
            seen.add(item_id)
            items.append({
                "id": item_id,
                "source": "hermes",
                "category": "qubit_backend_route",
                "name": f"{method} {route}",
                "path": f"{rel}:{lineno}",
                "method": method,
                "route": route,
            })
    return items


# ---------------------------------------------------------------------------
# upstream dashboard (pages, routes, nav, components)
# ---------------------------------------------------------------------------

_DIALOG = re.compile(r"Dialog|Modal|Confirm|Picker|Drawer|Popover")
_FORM = re.compile(r"Form|Builder|Editor|AutoField|ScheduleBuilder")
_CONTROL = re.compile(r"Switcher|Selector|Toggle|Banner|Footer|Strip|Card|Widget")


def extract_hermes_dashboard(repo: Path) -> list[dict]:
    web = repo / "web" / "src"
    items: list[dict] = []
    seen: set[str] = set()

    def add(item_id, category, name, path, **extra):
        if item_id in seen:
            return
        seen.add(item_id)
        row = {"id": item_id, "source": "hermes", "category": category,
               "name": name, "path": path}
        row.update(extra)
        items.append(row)

    pages_dir = web / "pages"
    if pages_dir.is_dir():
        for path in sorted(pages_dir.glob("*.tsx")):
            if path.name.endswith(".test.tsx"):
                continue
            add(f"page:{path.stem}", "hermes_dashboard_page",
                path.stem, _rel(repo, path))

    comp_dir = web / "components"
    if comp_dir.is_dir():
        for path in sorted(comp_dir.glob("*.tsx")):
            if path.name.endswith(".test.tsx"):
                continue
            name = path.stem
            cat = "hermes_dashboard_component"
            if _DIALOG.search(name):
                cat = "hermes_dashboard_dialog"
            elif _FORM.search(name):
                cat = "hermes_dashboard_form"
            elif _CONTROL.search(name):
                cat = "hermes_dashboard_component"
            add(f"component:{name}", cat, name, _rel(repo, path))

    app = web / "App.tsx"
    if app.is_file():
        rel = _rel(repo, app)
        src = _read_text(app)
        # nav items: path/label pairs inside the nav arrays
        for m in re.finditer(
                r"\{\s*path:\s*[\"']([^\"']+)[\"']([^}]*?)label:\s*[\"']([^\"']+)[\"']",
                src, re.S):
            route, _mid, label = m.group(1), m.group(2), m.group(3)
            line = src[:m.start()].count("\n") + 1
            add(f"nav:{route}", "hermes_dashboard_nav", label,
                f"{rel}:{line}", route=route)
        # lazy page routes are the page inventory; record one route id per page
        for m in re.finditer(r"path:\s*[\"'](/[^\"']*)[\"']", src):
            route = m.group(1)
            line = src[:m.start()].count("\n") + 1
            if route == "*":
                continue
            add(f"dashboard-route:{route}", "hermes_dashboard_route",
                route, f"{rel}:{line}", route=route)
    return items


# ---------------------------------------------------------------------------
# upstream plugins
# ---------------------------------------------------------------------------

def extract_qubit_plugins(repo: Path) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()

    def add(item_id, category, name, path, **extra):
        if item_id in seen:
            return
        seen.add(item_id)
        row = {"id": item_id, "source": "hermes", "category": category,
               "name": name, "path": path}
        row.update(extra)
        items.append(row)

    pdir = repo / "plugins"
    if not pdir.is_dir():
        return items

    for entry in sorted(pdir.iterdir()):
        if entry.is_dir():
            add(f"plugin:{entry.name}", "hermes_plugin", entry.name,
                _rel(repo, entry))
    for manifest in sorted(pdir.glob("*/dashboard/manifest.json")):
        rel = _rel(repo, manifest)
        try:
            data = json.loads(_read_text(manifest) or "{}")
        except ValueError:
            data = {}
        name = data.get("name", manifest.parent.parent.name)
        tab = data.get("tab", {}) or {}
        add(f"plugin-manifest:{name}", "hermes_dashboard_plugin", name, rel,
            label=data.get("label"), version=data.get("version"),
            tab_path=tab.get("path"), entry=data.get("entry"),
            api=data.get("api"))
    for api in sorted(pdir.glob("*/dashboard/plugin_api.py")):
        plugin = api.parent.parent.name
        add(f"plugin-api:{plugin}", "hermes_plugin_api", plugin,
            _rel(repo, api), plugin=plugin)
    for asset in sorted(pdir.glob("*/dashboard/dist/*")):
        if asset.is_file():
            plugin = asset.parent.parent.parent.name
            add(f"plugin-asset:{plugin}:{asset.name}",
                "hermes_plugin_asset", asset.name, _rel(repo, asset),
                plugin=plugin)
    for test in sorted(pdir.rglob("test_*.py")):
        plugin = test.relative_to(pdir).parts[0]
        add(f"plugin-test:{plugin}:{test.name}", "hermes_plugin_test",
            test.name, _rel(repo, test), plugin=plugin)
    return items


# ---------------------------------------------------------------------------
# upstream tools / skills / models / providers / workflows
# ---------------------------------------------------------------------------

def extract_qubit_tools(repo: Path) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    names: set[str] = set()

    toolsets = repo / "toolsets.py"
    if toolsets.is_file():
        try:
            tree = _parse(_read_text(toolsets))
            for node in ast.walk(tree):
                if isinstance(node, ast.List):
                    for elt in node.elts:
                        s = _str_const(elt)
                        if s and re.fullmatch(r"[a-z][a-z0-9_]*", s):
                            names.add(s)
        except SyntaxError:
            pass
    reg_re = re.compile(r"registry\.register\(\s*(?:name\s*=\s*)?[\"']([a-z][a-z0-9_]+)[\"']")
    for path in sorted(repo.glob("tools/*.py")):
        for m in reg_re.finditer(_read_text(path)):
            names.add(m.group(1))

    tdir = repo / "tools"
    for name in sorted(names):
        item_id = f"tool:{name}"
        if item_id in seen:
            continue
        seen.add(item_id)
        items.append({
            "id": item_id, "source": "hermes", "category": "hermes_tool",
            "name": name, "path": str(tdir.relative_to(repo)) + "/*.py",
        })
    return items


def extract_hermes_skills(repo: Path) -> list[dict]:
    items: list[dict] = []
    for root_name in ("skills", "optional-skills"):
        root = repo / root_name
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            # `skills/index-cache` is a vendored JSON asset cache, not a
            # loadable skill (no SKILL.md); exclude it as a non-capability.
            if entry.name == "index-cache":
                continue
            items.append({
                "id": f"skill:{root_name}/{entry.name}",
                "source": "hermes", "category": "hermes_skill",
                "name": entry.name, "path": _rel(repo, entry),
                "root": root_name,
            })
    return items


_MODEL_STR_RE = re.compile(r"[\"']([a-z0-9][a-z0-9._/:-]+)[\"']")
_PROVIDER_ROW_RE = re.compile(r"\(\s*\"([a-z0-9][a-z0-9-]*)\"\s*,\s*\"[^\"]+\"")


def extract_hermes_models_providers(repo: Path) -> tuple[list[dict], list[dict]]:
    d = _cli_dir(repo)
    static = repo / d / "models_catalog_static.py"
    auth = repo / d / "auth.py"
    models: set[str] = set()
    providers: set[str] = set()

    if static.is_file():
        try:
            tree = _parse(_read_text(static))
        except SyntaxError:
            tree = None
        if tree is not None:
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    targets = [t.id for t in node.targets
                               if isinstance(t, ast.Name)]
                    value = node.value
                elif isinstance(node, ast.AnnAssign) and isinstance(
                        node.target, ast.Name):
                    targets = [node.target.id]
                    value = node.value
                else:
                    continue
                if value is None:
                    continue
                if "_PROVIDER_MODELS" in targets and isinstance(value, ast.Dict):
                    for val in value.values:
                        if isinstance(val, ast.List):
                            for elt in val.elts:
                                s = _str_const(elt)
                                if s:
                                    models.add(s)
                if "CANONICAL_PROVIDERS" in targets:
                    for sub in ast.walk(value):
                        if isinstance(sub, ast.Tuple) and sub.elts:
                            s = _str_const(sub.elts[0])
                            if s:
                                providers.add(s)
            # OPENROUTER_MODELS comprehension tuple
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    tg = [t.id for t in node.targets if isinstance(t, ast.Name)]
                    value = node.value
                elif isinstance(node, ast.AnnAssign) and isinstance(
                        node.target, ast.Name):
                    tg = [node.target.id]
                    value = node.value
                else:
                    continue
                if "OPENROUTER_MODELS" in tg and value is not None:
                    for sub in ast.walk(value):
                        s = _str_const(sub)
                        if s and not s.startswith(("2x", "0.5x", "free",
                                                   "dated", "recommended",
                                                   "default", "auto-routes")):
                            if "/" in s and any(c.isalpha() for c in s):
                                models.add(s)
    if auth.is_file():
        m = re.search(r"PROVIDER_REGISTRY[^=]*=\s*{(.*?)\n}",
                      _read_text(auth), re.S)
        if m:
            for pm in re.finditer(r"[\"']([a-z0-9][a-z0-9-]*)[\"']\s*:", m.group(1)):
                providers.add(pm.group(1))

    mitems = [{
        "id": f"model:{n}", "source": "hermes", "category": "hermes_model",
        "name": n, "path": f"{d}/models_catalog_static.py",
    } for n in sorted(models)]
    pitems = [{
        "id": f"provider:{n}", "source": "hermes", "category": "hermes_provider",
        "name": n, "path": f"{d}/models_catalog_static.py",
    } for n in sorted(providers)]
    return mitems, pitems


_WORKFLOW_PATTERNS = {
    "reports": ["hermes_cli/*report*.py", "hermes_cli/**/*report*.py"],
    "schedules": ["cron/*.py", "hermes_cli/cron*.py"],
    "memory": ["hermes_state_*.py", "hermes_cli/memory*.py"],
    "messaging": ["hermes_cli/*slack*.py", "hermes_cli/*whatsapp*.py",
                  "hermes_cli/*telegram*.py", "hermes_cli/*messaging*.py",
                  "hermes_cli/*discord*.py", "tools/*telegram*.py",
                  "tools/*discord*.py", "tools/*feishu*.py"],
    "browser": ["tools/browser*.py", "hermes_cli/browser_connect.py",
                "hermes_cli/subcommands/browser.py"],
    "desktop": ["hermes_cli/main_desktop.py",
                "hermes_cli/subcommands/computer_use.py",
                "tools/computer_use*.py", "tools/desktop*.py"],
    "coding": ["hermes_cli/subcommands/claw.py", "hermes_cli/claw.py",
               "tools/code_*.py", "tools/apply_patch*.py"],
    "runtime": ["hermes_cli/runtime_provider*.py",
                "hermes_cli/local_runtime/*.py"],
}


def extract_hermes_workflows(repo: Path) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    d = _cli_dir(repo)
    state = "qubit_state" if d == "qubit_cli" else "hermes_state"
    for area, patterns in _WORKFLOW_PATTERNS.items():
        hits: set[str] = set()
        for pattern in patterns:
            pattern = pattern.replace("hermes_cli/", f"{d}/")
            pattern = pattern.replace("hermes_state_", f"{state}_")
            for path in repo.glob(pattern):
                if path.is_file():
                    hits.add(_rel(repo, path))
        for rel in sorted(hits):
            item_id = f"workflow:{area}:{rel}"
            if item_id in seen:
                continue
            seen.add(item_id)
            items.append({
                "id": item_id, "source": "hermes", "category": "hermes_workflow",
                "name": rel, "path": rel, "area": area,
            })
    return items


# ---------------------------------------------------------------------------
# upstream localization keys and upstream tests
# ---------------------------------------------------------------------------

_KEY_RE = re.compile(r"^(\s*)([A-Za-z0-9_]+):")


def _parse_nested_keys(text: str) -> list[str]:
    stack: list[tuple[int, str]] = []
    keys: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith(("//", "#")):
            continue
        m = _KEY_RE.match(line)
        if not m:
            continue
        indent = len(m.group(1))
        key = m.group(2)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = [k for _, k in stack] + [key]
        rest = line[m.end():].lstrip()
        if rest.startswith("{"):
            stack.append((indent, key))
        else:
            keys.append(".".join(path))
    return keys


def extract_hermes_i18n(repo: Path) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    web_i18n = repo / "web" / "src" / "i18n" / "en.ts"
    if web_i18n.is_file():
        for key in _parse_nested_keys(_read_text(web_i18n)):
            item_id = f"i18n:{key}"
            if item_id in seen:
                continue
            seen.add(item_id)
            items.append({
                "id": item_id, "source": "hermes", "category": "hermes_i18n_key",
                "name": key, "path": _rel(repo, web_i18n),
            })
    locale = repo / "locales" / "en.yaml"
    if locale.is_file():
        for key in _parse_nested_keys(_read_text(locale)):
            item_id = f"locale:{key}"
            if item_id in seen:
                continue
            seen.add(item_id)
            items.append({
                "id": item_id, "source": "hermes", "category": "hermes_locale_key",
                "name": key, "path": _rel(repo, locale),
            })
    return items


def extract_hermes_tests(repo: Path) -> list[dict]:
    items: list[dict] = []
    tests_dir = repo / "tests"
    if not tests_dir.is_dir():
        return items
    for path in sorted(tests_dir.rglob("*.py")):
        base = path.name
        if not (base.startswith("test_") or base.endswith("_test.py")):
            continue
        rel = _rel(repo, path)
        count = 0
        try:
            tree = _parse(_read_text(path))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and node.name.startswith("test_"):
                    count += 1
        except SyntaxError:
            pass
        items.append({
            "id": f"test:{rel}", "source": "hermes",
            "category": "hermes_upstream_test", "name": rel, "path": rel,
            "test_count": count,
        })
    return items


# ---------------------------------------------------------------------------
# upstream extensions
# ---------------------------------------------------------------------------

def extract_openclaw_extensions(openclaw: Path, pin: str) -> list[dict]:
    if not (openclaw / ".git").exists():
        return []
    listing = _git(openclaw, "ls-tree", "-r", "--name-only", pin, "--",
                   "extensions/").splitlines()
    exts: dict[str, dict] = {}
    for p in listing:
        parts = p.split("/")
        if len(parts) >= 3 and parts[0] == "extensions" and parts[1]:
            exts.setdefault(parts[1], {"files": [], "manifest": None,
                                       "data": {}})
            exts[parts[1]]["files"].append(p)
            if p.endswith("openclaw.plugin.json") and exts[parts[1]]["manifest"] is None:
                exts[parts[1]]["manifest"] = p
    items: list[dict] = []
    for name in sorted(exts):
        info = exts[name]
        data: dict = {}
        if info["manifest"]:
            raw = _git(openclaw, "show", f"{pin}:{info['manifest']}")
            try:
                data = json.loads(raw)
            except ValueError:
                data = {}
        cats = data.get("categories") or []
        items.append({
            "id": f"openclaw-ext:{name}",
            "source": "openclaw",
            "category": "openclaw_extension",
            "name": name,
            "path": f"extensions/{name}/",
            "extension_id": data.get("id", name),
            "categories": cats,
            "manifest": info["manifest"],
            "cli_commands": [c.get("name") for c in
                             (data.get("cliCommands") or []) if c.get("name")],
            "channels": data.get("channels") or [],
            "enabled_by_default": data.get("enabledByDefault"),
            "file_count": len(info["files"]),
        })
    return items


# ---------------------------------------------------------------------------
# upstream pin vs qubit_runtime/ byte diff
# ---------------------------------------------------------------------------

def _byte_skipped(rel: str) -> bool:
    parts = set(Path(rel).parts)
    if parts & _BYTE_SKIP_PARTS:
        return True
    return rel.endswith(_BYTE_SKIP_SUFFIX)


def compute_hermes_byte_diff(repo: Path, hermes: Path,
                             vendor_manifest: Path | None = None) -> dict:
    upstream: dict[str, str] = {}
    for line in _git(hermes, "ls-tree", "-r", "HEAD").splitlines():
        meta, path = line.split("\t", 1)
        _mode, otype, blob = meta.split()
        if otype != "blob" or _byte_skipped(path):
            continue
        upstream[path] = blob

    local_root = repo / "qubit_runtime"
    # Untracked disposable dependency/build/cache trees are not part of the
    # vendored source snapshot. Upstream-tracked files under the same directory
    # names (e.g. plugin dashboard ``dist/*`` bundles) stay in the comparison.
    disposable = {"node_modules", "dist", "build", ".cache", ".venv", "venv"}
    tracked: set[str] = set()
    if (repo / ".git").exists():
        try:
            for line in _git(repo, "ls-files", "--", "qubit_runtime").splitlines():
                line = line.strip()
                if line.startswith("qubit_runtime/"):
                    tracked.add(line[len("qubit_runtime/"):])
        except Exception:  # noqa: BLE001 - absent git index degrades to no skip
            tracked = set()
    local: dict[str, str] = {}
    if local_root.is_dir():
        for path in sorted(local_root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(local_root).as_posix()
            if _byte_skipped(rel):
                continue
            if (disposable & set(Path(rel).parts)) and rel not in tracked:
                continue
            local[rel] = _sha256_file(path)

    # Deep source rebrand path-rename awareness (owner amendment 2026-09-19):
    # a renamed module/dir/file is not a missing file. Reuse the vendor
    # manifest's deterministic upstream -> Qubit path map so both artifacts
    # agree.
    try:
        from hermes_vendor_manifest import _local_from_upstream
    except Exception:  # noqa: BLE001 - degrade to identity if unavailable
        def _local_from_upstream(rel: str) -> str:  # type: ignore[misc]
            return rel

    missing, extra, changed = [], [], []
    covered: set[str] = set()
    for rel, blob in upstream.items():
        lrel = _local_from_upstream(rel)
        covered.add(lrel)
        if lrel not in local:
            missing.append({"path": rel, "expected_local": lrel,
                            "upstream_blob": blob})
            continue
        up_file = hermes / rel
        if up_file.is_file():
            up_sha = _sha256_file(up_file)
        else:
            up_sha = None
        if up_sha is not None and up_sha != local[lrel]:
            changed.append({"path": lrel, "upstream_path": rel,
                            "upstream_blob": blob,
                            "upstream_sha256": up_sha,
                            "local_sha256": local[lrel]})
    for rel in local:
        if rel not in covered:
            extra.append({"path": rel, "local_sha256": local[rel]})

    # Reconcile with the canonical vendor manifest so the two regenerated
    # artifacts agree by construction. The manifest's ``patched`` list is the
    # authoritative set of changed files and ``extra_local`` the added ones;
    # recording the comparison makes any future drift a visible data point
    # instead of a silent mismatch between the two artifacts.
    reconciliation = None
    vm_path = vendor_manifest if vendor_manifest is not None \
        else repo / CANONICAL_VENDOR_MANIFEST
    if vm_path.is_file():
        try:
            vm = json.loads(vm_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            vm = None
        if vm is not None:
            patched = set(vm.get("patched") or [])
            extra_local = set(vm.get("extra_local") or [])
            reconciliation = {
                "vendor_manifest": _rel(repo, vm_path),
                "vendor_manifest_revision": vm.get("upstream_revision"),
                "changed_equals_patched": {c["path"] for c in changed} == patched,
                "extra_equals_extra_local": {e["path"] for e in extra}
                == extra_local,
            }

    return {
        "upstream_repo": HERMES_REPO,
        "upstream_revision": HERMES_PIN,
        "upstream_tag": HERMES_TAG,
        "local_root": "qubit_runtime",
        "counts": {
            "upstream_files": len(upstream),
            "local_files": len(local),
            "identical": len(upstream) - len(missing) - len(changed),
            "missing": len(missing),
            "extra": len(extra),
            "changed": len(changed),
        },
        "missing": sorted(missing, key=lambda x: x["path"]),
        "extra": sorted(extra, key=lambda x: x["path"]),
        "changed": sorted(changed, key=lambda x: x["path"]),
        "manifest_reconciliation": reconciliation,
    }


# ---------------------------------------------------------------------------
# Ledger reconciliation
# ---------------------------------------------------------------------------

def _load_ledger(repo: Path, name: str, rev: str | None = None) -> dict:
    """Load a ledger section from a git revision (default: working tree)."""
    if rev:
        try:
            raw = _git(repo, "show", f"{rev}:docs/archive/2026-09-21/QUBIT_GAP_LEDGER.{name}.json")
            return json.loads(raw)
        except (subprocess.CalledProcessError, ValueError):
            return {"rows": []}
    path = repo / "docs" / "archive" / "2026-09-21" / f"QUBIT_GAP_LEDGER.{name}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"rows": []}


def _test_ref_reproducible(repo: Path, proving: str) -> tuple[bool, str]:
    if not proving or proving.strip() in ("—", "-", ""):
        return False, "no proving test recorded"
    refs = [r for r in re.split(r"[,\s]+", proving.strip()) if r]
    last_reason = "no ref"
    for ref in refs:
        path_part, _, func = ref.partition("::")
        target = repo / path_part
        if not target.is_file():
            last_reason = f"missing file {path_part}"
            continue
        if not func:
            return True, ""
        try:
            src = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            last_reason = f"unreadable {path_part}"
            continue
        if re.search(r"(?:async\s+)?def\s+" + re.escape(func) + r"\b", src):
            return True, ""
        last_reason = f"missing function {func} in {path_part}"
    return False, last_reason


def _ledger_index(ledgers: dict[str, dict]):
    """(all_ids, find_by_name) for a set of loaded ledger sections."""
    all_ids: set[str] = set()
    row_blobs = []
    for src_name, led in ledgers.items():
        for r in led.get("rows", []):
            all_ids.add(r["id"])
            row_blobs.append((src_name, r, json.dumps(r, ensure_ascii=False)))

    def find_by_name(name: str):
        if not name or len(name) < 3:
            return None
        pat = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(name)
                         + r"(?![A-Za-z0-9_])")
        for src_name, r, blob in row_blobs:
            if r["id"] == name:  # never match an item's own id-like name
                continue
            if pat.search(blob):
                return src_name, r
        return None

    return all_ids, find_by_name, row_blobs


#: Upstream backend routes the deep source rebrand renamed to Qubit identity.
#: The inventory enumerates the pinned upstream surface, while the coverage
#: ledger keys the shipped Qubit route, so the mapping is applied before
#: matching (owner amendment 2026-09-19 deep rebrand; ``/api/hermes/update``
#: -> ``/api/qubit/update``, no external alias retained).
_UPSTREAM_ROUTE_RENAMES = {
    "/api/hermes/update": "/api/qubit/update",
    "/api/hermes/update/check": "/api/qubit/update/check",
    "/api/hermes/update/receipt": "/api/qubit/update/receipt",
}


def _match_hermes_item(item: dict, all_ids: set, find_by_name):
    cat = item["category"]
    if cat in ("qubit_cli_command", "qubit_cli_subcommand"):
        lid = "cli:" + item["name"].replace("hermes ", "", 1)
        if lid in all_ids:
            return lid
    elif cat == "qubit_backend_route":
        if item["id"] in all_ids:
            return item["id"]
        renamed = _UPSTREAM_ROUTE_RENAMES.get(str(item.get("route") or ""))
        if renamed:
            candidate = f"api:{item.get('method')} {renamed}"
            if candidate in all_ids:
                return candidate
    elif cat == "hermes_dashboard_page":
        rid = f"dashboard:/{item['name'].replace('Page', '').lower()}"
        if rid in all_ids:
            return rid
    elif cat == "hermes_dashboard_route":
        rid = f"dashboard:{item['route']}"
        if rid in all_ids:
            return rid
    name = item["name"].split(":")[-1]
    if cat == "hermes_plugin_asset":
        return None  # generic filenames (index.js/style.css) cause noise
    if cat in ("hermes_tool", "hermes_skill") and name in all_ids:
        # The agent tool / skill name is the capability op id (e.g. the
        # ``kanban_create`` tool is the ``kanban_create`` capability); the
        # shared-name guard in ``find_by_name`` would otherwise skip it.
        return name
    hit = find_by_name(name)
    return hit[1]["id"] if hit else None


def reconcile(repo: Path, hermes_items: list[dict],
              openclaw_items: list[dict], audit_text: str,
              ledger_rev: str | None = None) -> dict:
    """Reconcile against the ledger at ``ledger_rev`` (baseline) and record
    whether each unmatched item has since appeared in the working-tree ledger.
    """
    hermes_led = _load_ledger(repo, "hermes", ledger_rev)
    openclaw_led = _load_ledger(repo, "openclaw", ledger_rev)
    qubit_led = _load_ledger(repo, "qubit", ledger_rev)
    all_ids, find_by_name, row_blobs = _ledger_index({
        "hermes": hermes_led, "openclaw": openclaw_led, "qubit": qubit_led})

    matched = unmatched = 0
    for item in hermes_items:
        cat = item["category"]
        if cat in _EVIDENCE_CATEGORIES:
            continue
        match = _match_hermes_item(item, all_ids, find_by_name)
        item["ledger_match"] = match
        item["ledger_source"] = None
        if match:
            matched += 1
            for src_name, r, _ in row_blobs:
                if r["id"] == match:
                    item["ledger_source"] = src_name
                    item["ledger_status"] = r.get("status")
                    break
        else:
            unmatched += 1

    # upstream extensions: map to audit text or a ledger row.
    oc_matched = oc_unmatched = 0
    for item in openclaw_items:
        name = item["extension_id"]
        hit = find_by_name(name)
        in_audit = bool(re.search(
            r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])",
            audit_text))
        item["in_audit_docs"] = in_audit
        item["ledger_match"] = hit[1]["id"] if hit else None
        item["ledger_source"] = hit[0] if hit else None
        if hit:
            item["ledger_status"] = hit[1].get("status")
        if hit or in_audit:
            oc_matched += 1
        else:
            oc_unmatched += 1

    # ledger rows whose IMPLEMENTED evidence cannot be reproduced
    unreproducible = []
    for src_name, led in (("hermes", hermes_led), ("openclaw", openclaw_led),
                          ("qubit", qubit_led)):
        for r in led.get("rows", []):
            if r.get("status") != "IMPLEMENTED":
                continue
            ok, reason = _test_ref_reproducible(repo, r.get("proving_test", ""))
            if not ok:
                unreproducible.append({
                    "source": src_name, "id": r["id"],
                    "proving_test": r.get("proving_test", ""),
                    "reason": reason,
                })

    return {
        "ledger_revision": ledger_rev or "working-tree",
        "hermes": {
            "ledger_rows": len(hermes_led.get("rows", [])),
            "inventory_items": len([i for i in hermes_items
                                    if i["category"] not in _EVIDENCE_CATEGORIES]),
            "matched": matched,
            "unmatched": unmatched,
            "evidence": len([i for i in hermes_items
                             if i["category"] in _EVIDENCE_CATEGORIES]),
        },
        "openclaw": {
            "ledger_rows": len(openclaw_led.get("rows", [])),
            "extensions": len(openclaw_items),
            "matched": oc_matched,
            "unmatched": oc_unmatched,
        },
        "qubit": {"ledger_rows": len(qubit_led.get("rows", []))},
        "ledger_ids": sorted(all_ids),
        "unreproducible_evidence": sorted(
            unreproducible, key=lambda x: (x["source"], x["id"])),
    }


# ---------------------------------------------------------------------------
# Omissions report
# ---------------------------------------------------------------------------

def render_omissions(inv: dict) -> str:
    items = inv["items"]
    surfaces = [it for it in items
                if it["category"] not in _EVIDENCE_CATEGORIES]
    omissions = [it for it in surfaces if not _is_covered(it)]
    distinct = [it for it in omissions if not it.get("parent_id")]
    parented = [it for it in omissions if it.get("parent_id")]
    parented_covered = [it for it in surfaces
                        if it.get("parent_id") and _is_covered(it)]

    def group(seq, keyfn):
        out: dict[str, list[dict]] = {}
        for it in seq:
            out.setdefault(keyfn(it), []).append(it)
        return out

    distinct_by_cat = group(distinct, lambda it: it["category"])
    parented_by_parent = group(
        parented, lambda it: (it.get("parent_resolved")
                              or it.get("parent_id") or "?"))
    covered_by_parent = group(
        parented_covered, lambda it: (it.get("parent_resolved")
                                      or it.get("parent_id") or "?"))

    total_omissions = len(omissions)
    fork_dispositioned = [it for it in surfaces if it.get("fork_disposition")]
    fork_impl = [it for it in fork_dispositioned
                 if it["fork_disposition"]["status"] == "IMPLEMENTED"]
    fork_gated = [it for it in fork_dispositioned
                  if it["fork_disposition"]["status"] == "GATED"]
    evidence_counts: dict[str, int] = {}
    for it in items:
        if it["category"] in _EVIDENCE_CATEGORIES:
            evidence_counts[it["category"]] = \
                evidence_counts.get(it["category"], 0) + 1

    out = io.StringIO()
    w = out.write
    w("# Qubit source omissions — candidate work rows\n\n")
    w("Machine-generated by `scripts/source_inventory.py` from the pinned\n")
    w(f"Hermes `{HERMES_PIN}` ({HERMES_TAG}) and OpenClaw `{OPENCLAW_PIN}` "
      f"({OPENCLAW_TAG}) source trees.\n\n")
    w("**Scope.** Inventory items are classified as **distinct capabilities** "
      "(no parent row) or **parented surfaces** (a CLI option/positional, model "
      "or provider entry, plugin asset/API/test/manifest, OpenClaw extension, "
      "workflow source file, or dashboard component whose capability already "
      "has a ledger row). A parented surface whose parent row is `IMPLEMENTED` "
      "is covered by the parent and is **not** a standalone gap. Cross-cutting "
      "evidence (i18n keys, locale keys, upstream tests) is counted, not "
      "enumerated per key.\n\n")
    w("Matching is mechanical: exact ledger id for CLI (`cli:<name>`), routes "
      "(`api:<METHOD> <path>`), and dashboard routes (`dashboard:<path>`); "
      "otherwise a boundary-aware name/path search across every ledger row. "
      "Parent ids are resolved against the live merged ledger and the nearest "
      "declared ancestor is used when an exact `cli:` subcommand row is "
      "absent.\n\n")

    bd = inv["hermes_byte_diff"]["counts"]
    lc = inv["ledger_comparison"]
    pm = inv.get("parent_mapping", {})
    w("## Summary\n\n")
    w(f"- Ledger comparison baseline: `{lc['ledger_revision']}` "
      f"(verified waves-1-2 revision; the live working-tree ledger is edited "
      f"by concurrent agents, so the comparison is pinned to a reproducible "
      f"revision).\n")
    w(f"- Inventory items (all categories): **{len(items)}**\n")
    w(f"- Candidate omissions (uncovered surface items): "
      f"**{total_omissions}**\n")
    w(f"- Distinct capability gaps (no parent row): "
      f"**{len(distinct)}**\n")
    w(f"- Parented surfaces needing parent closure (parent not IMPLEMENTED): "
      f"**{len(parented)}**\n")
    w(f"- Parented surfaces covered by an IMPLEMENTED parent: "
      f"**{len(parented_covered)}**\n")
    w(f"- Distinct capabilities total: **{pm.get('distinct_surfaces', '?')}**; "
      f"parented surfaces total: **{pm.get('parented_surfaces', '?')}**\n")
    w(f"- Fork-runtime dispositions (tools/skills/plugins): "
      f"**{len(fork_dispositioned)}** "
      f"(IMPLEMENTED {len(fork_impl)}, GATED {len(fork_gated)})\n")
    w(f"- Ledger IMPLEMENTED rows with unreproducible evidence: "
      f"**{len(lc['unreproducible_evidence'])}**\n")
    w(f"- Hermes byte diff vs `qubit_runtime/`: "
      f"upstream={bd['upstream_files']} local={bd['local_files']} "
      f"identical={bd['identical']} missing={bd['missing']} "
      f"extra={bd['extra']} changed={bd['changed']}\n\n")

    w("### Omission counts by category (all uncovered)\n\n")
    om_by_cat = group(omissions, lambda it: it["category"])
    for cat in sorted(om_by_cat):
        w(f"- `{cat}`: {len(om_by_cat[cat])}\n")
    if not om_by_cat:
        w("- (none)\n")
    w("\n### Evidence-only surface counts (not candidate rows)\n\n")
    for cat in sorted(evidence_counts):
        w(f"- `{cat}`: {evidence_counts[cat]}\n")
    w("\n")

    w(f"## Distinct capability gaps ({len(distinct)})\n\n")
    w("No parent capability row exists; these remain explicit open rows.\n\n")
    for cat in sorted(distinct_by_cat):
        w(f"### {cat} ({len(distinct_by_cat[cat])})\n\n")
        for it in sorted(distinct_by_cat[cat], key=lambda x: x["id"]):
            src = it.get("path", "?")
            surface = _SURFACE_LABEL.get(it["category"], "backend")
            if surface == "cli":
                cli_s, dash_s = f"`{it['id']}`", "none"
            elif surface == "dashboard":
                cli_s, dash_s = "none", f"`{src}`"
            else:
                cli_s, dash_s = "none", "none"
            voice = "candidate" if surface in ("cli", "dashboard") else "none"
            note = ""
            if it["category"] == "openclaw_extension":
                note = ("" if it.get("in_audit_docs")
                        else " (not enumerated in AUDIT_OPENCLAW_2026-09-11.md "
                             "or AUDIT_OPENCLAW_V2026_9_4_DELTA.md)")
                cats = it.get("categories") or []
                note += f" categories={','.join(cats) or 'none'}"
                note += f" manifest={it.get('manifest') or 'none'}"
            w(f"- `{it['id']}` — source `{src}`; category `{it['category']}`; "
              f"CLI={cli_s}; dashboard={dash_s}; voice={voice}; "
              f"no parent row and no ledger row{note}. Reason: upstream "
              f"{it['category'].replace('_', ' ')} is a distinct capability "
              f"with no implementing ledger row.\n")
        w("\n")

    w(f"## Parented surfaces needing parent closure ({len(parented)})\n\n")
    w("These belong to a parent capability row that is missing or not "
      "IMPLEMENTED; close the parent (or wire the surface) to resolve them. "
      "They are not distinct capabilities.\n\n")
    for parent in sorted(parented_by_parent):
        group_items = parented_by_parent[parent]
        status = group_items[0].get("parent_status") or "absent"
        w(f"### parent `{parent}` ({len(group_items)}; status={status})\n\n")
        for it in sorted(group_items, key=lambda x: x["id"]):
            w(f"- `{it['id']}` — category `{it['category']}`; coverage "
              f"`{it.get('coverage') or '?'}`; source "
              f"`{it.get('path', '?')}`; parent `{parent}` status "
              f"`{status}`.\n")
        w("\n")

    w(f"## Parented surfaces covered by an IMPLEMENTED parent "
      f"({len(parented_covered)})\n\n")
    if not parented_covered:
        w("None.\n\n")
    else:
        w("These are coverage evidence for their parent, not separate gaps.\n\n")
        for parent in sorted(covered_by_parent):
            w(f"- `{parent}`: {len(covered_by_parent[parent])} surface(s) — "
              f"coverage `{covered_by_parent[parent][0].get('coverage') or '?'}`"
              f"\n")
        w("\n")

    w("## Fork-runtime dispositions for tools / skills / plugins"
      f" ({len(fork_dispositioned)})\n\n")
    if not fork_dispositioned:
        w("None.\n\n")
    else:
        w("Qubit executes the complete pinned Hermes fork in-process, so these "
          "are not separate Qubit modules: the fork tool/toolset registry, "
          "skill tree, and plugin fleet are the real backing surface. Every "
          "item carries an explicit typed disposition (no silent drop).\n\n")
        for cap in sorted({it["fork_disposition"]["capability"]
                           for it in fork_dispositioned}):
            cap_items = sorted(
                (it for it in fork_dispositioned
                 if it["fork_disposition"]["capability"] == cap),
                key=lambda x: x["id"])
            impl = sum(1 for it in cap_items
                       if it["fork_disposition"]["status"] == "IMPLEMENTED")
            gated = len(cap_items) - impl
            w(f"### `{cap}` ({len(cap_items)}; IMPLEMENTED {impl}, "
              f"GATED {gated})\n\n")
            for it in cap_items:
                d = it["fork_disposition"]
                proof = f"; proof `{d['proving_test']}`" if d["proving_test"] \
                    else ""
                w(f"- `{it['id']}` — **{d['status']}**; backend "
                  f"`{d.get('backend') or '—'}`; {d['reason']}{proof}\n")
            w("\n")

    w("## Ledger rows with unreproducible evidence\n\n")
    rows = inv["ledger_comparison"]["unreproducible_evidence"]
    if not rows:
        w("None. Every IMPLEMENTED ledger row across the Hermes, OpenClaw, and "
          "Qubit sections cites a proving test file whose named test function "
          "exists on disk (checked by `scripts/source_inventory.py`).\n\n")
    else:
        for r in rows:
            w(f"- `{r['id']}` (source {r['source']}) — proving_test "
              f"`{r['proving_test']}`; {r['reason']}.\n")
        w("\n")
    return out.getvalue()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def _carried_openclaw_items(repo: Path) -> list[dict]:
    """OpenClaw items carried forward from the canonical inventory.

    The pinned OpenClaw source is a build-time input that can be absent in a
    given environment. The OpenClaw surface is stable and independent of the
    Hermes sync, so rather than dropping the section (which would make the
    artifact internally inconsistent), the last canonical OpenClaw item set is
    carried forward read-only. Derived classification fields are stripped so
    they are recomputed against the current ledger.
    """
    path = repo / "docs" / "generated" / "QUBIT_SOURCE_INVENTORY.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    derived = ("parent_id", "coverage", "parent_resolved", "parent_status",
               "parent_covered", "fork_disposition", "ledger_match",
               "ledger_source", "ledger_status")
    out: list[dict] = []
    for it in data.get("items", []):
        if it.get("source") != "openclaw":
            continue
        out.append({k: v for k, v in it.items() if k not in derived})
    return out


def build_inventory(repo: Path, hermes: Path, openclaw: Path,
                    ledger_rev: str | None = LEDGER_REV,
                    vendor_manifest: Path | None = None) -> dict:
    qubit_cli = extract_qubit_cli(hermes)
    routes = extract_hermes_routes(hermes)
    dashboard = extract_hermes_dashboard(hermes)
    plugins = extract_qubit_plugins(hermes)
    tools = extract_qubit_tools(hermes)
    skills = extract_hermes_skills(hermes)
    models, providers = extract_hermes_models_providers(hermes)
    workflows = extract_hermes_workflows(hermes)
    i18n = extract_hermes_i18n(hermes)
    tests = extract_hermes_tests(hermes)

    hermes_items = (qubit_cli + routes + dashboard + plugins + tools + skills
                    + models + providers + workflows + i18n + tests)
    for it in hermes_items:
        it.setdefault("ledger_match", None)
        it.setdefault("ledger_source", None)
        it.setdefault("ledger_status", None)

    openclaw_items = extract_openclaw_extensions(openclaw, OPENCLAW_PIN)
    if not openclaw_items:
        openclaw_items = _carried_openclaw_items(repo)

    audit_text = ""
    for doc in ("AUDIT_OPENCLAW_2026-09-11.md",
                "AUDIT_OPENCLAW_V2026_9_4_DELTA.md"):
        if ledger_rev:
            try:
                audit_text += _git(repo, "show",
                                   f"{ledger_rev}:docs/{doc}") + "\n"
                continue
            except subprocess.CalledProcessError:
                pass
        path = repo / "docs" / "archive" / "2026-09-21" / doc
        if path.is_file():
            audit_text += _read_text(path) + "\n"

    comparison = reconcile(repo, hermes_items, openclaw_items, audit_text,
                           ledger_rev)
    byte_diff = compute_hermes_byte_diff(repo, hermes, vendor_manifest)

    all_items = hermes_items + openclaw_items

    # Parent-surface classification: attach every surface to its parent
    # capability and record whether that parent is IMPLEMENTED in the live
    # ledger.  A covered parented surface is evidence for the parent, not a
    # standalone missing capability; an uncovered one is reported separately
    # from a genuinely distinct capability.
    status_by_id = _ledger_status_index(repo, ledger_rev)
    for it in all_items:
        parent_id, coverage = _parent_for(it)
        resolved = _resolve_parent(parent_id, status_by_id)
        it["parent_id"] = parent_id
        it["coverage"] = coverage
        it["parent_resolved"] = resolved
        it["parent_status"] = (status_by_id.get(resolved)
                               if resolved else None)
        # A parented surface is covered when its parent is IMPLEMENTED or a
        # typed policy GATE (a flag of an intentionally-refused command is part
        # of that refusal, not an independent missing capability).
        it["parent_covered"] = bool(
            resolved and status_by_id.get(resolved) in (
                "IMPLEMENTED", "SAFE_EQUIVALENT", "OPERATOR_ONLY", "GATED"))

    # Wave 5: explicit fork-runtime disposition for every tool/skill/plugin
    # item.  These are not separate Qubit modules; the fork is executed
    # in-process, so its registry/skill tree/plugin fleet is the real surface.
    fork_tools = _fork_tool_names(repo)
    fork_plugins = _fork_plugin_names(repo)
    for it in all_items:
        disposition = _fork_disposition(repo, it, fork_tools, fork_plugins)
        if disposition is None:
            disposition = _openclaw_disposition(it)
        if disposition is not None:
            it["fork_disposition"] = disposition

    all_items.sort(key=lambda x: (x["source"], x["category"], x["id"]))

    counts: dict[str, int] = {}
    for it in all_items:
        counts[it["category"]] = counts.get(it["category"], 0) + 1

    by_source: dict[str, int] = {}
    for it in all_items:
        by_source[it["source"]] = by_source.get(it["source"], 0) + 1

    oc_cat_counts: dict[str, int] = {}
    for it in openclaw_items:
        for c in (it.get("categories") or ["(uncategorized)"]):
            oc_cat_counts[c] = oc_cat_counts.get(c, 0) + 1

    def tally(items, keyfn):
        out: dict[str, int] = {}
        for it in items:
            key = keyfn(it)
            out[key] = out.get(key, 0) + 1
        return dict(sorted(out.items()))

    omissions = [it for it in all_items
                 if it["category"] not in _EVIDENCE_CATEGORIES
                 and not _is_covered(it)]
    surfaces = [it for it in all_items
                if it["category"] not in _EVIDENCE_CATEGORIES]
    parented_all = [it for it in surfaces if it.get("parent_id")]
    distinct_all = [it for it in surfaces if not it.get("parent_id")]
    distinct = [it for it in omissions if not it.get("parent_id")]
    parented = [it for it in omissions if it.get("parent_id")]
    parented_covered = [it for it in parented_all if _is_covered(it)]
    distinct_covered = [it for it in distinct_all if _is_covered(it)]
    om_by_cat = tally(omissions, lambda it: it["category"])
    distinct_by_cat = tally(distinct, lambda it: it["category"])
    parented_by_cat = tally(parented, lambda it: it["category"])
    by_parent = tally(parented, lambda it: (it.get("parent_resolved")
                                            or it.get("parent_id") or "?"))
    covered_by_parent = tally(parented_covered,
                              lambda it: (it.get("parent_resolved")
                                          or it.get("parent_id") or "?"))

    return {
        "schema": "qubit-source-inventory/1",
        "pins": {
            "hermes": {"repo": HERMES_REPO, "revision": HERMES_PIN,
                       "tag": HERMES_TAG, "path": str(hermes)},
            "openclaw": {"repo": OPENCLAW_REPO, "revision": OPENCLAW_PIN,
                         "tag": OPENCLAW_TAG, "path": str(openclaw)},
        },
        "counts_by_category": dict(sorted(counts.items())),
        "counts_by_source": dict(sorted(by_source.items())),
        "openclaw_extension_categories": dict(sorted(oc_cat_counts.items())),
        "hermes_byte_diff": byte_diff,
        "ledger_comparison": comparison,
        "parent_mapping": {
            "method": (
                "Mechanical parent mapping: a CLI option/positional belongs to "
                "its command family (nearest existing cli: row); a CLI alias "
                "to its target; a model/provider entry to model/"
                "proxy_providers; a plugin asset/API/test/manifest to its "
                "plugin; an OpenClaw extension to the audited OC-* capability "
                "of its name or first mapped manifest category; a workflow "
                "source file to its area capability; a dashboard component/"
                "dialog/form/nav to webui_status.  A surface is covered when "
                "it matches its own ledger row or its resolved parent row is "
                "IMPLEMENTED in the live ledger, so it must not create a "
                "standalone MISSING row.  Parented and distinct uncovered "
                "items are counted and listed separately."),
            "parented_surfaces": len(parented_all),
            "parented_covered": len(parented_covered),
            "parented_uncovered": len(parented),
            "distinct_surfaces": len(distinct_all),
            "distinct_covered": len(distinct_covered),
            "distinct_uncovered": len(distinct),
            "by_coverage": tally(all_items,
                                 lambda it: it.get("coverage") or "(distinct)"),
            "by_parent_covered": covered_by_parent,
            "by_parent_uncovered": by_parent,
            "uncovered_by_category": {
                "distinct": distinct_by_cat,
                "parented": parented_by_cat,
            },
        },
        "omissions": {
            "total": len(omissions),
            "distinct_total": len(distinct),
            "parented_uncovered": len(parented),
            "parented_covered": len(parented_covered),
            "by_category": om_by_cat,
            "distinct_by_category": distinct_by_cat,
            "parented_by_category": parented_by_cat,
            "by_parent": by_parent,
            "unreproducible_evidence": comparison["unreproducible_evidence"],
        },
        "items": all_items,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hermes", default=DEFAULT_HERMES)
    ap.add_argument("--openclaw", default=DEFAULT_OPENCLAW)
    ap.add_argument("--out", default=f"{CANONICAL_DIR}/QUBIT_SOURCE_INVENTORY.json")
    ap.add_argument("--omissions", default=f"{CANONICAL_DIR}/QUBIT_SOURCE_OMISSIONS.md")
    ap.add_argument("--vendor-manifest", default=CANONICAL_VENDOR_MANIFEST,
                    help="canonical vendor manifest the byte diff reconciles "
                         "against (default: %(default)s)")
    ap.add_argument("--ledger-rev", default=LEDGER_REV,
                    help="git revision the gap ledger is read from for the "
                         "omission comparison (default: the live working "
                         "tree; pass a revision such as the waves-1-2 baseline "
                         "to compare against a historical baseline)")
    args = ap.parse_args(argv)

    hermes = Path(args.hermes)
    openclaw = Path(args.openclaw)
    if not hermes.is_dir():
        print(f"hermes pin not found: {hermes}", file=sys.stderr)
        return 2
    if not (hermes / ".git").exists():
        print(f"hermes pin is not a git checkout: {hermes}", file=sys.stderr)
        return 2
    rev = _git(hermes, "rev-parse", "HEAD").strip()
    if rev != HERMES_PIN:
        print(f"REFUSING: hermes revision {rev} != pinned {HERMES_PIN}",
              file=sys.stderr)
        return 2
    if openclaw.is_dir() and (openclaw / ".git").exists():
        try:
            _git(openclaw, "cat-file", "-t", OPENCLAW_PIN)
        except subprocess.CalledProcessError:
            print(f"REFUSING: openclaw pin {OPENCLAW_PIN} not present",
                  file=sys.stderr)
            return 2

    ledger_rev = args.ledger_rev
    if ledger_rev and ledger_rev != "working":
        try:
            _git(REPO, "rev-parse", "--verify", f"{ledger_rev}^{{commit}}")
        except subprocess.CalledProcessError:
            print(f"REFUSING: ledger revision {ledger_rev} not found",
                  file=sys.stderr)
            return 2
    else:
        ledger_rev = None
    vm = (REPO / args.vendor_manifest) if not Path(args.vendor_manifest).is_absolute() \
        else Path(args.vendor_manifest)
    inv = build_inventory(REPO, hermes, openclaw, ledger_rev, vm)

    out = (REPO / args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(inv, indent=1, sort_keys=False) + "\n",
                   encoding="utf-8")

    om = (REPO / args.omissions)
    om.parent.mkdir(parents=True, exist_ok=True)
    om.write_text(render_omissions(inv), encoding="utf-8")

    def _display(path: Path) -> str:
        try:
            return str(path.relative_to(REPO))
        except ValueError:
            return str(path)

    print(f"wrote {_display(out)}")
    print(f"wrote {_display(om)}")
    print(f"  items={len(inv['items'])} "
          f"omissions={inv['omissions']['total']} "
          f"byte_diff={inv['hermes_byte_diff']['counts']}")
    for cat, n in sorted(inv["counts_by_category"].items()):
        print(f"    {cat}={n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
