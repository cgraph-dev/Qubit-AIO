"""Wave 6 — audited disposition for the CURRENT-PIN OpenClaw extensions.

``EXTENSION_MAP`` is frozen to the audited v2026.9.4 set and ``WAVE5_FORMS``
covers only its 30 wave-5 names, so every newer extension reached the ledger
with no form and stayed MISSING even where Qubit really ships the capability.
``WAVE6_AUDIT`` records the audited answer, decided one extension at a time
against real code:

* ``implemented`` -> the ``form`` is a real ``file.py symbol(s)`` and the
  ``proving`` test exists and exercises it;
* ``gated`` -> a typed refusal naming the real gate code and need;
* absent -> no Qubit equivalent, which stays an honest MISSING row.

This test is the guard that keeps the table from drifting into fiction: a form
naming a symbol that does not exist, or a proving test that is not on disk,
fails here rather than silently closing a row.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SECTION_PATH = REPO_ROOT / "docs/generated/QUBIT_GAP_LEDGER.qubit.json"

_TYPED_CODE_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
_NEEDS = ("none", "creds", "hardware", "owner")


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(
        name, REPO_ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rebuild_openclaw = _load("rebuild_ledger_openclaw",
                         "rebuild_ledger_openclaw.py")
source_inventory = _load("source_inventory_wave6", "source_inventory.py")

WAVE6 = rebuild_openclaw.WAVE6_AUDIT


def test_wave6_keys_are_typed_and_agree_with_wave5() -> None:
    assert WAVE6, "wave-6 audit table is empty"
    for name, rec in WAVE6.items():
        assert rec["status"] in ("implemented", "gated"), name
        assert rec["parent"], name
        assert rec["why"].strip(), name
        assert rec["form"].strip(), name
        needs = rec.get("needs") or ("creds"
                                     if rec["status"] == "gated" else "none")
        assert needs in _NEEDS, (name, needs)
        if rec["status"] == "gated":
            assert _TYPED_CODE_RE.match(rec["gate"] or ""), (name, rec["gate"])
        # An extension already audited by wave 5 must keep its form; wave 6
        # closes a missing form, it does not re-decide the capability.
        wave5 = rebuild_openclaw.WAVE5_FORMS.get(name)
        if wave5 is not None:
            assert rec["form"] == wave5["form"], name


def test_wave6_forms_and_proving_tests_exist_on_disk() -> None:
    """Every IMPLEMENTED form names a real module + symbol, and a real test."""
    for name, rec in WAVE6.items():
        proving = rec.get("proving") or ""
        if rec["status"] == "implemented":
            assert proving, (name, "implemented with no proving test")
        if proving:
            assert (REPO_ROOT / proving.split("::", 1)[0]).is_file(), (
                name, proving)
        module_part, _, symbols_text = rec["form"].partition(" ")
        module = REPO_ROOT / module_part
        assert module.is_file(), (name, module_part)
        if rec["status"] != "implemented":
            continue
        source = module.read_text(encoding="utf-8")
        for symbol in symbols_text.split(" (")[0].split("/"):
            assert (f"def {symbol}" in source or f"class {symbol}" in source
                    or f"{symbol}:" in source or f"{symbol} =" in source), (
                name, module_part, symbol)


def test_wave6_disposition_matches_the_table() -> None:
    for name, rec in WAVE6.items():
        disposition = source_inventory._openclaw_disposition(
            {"category": "openclaw_extension", "name": name})
        assert disposition is not None, name
        assert disposition["status"] == (
            "IMPLEMENTED" if rec["status"] == "implemented" else "GATED"), name
        assert disposition["backend"] == rec["form"], name
        if rec["status"] == "implemented":
            assert disposition["proving_test"], name


def test_wave6_names_absent_from_the_table_stay_undispositioned() -> None:
    """An extension with no audited answer must NOT be dispositioned.

    This is the honesty guard: a name the audit left out is a real gap, and
    dispositioning it would be a fabricated implementation.
    """
    for name in ("open-prose", "phone-control", "qqbot", "qa-matrix",
                 "thread-ownership", "video-generation-core",
                 "media-understanding-core", "codex-supervisor"):
        assert name not in WAVE6, name
        assert source_inventory._openclaw_disposition(
            {"category": "openclaw_extension", "name": name}) is None, name


def test_wave6_implemented_rows_carry_a_real_backend_and_proof() -> None:
    """The ledger rows wave 6 closes are IMPLEMENTED with an on-disk proof."""
    if not SECTION_PATH.is_file():
        pytest.skip("docs/generated/QUBIT_GAP_LEDGER.qubit.json is a "
                    "gitignored generated artifact; regenerate it with "
                    "scripts/rebuild_ledger_qubit.py")
    section = json.loads(SECTION_PATH.read_text(encoding="utf-8"))
    rows = {row["id"]: row for row in section["rows"]}
    checked = 0
    for name, rec in WAVE6.items():
        row = rows.get(f"inv:openclaw-ext:{name}")
        if row is None:
            continue
        checked += 1
        assert row["status"] in ("IMPLEMENTED", "SAFE_EQUIVALENT"), (
            name, row["status"])
        assert row["qubit_backend"], name
        assert row["proving_test"], name
        assert (REPO_ROOT / row["proving_test"].split("::", 1)[0]).is_file(), (
            name, row["proving_test"])
    assert checked, "no wave-6 extension rows found in the ledger section"
