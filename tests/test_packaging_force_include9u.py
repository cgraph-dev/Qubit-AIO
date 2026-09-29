"""Packaging force-include completeness (audit 9U+).

The vendored runtime reads three trees at runtime by their position relative to
the executing module, so they must be shipped in the wheel/sdist subset:

* ``qubit_runtime/optional-mcps/``   — read by ``qubit_cli/mcp_catalog.py``
  (``Path(__file__).parent.parent / "optional-mcps"``).
* ``qubit_runtime/plugin-catalog/``  — read by ``qubit_cli/plugin_catalog.py``
  (same relative resolution).
* ``qubit_runtime/acp_adapter/``     — the ACP server reached by ``qubit agent
  acp`` / the ``qubit-acp`` console script.

In the installed wheel the runtime root is
``voicebridge/_data/qubit_runtime``, so each source must be force-included to
that destination, and the sdist must mirror it at the original ``qubit_runtime/``
path so the wheel rebuilds from the sdist (``test_packaging_wave3.py``).

These are contract checks over the declared mapping — no wheel is built.
"""
from __future__ import annotations

import ast
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PYPROJECT = REPO / "pyproject.toml"

# source-tree (repo-relative) -> wheel package-data destination
RUNTIME_TREES = {
    "qubit_runtime/optional-mcps":
        "voicebridge/_data/qubit_runtime/optional-mcps",
    "qubit_runtime/plugin-catalog":
        "voicebridge/_data/qubit_runtime/plugin-catalog",
    "qubit_runtime/acp_adapter":
        "voicebridge/_data/qubit_runtime/acp_adapter",
}


def _toml() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _targets() -> tuple[dict, dict]:
    targets = _toml()["tool"]["hatch"]["build"]["targets"]
    return targets["wheel"], targets["sdist"]


def test_runtime_catalog_trees_are_force_included_in_wheel():
    wheel, _ = _targets()
    force = wheel["force-include"]
    for source, dest in RUNTIME_TREES.items():
        assert force.get(source) == dest, (source, force.get(source))
        assert dest.startswith("voicebridge/_data/"), dest


def test_sdist_mirrors_the_new_runtime_trees_at_source_paths():
    _, sdist = _targets()
    sforce = sdist["force-include"]
    for source in RUNTIME_TREES:
        # The wheel is rebuilt from the sdist, so the sdist keeps the original
        # qubit_runtime/ layout for every force-included runtime source.
        assert sforce.get(source) == source, (source, sforce.get(source))


def test_force_included_sources_exist_and_hold_their_runtime_payload():
    for source in RUNTIME_TREES:
        root = REPO / source
        assert root.is_dir(), source
        assert any(p.is_file() for p in root.rglob("*")), source
    # the trees carry the exact files their readers expect
    assert (REPO / "qubit_runtime/optional-mcps/n8n/manifest.yaml").is_file()
    assert any((REPO / "qubit_runtime/plugin-catalog").glob("*.yaml"))
    assert (REPO / "qubit_runtime/acp_adapter/entry.py").is_file()


def test_mcp_and_plugin_catalog_reader_relative_resolution_hits_the_destination():
    """The readers resolve ``<runtime_root>/<name>``; the mapping must land the
    tree at exactly that package-relative location in the installed wheel.

    ``mcp_catalog.py`` / ``plugin_catalog.py`` live at
    ``<runtime_root>/qubit_cli/`` and read ``Path(__file__).parent.parent /
    <name>``.
    """
    wheel, _ = _targets()
    force = wheel["force-include"]
    module_dir = REPO / "voicebridge/_data/qubit_runtime/qubit_cli"
    runtime_root = module_dir.parent
    assert runtime_root == REPO / "voicebridge/_data/qubit_runtime"
    for name in ("optional-mcps", "plugin-catalog"):
        dest = (runtime_root.relative_to(REPO) / name).as_posix()
        assert force[f"qubit_runtime/{name}"] == dest, (name, force.get(f"qubit_runtime/{name}"))


def _script_targets() -> dict:
    return _toml()["project"]["scripts"]


def test_qubit_acp_script_points_at_the_packaged_vendored_entry():
    scripts = _script_targets()
    assert scripts.get("qubit-acp") == "acp_adapter.entry:main"
    module_path, _, attr = scripts["qubit-acp"].partition(":")
    assert attr, scripts["qubit-acp"]

    # Resolve ``acp_adapter.entry`` to the real vendored source file and prove
    # the callable exists (AST, no import of the vendored runtime).
    source = REPO / "qubit_runtime" / Path(*module_path.split(".")).with_suffix(".py")
    assert source.is_file(), source
    tree = ast.parse(source.read_text(encoding="utf-8"))
    callables = {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert attr in callables, (attr, sorted(callables))
    # the adapter is a package, not a loose module
    assert (source.parent / "__init__.py").is_file()


def test_frontend_force_include_is_not_broken_by_the_new_entries():
    wheel, _ = _targets()
    # The dashboard tree is staged by the custom hook (node_modules pruning),
    # and its build artifact stays declared in both targets.
    hook = wheel.get("hooks", {}).get("custom", {})
    assert (REPO / str(hook.get("path", ""))).is_file()
    assert "frontend/qubit-dist" in wheel.get("artifacts", [])
    _, sdist = _targets()
    assert "frontend/qubit-dist" in sdist.get("artifacts", [])
