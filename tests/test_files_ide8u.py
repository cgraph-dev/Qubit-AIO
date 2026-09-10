"""Packet 8U — scoped project-folder inspection + IDE launcher.

Hermetic: project folders under tmp_path, fake editor scripts on a
sandboxed PATH, isolated XDG/state. Proves root confinement (traversal,
symlink escape, outside-root refusal), binary/oversize handling,
redaction, CLI/voice/dashboard parity through the same modules, and
that opening is all the IDE path claims.
"""
from __future__ import annotations

import io
import json
import os
import stat
import time
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


def _ctx(cfg, session="voice", source="user"):
    from voicebridge import voice_commands as VC

    return VC.make_ctx(cfg, session=session, source=source)


def _project(cfg, tmp_path, name="Site"):
    from voicebridge import projects as PJ

    folder = tmp_path / "work" / "site"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "notes.md").write_text(
        "# Notes\nCall ops@example.com. bearer sk-test-abcdef1234\n")
    (folder / "sub").mkdir(exist_ok=True)
    (folder / "sub" / "a.txt").write_text("hello\n")
    created = PJ.create(cfg, name, [str(folder)], requested_by="cli:user",
                        confirmed=True)
    slug = created["project"]["slug"]
    PJ.use(cfg, slug, requested_by="cli:user", confirmed=True)
    return folder


# ------------------------------------------------------------------ files
def test_inspect_active_folder(tmp_path, monkeypatch):
    from voicebridge import files as FI

    cfg = _cfg(tmp_path, monkeypatch)
    folder = _project(cfg, tmp_path)
    out = FI.inspect(cfg, "")
    assert out["state"] == "OK"
    names = {e["name"] for e in out["entries"]}
    assert {"notes.md", "sub"} <= names
    assert out["folder"].endswith("site")


def test_inspect_empty_without_active(tmp_path, monkeypatch):
    from voicebridge import files as FI

    cfg = _cfg(tmp_path, monkeypatch)
    out = FI.inspect(cfg, "")
    assert out["state"] == "EMPTY"
    assert out["code"] == "NO_ACTIVE_PROJECT"


def test_read_redacted_and_bounded(tmp_path, monkeypatch):
    from voicebridge import files as FI

    cfg = _cfg(tmp_path, monkeypatch)
    folder = _project(cfg, tmp_path)
    out = FI.read_file(cfg, str(folder / "notes.md"))
    assert out["state"] == "OK"
    assert "sk-test-abcdef1234" not in out["text"]
    assert "ops@example.com" in out["text"]
    assert out["trust"] == "untrusted-file-text"
    # Relative path resolves against the active project folder.
    out2 = FI.read_file(cfg, "notes.md")
    assert out2["state"] == "OK"


