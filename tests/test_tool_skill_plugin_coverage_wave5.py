"""Wave 5 — tool / skill / plugin coverage against the in-process fork runtime.

Qubit executes the complete pinned Hermes fork *in-process*, so an upstream
agent tool, an optional/built-in skill, or a non-dashboard vendored plugin is
**not** a separate Qubit module: the fork's tool/toolset registry, skill tree,
and plugin fleet are the real backing surface.

This test locks the Wave 5 disposition rules:

* every ``hermes_tool`` / ``hermes_skill`` / ``hermes_plugin`` inventory item
  carries an explicit typed ``fork_disposition`` (IMPLEMENTED with a real
  proving test, or GATED with a reason) — never a silent drop;
* the tool proof imports the live vendored fork registry and asserts every
  inventory tool/toolset is exposed and dispatchable;
* the skill proof asserts every IMPLEMENTED skill is vendored with a loadable
  ``SKILL.md``; the plugin proof asserts every plugin is in the Qubit wrapper
  fleet (``voicebridge.hermes_plugins``);
* the Qubit agent dispatch path (``voicebridge.hermes_runtime`` -> fork
  ``model_tools``) resolves to the same fork registry object;
* the gap-ledger generator emits a typed row for every dispositioned item, so
  an IMPLEMENTED item can never remain an unexplained omission.

Hermetic: temp ``HERMES_HOME``; no network, no live services, no tool execution.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

INVENTORY_PATH = REPO_ROOT / "docs" / "QUBIT_SOURCE_INVENTORY.json"
COVERED_CATEGORIES = frozenset({"hermes_tool", "hermes_skill", "hermes_plugin"})
DISPOSITION_STATUSES = frozenset({"IMPLEMENTED", "GATED"})
CAPABILITY_BY_CATEGORY = {
    "hermes_tool": "fork_tool_runtime",
    "hermes_skill": "fork_skill_runtime",
    "hermes_plugin": "fork_plugin_runtime",
}
TOOL_PROOF = ("tests/test_tool_skill_plugin_coverage_wave5.py"
              "::test_fork_tool_registry_exposes_inventory_tools")
SKILL_PROOF = ("tests/test_tool_skill_plugin_coverage_wave5.py"
               "::test_all_inventory_skills_vendored_and_loadable")
PLUGIN_PROOF = ("tests/test_tool_skill_plugin_coverage_wave5.py"
                "::test_all_vendored_plugin_items_have_a_wrapper")
PROOF_FUNCTIONS = (
    "test_fork_tool_registry_exposes_inventory_tools",
    "test_all_inventory_skills_vendored_and_loadable",
    "test_all_vendored_plugin_items_have_a_wrapper",
)


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(
        name, REPO_ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def inventory() -> dict:
    assert INVENTORY_PATH.is_file(), "docs/QUBIT_SOURCE_INVENTORY.json missing"
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


@pytest.fixture()
def fork_path(tmp_path, monkeypatch):
    """Point HERMES_HOME at a temp dir and put the vendored fork on sys.path."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    from voicebridge import hermes_runtime

    hermes_runtime.ensure_path()
    return hermes_runtime


def _covered_items(inventory: dict):
    return [it for it in inventory["items"]
            if it.get("category") in COVERED_CATEGORIES]


# ---------------------------------------------------------------------------
# 1. Every tool / skill / plugin item has a typed disposition
# ---------------------------------------------------------------------------
def test_every_tool_skill_plugin_item_has_a_typed_disposition(inventory):
    items = _covered_items(inventory)
    assert items, "inventory has no tool/skill/plugin items"
    categories = {it["category"] for it in items}
    assert categories == set(COVERED_CATEGORIES), categories

    offenders = []
    for it in items:
        disposition = it.get("fork_disposition")
        if not isinstance(disposition, dict):
            offenders.append((it["id"], "no fork_disposition"))
            continue
        if disposition.get("status") not in DISPOSITION_STATUSES:
            offenders.append((it["id"], disposition.get("status")))
        if disposition.get("capability") != CAPABILITY_BY_CATEGORY[it["category"]]:
            offenders.append((it["id"], "wrong capability",
                              disposition.get("capability")))
        if not str(disposition.get("reason") or "").strip():
            offenders.append((it["id"], "empty reason"))
        if disposition.get("status") == "IMPLEMENTED":
            if not disposition.get("backend"):
                offenders.append((it["id"], "empty backend"))
            if not str(disposition.get("proving_test") or "").startswith(
                    "tests/"):
                offenders.append((it["id"], "no tests/ proving test"))
            if disposition.get("needs") not in (None, "none", ""):
                offenders.append((it["id"], "IMPLEMENTED with needs"))
    assert offenders == [], offenders


