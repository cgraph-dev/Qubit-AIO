"""Focused tests for the OpenJarvis 8-agent absorption audit.

Hermetic: every test builds a throwaway role tree under ``tmp_path``. The
audit script is loaded by path and is itself pure AST — no runtime import, no
OpenJarvis install and no network.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "audit_openjarvis_absorption.py"
REQUIRED = (
    "orchestrator",
    "react",
    "operative",
    "openhands",
    "simple",
    "researcher",
    "digest",
    "monitor",
)


def _load_audit():
    spec = importlib.util.spec_from_file_location(
        "audit_openjarvis_absorption", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


audit = _load_audit()


def _role_source(
    role_id: str,
    *,
    omit: tuple[str, ...] = (),
    overrides: dict[str, str] | None = None,
    prefix: tuple[str, ...] = (),
    suffix: tuple[str, ...] = (),
) -> str:
    values = {
        "id": repr(role_id),
        "title": repr(role_id.title()),
        "description": repr(f"Qubit-native {role_id} posture."),
        "system_prompt": repr(f"Act as the {role_id} role."),
        "toolsets": '("core",)',
        "model_tier": '"local"',
        "confirmation": '"preview"',
        "voice_aliases": f'("{role_id}",)',
        "recipe_id": '""',
        "source": repr(f"openjarvis:{role_id} (mapped)"),
    }
    if role_id == "digest":
        values["system_prompt"] = '""'
        values["recipe_id"] = repr("morning_digest")
    elif role_id == "monitor":
        values["system_prompt"] = '""'
        values["recipe_id"] = repr("monitor_operative")
    values.update(overrides or {})

    lines = ["from roles.base import AgentRole", ""]
    lines.extend(prefix)
    if prefix:
        lines.append("")
    lines.append("ROLE = AgentRole(")
    for name, expression in values.items():
        if name in omit:
            continue
        lines.append(f"    {name}={expression},")
    lines.append(")")
    if suffix:
        lines.append("")
        lines.extend(suffix)
    return "\n".join(lines) + "\n"


def _write(roles_dir: Path, module: str, source: str) -> None:
    (roles_dir / f"{module}.py").write_text(source, encoding="utf-8")


def _clean_tree(tmp_path: Path) -> Path:
    root = tmp_path / "qubit_runtime"
    roles_dir = root / "roles"
    roles_dir.mkdir(parents=True)
    for role_id in REQUIRED:
        _write(roles_dir, role_id, _role_source(role_id))
    return root


def _report(root: Path) -> dict:
    return audit.analyze(root)


def test_clean_eight_role_tree_passes(tmp_path, capsys):
    root = _clean_tree(tmp_path)
    code = audit.main(["--root", str(root)])
    output = capsys.readouterr().out
    assert code == 0
    assert "CLEAN (8/8, 0 wrappers)" in output
    report = _report(root)
    assert report["clean"] is True
    assert report["covered"] == "8/8"
    assert report["missing"] == []
    assert report["duplicates"] == {}
    assert report["wrapper_modules"] == []


def test_missing_role_id_fails(tmp_path):
    root = tmp_path / "qubit_runtime"
    roles_dir = root / "roles"
    roles_dir.mkdir(parents=True)
    for role_id in REQUIRED:
        if role_id == "monitor":
            continue
        _write(roles_dir, role_id, _role_source(role_id))
    report = _report(root)
    assert report["clean"] is False
    assert report["missing"] == ["monitor"]
    assert report["exit_code"] == 1
    assert audit.main(["--root", str(root)]) == 1


def test_duplicate_role_id_fails(tmp_path):
    root = _clean_tree(tmp_path)
    roles_dir = root / "roles"
    _write(roles_dir, "orchestrator_dup", _role_source("orchestrator"))
    report = _report(root)
    assert report["clean"] is False
    assert report["duplicates"] == {
        "orchestrator": ["orchestrator", "orchestrator_dup"]}
    assert any("duplicate role id 'orchestrator'" in gap
               for gap in report["gaps"])
    assert audit.main(["--root", str(root)]) == 1


def test_missing_voice_alias_fails(tmp_path):
    root = _clean_tree(tmp_path)
    roles_dir = root / "roles"
    _write(roles_dir, "react",
           _role_source("react", overrides={"voice_aliases": "()"}))
    report = _report(root)
    assert report["clean"] is False
    assert any("missing voice_aliases" in gap for gap in report["gaps"])
    assert audit.main(["--root", str(root)]) == 1


def test_missing_provenance_fails(tmp_path):
    root = _clean_tree(tmp_path)
    roles_dir = root / "roles"
    _write(roles_dir, "simple",
           _role_source("simple", overrides={"source": '""'}))
    report = _report(root)
    assert report["clean"] is False
    assert any("missing source" in gap for gap in report["gaps"])
    assert audit.main(["--root", str(root)]) == 1


def test_missing_prompt_and_recipe_fails(tmp_path):
    root = _clean_tree(tmp_path)
    roles_dir = root / "roles"
    _write(roles_dir, "researcher",
           _role_source("researcher",
                        omit=("system_prompt", "recipe_id")))
    report = _report(root)
    assert report["clean"] is False
    assert any("needs system_prompt or recipe_id" in gap
               for gap in report["gaps"])
    assert audit.main(["--root", str(root)]) == 1


def test_openjarvis_import_is_a_wrapper(tmp_path):
    root = _clean_tree(tmp_path)
    roles_dir = root / "roles"
    _write(roles_dir, "orchestrator",
           _role_source("orchestrator", prefix=("import openjarvis",)))
    report = _report(root)
    assert report["clean"] is False
    assert "orchestrator" in report["wrapper_modules"]
    role = next(item for item in report["roles"]
                if item["module"] == "orchestrator")
    assert role["wrapper"]["openjarvis"] is True
    assert audit.main(["--root", str(root)]) == 1


def test_subprocess_wrapper_fails(tmp_path):
    root = _clean_tree(tmp_path)
    roles_dir = root / "roles"
    suffix = (
        "import subprocess",
        "",
        "def _delegate():",
        '    return subprocess.run(["jarvis", "ask"])',
    )
    _write(roles_dir, "operative",
           _role_source("operative", suffix=suffix))
    report = _report(root)
    assert report["clean"] is False
    role = next(item for item in report["roles"]
                if item["module"] == "operative")
    assert role["wrapper"]["subprocess"] is True
    assert any("subprocess" in gap for gap in report["gaps"])
    assert audit.main(["--root", str(root)]) == 1


def test_http_wrapper_fails(tmp_path):
    root = _clean_tree(tmp_path)
    roles_dir = root / "roles"
    suffix = (
        "import urllib.request",
        "",
        "def _fetch():",
        '    return urllib.request.urlopen("http://127.0.0.1:8000")',
    )
    _write(roles_dir, "openhands",
           _role_source("openhands", suffix=suffix))
    report = _report(root)
    assert report["clean"] is False
    role = next(item for item in report["roles"]
                if item["module"] == "openhands")
    assert role["wrapper"]["http"] is True
    assert audit.main(["--root", str(root)]) == 1


def test_forwarding_module_fails(tmp_path):
    root = _clean_tree(tmp_path)
    roles_dir = root / "roles"
    _write(roles_dir, "forwarded", "from roles.orchestrator import ROLE\n")
    report = _report(root)
    assert report["clean"] is False
    assert "forwarded" in report["wrapper_modules"]
    role = next(item for item in report["roles"]
                if item["module"] == "forwarded")
    assert role["wrapper"]["forwarder"] is True
    assert audit.main(["--root", str(root)]) == 1


def test_extra_role_is_reported_but_does_not_fail(tmp_path):
    root = _clean_tree(tmp_path)
    _write(root / "roles", "future", _role_source("future"))
    report = _report(root)
    assert report["clean"] is True
    assert report["covered"] == "8/8"
    assert report["unexpected"] == ["future"]
    assert audit.main(["--root", str(root)]) == 0


def test_recipe_id_must_be_registered(tmp_path):
    root = _clean_tree(tmp_path)
    recipes = root / "recipes"
    recipes.mkdir()
    (recipes / "morning_digest.py").write_text(
        "from recipes.spec import RecipeSpec\n"
        'MORNING_DIGEST = RecipeSpec(id="morning_digest")\n',
        encoding="utf-8",
    )
    report = _report(root)
    assert report["clean"] is False
    assert any("recipe_id 'monitor_operative' is not registered" in gap
               for gap in report["gaps"])
    assert audit.main(["--root", str(root)]) == 1

    (recipes / "monitor_operative.py").write_text(
        "from recipes.spec import RecipeSpec\n"
        'MONITOR = RecipeSpec(id="monitor_operative")\n',
        encoding="utf-8",
    )
    report = _report(root)
    assert report["clean"] is True
    present = {role["id"]: role["recipe_present"]
               for role in report["roles"] if role["recipe_id"]}
    assert present == {"digest": True, "monitor": True}


def test_json_mode_is_valid_json(tmp_path, capsys):
    root = _clean_tree(tmp_path)
    code = audit.main(["--root", str(root), "--json"])
    output = capsys.readouterr().out
    assert code == 0
    report = json.loads(output)
    assert report["clean"] is True
    assert report["required"] == list(REQUIRED)
    assert report["covered"] == "8/8"
    assert {role["id"] for role in report["roles"]} == set(REQUIRED)
    assert report["exit_code"] == 0


def test_missing_roles_directory_is_a_gap(tmp_path, capsys):
    root = tmp_path / "qubit_runtime"
    root.mkdir()
    report = _report(root)
    assert report["clean"] is False
    assert any("roles directory not found" in gap
               for gap in report["gaps"])
    assert audit.main(["--root", str(root)]) == 1
