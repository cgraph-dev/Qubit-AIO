"""Hermetic tests for ``scripts/promote_public_release.py``.

Every test builds a throwaway source repository and, where relevant, a local
bare repository that stands in for the public remote. Nothing here exports the
real Qubit checkout, contacts a network, or pushes to a live repository.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PUBLIC_DOCS = (
    "docs/OFFICE-CLI-COMPLETENESS.md",
    "docs/OFFICE-DEPLOYMENT-CONTRACT.md",
    "docs/ORB_FX_SPEC.md",
    "docs/QUBIT-TV.md",
    "docs/UPDATE.md",
)


def _load():
    path = REPO / "scripts" / "promote_public_release.py"
    spec = importlib.util.spec_from_file_location("promote_public_release", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run(cmd, cwd=None):
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=True,
    )


def _git(repo: Path, *args: str) -> str:
    return _run(["git", "-C", str(repo), *args]).stdout.strip()


def _init_repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "test@qubit.local")
    _git(path, "config", "user.name", "Qubit Test")
    return path


def _write_fixture(repo: Path, body: str = "one") -> None:
    (repo / "voicebridge").mkdir(exist_ok=True)
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'name = "qubit"',
                'version = "0.0.1"',
                "",
                "[tool.hatch.build.targets.wheel]",
                "",
                "[tool.hatch.build.targets.sdist]",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo / "voicebridge" / "__init__.py").write_text(
        '__version__ = "0.0.1"\n', encoding="utf-8"
    )
    (repo / "voicebridge" / "state.txt").write_text(body, encoding="utf-8")
    (repo / "voicebridge" / "webui.py").write_text(
        "\n".join(
            [
                "def _gzip_cache_get(key):",
                "    return None",
                "def _gzip_cache_put(key, body):",
                "    return None",
                "class WebUIHandler:",
                "    def _send(self, code, body, ctype='text/plain',",
                "              extra_headers=None, cache_control='no-store'):",
                "        return None",
                "    def _gzip_if_wanted(self, body, path):",
                "        _gzip_cache_get((body, 0, 0))",
                "        return body, False",
                "    def _serve_qubit_static(self, sub):",
                "        if sub.startswith('assets/'):",
                "            cache_control = 'public, max-age=31536000, immutable'",
                "        return None",
                "    def _serve_qubit_index(self):",
                "        self._send(200, b'shell', 'text/html; charset=utf-8')",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo / "README.md").write_text("# Qubit fixture\n", encoding="utf-8")
    scripts = repo / "scripts"
    scripts.mkdir()
    (scripts / "bootstrap.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (scripts / "bootstrap.ps1").write_text("# windows bootstrap\n", encoding="utf-8")
    (scripts / "install.sh").write_text(
        "#!/usr/bin/env bash\n"
        "# seeds root config.yaml from cli-config.yaml.example\n"
        "exit 0\n",
        encoding="utf-8",
    )
    (scripts / "install.ps1").write_text(
        "# seeds root config.yaml from cli-config.yaml.example\n",
        encoding="utf-8",
    )
    (repo / "qubit_runtime" / "ui-tui" / "dist").mkdir(parents=True)
    (repo / "qubit_runtime" / "ui-tui" / "dist" / "entry.js").write_text(
        "// tui\n", encoding="utf-8"
    )
    (repo / "qubit_runtime" / "cli-config.yaml.example").write_text(
        "model:\n  provider: ollama\n  default: qubit-brain\n"
        "  base_url: http://127.0.0.1:11434\n",
        encoding="utf-8",
    )
    (repo / "qubit_runtime" / "qubit_cli").mkdir(parents=True)
    (repo / "qubit_runtime" / "qubit_cli" / "main_tui_launch.py").write_text(
        "def _find_bundled_tui(*args, **kwargs):\n"
        "    return None\n"
        "def _tui_node_bin(name):\n"
        "    return '/nonexistent/node'\n"
        "def _ensure_tui_workspace(tui_dir):\n"
        "    raise AssertionError('source workspace required')\n"
        "def _tui_need_rebuild(tui_dir):\n"
        "    raise AssertionError('source rebuild required')\n"
        "def _make_tui_argv(tui_dir, tui_dev):\n"
        "    return ['/nonexistent/node', '--expose-gc', "
        "str(tui_dir / 'dist' / 'entry.js')], tui_dir\n",
        encoding="utf-8",
    )
    for doc in PUBLIC_DOCS:
        (repo / doc).write_text("public doc\n", encoding="utf-8")
    (repo / "docs" / "INTERNAL.md").write_text("internal\n", encoding="utf-8")


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A", "-f")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _source_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = _init_repo(tmp_path / "source")
    _write_fixture(repo, "one")
    first = _commit(repo, "first")
    (repo / "voicebridge" / "state.txt").write_text("two", encoding="utf-8")
    second = _commit(repo, "second")
    return repo, first, second


def _bare_remote(tmp_path: Path) -> Path:
    bare = tmp_path / "public.git"
    _run(["git", "init", "-q", "--bare", str(bare)])
    return bare


def _promote(module, *args: str) -> int:
    return module.main(list(args))


def test_dry_run_exports_the_chosen_commit_and_reports(tmp_path: Path):
    module = _load()
    repo, first, second = _source_repo(tmp_path)
    dest = tmp_path / "seed"
    report = tmp_path / "report.md"

    assert _promote(
        module,
        "--repo", str(repo),
        "--source-commit", first,
        "--dest", str(dest),
        "--report", str(report),
    ) == 0

    # Bytes come from the requested commit, not HEAD.
    assert (dest / "voicebridge" / "state.txt").read_text() == "one"
    assert first != second
    assert _git(repo, "rev-parse", "HEAD") == second

    data = json.loads(report.with_suffix(".json").read_text())
    assert data["source"]["commit"] == first
    assert data["seed"]["commit"] != first
    assert data["seed"]["version"] == "0.0.1"
    assert data["seed"]["tag"] == "v0.0.1"
    assert data["applied"] is False
    assert data["remote"]["current_commit"] is None
    assert data["changed_paths"] == []
    assert data["export"]["public_docs"] == sorted(PUBLIC_DOCS)

    # The seed is a clean, independent one-commit repository.
    assert _git(dest, "rev-list", "--all", "--count") == "1"
    assert _git(dest, "tag") == "v0.0.1"
    assert _git(dest, "remote") == ""
    assert _git(dest, "status", "--porcelain") == ""
    assert not (dest / "docs" / "INTERNAL.md").exists()
    assert (dest / "PUBLIC_TREE_MANIFEST.md").is_file()
    assert (dest / "VERSION").read_text().strip() == "0.0.1"
    assert dest.name in report.read_text()


def test_personal_paths_models_and_internal_docs_are_excluded(tmp_path: Path):
    module = _load()
    repo = _init_repo(tmp_path / "source")
    _write_fixture(repo)
    (repo / "voicebridge" / "leak.txt").write_text(
        "/home/" + "trick/private\n", encoding="utf-8"
    )
    (repo / "voicebridge" / "weights.gguf").write_text("model", encoding="utf-8")
    (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")
    first = _commit(repo, "leaky")

    dest = tmp_path / "seed"
    report = tmp_path / "report.md"
    assert _promote(
        module,
        "--repo", str(repo),
        "--source-commit", first,
        "--dest", str(dest),
        "--report", str(report),
    ) == 0

    data = json.loads(report.with_suffix(".json").read_text())
    reasons = data["export"]["excluded_by_reason"]
    assert reasons.get("personal_path", 0) >= 1
    assert reasons.get("local_model_weight", 0) >= 1
    assert not (dest / "voicebridge" / "leak.txt").exists()
    assert not (dest / "voicebridge" / "weights.gguf").exists()
    assert not (dest / ".env").exists()
    assert not (dest / "docs" / "INTERNAL.md").exists()
    # No exported byte carries the operator path marker.
    for path in dest.rglob("*"):
        if path.is_file() and not path.is_symlink():
            assert b"/home/" + b"trick" not in path.read_bytes(), path


def test_apply_requires_a_remote_and_refuses_main(tmp_path: Path):
    module = _load()
    repo, first, _ = _source_repo(tmp_path)
    bare = _bare_remote(tmp_path)

    try:
        _promote(
            module,
            "--repo", str(repo),
            "--source-commit", first,
            "--dest", str(tmp_path / "seed-a"),
            "--apply",
        )
    except SystemExit as exc:
        assert "PROMOTE_REMOTE_REQUIRED" in str(exc)
    else:  # pragma: no cover - the assertion above must trip
        raise AssertionError("--apply without --remote-url was accepted")

    try:
        _promote(
            module,
            "--repo", str(repo),
            "--source-commit", first,
            "--dest", str(tmp_path / "seed-b"),
            "--apply",
            "--remote-url", str(bare),
            "--branch", "main",
        )
    except SystemExit as exc:
        assert "PROMOTE_BRANCH_PROTECTED" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("main was accepted as a promotion branch")

    assert _git(bare, "for-each-ref", "--format=%(refname)") == ""


def test_apply_pushes_production_with_force_with_lease_and_tags(tmp_path: Path):
    module = _load()
    repo, first, second = _source_repo(tmp_path)
    bare = _bare_remote(tmp_path)

    first_dest = tmp_path / "seed-1"
    assert _promote(
        module,
        "--repo", str(repo),
        "--source-commit", first,
        "--dest", str(first_dest),
        "--apply",
        "--remote-url", str(bare),
        "--push-tag",
        "--report", str(tmp_path / "report-1.md"),
    ) == 0
    assert _git(bare, "rev-parse", "refs/heads/production") == json.loads(
        (tmp_path / "report-1.json").read_text()
    )["seed"]["commit"]
    assert _git(bare, "for-each-ref", "--format=%(refname:short)", "refs/heads") == "production"
    assert _git(bare, "rev-parse", "refs/tags/v0.0.1^{}") == json.loads(
        (tmp_path / "report-1.json").read_text()
    )["seed"]["commit"]
    assert _git(bare, "show", "refs/heads/production:voicebridge/state.txt") == "one"

    # The seed has unrelated history, so a second update is only possible with
    # force-with-lease. This is the regression guard for the push flag.
    second_dest = tmp_path / "seed-2"
    assert _promote(
        module,
        "--repo", str(repo),
        "--source-commit", second,
        "--dest", str(second_dest),
        "--apply",
        "--remote-url", str(bare),
        "--report", str(tmp_path / "report-2.md"),
    ) == 0
    assert _git(bare, "show", "refs/heads/production:voicebridge/state.txt") == "two"
    data = json.loads((tmp_path / "report-2.json").read_text())
    assert data["applied"] is True
    assert data["remote"]["force_with_lease"] is True
    assert {"path": "voicebridge/state.txt", "status": "M"} in data["changed_paths"]


def test_dry_run_reports_changed_paths_without_writing_the_remote(tmp_path: Path):
    module = _load()
    repo, first, second = _source_repo(tmp_path)
    bare = _bare_remote(tmp_path)

    dry_dest = tmp_path / "seed-dry"
    report = tmp_path / "dry.md"
    assert _promote(
        module,
        "--repo", str(repo),
        "--source-commit", second,
        "--dest", str(dry_dest),
        "--remote-url", str(bare),
        "--report", str(report),
    ) == 0
    data = json.loads(report.with_suffix(".json").read_text())
    # No production branch yet: every seed path is reported as added.
    assert data["changed_paths"]
    assert all(row["status"] == "A" for row in data["changed_paths"])
    assert _git(bare, "for-each-ref", "--format=%(refname)") == ""

    # Seed the remote, then a dry-run must show the file change but not push it.
    assert _promote(
        module,
        "--repo", str(repo),
        "--source-commit", first,
        "--dest", str(tmp_path / "seed-base"),
        "--apply",
        "--remote-url", str(bare),
        "--report", str(tmp_path / "base.md"),
    ) == 0
    before = _git(bare, "rev-parse", "refs/heads/production")

    report2 = tmp_path / "changed.md"
    assert _promote(
        module,
        "--repo", str(repo),
        "--source-commit", second,
        "--dest", str(tmp_path / "seed-changed"),
        "--remote-url", str(bare),
        "--report", str(report2),
    ) == 0
    data2 = json.loads(report2.with_suffix(".json").read_text())
    assert data2["remote"]["current_commit"] == before
    assert {"path": "voicebridge/state.txt", "status": "M"} in data2["changed_paths"]
    assert data2["applied"] is False
    assert _git(bare, "rev-parse", "refs/heads/production") == before


def test_version_mismatch_is_refused_before_a_seed_commit(tmp_path: Path):
    module = _load()
    repo, first, _ = _source_repo(tmp_path)
    dest = tmp_path / "seed"
    try:
        _promote(
            module,
            "--repo", str(repo),
            "--source-commit", first,
            "--dest", str(dest),
            "--version", "9.9.9",
            "--report", str(tmp_path / "report.md"),
        )
    except SystemExit as exc:
        assert "SEED_VERIFY_FAILED" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a version mismatch was accepted")
    assert not (dest / ".git").exists()


def test_existing_release_tag_is_never_rewritten(tmp_path: Path):
    module = _load()
    repo, first, second = _source_repo(tmp_path)
    bare = _bare_remote(tmp_path)

    assert _promote(
        module,
        "--repo", str(repo),
        "--source-commit", first,
        "--dest", str(tmp_path / "seed-tag-1"),
        "--apply",
        "--remote-url", str(bare),
        "--push-tag",
        "--report", str(tmp_path / "tag-1.md"),
    ) == 0
    production = _git(bare, "rev-parse", "refs/heads/production")
    tag_commit = _git(bare, "rev-parse", "refs/tags/v0.0.1^{}")

    try:
        _promote(
            module,
            "--repo", str(repo),
            "--source-commit", second,
            "--dest", str(tmp_path / "seed-tag-2"),
            "--apply",
            "--remote-url", str(bare),
            "--push-tag",
            "--report", str(tmp_path / "tag-2.md"),
        )
    except SystemExit as exc:
        assert "PROMOTE_TAG_EXISTS" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("an existing release tag was rewritten")

    # The tag refusal happens before the branch push, so production is unchanged.
    assert _git(bare, "rev-parse", "refs/heads/production") == production
    assert _git(bare, "rev-parse", "refs/tags/v0.0.1^{}") == tag_commit
    # The failed run still leaves the dry-run evidence report behind.
    assert (tmp_path / "tag-2.json").is_file()


def test_the_same_source_commit_produces_the_same_seed_and_checksum(tmp_path: Path):
    module = _load()
    repo, first, _ = _source_repo(tmp_path)
    reports = []
    for name in ("one", "two"):
        report = tmp_path / ("%s.md" % name)
        assert _promote(
            module,
            "--repo", str(repo),
            "--source-commit", first,
            "--dest", str(tmp_path / ("seed-" + name)),
            "--report", str(report),
        ) == 0
        reports.append(json.loads(report.with_suffix(".json").read_text()))
    assert reports[0]["seed"]["commit"] == reports[1]["seed"]["commit"]
    assert reports[0]["seed"]["tree"] == reports[1]["seed"]["tree"]
    assert (
        reports[0]["checksums"]["archive_sha256"]
        == reports[1]["checksums"]["archive_sha256"]
    )
