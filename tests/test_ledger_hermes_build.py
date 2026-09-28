"""Invariants for the evidence-based Hermes gap-ledger regenerator.

The regenerator (``scripts/rebuild_ledger_hermes.py``) must be deterministic
and must never claim ``IMPLEMENTED`` without a behavioural proof: every
``IMPLEMENTED`` row carries a non-empty ``proving_test`` that exists on disk,
and no ``IMPLEMENTED`` row cites an unregistered ``qubit_compat`` handler.
"""
from __future__ import annotations

import importlib.util
import json
import re
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


rebuild = _load_module("rebuild_ledger_hermes", "rebuild_ledger_hermes.py")
gap_ledger = _load_module("gap_ledger", "gap_ledger.py")

from voicebridge import qubit_compat as HC  # noqa: E402

SECTION_PATH = REPO_ROOT / "docs/generated" / "QUBIT_GAP_LEDGER.hermes.json"
_HANDLER_RE = re.compile(r"^voicebridge/qubit_compat\.py:(\w+)$")


def _section() -> dict:
    return json.loads(SECTION_PATH.read_text(encoding="utf-8"))


def test_schema_is_valid() -> None:
    data = _section()
    # gap_ledger raises SchemaError on any missing key / bad status / bad needs.
    validated = gap_ledger.validate_section("hermes", data)
    assert validated["schema"] == "qubit-gap-ledger-section/1"
    assert validated["source"] == "hermes"
    assert validated["rows"]
    for row in data["rows"]:
        assert set(row) == set(gap_ledger.ROW_KEYS), sorted(
            set(row) ^ set(gap_ledger.ROW_KEYS))
    ids = [row["id"] for row in data["rows"]]
    assert len(ids) == len(set(ids)), "duplicate ledger row ids"


def test_output_is_deterministic() -> None:
    first = rebuild._render(rebuild.build_ledger())
    second = rebuild._render(rebuild.build_ledger())
    assert first == second


def test_output_is_deterministic_across_hash_seeds(tmp_path) -> None:
    """A fresh process with a different hash seed reproduces the committed JSON.

    Proof selection must never depend on set-iteration order.
    """
    import os
    import subprocess

    env = dict(os.environ)
    env["PYTHONHASHSEED"] = "1"
    out = tmp_path / "hermes.json"
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" /
                             "rebuild_ledger_hermes.py"), "--out", str(out)],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
        timeout=300)
    assert result.returncode == 0, result.stderr[-800:]
    assert out.read_text(encoding="utf-8") == \
        SECTION_PATH.read_text(encoding="utf-8")


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


def test_no_implemented_row_cites_unknown_handler() -> None:
    offenders = []
    for row in _section()["rows"]:
        if row["status"] != "IMPLEMENTED":
            continue
        match = _HANDLER_RE.match(row["qubit_backend"])
        if match and match.group(1) not in HC.HANDLERS:
            offenders.append((row["id"], row["qubit_backend"]))
    assert offenders == [], offenders


def test_counts_match_rows() -> None:
    data = _section()
    rows = data["rows"]
    assert data["counts"]["total"] == len(rows)
    assert data["counts"]["by_status"] == {
        status: sum(1 for r in rows if r["status"] == status)
        for status in sorted({r["status"] for r in rows})
    }
    assert data["counts"]["by_area"] == {
        area: sum(1 for r in rows if r["area"] == area)
        for area in sorted({r["area"] for r in rows})
    }


# ---------------------------------------------------------------------------
# CLI / cli-subcommand evidence rules
# ---------------------------------------------------------------------------
def test_cli_family_rows_are_caller_and_test_derived() -> None:
    """Every ``cli`` family row follows live-caller + on-disk-test evidence.

    The hardcoded audit ``surface`` column may no longer force PARTIAL: a live
    ``qubit`` family exercised by a ``tests/`` function is IMPLEMENTED.
    """
    families, _names = rebuild._parser_tree()
    index = rebuild._build_test_index(set(families))
    rows = {r["id"]: r for r in _section()["rows"] if r["area"] == "cli"}
    offenders = []
    for cmd, qfam, surface, _qubit, _note, _ref in rebuild.CLI:
        row = rows.get(f"cli:{cmd}")
        assert row is not None, cmd
        caller = bool(qfam) and qfam in families
        proof = index.family_proof(qfam) if qfam else ""
        expected = rebuild._terminal_status(
            rebuild._derive_cli_status(surface, caller, bool(proof),
                                       forwarded=False), row["needs"])
        if row["status"] != expected:
            offenders.append((cmd, row["status"], expected))
    assert offenders == [], offenders
    # The defect this guards: a live family with an on-disk test stays open.
    partial_but_proven = [
        row["id"] for row in rows.values()
        if row["status"] == "PARTIAL" and row["proving_test"]
    ]
    assert partial_but_proven == [], partial_but_proven


