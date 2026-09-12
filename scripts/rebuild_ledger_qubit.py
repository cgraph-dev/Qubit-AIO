#!/usr/bin/env python3
"""Regenerate ``docs/QUBIT_GAP_LEDGER.qubit.json`` deterministically.

The Qubit gap-ledger section is *evidence based*: a row is only reported
``IMPLEMENTED`` when

1. a behavioural test in ``tests/`` exercises the production caller (the
   dispatched backend / a downstream call the backend makes), **and**
2. every surface the capability genuinely declares is actually wired
   (``dashboard_ops`` catalog ``interfaces``), **and**
3. the op resolves to a real production backend callable.

No status is hand-flipped in the JSON.  Where a capability declares a surface
it does not wire (for example a ``voice`` interface with no live
``voice_commands.REGISTRY`` route), the row stays ``PARTIAL``; where the
catalog explicitly excludes a surface (``interfaces`` / ``interface_reason``),
that surface is not counted as applicable and is not used to inflate status.

Evidence sources
----------------
catalog rows
    ``voicebridge.dashboard_ops.catalog()`` / ``EXTRA_OPS`` supply the op id,
    title, group, help and the authoritative ``interfaces`` declaration.
    ``voicebridge.voice_commands.REGISTRY`` / ``_BACKENDS`` and
    ``dashboard_ops`` resolve the production backend; the declared surfaces are
    checked against the live CLI parser tree (``voicebridge.cli.build_parser``)
    and the registry.

gated rows
    Distinct ``voicebridge.hermes_compat.GATED`` codes (a typed policy gate is
    never ``IMPLEMENTED``).

dashboard / page rows
    ``frontend/hermes-qubit/plugins/*/manifest.json`` overlay pages and the
    vendored ``frontend/hermes-web/src/pages/*.tsx`` baseline.  A page or
    plugin declares only the dashboard surface (no CLI/voice interface by
    design): when a ``tests/`` production-route test proves it the dashboard
    surface is *complete*, so the row is ``IMPLEMENTED``; without an on-disk
    production-route test the row stays ``UNVERIFIED``.

proving tests
    An AST scan of ``tests/test_*.py`` marks the tests that reach a production
    dispatch entry point (``run_op`` / ``run_registry_op`` / ``run_extra_op`` /
    ``voice_say`` / ``confirm_op`` / ``cancel_op`` / ``execute`` / ...) and
    then matches the op id (literal, module constant, or a downstream call the
    backend makes).

The output keeps the schema and exact row keys required by
``scripts/gap_ledger.py`` plus the provenance fields.

Usage::

    python scripts/rebuild_ledger_qubit.py            # write the section
    python scripts/rebuild_ledger_qubit.py --out P    # write elsewhere
    python scripts/rebuild_ledger_qubit.py --check    # non-zero if stale
"""
from __future__ import annotations

import argparse
import ast
import glob
import inspect
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voicebridge import cli as CLI  # noqa: E402
from voicebridge import dashboard_ops as DO  # noqa: E402
from voicebridge import hermes_compat as HC  # noqa: E402
from voicebridge import voice_commands as VC  # noqa: E402

GENERATED = "2026-09-12"
OUT_PATH = ROOT / "docs" / "QUBIT_GAP_LEDGER.qubit.json"
INTERFACES = ("cli", "dashboard", "voice")
SURFACE_ONLY = {
    "cli": "CLI_ONLY",
    "dashboard": "DASHBOARD_ONLY",
    "voice": "VOICE_ONLY",
}


