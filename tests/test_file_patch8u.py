"""Packet 8U — brokered, confined file apply_patch + rollback.

Proves the write path reuses the confinement gate, confirmation, e-stop,
audit, and redaction stack: valid patches apply atomically and reverse
exactly; traversal / symlink escape / oversized input are typed refusals;
unconfirmed calls and an unavailable confinement state fail closed; and no
secret-shaped patch content reaches audit or receipts.

The confinement gate requires the real Bubblewrap primitive; on a platform
without it the happy-path tests skip (the fail-closed test monkeypatches the
probe and always runs).
"""
from __future__ import annotations

import json

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
    assert created.get("state") == "OK", created
    return created


def _confinement_ok():
    return FP.confinement_available()[0]


def _apply(cfg, target, patch, **kw):
    kw.setdefault("requested_by", "cli:user")
    kw.setdefault("confirmed", True)
    return FP.apply_patch(cfg, str(target), patch, **kw)


def _append_patch(old="beta", new="inserted"):
    return (f"--- a/notes.txt\n+++ b/notes.txt\n"
            f"@@ -1,3 +1,4 @@\n alpha\n+{new}\n {old}\n gamma\n")


# ------------------------------------------------------------------- happy
def test_valid_patch_applies_and_rolls_back(tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "notes.txt"
    target.write_text("alpha\nbeta\ngamma\n")

    receipt = _apply(cfg, target, _append_patch())
    assert receipt["state"] == "OK", receipt
    assert receipt["code"] == "FILE_PATCH_APPLIED"
    assert target.read_text() == "alpha\ninserted\nbeta\ngamma\n"
    assert receipt["before_sha256"] != receipt["after_sha256"]
    assert receipt["backup"]

    undone = FP.rollback(cfg, str(target), backup=receipt["backup"],
                         requested_by="cli:user", confirmed=True)
    assert undone["state"] == "OK", undone
    assert target.read_text() == "alpha\nbeta\ngamma\n"

    # Idempotent: a second rollback is a typed no-op, content unchanged.
    again = FP.rollback(cfg, str(target), backup=receipt["backup"],
                        requested_by="cli:user", confirmed=True)
    assert again["state"] == "NO_CHANGE", again
    assert target.read_text() == "alpha\nbeta\ngamma\n"


def test_rollback_defaults_to_latest_backup(tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "notes.txt"
    target.write_text("alpha\nbeta\ngamma\n")

    first = _apply(cfg, target, _append_patch(new="one"))
    second = _apply(cfg, target,
                    "--- a\n+++ b\n@@ -1,4 +1,5 @@\n alpha\n one\n"
                    "+two\n beta\n gamma\n")
    assert second["state"] == "OK", second
    undone = FP.rollback(cfg, str(target), requested_by="cli:user",
                         confirmed=True)
    assert undone["state"] == "OK", undone
    assert target.read_text() == "alpha\none\nbeta\ngamma\n"
    assert first["backup"] != second["backup"]


# --------------------------------------------------------------- refusals
def test_traversal_rejected(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    secret = tmp_path / "outside.txt"
    secret.write_text("untouched\n")
    with pytest.raises(FP.FilePatchError) as exc:
        _apply(cfg, root / ".." / "outside.txt", _append_patch())
    assert exc.value.code == FP.OUTSIDE_ROOT_CODE
    assert secret.read_text() == "untouched\n"


def test_symlink_escape_rejected(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("untouched\n")
    link = root / "evil"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(FP.FilePatchError) as exc:
        _apply(cfg, link / "secret.txt", _append_patch())
    assert exc.value.code == FP.OUTSIDE_ROOT_CODE
    assert (outside / "secret.txt").read_text() == "untouched\n"
    # A symlinked target (even pointing inside) is refused outright.
    inside = root / "real.txt"
    inside.write_text("alpha\nbeta\ngamma\n")
    tlink = root / "alias.txt"
    try:
        tlink.symlink_to(inside)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(FP.FilePatchError):
        _apply(cfg, tlink, _append_patch())


def test_oversized_patch_rejected(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "notes.txt"
    target.write_text("alpha\nbeta\ngamma\n")
    huge = "x" * (FP.MAX_PATCH_BYTES + 1)
    with pytest.raises(FP.FilePatchError) as exc:
        _apply(cfg, target, huge)
    assert exc.value.code == FP.TOO_LARGE_CODE
    assert target.read_text() == "alpha\nbeta\ngamma\n"


def test_unconfirmed_refused(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "notes.txt"
    target.write_text("alpha\nbeta\ngamma\n")
    with pytest.raises(FP.FilePatchError) as exc:
        FP.apply_patch(cfg, str(target), _append_patch(),
                       requested_by="model:auto", confirmed=False)
    assert exc.value.code == FP.CONFIRM_CODE
    assert target.read_text() == "alpha\nbeta\ngamma\n"


def test_estop_refuses(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "notes.txt"
    target.write_text("alpha\nbeta\ngamma\n")
    from voicebridge.power import power_control_store

    power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                 issued_by="user")
    with pytest.raises(FP.FilePatchError) as exc:
        _apply(cfg, target, _append_patch())
    assert exc.value.code == FP.ESTOP_CODE
    assert target.read_text() == "alpha\nbeta\ngamma\n"


def test_confinement_unavailable_fails_closed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "notes.txt"
    target.write_text("alpha\nbeta\ngamma\n")
    monkeypatch.setattr(FP, "confinement_available",
                        lambda: (False, "", "no userns here"))
    with pytest.raises(FP.FilePatchError) as exc:
        _apply(cfg, target, _append_patch())
    assert exc.value.code == FP.CONFINEMENT_CODE
    assert target.read_text() == "alpha\nbeta\ngamma\n"


def test_conflicting_patch_rejected(tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "notes.txt"
    target.write_text("alpha\nbeta\ngamma\n")
    bad = "--- a\n+++ b\n@@ -1,3 +1,4 @@\n alpha\n+new\n WRONG\n gamma\n"
    with pytest.raises(FP.FilePatchError) as exc:
        _apply(cfg, target, bad)
    assert exc.value.code == FP.CONFLICT_CODE
    assert target.read_text() == "alpha\nbeta\ngamma\n"


def test_permissions_preserved_across_apply_and_rollback(tmp_path,
                                                         monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "notes.txt"
    target.write_text("alpha\nbeta\ngamma\n")
    target.chmod(0o640)
    receipt = _apply(cfg, target, _append_patch())
    assert target.stat().st_mode & 0o777 == 0o640
    FP.rollback(cfg, str(target), backup=receipt["backup"],
                requested_by="cli:user", confirmed=True)
    assert target.stat().st_mode & 0o777 == 0o640


def test_rollback_without_backup_is_typed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "notes.txt"
    target.write_text("alpha\nbeta\ngamma\n")
    with pytest.raises(FP.FilePatchError) as exc:
        FP.rollback(cfg, str(target), requested_by="cli:user",
                    confirmed=True)
    assert exc.value.code == FP.BACKUP_NOT_FOUND_CODE


# --------------------------------------------------------------- redaction
def test_no_secret_leaks_into_receipt_or_audit(tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "notes.txt"
    target.write_text("top\n")

    secret = "sk-ABCDEFGHIJKLMNOPQRSTUVWX"
    captured = []
    import voicebridge.logging_setup as LS

    monkeypatch.setattr(
        LS, "log_event",
        lambda event, **fields: captured.append((event, fields)))
    patch = ("--- a\n+++ b\n@@ -1,1 +1,2 @@\n top\n"
             f"+token = {secret}\n")
    receipt = _apply(cfg, target, patch)
    blob = json.dumps(receipt)
    assert secret not in blob, receipt
    assert "[REDACTED]" in receipt["preview"]
    # The write still happened (proving redaction, not suppression).
    assert secret in target.read_text()
    audit_blob = json.dumps(captured)
    assert secret not in audit_blob, captured
    assert "file_patch.apply" in audit_blob
