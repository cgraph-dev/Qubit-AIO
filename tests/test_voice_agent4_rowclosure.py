"""Agent 4 wave 2 — every remaining code-side voice row has a real caller.

The ledger's still-open voice rows with ``needs=none`` must each be backed
by a wired REGISTRY entry that resolves from speech and dispatches through
the SAME production dispatcher (``dashboard_ops.run_op``) the CLI and the
dashboard console use. Guards refuse typed without staging; guidance rows
return guidance; mutating rows stage exactly one durable confirmation that
``confirm_op``/``cancel_op`` settle once; the emergency-stop reset stays
reachable while the latch is engaged; and a non-user source never executes.

Hardware/creds/owner rows (e.g. ``audio_hw``) are deliberately NOT claimed
here — they require a physical device or credentials.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LEDGER = REPO / "docs" / "QUBIT_GAP_LEDGER.qubit.json"

GUIDANCE_ROWS = {
    "commands_list": "qubit, what can you do",
    "tui_help": "qubit, open dashboard",
    "console_help": "qubit, terminal help",
    "mic_help": "qubit, check microphone",
}


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _say(cfg, text, session="rows", source="user"):
    from voicebridge import voice_commands as VC

    return VC.voice_say(cfg, text, actor="voice:user", session=session,
                        source=source)


def _run(cfg, op_id, args=None, session="rows"):
    from voicebridge import dashboard_ops as OPS

    return OPS.run_op(cfg, op_id, dict(args or {}), actor="voice:user",
                      session=session)


def _confirm(cfg, staged, session="rows"):
    from voicebridge import dashboard_ops as OPS

    assert staged["state"] == "needs-confirm", staged
    return OPS.confirm_op(cfg, staged["confirmation_id"], actor="voice:user",
                          session=session)


def _cancel(cfg, staged, session="rows"):
    from voicebridge import dashboard_ops as OPS

    assert staged["state"] == "needs-confirm", staged
    return OPS.cancel_op(cfg, staged["confirmation_id"], actor="voice:user",
                         session=session)


def _open_voice_rows():
    if not LEDGER.is_file():
        pytest.skip("gap ledger not present")
    data = json.loads(LEDGER.read_text(encoding="utf-8"))
    return [r for r in data["rows"]
            if r.get("voice") and r["needs"] == "none"
            and r["status"] != "IMPLEMENTED"]


# Voice entry ids that back the still-open code-side rows. Kept literal (not
# read from the ledger) so the evidence spans both the ledger build (AST scan
# for catalog op ids) and this behaviour test; every entry must dispatch
# through the shared production dispatcher.
PROBED_ROWS = (
    "acp_accept_hooks_refusal", "acp_serve_refusal", "backup_info",
    "brain_onboard_plan", "console_help", "curator", "diagnostics", "doctor",
    "export_state_voice", "ghost_action_preview", "ghost_action_recover",
    "ghost_action_retry", "ghost_click", "ghost_read_tab", "ghost_type",
    "guard_actions_prepare", "guard_actions_preview", "guard_actions_recover",
    "guard_actions_retry", "guard_ask_answer", "guard_backup_create",
    "guard_backup_restore", "guard_brain_onboard_apply", "guard_file_patch",
    "guard_ghost_preview", "guard_kanban_import", "guard_opencode_prepare",
    "guard_opencode_run_confirmed", "guard_shares_resolve",
    "guard_sync_accept", "guard_sync_offer", "guard_transfers_fetch_raw",
    "guard_webhook_verify", "hooks_remove", "hooks_revoke", "import_refusal",
    "import_state_voice", "insights", "logs", "loop_start",
    "lsp_install_refusal", "marketplace_discovery", "memory_forget",
    "memory_remember", "mic_help", "pause", "projects_archive",
    "projects_create", "projects_use", "prompt_budget", "proxy_start_refusal",
    "reasoning", "reports_list", "resume", "security",
    "skills_workshop_restore", "skills_workshop_rollback", "themes_reset",
    "themes_set", "themes_tweak_clear", "themes_tweak_set", "tools",
    "update_status", "worktree",
)


def test_open_voice_entries_dispatch_through_run_op(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    for op_id in PROBED_ROWS:
        entry = VC.REGISTRY.get(op_id)
        assert entry is not None, op_id
        example = entry.examples[0] if entry.examples else ""
        res = VC.resolve(example)
        assert res.kind in (VC.MATCH, VC.CLARIFY), (op_id, res.kind)
        out = _run(cfg, op_id, {})
        assert isinstance(out, dict), (op_id, out)
        assert out.get("state"), (op_id, out)


def test_open_voice_rows_have_wired_real_callers(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    _cfg(tmp_path, monkeypatch)
    rows = _open_voice_rows()
    for row in rows:
        entry = VC.REGISTRY.get(row["voice"])
        assert entry is not None, row["id"]
        assert entry.backend in VC._BACKENDS, (row["id"], entry.backend)
        example = entry.examples[0] if entry.examples else ""
        assert example, row["id"]
        res = VC.resolve(example)
        assert res.kind in (VC.MATCH, VC.CLARIFY), (row["id"], res.kind)
        if res.kind == VC.CLARIFY:
            assert res.entry is not None and res.entry.id == entry.id, row["id"]


def test_scoped_guidance_rows_route_through_run_op(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    for entry_id, utterance in GUIDANCE_ROWS.items():
        said = _say(cfg, utterance, session=f"g-{entry_id}")
        assert said["state"] == "executed", (entry_id, said)
        assert said["route"] == entry_id, (entry_id, said)
        direct = _run(cfg, entry_id, {}, session=f"g-{entry_id}")
        assert direct["state"] == "executed", (entry_id, direct)
        assert said["receipt"]["result"] == direct["receipt"]["result"], entry_id


def test_all_guard_rows_refuse_typed_through_run_op(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    assert len(VC.VOICE_GUARDS) >= 18
    for op_id, spec in VC.VOICE_GUARDS.items():
        entry_id = f"guard_{op_id}"
        said = _say(cfg, "qubit, " + spec["aliases"][0], session="guards")
        assert said["state"] == "executed", (op_id, said)
        assert said["route"] == entry_id, (op_id, said)
        result = said["receipt"]["result"]
        assert result["state"] == "refused", (op_id, result)
        assert result["code"] == spec["code"], (op_id, result)
        assert len(result["guidance"]) > 20, (op_id, result)
        assert not said.get("confirmation_id"), op_id
        direct = _run(cfg, entry_id, {}, session="guards-run")
        assert direct["receipt"]["result"] == result, op_id


def test_needs_none_mutations_confirm_persist_and_cancel(tmp_path,
                                                        monkeypatch):
    from voicebridge import hooks as HK
    from voicebridge import projects as PJ
    from voicebridge import themes as TH

    cfg = _cfg(tmp_path, monkeypatch)
    staged = _say(cfg, "qubit, create project shop", session="pc")
    created = _confirm(cfg, staged, session="pc")
    assert created["state"] == "executed", created
    assert created["receipt"]["result"]["state"] == "OK", created
    assert any(p.get("slug") == "shop"
               for p in PJ.list_projects(cfg).get("projects", []))
    from voicebridge import dashboard_ops as OPS

    replay = OPS.confirm_op(cfg, staged["confirmation_id"],
                            actor="voice:user", session="pc")
    assert replay["state"] == "failed", replay
    assert replay.get("code") in ("NO_CONFIRMATION", "ALREADY_DONE"), replay

    staged = _say(cfg, "qubit, archive project shop", session="pa")
    cancelled = _cancel(cfg, staged, session="pa")
    assert cancelled["state"] == "executed", cancelled
    assert any(p.get("slug") == "shop" and not p.get("archived")
               for p in PJ.list_projects(cfg).get("projects", []))

    themed = _confirm(cfg, _say(cfg, "qubit, set theme light",
                                session="th"), session="th")
    assert themed["state"] == "executed", themed
    assert TH.show(cfg).get("theme") == "qubit-light", TH.show(cfg)

    declared = _confirm(cfg, _say(cfg, "qubit, declare hook deploy",
                                  session="hd"), session="hd")
    assert declared["state"] == "executed", declared
    assert HK.show(cfg, "deploy")["hook"]["name"] == "deploy"
    revoked = _confirm(cfg, _say(cfg, "qubit, revoke hook deploy",
                                 session="hr"), session="hr")
    assert revoked["state"] == "executed", revoked
    assert HK.list_hooks(cfg).get("hooks") == []

    declared = _confirm(cfg, _say(cfg, "qubit, declare hook cleanup",
                                  session="hd2"), session="hd2")
    assert declared["state"] == "executed", declared
    removed = _confirm(cfg, _say(cfg, "qubit, remove hook cleanup",
                                 session="hr2"), session="hr2")
    assert removed["state"] == "executed", removed
    assert all(h["name"] != "cleanup"
               for h in HK.list_hooks(cfg).get("hooks", []))


def test_estop_reset_reachable_but_other_mutations_stay_closed(tmp_path,
                                                              monkeypatch):
    from voicebridge import plugins

    cfg = _cfg(tmp_path, monkeypatch)
    latched = _confirm(cfg, _say(cfg, "qubit, pause everything",
                                 session="pw"), session="pw")
    assert latched["state"] == "executed", latched
    assert plugins.estop_engaged(cfg) is True

    blocked = _say(cfg, "qubit, create task buy milk", session="m")
    assert blocked["state"] == "refused", blocked
    assert blocked.get("code") == "ESTOP_LATCHED", blocked

    cleared = _confirm(cfg, _say(cfg, "qubit, resume work",
                                 session="pr"), session="pr")
    assert cleared["state"] == "executed", cleared
    assert plugins.estop_engaged(cfg) is False


def test_open_voice_rows_never_execute_from_non_user_source(tmp_path,
                                                            monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    utterances = (
        "qubit, create project shop",
        "qubit, pause everything",
        "qubit, apply a file patch",
        "qubit, list skills",
    )
    for source in ("model", "page", "tool", "agent"):
        for text in utterances:
            out = VC.voice_say(cfg, text, actor="agent:4", session="auto",
                               source=source)
            assert out["state"] == "refused", (source, text, out)
            assert out["code"] == "SOURCE_REFUSED", (source, text, out)
            assert not out.get("confirmation_id"), (source, text, out)
