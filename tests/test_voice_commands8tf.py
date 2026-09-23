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

    par = json.loads((Path(VC.__file__).resolve().parents[1] / "docs/archive/2026-09-21" /
                      "PARITY_HERMES.json").read_text())
    mat = json.loads((Path(VC.__file__).resolve().parents[1] / "docs/archive/2026-09-21" /
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
               "block", "unblock", "archive", "restore", "move", "widget_add",
               "widget_remove", "widget_move", "layout_set", "role_grant",
               "role_revoke", "snapshot_restore"},
    "projects": {"create", "use", "archive", "rename", "add_folder",
                 "remove_folder", "set_primary", "restore", "rollback",
                 "bind_board"},
    "skills": {"enable", "disable", "remove", "rollback"},
    "sync": {"revoke"},
    "checkpoints": {"create", "prune"},
    "hooks": {"fire", "declare", "revoke", "remove"},
    "webhook": {"add", "remove"},
    "gateway": {"add", "remove", "supervision_request",
                "supervision_revert"},
    "runtime": {"install", "upgrade", "rollback", "remove", "recover",
                "repair"},
    "themes": {"set", "tweak_set", "tweak_clear", "reset"},
    "memory": {"remember", "forget"},
    "pairing": {"create", "approve", "revoke", "setup_code", "accept"},
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
    "node_host": {"enroll", "revoke"},
    "task_ledger": set(),
    "media_gen": {"generate"},
    "local_models": set(),
    "plugin_runtime": {"register", "invoke", "unregister", "policy_attest"},
    "channels": {"configure", "unconfigure"},
    "acp_client": {"configure", "unconfigure", "handshake", "send"},
    "file_transfer": {"fetch", "plan"},
    "active_memory": {"policy_set", "escalate"},
    "doc_extract": {"extract"},
    "question_prompt": {"ask"},
    "session_share": {"publish", "revoke", "purge"},
    "browser_profiles": {"create", "open", "close", "remove"},
    "computer_screen": {"arrange"},
    "egress": {"substitute"},
    "workshop": {"backup", "restore", "rollback"},
    "usage": set(),
    "receipts_ledger": set(),
    "logbook": set(),
    "automation": {"directive"},
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
    # voice guards for CLI/dashboard-only ops (typed refusal with guidance)
    "guard_actions_prepare": "qubit, prepare a structured action",
    "guard_actions_preview": "qubit, preview a structured action",
    "guard_actions_retry": "qubit, retry a structured action",
    "guard_actions_recover": "qubit, recover a structured action",
    "guard_ghost_preview": "qubit, preview a ghost action",
    "guard_backup_create": "qubit, create an encrypted backup",
    "guard_backup_restore": "qubit, restore an encrypted backup",
    "guard_brain_onboard_apply": "qubit, apply brain onboarding answers",
    "guard_kanban_import": "qubit, import a kanban snapshot",
    "guard_opencode_prepare": "qubit, prepare an in-project coding change",
    "guard_opencode_run_confirmed":
        "qubit, run a confirmed in-project coding change",
    "guard_sync_offer": "qubit, offer a sync bundle",
    "guard_sync_accept": "qubit, accept a sync bundle",
    "guard_webhook_verify": "qubit, verify a webhook signature",
    "guard_file_patch": "qubit, apply a file patch",
    "web_fetch": "qubit, fetch a web page https://example.com",
    "web_search": "qubit, search the web for rust async",
    "sessions": "qubit, list sessions",
    "sessions_search": "qubit, search sessions invoice",
    "session_show": "qubit, show session 12",
    "session_rename": "qubit, rename session 12 to invoices",
    "session_archive": "qubit, archive session 12",
    "session_delete": "qubit, delete session 12",
    "session_resume": "qubit, resume session 12",
    "chat_sessions": "qubit, list my chats",
    "chat_session_show": "qubit, show chat session abc123",
    "chat_session_search": "qubit, search my chats invoice",
    "capability": "qubit, is whatsapp available",
    "capabilities_list": "qubit, show available capabilities",
    "checkpoints_list": "qubit, list checkpoints",
    "checkpoints_write": "qubit, create checkpoint deploy",
    "auth_status": "qubit, credential status",
    "auth_login": "qubit, how do i log in xai",
    "auth_verify": "qubit, is xai configured",
    "auth_logout": "qubit, logout check",
    "mcp": "qubit, list mcp servers",
    "mcp_invoke": "qubit, call mcp tool cgraph-graft search",
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
    "sync_revoke_voice": "qubit, revoke sync offer abc123",
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
    "gateway_supervision_refusal": "qubit, restart gateway service",
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
    "plugins_remote_refusal": "qubit, install remote plugin actor/foo",
    "pets_downloads_refusal": "qubit, pet gallery",
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
    "kanban_restore": "qubit, restore task 3",
    "kanban_stats": "qubit, board stats",
    "kanban_log": "qubit, board log",
    "kanban_stores": "qubit, which kanban store",
    "kanban_migrate": "qubit, unify task boards",
    "kanban_export": "qubit, export board",
    "kanban_snapshots": "qubit, list canvas snapshots",
    "kanban_open": "qubit, open the board",
    "kanban_move": "qubit, move this task 3",
    "kanban_widget_add": "qubit, add board widget demo note",
    "kanban_widget_list": "qubit, list board widgets",
    "kanban_tab_list": "qubit, list board tabs",
    "kanban_tab_create": "qubit, create board tab invoices",
    "kanban_tab_rename": "qubit, rename board tab main to overview",
    "kanban_tab_delete": "qubit, delete board tab archive",
    "kanban_tab_move": "qubit, move board tab archive to position 2",
    "kanban_widget_remove": "qubit, remove board widget 2",
    "kanban_widget_move_voice": "qubit, move widget 4 to 100 200",
    "kanban_role_grant_voice": "qubit, grant board role editor to sam",
    "kanban_role_revoke_voice": "qubit, revoke board role from sam",
    "kanban_snapshot_restore_voice": "qubit, restore canvas snapshot 7",
    "kanban_swarm": "qubit, swarm board",
    "kanban_watch": "qubit, watch board",
    "kanban_dispatch": "qubit, dispatch board",
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
    "browser_screenshot": "qubit, take a screenshot",
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
    # OpenClaw automation (OC-31 auto-reply/heartbeat/flows, OC-D05 QA,
    # OC-D25 yields) on the shared schedules directive dispatcher.
    "automation_status": "qubit, automation status",
    "automation_orders": "qubit, list standing orders",
    "automation_heartbeats": "qubit, list heartbeats",
    "automation_flows": "qubit, list automation flows",
    "automation_scenarios": "qubit, list qa scenarios",
    "automation_yield_pending": "qubit, list pending handoffs",
    "automation_order_add":
        "qubit, add standing order name=greet match=hello reply=hi "
        "account=team to=C1",
    "automation_order_enable": "qubit, enable standing order name=greet",
    "automation_order_disable": "qubit, disable standing order name=greet",
    "automation_order_remove": "qubit, remove standing order name=greet",
    "automation_heartbeat_add":
        "qubit, add heartbeat name=hb every=5 note=liveness",
    "automation_flow_add":
        "qubit, add automation flow name=wake steps='[{\"action\":\"notify\","
        "\"text\":\"up\"}]'",
    "automation_flow_run": "qubit, run automation flow name=wake",
    "automation_qa_add":
        "qubit, add qa scenario name=smoke scenario='[{\"name\":\"ok\","
        "\"action\":\"check\"}]'",
    "automation_qa_run": "qubit, run qa scenario name=smoke",
    "automation_yield_handoff":
        "qubit, create yield handoff session=sess-1 reason=review "
        "context=continue depth=1",
    "automation_yield_resume":
        "qubit, resume yield handoff session=sess-1 consume=1",
    "files_inspect": "qubit, inspect this project folder",
    "files_list": "qubit, list project files",
    "file_patch_rollback": "qubit, roll back the patch on /srv/project/app.py",
    "files_read": "qubit, read project file notes",
    "git_status": "qubit, git status",
    "git_log": "qubit, recent commits",
    "git_diff": "qubit, show diff",
    "git_show": "qubit, show committed file notes",
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
    # --- paired node host / task ledger / media gen / local models
    "node_host_status": "qubit, node host status",
    "node_host_list": "qubit, list nodes",
    "node_host_show": "qubit, show node node_abc123",
    "node_host_capabilities": "qubit, node capabilities node_abc123",
    "node_host_enroll": "qubit, enroll node desk http://127.0.0.1:8443/",
    "node_host_revoke": "qubit, revoke node node_abc123",
    "node_host_dispatch":
        "qubit, dispatch node capability node_abc123 device.inventory",
    "task_ledger_status": "qubit, task ledger status",
    "task_ledger_list": "qubit, list background tasks",
    "task_ledger_show": "qubit, show background task kanban:1",
    "task_ledger_timeline": "qubit, task timeline kanban:1",
    "media_gen_status": "qubit, media status",
    "media_gen_catalog": "qubit, media catalog",
    "media_gen_generate": "qubit, generate image a red fox",
    "local_models_status": "qubit, local models status",
    "local_models_hardware": "qubit, local model hardware",
    "local_models_catalog": "qubit, local model catalog",
    "local_models_jobs": "qubit, local model jobs",
    "local_models_runtime_install": "qubit, install local runtime",
    "local_models_download": "qubit, download local model qubit-brain4",
    "local_models_sideload": "qubit, sideload local model /tmp/model.gguf",
    "local_models_server": "qubit, start local model server",
    "local_models_eject": "qubit, eject local model qubit-brain4",
    "local_models_activate": "qubit, activate local model qubit-brain4",
    "local_models_quickstart": "qubit, local model quickstart",
    # --- Packet 8U OpenClaw backends: plugin host / inbound channels / ACP
    "plugins_status": "qubit, plugin runtime status",
    "plugins_list": "qubit, list plugins",
    "plugins_show": "qubit, show plugin builtin.echo",
    "plugins_register": "qubit, register plugin manifest /tmp/acme.json",
    "plugins_invoke": "qubit, invoke plugin builtin.echo hello",
    "plugins_unregister": "qubit, unregister plugin acme.tool",
    "plugins_doctor": "qubit, plugin doctor",
    "channels_status": "qubit, channels status",
    "channels_catalog": "qubit, list channels",
    "channels_accounts": "qubit, list configured channels",
    "channels_configure": "qubit, configure channel discord env:q8u_secret",
    "channels_unconfigure": "qubit, unconfigure channel discord",
    "channels_receive_refusal": "qubit, receive an inbound channel payload",
    "channels_doctor": "qubit, channels doctor",
    "acp_client_status": "qubit, harness status",
    "acp_client_catalog": "qubit, list harnesses",
    "acp_client_configure": "qubit, configure harness claude-code env:acp_key",
    "acp_client_unconfigure": "qubit, unconfigure harness claude-code",
    "acp_client_handshake": "qubit, harness handshake claude-code",
    "acp_client_send": "qubit, send to harness claude-code hello",
    "acp_client_receipts": "qubit, harness receipts",
    # --- Wave 3 OpenClaw deltas: file transfer / active recall / documents /
    # question prompts / session sharing + their raw-byte/nested/secret guards
    "transfers_status": "qubit, transfer status",
    "transfers_list": "qubit, list node files /tmp/work",
    "transfers_fetch": "qubit, fetch file chunk /tmp/work/notes.txt",
    "transfers_plan": "qubit, plan file transfer /tmp/work/notes.txt",
    "transfers_resume": "qubit, resume file transfer xfer_abc",
    "recall_status": "qubit, active recall status",
    "recall_policy_get": "qubit, show recall policy",
    "recall_policy_set": "qubit, turn active recall on",
    "recall_query": "qubit, recall memories invoices",
    "recall_update": "qubit, update active memory 3 the corrected fact",
    "recall_resolve": "qubit, resolve recall context invoices",
    "recall_escalate": "qubit, escalate memory 3 reason important",
    "documents_status": "qubit, document status",
    "documents_kinds": "qubit, list document kinds",
    "documents_extract": "qubit, extract document /tmp/work/notes.txt",
    "questions_status": "qubit, question status",
    "questions_ask": "qubit, ask a question which branch",
    "questions_pending": "qubit, list pending questions",
    "shares_status": "qubit, share status",
    "shares_publish": "qubit, share session 12",
    "shares_list": "qubit, list session shares",
    "shares_revoke": "qubit, revoke share shr_abc",
    "shares_purge": "qubit, purge expired shares",
    "guard_transfers_fetch_raw": "qubit, read raw file bytes",
    "guard_ask_answer": "qubit, answer with structured answers",
    "guard_shares_resolve": "qubit, open a shared session",
    # --- integration wave: Agents 5-10 real backends
    "browser_profiles": "qubit, list browser profiles",
    "browser_profile_create": "qubit, create browser profile work",
    "browser_profile_open": "qubit, open browser profile work",
    "browser_profile_close": "qubit, close managed profile work",
    "browser_profile_remove": "qubit, remove browser profile work",
    "computer_screen": "qubit, show control ui layout",
    "computer_screen_panes": "qubit, list screen panes",
    "computer_screen_arrange": "qubit, arrange screen main board",
    "local_models_fallback": "qubit, model fallback chain",
    "local_models_resolve": "qubit, resolve model fallback",
    "gateway_supervision_request":
        "qubit, request gateway supervision team start",
    "gateway_supervision_revert": "qubit, revert gateway supervision team",
    "gateway_discovery": "qubit, gateway discovery",
    "pairing_setup_code": "qubit, generate pairing setup code",
    "pairing_accept": "qubit, accept setup code AB12CD",
    "runtime_recover": "qubit, recover runtime opencode-binary",
    "runtime_repair": "qubit, repair runtime",
    "runtime_history": "qubit, runtime update history",
    "runtime_discovery": "qubit, runtime discovery",
    "runtime_marketplace": "qubit, runtime marketplace",
    "egress_status": "qubit, egress status",
    "egress_check": "qubit, egress check api.example.com",
    "policy_conformance": "qubit, policy conformance",
    "policy_attest": "qubit, policy attest",
    "marketplace_discovery": "qubit, marketplace discovery",
    "hooks_emit": "qubit, emit lifecycle event task.completed",
    "skills_workshop": "qubit, skill workshop",
    "skills_workshop_backups": "qubit, workshop backups",
    "skills_workshop_curator": "qubit, workshop curator",
    "skills_workshop_backup": "qubit, back up workshop",
    "skills_workshop_restore": "qubit, restore workshop backup abc123",
    "skills_workshop_rollback": "qubit, rollback workshop",
    "usage_summary": "qubit, usage analytics",
    "evaluation_telemetry": "qubit, telemetry status",
    "receipts_ledger": "qubit, execution receipts",
    "logbook_entries": "qubit, work logbook",
    "logbook_summary": "qubit, logbook summary",
    "runtime_update_report": "qubit, runtime update report",
    "substitute_egress":
        "qubit, substitute egress secrets api.example.com "
        "authorization {{secret:api}}",
    "autonomy_status": "qubit, autonomy status",
    "autonomy_list": "qubit, list autonomy profiles",
    "autonomy_show": "qubit, show autonomy profile nightly",
    "autonomy_pause": "qubit, pause autonomy nightly",
    "autonomy_resume": "qubit, resume autonomy nightly",
    "autonomy_revoke": "qubit, revoke autonomy nightly",
    "autonomy_expire": "qubit, expire autonomy profiles",
    # S8 bounded desktop/browser metadata ops (A38/A39)
    "computer_ops": "qubit, list desktop operations",
    "computer_launch": "qubit, launch app browser",
    "computer_pip": "qubit, picture in picture",
    "computer_present": "qubit, present the screen",
    "computer_presentation_status": "qubit, presentation status",
    "browser_favicon": "qubit, get the page icon",
    "browser_favicon_cache": "qubit, list favicon cache",
    "browser_download": "qubit, download this page to /tmp/page.html",
    # S9 plugin runtime catalog/install + bundled skills
    "plugins_catalog": "qubit, show plugin catalog",
    "plugins_runtime_install": "qubit, install plugin runtime acme",
    "verified_bundled": "qubit, list bundled skills",
    # PRB-05 speech-profile lifecycle (main registry, canonical resolver).
    "speech_profile_list": "qubit, list speech profiles",
    "speech_profile_show": "qubit, show speech profile",
    "speech_profile_test": "qubit, test my speech profile",
    "speech_profile_create": "qubit, create a speech profile",
    "speech_profile_clone": "qubit, clone speech profile",
    "speech_profile_remove": "qubit, remove speech profile",
    "speech_profile_select": "qubit, select speech profile for this session",
    "speech_profile_clear": "qubit, clear session speech",
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
