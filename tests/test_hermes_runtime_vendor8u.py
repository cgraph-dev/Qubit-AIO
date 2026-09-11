"""Qubit-owned Hermes runtime fork — provenance and behavior proofs.

The product executes a Qubit-owned fork of the pinned Hermes source from this
tree (docs/QUBIT_OWNER_AMENDMENT_2026-09-11_HERMES_RUNTIME.md). These tests
prove the fork is present, byte-identical to the pin (per the per-file
manifest), importable, and that the real Hermes CLI runs in-process from the
Qubit launcher — not from an external installation.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FORK = REPO / "qubit_hermes"
MANIFEST = REPO / "docs" / "QUBIT_HERMES_VENDOR_MANIFEST.json"
PIN = "2237be355906fbe6065ce1815711eee52b2d646e"


def _manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_fork_present_with_license_and_entrypoint():
    assert FORK.is_dir(), "vendored Hermes fork missing (qubit_hermes/)"
    assert (FORK / "LICENSE").is_file(), "upstream MIT LICENSE not retained"
    assert (FORK / "hermes_cli" / "main.py").is_file()
    assert (FORK / "pyproject.toml").is_file()


def test_manifest_pins_exact_revision_and_is_clean():
    m = _manifest()
    assert m["upstream_revision"] == PIN
    assert m["upstream_repo"].endswith("/hermes-agent")
    assert m["local_root"] == "qubit_hermes"
    assert m["file_count"] > 1000
    assert m["missing_from_local"] == [], "fork is missing upstream files"
    assert m["extra_local"] == [], "fork has files not in the pin"
    assert m["patched"] == [], (
        "fork diverges from the pinned upstream without recorded patches: "
        f"{m['patched'][:10]}"
    )


def test_manifest_covers_every_file_on_disk():
    m = _manifest()
    expected = set(m["files"])
    actual = set()
    for p in FORK.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(FORK).as_posix()
        if "__pycache__" in rel or rel.endswith((".pyc", ".pyo")):
            continue
        actual.add(rel)
    assert actual <= expected, f"unmanifested files: {sorted(actual - expected)[:5]}"
    assert expected == actual, "manifest and on-disk fork disagree"


def test_runtime_is_importable_from_the_qubit_tree():
    """Prove the runtime imports from the fork (in a subprocess so the fork's
    top-level ``tests/``/``tools/`` trees cannot shadow this test session)."""
    code = (
        "import sys, pathlib\n"
        f"fork = pathlib.Path(r'{FORK}')\n"
        "sys.path.insert(0, str(fork))\n"
        "import hermes_cli.main as m\n"
        "p = pathlib.Path(m.__file__).resolve()\n"
        "assert p.is_relative_to(fork), p\n"
        "assert 'site-packages' not in str(p), p\n"
        "print('OK')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-800:]
    assert "OK" in proc.stdout


def test_runtime_status_reports_fork_and_pin():
    from voicebridge import hermes_runtime as hr

    st = hr.status()
    assert st["runtime"] == "qubit-hermes-fork"
    assert st["present"] is True
    assert st["importable"] is True
    assert st["upstream_revision"] == PIN
    assert st["patched"] == 0
    assert st["file_count"] > 1000


def test_qubit_launcher_runs_vendored_hermes_cli():
    env = dict(os.environ)
    home = tempfile.mkdtemp(prefix="hermes-home-")
    env["HOME"] = home
    env["XDG_CONFIG_HOME"] = os.path.join(home, ".config")
    env.pop("HERMES_HOME", None)
    proc = subprocess.run(
        [sys.executable, "-m", "voicebridge", "agent", "--version"],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    out = proc.stdout + proc.stderr
    assert "OpenAI SDK" in out, out[-800:]


def test_qubit_parser_exposes_agent_dispatch():
    from voicebridge.cli import build_parser

    parser = build_parser()
    ns = parser.parse_args(["agent", "status"])
    assert ns.command == "agent"
    assert ns.agent_args == ["status"]
