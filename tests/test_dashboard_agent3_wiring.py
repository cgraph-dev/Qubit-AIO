"""Agent 3 integration wave — Agent 5-10 capabilities through the dashboard.

The integration wave exposes the Agent 5-10 backend capabilities as Tier-1
registry operations (one canonical backend for CLI, dashboard, and voice).
This suite proves the dashboard dispatcher reaches exactly the same backend
dict the voice/CLI pipeline calls, with real confirmation, cancellation,
persistence, and typed negatives.

It intentionally asserts NO duplicate ``dashboard_ops.EXTRA_OPS`` shadow: a
capability with a registry entry is exercised through the shared Tier-1 path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# capability ids that Agents 5-10 provide as real operations
CAPABILITY_IDS = [
    "browser_profiles", "browser_profile_create", "browser_profile_open",
    "browser_profile_close", "browser_profile_remove",
    "computer_screen", "computer_screen_panes", "computer_screen_arrange",
    "local_models_fallback", "local_models_resolve",
    "gateway_supervision_request", "gateway_supervision_revert",
    "gateway_discovery",
    "pairing_setup_code", "pairing_accept",
    "runtime_recover", "runtime_repair", "runtime_history",
    "runtime_discovery", "runtime_marketplace",
    "egress_status", "egress_check", "policy_conformance", "policy_attest",
    "marketplace_discovery", "hooks_emit",
    "skills_workshop", "skills_workshop_backups", "skills_workshop_curator",
    "skills_workshop_backup", "skills_workshop_restore",
    "skills_workshop_rollback",
    "usage_summary", "receipts_ledger", "logbook_entries", "logbook_summary",
]

# (id, dashboard args, direct args) — direct args usually equal dashboard args
READ_CASES = [
    ("browser_profiles", {}, {}),
    ("computer_screen", {}, {}),
    ("computer_screen_panes", {}, {}),
    ("local_models_fallback", {}, {}),
    ("local_models_resolve", {"model": "qubit-brain4"},
     {"model": "qubit-brain4"}),
    ("gateway_discovery", {}, {}),
    ("egress_status", {}, {}),
    ("egress_check", {"host": "example.com"}, {"host": "example.com"}),
    ("skills_workshop", {}, {}),
    ("skills_workshop_backups", {}, {}),
    ("skills_workshop_curator", {}, {}),
    ("usage_summary", {}, {}),
    ("receipts_ledger", {}, {}),
    ("logbook_entries", {}, {}),
    ("logbook_summary", {}, {}),
    ("runtime_history", {}, {}),
    ("runtime_discovery", {}, {}),
    ("runtime_marketplace", {}, {}),
]


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _run(cfg, op_id, args=None, session="dashboard"):
    from voicebridge import dashboard_ops as OPS

    return OPS.run_op(cfg, op_id, dict(args or {}), actor="dashboard:user",
                      session=session)


def _confirm(cfg, out, sensitive=None):
    from voicebridge import dashboard_ops as OPS

    return OPS.confirm_op(cfg, out["confirmation_id"], actor="dashboard:user",
                          session="dashboard", sensitive=sensitive)


def _cancel(cfg, out):
    from voicebridge import dashboard_ops as OPS

    return OPS.cancel_op(cfg, out["confirmation_id"], actor="dashboard:user",
                         session="dashboard")


def _full(cfg, op_id, args=None):
    out = _run(cfg, op_id, args)
    if out.get("state") == "needs-confirm":
        out = _confirm(cfg, out)
    return out


def _direct(cfg, entry_id, explicit):
    from voicebridge import voice_commands as VC

    entry = VC.REGISTRY[entry_id]
    merged = dict(explicit)
    for key, value in (entry.fixed or ()):
        merged.setdefault(key, value)
    ctx = VC.make_ctx(cfg, actor="probe", session="probe", source="cli")
    return VC._BACKENDS[entry.backend](ctx, merged)


# ------------------------------------------------------------ exposure
def test_agent_capabilities_are_real_registry_ops(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    _cfg(tmp_path, monkeypatch)
    catalog = {o["id"]: o for o in OPS.catalog()}
    for op_id in CAPABILITY_IDS:
        entry = VC.REGISTRY.get(op_id)
        assert entry is not None, op_id
        assert entry.backend in VC._BACKENDS, (op_id, entry.backend)
        assert catalog[op_id]["tier"] == "registry", op_id
        assert catalog[op_id]["interfaces"] == ["cli", "dashboard", "voice"], \
            op_id
        assert entry.help, op_id
    # no shadowing Tier-2 extra duplicates the canonical registry ids
    assert not (set(OPS.EXTRA_OPS) & set(CAPABILITY_IDS))


def test_read_capabilities_match_direct_backend(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    for op_id, dash_args, direct_args in READ_CASES:
        got = _run(cfg, op_id, dash_args)
        assert got["state"] == "executed", (op_id, got)
        assert got["receipt"]["entry"] == op_id
        assert got["receipt"]["result"] == _direct(cfg, op_id, direct_args), \
            op_id


def test_policy_read_matches_direct_backend(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    got = _run(cfg, "policy_conformance", {"workspace": str(workspace)})
    assert got["state"] == "executed", got
    assert got["receipt"]["result"] == \
        _direct(cfg, "policy_conformance", {"workspace": str(workspace)})


# ----------------------------------------------- confirmation / cancel
def test_browser_profile_confirm_cancel_and_persist(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    # cancel denies before execution
    staged = _run(cfg, "browser_profile_create", {"profile": "cancelme"})
    assert staged["state"] == "needs-confirm", staged
    assert _cancel(cfg, staged)["state"] == "executed"
    assert _confirm(cfg, staged)["state"] == "failed"
    assert _run(cfg, "browser_profiles", {})["receipt"]["result"][
        "browser_profiles"]["profiles"] == []

    # confirm executes the real backend and persists
    create_stage = _run(cfg, "browser_profile_create", {"profile": "work"})
    assert create_stage["state"] == "needs-confirm"
    created = _confirm(cfg, create_stage)
    assert created["state"] == "executed", created
    assert created["receipt"]["result"]["browser_profile"]["state"] == "OK"
    listed = _run(cfg, "browser_profiles", {})["receipt"]["result"][
        "browser_profiles"]["profiles"]
    assert [p["name"] for p in listed] == ["work"]
    # settle-once: a replay of the same confirmation fails closed
    assert _confirm(cfg, create_stage)["state"] == "failed"
    # typed negative: duplicate name is refused, never duplicated
    dup = _full(cfg, "browser_profile_create", {"profile": "work"})
    assert dup["receipt"]["result"]["browser_profile"]["state"] == "EXISTS"


def test_skills_workshop_backup_persists(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    backup = _full(cfg, "skills_workshop_backup", {"label": "wave"})
    assert backup["state"] == "executed", backup
    result = backup["receipt"]["result"]["workshop"]
    assert result["state"] == "OK", result
    assert result["backup_id"]
    listed = _run(cfg, "skills_workshop_backups", {})["receipt"]["result"][
        "workshop"]["backups"]
    assert any(b["backup_id"] == result["backup_id"] for b in listed)


# ----------------------------------------------------- typed negatives
def test_egress_check_is_a_typed_local_decision(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    got = _run(cfg, "egress_check", {"host": "totally-not-allowed.test"})
    assert got["state"] == "executed"
    result = got["receipt"]["result"]["egress"]
    assert result["allowed"] is False
    assert result["code"] == "EGRESS_DENIED", result


def test_policy_conformance_bad_workspace_is_typed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    got = _run(cfg, "policy_conformance",
               {"workspace": str(tmp_path / "does-not-exist")})
    assert got["state"] == "failed", got
    assert got["code"] == "WORKSPACE_UNREADABLE", got


def test_marketplace_discovery_bad_source_is_typed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    got = _run(cfg, "marketplace_discovery", {"source": "does-not-exist"})
    assert got["state"] == "executed", got
    result = got["receipt"]["result"]
    assert result["state"] == "FAILED", result
    assert result["code"] == "MARKETPLACE_BAD_INPUT", result


def test_unknown_and_bad_args_are_typed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    assert _run(cfg, "agent3_nope", {})["code"] == "UNKNOWN_OP"
    assert _run(cfg, "egress_check", {})["code"] == "BAD_ARGS"
    assert _run(cfg, "local_models_resolve",
                {"bogus": "1"})["code"] == "BAD_ARGS"
    assert _run(cfg, "browser_profile_create",
                {"profile": "x", "bogus": "1"})["code"] == "BAD_ARGS"


# ================================ wave 2 — remaining dashboard rows


def _res(out):
    return (out.get("receipt") or {}).get("result")


# --------------------------------------------------------------- projects
def test_projects_full_lifecycle_and_persistence(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    ws1 = tmp_path / "ws1"
    ws2 = tmp_path / "ws2"
    ws1.mkdir()
    ws2.mkdir()

    created = _full(cfg, "projects_create", {"name": "Wave Two"})
    assert created["state"] == "executed", created
    slug = _res(created)["project"]["slug"]
    assert slug == "wave-two"

    listed = _run(cfg, "projects_list", {})
    assert [p["slug"] for p in _res(listed)["projects"]] == [slug]

    added = _full(cfg, "project_add_folder", {"slug": slug,
                                              "folder": str(ws1)})
    assert _res(added)["state"] == "OK"
    assert _res(added)["project"]["folders"] == [str(ws1)]
    _full(cfg, "project_add_folder", {"slug": slug, "folder": str(ws2)})
    primary = _full(cfg, "project_set_primary", {"slug": slug,
                                                 "folder": str(ws2)})
    assert _res(primary)["project"]["primary"] == str(ws2)
    renamed = _full(cfg, "project_rename", {"slug": slug, "name": "Renamed"})
    assert _res(renamed)["project"]["name"] == "Renamed"

    # real board binding
    assert _full(cfg, "kanban_boards_create",
                 {"board": "waveboards", "name": "Wave"})["state"] == \
        "executed"
    bound = _full(cfg, "project_bind_board", {"slug": slug,
                                              "board": "waveboards"})
    assert _res(bound)["project"]["board"] == "waveboards"

    removed = _full(cfg, "project_remove_folder", {"slug": slug,
                                                   "folder": str(ws1)})
    assert _res(removed)["project"]["folders"] == [str(ws2)]
    used = _full(cfg, "projects_use", {"slug": slug})
    assert used["state"] == "executed", used
    assert _res(used)["active_slug"] == slug
    archived = _full(cfg, "projects_archive", {"slug": slug})
    assert _res(archived)["project"]["archived"] is True
    restored = _full(cfg, "project_restore", {"slug": slug})
    assert _res(restored)["project"]["archived"] is False
    rollback = _full(cfg, "project_rollback", {})
    assert rollback["state"] == "executed", rollback
    assert "restored_projects" in _res(rollback)

    # persistence: a fresh config reads the same store
    from voicebridge import config as C

    fresh = C.Config.load(tmp_path / "voice.yaml")
    again = _run(fresh, "projects_show", {"slug": slug})
    assert again["state"] == "executed"
    assert _res(again)["project"]["board"] == "waveboards"
    active = _run(fresh, "projects_active", {})
    assert active["state"] == "executed"
    assert _res(active)["state"] in ("OK", "EMPTY", "NONE")


def test_projects_cancel_and_typed_negative(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    staged = _run(cfg, "projects_create", {"name": "Cancel Me"})
    assert staged["state"] == "needs-confirm"
    assert _cancel(cfg, staged)["state"] == "executed"
    assert _confirm(cfg, staged)["state"] == "failed"
    assert _run(cfg, "projects_list", {})["receipt"]["result"]["projects"] \
        == []
    missing = _run(cfg, "projects_show", {"slug": "no-such"})
    assert missing["state"] == "failed", missing
    assert _run(cfg, "project_add_folder",
                {"slug": "x"})["code"] == "BAD_ARGS"


# ------------------------------------------------------------ experiments
def _register(cfg, role, label, model):
    out = _full(cfg, "experiments_register",
                {"role": role, "label": label, "model": model})
    assert out["state"] == "executed", out
    return _res(out)["id"]


def test_experiments_full_lifecycle_and_persistence(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    assert _run(cfg, "experiments_status", {})["state"] == "executed"
    champ = _register(cfg, "champion", "Champ", "m-a")
    chall = _register(cfg, "challenger", "Chall", "m-b")
    started = _full(cfg, "experiments_start",
                    {"champion": champ, "challenger": chall})
    assert _res(started)["status"] == "running"
    exp_id = _res(started)["id"]
    trial = _full(cfg, "experiments_trial",
                  {"experiment_id": exp_id, "variant": "champion"})
    trial_id = _res(trial)["id"]
    assert _res(trial)["variant"] == "champion"
    finished = _full(cfg, "experiments_finish",
                     {"trial_id": trial_id, "outcome": "completed"})
    assert _res(finished)["outcome"] == "completed"
    signal = _full(cfg, "experiments_signal",
                   {"trial_id": trial_id, "kind": "explicit", "value": "0.9"})
    assert _res(signal)["value"] == 0.9

    evaluated = _run(cfg, "experiments_evaluate", {"experiment_id": exp_id})
    assert evaluated["state"] == "executed", evaluated
    assert _res(evaluated)["state"] == "OK"
    shown = _run(cfg, "experiments_show", {"experiment_id": exp_id})
    assert shown["state"] == "executed"
    assert _run(cfg, "experiments_list", {})["state"] == "executed"

    snapshot = _full(cfg, "experiments_snapshot", {})
    assert _res(snapshot)["experiments"], snapshot
    promoted = _full(cfg, "experiments_promote", {"experiment_id": exp_id})
    # honest gate outcome, never a fabricated promotion
    assert _res(promoted)["state"] in ("hold", "promoted", "blocked")

    # persistence: fresh config sees the experiment
    from voicebridge import config as C

    fresh = C.Config.load(tmp_path / "voice.yaml")
    listed = _run(fresh, "experiments_list", {})
    ids = [e["id"] for e in _res(listed)["experiments"]]
    assert exp_id in ids

    aborted = _full(cfg, "experiments_abort", {"experiment_id": exp_id})
    assert _res(aborted)["status"] == "aborted"


def test_experiments_cancel_and_typed_negative(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    staged = _run(cfg, "experiments_register",
                  {"role": "champion", "label": "Cancel"})
    assert staged["state"] == "needs-confirm"
    assert _cancel(cfg, staged)["state"] == "executed"
    assert _confirm(cfg, staged)["state"] == "failed"
    unknown_show = _run(cfg, "experiments_show",
                        {"experiment_id": "exp_nope"})
    assert unknown_show["state"] == "failed", unknown_show
    assert _run(cfg, "experiments_register",
                {"role": "bogus", "label": "x"})["code"] == "BAD_ARGS"


# ---------------------------------------------------------------- skills
def _seed_skill(cfg, skill_id="weather.editor"):
    from voicebridge.capabilities import Tier
    from voicebridge.permissions import SkillManifest, permissions_store

    manifest = SkillManifest(
        skill_id=skill_id, version="1.0.0", name="Weather Editor",
        description="synthetic", source="https://example.test/skills",
        revision="abc123", license="MIT", content_hash="a" * 64,
        capabilities=("web.observe.metadata",), scopes=("web",),
        tier=Tier.READ_ONLY, sensitivity="internal", approval_policy="auto",
        timeout_s=30.0, resource_limits={"max_bytes": 4096},
        audit_events=("skill.run",), compatibility="linux",
        rollback="remove restores prior manifest state")
    permissions_store(cfg).install_skill(manifest, requested_by="user",
                                         confirmed=True)


def test_skills_full_lifecycle_and_persistence(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _seed_skill(cfg)
    shown = _run(cfg, "skills_show", {"skill_id": "weather.editor"})
    assert _res(shown)["skill"]["status"] == "staged"
    enabled = _full(cfg, "skills_enable", {"skill_id": "weather.editor"})
    # _b_skills reports the canonical enabled/disabled boolean; the permissioned
    # record's own stored status remains the persistence truth.
    assert _res(enabled)["status"] == "enabled"
    listed = _run(cfg, "skills_list", {})
    # N14: a manifest-only permissioned record is a capability grant, not a
    # model skill. It stays manageable (show/enable/disable/rollback/remove
    # below) but is not listed as an agent-visible skill — listing it made
    # `qubit skills list` disagree with the agent's directory scan.
    assert all(s["skill_id"] != "weather.editor"
               for s in _res(listed)["skills"])
    disabled = _full(cfg, "skills_disable", {"skill_id": "weather.editor"})
    assert _res(disabled)["status"] == "disabled"
    rolled = _full(cfg, "skills_rollback", {"skill_id": "weather.editor"})
    assert _res(rolled)["status"] in ("active", "disabled", "staged")
    removed = _full(cfg, "skills_remove", {"skill_id": "weather.editor"})
    assert _res(removed)["status"] == "removed"

    from voicebridge import config as C

    fresh = C.Config.load(tmp_path / "voice.yaml")
    assert _run(fresh, "skills_show", {"skill_id": "weather.editor"})[
        "receipt"]["result"]["skill"] is None


def test_skills_cancel_and_typed_negative(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _seed_skill(cfg, "skill.cancel")
    staged = _run(cfg, "skills_enable", {"skill_id": "skill.cancel"})
    assert staged["state"] == "needs-confirm"
    assert _cancel(cfg, staged)["state"] == "executed"
    assert _confirm(cfg, staged)["state"] == "failed"
    assert _run(cfg, "skills_show",
                {"skill_id": "missing.skill"})["receipt"]["result"]["skill"] \
        is None
    assert _run(cfg, "skills_enable", {})["code"] == "BAD_ARGS"


# ---------------------------------------------------------------- themes
def test_themes_lifecycle_and_typed_negative(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    staged = _run(cfg, "themes_set", {"name": "dark"})
    assert staged["state"] == "needs-confirm"
    assert _cancel(cfg, staged)["state"] == "executed"
    assert _confirm(cfg, staged)["state"] == "failed"

    chosen = _full(cfg, "themes_set", {"name": "dark"})
    assert _res(chosen)["theme"] == "qubit-dark"
    tweaked = _full(cfg, "themes_tweak_set",
                    {"tweak": "--bg", "color": "#10141c"})
    assert _res(tweaked)["tweaks"]["--bg"] == "#10141c"
    cleared = _full(cfg, "themes_tweak_clear", {"tweak": "--bg"})
    assert _res(cleared)["tweaks"] == {}
    reset = _full(cfg, "themes_reset", {})
    assert _res(reset)["theme"] == "qubit-dark"
    assert _run(cfg, "themes_set", {"name": "not-a-theme"})["code"] == \
        "BAD_ARGS"
    assert _run(cfg, "themes_tweak_set",
                {"tweak": "--nope", "color": "#fff"})["state"] == \
        "needs-confirm"
    bad = _confirm(cfg, _run(cfg, "themes_tweak_set",
                             {"tweak": "--nope", "color": "#fff"}))
    assert bad["state"] == "failed", bad


# --------------------------------------------------------- brain vault
def test_brain_vault_remove_roundtrip_and_negative(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    staged = _run(cfg, "brain_vault_set", {"name": "api",
                                           "ref": "env:API_KEY"})
    assert staged["state"] == "needs-confirm"
    assert _confirm(cfg, staged)["state"] == "executed"
    listed = _run(cfg, "brain_vault_list", {})
    assert [e["name"] for e in _res(listed)["entries"]] == ["api"]
    # secret value is never persisted or returned
    assert "API_KEY" not in json.dumps(listed)
    removed = _full(cfg, "brain_vault_remove", {"name": "api"})
    assert _res(removed)["removed"] == "api"
    assert _run(cfg, "brain_vault_list", {})["receipt"]["result"]["entries"] \
        == []
    missing = _full(cfg, "brain_vault_remove", {"name": "api"})
    assert missing["state"] == "failed", missing
    assert missing["code"] == "BRAIN_NOT_FOUND", missing


# ---------------------------------------------------------------- ghost
def test_ghost_reads_grants_preview_and_negatives(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    for op_id in ("ghost_list", "ghost_status", "ghost_receipts"):
        assert _run(cfg, op_id, {})["state"] == "executed", op_id
    shown = _run(cfg, "ghost_show", {"action_id": "ghost.input.click"})
    assert _res(shown)["action"]["action_id"] == "ghost.input.click"
    checked = _run(cfg, "ghost_check", {"action_id": "ghost.input.click"})
    assert _res(checked)["allowed"] in (True, False)

    # high-impact grant is a typed refusal (no standing grant is minted)
    high = _full(cfg, "ghost_grant",
                 {"capability": "ghost.input.click", "scope": "web",
                  "target": "desktop"})
    assert high["state"] == "failed", high
    assert high["code"] == "HIGH_IMPACT_CAPABILITY", high

    # a read-only capability grants and revokes for real
    granted = _full(cfg, "ghost_grant",
                    {"capability": "ghost.desktop.discover",
                     "scope": "desktop", "target": "desktop"})
    assert granted["state"] == "executed", granted
    grant_id = _res(granted)["grant"]["grant_id"]
    revoked = _full(cfg, "ghost_revoke", {"grant_id": grant_id})
    assert _res(revoked)["grant_id"] == grant_id

    # dashboard-only preview extra reaches the real preview backend
    preview = _full(cfg, "ghost_preview",
                    {"action_id": "ghost.desktop.discover",
                     "args_json": "{}", "scope": "desktop"})
    assert preview["state"] == "executed", preview
    assert _res(preview)["state"] == "preview"
    # a bad action shape is a typed validation error, never a fabricated run
    bad = _full(cfg, "ghost_preview",
                {"action_id": "ghost.input.click", "args_json": "{}",
                 "scope": "web"})
    assert bad["state"] == "failed", bad
    # unknown receipt cancel is typed
    cancel = _full(cfg, "ghost_cancel", {"receipt_id": "nope"})
    assert cancel["state"] == "failed", cancel


# -------------------------------------------------------------- verified
def _seed_verified_skill(cfg):
    from voicebridge import verified_skills as V

    store = V.verified_skills_store(cfg)
    record = store.submit(V.SkillProposal(
        name="add_two", interface="add_two(x)", implementation="return x + 2",
        acceptance_test="assert run(3)==6", task="add two to a number"))
    store.close()
    return record.id


def test_verified_reads_are_real(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    skill_id = _seed_verified_skill(cfg)
    listed = _run(cfg, "verified_list", {})
    assert listed["state"] == "executed"
    assert any(s["id"] == skill_id for s in _res(listed)["skills"])
    shown = _run(cfg, "verified_show", {"skill_id": skill_id})
    assert _res(shown)["skill"]["id"] == skill_id
    recalled = _run(cfg, "verified_recall", {"query": "add two"})
    assert recalled["state"] == "executed"
    missing = _run(cfg, "verified_show", {"skill_id": "nope"})
    assert missing["state"] == "failed", missing


# ------------------------------------------------------- hermes page rows
# Every vendored hermes-web baseline page row: the ledger id, the on-disk
# component, the Qubit route alias (None when the page is reached directly
# through the shell), the compat API route the page reads, and a real Qubit
# operation the page drives through the shared dispatcher.
PAGE_ROWS = [
    # (row_id, component, alias, method, api, qubit_op)
    ("page_analyticspage", "AnalyticsPage", "/analytics", "GET",
     "/api/analytics", "insights"),
    ("page_agentspage", "AgentsPage", None, "GET", "/api/qubit/agents",
     "status"),
    ("page_channelspage", "ChannelsPage", "/channels", "GET",
     "/api/messaging/platforms", "channels_status"),
    ("page_chatpage", "ChatPage", "/qubit-chat", "POST", "/api/qubit/chat",
     "status"),
    ("page_terminalchatpage", "TerminalChatPage", "/chat", "POST",
     "/api/auth/ws-ticket", "tui_help"),
    ("page_configpage", "ConfigPage", "/config", "GET", "/api/config",
     "config_profiles"),
    ("page_cronpage", "CronPage", "/cron", "GET", "/api/cron/jobs",
     "schedules_list"),
    ("page_docspage", "DocsPage", "/docs", None, None, "tui_help"),
    ("page_envpage", "EnvPage", "/env", "GET", "/api/env", "flags"),
    ("page_filespage", "FilesPage", "/files", "GET", "/api/files",
     "git_status"),
    ("page_logspage", "LogsPage", "/logs", "GET", "/api/logs", "logs"),
    ("page_mcppage", "McpPage", "/mcp", "GET", "/api/mcp", "mcp"),
    ("page_modelspage", "ModelsPage", "/models", "GET",
     "/api/local-models/catalog", "local_models_status"),
    ("page_pairingpage", "PairingPage", "/pairing", "GET", "/api/pairing",
     "pairing_list"),
    ("page_pluginspage", "PluginsPage", "/plugins", "GET",
     "/api/dashboard/plugins", "plugins_list"),
    ("page_profilebuilderpage", "ProfileBuilderPage", "/profiles", "GET",
     "/api/profiles", "config_profiles"),
    ("page_profilespage", "ProfilesPage", "/profiles", "GET", "/api/profiles",
     "config_profiles"),
    ("page_sessionspage", "SessionsPage", "/sessions", "GET", "/api/sessions",
     "sessions"),
    ("page_skillspage", "SkillsPage", "/skills", "GET", "/api/skills",
     "skills_list"),
    ("page_systempage", "SystemPage", "/system", "GET", "/api/system/stats",
     "status"),
    ("page_webhookspage", "WebhooksPage", "/webhooks", "GET", "/api/webhooks",
     "webhooks"),
    ("page_voiceeditpage", "VoiceEditPage", "/voice-edit", None, None,
     "status"),
]


def test_every_qubit_web_page_served_through_real_route(tmp_path,
                                                         monkeypatch):
    """All vendored baseline pages + the Qubit terminal page render/serve.

    For each ``page_*`` ledger row: the vendored component exists on disk, its
    Qubit route redirects into the Hermes shell, the page's compat API route is
    registered (and dispatched for safe reads), and a representative Qubit
    operation executes through ``dashboard_ops.run_op``. A fabricated route is
    a typed 404/NOT_FOUND, never a fabricated render.
    """
    from voicebridge import qubit_compat as HC
    from voicebridge.webui import qubit_alias_target

    cfg = _cfg(tmp_path, monkeypatch)
    pages_dir = REPO_ROOT / "frontend" / "qubit-web" / "src" / "pages"
    assert len(PAGE_ROWS) == 22
    for row_id, component, alias, method, api, op_id in PAGE_ROWS:
        assert row_id  # ledger row id literal for the proving-test index
        assert (pages_dir / f"{component}.tsx").is_file(), component
        target = alias if alias else None
        if alias:
            assert qubit_alias_target(alias) == target, (row_id, alias)
        if api:
            route, _ = HC.match(method, api)
            assert route is not None, (row_id, method, api)
            if method == "GET":
                status, _body = HC.dispatch(cfg, "GET", api)
                assert status != 404, (row_id, api, status)
        out = _run(cfg, op_id, {})
        assert out["state"] == "executed", (row_id, op_id, out)
        assert out["receipt"]["entry"] == op_id


def test_unknown_page_and_route_are_typed_negatives(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge.webui import qubit_alias_target

    cfg = _cfg(tmp_path, monkeypatch)
    assert qubit_alias_target("/agent3-not-a-page") is None
    status, body = HC.dispatch(cfg, "GET", "/api/agent3-not-a-route")
    assert status == 404, (status, body)
    assert body.get("code") == "NOT_FOUND", body


def test_agents_route_is_a_shell_route_not_the_api_namespace():
    """`/agents` is a real SPA page; only `/agent` (+ `/agent/...`) is API.

    A prior regression matched any path starting with ``/agent``, so the
    ``/agents`` page hard-loaded 404 while its client-side nav worked.
    """
    from voicebridge.webui import SPA_ROUTE_SET, qubit_alias_target

    assert "/agents" in SPA_ROUTE_SET
    assert qubit_alias_target("/agents") == "/agents"
    assert qubit_alias_target("/agents/team-1") == "/agents/team-1"
    # The SPA API namespace and the gone upstream mount stay non-shell.
    assert qubit_alias_target("/agent") is None
    assert qubit_alias_target("/agent/api/status") is None
    assert qubit_alias_target("/hermes") is None


def test_action_status_route_serves_the_real_recorded_outcome(
        tmp_path, monkeypatch):
    """`GET /api/actions/<name>/status` must answer the viewer's real shape.

    The upstream action-log viewer polls this path. The generic
    ``/api/actions/.*`` wildcard previously shadowed it and returned the
    actions *list* shape, so ``lines`` was undefined and the page crashed.
    """
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    # Isolate the process-local registry from any earlier action in this run.
    HC._ACTION_STATUS.clear()
    # A name that has not run is a typed refusal, never a fabricated success.
    status, body = HC.dispatch(cfg, "GET", "/api/actions/doctor/status")
    assert status == 404, (status, body)
    assert body["ok"] is False
    assert body["code"] == "ACTION_NOT_TRACKED"
    # Running the real action records its terminal outcome under its name.
    status, run = HC.dispatch(cfg, "POST", "/api/ops/doctor", body={})
    assert status == 200, (status, run)
    status, body = HC.dispatch(cfg, "GET", "/api/actions/doctor/status")
    assert status == 200, (status, body)
    assert body["name"] == "doctor"
    assert body["running"] is False
    assert body["exit_code"] == 0
    assert isinstance(body["lines"], list) and body["lines"]
