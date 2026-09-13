"""Invariants for the evidence-based Qubit gap-ledger regenerator.

The regenerator (``scripts/rebuild_ledger_qubit.py``) must be deterministic,
keep the fixed schema/row keys ``scripts/gap_ledger.py`` validates, and never
claim ``IMPLEMENTED`` without proof:

* every ``IMPLEMENTED`` row carries a non-empty ``proving_test`` that exists
  on disk;
* every ``IMPLEMENTED`` row cites a non-empty production backend;
* every ``IMPLEMENTED`` row has all of its declared (applicable) surfaces;
* ``plugin_*`` / ``page_*`` rows declare only the dashboard surface (no
  CLI/voice interface by design), so an on-disk production-route proving test
  completes that applicable surface (``IMPLEMENTED``), while no proof leaves
  them ``UNVERIFIED``;
* the qubit section merges with the hermes/openclaw sections without a schema
  violation.
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


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(
        name, REPO_ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rebuild = _load_module("rebuild_ledger_qubit", "rebuild_ledger_qubit.py")
gap_ledger = _load_module("gap_ledger", "gap_ledger.py")

SECTION_PATH = REPO_ROOT / "docs" / "QUBIT_GAP_LEDGER.qubit.json"


def _section() -> dict:
    return json.loads(SECTION_PATH.read_text(encoding="utf-8"))


def test_schema_and_row_keys() -> None:
    data = _section()
    # gap_ledger raises SchemaError on any missing key / bad status / bad needs.
    validated = gap_ledger.validate_section("qubit", data)
    assert validated["schema"] == "qubit-gap-ledger-section/1"
    assert validated["source"] == "qubit-surface"
    assert validated["rows"]
    for row in data["rows"]:
        assert set(row) == set(gap_ledger.ROW_KEYS), sorted(
            set(row) ^ set(gap_ledger.ROW_KEYS))
    ids = [row["id"] for row in data["rows"]]
    assert len(ids) == len(set(ids)), "duplicate ledger row ids"
    assert data["generated_from"], "missing provenance generated_from"
    assert data["counts_by_status"] == {
        status: sum(1 for r in data["rows"] if r["status"] == status)
        for status in sorted({r["status"] for r in data["rows"]})
    }
    assert data["counts_by_source"] == {"qubit-surface": len(data["rows"])}


def test_output_is_deterministic() -> None:
    first = rebuild._render(rebuild.build_ledger())
    second = rebuild._render(rebuild.build_ledger())
    assert first == second


def test_implemented_rows_have_existing_proving_test() -> None:
    missing = []
    for row in _section()["rows"]:
        if row["status"] != "IMPLEMENTED":
            continue
        proving = row["proving_test"]
        if not proving:
            missing.append((row["id"], proving, "empty"))
            continue
        path = proving.split("::", 1)[0]
        if not (REPO_ROOT / path).is_file():
            missing.append((row["id"], proving, "not on disk"))
    assert missing == [], missing


def test_no_implemented_row_has_empty_backend() -> None:
    offenders = [(r["id"], r["qubit_backend"]) for r in _section()["rows"]
                 if r["status"] == "IMPLEMENTED" and not r["qubit_backend"]]
    assert offenders == [], offenders


def _applicable_surfaces(row: dict, catalog: dict) -> set:
    """The surfaces a row's capability genuinely declares.

    Catalog ops use the declared ``interfaces``; page/plugin rows declare only
    the dashboard surface (they have no CLI/voice entry point by design).
    """
    if row["id"] in catalog:
        return set(catalog[row["id"]].get("interfaces") or ())
    if row["id"].startswith(("page_", "plugin_")):
        return set(rebuild.PAGE_PLUGIN_INTERFACES)
    return set()


def test_implemented_rows_have_every_applicable_surface() -> None:
    from voicebridge import dashboard_ops as DO

    catalog = {o["id"]: o for o in DO.catalog()}
    offenders = []
    for row in _section()["rows"]:
        if row["status"] != "IMPLEMENTED":
            continue
        applicable = _applicable_surfaces(row, catalog)
        present = {s for s, key in (
            ("cli", "cli"), ("dashboard", "dashboard"), ("voice", "voice"))
            if row.get(key)}
        missing = applicable - present
        if missing:
            offenders.append((row["id"], sorted(missing)))
    assert offenders == [], offenders


def test_page_plugin_applicable_surface_rule() -> None:
    """Page/plugin rows are dashboard-only and only IMPLEMENTED with proof."""
    offenders = []
    for row in _section()["rows"]:
        if not row["id"].startswith(("page_", "plugin_")):
            continue
        # the declared surface is exactly the dashboard
        if not row["dashboard"]:
            offenders.append((row["id"], "missing dashboard surface"))
        if row["cli"] or row["voice"]:
            offenders.append((row["id"], "unexpected CLI/voice surface"))
        # a proof is required for IMPLEMENTED, and IMPLEMENTED requires proof
        if row["status"] == "IMPLEMENTED":
            if not row["proving_test"]:
                offenders.append((row["id"], "IMPLEMENTED without proof"))
        elif row["status"] != "UNVERIFIED":
            offenders.append((row["id"], row["status"]))
        elif row["proving_test"]:
            offenders.append((row["id"], "UNVERIFIED with proof"))
    assert offenders == [], offenders


def test_plugin_page_rows_use_test_evidence() -> None:
    """Every plugin/page row's status follows its on-disk route evidence."""
    offenders = []
    seen = {"plugin": 0, "page": 0}
    for row in _section()["rows"]:
        family = ("plugin" if row["id"].startswith("plugin_")
                  else "page" if row["id"].startswith("page_") else None)
        if family is None:
            continue
        seen[family] += 1
        proof = row["proving_test"]
        expected = "IMPLEMENTED" if proof else "UNVERIFIED"
        if row["status"] != expected:
            offenders.append((row["id"], row["status"], expected))
        if proof:
            path = proof.split("::", 1)[0]
            if not (REPO_ROOT / path).is_file():
                offenders.append((row["id"], proof, "proof not on disk"))
            if not proof.startswith("tests/test_"):
                offenders.append((row["id"], proof, "not a tests/ proof"))
    assert offenders == [], offenders
    # Agent 3's production-route tests prove every plugin and every page.
    assert seen["plugin"] >= 10, seen
    assert seen["page"] >= 19, seen