def test_implemented_dispositions_cite_an_executable_test(inventory):
    """Every IMPLEMENTED disposition names an on-disk test that runs here."""
    cited = set()
    offenders = []
    for it in _covered_items(inventory):
        disposition = it.get("fork_disposition") or {}
        if disposition.get("status") != "IMPLEMENTED":
            continue
        proving = str(disposition.get("proving_test") or "")
        path_part, _, func = proving.partition("::")
        if not (REPO_ROOT / path_part).is_file():
            offenders.append((it["id"], proving, "file not on disk"))
            continue
        if not func or not callable(globals().get(func)):
            offenders.append((it["id"], proving, "function not executable"))
            continue
        cited.add(proving)
    assert offenders == [], offenders
    # All three category proofs are real, executable tests in this module.
    assert {TOOL_PROOF, SKILL_PROOF, PLUGIN_PROOF} <= cited, sorted(cited)
    for func in PROOF_FUNCTIONS:
        assert callable(globals().get(func)), func


# ---------------------------------------------------------------------------
# 2. Fork tool registry exposes every inventory tool (live import)
# ---------------------------------------------------------------------------
def test_fork_tool_registry_exposes_inventory_tools(inventory, fork_path):
    """The cited tool proof: import the live fork registry, assert membership.

    A tool item is exposed when the fork either registers it directly
    (dispatchable, callable handler) or declares it in its ``TOOLSETS`` table
    (individual tools, toolset names, and included toolsets).
    """
    from tools.registry import discover_builtin_tools, registry
    import toolsets

    discover_builtin_tools()
    registered = set(registry.get_all_tool_names())
    exposed = set(registered)
    for name, spec in toolsets.TOOLSETS.items():
        exposed.add(name)
        exposed.update(spec.get("tools") or [])
        exposed.update(spec.get("includes") or [])

    tool_names = sorted(it["name"] for it in _covered_items(inventory)
                        if it["category"] == "hermes_tool")
    assert tool_names, "no hermes_tool inventory items"

    missing = sorted(set(tool_names) - exposed)
    assert missing == [], f"fork runtime does not expose: {missing}"

    # Every directly-registered tool must be dispatchable (callable handler).
    no_handler = sorted(
        name for name in tool_names
        if name in registered
        and not callable(getattr(registry.get_entry(name), "handler", None)))
    assert no_handler == [], no_handler

    # Spot-check the agent dispatch contract on a core registered tool.
    assert "terminal" in registered
    assert callable(registry.get_entry("terminal").handler)
    defs = registry.get_definitions({"terminal"}, quiet=True)
    assert defs and defs[0]["function"]["name"] == "terminal"


# ---------------------------------------------------------------------------
# 3. Fork skills vendored + loadable
# ---------------------------------------------------------------------------
def test_all_inventory_skills_vendored_and_loadable(inventory, fork_path):
    """The cited skill proof: every IMPLEMENTED skill has a real SKILL.md."""
    skill_items = [it for it in _covered_items(inventory)
                   if it["category"] == "hermes_skill"]
    assert skill_items, "no hermes_skill inventory items"
    offenders = []
    for it in skill_items:
        disposition = it.get("fork_disposition") or {}
        root = str(it.get("root") or "")
        name = str(it.get("name") or "")
        skill_dir = REPO_ROOT / "qubit_hermes" / root / name
        has_skill = skill_dir.is_dir() and any(skill_dir.rglob("SKILL.md"))
        if disposition.get("status") == "IMPLEMENTED":
            if not has_skill:
                offenders.append((it["id"], "IMPLEMENTED but no SKILL.md"))
        else:
            if has_skill:
                offenders.append((it["id"], "GATED but loadable"))
            if "SKILL.md" not in str(disposition.get("reason") or ""):
                offenders.append((it["id"], "GATED without reason"))
    assert offenders == [], offenders


