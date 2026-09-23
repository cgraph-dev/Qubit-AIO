"""9U — OpenClaw OPERATOR_ONLY disposition review coverage gate.

The contractual-pin surface is fully disposed, but 95 rows are ``OPERATOR_ONLY``
and need owner review. ``docs/archive/2026-09-22-pre-reset/QUBIT_OPENCLAW_OPERATOR_ONLY_REVIEW_2026-09-21.md``
is the review document generated deterministically by
``scripts/openclaw_operator_only_review.py``.

These tests assert the review covers *exactly* the manifest's OPERATOR_ONLY set
(no missing, no extra, no duplicate), that every row carries a prerequisite and
a classification (and, for architecture-gated rows, the "what would need to
change" / "real capability vs intentional choice" notes), and that the document
is regenerable from the manifest and handler source. They do not assert the
classification is *correct* — that is the owner's ruling — and they do not
change any disposition.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import openclaw_operator_only_review as REVIEW  # noqa: E402

MANIFEST = REPO_ROOT / "docs/generated/QUBIT_OPENCLAW_UPGRADE_MANIFEST.json"
REVIEW_DOC = REPO_ROOT / "docs/generated/QUBIT_OPENCLAW_OPERATOR_ONLY_REVIEW_2026-09-21.md"
EVIDENCE_RE = re.compile(r"^(?P<path>[^\s:]+\.py):(?P<line>\d+)$")


def _load_manifest_operator_only() -> dict:
    data = json.loads(MANIFEST.read_text())
    methods = data["pinned_surface_disposition"]["methods"]
    return {name: rec for name, rec in methods.items()
            if rec.get("status") in ("OPERATOR_ONLY",
                                     "OPERATOR_ONLY_EXTERNAL",
                                     "OPERATOR_ONLY_ARCHITECTURE")}


def _doc_index() -> dict:
    text = REVIEW_DOC.read_text()
    begin = text.index(REVIEW.JSON_BEGIN) + len(REVIEW.JSON_BEGIN)
    end = text.index(REVIEW.JSON_END)
    return json.loads(text[begin:end])


@pytest.fixture(scope="module")
def manifest_ops() -> dict:
    return _load_manifest_operator_only()


@pytest.fixture(scope="module")
def index() -> dict:
    return _doc_index()


def test_review_document_exists() -> None:
    assert REVIEW_DOC.is_file(), f"missing {REVIEW_DOC}"


def test_review_covers_exactly_the_manifest_operator_only_set(
        index: dict, manifest_ops: dict) -> None:
    """No missing, no extra: the review's method set IS the manifest's set."""
    rows = index["rows"]
    reviewed = [row["method"] for row in rows]
    manifest_set = set(manifest_ops)
    assert len(reviewed) == len(set(reviewed)), "duplicate method rows"
    assert set(reviewed) == manifest_set, (
        "review/manifest OPERATOR_ONLY mismatch; missing="
        f"{sorted(manifest_set - set(reviewed))} "
        f"extra={sorted(set(reviewed) - manifest_set)}")


def test_review_counts_match_the_rows(index: dict, manifest_ops: dict) -> None:
    rows = index["rows"]
    counts = index["counts"]
    assert counts["total"] == len(rows) == len(manifest_ops) == 95
    external = sum(1 for r in rows
                   if r["classification"] == REVIEW.EXTERNAL)
    gated = sum(1 for r in rows if r["classification"] == REVIEW.ARCH)
    assert counts[REVIEW.EXTERNAL] == external
    assert counts[REVIEW.ARCH] == gated
    assert external + gated == counts["total"]


def test_every_row_has_required_fields(index: dict) -> None:
    for row in index["rows"]:
        method = row["method"]
        assert row.get("code"), f"{method}: missing typed code"
        assert row.get("prerequisite"), f"{method}: missing prerequisite"
        classification = row.get("classification")
        assert classification in REVIEW.CLASSIFICATIONS, (
            f"{method}: bad classification {classification!r}")
        assert row.get("counterpart"), f"{method}: missing counterpart/refusal"
        assert row.get("category"), f"{method}: missing category"
        assert row.get("rationale"), f"{method}: missing rationale"


def test_every_row_evidence_is_a_real_file_line(index: dict) -> None:
    for row in index["rows"]:
        match = EVIDENCE_RE.match(row["evidence"])
        assert match, f"{row['method']}: bad evidence {row['evidence']!r}"
        path = REPO_ROOT / match.group("path")
        assert path.is_file(), f"{row['method']}: no such file {path}"
        line = int(match.group("line"))
        total = len(path.read_text().splitlines())
        assert 1 <= line <= total, (
            f"{row['method']}: evidence line {line} out of range for {path}")


def test_row_code_matches_the_manifest(index: dict, manifest_ops: dict) -> None:
    for row in index["rows"]:
        assert row["code"] == manifest_ops[row["method"]]["code"], row["method"]
        assert row["evidence"].split(":")[0] \
            == manifest_ops[row["method"]]["evidence"], row["method"]


def test_architecture_gated_rows_state_change_and_choice(index: dict) -> None:
    gated = [r for r in index["rows"] if r["classification"] == REVIEW.ARCH]
    assert gated, "expected architecture-gated rows"
    for row in gated:
        assert row.get("would_need"), (
            f"{row['method']}: architecture-gated row without 'would_need'")
        assert row.get("choice"), (
            f"{row['method']}: architecture-gated row without 'choice'")
    for row in index["rows"]:
        if row["classification"] == REVIEW.EXTERNAL:
            assert row.get("would_need") is None, row["method"]
            assert row.get("choice") is None, row["method"]


def test_classification_table_covers_every_manifest_code(
        manifest_ops: dict) -> None:
    codes = {rec["code"] for rec in manifest_ops.values()}
    assert codes == set(REVIEW.CLASSIFICATION), (
        "classification table / manifest code mismatch; missing="
        f"{sorted(codes - set(REVIEW.CLASSIFICATION))} "
        f"extra={sorted(set(REVIEW.CLASSIFICATION) - codes)}")


def test_document_is_regenerable_from_the_manifest(index: dict) -> None:
    """Re-derive the rows and compare; catches hand-edited drift."""
    fresh = REVIEW.build_rows(MANIFEST)
    assert fresh == index["rows"]
    assert REVIEW.render(fresh, MANIFEST) == REVIEW_DOC.read_text()


def test_generator_check_mode_passes() -> None:
    assert REVIEW.main(["--check"]) == 0
