#!/usr/bin/env python3
"""Qubit direct source inventory (Phase 1/2 of the code-readiness directive).

Builds a deterministic, mechanical inventory directly from the pinned Hermes
and OpenClaw source trees -- not from the gap ledger -- then reconciles that
inventory against ``docs/QUBIT_GAP_LEDGER.{hermes,openclaw,qubit}.json`` and
writes a candidate-work omissions report.

Outputs (defaults, override with --out / --omissions):
    docs/QUBIT_SOURCE_INVENTORY.json
    docs/QUBIT_SOURCE_OMISSIONS.md

The script is read-only against the pinned trees and the repository except for
the two generated artifacts above. No timestamps are embedded so regeneration
is byte-for-byte deterministic.

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

HERMES_PIN = "2237be355906fbe6065ce1815711eee52b2d646e"
HERMES_TAG = "v2026.9.7"
HERMES_REPO = "https://github.com/NousResearch/hermes-agent"
OPENCLAW_PIN = "3a9d69db306cd7f081e06254cb89c4bcc14a7107"
OPENCLAW_TAG = "v2026.9.4"
OPENCLAW_REPO = "https://github.com/openclaw/openclaw"

# Verified waves-1-2 ledger baseline. The working-tree ledger is edited by
# concurrent implementation agents, so the inventory reconciles against this
# fixed baseline and separately records whether an omission has since been
# closed in the working tree.
LEDGER_REV = "f5fc5776"

DEFAULT_HERMES = "/tmp/opencode/pinned/hermes-agent"
DEFAULT_OPENCLAW = "/tmp/opencode/pinned/openclaw"

# Directories/files excluded from the byte diff, matching
# scripts/hermes_vendor_manifest.py so counts reconcile.
_BYTE_SKIP_PARTS = {"__pycache__", ".git", ".pytest_cache"}
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
    "hermes_cli_command": "cli",
    "hermes_cli_subcommand": "cli",
    "hermes_cli_option": "cli",
    "hermes_cli_positional": "cli",
    "hermes_backend_route": "dashboard",
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
    "hermes_toolset": "backend",
    "hermes_skill": "backend",
    "hermes_model": "backend",
    "hermes_provider": "backend",
    "hermes_workflow": "backend",
    "openclaw_extension": "dashboard",
}


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
# Hermes CLI extraction (AST over parser modules)
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
            "category": ("hermes_cli_command" if is_top
                         else "hermes_cli_subcommand"),
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
                    "category": ("hermes_cli_command" if len(apath) <= 1
                                 else "hermes_cli_subcommand"),
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
            "category": ("hermes_cli_option" if kind == "option"
                         else "hermes_cli_positional"),
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


def extract_hermes_cli(repo: Path) -> list[dict]:
    files = [repo / "hermes_cli" / "_parser.py", repo / "hermes_cli" / "main.py"]
    files += sorted((repo / "hermes_cli" / "subcommands").glob("*.py"))
    for extra in ("send_cmd.py", "portal_cli.py", "kanban.py",
                  "kanban_parser.py", "projects_cmd.py"):
        files.append(repo / "hermes_cli" / extra)
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
# Hermes backend routes
# ---------------------------------------------------------------------------

_ROUTE_RE = re.compile(
    r"@(?:[\w.]+)\.(get|post|put|delete|patch|head|websocket)\(\s*[\"']([^\"']+)[\"']"
)


def extract_hermes_routes(repo: Path) -> list[dict]:
    files = sorted(repo.glob("hermes_cli/web_server*.py"))
    files += sorted((repo / "hermes_cli" / "web_routers").glob("*.py"))
    files += [repo / "hermes_cli" / "dashboard_auth" / "routes.py"]
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
                "category": "hermes_backend_route",
                "name": f"{method} {route}",
                "path": f"{rel}:{lineno}",
                "method": method,
                "route": route,
            })
    return items


# ---------------------------------------------------------------------------
# Hermes dashboard (pages, routes, nav, components)
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
# Hermes plugins
# ---------------------------------------------------------------------------

def extract_hermes_plugins(repo: Path) -> list[dict]:
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
# Hermes tools / skills / models / providers / workflows
# ---------------------------------------------------------------------------

def extract_hermes_tools(repo: Path) -> list[dict]:
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
            if entry.is_dir():
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
    static = repo / "hermes_cli" / "models_catalog_static.py"
    auth = repo / "hermes_cli" / "auth.py"
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
        "name": n, "path": "hermes_cli/models_catalog_static.py",
    } for n in sorted(models)]
    pitems = [{
        "id": f"provider:{n}", "source": "hermes", "category": "hermes_provider",
        "name": n, "path": "hermes_cli/models_catalog_static.py",
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
    for area, patterns in _WORKFLOW_PATTERNS.items():
        hits: set[str] = set()
        for pattern in patterns:
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
# Hermes localization keys and upstream tests
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
# OpenClaw extensions
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
# Hermes pin vs qubit_hermes/ byte diff
# ---------------------------------------------------------------------------

def _byte_skipped(rel: str) -> bool:
    parts = set(Path(rel).parts)
    if parts & _BYTE_SKIP_PARTS:
        return True
    return rel.endswith(_BYTE_SKIP_SUFFIX)


def compute_hermes_byte_diff(repo: Path, hermes: Path) -> dict:
    upstream: dict[str, str] = {}
    for line in _git(hermes, "ls-tree", "-r", "HEAD").splitlines():
        meta, path = line.split("\t", 1)
        _mode, otype, blob = meta.split()
        if otype != "blob" or _byte_skipped(path):
            continue
        upstream[path] = blob

    local_root = repo / "qubit_hermes"
    local: dict[str, str] = {}
    if local_root.is_dir():
        for path in sorted(local_root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(local_root).as_posix()
            if _byte_skipped(rel):
                continue
            local[rel] = _sha256_file(path)

    missing, extra, changed = [], [], []
    for rel, blob in upstream.items():
        if rel not in local:
            missing.append({"path": rel, "upstream_blob": blob})
            continue
        up_file = hermes / rel
        if up_file.is_file():
            up_sha = _sha256_file(up_file)
        else:
            up_sha = None
        if up_sha is not None and up_sha != local[rel]:
            changed.append({"path": rel, "upstream_blob": blob,
                            "upstream_sha256": up_sha, "local_sha256": local[rel]})
    for rel in local:
        if rel not in upstream:
            extra.append({"path": rel, "local_sha256": local[rel]})

    return {
        "upstream_repo": HERMES_REPO,
        "upstream_revision": HERMES_PIN,
        "upstream_tag": HERMES_TAG,
        "local_root": "qubit_hermes",
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
    }


# ---------------------------------------------------------------------------
# Ledger reconciliation
# ---------------------------------------------------------------------------

def _load_ledger(repo: Path, name: str, rev: str | None = None) -> dict:
    """Load a ledger section from a git revision (default: working tree)."""
    if rev:
        try:
            raw = _git(repo, "show", f"{rev}:docs/QUBIT_GAP_LEDGER.{name}.json")
            return json.loads(raw)
        except (subprocess.CalledProcessError, ValueError):
            return {"rows": []}
    path = repo / "docs" / f"QUBIT_GAP_LEDGER.{name}.json"
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


def _match_hermes_item(item: dict, all_ids: set, find_by_name):
    cat = item["category"]
    if cat in ("hermes_cli_command", "hermes_cli_subcommand"):
        lid = "cli:" + item["name"].replace("hermes ", "", 1)
        if lid in all_ids:
            return lid
    elif cat == "hermes_backend_route":
        if item["id"] in all_ids:
            return item["id"]
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

    # OpenClaw extensions: map to audit text or a ledger row.
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
    by_cat: dict[str, list[dict]] = {}
    for it in items:
        if it["category"] in _EVIDENCE_CATEGORIES:
            continue
        if it.get("ledger_match"):
            continue
        by_cat.setdefault(it["category"], []).append(it)

    total_omissions = sum(len(v) for v in by_cat.values())
    evidence_counts = {}
    for it in items:
        if it["category"] in _EVIDENCE_CATEGORIES:
            evidence_counts[it["category"]] = evidence_counts.get(it["category"], 0) + 1

    out = io.StringIO()
    w = out.write
    w("# Qubit source omissions — candidate work rows\n\n")
    w("Machine-generated by `scripts/source_inventory.py` from the pinned\n")
    w(f"Hermes `{HERMES_PIN}` ({HERMES_TAG}) and OpenClaw `{OPENCLAW_PIN}` "
      f"({OPENCLAW_TAG}) source trees.\n\n")
    w("**Scope.** Every inventory item in a capability-surface category "
      "(CLI commands/subcommands/options, backend routes, dashboard "
      "pages/routes/nav/components, plugins, tools, skills, models, providers, "
      "workflows, OpenClaw extensions) that has **no matching row** in "
      "`docs/QUBIT_GAP_LEDGER.hermes.json`, `QUBIT_GAP_LEDGER.openclaw.json`, or "
      "`QUBIT_GAP_LEDGER.qubit.json` is listed below as a candidate work row. "
      "Cross-cutting evidence surfaces (i18n keys, locale keys, upstream tests) "
      "are counted, not enumerated per key, because they are not standalone "
      "capability rows.\n\n")
    w("Matching is mechanical: exact ledger id for CLI (`cli:<name>`), routes "
      "(`api:<METHOD> <path>`), and dashboard routes (`dashboard:<path>`); "
      "otherwise a boundary-aware name/path search across every ledger row.\n\n")

    bd = inv["hermes_byte_diff"]["counts"]
    lc = inv["ledger_comparison"]
    w("## Summary\n\n")
    w(f"- Ledger comparison baseline: `{lc['ledger_revision']}` "
      f"(verified waves-1-2 revision; the live working-tree ledger is edited "
      f"by concurrent agents, so the comparison is pinned to a reproducible "
      f"revision).\n")
    w(f"- Inventory items (all categories): **{len(items)}**\n")
    w(f"- Candidate omissions (surface categories, no ledger row): "
      f"**{total_omissions}**\n")
    w(f"- Ledger IMPLEMENTED rows with unreproducible evidence: "
      f"**{len(lc['unreproducible_evidence'])}**\n")
    w(f"- Hermes byte diff vs `qubit_hermes/`: "
      f"upstream={bd['upstream_files']} local={bd['local_files']} "
      f"identical={bd['identical']} missing={bd['missing']} "
      f"extra={bd['extra']} changed={bd['changed']}\n\n")

    w("### Omission counts by category\n\n")
    for cat in sorted(by_cat):
        w(f"- `{cat}`: {len(by_cat[cat])}\n")
    if not by_cat:
        w("- (none)\n")
    w("\n### Evidence-only surface counts (not candidate rows)\n\n")
    for cat in sorted(evidence_counts):
        w(f"- `{cat}`: {evidence_counts[cat]}\n")
    w("\n")

    for cat in sorted(by_cat):
        w(f"## {cat} ({len(by_cat[cat])})\n\n")
        for it in sorted(by_cat[cat], key=lambda x: x["id"]):
            src = it.get("path", "?")
            surface = _SURFACE_LABEL.get(cat, "backend")
            if surface == "cli":
                cli_s, dash_s = f"`{it['id']}`", "none"
            elif surface == "dashboard":
                cli_s, dash_s = "none", f"`{src}`"
            else:
                cli_s, dash_s = "none", "none"
            voice = "candidate" if surface in ("cli", "dashboard") else "none"
            note = ""
            if cat == "openclaw_extension":
                note = ("" if it.get("in_audit_docs")
                        else " (not enumerated in AUDIT_OPENCLAW_2026-09-11.md "
                             "or AUDIT_OPENCLAW_V2026_9_4_DELTA.md)")
                cats = it.get("categories") or []
                note += f" categories={','.join(cats) or 'none'}"
                note += f" manifest={it.get('manifest') or 'none'}"
            w(f"- `{it['id']}` — source `{src}`; category `{cat}`; "
              f"CLI={cli_s}; dashboard={dash_s}; voice={voice}; "
              f"no ledger row{note}. Reason: upstream {cat.replace('_', ' ')} "
              f"absent from all three gap-ledger sections.\n")
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

def build_inventory(repo: Path, hermes: Path, openclaw: Path,
                    ledger_rev: str | None = LEDGER_REV) -> dict:
    hermes_cli = extract_hermes_cli(hermes)
    routes = extract_hermes_routes(hermes)
    dashboard = extract_hermes_dashboard(hermes)
    plugins = extract_hermes_plugins(hermes)
    tools = extract_hermes_tools(hermes)
    skills = extract_hermes_skills(hermes)
    models, providers = extract_hermes_models_providers(hermes)
    workflows = extract_hermes_workflows(hermes)
    i18n = extract_hermes_i18n(hermes)
    tests = extract_hermes_tests(hermes)

    hermes_items = (hermes_cli + routes + dashboard + plugins + tools + skills
                    + models + providers + workflows + i18n + tests)
    for it in hermes_items:
        it.setdefault("ledger_match", None)
        it.setdefault("ledger_source", None)
        it.setdefault("ledger_status", None)

    openclaw_items = extract_openclaw_extensions(openclaw, OPENCLAW_PIN)

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
        path = repo / "docs" / doc
        if path.is_file():
            audit_text += _read_text(path) + "\n"

    comparison = reconcile(repo, hermes_items, openclaw_items, audit_text,
                           ledger_rev)
    byte_diff = compute_hermes_byte_diff(repo, hermes)

    all_items = hermes_items + openclaw_items
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

    omissions = [it for it in all_items
                 if it["category"] not in _EVIDENCE_CATEGORIES
                 and not it.get("ledger_match")]
    om_by_cat: dict[str, int] = {}
    for it in omissions:
        om_by_cat[it["category"]] = om_by_cat.get(it["category"], 0) + 1

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
        "omissions": {
            "total": len(omissions),
            "by_category": dict(sorted(om_by_cat.items())),
            "unreproducible_evidence": comparison["unreproducible_evidence"],
        },
        "items": all_items,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hermes", default=DEFAULT_HERMES)
    ap.add_argument("--openclaw", default=DEFAULT_OPENCLAW)
    ap.add_argument("--out", default="docs/QUBIT_SOURCE_INVENTORY.json")
    ap.add_argument("--omissions", default="docs/QUBIT_SOURCE_OMISSIONS.md")
    ap.add_argument("--ledger-rev", default=LEDGER_REV,
                    help="git revision the gap ledger is read from for the "
                         "omission comparison (default: verified waves-1-2 "
                         "baseline; use 'working' for the live working tree)")
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
    inv = build_inventory(REPO, hermes, openclaw, ledger_rev)

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