def test_page_plugin_evidence_rule_branches(tmp_path) -> None:
    """An on-disk production-route proof => IMPLEMENTED; else UNVERIFIED."""
    index = rebuild.build_test_index()
    # every discovered id is proven by a production-route test in tests/
    for row_id in sorted(rebuild.PAGE_PLUGIN_IDS):
        proof = rebuild.find_page_plugin_proof(index, row_id)
        assert proof, row_id
        assert proof.startswith("tests/test_"), (row_id, proof)
        assert (REPO_ROOT / proof.split("::", 1)[0]).is_file(), (row_id, proof)
    # an id with no matching route test has no proof
    assert rebuild.find_page_plugin_proof(
        index, "page_agent10_no_such_page") == ""
    assert rebuild.find_page_plugin_proof(
        index, "plugin_agent10-no-such-plugin") == ""
    # on-disk route proof => IMPLEMENTED; a missing proof/test => UNVERIFIED
    assert rebuild._page_plugin_status(
        "tests/test_dashboard_agent3_wiring.py::test_x") == "IMPLEMENTED"
    assert rebuild._page_plugin_status("") == "UNVERIFIED"
    assert rebuild._page_plugin_status(
        "tests/test_agent10_does_not_exist.py::test_y") == "UNVERIFIED"
    # these families declare only the dashboard surface
    assert set(rebuild.PAGE_PLUGIN_INTERFACES) == {"dashboard"}

    real_page = (REPO_ROOT / "frontend/hermes-web/src/pages/AnalyticsPage.tsx")
    real_manifest = (REPO_ROOT / "frontend/hermes-qubit/plugins/qubit-ops/"
                     "manifest.json")
    assert real_page.is_file() and real_manifest.is_file()

    proven_page = rebuild.build_page_rows(index, [real_page])[0]
    assert proven_page["status"] == "IMPLEMENTED", proven_page
    assert proven_page["proving_test"], proven_page

    ghost_page = tmp_path / "Agent10Synthetic.tsx"
    ghost_page.write_text("export default function Agent10Synthetic() {}\n")
    unproven_page = rebuild.build_page_rows(index, [ghost_page])[0]
    assert unproven_page["status"] == "UNVERIFIED", unproven_page
    assert unproven_page["proving_test"] == "", unproven_page

    proven_plugin = rebuild.build_plugin_rows(index, [real_manifest])[0]
    assert proven_plugin["status"] == "IMPLEMENTED", proven_plugin
    assert proven_plugin["proving_test"], proven_plugin

    ghost_manifest = tmp_path / "manifest.json"
    ghost_manifest.write_text(json.dumps({
        "name": "agent10-synthetic", "label": "Synthetic",
        "description": "synthetic", "entry": "synthetic.js",
        "tab": {"path": "/synthetic"},
    }))
    unproven_plugin = rebuild.build_plugin_rows(index, [ghost_manifest])[0]
    assert unproven_plugin["status"] == "UNVERIFIED", unproven_plugin
    assert unproven_plugin["proving_test"] == "", unproven_plugin


