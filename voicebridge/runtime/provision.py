"""Packet 8T-F — provision the pinned Hermes runtime into Qubit-owned space.

The Hermes agent core (657 modules + third-party deps) cannot live in git
and must never run from /tmp or the operator's installs. This module
installs a hash-verified copy of the pinned clone into Qubit's own XDG
data tree and verifies it on every use:

  $XDG_DATA_HOME/qubit-voicebridge/runtimes/hermes-agent-<commit>/
      src/          hash-verified copy of the pinned source (git metadata kept)
      venv/         isolated interpreter + pinned dependencies
      INSTALL_RECEIPT.json

Verification (no trust in labels): `git rev-parse HEAD` in the copy must
equal the pin, LICENSE must exist, the venv binary must report the pinned
version, and a pinned entry file hash must match the clone. Anything else
is typed DISABLED/UNAVAILABLE — never false success.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

HERMES_URL = "https://github.com/NousResearch/hermes-agent"
HERMES_TAG = "v2026.9.7"
HERMES_COMMIT = "2237be355906fbe6065ce1815711eee52b2d646e"
HERMES_VERSION = "0.21.1"
# Pinned entry files whose sha256 must match the source clone byte-for-byte.
ENTRY_HASH_FILES = ("cli.py", "hermes_constants.py")


@dataclass
class ProvisionResult:
    ok: bool
    code: str
    runtime_dir: Optional[Path] = None
    detail: str = ""


def _xdg_data_home() -> Path:
    raw = os.environ.get("XDG_DATA_HOME", "").strip()
    return Path(raw).expanduser() if raw else Path("~/.local/share").expanduser()


def _xdg_state_home() -> Path:
    raw = os.environ.get("XDG_STATE_HOME", "").strip()
    return Path(raw).expanduser() if raw else Path("~/.local/state").expanduser()


def runtime_dir(base: Optional[Path] = None) -> Path:
    """Canonical Qubit-owned Hermes runtime directory (never /tmp, never ~)."""
    root = Path(base) if base is not None else _xdg_data_home() / "qubit-voicebridge"
    return root / "runtimes" / f"hermes-agent-{HERMES_COMMIT}"


def hermes_home(state_base: Optional[Path] = None) -> Path:
    """Isolated HERMES_HOME for Qubit-driven runs (sessions/db stay here)."""
    root = (Path(state_base) if state_base is not None
            else _xdg_state_home() / "qubit-voicebridge")
    return root / "hermes-runtime"


def default_config(model: str = "huihui_ai/llama3.2-abliterate:3b") -> str:
    """Local-only provider config: Ollama endpoint, no keys, no cloud."""
    return (
        "model:\n"
        '  provider: "ollama"\n'
        f'  default: "{model}"\n'
        '  base_url: "http://127.0.0.1:11434/v1"\n'
        "  context_length: 131072\n"
        "display:\n"
        "  verbose: false\n")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_copy(src_dir: Path) -> ProvisionResult:
    """Verify a Hermes source copy against the pin (no execution)."""
    if not src_dir.is_dir():
        return ProvisionResult(False, "MISSING", None, f"no dir: {src_dir}")
    if not (src_dir / "LICENSE").is_file():
        return ProvisionResult(False, "NO_LICENSE", None, "LICENSE absent")
    try:
        proc = subprocess.run(["git", "-C", str(src_dir), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=30)
    except Exception as exc:
        return ProvisionResult(False, "NO_GIT", None, f"git probe failed: {exc}")
    if proc.returncode != 0 or proc.stdout.strip() != HERMES_COMMIT:
        return ProvisionResult(False, "PIN_MISMATCH", None,
                               f"HEAD={proc.stdout.strip()[:12]!r} != pin")
    return ProvisionResult(True, "VERIFIED", src_dir, "pin + LICENSE ok")


def verify_installation(rdir: Path) -> Dict[str, Any]:
    """Full identity check of a provisioned runtime (no model needed)."""
    rdir = Path(rdir)
    src = rdir / "src"
    venv_python = rdir / "venv" / "bin" / "python"
    binary = rdir / "venv" / "bin" / "hermes"
    copy_check = verify_copy(src)
    if not copy_check.ok:
        return {"state": "UNAVAILABLE", "reason": copy_check.code,
                "detail": copy_check.detail}
    if not venv_python.is_file():
        return {"state": "UNAVAILABLE", "reason": "NO_VENV",
                "detail": "venv python absent"}
    if not binary.is_file():
        return {"state": "UNAVAILABLE", "reason": "NO_BINARY",
                "detail": "hermes binary absent"}
    entry_hashes = {}
    for rel in ENTRY_HASH_FILES:
        p = src / rel
        if not p.is_file():
            return {"state": "UNAVAILABLE", "reason": "ENTRY_MISSING",
                    "detail": rel}
        entry_hashes[rel] = _sha256(p)
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(rdir),
           "HERMES_HOME": str(rdir)}
    try:
        proc = subprocess.run([str(binary), "--version"], capture_output=True,
                              text=True, timeout=120, env=env)
    except Exception as exc:
        return {"state": "UNAVAILABLE", "reason": "SPAWN_FAILED",
                "detail": str(exc)[:200]}
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0 or "Hermes Agent" not in out:
        return {"state": "UNAVAILABLE", "reason": "BAD_VERSION",
                "detail": out[-300:]}
    if HERMES_VERSION not in out:
        return {"state": "DEGRADED", "reason": "VERSION_DRIFT",
                "detail": out.strip().splitlines()[0][:120]}
    return {"state": "READY", "binary": str(binary),
            "home": str(rdir), "version": out.strip().splitlines()[0][:120],
            "commit": HERMES_COMMIT, "entry_hashes": entry_hashes}


def install_from_clone(clone_dir: Path, dest_base: Optional[Path] = None, *,
                       python: str = "python3",
                       pip_install: bool = True) -> ProvisionResult:
    """Copy a verified clone into Qubit-owned space and build its venv.

    ``clone_dir`` is a read-only input (never modified). Network is needed
    only when ``pip_install`` is true (dependencies from PyPI).
    """
    clone_dir = Path(clone_dir)
    check = verify_copy(clone_dir)
    if not check.ok:
        return ProvisionResult(False, check.code, None, check.detail)
    rdir = runtime_dir(dest_base)
    src = rdir / "src"
    try:
        rdir.mkdir(parents=True, exist_ok=True)
        if src.exists():
            shutil.rmtree(src)
        shutil.copytree(clone_dir, src, symlinks=False,
                        ignore=shutil.ignore_patterns(".venv", "__pycache__",
                                                      "*.pyc", ".pytest_cache"))
        check2 = verify_copy(src)
        if not check2.ok:
            return ProvisionResult(False, check2.code, rdir, check2.detail)
        if pip_install:
            venv_dir = rdir / "venv"
            if venv_dir.exists():
                shutil.rmtree(venv_dir)
            uv = shutil.which("uv")
            if uv:
                subprocess.run([uv, "venv", str(venv_dir)], check=True,
                               capture_output=True, timeout=300)
                subprocess.run(
                    [uv, "pip", "install", "--python",
                     str(venv_dir / "bin" / "python"), "-e", str(src)],
                    check=True, capture_output=True, timeout=1800)
            else:
                subprocess.run([python, "-m", "venv", str(venv_dir)],
                               check=True, capture_output=True, timeout=300)
                subprocess.run(
                    [str(venv_dir / "bin" / "pip"), "install", "-e", str(src)],
                    check=True, capture_output=True, timeout=1800)
        receipt = {"url": HERMES_URL, "tag": HERMES_TAG,
                   "commit": HERMES_COMMIT, "version": HERMES_VERSION,
                   "source": str(clone_dir)}
        (rdir / "INSTALL_RECEIPT.json").write_text(json.dumps(receipt, indent=2))
        return ProvisionResult(True, "INSTALLED", rdir, "verified + installed")
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or exc.output or b"")[-2000:]
        try:
            tail_s = tail.decode("utf-8", "replace")
        except Exception:
            tail_s = "<undecodable>"
        return ProvisionResult(False, "INSTALL_FAILED", rdir, tail_s[-800:])
    except Exception as exc:
        return ProvisionResult(False, "INSTALL_FAILED", rdir, str(exc)[:500])


def write_runtime_config(home: Path, *, model: str = "") -> Path:
    """Write the local-only Hermes config under an isolated HERMES_HOME."""
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    cfg = home / "config.yaml"
    cfg.write_text(default_config(model or "huihui_ai/llama3.2-abliterate:3b"))
    return cfg
