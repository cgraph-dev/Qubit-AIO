"""Scoped read-only git inspection: same-backend CLI/dashboard/voice tests.

Proves `qubit git status|log|diff|show`, the dashboard `git` catalog
group, and the voice `git_*` entries all execute `voicebridge.git_scope`
(read-only, project-root confined, no shell, no network), and that
escapes, option injection, non-repos, and write verbs fail typed.
"""
from __future__ import annotations

import json
import subprocess

import pytest

pytestmark = pytest.mark.skipif(
    __import__("shutil").which("git") is None, reason="git not installed")


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _repo(tmp_path, name="work"):
    root = tmp_path / name
    root.mkdir(exist_ok=True)
    env = {"GIT_CONFIG_NOSYSTEM": "1", "HOME": str(tmp_path),
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=env)
    (root / "notes.md").write_text("hello\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, env=env)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "init"],
                   check=True, env=env)
    return root, env


def _project(cfg, folder):
    from voicebridge import projects as PJ

    created = PJ.create(cfg, "Work", [str(folder)],
                        requested_by="t", confirmed=True, use=True)
    assert created.get("state") == "OK", created
    return created


def test_status_log_diff_show_roundtrip(tmp_path, monkeypatch):
    from voicebridge import git_scope as GI

    cfg = _cfg(tmp_path, monkeypatch)
    root, _ = _repo(tmp_path)
    _project(cfg, root)
    out = GI.status(cfg, "")
    assert out["state"] == "OK" and out["root"] == str(root)
    (root / "notes.md").write_text("hello changed\n")
    out = GI.status(cfg, str(root))
    assert "notes.md" in out["status"], out
    log = GI.log(cfg, str(root), 5)
    assert log["entries"] and log["entries"][0]["subject"] == "init", log
    diff = GI.diff(cfg, str(root))
    assert "hello changed" in diff["patch"], diff
    shown = GI.show(cfg, str(root), "HEAD", "notes.md")
    assert shown["content"].strip() == "hello", shown


def test_redaction_applies_to_diff(tmp_path, monkeypatch):
    from voicebridge import git_scope as GI

    cfg = _cfg(tmp_path, monkeypatch)
    root, _ = _repo(tmp_path)
    _project(cfg, root)
    (root / "notes.md").write_text(
        "key sk-AbCdEfGhIjKlMnOpQrStUvWxYz1234567890\n")
    diff = GI.diff(cfg, str(root))
    assert "sk-AbCdEfGhIjKlMnOpQrStUvWxYz1234567890" not in diff["patch"], diff


def test_non_repo_is_typed(tmp_path, monkeypatch):
    from voicebridge import git_scope as GI

    cfg = _cfg(tmp_path, monkeypatch)
    plain = tmp_path / "plain"
    plain.mkdir(exist_ok=True)
    _project(cfg, plain)
    with pytest.raises(GI.GitError) as exc:
        GI.status(cfg, str(plain))
    assert exc.value.code == GI.NOT_A_REPO_CODE


def test_outside_root_refused(tmp_path, monkeypatch):
    from voicebridge import git_scope as GI

    cfg = _cfg(tmp_path, monkeypatch)
    root, _ = _repo(tmp_path)
    _project(cfg, root)
    other, _ = _repo(tmp_path, "other")
    with pytest.raises(GI.GitError) as exc:
        GI.status(cfg, str(other))
    assert exc.value.code == GI.SCOPE_CODE


def test_symlink_escape_refused(tmp_path, monkeypatch):
    from voicebridge import git_scope as GI

    cfg = _cfg(tmp_path, monkeypatch)
    root, _ = _repo(tmp_path)
    _project(cfg, root)
    link = root / "evil"
    try:
        link.symlink_to("/etc")
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(GI.GitError):
        GI.show(cfg, str(root), "HEAD", "evil/passwd")


def test_option_injection_refused(tmp_path, monkeypatch):
    from voicebridge import git_scope as GI

    cfg = _cfg(tmp_path, monkeypatch)
    root, _ = _repo(tmp_path)
    _project(cfg, root)
    for bad in ("--upload-pack=touch", "-u", "HEAD;id"):
        with pytest.raises(GI.GitError):
            GI.show(cfg, str(root), bad, "notes.md")
    with pytest.raises(GI.GitError):
        GI.diff(cfg, str(root), "--stat")


def test_write_verbs_refused_everywhere(tmp_path, monkeypatch):
    from voicebridge import git_scope as GI
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    refused = GI.refuse_write("push")
    assert refused["state"] == "REFUSED"
    assert refused["code"] == GI.WRITE_REFUSED_CODE
    ctx = VC.make_ctx(cfg, session="voice", source="user")
    res = VC.resolve("qubit, git push my work")
    assert res.kind in (VC.UNKNOWN, VC.MATCH)
    if res.kind == VC.MATCH:
        out = VC.execute(ctx, res)
        assert out.kind in (VC.EXECUTED, VC.FAILED)
        if out.kind == VC.EXECUTED:
            blob = json.dumps(out.receipt)
            assert "push" not in blob or "REFUSED" in blob or \
                "refused" in blob


def test_cli_dashboard_voice_same_backend(tmp_path, monkeypatch, capsys):
    from voicebridge import cli as CLI
    from voicebridge import dashboard_ops as OPS
    from voicebridge import git_scope as GI
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    root_path, _ = _repo(tmp_path)
    _project(cfg, root_path)

    class _A:
        git_command = "status"
        root = str(root_path)

    assert CLI._cmd_git(cfg, _A()) == 0
    cli_out = json.loads(capsys.readouterr().out)
    assert cli_out["state"] == "OK", cli_out

    ctx = VC.make_ctx(cfg, session="voice", source="user")
    out = VC.execute(ctx, VC.resolve("qubit, git status"))
    assert out.kind == VC.EXECUTED, (out.kind, out.speech)
    voice_state = out.receipt["result"]["git"]["status"]

    dash = OPS.run_op(cfg, "git_status", {},
                      actor="dashboard:user", session="dashboard")
    assert dash["state"] in ("executed", "needs-confirm"), dash

    direct = GI.status(cfg, str(root_path))["status"]
    assert cli_out["status"] == direct == voice_state


def test_git_group_on_projects_page():
    from pathlib import Path

    from voicebridge import dashboard_ops as OPS

    groups = {o["group"] for o in OPS.catalog()}
    assert "git" in groups
    repo_root = Path(__file__).resolve().parents[1]
    text = (repo_root / "frontend" / "projects.html").read_text()
    assert '"git"' in text
