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
KNOWN_UNREFERENCED_MODULES: set = {
    # ``voicebridge/gateway/qubit_handlers/__init__.register_all`` composes
    # these families with ``importlib.import_module(f"{__name__}.{name}")`` from
    # FAMILY_MODULES, so the AST import scanner cannot see the reference. Each
    # module exposes ``register(registry)`` and is reached through
    # ``voicebridge/gateway/rpc.py`` -> ``register_all``.
    "voicebridge/gateway/qubit_handlers/agents.py",
    "voicebridge/gateway/qubit_handlers/board.py",
    "voicebridge/gateway/qubit_handlers/content.py",
    "voicebridge/gateway/qubit_handlers/security.py",
    "voicebridge/gateway/qubit_handlers/sessions.py",
    "voicebridge/gateway/qubit_handlers/sessions_advanced.py",
    "voicebridge/gateway/qubit_handlers/sessions_compaction.py",
    "voicebridge/gateway/qubit_handlers/surface_control.py",
    "voicebridge/gateway/qubit_handlers/system.py",
    "voicebridge/gateway/qubit_handlers/worktrees.py",
    # HUD collectors are resolved by NAME from the table in
    # ``voicebridge/hud_routes.py`` (``(kind, "collectors_<x>", "<fn>")``), so
    # no module imports them statically.
    "voicebridge/hud/collectors_cron.py",
    "voicebridge/hud/collectors_gateway.py",
    "voicebridge/hud/collectors_live_chat.py",
    "voicebridge/hud/collectors_live_models.py",
    "voicebridge/hud/collectors_memory.py",
    "voicebridge/hud/collectors_model_analytics.py",
    "voicebridge/hud/collectors_patterns.py",
    "voicebridge/hud/collectors_plugins.py",
    "voicebridge/hud/collectors_profiles.py",
    "voicebridge/hud/collectors_projects.py",
    "voicebridge/hud/collectors_providers.py",
    "voicebridge/hud/collectors_replay.py",
    "voicebridge/hud/collectors_sessions.py",
    "voicebridge/hud/collectors_skills.py",
    "voicebridge/hud/collectors_sudo.py",
    "voicebridge/hud/collectors_verify.py",
    # Loaded by file path at call time: ``voicebridge/voice_commands.py`` runs
    # ``spec_from_file_location("voicebridge.canvas_host", ...)`` and
    # ``qubit_cli/overlay.py`` resolves it via ``_need("files.canvas_host")``.
    "voicebridge/files/canvas_host.py",
    # Documented read-only integration map (its own docstring: "no wiring");
    # referenced from ``voicebridge/jev/__init__.py`` prose only.
    "voicebridge/jev/seams.py",
    # Operator/analysis tools run directly as ``python -m`` / by path per
    # ``docs/WORKBENCH_UI_PORT.md`` (pair checks, DOM/live proofs, probes).
    "voicebridge/workbench/_probe_ast.py",
    "voicebridge/workbench/_probe_live.py",
    "voicebridge/workbench/_probe_routes.py",
    "voicebridge/workbench_ui/_check_pairs.py",
    "voicebridge/workbench_ui/_prove_config.py",
    "voicebridge/workbench_ui/_prove_dom.py",
    "voicebridge/workbench_ui/_rebrand.py",
    "voicebridge/workbench_ui/gateway_handlers.py",
    # ``run_agent`` (the vendored runtime) resolves this by name via its TUI
    # banner hook; nothing in voicebridge imports it statically.
    "voicebridge/tui_banner_art.py",
}

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
    # Restored full-branch artifact/rebase/brand guard suites: they assert
    # repository artifacts or scan source text by path rather than importing
    # production modules, so the import heuristic cannot classify them.
    "test_archive_completeness_wave24.py",
    "test_branding_bundle_scan9u.py",
    "test_branding_frontend_classification9u.py",
    "test_branding_runtime_surfaces8u.py",
    "test_cli_auth_ports_wave20.py",
    "test_cli_branding_leaks9u.py",
    "test_cli_branding_url_leaks9u.py",
    "test_cli_ports_wave11.py",
    "test_cli_ports_wave6.py",
    "test_cli_ports_wave7.py",
    "test_cli_ports_wave8.py",
    "test_cli_ports_wave9_repair.py",
    "test_controls_e2e_audit.py",
    "test_cron_port_wave19.py",
    "test_forensic_audit_harness.py",
    "test_generator_determinism9u.py",
    "test_generator_durable_source_wave25.py",
    "test_home_fallback_independence_wave24.py",
    "test_identifier_rename_generator9u.py",
    "test_installer_integrity_wave25.py",
    "test_logs_pagination_wave19.py",
    "test_mcp_capability_routes_wave19.py",
    "test_merge_harness.py",
    "test_migration_canonical9u.py",
    "test_openclaw_operator_only_review9u.py",
    "test_outbound_brand_headers_wave24.py",
    "test_outbound_identity_tail_wave24.py",
    "test_printed_home_guidance_wave24.py",
    "test_profile_identity_rebind_wave8.py",
    "test_public_surface_branding_wave22.py",
    "test_rebase_agent.py",
    "test_rebase_c1_fork_patches.py",
    "test_rebase_c3_fork_patches.py",
    "test_rebase_c4_gateway_plugins.py",
    "test_rebase_candidate.py",
    "test_rebase_driver.py",
    "test_rebase_gate_runner.py",
    "test_rebase_gateway.py",
    "test_rebase_n26_record_home_readers.py",
    "test_rebase_plugins.py",
    "test_rebase_providers.py",
    "test_rebase_qubit_cli.py",
    "test_rebase_qubit_state.py",
    "test_rebase_remaining.py",
    "test_rebase_task12_deps_entrypoint.py",
    "test_rebase_task14_brand.py",
    "test_rebase_task_a3_test_deps.py",
    "test_rebase_task_n2_pm_cluster.py",
    "test_rebase_task_r4_anthropic_sdk.py",
    "test_rebase_tools.py",
    "test_rebase_tui_gateway.py",
    "test_rebrand_asymmetry_b1.py",
    "test_rebrand_asymmetry_wave24.py",
    "test_rebrand_determinism.py",
    "test_rebrand_engine_tokens.py",
    "test_rebrand_files_wave25.py",
    "test_rebrand_identifier_map.py",
    "test_rebrand_path_coverage.py",
    "test_rebrand_tail_wave24.py",
    "test_route_e2e_audit.py",
    "test_skill_debrand_migration9u.py",
    "test_tmpdir_policy.py",
    "test_ui_tui_vendor_gap12_9u.py",
    "test_update_branding_help_wave21.py",
    "test_update_branding_wave20.py",
    "test_v0215_class_a_repairs.py",
    "test_vendor_manifest_test_classification.py",
    "test_voice_coverage_report.py",
    "test_w9_agent_slice_surface_switch_failure_copy.py",
    "test_w9_gateway_tools_slice_held_back.py",
    "test_w9_held_back_port_wave.py",
    # --- Office frontend: assert the built JS bundle and its sources rather
    # than importing production Python. They read the served artifacts
    # (qubit-office.game.js / the module sources) and drive node where needed.
    "test_office_assets_vendored.py",
    "test_office_atomic_serving.py",
    "test_office_bundle.py",
    "test_office_camera.py",
    "test_office_control_bar.py",
    "test_office_deployment_contract.py",
    "test_office_determinism.py",
    "test_office_doc_consistency.py",
    "test_office_full_integration.py",
    "test_office_game_adapter.py",
    "test_office_game_art.py",
    "test_office_game_character.py",
    "test_office_game_input_context.py",
    "test_office_game_iso.py",
    "test_office_game_navigation.py",
    "test_office_game_operational.py",
    "test_office_game_packaging.py",
    "test_office_game_shell.py",
    "test_office_game_slice.py",
    "test_office_hud_drawer.py",
    "test_office_inspector.py",
    "test_office_interaction_lifecycle.py",
    "test_office_iso_engine.py",
    "test_office_label_clipping.py",
    "test_office_label_layout.py",
    "test_office_render_volume.py",
    "test_office_sprites.py",
    "test_office_suite_health.py",
    "test_office_tower_integration.py",
    "test_office_tower_visual.py",
    "test_office_vendor_habblaud.py",
    "test_office_vendor_reproducibility.py",
    "test_office_vendor_stream_rewrite.py",
    "test_office_visual_scenes.py",
    "test_office_wall_volume.py",
    "test_qubit_office_route_wiring.py",
    # --- Installer / release / packaging: drive the shell installers and the
    # release pipeline as subprocesses against the built tree; no Python
    # production import to detect.
    "test_artifact_install_e2e.py",
    "test_bootstrap_installer.py",
    "test_bootstrap_installer_windows.py",
    "test_fresh_install_config_brand8.py",
    "test_install_source_rehearsal9v.py",
    "test_packaging_force_include9u.py",
    "test_prepare_release_seed.py",
    "test_production_release.py",
    "test_promote_public_release.py",
    "test_public_export_buildable9u.py",
    "test_public_tree_feature_parity.py",
    "test_release_asset_pipeline.py",
    "test_release_fresh_install_gate.py",
    "test_root_installer_release.py",
    "test_root_installer_windows.py",
    "test_verify_fresh_install.py",
    "test_verify_public_release.py",
    "test_windows_public_install_layout.py",
    # --- Repo-hygiene / generated-ledger / script guards: assert artifacts and
    # invoke scripts by path (hygiene, drift, CI wiring, purged ledger).
    "test_check_commit_hygiene.py",
    "test_export_public_tree.py",
    "test_openjarvis_absorption.py",
    "test_parity_ledger.py",
    "test_plugin_sdk_dual_publish.py",
    "test_public_docs.py",
    "test_purged_ledger_helper.py",
    "test_run_local_ci.py",
    "test_tools_namespace_shadow_regression.py",
    "test_upstream_drift_report.py",
    "test_vendor_finish_line_provenance.py",
    "test_workflow_security.py",
    "test_worktree_cli_surface19.py",
    "test_worktree_remove_subcommand19.py",
    # --- UI/voice surface guards: read SPA sources or spawn the real script.
    "test_dashboard_auth_nous_binding9u.py",
    "test_dashboard_page_smoke.py",
    "test_voice_edit_ui_fix9u.py",
    "test_voice_orb_live_capture9u.py",
    "test_wait_for_display.py",
    # Loads the live-controls audit script (scripts/controls_e2e_audit.py) by
    # path to guard that its mutating steps stay behind --allow-live-mutations;
    # it does not import production modules.
    "test_controls_e2e_audit_gate9v.py",
    # Prose guards over the licence paragraphs in THIRD_PARTY_NOTICES.md; they
    # assert repository text, not production behaviour.
    "test_office_theme_licences.py",
    # Guards the WAVE6_AUDIT table in the generated gap ledger
    # (docs/generated/QUBIT_GAP_LEDGER.qubit.json): every audited ``form``
    # symbol and ``proving`` test must exist on disk.
    "test_openclaw_extensions_wave6.py",
}