def test_all_rows_are_terminal_or_documented_open() -> None:
    """Every committed row is terminal except the one documented open row.

    ``skills/index-cache`` is a vendored JSON asset cache with no loadable
    ``SKILL.md``; it is a non-capability and is excluded from the surface
    inventory entirely, so the committed ledger has no open row.  Any
    non-terminal row fails this gate.
    """
    rows = _section()["rows"]
    allowed_open = set()
    non_terminal = [(r["id"], r["status"]) for r in rows
                    if r["status"] not in gap_ledger.TERMINAL_STATUSES]
    assert sorted(r[0] for r in non_terminal) == sorted(allowed_open)
    # The pre-2026-09-13 gate statuses must not leak in for any other row.
    for stale in ("GATED", "PARTIAL", "MISSING", "UNVERIFIED", "CLI_ONLY",
                  "DASHBOARD_ONLY", "VOICE_ONLY"):
        offenders = [r["id"] for r in rows if r["status"] == stale]
        assert set(offenders) <= allowed_open, (stale, offenders)


def test_terminal_rows_have_proof_and_valid_needs() -> None:
    """SAFE_EQUIVALENT / OPERATOR_ONLY rows keep real evidence and a live need."""
    allowed = {"creds", "hardware", "owner"}
    for row in _section()["rows"]:
        if row["status"] not in ("SAFE_EQUIVALENT", "OPERATOR_ONLY"):
            continue
        assert row["needs"] in allowed, (row["id"], row["needs"])
        assert row["needs"] != "none", row["id"]
        proving = row["proving_test"]
        assert proving, (row["id"], "empty proving_test")
        assert (REPO_ROOT / proving.split("::", 1)[0]).is_file(), \
            (row["id"], proving)


def test_browser_close_rows_are_safe_equivalents() -> None:
    """The browser close rows are typed refusals, not hardware-gated manual rows."""
    rows = {r["id"]: r for r in _section()["rows"]}
    for rid in ("browser_close", "browser_close_refusal"):
        row = rows[rid]
        assert row["status"] == "SAFE_EQUIVALENT", (rid, row["status"])
        assert row["needs"] == "owner", (rid, row["needs"])
        assert "BROWSER_CLOSE_REFUSED" in row["note"], rid
        assert row["proving_test"], rid
        assert (REPO_ROOT / row["proving_test"].split("::", 1)[0]).is_file()


