"""Focused tests for ``scripts/prepare_release_seed.py``.

These are hermetic: they use temporary directories and never export the real
repository, build a wheel, or touch a live install.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load():
    path = REPO / "scripts" / "prepare_release_seed.py"
    spec = importlib.util.spec_from_file_location("prepare_release_seed", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_prune_missing_build_refs(tmp_path: Path):
    module = _load()
    dest = tmp_path / "seed"
    dest.mkdir()
    (dest / "present.txt").write_text("x", encoding="utf-8")
    (dest / "scripts").mkdir()
    pyproject = dest / "pyproject.toml"
    pyproject.write_text(
        "\n".join(
            [
                "[tool.hatch.build.targets.wheel]",
                'artifacts = ["missing-dist"]',
                "",
                "[tool.hatch.build.targets.wheel.force-include]",
                '"present.txt" = "pkg/present.txt"',
                '"docs/INTERNAL.md" = "pkg/internal.md"',
                "",
                "[tool.hatch.build.targets.sdist]",
                "artifacts = [",
                '  "missing-dist",',
                '  "present.txt",',
                "]",
                "",
                "[tool.hatch.build.targets.sdist.force-include]",
                '"docs/OTHER.md" = "docs/other.md"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    removed = module.prune_missing_build_refs(pyproject, dest)
    assert "docs/INTERNAL.md" in removed
    assert "docs/OTHER.md" in removed
    assert "missing-dist" in removed
    text = pyproject.read_text(encoding="utf-8")
    assert "docs/INTERNAL.md" not in text
    assert "docs/OTHER.md" not in text
    assert "present.txt" in text
    assert module.missing_build_refs(pyproject, dest) == []


def test_scan_forbidden_blocks_state_models_and_internal_docs(tmp_path: Path):
    module = _load()
    dest = tmp_path / "seed"
    (dest / "docs").mkdir(parents=True)
    (dest / "docs" / "PUBLIC.md").write_text("ok", encoding="utf-8")
    (dest / "docs" / "INTERNAL.md").write_text("internal", encoding="utf-8")
    (dest / ".env").write_text("SECRET=1", encoding="utf-8")
    (dest / "state.db").write_text("db", encoding="utf-8")
    (dest / "model.onnx").write_text("w", encoding="utf-8")
    (dest / "leak.txt").write_text("/home/" + "trick/private", encoding="utf-8")
    forbidden, personal = module.scan_forbidden(dest, {"docs/PUBLIC.md"})
    assert "docs/INTERNAL.md" in forbidden
    assert ".env" in forbidden
    assert "state.db" in forbidden
    assert "model.onnx" in forbidden
    assert personal == ["leak.txt"]


def test_version_file_and_git_metadata(tmp_path: Path):
    module = _load()
    dest = tmp_path / "seed"
    dest.mkdir()
    (dest / "README.md").write_text("seed", encoding="utf-8")
    module.write_version(dest, "0.0.1")
    assert (dest / "VERSION").read_text(encoding="utf-8") == "0.0.1\n"
    checks = module.Checks()
    commit = module.init_git(dest, "deadbeef", "v0.0.1", checks, 2)
    checks.finish()
    assert len(commit) == 40
    log = module._run(["git", "log", "--oneline"], cwd=dest).stdout
    assert log.count("\n") == 1
    assert module._run(["git", "tag"], cwd=dest).stdout.strip() == "v0.0.1"


def test_release_base_publishes_installer_and_source_archive(tmp_path: Path):
    module = _load()
    dest = tmp_path / "seed"
    (dest / "scripts").mkdir(parents=True)
    installer = dest / "scripts" / "install.sh"
    installer.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    archive = tmp_path / "Qubit-0.0.1-seed.tar.gz"
    archive.write_bytes(b"fake archive bytes")
    base = tmp_path / "release"

    asset, checksums = module.make_release_base(dest, base, archive)

    assert asset == base / "install.sh"
    assert asset.read_bytes() == installer.read_bytes()
    release_archive = base / "qubit-release.tar.gz"
    assert release_archive.read_bytes() == archive.read_bytes()
    lines = checksums.read_text(encoding="utf-8").splitlines()
    assert f"{module._sha256(asset)}  install.sh" in lines
    assert f"{module._sha256(release_archive)}  qubit-release.tar.gz" in lines
