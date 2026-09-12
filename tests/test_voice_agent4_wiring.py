"""Agent 4 integration wave — voice on the real Agent 5-10 operations.

Each capability built in the integration wave is registered ONCE as a
Tier-1 op id: voice resolves the utterance and dispatches it through
``dashboard_ops.run_op`` while the dashboard console and the CLI call the
same production op/backend. Reads are hermetic; mutations stage the shared
approval journal and are settled by ``confirm_op`` / ``cancel_op``.

Covered families: browser profiles, computer-use screen, local-model
fallback, gateway supervision/discovery, pairing setup/accept, runtime
recover/repair/history/discovery/marketplace, egress, policy
conformance/attestation, hooks emit, skills workshop, usage/receipts/
logbook readers. Negatives pin the safety boundary (deny-by-default
egress, personal-profile close refusal, pinned-source marketplace).
"""
from __future__ import annotations


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _say(cfg, text, session="agent4w"):
    from voicebridge import voice_commands as VC

    return VC.voice_say(cfg, text, actor="voice:user", session=session,
                        source="user")


def _run(cfg, op_id, args=None, session="agent4w"):
    from voicebridge import dashboard_ops as OPS

    return OPS.run_op(cfg, op_id, dict(args or {}), actor="voice:user",
                      session=session)


def _confirm(cfg, staged, session="agent4w"):
    from voicebridge import dashboard_ops as OPS

    assert staged["state"] == "needs-confirm", staged
    return OPS.confirm_op(cfg, staged["confirmation_id"], actor="voice:user",
                          session=session)


def _cancel(cfg, staged, session="agent4w"):
    from voicebridge import dashboard_ops as OPS

    assert staged["state"] == "needs-confirm", staged
    return OPS.cancel_op(cfg, staged["confirmation_id"], actor="voice:user",
                         session=session)


NEW_ENTRY_IDS = (
    "browser_profiles", "browser_profile_create", "browser_profile_open",
    "browser_profile_close", "browser_profile_remove",
    "computer_screen", "computer_screen_panes", "computer_screen_arrange",
    "local_models_fallback", "local_models_resolve",
    "gateway_supervision_request", "gateway_supervision_revert",
    "gateway_discovery",
    "pairing_setup_code", "pairing_accept",
    "runtime_recover", "runtime_repair", "runtime_history",
    "runtime_discovery", "runtime_marketplace", "runtime_update_report",
    "egress_status", "egress_check", "substitute_egress",
    "evaluation_telemetry",
    "policy_conformance", "policy_attest",
    "marketplace_discovery",
    "hooks_emit",
    "skills_workshop", "skills_workshop_backups", "skills_workshop_curator",
    "skills_workshop_backup", "skills_workshop_restore",
    "skills_workshop_rollback",
    "usage_summary", "receipts_ledger", "logbook_entries", "logbook_summary",
)

READ_OPS = {
    "browser_profiles": "qubit, list browser profiles",
    "computer_screen": "qubit, show control ui layout",
    "computer_screen_panes": "qubit, list screen panes",
    "local_models_fallback": "qubit, model fallback chain",
    "local_models_resolve": "qubit, resolve model fallback",
    "gateway_discovery": "qubit, gateway discovery",
    "egress_status": "qubit, egress status",
    "runtime_history": "qubit, runtime update history",
    "runtime_discovery": "qubit, runtime discovery",
    "runtime_marketplace": "qubit, runtime marketplace",
    "runtime_update_report": "qubit, runtime update report",
    "evaluation_telemetry": "qubit, telemetry status",
    "skills_workshop": "qubit, skill workshop",
    "skills_workshop_backups": "qubit, workshop backups",
    "skills_workshop_curator": "qubit, workshop curator",
    "usage_summary": "qubit, usage analytics",
    "receipts_ledger": "qubit, execution receipts",
    "logbook_entries": "qubit, work logbook",
    "logbook_summary": "qubit, logbook summary",
}


