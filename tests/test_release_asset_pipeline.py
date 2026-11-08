"""The release workflow must publish the complete asset set, never a partial one.

A workflow dispatch that uploaded only wheel/sdist rewrote ``SHA256SUMS`` and
destroyed the bootstraps, installers and manifests of an existing release.
These tests pin the canonical asset list, the staging helper, and the workflow
shape that keeps the two in sync.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
HELPER = REPO / "scripts" / "release_assets.py"
PREPARE = REPO / "scripts" / "prepare_production_release.py"
WORKFLOW = REPO / ".github" / "workflows" / "release.yml"

FIXED_ASSETS = {
    "bootstrap.sh",
    "bootstrap.ps1",
    "install.sh",
    "install.ps1",
    "qubit-release.tar.gz",
    "qubit-release.zip",
    "component-manifest.json",
    "release-notes.md",
    "release-gate-report.json",
    "release-summary.json",
    "SHA256SUMS",
}

WHEEL = "qubit_voicebridge-9.9.9-py3-none-any.whl"
SDIST = "qubit_voicebridge-9.9.9.tar.gz"
SOURCE = "Qubit-9.9.9-source.tar.gz"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _helper():
    return _load(HELPER, "release_assets")


def _write_complete_set(directory: Path, checksum_names: set[str] | None = None) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in FIXED_ASSETS | {WHEEL, SDIST, SOURCE}:
        if name == "SHA256SUMS":
            continue
        (directory / name).write_bytes(name.encode())
    covered = FIXED_ASSETS | {WHEEL, SDIST, SOURCE}
    covered.discard("SHA256SUMS")
    if checksum_names is not None:
        covered &= checksum_names
    lines = [
        "%s  %s" % (hashlib.sha256((directory / name).read_bytes()).hexdigest(), name)
        for name in sorted(covered)
    ]
    (directory / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_canonical_asset_list_is_the_release_contract():
    helper = _helper()
    assert set(helper.REQUIRED_ASSETS) == FIXED_ASSETS
    assert "release-gate-report.json" in helper.REQUIRED_ASSETS
    assert "release-summary.json" in helper.REQUIRED_ASSETS


def test_partial_wheel_and_sdist_set_is_refused(tmp_path: Path):
    (tmp_path / WHEEL).write_bytes(b"wheel")
    (tmp_path / SDIST).write_bytes(b"sdist")
    (tmp_path / "SHA256SUMS").write_text("", encoding="utf-8")
    helper = _helper()
    with pytest.raises(SystemExit) as exc:
        helper.collect_assets(tmp_path)
    message = str(exc.value)
    for name in ("bootstrap.sh", "install.sh", "qubit-release.zip",
                 "component-manifest.json", "release-summary.json"):
        assert name in message


def test_checksum_file_must_cover_every_asset(tmp_path: Path):
    _write_complete_set(tmp_path, checksum_names={WHEEL, SDIST})
    helper = _helper()
    with pytest.raises(SystemExit) as exc:
        helper.check_assets(tmp_path)
    assert "does not cover" in str(exc.value)


def test_stage_and_check_round_trip_with_manifest(tmp_path: Path):
    source = tmp_path / "source"
    _write_complete_set(source)
    dest = tmp_path / "staged"
    manifest = tmp_path / "assets.json"
    helper = _helper()
    staged = helper.stage_assets(source, dest, manifest)
    assert {path.name for path in staged} == FIXED_ASSETS | {WHEEL, SDIST, SOURCE}
    data = json.loads(manifest.read_text())
    assert set(data["required"]) == FIXED_ASSETS
    helper.check_assets(dest, manifest)
    (dest / "bootstrap.sh").write_bytes(b"tampered")
    with pytest.raises(SystemExit) as exc:
        helper.check_assets(dest, manifest)
    assert "mismatched" in str(exc.value) or "mismatch" in str(exc.value)


def test_prepare_emits_every_required_asset_including_summary_and_gate_report(tmp_path: Path):
    module = _load(PREPARE, "prepare_production_release")
    payloads = [
        tmp_path / name
        for name in ("bootstrap.sh", "bootstrap.ps1", "install.sh", "install.ps1",
                     "qubit-release.tar.gz", "qubit-release.zip")
    ]
    paths = module._final_asset_paths(
        {"wheel": tmp_path / WHEEL, "sdist": tmp_path / SDIST},
        tmp_path / SOURCE,
        payloads,
        tmp_path / "component-manifest.json",
        tmp_path / "release-notes.md",
        tmp_path / "release-gate-report.json",
        tmp_path / "release-summary.json",
    )
    names = {path.name for path in paths}
    assert (set(_helper().REQUIRED_ASSETS) - {"SHA256SUMS"}) <= names
    assert WHEEL in names and SDIST in names and SOURCE in names


def test_workflow_uses_full_release_prep_and_stages_the_canonical_set():
    text = WORKFLOW.read_text(encoding="utf-8")
    doc = yaml.safe_load(text)

    assert "scripts/prepare_production_release.py" in text
    assert "scripts/release_assets.py" in text
    assert "--source \"$OUT\" --dest \"$ASSETS\" --manifest \"$MANIFEST\"" in text
    assert "--check \"$ASSETS\" --manifest \"$MANIFEST\"" in text

    build = doc["jobs"]["build"]
    uploads = [step for step in build["steps"]
               if step.get("uses", "").startswith("actions/upload-artifact@")]
    assert len(uploads) == 1
    upload_path = uploads[0]["with"]["path"]
    assert "release-assets/" in upload_path
    assert "release-assets-manifest.json" in upload_path
    # The old narrow path is exactly what produced the incident.
    assert "dist/release/build1/*.whl" not in upload_path

    # No partial checksum over only wheel/sdist may remain anywhere.
    assert "sha256sum ./*.whl" not in text
    assert "./*.tar.gz > ../../SHA256SUMS" not in text

    draft = doc["jobs"]["draft"]
    assert draft["environment"] == "release"
    draft_text = "\n".join(str(step.get("run", "")) for step in draft["steps"])
    assert "release-dist/release-assets/*" in draft_text
    assert "--notes-file release-dist/release-assets/release-notes.md" in draft_text
    assert "isDraft" in draft_text
    assert "refusing to modify" in draft_text
    assert "gh release create" in draft_text and "--draft" in draft_text
    assert "--verify-tag" in draft_text


def test_dispatch_checks_out_the_existing_tag_not_a_branch():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "refs/tags/{0}" in text
    assert "format('refs/tags/{0}', inputs.tag)" in text
