"""Wave 25 — pinned-source resolution must survive a fresh checkout.

Forensic finding (Medium): ``scripts/hermes_vendor_manifest.py`` and
``scripts/hermes_byte_manifest.py`` defaulted their pinned-source input to
ephemeral ``/tmp/opencode/pinned/...`` paths that do not exist on a fresh
checkout, so ``--check`` could not run without an operator-supplied path.

T1 fixed ``scripts/source_inventory.py`` to auto-resolve a candidate whose
``git rev-parse HEAD`` equals the pin. These scripts must mirror that:

1. env overrides ``QUBIT_PINNED_HERMES`` / ``QUBIT_REFERENCE_HOME`` (and the
   OpenClaw equivalents) are tried first;
2. then the pin-named pristine clones, the live reference install, and the
   unsuffixed ``/tmp/opencode/pinned/...`` path;
3. the first candidate whose HEAD equals the pin wins; if none match, the first
   existing candidate is used with a loud warning (the caller's revision check
   then fails closed);
4. explicit ``--upstream`` / ``--openclaw`` still override.

Hermetic: temporary git repos are created under ``tmp_path``; no network and no
reads of the real pinned trees are required.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"

VENDOR_SCRIPT = SCRIPTS / "hermes_vendor_manifest.py"
BYTE_SCRIPT = SCRIPTS / "hermes_byte_manifest.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def hvm():
    return _load("wave25_hermes_vendor_manifest", VENDOR_SCRIPT)


@pytest.fixture(scope="module")
def hbm():
    return _load("wave25_hermes_byte_manifest", BYTE_SCRIPT)


def _git_env() -> dict:
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    })
    return env


def _make_repo(path: Path) -> str:
    """Create a real single-commit git repo; return its HEAD sha.

    The seed file content is derived from the directory name so two repos made
    in the same second do not collide on the empty-commit hash.
    """
    path.mkdir(parents=True, exist_ok=True)
    (path / "seed.txt").write_text(path.name, encoding="utf-8")
    env = _git_env()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, env=env)
    subprocess.run(["git", "add", "seed.txt"], cwd=path, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "x"],
                   cwd=path, check=True, env=env)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, check=True,
                          capture_output=True, text=True).stdout.strip()


# --------------------------------------------------------------------------- #
# 1 — defaults are not a single ephemeral path
# --------------------------------------------------------------------------- #
def test_vendor_hermes_candidates_include_durable_fallbacks(hvm):
    candidates = tuple(hvm.UPSTREAM_CANDIDATES)
    assert len(candidates) >= 4, candidates
    joined = [str(p) for p in candidates]
    assert any(p.startswith("/tmp/opencode/pinned/hermes-agent-") for p in joined)
    assert any(p.startswith("/tmp/qubit-pin-") for p in joined)
    # the live reference install is the durable source of record
    assert any(p.endswith("/.hermes/hermes-agent") for p in joined), joined
    assert "/tmp/opencode/pinned/hermes-agent" in joined


def test_byte_hermes_candidates_include_durable_fallbacks(hbm):
    candidates = tuple(hbm.DEFAULT_UPSTREAM_CANDIDATES)
    assert len(candidates) >= 4, candidates
    joined = [str(p) for p in candidates]
    assert any(p.startswith("/tmp/opencode/pinned/hermes-agent-") for p in joined)
    assert any(p.startswith("/tmp/qubit-pin-") for p in joined)
    assert any(p.endswith("/.hermes/hermes-agent") for p in joined), joined
    assert "/tmp/opencode/pinned/hermes-agent" in joined


def test_openclaw_resolution_is_not_ephemeral_only(hvm):
    candidates = tuple(hvm.OPENCLAW_CANDIDATES)
    assert len(candidates) >= 2, candidates
    joined = [str(p) for p in candidates]
    assert "/tmp/opencode/pinned/openclaw" in joined, joined
    assert any(p.startswith("/tmp/opencode/pinned/openclaw-") for p in joined)
    # the CLI default must not be a hardcoded single ephemeral literal
    source = VENDOR_SCRIPT.read_text(encoding="utf-8")
    assert 'default=f"/tmp/opencode/pinned/openclaw' not in source
    assert "def _resolve_openclaw" in source


# --------------------------------------------------------------------------- #
# 2 — resolution picks the pin-matching candidate
# --------------------------------------------------------------------------- #
def test_vendor_upstream_resolution_picks_pin_match(hvm, tmp_path, monkeypatch):
    other = tmp_path / "other"
    matching = tmp_path / "matching"
    _make_repo(other)
    matching_sha = _make_repo(matching)
    monkeypatch.setattr(hvm, "UPSTREAM_CANDIDATES", (other, matching))
    monkeypatch.setattr(hvm, "PIN", matching_sha)
    for var in hvm.ENV_UPSTREAM_CANDIDATE_VARS:
        monkeypatch.delenv(var, raising=False)
    assert hvm._resolve_upstream() == matching


def test_vendor_openclaw_resolution_picks_pin_match(hvm, tmp_path, monkeypatch):
    other = tmp_path / "other"
    matching = tmp_path / "matching"
    _make_repo(other)
    matching_sha = _make_repo(matching)
    monkeypatch.setattr(hvm, "OPENCLAW_CANDIDATES", (other, matching))
    monkeypatch.setattr(hvm, "OPENCLAW_PIN", matching_sha)
    for var in hvm.ENV_OPENCLAW_CANDIDATE_VARS:
        monkeypatch.delenv(var, raising=False)
    assert hvm._resolve_openclaw() == matching


def test_byte_upstream_resolution_picks_pin_match(hbm, tmp_path, monkeypatch):
    other = tmp_path / "other"
    matching = tmp_path / "matching"
    _make_repo(other)
    matching_sha = _make_repo(matching)
    monkeypatch.setattr(hbm, "_candidate_paths", lambda: (other, matching))
    assert hbm.discover_upstream(None, matching_sha) == matching


def test_env_override_is_tried_first(hvm, hbm, tmp_path, monkeypatch):
    pinned = tmp_path / "pinned"
    _make_repo(pinned)
    monkeypatch.setenv("QUBIT_PINNED_HERMES", str(pinned))
    monkeypatch.setenv("QUBIT_REFERENCE_HOME", str(tmp_path / "unused"))
    assert hbm._candidate_paths()[0] == pinned
    assert hvm._env_candidate_paths(*hvm.ENV_UPSTREAM_CANDIDATE_VARS)[0] == pinned


# --------------------------------------------------------------------------- #
# 3 — fallback is the first existing candidate + a loud warning
# --------------------------------------------------------------------------- #
def test_resolution_falls_back_loudly_when_no_pin_match(hvm, tmp_path,
                                                        monkeypatch, capsys):
    other = tmp_path / "other"
    matching = tmp_path / "matching"
    _make_repo(other)
    _make_repo(matching)
    for var in hvm.ENV_UPSTREAM_CANDIDATE_VARS:
        monkeypatch.delenv(var, raising=False)
    resolved = hvm._resolve_pinned(
        None, "0" * 40, (other, matching), "upstream (Hermes)", other)
    assert resolved == other
    err = capsys.readouterr().err
    assert "WARNING" in err and "no upstream (Hermes) clone at pin" in err


def test_byte_falls_back_loudly_when_no_pin_match(hbm, tmp_path, monkeypatch,
                                                  capsys):
    other = tmp_path / "other"
    matching = tmp_path / "matching"
    _make_repo(other)
    _make_repo(matching)
    monkeypatch.setattr(hbm, "_candidate_paths", lambda: (other, matching))
    resolved = hbm.discover_upstream(None, "0" * 40)
    assert resolved == other
    assert "WARNING" in capsys.readouterr().err


def test_byte_blocked_when_nothing_exists(hbm, tmp_path, monkeypatch):
    missing_a = tmp_path / "missing-a"
    missing_b = tmp_path / "missing-b"
    monkeypatch.setattr(hbm, "_candidate_paths", lambda: (missing_a, missing_b))
    with pytest.raises(hbm.Blocked):
        hbm.discover_upstream(None, hbm.PIN)


# --------------------------------------------------------------------------- #
# 4 — explicit overrides still win
# --------------------------------------------------------------------------- #
def test_explicit_overrides_win(hvm, hbm, tmp_path):
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    assert hvm._resolve_upstream(str(explicit)) == explicit
    assert hvm._resolve_openclaw(str(explicit)) == explicit
    assert hbm.discover_upstream(str(explicit), hbm.PIN) == explicit
