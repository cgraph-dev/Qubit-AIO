"""Public catalog/install/update URLs target the public Qubit repository.

The retired private ``cgraph-dev/Qubit-AIO`` repository is not reachable for a
public user. These tests pin every runtime URL that a clean install uses for
catalog refresh, installer/update source resolution, or the update channel to
``cgraph-dev/Qubit`` on its default ``production`` branch, with the website
assets living under ``qubit_runtime/website/static/api``.

Hermetic: this asserts resolved string constants only; no network, no service,
no checkout.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "qubit_runtime"))
sys.path.insert(0, str(REPO))

PUBLIC_REPO = "cgraph-dev/Qubit"
RETIRED_REPO = "cgraph-dev/Qubit-AIO"
RAW_PREFIX = "https://raw.githubusercontent.com/cgraph-dev/Qubit/production/"
REPO_BASE = "https://github.com/cgraph-dev/Qubit"
API_ASSETS = "qubit_runtime/website/static/api"


class _Cfg:
    """Minimal nested-dict config implementing ``.get(*path, default=)``."""

    def __init__(self, data=None):
        self._d = data or {}

    def get(self, *path, default=None):
        node = self._d
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


def test_install_source_targets_the_public_repository_and_production():
    from qubit_cli import install_source as s

    assert s.DEFAULT_REPOSITORY == PUBLIC_REPO
    assert s.official_repository() == PUBLIC_REPO
    assert s.branch() == "production"
    assert s.base_url() == REPO_BASE
    assert s.install_sh_url() == f"{REPO_BASE}/raw/production/scripts/install.sh"
    assert s.install_ps1_url() == f"{REPO_BASE}/raw/production/scripts/install.ps1"
    assert s.releases_base_url() == f"{REPO_BASE}/releases"
    assert s.docs_base_url() == f"{REPO_BASE}/tree/production/docs"
    assert s.release_assets_base_url() == REPO_BASE

    urls = {
        s.base_url(), s.install_sh_url(), s.install_ps1_url(),
        s.releases_base_url(), s.docs_base_url(), s.official_repo_url(),
        s.release_assets_base_url(), s.raw_asset_url(f"{API_ASSETS}/model-catalog.json"),
    }
    assert all(RETIRED_REPO not in url for url in urls)
    assert all("github" in url or "raw.githubusercontent" in url for url in urls)
    assert all(PUBLIC_REPO in url for url in urls)


def test_public_catalog_fetches_use_the_runtime_website_paths():
    from qubit_cli import model_catalog, plugin_catalog
    from qubit_cli.local_runtime import catalog as runtime_catalog
    from tools import skills_hub_search

    assert model_catalog.DEFAULT_CATALOG_URL == (
        f"{RAW_PREFIX}{API_ASSETS}/model-catalog.json")
    assert model_catalog.DEFAULT_CATALOG_FALLBACK_URLS == (
        f"{RAW_PREFIX}{API_ASSETS}/model-catalog.json",)
    assert plugin_catalog.LIVE_CATALOG_PATH == (
        f"{API_ASSETS}/plugin-catalog.json")
    assert plugin_catalog.LIVE_CATALOG_URL == (
        f"{RAW_PREFIX}{API_ASSETS}/plugin-catalog.json")
    assert runtime_catalog._CATALOG_URL == (
        f"{RAW_PREFIX}qubit_runtime/qubit_cli/local_runtime/catalog.json")
    assert skills_hub_search.QUBIT_INDEX_URL == (
        f"{RAW_PREFIX}{API_ASSETS}/skills-index.json")

    urls = (
        model_catalog.DEFAULT_CATALOG_URL,
        *model_catalog.DEFAULT_CATALOG_FALLBACK_URLS,
        plugin_catalog.LIVE_CATALOG_URL,
        runtime_catalog._CATALOG_URL,
        skills_hub_search.QUBIT_INDEX_URL,
    )
    assert all(RETIRED_REPO not in url for url in urls)
    assert all(url.startswith(RAW_PREFIX) for url in urls)


def test_update_channel_canonical_source_is_the_public_repository():
    from qubit_cli import source_releases
    from voicebridge import update_channel as UC

    assert UC.QUBIT_REPOSITORY == PUBLIC_REPO
    assert UC.QUBIT_UPDATE_URL == f"{REPO_BASE}.git"
    assert UC.QUBIT_REPOSITORY_URL == f"{REPO_BASE}.git"
    assert UC.DEFAULT_BRANCH == "production"
    assert source_releases.OFFICIAL_REPOSITORY == PUBLIC_REPO
    assert source_releases._PUBLIC_BASE == REPO_BASE

    out = UC.resolve_source(_Cfg({}), remote_url_lookup=lambda _name: None)
    assert out["ok"] is True
    assert out["source_kind"] == "canonical"
    assert out["url"] == f"{REPO_BASE}.git"
    assert out["branch"] == "production"
    assert RETIRED_REPO not in out["url"]

    # Read-old: an existing checkout whose origin is the retired private repo
    # is still recognised as Qubit-owned, but the canonical destination never
    # points there.
    assert UC._points_at_qubit_repo(f"https://github.com/{RETIRED_REPO}.git") is True
    assert UC._points_at_qubit_repo(out["url"]) is True


def test_in_scope_runtime_modules_have_no_retired_github_url():
    """Only the explicit read-old identifier may mention the retired repo."""
    files = (
        "qubit_runtime/qubit_cli/install_source.py",
        "qubit_runtime/qubit_cli/model_catalog.py",
        "qubit_runtime/qubit_cli/plugin_catalog.py",
        "qubit_runtime/qubit_cli/local_runtime/catalog.py",
        "qubit_runtime/tools/skills_hub_search.py",
        "qubit_runtime/qubit_constants.py",
        "qubit_runtime/setup.py",
        "voicebridge/update_channel.py",
    )
    for rel in files:
        text = (REPO / rel).read_text(encoding="utf-8")
        assert f"https://github.com/{RETIRED_REPO}" not in text, rel
        assert f"raw.githubusercontent.com/{RETIRED_REPO}" not in text, rel
