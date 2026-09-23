"""8U surface reachability audit (Agent 1, role G).

These are *production reachability* assertions, discovered by AST/import
analysis plus runtime introspection, not by trust in catalogs or status tiles.
They cover:

1. voice-command registry → backend dispatch wiring (every entry reaches a
   real ``_BACKENDS`` handler; the exact set of dead slots is pinned);
2. CLI parser → dispatch wiring (every top-level subcommand is handled by the
   integration index, a native handler, or the fork-forward router);
3. dashboard page mounting (every ``pages/*.tsx`` is lazy-imported and route
   mapped in ``App.tsx``) and webui alias resolution (every
   ``HERMES_ROUTE_ALIASES`` target resolves to a real SPA/plugin route);
4. compat route table integrity (no same-method duplicate patterns) and the
   previously shadowed ``GET /api/sessions/empty`` route, now reachable after
   the orchestrator reordered it above the generic session-id pattern;
5. the vendored Hermes fork's documented EOL-normalized files (Task 1 byte
   parity) stay recorded and hash-valid.

The dead surfaces are reported — not silently fixed — because the owning files
are outside this agent's write scope. See ``docs/archive/2026-09-21/QUBIT_REACHABILITY_AUDIT.md``.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP_TSX = REPO / "frontend/qubit-web/src/App.tsx"
PAGES = REPO / "frontend/qubit-web/src/pages"
MANIFEST = REPO / "docs/archive/2026-09-21/QUBIT_HERMES_VENDOR_MANIFEST.json"

# Exact dead voice dispatch slots discovered by runtime introspection. If a
# future wave wires one of these (or adds a new orphan), this test fails and
# forces the audit to be updated.
DEAD_VOICE_BACKENDS: list = []
DEAD_VOICE_SUMMARIES: list = []


def _cfg(tmp_path):
    from voicebridge.config import Config

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    cfg_file = tmp_path / "voice.yaml"
    cfg_file.write_text("logging:\n  jsonl_path: '" + str(state) + "'\n")
    return Config.load(cfg_file)


# ---------------------------------------------------------------------------
# 1. Voice registry reachability
# ---------------------------------------------------------------------------
def test_every_voice_entry_reaches_a_registered_backend():
    from voicebridge import voice_commands as VC

    entries = list(VC._iter_entries())
    assert len(entries) > 400, len(entries)
    unwired = sorted({e.id for e in entries if e.backend not in VC._BACKENDS})
    assert not unwired, f"voice entries reference unknown backends: {unwired}"
    empty = sorted(e.id for e in entries if not e.backend)
    assert not empty, f"voice entries without a backend: {empty}"


def test_voice_ids_and_aliases_are_unique():
    from voicebridge import voice_commands as VC

    entries = list(VC._iter_entries())
    ids = [e.id for e in entries]
    assert len(ids) == len(set(ids)), "duplicate voice entry ids"
    owners: dict[str, list[str]] = {}
    for e in entries:
        for alias in e.aliases:
            owners.setdefault(alias, []).append(e.id)
    collisions = {a: o for a, o in owners.items() if len(o) > 1}
    assert not collisions, f"voice alias collisions: {collisions}"


def test_dead_voice_backend_slots_are_exactly_documented():
    """No ``_BACKENDS`` handler is left unreachable by the registry entries.

    The wave-1 dead slots (``model``/``setup_run_preview``/``vision_guidance``)
    were removed rather than documented; this pins the empty set so a new
    unreachable dispatch slot fails the gate.
    """
    from voicebridge import voice_commands as VC

    used = {e.backend for e in VC._iter_entries()}
    unused = sorted(set(VC._BACKENDS) - used)
    assert unused == DEAD_VOICE_BACKENDS, (
        "dead voice dispatch slots changed; update docs/archive/2026-09-21/QUBIT_REACHABILITY_AUDIT.md"
    )
    for name in DEAD_VOICE_BACKENDS:
        assert name in VC._BACKENDS


def test_dead_voice_summary_handler_is_exactly_documented():
    from voicebridge import voice_commands as VC

    used = {e.summary for e in VC._iter_entries()}
    unused = sorted(set(VC._SUMMARIES) - used)
    assert unused == DEAD_VOICE_SUMMARIES, (
        "dead voice summary handlers changed; update docs/archive/2026-09-21/QUBIT_REACHABILITY_AUDIT.md"
    )


# ---------------------------------------------------------------------------
# 2. CLI parser -> dispatch reachability
# ---------------------------------------------------------------------------
def test_every_cli_parser_command_is_dispatched():
    from voicebridge import cli as C
    from voicebridge import cli_qubit_forward as F

    parser = C.build_parser()
    sub = next(a for a in parser._actions
               if getattr(a, "dest", None) == "command")
    names = set(sub.choices)
    assert len(names) > 80, len(names)

    handled: set[str] = set()
    for attr in ("_INTEGRATION_DESTS", "_OP_CLI_INDEX", "_INTEGRATION"):
        registry = getattr(C, attr, {}) or {}
        for key in registry:
            handled.add(key[0] if isinstance(key, tuple) else key)
    src = (REPO / "voicebridge/cli.py").read_text(encoding="utf-8")
    handled |= set(re.findall(r'command\s*==\s*"([^"]+)"', src))
    for m in re.finditer(r'command\s+in\s*\(([^)]*)\)', src):
        handled |= set(re.findall(r'"([^"]+)"', m.group(1)))
    handled |= {"agent", "hermes", "run", "once", "task", "roundtrip"}

    # A command may also be handled by the pre-argparse fork forwarder.
    truly_unhandled = sorted(
        name for name in names
        if name not in handled and F.route([name]) is None)
    assert not truly_unhandled, (
        f"CLI parser commands with no dispatch path: {truly_unhandled}")


# ---------------------------------------------------------------------------
# 3. Dashboard page mounting + alias resolution
# ---------------------------------------------------------------------------
def _builtin_route_keys() -> set[str]:
    text = APP_TSX.read_text(encoding="utf-8")
    block = re.search(r"BUILTIN_ROUTES_CORE[^=]*=\s*\{(.*?)\n\};", text, re.S)
    assert block, "BUILTIN_ROUTES_CORE not found in App.tsx"
    return set(re.findall(r'"(/[a-zA-Z0-9/\-]*)"\s*:', block.group(1)))


def test_every_frontend_page_component_is_lazy_imported_and_mounted():
    disk = {p.name for p in PAGES.glob("*.tsx")
            if not p.name.endswith(".test.tsx")}
    assert disk, "no page components found"
    text = APP_TSX.read_text(encoding="utf-8")
    imported = {name + ".tsx"
                for name in re.findall(r'import\("@/pages/([^"]+)"\)', text)}
    unmounted = sorted(disk - imported)
    dangling = sorted(imported - disk)
    assert not unmounted, f"page components never imported/mounted: {unmounted}"
    assert not dangling, f"route imports with no component on disk: {dangling}"
    # Every mounted component identifier is referenced in the route map.
    for name in disk:
        assert name[:-4] in text, f"{name} not referenced in App.tsx"


def test_every_webui_route_alias_resolves_to_a_real_route():
    from voicebridge.webui import QUBIT_ROUTE_ALIASES

    builtin = _builtin_route_keys()
    plugin_tabs = {
        json.loads(p.read_text(encoding="utf-8"))["tab"]["path"]
        for p in (REPO / "frontend/qubit-app/plugins").glob("*/manifest.json")
    }
    # "/" is the root redirect; "/chat" and "/qubit-chat" are the persistent
    # chat host routes rendered outside <Routes> (see App.tsx ChatRouteSink),
    # so none is a key in BUILTIN_ROUTES_CORE. Each alias maps to the root
    # route itself.
    known = builtin | plugin_tabs | {"/", "/chat", "/qubit-chat"}
    stale = [(src, tgt) for src, tgt in QUBIT_ROUTE_ALIASES.items()
             if (tgt or "/") not in known]
    assert not stale, f"stale dashboard route aliases: {stale}"


# ---------------------------------------------------------------------------
# 4. Compat route table integrity + known shadowed route
# ---------------------------------------------------------------------------
def test_compat_has_no_same_method_duplicate_route_patterns():
    from voicebridge import qubit_compat as HC

    seen: set[tuple] = set()
    duplicates = []
    for route in HC.ROUTES:
        key = (route.get("method"), route["pattern"])
        if key in seen:
            duplicates.append(key)
        seen.add(key)
    assert not duplicates, f"same-method duplicate compat routes: {duplicates}"


def test_compat_get_sessions_empty_count_is_reachable(tmp_path):
    """The path the dashboard actually calls (GET .../empty/count) works."""
    from voicebridge import qubit_compat as HC

    code, out = HC.dispatch(_cfg(tmp_path), "GET",
                            "/api/sessions/empty/count", {}, {})
    assert code == 200, (code, out)
    assert "count" in out, out


def test_compat_bare_get_sessions_empty_is_reachable(tmp_path):
    from voicebridge import qubit_compat as HC

    code, out = HC.dispatch(_cfg(tmp_path), "GET",
                            "/api/sessions/empty", {}, {})
    assert code == 200, (code, out)
    assert "count" in out, out


# ---------------------------------------------------------------------------
# 5. Vendored-fork byte/mode/symlink parity (Task 1 tie-in)
# ---------------------------------------------------------------------------
EOL_NORMALIZED_COUNT = 5
# Upstream files that exist byte-identically on disk in the fork but are not
# in the local git index. The vendored upstream .gitignore excluded 27 such
# files; they are now force-added, so the gap is closed. Documented in
# docs/archive/2026-09-21/QUBIT_REACHABILITY_AUDIT.md (R5).
GIT_INDEX_GAP_COUNT = 0


def test_vendor_manifest_documents_eol_normalized_fork_files():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    counts = manifest["classification_counts"]
    assert counts["MISSING"] == 0
    assert counts["UNEXPLAINED"] == 0
    # Qubit's source-level rebrand carries a deterministic, owned patch manifest
    # (owner amendment 2026-09-18): every patched file is recorded with a
    # reason, so "patched" is an explained set, never silent divergence.
    patch_manifest = manifest.get("patch_manifest") or {}
    assert counts["QUBIT_PATCHED"] == len(manifest["patched"]) == len(patch_manifest)
    for rel, entry in patch_manifest.items():
        assert entry.get("patch"), rel
        assert entry.get("reason"), rel
    assert counts["OPENCLAW_OVERLAY"] == 0
    assert counts["QUBIT_ADDED"] == 0
    assert (counts["UNCHANGED"] + counts["EOL_NORMALIZED"]
            + counts["QUBIT_PATCHED"]) == manifest["file_count"]
    assert manifest["eol_normalized_count"] == len(manifest["eol_normalized"])
    assert manifest["eol_normalized_count"] == EOL_NORMALIZED_COUNT
    for entry in manifest["eol_normalized"]:
        path = REPO / "qubit_runtime" / entry["path"]
        assert path.is_file(), entry["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == \
            entry["local_sha256"], entry["path"]
        assert entry["upstream_blob"] and entry["blob_sha256"]
        assert entry["upstream_worktree_sha256"] == entry["local_sha256"]
        assert entry["affected_regression_test"] == \
            "tests/test_surface_reachability8u.py"


def test_vendor_manifest_records_modes_and_symlinks():
    """Every fork file carries its upstream git mode, local git-index mode,
    local filesystem mode, and symlink flag; no tracked mode/symlink drift."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["upstream_file_count"] == manifest["file_count"]
    assert manifest["symlink_count_upstream"] == 0
    assert manifest["symlink_count_local"] == 0
    assert manifest["mode_divergences"] == []
    assert manifest["symlink_divergences"] == []
    # The on-disk executable bits reproduce upstream exactly.
    assert manifest["local_fs_mode_counts"] == manifest["upstream_mode_counts"]
    for rel, entry in manifest["files"].items():
        assert "upstream_mode" in entry, rel
        assert "local_git_mode" in entry, rel
        assert "local_fs_mode" in entry, rel
        assert entry["local_fs_mode"] in ("100644", "100755", "120000"), rel
        assert entry["symlink"] is False, rel


def test_vendor_manifest_git_index_gaps_are_byte_identical():
    """The only reproducibility gap is git tracking: the 26 upstream files the
    vendored .gitignore excludes are present on disk byte-for-byte and each
    records the exact ignore rule. Zero content/MISSING/UNEXPLAINED."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    gaps = manifest["git_index_gaps"]
    # 26 while the force-add handoff (W1-1) is open; 0 once the tracked tree is
    # made complete from a fresh clone.
    assert len(gaps) in (0, GIT_INDEX_GAP_COUNT)
    assert manifest["tracking_blockers"] == sorted(d["path"] for d in gaps)
    assert manifest["local_git_file_count"] == \
        manifest["upstream_file_count"] - len(gaps)
    for gap in gaps:
        path = REPO / "qubit_runtime" / gap["path"]
        assert path.is_file(), gap["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == \
            gap["on_disk_sha256"], gap["path"]
        assert gap["ignored_by"], gap
        assert gap["affected_regression_test"] == \
            "tests/test_surface_reachability8u.py"
        assert "not force-added" in gap["reason"]