# ---------------------------------------------------------------------------
# Backend call-candidate extraction (what production function does this call?)
# ---------------------------------------------------------------------------
def parse_source_candidates(src: str) -> Set[Tuple[str, str]]:
    """Return ``(module, function)`` pairs the source calls into.

    Handles both ``from . import mod as alias`` and
    ``from .mod import fn as local`` spellings, which is how the voice /
    dashboard backends reach their real implementation.
    """
    aliases: Dict[str, str] = {}
    direct: Dict[str, Tuple[str, str]] = {}
    for match in re.finditer(r'from\s+\.\s+import\s+([^\n]+)', src):
        for part in match.group(1).split(','):
            part = part.strip()
            if not part:
                continue
            bits = part.split(' as ')
            name, alias = bits[0].strip(), bits[-1].strip()
            if re.match(r'^[A-Za-z_]\w*$', name):
                aliases[alias] = name
    for match in re.finditer(r'from\s+\.(\w+)\s+import\s+([^\n]+)', src):
        module = match.group(1)
        for part in match.group(2).split(','):
            part = part.strip()
            if not part or part.startswith('('):
                continue
            bits = part.split(' as ')
            orig, local = bits[0].strip(), bits[-1].strip()
            if not re.match(r'^[A-Za-z_]\w*$', orig):
                continue
            direct[local] = (module, orig)
            aliases.setdefault(local, module)
    calls: Set[Tuple[str, str]] = set()
    for match in re.finditer(r'([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*\(', src):
        attr, fn = match.group(1), match.group(2)
        if attr in aliases:
            calls.add((aliases[attr], fn))
    for match in re.finditer(r'(?<![.\w])([A-Za-z_]\w*)\s*\(', src):
        fn = match.group(1)
        if fn in direct:
            calls.add(direct[fn])
    return calls


_BRIDGE: Dict[object, Set[Tuple[str, str]]] = {}


def bridge_candidates(fn) -> Set[Tuple[str, str]]:
    if fn in _BRIDGE:
        return _BRIDGE[fn]
    try:
        src = inspect.getsource(fn)
    except Exception:
        src = ""
    cands = parse_source_candidates(src)
    _BRIDGE[fn] = cands
    return cands


def op_candidates(backend: str, fixed) -> Set[Tuple[str, str]]:
    """The downstream production calls the op's backend makes."""
    if not backend:
        return set()
    fn = VC._BACKENDS.get(backend)
    if fn is None:
        spec = DO.EXTRA_OPS.get(backend)
        if spec is not None:
            fn = getattr(spec, "run", None)
    cands = bridge_candidates(fn) if callable(fn) else set()
    action = dict(fixed or ()).get('action')
    if action and cands:
        filtered = {c for c in cands if c[1] == action}
        if filtered:
            return filtered
    return cands


# ---------------------------------------------------------------------------
# Production backend resolution (reject / flag ops with no real backend)
# ---------------------------------------------------------------------------
def resolve_backend(op_id: str) -> Tuple[str, bool, str, tuple]:
    """Return ``(qualified_backend, has_backend, backend_key, fixed)``.

    ``has_backend`` is True only when the op resolves to a real callable
    production backend.  A missing / non-callable backend yields an empty
    qualified name so a row can never be ``IMPLEMENTED`` on a phantom caller.
    """
    entry = VC.REGISTRY.get(op_id)
    if entry is not None:
        backend = getattr(entry, "backend", "")
        fixed = tuple(getattr(entry, "fixed", ()) or ())
        fn = VC._BACKENDS.get(backend)
        if callable(fn):
            return ("voicebridge.voice_commands." + fn.__name__, True,
                    backend, fixed)
        return ("", False, backend, fixed)
    spec = DO.EXTRA_OPS.get(op_id)
    if spec is not None:
        run = getattr(spec, "run", None)
        if callable(run):
            return ("voicebridge.dashboard_ops." + run.__name__, True,
                    op_id, ())
        return ("", False, op_id, ())
    return ("", False, op_id, ())


# ---------------------------------------------------------------------------
# Test evidence index (AST scan of tests/)
# ---------------------------------------------------------------------------
EXEC_NAMES = {
    "run_op", "confirm_op", "cancel_op", "execute", "confirm_pending",
    "cancel_pending", "run_extra_op", "run_registry_op", "voice_say",
}
# Real production dashboard routes.  Page/plugin rows are only IMPLEMENTED
# when a test reaches one of these (the shared dashboard dispatcher or the
# compat route/shell resolvers), never on a row-id string reference alone.
DASHBOARD_ROUTE_NAMES = {
    "run_op", "confirm_op", "cancel_op", "run_extra_op", "run_registry_op",
    "dispatch", "plugins_response", "hermes_alias_target",
}
CATALOG_IDS = {o["id"] for o in DO.catalog()} | set(DO.EXTRA_OPS)


