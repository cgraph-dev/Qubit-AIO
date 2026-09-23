"""Canonical-authority repair: a real, policy-bounded ``file delete`` capability.

Proves the dashboard delete path has a real backend (not a blanket refusal)
without weakening the canonical authority chain:

* ``local.files_delete`` is registered MUTATING on the shared host broker
  (confirmation-required, ``local`` scope) alongside apply/mkdir;
* deletion requires confirmation, is confined to the approved project root
  (absolute escapes, ``..`` traversal, and symlinks are typed refusals), and
  refuses to delete the approved root itself;
* a confirmed delete performs the real archive-move on a temp root and is
  reversible via ``restore_deleted``;
* the delete emits an audit event;
* the write/mkdir/delete capabilities accept a dashboard body that carries the
  settle flags (``confirm`` / ``confirmation_id``) — stripped as broker-level
  control, never mistaken for capability args.
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


def _broker(cfg):
    from voicebridge.broker import build_host_broker, register_file_patch

    broker = build_host_broker(cfg=cfg)
    register_file_patch(broker, cfg=cfg)
    return broker


# ------------------------------------------------------------- registration
def test_register_file_delete_is_mutating_and_confined():
    from voicebridge import file_patch as FP
    from voicebridge.broker import build_host_broker, register_file_patch
    from voicebridge.capabilities import Tier

    broker = build_host_broker()
    names = register_file_patch(broker)
    assert FP.delete_capability_name() == "local.files_delete"
    assert FP.delete_capability_name() in names
    spec = broker.registry.get(FP.delete_capability_name())
    assert spec is not None
    assert spec.tier is Tier.MUTATING
    assert spec.needs_confirmation is True
    assert "local" in spec.scopes


# ------------------------------------------------- confirmation required
def test_delete_requires_confirmation(tmp_path, monkeypatch):
    from voicebridge import file_patch as FP

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "kill.txt"
    target.write_text("keep me\n")

    with pytest.raises(FP.FilePatchError) as exc:
        FP.delete_path(cfg, str(target), requested_by="dashboard:user",
                       confirmed=False)
    assert exc.value.code == FP.CONFIRM_CODE, exc.value.code
    assert target.exists(), "unconfirmed delete must not remove the file"


def test_delete_refused_under_latched_estop(tmp_path, monkeypatch):
    from voicebridge import file_patch as FP
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "kill.txt"
    target.write_text("keep me\n")
    power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                 issued_by="user")

    with pytest.raises(FP.FilePatchError) as exc:
        FP.delete_path(cfg, str(target), requested_by="t", confirmed=True)
    assert exc.value.code == FP.ESTOP_CODE, exc.value.code
    assert target.exists(), "e-stop delete must not remove the file"


# --------------------------------------------------------------- confinement
def test_delete_outside_root_refused(tmp_path, monkeypatch):
    from voicebridge import file_patch as FP

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    outside = tmp_path / "outside.txt"
    outside.write_text("nope\n")

    with pytest.raises(FP.FilePatchError) as exc:
        FP.delete_path(cfg, str(outside), requested_by="t", confirmed=True)
    assert exc.value.code == FP.OUTSIDE_ROOT_CODE, exc.value.code
    assert outside.exists()


def test_delete_traversal_refused(tmp_path, monkeypatch):
    from voicebridge import file_patch as FP

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    sub = root / "sub"
    sub.mkdir(parents=True)
    _project(cfg, root)
    outside = tmp_path / "escape.txt"
    outside.write_text("nope\n")

    traversal = str(sub / ".." / ".." / "escape.txt")
    with pytest.raises(FP.FilePatchError) as exc:
        FP.delete_path(cfg, traversal, requested_by="t", confirmed=True)
    assert exc.value.code == FP.OUTSIDE_ROOT_CODE, exc.value.code
    assert outside.exists()


def test_delete_symlink_refused(tmp_path, monkeypatch):
    from voicebridge import file_patch as FP

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    real = root / "real.txt"
    real.write_text("data\n")
    link = root / "link.txt"
    os.symlink(real, link)

    with pytest.raises(FP.FilePatchError) as exc:
        FP.delete_path(cfg, str(link), requested_by="t", confirmed=True)
    assert exc.value.code == FP.OUTSIDE_ROOT_CODE, exc.value.code
    assert real.exists() and link.is_symlink()


def test_delete_approved_root_refused(tmp_path, monkeypatch):
    from voicebridge import file_patch as FP

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)

    with pytest.raises(FP.FilePatchError) as exc:
        FP.delete_path(cfg, str(root), requested_by="t", confirmed=True,
                       recursive=True)
    assert exc.value.code == FP.DELETE_ROOT_CODE, exc.value.code
    assert root.is_dir()


# --------------------------------------------------- real delete + reversal
def test_delete_file_archives_and_restores(tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    from voicebridge import file_patch as FP

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "notes.txt"
    target.write_text("alpha\nbeta\n")

    receipt = FP.delete_path(cfg, str(target), requested_by="dashboard:user",
                             confirmed=True)
    assert receipt["state"] == "OK", receipt
    assert receipt["code"] == "FILES_DELETED", receipt
    assert receipt["kind"] == "file"
    assert not target.exists(), "real delete must remove the file"
    archive = receipt["archived_to"]
    assert os.path.exists(archive), receipt

    restored = FP.restore_deleted(cfg, archive, requested_by="dashboard:user",
                                  confirmed=True)
    assert restored["state"] == "OK", restored
    assert target.read_text() == "alpha\nbeta\n"


def test_delete_directory_recursive_and_reversible(tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    from voicebridge import file_patch as FP

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    folder = root / "bundle"
    folder.mkdir()
    (folder / "a.txt").write_text("a\n")
    (folder / "nested").mkdir()
    (folder / "nested" / "b.txt").write_text("b\n")

    with pytest.raises(FP.FilePatchError) as exc:
        FP.delete_path(cfg, str(folder), requested_by="t", confirmed=True)
    assert exc.value.code == FP.DELETE_NOT_EMPTY_CODE, exc.value.code
    assert folder.is_dir()

    receipt = FP.delete_path(cfg, str(folder), requested_by="t",
                             confirmed=True, recursive=True)
    assert receipt["kind"] == "directory", receipt
    assert not folder.exists(), receipt
    assert os.path.isdir(receipt["archived_to"]), receipt

    restored = FP.restore_deleted(cfg, receipt["archived_to"],
                                  requested_by="t", confirmed=True)
    assert restored["state"] == "OK", restored
    assert (folder / "a.txt").read_text() == "a\n"
    assert (folder / "nested" / "b.txt").read_text() == "b\n"


def test_delete_is_audited(tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    from voicebridge import file_patch as FP

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "auditme.txt"
    target.write_text("bye\n")

    events = []
    monkeypatch.setattr("voicebridge.logging_setup.log_event",
                        lambda event, **fields: events.append((event, fields)))
    FP.delete_path(cfg, str(target), requested_by="dashboard:user",
                   confirmed=True)
    assert any(e == "file_patch.delete" for e, _ in events), events
    detail = next(f for e, f in events if e == "file_patch.delete")
    assert detail.get("target", "").endswith("auditme.txt"), detail


# ------------------------------------ broker executes the confirmed delete
def test_broker_confirmed_delete_executes(tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    from voicebridge import file_patch as FP

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "broker.txt"
    target.write_text("x\n")
    broker = _broker(cfg)
    clean = {"target_path": str(target), "requested_by": "dashboard:user",
             "project_root": ""}
    rec = broker.issue_confirmation(FP.delete_capability_name(), clean,
                                    issued_by="user", ttl_s=120)
    res = broker.execute(FP.delete_capability_name(), clean, source="user",
                         confirmation_id=rec.id, target=str(target))
    assert res.ok, (res.denied, res.error)
    assert not target.exists()


# ---------------------------------- dashboard settle flags are not args
def _settle(broker, capability, clean, *, target):
    rec = broker.issue_confirmation(capability, clean, issued_by="user",
                                    ttl_s=120)
    # The dashboard re-issues the SAME body with the settle flags added; they
    # must not change the confirmation digest, be rejected as args, or be
    # treated as authority on their own.
    forwarded = {**clean, "confirm": True, "confirmation_id": rec.id}
    res = broker.execute(capability, forwarded, source="user",
                         confirmation_id=rec.id, target=target)
    assert res.ok, (res.denied, res.error)


def test_forwarded_settle_flags_are_stripped_for_file_capabilities(
        tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    from voicebridge import file_patch as FP

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    broker = _broker(cfg)

    target = root / "patch.txt"
    target.write_text("one\n")
    patch = ("--- a/patch.txt\n+++ b/patch.txt\n@@ -1 +1 @@\n-one\n+two\n")
    _settle(broker, FP.capability_name(),
            {"target_path": str(target), "patch_text": patch,
             "requested_by": "dashboard:user", "project_root": ""},
            target=str(target))
    assert target.read_text() == "two\n"

    made = root / "made"
    mkdir_args = {"target_path": str(made), "requested_by": "dashboard:user",
                  "project_root": ""}
    _settle(broker, FP.mkdir_capability_name(), mkdir_args, target=str(made))
    assert made.is_dir()

    delete_args = {**mkdir_args, "recursive": True}
    _settle(broker, FP.delete_capability_name(), delete_args, target=str(made))
    assert not made.exists()


# ------------------------- exact contract the compat handler will wire
def test_compat_broker_mutation_contract_for_delete(tmp_path, monkeypatch):
    """The dashboard handler stages then settles by re-issuing the same request.

    This mirrors ``h_files_mkdir``'s use of ``_brokered_file_mutation`` so the
    wiring agent can copy it: no ``confirmation_id`` -> typed 409 stage; the
    SAME args plus the staged id -> real, audited, confined delete.
    """
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    from voicebridge import file_patch as FP
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "dashboard-delete.txt"
    target.write_text("remove me\n")
    args = {"target_path": str(target), "requested_by": "dashboard:user",
            "project_root": "", "recursive": False}
    kwargs = dict(q={}, target=str(target), detail="stage the delete",
                  counterpart="CLI `qubit files inspect`")

    resp, receipt = HC._brokered_file_mutation(
        cfg, FP.delete_capability_name(), args, b={}, **kwargs)
    assert resp is not None and resp[0] == 409, (resp, receipt)
    assert resp[1]["confirmation_id"], resp
    assert target.exists(), "stage must not delete"

    resp, receipt = HC._brokered_file_mutation(
        cfg, FP.delete_capability_name(), args,
        b={"confirmation_id": resp[1]["confirmation_id"]}, **kwargs)
    assert resp is None and receipt is not None, resp
    assert receipt.get("state") == "OK", receipt
    assert not target.exists(), "settled delete must remove the file"