def test_terminal_status_mapping_follows_owner_decision() -> None:
    """GATED(+backend) -> SAFE_EQUIVALENT; PARTIAL(creds/hw + typed) -> OPERATOR_ONLY."""
    rows = [
        {"id": "a", "status": "GATED", "needs": "owner", "proving_test": "x",
         "qubit_backend": "b", "note": "gate"},
        {"id": "b", "status": "GATED", "needs": "creds", "proving_test": "x",
         "qubit_backend": "b", "note": "gate"},
        {"id": "c", "status": "GATED", "needs": "hardware", "proving_test": "x",
         "qubit_backend": "b", "note": "gate"},
        {"id": "d", "status": "PARTIAL", "needs": "creds", "proving_test": "x",
         "note": "Typed refusal: FOO_BAR."},
        {"id": "e", "status": "PARTIAL", "needs": "hardware", "proving_test": "x",
         "note": "driver state MISSING"},
        {"id": "f", "status": "PARTIAL", "needs": "owner", "proving_test": "x",
         "note": "Typed refusal: FOO_BAR."},
        {"id": "g", "status": "MISSING", "needs": "owner", "proving_test": "",
         "note": ""},
        {"id": "h", "status": "IMPLEMENTED", "needs": "none", "proving_test": "x",
         "note": ""},
    ]
    out = rebuild._finalize_terminal_statuses([dict(r) for r in rows])
    assert [r["status"] for r in out] == [
        "SAFE_EQUIVALENT", "SAFE_EQUIVALENT", "SAFE_EQUIVALENT",
        "OPERATOR_ONLY", "OPERATOR_ONLY", "PARTIAL", "MISSING", "IMPLEMENTED",
    ]


def test_partial_remap_requires_a_typed_code_and_a_backend() -> None:
    """A PARTIAL/stub or a backend-less GATED row stays open (negative guard)."""
    # PARTIAL needs=creds but the note is a bare stub: stays open.
    assert rebuild._finalize_terminal_statuses([{
        "id": "stub", "status": "PARTIAL", "needs": "creds",
        "proving_test": "", "qubit_backend": "", "note": "stub",
    }])[0]["status"] == "PARTIAL"
    # needs=none never closes even with a typed code.
    assert rebuild._finalize_terminal_statuses([{
        "id": "nones", "status": "PARTIAL", "needs": "none",
        "proving_test": "", "qubit_backend": "", "note": "Typed refusal: FOO_BAR.",
    }])[0]["status"] == "PARTIAL"
    # A GATED row with no backend path is not a safe equivalent.
    gated = rebuild._finalize_terminal_statuses([{
        "id": "no-backend", "status": "GATED", "needs": "owner",
        "proving_test": "", "qubit_backend": "",
        "note": "no loadable manifest",
    }])[0]
    assert gated["status"] == "GATED", gated


def test_missing_registry_entry_is_flagged_not_fatal() -> None:
    """A catalog id with no registry/extra entry is flagged, never faked."""
    qualified, has_backend, _key, _fixed = rebuild.resolve_backend(
        "agent10_no_such_op")
    assert qualified == ""
    assert has_backend is False
    assert rebuild.op_candidates("agent10_no_such_backend", ()) == set()

    rows = rebuild.build_catalog_rows(
        {"agent10_ghost": {
            "id": "agent10_ghost", "group": "ghost", "title": "Ghost",
            "help": "", "interfaces": ["cli", "dashboard", "voice"],
        }},
        rebuild._parser_tree(), rebuild.build_test_index())
    assert len(rows) == 1
    assert rows[0]["qubit_backend"] == ""
    assert rows[0]["status"] != "IMPLEMENTED", rows[0]
    assert "no resolvable production backend" in rows[0]["note"].lower(), rows


def test_all_sections_merge_without_schema_violation() -> None:
    docs = REPO_ROOT / "docs"
    sections, missing, warnings = gap_ledger.load_sections(docs)
    assert missing == [], f"missing required ledger sections: {missing}"
    assert {s["file_section"] for s in sections} == set(gap_ledger.SECTIONS)
    summary = gap_ledger.build_summary(sections, missing)
    assert summary["total_rows"] == sum(len(s["rows"]) for s in sections)
    assert summary["by_source"]
    # The regenerated qubit section must not introduce extra row keys.
    assert not any("qubit" in w and "extra row keys" in w for w in warnings), \
        warnings


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
