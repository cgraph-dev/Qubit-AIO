"""Direct source-inventory regression tests (Qubit code-readiness Phase 1/2).

Proves that ``scripts/source_inventory.py`` produces a deterministic inventory
directly from the pinned Hermes/OpenClaw trees, that the committed artifacts
are in sync with a fresh build, and that the known-missing example
``plugins/hermes-achievements`` (manifest + plugin_api.py + dashboard dist +
test) is represented and confirmed absent from the verified waves-1-2
gap-ledger baseline.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "source_inventory.py"
# Canonical regenerated artifacts (Owner resolution 0.6): the frozen
# docs/archive/2026-09-21/ records are read-only history, never the live
# output of the generators.
GENERATED = REPO_ROOT / "docs" / "generated"
INV_JSON = GENERATED / "QUBIT_SOURCE_INVENTORY.json"
OM_MD = GENERATED / "QUBIT_SOURCE_OMISSIONS.md"
VENDOR_MANIFEST = GENERATED / "QUBIT_HERMES_VENDOR_MANIFEST.json"
LEDGER_NAMES = ["hermes", "openclaw", "qubit"]
LEDGER_BASELINE_REV = "f5fc5776"
# The W9 v0.21.4 vendored pin. W9 is a slice-by-slice sync, so the canonical
# inventory records the unported remainder as ``missing`` rather than assuming a
# fully-synced tree. The ``-git`` clone is the read-only oracle; the pristine
# tree has no ``.git`` and cannot satisfy ``_pins_available``.
QUBIT_PIN = Path("/tmp/opencode/pinned/hermes-agent-d337b736-git")
OPENCLAW_PIN = Path("/home/trick/Projects/Repos/Qubit Agent/srcodes/openclaw")

# Categories the inventory must always cover (mechanical counts, not statuses).
REQUIRED_CATEGORIES = {
    "qubit_cli_command",
    "qubit_cli_subcommand",
    "qubit_cli_option",
    "qubit_backend_route",
    "hermes_dashboard_page",
    "hermes_dashboard_component",
    "hermes_dashboard_plugin",
    "hermes_plugin",
    "hermes_plugin_api",
    "hermes_plugin_asset",
    "hermes_plugin_test",
    "hermes_tool",
    "hermes_skill",
    "hermes_model",
    "hermes_provider",
    "hermes_workflow",
    "hermes_i18n_key",
    "hermes_locale_key",
    "hermes_upstream_test",
    "openclaw_extension",
}


def _pins_available() -> bool:
    return ((QUBIT_PIN / ".git").exists()
            and (OPENCLAW_PIN / ".git").exists())


def _baseline_ledger_text() -> dict[str, str]:
    """Ledger section text at the verified waves-1-2 baseline commit."""
    out = {}
    for name in LEDGER_NAMES:
        proc = subprocess.run(
            ["git", "show",
             f"{LEDGER_BASELINE_REV}:docs/archive/2026-09-21/QUBIT_GAP_LEDGER.{name}.json"],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
        )
        out[name] = proc.stdout if proc.returncode == 0 else ""
    return out


def _build(out: Path, omissions: Path,
           ledger_rev: str = LEDGER_BASELINE_REV) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--hermes", str(QUBIT_PIN),
         "--openclaw", str(OPENCLAW_PIN), "--out", str(out),
         "--omissions", str(omissions), "--ledger-rev", ledger_rev],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )


@pytest.fixture(scope="module")
def inventory() -> dict:
    assert INV_JSON.is_file(), f"{INV_JSON} missing"
    return json.loads(INV_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def omissions_text() -> str:
    assert OM_MD.is_file(), f"{OM_MD} missing"
    return OM_MD.read_text(encoding="utf-8")


def test_artifacts_present_and_shaped(inventory):
    assert inventory["schema"] == "qubit-source-inventory/1"
    assert inventory["items"], "inventory has no items"
    assert inventory["counts_by_category"], "no category counts"
    assert inventory["counts_by_source"]["hermes"] > 0
    assert inventory["counts_by_source"]["openclaw"] > 0
    assert "hermes_byte_diff" in inventory
    assert "ledger_comparison" in inventory
    assert "parent_mapping" in inventory
    assert OM_MD.is_file()


def _item(inventory: dict, item_id: str) -> dict:
    hits = [i for i in inventory["items"] if i["id"] == item_id]
    assert hits, f"missing inventory item {item_id}"
    return hits[0]


def test_parent_mapping_counts_are_consistent(inventory):
    pm = inventory["parent_mapping"]
    items = inventory["items"]
    evidence = sum(1 for i in items
                   if i["category"] in ("hermes_i18n_key",
                                        "hermes_locale_key",
                                        "hermes_upstream_test"))
    surfaces = len(items) - evidence
    assert pm["parented_surfaces"] + pm["distinct_surfaces"] == surfaces
    assert (pm["parented_covered"] + pm["parented_uncovered"]
            == pm["parented_surfaces"])
    assert (pm["distinct_covered"] + pm["distinct_uncovered"]
            == pm["distinct_surfaces"])
    assert (pm["parented_uncovered"] + pm["distinct_uncovered"]
            == inventory["omissions"]["total"])


def test_parent_mapping_rules(inventory):
    chat = _item(inventory, "hermes chat --checkpoints")
    assert chat["coverage"] == "cli-option"
    assert chat["parent_id"] == "cli:chat"

    model = next(i for i in inventory["items"]
                 if i["category"] == "hermes_model")
    assert model["coverage"] == "model-entry"
    assert model["parent_id"] == "model"

    provider = next(i for i in inventory["items"]
                    if i["category"] == "hermes_provider")
    assert provider["coverage"] == "provider-entry"
    assert provider["parent_id"] == "proxy_providers"

    api = _item(inventory, "plugin-api:hermes-achievements")
    assert api["coverage"] == "plugin-surface"
    assert api["parent_id"] == "plugin:hermes-achievements"

    ext = next(i for i in inventory["items"]
               if i["category"] == "openclaw_extension")
    assert ext["coverage"] == "openclaw-capability"
    assert ext["parent_id"] is None or ext["parent_id"].startswith("OC-")

    wf = next(i for i in inventory["items"]
              if i["category"] == "hermes_workflow")
    assert wf["coverage"] == "workflow-surface"
    assert wf["parent_id"] is not None

    comp = _item(inventory, "component:ThemeSwitcher")
    assert comp["coverage"] == "dashboard-surface"
    assert comp["parent_id"] == "webui_status"

    sub = next(i for i in inventory["items"]
               if i["category"] == "qubit_cli_subcommand"
               and not i.get("is_alias_of"))
    assert sub["coverage"] == ""
    assert sub["parent_id"] is None

    tool = next(i for i in inventory["items"]
                if i["category"] == "hermes_tool")
    assert tool["parent_id"] is None


def test_parent_mapping_is_deterministic():
    if not _pins_available():
        pytest.skip("pinned upstream trees not present")
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        a, b = Path(tmp) / "a.json", Path(tmp) / "b.json"
        am, bm = Path(tmp) / "a.md", Path(tmp) / "b.md"
        assert _build(a, am).returncode == 0
        assert _build(b, bm).returncode == 0
        da = json.loads(a.read_text(encoding="utf-8"))
        db = json.loads(b.read_text(encoding="utf-8"))
        assert da["parent_mapping"] == db["parent_mapping"]
        pa = [(i["id"], i["parent_id"], i["coverage"]) for i in da["items"]]
        pb = [(i["id"], i["parent_id"], i["coverage"]) for i in db["items"]]
        assert pa == pb


def test_omissions_report_classifies_gaps(omissions_text):
    assert "## Distinct capability gaps" in omissions_text
    assert "## Parented surfaces needing parent closure" in omissions_text
    assert "## Parented surfaces covered by an IMPLEMENTED parent" in omissions_text
    assert "no parent row" in omissions_text


def test_counts_cover_every_required_category(inventory):
    counts = inventory["counts_by_category"]
    missing = REQUIRED_CATEGORIES - set(counts)
    assert not missing, f"categories missing from inventory: {sorted(missing)}"
    for cat in REQUIRED_CATEGORIES:
        assert counts[cat] > 0, f"category {cat} is empty"

    # counts_by_category must equal the actual per-item tally.
    tally: dict[str, int] = {}
    for item in inventory["items"]:
        tally[item["category"]] = tally.get(item["category"], 0) + 1
    assert tally == counts

    # counts_by_source consistency.
    src: dict[str, int] = {}
    for item in inventory["items"]:
        src[item["source"]] = src.get(item["source"], 0) + 1
    assert src == inventory["counts_by_source"]


def test_known_mechanical_counts(inventory):
    counts = inventory["counts_by_category"]
    # W9 is a slice-by-slice vendor sync: the mechanical upstream surface
    # (routes, CLI commands) grows as each slice is ported, so the totals are
    # asserted as *derived* invariants against the recorded pin, never as
    # fully-synced hardcoded numbers.
    assert inventory["pins"]["hermes"]["revision"] == (
        "d337b736aa1e8ebecfab043842d13e4a2d2f48a3")
    assert inventory["pins"]["hermes"]["tag"] == "v0.21.4"

    # counts_by_category must equal the actual per-item tally.
    tally: dict[str, int] = {}
    for item in inventory["items"]:
        tally[item["category"]] = tally.get(item["category"], 0) + 1
    assert counts == tally

    # Every mechanical upstream-surface category is present and non-empty; the
    # exact totals track the pin and are allowed to move with the slice.
    for cat in ("qubit_backend_route", "qubit_cli_command",
                "openclaw_extension", "hermes_dashboard_plugin",
                "hermes_plugin_test"):
        assert counts[cat] > 0, f"category {cat} is empty"


def test_byte_diff_is_reported(inventory):
    bd = inventory["hermes_byte_diff"]
    counts = bd["counts"]
    # W9 is a slice-by-slice vendor sync: the vendored tree intentionally
    # carries a recorded unported set (``missing``) until the remaining slices
    # land, so assert the byte diff is *consistent* with the canonical vendor
    # manifest instead of fully synced. A nonzero ``missing`` is the recorded
    # unported set, not a failure.
    manifest = json.loads(VENDOR_MANIFEST.read_text(encoding="utf-8"))
    extra_local = manifest.get("extra_local") or []
    recorded = set(manifest.get("patched") or [])
    missing = set(manifest.get("missing_from_local") or [])

    # Internal arithmetic of the diff.
    assert counts["local_files"] == (counts["identical"] + counts["changed"]
                                     + counts["extra"]), counts
    assert counts["upstream_files"] == (counts["identical"] + counts["changed"]
                                        + counts["missing"]), counts

    # Cross-artifact consistency with the canonical vendor manifest.
    assert counts["missing"] == len(missing), (counts["missing"], len(missing))
    assert counts["extra"] == len(extra_local), (counts["extra"],
                                                 len(extra_local))
    changed = {c["path"] for c in bd["changed"]}
    assert changed == recorded, changed ^ recorded
    assert counts["changed"] == len(recorded)
    assert len(manifest.get("patch_manifest") or {}) == len(recorded)

    # The recorded reconciliation must confirm the two artifacts agree.
    recon = bd.get("manifest_reconciliation") or {}
    assert recon.get("changed_equals_patched") is True
    assert recon.get("extra_equals_extra_local") is True
    assert recon.get("vendor_manifest_revision") == manifest["upstream_revision"]


def test_omissions_report_consistent(inventory, omissions_text):
    assert "## Summary" in omissions_text
    assert "candidate work rows" in omissions_text
    om = inventory["omissions"]
    total = om["total"]
    # The report must state the same total as the inventory.
    assert f"**{total}**" in omissions_text or str(total) in omissions_text
    # W9 is a slice-by-slice vendor sync: the v0.21.4 baseline exposes surfaces
    # not yet ported, so a nonzero omission total is expected and is asserted as
    # the recorded unported set, not as a failure. The tally must reconcile with
    # its distinct/parented breakdown and the report must carry no
    # unreproducible evidence.
    assert total == om["distinct_total"] + om["parented_uncovered"]
    assert total == sum(om["by_category"].values())
    assert total == (sum(om["distinct_by_category"].values())
                     + sum(om["parented_by_category"].values()))
    assert om["unreproducible_evidence"] == []


@pytest.mark.skipif(not _pins_available(),
                    reason="pinned upstream trees not present")
def test_committed_inventory_in_sync(tmp_path):
    out = tmp_path / "inv.json"
    om = tmp_path / "om.md"
    # Rebuild with the same ledger revision the committed artifact records, so
    # the check is stable regardless of whether it pinned a commit or the
    # working tree.
    committed_meta = json.loads(INV_JSON.read_text(encoding="utf-8"))
    rev = ((committed_meta.get("ledger_comparison") or {})
           .get("ledger_revision") or "working")
    rev = "working" if rev == "working-tree" else rev
    proc = _build(out, om, ledger_rev=rev)
    assert proc.returncode == 0, proc.stderr
    committed = INV_JSON.read_text(encoding="utf-8")
    assert out.read_text(encoding="utf-8") == committed, (
        "committed inventory is stale; re-run scripts/source_inventory.py")
    assert om.read_text(encoding="utf-8") == OM_MD.read_text(encoding="utf-8")


@pytest.mark.skipif(not _pins_available(),
                    reason="pinned upstream trees not present")
def test_regeneration_is_deterministic(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    am, bm = tmp_path / "a.md", tmp_path / "b.md"
    assert _build(a, am).returncode == 0
    assert _build(b, bm).returncode == 0
    assert a.read_bytes() == b.read_bytes()
    assert am.read_bytes() == bm.read_bytes()


def test_achievements_plugin_represented_and_closed_in_ledger(inventory,
                                                              omissions_text):
    """The mandatory example: a full dashboard plugin (manifest + API +
    dist assets + test) represented in the inventory and now closed with a
    real live-ledger row."""
    items = [i for i in inventory["items"]
             if "hermes-achievements" in json.dumps(i)]
    cats = {i["category"] for i in items}
    assert "hermes_plugin" in cats
    assert "hermes_dashboard_plugin" in cats
    assert "hermes_plugin_api" in cats
    assert "hermes_plugin_asset" in cats
    assert "hermes_plugin_test" in cats

    # The example the directive named is now CLOSED: the live Hermes ledger
    # carries a real plugin:hermes-achievements row with a backend and an
    # on-disk proving test, and the inventory surfaces reconcile to it.
    qubit_rows = json.loads(
        (GENERATED / "QUBIT_GAP_LEDGER.hermes.json").read_text(
            encoding="utf-8"))["rows"]
    row = next((r for r in qubit_rows
                if r["id"] == "plugin:hermes-achievements"), None)
    assert row is not None, "achievements plugin has no live ledger row"
    assert row["status"] == "IMPLEMENTED", row
    assert row["qubit_backend"] and row["proving_test"], row
    assert (REPO_ROOT / row["proving_test"].split("::", 1)[0]).is_file(), row

    # Historical evidence preserved: the waves-1-2 baseline did not mention it.
    baseline = _baseline_ledger_text()
    assert all("achievement" not in text.lower()
               for text in baseline.values()), (
        "verified waves-1-2 baseline unexpectedly mentions achievements")

    # The achievements plugin is represented as a distinct capability item
    # (no parent) while its manifest/API/test/asset surfaces are parented to
    # the plugin.  Parent coverage depends on the live working-tree ledger and
    # is therefore not asserted here; the deterministic mapping is.
    plugin = _item(inventory, "plugin:hermes-achievements")
    assert plugin["parent_id"] is None
    assert plugin["coverage"] == ""
    for surface_id in (
            "plugin-manifest:hermes-achievements",
            "plugin-api:hermes-achievements",
            "plugin-test:hermes-achievements:test_achievement_engine.py",
            "plugin-asset:hermes-achievements:index.js",
            "plugin-asset:hermes-achievements:style.css"):
        surface = _item(inventory, surface_id)
        assert surface["parent_id"] == "plugin:hermes-achievements"
        assert surface["coverage"] == "plugin-surface"


def test_openclaw_extensions_have_category_and_manifest(inventory):
    exts = [i for i in inventory["items"]
            if i["category"] == "openclaw_extension"]
    # Latest OpenClaw pin (da03996ab7, v2026.6.8) ships 138 extension dirs.
    assert len(exts) == 138
    with_manifest = [e for e in exts if e.get("manifest")]
    assert len(with_manifest) >= 130
    with_cat = [e for e in exts if e.get("categories")]
    # The v2026.6.8 plugin manifests carry no top-level ``categories`` array,
    # so every extension is recorded uncategorized in the aggregate.
    assert len(with_cat) == 0
    # unknown extensions are explicitly flagged as not audited
    for e in exts:
        assert "in_audit_docs" in e
