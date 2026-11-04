"""Finish-line vendor provenance — the honest two-anchor contract.

The canonical W9 manifest records where the vendored runtime came FROM
(``PIN``/69948). The finish line is a different revision (``FROZEN_REV``/088f).
Repointing ``PIN`` at the finish line would falsify the vendoring record,
because a differing file could be a Qubit rebrand of the old source OR upstream
work we have not ported, and one tree cannot tell those apart.

These tests pin the sibling finish-line artifacts that DO make the distinction
from three real trees (vendoring source, finish line, local). They never assert
that the tree is synced; they assert that the recorded state is honest,
internally consistent, reproducible, and does not replace the W9 manifest.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import hermes_vendor_manifest as hvm  # noqa: E402

GENERATED = REPO_ROOT / "docs" / "generated"
PROVENANCE = GENERATED / "QUBIT_HERMES_FINISH_LINE_PROVENANCE.json"
FINISH_COMPARISON = GENERATED / "HERMES_SOURCE_COMPARISON_FINISH_LINE.json"
CANONICAL_MANIFEST = GENERATED / "QUBIT_HERMES_VENDOR_MANIFEST.json"
CANONICAL_COMPARISON = GENERATED / "HERMES_SOURCE_COMPARISON.json"

SOURCE_REV = "69948c005791d4d0e51670ca015238c2c7663749"
HEX64 = re.compile(r"^[0-9a-f]{64}$")

FINISH_REV = hvm._frozen_rev()

FINISH_CANDIDATES = (
    Path("/tmp/opencode/hermes-pin"),
    Path("/tmp/opencode/pinned/hermes-agent-finish-line"),
    Path.home() / ".hermes.pre-retirement" / "hermes-agent",
    Path.home() / ".hermes" / "hermes-agent",
)
SOURCE_CANDIDATES = (
    Path("/tmp/opencode/hermes-pin-69948"),
    Path("/tmp/opencode/pinned/hermes-agent-69948c0057"),
    Path("/tmp/qubit-pin-69948c0057"),
)


def _first_rev(candidates, rev):
    for path in candidates:
        if not (path / ".git").exists():
            continue
        import subprocess
        got = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()
        if got == rev:
            return path
    return None


def _load(path: Path) -> dict:
    assert path.is_file(), f"missing generated artifact {path}"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def provenance() -> dict:
    return _load(PROVENANCE)


@pytest.fixture(scope="module")
def comparison() -> dict:
    return _load(FINISH_COMPARISON)


def test_two_anchors_are_distinct_and_manifest_is_preserved(provenance):
    """The finish line must never be silently folded into the W9 anchor."""
    assert hvm.PIN == SOURCE_REV
    assert FINISH_REV != SOURCE_REV
    assert provenance["schema"] == hvm.FINISH_LINE_SCHEMA
    assert provenance["source"]["revision"] == SOURCE_REV
    assert provenance["finish_line"]["revision"] == FINISH_REV
    assert provenance["canonical_manifest_preserved"] is True
    assert provenance["canonical_vendor_manifest"] == (
        "docs/generated/QUBIT_HERMES_VENDOR_MANIFEST.json")

    # The W9 authority still records the source anchor, untouched.
    canonical = _load(CANONICAL_MANIFEST)
    assert canonical["upstream_revision"] == SOURCE_REV
    assert canonical["upstream_revision"] != provenance["finish_line"]["revision"]
    assert _load(CANONICAL_COMPARISON)["hermes"]["rev"] == SOURCE_REV


def test_finish_line_sets_are_explicit_and_partition_consistently(provenance):
    counts = provenance["classification_counts"]
    assert set(counts) == set(hvm._FINISH_CLASSIFICATIONS)
    assert sum(counts.values()) == provenance["upstream_file_count"]

    sets = {
        hvm.FINISH_IDENTICAL: counts[hvm.FINISH_IDENTICAL],
        hvm.FINISH_QUBIT_PATCHED: len(provenance["qubit_patched"]),
        hvm.FINISH_BEHIND: len(provenance["behind_finish_line"]),
        hvm.FINISH_DIVERGED: len(provenance["diverged_from_both"]),
        hvm.FINISH_MISSING: len(provenance["missing_from_local"]),
        hvm.FINISH_OMITTED: len(provenance["intentionally_omitted"]),
    }
    assert sets == counts
    assert len(provenance["missing_from_local"]) > 0, (
        "the finish-line report must list the unported set, not hide it")
    assert len(provenance["intentionally_omitted"]) > 0

    missing = {r["path"] for r in provenance["missing_from_local"]}
    omitted = {r["path"] for r in provenance["intentionally_omitted"]}
    assert missing.isdisjoint(omitted)
    assert len(missing) == counts[hvm.FINISH_MISSING]


def test_missing_entries_carry_real_upstream_hashes(provenance):
    for row in provenance["missing_from_local"]:
        assert row["path"]
        assert re.fullmatch(r"[0-9a-f]{40}", row["upstream_blob"]), row
        assert HEX64.match(row["upstream_sha256"]), row
    for row in provenance["intentionally_omitted"]:
        assert row["path"]
        assert row["reason"]


def test_behind_means_local_equals_source_and_finish_moved(provenance):
    for row in provenance["behind_finish_line"]:
        assert HEX64.match(row["local_sha256"]), row
        assert HEX64.match(row["source_blob_sha256"]), row
        assert HEX64.match(row["finish_line_blob_sha256"]), row
        assert row["local_sha256"] == row["source_blob_sha256"]
        assert row["source_blob_sha256"] != row["finish_line_blob_sha256"]


def test_diverged_is_never_credited_as_a_qubit_patch(provenance):
    for row in provenance["diverged_from_both"]:
        assert HEX64.match(row["local_sha256"]), row
        assert HEX64.match(row["finish_line_blob_sha256"]), row
        assert row["local_sha256"] != row["finish_line_blob_sha256"]
        if row["source_blob_sha256"] is not None:
            assert HEX64.match(row["source_blob_sha256"]), row
            # Diverged means both sides moved: not merely a local patch.
            assert row["local_sha256"] != row["source_blob_sha256"]
            assert row["source_blob_sha256"] != row["finish_line_blob_sha256"]
    patched = set(provenance["qubit_patched"])
    diverged = {r["path"] for r in provenance["diverged_from_both"]}
    assert patched.isdisjoint(diverged)


def test_production_python_has_no_missing_set_but_reports_drift(provenance):
    py = provenance["python"]
    assert py["finish_line_files"] > 0
    assert py["finish_line_production_files"] > 0
    # The measured state at this commit: every absent production .py from the
    # re-pin baseline has been ported. If this changes, regenerate the report.
    assert py["missing_production"] == 0
    # Drift is not zero and must stay visible rather than be relabelled.
    assert py["behind_production"] > 0
    assert py["diverged_production"] > 0


def test_finish_line_comparison_records_its_own_revision(comparison):
    assert comparison["finish_line"] is True
    assert comparison["hermes"]["rev"] == FINISH_REV
    assert comparison["source"]["rev"] == SOURCE_REV
    assert comparison["canonical_manifest_preserved"] is True
    assert comparison["rows"]

    allowed = {
        hvm.FINISH_IDENTICAL, hvm.FINISH_QUBIT_PATCHED, hvm.FINISH_BEHIND,
        hvm.FINISH_DIVERGED, hvm.FINISH_MISSING, "NOT_SHIPPED",
        "REMOVED_UPSTREAM",
    }
    for row in comparison["rows"]:
        cls = row.get("classification")
        assert cls in allowed, (row.get("upstream"), cls)
        if cls == hvm.FINISH_QUBIT_PATCHED:
            assert row["patch"] and row["reason"], row
        elif cls in (hvm.FINISH_BEHIND, hvm.FINISH_DIVERGED,
                     hvm.FINISH_MISSING, "REMOVED_UPSTREAM"):
            assert row["patch"] is None, row
            assert row["reason"], row


def test_artifacts_are_reproducible_from_the_pinned_trees():
    """Rebuild in memory and compare byte-for-byte; skip without the clones."""
    source = _first_rev(SOURCE_CANDIDATES, SOURCE_REV)
    finish = _first_rev(FINISH_CANDIDATES, FINISH_REV)
    if source is None or finish is None:
        pytest.skip("pinned source/finish-line trees not present")
    openclaw = None
    for candidate in (Path("/tmp/opencode/pinned/openclaw"),
                      Path("/tmp/opencode/pinned/openclaw-v2026.4.19-beta.2-28254-gda03996ab7")):
        if (candidate / ".git").exists():
            openclaw = candidate
            break
    if openclaw is None:
        pytest.skip("pinned OpenClaw tree not present")

    local = REPO_ROOT / "qubit_runtime"
    rebuilt = hvm.build_finish_line_provenance(
        source, finish, local, "qubit_runtime",
        source_rev=SOURCE_REV, finish_rev=FINISH_REV)
    assert (json.dumps(rebuilt, indent=1, sort_keys=True) + "\n") == \
        PROVENANCE.read_text(encoding="utf-8")

    rebuilt_cmp = hvm.build_comparison(
        finish, openclaw, rev=FINISH_REV,
        source_upstream=source, source_rev=SOURCE_REV)
    assert (json.dumps(rebuilt_cmp, indent=1) + "\n") == \
        FINISH_COMPARISON.read_text(encoding="utf-8")
