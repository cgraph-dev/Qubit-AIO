from __future__ import annotations

import importlib.util
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from voicebridge.business import character_profile as cp
from voicebridge.business.character_profile import (
    ANIMATIONS,
    PART_COUNTS,
    SCHEMA,
    STYLE_VERSION,
    CharacterProfile,
    CharacterProfileError,
    CharacterProfileStore,
    PaidArtRefused,
    RevisionNotFound,
    SourceAssetRef,
    StaleProfileError,
    UnknownProfileError,
    apply_fallbacks,
    default_profile,
    migrate_profile_dict,
    part_counts,
    validate_profile,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_character_profile.py"


def _load_verifier():
    spec = importlib.util.spec_from_file_location(
        "verify_character_profile_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _store(tmp_path: Path, **kwargs) -> CharacterProfileStore:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    return CharacterProfileStore(tmp_path / "state", repo_root=repo, **kwargs)


def test_measured_part_catalog_counts_and_unique_ids():
    counts = part_counts()
    assert {key: counts[key] for key in PART_COUNTS} == dict(PART_COUNTS)
    ids = [part.part_id for part in cp.CATALOG]
    assert len(ids) == len(set(ids))
    for part in cp.CATALOG:
        assert part.category in (*cp.PART_CATEGORIES, "accessory")
        assert len(part.sha256) == 64
        assert part.local_path.startswith("/home/trick/Desktop/UI Office/")


def test_default_profile_is_valid(tmp_path):
    profile = default_profile("profile-a", "agent-a")
    assert validate_profile(profile, repo_root=tmp_path / "repo") == []
    assert profile.schema == SCHEMA
    assert profile.style_version == STYLE_VERSION
    assert profile.portrait_size == 32
    assert profile.animation_set == ANIMATIONS
    assert {ref.part_id for ref in profile.source_asset_refs} == {
        "body-00", "skin-00", "eyes-00", "hair-00", "outfit-00"}


def test_profile_roundtrip_preserves_every_field(tmp_path):
    profile = replace(
        default_profile("profile-a", "agent-a"),
        body_id="body-03", accessory_ids=("accessory-01",),
        palette=("#000000", "#ffffff"), shadow_policy="room",
        portrait_size=48, animation_set=("idle", "walk"), seed=42)
    rebuilt = CharacterProfile.from_dict(profile.to_dict())
    assert rebuilt == profile
    assert validate_profile(rebuilt, repo_root=tmp_path / "repo") == []


def test_validation_names_bad_fields(tmp_path):
    base = default_profile("profile-a", "agent-a")
    bad = replace(
        base,
        portrait_size=99,
        palette=("red",),
        layer_order=("body", "body"),
        shadow_policy="glow",
        animation_set=("dance",),
        body_id="body-999",
    )
    problems = validate_profile(bad, repo_root=tmp_path / "repo")
    joined = " | ".join(problems)
    assert "portrait_size" in joined
    assert "palette" in joined
    assert "layer_order" in joined
    assert "shadow_policy" in joined
    assert "animation_set" in joined
    assert "body-999" in joined


def test_store_save_load_revisions_atomic_and_private(tmp_path):
    store = _store(tmp_path)
    profile = default_profile("profile-a", "agent-a")
    saved = store.save(profile)
    assert saved.revision == 1
    loaded = store.load("profile-a")
    assert loaded.body_id == profile.body_id
    assert store.exists("profile-a")

    path = store.path_for("profile-a")
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert os.stat(path.parent).st_mode & 0o777 == 0o700

    second = store.save(replace(loaded, body_id="body-02"))
    assert second.revision == 2
    revisions = store.list_revisions("profile-a")
    assert [r["revision"] for r in revisions] == [1, 2]
    assert revisions[-1]["current"] is True

    parsed = json.loads(path.read_text())
    assert parsed["schema"] == cp.STORE_SCHEMA
    assert parsed["current_revision"] == 2


def test_stale_writer_is_refused(tmp_path):
    store = _store(tmp_path)
    saved = store.save(default_profile("profile-a", "agent-a"))
    store.save(replace(saved, body_id="body-02"))
    with pytest.raises(StaleProfileError) as exc:
        store.save(replace(saved, body_id="body-03", revision=saved.revision))
    assert exc.value.code == "CHARACTER_PROFILE_STALE"


def test_rollback_appends_a_new_revision(tmp_path):
    store = _store(tmp_path)
    first = store.save(default_profile("profile-a", "agent-a"))
    store.save(replace(first, body_id="body-02"))
    rolled = store.rollback("profile-a", 1)
    assert rolled.revision == 3
    assert rolled.body_id == first.body_id
    assert store.load("profile-a").body_id == first.body_id
    assert store.list_revisions("profile-a")[-1]["kind"] == "rollback"
    assert store.get_revision("profile-a", 1).body_id == first.body_id
    with pytest.raises(RevisionNotFound):
        store.get_revision("profile-a", 99)


def test_unknown_profile_and_revision_are_typed(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(UnknownProfileError):
        store.load("missing")
    saved = store.save(default_profile("profile-a", "agent-a"))
    with pytest.raises(RevisionNotFound):
        store.rollback("profile-a", 99)
    assert saved.revision == 1


def test_export_import_roundtrip(tmp_path):
    store = _store(tmp_path)
    profile = replace(default_profile("profile-a", "agent-a"),
                      body_id="body-04", seed=7,
                      animation_set=("idle", "talk"))
    store.save(profile)
    destination = tmp_path / "export.json"
    document = store.export("profile-a", destination)
    assert destination.exists()
    assert document["schema"] == cp.EXPORT_SCHEMA
    assert json.loads(destination.read_text())["profile"]["body_id"] == "body-04"

    other = CharacterProfileStore(tmp_path / "state-b",
                                  repo_root=tmp_path / "repo")
    imported = other.import_file(destination)
    assert imported.body_id == "body-04"
    assert imported.seed == 7
    assert imported.animation_set == ("idle", "talk")
    assert imported.revision == 1
    assert other.load("profile-a").profile_id == "profile-a"


def test_import_accepts_a_bare_profile(tmp_path):
    store = _store(tmp_path)
    bare = default_profile("profile-bare", "agent-bare").to_dict()
    imported = store.import_document(bare)
    assert imported.profile_id == "profile-bare"


def test_paid_art_inside_repository_is_refused_on_save(tmp_path):
    store = _store(tmp_path)
    repo = tmp_path / "repo"
    ref = SourceAssetRef(
        path=str(repo / "body.png"),
        sha256="a" * 64, paid=True, local_only=True, pack="paid")
    profile = replace(default_profile("profile-a", "agent-a"),
                      source_asset_refs=(ref,))
    with pytest.raises(PaidArtRefused) as exc:
        store.save(profile)
    assert exc.value.code == "PAID_ART_REFUSED"
    assert not store.exists("profile-a")


def test_paid_art_inside_repository_is_refused_on_export(tmp_path):
    store = _store(tmp_path)
    store.save(default_profile("profile-a", "agent-a"))
    with pytest.raises(PaidArtRefused):
        store.export("profile-a", tmp_path / "repo" / "export.json")
    assert not (tmp_path / "repo" / "export.json").exists()


def test_paid_profile_cannot_live_in_a_repository_state_dir(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    store = CharacterProfileStore(repo / "state", repo_root=repo)
    with pytest.raises(PaidArtRefused):
        store.save(default_profile("profile-a", "agent-a"))
    assert not (repo / "state" / "character_profiles").exists()


def test_non_paid_profile_export_inside_repository_is_allowed(tmp_path):
    store = _store(tmp_path)
    profile = replace(default_profile("profile-free", "agent-free"),
                      source_asset_refs=())
    store.save(profile)
    target = tmp_path / "repo" / "free-profile.json"
    store.export("profile-free", target)
    assert target.exists()


def test_missing_part_fallback_is_deterministic(tmp_path):
    profile = replace(default_profile("profile-a", "agent-a"),
                      body_id="body-999",
                      accessory_ids=("accessory-999", "accessory-01"))
    resolved, fallbacks = apply_fallbacks(profile,
                                          repo_root=tmp_path / "repo")
    assert resolved.body_id == "body-00"
    assert resolved.accessory_ids == ("accessory-01",)
    categories = {(row["category"], row["reason"]) for row in fallbacks}
    assert ("body", "missing_part") in categories
    assert ("accessory", "missing_part") in categories
    assert validate_profile(resolved, repo_root=tmp_path / "repo") == []
    again, again_fallbacks = apply_fallbacks(profile,
                                             repo_root=tmp_path / "repo")
    assert again == resolved
    assert again_fallbacks == fallbacks


def test_store_load_resolved_reports_fallbacks(tmp_path):
    store = _store(tmp_path)
    store.save(default_profile("profile-a", "agent-a"))
    path = store.path_for("profile-a")
    doc = json.loads(path.read_text())
    doc["revisions"][-1]["profile"]["hair_id"] = "hair-999"
    path.write_text(json.dumps(doc))
    resolved, fallbacks = store.load_resolved("profile-a")
    assert resolved.hair_id == "hair-00"
    assert any(row["category"] == "hair" for row in fallbacks)


def test_invalid_layer_and_portrait_fallbacks_are_reported(tmp_path):
    profile = replace(default_profile("profile-a", "agent-a"),
                      layer_order=("body",), portrait_size=12,
                      animation_set=("dance",))
    resolved, fallbacks = apply_fallbacks(profile,
                                          repo_root=tmp_path / "repo")
    assert resolved.layer_order == cp.LAYER_ORDER
    assert resolved.portrait_size == 32
    assert resolved.animation_set == ANIMATIONS
    reasons = {row["reason"] for row in fallbacks}
    assert {"invalid_layer_order", "invalid_portrait_size",
            "invalid_animation_set"} <= reasons


def test_legacy_profile_migrates_with_steps():
    legacy = {
        "profile_id": "profile-legacy",
        "agent_id": "agent-legacy",
        "body": "body-02",
        "colors": ["#101010"],
        "layers": ["body", "outfit", "skin", "eyes", "hair", "accessory"],
    }
    migrated, steps = migrate_profile_dict(legacy)
    assert migrated["style_version"] == STYLE_VERSION
    assert migrated["body_id"] == "body-02"
    assert migrated["palette"] == ["#101010"]
    assert migrated["seed"] == 0
    assert steps
    profile = CharacterProfile.from_dict(legacy)
    assert profile.body_id == "body-02"
    assert profile.style_version == STYLE_VERSION
    # idempotent: migrating already-current data adds no steps
    _, again = migrate_profile_dict(profile.to_dict())
    assert again == []


def test_invalid_store_configuration_is_typed(tmp_path):
    with pytest.raises(CharacterProfileError):
        CharacterProfileStore(tmp_path, max_revisions=0)
    store = _store(tmp_path)
    with pytest.raises(CharacterProfileError):
        store.save(replace(default_profile("Bad ID", "agent-a")))


def test_revision_pruning_keeps_the_current_revision(tmp_path):
    store = _store(tmp_path, max_revisions=2)
    current = store.save(default_profile("profile-a", "agent-a"))
    for body in ("body-01", "body-02", "body-03"):
        current = store.save(replace(current, body_id=body))
    revisions = store.list_revisions("profile-a")
    assert len(revisions) == 2
    assert revisions[-1]["current"] is True
    assert store.load("profile-a").body_id == "body-03"


def test_verifier_reports_all_checks_passing(tmp_path):
    verifier = _load_verifier()
    report = verifier.run_verification(tmp_path / "verify")
    assert report["ok"] is True
    assert report["checks_failed"] == 0
    assert report["checks_total"] >= 12
    names = {check["name"] for check in report["checks"]}
    assert {
        "part_counts",
        "save_revision_1",
        "stale_writer_refused",
        "rollback_appends",
        "export_import_roundtrip",
        "missing_part_fallback",
        "revision_migration",
        "paid_art_save_refused",
        "paid_art_export_refused",
        "writes_stay_inside_target",
    } <= names
    repo = tmp_path / "verify" / "repo"
    assert [p for p in repo.rglob("*") if p.is_file()] == []


def test_verifier_cli_json_exit_zero(capsys):
    verifier = _load_verifier()
    assert verifier.main(["--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert report["counts"]["body"] == 9
    assert report["counts"]["hair"] == 29


def test_verifier_never_writes_outside_target(tmp_path):
    verifier = _load_verifier()
    work = tmp_path / "isolated"
    verifier.run_verification(work)
    files = {p.relative_to(work).as_posix()
             for p in work.rglob("*") if p.is_file()}
    assert files
    assert all(name.startswith(("state-a/", "state-b/"))
               or name in {"profile-export.json"} for name in files)
