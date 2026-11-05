"""Fresh `git clone` install -> `qubit update` discovery (wave 6).

A fresh source install runs ``scripts/install.sh``, which installs the package
non-editable into the checkout's ``.venv``. The canonical ``qubit update`` used
to look only at ``__file__``, find ``site-packages`` there, and classify the
install as an unupdatable package. These tests pin the three ways the checkout
is recovered (PEP 610 ``direct_url.json``, the venv beside/above the package,
and the PM generation record), the public-repository identity used by the
README clone, and an end-to-end check against a disposable remote.

Hermetic: temp bare remotes/checkouts only, injected real-git runner, no
network, no host checkout, no services.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge import update_channel as UC  # noqa: E402
from voicebridge import update_manager as UM  # noqa: E402


class _Cfg:
    def __init__(self, data=None):
        self._d = data or {}

    def get(self, *path, default=None):
        node = self._d
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


def _checkout(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".git").mkdir(exist_ok=True)
    (root / "pyproject.toml").write_text(
        "[project]\nname = \"qubit-voicebridge\"\nversion = \"0.0.5\"\n")
    (root / "voicebridge").mkdir(exist_ok=True)
    (root / "voicebridge" / "update_manager.py").write_text("# checkout marker\n")
    return root


def _installed_module(root: Path) -> Path:
    module = root / ".venv" / "lib" / "python3.12" / "site-packages" / "voicebridge" / "update_manager.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text("# installed copy\n")
    return module


def test_repo_root_recovers_checkout_from_the_venv_beside_site_packages(tmp_path, monkeypatch):
    checkout = _checkout(tmp_path / "checkout")
    module = _installed_module(checkout)
    monkeypatch.setattr(UM, "__file__", str(module))
    monkeypatch.setattr(UM, "_distribution_source_root", lambda: None)
    assert UM.repo_root(_Cfg()) == checkout.resolve()


def test_repo_root_uses_pep610_source_directory(tmp_path, monkeypatch):
    checkout = _checkout(tmp_path / "checkout")
    unrelated = tmp_path / "elsewhere" / "site-packages" / "voicebridge"
    unrelated.mkdir(parents=True)
    module = unrelated / "update_manager.py"
    module.write_text("# installed copy\n")
    monkeypatch.setattr(UM, "__file__", str(module))
    monkeypatch.setattr(UM, "_distribution_source_root", lambda: checkout.resolve())
    assert UM.repo_root(_Cfg()) == checkout.resolve()


def test_repo_root_ignores_a_release_archive_source_that_is_not_a_checkout(tmp_path, monkeypatch):
    installed = tmp_path / "site-packages" / "voicebridge"
    installed.mkdir(parents=True)
    module = installed / "update_manager.py"
    module.write_text("# wheel copy\n")
    released = tmp_path / "extracted-release"
    released.mkdir()
    monkeypatch.setattr(UM, "__file__", str(module))
    monkeypatch.setattr(UM, "_distribution_source_root", lambda: None)
    monkeypatch.setattr(UM, "_owning_checkout", lambda _root: None)
    assert UM.repo_root(_Cfg()) == installed.parent


@pytest.mark.parametrize("url", [
    "https://github.com/cgraph-dev/Qubit.git",
    "https://github.com/cgraph-dev/Qubit",
    "git@github.com:cgraph-dev/Qubit.git",
    "https://github.com/cgraph-dev/Qubit-AIO.git",
])
def test_both_qubit_repositories_are_recognised(url):
    assert UC._points_at_qubit_repo(url) is True


def test_public_clone_keeps_its_own_origin_as_the_update_source():
    cfg = _Cfg()
    policy = UC.resolve_source(
        cfg, remote_url_lookup=lambda _name: "https://github.com/cgraph-dev/Qubit.git")
    assert policy["ok"] is True
    assert policy["source_kind"] == "origin_qubit"
    assert policy["remote"] == UC.DEFAULT_REMOTE_NAME


def test_package_install_is_detected_by_its_own_distribution_name(monkeypatch):
    import importlib.metadata as md

    seen = []

    def version(name):
        seen.append(name)
        if name == "qubit-voicebridge":
            return "0.0.5"
        raise md.PackageNotFoundError(name)

    monkeypatch.setattr(md, "version", version)
    assert UM._package_installed() is True
    assert seen[0] == "qubit-voicebridge"


# ---------------------------------------------------------------------------
# end-to-end check from the installed (non-editable) layout
# ---------------------------------------------------------------------------
def _git_env(tmp_path: Path) -> dict:
    empty = tmp_path / "gitconfig-empty"
    empty.write_text("")
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "Qubit Update Test",
        "GIT_AUTHOR_EMAIL": "update@example.invalid",
        "GIT_COMMITTER_NAME": "Qubit Update Test",
        "GIT_COMMITTER_EMAIL": "update@example.invalid",
        "GIT_CONFIG_GLOBAL": str(empty),
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    })
    return env


def _git(args, cwd: Path, env: dict) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, env=env)
    assert proc.returncode == 0, (
        f"git {' '.join(args)} failed: {proc.stdout}{proc.stderr}")
    return proc.stdout.strip()


def _commit(repo: Path, env: dict, text: str, message: str) -> str:
    (repo / "VERSION").write_text(text)
    _git(["add", "-A"], repo, env)
    _git(["commit", "-m", message], repo, env)
    return _git(["rev-parse", "HEAD"], repo, env)


class _Runner:
    def __init__(self, env):
        self.env = env
        self.calls = []

    def __call__(self, argv, timeout):
        argv = list(argv)
        self.calls.append(argv)
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, env=self.env)
        return proc.returncode, proc.stdout, proc.stderr


def test_installed_layout_check_reports_git_checkout_and_available_update(tmp_path, monkeypatch):
    env = _git_env(tmp_path)
    bare = tmp_path / "cgraph-dev" / "Qubit"
    source = tmp_path / "source"
    checkout = tmp_path / "install"

    bare.parent.mkdir(parents=True)
    _git(["init", "--bare", "-b", "production", str(bare)], tmp_path, env)
    _git(["init", "-b", "production", str(source)], tmp_path, env)
    (source / "VERSION").write_text("1\n")
    (source / "pyproject.toml").write_text(
        "[project]\nname = \"qubit-voicebridge\"\nversion = \"0.0.5\"\n")
    (source / ".gitignore").write_text(".venv/\n")
    _git(["add", "-A"], source, env)
    _git(["commit", "-m", "first"], source, env)
    _git(["remote", "add", "origin", str(bare)], source, env)
    _git(["push", "-u", "origin", "production"], source, env)
    _git(["clone", str(bare), str(checkout)], tmp_path, env)
    first = _git(["rev-parse", "HEAD"], checkout, env)

    second = _commit(source, env, "2\n", "second")
    _git(["push", "origin", "production"], source, env)

    module = _installed_module(checkout)
    monkeypatch.setattr(UM, "__file__", str(module))
    monkeypatch.setattr(UM, "_distribution_source_root", lambda: None)
    monkeypatch.setattr(UM, "_estop", lambda _cfg: False)

    assert UM.repo_root(_Cfg()) == checkout.resolve()
    out = UM.check(_Cfg(), git=_Runner(env))
    assert out["state"] == "OK", out
    assert out["install_method"] == "git-checkout"
    assert out["current_revision"] == first
    assert out["remote_revision"] == second
    assert out["update_available"] is True
    assert out["can_apply"] is True


def test_dependency_reinstall_targets_the_running_interpreter(tmp_path, monkeypatch):
    """A bare `uv pip install` follows $VIRTUAL_ENV; the update must target us."""
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\nversion = '0'\n")
    monkeypatch.setattr(
        UM.shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(UM.sys, "executable", "/opt/qubit/.venv/bin/python")
    calls = []

    def runner(argv, timeout):
        calls.append(list(argv))
        return 0, "", ""

    out = UM.sync_dependencies(_Cfg(), runner=runner, repo_root=root)
    assert out["ok"] is True
    assert calls == [["uv", "pip", "install", "--python",
                      "/opt/qubit/.venv/bin/python", "-e", str(root),
                      "--no-deps"]], calls


def test_persisted_channel_is_used_by_the_next_bare_check(monkeypatch):
    """`--apply --channel stable` must not become write-only."""
    from types import SimpleNamespace

    from voicebridge import cli as CLI

    seen = {}

    def fake_check(cfg, **kwargs):
        seen.update(kwargs)
        return {"state": "OK", "install_method": "git-checkout"}

    monkeypatch.setattr(CLI.UM if hasattr(CLI, "UM") else UM, "check", fake_check)
    monkeypatch.setattr(UM, "check", fake_check)
    monkeypatch.setattr(CLI, "_update_runtime_state", lambda _cfg: None)
    monkeypatch.setattr(
        UC, "channel_record", lambda _cfg: {"channel": "stable"})
    rc = CLI._cmd_update(object(), SimpleNamespace(
        check=True, apply=False, rollback=False, json=True, source="",
        channel="", tag="", backup_dir=None, dry_run=False))
    assert rc == 0
    assert seen.get("channel") == "stable", seen


def test_channel_resolution_reads_the_checkout_origin(tmp_path, monkeypatch):
    from voicebridge import cli as CLI

    seen = {}

    def fake_resolve(cfg, source=None, *, repo=None, remote_url_lookup=None):
        seen["repo"] = repo
        return {"ok": True, "remote": "origin"}

    monkeypatch.setattr(UM, "repo_root", lambda _cfg: tmp_path)
    monkeypatch.setattr(UC, "resolve_source", fake_resolve)
    assert CLI._update_resolve_channel(None, None, "stable", None) == "origin"
    assert seen["repo"] == tmp_path