def _plugin_manifest_paths() -> List[Path]:
    return sorted(Path(p) for p in glob.glob(
        str(ROOT / "frontend/hermes-qubit/plugins/*/manifest.json")))


def _page_paths() -> List[Path]:
    return sorted(p for p in
                  (ROOT / "frontend/hermes-web/src/pages").glob("*.tsx")
                  if not p.name.endswith(".test.tsx"))


def _manifest_name(path: Path) -> str:
    """The manifest's ``name``, falling back to its directory name.

    A missing/renamed manifest must never raise: the row id stays stable and
    the capability simply carries no proof.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return path.parent.name
    return str(data.get("name") or path.parent.name)


# Row ids for the two dashboard-only surface families.  They are part of the
# test-evidence index so a production test that references a page/plugin row
# id (directly or through a module constant such as the Agent 3 ``PAGE_ROWS`` /
# ``PLUGIN_ROW_IDS`` tables) proves that row.
PLUGIN_IDS = {"plugin_" + _manifest_name(p) for p in _plugin_manifest_paths()}
PAGE_IDS = {"page_" + p.stem.lower() for p in _page_paths()}
PAGE_PLUGIN_IDS = PLUGIN_IDS | PAGE_IDS
EVIDENCE_IDS = CATALOG_IDS | PAGE_PLUGIN_IDS


def _calls_in(node: ast.AST) -> Set[str]:
    out: Set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Attribute):
                out.add(func.attr)
            elif isinstance(func, ast.Name):
                out.add(func.id)
    return out


def _strings_in(node: ast.AST) -> Set[str]:
    return {n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)}


class TestIndex:
    """Behavioural-test evidence for the production callers."""

    def __init__(self) -> None:
        self.module_calls: Dict[Tuple[str, str], Set[Tuple[str, str]]] = \
            defaultdict(set)
        self.op_hits: Dict[str, Set[Tuple[str, str]]] = defaultdict(set)
        self.route_hits: Dict[str, Set[Tuple[str, str]]] = defaultdict(set)
        self.guard_tests: Set[Tuple[str, str]] = set()

    def add_file(self, path: Path) -> None:
        rel = str(path.relative_to(ROOT))
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (OSError, SyntaxError):
            return
        aliases: Dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and \
                    node.module.startswith("voicebridge"):
                parts = node.module.split(".")
                base = parts[1] if len(parts) > 1 else None
                for alias in node.names:
                    aliases[alias.asname or alias.name] = base or alias.name
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("voicebridge"):
                        parts = alias.name.split(".")
                        if len(parts) > 1:
                            aliases[alias.asname or parts[-1]] = parts[1]
        # module-level constants that hold catalog/page/plugin ids
        const_ids: Dict[str, Set[str]] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                ids = {s for s in _strings_in(node.value)
                       if s in EVIDENCE_IDS}
                if ids:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            const_ids[target.id] = ids
        funcs = [n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        callmap = {f.name: _calls_in(f) for f in funcs}

        def _closure(seeds: Set[str]) -> Set[str]:
            reached: Set[str] = set()
            changed = True
            while changed:
                changed = False
                for fn, callees in callmap.items():
                    if fn in reached:
                        continue
                    if callees & seeds or any(c in reached for c in callees):
                        reached.add(fn)
                        changed = True
            return reached

        exec_set = _closure(EXEC_NAMES)
        route_set = _closure(DASHBOARD_ROUTE_NAMES)
        for node in funcs:
            if not node.name.startswith("test_"):
                continue
            has_exec = node.name in exec_set
            has_route = node.name in route_set
            strings = _strings_in(node)
            names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            refs = names | {n.attr for n in ast.walk(node)
                            if isinstance(n, ast.Attribute)}
            if has_exec and "VOICE_GUARDS" in refs:
                self.guard_tests.add((rel, node.name))
            if has_exec or has_route:
                hits = set()
                for s in strings:
                    if s in EVIDENCE_IDS:
                        hits.add(s)
                for nm in names:
                    if nm in const_ids:
                        hits |= const_ids[nm]
                if has_exec:
                    for s in hits:
                        self.op_hits[s].add((rel, node.name))
                if has_route:
                    for s in hits:
                        self.route_hits[s].add((rel, node.name))
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    func = sub.func
                    if isinstance(func, ast.Attribute) and \
                            isinstance(func.value, ast.Name):
                        base = func.value.id
                        if base in aliases:
                            self.module_calls[(aliases[base], func.attr)].add(
                                (rel, node.name))


def build_test_index() -> TestIndex:
    index = TestIndex()
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        index.add_file(path)
    return index


def find_proof(index: TestIndex, op_id: str,
               cands: Set[Tuple[str, str]], is_guard: bool) -> str:
    hits: Set[Tuple[str, str]] = set()
    for cand in cands:
        hits |= index.module_calls.get(cand, set())
    if op_id in index.op_hits:
        hits |= index.op_hits[op_id]
    if is_guard:
        hits |= index.guard_tests
    if not hits:
        return ""
    rel, test = sorted(hits)[0]
    return f"tests/{Path(rel).name}::{test}"


def find_page_plugin_proof(index: TestIndex, row_id: str) -> str:
    """Production-route proof for a dashboard-only page/plugin row.

    Unlike a catalog op, a page/plugin has no backend callable to trace, so the
    proof must be a ``tests/`` function that both references the row id (or a
    module constant holding it) *and* reaches a real production dashboard route
    (``dashboard_ops.run_op`` / route dispatcher / shell resolver).  A bare
    row-id string in a non-route test is never proof.
    """
    hits = index.route_hits.get(row_id, set())
    if not hits:
        return ""
    rel, test = sorted(hits)[0]
    return f"tests/{Path(rel).name}::{test}"


# ---------------------------------------------------------------------------
# Live CLI surface tree
# ---------------------------------------------------------------------------
def _parser_tree() -> Dict[str, List[str]]:
    import argparse

    tree: Dict[str, List[str]] = {}
    parser = CLI.build_parser()
    top = next(a for a in parser._actions
               if isinstance(a, argparse._SubParsersAction))
    for family, subparser in top.choices.items():
        subs = [a for a in subparser._actions
                if isinstance(a, argparse._SubParsersAction)]
        tree[family] = sorted(subs[0].choices) if subs else []
    return tree


def _cli_tables(tree: Dict[str, List[str]]):
    join: Dict[str, Tuple[str, str]] = {}
    for family, subs in tree.items():
        for sub in subs:
            join.setdefault(family.replace('-', '_') + "_" +
                            sub.replace('-', '_'), (family, sub))
    reverse_cov: Dict[str, str] = {}
    for family, ids in VC.CLI_COVERAGE.items():
        for op_id in ids:
            reverse_cov.setdefault(op_id, family)
    extra_cli = {"export_state": "export-state",
                 "import_state": "import-state"}
    return join, reverse_cov, extra_cli


def _cli_path_for(op_id: str, tree: Dict[str, List[str]],
                  join: Dict[str, Tuple[str, str]], reverse_cov: Dict[str, str],
                  extra_cli: Dict[str, str]) -> str:
    """Resolve ``qubit ...`` path, or ``""`` if it is not real.

    Every returned path is validated against the live parser tree so the
    fuzzy plural fallback can never fabricate a CLI surface.
    """
    path = ""
    if op_id in extra_cli:
        path = f"qubit {extra_cli[op_id]}"
    elif op_id in join:
        family, sub = join[op_id]
        path = f"qubit {family} {sub}"
    elif op_id in reverse_cov:
        path = f"qubit {reverse_cov[op_id]}"
    else:
        for key, (family, sub) in join.items():
            if key.rstrip('s') == op_id.rstrip('s'):
                path = f"qubit {family} {sub}"
                break
    if not path:
        return ""
    parts = path.split()[1:]
    if not parts or parts[0] not in tree:
        return ""
    if len(parts) > 1 and parts[1] not in tree[parts[0]]:
        return ""
    return path


# ---------------------------------------------------------------------------
# Surface / provenance helpers
# ---------------------------------------------------------------------------
VOICE_IDS = set(VC.REGISTRY)

PLUGIN = {
    "brain": "qubit-memory:/memory", "memory": "qubit-memory:/memory",
    "lessons": "qubit-memory:/memory", "curator": "qubit-memory:/memory",
    "active_memory": "qubit-memory:/memory",
    "kanban": "qubit-boards:/qubit-boards",
    "gateway": "qubit-gateway:/qubit-gateway",
    "peer": "qubit-gateway:/qubit-gateway",
    "runtime": "qubit-devices:/devices", "browser": "qubit-devices:/devices",
    "browser_tabs": "qubit-devices:/devices",
    "computer": "qubit-devices:/devices",
    "audio": "qubit-devices:/devices",
    "vision_status": "qubit-devices:/devices",
    "vision_describe": "qubit-devices:/devices",
    "media_gen": "qubit-devices:/devices",
    "local_models": "qubit-devices:/devices",
    "approvals": "qubit-approvals:/qubit-approvals",
    "reports": "qubit-reports:/reports", "insights": "qubit-reports:/reports",
    "logs": "qubit-reports:/reports", "themes": "qubit-brand:/qubit-brand",
    "task_run": "qubit-chat:/qubit-chat", "agent": "qubit-chat:/qubit-chat",
    "sessions": "qubit-chat:/sessions",
    "session_share": "qubit-chat:/sessions",
}


def dashboard_for(group: str) -> str:
    return PLUGIN.get(group, "qubit-ops:/operations")


NEEDS_MAP = {
    "hardware": {
        "audio", "audio_hw", "vision_status", "vision_describe", "computer",
        "browser", "browser_tabs", "media_gen", "local_models", "runtime",
        "wake",
    },
    "creds": {
        "msg", "gateway", "peer", "pairing", "webhooks", "webhook", "sync",
        "channels", "acp_client", "frontier", "frontier_chat",
        "frontier_show", "auth_status", "auth_login", "auth_verify",
        "auth_logout", "secrets", "websites",
    },
    "owner": {
        "permissions", "power_status", "product", "package", "approvals",
        "config_profiles", "branding",
    },
}


def needs_for(group: str) -> str:
    for need, groups in NEEDS_MAP.items():
        if group in groups:
            return need
    return "none"


def _declared_interfaces(item: Dict[str, object]) -> Set[str]:
    declared = item.get("interfaces")
    if not declared:
        return set(INTERFACES)
    # Only the three known surfaces count; anything else is ignored.
    return {s for s in declared if s in INTERFACES}


# ---------------------------------------------------------------------------
# Catalog rows (Tier 1 registry + Tier 2 extras)
# ---------------------------------------------------------------------------
def build_catalog_rows(catalog: Dict[str, Dict[str, object]],
                       tree: Dict[str, List[str]],
                       index: TestIndex) -> List[Dict[str, str]]:
    join, reverse_cov, extra_cli = _cli_tables(tree)
    rows: List[Dict[str, str]] = []
    for op_id, item in sorted(catalog.items()):
        qualified, has_backend, backend_key, fixed = resolve_backend(op_id)
        entry = VC.REGISTRY.get(op_id)
        spec = DO.EXTRA_OPS.get(op_id)
        if entry is not None:
            upstream = ",".join((getattr(entry, "parity", ()) or ())[:1])
        elif spec is not None:
            upstream = ",".join((getattr(spec, "parity", ()) or ())[:1])
        else:
            upstream = ""
        cands = op_candidates(backend_key, fixed)
        backend_s = qualified
        if cands:
            module, fn = sorted(cands)[0]
            backend_s += " -> voicebridge.%s.%s" % (module, fn)

        applicable = _declared_interfaces(item)
        cli_path = _cli_path_for(op_id, tree, join, reverse_cov, extra_cli)
        dashboard = dashboard_for(str(item.get("group") or "general"))
        voice = op_id if (op_id in VOICE_IDS and "voice" in applicable) else ""

        present: Set[str] = set()
        if cli_path:
            present.add("cli")
        if dashboard:
            present.add("dashboard")
        if voice:
            present.add("voice")
        missing = applicable - present

        is_guard = op_id.startswith("guard_")
        proof = find_proof(index, op_id, cands, is_guard)
        note = str(item.get("help") or "")

        if not has_backend:
            status = "UNVERIFIED"
            note = (note + " " if note else "") + (
                "No resolvable production backend; excluded from "
                "IMPLEMENTED.")
            backend_s = ""
        elif proof and not missing:
            status = "IMPLEMENTED"
        elif proof and missing:
            if len(present) == 1:
                status = SURFACE_ONLY[sorted(present)[0]]
            else:
                status = "PARTIAL"
        elif present:
            status = "UNVERIFIED"
        else:
            status = "MISSING"

        # A row may only claim IMPLEMENTED with an on-disk proving test.
        if status == "IMPLEMENTED":
            proof_path = proof.split("::", 1)[0] if proof else ""
            if not proof or not (ROOT / proof_path).is_file():
                status = "UNVERIFIED"
                proof = ""
                note = (note + " " if note else "") + (
                    "No on-disk proving test; downgraded to UNVERIFIED.")

        rows.append({
            "id": op_id,
            "area": str(item.get("group") or "general"),
            "capability": str(item.get("title") or ""),
            "upstream_ref": upstream,
            "qubit_backend": backend_s,
            "cli": cli_path,
            "dashboard": dashboard,
            "voice": voice,
            "status": status,
            "proving_test": proof,
            "note": note,
            "needs": needs_for(str(item.get("group") or "general")),
        })
    return rows


# ---------------------------------------------------------------------------
# Hermes gated upstream surface (distinct gate codes)
# ---------------------------------------------------------------------------
def _gate_sample(pattern: str) -> str:
    """Build a concrete path for a compat regex pattern (best effort)."""
    p = re.sub(r"\(\?P<[^>]+>", "", pattern)
    p = p.replace("[^/]+", "x").replace(".*", "").replace(".+", "x")
    return p.replace("(", "").replace(")", "").replace("?", "")


def _handler_kind(name: str) -> str:
    """Reuse the Hermes generator's live handler classification."""
    try:
        scripts_dir = str(Path(__file__).resolve().parent)
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from rebuild_ledger_hermes import _handler_kind as _hk
        return _hk(name)
    except Exception:
        return ""


