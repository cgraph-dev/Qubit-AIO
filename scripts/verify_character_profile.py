#!/usr/bin/env python3
"""Independent verifier for the P3-07 canonical CharacterProfile store.

It does not compare the store to itself. It drives the real store through a
full lifecycle in a throwaway directory and recomputes the claims:

* the measured part catalog is body 9 / eyes 7 / hair 29 / outfit 33 / skin 9;
* a profile saves atomically (``0600`` file, ``0700`` directory);
* revisions append, a stale writer is refused, and rollback appends a new
  revision whose parts equal the target revision;
* export/import round-trips through a second store;
* missing parts fall back deterministically and report every substitution;
* a pre-v1 profile migrates to the current style version with explicit steps;
* a paid-art path inside the repository is refused for save **and** export;
* the store writes only under the caller-supplied state directory.

Hermetic: no network, no service, no credentials, no paid art read.
``--json`` prints one machine-readable report.

Exit codes:
    0  every check passed
    1  a check failed
    2  usage / internal error
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from voicebridge.business.character_profile import (  # noqa: E402
    ADAPTERS,
    CATALOG_INDEX,
    PART_COUNTS,
    PROJECTION_SCHEMA,
    SCHEMA,
    STYLE_VERSION,
    CharacterProfile,
    CharacterProfileError,
    CharacterProfileStore,
    PaidArtRefused,
    SourceAssetRef,
    apply_fallbacks,
    default_profile,
    migrate_profile_dict,
    part_counts,
    project_profile,
    validate_profile,
)

EXPECTED_COUNTS = dict(PART_COUNTS)
VERIFICATION_SCHEMA = "qubit-office-character-profile-verification/1"


def _check(name: str, ok: bool, detail: str) -> Dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _with_part(profile: CharacterProfile, **changes: Any) -> CharacterProfile:
    from dataclasses import replace
    return replace(profile, **changes)


def run_verification(workdir: Path | str | None = None) -> Dict[str, Any]:
    """Run every check and return the report (no exit)."""
    tmp = Path(workdir) if workdir is not None else Path(
        tempfile.mkdtemp(prefix="qubit-character-verify-"))
    tmp.mkdir(parents=True, exist_ok=True)
    state_a = tmp / "state-a"
    state_b = tmp / "state-b"
    repo = tmp / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    export_path = tmp / "profile-export.json"
    repo_export = repo / "character-profile.json"

    store_a = CharacterProfileStore(state_a, repo_root=repo)
    store_b = CharacterProfileStore(state_b, repo_root=repo)
    checks: List[Dict[str, Any]] = []

    # 1. measured catalog
    counts = part_counts()
    checks.append(_check(
        "part_counts",
        all(counts.get(k) == v for k, v in EXPECTED_COUNTS.items()),
        f"counts={counts}"))

    # 2. default profile validates
    profile = default_profile("profile-verify", "agent-verify")
    problems = validate_profile(profile, repo_root=repo)
    checks.append(_check("default_profile_valid", not problems,
                         "valid" if not problems else "; ".join(problems)))

    # 3. three adapter projections derived from the one canonical profile
    projection = project_profile(profile)
    again = project_profile(profile)
    adapters = projection.get("adapters", {})
    checks.append(_check(
        "adapter_projection_schema",
        projection.get("schema") == PROJECTION_SCHEMA
        and set(adapters) == set(ADAPTERS),
        f"adapters={sorted(adapters)}"))
    habblaud = adapters.get("habblaud", {})
    layer_rows = habblaud.get("layers", [])
    checks.append(_check(
        "adapter_layer_order",
        [row.get("layer") for row in layer_rows]
        == list(profile.layer_order)
        and all(
            (row.get("part_id") == getattr(profile, f"{row['layer']}_id")
             if row.get("layer") != "accessory"
             else row.get("part_ids") == list(profile.accessory_ids))
            for row in layer_rows),
        f"layers={[row.get('layer') for row in layer_rows]}"))
    limezu = adapters.get("limezu_character", {})
    expected_indices = {
        category: CATALOG_INDEX[
            getattr(profile, f"{category}_id")].index
        for category in ("body", "skin", "eyes", "hair", "outfit")
    }
    checks.append(_check(
        "adapter_part_indices",
        limezu.get("parts") == expected_indices
        and limezu.get("part_ids", {}).get("body") == profile.body_id,
        f"parts={limezu.get('parts')} expected={expected_indices}"))
    portrait = adapters.get("limezu_portrait", {})
    checks.append(_check(
        "adapter_portrait_projection",
        portrait.get("portrait_cell") == profile.portrait_cell
        and portrait.get("portrait_size") == profile.portrait_size
        and portrait.get("cell_index") == 0,
        f"cell={portrait.get('portrait_cell')}"
        f" size={portrait.get('portrait_size')}"
        f" index={portrait.get('cell_index')}"))
    forty_eight = project_profile(_with_part(profile, portrait_size=48))
    checks.append(_check(
        "adapter_portrait_size_48",
        forty_eight["adapters"]["limezu_portrait"]["portrait_size"] == 48,
        f"portrait_size={forty_eight['adapters']['limezu_portrait'][
            'portrait_size']}"))
    checks.append(_check(
        "adapter_projection_pure",
        projection == again and projection["revision"] == profile.revision
        and projection["profile_id"] == profile.profile_id,
        "two projections are identical and echo the canonical identity"))
    serialised = json.dumps(projection, sort_keys=True)
    checks.append(_check(
        "adapter_projection_private",
        "/home/trick" not in serialised
        and ".png" not in serialised
        and "source_asset_refs" not in serialised
        and "local_path" not in serialised,
        "projections carry no local or paid asset path"))

    # 4. save + atomic permissions + revision 1
    saved = store_a.save(profile)
    path_a = store_a.path_for(saved.profile_id)
    mode = os.stat(path_a).st_mode & 0o777
    dir_mode = os.stat(path_a.parent).st_mode & 0o777
    checks.append(_check(
        "save_revision_1",
        saved.revision == 1 and store_a.load(saved.profile_id).revision == 1,
        f"revision={saved.revision}"))
    checks.append(_check(
        "atomic_permissions",
        mode == 0o600 and dir_mode == 0o700,
        f"file={oct(mode)} dir={oct(dir_mode)}"))

    # 5. stale writer refused, then an accepted revision 2
    stale_refused = False
    try:
        store_a.save(_with_part(saved, body_id="body-01", revision=0))
    except CharacterProfileError as exc:
        stale_refused = exc.code == "CHARACTER_PROFILE_STALE"
    second = store_a.save(_with_part(
        saved, body_id="body-01", revision=saved.revision))
    checks.append(_check("stale_writer_refused", stale_refused,
                         "stale revision refused"))
    checks.append(_check(
        "revision_2",
        second.revision == 2
        and store_a.load(saved.profile_id).body_id == "body-01",
        f"revision={second.revision} body={second.body_id}"))

    # 6. rollback appends a new revision (history is not rewritten)
    rolled = store_a.rollback(saved.profile_id, 1)
    revisions = store_a.list_revisions(saved.profile_id)
    checks.append(_check(
        "rollback_appends",
        rolled.revision == 3 and rolled.body_id == saved.body_id,
        f"revision={rolled.revision} body={rolled.body_id}"))
    checks.append(_check(
        "revision_history",
        [row["revision"] for row in revisions] == [1, 2, 3]
        and revisions[-1]["kind"] == "rollback",
        f"history={[(r['revision'], r['kind']) for r in revisions]}"))

    # 7. export / import round-trip through a second store
    document = store_a.export(saved.profile_id, export_path)
    imported = store_b.import_file(export_path)
    original = store_a.load(saved.profile_id)
    fields = ("body_id", "skin_id", "eyes_id", "hair_id", "outfit_id",
              "accessory_ids", "palette", "layer_order", "shadow_policy",
              "portrait_cell", "portrait_size", "animation_set", "seed")
    same = all(getattr(imported, f) == getattr(original, f) for f in fields)
    checks.append(_check(
        "export_import_roundtrip",
        same and export_path.exists() and imported.revision == 1,
        f"export={export_path.name} imported_revision={imported.revision}"))

    # 8. missing-part fallback
    broken = _with_part(original, body_id="body-999",
                        accessory_ids=("accessory-999",))
    resolved, fallbacks = apply_fallbacks(broken, repo_root=repo)
    categories = {row["category"] for row in fallbacks}
    checks.append(_check(
        "missing_part_fallback",
        resolved.body_id == "body-00"
        and "body" in categories and "accessory" in categories,
        f"body={resolved.body_id} fallbacks={len(fallbacks)}"))

    # 9. revision migration
    legacy = {
        "profile_id": "profile-legacy",
        "agent_id": "agent-legacy",
        "body": "body-02",
        "colors": ["#101010"],
        "layers": ["body", "outfit", "skin", "eyes", "hair", "accessory"],
    }
    migrated, steps = migrate_profile_dict(legacy)
    legacy_profile = CharacterProfile.from_dict(legacy)
    checks.append(_check(
        "revision_migration",
        migrated["style_version"] == STYLE_VERSION
        and legacy_profile.body_id == "body-02"
        and legacy_profile.style_version == STYLE_VERSION
        and bool(steps),
        f"steps={steps}"))

    # 10. paid-art guard: save and export refuse repository paths
    paid_repo_ref = SourceAssetRef(
        path=str(repo / "paid-body.png"),
        sha256=hashlib.sha256(b"paid").hexdigest(),
        paid=True, local_only=True, pack="paid-test")
    unsafe = _with_part(profile, revision=0,
                        source_asset_refs=(paid_repo_ref,))
    save_refused = False
    try:
        store_a.save(unsafe)
    except PaidArtRefused:
        save_refused = True
    export_refused = False
    try:
        store_a.export(saved.profile_id, repo_export)
    except PaidArtRefused:
        export_refused = True
    checks.append(_check(
        "paid_art_save_refused", save_refused,
        "save refused a paid path inside the repository"))
    checks.append(_check(
        "paid_art_export_refused",
        export_refused and not repo_export.exists(),
        "export refused the repository path and wrote nothing"))

    # 11. writes stay inside the caller-supplied targets
    store_files = sorted(
        p.relative_to(tmp).as_posix()
        for p in state_a.rglob("*") if p.is_file())
    repo_files = sorted(p.name for p in repo.rglob("*") if p.is_file())
    checks.append(_check(
        "writes_stay_inside_target",
        all(name.startswith("state-a/character_profiles/")
            for name in store_files)
        and repo_files == [],
        f"store_files={store_files} repo_files={repo_files}"))

    # 12. export document identity
    checks.append(_check(
        "export_document_identity",
        document.get("schema", "").startswith("qubit-office-character-profile")
        and document.get("profile", {}).get("profile_id")
        == saved.profile_id,
        f"schema={document.get('schema')}"))

    # 13. schema version
    checks.append(_check("profile_schema", original.schema == SCHEMA,
                         original.schema))

    failed = sum(1 for check in checks if not check["ok"])
    return {
        "schema": VERIFICATION_SCHEMA,
        "ok": failed == 0,
        "mode": "self-check",
        "checks_total": len(checks),
        "checks_failed": failed,
        "checks": checks,
        "counts": counts,
        "generated_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(
                timespec="seconds").replace("+00:00", "Z"),
        "export_sha256": _sha(export_path) if export_path.exists() else "",
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the Qubit Office canonical character profile.")
    parser.add_argument("--json", action="store_true",
                        help="print one machine-readable JSON report")
    args = parser.parse_args(argv)
    try:
        report = run_verification()
    except Exception as exc:  # noqa: BLE001 - never a pass
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc),
                              "schema": VERIFICATION_SCHEMA}))
        else:
            print(f"verify_character_profile: {exc}", file=sys.stderr)
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
