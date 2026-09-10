"""Packet 8T-F — voice command registry: full pipeline behavioral tests.

Proves every implemented capability is voice-addressable (resolution +
execution through policy gates), ambiguous/incomplete/unknown inputs are
handled honestly, confirmations are single-use expiring shared-journal
records, e-stop/source/redaction/cancellation hold, and the orchestrator
branch never hijacks non-command turns.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _ctx(cfg, session="voice", source="user"):
    from voicebridge import voice_commands as VC

    return VC.make_ctx(cfg, session=session, source=source)


# ------------------------------------------------- coverage + schema
def test_registry_covers_every_implemented_row():
    from voicebridge import voice_commands as VC

    par = json.loads((Path(VC.__file__).resolve().parents[1] / "docs" /
                      "PARITY_HERMES.json").read_text())
    mat = json.loads((Path(VC.__file__).resolve().parents[1] / "docs" /
                      "MATRIX_8T_F.json").read_text())
    covered = set()
    for entry in VC.REGISTRY.values():
        covered.update(entry.parity)
    need_par = {r["cmd"] for r in par["rows"] if r["verdict"] == "IMPLEMENTED"}
    assert (need_par - covered) <= set(VC.FALLBACK_ROWS), \
        f"uncovered parity rows: {sorted((need_par - covered) - set(VC.FALLBACK_ROWS))}"
    need_mat = {r["id"] for r in mat["rows"]
                if r.get("verdict") in ("REUSE", "ADAPT")}
    assert need_mat <= set(VC.MATRIX_COVERAGE), \
        f"unmapped matrix rows: {sorted(need_mat - set(VC.MATRIX_COVERAGE))}"
    for matrix_id, entry_id in VC.MATRIX_COVERAGE.items():
        assert entry_id in VC.REGISTRY, f"{matrix_id} -> missing {entry_id}"


def test_fallback_rows_are_all_chat_flags():
    from voicebridge import voice_commands as VC

    assert "chat" in VC.FALLBACK_ROWS
    assert all(r.startswith(("-", "--")) or r == "worktree/--worktree"
               for r in VC.FALLBACK_ROWS if r != "chat")


def test_entry_schema_hygiene():
    from voicebridge import voice_commands as VC

    assert len(VC.REGISTRY) >= 80, "registry must span every capability"
    for entry in VC.REGISTRY.values():
        assert entry.backend in VC._BACKENDS, f"{entry.id}: unwired backend"
        assert entry.aliases and entry.examples and entry.help, entry.id
        assert entry.scope, entry.id
        assert isinstance(entry.confirm, bool)
        for alias in entry.aliases:
            assert alias == alias.lower().strip(), (entry.id, alias)


def test_no_secret_shaped_arguments():
    from voicebridge import voice_commands as VC

    banned = ("passphrase", "password", "secret", "token", "api_key", "cookie")
    for entry in VC.REGISTRY.values():
        for arg in entry.args:
            assert arg.name.lower() not in banned, (entry.id, arg.name)


MUTATING_ACTIONS = {
    "browser_tabs": {"open_browser", "activate", "open", "navigate",
                     "fill", "click", "close"},
    "kanban": {"create", "claim", "complete", "comment", "init",
               "boards_create", "assign", "reassign", "reclaim", "edit",
               "block", "unblock", "archive"},
    "projects": {"create", "use", "archive", "rename", "add_folder",
                 "remove_folder", "set_primary", "restore", "rollback",
                 "bind_board"},
    "skills": {"enable", "disable", "remove", "rollback"},
    "checkpoints": {"create", "prune"},
    "hooks": {"fire", "declare", "revoke", "remove"},
    "webhook": {"add", "remove"},
    "gateway": {"add", "remove"},
    "themes": {"set", "tweak_set", "tweak_clear", "reset"},
    "memory": {"remember", "forget"},
    "pairing": {"create", "approve", "revoke"},
    "msg": {"draft", "approve", "send", "account_add", "account_remove",
            "retry", "draft_reply"},
    "peer": {"dm", "run", "add", "remove"},
    "brain": {"principal_add", "member_add", "member_remove", "profile_set",
              "profile_forget", "profile_sweep", "account_add",
              "account_remove", "vault_set", "vault_remove"},
    "lessons": {"promote", "remove"},
    "lsp": {"restart"},
    "task_run": {"run"},
    "actions": {"execute", "cancel"},
    "ghost": {"grant", "revoke", "execute", "cancel", "enable", "disable",
              "emergency-stop", "reset"},
    "opencode": set(),
    "verified": {"verdict", "pin", "unpin"},
    "experiments": {"register_candidate", "start", "trial", "finish_trial",
                    "signal", "abort", "snapshot", "promote"},
    "product": {"install", "migrate", "rollback", "repair"},
    "package": set(),
    "webui": set(),
    "frontier_chat": set(),
    "frontier_show": set(),
    "commands": set(),
    "audio_hw": set(),
    "loop": set(),
    "device_enroll": {"enroll"},
    "branding": {"migrate"},
    "config_profiles": {"select", "reset", "upgrade", "remove", "rollback"},
    "vision_describe": set(),
    "plugins_install": set(),
    "worktree": set(),
    "monitor": set(),
    "pause": set(),
    "resume": set(),
}


def test_mutating_backends_need_confirmation():
    from voicebridge import voice_commands as VC

    for entry in VC.REGISTRY.values():
        fixed_action = dict(entry.fixed).get("action")
        mutating = MUTATING_ACTIONS.get(entry.backend, set())
        if entry.backend in ("vision_describe", "plugins_install", "worktree",
                             "monitor", "pause", "resume", "frontier_chat",
                             "task_run"):
            assert entry.confirm, f"{entry.id}: heavy op must confirm"
        elif fixed_action in mutating:
            assert entry.confirm, f"{entry.id}: mutating action must confirm"


# ------------------------------------------------- resolution table
COMPLETE_EXAMPLES = {
    "status": "qubit, system status",
    "pause": "qubit, pause everything",
    "resume": "qubit, resume work",
    "doctor": "qubit, run diagnostics",
    "setup_check": "qubit, setup status",
    "verify_release": "qubit, verify release",
    "security": "qubit, security status",
    "diagnostics": "qubit, show diagnostics",
    "backup_info": "qubit, how do i back up",
    "export_state_voice": "qubit, export my state",
    "import_state_voice": "qubit, import my state",
    "sessions": "qubit, list sessions",
    "capability": "qubit, is whatsapp available",
    "capabilities_list": "qubit, show available capabilities",
    "checkpoints_list": "qubit, list checkpoints",
    "checkpoints_write": "qubit, create checkpoint deploy",
    "auth_status": "qubit, credential status",
    "auth_login": "qubit, how do i log in xai",
    "auth_verify": "qubit, is xai configured",
    "auth_logout": "qubit, logout check",
    "mcp": "qubit, list mcp servers",
    "worker": "qubit, worker status",
    "memory_search": "qubit, what do you remember about invoices",
    "memory_remember": "qubit, remember the gate code",
    "memory_forget": "qubit, forget memory 12",
    "skills_list": "qubit, list skills",
    "skills_show": "qubit, describe skill weather",
    "skills_enable": "qubit, enable skill weather",
    "skills_disable": "qubit, disable skill weather",
    "projects_list": "qubit, list projects",
    "projects_show": "qubit, show project shop",
    "projects_create": "qubit, create project shop",
    "projects_use": "qubit, switch to project shop",
    "projects_archive": "qubit, archive project shop",
    "projects_active": "qubit, open my active project",
    "kanban_boards": "qubit, list boards",
    "kanban_list": "qubit, list tasks",
    "kanban_show": "qubit, show task 5",
    "kanban_current": "qubit, inspect the current task",
    "kanban_create": "qubit, create task buy milk",
    "kanban_claim": "qubit, claim task 5",
    "kanban_complete": "qubit, complete task 5",
    "kanban_comment": "qubit, comment on task 5 looks good",
    "themes": "qubit, what theme",
    "themes_set": "qubit, set theme light",
    "proxy": "qubit, proxy status",
    "import_inspect": "qubit, inspect foreign setup at /tmp/x",
    "import_refusal": "qubit, import foreign setup",
    "hooks_list": "qubit, list hooks",
    "hooks_show": "qubit, show hook deploy",
    "hooks_fire": "qubit, fire hook deploy",
    "sync_list": "qubit, sync status",
    "vision_status": "qubit, is vision ready",
    "vision_describe": "qubit, describe image at /tmp/pic.png",
    "pairing_list": "qubit, list pairing codes",
    "pairing_create": "qubit, pair a device",
    "pairing_approve": "qubit, pair with code AB12CD34",
    "pairing_revoke": "qubit, revoke pairing abc",
    "webhooks": "qubit, list webhooks",
    "msg_accounts": "qubit, list messaging accounts",
    "msg_link": "qubit, check account team",
    "msg_inbox": "qubit, check messages",
    "msg_show": "qubit, show message",
    "msg_draft": "qubit, text sam saying running late",
    "msg_send": "qubit, send draft",
    "msg_approve": "qubit, approve draft",
    "msg_summarize": "qubit, read message",
    "gateway": "qubit, list gateways",
    "peer_list": "qubit, list peers",
    "peer_dm": "qubit, tell peer relay hello",
    "peer_run": "qubit, run on peer relay status",
    "brain": "qubit, list profiles",
    "permissions": "qubit, permission mode",
    "monitor": "qubit, run monitor check",
    "model": "qubit, which model",
    "audio": "qubit, list audio devices",
    "device": "qubit, device status",
    "flags": "qubit, list flags",
    "logs": "qubit, show logs",
    "insights": "qubit, activity summary",
    "prompt_budget": "qubit, prompt budget",
    "reasoning": "qubit, reasoning levels",
    "approvals": "qubit, pending approvals",
    "tools": "qubit, list tools",
    "secrets": "qubit, secret status",
    "worktree": "qubit, clean worktrees",
    "completions": "qubit, shell completions",
    "tui_help": "qubit, open dashboard",
    "console_help": "qubit, terminal help",
    "mic_help": "qubit, check microphone",
    "acp": "qubit, editor check",
    "lsp": "qubit, language server check",
    "browser_status": "qubit, browser status",
    "computer_status": "qubit, desktop status",
    "plugins_install": "qubit, install plugin from manifest",
    "curator": "qubit, learning summary",
    "update_status": "qubit, update status",
    "config_profiles": "qubit, list config profiles",
    "migrate_status": "qubit, migration status",
    "brain_principal_add": "qubit, add brain principal maria",
    "brain_member_add": "qubit, link brain member acme maria",
    "brain_member_remove": "qubit, unlink brain member acme maria",
    "brain_profile_set": "qubit, set brain field maria timezone",
    "brain_profile_forget": "qubit, forget brain field maria",
    "brain_profile_sweep": "qubit, sweep brain fields",
    "brain_profile_export": "qubit, export brain profile maria",
    "brain_account_add": "qubit, add brain account shop",
    "brain_account_remove": "qubit, remove brain account shop",
    "brain_vault_list": "qubit, list vault references",
    "brain_vault_set": "qubit, record vault reference shop-key",
    "brain_vault_remove": "qubit, remove vault reference shop-key",
    "brain_onboard_plan": "qubit, brain setup plan",
    "hooks_declare": "qubit, declare hook deploy",
    "hooks_validate": "qubit, validate hook deploy",
    "hooks_test": "qubit, hook test plan deploy",
    "hooks_revoke": "qubit, revoke hook deploy",
    "hooks_remove": "qubit, remove hook deploy",
    "hooks_doctor": "qubit, hook health",
    "webhook_add": "qubit, add webhook alerts",
    "webhook_show": "qubit, show webhook alerts",
    "webhook_remove": "qubit, remove webhook alerts",
    "webhook_test": "qubit, webhook test plan alerts",
    "gateway_show": "qubit, show gateway main",
    "gateway_add": "qubit, add gateway main",
    "gateway_remove": "qubit, remove gateway main",
    "gateway_link": "qubit, link gateway main",
    "peer_show": "qubit, show peer office",
    "peer_add": "qubit, add peer office",
    "peer_remove": "qubit, remove peer office",
    "peer_link": "qubit, link peer office",
    "msg_account_add": "qubit, add messaging account team",
    "msg_account_show": "qubit, show messaging account team",
    "msg_account_remove": "qubit, remove messaging account team",
    "msg_list": "qubit, list outbox",
    "msg_retry": "qubit, retry message",
    "msg_draft_reply": "qubit, draft reply to team saying on my way",
    "kanban_init": "qubit, init task board",
    "kanban_boards_create": "qubit, create board home",
    "kanban_assign": "qubit, assign task 3 to sam",
    "kanban_reassign": "qubit, reassign task 3 to jo",
    "kanban_reclaim": "qubit, reclaim task 3",
    "kanban_edit": "qubit, edit task 3 buy oat milk",
    "kanban_block": "qubit, block task 3",
    "kanban_unblock": "qubit, unblock task 3",
    "kanban_archive": "qubit, archive task 3",
    "kanban_stats": "qubit, board stats",
    "kanban_log": "qubit, board log",
    "kanban_export": "qubit, export board",
    "kanban_open": "qubit, open the board",
    "kanban_move": "qubit, move this task 3",
    "kanban_widget_add": "qubit, add board widget demo note",
    "kanban_widget_list": "qubit, list board widgets",
    "kanban_widget_remove": "qubit, remove board widget 2",
    "kanban_layout_show": "qubit, show board layout",
    "project_rename": "qubit, rename project shop",
    "project_add_folder": "qubit, add project folder shop",
    "project_remove_folder": "qubit, remove project folder shop",
    "project_set_primary": "qubit, set primary folder shop",
    "project_restore": "qubit, restore project shop",
    "project_rollback": "qubit, rollback projects",
    "project_bind_board": "qubit, bind board shop",
    "skills_remove": "qubit, remove skill notes",
    "skills_rollback": "qubit, rollback skill notes",
    "themes_tweak_set": "qubit, tweak theme color accent",
    "themes_tweak_clear": "qubit, clear theme tweak accent",
    "themes_reset": "qubit, reset theme",
    "computer_doctor": "qubit, desktop health",
    "computer_permissions": "qubit, desktop permissions",
    "computer_install_refusal": "qubit, install desktop driver",
    "browser_close_refusal": "qubit, close browser profile",
    "browser_doctor": "qubit, browser doctor",
    "browser_tabs": "qubit, list open tabs",
    "browser_read": "qubit, read the current tab",
    "browser_find": "qubit, search this page invoices",
    "browser_open_browser": "qubit, open the browser",
    "browser_activate": "qubit, switch to tab inbox",
    "browser_open": "qubit, open page https://example.com",
    "browser_navigate": "qubit, navigate to https://example.com",
    "browser_fill": "qubit, fill form email bob@example.com",
    "browser_click": "qubit, click sign in",
    "browser_close_tab": "qubit, close this tab",
    "schedules_create": "qubit, schedule task check inbox every 30 minutes",
    "schedules_list": "qubit, list schedules",
    "schedules_show": "qubit, show schedule 2",
    "schedules_pause": "qubit, pause schedule 2",
    "schedules_resume": "qubit, resume schedule 2",
    "schedules_delete": "qubit, delete schedule 2",
    "schedules_history": "qubit, schedule history",
    "schedules_run_due": "qubit, run due schedules",
    "files_inspect": "qubit, inspect this project folder",
    "files_list": "qubit, list project files",
    "files_read": "qubit, read project file notes",
    "ide_status": "qubit, which editor",
    "ide_open": "qubit, open my ide",
    "wake_status": "qubit, is wake word ready",
    "proxy_start_refusal": "qubit, start proxy",
    "proxy_providers": "qubit, proxy providers",
    "acp_serve_refusal": "qubit, start acp server",
    "acp_accept_hooks_refusal": "qubit, acp accept hooks",
    "lsp_restart": "qubit, restart language servers",
    "lsp_install_refusal": "qubit, install language server",
    "lessons_list": "qubit, list lessons",
    "lessons_promote": "qubit, promote lesson abc",
    "lessons_remove": "qubit, remove lesson abc",
    "power_status": "qubit, power status",
    "websites_list": "qubit, list websites",
    "websites_show": "qubit, show website example",
    # --- task-mandated natural commands + 8F/8N/8P/8Q/product coverage
    "task_run": "qubit, run the task summarize my notes",
    "task_run_current": "qubit, run this task",
    "actions_list": "qubit, list actions",
    "actions_show": "qubit, show action demo",
    "actions_check": "qubit, check action demo",
    "actions_receipts": "qubit, action receipts",
    "actions_receipt": "qubit, show action receipt abc123",
    "actions_execute": "qubit, execute action demo",
    "actions_cancel": "qubit, cancel action abc123",
    "ghost_list": "qubit, list ghost capabilities",
    "ghost_status": "qubit, ghost status",
    "ghost_show": "qubit, show ghost capability demo",
    "ghost_check": "qubit, check ghost capability demo",
    "ghost_grant": "qubit, grant ghost session screenshots",
    "ghost_revoke": "qubit, revoke ghost grant abc123",
    "ghost_execute": "qubit, execute ghost action demo",
    "ghost_cancel": "qubit, cancel ghost action abc123",
    "ghost_receipts": "qubit, ghost receipts",
    "ghost_enable": "qubit, enable ghost",
    "ghost_disable": "qubit, disable ghost",
    "ghost_estop": "qubit, stop ghost",
    "ghost_reset": "qubit, reset ghost",
    "ghost_action_preview": "qubit, preview ghost action ghost.desktop.discover",
    "ghost_click": "qubit, click this button submit",
    "ghost_type": "qubit, type into this field name hello",
    "ghost_read_tab": "qubit, read the browser tab",
    "ghost_action_retry": "qubit, retry ghost action demo",
    "ghost_action_recover": "qubit, recover ghost action demo",
    "runtime_images": "qubit, list runtime images",
    "runtime_status": "qubit, runtime status",
    "runtime_doctor": "qubit, diagnose runtimes",
    "runtime_install": "qubit, install runtime opencode-binary",
    "runtime_upgrade": "qubit, upgrade runtime opencode-binary",
    "runtime_rollback": "qubit, roll back runtime opencode-binary",
    "runtime_remove": "qubit, remove runtime opencode-binary",
    "opencode_status": "qubit, opencode status",
    "opencode_list": "qubit, list opencode capabilities",
    "opencode_show": "qubit, show opencode capability demo",
    "opencode_check": "qubit, check opencode capability demo",
    "opencode_grant": "qubit, allow opencode run",
    "opencode_revoke": "qubit, revoke opencode grant demo",
    "opencode_preview": "qubit, preview coding task add tests",
    "opencode_run": "qubit, run this opencode task add tests",
    "opencode_cancel": "qubit, cancel coding run demo",
    "opencode_retry": "qubit, retry coding run demo add tests",
    "opencode_recover": "qubit, recover coding run demo",
    "opencode_receipts": "qubit, list coding runs",
    "verified_list": "qubit, list verified skills",
    "verified_show": "qubit, show verified skill demo",
    "verified_recall": "qubit, recall skills for pdf reports",
    "verified_verdict": "qubit, verify skill demo verified",
    "verified_pin": "qubit, pin skill demo",
    "verified_unpin": "qubit, unpin skill demo",
    "experiments_status": "qubit, experiment status",
    "experiments_list": "qubit, list experiments",
    "experiments_show": "qubit, show experiment exp1",
    "experiments_evaluate": "qubit, evaluate experiment exp1",
    "experiments_register": "qubit, register candidate challenger nightly",
    "experiments_start": "qubit, start experiment cfg1 cfg2",
    "experiments_trial": "qubit, start trial exp1 fast",
    "experiments_finish": "qubit, finish trial tr1 completed",
    "experiments_signal": "qubit, record signal tr1 explicit 0.8",
    "experiments_abort": "qubit, abort experiment exp1",
    "experiments_snapshot": "qubit, snapshot experiments",
    "experiments_promote": "qubit, promote experiment exp1",
    "product_status": "qubit, product status",
    "product_probe": "qubit, probe install",
    "product_first_run": "qubit, first run check",
    "product_install": "qubit, install product",
    "product_migrate": "qubit, migrate install",
    "product_rollback": "qubit, roll back product",
    "product_repair": "qubit, repair install",
    "package_certify": "qubit, certify release",
    "package_status": "qubit, package status",
    "webui_status": "qubit, dashboard status",
    "reports_list": "qubit, show my reports",
    "frontier_chat": "qubit, ask frontier why is the sky blue",
    "frontier_show": "qubit, show model local/qubit-brain4",
    "commands_list": "qubit, what can you do",
    "device_enroll": "qubit, enroll this device",
    "branding_plan": "qubit, branding plan",
    "branding_verify": "qubit, verify branding",
    "branding_migrate": "qubit, apply migration",
    "mic_test": "qubit, microphone test",
    "asr_test": "qubit, transcription test",
    "tts_test": "qubit, speech test",
    "enroll_voice": "qubit, enroll my voice",
    "verify_voice": "qubit, verify my voice",
    "roundtrip_test": "qubit, test yourself",
    "loop_start": "qubit, start listening",
    "profile_show": "qubit, show config profile kind audio",
    "profile_check": "qubit, check profile audio",
    "profile_select": "qubit, select profile audio pro",
    "profile_reset": "qubit, reset profile audio",
    "profile_upgrade": "qubit, upgrade profile audio",
    "profile_remove": "qubit, remove profile audio pro",
    "profile_rollback": "qubit, roll back profile audio",
}


def test_every_entry_resolves_its_example():
    from voicebridge import voice_commands as VC

    assert set(COMPLETE_EXAMPLES) == set(VC.REGISTRY), \
        f"example table drift: {sorted(set(VC.REGISTRY) ^ set(COMPLETE_EXAMPLES))}"
    for entry_id, text in COMPLETE_EXAMPLES.items():
        res = VC.resolve(text)
        assert res.kind == VC.MATCH, (entry_id, text, res.kind, res.speech)
        assert res.entry is not None and res.entry.id == entry_id, \
            (entry_id, text, getattr(res.entry, "id", None))


def test_single_word_and_unknown_fall_through():
    from voicebridge import voice_commands as VC

    for text in ("status", "list", "help", "stop", "do the thing",
                 "blargle wibble zzz", ""):
        assert VC.resolve(text).kind == VC.UNKNOWN, text
    res = VC.resolve("qubit status")
    assert res.kind == VC.MATCH and res.entry.id == "status"


def test_clarify_and_ambiguous():
    from voicebridge import voice_commands as VC

    res = VC.resolve("qubit, claim task")
    assert res.kind == VC.CLARIFY and "task" in res.speech.lower()
    res = VC.resolve("qubit, text sam")
    assert res.kind == VC.CLARIFY, "to-saying without saying clarifies"
    a = VC.VoiceCommand(id="__ta", title="A", aliases=("toggle mode",),
                        backend="status", parity=())
    b = VC.VoiceCommand(id="__tb", title="B", aliases=("toggle mode",),
                        backend="status", parity=())
    VC.REGISTRY["__ta"] = a
    VC.REGISTRY["__tb"] = b
    try:
        res = VC.resolve("qubit, toggle mode")
        assert res.kind == VC.AMBIGUOUS, res.kind
        assert "Did you mean" in res.speech
    finally:
        del VC.REGISTRY["__ta"]
        del VC.REGISTRY["__tb"]


def test_arg_splits():
    from voicebridge import voice_commands as VC

    res = VC.resolve("qubit, text sam saying running late")
    assert res.kind == VC.MATCH and res.entry.id == "msg_draft"
    assert res.args == {"to": "sam", "body": "running late"}, res.args
    res = VC.resolve("qubit, tell peer relay hello there")
    assert res.entry.id == "peer_dm"
    assert res.args == {"peer_id": "relay", "text": "hello there"}, res.args
    res = VC.resolve("qubit, comment on task 5 looks good today")
    assert res.args == {"task_id": 5, "body": "looks good today"}, res.args
    res = VC.resolve("qubit, describe image at /tmp/pic.png")
    assert res.args["image"] == "/tmp/pic.png", res.args
    res = VC.resolve("qubit, show task five")
    assert res.kind == VC.CLARIFY, "words are not numbers"


# ------------------------------------------------- execution
def test_read_executes_with_receipt(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    out = VC.execute(ctx, VC.resolve("qubit, list skills"))
    assert out.kind == VC.EXECUTED
    assert out.receipt["entry"] == "skills_list"
    assert out.speech, "speech always present"


def test_confirm_flow_single_use_and_shared_journal(tmp_path, monkeypatch):
    from voicebridge import approvals as AP
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    out = VC.execute(ctx, VC.resolve("qubit, create task buy milk"))
    assert out.kind == VC.NEEDS_CONFIRM and out.confirmation_id
    assert "yes to confirm" in out.speech.lower()
    journal = AP.ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl")
    pending = [a for a in journal.pending()
               if a.capability == "voice.kanban_create"]
    assert len(pending) == 1, "dashboard reads the same journal"
    done = VC.confirm_pending(ctx, out.confirmation_id)
    assert done.kind == VC.EXECUTED
    assert "buy milk" in done.speech or "Task" in done.speech
    again = VC.confirm_pending(ctx, out.confirmation_id)
    assert again.kind == VC.FAILED and "gone" in again.speech.lower()
    latest = VC.confirm_pending(ctx)
    assert latest.kind == VC.FAILED, "nothing left pending"


def test_confirm_expiry(tmp_path, monkeypatch):
    import time as _time

    from voicebridge import approvals as AP
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    journal = AP.ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl",
                                 default_ttl_s=1.0)
    item = journal.create("voice.test", "test", ttl_s=1.0, session="voice")
    _time.sleep(1.15)
    assert item.id not in {a.id for a in journal.pending()}, "expired"


def test_cancel_denies_pending(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    out = VC.execute(ctx, VC.resolve("qubit, create task buy milk"))
    assert out.kind == VC.NEEDS_CONFIRM
    cancelled = VC.cancel_pending(ctx)
    assert cancelled.kind == VC.EXECUTED and "Cancelled 1" in cancelled.speech
    gone = VC.confirm_pending(ctx, out.confirmation_id)
    assert gone.kind == VC.FAILED


def test_estop_refuses_mutations_not_reads(tmp_path, monkeypatch):
    from voicebridge import power as PW
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    PW.power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                    issued_by="cli:user")
    try:
        out = VC.execute(ctx, VC.resolve("qubit, create task buy milk"))
        assert out.kind == VC.REFUSED and "latched" in out.speech.lower()
        out = VC.execute(ctx, VC.resolve("qubit, list skills"))
        assert out.kind == VC.EXECUTED, "reads stay available"
    finally:
        PW.power_control_store(cfg).set(emergency_stop=False,
                                        global_disable=False,
                                        issued_by="cli:user")


def test_source_gate_refuses_model_text(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg, source="model")
    out = VC.execute(ctx, VC.resolve("qubit, list skills"))
    assert out.kind == VC.REFUSED
    assert out.receipt == {}, "refused work leaves no receipt"


def test_redaction_in_summary_and_audit(tmp_path, monkeypatch):
    from voicebridge import logging_setup as LS
    from voicebridge import voice_commands as VC
    from voicebridge.logging_setup import setup

    cfg = _cfg(tmp_path, monkeypatch)
    setup(level="INFO", jsonl=True, state_dir=cfg.state_dir())
    try:
        ctx = _ctx(cfg)
        out = VC.execute(ctx, VC.resolve("qubit, remember token=abc123secret"))
        assert out.kind == VC.NEEDS_CONFIRM
        blob = (Path(cfg.state_dir()) / "events.jsonl").read_text()
        assert "abc123secret" not in blob
        assert "voice.needs_confirm" in blob
        speech = VC.summarize(out.entry, {"state": "OK", "x": "sk-live-1"})
        assert "sk-live-1" not in speech
    finally:
        LS._EVENT_FILE = None


# ------------------------------------------------- orchestrator + CLI
class _FakeOrch:
    def __init__(self, cfg):
        self.cfg = cfg
        self.spoken = []
        self.feed = []

    def speak(self, text, *a, **k):
        self.spoken.append(text)

    def feed_push(self, item):
        self.feed.append(item)


def test_orchestrator_branch_executes_and_falls_through(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    orch = _FakeOrch(cfg)
    assert VC.handle_turn(orch, "conversation", "qubit, list skills") is True
    assert orch.spoken, "reads speak immediately"
    assert orch.feed and orch.feed[0]["route"] == "voice:skills_list"
    orch2 = _FakeOrch(cfg)
    assert VC.handle_turn(orch2, "conversation", "do the thing") is False
    assert orch2.spoken == [], "non-commands never speak"
    assert VC.handle_turn(orch2, "confirm", "qubit, list skills") is False, \
        "confirm turns never enter the registry"
    assert VC.handle_turn(orch2, "cancel", "qubit, list skills") is False


def test_orchestrator_clarify_speaks_question(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    orch = _FakeOrch(cfg)
    assert VC.handle_turn(orch, "conversation", "qubit, claim task") is True
    assert "task" in orch.spoken[0].lower()


def test_orchestrator_confirm_and_cancel_loop(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    orch = _FakeOrch(cfg)
    assert VC.handle_turn(orch, "conversation", "qubit, create task buy milk") is True
    assert "yes to confirm" in orch.spoken[0].lower()
    assert VC.confirm_voice_turn(orch) is True
    assert orch.spoken[-1], "execution speaks a receipt summary"
    assert VC.confirm_voice_turn(orch) is False, "nothing left"
    VC.handle_turn(orch, "conversation", "qubit, create task second")
    VC.cancel_voice_turn(orch)
    assert VC.confirm_voice_turn(orch) is False, "cancelled is gone"


def test_cli_voice_group(tmp_path, monkeypatch):
    from voicebridge import cli as CLI

    cfg = _cfg(tmp_path, monkeypatch)

    class _A:
        def __init__(self, **kw):
            self.voice_command = kw.get("cmd", "commands")
            self.__dict__.update(kw)

    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_voice(cfg, _A(cmd="commands")) == 0
    body = json.loads(buf.getvalue())
    assert len(body["commands"]) >= 80
    assert all("aliases" in c and "help" in c for c in body["commands"])
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_voice(cfg, _A(cmd="say", text=["list", "skills"],
                                      session="cli", json=False)) == 0
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_voice(cfg, _A(cmd="say", text=["blargle", "zzz"],
                                    session="cli", json=True))
    assert rc == 1
    assert json.loads(buf.getvalue())["state"] == "unknown"
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_voice(cfg, _A(cmd="say", text=["create", "task", "x"],
                                    session="cli", json=True))
    assert rc == 0
    cid = json.loads(buf.getvalue())["confirmation_id"]
    assert cid, "CLI say stages shared-journal confirmations"
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_voice(cfg, _A(cmd="confirm", confirmation_id=cid,
                                      session="cli")) == 0
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_voice(cfg, _A(cmd="cancel", session="cli")) == 2, \
            "nothing left to cancel"
    assert "voice" in CLI.build_parser().format_help()


# ------------------------------------------- CLI family coverage proof
def test_cli_families_all_mapped_with_voice_or_reason():
    """Every CLI top-level command has a voice registry entry or a tested
    voice-impossible reason (family.subaction granularity for exclusions)."""
    import argparse

    from voicebridge import voice_commands as VC
    from voicebridge.cli import build_parser

    names = set()
    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            names.update(action.choices.keys())
    assert set(VC.CLI_COVERAGE) == names, \
        sorted(set(names) ^ set(VC.CLI_COVERAGE))
    for family, targets in VC.CLI_COVERAGE.items():
        for target in targets:
            assert target in VC.REGISTRY, (family, target)
    for key, reason in VC.VOICE_IMPOSSIBLE.items():
        assert reason and len(reason) > 20, key
        family = key.split(".")[0]
        assert family in VC.CLI_COVERAGE, key
        if "." not in key:
            assert VC.CLI_COVERAGE[family] == (), key


def test_task_mandated_utterances_execute(tmp_path, monkeypatch):
    """'list my projects' / 'show my sessions' execute; 'run the task'
    stages a confirmation and degrades honestly without a provisioned
    runtime (typed UNAVAILABLE, never fake success)."""
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    for text in ("qubit, list my projects", "qubit, show my sessions"):
        res = VC.resolve(text)
        assert res.kind == VC.MATCH, (text, res.kind)
        out = VC.execute(ctx, res)
        assert out.kind == VC.EXECUTED, (text, out.kind, out.speech)
        assert out.receipt, text
    res = VC.resolve("qubit, run the task summarize my notes")
    assert res.kind == VC.MATCH and res.entry.id == "task_run"
    out = VC.execute(ctx, res)
    assert out.kind == VC.NEEDS_CONFIRM, (out.kind, out.speech)
    cid = out.confirmation_id
    assert cid
    # Confirming with the agent explicitly disabled is a typed
    # unavailable state (AGENT_DISABLED), not success and not a crash.
    cfg.data.setdefault("agent", {})["enabled"] = False
    out2 = VC.confirm_pending(ctx, cid)
    assert out2.kind == VC.EXECUTED, (out2.kind, out2.speech)
    task = out2.receipt["result"]["task"]
    assert task["state"] == "UNAVAILABLE", task
    assert task["code"] in ("AGENT_DISABLED", "EMERGENCY_STOP"), task


def test_task_run_honors_estop_and_config(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    # agent.enabled=false short-circuits before any model call.
    cfg.data.setdefault("agent", {})["enabled"] = False
    res = VC.resolve("qubit, run the task x")
    out = VC.execute(ctx, res)
    assert out.kind == VC.NEEDS_CONFIRM
    out2 = VC.confirm_pending(ctx, out.confirmation_id)
    assert out2.receipt["result"]["task"]["code"] == "AGENT_DISABLED"


def test_new_read_backends_execute_hermetic(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    for text in ("qubit, list actions", "qubit, list ghost capabilities",
                 "qubit, ghost status", "qubit, opencode status",
                 "qubit, list opencode capabilities",
                 "qubit, list verified skills", "qubit, experiment status",
                 "qubit, list experiments", "qubit, product status",
                 "qubit, probe install", "qubit, package status",
                 "qubit, dashboard status", "qubit, what can you do",
                 "qubit, branding plan", "qubit, verify branding",
                 "qubit, check profile audio", "qubit, list config profiles",
                 "qubit, show model local/qubit-brain4"):
        res = VC.resolve(text)
        assert res.kind == VC.MATCH, (text, res.kind, res.speech)
        out = VC.execute(ctx, res)
        assert out.kind == VC.EXECUTED, (text, out.kind, out.speech)
        assert out.receipt.get("result"), text


def test_hardware_guidance_names_qubit_spelling(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    for text in ("qubit, microphone test", "qubit, transcription test",
                 "qubit, speech test", "qubit, enroll my voice",
                 "qubit, verify my voice", "qubit, test yourself",
                 "qubit, start listening"):
        res = VC.resolve(text)
        assert res.kind == VC.MATCH, (text, res.kind)
        out = VC.execute(ctx, res)
        assert out.kind == VC.EXECUTED, (text, out.kind)
        speech = out.speech
        assert "qubit" in speech and "voicebridge " not in speech, speech


def test_commands_list_has_no_secrets(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    res = VC.resolve("qubit, what can you do")
    out = VC.execute(ctx, res)
    assert out.kind == VC.EXECUTED
    body = json.dumps(out.receipt)
    assert len(out.receipt["result"]["commands"]) == len(VC.REGISTRY)
    for bad in ("sk-", "pplx-", "api_key", "passphrase", "BEGIN "):
        assert bad not in body, bad


def test_opencode_voice_workflow_wired_disposable_only():
    """Voice covers the disposable-worktree OpenCode workflow (preview, run,
    cancel, status, retry, recover, receipts, grant, revoke) on the shared
    request_coding_run entry; in-project writes stay CLI/dashboard-only
    behind a preview hash (VOICE_IMPOSSIBLE documents the boundary)."""
    from voicebridge import voice_commands as VC
    from voicebridge.opencode import runner as _R

    assert "INTENTIONALLY NOT WIRED" not in (
        _R.request_coding_run.__doc__ or "")
    run_ids = {e.id for e in VC.REGISTRY.values()
               if e.backend == "opencode" and e.confirm}
    assert {"opencode_run", "opencode_cancel", "opencode_retry",
            "opencode_recover", "opencode_grant",
            "opencode_revoke"} <= run_ids, run_ids
    assert "opencode.execute" in VC.VOICE_IMPOSSIBLE  # hash flow boundary
    assert "opencode_run" not in VC.VOICE_IMPOSSIBLE.values()
