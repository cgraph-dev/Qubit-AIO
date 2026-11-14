#!/usr/bin/env python3
"""Verify the Qubit Office licence-aware asset build + local-first sync (P6-06).

The evidence instrument for P6-06. It exercises the real modules hermetically:

* asset provenance is required (pack/licence/sha256/use) and a missing or
  unverified field is a typed refusal;
* a paid asset that claims redistribution is refused;
* a bundle manifest is built, licence-aware and verifies clean;
* a leak scan of bundle/cache/screenshots/backups/exports finds paid pack
  names, archives, paid-asset hashes and operator-path references;
* sync detects a conflict, refuses to merge it unreviewed, then merges it after
  an explicit resolution;
* export writes a verifiable local bundle and ``close`` proves no data loss;
* a remote (network/share) export path is a typed refusal.

Everything happens under a throwaway temp directory. No network, no service, no
live state, no paid art is read or committed.

Exit codes:
    0  every check passed
    1  at least one check failed
    2  usage / build error
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from voicebridge.business.asset_build import (  # noqa: E402
    SCHEMA as ASSET_SCHEMA,
    AssetBuildError,
    build_bundle,
    scan_targets,
    validate_bundle,
)
from voicebridge.business.local_sync import (  # noqa: E402
    SCHEMA as SYNC_SCHEMA,
    LocalSync,
    LocalSyncError,
    detect_conflicts,
)

REPORT_SCHEMA = "qubit-office-asset-build-verification/1"
CREDIT = "Office art: LimeZu — https://limezu.itch.io"
PAID_PACK = "Modern_Interiors_RPG_Maker_Version"


def _check(name: str, ok: bool, detail: str) -> Dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _error_code(fn: Callable[[], Any]) -> str:
    try:
        fn()
    except (AssetBuildError, LocalSyncError) as exc:
        return exc.code
    return ""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _row(asset_id: str, *, pack: str, use: str, license: str,
         paid: bool = False, sha: str = "", **extra: Any) -> Dict[str, Any]:
    data = {
        "asset_id": asset_id,
        "pack": pack,
        "sha256": sha or _sha(asset_id.encode("utf-8")),
        "intended_use": use,
        "license": license,
        "license_source": "LICENSE.txt",
        "paid": paid,
        "local_only": paid,
        "redistribution_allowed": not paid,
        "credit": CREDIT if paid else "",
        "bytes": 128,
    }
    data.update(extra)
    return data


def _leak_fixture(root: Path, paid_hash: str, paid_bytes: bytes) -> Dict[str, Path]:
    targets = {
        "bundle": root / "bundle",
        "cache": root / "cache",
        "screenshots": root / "screenshots",
        "backups": root / "backups",
        "exports": root / "exports",
    }
    for path in targets.values():
        path.mkdir(parents=True, exist_ok=True)
    (targets["bundle"] / "manifest.json").write_text(
        '{"pack": "free-tiles", "files": 2}\n', encoding="utf-8")
    (targets["cache"] / "notes.json").write_text(
        '{"source": "~/Desktop/UI Office/moderninteriors-win"}\n',
        encoding="utf-8")
    (targets["screenshots"] / "shot.png").write_bytes(paid_bytes)
    (targets["exports"] / f"{PAID_PACK}.zip").write_bytes(b"PK\x03\x04paid")
    (targets["backups"] / "readme.txt").write_text("backup ok\n",
                                                   encoding="utf-8")
    return targets


def run_checks() -> Dict[str, Any]:
    workspace = Path(tempfile.mkdtemp(prefix="qoffice-p6-assets-"))
    checks: List[Dict[str, Any]] = []
    try:
        paid_bytes = b"paid-art-placeholder-not-real"
        paid_hash = _sha(paid_bytes)
        rows = [
            _row("asset-free-floor", pack="free-tiles", use="room",
                 license="CC0"),
            _row("asset-free-grass", pack="free-tiles", use="outside",
                 license="MIT"),
            _row("asset-paid-brick", pack=PAID_PACK, use="room",
                 license="LimeZu paid local-only", paid=True,
                 sha=paid_hash),
        ]

        # -- provenance required --------------------------------------
        bundle = build_bundle(rows, bundle_id="verify", credit=CREDIT)
        problems = validate_bundle(bundle)
        checks.append(_check(
            "bundle_builds", not problems,
            "manifest clean" if not problems else "; ".join(problems[:5])))
        checks.append(_check(
            "paid_art_metadata_only",
            all(a["included_in_bundle"] is False
                for a in bundle["assets"] if a["paid"])
            and bundle["policy"]["paid_art_redistributed"] is False,
            f"included={bundle['counts']['included']}"
            f" paid_excluded={bundle['counts']['paid_excluded']}"))

        missing_pack = _error_code(lambda: build_bundle(
            [{k: v for k, v in rows[0].items() if k != "pack"}],
            bundle_id="x"))
        missing_license = _error_code(lambda: build_bundle(
            [_row("asset-no-license", pack="free-tiles", use="room",
                  license="", license_status="unknown")], bundle_id="x"))
        missing_hash = _error_code(lambda: build_bundle(
            [{k: v for k, v in rows[0].items() if k != "sha256"}],
            bundle_id="x"))
        missing_use = _error_code(lambda: build_bundle(
            [{k: v for k, v in rows[0].items() if k != "intended_use"}],
            bundle_id="x"))
        bad_hash = _error_code(lambda: build_bundle(
            [_row("asset-bad-hash", pack="free-tiles", use="room",
                  license="MIT", sha="not-a-hash")], bundle_id="x"))
        paid_misconfigured = _error_code(lambda: build_bundle(
            [_row("asset-paid-bad", pack=PAID_PACK, use="room",
                  license="LimeZu paid local-only", paid=True,
                  local_only=False, redistribution_allowed=True)],
            bundle_id="x"))
        provenance_ok = (
            missing_pack == "ASSET_PROVENANCE_MISSING"
            and missing_hash == "ASSET_PROVENANCE_MISSING"
            and missing_use == "ASSET_PROVENANCE_MISSING"
            and missing_license == "ASSET_LICENSE_UNVERIFIED"
            and bad_hash == "ASSET_HASH_INVALID"
            and paid_misconfigured == "ASSET_PAID_REDISTRIBUTION")
        checks.append(_check(
            "provenance_required", provenance_ok,
            f"missing_pack={missing_pack} missing_license={missing_license}"
            f" missing_hash={missing_hash} missing_use={missing_use}"
            f" bad_hash={bad_hash} paid={paid_misconfigured}"))

        # -- leak scan ------------------------------------------------
        targets = _leak_fixture(workspace / "scan", paid_hash, paid_bytes)
        report = scan_targets(targets, paid_hashes=[paid_hash])
        kinds = {finding["kind"] for finding in report["findings"]}
        checks.append(_check(
            "leak_scan_detects_paid_art",
            report["ok"] is False
            and {"paid_pack_name", "archive_or_binary",
                 "paid_asset_hash", "paid_path_reference"} <= kinds,
            f"findings={len(report['findings'])} kinds={sorted(kinds)}"))
        clean_root = workspace / "clean"
        clean_root.mkdir()
        (clean_root / "manifest.json").write_text('{"pack": "free-tiles"}\n',
                                                  encoding="utf-8")
        clean = scan_targets({"bundle": clean_root}, paid_hashes=[paid_hash])
        checks.append(_check(
            "leak_scan_clean_bundle", clean["ok"] is True,
            f"checked={clean['checked_files']} findings="
            f"{len(clean['findings'])}"))

        # -- sync conflict review -------------------------------------
        store = workspace / "sync" / "records.json"
        export_path = workspace / "sync" / "export.json"
        local = [{"id": "rec-a", "fields": {"value": 1}}]
        incoming = [
            {"id": "rec-a", "fields": {"value": 2}},
            {"id": "rec-b", "fields": {"value": 3}},
        ]
        plan = detect_conflicts(local, incoming)
        checks.append(_check(
            "conflict_detected",
            len(plan.conflicts) == 1 and plan.added == ("rec-b",)
            and plan.conflicts[0]["id"] == "rec-a",
            f"conflicts={len(plan.conflicts)} added={list(plan.added)}"))

        sync = LocalSync(tenant_id="tenant-local", store_path=store,
                         records=local)
        unreviewed = _error_code(lambda: sync.publish(incoming))
        unresolved = _error_code(lambda: sync.publish(incoming, reviewed=True))
        sync.review("rec-a", "incoming")
        receipt = sync.publish(incoming, reviewed=True)
        checks.append(_check(
            "review_before_merge",
            unreviewed == "SYNC_REVIEW_REQUIRED"
            and unresolved == "SYNC_CONFLICT_UNRESOLVED"
            and receipt.applied == ("rec-a", "rec-b")
            and sync.get("rec-a")["fields"]["value"] == 2,
            f"unreviewed={unreviewed} unresolved={unresolved}"
            f" applied={list(receipt.applied)}"))

        local_wins = LocalSync(
            tenant_id="tenant-local", store_path=workspace / "sync2.json",
            records=local)
        local_wins.review("rec-a", "local")
        kept = local_wins.publish(incoming, reviewed=True)
        merged_sync = LocalSync(
            tenant_id="tenant-local", store_path=workspace / "sync3.json",
            records=local)
        merged_sync.review("rec-a", "merged", {"value": 99})
        merged = merged_sync.publish(incoming, reviewed=True)
        checks.append(_check(
            "review_resolutions",
            kept.kept == ("rec-a",)
            and local_wins.get("rec-a")["fields"]["value"] == 1
            and merged_sync.get("rec-a")["fields"]["value"] == 99,
            f"local_kept={list(kept.kept)} merged_value="
            f"{merged_sync.get('rec-a')['fields']['value']}"
            f" applied={list(merged.applied)}"))

        # -- export + safe exit ---------------------------------------
        exported = sync.export(export_path)
        written = json.loads(export_path.read_text("utf-8"))
        checks.append(_check(
            "export_bundle",
            export_path.is_file()
            and written["schema"] == SYNC_SCHEMA
            and exported["records"] == 2
            and exported["sha256"] == _sha(export_path.read_bytes()),
            f"path={export_path.name} records={exported['records']}"))

        closed = sync.close()
        persisted = json.loads(store.read_text("utf-8"))
        checks.append(_check(
            "safe_close_no_data_loss",
            closed["ok"] is True and closed["records"] == 2
            and {r["id"] for r in persisted["records"]} == {"rec-a", "rec-b"},
            f"path={Path(closed['path']).name}"
            f" records={closed['records']}"))

        remote = _error_code(lambda: sync.export("https://cloud.example/sync"))
        checks.append(_check(
            "no_network_export", remote == "SYNC_REMOTE_REFUSED",
            f"remote={remote}"))
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    failed = sum(1 for check in checks if not check["ok"])
    return {
        "schema": REPORT_SCHEMA,
        "ok": failed == 0,
        "mode": "self-check",
        "asset_schema": ASSET_SCHEMA,
        "sync_schema": SYNC_SCHEMA,
        "checks_total": len(checks),
        "checks_failed": failed,
        "checks": checks,
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the Qubit Office asset build and local sync.")
    parser.add_argument("--json", action="store_true",
                        help="print one machine-readable JSON report")
    args = parser.parse_args(argv)
    try:
        report = run_checks()
    except Exception as exc:  # noqa: BLE001 - usage/build error, never a pass
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc),
                              "schema": REPORT_SCHEMA}))
        else:
            print(f"verify_asset_build: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for check in report["checks"]:
            mark = "PASS" if check["ok"] else "FAIL"
            print(f"[{mark}] {check['name']}: {check['detail']}")
        print("ok" if report["ok"] else "failed")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
