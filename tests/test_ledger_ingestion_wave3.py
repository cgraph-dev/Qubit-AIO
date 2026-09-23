"""Wave 3 — ledger evidence gates and direct source-inventory ingestion.

Invariants locked here:

* ``scripts/rebuild_ledger_qubit.py`` is deterministic across runs and hash
  seeds, and its committed section keeps the fixed schema ``gap_ledger.py``
  validates;
* no ``IMPLEMENTED`` row exists without an on-disk behavioral test and a real
  production backend;
* op evidence is *linked* to a production dispatch, so a textual mention
  (docstring, comment, unrelated assertion) can never close a row;
* the direct ``docs/archive/2026-09-21/QUBIT_SOURCE_INVENTORY.json`` is ingested when present:
  unmatched surface items become explicit open rows and the omissions summary
  is recorded; a missing or malformed inventory is tolerated and recorded;
* ``gap_ledger.py --require-inventory-complete`` fails on unexplained
  omissions and passes when complete, and its omissions summary is
  machine-readable.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(
        name, REPO_ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rebuild = _load_module("rebuild_ledger_qubit_wave3", "rebuild_ledger_qubit.py")
gap_ledger = _load_module("gap_ledger_wave3", "gap_ledger.py")

SECTION_PATH = REPO_ROOT / "docs/generated" / "QUBIT_GAP_LEDGER.qubit.json"


def _section() -> dict:
    return json.loads(SECTION_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Determinism + schema
# ---------------------------------------------------------------------------
def test_generator_is_deterministic() -> None:
    first = rebuild._render(rebuild.build_ledger())
    second = rebuild._render(rebuild.build_ledger())
    assert first == second


def test_generator_deterministic_across_hash_seeds(tmp_path) -> None:
    """A fresh process with a different hash seed reproduces the same bytes.

    A fixed synthetic inventory is used so the check isolates set-iteration
    order from the (separately owned, moving) real source inventory.
    """
    inv_path = _write_inventory(tmp_path / "stable_inv.json", [
        {"id": "wave3:seed-open", "source": "hermes",
         "category": "qubit_cli_subcommand", "name": "Seed open",
         "path": "qubit_cli/seed.py", "ledger_match": None},
    ])
    outputs = []
    for seed in ("0", "1"):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        out = tmp_path / f"qubit_{seed}.json"
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" /
                                 "rebuild_ledger_qubit.py"),
             "--out", str(out), "--inventory", str(inv_path)],
            cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
            timeout=600)
        assert result.returncode == 0, result.stderr[-800:]
        outputs.append(out.read_text(encoding="utf-8"))
    assert outputs[0] == outputs[1]


def test_committed_section_schema_is_valid() -> None:
    data = _section()
    validated = gap_ledger.validate_section("qubit", data)
    assert validated["schema"] == "qubit-gap-ledger-section/1"
    assert validated["source"] == "qubit-surface"
    for row in data["rows"]:
        assert set(row) == set(gap_ledger.ROW_KEYS), sorted(
            set(row) ^ set(gap_ledger.ROW_KEYS))
    ids = [row["id"] for row in data["rows"]]
    assert len(ids) == len(set(ids)), "duplicate ledger row ids"
    assert data["counts_by_status"] == {
        status: sum(1 for r in data["rows"] if r["status"] == status)
        for status in sorted({r["status"] for r in data["rows"]})
    }


def test_gap_ledger_schema_validation_stays_strict(tmp_path) -> None:
    with pytest.raises(gap_ledger.SchemaError):
        gap_ledger.validate_section("x", {"schema": "s", "source": "s",
                                          "rows": [{"id": "a"}]})
    good = {"schema": "s", "source": "s", "rows": [{
        key: "" for key in gap_ledger.ROW_KEYS}]}
    good["rows"][0]["status"] = "IMPLEMENTED"
    good["rows"][0]["needs"] = "none"
    gap_ledger.validate_section("x", good)
    bad_status = {"schema": "s", "source": "s", "rows": [dict(
        good["rows"][0], status="MAGIC")]}
    with pytest.raises(gap_ledger.SchemaError):
        gap_ledger.validate_section("x", bad_status)


# ---------------------------------------------------------------------------
# No IMPLEMENTED without real production caller + on-disk behavioral test
# ---------------------------------------------------------------------------
def test_no_implemented_without_proof_and_backend() -> None:
    offenders = []
    for row in _section()["rows"]:
        if row["status"] != "IMPLEMENTED":
            continue
        if not row["qubit_backend"]:
            offenders.append((row["id"], "empty backend"))
        proving = row["proving_test"]
        if not proving:
            offenders.append((row["id"], "empty proving_test"))
        elif not (REPO_ROOT / proving.split("::", 1)[0]).is_file():
            offenders.append((row["id"], proving, "not on disk"))
    assert offenders == [], offenders


def test_implemented_op_ids_are_linkable_to_a_dispatch() -> None:
    """Every IMPLEMENTED catalog op has a real linked proof, not text."""
    from voicebridge import dashboard_ops as DO

    catalog = {o["id"]: o for o in DO.catalog()}
    index = rebuild.build_test_index()
    offenders = []
    for row in _section()["rows"]:
        if row["status"] != "IMPLEMENTED":
            continue
        if row["id"].startswith(("page_", "plugin_", "inv:")):
            continue
        if row["id"] not in catalog:
            continue
        _q, has_backend, backend_key, fixed = rebuild.resolve_backend(row["id"])
        if not has_backend:
            offenders.append((row["id"], "no backend"))
            continue
        cands = rebuild.op_candidates(backend_key, fixed)
        module_hits = set()
        for cand in cands:
            module_hits |= index.module_calls.get(cand, set())
        linked = index.op_hits.get(row["id"], set())
        if not (module_hits or linked):
            offenders.append((row["id"], "not linked to a dispatcher"))
    assert offenders == [], offenders


def test_textual_mention_is_never_evidence(tmp_path) -> None:
    """A docstring/comment/assertion mention must not close a row."""
    synthetic = tmp_path / "test_wave3_textual.py"
    synthetic.write_text(
        '"""Module docstring mentioning actions_retry, never dispatched."""\n'
        "from voicebridge import dashboard_ops as DO\n\n\n"
        "def test_only_mentions_actions_retry():\n"
        '    """actions_retry is documented here, not run."""\n'
        "    # actions_retry in a comment\n"
        '    assert "actions_retry" == "actions_retry"\n'
        '    DO.run_op(None, "backup_info", {})\n',
        encoding="utf-8")
    index = rebuild.TestIndex()
    index.add_file(synthetic)
    assert "backup_info" in index.op_hits
    assert "actions_retry" not in index.op_hits


def test_linked_evidence_keeps_parametrized_and_table_dispatch(tmp_path) -> None:
    parametrized = tmp_path / "test_wave3_param.py"
    parametrized.write_text(
        "import pytest\n"
        "from voicebridge import dashboard_ops as DO\n\n"
        '@pytest.mark.parametrize("op_id", ["backup_info"])\n'
        "def test_param(op_id):\n"
        "    DO.run_op(None, op_id, {})\n",
        encoding="utf-8")
    table = tmp_path / "test_wave3_table.py"
    table.write_text(
        "from voicebridge import dashboard_ops as DO\n"
        'OP_IDS = ["backup_info"]\n\n'
        "def test_table():\n"
        "    for op_id in OP_IDS:\n"
        "        DO.run_op(None, op_id, {})\n",
        encoding="utf-8")
    index = rebuild.TestIndex()
    index.add_file(parametrized)
    index.add_file(table)
    assert "backup_info" in index.op_hits


# ---------------------------------------------------------------------------
# Inventory ingestion
# ---------------------------------------------------------------------------
def _write_inventory(path: Path, items: list) -> Path:
    path.write_text(json.dumps({
        "schema": "qubit-source-inventory/1",
        "counts_by_source": {"hermes": len(items)},
        "omissions": {"total": 0, "by_category": {}},
        "ledger_comparison": {"unreproducible_evidence": []},
        "items": items,
    }), encoding="utf-8")
    return path


def test_absent_inventory_is_tolerated_and_recorded(tmp_path) -> None:
    missing = tmp_path / "does-not-exist.json"
    report = rebuild.load_source_inventory(missing)
    assert report["present"] is False
    assert report["items"] == []
    assert "absent" in report["error"]

    data = rebuild.build_ledger(inventory_path=missing)
    inv = data["inventory"]
    assert inv["present"] is False
    assert inv["unexplained_omissions"] == 0
    assert "absent" in inv["error"]
    assert not [r for r in data["rows"] if r["id"].startswith("inv:")]


def test_malformed_inventory_is_tolerated_and_recorded(tmp_path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    report = rebuild.load_source_inventory(bad)
    assert report["present"] is True
    assert report["items"] == []
    assert "unreadable" in report["error"]
    data = rebuild.build_ledger(inventory_path=bad)
    assert data["inventory"]["error"]
    assert not [r for r in data["rows"] if r["id"].startswith("inv:")]


def test_unmatched_inventory_items_become_explicit_open_rows(tmp_path) -> None:
    inv_path = _write_inventory(tmp_path / "inv.json", [
        {"id": "wave3:novel-op", "source": "hermes",
         "category": "qubit_cli_subcommand", "name": "Novel op",
         "path": "qubit_cli/novel.py", "ledger_match": None},
        {"id": "api:GET /api/backup", "source": "hermes",
         "category": "qubit_backend_route", "name": "GET /api/backup",
         "path": "qubit_cli/backup.py", "ledger_match": "backup_info"},
    ])
    data = rebuild.build_ledger(inventory_path=inv_path)
    inv = data["inventory"]
    assert inv["present"] is True
    assert inv["items_total"] == 2
    assert inv["surface_items"] == 2
    assert inv["matched"] == 1
    assert inv["unmatched"] == 1
    assert inv["unexplained_omissions"] == 1
    assert inv["omitted_ids"] == ["wave3:novel-op"]

    inv_rows = [r for r in data["rows"] if r["id"].startswith("inv:")]
    assert len(inv_rows) == 1, inv_rows
    row = inv_rows[0]
    assert row["status"] == "MISSING"
    assert row["needs"] in gap_ledger.NEEDS_VALUES
    assert not row["qubit_backend"]
    assert "novel-op" in row["id"]
    # the matched item produced no placeholder row
    assert not [r for r in data["rows"]
                if "api:GET /api/backup" in r["id"]]


def test_broken_ledger_match_is_an_explicit_open_row(tmp_path) -> None:
    inv_path = _write_inventory(tmp_path / "inv.json", [
        {"id": "wave3:stale", "source": "hermes",
         "category": "hermes_tool", "name": "Stale tool",
         "path": "tools/stale.py", "ledger_match": "no_such_ledger_row"},
    ])
    data = rebuild.build_ledger(inventory_path=inv_path)
    inv = data["inventory"]
    assert inv["matched"] == 0
    assert inv["unmatched"] == 1
    inv_rows = [r for r in data["rows"] if r["id"].startswith("inv:")]
    assert len(inv_rows) == 1
    assert "does not exist" in inv_rows[0]["note"]


def test_evidence_only_items_are_counted_not_rows(tmp_path) -> None:
    inv_path = _write_inventory(tmp_path / "inv.json", [
        {"id": "i18n:x.y", "source": "hermes", "category": "hermes_i18n_key",
         "name": "x.y", "path": "web/src/i18n/en.ts", "ledger_match": None},
        {"id": "test:tests/a.py", "source": "hermes",
         "category": "hermes_upstream_test", "name": "tests/a.py",
         "path": "tests/a.py", "ledger_match": None},
    ])
    data = rebuild.build_ledger(inventory_path=inv_path)
    inv = data["inventory"]
    assert inv["evidence_only"] == 2
    assert inv["surface_items"] == 0
    assert inv["unmatched"] == 0
    assert not [r for r in data["rows"] if r["id"].startswith("inv:")]


# ---------------------------------------------------------------------------
# Parent-mapping coverage (distinct vs parented surfaces)
# ---------------------------------------------------------------------------
def test_parented_surface_covered_by_implemented_parent(tmp_path) -> None:
    inv_path = _write_inventory(tmp_path / "inv.json", [
        {"id": "synthetic:model-entry", "source": "hermes",
         "category": "hermes_model", "name": "gpt-5",
         "path": "qubit_cli/models_catalog_static.py",
         "parent_id": "model", "coverage": "model-entry"},
    ])
    data = rebuild.build_ledger(inventory_path=inv_path)
    inv = data["inventory"]
    assert inv["parented_surfaces"] == 1
    assert inv["parented_covered"] == 1
    assert inv["parented_uncovered"] == 0
    assert inv["distinct_uncovered"] == 0
    assert not [r for r in data["rows"] if "model-entry" in r["id"]]


def test_parented_surface_with_missing_parent_is_open(tmp_path) -> None:
    inv_path = _write_inventory(tmp_path / "inv.json", [
        {"id": "synthetic:ghost-asset", "source": "hermes",
         "category": "hermes_plugin_asset", "name": "index.js",
         "path": "plugins/ghost/dashboard/dist/index.js",
         "parent_id": "plugin:ghost", "coverage": "plugin-surface"},
    ])
    data = rebuild.build_ledger(inventory_path=inv_path)
    inv = data["inventory"]
    assert inv["parented_surfaces"] == 1
    assert inv["parented_covered"] == 0
    assert inv["parented_uncovered"] == 1
    assert inv["distinct_uncovered"] == 0
    inv_rows = [r for r in data["rows"] if r["id"].startswith("inv:")]
    assert len(inv_rows) == 1
    assert "Parented surface" in inv_rows[0]["note"]
    assert "plugin:ghost" in inv_rows[0]["note"]


def test_distinct_vs_parented_counts(tmp_path) -> None:
    inv_path = _write_inventory(tmp_path / "inv.json", [
        {"id": "synthetic:model-entry", "source": "hermes",
         "category": "hermes_model", "name": "gpt-5",
         "path": "m.py", "parent_id": "model", "coverage": "model-entry"},
        {"id": "synthetic:ghost-asset", "source": "hermes",
         "category": "hermes_plugin_asset", "name": "i.js",
         "path": "p.js", "parent_id": "plugin:ghost",
         "coverage": "plugin-surface"},
        {"id": "synthetic:distinct", "source": "hermes",
         "category": "hermes_skill", "name": "novel-skill", "path": "s"},
        {"id": "i18n:a.b", "source": "hermes", "category": "hermes_i18n_key",
         "name": "a.b", "path": "en.ts"},
    ])
    data = rebuild.build_ledger(inventory_path=inv_path)
    inv = data["inventory"]
    assert inv["surface_items"] == 3
    assert inv["evidence_only"] == 1
    assert inv["parented_surfaces"] == 2
    assert inv["distinct_surfaces"] == 1
    assert inv["parented_covered"] == 1
    assert inv["parented_uncovered"] == 1
    assert inv["distinct_uncovered"] == 1
    assert inv["matched"] + inv["unmatched"] == inv["surface_items"]
    assert inv["unexplained_omissions"] == 2
    assert inv["distinct_omitted_ids"] == ["synthetic:distinct"]
    assert inv["parented_omitted_ids"] == ["synthetic:ghost-asset"]


def test_live_exact_id_match_is_not_missing(tmp_path) -> None:
    """An item whose id is a live ledger row is matched, never re-emitted."""
    inv_path = _write_inventory(tmp_path / "inv.json", [
        {"id": "model", "source": "hermes", "category": "hermes_tool",
         "name": "model", "path": "tools/x.py"},
    ])
    data = rebuild.build_ledger(inventory_path=inv_path)
    inv = data["inventory"]
    assert inv["matched"] == 1
    assert inv["unmatched"] == 0
    assert not [r for r in data["rows"] if r["id"].startswith("inv:")]


def test_inventory_summary_is_internally_consistent() -> None:
    inv = _section().get("inventory")
    assert isinstance(inv, dict), "qubit section missing inventory block"
    assert inv["matched"] + inv["unmatched"] == inv["surface_items"]
    assert inv["unexplained_omissions"] == (
        inv["unmatched"] + inv.get("unidentified", 0))
    assert inv["evidence_only"] + inv["surface_items"] == inv["items_total"]


def _read_inventory() -> dict:
    path = REPO_ROOT / "docs/archive/2026-09-21" / "QUBIT_SOURCE_INVENTORY.json"
    assert path.is_file(), path
    return json.loads(path.read_text(encoding="utf-8"))


def test_committed_inventory_is_at_the_terminal_fixed_point() -> None:
    """source_inventory(working) ⇄ rebuild_ledger_qubit is a stable fixed point.

    The committed inventory is reconciled against the working-tree ledger, so
    every recorded status is a declared ledger status.  All recorded statuses
    are terminal; the vendored ``skills/index-cache`` asset cache is excluded
    from the surface inventory as a non-capability, so there is no open row.
    """
    data = _read_inventory()
    comparison = data.get("ledger_comparison") or {}
    assert comparison.get("ledger_revision") == "working-tree", comparison
    statuses = set(gap_ledger.STATUS_VALUES)
    terminal = set(gap_ledger.TERMINAL_STATUSES)
    recorded = set()
    non_terminal_items = []
    for item in data["items"]:
        for key in ("ledger_status", "parent_status"):
            value = item.get(key)
            if value is not None:
                assert value in statuses, (item.get("id"), key, value)
                recorded.add(value)
                if value not in terminal:
                    non_terminal_items.append((item.get("id"), key, value))
    assert recorded, "inventory records no ledger statuses at all"
    # No non-terminal status may be recorded: every surface is terminal.
    assert non_terminal_items == [], non_terminal_items


def test_inventory_parent_coverage_treats_terminal_gate_as_covering() -> None:
    """A SAFE_EQUIVALENT / OPERATOR_ONLY parent covers its child surfaces."""
    data = _read_inventory()
    covered = [
        item for item in data["items"]
        if item.get("parent_covered")
        and item.get("parent_status") in ("SAFE_EQUIVALENT", "OPERATOR_ONLY")
    ]
    assert covered, ("no parented surface was covered by a terminal gate "
                     "parent; source_inventory parent coverage may be stale")


# ---------------------------------------------------------------------------
# gap_ledger --require-inventory-complete + omissions summary
# ---------------------------------------------------------------------------
def _write_sections(docs: Path, inventory: dict,
                    status: str = "IMPLEMENTED",
                    needs: str = "none") -> None:
    docs.mkdir(parents=True, exist_ok=True)
    row = {key: "" for key in gap_ledger.ROW_KEYS}
    row.update({"id": "seed", "area": "seed", "capability": "seed",
                "status": status, "needs": needs})
    section = {"schema": "qubit-gap-ledger-section/1",
               "source": "qubit-surface", "rows": [row]}
    if inventory is not None:
        section["inventory"] = inventory
    (docs / "QUBIT_GAP_LEDGER.qubit.json").write_text(
        json.dumps(section), encoding="utf-8")


def test_require_inventory_complete_fails_on_omissions(tmp_path) -> None:
    docs = tmp_path / "docs"
    _write_sections(docs, {
        "present": True, "path": "docs/archive/2026-09-21/QUBIT_SOURCE_INVENTORY.json",
        "items_total": 3, "surface_items": 3, "matched": 2, "unmatched": 1,
        "unexplained_omissions": 1, "omitted_ids": ["wave3:missing"],
        "error": "",
    })
    omissions_out = tmp_path / "omissions.json"
    rc = gap_ledger.main([
        "--docs-dir", str(docs), "--out", str(tmp_path / "LEDGER.md"),
        "--require-inventory-complete", "--omissions-out", str(omissions_out)])
    assert rc == 1
    written = json.loads(omissions_out.read_text(encoding="utf-8"))
    assert written["unexplained_omissions"] == 1
    assert {"section": "qubit", "id": "wave3:missing"} in written["omitted"]


def test_require_inventory_complete_passes_when_complete(tmp_path) -> None:
    docs = tmp_path / "docs"
    _write_sections(docs, {
        "present": True, "path": "docs/archive/2026-09-21/QUBIT_SOURCE_INVENTORY.json",
        "items_total": 2, "surface_items": 2, "matched": 2, "unmatched": 0,
        "unexplained_omissions": 0, "omitted_ids": [], "error": "",
    })
    rc = gap_ledger.main([
        "--docs-dir", str(docs), "--out", str(tmp_path / "LEDGER.md"),
        "--require-inventory-complete"])
    assert rc == 0


def test_require_inventory_complete_fails_when_inventory_absent(tmp_path) -> None:
    docs = tmp_path / "docs"
    _write_sections(docs, None)
    rc = gap_ledger.main([
        "--docs-dir", str(docs), "--out", str(tmp_path / "LEDGER.md"),
        "--require-inventory-complete"])
    assert rc == 1


# ---------------------------------------------------------------------------
# gap_ledger --require-code-ready
# ---------------------------------------------------------------------------
# The vendored ``skills/index-cache`` JSON asset cache is excluded from the
# surface inventory as a non-capability, so the committed ledger has no open row.
DOCUMENTED_OPEN_ROW = None


def test_committed_ledgers_have_no_open_rows() -> None:
    """The committed sections are code-complete: zero open rows."""
    sections, missing, _ = gap_ledger.load_sections(
        REPO_ROOT / "docs" / "archive" / "2026-09-21")
    assert missing == [], missing
    summary = gap_ledger.build_summary(sections, missing)
    assert summary["open_rows"] == 0, summary["by_status"]
    assert summary["code_ready"] is True
    assert summary["safe_equivalent_rows"] > 0
    assert summary["operator_qualification_rows"] > 0
    assert summary["complete"] is False


def test_require_code_ready_passes_on_committed_ledgers(tmp_path) -> None:
    rc = gap_ledger.main([
        "--docs-dir", str(REPO_ROOT / "docs" / "archive" / "2026-09-21"),
        "--out", str(tmp_path / "LEDGER.md"),
        "--require-code-ready"])
    assert rc == 0


def test_require_code_ready_fails_on_non_terminal_row(tmp_path) -> None:
    docs = tmp_path / "docs"
    _write_sections(docs, None, status="MISSING", needs="owner")
    rc = gap_ledger.main([
        "--docs-dir", str(docs), "--out", str(tmp_path / "LEDGER.md"),
        "--require-code-ready"])
    assert rc == 1


def test_require_code_ready_accepts_terminal_gate_rows(tmp_path) -> None:
    for status, needs in (("SAFE_EQUIVALENT", "owner"),
                          ("OPERATOR_ONLY", "hardware")):
        docs = tmp_path / f"docs_{status}"
        _write_sections(docs, None, status=status, needs=needs)
        rc = gap_ledger.main([
            "--docs-dir", str(docs), "--out", str(tmp_path / "LEDGER.md"),
            "--require-code-ready"])
        assert rc == 0, status


def test_code_ready_check_reports_every_offender() -> None:
    rows = []
    for i, status in enumerate(("MISSING", "PARTIAL", "UNVERIFIED")):
        row = {key: "" for key in gap_ledger.ROW_KEYS}
        row.update({"id": f"open{i}", "area": "a", "capability": "c",
                    "status": status, "needs": "owner"})
        row["_source"] = "qubit"
        rows.append(row)
    ok, reason = gap_ledger.code_ready_check([{"rows": rows}])
    assert ok is False
    assert "3 non-terminal row" in reason
    assert "open0(MISSING)" in reason
    ok, reason = gap_ledger.code_ready_check([{"rows": []}])
    assert ok is True and reason == ""


def test_omissions_summary_in_json_output(tmp_path, capsys) -> None:
    docs = tmp_path / "docs"
    _write_sections(docs, {
        "present": True, "items_total": 1, "surface_items": 1, "matched": 0,
        "unmatched": 1, "unexplained_omissions": 1, "omitted_ids": ["x"],
        "error": "",
    })
    rc = gap_ledger.main([
        "--docs-dir", str(docs), "--out", str(tmp_path / "LEDGER.md"), "--json"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert '"unexplained_omissions": 1' in captured
    assert '"omitted"' in captured


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
