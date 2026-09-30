"""Wave 20 — fresh-install update check under a fully isolated HOME/QUBIT_HOME.

A clean machine has no ``~/.hermes``, no reference service, and no ambient
Qubit state. This file proves the update ``--check`` path works in that world
and never reads the operator's real home:

* a disposable local bare remote + checkout is built under ``tmp_path`` (the
  update source is addressed entirely through the temp config),
* ``HOME`` / ``QUBIT_HOME`` / every ``XDG_*`` dir point into ``tmp_path``,
* the real repo is on ``PYTHONPATH`` read-only (the code under test), nothing
  else,
* the JSON check is a truthful typed state, and
* an ``strace`` of the child proves no path under the real ``~/.hermes`` is
  opened.

No network, no product checkout mutation, no service interaction.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Captured before any test patches HOME: the read-only reference home.
REAL_HOME = Path(os.path.expanduser("~")).resolve()
VENV_PY = REPO_ROOT / ".venv" / "bin" / "python"

FAKE_OPENAI = "sk-FAKEOPENAI0123456789abcdef"
FAKE_DEEPSEEK = "sk-FAKEDEEPSEEK0123456789abcdef"


# ---------------------------------------------------------------------------
# disposable git world (self-contained; no imports from other test files)
# ---------------------------------------------------------------------------
def _git_env(tmp_path: Path) -> dict:
    empty = tmp_path / "gitconfig-empty"
    empty.write_text("")
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "Qubit Wave20",
        "GIT_AUTHOR_EMAIL": "wave20@example.invalid",
        "GIT_COMMITTER_NAME": "Qubit Wave20",
        "GIT_COMMITTER_EMAIL": "wave20@example.invalid",
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
        f"git {' '.join(args)} failed ({proc.returncode}): "
        f"{proc.stdout}{proc.stderr}")
    return proc.stdout.strip()


def _build_disposable_remote(tmp_path: Path):
    env = _git_env(tmp_path)
    bare = tmp_path / "remote.git"
    source = tmp_path / "source"
    checkout = tmp_path / "install"

    _git(["init", "--bare", "-b", "main", str(bare)], tmp_path, env)
    _git(["init", "-b", "main", str(source)], tmp_path, env)
    (source / "VERSION").write_text("1\n")
    _git(["add", "-A"], source, env)
    _git(["commit", "-m", "first"], source, env)
    _git(["remote", "add", "origin", str(bare)], source, env)
    _git(["push", "-u", "origin", "main"], source, env)

    _git(["clone", str(bare), str(checkout)], tmp_path, env)
    first = _git(["rev-parse", "HEAD"], checkout, env)

    (source / "VERSION").write_text("2\n")
    _git(["add", "-A"], source, env)
    _git(["commit", "-m", "second"], source, env)
    _git(["push", "origin", "main"], source, env)
    second = _git(["rev-parse", "HEAD"], source, env)

    assert first != second
    return first, second


def _isolated_env(home: Path) -> dict:
    """A clean child environment rooted entirely in *home*."""
    env = {
        "HOME": str(home),
        "QUBIT_HOME": str(home / ".qhome"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_STATE_HOME": str(home / ".state"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "PATH": os.environ.get("PATH", ""),
        "LANG": "C.UTF-8",
        "TZ": "UTC",
        "PYTHONPATH": str(REPO_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        # Fake credentials: they must never appear in the child's output.
        "OPENAI_API_KEY": FAKE_OPENAI,
        "DEEPSEEK_API_KEY": FAKE_DEEPSEEK,
    }
    return env


def _write_temp_config(home: Path, checkout: Path) -> None:
    cfg_dir = home / ".config" / "qubit-voicebridge"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "voice.yaml").write_text(
        "update:\n"
        f"  repo_root: {checkout}\n"
        "  remote: origin\n"
        "  branch: main\n")


def _run_check(home: Path, checkout: Path, *, strace: Path | None = None):
    """Run the real CLI check in a child; optionally under a file trace."""
    cmd = [str(VENV_PY), "-m", "voicebridge", "update", "--check", "--json"]
    if strace is not None:
        cmd = ["strace", "-f", "-e",
               "trace=openat,open,stat,newfstatat,access",
               "-o", str(strace), *cmd]
    return subprocess.run(cmd, cwd=str(home), env=_isolated_env(home),
                          capture_output=True, text=True, timeout=120)


@pytest.fixture
def fresh(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    first, second = _build_disposable_remote(tmp_path)
    _write_temp_config(home, tmp_path / "install")
    return home, first, second


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------
def test_fresh_install_check_cli_is_isolated_and_typed(fresh, tmp_path):
    home, first, second = fresh
    proc = _run_check(home, tmp_path / "install")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    body = json.loads(proc.stdout)
    assert body["state"] == "OK", body
    assert body["install_method"] == "git-checkout"
    assert body["current_revision"] == first
    assert body["remote_revision"] == second
    assert body["update_available"] is True
    assert body["can_apply"] is True

    # Nothing leaked into the isolated home.
    assert not (home / ".hermes").exists()

    # Fake credentials were present in the environment but never printed.
    combined = proc.stdout + proc.stderr
    assert FAKE_OPENAI not in combined
    assert FAKE_DEEPSEEK not in combined
    assert "sk-FAKE" not in combined


def test_fresh_install_check_never_opens_real_hermes_home(fresh, tmp_path):
    if shutil.which("strace") is None:
        pytest.skip("strace unavailable; cannot prove no-open of the real home")

    home, first, second = fresh
    trace = tmp_path / "strace.txt"
    proc = _run_check(home, tmp_path / "install", strace=trace)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    text = trace.read_text(errors="replace")
    real_hermes = str(REAL_HOME / ".hermes")
    hits = [ln for ln in text.splitlines() if real_hermes in ln]
    assert hits == [], f"child opened the real reference home:\n" + "\n".join(hits[:5])
    # And the disposable source was actually used instead.
    assert str(tmp_path / "install") in text


def test_fresh_install_check_direct_is_typed_and_secret_free(fresh, monkeypatch):
    """The in-process path (UM.check directly) is equally isolated + typed."""
    from voicebridge import update_manager as UM

    home, first, second = fresh
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QUBIT_HOME", str(home / ".qhome"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_OPENAI)
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_DEEPSEEK)

    env = _git_env(home)
    checkout = home.parent / "install"

    class _Runner:
        def __init__(self):
            self.calls = []

        def __call__(self, argv, timeout):
            argv = list(argv)
            self.calls.append(argv)
            p = subprocess.run(argv, capture_output=True, text=True,
                               timeout=timeout, env=env)
            return p.returncode, p.stdout, p.stderr

    runner = _Runner()
    cfg = _cfg((home.parent / "install"))
    out = UM.check(cfg, git=runner)
    assert out["state"] == "OK", out
    assert out["current_revision"] == first
    assert out["remote_revision"] == second

    blob = json.dumps(out)
    assert FAKE_OPENAI not in blob and FAKE_DEEPSEEK not in blob
    assert not (home / ".hermes").exists()


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


def _cfg(checkout: Path):
    return _Cfg({"update": {"repo_root": str(checkout),
                            "remote": "origin", "branch": "main"}})
