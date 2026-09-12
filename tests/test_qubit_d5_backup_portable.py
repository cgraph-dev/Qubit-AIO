"""D5 — Qubit code-side backup/portable rows through the shared dispatcher.

Exercises the real portable-state backend (``voicebridge.portable``: audited
OpenSSL encryption, PBKDF2, HMAC) via the production ``run_op`` /
``confirm_op`` / ``cancel_op`` path. Covered: real export/import state
transitions, persistence, confirmation + cancellation, enrollment-gated
apply, pre-restore backup, secret (passphrase) resupply/redaction, and typed
negatives (wrong passphrase, tampered bundle, missing bundle). Hermetic.
"""
from __future__ import annotations

import json
from pathlib import Path


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _op(cfg, op_id, args=None, *, sensitive=None, session="d5"):
    from voicebridge import dashboard_ops as OPS

    out = OPS.run_op(cfg, op_id, dict(args or {}), actor="dashboard:user",
                     session=session)
    if out.get("state") == "needs-confirm":
        out = OPS.confirm_op(cfg, out["confirmation_id"],
                             actor="dashboard:user", session=session,
                             sensitive=sensitive)
    return out


def _journal_text(cfg):
    p = Path(cfg.state_dir()) / "approvals.jsonl"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _enroll(cfg):
    out = _op(cfg, "device_enroll", {})
    assert out["state"] == "executed", out


