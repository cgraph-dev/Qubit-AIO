"""Agent 3 — dashboard ops close-out (backup / brain / actions).

Focused, hermetic coverage for the Tier-2 dashboard extras whose ledger rows
were DASHBOARD_ONLY / PARTIAL / UNVERIFIED:

* ``backup_create`` / ``backup_restore``
* ``brain_export`` / ``brain_onboard_apply``
* ``actions_preview`` / ``actions_prepare``

Every test calls the PRODUCTION dispatcher (``run_op`` / ``confirm_op`` /
``cancel_op`` / ``run_extra_op``) — never a private helper — and asserts the
real backend result, not a catalog row. Covered behavior: staging, real state
transitions, confirmation, cancellation, recovery, redaction (secrets and
nested answers never journaled), audit, persistence, and typed negatives.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "actions"
_MAIL_ARGS = {"recipient": "bob@example.com", "subject": "hi", "body": "hello"}


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


def _journal_text(cfg):
    return (Path(cfg.state_dir()) / "approvals.jsonl").read_text(
        encoding="utf-8")


def _latch(cfg, on=True):
    from voicebridge.power import power_control_store

    store = power_control_store(cfg)
    store.set(emergency_stop=bool(on), global_disable=False,
              issued_by="cli:user")


# ------------------------------------------------------------------ backup
def test_backup_create_confirms_cancels_redacts_and_persists(tmp_path,
                                                             monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    pw = "agent3 backup passphrase hunter2"

    cancelled = _run(cfg, "backup_create", {"passphrase": pw, "label": "cancel"})
    assert cancelled["state"] == "needs-confirm", cancelled
    assert cancelled["sensitive_required"] == ["passphrase"], cancelled
    assert pw not in _journal_text(cfg)
    # cancel through the real dispatcher: nothing executes, confirm fails
    from voicebridge import dashboard_ops as OPS

    cancel = OPS.cancel_op(cfg, cancelled["confirmation_id"],
                           actor="dashboard:user", session="dashboard")
    assert cancel["state"] == "executed", cancel
    after = _confirm(cfg, cancelled)
    assert after["state"] == "failed", after

    staged = _run(cfg, "backup_create", {"passphrase": pw, "label": "agent3"})
    assert staged["state"] == "needs-confirm", staged
    assert pw not in _journal_text(cfg)
    done = _confirm(cfg, staged, sensitive={"passphrase": pw})
    assert done["state"] == "executed", done
    result = done["receipt"]["result"]
    bundle = Path(result["path"])
    assert bundle.exists()
    assert bundle.stat().st_mode & 0o777 == 0o600, oct(bundle.stat().st_mode)
    assert result["bytes"] > 0
    assert pw.encode() not in bundle.read_bytes()
    assert pw not in json.dumps(done)
    assert pw not in _journal_text(cfg)
    # settle-once: the confirmation is gone after a restart too
    again = _confirm(cfg, staged, sensitive={"passphrase": pw})
    assert again["state"] == "failed", again
    from voicebridge import config as C

    fresh = C.Config.load(tmp_path / "voice.yaml")
    assert bundle.exists()
    assert _run(fresh, "backup_info", {})["state"] == "executed"


def test_backup_restore_dry_run_then_enrolled_apply_keeps_prerestore_backup(
        tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    pw = "agent3 restore passphrase hunter2"

    enroll = _run(cfg, "device_enroll", {})
    assert enroll["state"] == "needs-confirm", enroll
    assert _confirm(cfg, enroll)["state"] == "executed", enroll

    created = _run(cfg, "backup_create", {"passphrase": pw, "label": "restore"})
    created_done = _confirm(cfg, created, sensitive={"passphrase": pw})
    assert created_done["state"] == "executed", created_done
    bundle_path = created_done["receipt"]["result"]["path"]

    dry = _run(cfg, "backup_restore",
               {"bundle": bundle_path, "passphrase": pw})
    assert dry["state"] == "needs-confirm", dry
    dry_done = _confirm(cfg, dry, sensitive={"passphrase": pw})
    assert dry_done["state"] == "executed", dry_done
    dry_result = dry_done["receipt"]["result"]
    assert dry_result["dry_run"] is True
    assert dry_result["enrolled"] is True
    assert isinstance(dry_result["schema_version"], int)
    assert isinstance(dry_result["plan"], list)

    applied = _run(cfg, "backup_restore",
                   {"bundle": bundle_path, "passphrase": pw, "apply": "true"})
    assert applied["state"] == "needs-confirm", applied
    applied_done = _confirm(cfg, applied, sensitive={"passphrase": pw})
    assert applied_done["state"] == "executed", applied_done
    result = applied_done["receipt"]["result"]
    assert result["restored"] is True
    assert Path(result["backup"]).exists(), result
    assert pw not in json.dumps(applied_done)
    assert pw not in _journal_text(cfg)

    bad = _run(cfg, "backup_restore",
               {"bundle": "missing-bundle.vbp", "passphrase": pw})
    bad_done = _confirm(cfg, bad, sensitive={"passphrase": pw})
    assert bad_done["state"] == "failed", bad_done
    assert bad_done["code"], bad_done


def test_backup_restore_apply_requires_enrollment(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    pw = "agent3 no-enroll hunter2"
    created = _run(cfg, "backup_create", {"passphrase": pw})
    bundle_path = _confirm(cfg, created, sensitive={"passphrase": pw})[
        "receipt"]["result"]["path"]
    applied = _run(cfg, "backup_restore",
                   {"bundle": bundle_path, "passphrase": pw, "apply": "true"})
    out = _confirm(cfg, applied, sensitive={"passphrase": pw})
    assert out["state"] == "failed", out
    assert "enroll" in json.dumps(out).lower(), out


# ------------------------------------------------------------------- brain
def test_brain_onboard_answers_secret_resupplied_and_never_journaled(
        tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    marker = "Agent3SecretDisplayName"
    answers = json.dumps({"personal": {"display_name": marker}})

    staged = _run(cfg, "brain_onboard_apply", {"answers": answers})
    assert staged["state"] == "needs-confirm", staged
    assert staged["sensitive_required"] == ["answers"], staged
    assert marker not in _journal_text(cfg)
    assert marker not in json.dumps(staged)

    blocked = _confirm(cfg, staged)
    assert blocked["state"] == "failed", blocked
    assert blocked["code"] == "SENSITIVE_REQUIRED", blocked
    assert marker not in _journal_text(cfg)

    done = _confirm(cfg, staged, sensitive={"answers": answers})
    assert done["state"] == "executed", done
    assert done["receipt"]["result"]["state"] == "OK", done
    assert marker not in _journal_text(cfg)
    assert marker not in json.dumps(done)

    # typed negatives: malformed / non-object answers fail closed
    bad = _run(cfg, "brain_onboard_apply", {"answers": "{bad"})
    bad_done = _confirm(cfg, bad, sensitive={"answers": "{bad"})
    assert bad_done["state"] == "failed", bad_done
    assert bad_done["code"] == "BRAIN_BAD_ANSWERS", bad_done
    lst = _run(cfg, "brain_onboard_apply", {"answers": "[1, 2]"})
    lst_done = _confirm(cfg, lst, sensitive={"answers": "[1, 2]"})
    assert lst_done["state"] == "failed", lst_done
    assert lst_done["code"] == "BRAIN_BAD_ANSWERS", lst_done

    from voicebridge import dashboard_ops as OPS

    catalog = {o["id"]: o for o in OPS.catalog()}
    answers_spec = [a for a in catalog["brain_onboard_apply"]["args"]
                    if a["name"] == "answers"][0]
    assert answers_spec["secret"] is True


def test_brain_export_real_transition_and_persistence(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    added = _run(cfg, "brain_principal_add", {"pid": "agent3", "label": "A3"})
    assert _confirm(cfg, added)["state"] == "executed"
    field = _run(cfg, "brain_profile_set",
                 {"principal": "agent3", "key": "display_name", "value": "A3"})
    assert _confirm(cfg, field)["state"] == "executed"

    # confirm=False extra: executes immediately through the real backend
    exported = _run(cfg, "brain_export", {"principal": "agent3"})
    assert exported["state"] == "executed", exported
    result = exported["receipt"]["result"]
    assert result["state"] == "OK", result
    assert result["principal"] == "agent3"
    assert result["profiles"]["default"]["display_name"]["value"] == "A3"

    # persistence: a fresh config reads the same store
    from voicebridge import config as C

    fresh = C.Config.load(tmp_path / "voice.yaml")
    again = _run(fresh, "brain_export", {"principal": "agent3"})
    assert again["receipt"]["result"] == result

    # negative: unknown principal is typed, never fabricated
    missing = _run(cfg, "brain_export", {"principal": "nobody"})
    assert missing["state"] == "failed", missing
    assert missing["code"] == "BRAIN_NOT_FOUND", missing


# ----------------------------------------------------------------- actions
def _envelope(action_id="act.browser.draft", args=None, target=None,
              scope="web"):
    from voicebridge import actions as A

    spec = A.action_spec(action_id)
    return A.build_envelope(
        spec, args=dict(args or _MAIL_ARGS),
        target=target or "https://mail.google.com", scope=scope)


def test_actions_preview_and_prepare_hash_bound_lifecycle(tmp_path,
                                                          monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    env = _envelope()

    preview = _run(cfg, "actions_preview",
                   {"action_id": "act.browser.draft",
                    "args_json": json.dumps(_MAIL_ARGS),
                    "target": "https://mail.google.com", "scope": "web"})
    assert preview["state"] == "needs-confirm", preview
    preview_done = _confirm(cfg, preview)
    assert preview_done["state"] == "executed", preview_done
    preview_result = preview_done["receipt"]["result"]
    assert preview_result["state"] == "preview", preview_result
    assert preview_result["action_hash"] == env["action_hash"]
    assert preview_result["persisted"] is False
    # settle-once: a replay fails closed
    assert _confirm(cfg, preview)["state"] == "failed"

    prepared = _run(cfg, "actions_prepare",
                    {"action_id": "act.browser.draft",
                     "envelope_json": json.dumps(env)})
    assert prepared["state"] == "needs-confirm", prepared
    prepared_done = _confirm(cfg, prepared)
    assert prepared_done["state"] == "executed", prepared_done
    prepared_result = prepared_done["receipt"]["result"]
    assert prepared_result["state"] == "prepared", prepared_result
    assert prepared_result["action_hash"] == env["action_hash"]
    assert prepared_result["confirmation_id"], prepared_result
    assert prepared_result["issued_by"] == "user", prepared_result
    assert _confirm(cfg, prepared)["state"] == "failed"


def test_actions_extras_cancel_and_typed_negatives(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge import dashboard_ops as OPS

    staged = _run(cfg, "actions_preview",
                  {"action_id": "act.browser.draft",
                   "args_json": json.dumps(_MAIL_ARGS),
                   "target": "https://mail.google.com", "scope": "web"})
    assert staged["state"] == "needs-confirm", staged
    cancel = OPS.cancel_op(cfg, staged["confirmation_id"],
                           actor="dashboard:user", session="dashboard")
    assert cancel["state"] == "executed", cancel
    assert _confirm(cfg, staged)["state"] == "failed"

    # malformed JSON fails closed inside the backend
    bad = _run(cfg, "actions_preview",
               {"action_id": "act.browser.draft", "args_json": "{bad"})
    bad_done = _confirm(cfg, bad)
    assert bad_done["state"] == "failed", bad_done
    assert bad_done["code"], bad_done

    # unknown action is typed, never a fabricated preview
    unknown = _run(cfg, "actions_preview",
                   {"action_id": "act.no.such", "args_json": "{}"})
    unknown_done = _confirm(cfg, unknown)
    assert unknown_done["state"] == "failed", unknown_done
    assert unknown_done["code"], unknown_done

    # unknown op / missing required args / unknown extra arg
    assert _run(cfg, "agent3_nope", {})["code"] == "UNKNOWN_OP"
    assert _run(cfg, "backup_create", {})["code"] == "BAD_ARGS"
    assert _run(cfg, "brain_onboard_apply", {})["code"] == "BAD_ARGS"
    assert _run(cfg, "actions_preview",
                {"action_id": "x", "args_json": "{}",
                 "bogus": "1"})["code"] == "BAD_ARGS"


def test_actions_recover_reaches_real_backend_via_dispatcher(tmp_path,
                                                             monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge import actions as A
    from voicebridge.permissions import permissions_store

    outcomes = A.load_fixture_outcomes(FIXTURES / "outcomes.json")
    perms = permissions_store(cfg)
    perms.grant("act.mail.send", requested_by="user", confirmed=True,
                scopes=("mail",))
    runner = A.action_runner(cfg, executors=A.make_fixture_executors(outcomes))
    sent = runner.execute("act.mail.send", args=_MAIL_ARGS,
                          target="https://mail.google.com", scope="mail",
                          confirmed=True)
    assert sent["state"] == "OK", sent

    staged = _run(cfg, "actions_recover", {"receipt_id": sent["receipt_id"]})
    assert staged["state"] == "needs-confirm", staged
    done = _confirm(cfg, staged)
    assert done["state"] == "executed", done
    assert done["receipt"]["result"]["state"] == "ROLLBACK_IMPOSSIBLE", \
        done["receipt"]["result"]


# ------------------------------------------------------ safety + audit
def test_agent3_extras_refuse_when_estop_latched(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    try:
        _latch(cfg, True)
        for op_id, args in (
            ("backup_create", {"passphrase": "pw"}),
            ("brain_onboard_apply", {"answers": "{}"}),
            ("actions_preview", {"action_id": "x", "args_json": "{}"}),
        ):
            out = _run(cfg, op_id, args)
            assert out["state"] == "refused", (op_id, out)
            assert out["code"] == "ESTOP_LATCHED", (op_id, out)
    finally:
        _latch(cfg, False)


def test_agent3_audit_events_never_carry_secrets(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    events = []

    def _capture(name, **detail):
        events.append((name, detail))

    monkeypatch.setattr("voicebridge.logging_setup.log_event", _capture)
    pw = "agent3-audit-secret-hunter2"
    staged = _run(cfg, "backup_create", {"passphrase": pw})
    _confirm(cfg, staged, sensitive={"passphrase": pw})
    marker = "Agent3AuditMarker"
    answers = json.dumps({"personal": {"display_name": marker}})
    onboard = _run(cfg, "brain_onboard_apply", {"answers": answers})
    _confirm(cfg, onboard, sensitive={"answers": answers})

    names = [name for name, _ in events]
    assert "dashboard.ops.needs_confirm" in names, names
    assert "dashboard.ops.confirmed_executed" in names, names
    audited_ops = {str(detail.get("op", ""))
                   for name, detail in events
                   if name.startswith("dashboard.ops.")}
    assert {"backup_create", "brain_onboard_apply"} <= audited_ops, \
        audited_ops
    blob = json.dumps(events)
    assert pw not in blob
    assert marker not in blob
