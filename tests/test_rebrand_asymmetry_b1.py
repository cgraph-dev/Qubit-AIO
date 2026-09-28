"""B1 — rebrand asymmetry regression tests.

Each test pins one place where the Qubit rebrand renamed a symbol on one side of
a read/write (or name resolution) pair but not the other, so the code stopped
resolving the Qubit name. The tests fail on the pre-fix tree and pass after the
fix.

Hermetic: a throwaway HOME/QUBIT_HOME under the test's tmp dir; no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "qubit_runtime"))


@pytest.fixture(autouse=True)
def _b1_throwaway_home(tmp_path, monkeypatch):
    """Pin HOME/QUBIT_HOME under the test tmp dir.

    The suite exercises home resolution; without this a module that falls back to
    the platform default could create a real ``~/.hermes``. ``QUBIT_HOME`` is
    re-set here after the shared ``tests/conftest.py`` fixture clears it.
    """
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QUBIT_HOME", str(home / ".qubit"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))


# ---------------------------------------------------------------- skill metadata
def test_skill_utils_reads_metadata_qubit():
    from agent.skill_utils import _qubit_metadata, extract_skill_conditions

    fm = {"metadata": {"qubit": {"tags": ["a", "b"], "related_skills": ["x"]}}}
    assert _qubit_metadata(fm) == {"tags": ["a", "b"], "related_skills": ["x"]}
    assert extract_skill_conditions(fm)["session_platforms"] == []


def test_skill_utils_still_reads_legacy_metadata_hermes():
    from agent.skill_utils import _qubit_metadata

    fm = {"metadata": {"hermes": {"tags": ["legacy"]}}}
    assert _qubit_metadata(fm) == {"tags": ["legacy"]}


def test_learning_graph_reads_metadata_qubit():
    from agent.learning_graph import _fm_field

    fm = {"metadata": {"qubit": {"related_skills": ["one"]}}}
    assert _fm_field(fm, "related_skills") == ["one"]


def test_blueprints_parse_qubit_metadata_and_export_qubit_key():
    from tools.blueprints import export_blueprint, parse_blueprint

    skill = (
        "---\nname: demo\nmetadata:\n  qubit:\n    blueprint:\n"
        "      schedule: '0 9 * * *'\n---\n\nBody.\n"
    )
    spec = parse_blueprint(skill)
    assert spec is not None and spec.schedule == "0 9 * * *"
    rendered = export_blueprint({"name": "demo", "schedule_display": "0 9 * * *"}, "Body line")
    assert "qubit:" in rendered and "blueprint:" in rendered


def test_skill_linter_reads_metadata_qubit():
    from tools.skill_linter import lint_content

    ok = (
        "---\nname: demo\ndescription: A demo skill\nversion: 1.0.0\n"
        "author: Someone\nlicense: MIT\n"
        "metadata:\n  qubit:\n    tags: [demo]\n---\nbody\n"
    )
    rule_ids = {f.rule for f in lint_content(ok) if "metadata" in f.rule}
    assert "missing-metadata" not in rule_ids


def test_skills_hub_models_reads_metadata_qubit_tags():
    from tools.skills_hub_models import _qubit_tags

    assert _qubit_tags({"metadata": {"qubit": {"tags": ["t1"]}}}) == ["t1"]


def test_skills_hub_official_upstream_pointer_reads_metadata_qubit():
    from tools.skills_hub_official import OptionalSkillSource

    content = (
        "---\nname: demo\nmetadata:\n  qubit:\n    upstream:\n"
        "      repo: owner/name\n      path: skills/demo\n---\nbody\n"
    )
    src = OptionalSkillSource.__new__(OptionalSkillSource)
    pointer = OptionalSkillSource._upstream_pointer_from_content(src, content)
    assert pointer == {"repo": "owner/name", "path": "skills/demo"}


# ---------------------------------------------------------------- executable name
def test_qubit_state_holders_recognizes_qubit_console_scripts():
    from qubit_state_holders import _QUBIT_EXECUTABLES, _looks_like_qubit

    assert {"qubit", "qubit-agent"} <= set(_QUBIT_EXECUTABLES)
    assert _looks_like_qubit(["qubit", "gateway", "run"]) is True
    assert _looks_like_qubit(["hermes", "gateway", "run"]) is True  # read-old


def test_gateway_command_subcommand_recognizes_qubit_binary():
    from gateway.status import _gateway_command_subcommand

    assert _gateway_command_subcommand("qubit gateway run") == "run"
    assert _gateway_command_subcommand("hermes gateway run") == "run"  # read-old


def test_resolve_qubit_bin_prefers_qubit(monkeypatch, tmp_path):
    import importlib.util
    import shutil

    from gateway.run import _resolve_qubit_bin

    fake = tmp_path / "qubit"
    fake.write_text("#!/bin/sh\n")
    real_which = shutil.which

    def _which(name, *a, **k):
        return str(fake) if name == "qubit" else None

    # v0.21.5 hardens the resolver to prefer the running interpreter's module argv
    # (#111569); force the PATH fallback so this pins the Qubit-before-Hermes name
    # preference exactly as the asymmetry requires.
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(shutil, "which", _which)
    try:
        assert _resolve_qubit_bin() == [str(fake)]
    finally:
        monkeypatch.setattr(shutil, "which", real_which)


# ---------------------------------------------------------------- default home
def test_platform_default_home_is_qubit_owned():
    from qubit_constants import _get_platform_default_qubit_home

    home = str(_get_platform_default_qubit_home())
    assert home.endswith(".qubit") or home.endswith("qubit")


def test_startup_watchdog_default_home_is_qubit_owned(monkeypatch):
    from qubit_startup_watchdog import _process_qubit_home

    monkeypatch.delenv("QUBIT_HOME", raising=False)
    home = str(_process_qubit_home())
    assert not home.endswith(".hermes") and not home.endswith("/hermes")
    assert home.endswith(".qubit") or home.endswith("/qubit")


def test_node_fallback_uses_qubit_home(monkeypatch, tmp_path):
    import os

    from tools.mcp_tool_config import _node_fallback

    monkeypatch.delenv("QUBIT_HOME", raising=False)
    real_expanduser = os.path.expanduser
    monkeypatch.setattr(
        os.path, "expanduser",
        lambda p: str(tmp_path) if p == "~" else real_expanduser(p))
    target = tmp_path / ".qubit" / "node" / "bin" / "npx"
    target.parent.mkdir(parents=True)
    target.write_text("#!/bin/sh\n")
    target.chmod(0o755)
    assert _node_fallback("npx") == str(target)


def test_env_loader_default_home_is_qubit_owned():
    import inspect

    from qubit_cli import env_loader

    assert 'Path.home() / ".qubit"' in inspect.getsource(env_loader.load_qubit_dotenv)
    assert 'Path.home() / ".hermes"' not in inspect.getsource(env_loader.load_qubit_dotenv)


def test_providers_scan_reads_qubit_entry_point_group():
    import inspect

    from providers import _discover_entry_point_providers

    src = inspect.getsource(_discover_entry_point_providers)
    assert '("qubit_agent.plugins", "hermes_agent.plugins")' in src


def test_mcp_serve_default_home_is_qubit_owned(monkeypatch):
    import qubit_constants

    monkeypatch.delenv("QUBIT_HOME", raising=False)
    monkeypatch.setattr(
        qubit_constants, "get_qubit_home",
        lambda: (_ for _ in ()).throw(ImportError("forced")))
    import mcp_serve

    assert str(mcp_serve._qubit_home()).endswith(".qubit")


# ---------------------------------------------------------------- env aliasing
def test_env_var_enabled_honors_qubit_alias(monkeypatch):
    from utils import env_var_alias, env_var_enabled

    monkeypatch.setenv("QUBIT_TEST_FLAG", "1")
    monkeypatch.delenv("HERMES_TEST_FLAG", raising=False)
    assert env_var_alias("HERMES_TEST_FLAG") == "QUBIT_TEST_FLAG"
    assert env_var_enabled("HERMES_TEST_FLAG") is True

    monkeypatch.delenv("QUBIT_TEST_FLAG", raising=False)
    monkeypatch.setenv("HERMES_TEST_FLAG", "on")
    assert env_var_enabled("QUBIT_TEST_FLAG") is True


def test_project_plugins_dir_prefers_qubit(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".qubit" / "plugins" / "demo").mkdir(parents=True)
    (tmp_path / ".hermes" / "plugins" / "legacy").mkdir(parents=True)
    monkeypatch.setenv("QUBIT_ENABLE_PROJECT_PLUGINS", "1")
    monkeypatch.delenv("HERMES_ENABLE_PROJECT_PLUGINS", raising=False)

    from plugins.memory import _get_project_plugins_dir

    assert _get_project_plugins_dir() == tmp_path / ".qubit" / "plugins"


# ---------------------------------------------------------------- entry points
def test_plugin_validate_accepts_requires_qubit():
    from qubit_cli.plugin_validate import _manifest_requires_spec

    assert _manifest_requires_spec({"requires_qubit": ">=1.0"}) == ">=1.0"
    assert _manifest_requires_spec({"qubit": ">=2.0"}) == ">=2.0"
    assert _manifest_requires_spec({"requires_hermes": ">=3.0"}) == ">=3.0"


def test_dashboard_plugin_search_dirs_scans_qubit_project_dir(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".qubit" / "plugins").mkdir(parents=True)
    monkeypatch.setenv("QUBIT_ENABLE_PROJECT_PLUGINS", "1")
    monkeypatch.delenv("HERMES_ENABLE_PROJECT_PLUGINS", raising=False)

    from qubit_cli.web_server_dashboard import _dashboard_plugin_search_dirs

    dirs = [str(d) for d, _src in _dashboard_plugin_search_dirs()]
    assert any(d.endswith(".qubit/plugins") for d in dirs)


def test_plugins_discovery_unions_qubit_and_legacy_entry_point_groups():
    from qubit_cli.plugins_discovery import _select_entry_point_groups
    from qubit_cli.plugins_discovery import (
        ENTRY_POINTS_GROUP,
        QUBIT_ENTRY_POINTS_GROUP,
    )

    class _EP:
        def __init__(self, group, name):
            self.group = group
            self.name = name
            self.value = f"{name}:register"

    eps = [_EP(QUBIT_ENTRY_POINTS_GROUP, "q"), _EP(ENTRY_POINTS_GROUP, "h")]
    selected = _select_entry_point_groups(eps, (QUBIT_ENTRY_POINTS_GROUP, ENTRY_POINTS_GROUP))
    assert {ep.name for ep in selected} == {"q", "h"}


def test_memory_entry_points_unions_qubit_and_legacy_groups(monkeypatch):
    import importlib.metadata as md

    from plugins import memory

    class _EP:
        def __init__(self, group, name):
            self.group = group
            self.name = name
            self.value = f"{name}:register"

    class _EPS(list):
        def select(self, group):
            return [ep for ep in self if ep.group == group]

    eps = _EPS([
        _EP(memory.QUBIT_ENTRY_POINTS_GROUP, "q"),
        _EP(memory.ENTRY_POINTS_GROUP, "h"),
    ])
    monkeypatch.setattr(md, "entry_points", lambda: eps)
    assert {ep.name for ep in memory._iter_entry_points()} == {"q", "h"}


def test_distribution_manifest_reads_and_writes_qubit_requires(tmp_path):
    from qubit_cli.profile_distribution import (
        MANIFEST_FILENAME,
        DistributionManifest,
        read_manifest,
        write_manifest,
    )

    (tmp_path / MANIFEST_FILENAME).write_text("name: x\nqubit_requires: '>=2.0'\n")
    parsed = read_manifest(tmp_path)
    assert parsed.qubit_requires == ">=2.0"
    assert parsed.to_dict()["qubit_requires"] == ">=2.0"

    legacy = DistributionManifest.from_dict({"name": "y", "hermes_requires": ">=3.0"})
    assert legacy.qubit_requires == ">=3.0"  # read-old
    write_manifest(tmp_path, legacy)
    assert read_manifest(tmp_path).qubit_requires == ">=3.0"