def test_cli_subcommand_rows_are_caller_and_test_derived() -> None:
    families, _names = rebuild._parser_tree()
    index = rebuild._build_test_index(set(families))
    forward = rebuild._fwd.FORWARD_TABLE
    rows = {r["id"]: r for r in _section()["rows"]
            if r["area"] == "cli-subcommand"}
    offenders = []
    for fam, sub, qfam, qsub, surface, _qubit, _ref in rebuild.SUBS:
        row = rows.get(f"cli:{fam} {sub}")
        assert row is not None, (fam, sub)
        caller, forwarded, proof = rebuild._sub_caller_proof(
            qfam, qsub, families, index, forward)
        expected = rebuild._derive_cli_status(surface, caller, bool(proof),
                                              forwarded=forwarded)
        if expected == "IMPLEMENTED" and not proof:
            expected = "UNVERIFIED"
        expected = rebuild._terminal_status(expected, row["needs"])
        if row["status"] != expected:
            offenders.append((fam, sub, row["status"], expected))
    assert offenders == [], offenders


def test_cli_derive_never_returns_partial_when_proven() -> None:
    for surface in ("PARTIAL", "MISSING", "IMPLEMENTED"):
        assert rebuild._derive_cli_status(surface, True, True, False) == \
            "IMPLEMENTED", surface
    # A policy gate always wins over caller/test evidence.
    assert rebuild._derive_cli_status("GATED", True, True, False) == "GATED"
    # An explicit GATED surface is a policy refusal: it is never counted as
    # IMPLEMENTED even when a read-only forward or native caller exists (a
    # forwarded gated surface would be a bypass).
    assert rebuild._derive_cli_status("GATED", True, True, True) == "GATED"
    # No caller keeps the honest open status.
    assert rebuild._derive_cli_status("PARTIAL", False, False, False) == \
        "PARTIAL"
    assert rebuild._derive_cli_status("MISSING", False, False, False) == \
        "MISSING"


# ---------------------------------------------------------------------------
# API handler-evidence rules
# ---------------------------------------------------------------------------
REAL_HANDLERS = (
    "config_save", "env_write", "toolset_write", "skill_update",
    "h_skills_toggle", "mem_config_write", "h_memory", "toolset_config",
    "toolset_models", "h_model_auxiliary", "h_model_moa", "h_ops",
    "h_events", "mem_config", "mem_provider_set", "h_profile_setup_command",
)


def test_denylists_are_typed_gates_and_stubs() -> None:
    """The denylists only carry genuine gates / wire-shape stubs."""
    for handler in rebuild.GATE_HANDLERS:
        assert rebuild._handler_kind(handler) == "gate", handler
    for handler in rebuild.STUB_HANDLERS:
        assert rebuild._handler_kind(handler) == "stub", handler


def test_real_handlers_are_not_denylisted() -> None:
    """Handlers that now perform a real read/write are IMPLEMENTED, not gated."""
    for handler in REAL_HANDLERS:
        assert handler not in rebuild.GATE_HANDLERS, handler
        assert handler not in rebuild.STUB_HANDLERS, handler
        assert rebuild._handler_kind(handler) == "real", handler


def test_implemented_api_rows_use_real_handlers_and_evidence() -> None:
    index = rebuild.build_api_index()
    offenders = []
    for row in _section()["rows"]:
        if row["status"] != "IMPLEMENTED":
            continue
        match = _HANDLER_RE.match(row["qubit_backend"])
        if not match:
            continue
        handler = match.group(1)
        if rebuild._handler_kind(handler) != "real":
            offenders.append((row["id"], handler, "not a real handler"))
            continue
        method, _, path = row["capability"].partition(" ")
        override_test = rebuild.ROUTE_EVIDENCE.get(
            (method, path), {}).get("test", "")
        if not index.proof(handler) and not override_test:
            offenders.append((row["id"], handler, "no AST evidence"))
    assert offenders == [], offenders


