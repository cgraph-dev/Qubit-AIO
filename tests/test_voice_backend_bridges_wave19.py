"""Wave 19 — voice bridges onto REAL, already-existing backends.

The parity inventory still names CLI literals (``qubit config get``, ``qubit
tools enable a2a``, ``qubit plugins validate``, ``qubit sessions export`` …)
whose canonical handler already exists but had no spoken alias. This wave adds
additive ``CLI_BRIDGE_REGISTRY`` entries that bind each exact CLI literal to the
SAME backend the CLI/dashboard call — no new backend, no stub, no fabricated
success.

Scope:

* every intent in ``BINDINGS`` resolves through the production ``resolve()`` to
  its intended entry, bound to a real ``_BACKENDS`` callable (never
  ``voice_unsupported`` except the explicit ``GUARD_BINDINGS``);
* the real backends actually execute: config reads against a temp config, the
  toolset mutation persists, the plugin validator returns a real report, the
  canonical chat-session export reads the one store, and the profile admin
  dispatches the Profiles dashboard route;
* mutating entries are confirmation-gated;
* no alias collides across any registry and no new string leaks legacy brand.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

try:  # pragma: no cover - interpreter detail
    if getattr(re, "_MAXCACHE", 0) < 20000:
        re._MAXCACHE = 20000
except Exception:  # pragma: no cover
    pass

LEGACY_TOKENS = ("hermes", "nousresearch", "nous portal", "nousportal",
                 "openclaw", "nous research")

# CLI literal -> the wave-19 entry id it must resolve to.
BINDINGS = {
    "qubit config path": "cli_config_path",
    "qubit config get": "cli_config_get",
    "qubit config check": "cli_config_check",
    "qubit config env-path": "cli_config_env_path",
    "qubit config set": "cli_config_set",
    "qubit config unset": "cli_config_unset",
    "qubit tools enable a2a": "cli_tools_enable",
    "qubit tools disable todo": "cli_tools_disable",
    "qubit plugins enable echo": "cli_plugins_enable",
    "qubit plugins disable echo": "cli_plugins_disable",
    "qubit plugins validate": "cli_plugins_validate",
    "qubit plugins search demo": "cli_plugins_search",
    "qubit mcp add airtable": "cli_mcp_add",
    "qubit sessions export abc": "cli_sessions_export",
    "qubit profile create work": "cli_agent_profile_create",
    "qubit profile rename work home": "cli_agent_profile_rename",
    "qubit profile describe work": "cli_agent_profile_describe",
    "qubit files read": "cli_files_read_cmd",
    "qubit files list": "cli_files_list_cmd",
    "qubit file patch rollback": "cli_file_patch_rollback",
    "qubit vision describe": "cli_vision_describe_cmd",
    "qubit verified list": "cli_verified_list_cmd",
    "qubit shares publish": "cli_shares_publish_cmd",
    "qubit shares revoke": "cli_shares_revoke_cmd",
    "qubit shares purge": "cli_shares_purge_cmd",
    "qubit transfers fetch": "cli_transfers_fetch_cmd",
    "qubit transfers plan": "cli_transfers_plan_cmd",
    "qubit transfers resume": "cli_transfers_resume_cmd",
    "qubit package certify": "cli_package_certify",
    "qubit branding migrate": "cli_branding_migrate",
    "qubit capabilities list": "cli_capabilities_list",
    "qubit capability": "cli_capability",
    "qubit worktree list": "cli_worktree_list",
    "qubit worktree prune": "cli_worktree_prune",
    "qubit monitor": "cli_monitor",
    "qubit reasoning": "cli_reasoning",
    "qubit loop start": "cli_loop_start",
    "qubit verify": "cli_verify",
    "qubit skin": "cli_skin",
    "qubit verify voice": "cli_verify_voice",
    "qubit task run current": "cli_task_run_current",
    "qubit computer screen": "cli_computer_screen",
    "qubit computer screen arrange": "cli_computer_screen_arrange",
}

# Documented typed refusals (an interactive/inspection surface whose real
# handler is CLI/dashboard-only), still bound to the audited guard backend.
GUARD_BINDINGS = {
    "qubit config edit": "cli_config_edit_guard",
    "qubit config migrate": "cli_config_migrate_guard",
}

# A representative set of mutating literals must be confirmation-gated.
CONFIRM_GATED = {
    "cli_config_set", "cli_config_unset", "cli_tools_enable",
    "cli_tools_disable", "cli_plugins_enable", "cli_plugins_disable",
    "cli_mcp_add", "cli_agent_profile_create", "cli_agent_profile_rename",
    "cli_file_patch_rollback", "cli_vision_describe_cmd",
    "cli_shares_publish_cmd", "cli_shares_revoke_cmd", "cli_shares_purge_cmd",
    "cli_transfers_fetch_cmd", "cli_transfers_plan_cmd",
    "cli_branding_migrate", "cli_worktree_prune", "cli_monitor",
    "cli_verify_voice", "cli_task_run_current",
    "cli_computer_screen_arrange",
}


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_wave19_entries_are_registered():
    from voicebridge import voice_commands as VC

    for entry_id in set(BINDINGS.values()) | set(GUARD_BINDINGS.values()):
        assert entry_id in VC.CLI_BRIDGE_REGISTRY, entry_id
        assert VC._lookup_entry(entry_id) is VC.CLI_BRIDGE_REGISTRY[entry_id]


def test_every_wave19_intent_resolves_to_its_entry():
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
    assert not misrouted, f"wave-19 intents did not resolve: {misrouted}"

    for text, expected in GUARD_BINDINGS.items():
        res = VC.resolve(text)
        assert res.kind in (VC.MATCH, VC.CLARIFY), (text, res.kind)
        assert res.entry.id == expected, (text, res.entry.id)
        assert res.entry.backend == "voice_unsupported", text


def test_wave19_mutations_are_confirmation_gated():
    from voicebridge import voice_commands as VC

    for entry_id in CONFIRM_GATED:
        entry = VC.CLI_BRIDGE_REGISTRY[entry_id]
        assert entry.confirm is True, f"{entry_id} must be confirm-gated"


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


def test_wave19_strings_have_no_legacy_brand():
    from voicebridge import voice_commands as VC

    src = (REPO_ROOT / "voicebridge" / "voice_commands.py").read_text(
        encoding="utf-8")
    new_ids = set(re.findall(r'_CL\("([a-z0-9_]+)"', src))
    assert new_ids
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
    assert offenders == [], f"legacy brand in wave-19 strings: {offenders}"


# ----------------------------------------------------------- real behavior
def test_config_backend_reads_the_canonical_store(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = VC.make_ctx(cfg, session="w19", source="cli")

    path = VC._b_config(ctx, {"action": "path"})["config_path"]
    assert path["state"] == "OK" and path["path"] == str(cfg.path)

    got = VC._b_config(ctx, {"action": "get", "key": "logging.jsonl"})
    assert got["config_value"]["state"] == "OK"
    assert got["config_value"]["value"] is False

    check = VC._b_config(ctx, {"action": "check"})["config_check"]
    assert "logging" in check["sections"]
    assert check["env_refs"] >= 0


def test_tools_backend_persists_a_real_toolset_mutation(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = VC.make_ctx(cfg, session="w19", source="cli")

    enabled = VC._b_tools(ctx, {"action": "enable", "name": "web"})["tools"]
    assert enabled.get("ok") is True and enabled.get("enabled") is True, enabled

    disabled = VC._b_tools(ctx, {"action": "disable", "name": "web"})["tools"]
    assert disabled.get("ok") is True and disabled.get("enabled") is False


def test_plugins_validate_runs_the_real_validator(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = VC.make_ctx(cfg, session="w19", source="cli")
    plugin_dir = tmp_path / "plug"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.yaml").write_text(
        "name: fixture-plugin\nversion: 1.0.0\ndescription: fixture\n")

    report = VC._b_plugin_runtime(
        ctx, {"action": "validate", "path": str(plugin_dir)})["plugins_runtime"]
    assert isinstance(report, dict) and "ok" in report
    assert isinstance(report.get("checks"), list) and report["checks"]


def test_plugins_search_reads_the_curated_index(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = VC.make_ctx(cfg, session="w19", source="cli")
    out = VC._b_plugin_runtime(
        ctx, {"action": "search", "query": "zz-no-such-plugin"})["plugins_runtime"]
    assert isinstance(out, dict) and out.get("ok") is True
    assert out.get("count", 0) == 0


def test_sessions_export_reads_the_one_canonical_store(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = VC.make_ctx(cfg, session="w19", source="cli")
    out = VC._b_chat_sessions(
        ctx, {"action": "export", "session_id": "no-such-session"})["export"]
    assert isinstance(out, dict)
    assert out.get("state") in ("MISSING", "OK"), out


def test_profile_admin_dispatches_the_dashboard_route(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import voice_commands as VC

    calls = []

    def _fake_dispatch(cfg, method, path, **kwargs):
        calls.append((method, path, kwargs.get("body")))
        return 200, {"ok": True, "name": (kwargs.get("body") or {}).get("name")}

    monkeypatch.setattr(HC, "dispatch", _fake_dispatch)
    ctx = VC.make_ctx(_cfg(tmp_path, monkeypatch), session="w19", source="cli")

    VC._b_profile_admin(ctx, {"action": "create", "name": "work"})
    assert calls[-1][0] == "POST" and calls[-1][1] == "/api/profiles"
    assert calls[-1][2]["confirm"] is True and calls[-1][2]["name"] == "work"

    VC._b_profile_admin(
        ctx, {"action": "rename", "name": "work", "new_name": "home"})
    assert calls[-1][0] == "PATCH" and calls[-1][1] == "/api/profiles/work"
    assert calls[-1][2]["new_name"] == "home"

    VC._b_profile_admin(ctx, {"action": "describe", "name": "work"})
    assert calls[-1][0] == "POST"
    assert calls[-1][1] == "/api/profiles/work/describe-auto"


def test_profile_admin_propagates_a_typed_refusal(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import voice_commands as VC

    monkeypatch.setattr(
        HC, "dispatch",
        lambda *a, **k: (409, {"ok": False, "code": "CONFIRM_REQUIRED"}))
    ctx = VC.make_ctx(_cfg(tmp_path, monkeypatch), session="w19", source="cli")
    with pytest.raises(VC.PluginFleetError):
        VC._b_profile_admin(ctx, {"action": "create", "name": "work"})


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
