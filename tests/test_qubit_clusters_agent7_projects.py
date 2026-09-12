"""Agent 7 — projects cluster, real-module proofs.

One focused test per Qubit gap-ledger row in the ``projects`` area, calling
``voicebridge.projects`` directly (state transitions, persistence, rollback
recovery, access control, redaction, audit) rather than only the CLI route.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from voicebridge import projects as PJ


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _work(tmp_path, *names):
    out = []
    for name in names:
        d = tmp_path / "work" / name
        d.mkdir(parents=True, exist_ok=True)
        out.append(str(d))
    return out


def _create(cfg, tmp_path, name="Site", **kw):
    alpha, beta = _work(tmp_path, "alpha", "beta")
    kw.setdefault("folders", [alpha])
    out = PJ.create(cfg, name, kw.pop("folders"), **kw)
    return out, alpha, beta


# ------------------------------------------------------------------ projects_create
def test_projects_create_validation_persistence_and_access(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    alpha = _work(tmp_path, "alpha")[0]
    with pytest.raises(PJ.ProjectError) as exc:
        PJ.create(cfg, "Site", [alpha], confirmed=False)
    assert exc.value.code == PJ.CONFIRM_CODE
    out = PJ.create(cfg, "Site", [alpha], confirmed=True)
    assert out["state"] == "OK" and out["project"]["slug"] == "site"
    # Persistence across reopen.
    assert [p["slug"] for p in PJ.list_projects(cfg)["projects"]] == ["site"]
    with pytest.raises(PJ.ProjectError) as exc:
        PJ.create(cfg, "Site", [alpha], confirmed=True)
    assert exc.value.code == PJ.EXISTS_CODE
    with pytest.raises(PJ.ProjectError):
        PJ.create(cfg, "Bad", ["/nonexistent-agent7"], confirmed=True)
    with pytest.raises(PJ.ProjectError):
        PJ.create(cfg, "  ", [alpha], confirmed=True)


# -------------------------------------------------------------------- projects_list
def test_projects_list_hides_archived_and_marks_active(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _create(cfg, tmp_path, name="One", confirmed=True, use=True)
    _create(cfg, tmp_path, name="Two", confirmed=True)
    listed = PJ.list_projects(cfg)
    assert listed["active_slug"] == "one"
    assert {p["slug"] for p in listed["projects"]} == {"one", "two"}
    PJ.archive(cfg, "one", confirmed=True)
    assert [p["slug"] for p in PJ.list_projects(cfg)["projects"]] == ["two"]
    assert PJ.list_projects(cfg, include_archived=True)["active_slug"] is None


# -------------------------------------------------------------------- projects_show
def test_projects_show_and_not_found(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _, alpha, _ = _create(cfg, tmp_path, confirmed=True)
    shown = PJ.show(cfg, "site")["project"]
    assert shown["name"] == "Site" and shown["folders"] == [alpha]
    with pytest.raises(PJ.ProjectError) as exc:
        PJ.show(cfg, "missing")
    assert exc.value.code == PJ.NOT_FOUND_CODE


# ------------------------------------------------------------------ projects_active
def test_projects_active_empty_dangling_and_set(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    assert PJ.active_project(cfg)["state"] == "EMPTY"
    _create(cfg, tmp_path, confirmed=True, use=True)
    assert PJ.active_project(cfg)["project"]["slug"] == "site"
    # A dangling pointer (restored state) must not fabricate a workspace.
    state_file = Path(cfg.state_dir()) / PJ.STORE_NAME
    raw = json.loads(state_file.read_text())
    raw["active_slug"] = "ghost"
    state_file.write_text(json.dumps(raw))
    empty = PJ.active_project(cfg)
    assert empty["state"] == "EMPTY" and empty["code"] == "NO_ACTIVE_PROJECT"


# --------------------------------------------------------------------- projects_use
def test_projects_use_set_and_clear(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _create(cfg, tmp_path, confirmed=True)
    assert PJ.use(cfg, "site", confirmed=True)["active_slug"] == "site"
    assert PJ.use(cfg, "", confirmed=True, clear=True)["active_slug"] is None
    with pytest.raises(PJ.ProjectError):
        PJ.use(cfg, "missing", confirmed=True)


# ----------------------------------------------------------------- projects_archive
def test_projects_archive_blocks_mutation_until_restore(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _create(cfg, tmp_path, confirmed=True, use=True)
    out = PJ.archive(cfg, "site", confirmed=True)
    assert out["project"]["archived"] is True
    assert PJ.active_project(cfg)["state"] == "EMPTY"
    with pytest.raises(PJ.ProjectError) as exc:
        PJ.add_folder(cfg, "site", _work(tmp_path, "beta")[0], confirmed=True)
    assert exc.value.code == PJ.ARCHIVED_CODE
    with pytest.raises(PJ.ProjectError):
        PJ.use(cfg, "site", confirmed=True)


# ------------------------------------------------------------------ project_restore
def test_project_restore_roundtrip(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _create(cfg, tmp_path, confirmed=True)
    PJ.archive(cfg, "site", confirmed=True)
    restored = PJ.restore(cfg, "site", confirmed=True)
    assert restored["project"]["archived"] is False
    with pytest.raises(PJ.ProjectError):
        PJ.restore(cfg, "site", confirmed=True)


# ------------------------------------------------------------------- project_rename
def test_project_rename_slug_stable_and_persisted(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _create(cfg, tmp_path, confirmed=True)
    out = PJ.rename(cfg, "site", "Portal", confirmed=True)
    assert out["project"]["name"] == "Portal"
    assert out["project"]["slug"] == "site"
    assert PJ.show(cfg, "site")["project"]["name"] == "Portal"


# --------------------------------------------------------------- project_add_folder
def test_project_add_folder_validation_and_primary(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _, alpha, beta = _create(cfg, tmp_path, confirmed=True)
    out = PJ.add_folder(cfg, "site", beta, confirmed=True)
    assert out["project"]["folders"] == [alpha, beta]
    with pytest.raises(PJ.ProjectError):
        PJ.add_folder(cfg, "site", beta, confirmed=True)
    with pytest.raises(PJ.ProjectError):
        PJ.add_folder(cfg, "site", "/nonexistent-agent7", confirmed=True)


# ------------------------------------------------------------ project_remove_folder
def test_project_remove_folder_primary_fallback_and_vanished(tmp_path,
                                                            monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _, alpha, beta = _create(cfg, tmp_path, confirmed=True)
    PJ.add_folder(cfg, "site", beta, confirmed=True)
    PJ.set_primary(cfg, "site", beta, confirmed=True)
    out = PJ.remove_folder(cfg, "site", beta, confirmed=True)
    assert out["project"]["primary"] == alpha
    # A folder that vanished on disk must still be removable from the registry
    # (recovery: the workspace is not permanently stuck).
    import shutil

    shutil.rmtree(alpha)
    out = PJ.remove_folder(cfg, "site", alpha, confirmed=True)
    assert out["project"]["folders"] == [] and out["project"]["primary"] is None
    with pytest.raises(PJ.ProjectError):
        PJ.remove_folder(cfg, "site", "/other/agent7", confirmed=True)


# ------------------------------------------------------------- project_set_primary
def test_project_set_primary_requires_membership(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _, alpha, beta = _create(cfg, tmp_path, confirmed=True)
    with pytest.raises(PJ.ProjectError):
        PJ.set_primary(cfg, "site", beta, confirmed=True)
    PJ.add_folder(cfg, "site", beta, confirmed=True)
    assert PJ.set_primary(cfg, "site", beta,
                          confirmed=True)["project"]["primary"] == beta


# ----------------------------------------------------------------- project_rollback
def test_project_rollback_recovers_audited_snapshot(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _create(cfg, tmp_path, name="One", confirmed=True)
    _create(cfg, tmp_path, name="Two", confirmed=True)
    out = PJ.rollback(cfg, confirmed=True)
    assert out["restored_projects"] == ["one"]
    assert [p["slug"] for p in PJ.list_projects(cfg)["projects"]] == ["one"]
    with pytest.raises(PJ.ProjectError):
        PJ.rollback(cfg, confirmed=True, backup="/nonexistent/agent7.json")


# ----------------------------------------------------------------- project_bind_board
def test_project_bind_board_real_kanban_roundtrip(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge import kanban as KB

    _create(cfg, tmp_path, confirmed=True)
    with pytest.raises(PJ.ProjectError) as exc:
        PJ.bind_board(cfg, "site", "team", confirmed=True)
    assert exc.value.code == PJ.NO_BOARD_CODE
    KB.boards_create(cfg, "team", "Team", confirmed=True)
    out = PJ.bind_board(cfg, "site", "team", confirmed=True)
    assert out["project"]["board"] == "team"
    board = next(b for b in KB.boards_list(cfg)["boards"]
                 if b["board"] == "team")
    assert board["project_slug"] == "site"
    unbound = PJ.bind_board(cfg, "site", "", confirmed=True, unbind=True)
    assert unbound["project"]["board"] is None


# --------------------------------------------------------- corrupt + redaction/audit
def test_projects_corrupt_store_fails_closed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _create(cfg, tmp_path, confirmed=True)
    store = Path(cfg.state_dir()) / PJ.STORE_NAME
    store.write_text("{corrupt")
    with pytest.raises(PJ.ProjectError) as exc:
        PJ.list_projects(cfg)
    assert exc.value.code == PJ.CORRUPT_CODE


def test_projects_redaction_and_audit(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    captured = []
    import voicebridge.logging_setup as LS

    monkeypatch.setattr(LS, "log_event",
                        lambda event, **fields: captured.append((event, fields)))
    alpha = _work(tmp_path, "alpha")[0]
    out = PJ.create(cfg, "Site", [alpha],
                    description="token=abc123secretvalue",
                    requested_by="cli:user", confirmed=True)
    blob = json.dumps(out)
    assert "abc123secretvalue" not in blob and "[REDACTED]" in blob
    audit = json.dumps(captured)
    assert "project.create" in audit
    assert "abc123secretvalue" not in audit