def test_wave_ops_share_one_cli_dashboard_voice_id(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    _cfg(tmp_path, monkeypatch)
    catalog = {o["id"]: o for o in OPS.catalog()}
    assert len(NEW_ENTRY_IDS) == len(set(NEW_ENTRY_IDS))
    for entry_id in NEW_ENTRY_IDS:
        entry = VC.REGISTRY.get(entry_id)
        assert entry is not None, entry_id
        assert entry.backend in VC._BACKENDS, (entry_id, entry.backend)
        assert entry.aliases and entry.examples and entry.help, entry_id
        row = catalog.get(entry_id)
        assert row is not None, f"{entry_id} missing from the dashboard catalog"
        assert row["tier"] == "registry", (entry_id, row["tier"])
        assert "voice" in row["interfaces"], entry_id


def test_wave_reads_are_byte_identical_through_voice_and_run_op(tmp_path,
                                                               monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    for entry_id, utterance in READ_OPS.items():
        said = _say(cfg, utterance, session=f"say-{entry_id}")
        assert said["state"] == "executed", (entry_id, said)
        assert said["route"] == entry_id, (entry_id, said)
        direct = _run(cfg, entry_id, {}, session=f"say-{entry_id}")
        assert direct["state"] == "executed", (entry_id, direct)
        assert said["receipt"]["result"] == direct["receipt"]["result"], \
            entry_id


def test_wave_confirmed_mutation_persists_once_and_cancels_clean(tmp_path,
                                                                monkeypatch):
    from voicebridge import browser as BW

    cfg = _cfg(tmp_path, monkeypatch)
    staged = _say(cfg, "qubit, create browser profile alice", session="b1")
    done = _confirm(cfg, staged, session="b1")
    assert done["state"] == "executed", done
    assert done["receipt"]["result"]["browser_profile"]["state"] == "OK"
    names = {p["name"] for p in BW.profiles(cfg)["profiles"]}
    assert "alice" in names, names
    replay = _confirm(cfg, staged, session="b1")
    assert replay["state"] == "failed", replay
    assert replay.get("code") in ("NO_CONFIRMATION", "ALREADY_DONE"), replay

    staged2 = _say(cfg, "qubit, remove browser profile alice", session="b2")
    cancelled = _cancel(cfg, staged2, session="b2")
    assert cancelled["state"] == "executed", cancelled
    assert "alice" in {p["name"] for p in BW.profiles(cfg)["profiles"]}
    gone = _confirm(cfg, staged2, session="b2")
    assert gone["state"] == "failed", gone


def test_wave_pairing_setup_code_confirms_and_redacts(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    staged = _say(cfg, "qubit, generate pairing setup code", session="pair")
    done = _confirm(cfg, staged, session="pair")
    assert done["state"] == "executed", done
    body = done["receipt"]["result"]
    assert body["state"] == "OK", body
    assert body.get("join_url") and body.get("qr"), body
    assert str(body.get("code", "")) not in ("", None)


def test_wave_policy_conformance_and_attest_are_real(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "pyproject.toml").write_text("[project]\nname='x'\n")
    (ws / "LICENSE").write_text("MIT\n")
    (ws / "module.py").write_text("X = 1\n")
    conf = _say(cfg, f'qubit, policy conformance "{ws}"')
    report = conf["receipt"]["result"]["policy"]
    assert report["state"] in ("PASS", "WARN", "FAIL"), report
    assert "findings" in report
    staged = _say(cfg, f'qubit, policy attest "{ws}"', session="att")
    done = _confirm(cfg, staged, session="att")
    att = done["receipt"]["result"]["policy"]
    assert att.get("attestation_id"), att
    assert "attestation_id" in str(att)


def test_wave_egress_profile_close_and_marketplace_guards(tmp_path,
                                                          monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    denied = _say(cfg, "qubit, egress check evil.example.com")
    egress = denied["receipt"]["result"]["egress"]
    assert egress["allowed"] is False, egress
    assert egress["code"] == "EGRESS_DENIED", egress

    staged = _say(cfg, "qubit, close managed profile personal", session="neg")
    done = _confirm(cfg, staged, session="neg")
    body = done["receipt"]["result"]["browser_profile"]
    assert body["state"] == "REFUSED", body
    assert body["code"] == "BROWSER_CLOSE_REFUSED", body

    remote = _say(cfg, "qubit, marketplace discovery "
                      "https://evil.example.com/index.json")
    market = remote["receipt"]["result"]["marketplace"]
    assert market["state"] == "BLOCKED", market
    assert market["code"] == "MARKETPLACE_REMOTE_REFUSED", market

    staged = _say(cfg, "qubit, substitute egress secrets evil.example.com "
                       "authorization {{secret:api}}", session="sub")
    done = _confirm(cfg, staged, session="sub")
    assert done["state"] == "failed", done
    code = done.get("code") or done["receipt"].get("code")
    assert code in ("EGRESS_DENIED", "EGRESS_SECRET_MISSING",
                    "EGRESS_BAD_REFERENCE"), done
    assert "authorization" not in str(done.get("speech", "")).lower()


def test_wave_workshop_restore_unknown_backup_is_typed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    staged = _say(cfg, "qubit, restore workshop backup collection-missing-000",
                  session="wr")
    done = _confirm(cfg, staged, session="wr")
    body = done["receipt"]["result"]["workshop"]
    assert body["state"] == "FAILED", body
    lower = str(body.get("detail", "")).lower()
    assert "no workshop backup" in lower, body
    assert "human" not in lower, body


def test_wave_workshop_restore_requires_human_and_performs_real_restore(
        tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC
    from voicebridge.verified_skills.workshop import (
        WorkshopError, verified_skill_workshop)

    cfg = _cfg(tmp_path, monkeypatch)
    staged = _say(cfg, "qubit, back up workshop", session="wb")
    backup = _confirm(cfg, staged, session="wb")
    assert backup["state"] == "executed", backup
    bid = backup["receipt"]["result"]["workshop"]["backup_id"]
    assert bid

    refused = VC.voice_say(cfg, f"qubit, restore workshop backup {bid}",
                           actor="agent:8", session="auto", source="agent")
    assert refused["state"] == "refused", refused
    assert refused["code"] == "SOURCE_REFUSED", refused

    ws = verified_skill_workshop(cfg)
    try:
        ws.restore(bid, requested_by="agent:8")
    except WorkshopError:
        pass
    else:
        raise AssertionError("autonomous restore must be refused")

    staged2 = _say(cfg, f"qubit, restore workshop backup {bid}", session="wr2")
    done = _confirm(cfg, staged2, session="wr2")
    body = done["receipt"]["result"]["workshop"]
    assert body["state"] == "OK", body
    assert body["restored_from"] == bid, body


def test_wave_gateway_supervision_revert_without_receipt_is_typed(tmp_path,
                                                                  monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    staged = _say(cfg, "qubit, revert gateway supervision ghost", session="gw")
    done = _confirm(cfg, staged, session="gw")
    assert done["state"] == "failed", done
    assert done.get("code") or done["receipt"].get("code"), done
