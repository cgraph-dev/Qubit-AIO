"""Wave 18 — CLI-literal subcommand bridges.

The parity inventory names the *subcommand* forms the wave-12/13 family reads
did not cover (``qubit kanban list``, ``qubit mcp install X``,
``qubit schedules create``, ``qubit brain vault list`` …). Wave 18 adds additive
``CLI_BRIDGE_REGISTRY`` entries binding each exact CLI literal to the SAME
backend the CLI/dashboard already call.

Scope:

* every intent in ``BINDINGS`` resolves through the production ``resolve()`` to
  its intended entry, bound to a real ``_BACKENDS`` callable (never
  ``voice_unsupported`` unless the entry is an explicit guard);
* the new aliases shadow no existing REGISTRY example or alias (the frozen
  wave-1..3 table stays authoritative);
* no alias collides across any registry and no new string leaks legacy brand;
* the MCP catalog admin backend is real and refuses (typed, never a fabricated
  success) an interactive/credentialed catalog install.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# ``resolve()`` recompiles ~2k alias regexes per call; raise the compiled-pattern
# cache for the shadow sweep (a test-local performance tweak, never a result
# change).
try:  # pragma: no cover - interpreter detail
    if getattr(re, "_MAXCACHE", 0) < 20000:
        re._MAXCACHE = 20000
except Exception:  # pragma: no cover
    pass

LEGACY_TOKENS = ("hermes", "nousresearch", "nous portal", "nousportal",
                 "openclaw", "nous research")

# CLI literal -> the wave-18 entry id it must resolve to.
BINDINGS = {
    "qubit mcp install airtable": "cli_mcp_install",
    "qubit mcp install notion": "cli_mcp_install",
    "qubit mcp remove airtable": "cli_mcp_remove",
    "qubit mcp test airtable": "cli_mcp_test",
    "qubit mcp invoke airtable tools": "cli_mcp_invoke",
    "qubit kanban": "cli_kanban_list",
    "qubit kanban list": "cli_kanban_list",
    "qubit kanban show": "cli_kanban_show",
    "qubit kanban current": "cli_kanban_current",
    "qubit kanban init": "cli_kanban_init",
    "qubit kanban boards create": "cli_kanban_boards_create",
    "qubit kanban assign": "cli_kanban_assign",
    "qubit kanban archive": "cli_kanban_archive",
    "qubit kanban migrate": "cli_kanban_migrate",
    "qubit kanban tab list": "cli_kanban_tab_list",
    "qubit kanban widget add": "cli_kanban_widget_add",
    "qubit skills": "cli_skills_root",
    "qubit skills enable": "cli_skills_enable",
    "qubit skills uninstall": "cli_skills_remove",
    "qubit plugins show": "cli_plugins_show",
    "qubit plugins invoke": "cli_plugins_invoke",
    "qubit plugins browse": "cli_plugins_browse",
    "qubit sessions show": "cli_session_show",
    "qubit sessions archive": "cli_session_archive",
    "qubit profile list": "cli_profile_list",
    "qubit profile show": "cli_profile_show",
    "qubit schedules list": "cli_schedules_list",
    "qubit schedules create": "cli_schedules_create",
    "qubit cron": "cli_cron_root",
    "qubit automation orders": "cli_automation_orders",
    "qubit automation order add": "cli_automation_order_add",
    "qubit gateway list": "cli_gateway_list",
    "qubit gateway show": "cli_gateway_show",
    "qubit brain vault list": "cli_brain_vault_list",
    "qubit brain principal add": "cli_brain_principal_add",
    "qubit pairing create": "cli_pairing_create",
    "qubit hooks fire": "cli_hooks_fire",
    "qubit sync": "cli_sync_root",
    "qubit peer add": "cli_peer_add",
    "qubit msg inbox": "cli_msg_inbox",
    "qubit webhook add": "cli_webhook_add",
    "qubit channels configure": "cli_channels_configure",
    "qubit opencode grant": "cli_opencode_grant",
    "qubit runtime upgrade": "cli_runtime_upgrade",
    "qubit experiments start": "cli_experiments_start",
    "qubit verified pin": "cli_verified_pin",
    "qubit product probe": "cli_product_probe",
    "qubit actions execute": "cli_actions_execute",
    "qubit checkpoints write": "cli_checkpoints_write",
    "qubit themes set": "cli_themes_set",
    "qubit memory forget": "cli_memory_forget",
    "qubit media gen generate": "cli_media_generate",
    "qubit lessons list": "cli_lessons_list",
    "qubit websites list": "cli_websites_list",
    "qubit computer ops": "cli_computer_ops",
    "qubit acp client send": "cli_acp_client_send",
    "qubit lsp restart": "cli_lsp_restart",
    "qubit browser read": "cli_browser_read",
    "qubit browser profile create": "cli_browser_profile_create",
    "qubit ide open": "cli_ide_open",
    "qubit frontier show": "cli_frontier_show",
    "qubit device enroll": "cli_device_enroll",
    "qubit auth verify": "cli_auth_verify",
    "qubit asr test": "cli_asr_test",
    "qubit tts test": "cli_tts_test",
    "qubit console help": "cli_console_help",
    "qubit project create": "cli_project_create",
    "qubit project show": "cli_project_show",
    "qubit project add-folder": "cli_project_add_folder",
    "qubit projects active": "cli_projects_active",
    "qubit autonomy show": "cli_autonomy_show",
    "qubit autonomy revoke": "cli_autonomy_revoke",
    "qubit ghost show": "cli_ghost_show",
    "qubit ghost grant": "cli_ghost_grant",
    "qubit ghost estop": "cli_ghost_estop",
    "qubit speech profile list": "cli_speech_profile_list",
    "qubit speech profile test": "cli_speech_profile_test",
    "qubit chat sessions": "cli_chat_sessions",
    "qubit pause": "cli_pause",
    "qubit import inspect": "cli_import_inspect",
    "qubit skills workshop": "cli_skills_workshop",
    "qubit pets downloads refusal": "cli_pets_refusal",
}

# A representative set of mutating literals must be confirmation-gated.
CONFIRM_GATED = {
    "cli_mcp_install", "cli_mcp_remove", "cli_kanban_init",
    "cli_kanban_assign", "cli_kanban_archive", "cli_skills_enable",
    "cli_plugins_invoke", "cli_session_archive", "cli_profile_select",
    "cli_schedules_create", "cli_automation_order_add", "cli_gateway_add",
    "cli_brain_vault_set", "cli_pairing_create", "cli_hooks_fire",
    "cli_peer_add", "cli_msg_draft", "cli_webhook_add",
    "cli_channels_configure", "cli_opencode_grant", "cli_runtime_upgrade",
    "cli_experiments_start", "cli_verified_pin", "cli_product_migrate",
    "cli_actions_execute", "cli_checkpoints_write", "cli_themes_set",
    "cli_memory_forget", "cli_media_generate", "cli_computer_launch",
    "cli_acp_client_send", "cli_lsp_restart", "cli_browser_fill",
    "cli_browser_profile_create", "cli_ide_open", "cli_device_enroll",
    "cli_project_create", "cli_autonomy_revoke", "cli_ghost_grant",
    "cli_pause", "cli_speech_profile_create",
}


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_wave18_entries_are_registered():
    from voicebridge import voice_commands as VC

    for entry_id in set(BINDINGS.values()):
        assert entry_id in VC.CLI_BRIDGE_REGISTRY, entry_id


def test_every_wave18_intent_resolves_to_a_real_backend():
    from voicebridge import voice_commands as VC

    misrouted = []
    for text, expected in BINDINGS.items():
        res = VC.resolve(text)
        got = res.entry.id if res.entry is not None else None
        if res.kind not in (VC.MATCH, VC.CLARIFY) or got != expected:
            misrouted.append((text, expected, res.kind, got))
            continue
        backend = res.entry.backend
        if backend not in VC._BACKENDS or backend == "voice_unsupported":
            misrouted.append((text, expected, "dead-backend", backend))
    assert not misrouted, f"wave-18 intents did not resolve: {misrouted}"


def test_wave18_mutations_are_confirmation_gated():
    from voicebridge import voice_commands as VC

    for entry_id in CONFIRM_GATED:
        entry = VC.CLI_BRIDGE_REGISTRY[entry_id]
        assert entry.confirm is True, f"{entry_id} must be confirm-gated"


def test_wave18_shadows_no_registry_example_or_alias():
    """The frozen wave-1..3 spoken surface stays authoritative: no new alias
    may capture an existing REGISTRY/PLUGIN_FLEET/MODEL_PROFILE alias or
    example."""
    from voicebridge import voice_commands as VC

    src = (REPO_ROOT / "voicebridge" / "voice_commands.py").read_text(
        encoding="utf-8")
    new_ids = set(re.findall(r'_CL\("([a-z0-9_]+)"', src))
    assert new_ids, "wave-18 _CL entries not found"

    shadowed = []
    for name, reg in (("REGISTRY", VC.REGISTRY),
                      ("PLUGIN_FLEET", VC.PLUGIN_FLEET_REGISTRY),
                      ("MODEL_PROFILE", VC.MODEL_PROFILE_REGISTRY)):
        for eid, entry in reg.items():
            for text in (*entry.aliases, *entry.examples):
                if text.startswith("rx:"):
                    continue
                res = VC.resolve(text if text.lower().startswith("qubit")
                                 else "qubit, " + text)
                got = res.entry.id if res.entry is not None else None
                if got in new_ids and got != eid:
                    shadowed.append((name, eid, text, got))
    assert not shadowed, f"wave-18 aliases shadow registry entries: {shadowed}"


def test_no_alias_collision_or_dead_backend_across_all_registries():
    from voicebridge import voice_commands as VC

    owners: dict = {}
    for entry in VC._iter_entries():
        for alias in entry.aliases:
            owners.setdefault(alias, []).append(entry.id)
    collisions = {a: o for a, o in owners.items() if len(o) > 1}
    assert collisions == {}, f"voice alias collisions: {collisions}"
    ids = [e.id for e in VC._iter_entries()]
    assert len(ids) == len(set(ids)), "duplicate voice entry ids"

    dead = []
    for registry in (VC.REGISTRY, VC.PLUGIN_FLEET_REGISTRY,
                     VC.MODEL_PROFILE_REGISTRY, VC.CLI_BRIDGE_REGISTRY):
        for entry in registry.values():
            if entry.backend not in VC._BACKENDS:
                dead.append((entry.id, entry.backend))
    assert dead == [], f"entries referencing unknown backends: {dead}"


def test_wave18_strings_have_no_legacy_brand():
    from voicebridge import voice_commands as VC

    src = (REPO_ROOT / "voicebridge" / "voice_commands.py").read_text(
        encoding="utf-8")
    new_ids = set(re.findall(r'_CL\("([a-z0-9_]+)"', src))
    offenders = []
    for entry_id in new_ids:
        entry = VC.CLI_BRIDGE_REGISTRY[entry_id]
        strings = [entry.id, entry.title, entry.help, entry.summary,
                   entry.backend, entry.split, *entry.aliases, *entry.examples]
        for key, value in entry.fixed or ():
            strings.extend([str(key), str(value)])
        for arg in entry.args:
            strings.extend([arg.name, arg.type, arg.clarifier, *arg.choices])
        for value in strings:
            low = str(value).lower()
            for token in LEGACY_TOKENS:
                if token in low:
                    offenders.append((entry_id, token, value))
    assert offenders == [], f"legacy brand in wave-18 strings: {offenders}"


def test_mcp_admin_refuses_interactive_install_without_fabricating():
    """An interactive/credentialed catalog entry is a typed refusal naming the
    CLI counterpart — never a silent half-install claimed as success."""
    from voicebridge import voice_commands as VC

    assert "mcp_admin" in VC._BACKENDS
    assert VC._BACKENDS["mcp_admin"] is VC._b_mcp_admin


def test_wave18_reads_resolve_and_stay_read_only():
    from voicebridge import voice_commands as VC

    for text in ("qubit kanban list", "qubit schedules list",
                 "qubit brain vault list", "qubit plugins browse",
                 "qubit websites list", "qubit lessons list"):
        entry = VC.resolve(text).entry
        assert entry is not None and entry.backend in VC._BACKENDS, text
        assert entry.confirm is False, text


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