def test_all_rows_are_terminal_or_open() -> None:
    """No row may keep a pre-terminal status once the generator finalizes."""
    rows = _section()["rows"]
    non_terminal = [(r["id"], r["status"]) for r in rows
                    if r["status"] not in gap_ledger.TERMINAL_STATUSES]
    assert non_terminal == [], non_terminal
    # The pre-2026-09-13 gate statuses must not leak into the committed section.
    for stale in ("GATED", "PARTIAL", "MISSING", "UNVERIFIED", "CLI_ONLY",
                  "DASHBOARD_ONLY", "VOICE_ONLY"):
        assert not [r["id"] for r in rows if r["status"] == stale], stale


def test_terminal_rows_have_honest_needs_and_proof() -> None:
    """SAFE_EQUIVALENT / OPERATOR_ONLY rows stay visible with real evidence."""
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


def test_terminal_status_mapping_follows_owner_decision() -> None:
    """GATED -> SAFE_EQUIVALENT; PARTIAL(creds/hardware + typed note) -> OPERATOR_ONLY."""
    typed = "Typed refusal: COMPUTER_USE_NO_DRIVER."
    assert rebuild._terminal_status("GATED", "owner") == "SAFE_EQUIVALENT"
    assert rebuild._terminal_status("GATED", "creds") == "SAFE_EQUIVALENT"
    assert rebuild._terminal_status("GATED", "hardware") == "SAFE_EQUIVALENT"
    assert rebuild._terminal_status("PARTIAL", "creds", typed) == "OPERATOR_ONLY"
    assert rebuild._terminal_status("PARTIAL", "hardware", typed) == \
        "OPERATOR_ONLY"
    # A PARTIAL with no genuine live prerequisite is not silently closed.
    assert rebuild._terminal_status("PARTIAL", "owner", typed) == "PARTIAL"
    assert rebuild._terminal_status("PARTIAL", "none", typed) == "PARTIAL"
    assert rebuild._terminal_status("UNVERIFIED", "creds", typed) == \
        "UNVERIFIED"
    assert rebuild._terminal_status("IMPLEMENTED", "none") == "IMPLEMENTED"
    # Per-route gate needs: secrets => creds, hardware => hardware, else owner.
    assert rebuild._needs_for("GATED", {}, "", "SECRET_NEVER_SERVED") == "creds"
    assert rebuild._needs_for("GATED", {}, "", "LOCAL_MODEL_BROKERED") == \
        "hardware"
    assert rebuild._needs_for("GATED", {}, "", "MCP_NO_MOUNT") == "owner"


def test_partial_remap_requires_a_typed_code_in_the_note() -> None:
    """A PARTIAL/stub row must stay open unless its note records a typed code.

    Guards the owner criterion that PARTIAL -> OPERATOR_ONLY is only for a
    code-complete, typed live-prerequisite path, never a bare stub.
    """
    # needs=creds but the note is a bare stub: stays open, not OPERATOR_ONLY.
    assert rebuild._terminal_status("PARTIAL", "creds", "stub") == "PARTIAL"
    assert rebuild._terminal_status("PARTIAL", "hardware", "") == "PARTIAL"
    # A typed state token also counts (the amendment allows a typed state).
    assert rebuild._terminal_status(
        "PARTIAL", "hardware", "driver state MISSING") == "OPERATOR_ONLY"
    # needs=none never closes even with a typed code.
    assert rebuild._terminal_status(
        "PARTIAL", "none", "Typed refusal: FOO_BAR.") == "PARTIAL"
    # End to end through the finalizer.
    rows = [
        {"id": "stub", "status": "PARTIAL", "needs": "creds",
         "proving_test": "", "note": "Subcommand-surface gap.", "backend": ""},
        {"id": "typed", "status": "PARTIAL", "needs": "hardware",
         "proving_test": "x", "note": "Typed refusal: FOO_BAR."},
    ]
    out = rebuild._finalize_statuses([dict(r) for r in rows])
    assert [r["status"] for r in out] == ["PARTIAL", "OPERATOR_ONLY"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