def test_outside_root_traversal_symlink_refused(tmp_path, monkeypatch):
    from voicebridge import files as FI

    cfg = _cfg(tmp_path, monkeypatch)
    folder = _project(cfg, tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("private\n")
    for bad in (str(outside), "/etc/hostname",
                str(folder / ".." / "outside.txt")):
        with pytest.raises(FI.FilesError) as exc:
            FI.read_file(cfg, bad)
        assert exc.value.code in (FI.OUTSIDE_ROOT_CODE, FI.NOT_FOUND_CODE,
                                  FI.BAD_INPUT_CODE), bad
    link = folder / "evil"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(FI.FilesError) as exc:
        FI.read_file(cfg, str(link))
    assert exc.value.code == FI.OUTSIDE_ROOT_CODE


def test_binary_oversize_special_refused(tmp_path, monkeypatch):
    from voicebridge import files as FI

    cfg = _cfg(tmp_path, monkeypatch)
    folder = _project(cfg, tmp_path)
    binary = folder / "blob.bin"
    binary.write_bytes(b"\x00\x01\x02binary")
    with pytest.raises(FI.FilesError) as exc:
        FI.read_file(cfg, str(binary))
    assert exc.value.code == FI.BINARY_CODE
    big = folder / "big.txt"
    with open(big, "w") as fh:
        fh.write("x" * (FI.MAX_FILE_BYTES + 8))
    with pytest.raises(FI.FilesError) as exc:
        FI.read_file(cfg, str(big))
    assert exc.value.code == FI.TOO_LARGE_CODE


def test_cli_files_parity(tmp_path, monkeypatch):
    from voicebridge import cli as CLI

    cfg = _cfg(tmp_path, monkeypatch)
    folder = _project(cfg, tmp_path)

    def _run(cmd, **kw):
        args = type("A", (), {"files_command": cmd, **kw})()
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = CLI._cmd_files(cfg, args)
        return rc, json.loads(buf.getvalue())

    rc, body = _run("inspect")
    assert rc == 0 and body["state"] == "OK"
    rc, body = _run("list", path=str(folder))
    assert rc == 0 and len(body["entries"]) >= 2
    rc, body = _run("read", path=str(folder / "notes.md"))
    assert rc == 0 and "sk-test-abcdef1234" not in body["text"]
    rc, body = _run("read", path="/etc/hostname")
    assert rc == 2 and body["code"] == "FILES_OUTSIDE_ROOT"


def test_voice_files_parity(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    _project(cfg, tmp_path)
    for text in ("qubit, inspect this project folder",
                 "qubit, list project files",
                 "qubit, read project file notes.md"):
        res = VC.resolve(text)
        assert res.kind == VC.MATCH, (text, res.kind, res.speech)
        out = VC.execute(_ctx(cfg), res)
        assert out.kind == VC.EXECUTED, (text, out.kind, out.speech)
    assert "sk-test-abcdef1234" not in out.speech


def test_dashboard_files_parity(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as D

    cfg = _cfg(tmp_path, monkeypatch)
    _project(cfg, tmp_path)
    out = D.run_registry_op(cfg, "files_inspect", {},
                            actor="dashboard:user", session="dashboard")
    assert out["state"] == "executed", out
    assert "entries" in out["speech"] or "entries" in json.dumps(
        out.get("receipt", {}))


# --------------------------------------------------------------------- ide
def _fake_editor(tmp_path, monkeypatch, name="code"):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    script = bindir / name
    script.write_text("#!/bin/sh\necho \"$@\" > \"$QB_PROBE_OUT\"\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep +
                       os.environ.get("PATH", ""))
    return bindir


def test_ide_status_lists_detected(tmp_path, monkeypatch):
    from voicebridge import ide as IDE

    _fake_editor(tmp_path, monkeypatch, "code")
    cfg = _cfg(tmp_path, monkeypatch)
    out = IDE.status(cfg)
    assert out["state"] == "OK"
    assert any(e["editor"] == "code" for e in out["editors"])
    assert out["default"] == "code"


def test_ide_open_launches_workspace(tmp_path, monkeypatch):
    from voicebridge import ide as IDE

    _fake_editor(tmp_path, monkeypatch, "cursor")
    probe = tmp_path / "launched.txt"
    monkeypatch.setenv("QB_PROBE_OUT", str(probe))
    monkeypatch.setenv("DISPLAY", ":99")
    cfg = _cfg(tmp_path, monkeypatch)
    folder = _project(cfg, tmp_path)
    with pytest.raises(IDE.IdeError) as exc:
        IDE.open_editor(cfg, "cursor", str(folder), confirmed=False)
    assert exc.value.code == IDE.CONFIRM_CODE
    out = IDE.open_editor(cfg, "cursor", str(folder), confirmed=True)
    assert out["state"] == "OK" and out["editor"] == "cursor"
    for _ in range(100):
        if probe.exists():
            break
        time.sleep(0.05)
    assert str(folder) in probe.read_text()
    assert "launcher only" in out["control"]


def test_ide_open_headless_is_typed(tmp_path, monkeypatch):
    from voicebridge import ide as IDE

    _fake_editor(tmp_path, monkeypatch, "code")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    cfg = _cfg(tmp_path, monkeypatch)
    folder = _project(cfg, tmp_path)
    out = IDE.open_editor(cfg, "code", str(folder), confirmed=True)
    assert out["state"] == "UNAVAILABLE"
    assert out["code"] == IDE.NO_DESKTOP_CODE


def test_ide_open_rejects_unknown_and_missing(tmp_path, monkeypatch):
    from voicebridge import ide as IDE

    cfg = _cfg(tmp_path, monkeypatch)
    _project(cfg, tmp_path)
    with pytest.raises(IDE.IdeError) as exc:
        IDE.open_editor(cfg, "evil --flag", "/tmp", confirmed=True)
    assert exc.value.code == IDE.BAD_INPUT_CODE
    with pytest.raises(IDE.IdeError) as exc:
        IDE.open_editor(cfg, "zed", str("/nonexistent-xyz"),
                        confirmed=True)
    assert exc.value.code in (IDE.NOT_FOUND_CODE, IDE.BAD_INPUT_CODE)


def test_cli_ide_parity(tmp_path, monkeypatch):
    from voicebridge import cli as CLI

    _fake_editor(tmp_path, monkeypatch, "code")
    cfg = _cfg(tmp_path, monkeypatch)

    def _run(cmd, **kw):
        args = type("A", (), {"ide_command": cmd, "editor": "",
                              "folder": "", "confirm": False,
                              "dry_run": False, **kw})()
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = CLI._cmd_ide(cfg, args)
        return rc, json.loads(buf.getvalue())

    rc, body = _run("status")
    assert rc == 0 and body["default"] == "code"
    rc, body = _run("open")
    assert rc == 3 and body["state"] == "denied"
    rc, body = _run("open", dry_run=True)
    assert rc == 0 and body["state"] == "preview"


def test_voice_ide_parity(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    _fake_editor(tmp_path, monkeypatch, "code")
    cfg = _cfg(tmp_path, monkeypatch)
    _project(cfg, tmp_path)
    res = VC.resolve("qubit, which editor")
    assert res.kind == VC.MATCH
    out = VC.execute(_ctx(cfg), res)
    assert out.kind == VC.EXECUTED and "code" in out.speech
    res = VC.resolve("qubit, open my ide")
    out = VC.execute(_ctx(cfg), res)
    assert out.kind == VC.NEEDS_CONFIRM
    assert out.confirmation_id
    done = VC.confirm_pending(_ctx(cfg), out.confirmation_id)
    assert done.kind == VC.EXECUTED, (done.kind, done.speech)


def test_dashboard_ide_parity(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as D

    _fake_editor(tmp_path, monkeypatch, "code")
    cfg = _cfg(tmp_path, monkeypatch)
    out = D.run_registry_op(cfg, "ide_status", {},
                            actor="dashboard:user", session="dashboard")
    assert out["state"] == "executed", out
    staged = D.run_registry_op(cfg, "ide_open", {},
                               actor="dashboard:user", session="dashboard")
    assert staged["state"] == "needs-confirm", staged
