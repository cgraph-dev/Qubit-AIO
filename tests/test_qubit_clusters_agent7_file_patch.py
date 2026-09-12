"""Agent 7 — brokered file-patch cluster, real-module proofs.

Drives ``voicebridge.file_patch`` directly (confinement probe stubbed OK so the
behavioral path always runs): parse/apply/rollback, no-op idempotence, typed
refusals, audit/redaction, and backup-integrity recovery.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from voicebridge import file_patch as FP


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _project(cfg, folder):
    from voicebridge import projects as PJ

    created = PJ.create(cfg, "Work", [str(folder)], requested_by="t",
                        confirmed=True, use=True)
    assert created["state"] == "OK", created


@pytest.fixture
def confined(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(FP, "confinement_available",
                        lambda: (True, "/usr/bin/bwrap", "ok"))
    monkeypatch.setattr(FP, "estop_engaged", lambda cfg: False)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    return cfg, root


def _append_patch(old="beta", new="inserted"):
    return (f"--- a/notes.txt\n+++ b/notes.txt\n"
            f"@@ -1,3 +1,4 @@\n alpha\n+{new}\n {old}\n gamma\n")


def test_apply_no_change_is_typed_noop(confined):
    cfg, root = confined
    target = root / "notes.txt"
    target.write_text("alpha\nbeta\ngamma\n")
    # A patch that deletes and re-adds the same line yields no change.
    patch = ("--- a\n+++ b\n@@ -2,1 +2,1 @@\n-beta\n+beta\n")
    out = FP.apply_patch(cfg, str(target), patch, requested_by="cli:user",
                         confirmed=True)
    assert out["state"] == "NO_CHANGE" and out["code"] == "FILE_PATCH_NO_CHANGE"
    assert out["backup"] == ""
    assert target.read_text() == "alpha\nbeta\ngamma\n"


def test_apply_rollback_and_audit(confined, monkeypatch):
    cfg, root = confined
    target = root / "notes.txt"
    target.write_text("alpha\nbeta\ngamma\n")
    captured = []
    import voicebridge.logging_setup as LS

    monkeypatch.setattr(LS, "log_event",
                        lambda event, **fields: captured.append((event, fields)))
    out = FP.apply_patch(cfg, str(target), _append_patch(),
                         requested_by="cli:user", confirmed=True)
    assert out["state"] == "OK"
    assert target.read_text() == "alpha\ninserted\nbeta\ngamma\n"
    assert any(e == "file_patch.apply" for e, _ in captured)
    undone = FP.rollback(cfg, str(target), backup=out["backup"],
                         requested_by="cli:user", confirmed=True)
    assert undone["state"] == "OK"
    assert target.read_text() == "alpha\nbeta\ngamma\n"


def test_parse_errors_are_typed(confined):
    cfg, root = confined
    target = root / "notes.txt"
    target.write_text("alpha\n")
    for bad in ("", "no hunks here", "--- a\n+++ b\n@@ -1,2 +1,1 @@\n-alpha\n"):
        with pytest.raises(FP.FilePatchError) as exc:
            FP.apply_patch(cfg, str(target), bad, requested_by="cli:user",
                           confirmed=True)
        assert exc.value.code in (FP.BAD_INPUT_CODE, FP.PARSE_CODE,
                                  FP.CONFLICT_CODE)


def test_rollback_rejects_tampered_backup(confined):
    cfg, root = confined
    target = root / "notes.txt"
    target.write_text("alpha\nbeta\ngamma\n")
    out = FP.apply_patch(cfg, str(target), _append_patch(),
                         requested_by="cli:user", confirmed=True)
    backup = Path(out["backup"])
    record = json.loads(backup.read_text())
    record["content_b64"] = "AAAA"
    backup.write_text(json.dumps(record))
    with pytest.raises(FP.FilePatchError) as exc:
        FP.rollback(cfg, str(target), backup=str(backup),
                    requested_by="cli:user", confirmed=True)
    assert exc.value.code == FP.ROLLBACK_CODE
    assert target.read_text() == "alpha\ninserted\nbeta\ngamma\n"


def test_preview_redacts_secret_payload(confined):
    cfg, root = confined
    target = root / "notes.txt"
    target.write_text("top\n")
    secret = "sk-ABCDEFGHIJKLMNOPQRSTUVWX"
    patch = ("--- a\n+++ b\n@@ -1,1 +1,2 @@\n top\n"
             f"+token = {secret}\n")
    out = FP.apply_patch(cfg, str(target), patch, requested_by="cli:user",
                         confirmed=True)
    assert secret not in json.dumps(out)
    assert "[REDACTED]" in out["preview"]
    # The write itself still happened: redaction, not suppression.
    assert secret in target.read_text()


def test_confinement_unavailable_fails_closed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(FP, "confinement_available",
                        lambda: (False, "", "no userns"))
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "notes.txt"
    target.write_text("alpha\n")
    with pytest.raises(FP.FilePatchError) as exc:
        FP.apply_patch(cfg, str(target), _append_patch(),
                       requested_by="cli:user", confirmed=True)
    assert exc.value.code == FP.CONFINEMENT_CODE
    assert target.read_text() == "alpha\n"
