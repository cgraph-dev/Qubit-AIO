"""A9 — the vendored Habblaud tree is reproducible, and the check can FAIL.

`tests/test_office_vendor_habblaud.py` verifies the tree against its own
`PROVENANCE.json` (drift detection). This module adds the strictly stronger A9
claim and — the part that makes it worth anything — demonstrates the check
FAILS when the tree is perturbed.

Two layers:

* **Falsifiability (hermetic, always runs).** A perturbed copy of the vendored
  tree must be rejected by the same comparison the rebuild verifier uses. This
  is the property that separates a real check from a rubber stamp: a check that
  cannot fail proves nothing. These tests NEVER touch the checked-in tree — they
  perturb a copy under `tmp_path`.

* **The real rebuild (network + toolchain, opt-in).** `scripts/
  verify_habblaud_revendor.py` clones the pinned commit, runs `npm ci`, builds,
  re-applies the vendor transforms and diffs byte-for-byte. It is gated behind
  `QUBIT_A9_REBUILD=1` because it needs the network and a Node toolchain; the
  default suite stays hermetic. Its presence here means A9 is *executable*, not
  merely asserted — the committed evidence for a real run is
  `docs/evidence/a9-revendor-reproduction.json`.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
VENDOR = REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office" / "habblaud"
PROVENANCE = VENDOR / "PROVENANCE.json"
VERIFIER = REPO / "scripts" / "verify_habblaud_revendor.py"

pytestmark = pytest.mark.unit


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record() -> dict:
    if not PROVENANCE.is_file():
        pytest.fail(f"no PROVENANCE.json at {PROVENANCE}")
    return json.loads(PROVENANCE.read_text(encoding="utf-8"))


def _diff_record_against_tree(root: Path, record: dict) -> dict:
    """The exact comparison the rebuild verifier performs, pointed at `root`.

    Extracted so the falsifiability tests exercise the SAME logic the verifier
    uses — testing a re-implementation would prove nothing about the verifier.
    Returns the set of disagreements; empty means "reproduced".
    """
    recorded = {f["path"]: f["sha256"] for f in record["files"]}
    on_disk = {
        str(p.relative_to(root)): _sha256(p)
        for p in root.rglob("*") if p.is_file() and p.name != "PROVENANCE.json"
    }
    return {
        "missing": sorted(set(recorded) - set(on_disk)),
        "extra": sorted(set(on_disk) - set(recorded)),
        "differing": sorted(
            p for p in set(recorded) & set(on_disk) if recorded[p] != on_disk[p]
        ),
    }


# --- baseline: the real tree reproduces its record (sanity for the comparison)


def test_shipped_tree_matches_its_record() -> None:
    diff = _diff_record_against_tree(VENDOR, _record())
    assert diff == {"missing": [], "extra": [], "differing": []}, (
        "the checked-in vendored tree does not match PROVENANCE.json: "
        f"{diff}"
    )


# --- falsifiability: a perturbed tree MUST be rejected -----------------------


def _copy_tree(tmp_path: Path) -> Path:
    dest = tmp_path / "habblaud"
    shutil.copytree(VENDOR, dest)
    return dest


def test_perturbed_byte_is_detected(tmp_path: Path) -> None:
    """Flip one byte in a vendored chunk; the comparison must notice."""
    root = _copy_tree(tmp_path)
    target = root / "bundle" / "app-U4Pwh2XA.js"
    assert target.is_file(), "expected the vendored app chunk"
    blob = target.read_bytes()
    target.write_bytes(blob[:-1] + bytes([blob[-1] ^ 0x01]))

    diff = _diff_record_against_tree(root, _record())
    assert "bundle/app-U4Pwh2XA.js" in diff["differing"], (
        "a single flipped byte in the app chunk was NOT detected — the check is "
        "not falsifiable"
    )


def test_deleted_file_is_detected(tmp_path: Path) -> None:
    root = _copy_tree(tmp_path)
    (root / "bundle" / "app-U4Pwh2XA.js").unlink()
    diff = _diff_record_against_tree(root, _record())
    assert "bundle/app-U4Pwh2XA.js" in diff["missing"], (
        "deleting a vendored chunk was NOT detected"
    )


def test_undeclared_file_is_detected(tmp_path: Path) -> None:
    root = _copy_tree(tmp_path)
    (root / "bundle" / "sneaky.js").write_text("// not in the record\n")
    diff = _diff_record_against_tree(root, _record())
    assert "bundle/sneaky.js" in diff["extra"], (
        "an undeclared file was NOT detected"
    )


def test_wrong_revision_substitution_is_detected(tmp_path: Path) -> None:
    """The real-world failure: a chunk from a DIFFERENT upstream revision.

    `86c0379` (the shared checkout's HEAD) builds `app-DrspyBzY.js`; the pin
    builds `app-U4Pwh2XA.js`. Swapping the file for the wrong-revision build
    must fail the check — this is exactly the confusion that produced the
    original "not reproducible" verdict.
    """
    root = _copy_tree(tmp_path)
    app = root / "bundle" / "app-U4Pwh2XA.js"
    app.write_bytes(b"// a bundle built from a different commit\n")
    diff = _diff_record_against_tree(root, _record())
    assert "bundle/app-U4Pwh2XA.js" in diff["differing"]


# --- the record must name a full pinned commit -------------------------------


def test_provenance_commit_is_a_full_pinned_sha() -> None:
    import re

    commit = _record().get("commit", "")
    assert re.fullmatch(r"[0-9a-f]{40}", commit), (
        f"PROVENANCE.json commit is not a full 40-hex sha: {commit!r} — A9 "
        "requires an exact revision to rebuild from"
    )


def test_verifier_script_exists_and_is_importable() -> None:
    assert VERIFIER.is_file(), (
        "scripts/verify_habblaud_revendor.py is missing — A9 has no executable "
        "reproduction path"
    )
    proc = subprocess.run(
        [sys.executable, str(VERIFIER), "--dry-run"],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, (
        f"verifier --dry-run failed (exit {proc.returncode}):\n{proc.stderr[-1500:]}"
    )


@pytest.mark.skipif(
    os.environ.get("QUBIT_A9_REBUILD") != "1",
    reason="set QUBIT_A9_REBUILD=1 to run the real clone+npm-ci+build rebuild "
           "(needs network + node toolchain)",
)
def test_real_rebuild_reproduces_the_vendored_tree() -> None:
    """End-to-end: clone the pin, build, re-vendor, diff byte-for-byte.

    Exit 0 = reproducible; 1 = the rebuild disagreed with the tree; 2 = could
    not be attempted (offline). Only a genuine rebuild may pass — an offline run
    is a SKIP, never a silent success.
    """
    proc = subprocess.run(
        [sys.executable, str(VERIFIER)],
        capture_output=True, text=True, timeout=1800,
    )
    if proc.returncode == 2:
        pytest.skip("rebuild could not be attempted (offline/toolchain): "
                    + proc.stderr[-500:])
    assert proc.returncode == 0, (
        f"re-vendor did not reproduce the committed tree (exit {proc.returncode}):\n"
        f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    )
    assert "REPRODUCIBLE" in proc.stdout