_API_INDEX_CACHE: Optional[object] = None


def _api_proof(handler: str) -> str:
    """On-disk proving test for ``handler`` (Hermes generator's own index)."""
    global _API_INDEX_CACHE
    try:
        scripts_dir = str(Path(__file__).resolve().parent)
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from rebuild_ledger_hermes import build_api_index, ROUTE_EVIDENCE
        if _API_INDEX_CACHE is None:
            _API_INDEX_CACHE = build_api_index()
        return _API_INDEX_CACHE.proof(handler)
    except Exception:
        return ""


def _string_gate_superseded(method: str, pattern: str, gate: object) -> bool:
    """True when a bare-handler GATED entry is genuinely superseded.

    A bare-string gate is only retired when ALL of these hold, so a capability
    can never silently vanish from the ledger:

    * ``HC.match`` resolves the route to a real read/write handler (not a
      gate/stub);
    * no ``ROUTE_EVIDENCE`` override still forces the route to ``GATED``;
    * an on-disk proving test exercises the resolved handler.

    Otherwise the gate stays open (it represents real Qubit policy or an
    untested capability).
    """
    try:
        from rebuild_ledger_hermes import ROUTE_EVIDENCE
    except Exception:
        return False
    override = ROUTE_EVIDENCE.get((method, pattern), {})
    if override.get("status") == "GATED":
        return False
    try:
        route, _ = HC.match(method, _gate_sample(pattern))
    except Exception:
        return False
    if not route or route.get("disposition") == "gated":
        return False
    handler = str(route.get("handler", ""))
    if _handler_kind(handler) not in ("real", "implemented"):
        return False
    return bool(_api_proof(handler))


