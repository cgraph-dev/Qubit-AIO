#!/usr/bin/env python3
"""Audit Qubit's absorption of OpenJarvis's eight agents as native roles.

Pure AST audit: it does NOT import the runtime, an ``openjarvis`` package, or
any role module, makes no network calls and starts no subprocess. It reads
``roles/*.py`` from a root (default: this checkout's ``qubit_runtime``), finds
each module-level ``ROLE = AgentRole(...)`` and reports coverage against the
required id set, the per-role contract fields, and the wrapper indicators that
would mean a role forwards to OpenJarvis instead of being Qubit-native.

Exit 0 only when the eight required roles are present, unique and clean (no
wrapper). Exit 1 on any gap. Extra role ids are reported as ``unexpected`` but
do not fail the audit, so future roles can be added without editing this tool.

Usage:
    .venv/bin/python scripts/audit_openjarvis_absorption.py
    .venv/bin/python scripts/audit_openjarvis_absorption.py --json
    .venv/bin/python scripts/audit_openjarvis_absorption.py --root /tmp/tree
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REQUIRED_ROLES: tuple[str, ...] = (
    "orchestrator",
    "react",
    "operative",
    "openhands",
    "simple",
    "researcher",
    "digest",
    "monitor",
)

EXCLUDED_MODULES: frozenset[str] = frozenset(
    {"base", "registry", "runner", "__init__"})

VALID_TIERS: frozenset[str] = frozenset({"local", "detail", "api", ""})
VALID_CONFIRMATIONS: frozenset[str] = frozenset(
    {"preview", "confirm", "auto"})

FIELD_DEFAULTS: dict[str, Any] = {
    "title": "",
    "description": "",
    "system_prompt": "",
    "toolsets": (),
    "model_tier": "local",
    "confirmation": "preview",
    "voice_aliases": (),
    "recipe_id": "",
    "source": "",
}

POSITIONAL_FIELDS: tuple[str, ...] = (
    "id",
    "title",
    "description",
    "system_prompt",
    "toolsets",
    "model_tier",
    "confirmation",
    "voice_aliases",
    "recipe_id",
    "source",
)

SUBPROCESS_IMPORTS: frozenset[str] = frozenset({"subprocess"})
HTTP_IMPORTS: frozenset[str] = frozenset(
    {"requests", "httpx", "aiohttp", "urllib3", "http.client", "urllib.request"})


@dataclass
class RoleRecord:
    module: str
    id: str = ""
    values: dict[str, Any] = field(default_factory=dict)
    unreadable: set[str] = field(default_factory=set)
    multiple_assignments: bool = False
    constructed: bool = False
    forwarder: bool = False
    wrapper_openjarvis: bool = False
    wrapper_subprocess: bool = False
    wrapper_http: bool = False
    recipe_present: bool | None = None
    gaps: list[str] = field(default_factory=list)

    def clean(self) -> bool:
        return not self.gaps


def _literal(node: ast.AST, consts: dict[str, Any]) -> tuple[Any, bool]:
    """Resolve a literal-ish AST node to a str / tuple[str, ...].

    Supports string constants, implicit/explicit concatenation, simple
    top-level constant references, and tuple/list/set literals of strings.
    Anything else is unreadable (``ok=False``) rather than guessed.
    """
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return node.value, True
        return None, False
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                return None, False
        return "".join(parts), True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, left_ok = _literal(node.left, consts)
        right, right_ok = _literal(node.right, consts)
        if left_ok and right_ok and isinstance(left, str) and isinstance(right, str):
            return left + right, True
        return None, False
    if isinstance(node, ast.Name):
        if node.id in consts:
            return consts[node.id], True
        return None, False
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        items: list[str] = []
        for element in node.elts:
            value, ok = _literal(element, consts)
            if not ok or not isinstance(value, str):
                return None, False
            items.append(value)
        return tuple(items), True
    if isinstance(node, ast.Call):
        name = _dotted(node.func)
        if name in {"tuple", "list", "frozenset"}:
            items = []
            for element in node.args:
                value, ok = _literal(element, consts)
                if not ok or not isinstance(value, str):
                    return None, False
                items.append(value)
            return tuple(items), True
    return None, False


def _dotted(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def _collect_constants(tree: ast.Module) -> dict[str, Any]:
    """Collect simple module-level constants, resolving references in order."""
    consts: dict[str, Any] = {}
    for _ in range(3):
        for node in tree.body:
            target = None
            value = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                if isinstance(node.targets[0], ast.Name):
                    target = node.targets[0].id
                    value = node.value
            elif (isinstance(node, ast.AnnAssign)
                  and isinstance(node.target, ast.Name)
                  and node.value is not None):
                target = node.target.id
                value = node.value
            if target is None:
                continue
            resolved, ok = _literal(value, consts)
            if ok:
                consts[target] = resolved
    return consts


def _is_agent_role_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "AgentRole"
    if isinstance(func, ast.Attribute):
        return func.attr == "AgentRole"
    return False


def _extract_assignment(node: ast.AST, consts: dict[str, Any]
                        ) -> tuple[dict[str, Any], set[str], bool]:
    """Return (values, unreadable fields, is_agent_role_construction)."""
    values: dict[str, Any] = {}
    unreadable: set[str] = set()
    if not _is_agent_role_call(node):
        return values, unreadable, False
    assert isinstance(node, ast.Call)
    for index, argument in enumerate(node.args):
        if index >= len(POSITIONAL_FIELDS):
            unreadable.add("*args")
            continue
        name = POSITIONAL_FIELDS[index]
        resolved, ok = _literal(argument, consts)
        if ok:
            values[name] = resolved
        else:
            unreadable.add(name)
    for keyword in node.keywords:
        if keyword.arg is None:
            unreadable.add("**kwargs")
            continue
        resolved, ok = _literal(keyword.value, consts)
        if ok:
            values[keyword.arg] = resolved
        else:
            unreadable.add(keyword.arg)
    return values, unreadable, True


def _assignment_targets(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Assign):
        return [target.id for target in node.targets
                if isinstance(target, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def _imports_openjarvis(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(alias.name == "openjarvis"
                   or alias.name.startswith("openjarvis.")
                   for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        return module == "openjarvis" or module.startswith("openjarvis.")
    if isinstance(node, ast.Name):
        return node.id == "openjarvis"
    return False


def _wrapper_flags(tree: ast.Module) -> tuple[bool, bool, bool]:
    openjarvis = False
    subprocess = False
    http = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            for module in modules:
                if module == "openjarvis" or module.startswith("openjarvis."):
                    openjarvis = True
                root = module.split(".", 1)[0]
                if module in SUBPROCESS_IMPORTS or root in SUBPROCESS_IMPORTS:
                    subprocess = True
                if module in HTTP_IMPORTS or root in {"requests", "httpx",
                                                     "aiohttp", "urllib3"}:
                    http = True
                if module == "http" or module.startswith("http."):
                    http = True
                if module == "urllib" or module.startswith("urllib.request"):
                    http = True
            if _imports_openjarvis(node):
                openjarvis = True
        if _imports_openjarvis(node):
            openjarvis = True
        if isinstance(node, ast.Call):
            dotted = _dotted(node.func)
            if (dotted.startswith("subprocess.")
                    or dotted in {"os.system", "os.popen"}
                    or dotted.startswith("os.spawn")
                    or dotted.startswith("asyncio.create_subprocess")):
                subprocess = True
            root = dotted.split(".", 1)[0]
            if (root in {"requests", "httpx", "aiohttp", "urllib3"}
                    or dotted.endswith("urlopen")
                    or dotted.startswith("urllib.request")
                    or dotted.startswith("http.client")
                    or dotted.startswith("http.HTTP")
                    or "ClientSession" in dotted):
                http = True
    return openjarvis, subprocess, http


def _recipe_ids(recipes_dir: Path) -> set[str] | None:
    """Recipe ids declared by ``RecipeSpec(id=...)`` in ``recipes/*.py``."""
    if not recipes_dir.is_dir():
        return None
    ids: set[str] = set()
    for path in sorted(recipes_dir.glob("*.py")):
        if path.name in {"__init__.py", "registry.py", "runner.py", "sections.py"}:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        consts = _collect_constants(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _dotted(node.func)
            if name.split(".")[-1] != "RecipeSpec":
                continue
            for keyword in node.keywords:
                if keyword.arg != "id":
                    continue
                resolved, ok = _literal(keyword.value, consts)
                if ok and isinstance(resolved, str) and resolved:
                    ids.add(resolved)
    return ids


def _role_gaps(record: RoleRecord,
               recipe_ids: set[str] | None) -> list[str]:
    gaps: list[str] = []
    where = f"role {record.id or '<unknown>'!r} ({record.module}.py)"

    def wrapper_gaps() -> None:
        if record.multiple_assignments:
            gaps.append(f"{where}: more than one module-level ROLE assignment")
        if record.forwarder:
            gaps.append(
                f"{where}: forwarder (ROLE is not constructed via AgentRole)")
        if record.wrapper_openjarvis:
            gaps.append(
                f"{where}: wrapper indicator (openjarvis import/reference)")
        if record.wrapper_subprocess:
            gaps.append(f"{where}: wrapper indicator (subprocess call/import)")
        if record.wrapper_http:
            gaps.append(f"{where}: wrapper indicator (HTTP call/import)")
        for name in sorted(record.unreadable):
            gaps.append(f"{where}: field {name} is not statically readable")

    if not record.constructed:
        wrapper_gaps()
        return gaps

    if not record.id:
        gaps.append(f"{where}: missing or unreadable id")

    for name in ("title", "description", "voice_aliases", "source"):
        value = record.values.get(name, FIELD_DEFAULTS[name])
        if name == "voice_aliases":
            if not isinstance(value, (tuple, list)) or not value:
                gaps.append(f"{where}: missing voice_aliases")
        elif not isinstance(value, str) or not value.strip():
            gaps.append(f"{where}: missing {name}")

    prompt = record.values.get("system_prompt", "")
    recipe = record.values.get("recipe_id", "")
    has_prompt = isinstance(prompt, str) and bool(prompt.strip())
    has_recipe = isinstance(recipe, str) and bool(recipe.strip())
    if not (has_prompt or has_recipe):
        gaps.append(f"{where}: needs system_prompt or recipe_id")

    tier = record.values.get("model_tier", FIELD_DEFAULTS["model_tier"])
    if not isinstance(tier, str) or tier not in VALID_TIERS:
        gaps.append(f"{where}: invalid model_tier {tier!r}")
    confirmation = record.values.get(
        "confirmation", FIELD_DEFAULTS["confirmation"])
    if (not isinstance(confirmation, str)
            or confirmation not in VALID_CONFIRMATIONS):
        gaps.append(f"{where}: invalid confirmation {confirmation!r}")

    if has_recipe and recipe_ids is not None:
        record.recipe_present = recipe in recipe_ids
        if not record.recipe_present:
            gaps.append(f"{where}: recipe_id {recipe!r} is not registered")
    elif has_recipe:
        record.recipe_present = None

    wrapper_gaps()
    return gaps


def _parse_role_module(path: Path) -> RoleRecord:
    record = RoleRecord(module=path.stem)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        record.gaps.append(f"{path.stem}.py: syntax error: {exc.msg}")
        return record
    except OSError as exc:
        record.gaps.append(f"{path.stem}.py: cannot read: {exc}")
        return record

    consts = _collect_constants(tree)
    assignments: list[ast.AST] = []
    imported_role = False
    for node in tree.body:
        if "ROLE" in _assignment_targets(node):
            target = node.value
            if target is not None:
                assignments.append(target)
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "ROLE" and alias.asname in (None, "ROLE"):
                    imported_role = True

    record.multiple_assignments = len(assignments) > 1
    record.wrapper_openjarvis, record.wrapper_subprocess, record.wrapper_http = (
        _wrapper_flags(tree))

    if not assignments:
        if imported_role:
            record.forwarder = True
        else:
            record.gaps.append(f"{path.stem}.py: no module-level ROLE object")
        return record

    values, unreadable, constructed = _extract_assignment(
        assignments[0], consts)
    record.values = values
    record.unreadable = unreadable
    record.constructed = constructed
    if not constructed:
        record.forwarder = True
    else:
        # The id lives inside the constructor call, not on the assignment value.
        resolved_id = values.get("id", "")
        record.id = resolved_id if isinstance(resolved_id, str) else ""
    return record


def _resolve_runtime_root(root: Path) -> tuple[Path, Path]:
    """Return (runtime_root, roles_dir) accepting either the runtime or repo root."""
    if (root / "roles").is_dir():
        return root, root / "roles"
    if (root / "qubit_runtime" / "roles").is_dir():
        return root / "qubit_runtime", root / "qubit_runtime" / "roles"
    return root, root / "roles"


def analyze(root: Path) -> dict[str, Any]:
    runtime_root, roles_dir = _resolve_runtime_root(Path(root))
    recipe_ids = _recipe_ids(runtime_root / "recipes")
    records: list[RoleRecord] = []
    gaps: list[str] = []

    if not roles_dir.is_dir():
        gaps.append(f"roles directory not found: {roles_dir}")
        files: list[Path] = []
    else:
        files = sorted(
            path for path in roles_dir.glob("*.py")
            if path.stem not in EXCLUDED_MODULES)

    for path in files:
        record = _parse_role_module(path)
        records.append(record)

    for record in records:
        record.gaps = _role_gaps(record, recipe_ids)

    by_id: dict[str, list[str]] = {}
    for record in records:
        if record.id:
            by_id.setdefault(record.id, []).append(record.module)

    missing = [role_id for role_id in REQUIRED_ROLES if role_id not in by_id]
    duplicates = {
        role_id: sorted(modules)
        for role_id, modules in sorted(by_id.items())
        if len(modules) > 1
    }
    unexpected = sorted(set(by_id) - set(REQUIRED_ROLES))
    wrapper_records = [
        record.module for record in records
        if (record.wrapper_openjarvis or record.wrapper_subprocess
            or record.wrapper_http or record.forwarder)
    ]

    for role_id in missing:
        gaps.append(f"missing required role id: {role_id}")
    for role_id, modules in duplicates.items():
        gaps.append(
            f"duplicate role id {role_id!r} in: {', '.join(modules)}")
    for record in records:
        gaps.extend(record.gaps)

    ordered_gaps = list(dict.fromkeys(gaps))
    clean = not ordered_gaps

    return {
        "root": str(runtime_root),
        "roles_dir": str(roles_dir),
        "required": list(REQUIRED_ROLES),
        "found": sorted(by_id),
        "covered": f"{len(set(by_id) & set(REQUIRED_ROLES))}/{len(REQUIRED_ROLES)}",
        "missing": missing,
        "duplicates": duplicates,
        "unexpected": unexpected,
        "wrapper_modules": sorted(set(wrapper_records)),
        "recipes_available": (
            sorted(recipe_ids) if recipe_ids is not None else None),
        "roles": [
            {
                "id": record.id,
                "module": record.module,
                "title": record.values.get("title", ""),
                "description": record.values.get("description", ""),
                "toolsets": list(record.values.get("toolsets", ())),
                "model_tier": record.values.get("model_tier", "local"),
                "confirmation": record.values.get(
                    "confirmation", "preview"),
                "voice_aliases": list(
                    record.values.get("voice_aliases", ())),
                "recipe_id": record.values.get("recipe_id", ""),
                "recipe_present": record.recipe_present,
                "source": record.values.get("source", ""),
                "has_system_prompt": bool(
                    str(record.values.get("system_prompt", "")).strip()),
                "wrapper": {
                    "openjarvis": record.wrapper_openjarvis,
                    "subprocess": record.wrapper_subprocess,
                    "http": record.wrapper_http,
                    "forwarder": record.forwarder,
                },
                "gaps": list(record.gaps),
                "clean": record.clean(),
            }
            for record in records
        ],
        "gaps": ordered_gaps,
        "clean": clean,
        "exit_code": 0 if clean else 1,
    }


def _status(role: dict[str, Any]) -> str:
    if role["clean"]:
        return "ok"
    return f"gap({len(role['gaps'])})"


def _prompt_cell(role: dict[str, Any]) -> str:
    if role["recipe_id"]:
        present = role.get("recipe_present")
        suffix = "" if present is None else (" +" if present else " !")
        return f"recipe:{role['recipe_id']}{suffix}"
    if role["has_system_prompt"]:
        return "prompt"
    return "-"


def _print_table(report: dict[str, Any]) -> None:
    headers = ("ID", "MODULE", "TITLE", "VOICE ALIASES",
               "PROVENANCE", "PROMPT/RECIPE", "STATUS")
    rows: list[tuple[str, ...]] = []
    for role in report["roles"]:
        rows.append((
            role["id"] or "<unknown>",
            role["module"],
            role["title"] or "-",
            ", ".join(role["voice_aliases"]) or "-",
            role["source"] or "-",
            _prompt_cell(role),
            _status(role),
        ))
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    line = "  ".join(header.ljust(widths[index])
                     for index, header in enumerate(headers))
    print(line)
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(cell.ljust(widths[index])
                        for index, cell in enumerate(row)))
    print()
    print(f"root:      {report['root']}")
    print(f"roles:     {report['covered']} required covered, "
          f"{len(report['found'])} found")
    print(f"missing:   {', '.join(report['missing']) or '-'}")
    print(f"duplicate: {', '.join(report['duplicates']) or '-'}")
    unexpected = ", ".join(report["unexpected"]) or "-"
    print(f"unexpected:{' ' + unexpected}")
    print(f"wrappers:  {', '.join(report['wrapper_modules']) or '-'}")
    if report["gaps"]:
        print("gaps:")
        for gap in report["gaps"]:
            print(f"  - {gap}")
    verdict = "CLEAN (8/8, 0 wrappers)" if report["clean"] else "GAPS"
    print(f"verdict:   {verdict}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit Qubit's native roles against the OpenJarvis 8-agent "
                    "absorption contract (pure AST, no network, no imports).")
    parser.add_argument(
        "--root", default=None,
        help="runtime root (contains roles/) or repo root; default: this "
             "checkout's qubit_runtime")
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    if args.root:
        root = Path(args.root)
    else:
        root = Path(__file__).resolve().parents[1] / "qubit_runtime"

    report = analyze(root)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_table(report)
    return int(report["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
