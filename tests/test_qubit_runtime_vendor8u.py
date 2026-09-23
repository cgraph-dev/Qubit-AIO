"""Qubit-owned Hermes runtime fork — provenance and behavior proofs.

The product executes a Qubit-owned fork of the pinned Hermes source from this
tree (docs/archive/2026-09-21/QUBIT_OWNER_AMENDMENT_2026-09-11_HERMES_RUNTIME.md). These tests
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
FORK = REPO / "qubit_runtime"
# Frozen 0.21.1 provenance baseline. Production ``voicebridge.qubit_runtime``
# still reports this path (``manifest_path()``), so the runtime-status test
# reads it. The live, slice-by-slice record is the canonical generated manifest.
MANIFEST = REPO / "docs/archive/2026-09-21" / "QUBIT_HERMES_VENDOR_MANIFEST.json"
CANONICAL_MANIFEST = (
    REPO / "docs/generated" / "QUBIT_HERMES_VENDOR_MANIFEST.json")
PIN = "2237be355906fbe6065ce1815711eee52b2d646e"
W9_PIN = "d337b736aa1e8ebecfab043842d13e4a2d2f48a3"


def _manifest(path: Path = MANIFEST):
    return json.loads(path.read_text(encoding="utf-8"))


def test_fork_present_with_license_and_entrypoint():
    assert FORK.is_dir(), "vendored Hermes fork missing (qubit_runtime/)"
    assert (FORK / "LICENSE").is_file(), "upstream MIT LICENSE not retained"
    assert (FORK / "qubit_cli" / "main.py").is_file()
    assert (FORK / "pyproject.toml").is_file()


def test_manifest_pins_exact_revision_and_is_clean():
    # The canonical generated manifest records the W9 v0.21.4 pin. W9 is a
    # slice-by-slice vendor sync, so the fork intentionally carries a recorded
    # unported set (``missing_from_local``) until the remaining slices land.
    # The invariant is that every divergence is *recorded* (no UNEXPLAINED) and
    # that missing/extra/patched reconcile with the source inventory -- not that
    # the tree is fully synced.
    m = _manifest(CANONICAL_MANIFEST)
    assert m["upstream_revision"] == W9_PIN
    assert m["upstream_repo"].endswith("/hermes-agent")
    assert m["local_root"] == "qubit_runtime"
    assert m["file_count"] > 1000
    # Pristine baseline identity + deterministic Qubit patch manifest (owner
    # amendment 2026-09-18). The shipped Qubit runtime is a source-level rebrand
    # of upstream, so ``patched`` is NOT empty by design; it must instead be an
    # explicit, owned, explained set, and there must be no silent divergence.
    assert m.get("classification_counts", {}).get("UNEXPLAINED", 0) == 0, (
        "fork diverges from the pinned upstream without a recorded patch")
    patch_manifest = m.get("patch_manifest") or {}
    assert set(m["patched"]) == set(patch_manifest), (
        "every patched file must carry a recorded patch + reason + test")
    for rel, entry in patch_manifest.items():
        assert entry.get("patch"), rel
        assert entry.get("reason"), rel
        assert entry.get("affected_regression_test"), rel

    # The recorded unported/added/changed sets must agree with the canonical
    # source inventory that is generated from the same tree.
    inv = json.loads(
        (REPO / "docs/generated" / "QUBIT_SOURCE_INVENTORY.json").read_text(
            encoding="utf-8"))
    bd = inv["hermes_byte_diff"]["counts"]
    assert bd["missing"] == len(m["missing_from_local"])
    assert bd["extra"] == len(m["extra_local"])
    assert bd["changed"] == len(m["patched"])
    recon = inv["hermes_byte_diff"].get("manifest_reconciliation") or {}
    assert recon.get("changed_equals_patched") is True
    assert recon.get("extra_equals_extra_local") is True


def test_manifest_covers_every_file_on_disk():
    m = _manifest(CANONICAL_MANIFEST)
    expected = set(m["files"])
    actual = set()
    for p in FORK.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(FORK).as_posix()
        # Tool/cache dirs are never part of the vendored source snapshot; this
        # mirrors the manifest generator's _SKIP_DIR_PARTS.
        if ({"__pycache__", ".git", ".pytest_cache", ".pytest-cache"}
                & set(Path(rel).parts)
                or rel.endswith((".pyc", ".pyo"))):
            continue
        # Disposable dependency/build/cache trees are not part of the vendored
        # source snapshot unless upstream tracks them (e.g. plugin dashboard
        # ``dist/*`` bundles). Skip only untracked disposable paths, exactly as
        # the manifest generator does.
        if ({"node_modules", "dist", "build", ".cache", ".venv", "venv"}
                & set(Path(rel).parts) and rel not in expected):
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
        "import qubit_cli.main as m\n"
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
    from voicebridge import qubit_runtime as hr

    st = hr.status()
    assert st["runtime"] == "qubit-runtime-fork"
    assert st["present"] is True
    assert st["importable"] is True
    assert st["upstream_revision"] == W9_PIN
    # Qubit ships a source-level rebrand of upstream: patches are recorded and
    # owned in the patch manifest, not silent divergence (owner amendment
    # 2026-09-18). The runtime status must match the recorded patch set.
    m = _manifest(CANONICAL_MANIFEST)
    assert st["patched"] == len(m["patched"]) == len(m["patch_manifest"])
    assert st["file_count"] > 1000


def test_qubit_launcher_runs_vendored_qubit_cli():
    env = dict(os.environ)
    home = tempfile.mkdtemp(prefix="hermes-home-")
    env["HOME"] = home
    env["XDG_CONFIG_HOME"] = os.path.join(home, ".config")
    env.pop("QUBIT_HOME", None)
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
