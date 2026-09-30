"""Canonical-authority repair: dashboard file writes go through the broker.

Proves the confirmed, reversible file-mutation path is brokered, not gated by
a client-supplied boolean:

* ``local.apply_patch`` / ``local.files_mkdir`` are registered MUTATING
  capabilities on the canonical host broker (correct tier + scopes);
* a client ``confirm`` boolean is NOT authority — a durable, single-use
  ``confirmation_id`` staged on the SHARED confirmation journal the CLI/voice
  use is required before any write;
* a latched emergency stop refuses ``mkdir`` (which previously skipped the
  gate entirely) and ``write-text`` BEFORE anything is written;
* a confirmed write persists and is reversible from its durable backup.
"""
from __future__ import annotations

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


# ------------------------------------------------------------- registration
def test_register_file_patch_registers_mutating_capabilities():
    from voicebridge import file_patch as FP
    from voicebridge.broker import build_host_broker, register_file_patch
    from voicebridge.capabilities import Tier

    broker = build_host_broker()
    names = register_file_patch(broker)
    assert FP.capability_name() in names
    assert FP.mkdir_capability_name() in names
    # D5: the confined binary download read is registered alongside the writes
    # but is READ_ONLY (a download needs no confirmation) — e-stop/audit/confinement
    # still apply through the shared broker.
    read_name = FP.download_capability_name()
    for name in names:
        spec = broker.registry.get(name)
        assert spec is not None, name
        assert "local" in spec.scopes, name
        if name == read_name:
            assert spec.tier is Tier.READ_ONLY, name
            assert spec.needs_confirmation is False, name
        else:
            assert spec.tier is Tier.MUTATING, name
            assert spec.needs_confirmation is True, name
    assert set(broker.names()) >= set(names)


# ------------------------------------------------------------------- e-stop
def test_mkdir_refused_under_latched_estop(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                 issued_by="user")
    target = root / "should-not-exist"
    code, out = HC.dispatch(cfg, "POST", "/api/files/mkdir", {},
                            {"path": str(target), "confirm": True})
    assert (code, out["code"]) == (403, "FILE_PATCH_ESTOP"), (code, out)
    assert not target.exists(), "estop mkdir must not create"


def test_write_text_refused_under_latched_estop(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "notes.txt"
    target.write_text("hello\n")
    power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                 issued_by="user")
    code, out = HC.dispatch(cfg, "POST", "/api/fs/write-text", {},
                            {"path": str(target), "content": "world",
                             "confirm": True})
    assert (code, out["code"]) == (403, "FILE_PATCH_ESTOP"), (code, out)
    assert target.read_text() == "hello\n"


# ------------------------------------------------ real confirmation required
def test_client_boolean_is_not_authority(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "notes.txt"
    target.write_text("hello\n")
    body = {"path": str(target), "content": "world"}

    # ``confirm: true`` alone stages — it never writes.
    code, out = HC.dispatch(cfg, "POST", "/api/fs/write-text", {},
                            {**body, "confirm": True})
    assert (code, out["code"]) == (409, "CONFIRM_REQUIRED"), (code, out)
    assert out.get("confirmation_id"), out
    assert out.get("state") == "NEEDS_CONFIRMATION", out
    assert target.read_text() == "hello\n", "boolean must not write"


def test_write_text_staged_id_is_durable_and_single_use(tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "notes.txt"
    target.write_text("hello\n")
    body = {"path": str(target), "content": "world"}

    code, out = HC.dispatch(cfg, "POST", "/api/fs/write-text", {}, body)
    assert (code, out["code"]) == (409, "CONFIRM_REQUIRED"), (code, out)
    cid = out.get("confirmation_id")
    assert cid, out
    assert target.read_text() == "hello\n"

    code, out = HC.dispatch(cfg, "POST", "/api/fs/write-text", {},
                            {**body, "confirmation_id": cid})
    assert code == 200 and out["ok"] is True, (code, out)
    assert target.read_text() == "world\n"

    # The durable id is consumed exactly once; a replay fails closed.
    code, out = HC.dispatch(cfg, "POST", "/api/fs/write-text", {},
                            {**body, "confirmation_id": cid})
    assert (code, out["code"]) == (409, "REPLAYED_CONFIRMATION"), (code, out)


def test_upload_stages_on_the_shared_confirmation_journal(tmp_path,
                                                          monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "up.txt"

    code, out = HC.dispatch(cfg, "POST", "/api/files/upload", {},
                            {"path": str(target), "content": "up"})
    assert (code, out["code"]) == (409, "CONFIRM_REQUIRED"), (code, out)
    cid = out["confirmation_id"]

    # The staged id is the SAME record the CLI/voice/dashboard banner read.
    acode, appr = HC.dispatch(cfg, "GET", "/api/ops/approvals", {}, {})
    assert acode == 200, appr
    assert cid in {a["id"] for a in appr["approvals"]}, appr
    assert any(a["capability"] == "local.apply_patch"
               for a in appr["approvals"]), appr

    code, out = HC.dispatch(cfg, "POST", "/api/files/upload", {},
                            {"path": str(target), "content": "up",
                             "confirmation_id": cid})
    assert code == 200 and out["ok"] is True, (code, out)
    assert target.read_text() == "up"


# ------------------------------------------------------- persist + reverse
def test_confirmed_write_persists_and_is_reversible(tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    from voicebridge import file_patch as FP
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "notes.txt"
    target.write_text("alpha\nbeta\ngamma\n")
    body = {"path": str(target), "content": "broken"}

    code, out = HC.dispatch(cfg, "POST", "/api/fs/write-text", {}, body)
    cid = out["confirmation_id"]
    code, out = HC.dispatch(cfg, "POST", "/api/fs/write-text", {},
                            {**body, "confirmation_id": cid})
    assert code == 200 and out["ok"] is True, (code, out)
    assert target.read_text() == "broken\n"
    backup = out["receipt"].get("backup")
    assert backup, out
    assert out["receipt"]["code"] == "FILE_PATCH_APPLIED", out

    undone = FP.rollback(cfg, str(target), backup=backup,
                         requested_by="dashboard:user", confirmed=True)
    assert undone["state"] == "OK", undone
    assert target.read_text() == "alpha\nbeta\ngamma\n"


def test_confirmed_mkdir_persists(tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "made"

    code, out = HC.dispatch(cfg, "POST", "/api/files/mkdir", {},
                            {"path": str(target)})
    assert (code, out["code"]) == (409, "CONFIRM_REQUIRED"), (code, out)
    cid = out["confirmation_id"]
    code, out = HC.dispatch(cfg, "POST", "/api/files/mkdir", {},
                            {"path": str(target), "confirmation_id": cid})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["created"] is True and target.is_dir()
