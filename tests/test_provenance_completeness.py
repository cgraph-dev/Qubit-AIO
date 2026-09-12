"""Wave 4 — per-file dashboard provenance completeness.

Required by ``docs/QUBIT_DASHBOARD_REBUILD_PLAN.md`` (acceptance gate) and the
owner amendment 2026-09-11: every vendored Hermes frontend file carries an
upstream path, an upstream blob sha256, a local sha256, and either byte-equality
or a documented Qubit patch with a reason and an affected regression test.

This test reconciles the pinned Hermes frontend
(``/tmp/opencode/pinned/hermes-agent/{web,apps/shared}``) against the vendored
copies (``frontend/hermes-web`` / ``frontend/hermes-shared``) and asserts:

1. zero unexplained differences: every missing file is absent, every extra file
   is a documented Qubit addition, and every diverging file has a ``rows[]``
   patch entry whose ``qubit_sha256`` matches disk;
2. every documented divergence/addition names an on-disk affected regression
   test;
3. ``docs/HERMES_FRONTEND_MAP.json`` no longer claims React/TS cannot execute
   in Qubit's stack (the stale adaptation rule);
4. a Wave 4 handoff diff, when present, is merged into the manifest.

The pin is only required for the byte-comparison tests; they skip cleanly when
the read-only oracle is absent (source-only checkout).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "docs" / "HERMES_SOURCE_COMPARISON.json"
FRONTEND_MAP = REPO_ROOT / "docs" / "HERMES_FRONTEND_MAP.json"
AUDIT = REPO_ROOT / "docs" / "PROVENANCE_AUDIT_WAVE4.md"
WAVE4_DIFF = REPO_ROOT / "docs" / "HERMES_SOURCE_COMPARISON.wave4.diff"

UPSTREAM = Path("/tmp/opencode/pinned/hermes-agent")
HERMES_REV = "2237be355906fbe6065ce1815711eee52b2d646e"

VENDOR_PAIRS = [
    (UPSTREAM / "web", REPO_ROOT / "frontend" / "hermes-web"),
    (UPSTREAM / "apps" / "shared", REPO_ROOT / "frontend" / "hermes-shared"),
]
EXCLUDE_DIRS = {"node_modules", "dist", "build", ".git", "__pycache__"}
EXCLUDE_NAMES = {"package-lock.json", "npm-shrinkwrap.json", "yarn.lock",
                 "pnpm-lock.yaml"}

FRONTEND_SCOPES = {"hermes-agent/web", "hermes-agent/apps/shared",
                   "hermes-agent/plugins"}


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _tree_files(root: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and not any(part in EXCLUDE_DIRS for part in p.parts) \
                and p.name not in EXCLUDE_NAMES:
            out[p.relative_to(root).as_posix()] = p
    return out


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _documented_patches(man: dict) -> dict[str, dict]:
    """qubit path -> rows[] entry that documents a patch (divergence)."""
    return {
        r["qubit"]: r for r in man["rows"]
        if r.get("qubit") and r.get("patch") and r.get("reason")
    }


def _documented_additions(man: dict) -> dict[str, dict]:
    """qubit path -> added entry (wave3/wave4) for Qubit-only files."""
    added: dict[str, dict] = {}
    for wave in ("wave3", "wave4"):
        block = man.get(wave, {}) or {}
        for entry in list(block.get("added", []) or []) + \
                list(block.get("overlay", []) or []):
            if entry.get("qubit"):
                added[entry["qubit"]] = entry
    return added


def _all_documented(man: dict) -> set[str]:
    return set(_documented_patches(man)) | set(_documented_additions(man))


# ---------------------------------------------------------------------------
# 1. Pin reconciliation: zero unexplained differences
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not UPSTREAM.is_dir(),
                    reason="pinned Hermes oracle absent")
def test_vendored_frontend_reconciles_with_pinned_source():
    man = _manifest()
    assert man["hermes"]["rev"] == HERMES_REV
    rows = {r["qubit"]: r for r in man["rows"] if r.get("qubit")}
    patches = _documented_patches(man)
    additions = _documented_additions(man)

    diverging_total = 0
    added_total = 0
    for up_root, q_root in VENDOR_PAIRS:
        up = _tree_files(up_root)
        q = _tree_files(q_root)
        prefix = q_root.relative_to(REPO_ROOT).as_posix()

        missing = sorted(set(up) - set(q))
        assert not missing, f"vendored files missing from {prefix}: {missing[:10]}"

        for rel in sorted(set(q) - set(up)):
            qrel = f"{prefix}/{rel}"
            row = rows.get(qrel)
            documented = qrel in additions or (row and row.get("patch")
                                               and row.get("reason"))
            assert documented, (
                f"undocumented Qubit addition in {prefix}: {qrel}")
            added_total += 1

        for rel in sorted(up):
            if _sha(up[rel]) == _sha(q[rel]):
                continue
            qrel = f"{prefix}/{rel}"
            row = rows.get(qrel)
            assert row is not None and row.get("patch") and row.get("reason"), (
                f"diverging vendored file lacks a documented patch: {qrel}")
            assert _sha(q[rel]) == row["qubit_sha256"], (
                f"documented patch hash is stale for {qrel}")
            assert row.get("upstream_blob_sha256") in (None, row["upstream_sha256"]), (
                f"upstream blob hash alias drift for {qrel}")
            if row.get("upstream_sha256"):
                assert _sha(up[rel]) == row["upstream_sha256"], (
                    f"upstream pin drift for {qrel}")
            diverging_total += 1

    assert diverging_total > 0, "no divergences found — manifest not reconciled?"


# ---------------------------------------------------------------------------
# 2. Every documented divergence/addition names an on-disk regression test
# ---------------------------------------------------------------------------
def test_documented_frontend_rows_carry_hashes_reason_and_regression_test():
    man = _manifest()
    checked = 0
    for row in man["rows"]:
        if row.get("scope") not in FRONTEND_SCOPES or not row.get("qubit"):
            continue
        assert row.get("reason"), row.get("qubit")
        assert row.get("upstream_blob_sha256") or not row.get("upstream_sha256"), \
            row.get("qubit")
        dep = row.get("affected_regression_test")
        assert dep, f"{row['qubit']}: missing affected_regression_test"
        assert (REPO_ROOT / dep).is_file(), \
            f"{row['qubit']}: affected_regression_test missing on disk: {dep}"
        if row.get("patch"):
            assert row.get("qubit_sha256"), row["qubit"]
            checked += 1
    assert checked >= 30, checked

    for wave in ("wave3", "wave4"):
        for entry in man.get(wave, {}).get("added", []) or []:
            dep = entry.get("affected_regression_test")
            assert dep, f"{entry.get('qubit')}: addition without regression test"
            assert (REPO_ROOT / dep).is_file(), dep


# ---------------------------------------------------------------------------
# 3. Frontend map has no stale adaptation rule
# ---------------------------------------------------------------------------
def test_frontend_map_has_no_stale_react_runtime_rule():
    doc = json.loads(FRONTEND_MAP.read_text(encoding="utf-8"))
    rule = doc["adaptation_rule"].lower()
    for stale in ("cannot execute", "static-js stack", "no react/node runtime",
                  "static-js adapter"):
        assert stale not in rule, f"stale adaptation rule still claims: {stale!r}"
    assert "react" in rule and ("vite" in rule or "node" in rule), rule
    assert "vendored" in rule or "vendor" in rule, rule

    for entry in doc["files"]:
        assert "static-js adapter" not in entry["disposition"].lower(), \
            entry["path"]

    # the map records the real vendored runtime + provenance manifest
    assert "vendoring" in doc, "frontend map missing the vendored-runtime block"
    vend = doc["vendoring"]
    assert vend["dashboard_baseline"].startswith("frontend/hermes-web")
    assert vend["shared_library"].startswith("frontend/hermes-shared")
    assert vend["pinned_revision"] == HERMES_REV
    for field in ("upstream", "upstream_blob_sha256", "qubit_sha256",
                  "patch", "reason", "affected_regression_test"):
        assert field in vend["per_file_fields"], field


# ---------------------------------------------------------------------------
# 4. Wave 4 handoff diff, when present, is merged
# ---------------------------------------------------------------------------
def test_wave4_handoff_diff_is_merged_when_present():
    if not WAVE4_DIFF.is_file():
        pytest.skip("no Wave 4 handoff diff produced yet")
    text = WAVE4_DIFF.read_text(encoding="utf-8")
    referenced = set(re.findall(r'"qubit"\s*:\s*"([^"]+)"', text))
    assert referenced, WAVE4_DIFF.name
    documented = _all_documented(_manifest())
    missing = sorted(p for p in referenced if p not in documented)
    assert not missing, (
        f"Wave 4 diff has unmerged qubit paths: {missing[:10]}")
    man = _manifest()
    assert man.get("wave4") or "wave4" in str(man.get("wave3", {})
                                             .get("handoff_merged", "")), \
        "Wave 4 diff exists but the manifest does not record the merge"


# ---------------------------------------------------------------------------
# 5. Audit artifact recorded
# ---------------------------------------------------------------------------
def test_provenance_audit_wave4_recorded():
    assert AUDIT.is_file(), AUDIT
    text = AUDIT.read_text(encoding="utf-8")
    assert "diverg" in text.lower(), "audit must record the divergence count"
    assert "adaptation rule" in text.lower(), \
        "audit must record the fixed frontend-map adaptation rule"
