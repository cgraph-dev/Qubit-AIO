"""Focused tests for scripts/export_public_tree.py.

Hermetic: every test builds a throwaway Git repository under pytest's
``tmp_path``.  No live checkout, network, service, or operator HOME is read.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "export_public_tree.py"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")

    included = {
        "README.md": "committed\n",
        "INSTALL.md": "# install\n",
        "TROUBLESHOOTING.md": "# troubleshooting\n",
        "LICENSE": "MIT\n",
        ".env.example": "DEEPSEEK_API_KEY=\n",
        "pyproject.toml": "[project]\nname = 'x'\n",
        "pytest.ini": "[pytest]\n",
        "qubit_runtime/app.py": "print('runtime')\n",
        "qubit_runtime/plugins/foo/manifest.json": "{}\n",
        "qubit_runtime/ui-tui/dist/entry.js": "x\n",
        "qubit_runtime/plugin-catalog/foo.yaml": "name: foo\n",
        "qubit_runtime/website/static/api/plugin-catalog.json": "{}\n",
        "qubit_runtime/optional-skills/foo/SKILL.md": "# optional skill\n",
        "qubit_runtime/plugins/qubit-achievements/dashboard/dist/index.js": "x\n",
        "frontend/qubit-web/public/plugins/qubit-achievements/dist/index.js": "x\n",
        "frontend/qubit-web/src/App.tsx": "export default 1;\n",
        "qubit_runtime/contributors/emails/a@example.invalid": "alice\n",
        "voicebridge/app.py": "print('voice')\n",
        "frontend/src/App.tsx": "export default 1;\n",
        "tests/test_x.py": "def test_x():\n    assert True\n",
        "skills/foo/SKILL.md": "# skill\n",
        "config/voice.example.yaml": "output_device: ''\n",
        "systemd/foo.service": "[Unit]\n",
        "docs/QUBIT-TV.md": "# public feature doc\n",
        "Modelfile.qubit.example": "FROM public/model:latest\n",
        "scripts/ok.py": "print('ok')\n",
        "adapters/registry.py": "REGISTRY = {}\n",
    }
    for rel, text in included.items():
        _write(repo, rel, text)

    excluded = {
        ".env": "SECRET=1\n",
        "auth.json": "{}\n",
        "server.pem": "-----BEGIN PRIVATE KEY-----\n",
        "qubit_runtime/.env": "SECRET=1\n",
        "qubit_runtime/auth.json": "{}\n",
        "qubit_runtime/server.pem": "-----BEGIN PRIVATE KEY-----\n",
        "node_modules/pkg/index.js": "x\n",
        "frontend/qubit-dist/index.html": "x\n",
        "qubit_runtime/plugins/foo/dashboard/dist/index.js": "x\n",
        "tmp/foo.txt": "x\n",
        "errs/x.txt": "x\n",
        "PORTED.json": "{}\n",
        "PROGRESS.md": "# internal\n",
        "docs/STATUS.md": "# internal\n",
        "docs/OPEN_THREADS.md": "# internal\n",
        "docs/DECISIONS.md": "# internal\n",
        "docs/PLAN.md": "# internal\n",
        "docs/generated/x.json": "{}\n",
        "docs/archive/x.md": "# frozen\n",
        "docs/evidence/shot.png": "png\n",
        "docs/game-office/x.md": "# internal\n",
        "docs/NOT-PUBLIC.md": "# internal report\n",
        "docs/PARITY.md": "# internal process doc\n",
        "DESIGN.md": "# not in the simple public set\n",
        "PRODUCT.md": "# not in the simple public set\n",
        "screenshot.png": "png\n",
        "e2e-shot.png": "png\n",
        "config/voice.local.yaml": "personal: true\n",
        ".opencode/config.json": "{}\n",
        ".claude/helper.md": "# internal\n",
        "Modelfile.qubit": "FROM x\n",
        "qubit_runtime/model.gguf": "weights\n",
        "docs/private.md": "path /home/example/Projects/Repos/Qubit\n",
        "qubit_runtime/secret_notes.md": "path /home/example/.config\n",
    }
    for rel, text in excluded.items():
        _write(repo, rel, text)

    os.symlink("app.py", repo / "qubit_runtime" / "link-ok")
    os.symlink("/etc/passwd", repo / "qubit_runtime" / "link-escape")
    os.symlink("../../outside", repo / "qubit_runtime" / "link-parent")

    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fixture")
    return repo


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(repo),
            "--personal-path-marker",
            "/home/example",
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _check_json(repo: Path) -> dict:
    result = _run(repo, "--check", "--json")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _risk(report: dict, kind: str) -> dict:
    return next(r for r in report["residual_risks"] if r["kind"] == kind)


def _tree_digest(root: Path) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            rows.append((rel, "symlink", os.readlink(path)))
        elif path.is_file():
            rows.append((rel, "file", hashlib.sha256(path.read_bytes()).hexdigest()))
    return rows


def test_check_reports_allowlist_and_exclusions(sample_repo: Path) -> None:
    report = _check_json(sample_repo)
    included = set(report["included_paths"])

    for rel in (
        "README.md",
        "INSTALL.md",
        "TROUBLESHOOTING.md",
        "LICENSE",
        ".env.example",
        "qubit_runtime/app.py",
        "qubit_runtime/plugins/foo/manifest.json",
        "qubit_runtime/ui-tui/dist/entry.js",
        "qubit_runtime/plugins/qubit-achievements/dashboard/dist/index.js",
        "frontend/qubit-web/public/plugins/qubit-achievements/dist/index.js",
        "qubit_runtime/link-ok",
        "voicebridge/app.py",
        "frontend/src/App.tsx",
        "tests/test_x.py",
        "skills/foo/SKILL.md",
        "config/voice.example.yaml",
        "systemd/foo.service",
        "docs/QUBIT-TV.md",
        "Modelfile.qubit.example",
        "scripts/ok.py",
    ):
        assert rel in included, rel

    for rel in (
        ".env",
        "auth.json",
        "server.pem",
        "node_modules/pkg/index.js",
        "frontend/qubit-dist/index.html",
        "qubit_runtime/plugins/foo/dashboard/dist/index.js",
        "tmp/foo.txt",
        "errs/x.txt",
        "PORTED.json",
        "PROGRESS.md",
        "docs/STATUS.md",
        "docs/OPEN_THREADS.md",
        "docs/DECISIONS.md",
        "docs/PLAN.md",
        "docs/NOT-PUBLIC.md",
        "docs/PARITY.md",
        "docs/generated/x.json",
        "docs/archive/x.md",
        "docs/evidence/shot.png",
        "docs/game-office/x.md",
        "DESIGN.md",
        "PRODUCT.md",
        "screenshot.png",
        "e2e-shot.png",
        "config/voice.local.yaml",
        ".opencode/config.json",
        ".claude/helper.md",
        "Modelfile.qubit",
        "qubit_runtime/model.gguf",
        "docs/private.md",
        "qubit_runtime/secret_notes.md",
        "qubit_runtime/link-escape",
        "qubit_runtime/link-parent",
    ):
        assert rel not in included, rel

    assert report["excluded_by_reason"]["personal_path"] >= 1
    assert report["excluded_by_reason"]["secret_like_path"] >= 2
    assert report["excluded_by_reason"]["unsafe_symlink"] == 2
    assert report["included_by_top_level"]["qubit_runtime"] >= 1


def test_check_reports_personal_paths_and_secrets(sample_repo: Path) -> None:
    report = _check_json(sample_repo)
    personal = set(_risk(report, "personal_absolute_path")["files"])
    secrets = set(_risk(report, "secret_like_path")["files"])
    unsafe = set(_risk(report, "unsafe_symlink")["files"])

    assert {"docs/private.md", "qubit_runtime/secret_notes.md"} <= personal
    assert {"qubit_runtime/.env", "qubit_runtime/auth.json", "qubit_runtime/server.pem"} <= secrets
    assert {"qubit_runtime/link-escape", "qubit_runtime/link-parent"} == unsafe
    weights = set(_risk(report, "local_model_weight")["files"])
    assert {"Modelfile.qubit", "qubit_runtime/model.gguf"} <= weights


def test_public_docs_are_an_explicit_allowlist(sample_repo: Path) -> None:
    report = _check_json(sample_repo)
    assert report["public_docs"] == ["docs/QUBIT-TV.md"]
    assert "DESIGN.md" not in report["included_paths"]
    assert "PRODUCT.md" not in report["included_paths"]
    assert "Modelfile.qubit.example" in report["included_paths"]
    assert "Modelfile.qubit" not in report["included_paths"]


def test_report_names_every_product_surface(sample_repo: Path) -> None:
    surfaces = _check_json(sample_repo)["product_surfaces"]
    expected = {
        "runtime",
        "voicebridge",
        "frontend_source",
        "tui_dist",
        "plugin_catalog",
        "published_plugin_catalog",
        "systemd",
        "official_skills",
        "optional_skills",
        "public_docs",
    }
    assert set(surfaces) == expected
    for label, surface in surfaces.items():
        assert surface["present"], label
        assert surface["files"] >= 1, label


def test_export_manifest_summarizes_docs_and_surfaces(
    sample_repo: Path, tmp_path: Path
) -> None:
    dest = tmp_path / "out"
    result = _run(sample_repo, "--dest", str(dest))
    assert result.returncode == 0, result.stderr

    manifest = (dest / "PUBLIC_TREE_MANIFEST.md").read_text(encoding="utf-8")
    assert "## Public docs" in manifest
    assert "`docs/QUBIT-TV.md`" in manifest
    assert "## Root files" in manifest
    assert "- `README.md`" in manifest
    assert "- `DESIGN.md`" not in manifest
    assert "- `PRODUCT.md`" not in manifest
    assert "## Product surfaces" in manifest
    for label in ("runtime", "voicebridge", "tui_dist", "public_docs"):
        assert f"`{label}`: present" in manifest
    assert "## Local model recipe" in manifest
    assert "`Modelfile.qubit.example`" in manifest
    assert "## Packaging" in manifest
    assert "git add -A -f" in manifest
    assert os.path.join("/home", "trick") not in manifest



    before = sorted(p.relative_to(sample_repo) for p in sample_repo.rglob("*"))
    status_before = _git(sample_repo, "status", "--porcelain").stdout

    result = _run(sample_repo, "--check")
    assert result.returncode == 0, result.stderr
    assert "included" in result.stdout

    after = sorted(p.relative_to(sample_repo) for p in sample_repo.rglob("*"))
    status_after = _git(sample_repo, "status", "--porcelain").stdout
    assert before == after
    assert status_before == status_after


def test_export_uses_head_not_working_tree(sample_repo: Path, tmp_path: Path) -> None:
    (sample_repo / "README.md").write_text("WORKTREE\n", encoding="utf-8")
    (sample_repo / "qubit_runtime" / "untracked.py").write_text(
        "print('untracked')\n", encoding="utf-8"
    )

    dest = tmp_path / "out"
    result = _run(sample_repo, "--dest", str(dest))
    assert result.returncode == 0, result.stderr

    assert (dest / "README.md").read_text(encoding="utf-8") == "committed\n"
    assert not (dest / "qubit_runtime" / "untracked.py").exists()
    assert not (dest / "docs" / "STATUS.md").exists()
    assert not (dest / "qubit_runtime" / ".env").exists()
    assert (dest / "qubit_runtime" / "link-ok").is_symlink()
    assert not (dest / "qubit_runtime" / "link-escape").exists()
    assert not (dest / "qubit_runtime" / "link-parent").exists()

    manifest = (dest / "PUBLIC_TREE_MANIFEST.md").read_text(encoding="utf-8")
    commit = _git(sample_repo, "rev-parse", "HEAD").stdout.strip()
    assert commit in manifest
    assert os.path.join("/home", "trick") not in manifest
    assert len(manifest) < 5000


def test_export_is_deterministic(sample_repo: Path, tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    assert _run(sample_repo, "--dest", str(first)).returncode == 0
    assert _run(sample_repo, "--dest", str(second)).returncode == 0

    assert _tree_digest(first) == _tree_digest(second)
    for path in first.rglob("*"):
        if path.is_file():
            assert int(path.stat().st_mtime) == 946684800


def test_fail_on_risk_and_non_empty_dest(sample_repo: Path, tmp_path: Path) -> None:
    result = _run(sample_repo, "--check", "--fail-on-risk")
    assert result.returncode == 1
    assert "personal_absolute_path" in result.stdout

    dest = tmp_path / "occupied"
    dest.mkdir()
    (dest / "keep.txt").write_text("x", encoding="utf-8")
    result = _run(sample_repo, "--dest", str(dest))
    assert result.returncode != 0
    assert "not empty" in result.stderr


def test_export_refuses_destination_inside_checkout(
    sample_repo: Path,
) -> None:
    dest = sample_repo / "public-export"
    result = _run(sample_repo, "--dest", str(dest))
    assert result.returncode != 0
    assert "outside the source checkout" in result.stderr
    assert not dest.exists()
