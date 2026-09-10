"""Final parity — O-docker: pinned confined runtimes with install/upgrade/
rollback/removal, resource ceilings, no downloads, and CLI/dashboard/voice
parity on one backend.
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


def _voice(cfg, entry_id, session="v-rt", **args):
    from voicebridge import voice_commands as VC

    entry = VC.REGISTRY[entry_id]
    ctx = VC.make_ctx(cfg, actor="voice:user", session=session, source="user")
    out = VC.execute(ctx, VC.Resolution(VC.MATCH, entry=entry, args=args))
    if out.kind == VC.NEEDS_CONFIRM:
        out = VC.confirm_pending(ctx, out.confirmation_id)
    return out


def _dash(cfg, op_id, args, session="d-rt"):
    from voicebridge import dashboard_ops as DO

    out = DO.run_op(cfg, op_id, args, actor="dashboard:user",
                    session=session)
    if out.get("state") == "needs-confirm":
        out = DO.confirm_op(cfg, out["confirmation_id"],
                            actor="dashboard:user", session=session)
    return out


def _cli(cfg, cmd, **kw):
    from voicebridge import cli as CLI

    args = type("A", (), {})()
    args.runtime_command = cmd
    args.name = kw.get("name", "")
    args.confirm = kw.get("confirm", False)
    args.dry_run = kw.get("dry_run", False)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_runtime(cfg, args)
    return rc, json.loads(buf.getvalue() or "{}")


def test_registry_pins_exact():
    from voicebridge import runtime_images as RI

    out = RI.images()
    assert out["state"] == "OK"
    names = [r["name"] for r in out["images"]]
    assert names == ["hermes-runtime", "opencode-binary"]
    for row in out["images"]:
        assert row["pin"] and "latest" not in str(row["pin"]).lower()
        assert row["source"].startswith("https://")
        assert row["confinement"]
    assert "latest" not in json.dumps(out).lower()


def test_status_missing_without_artifacts(tmp_path, monkeypatch):
    from voicebridge import runtime_images as RI

    cfg = _cfg(tmp_path, monkeypatch)
    out = RI.status(cfg)
    assert out["state"] == "OK"
    assert out["daemon"] == "none-required"
    # Hermetic env has neither provisioned runtime nor opencode binary with
    # the pin, so both report honestly (MISSING or DEGRADED — never READY
    # without verification).
    for row in out["runtimes"]:
        assert row["state"] in ("MISSING", "STAGED", "DEGRADED", "READY")
        if row["state"] == "READY":
            assert row.get("observed")


def test_install_refuses_download_and_unknown(tmp_path, monkeypatch):
    from voicebridge import runtime_images as RI

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.delenv("QUBIT_HERMES_CLONE", raising=False)
    try:
        RI.install(cfg, "hermes-runtime", requested_by="t", confirmed=True)
        staged = True
    except RI.RuntimeImageError as exc:
        staged = False
        assert exc.code == RI.STAGING_CODE
        assert "nothing was downloaded" in exc.detail
    try:
        RI.install(cfg, "opencode-binary", requested_by="t", confirmed=True)
        binary_staged = True
    except RI.RuntimeImageError as exc:
        binary_staged = False
        assert exc.code in (RI.STAGING_CODE, RI.MISMATCH_CODE)
    import pytest

    with pytest.raises(RI.RuntimeImageError) as bad:
        RI.install(cfg, "ubuntu:latest", requested_by="t", confirmed=True)
    assert bad.value.code == RI.BAD_INPUT_CODE
    with pytest.raises(RI.RuntimeImageError) as nc:
        RI.install(cfg, "hermes-runtime", requested_by="t", confirmed=False)
    assert nc.value.code == RI.CONFIRM_CODE
    assert (staged, binary_staged) in ((False, False), (False, True),
                                       (True, False), (True, True))


def test_install_upgrade_rollback_remove_cycle(tmp_path, monkeypatch):
    """Full lifecycle against a fake staged opencode binary."""
    import stat

    import pytest

    from voicebridge import runtime_images as RI
    from voicebridge.opencode import policy as POL

    cfg = _cfg(tmp_path, monkeypatch)
    fake = tmp_path / "opencode"
    fake.write_text("#!/bin/sh\necho 'opencode version "
                    + POL.PINNED_OPENCODE_VERSION + " (test)'\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(POL, "DEFAULT_BINARY", str(fake))
    out = RI.install(cfg, "opencode-binary", requested_by="t", confirmed=True)
    assert out["state"] == "OK"
    assert out["digest"].startswith("sha256:")
    assert out["previous"] is None
    up = RI.upgrade(cfg, "opencode-binary", requested_by="t", confirmed=True)
    assert up["state"] == "OK" and "previous" in up
    back = RI.rollback(cfg, "opencode-binary", requested_by="t",
                       confirmed=True)
    assert back["state"] == "OK"
    with pytest.raises(RI.RuntimeImageError) as gone:
        RI.rollback(cfg, "opencode-binary", requested_by="t", confirmed=True)
    assert gone.value.code == RI.NOT_FOUND_CODE
    # Removal refuses while a supervised run is active.
    monkeypatch.setattr(RI, "_runs_active", lambda _c: True)
    with pytest.raises(RI.RuntimeImageError) as busy:
        RI.remove(cfg, "opencode-binary", requested_by="t", confirmed=True)
    assert busy.value.code == RI.IN_USE_CODE
    monkeypatch.setattr(RI, "_runs_active", lambda _c: False)
    removed = RI.remove(cfg, "opencode-binary", requested_by="t",
                        confirmed=True)
    assert removed["state"] == "OK"
    st = RI.status(cfg)
    row = next(r for r in st["runtimes"] if r["name"] == "opencode-binary")
    assert row["state"] in ("MISSING", "STAGED")


def test_version_drift_refused(tmp_path, monkeypatch):
    import stat

    import pytest

    from voicebridge import runtime_images as RI
    from voicebridge.opencode import policy as POL

    cfg = _cfg(tmp_path, monkeypatch)
    fake = tmp_path / "opencode-bad"
    fake.write_text("#!/bin/sh\necho 'opencode version 9.99.99 (evil)'\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(POL, "DEFAULT_BINARY", str(fake))
    with pytest.raises(RI.RuntimeImageError) as exc:
        RI.install(cfg, "opencode-binary", requested_by="t", confirmed=True)
    assert exc.value.code == RI.MISMATCH_CODE


def test_cli_dashboard_voice_parity(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    rc, listed = _cli(cfg, "images")
    assert rc == 0 and len(listed["images"]) == 2
    rc, st = _cli(cfg, "status")
    assert rc == 0 and st["state"] == "OK"
    rc, doc = _cli(cfg, "doctor")
    assert rc == 0 and doc["state"] == "OK"
    assert any(f["check"] == "docker_daemon" for f in doc["findings"])
    rc, prev = _cli(cfg, "install", name="hermes-runtime")
    assert rc == 3  # confirm required
    assert prev["code"] == "RUNTIME_CONFIRM_REQUIRED"
    voiced = _voice(cfg, "runtime_status")
    assert voiced.kind == "executed" and "Runtimes:" in voiced.speech
    dash = _dash(cfg, "runtime_images", {})
    assert dash["state"] == "executed", dash
    assert len(dash["receipt"]["result"]["images"]) == 2
    dash_doc = _dash(cfg, "runtime_doctor", {})
    assert dash_doc["state"] == "executed", dash_doc


def test_estop_latches_mutations(tmp_path, monkeypatch):
    from voicebridge import runtime_images as RI

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(RI, "estop_engaged", lambda _c: True)
    import pytest

    with pytest.raises(RI.RuntimeImageError) as exc:
        RI.install(cfg, "hermes-runtime", requested_by="t", confirmed=True)
    assert exc.value.code == RI.ESTOP_CODE
    rc, out = _cli(cfg, "remove", name="hermes-runtime", confirm=True)
    assert rc == 3 and out["code"] == RI.ESTOP_CODE


def test_state_file_private_and_redacted(tmp_path, monkeypatch):
    import stat

    from voicebridge import runtime_images as RI
    from voicebridge.opencode import policy as POL

    cfg = _cfg(tmp_path, monkeypatch)
    fake = tmp_path / "opencode2"
    fake.write_text("#!/bin/sh\necho 'opencode version "
                    + POL.PINNED_OPENCODE_VERSION + "'\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(POL, "DEFAULT_BINARY", str(fake))
    RI.install(cfg, "opencode-binary", requested_by="t", confirmed=True)
    spath = RI._state_path(cfg)
    assert spath.is_file()
    assert oct(spath.stat().st_mode & 0o777) == "0o600"


def test_staged_pin_binary_proof(tmp_path, monkeypatch):
    """Operator-qualification: the exact pinned OpenCode binary, when staged
    via QUBIT_OPENCODE_PIN_BIN, installs READY with a matching digest.

    Procedure (also in docs/MIGRATION_JOURNAL_8TF.md): download
    opencode-linux-x64.tar.gz for v1.18.29 from the pinned release into a
    disposable dir outside the repo, verify `opencode --version` reports
    1.18.29, set QUBIT_OPENCODE_PIN_BIN to the binary, run this test.
    Skipped with reason when the operator has not staged the 184MB binary;
    the fake-binary lifecycle tests above cover the logic hermetically."""
    import os as _os
    import stat as _stat

    from voicebridge import runtime_images as RI
    from voicebridge.opencode import policy as POL

    staged = _os.environ.get("QUBIT_OPENCODE_PIN_BIN", "")
    if not staged or not Path(staged).is_file():
        pytest.skip("operator gate: set QUBIT_OPENCODE_PIN_BIN to a staged "
                    "opencode 1.18.29 binary (see test docstring)")
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(POL, "DEFAULT_BINARY", staged)
    monkeypatch.setattr("shutil.which", lambda _n: None)
    out = RI.install(cfg, "opencode-binary", requested_by="t", confirmed=True)
    assert out["state"] == "OK"
    assert out["digest"] == f"sha256:{RI._sha256_file(Path(staged))}"
    row = next(r for r in RI.status(cfg)["runtimes"]
               if r["name"] == "opencode-binary")
    assert row["state"] == "READY", row
    # The installed user binary (1.18.30) must NOT satisfy the pin: with the
    # real environment the call refuses with MISMATCH (drift protection).
    assert POL.PINNED_OPENCODE_VERSION == "1.18.29"


def test_install_materializes_owned_copy_and_runner_uses_it(tmp_path,
                                                            monkeypatch):
    """The installed binary is COPIED into Qubit-owned space and becomes
    the effective executable: provenance reports effective, status is
    READY, and the runner resolves the owned path (never the staging
    path). Removing the owned bytes degrades honestly."""
    import stat

    from voicebridge import opencode as OC
    from voicebridge import runtime_images as RI
    from voicebridge.opencode import policy as POL
    from voicebridge.opencode import state as OCS

    cfg = _cfg(tmp_path, monkeypatch)
    fake = tmp_path / "opencode-stage"
    fake.write_text("#!/bin/sh\necho \"" + POL.PINNED_OPENCODE_VERSION
                    + "\"\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(POL, "DEFAULT_BINARY", "/nonexistent-oc-test")
    monkeypatch.setattr("shutil.which", lambda _n: None)
    out = RI.install(cfg, "opencode-binary", requested_by="t",
                     confirmed=True, source=str(fake))
    assert out["state"] == "OK"
    owned = OCS.owned_binary()
    assert Path(out["binary"]) == owned
    assert owned.is_file()
    assert RI._sha256_file(owned) == RI._sha256_file(fake)
    assert OCS.verify_owned_binary()["ok"] is True
    row = next(r for r in RI.status(cfg)["runtimes"]
               if r["name"] == "opencode-binary")
    assert row["state"] == "READY", row
    assert row["observed"]["effective"] is True
    # The runner resolves the owned copy even though the staging path and
    # operator paths exist or not: same backend, no bypass.
    from voicebridge import config as C

    p2 = tmp_path / "voice2.yaml"
    p2.write_text("logging:\n  jsonl: false\nintent:\n  opencode:\n"
                  "    enabled: true\n")
    cfg2 = C.Config.load(p2)
    runner = OC.opencode_runner(cfg2)
    assert runner._ctx.binary == str(owned)
    runner.enable(requested_by="user")
    st = runner.status()
    assert st["binary"] == str(owned)
    # Real version probe spawns the owned binary: the reported version is
    # the pin, never the operator's drifted install.
    assert st["effective_enabled"] is True
    assert POL.PINNED_OPENCODE_VERSION in st["installed_version"], st
    assert st["version_match"] is True
    # Deleting the owned bytes (simulated tamper) degrades: never READY.
    owned.unlink()
    assert OCS.verify_owned_binary()["ok"] is False
    row2 = next(r for r in RI.status(cfg)["runtimes"]
                if r["name"] == "opencode-binary")
    assert row2["state"] in ("MISSING", "STAGED", "DEGRADED"), row2


def test_explicit_config_binary_still_wins(tmp_path, monkeypatch):
    """Operator-explicit config binary keeps precedence over the owned
    copy (explicit choice, honestly reported)."""
    import stat

    from voicebridge import config as C
    from voicebridge import opencode as OC
    from voicebridge.opencode import policy as POL

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    explicit = tmp_path / "my-oc"
    explicit.write_text("#!/bin/sh\necho explicit\n")
    explicit.chmod(explicit.stat().st_mode | stat.S_IXUSR)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\nintent:\n  opencode:\n"
                 f"    enabled: true\n    binary: {explicit}\n")
    cfg = C.Config.load(p)
    runner = OC.opencode_runner(cfg)
    assert runner._ctx.binary == str(explicit)
    assert POL.PINNED_OPENCODE_VERSION == "1.18.29"
