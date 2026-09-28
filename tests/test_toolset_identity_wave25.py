"""Wave 25 — Qubit toolset/platform identity.

The vendored registry shipped its platform bundles and defaults under the
upstream ``hermes-*`` spelling. Wave 25 makes ``qubit-*`` the canonical,
user-visible identity while keeping ``hermes-*`` as a read-old alias (existing
configs, the dashboard overlay and the plugin-platform registry name it), so a
lookup of either spelling resolves the same tool list.

Hermetic: throwaway ``HOME``/``XDG_*``/``QUBIT_HOME`` only; no network, no live
services, no reference-home reads.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Throwaway HOME/XDG/QUBIT_HOME so nothing here resolves the live install."""
    for name in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME",
                 "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qhome"))
    (tmp_path / "qhome").mkdir(parents=True, exist_ok=True)
    yield tmp_path


@pytest.fixture
def runtime():
    """Import the vendored runtime WITHOUT applying the dashboard overlay, so
    this proves the canonical ``qubit-*`` keys live in ``toolsets.py`` itself."""
    from voicebridge import qubit_runtime

    qubit_runtime.ensure_path()
    import toolsets  # noqa: F401  (import after the path is set)

    return toolsets


# --------------------------------------------------------------------------- #
# 1. Three-way resolution: canonical spelling, capital-Q rebrand artifact, and
#    the read-old upstream spelling all resolve one non-empty tool list.
# --------------------------------------------------------------------------- #
def test_three_way_resolution_is_identical_and_non_empty(runtime):
    canonical = runtime.resolve_toolset("qubit-cli")
    capital_q = runtime.resolve_toolset("Qubit-cli")
    read_old = runtime.resolve_toolset("hermes-cli")

    assert canonical, "qubit-cli resolved to zero tools"
    assert canonical == capital_q == read_old, (
        "qubit-cli / Qubit-cli / hermes-cli must resolve the same tool list")


def test_get_toolset_resolves_to_same_bundle(runtime):
    canonical = runtime.get_toolset("qubit-cli", include_registry=False)
    read_old = runtime.get_toolset("hermes-cli", include_registry=False)
    assert canonical is not None and read_old is not None
    # The canonical spelling and the read-old spelling resolve identical tools.
    assert runtime.resolve_toolset("qubit-cli", include_registry=False) == \
        runtime.resolve_toolset("hermes-cli", include_registry=False)


# --------------------------------------------------------------------------- #
# 2. `hermes-*` keys are preserved (read-old) and every one has a `qubit-*`
#    canonical counterpart resolving to the same tools.
# --------------------------------------------------------------------------- #
def test_hermes_keys_are_preserved_and_have_qubit_counterparts(runtime):
    hermes_keys = {k for k in runtime.TOOLSETS if k.startswith("hermes-")}
    assert hermes_keys, "the upstream hermes-* keys must not be deleted"
    for legacy in hermes_keys:
        canonical = "qubit-" + legacy[len("hermes-"):]
        assert canonical in runtime.TOOLSETS, f"missing canonical alias {canonical!r}"
        assert runtime.resolve_toolset(canonical, include_registry=False) == \
            runtime.resolve_toolset(legacy, include_registry=False)


def test_validate_accepts_all_three_spellings(runtime):
    for name in ("qubit-cli", "Qubit-cli", "hermes-cli"):
        assert runtime.validate_toolset(name), f"{name!r} not a valid toolset"


# --------------------------------------------------------------------------- #
# 3. Defaults are the Qubit-owned spelling.
# --------------------------------------------------------------------------- #
def test_config_default_toolset_is_qubit_cli(runtime):
    import qubit_cli.config_defaults as cd

    assert cd.DEFAULT_CONFIG["toolsets"] == ["qubit-cli"]


def test_platform_defaults_use_qubit_spelling(runtime):
    from qubit_cli import platforms as platforms_mod

    assert platforms_mod.PLATFORMS["cli"].default_toolset == "qubit-cli"
    builtin_defaults = {info.default_toolset for info in platforms_mod.PLATFORMS.values()}
    leaked = {name for name in builtin_defaults if name.startswith("hermes-")}
    assert not leaked, f"builtin platform defaults still carry upstream spelling: {sorted(leaked)}"


def test_platform_default_resolves_real_tools(runtime):
    from qubit_cli import platforms as platforms_mod

    for platform, info in platforms_mod.PLATFORMS.items():
        assert info.default_toolset.startswith("qubit-"), (platform, info.default_toolset)
        assert runtime.resolve_toolset(info.default_toolset), (
            f"{platform} default {info.default_toolset!r} resolves to zero tools")


# --------------------------------------------------------------------------- #
# 4. Plugin-platform bundles resolve under either spelling.
# --------------------------------------------------------------------------- #
def test_plugin_platform_bundle_accepts_both_spellings(runtime):
    # `qubit-telegram` is a static bundle (hermes-telegram counterpart); the
    # resolver must treat the canonical spelling identically.
    assert runtime.resolve_toolset("qubit-telegram") == runtime.resolve_toolset("hermes-telegram")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
