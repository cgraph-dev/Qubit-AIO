"""Wave 24 — rebrand asymmetry regression tests.

Each site below is a lookup/key/registration where the rebrand renamed one side
of a read/write pair but not the other, so a Qubit-spelled key silently failed
to resolve. The canonical spelling is Qubit; the legacy Hermes spelling stays
accepted read-old (and the Qubit spelling is accepted wherever only the legacy
one used to be recognised).

Hermetic: throwaway HOME/QUBIT_HOME under the test tmp dir; no network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "qubit_runtime"))


@pytest.fixture(autouse=True)
def _throwaway_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QUBIT_HOME", str(home / ".qubit"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))


# ------------------------------------------------- skill frontmatter metadata key
def test_skill_metadata_key_accepts_qubit_and_legacy_hermes():
    from agent.skill_utils import _qubit_metadata

    assert _qubit_metadata({"metadata": {"qubit": {"tags": ["q"]}}}) == {"tags": ["q"]}
    assert _qubit_metadata({"metadata": {"hermes": {"tags": ["h"]}}}) == {"tags": ["h"]}


def test_skill_metadata_key_prefers_qubit():
    from agent.skill_utils import _qubit_metadata

    both = {"metadata": {"qubit": {"tags": ["q"]}, "hermes": {"tags": ["h"]}}}
    assert _qubit_metadata(both) == {"tags": ["q"]}


def test_learning_graph_reads_both_metadata_spellings():
    from agent.learning_graph import _fm_field

    assert _fm_field({"metadata": {"qubit": {"related_skills": ["q"]}}}, "related_skills") == ["q"]
    assert _fm_field({"metadata": {"hermes": {"related_skills": ["h"]}}}, "related_skills") == ["h"]


def test_skills_hub_models_reads_both_tags_spellings():
    from tools.skills_hub_models import _qubit_tags

    assert _qubit_tags({"metadata": {"qubit": {"tags": ["q"]}}}) == ["q"]
    assert _qubit_tags({"metadata": {"hermes": {"tags": ["h"]}}}) == ["h"]


def test_skills_hub_official_reads_both_upstream_spellings():
    from tools.skills_hub_official import OptionalSkillSource

    src = OptionalSkillSource.__new__(OptionalSkillSource)
    qubit = ("---\nname: demo\nmetadata:\n  qubit:\n    upstream:\n"
             "      repo: o/q\n      path: skills/q\n---\nbody\n")
    hermes = ("---\nname: demo\nmetadata:\n  hermes:\n    upstream:\n"
              "      repo: o/h\n      path: skills/h\n---\nbody\n")
    assert OptionalSkillSource._upstream_pointer_from_content(src, qubit) == {"repo": "o/q", "path": "skills/q"}
    assert OptionalSkillSource._upstream_pointer_from_content(src, hermes) == {"repo": "o/h", "path": "skills/h"}


def test_skill_linter_accepts_both_metadata_spellings():
    from tools.skill_linter import lint_content

    def rules(meta_key: str) -> set:
        text = (
            "---\nname: demo\ndescription: A demo skill\nversion: 1.0.0\n"
            "author: Someone\nlicense: MIT\n"
            f"metadata:\n  {meta_key}:\n    tags: [demo]\n---\nbody\n"
        )
        return {f.rule for f in lint_content(text) if "missing-metadata" in f.rule}

    assert rules("qubit") == set()
    assert rules("hermes") == set()


def test_blueprints_read_both_and_write_qubit_metadata():
    from tools.blueprints import export_blueprint, parse_blueprint

    qubit = ("---\nname: demo\nmetadata:\n  qubit:\n    blueprint:\n"
             "      schedule: '0 9 * * *'\n---\n\nBody.\n")
    hermes = ("---\nname: demo\nmetadata:\n  hermes:\n    blueprint:\n"
              "      schedule: '0 9 * * *'\n---\n\nBody.\n")
    assert parse_blueprint(qubit).schedule == "0 9 * * *"
    assert parse_blueprint(hermes).schedule == "0 9 * * *"
    rendered = export_blueprint({"name": "demo", "schedule_display": "0 9 * * *"}, "Body line")
    assert "  qubit:" in rendered and "hermes:" not in rendered


def test_skills_tool_skill_view_reads_both_metadata_spellings(monkeypatch, tmp_path):
    from tools.skills_tool import skill_view

    skills = tmp_path / "home" / ".qubit" / "skills"
    for name, key, tags in (("demo-q", "qubit", ["q1"]), ("demo-h", "hermes", ["h1"])):
        d = skills / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: A demo\nmetadata:\n  {key}:\n"
            f"    tags: [{tags[0]}]\n---\nbody\n"
        )
    assert json.loads(skill_view("demo-q"))["tags"] == ["q1"]
    assert json.loads(skill_view("demo-h"))["tags"] == ["h1"]


# ------------------------------------------------- executable-name identity
def test_holder_executables_include_qubit_and_legacy_hermes():
    from qubit_state_holders import _QUBIT_EXECUTABLES, _looks_like_qubit

    assert {"qubit", "qubit-agent", "qubit-dev"} <= set(_QUBIT_EXECUTABLES)
    assert {"hermes", "hermes-agent", "hermes-acp"} <= set(_QUBIT_EXECUTABLES)
    assert _looks_like_qubit(["qubit", "gateway", "run"]) is True
    assert _looks_like_qubit(["hermes", "gateway", "run"]) is True


# ------------------------------------------------- pip entry-point groups
def test_providers_scan_reads_both_entry_point_groups():
    import inspect

    from providers import _discover_entry_point_providers

    src = inspect.getsource(_discover_entry_point_providers)
    assert '"qubit_agent.plugins"' in src and '"hermes_agent.plugins"' in src


def test_plugins_discovery_unions_both_entry_point_groups():
    from qubit_cli.plugins_discovery import (
        ENTRY_POINTS_GROUP,
        QUBIT_ENTRY_POINTS_GROUP,
        _select_entry_point_groups,
    )

    class _EP:
        def __init__(self, group, name):
            self.group, self.name, self.value = group, name, f"{name}:register"

    eps = [_EP(QUBIT_ENTRY_POINTS_GROUP, "q"), _EP(ENTRY_POINTS_GROUP, "h")]
    selected = _select_entry_point_groups(eps, (QUBIT_ENTRY_POINTS_GROUP, ENTRY_POINTS_GROUP))
    assert {ep.name for ep in selected} == {"q", "h"}


def test_memory_entry_points_unions_both_groups(monkeypatch):
    import importlib.metadata as md

    from plugins import memory

    class _EP:
        def __init__(self, group, name):
            self.group, self.name, self.value = group, name, f"{name}:register"

    class _EPS(list):
        def select(self, group):
            return [ep for ep in self if ep.group == group]

    eps = _EPS([_EP(memory.QUBIT_ENTRY_POINTS_GROUP, "q"), _EP(memory.ENTRY_POINTS_GROUP, "h")])
    monkeypatch.setattr(md, "entry_points", lambda: eps)
    assert {ep.name for ep in memory._iter_entry_points()} == {"q", "h"}


# ------------------------------------------------- project-plugins env flag
def test_project_plugins_flag_honors_qubit_alias(monkeypatch):
    from utils import env_var_alias, env_var_enabled

    monkeypatch.setenv("QUBIT_TEST_FLAG", "1")
    monkeypatch.delenv("HERMES_TEST_FLAG", raising=False)
    assert env_var_alias("HERMES_TEST_FLAG") == "QUBIT_TEST_FLAG"
    assert env_var_enabled("HERMES_TEST_FLAG") is True

    monkeypatch.delenv("QUBIT_TEST_FLAG", raising=False)
    monkeypatch.setenv("HERMES_TEST_FLAG", "on")
    assert env_var_enabled("QUBIT_TEST_FLAG") is True


def test_project_plugin_dir_prefers_qubit_and_falls_back_to_legacy(monkeypatch, tmp_path):
    from plugins.memory import _get_project_plugins_dir

    monkeypatch.setenv("QUBIT_ENABLE_PROJECT_PLUGINS", "1")
    monkeypatch.delenv("HERMES_ENABLE_PROJECT_PLUGINS", raising=False)

    both = tmp_path / "both"
    (both / ".qubit" / "plugins" / "q").mkdir(parents=True)
    (both / ".hermes" / "plugins" / "h").mkdir(parents=True)
    monkeypatch.chdir(both)
    assert _get_project_plugins_dir() == both / ".qubit" / "plugins"

    legacy = tmp_path / "legacy"
    (legacy / ".hermes" / "plugins" / "h").mkdir(parents=True)
    monkeypatch.chdir(legacy)
    assert _get_project_plugins_dir() == legacy / ".hermes" / "plugins"


def test_plugins_discovery_scans_qubit_project_dir(monkeypatch):
    import inspect

    from qubit_cli.plugins_discovery import collect_directory_manifests

    src = inspect.getsource(collect_directory_manifests)
    assert 'Path.cwd() / ".qubit" / "plugins"' in src
    assert 'Path.cwd() / ".hermes" / "plugins"' in src


# ------------------------------------------------- distribution manifest key
def test_distribution_manifest_uses_qubit_key_with_legacy_read(tmp_path):
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
    assert legacy.qubit_requires == ">=3.0"
    write_manifest(tmp_path, legacy)
    assert read_manifest(tmp_path).qubit_requires == ">=3.0"


# ------------------------------------------------- plugin manifest key
def test_plugin_validate_accepts_qubit_and_legacy_requires():
    from qubit_cli.plugin_validate import _manifest_requires_spec

    assert _manifest_requires_spec({"requires_qubit": ">=1.0"}) == ">=1.0"
    assert _manifest_requires_spec({"qubit": ">=2.0"}) == ">=2.0"
    assert _manifest_requires_spec({"requires_hermes": ">=3.0"}) == ">=3.0"


def test_plugins_manifest_reads_qubit_requires(tmp_path, monkeypatch):
    import qubit_cli.plugins_manifest as pm

    (tmp_path / "plugin.yaml").write_text("name: x\nversion: 0.1.0\nrequires_qubit: '>=9999'\n")
    manifest = pm.parse_manifest_file(tmp_path / "plugin.yaml", tmp_path, "user", "")
    assert manifest is not None and manifest.requires_hermes == ">=9999"

    monkeypatch.setattr(pm, "running_qubit_version", lambda: "0.21.5")
    assert pm.requires_qubit_error({"requires_qubit": ">=9999"}) == "requires qubit >=9999, running 0.21.5"
    assert pm.requires_qubit_error({"requires_hermes": ">=9999"}) == "requires qubit >=9999, running 0.21.5"
