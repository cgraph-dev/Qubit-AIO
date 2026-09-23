"""QBPV Agent 4 (D) — pinned OpenClaw source + ledger evidence.

Independent verification that the recorded OpenClaw ledger is anchored to the
*recorded pin* (v2026.9.4, ``3a9d69db306cd7f081e06254cb89c4bcc14a7107``) and
not to whatever the local worktree happens to have checked out, that every
upstream extension is dispositioned, and that every IMPLEMENTED row's cited
backend + proving test exist on disk. The generator's own ``--check`` is run
too, so a hand-edited ledger cannot pass.

If the read-only pinned clone is absent these tests skip (CI without the
clone); they never fabricate a pass.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PIN = "3a9d69db306cd7f081e06254cb89c4bcc14a7107"
TAG = "v2026.9.4"
PIN_TREE = Path("/tmp/opencode/pinned/openclaw")
LEDGER = REPO / "docs/archive/2026-09-21" / "QUBIT_GAP_LEDGER.openclaw.json"
GEN = REPO / "scripts" / "rebuild_ledger_openclaw.py"


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(PIN_TREE), *args],
                          capture_output=True, text=True, check=True).stdout


def _ledger() -> dict:
    return json.loads(LEDGER.read_text(encoding="utf-8"))


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "rebuild_ledger_openclaw_qbpv_oc4", GEN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ledger_records_the_exact_pin() -> None:
    data = _ledger()
    assert data["upstream_pin"] == PIN, data["upstream_pin"]
    assert data["upstream_tag"] == TAG, data["upstream_tag"]


@pytest.mark.skipif(not (PIN_TREE / ".git").exists(),
                    reason="pinned openclaw clone not present")
def test_pin_commit_exists_and_tag_matches() -> None:
    kind = _git("cat-file", "-t", PIN).strip()
    assert kind == "commit"
    tag_commit = subprocess.run(
        ["git", "-C", str(PIN_TREE), "rev-list", "-n", "1", TAG],
        capture_output=True, text=True, check=True).stdout.strip()
    assert tag_commit == PIN, (TAG, tag_commit)
    version = _git("show", f"{PIN}:package.json")
    assert '"version": "2026.9.4"' in version
    protocol = _git("show",
                    f"{PIN}:packages/gateway-protocol/src/version.ts")
    assert "PROTOCOL_VERSION = 4" in protocol


@pytest.mark.skipif(not (PIN_TREE / ".git").exists(),
                    reason="pinned openclaw clone not present")
def test_every_pinned_extension_is_dispositioned() -> None:
    rebuild = _load_generator()
    listed = _git("ls-tree", "--name-only", f"{PIN}:extensions/").split()
    skip = (".md", ".json", ".ts", ".mts", ".js", ".cjs", ".mjs")
    upstream = sorted(x for x in listed
                      if not x.startswith(".") and not x.endswith(skip))
    mapped = set(rebuild.EXTENSION_MAP)
    assert upstream, "no upstream extensions found at the pin"
    assert set(upstream) <= mapped, sorted(set(upstream) - mapped)
    assert mapped <= set(upstream), sorted(mapped - set(upstream))


def test_every_implemented_row_cites_real_backend_and_proof() -> None:
    path_re = re.compile(r"(voicebridge/[\w./-]+\.py)")
    rows = _ledger()["rows"]
    assert rows, "empty openclaw ledger"
    for row in rows:
        if row.get("status") != "IMPLEMENTED":
            continue
        for rel in path_re.findall(str(row.get("qubit_backend", ""))):
            assert (REPO / rel).is_file(), (row["id"], rel)
        proving = str(row.get("proving_test", "")).split("::", 1)[0]
        assert proving, (row["id"], "IMPLEMENTED without a proving test")
        assert (REPO / proving).is_file(), (row["id"], proving)


def test_no_ledger_row_is_left_open() -> None:
    rows = _ledger()["rows"]
    open_rows = [r["id"] for r in rows
                 if r["status"] not in ("IMPLEMENTED", "SAFE_EQUIVALENT",
                                        "OPERATOR_ONLY")]
    assert open_rows == [], open_rows


def test_generator_check_is_clean() -> None:
    out = subprocess.run([sys.executable, str(GEN), "--check"],
                         cwd=str(REPO), capture_output=True, text=True)
    assert out.returncode == 0, (out.returncode, out.stdout, out.stderr)
    assert "up to date" in out.stdout
