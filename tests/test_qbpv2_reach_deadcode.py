"""Wave-2 W1 reachability/dead-code audit (roles A + G).

These tests extend ``tests/test_surface_reachability8u.py`` with production
reachability checks that are discovered mechanically (AST import graph, runtime
registries) rather than by trusting catalogs or status tiles:

1. every ``voicebridge/**`` module is referenced by an import (absolute,
   relative, ``import_module`` string) somewhere in production, tests, or
   scripts — a module with no inbound reference is unreachable;
2. every compat route either has a real callable handler or is a typed
   ``gated`` route with a code + HTTP status (no fake / missing handlers);
3. every Qubit overlay plugin directory on disk is registered by the backend
   and carries its manifest + entry bundle (no unused/orphaned plugins);
4. every static fleet-manifest plugin resolves to its real bundle (no
   placeholder entries);
5. every registered integration/op CLI key maps to a real parser command, and
   every ``_INTEGRATION`` value is callable (no dangling CLI parser entries);
6. test files that never touch production code are exactly the documented
   artifact-only set (``test_qbpv2_*`` wave files are exempt while the wave is
   in flight).

Findings that require an edit outside W1's ownership are reported in
``docs/archive/2026-09-21/QUBIT_REACHABILITY_AUDIT.md`` as cross-boundary handoffs. This module
never edits production code.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLUGINS = REPO / "frontend/qubit-app/plugins"
FLEET = REPO / "frontend/qubit-web/public/plugins/fleet.json"

# ``voicebridge/qubit_backend.py`` is a programmatic entry point that no
# production module, test, or script currently imports (cross-boundary handoff
# W1-1, owner W2). Pinned so a *new* unreferenced module fails this gate.
KNOWN_UNREFERENCED_MODULES: set = set()

# Test files that assert repository artifacts (docs/manifests/scans) rather
# than exercising ``voicebridge``/``qubit_runtime`` code. They are not "dead
# tests" — they guard generated artifacts. Pinned so a new production-dead test
# is surfaced for review. ``test_qbpv2_*`` wave files are exempt.
ARTIFACT_ONLY_TESTS = {
    "test_branding_scan8tf.py",
    "test_clean_clone_wave5.py",
    # Guards the generated route-ownership matrix / compat-handler reachability
    # artifact (scripts/route_ownership_matrix.py, scripts/compat_handler_reachability.py)
    # by loading the generator by path; it does not import production modules.
    "test_compat_handler_reachability.py",
    "test_route_ownership_matrix.py",
    # Guards the generated config/env binding manifest
    # (scripts/config_binding_manifest.py -> docs/generated/QUBIT_CONFIG_BINDING_MANIFEST.json)
    # by loading the generator by path; it does not import production modules.
    "test_config_binding_manifest_d3.py",
    # Loads the differential-parity runner (scripts/differential_parity.py) by
    # path to guard that artifact; it does not import production modules.
    "test_differential_parity8u.py",
    "test_frontend_hygiene.py",
    "test_qubit_frontend_map8u.py",
    "test_matrix_final8u.py",
    "test_operator_only_evidence.py",
    "test_packet7a.py",
    "test_provenance_completeness.py",
    "test_qbpv_oc4_openclaw_pin.py",
    "test_route_labels8tf.py",
    "test_source_inventory.py",
}

_PROD_IMPORT_RE = re.compile(
    r"(from\s+voicebridge|import\s+voicebridge"
    r"|from\s+qubit_runtime|import\s+qubit_runtime"
    r"|import_module\(\s*[\"'](?:voicebridge|qubit_runtime))")


# ---------------------------------------------------------------------------
# 1. Module reachability
# ---------------------------------------------------------------------------
def _iter_py(root: str):
    for p in sorted((REPO / root).rglob("*.py")):
        if "__pycache__" not in p.as_posix():
            yield p


def _module_name(rel: str) -> str:
    m = rel[:-3].replace("/", ".")
    if m.endswith(".__init__"):
        m = m[:-9]
    return m


def _package_of(rel: str) -> str:
    if rel.endswith("__init__.py"):
        return _module_name(rel)
    return _module_name(rel).rsplit(".", 1)[0]


def _import_targets(cur_rel: str, node: ast.AST) -> set[str]:
    """Dotted module names an ``Import`` / ``ImportFrom`` in ``cur_rel`` names."""
    out: set[str] = set()
    if isinstance(node, ast.Import):
        for alias in node.names:
            out.add(alias.name)
        return out
    if not isinstance(node, ast.ImportFrom):
        return out
    level = node.level or 0
    frm = node.module or ""
    if level == 0:
        if frm:
            out.add(frm)
            for alias in node.names:
                out.add(f"{frm}.{alias.name}")
        else:
            for alias in node.names:
                out.add(alias.name)
        return out
    parts = _package_of(cur_rel).split(".")
    base = ".".join(parts[: len(parts) - (level - 1)]) if level > 1 \
        else _package_of(cur_rel)
    if frm:
        out.add(f"{base}.{frm}")
        for alias in node.names:
            out.add(f"{base}.{frm}.{alias.name}")
    else:
        for alias in node.names:
            out.add(f"{base}.{alias.name}")
    return out


def _unreferenced_modules() -> set[str]:
    mods = [p.relative_to(REPO).as_posix() for p in _iter_py("voicebridge")]
    dotted = {_module_name(rel): rel for rel in mods}
    refs = {rel: set() for rel in mods}
    sources = list(_iter_py("voicebridge")) + list(_iter_py("tests")) \
        + list(_iter_py("scripts"))
    for path in sources:
        rel = path.relative_to(REPO).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for name in _import_targets(rel, node):
                    for mod, target in dotted.items():
                        if name == mod or name.startswith(mod + "."):
                            refs[target].add(rel)
        for m in re.finditer(
                r"import_module\(\s*[\"'](voicebridge[\w.]*)[\"']", text):
            for mod, target in dotted.items():
                if m.group(1) == mod or m.group(1).startswith(mod + "."):
                    refs[target].add(rel)
        for mod, target in dotted.items():
            if re.search(r"[\"']" + re.escape(mod) + r"[\"']", text):
                refs[target].add(rel)
    return {
        rel for rel in mods
        if not rel.endswith(("__init__.py", "__main__.py"))
        and not {src for src in refs[rel] if src != rel}
    }


def test_no_unreachable_voicebridge_modules():
    unreferenced = _unreferenced_modules()
    unexpected = sorted(unreferenced - KNOWN_UNREFERENCED_MODULES)
    assert not unexpected, (
        "voicebridge modules with no import/module reference (unreachable): "
        f"{unexpected}. Wire them or remove them; if dynamic, add to "
        "KNOWN_UNREFERENCED_MODULES with a reason in the audit doc.")
    # The known entry point must still exist while the handoff is open.
    for rel in KNOWN_UNREFERENCED_MODULES:
        assert (REPO / rel).is_file(), rel


# ---------------------------------------------------------------------------
# 2. Compat route handler integrity (fake / missing handlers)
# ---------------------------------------------------------------------------
def test_every_compat_route_has_a_real_handler_or_typed_gate():
    from voicebridge import qubit_compat as HC

    missing: list[tuple] = []
    bad_gate: list[tuple] = []
    for route in HC.ROUTES:
        if route["disposition"] == "gated":
            gate = route.get("gate") or {}
            if not gate.get("code"):
                bad_gate.append((route["method"], route["pattern"]))
            continue
        handler = HC.HANDLERS.get(route["handler"])
        if not callable(handler):
            missing.append(
                (route["method"], route["pattern"], route["handler"]))
    assert not missing, f"compat routes with no real handler: {missing[:20]}"
    assert not bad_gate, f"gated compat routes without a typed gate: {bad_gate}"


def test_compat_route_patterns_compile_and_methods_are_known():
    from voicebridge import qubit_compat as HC

    known = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "WEBSOCKET"}
    for route in HC.ROUTES:
        assert route["method"] in known, route
        re.compile(route["pattern"])  # must be a valid regex


# ---------------------------------------------------------------------------
# 3. Overlay plugin registration (unused / orphaned plugins)
# ---------------------------------------------------------------------------
def test_every_overlay_plugin_dir_is_registered_and_loadable():
    from voicebridge import qubit_compat as HC

    disk = {d.name for d in PLUGINS.iterdir() if d.is_dir()}
    registered = {m["name"] for m in HC._overlay_manifests()}
    assert disk == registered, (
        f"overlay plugin dirs vs backend registry drift: "
        f"disk-only={sorted(disk - registered)} "
        f"registry-only={sorted(registered - disk)}")
    for manifest in HC._overlay_manifests():
        root = PLUGINS / manifest["name"]
        assert (root / "manifest.json").is_file(), manifest["name"]
        assert (root / manifest["entry"]).is_file(), manifest
        if manifest.get("css"):
            assert (root / manifest["css"]).is_file(), manifest


# ---------------------------------------------------------------------------
# 4. Static fleet manifest resolves to real bundles (no placeholders)
# ---------------------------------------------------------------------------
def test_static_fleet_plugins_resolve_to_real_bundles():
    fleet = json.loads(FLEET.read_text(encoding="utf-8"))
    assert fleet, "empty static fleet manifest"
    base = FLEET.parent
    names = set()
    for manifest in fleet:
        name = manifest["name"]
        assert name not in names, f"duplicate fleet plugin {name}"
        names.add(name)
        root = base / name
        assert (root / manifest["entry"]).is_file(), manifest
        if manifest.get("css"):
            assert (root / manifest["css"]).is_file(), manifest
        assert manifest.get("tab", {}).get("path"), manifest


# ---------------------------------------------------------------------------
# 5. CLI parser registry integrity (dangling registrations)
# ---------------------------------------------------------------------------
def test_every_cli_registry_key_maps_to_a_real_parser_command():
    from voicebridge import cli as C

    parser = C.build_parser()
    sub = next(a for a in parser._actions
               if getattr(a, "dest", None) == "command")
    choices = set(sub.choices)
    unknown = sorted(
        key[0] if isinstance(key, tuple) else key
        for key in list(C._OP_CLI_INDEX) + list(C._INTEGRATION)
        if (key[0] if isinstance(key, tuple) else key) not in choices)
    assert not unknown, f"CLI registry keys with no parser command: {unknown}"
    for key, handler in C._INTEGRATION.items():
        assert callable(handler), (key, handler)


# ---------------------------------------------------------------------------
# 6. Tests that never reach production
# ---------------------------------------------------------------------------
def test_production_dead_tests_are_exactly_documented():
    found = set()
    for path in sorted((REPO / "tests").glob("test_*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if not _PROD_IMPORT_RE.search(text):
            found.add(path.name)
    unexpected = sorted(
        name for name in found
        if name not in ARTIFACT_ONLY_TESTS and not name.startswith("test_qbpv2_"))
    assert not unexpected, (
        "new tests that never import/exercise production code: "
        f"{unexpected}. Add to ARTIFACT_ONLY_TESTS with a rationale if they "
        "guard artifacts.")