def build_gated_rows() -> List[Dict[str, str]]:
    gate_rows: Dict[str, Dict[str, object]] = {}
    for method, pattern, gate in HC.GATED:
        if not isinstance(gate, dict) and \
                _string_gate_superseded(method, pattern, gate):
            continue
        if isinstance(gate, dict):
            code = gate.get("code", "GATED")
            info = {
                "detail": gate.get("detail", ""),
                "counterpart": gate.get("counterpart", ""),
                "owner": gate.get("owner", "qubit-policy"),
                "routes": [],
            }
        else:
            code = "STUB_" + str(gate).upper()
            info = {
                "detail": (f"typed disposition stub handler {gate!r}; "
                           "not a live upstream operation"),
                "counterpart": "n/a",
                "owner": "qubit-policy",
                "routes": [],
            }
        gate_rows.setdefault(code, info)
        gate_rows[code]["routes"].append(f"{method} {pattern}")

    rows: List[Dict[str, str]] = []
    for code, info in sorted(gate_rows.items()):
        routes = info["routes"]
        rows.append({
            "id": "hermes_gate_" + code.lower(),
            "area": "hermes_gated",
            "capability": "Gated upstream route: " + code,
            "upstream_ref": "hermes-agent",
            "qubit_backend": "voicebridge.hermes_compat",
            "cli": "",
            "dashboard": str(info["counterpart"]),
            "voice": "",
            "status": "GATED",
            "proving_test": "",
            "note": f"{info['detail']} routes={len(routes)}",
            "needs": "owner",
        })
    return rows


