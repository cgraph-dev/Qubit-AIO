"""P6-06 — licence-aware asset build + local-first sync (hermetic)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from voicebridge.business import asset_build as AB
from voicebridge.business import local_sync as LS

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_asset_build.py"
CREDIT = "Office art: LimeZu — https://limezu.itch.io"
PAID_PACK = "Modern_Interiors_RPG_Maker_Version"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _row(asset_id: str, *, pack: str = "free-tiles", use: str = "room",
         license: str = "MIT", paid: bool = False, sha: str = "",
         **extra) -> dict:
    row = {
        "asset_id": asset_id,
        "pack": pack,
        "sha256": sha or _sha(asset_id.encode()),
        "intended_use": use,
        "license": license,
        "license_source": "LICENSE.txt",
        "paid": paid,
        "local_only": paid,
        "redistribution_allowed": not paid,
        "credit": CREDIT if paid else "",
        "bytes": 64,
    }
    row.update(extra)
    return row


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "verify_asset_build_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------- provenance
def test_require_provenance_accepts_complete_rows():
    records = AB.require_provenance([
        _row("asset-a"),
        _row("asset-b", pack=PAID_PACK, license="LimeZu paid local-only",
             paid=True),
    ])
    assert [r.asset_id for r in records] == ["asset-a", "asset-b"]
    assert records[1].paid and records[1].local_only
    assert records[1].redistribution_allowed is False


def test_missing_provenance_is_a_typed_refusal():
    with pytest.raises(AB.AssetBuildError) as exc:
        AB.require_provenance([{k: v for k, v in _row("a").items()
                                if k != "pack"}])
    assert exc.value.code == "ASSET_PROVENANCE_MISSING"

    with pytest.raises(AB.AssetBuildError) as exc:
        AB.require_provenance([{k: v for k, v in _row("a").items()
                                if k != "sha256"}])
    assert exc.value.code == "ASSET_PROVENANCE_MISSING"

    with pytest.raises(AB.AssetBuildError) as exc:
        AB.require_provenance([{k: v for k, v in _row("a").items()
                                if k != "intended_use"}])
    assert exc.value.code == "ASSET_PROVENANCE_MISSING"


def test_unverified_license_is_refused():
    with pytest.raises(AB.AssetBuildError) as exc:
        AB.require_provenance([_row("a", license="",
                                    license_status="unknown")])
    assert exc.value.code == "ASSET_LICENSE_UNVERIFIED"


def test_invalid_hash_and_paid_redistribution_are_refused():
    with pytest.raises(AB.AssetBuildError) as exc:
        AB.require_provenance([_row("a", sha="not-a-hash")])
    assert exc.value.code == "ASSET_HASH_INVALID"

    with pytest.raises(AB.AssetBuildError) as exc:
        AB.require_provenance([_row(
            "paid", pack=PAID_PACK, license="LimeZu paid local-only",
            paid=True, local_only=False, redistribution_allowed=True)])
    assert exc.value.code == "ASSET_PAID_REDISTRIBUTION"


def test_ledger_style_license_status_is_accepted():
    records = AB.require_provenance([{
        "path": "Modern_Interiors/floor.png",
        "pack": PAID_PACK,
        "sha256": _sha(b"x"),
        "intended_use": "room",
        "license_status": "license_file_present",
        "license_source": "LICENSE.txt",
        "paid": True,
        "credit": CREDIT,
    }])
    assert records[0].asset_id == "Modern_Interiors/floor.png"
    assert records[0].license.startswith("license_file_present")


# -------------------------------------------------------------------- bundle
def test_bundle_is_licence_aware_and_excludes_paid_art():
    manifest = AB.build_bundle([
        _row("free-a"),
        _row("paid-b", pack=PAID_PACK, license="LimeZu paid local-only",
             paid=True),
    ], bundle_id="b", credit=CREDIT)
    assert AB.validate_bundle(manifest) == []
    assert manifest["schema"] == AB.SCHEMA
    assert manifest["counts"]["total"] == 2
    assert manifest["counts"]["included"] == 1
    assert manifest["counts"]["paid_excluded"] == 1
    assert manifest["policy"]["paid_art_redistributed"] is False
    paid = next(a for a in manifest["assets"] if a["paid"])
    assert paid["included_in_bundle"] is False
    assert paid["status"] == "metadata-only"
    assert paid["credit"] == CREDIT


def test_bundle_digest_is_stable():
    rows = [_row("a"), _row("b")]
    first = AB.build_bundle(rows, bundle_id="b")
    second = AB.build_bundle(rows, bundle_id="b")
    assert first["digest"] == second["digest"]


def test_validate_bundle_rejects_bundled_paid_art():
    manifest = AB.build_bundle([_row("a")], bundle_id="b")
    manifest["assets"].append({
        **AB.build_bundle([_row(
            "paid", pack=PAID_PACK, license="LimeZu paid local-only",
            paid=True)], bundle_id="x")["assets"][0],
        "included_in_bundle": True,
    })
    problems = AB.validate_bundle(manifest)
    assert any("paid art must not be bundled" in p for p in problems)


# ----------------------------------------------------------------- leak scan
def _paid_fixture(root: Path, paid_bytes: bytes) -> dict[str, Path]:
    targets = {name: root / name for name in (
        "bundle", "cache", "screenshots", "backups", "exports")}
    for path in targets.values():
        path.mkdir(parents=True, exist_ok=True)
    (targets["bundle"] / "manifest.json").write_text(
        '{"pack": "free-tiles"}\n', encoding="utf-8")
    (targets["cache"] / "notes.json").write_text(
        '{"source": "/home/trick/Desktop/UI Office/moderninteriors-win"}\n',
        encoding="utf-8")
    (targets["screenshots"] / "shot.png").write_bytes(paid_bytes)
    (targets["exports"] / f"{PAID_PACK}.zip").write_bytes(b"PK\x03\x04")
    (targets["backups"] / "ok.txt").write_text("ok\n", encoding="utf-8")
    return targets


def test_leak_scan_finds_every_leak_channel(tmp_path):
    paid_bytes = b"paid-placeholder"
    targets = _paid_fixture(tmp_path / "scan", paid_bytes)
    report = AB.scan_targets(targets, paid_hashes=[_sha(paid_bytes)])
    assert report["ok"] is False
    kinds = {finding["kind"] for finding in report["findings"]}
    assert {"paid_pack_name", "archive_or_binary", "paid_asset_hash",
            "paid_path_reference"} <= kinds
    by_target = {finding["target"] for finding in report["findings"]}
    assert {"cache", "screenshots", "exports"} <= by_target


def test_leak_scan_is_clean_for_a_free_bundle(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "manifest.json").write_text('{"pack": "free-tiles"}',
                                        encoding="utf-8")
    report = AB.scan_for_leaks(root)
    assert report.ok is True
    assert report.checked_files == 1


def test_leak_scan_missing_root_is_empty_not_an_error(tmp_path):
    report = AB.scan_for_leaks(tmp_path / "absent")
    assert report.ok is True
    assert report.checked_files == 0


# -------------------------------------------------------------------- sync
def test_detect_conflicts_classifies_records():
    plan = LS.detect_conflicts(
        [{"id": "a", "fields": {"v": 1}}, {"id": "keep", "fields": {}}],
        [{"id": "a", "fields": {"v": 2}}, {"id": "b", "fields": {"v": 3}}],
    )
    assert plan.added == ("b",)
    assert plan.local_only == ("keep",)
    assert [c["id"] for c in plan.conflicts] == ["a"]
    assert plan.ok is False


def test_publish_requires_review_then_merges(tmp_path):
    sync = LS.LocalSync(
        tenant_id="t", store_path=tmp_path / "records.json",
        records=[{"id": "a", "fields": {"v": 1}}])
    incoming = [{"id": "a", "fields": {"v": 2}}, {"id": "b", "fields": {}}]

    with pytest.raises(LS.LocalSyncError) as exc:
        sync.publish(incoming)
    assert exc.value.code == "SYNC_REVIEW_REQUIRED"

    with pytest.raises(LS.LocalSyncError) as exc:
        sync.publish(incoming, reviewed=True)
    assert exc.value.code == "SYNC_CONFLICT_UNRESOLVED"

    # Nothing was merged while the conflict was unreviewed.
    assert sync.get("a")["fields"]["v"] == 1
    assert sync.get("b") is None

    sync.review("a", "incoming")
    receipt = sync.publish(incoming, reviewed=True)
    assert receipt.applied == ("a", "b")
    assert receipt.conflicts == 1
    assert sync.get("a")["fields"]["v"] == 2
    assert sync.get("b") is not None


def test_local_and_merged_resolutions_are_honoured(tmp_path):
    local = [{"id": "a", "fields": {"v": 1}}]
    incoming = [{"id": "a", "fields": {"v": 2}}]

    keep = LS.LocalSync(tenant_id="t", store_path=tmp_path / "k.json",
                        records=local)
    keep.review("a", "local")
    receipt = keep.publish(incoming, reviewed=True)
    assert receipt.kept == ("a",)
    assert keep.get("a")["fields"]["v"] == 1

    merged = LS.LocalSync(tenant_id="t", store_path=tmp_path / "m.json",
                          records=local)
    merged.review("a", "merged", {"v": 99})
    merged.publish(incoming, reviewed=True)
    assert merged.get("a")["fields"]["v"] == 99

    with pytest.raises(LS.LocalSyncError) as exc:
        merged.review("a", "merged")
    assert exc.value.code == "SYNC_BAD_INPUT"


def test_export_and_safe_close(tmp_path):
    sync = LS.LocalSync(
        tenant_id="t", store_path=tmp_path / "records.json",
        records=[{"id": "a", "fields": {"v": 1}}])
    exported = sync.export(tmp_path / "export.json")
    payload = json.loads((tmp_path / "export.json").read_text("utf-8"))
    assert payload["schema"] == LS.SCHEMA
    assert exported["sha256"] == _sha((tmp_path / "export.json").read_bytes())
    assert exported["records"] == 1

    closed = sync.close()
    assert closed["ok"] is True
    persisted = json.loads((tmp_path / "records.json").read_text("utf-8"))
    assert [row["id"] for row in persisted["records"]] == ["a"]


def test_remote_export_path_is_refused(tmp_path):
    sync = LS.LocalSync(tenant_id="t", store_path=tmp_path / "r.json")
    with pytest.raises(LS.LocalSyncError) as exc:
        sync.export("https://cloud.example/sync.json")
    assert exc.value.code == "SYNC_REMOTE_REFUSED"
    with pytest.raises(LS.LocalSyncError) as exc:
        LS.LocalSync(tenant_id="t", store_path="//share/sync.json")
    assert exc.value.code == "SYNC_REMOTE_REFUSED"


def test_bad_input_is_refused(tmp_path):
    with pytest.raises(LS.LocalSyncError):
        LS.detect_conflicts([{"id": "a"}], [{"id": "a"}, {"id": "a"}])
    with pytest.raises(LS.LocalSyncError):
        LS.detect_conflicts([{"fields": {}}], [])
    with pytest.raises(LS.LocalSyncError):
        LS.LocalSync(tenant_id="", store_path=tmp_path / "x.json")


def test_local_sync_imports_no_network_client():
    source = (ROOT / "voicebridge" / "business" / "local_sync.py").read_text(
        encoding="utf-8")
    for forbidden in ("import requests", "import urllib", "import socket",
                      "http.client", "aiohttp"):
        assert forbidden not in source


# ------------------------------------------------------------ verify script
def test_verify_script_passes_and_lists_its_checks():
    module = _load_script()
    report = module.run_checks()
    assert report["ok"] is True
    assert report["checks_failed"] == 0
    names = {check["name"] for check in report["checks"]}
    assert {
        "bundle_builds", "paid_art_metadata_only", "provenance_required",
        "leak_scan_detects_paid_art", "leak_scan_clean_bundle",
        "conflict_detected", "review_before_merge", "review_resolutions",
        "export_bundle", "safe_close_no_data_loss", "no_network_export",
    } <= names
    assert json.loads(json.dumps(report))["ok"] is True


def test_verify_script_exit_codes(monkeypatch):
    module = _load_script()
    assert module.main(["--json"]) == 0

    def boom():
        raise RuntimeError("build failed")

    monkeypatch.setattr(module, "run_checks", boom)
    assert module.main(["--json"]) == 2