_PROD_IMPORT_RE = re.compile(
    r"(from\s+voicebridge|import\s+voicebridge"
    r"|from\s+qubit_runtime|import\s+qubit_runtime"
    r"|from\s+tui_gateway|import\s+tui_gateway"
    r"|import_module\(\s*[\"'](?:voicebridge|qubit_runtime|tui_gateway))")


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

    # An overlay plugin is a dir carrying a top-level manifest.json -- exactly
    # what ``_overlay_manifests()`` globs. ``qubit-browser-ext`` is a Chrome
    # extension (its manifest is nested under extension/), not a dashboard
    # overlay, so it is not part of this registry.
    disk = {d.name for d in PLUGINS.iterdir()
            if d.is_dir() and (d / "manifest.json").is_file()}
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
    # The loader resolves ``assetRoot + "/" + entry`` where assetRoot is
    # ``assetBase`` or ``/dashboard-plugins/<name>`` -- the backend-served app
    # tree, NOT necessarily ``public/plugins/<name>``. qubit-office is exactly
    # that case: its code is served from frontend/qubit-app (backend overlay,
    # manifest "source": "qubit-overlay") and public/plugins holds only its
    # assets. A fleet row is satisfied when its entry resolves on either root.
    app_plugins = REPO / "frontend/qubit-app/plugins"
    names = set()
    for manifest in fleet:
        name = manifest["name"]
        assert name not in names, f"duplicate fleet plugin {name}"
        names.add(name)
        roots = [base / name, app_plugins / name]
        assert any((r / manifest["entry"]).is_file() for r in roots), manifest
        for root in roots:
            css = manifest.get("css")
            if css and (root / "manifest.json").is_file():
                assert (root / css).is_file(), manifest
                break
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