# ---------------------------------------------------------------------------
# Qubit dashboard overlay plugin pages
# ---------------------------------------------------------------------------
# A page/plugin surface family declares only the dashboard interface: there is
# no CLI or voice entry point for a rendered page by design.  Under the
# "surfaces where applicable" rule this is the *complete* applicable surface
# set, so a production-route proof makes the row IMPLEMENTED.  With no proof it
# stays UNVERIFIED -- the status is never faked.
PAGE_PLUGIN_INTERFACES = frozenset({"dashboard"})


def _page_plugin_status(proof: str) -> str:
    """Status for a page/plugin row under the applicable-surface rule.

    ``proof`` must name an on-disk ``tests/`` function reaching a real
    production dashboard route (see ``find_page_plugin_proof``).  The declared
    surface set is dashboard-only, so an on-disk proof completes every
    applicable surface (``IMPLEMENTED``); anything else is ``UNVERIFIED``.
    """
    applicable = set(PAGE_PLUGIN_INTERFACES)
    present = {"dashboard"}
    if not proof or applicable - present:
        return "UNVERIFIED"
    path = proof.split("::", 1)[0]
    if not (ROOT / path).is_file():
        return "UNVERIFIED"
    return "IMPLEMENTED"


def build_plugin_rows(index: TestIndex,
                      manifests: Optional[List[Path]] = None
                      ) -> List[Dict[str, str]]:
    paths = [Path(p) for p in manifests] if manifests is not None \
        else _plugin_manifest_paths()
    rows: List[Dict[str, str]] = []
    for manifest in paths:
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        name = str(data.get("name") or manifest.parent.name)
        row_id = "plugin_" + name
        tab = str((data.get("tab") or {}).get("path") or "")
        proof = find_page_plugin_proof(index, row_id)
        status = _page_plugin_status(proof)
        if status != "IMPLEMENTED":
            proof = ""
        rows.append({
            "id": row_id,
            "area": "dashboard_plugin",
            "capability": str(data.get("label") or name),
            "upstream_ref": "qubit-overlay",
            "qubit_backend": "voicebridge.hermes_compat+frontend/hermes-qubit",
            "cli": "",
            "dashboard": f"{name}:{tab}",
            "voice": "",
            "status": status,
            "proving_test": proof,
            "note": str(data.get("description") or "")[:180],
            "needs": "none",
        })
    return rows


