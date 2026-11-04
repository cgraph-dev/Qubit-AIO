"""Focused, hermetic tests for ``scripts/prepare_production_release.py``.

Nothing here builds the real wheel, exports the real repository, touches a
GitHub release, or reads operator state.  The heavy end-to-end proof is the
operator run recorded in the lane report.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "prepare_production_release.py"


def _load():
    spec = importlib.util.spec_from_file_location("prepare_production_release", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _repo(tmp_path: Path, version: str = "0.0.2") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    (repo / "pyproject.toml").write_text(
        "[project]\nname = \"qubit-voicebridge\"\nversion = \"%s\"\n" % version,
        encoding="utf-8",
    )
    _git(repo, "add", "pyproject.toml")
    _git(repo, "commit", "-q", "-m", "release")
    return repo


def test_resolve_release_binds_version_and_tag(tmp_path: Path):
    module = _load()
    repo = _repo(tmp_path)
    release = module.resolve_release(repo, tag="v0.0.2")
    assert release["version"] == "0.0.2"
    assert release["tag"] == "v0.0.2"
    assert release["identity"]["state"] == "OK"


def test_resolve_release_rejects_version_and_tag_mismatch(tmp_path: Path):
    module = _load()
    repo = _repo(tmp_path)
    with pytest.raises(SystemExit):
        module.resolve_release(repo, version="0.0.3")
    with pytest.raises(SystemExit):
        module.resolve_release(repo, tag="v0.0.3")
    with pytest.raises(SystemExit):
        module.resolve_release(repo, tag="0.0.2")


def test_tag_is_never_moved(tmp_path: Path):
    module = _load()
    repo = _repo(tmp_path)
    _git(repo, "tag", "-a", "v0.0.2", "-m", "old tag")
    (repo / "new.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "new.txt")
    _git(repo, "commit", "-q", "-m", "later")
    with pytest.raises(SystemExit) as exc:
        module.check_tag(repo, "v0.0.2")
    assert "refusing to move" in str(exc.value).lower()


def test_absent_tag_is_allowed_for_local_preparation(tmp_path: Path):
    module = _load()
    repo = _repo(tmp_path)
    assert module.check_tag(repo, "v0.0.2")["state"] == "ABSENT"


def test_dirty_tree_is_refused(tmp_path: Path):
    module = _load()
    repo = _repo(tmp_path)
    (repo / "untracked.txt").write_text("x\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        module.check_tree(repo)


def test_sha256sums_round_trip(tmp_path: Path):
    module = _load()
    (tmp_path / "a.bin").write_bytes(b"a")
    (tmp_path / "b.bin").write_bytes(b"b")
    sums = tmp_path / "SHA256SUMS"
    records = module._write_sha256sums(
        [tmp_path / "a.bin", tmp_path / "b.bin"], sums
    )
    assert [r["name"] for r in records] == ["a.bin", "b.bin"]
    assert records[0]["sha256"] == hashlib.sha256(b"a").hexdigest()
    module._verify_sha256sums(tmp_path)
    (tmp_path / "a.bin").write_bytes(b"tampered")
    with pytest.raises(SystemExit):
        module._verify_sha256sums(tmp_path)


def test_source_tar_is_deterministic_and_keeps_exec_bits(tmp_path: Path):
    module = _load()
    tree = tmp_path / "tree"
    (tree / "scripts").mkdir(parents=True)
    script = tree / "scripts" / "run.sh"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    script.chmod(0o755)
    (tree / "README.md").write_text("hello\n", encoding="utf-8")
    (tree / "link").symlink_to("README.md")

    one = tmp_path / "one.tar.gz"
    two = tmp_path / "two.tar.gz"
    module._tar_source(tree, "0.0.2", one)
    module._tar_source(tree, "0.0.2", two)
    assert module.sha256(one) == module.sha256(two)

    import tarfile

    with tarfile.open(one, "r:gz") as tar:
        names = sorted(tar.getnames())
        assert names == sorted([
            "Qubit-0.0.2/README.md",
            "Qubit-0.0.2/link",
            "Qubit-0.0.2/scripts",
            "Qubit-0.0.2/scripts/run.sh",
        ])
        assert tar.getmember("Qubit-0.0.2/scripts/run.sh").mode == 0o755
        assert tar.getmember("Qubit-0.0.2/link").issym()


def test_component_manifest_records_components_and_artifacts(tmp_path: Path):
    module = _load()
    repo = _repo(tmp_path)
    (repo / "qubit_runtime").mkdir()
    (repo / "qubit_runtime" / "pyproject.toml").write_text(
        "[project]\nname = \"qubit-agent\"\nversion = \"0.21.5\"\n",
        encoding="utf-8",
    )
    (repo / "qubit_runtime" / "plugin-catalog").mkdir()
    (repo / "qubit_runtime" / "plugin-catalog" / "one.yaml").write_text("x\n")
    (repo / "qubit_runtime" / "ui-tui" / "packages" / "qubit-ink").mkdir(parents=True)
    (repo / "qubit_runtime" / "ui-tui" / "package.json").write_text(
        json.dumps({"name": "qubit-tui", "version": "0.0.1"}) + "\n"
    )
    (repo / "qubit_runtime" / "ui-tui" / "packages" / "qubit-ink" / "package.json").write_text(
        json.dumps({"name": "@qubit/ink", "version": "0.0.1"}) + "\n"
    )
    (repo / "frontend" / "qubit-dist").mkdir(parents=True)
    (repo / "frontend" / "qubit-dist" / "build-info.json").write_text(
        json.dumps({"schema": "qubit-dashboard-build/1",
                    "source_head": "deadbeef", "source_dirty": False}) + "\n"
    )
    (repo / "skills" / "one").mkdir(parents=True)
    (repo / "skills" / "one" / "SKILL.md").write_text("# skill\n")

    release = module.resolve_release(repo, tag="v0.0.2")
    tree = {"head": _git(repo, "rev-parse", "HEAD").stdout.strip(),
            "branch": "main", "clean": True}
    source = {"source": {"tracked_files": 3}, "included_files": 2,
              "excluded_files": 1, "included_by_top_level": {"(root)": 2},
              "excluded_by_reason": {"not_allowlisted": 1}}
    manifest = module._component_manifest(
        repo, release, source,
        [{"kind": "wheel", "name": "x.whl", "sha256": "0" * 64,
          "size_bytes": 1}],
        {"verdict": "PASS", "version_identity": {"state": "OK"},
         "steps": {"build": {"signing": {"state": "UNSIGNED_POLICY_APPROVED"}}}},
        tree,
    )
    assert manifest["schema"] == "qubit-component-manifest/1"
    assert manifest["product"]["version"] == "0.0.2"
    assert manifest["product"]["tag"] == "v0.0.2"
    assert manifest["components"]["vendored_runtime"]["version"] == "0.21.5"
    assert manifest["components"]["tui"]["ink"]["name"] == "@qubit/ink"
    assert manifest["components"]["plugin_catalog"]["yaml_entries"] == 1
    assert manifest["artifacts"][0]["name"] == "x.whl"
    assert manifest["publish"]["state"] == "NOT_PUBLISHED"


def test_release_notes_never_claim_verified_or_publish(tmp_path: Path):
    module = _load()
    notes = module._release_notes(
        {"version": "0.0.2", "tag": "v0.0.2"},
        {"head": "a" * 40, "branch": "production"},
        [{"name": "x.whl", "sha256": "0" * 64, "size_bytes": 1}],
    )
    assert "Qubit 0.0.2" in notes
    assert "sha256sum -c SHA256SUMS" in notes
    assert "unsigned" in notes
    assert "--unsigned-ok" in notes
    assert "VERIFIED" not in notes
    assert "gh release" not in notes
