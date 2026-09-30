"""Edge coverage for the confined, reversible file-delete capability.

Complements ``test_file_delete_broker_authority.py`` with the two cases that
prove the confinement is *escape* proof and the recovery handle is *itself*
confined (the archive must live under the Qubit state store, never an arbitrary
path):

* a symlink whose target escapes the approved root is refused (not merely a
  symlink to a sibling file inside the root);
* ``restore_deleted`` refuses an ``archive_path`` outside the delete archive,
  so a caller cannot use the recovery API to move an arbitrary host path.

All work is on temp roots; no live user file is touched.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.unit


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
    from voicebridge import file_patch as FP

    return FP.confinement_available()[0]


def test_delete_symlink_escaping_root_refused(tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    from voicebridge import file_patch as FP

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("do not delete\n")
    link = root / "escape-link"
    os.symlink(outside, link)

    with pytest.raises(FP.FilePatchError) as exc:
        FP.delete_path(cfg, str(link), requested_by="t", confirmed=True)
    assert exc.value.code == FP.OUTSIDE_ROOT_CODE, exc.value.code
    assert outside.exists(), "symlink escape must never delete the outside file"
    assert link.is_symlink(), "the link itself is untouched"


def test_restore_refuses_archive_outside_state_dir(tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    from voicebridge import file_patch as FP

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    bystander = tmp_path / "not-an-archive.txt"
    bystander.write_text("keep\n")

    with pytest.raises(FP.FilePatchError) as exc:
        FP.restore_deleted(cfg, str(bystander), requested_by="t",
                           confirmed=True)
    assert exc.value.code == FP.DELETE_ARCHIVE_CODE, exc.value.code
    assert bystander.exists(), "restore must not move an arbitrary host path"


def test_delete_relative_path_without_active_project_refused(tmp_path,
                                                             monkeypatch):
    from voicebridge import file_patch as FP

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(FP.FilePatchError) as exc:
        FP.delete_path(cfg, "relative.txt", requested_by="t", confirmed=True)
    assert exc.value.code == FP.BAD_INPUT_CODE, exc.value.code
