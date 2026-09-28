"""Wave 21 — `qubit update` help surface + truthful JSON check branding.

Two public-surface checks:

1. ``python -m voicebridge.cli update --help`` exits 0, documents the three
   sanctioned verbs (``--check`` / ``--apply`` / ``--rollback``) and prints no
   legacy brand (``Hermes`` / ``OpenClaw`` / ``NousResearch`` / ``NousPortal`` /
   ``@NousResearch``).
2. ``qubit update --check --json`` in a fully isolated temp HOME (its own bare
   local remote + one-behind checkout) emits valid JSON with a truthful state —
   no network, no product-worktree mutation, no ``~/.hermes`` read.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

VENV_PY = REPO_ROOT / ".venv" / "bin" / "python"
PY = str(VENV_PY) if VENV_PY.is_file() else sys.executable

# Exact legacy tokens the owner policy forbids on product surfaces.
FORBIDDEN = ("Hermes", "OpenClaw", "NousResearch", "NousPortal",
             "@NousResearch")
LEGACY_RE = re.compile(
    r"hermes|openclaw|nousresearch|nousportal|nous\s+research", re.I)


# ---------------------------------------------------------------------------
# disposable git world
# ---------------------------------------------------------------------------
def _git_env(tmp_path: Path) -> dict:
    empty = tmp_path / "gitconfig-empty"
    empty.write_text("")
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "Qubit Wave21",
        "GIT_AUTHOR_EMAIL": "wave21@example.invalid",
        "GIT_COMMITTER_NAME": "Qubit Wave21",
        "GIT_COMMITTER_EMAIL": "wave21@example.invalid",
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
    return {
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
    }


def _write_temp_config(home: Path, checkout: Path) -> None:
    cfg_dir = home / ".config" / "qubit-voicebridge"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "voice.yaml").write_text(
        "update:\n"
        f"  repo_root: {checkout}\n"
        "  remote: origin\n"
        "  branch: main\n")


# ---------------------------------------------------------------------------
# 1. help surface
# ---------------------------------------------------------------------------
def test_update_help_lists_verbs_and_has_no_legacy_brand():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [PY, "-m", "voicebridge.cli", "update", "--help"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    out = proc.stdout
    for verb in ("--check", "--apply", "--rollback"):
        assert verb in out, f"{verb} missing from `qubit update --help`:\n{out}"
    assert "qubit update" in out, out

    # Explicit legacy tokens, and the broad case-insensitive scan.
    for token in FORBIDDEN:
        assert token not in out, f"legacy token {token!r} in help output"
    m = LEGACY_RE.search(out)
    assert m is None, f"legacy brand {m.group(0)!r} leaked in help output"


# ---------------------------------------------------------------------------
# 2. truthful JSON check on an isolated temp install
# ---------------------------------------------------------------------------
def test_cli_update_check_json_is_valid_truthful_and_brand_clean(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    first, second = _build_disposable_remote(tmp_path)
    _write_temp_config(home, tmp_path / "install")

    proc = subprocess.run(
        [PY, "-m", "voicebridge.cli", "update", "--check", "--json"],
        cwd=str(home), env=_isolated_env(home), capture_output=True, text=True,
        timeout=120)
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)

    body = json.loads(proc.stdout)
    assert isinstance(body, dict)
    assert body["state"] == "OK", body
    assert body["install_method"] == "git-checkout", body
    assert body["current_revision"] == first, body
    assert body["remote_revision"] == second, body
    assert body["update_available"] is True, body
    assert body["can_apply"] is True, body

    # The isolated home stayed isolated, and no brand leaked to the output.
    assert not (home / ".hermes").exists()
    combined = proc.stdout + proc.stderr
    m = LEGACY_RE.search(combined)
    assert m is None, f"legacy brand {m.group(0)!r} in update --check output"