# ---------------------------------------------------------------------------
# 4. Vendored plugins covered by the Qubit wrapper fleet
# ---------------------------------------------------------------------------
def test_all_vendored_plugin_items_have_a_wrapper(inventory, fork_path):
    """The cited plugin proof: every plugin item is in the wrapper fleet."""
    from voicebridge import hermes_plugins as HP

    fleet = set(HP.VENDORED_PLUGIN_FLEET)
    audited = {row["name"] for row in HP.plugin_fleet_audit()}
    plugin_items = [it for it in _covered_items(inventory)
                    if it["category"] == "hermes_plugin"]
    assert plugin_items, "no hermes_plugin inventory items"
    offenders = []
    for it in plugin_items:
        name = str(it.get("name") or "")
        directory = REPO_ROOT / "qubit_hermes" / "plugins" / name
        disposition = it.get("fork_disposition") or {}
        if name not in fleet:
            offenders.append((it["id"], "not in VENDORED_PLUGIN_FLEET"))
        if name not in audited:
            offenders.append((it["id"], "not covered by plugin_fleet_audit"))
        if not directory.is_dir():
            offenders.append((it["id"], "plugin dir missing"))
        if disposition.get("status") != "IMPLEMENTED":
            offenders.append((it["id"], disposition.get("status")))
    assert offenders == [], offenders


# ---------------------------------------------------------------------------
# 5. Qubit agent/CLI dispatch path reaches the same fork registry
# ---------------------------------------------------------------------------
def test_fork_runtime_is_the_qubit_agent_dispatch_path(fork_path):
    """The Qubit-owned runtime resolves to the fork, whose agent loop
    (``model_tools``) dispatches against the exact registry the tool proof
    introspects."""
    from voicebridge import hermes_runtime
    from tools.registry import registry as fork_registry
    import model_tools

    root = hermes_runtime.runtime_root()
    assert root.name == "qubit_hermes", root
    assert root == REPO_ROOT / "qubit_hermes"
    # one in-process registry authority for the fork agent path
    assert model_tools.registry is fork_registry
    assert callable(model_tools.handle_function_call)
    assert callable(model_tools.get_tool_definitions)
    names = set(model_tools.get_all_tool_names())
    assert "terminal" in names and "web_search" in names, sorted(names)[:20]


# ---------------------------------------------------------------------------
# 6. The generator never turns a dispositioned item into an omission
# ---------------------------------------------------------------------------
def test_ledger_emits_a_typed_row_for_every_dispositioned_item(inventory):
    rebuild = _load_module(
        "rebuild_ledger_qubit_wave5", "rebuild_ledger_qubit.py")
    gap_ledger = _load_module("gap_ledger_wave5", "gap_ledger.py")

    data = rebuild.build_ledger()
    summary = data["inventory"]
    assert summary["dispositioned"] == (
        summary["dispositioned_implemented"] + summary["dispositioned_gated"])
    assert summary["dispositioned_implemented"] > 0
    assert summary["dispositioned"] >= 100

    disposition_rows = [
        r for r in data["rows"]
        if r["id"].startswith("inv:")
        and r["status"] in DISPOSITION_STATUSES]
    assert len(disposition_rows) == summary["dispositioned"], (
        len(disposition_rows), summary["dispositioned"])

    for row in disposition_rows:
        assert row["needs"] in gap_ledger.NEEDS_VALUES, row
        if row["status"] == "IMPLEMENTED":
            assert row["qubit_backend"], row
            assert row["proving_test"], row
            assert (REPO_ROOT / row["proving_test"].split("::", 1)[0]).is_file(), \
                row

    # An IMPLEMENTED disposition may not also appear as a MISSING omission.
    missing_ids = {r["id"] for r in data["rows"] if r["status"] == "MISSING"}
    offenders = []
    for it in _covered_items(inventory):
        disposition = it.get("fork_disposition") or {}
        if disposition.get("status") != "IMPLEMENTED":
            continue
        row_id = rebuild._inventory_row_id(it["id"])
        if row_id in missing_ids:
            offenders.append((it["id"], row_id))
    assert offenders == [], offenders

    # Every emitted disposition row matches its inventory disposition status.
    by_row_id = {rebuild._inventory_row_id(it["id"]): it
                 for it in _covered_items(inventory)}
    for row in disposition_rows:
        item = by_row_id.get(row["id"].split("#", 1)[0])
        if item is None:
            # Non-tool/skill/plugin dispositions (e.g. OpenClaw extensions)
            # are asserted by their own wave-5 suite; this test owns the fork
            # tool/skill/plugin disposition path.
            continue
        expected = (item.get("fork_disposition") or {}).get("status")
        assert row["status"] == expected, (row["id"], row["status"], expected)