# ---------------------------------------------------------------------------
# Hermes-web baseline pages (vendored, surfaced through the compat shell)
# ---------------------------------------------------------------------------
def build_page_rows(index: TestIndex,
                    pages: Optional[List[Path]] = None
                    ) -> List[Dict[str, str]]:
    files = [Path(p) for p in pages] if pages is not None else _page_paths()
    rows: List[Dict[str, str]] = []
    for page in files:
        if page.name.endswith(".test.tsx"):
            continue
        name = page.stem
        row_id = "page_" + name.lower()
        proof = find_page_plugin_proof(index, row_id)
        status = _page_plugin_status(proof)
        if status != "IMPLEMENTED":
            proof = ""
        rows.append({
            "id": row_id,
            "area": "hermes_web_page",
            "capability": f"Hermes web page {name}",
            "upstream_ref": "hermes-agent@2237be3",
            "qubit_backend": "voicebridge.hermes_compat",
            "cli": "",
            "dashboard": f"hermes-web:/{name.lower()}",
            "voice": "",
            "status": status,
            "proving_test": proof,
            "note": ("vendored Hermes baseline page served by the Qubit compat "
                     "backend"),
            "needs": "none",
        })
    return rows


# ---------------------------------------------------------------------------
# Assemble
# ---------------------------------------------------------------------------
def build_ledger() -> Dict[str, object]:
    tree = _parser_tree()
    index = build_test_index()
    catalog = {o["id"]: o for o in DO.catalog()}

    rows = (build_catalog_rows(catalog, tree, index) +
            build_gated_rows() +
            build_plugin_rows(index) +
            build_page_rows(index))

    no_backend = [r["id"] for r in rows
                  if r["status"] == "IMPLEMENTED" and not r["qubit_backend"]]
    by_status = Counter(r["status"] for r in rows)
    by_area = Counter(r["area"] for r in rows)
    tri: Counter = Counter()
    for row in rows:
        key = (("has-cli " if row["cli"] else "") +
               ("has-dashboard " if row["dashboard"] else "") +
               ("has-voice" if row["voice"] else ""))
        tri[key.strip() or "no-surface"] += 1

    return {
        "schema": "qubit-gap-ledger-section/1",
        "source": "qubit-surface",
        "generated": GENERATED,
        "method": (
            "Evidence-based regeneration. Catalog rows derive their status "
            "from the live production backend (voice_commands.REGISTRY / "
            "_BACKENDS + dashboard_ops.EXTRA_OPS), the declared interfaces "
            "(catalog), the live CLI parser tree, the registry voice surface, "
            "and an AST scan of tests/ for a proving caller. A plugin/page "
            "row declares only the dashboard surface (no CLI/voice by design); "
            "it is IMPLEMENTED when a tests/ test reaching a real production "
            "dashboard route references its row id (directly or through a "
            "module constant), else UNVERIFIED. IMPLEMENTED requires a "
            "resolvable backend, an on-disk proving test, and every applicable "
            "surface present (catalog ops use the catalog interfaces). No "
            "status is hand-flipped."),
        "sources_reconciled": [
            "voicebridge/voice_commands.py (REGISTRY/_BACKENDS/CLI_COVERAGE)",
            "voicebridge/dashboard_ops.py (catalog/EXTRA_OPS)",
            "voicebridge/cli.py (build_parser)",
            "voicebridge/hermes_compat.py (ROUTES/GATED)",
            "tests/ (AST caller evidence)",
            "frontend/hermes-qubit/plugins/*/manifest.json",
            "frontend/hermes-web/src/pages/*.tsx",
        ],
        "generated_from": {
            "voice_commands.REGISTRY": len(VC.REGISTRY),
            "dashboard_ops.catalog": len(catalog),
            "dashboard_ops.EXTRA_OPS": len(DO.EXTRA_OPS),
            "voice_commands.CLI_COVERAGE": len(VC.CLI_COVERAGE),
            "hermes_compat.ROUTES": len(HC.ROUTES),
            "hermes_compat.GATED": len(HC.GATED),
            "cli_families": len(tree),
        },
        "counts_by_status": dict(sorted(by_status.items())),
        "counts_by_area": dict(sorted(by_area.items())),
        "counts_by_source": {"qubit-surface": len(rows)},
        "tri_surface": dict(sorted(tri.items())),
        "backend_flags": sorted(no_backend),
        "provenance": {
            "generator": "scripts/rebuild_ledger_qubit.py",
            "evidence": ("production backend + declared interfaces + live CLI "
                         "tree + voice registry + tests/ AST proofs"),
        },
        "rows": rows,
    }


def _render(data: Dict[str, object]) -> str:
    return json.dumps(data, indent=1)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(OUT_PATH),
                        help="output JSON path (default: docs section)")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if the committed section differs")
    args = parser.parse_args(argv)

    data = build_ledger()
    text = _render(data)
    out = Path(args.out)
    if args.check:
        current = out.read_text(encoding="utf-8") if out.exists() else ""
        if current != text:
            print(f"STALE: {out} does not match the regenerated section",
                  file=sys.stderr)
            return 1
        print(f"OK: {out} is up to date")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out} ({len(data['rows'])} rows)")  # type: ignore[arg-type]
    print(f"  by_status: {data['counts_by_status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
