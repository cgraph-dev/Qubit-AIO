from __future__ import annotations

import hashlib
import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import pytest

from voicebridge.business import character_profile as cp
from voicebridge.business.character_profile import (
    ANIMATIONS,
    LAYER_ORDER,
    SHADOW_POLICIES,
    STYLE_VERSION,
    CharacterProfileStore,
    PaidArtRefused,
    SourceAssetRef,
    default_profile,
    project_profile,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_character_profile.py"


def _load_verifier():
    spec = importlib.util.spec_from_file_location(
        "verify_character_profile_phase6", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _store(tmp_path: Path) -> CharacterProfileStore:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    return CharacterProfileStore(tmp_path / "state", repo_root=repo)


def _profile(**changes):
    return replace(default_profile("profile-a", "agent-a"), **changes)


def test_one_record_holds_every_p6_contract_field(tmp_path):
    profile = _profile(
        accessory_ids=("accessory-00",),
        palette=("#101010", "#f0b429"),
        shadow_policy="room",
        portrait_cell="cell-2",
        portrait_size=48,
        animation_set=("idle", "walk", "type", "talk", "think", "break",
                       "celebrate"),
        seed=321,
        revision=0,
    )
    assert profile.style_version == STYLE_VERSION
    assert profile.layer_order == LAYER_ORDER
    assert set(profile.animation_set) == set(ANIMATIONS)
    assert profile.shadow_policy in SHADOW_POLICIES
    assert profile.portrait_size in (32, 48)
    assert profile.seed == 321
    assert profile.source_asset_refs
    assert all(ref.part_id for ref in profile.source_asset_refs)
    assert cp.validate_profile(profile, repo_root=tmp_path / "repo") == []
    projection = project_profile(profile)
    assert projection["adapters"]["limezu_portrait"]["portrait_size"] == 48
    assert projection["adapters"]["limezu_portrait"]["cell_index"] == 2


def test_single_save_authority_revisions_rollback_and_import_export(tmp_path):
    store = _store(tmp_path)
    first = store.save(_profile(body_id="body-00"))
    second = store.save(replace(first, body_id="body-05"))
    assert (first.revision, second.revision) == (1, 2)
    rolled = store.rollback("profile-a", 1)
    assert rolled.revision == 3
    assert rolled.body_id == "body-00"
    kinds = [(row["revision"], row["kind"])
             for row in store.list_revisions("profile-a")]
    assert kinds == [(1, "save"), (2, "save"), (3, "rollback")]

    document = store.export("profile-a")
    assert document["schema"] == cp.EXPORT_SCHEMA
    imported_store = CharacterProfileStore(
        tmp_path / "state-b", repo_root=tmp_path / "repo")
    imported = imported_store.import_document(document)
    assert imported.body_id == "body-00"
    assert imported.revision == 1


def test_paid_art_guard_refuses_save_and_export_inside_repository(tmp_path):
    store = _store(tmp_path)
    repo = tmp_path / "repo"
    paid = SourceAssetRef(
        path=str(repo / "hair.png"),
        sha256=hashlib.sha256(b"paid").hexdigest(),
        paid=True, local_only=True, pack="LimeZu",
    )
    with pytest.raises(PaidArtRefused):
        store.save(_profile(source_asset_refs=(paid,)))
    assert not store.exists("profile-a")

    local_paid = replace(
        paid, path=str(tmp_path / "local-paid-art" / "hair.png"))
    store.save(_profile(source_asset_refs=(local_paid,)))
    assert store.exists("profile-a")
    with pytest.raises(PaidArtRefused):
        store.export("profile-a", repo / "export.json")
    assert not (repo / "export.json").exists()


def test_verifier_covers_adapters_counts_rollback_and_paid_guard(tmp_path):
    verifier = _load_verifier()
    report = verifier.run_verification(tmp_path / "verify")
    assert report["ok"] is True
    assert report["checks_failed"] == 0
    names = {check["name"] for check in report["checks"]}
    assert {
        "part_counts",
        "adapter_projection_schema",
        "adapter_layer_order",
        "adapter_part_indices",
        "adapter_portrait_projection",
        "adapter_portrait_size_48",
        "adapter_projection_pure",
        "adapter_projection_private",
        "rollback_appends",
        "export_import_roundtrip",
        "missing_part_fallback",
        "revision_migration",
        "paid_art_save_refused",
        "paid_art_export_refused",
    } <= names
    assert report["counts"] == {
        "body": 9, "skin": 9, "eyes": 7, "hair": 29, "outfit": 33,
        "accessory": 6,
    }


def test_verifier_json_exit_zero_and_no_repo_writes(tmp_path):
    verifier = _load_verifier()
    work = tmp_path / "isolated"
    report = verifier.run_verification(work)
    files = {p.relative_to(work).as_posix()
             for p in work.rglob("*") if p.is_file()}
    assert files
    assert all(name.startswith(("state-a/", "state-b/"))
               or name == "profile-export.json" for name in files)
    assert report["ok"] is True
    payload = json.loads(json.dumps(report, sort_keys=True))
    assert payload["schema"] == "qubit-office-character-profile-verification/1"


def test_verifier_cli_writes_one_json_document(capsys):
    verifier = _load_verifier()
    assert verifier.main(["--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert report["checks_failed"] == 0
    assert report["checks_total"] >= 20
