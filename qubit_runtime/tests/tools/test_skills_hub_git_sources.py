"""Hermes / OpenClaw git skill sources (local repos only)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.skills_hub_git_sources import HermesGitSource, OpenClawGitSource


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "origin"
    repo.mkdir()
    _git(repo, "init", "-q")
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "init")
    return repo


def _hermes_repo(tmp_path: Path) -> Path:
    return _repo(tmp_path, {
        "skills/research/DESCRIPTION.md": "category notes",
        "skills/research/demo/SKILL.md": "---\nname: demo\ndescription: Demo skill.\n---\n# demo\n",
        "skills/research/demo/references/notes.md": "notes\n",
        "skills/other/demo/SKILL.md": "---\nname: demo\ndescription: Duplicate name.\n---\n",
    })


def test_hermes_search_inspect_fetch(tmp_path):
    origin = _hermes_repo(tmp_path)
    src = HermesGitSource(cache_root=tmp_path / "cache", repo_url=str(origin))
    src.sync()

    found = src.search("research", limit=5)
    assert [m.name for m in found] == ["demo"]
    assert found[0].identifier == "hermes:demo"
    assert found[0].extra["category"] == "research"
    assert found[0].extra["commit"] == src.current_revision("hermes:demo")

    meta = src.inspect("hermes:research/demo")
    assert meta and meta.description == "Demo skill."

    bundle = src.fetch("hermes:research/demo")
    assert bundle is not None
    assert bundle.files["SKILL.md"].startswith("---")
    assert bundle.files["references/notes.md"] == "notes\n"
    assert "DESCRIPTION.md" not in bundle.files
    assert bundle.metadata["commit"] == src.current_revision()


def test_ambiguous_bare_name_is_refused(tmp_path):
    origin = _hermes_repo(tmp_path)
    src = HermesGitSource(cache_root=tmp_path / "cache", repo_url=str(origin))
    src.sync()
    with pytest.raises(ValueError, match="ambiguous"):
        src.inspect("hermes:demo")


def test_search_does_not_clone_then_prepare_does(tmp_path):
    origin = _hermes_repo(tmp_path)
    src = HermesGitSource(cache_root=tmp_path / "cache", repo_url=str(origin))
    assert src.search("demo") == []  # no cache, no clone
    assert not (tmp_path / "cache").exists()
    assert src.prepare() is True
    assert src.search("research")


def test_openclaw_owner_layout_and_sidecar(tmp_path):
    origin = _repo(tmp_path, {
        "skills/acme/demo/SKILL.md": "---\nname: demo\ndescription: OpenClaw demo.\n---\n",
        "skills/acme/demo/_meta.json": '{"downloads": 7}',
        "skills/acme/demo/scripts/run.sh": "echo hi\n",
    })
    src = OpenClawGitSource(cache_root=tmp_path / "cache", repo_url=str(origin))
    src.sync()

    meta = src.inspect("openclaw:acme/demo")
    assert meta and meta.extra["category"] == "acme"
    bundle = src.fetch("openclaw:acme/demo")
    assert bundle is not None
    assert "_meta.json" not in bundle.files
    assert bundle.metadata["sidecar"] == {"downloads": 7}
    assert bundle.files["scripts/run.sh"] == "echo hi\n"


def test_pull_failure_keeps_cache_and_warns(tmp_path):
    origin = _hermes_repo(tmp_path)
    src = HermesGitSource(cache_root=tmp_path / "cache", repo_url=str(origin))
    src.sync()
    assert src.search("research")

    _git(src.cache_dir(), "remote", "set-url", "origin", str(tmp_path / "gone"))
    src.sync()

    assert src.search("research")
    assert "stale" in src.refresh_warning


def test_search_matches_partial_names_and_empty_query_returns_all(tmp_path):
    origin = _repo(tmp_path, {
        "skills/research/alpha/SKILL.md": "---\nname: alpha\ndescription: First.\n---\n",
        "skills/research/beta/SKILL.md": "---\nname: beta\ndescription: Second.\n---\n",
        "skills/research/no-skill/notes.md": "not a skill\n",
    })
    src = HermesGitSource(cache_root=tmp_path / "cache", repo_url=str(origin))
    src.sync()

    assert [m.name for m in src.search("")] == ["alpha", "beta"]
    assert [m.name for m in src.search("bet")] == ["beta"]
    assert [m.name for m in src.search("research")] == ["alpha", "beta"]
    assert src.search("notes") == []


def test_frontmatter_absent_falls_back_to_the_directory_name(tmp_path):
    origin = _repo(tmp_path, {
        "skills/research/plain/SKILL.md": "Just a body, no frontmatter.\n",
    })
    src = HermesGitSource(cache_root=tmp_path / "cache", repo_url=str(origin))
    src.sync()

    found = src.search("plain")
    assert [m.name for m in found] == ["plain"]
    assert found[0].description == ""
    bundle = src.fetch("hermes:research/plain")
    assert bundle is not None
    assert bundle.files["SKILL.md"] == "Just a body, no frontmatter.\n"


def test_initial_clone_failure_is_fatal_but_prepare_is_safe(tmp_path):
    src = HermesGitSource(
        cache_root=tmp_path / "cache", repo_url=str(tmp_path / "does-not-exist"))
    with pytest.raises(subprocess.CalledProcessError):
        src.sync()
    assert src.refresh_warning == ""
    assert src.prepare() is False
    assert src.search("demo") == []


def test_empty_cache_does_not_hide_a_failed_refresh(tmp_path):
    cache = tmp_path / "cache"
    (cache / ".git").mkdir(parents=True)  # looks cloned, but holds no skills
    src = HermesGitSource(cache_root=cache, repo_url=str(tmp_path / "unused"))

    with pytest.raises(RuntimeError, match="cache is empty"):
        src.sync()
    assert src.refresh_warning == ""


def test_successful_refresh_clears_the_stale_warning(tmp_path):
    origin = _hermes_repo(tmp_path)
    src = HermesGitSource(cache_root=tmp_path / "cache", repo_url=str(origin))
    src.sync()

    _git(src.cache_dir(), "remote", "set-url", "origin", str(tmp_path / "gone"))
    src.sync()
    assert "stale" in src.refresh_warning

    _git(src.cache_dir(), "remote", "set-url", "origin", str(origin))
    src.sync()
    assert src.refresh_warning == ""
    assert src.search("research")


def test_failed_refresh_still_serves_the_cached_bundle_and_commit(tmp_path):
    origin = _hermes_repo(tmp_path)
    src = HermesGitSource(cache_root=tmp_path / "cache", repo_url=str(origin))
    src.sync()
    commit = src.current_revision()

    _git(src.cache_dir(), "remote", "set-url", "origin", str(tmp_path / "gone"))
    src.sync()

    bundle = src.fetch("hermes:research/demo")
    assert bundle is not None
    assert bundle.metadata["commit"] == commit
    assert "stale" in src.refresh_warning


def test_cache_cannot_satisfy_a_missing_skill(tmp_path):
    origin = _hermes_repo(tmp_path)
    src = HermesGitSource(cache_root=tmp_path / "cache", repo_url=str(origin))
    src.sync()

    assert src.inspect("hermes:research/missing") is None
    assert src.fetch("hermes:research/missing") is None

    _git(src.cache_dir(), "remote", "set-url", "origin", str(tmp_path / "gone"))
    src.sync()
    assert src.inspect("hermes:research/missing") is None
    assert src.fetch("hermes:research/missing") is None


def test_router_registers_both_sources_and_keeps_them_out_of_all():
    from tools.skills_hub_search import _select_active_sources, create_source_router

    sources = create_source_router()
    ids = {s.source_id() for s in sources}
    assert {"hermes", "openclaw"} <= ids
    all_ids = {s.source_id() for s in _select_active_sources(sources, "all")}
    assert not ({"hermes", "openclaw"} & all_ids)
    explicit = {s.source_id() for s in _select_active_sources(sources, "hermes")}
    assert "hermes" in explicit and "official" in explicit


def test_cli_offers_both_sources():
    from qubit_cli.subcommands.skills import _SOURCE_CHOICES
    assert {"hermes", "openclaw"} <= set(_SOURCE_CHOICES)