def test_d5_backup_create_restore_roundtrip_via_dispatcher(tmp_path,
                                                           monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _enroll(cfg)
    pw = "d5 portable passphrase hunter2"
    from voicebridge import dashboard_ops as OPS

    # Secret args are never staged: the passphrase must be re-supplied at
    # confirm time and never lands in the approval journal.
    run = OPS.run_op(cfg, "backup_create", {"passphrase": pw, "label": "d5"},
                     actor="dashboard:user", session="d5")
    assert run["state"] == "needs-confirm", run
    assert run["sensitive_required"] == ["passphrase"], run
    assert pw not in _journal_text(cfg)
    created = OPS.confirm_op(cfg, run["confirmation_id"],
                             actor="dashboard:user", session="d5",
                             sensitive={"passphrase": pw})
    assert created["state"] == "executed", created
    result = created["receipt"]["result"]
    bundle = Path(result["path"])
    assert bundle.exists() and bundle.suffix == ".vbp"
    assert bundle.stat().st_mode & 0o777 == 0o600, oct(bundle.stat().st_mode)
    assert result["bytes"] > 0
    assert pw.encode() not in bundle.read_bytes()
    assert pw not in json.dumps(created)
    assert pw not in _journal_text(cfg)

    # Dry-run preview needs no enrollment change and reports the schema/plan.
    dry = _op(cfg, "backup_restore", {"bundle": str(bundle), "passphrase": pw},
              sensitive={"passphrase": pw})
    assert dry["state"] == "executed", dry
    assert dry["receipt"]["result"]["dry_run"] is True
    assert dry["receipt"]["result"]["enrolled"] is True
    assert isinstance(dry["receipt"]["result"]["schema_version"], int)

    # Applied restore keeps a pre-restore backup and never echoes the secret.
    applied = _op(cfg, "backup_restore",
                  {"bundle": str(bundle), "passphrase": pw, "apply": "true"},
                  sensitive={"passphrase": pw})
    assert applied["state"] == "executed", applied
    ar = applied["receipt"]["result"]
    assert ar["restored"] is True
    assert Path(ar["backup"]).exists()
    assert pw not in json.dumps(applied)
    assert pw not in _journal_text(cfg)


def test_d5_backup_wrong_passphrase_and_tamper_are_typed(tmp_path,
                                                         monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _enroll(cfg)
    pw = "d5 correct passphrase"
    from voicebridge import dashboard_ops as OPS

    run = OPS.run_op(cfg, "backup_create", {"passphrase": pw},
                     actor="dashboard:user", session="d5")
    created = OPS.confirm_op(cfg, run["confirmation_id"],
                             actor="dashboard:user", session="d5",
                             sensitive={"passphrase": pw})
    bundle = Path(created["receipt"]["result"]["path"])

    wrong = _op(cfg, "backup_restore",
                {"bundle": str(bundle), "passphrase": "not-the-passphrase"},
                sensitive={"passphrase": "not-the-passphrase"})
    assert wrong["state"] == "failed", wrong
    assert wrong["code"] == "WrongPassphraseError", wrong

    tampered = bytearray(bundle.read_bytes())
    tampered[-1] ^= 0xFF
    bad_path = bundle.with_suffix(".tampered.vbp")
    bad_path.write_bytes(bytes(tampered))
    tamper = _op(cfg, "backup_restore",
                 {"bundle": str(bad_path), "passphrase": pw},
                 sensitive={"passphrase": pw})
    assert tamper["state"] == "failed", tamper
    assert tamper["code"] in ("WrongPassphraseError", "CorruptedBundleError"), \
        tamper

    missing = _op(cfg, "backup_restore",
                  {"bundle": "does-not-exist.vbp", "passphrase": pw},
                  sensitive={"passphrase": pw})
    assert missing["state"] == "failed", missing
    assert missing["code"], missing


def test_d5_backup_apply_requires_enrollment(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    pw = "d5 no-enroll passphrase"
    from voicebridge import dashboard_ops as OPS

    run = OPS.run_op(cfg, "backup_create", {"passphrase": pw},
                     actor="dashboard:user", session="d5")
    created = OPS.confirm_op(cfg, run["confirmation_id"],
                             actor="dashboard:user", session="d5",
                             sensitive={"passphrase": pw})
    bundle = created["receipt"]["result"]["path"]
    applied = _op(cfg, "backup_restore",
                  {"bundle": bundle, "passphrase": pw, "apply": "true"},
                  sensitive={"passphrase": pw})
    assert applied["state"] == "failed", applied
    assert "enroll" in json.dumps(applied).lower(), applied


def test_d5_export_import_state_preview_only(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    pw = "d5 export-state passphrase"
    from voicebridge import dashboard_ops as OPS

    run = OPS.run_op(cfg, "export_state", {"passphrase": pw, "label": "d5"},
                     actor="dashboard:user", session="d5")
    assert run["state"] == "needs-confirm", run
    exported = OPS.confirm_op(cfg, run["confirmation_id"],
                              actor="dashboard:user", session="d5",
                              sensitive={"passphrase": pw})
    assert exported["state"] == "executed", exported
    name = exported["receipt"]["result"]["bundle"]
    bundle_path = Path(cfg.state_dir()) / name
    assert bundle_path.exists() and bundle_path.stat().st_mode & 0o777 == 0o600
    assert pw not in _journal_text(cfg)

    preview = _op(cfg, "import_state",
                  {"bundle_path": str(bundle_path), "passphrase": pw},
                  sensitive={"passphrase": pw})
    assert preview["state"] == "executed", preview
    assert isinstance(preview["receipt"]["result"]["plan"], list)
    assert "preview only" in preview["receipt"]["result"]["note"]

    bad = _op(cfg, "import_state",
              {"bundle_path": str(bundle_path), "passphrase": "wrong"},
              sensitive={"passphrase": "wrong"})
    assert bad["state"] == "failed", bad
    assert bad["code"] == "WrongPassphraseError", bad


def test_d5_backup_cancel_and_estop(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge import dashboard_ops as OPS

    staged = OPS.run_op(cfg, "backup_create", {"passphrase": "pw"},
                        actor="dashboard:user", session="d5")
    assert staged["state"] == "needs-confirm", staged
    cancelled = OPS.cancel_op(cfg, staged["confirmation_id"],
                              actor="dashboard:user", session="d5")
    assert cancelled["state"] == "executed", cancelled
    after = OPS.confirm_op(cfg, staged["confirmation_id"],
                           actor="dashboard:user", session="d5",
                           sensitive={"passphrase": "pw"})
    assert after["state"] == "failed", after
    assert list(Path(cfg.state_dir()).glob("*.vbp")) == []

    from voicebridge.power import power_control_store

    store = power_control_store(cfg)
    store.set(emergency_stop=True, global_disable=False, issued_by="cli:user")
    try:
        refused = _op(cfg, "backup_create", {"passphrase": "pw"})
        assert refused["state"] == "refused", refused
        assert refused["code"] == "ESTOP_LATCHED", refused
    finally:
        store.set(emergency_stop=False, global_disable=False,
                  issued_by="cli:user")
