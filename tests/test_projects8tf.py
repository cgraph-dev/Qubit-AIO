"""Packet 8T-F — Hermes `project` parity: named multi-folder workspaces.

Proves the registry is real (validated folders, primary tracking, active
pointer, archive/restore, audited rollback) and the unsafe/dependent paths
are typed-refused (secret-bearing folders, board binding before kanban,
e-stop latch, missing confirmation).
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


def _work(tmp_path, *names):
    dirs = []
    for name in names:
        d = tmp_path / "work" / name
        d.mkdir(parents=True, exist_ok=True)
        dirs.append(str(d))
    return dirs


class _Args:
    def __init__(self, cmd, **kw):
        self.project_command = cmd
        self.name = kw.get("name", "")
        self.folders = kw.get("folders", [])
        self.slug = kw.get("slug", "")
        self.new_name = kw.get("new_name", "")
        self.folder = kw.get("folder", "")
        self.description = kw.get("description", "")
        self.primary = kw.get("primary", "")
        self.board = kw.get("board", "")
        self.backup = kw.get("backup", "")
        self.archived = kw.get("archived", False)
        self.use = kw.get("use", False)
        self.clear = kw.get("clear", False)
        self.confirm = kw.get("confirm", False)
        self.dry_run = kw.get("dry_run", False)


def _run(cfg, args):
    from voicebridge import cli as CLI

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_project(cfg, args)
    return rc, json.loads(buf.getvalue())


def _create(cfg, tmp_path, name="Site", **kw):
    alpha, beta = _work(tmp_path, "alpha", "beta")
    kw.setdefault("folders", [alpha])
    return _run(cfg, _Args("create", name=name, **kw)), alpha, beta


# ------------------------------------------------- happy path
def test_create_list_show_roundtrip(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    (rc, out), alpha, _ = _create(cfg, tmp_path, confirm=True)
    assert rc == 0, out
    proj = out["project"]
    assert proj["slug"] == "site" and proj["primary"] == alpha
    assert proj["folders"] == [alpha] and proj["active"] is False
    rc, out = _run(cfg, _Args("list"))
    assert rc == 0 and [p["slug"] for p in out["projects"]] == ["site"]
    assert out["active_slug"] is None
    rc, out = _run(cfg, _Args("show", slug="site"))
    assert rc == 0 and out["project"]["name"] == "Site"


def test_create_with_use_sets_active(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    (rc, out), _, _ = _create(cfg, tmp_path, confirm=True, use=True)
    assert rc == 0 and out["project"]["active"] is True
    assert _run(cfg, _Args("list"))[1]["active_slug"] == "site"


def test_use_and_clear_pointer(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _create(cfg, tmp_path, confirm=True)
    assert _run(cfg, _Args("use", slug="site", confirm=True))[1]["active_slug"] == "site"
    assert _run(cfg, _Args("use", clear=True, confirm=True))[1]["active_slug"] is None


def test_folder_lifecycle_and_primary_fallback(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    (rc, _), alpha, beta = _create(cfg, tmp_path, confirm=True)
    assert rc == 0
    assert _run(cfg, _Args("add-folder", slug="site", folder=beta,
                           confirm=True))[1]["project"]["folders"] == [alpha, beta]
    rc, out = _run(cfg, _Args("set-primary", slug="site", folder=beta, confirm=True))
    assert rc == 0 and out["project"]["primary"] == beta
    rc, out = _run(cfg, _Args("remove-folder", slug="site", folder=beta, confirm=True))
    assert rc == 0 and out["project"]["primary"] == alpha, \
        "primary falls back when removed"
    assert out["project"]["folders"] == [alpha]


def test_rename_archive_restore(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _create(cfg, tmp_path, confirm=True)
    rc, out = _run(cfg, _Args("rename", slug="site", new_name="Portal", confirm=True))
    assert rc == 0 and out["project"]["name"] == "Portal"
    assert out["project"]["slug"] == "site", "slug is stable across rename"
    assert _run(cfg, _Args("archive", slug="site", confirm=True))[0] == 0
    assert _run(cfg, _Args("list"))[1]["projects"] == []
    assert len(_run(cfg, _Args("list", archived=True))[1]["projects"]) == 1
    rc, out = _run(cfg, _Args("add-folder", slug="site",
                              folder=str(tmp_path), confirm=True))
    assert rc == 2 and out["code"] == "PROJECT_ARCHIVED", out
    assert _run(cfg, _Args("restore", slug="site", confirm=True))[0] == 0
    assert len(_run(cfg, _Args("list"))[1]["projects"]) == 1


def test_rollback_restores_snapshot(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _create(cfg, tmp_path, name="One", confirm=True)
    _create(cfg, tmp_path, name="Two", confirm=True)
    assert len(_run(cfg, _Args("list"))[1]["projects"]) == 2
    rc, out = _run(cfg, _Args("rollback", confirm=True))
    assert rc == 0 and out["restored_projects"] == ["one"], out
    assert [p["slug"] for p in _run(cfg, _Args("list"))[1]["projects"]] == ["one"]


def test_confirm_required_and_dry_run_preview(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    alpha = _work(tmp_path, "alpha")[0]
    rc, out = _run(cfg, _Args("create", name="Site", folders=[alpha]))
    assert rc == 3 and out["state"] == "denied"
    assert _run(cfg, _Args("list"))[1]["projects"] == []
    rc, out = _run(cfg, _Args("create", name="Site", folders=[alpha],
                              confirm=True, dry_run=True))
    assert rc == 0 and out["state"] == "preview"
    assert _run(cfg, _Args("list"))[1]["projects"] == []


def test_folders_prevalidated_as_runnable_roots(tmp_path, monkeypatch):
    """Recorded folders pass the 8R project-root gate (no decoration)."""
    from voicebridge.opencode.policy import resolve_project_root

    cfg = _cfg(tmp_path, monkeypatch)
    (rc, out), alpha, _ = _create(cfg, tmp_path, confirm=True)
    assert rc == 0
    assert resolve_project_root(out["project"]["folders"][0]) == Path(alpha)


# ------------------------------------------------- refusals
def test_secret_and_root_folders_refused(tmp_path, monkeypatch):
    import os

    cfg = _cfg(tmp_path, monkeypatch)
    ssh = tmp_path / "vault" / ".ssh"
    # A stand-in secret-bearing path (never touches the real ~/.ssh): the
    # 8R gate refuses any root at or inside an exact secret dir name.
    ssh.mkdir(parents=True, exist_ok=True)
    try:
        rc, out = _run(cfg, _Args("create", name="Bad", folders=[str(ssh)],
                                  confirm=True))
        assert rc == 2 and out["code"] == "PROJECT_BAD_FOLDER", out
    finally:
        ssh.rmdir()
    # Home root itself is refused by the same gate (any existing home).
    rc, out = _run(cfg, _Args("create", name="Bad",
                              folders=[os.path.expanduser("~")], confirm=True))
    assert rc == 2 and out["code"] == "PROJECT_BAD_FOLDER", out
    assert _run(cfg, _Args("list"))[1]["projects"] == []


def test_missing_and_duplicate_and_bad_names(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    alpha = _work(tmp_path, "alpha")[0]
    rc, out = _run(cfg, _Args("create", name="Site",
                              folders=["/nonexistent-8tf/wd"], confirm=True))
    assert rc == 2 and out["code"] == "PROJECT_BAD_FOLDER", out
    assert _run(cfg, _Args("create", name="Site", folders=[alpha],
                           confirm=True))[0] == 0
    rc, out = _run(cfg, _Args("create", name="Site", folders=[alpha],
                              confirm=True))
    assert rc == 2 and out["code"] == "PROJECT_EXISTS", out
    rc, out = _run(cfg, _Args("show", slug="nope"))
    assert rc == 2 and out["code"] == "PROJECT_NOT_FOUND", out
    rc, out = _run(cfg, _Args("create", name="  ", folders=[alpha], confirm=True))
    assert rc == 2 and out["code"] == "PROJECT_BAD_NAME", out


def test_primary_must_be_member(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    alpha, beta = _work(tmp_path, "alpha", "beta")
    rc, out = _run(cfg, _Args("create", name="Site", folders=[alpha],
                              primary=beta, confirm=True))
    assert rc == 0, out
    assert out["project"]["primary"] == beta, \
        "explicit primary joins the folders (Hermes parity)"
    assert set(out["project"]["folders"]) == {alpha, beta}


def test_bind_board_typed_refusal_until_kanban(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _create(cfg, tmp_path, confirm=True)
    rc, out = _run(cfg, _Args("bind-board", slug="site", board="team",
                              confirm=True))
    assert rc == 2 and out["code"] == "PROJECT_NO_BOARD", out


def test_description_redacted_in_output(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    alpha = _work(tmp_path, "alpha")[0]
    rc, out = _run(cfg, _Args("create", name="Site", folders=[alpha],
                              description="token=abc123secretvalue",
                              confirm=True))
    assert rc == 0
    assert "abc123secretvalue" not in json.dumps(out)
    assert "[REDACTED]" in json.dumps(out)


def test_audit_journal_records_mutations(tmp_path, monkeypatch):
    from voicebridge.logging_setup import setup

    cfg = _cfg(tmp_path, monkeypatch)
    setup(level="INFO", jsonl=True, state_dir=cfg.state_dir())
    try:
        _create(cfg, tmp_path, confirm=True)
        journal = cfg.state_dir() / "events.jsonl"
        assert journal.exists()
        blob = journal.read_text()
        assert "project.create" in blob and "site" in blob
        assert "abc123" not in blob
    finally:
        from voicebridge import logging_setup as LS

        LS._EVENT_FILE = None


def test_estop_latch_refuses_mutations_but_not_reads(tmp_path, monkeypatch):
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    _create(cfg, tmp_path, confirm=True)
    power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                 issued_by="cli:user")
    try:
        alpha = _work(tmp_path, "other")[0]
        rc, out = _run(cfg, _Args("create", name="Late", folders=[alpha],
                                  confirm=True))
        assert rc == 3 and out["code"] == "PROJECT_ESTOP", out
        assert len(_run(cfg, _Args("list"))[1]["projects"]) == 1
    finally:
        power_control_store(cfg).set(emergency_stop=False, global_disable=False,
                                     issued_by="cli:user")


# ------------------------------------------------- catalog + parser
def test_capability_catalog_reports_project_implemented():
    from voicebridge.capability_catalog import check

    out = check("project")
    assert out["state"] == "IMPLEMENTED", out
    assert out["code"] == "OK"


def test_project_surface_in_parser_help():
    from voicebridge import cli as CLI

    assert "project" in CLI.build_parser().format_help()
    buf = io.StringIO()
    with redirect_stdout(buf):
        try:
            CLI.build_parser().parse_args(["project", "--help"])
        except SystemExit as exc:
            assert exc.code == 0
    assert "add-folder" in buf.getvalue()
