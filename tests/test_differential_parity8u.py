"""Hermetic contract tests for the differential parity runner (agent 8u).

The runner lives at ``scripts/differential_parity.py`` and is loaded
dynamically by path so these tests are robust to package layout. While the
file is absent they ``pytest.skip`` (never fail); the frozen contract is
``/tmp/qubit-differential/INTERFACE.md`` (FROZEN).

Hermetic: no network, no third-party imports, all scratch under ``tmp_path``.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import http.server
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = REPO_ROOT / "scripts" / "differential_parity.py"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "differential"
MANIFEST_PATH = FIXTURES_DIR / "MANIFEST.json"
ROW_RESULT_SCHEMA_PATH = FIXTURES_DIR / "schema" / "row_result.schema.json"

# Contractual upstream pins mirrored from the frozen pins in OWNERSHIP.md.
PIN_QUBIT = "d337b736aa1e8ebecfab043842d13e4a2d2f48a3"
PIN_OPENCLAW = "3a9d69db306cd7f081e06254cb89c4bcc14a7107"
VISUAL_REF_FIELDS = ("screenshot_ref", "dom_snapshot_ref", "a11y_snapshot_ref")

# INTERFACE.md: allowlist is EXACT; anything else is an error.
ALLOWED_NORMALIZERS = frozenset({
    "timestamp",
    "uuid",
    "temp_path",
    "pid",
    "session_token",
    "generated_hash",
    "product_branding",
})

# INTERFACE.md fail-closed gates: verify must emit a result for every one.
FAIL_CLOSED_GATES = (
    "unexecuted_row",
    "missing_production_caller",
    "skipped_fixture",
    "mock_replaced_real_path",
    "refusal_counted_as_success",
    "unsafe_normalizer",
    "visual_only_control",
    "voice_backend_mismatch",
    "persistence_or_recovery_diff",
    "secret_in_artifact",
    # v1.1 addendum.
    "openclaw_undisposed_addition",
    # v1.2 integration: an OPEN row is not terminal and fails the report.
    "unexecuted_open_row",
    # v1.3 integration: no PASS without an executed upstream reference.
    "pass_without_reference",
    # v1.4: pinned-reference honesty and explicit inventory coverage.
    "unpinned_reference_pass",
    "inventory_coverage",
    # v1.5: a non-executed PASS claim is refused fail-closed.
    "pass_without_proof",
)

# A row result may only be terminal (OPEN is an unexecuted omission).
TERMINAL_DISPOSITIONS = frozenset(
    {"PASS", "FAIL", "SAFE_EQUIVALENT", "OPERATOR_ONLY"})

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}=*"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token"
        r"|client[_-]?secret|secret[_-]?key)\b\s*[:=]\s*[\"']?"
        r"[A-Za-z0-9._~+/-]{16,}"),
)


def _load_runner():
    """Load ``scripts/differential_parity.py`` dynamically by path.

    The module is registered in ``sys.modules`` before execution: a frozen
    dataclass combined with ``from __future__ import annotations`` makes
    ``dataclasses`` look the defining module up in ``sys.modules``, so an
    unregistered spec cannot be executed.
    """
    import importlib.util
    import pathlib
    p = (pathlib.Path(__file__).resolve().parents[1]
         / "scripts" / "differential_parity.py")
    spec = importlib.util.spec_from_file_location("differential_parity", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return mod


@pytest.fixture(scope="module")
def runner():
    if not RUNNER_PATH.is_file():
        pytest.skip(f"differential runner not written yet: {RUNNER_PATH}")
    return _load_runner()


# --------------------------------------------------------------- shared helpers
def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def _diag_field(diag, name):
    if isinstance(diag, dict):
        return diag.get(name)
    return getattr(diag, name, None)


def _diag_paths(diags) -> list[str]:
    paths = []
    for diag in diags:
        path = _diag_field(diag, "path")
        if path is not None:
            paths.append(path)
    return paths


def _gate_ok_map(returned) -> dict[str, bool]:
    """Collect ``gate -> ok`` from any apply_fail_closed_gates shape.

    Tolerates a list of gate dicts, a mapping, a report dict with ``gates``, or
    per-row result objects carrying a ``gates`` list. Aggregation is AND (fail
    closed): if any occurrence of a gate is not ok the gate is False.
    """
    found: dict[str, bool] = {}

    def visit(node):
        if isinstance(node, dict):
            name = node.get("gate")
            if isinstance(name, str) and "ok" in node:
                ok = bool(node["ok"])
                found[name] = found[name] and ok if name in found else ok
            for value in node.values():
                visit(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                visit(value)

    visit(returned)
    return found


def _assert_gate_failed(runner_mod, rows, results, gate_name) -> None:
    returned = runner_mod.apply_fail_closed_gates(rows, results)
    gate_map = _gate_ok_map(returned)
    assert gate_name in gate_map, (
        f"runner did not emit fail-closed gate {gate_name!r}; "
        f"emitted gates: {sorted(gate_map)}")
    assert gate_map[gate_name] is False, (
        f"gate {gate_name!r} passed for a known-bad input: {returned!r}")


def _row(**over) -> dict:
    row = {
        "capability_id": "cli.area.name",
        "family": "cli",
        "upstream_project": "hermes",
        "upstream_commit": "0" * 40,
        "upstream_path": "hermes/x.py:1",
        "upstream_symbol": "fn",
        "upstream_hash": "sha256:" + "0" * 64,
        "qubit_path": "voicebridge/cli.py:1",
        "qubit_symbol": "main",
        "disposition": "PASS",
        "command": "qubit --help",
        "fixture": None,
        "fixture_hash": None,
        "upstream_result_hash": "sha256:" + "1" * 64,
        "qubit_result_hash": "sha256:" + "1" * 64,
        "test_ids": ["tests/test_differential_parity8u.py::test_x"],
        "trace_refs": ["/tmp/qubit-differential/evidence/x.json"],
        "operator_prerequisite": None,
        "rollback_ref": "git revert " + "0" * 40,
        "normalizations": [],
    }
    row.update(over)
    return row


def _result(row, **over) -> dict:
    """Minimal runner result object mirroring the runner's own schema."""
    result = dict(row)
    result.update({
        "declared_disposition": row.get("disposition", "OPEN"),
        "disposition": row.get("disposition", "OPEN"),
        "executed": True,
        "fixture_present": True,
        "qubit_executed": True,
        "reference_executed": True,
        "adapter_mock": False,
        "refused": False,
        "unsafe_normalizers": [],
        "visual_only": False,
        "voice_mismatch": False,
        "persistence_diff": False,
        "secret_in_artifact": False,
        "production_caller": row.get("qubit_symbol"),
        "diagnostics": [],
    })
    result.update(over)
    return result


def _run_cli(*args):
    return subprocess.run(
        [sys.executable, str(RUNNER_PATH), *args],
        capture_output=True, text=True, timeout=180, cwd=str(REPO_ROOT))


def _read_first_json(paths):
    for path in paths:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    return None


def _inventory_candidates() -> list[Path]:
    paths = [
        FIXTURES_DIR / "inventory.json",
        FIXTURES_DIR / "INVENTORY.json",
        REPO_ROOT / "docs" / "QUBIT_CAPABILITY_INVENTORY_2026-09-16.json",
    ]
    if FIXTURES_DIR.is_dir():
        paths += sorted(FIXTURES_DIR.glob("*inventory*.json"))
    return paths


def _report_candidates() -> list[Path]:
    paths = [
        FIXTURES_DIR / "report.json",
        FIXTURES_DIR / "REPORT.json",
        REPO_ROOT / "docs" / "QUBIT_DIFFERENTIAL_REPORT_2026-09-16.json",
    ]
    if FIXTURES_DIR.is_dir():
        paths += sorted(FIXTURES_DIR.glob("*report*.json"))
    docs = REPO_ROOT / "docs"
    if docs.is_dir():
        paths += sorted(docs.glob("*DIFFERENTIAL*REPORT*.json"))
    return paths


def _looks_secret(value: str) -> bool:
    if not isinstance(value, str) or len(value) < 16:
        return False
    lowered = value.lower()
    if ("redacted" in lowered or "<ref>" in lowered
            or lowered.startswith("sha256:")):
        return False
    if lowered in {"session_token", "generated_hash", "temp_path",
                   "product_branding"}:
        return False
    return any(pattern.search(value) for pattern in _SECRET_PATTERNS)


def _matches_json_type(value, allowed) -> bool:
    for name in allowed:
        if name == "string" and isinstance(value, str):
            return True
        if name == "boolean" and isinstance(value, bool):
            return True
        if name == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if name == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if name == "array" and isinstance(value, list):
            return True
        if name == "object" and isinstance(value, dict):
            return True
        if name == "null" and value is None:
            return True
    return False


def _schema_errors(schema, instance, path="$", root=None):
    """Validate against the stdlib-checkable JSON Schema subset we author.

    Supports ``$ref`` (local ``#/`` pointers), ``type``, ``enum``, ``required``,
    ``properties``, ``items`` and ``additionalProperties`` — the subset used by
    the repo's draft 2020-12 schemas. Returns a list of ``(path, message)``
    errors.
    """
    errors = []
    if not isinstance(schema, dict):
        return errors
    if root is None:
        root = schema
    ref = schema.get("$ref")
    if isinstance(ref, str):
        if not ref.startswith("#/"):
            return [(path, f"unsupported $ref {ref!r}")]
        target = root
        for part in ref[2:].split("/"):
            target = target[part]
        return _schema_errors(target, instance, path, root)
    declared = schema.get("type")
    if declared is not None:
        allowed = [declared] if isinstance(declared, str) else list(declared)
        if not _matches_json_type(instance, allowed):
            return [(path, f"type mismatch: {instance!r} not in {allowed}")]
    if "enum" in schema and instance not in schema["enum"]:
        errors.append((path, f"{instance!r} not in enum"))
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append((f"{path}.{key}", "missing required property"))
        props = schema.get("properties", {})
        for key, value in instance.items():
            if key in props:
                errors.extend(_schema_errors(props[key], value, f"{path}.{key}", root))
            elif schema.get("additionalProperties") is False:
                errors.append((f"{path}.{key}", "additional property not allowed"))
    if isinstance(instance, list) and "items" in schema:
        for index, value in enumerate(instance):
            errors.extend(_schema_errors(schema["items"], value, f"{path}[{index}]", root))
    return errors


def _complete_result(runner_mod, **over) -> dict:
    """A fully-populated PASS row-result valid against the v1.1 schema."""
    result = {
        "capability_id": "api.route.status_schema",
        "declared_disposition": "PASS",
        "disposition": "PASS",
        "executed": True,
        "qubit_executed": True,
        "reference_executed": True,
        "fixture_present": True,
        "adapter_mock": False,
        "refused": False,
        "unsafe_normalizers": [],
        "visual_only": False,
        "voice_mismatch": False,
        "persistence_diff": False,
        "secret_in_artifact": False,
        "production_caller": "_hermes_api",
        "diagnostics": [],
        "operator_prerequisite": None,
        "upstream_result_hash": "sha256:" + "1" * 64,
        "qubit_result_hash": "sha256:" + "1" * 64,
        "fixture_hash": "sha256:" + "2" * 64,
        "unclassifiable": False,
    }
    for field in runner_mod.EVIDENCE_FIELDS:
        if field == "error_codes":
            result[field] = []
        elif field == "response_headers":
            result[field] = {"content-type": "application/json"}
        elif field == "raw_hash":
            result[field] = "sha256:" + "a" * 64
        else:
            result[field] = f"<{field}>"
    result.update(over)
    return result


# ------------------------------------------------------------------ normalizer
def test_normalizer_allowlist_is_exact(runner):
    allowed = getattr(runner, "ALLOWED_NORMALIZERS", None)
    assert isinstance(allowed, dict), "ALLOWED_NORMALIZERS must be a dict"
    assert set(allowed) == set(ALLOWED_NORMALIZERS), (
        f"allowlist drift: {sorted(set(allowed) ^ set(ALLOWED_NORMALIZERS))}")
    for name, fn in allowed.items():
        assert callable(fn), f"normalizer {name!r} is not callable"


# ---------------------------------------------------------------- result hashing
def test_result_hash_deterministic(runner):
    payload = {"z": 1, "a": {"nested": [1, 2, {"t": "x"}]}, "flag": True}
    reordered = {"flag": True, "a": {"nested": [1, 2, {"t": "x"}]}, "z": 1}
    first = runner.result_hash(payload)
    second = runner.result_hash(payload)
    third = runner.result_hash(reordered)
    assert first == second == third, "result_hash is not deterministic"
    assert isinstance(first, str) and first.startswith("sha256:"), first
    digest = first.split("sha256:", 1)[1]
    assert len(digest) == 64, digest
    expected = "sha256:" + hashlib.sha256(
        _canonical(payload).encode("utf-8")).hexdigest()
    assert first == expected, "canonical_json / hashing differs from contract"


# ------------------------------------------------------------------- diagnostics
def test_diagnostic_prints_exact_json_path(runner):
    equal = {"a": {"b": {"c": 1}}}
    assert _diag_paths(runner.compare_expected_actual(equal, dict(equal))) == []
    diags = runner.compare_expected_actual(
        {"a": {"b": {"c": 1}}}, {"a": {"b": {"c": 2}}})
    paths = _diag_paths(diags)
    assert set(paths) == {"$.a.b.c"}, paths


# ----------------------------------------------------------- fail-closed: gates
def test_fail_closed_gate_surface_is_complete(runner):
    row = _row(capability_id="cli.clean.row")
    result = _result(row)
    gate_map = _gate_ok_map(runner.apply_fail_closed_gates([row], [result]))
    missing = sorted(set(FAIL_CLOSED_GATES) - set(gate_map))
    assert missing == [], f"runner omitted fail-closed gate results: {missing}"


def test_fail_closed_unexecuted_row(runner):
    row = _row(capability_id="cli.unexecuted.row", disposition="PASS")
    # A row claiming PASS with no result at all was never executed.
    _assert_gate_failed(runner, [row], [], "unexecuted_row")
    # A result that still CLAIMS PASS while unexecuted must also trip it.
    _assert_gate_failed(
        runner, [row],
        [_result(row, executed=False, disposition="PASS")],
        "unexecuted_row")
    # v1.1 gate semantics: OPEN is NOT a success claim, so unexecuted_row must
    # stay green for an OPEN unexecuted row (OPEN omissions are caught by the
    # all-inventory coverage test); skipped_fixture is the gate that catches it.
    open_result = _result(row, executed=False, disposition="OPEN")
    gate_map = _gate_ok_map(runner.apply_fail_closed_gates([row], [open_result]))
    assert gate_map["unexecuted_row"] is True, (
        "unexecuted_row fired on an OPEN (non-success) row; it must only fire "
        "on PASS/SAFE_EQUIVALENT success claims")
    assert gate_map["skipped_fixture"] is False, (
        "an unexecuted OPEN row with a fixture present must trip skipped_fixture")


def test_fail_closed_skipped_fixture(runner):
    row = _row(
        capability_id="cli.skipped.fixture",
        disposition="PASS",
        fixture="tests/fixtures/differential/cli/missing_fixture_8u.json",
        fixture_hash=None)
    result = _result(
        row, executed=False, fixture_present=True, disposition="OPEN",
        detail="fixture present but execution skipped")
    _assert_gate_failed(runner, [row], [result], "skipped_fixture")


def test_fail_closed_missing_production_caller(runner):
    row = _row(
        capability_id="cli.missing.caller",
        disposition="PASS",
        qubit_path="voicebridge/_does_not_exist_8u.py:1",
        qubit_symbol=None)
    result = _result(row, production_caller=None)
    _assert_gate_failed(runner, [row], [result], "missing_production_caller")


def test_fail_closed_mock_replaced_real_path(runner):
    row = _row(
        capability_id="cli.mock.path",
        disposition="PASS",
        qubit_path="tests/fakes/mock_backend_8u.py:1",
        qubit_symbol="MockBackend")
    result = _result(row, adapter_mock=True, disposition="PASS",
                     qubit_path="tests/fakes/mock_backend_8u.py:1")
    _assert_gate_failed(runner, [row], [result], "mock_replaced_real_path")


def test_fail_closed_refusal_counted_as_success(runner):
    row = _row(capability_id="cli.refusal.success", disposition="PASS")
    result = _result(row, refused=True, disposition="PASS",
                     detail="PERMISSION_REFUSED treated as PASS")
    _assert_gate_failed(runner, [row], [result], "refusal_counted_as_success")


def test_fail_closed_unsafe_normalizer_hides_difference(runner):
    row = _row(
        capability_id="cli.unsafe.normalizer",
        disposition="PASS",
        normalizations=["timestamp"],
        upstream_result_hash="sha256:" + "a" * 64,
        qubit_result_hash="sha256:" + "b" * 64)
    result = _result(
        row, disposition="PASS",
        upstream_result_hash="sha256:" + "a" * 64,
        qubit_result_hash="sha256:" + "b" * 64,
        unsafe_normalizers=["$.payload.created"],
        diagnostics=[{
            "path": "$.payload.created",
            "kind": "normalized",
            "detail": "timestamp normalizer hid a non-timestamp difference",
        }])
    _assert_gate_failed(runner, [row], [result], "unsafe_normalizer")


def test_fail_closed_visual_only_control(runner):
    row = _row(
        capability_id="dashboard.visual.control",
        family="dashboard",
        disposition="PASS",
        upstream_path="frontend/qubit-web/src/routes/page.tsx:1",
        upstream_symbol="#submit-button",
        qubit_path="frontend/qubit-web/src/routes/page.tsx:1",
        qubit_symbol="#submit-button")
    result = _result(
        row, visual_only=True, disposition="PASS", production_caller=None,
        backend_caller=None)
    _assert_gate_failed(runner, [row], [result], "visual_only_control")


def test_fail_closed_voice_backend_mismatch(runner):
    row = _row(
        capability_id="voice.backend.mismatch",
        family="voice",
        disposition="PASS")
    result = _result(
        row, voice_mismatch=True, disposition="PASS",
        voice_operation="voicebridge.actions:open_url",
        cli_operation="voicebridge.actions:open_browser")
    _assert_gate_failed(runner, [row], [result], "voice_backend_mismatch")


def test_fail_closed_persistence_diff(runner):
    row = _row(
        capability_id="session.persistence.diff",
        family="session",
        disposition="PASS")
    result = _result(
        row, persistence_diff=True, disposition="PASS",
        state_diff={"sessions": "upstream != qubit"})
    _assert_gate_failed(runner, [row], [result],
                        "persistence_or_recovery_diff")


def test_fail_closed_secret_in_artifact(runner):
    row = _row(
        capability_id="provider.secret.artifact",
        family="provider",
        disposition="PASS")
    fake_key = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2"
    result = _result(
        row, secret_in_artifact=True,
        qubit_payload={"authorization": f"Bearer {fake_key}"})
    _assert_gate_failed(runner, [row], [result], "secret_in_artifact")


# ------------------------------------------------- v1.1 coverage additions
def test_fail_closed_openclaw_undisposed_addition(runner):
    # An OpenClaw default-branch delta row with no Qubit disposition/proof is
    # undisposed and must trip the new gate.
    row = _row(
        capability_id="gateway.openclaw.latest_delta",
        family="gateway",
        upstream_project="openclaw",
        upstream_commit="main",
        disposition="OPEN",
        qubit_path=None,
        qubit_symbol=None,
        trace_refs=[],
        upstream_hash=None,
        upstream_result_hash=None,
        qubit_result_hash=None)
    result = _result(
        row, disposition="OPEN", executed=False, production_caller=None)
    _assert_gate_failed(runner, [row], [result], "openclaw_undisposed_addition")

    # A delta row that HAS a terminal Qubit disposition and proof is disposed.
    disposed = _row(
        capability_id="gateway.openclaw.latest_delta.disposed",
        family="gateway",
        upstream_project="openclaw",
        upstream_commit="main",
        disposition="PASS",
        qubit_path="voicebridge/gateway/gateway_ws.py:1",
        qubit_symbol="handle_delta")
    disposed_result = _result(disposed, disposition="PASS")
    gate_map = _gate_ok_map(
        runner.apply_fail_closed_gates([disposed], [disposed_result]))
    assert gate_map["openclaw_undisposed_addition"] is True, (
        "a disposed OpenClaw delta row must not trip openclaw_undisposed_addition")

    # A pinned OpenClaw row is not a delta row and is exempt.
    pinned = _row(
        capability_id="gateway.openclaw.pinned",
        family="gateway",
        upstream_project="openclaw",
        upstream_commit="3a9d69db306cd7f081e06254cb89c4bcc14a7107",
        disposition="OPEN",
        qubit_path=None,
        qubit_symbol=None,
        trace_refs=[])
    pinned_result = _result(pinned, disposition="OPEN", executed=False,
                            production_caller=None)
    gate_map = _gate_ok_map(
        runner.apply_fail_closed_gates([pinned], [pinned_result]))
    assert gate_map["openclaw_undisposed_addition"] is True, (
        "a pinned OpenClaw row is contractual, not a delta row")


def test_openclaw_delta_accepts_terminal_disposition_with_prerequisite(runner):
    # Frozen contract: a terminal OPERATOR_ONLY / SAFE_EQUIVALENT disposition with
    # a non-empty operator_prerequisite (or a real proof) is a legitimate
    # closure. A bare OPEN, or a terminal disposition with no basis at all, must
    # still fire the gate.
    def gate_for(row):
        result = _result(row, executed=False, production_caller=None)
        return _gate_ok_map(
            runner.apply_fail_closed_gates([row], [result])
        )["openclaw_undisposed_addition"]

    def delta(**over):
        base = dict(
            capability_id="dashboard.control_source.debug_overlay_content",
            family="dashboard",
            upstream_project="openclaw",
            upstream_commit="main",
            qubit_path=None,
            qubit_symbol=None,
            trace_refs=[],
            test_ids=[],
            disposition="OPEN",
            operator_prerequisite=None,
        )
        base.update(over)
        return _row(**base)

    # OPERATOR_ONLY + non-empty prerequisite disposes the delta addition.
    assert gate_for(delta(
        disposition="OPERATOR_ONLY",
        operator_prerequisite=(
            "no Qubit equivalent; owner must implement or record an explicit "
            "parity omission"),
    )) is True, "OPERATOR_ONLY with an operator_prerequisite must satisfy the gate"

    # SAFE_EQUIVALENT + non-empty prerequisite is likewise a typed closure.
    assert gate_for(delta(
        disposition="SAFE_EQUIVALENT",
        operator_prerequisite="owner must bind a row-linked executed test",
    )) is True, (
        "SAFE_EQUIVALENT with an operator_prerequisite must satisfy the gate")

    # A bare OPERATOR_ONLY with no prerequisite and no proof is NOT disposed.
    assert gate_for(delta(
        disposition="OPERATOR_ONLY",
        operator_prerequisite="",
    )) is False, (
        "OPERATOR_ONLY with no prerequisite and no proof must still fire the gate")

    # A bare OPEN is never disposed.
    assert gate_for(delta(disposition="OPEN")) is False, (
        "an OPEN OpenClaw delta row must still fire the gate")

    # FAIL is a recorded defect, not a closure: it keeps firing even with a
    # prerequisite present.
    assert gate_for(delta(
        disposition="FAIL",
        operator_prerequisite="recorded defect OC-72",
    )) is False, (
        "a FAIL OpenClaw delta row must still fire the gate")


def test_success_rows_require_evidence_fields(runner):
    assert isinstance(runner.EVIDENCE_FIELDS, tuple) and runner.EVIDENCE_FIELDS
    # Every result produced by the runner carries every evidence key.
    produced = runner._new_result(_row(capability_id="api.evidence.keys"))
    assert runner.missing_evidence_keys(produced) == [], (
        f"runner results omit evidence keys: {runner.missing_evidence_keys(produced)}")

    complete = _complete_result(runner)
    assert runner.missing_evidence_fields(complete) == [], (
        "a fully evidenced PASS row must satisfy the evidence requirement")

    missing_key = dict(complete)
    missing_key.pop("response_headers")
    assert "response_headers" in runner.missing_evidence_fields(missing_key)

    nulled = dict(complete)
    nulled["audit_receipt_proof"] = None
    assert "audit_receipt_proof" in runner.missing_evidence_fields(nulled)

    # A success row whose evidence is incomplete must NOT be counted PASS by
    # the runner (fail closed on the actual run path).
    row = _row(capability_id="cli.evidence.missing")
    incomplete = _result(row, disposition="PASS")
    assert "response_headers" in runner.missing_evidence_fields(incomplete)

    # OPEN/OPERATOR_ONLY rows are exempt here: their omissions are handled by
    # the all-inventory coverage test, not the evidence requirement.
    for disposition in ("OPEN", "OPERATOR_ONLY"):
        exempt = dict(missing_key)
        exempt["disposition"] = disposition
        assert runner.missing_evidence_fields(exempt) == []


def test_response_header_comparison_ignores_nondeterministic(runner):
    expected = {
        "Date": "Mon, 01 Jan 2024 00:00:00 GMT",
        "Server": "uvicorn",
        "X-Request-Id": "upstream-abc",
        "Content-Type": "application/json",
    }
    actual = {
        "date": "Tue, 02 Jan 2024 01:02:03 GMT",
        "server": "hypercorn",
        "x-request-id": "qubit-xyz",
        "content-type": "application/json",
    }
    assert runner.compare_response_headers(expected, actual) == [], (
        "nondeterministic response headers must not report a difference")

    changed = dict(actual, **{"content-type": "text/plain"})
    diags = runner.compare_response_headers(expected, changed)
    assert len(diags) == 1, [d.to_dict() for d in diags]
    assert "content-type" in diags[0].path

    # The structured comparator honours the same rule for a `headers` block.
    top = runner.compare_expected_actual(
        {"headers": expected}, {"headers": actual})
    assert top == []


def test_help_text_asserts_canonical_qubit_command(runner):
    canonical = runner.CANONICAL_QUBIT_COMMAND
    assert isinstance(canonical, str) and canonical, "canonical command missing"
    assert runner.check_help_text(
        f"usage: {canonical} [-h] [--json] COMMAND ...") == []
    assert runner.check_help_text(
        {"stdout": f"Usage: {canonical} profile model list"}) == []

    absent = runner.check_help_text("usage: some-other-tool [-h] COMMAND")
    assert absent, "help text without the canonical command must fail"
    assert {d.kind for d in absent} == {"help_missing"}

    # The CLI family wiring drives the assertion from fixture.expected.
    wired = runner._family_diagnostics(
        "cli", {"help_contains": [canonical]}, {"stdout": "usage: other-tool"})
    assert wired and all(d.kind == "help_missing" for d in wired)
    ok = runner._family_diagnostics(
        "cli", {"help_contains": [canonical]},
        {"stdout": f"usage: {canonical} [-h]"})
    assert ok == []


def test_row_result_schema_valid_and_rejects_missing(runner):
    if not ROW_RESULT_SCHEMA_PATH.is_file():
        pytest.skip(f"result schema not written yet: {ROW_RESULT_SCHEMA_PATH}")
    schema = json.loads(ROW_RESULT_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema"
    required = set(schema.get("required", []))
    for key in ("capability_id", "declared_disposition", "disposition", "executed",
                "qubit_result_hash", "unclassifiable"):
        assert key in required, f"result schema omits required {key!r}"
    for field in runner.EVIDENCE_FIELDS:
        assert field in required, f"result schema omits evidence field {field!r}"

    valid = _complete_result(runner)
    assert _schema_errors(schema, valid) == [], (
        f"schema rejected a valid result: {_schema_errors(schema, valid)}")

    for missing in ("qubit_result_hash", "executed", "response_headers"):
        broken = dict(valid)
        broken.pop(missing)
        errors = _schema_errors(schema, broken)
        assert any(missing == path.rsplit(".", 1)[-1] for path, _ in errors), (
            f"schema accepted a result missing required {missing!r}: {errors}")

    wrong_type = dict(valid, executed="yes")
    assert any(path.endswith(".executed") for path, _ in _schema_errors(schema, wrong_type))

    bad_enum = dict(valid, disposition="MAYBE")
    assert any("enum" in message for _, message in _schema_errors(schema, bad_enum))


# ------------------------------------------------ fixture-driven execution
def _manifest_fixture_entries():
    if not MANIFEST_PATH.is_file():
        return None
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    entries = data.get("fixtures")
    return entries if isinstance(entries, list) and entries else None


def test_every_manifest_fixture_is_executed(runner):
    entries = _manifest_fixture_entries()
    if entries is None:
        pytest.skip(f"no fixture manifest yet: {MANIFEST_PATH}")
    fixture_ids = [str(entry["fixture_id"]) for entry in entries]
    # Hermetic: adapters={} classifies each fixture OPERATOR_ONLY rather than
    # spawning a real subprocess, but every fixture must still yield a result.
    results = runner.run_inventory([], FIXTURES_DIR, adapters={})
    by_id = {str(r.get("capability_id")): r for r in results}
    missing = [fid for fid in fixture_ids if fid not in by_id]
    assert missing == [], f"manifest fixtures silently ignored: {missing}"
    open_rows = [(fid, by_id[fid].get("disposition")) for fid in fixture_ids
                 if str(by_id[fid].get("disposition")) == "OPEN"]
    assert open_rows == [], f"manifest fixtures left OPEN: {open_rows}"
    assert set(by_id) == set(fixture_ids), (
        "fixture-driven execution produced rows outside the manifest")


def test_fixture_derived_row_has_production_caller(runner):
    entries = _manifest_fixture_entries()
    if entries is None:
        pytest.skip(f"no fixture manifest yet: {MANIFEST_PATH}")
    results = runner.run_inventory([], FIXTURES_DIR, adapters={})
    by_id = {str(r.get("capability_id")): r for r in results}
    for entry in entries:
        fid = str(entry["fixture_id"])
        result = by_id[fid]
        assert result.get("manifest_fixture") is True, (
            f"fixture-derived row {fid} is not marked as manifest-derived")
        assert result.get("production_caller"), (
            f"fixture-derived row {fid} has no production caller")
        assert result.get("qubit_path"), f"fixture-derived row {fid} has no qubit_path"
        assert result.get("fixture_hash"), f"fixture-derived row {fid} has no fixture_hash"
        assert result.get("executed") is False  # adapters={} — operator-only


def test_open_rows_fail_closed(runner):
    row = _row(capability_id="cli.open.row", disposition="OPEN")
    result = _result(row, disposition="OPEN", executed=False)
    gates = runner.apply_fail_closed_gates([row], [result])
    gate_map = _gate_ok_map(gates)
    assert gate_map["unexecuted_open_row"] is False, (
        "an OPEN row must trip unexecuted_open_row")
    report = runner.build_report([result], gates)
    assert runner.report_exit_code(report) != 0, (
        "an OPEN row must not yield a clean exit code")

    # OPERATOR_ONLY is a terminal classification and must NOT be downgraded.
    op_row = _row(capability_id="cli.operator.row", disposition="OPERATOR_ONLY")
    op_result = _result(op_row, disposition="OPERATOR_ONLY", executed=False,
                        operator_prerequisite="no transport adapter")
    op_gates = _gate_ok_map(runner.apply_fail_closed_gates([op_row], [op_result]))
    assert op_gates["unexecuted_open_row"] is True, (
        "unexecuted_open_row must not fire on an OPERATOR_ONLY row")


def test_report_verdict_incomplete_when_open_rows(runner):
    row = _row(capability_id="cli.open.verdict", disposition="OPEN")
    result = _result(row, disposition="OPEN", executed=False)
    gates = runner.apply_fail_closed_gates([row], [result])
    report = runner.build_report([result], gates)
    assert report["verdict"] == "INCOMPLETE", (
        f"OPEN rows must make the verdict INCOMPLETE, got {report['verdict']!r}")


# ------------------------------------------- tri-way differential semantics
def _fake_adapter(runner_mod, name, **payload):
    class _FakeAdapter:
        project = name
        binary = f"/nonexistent/{name}"

        def available(self):
            return True

        def execute(self, request):
            return runner_mod.CallResult(**payload)

    _FakeAdapter.name = name
    return _FakeAdapter()


def _evidence_block(runner_mod):
    evidence = {field: f"<{field}>" for field in runner_mod.EVIDENCE_FIELDS}
    evidence["error_codes"] = []
    evidence["response_headers"] = {"content-type": "application/json"}
    return evidence


def _write_fixture(tmp_path, doc):
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _cli_fixture(runner_mod, fixture_id, references=None):
    doc = {
        "schema_version": 1,
        "fixture_id": fixture_id,
        "family": "cli",
        "command": {"argv": ["qubit", "--help"], "stdin": None, "env": {}},
        "request": None,
        "normalizers": [],
        "expected": {"exit_code": 0},
        "qubit": {"path": "voicebridge/cli.py:1", "caller": "build_parser", "approval": None},
        "hermes": {"path": "qubit_cli/_parser.py:280", "symbol": "build_top_level_parser"},
        "openclaw": {"path": None, "symbol": None, "mapping_status": "UNVERIFIED"},
        "evidence": _evidence_block(runner_mod),
        "notes": "tri-way reference test fixture",
    }
    if references is not None:
        doc["references"] = references
    return doc


def test_normalizers_applied_symmetrically(runner):
    diags = runner.compare_expected_actual(
        {"created_at": "2024-01-02T03:04:05Z", "name": "hermes"},
        {"created_at": "2025-06-07T08:09:10Z", "name": "hermes"},
        normalizers=["timestamp", "product_branding"],
    )
    assert diags == [], f"symmetric normalizers left diagnostics: {[d.to_dict() for d in diags]}"

    # A one-sided collapse that hides a difference is flagged.
    flagged = runner.asymmetric_normalizers(
        {"created_at": "2024-01-02T03:04:05Z"},
        {"created_at": "not-a-timestamp"},
        ["timestamp"],
    )
    assert flagged == ["$.created_at"], flagged


def test_envelope_subset_allows_adapter_transport_fields(runner):
    expected = {"exit_code": 0}
    actual = {"exit_code": 0, "stdout": "usage: ...", "stderr": "", "duration": 0.5}
    assert runner.compare_expected_actual(expected, actual) == [], (
        "adapter transport fields must not be flagged when expected omits them")

    closed = runner.compare_expected_actual({"closed": True, "exit_code": 0}, actual)
    assert {d.path for d in closed} == {"$.stdout", "$.stderr", "$.duration"}, (
        f"closed object must flag extras, got {[d.path for d in closed]}")

    schema = runner.compare_expected_actual(
        {"stdout_schema": {"closed": True, "state": "OK"}},
        {"stdout": json.dumps({"state": "OK", "extra": 1})},
    )
    assert [d.path for d in schema] == ["$.stdout_schema.extra"], (
        f"closed *_schema must flag extras, got {[d.path for d in schema]}")


def test_pass_requires_reference_executed(runner):
    row = _row(capability_id="cli.pass.without.reference", disposition="PASS")
    bad = _result(row, disposition="PASS", reference_executed=False)
    gate_map = _gate_ok_map(runner.apply_fail_closed_gates([row], [bad]))
    assert gate_map["pass_without_reference"] is False, (
        "a PASS result with no executed reference must trip pass_without_reference")

    good = _result(row, disposition="PASS", reference_executed=True)
    good_map = _gate_ok_map(runner.apply_fail_closed_gates([row], [good]))
    assert good_map["pass_without_reference"] is True, (
        "pass_without_reference must not fire when a reference executed")


def test_safe_equivalent_wiring_proof_is_accounted_not_unexecuted(runner):
    """A SAFE_EQUIVALENT row with a valid typed wiring proof is accounted.

    Frozen-contract alignment: ``unexecuted_row``/``pass_without_reference``
    target a success claim that has neither execution NOR a valid typed proof.
    A wiring-level proof whose cited test really exercises the row's route (a
    resolvable AND linked reference) is proof of record even though the row was
    not live-executed and has no upstream reference.
    """
    row = _row(
        capability_id="dashboard.control.accounted8u",
        family="dashboard",
        disposition="SAFE_EQUIVALENT",
        qubit_path="voicebridge/qubit_compat.py:1000",
        qubit_symbol=_MEMBER_SYMBOL,
        dashboard_route=_MEMBER_ROUTE,
        request={"method": "GET", "path": _MEMBER_ROUTE},
        test_ids=[_ROUTE_TABLE_SURFACE_TEST],
        trace_refs=[_ROUTE_TABLE_SURFACE_TEST],
    )
    result = _result(
        row, disposition="SAFE_EQUIVALENT", declared_disposition="SAFE_EQUIVALENT",
        executed=False, reference_executed=False, fixture_present=False,
        qubit_result_hash=None, upstream_result_hash=None, proof_level="wiring")
    gates = _gate_ok_map(runner.apply_fail_closed_gates([row], [result]))
    for name in ("unexecuted_row", "pass_without_reference", "proof_level",
                 "proof_level_requires_resolvable_test_id",
                 "safe_equivalent_requires_proof"):
        assert gates.get(name) is True, (name, gates.get(name))


def test_safe_equivalent_without_proof_still_trips(runner):
    """The alignment must not excuse an unproven SAFE_EQUIVALENT claim."""
    row = _row(
        capability_id="dashboard.control.unproven8u",
        disposition="SAFE_EQUIVALENT",
        qubit_path="voicebridge/qubit_compat.py:1000",
        qubit_symbol="NotARealSymbol8u",
        test_ids=[], trace_refs=[])
    result = _result(
        row, disposition="SAFE_EQUIVALENT", declared_disposition="SAFE_EQUIVALENT",
        executed=False, reference_executed=False, proof_level=None)
    gates = _gate_ok_map(runner.apply_fail_closed_gates([row], [result]))
    assert gates["unexecuted_row"] is False, (
        "SAFE_EQUIVALENT without a typed proof must still trip unexecuted_row")
    assert gates["pass_without_reference"] is False, (
        "SAFE_EQUIVALENT without a typed proof must still trip "
        "pass_without_reference")
    assert gates["safe_equivalent_requires_proof"] is False, (
        "SAFE_EQUIVALENT without proof must still trip "
        "safe_equivalent_requires_proof")


def test_declaration_refuses_unproven_pass_and_keeps_se(runner):
    """A FAIL declaration corrects an unproven PASS; a terminal SE is untouched."""
    declarations = {
        "a.pass.8u": {"capability_id": "a.pass.8u", "disposition": "FAIL",
                      "operator_prerequisite": "owner basis", "basis": "b"},
        "b.se.8u": {"capability_id": "b.se.8u", "disposition": "FAIL",
                    "operator_prerequisite": "owner basis", "basis": "b"},
    }
    rows = [
        {"capability_id": "a.pass.8u", "disposition": "PASS"},
        {"capability_id": "b.se.8u", "disposition": "SAFE_EQUIVALENT"},
    ]
    out = runner.apply_declarations(rows, declarations)
    assert out[0]["disposition"] == "FAIL", (
        "an unproven PASS claim was not refused by the typed FAIL declaration")
    assert out[1]["disposition"] == "SAFE_EQUIVALENT", (
        "a terminal SAFE_EQUIVALENT row must never be overwritten")


def test_reference_execution_recorded(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("QUBIT_DIFFERENTIAL_HERMES_COMMIT", PIN_QUBIT)
    doc = _cli_fixture(
        runner,
        "cli.reference.recorded",
        references={"hermes": {"argv": ["hermes", "--help"], "env": {}}},
    )
    path = _write_fixture(tmp_path, doc)
    row = _row(
        capability_id="cli.reference.recorded", disposition="OPEN", fixture=str(path))
    adapters = {
        "qubit": _fake_adapter(runner, "qubit", exit_code=0, stdout="usage"),
        "hermes": _fake_adapter(runner, "hermes", exit_code=0, stdout="usage"),
    }
    results = runner.run_inventory([row], tmp_path, adapters=adapters)
    result = next(r for r in results if r["capability_id"] == "cli.reference.recorded")

    assert result["reference_executed"] is True, "declared reference was not executed"
    assert result.get("upstream_result_hash"), "upstream_result_hash not recorded"
    assert result["references"]["hermes"]["executed"] is True
    assert result["references"]["hermes"]["result_hash"] == result["reference_result_hash"]
    assert result["references"]["hermes"]["reference_pinned"] is True
    assert result["disposition"] == "PASS", result["diagnostics"]


def test_unpinned_reference_cannot_pass(runner, tmp_path, monkeypatch):
    monkeypatch.delenv("QUBIT_DIFFERENTIAL_HERMES_COMMIT", raising=False)
    monkeypatch.delenv("QUBIT_DIFFERENTIAL_OPENCLAW_COMMIT", raising=False)
    doc = _cli_fixture(
        runner,
        "cli.unpinned.reference",
        references={"hermes": {"argv": ["hermes", "--help"], "env": {}}},
    )
    path = _write_fixture(tmp_path, doc)
    row = _row(
        capability_id="cli.unpinned.reference", disposition="OPEN", fixture=str(path))
    adapters = {
        "qubit": _fake_adapter(runner, "qubit", exit_code=0, stdout="usage"),
        "hermes": _fake_adapter(runner, "hermes", exit_code=0, stdout="usage"),
    }
    result = runner.run_inventory([row], tmp_path, adapters=adapters)[0]

    assert result["reference_executed"] is True
    assert result["references"]["hermes"]["reference_pinned"] is False
    assert "hermes" in result["unpinned_references"]
    assert result["disposition"] == "SAFE_EQUIVALENT", result["diagnostics"]
    assert result["disposition"] != "PASS", (
        "an unpinned reference must never produce a PASS claim")

    # The gate is the safety net for any PASS that still carries an unpinned ref.
    bad = _result(
        _row(capability_id="cli.unpinned.pass", disposition="PASS"),
        disposition="PASS", reference_executed=True, unpinned_references=["hermes"])
    gate_map = _gate_ok_map(runner.apply_fail_closed_gates([bad], [bad]))
    assert gate_map["unpinned_reference_pass"] is False, (
        "a PASS relying on an unpinned reference must trip unpinned_reference_pass")


def test_pinned_reference_pass_allowed(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("QUBIT_DIFFERENTIAL_HERMES_COMMIT", PIN_QUBIT)
    doc = _cli_fixture(
        runner,
        "cli.pinned.reference",
        references={"hermes": {"argv": ["hermes", "--help"], "env": {}}},
    )
    path = _write_fixture(tmp_path, doc)
    row = _row(
        capability_id="cli.pinned.reference", disposition="OPEN", fixture=str(path))
    adapters = {
        "qubit": _fake_adapter(runner, "qubit", exit_code=0, stdout="usage"),
        "hermes": _fake_adapter(runner, "hermes", exit_code=0, stdout="usage"),
    }
    result = runner.run_inventory([row], tmp_path, adapters=adapters)[0]

    assert result["references"]["hermes"]["reference_pinned"] is True
    assert result["unpinned_references"] == []
    assert result["disposition"] == "PASS", result["diagnostics"]
    gate_map = _gate_ok_map(
        runner.apply_fail_closed_gates([result], [result]))
    assert gate_map["unpinned_reference_pass"] is True


def test_visual_success_requires_visual_evidence_refs(runner):
    complete = _complete_result(runner)
    complete["family"] = "cli"
    for field in VISUAL_REF_FIELDS:
        complete[field] = None
    assert runner.missing_evidence_fields(complete) == [], (
        "non-visual families must not require screenshot/dom/a11y refs")

    complete["family"] = "dashboard"
    missing = set(runner.missing_evidence_fields(complete))
    assert set(VISUAL_REF_FIELDS) <= missing, (
        f"visual success rows must require {VISUAL_REF_FIELDS}, got {sorted(missing)}")


def test_timestamp_normalizer_makes_embedded_timestamps_deterministic(runner):
    blob_a = json.dumps({
        "state": "OK", "generated_at": 1789524230.8453813,
        "note": "generated at 2026-09-16T04:00:00Z"})
    blob_b = json.dumps({
        "state": "OK", "generated_at": 1789524999.1234567,
        "note": "generated at 2026-09-16T05:11:22.500+00:00"})
    norm_a = runner.normalize({"stdout": blob_a}, ["timestamp"])
    norm_b = runner.normalize({"stdout": blob_b}, ["timestamp"])

    assert norm_a == norm_b, (norm_a, norm_b)
    assert runner.result_hash(norm_a) == runner.result_hash(norm_b)
    assert "1789524230" not in norm_a["stdout"]
    assert "2026-09-16T04:00:00Z" not in norm_a["stdout"]

    _, audit = runner.normalize_and_audit({"stdout": blob_a}, ["timestamp"])
    assert runner.detect_unsafe_normalizers(audit) == [], (
        "embedded-timestamp normalization must not be flagged unsafe")

    ns_a = runner.normalize(
        {"stdout": json.dumps({"time_ns": 1789524230845381300})}, ["timestamp"])
    ns_b = runner.normalize(
        {"stdout": json.dumps({"time_ns": 1789524999123456700})}, ["timestamp"])
    assert ns_a == ns_b, (ns_a, ns_b)


def test_repeated_run_is_deterministic(runner, tmp_path):
    doc = _cli_fixture(runner, "cli.ticking.determinism")
    doc["normalizers"] = ["timestamp"]
    doc["expected"] = {"exit_code": 0}
    path = _write_fixture(tmp_path, doc)
    row = _row(
        capability_id="cli.ticking.determinism", disposition="OPEN", fixture=str(path))
    counter = {"n": 0}

    class _TickingAdapter:
        project = "qubit"
        binary = "/nonexistent/qubit"

        def available(self):
            return True

        def execute(self, request):
            counter["n"] += 1
            return runner.CallResult(
                exit_code=0,
                stdout=json.dumps({
                    "schema": "ticking/1",
                    "generated_at": 1789524000 + counter["n"],
                    "state": "OK",
                }),
            )

    hashes = []
    for _ in range(2):
        result = runner.run_inventory(
            [row], tmp_path, adapters={"qubit": _TickingAdapter()})[0]
        hashes.append(result["qubit_result_hash"])
    assert hashes[0] == hashes[1], f"repeated run hashes differ: {hashes}"


def test_no_reference_is_operator_only_not_pass(runner, tmp_path):
    doc = _cli_fixture(runner, "cli.no.reference")
    path = _write_fixture(tmp_path, doc)
    row = _row(
        capability_id="cli.no.reference", disposition="OPEN", fixture=str(path))
    adapters = {"qubit": _fake_adapter(runner, "qubit", exit_code=0, stdout="usage")}
    results = runner.run_inventory([row], tmp_path, adapters=adapters)
    result = next(r for r in results if r["capability_id"] == "cli.no.reference")

    assert result["disposition"] == "OPERATOR_ONLY", (
        f"a row with no executed reference must be OPERATOR_ONLY, got {result['disposition']}")
    assert result["disposition"] != "PASS"
    assert result["reference_executed"] is False
    assert result.get("operator_prerequisite"), "OPERATOR_ONLY needs a prerequisite"


# ------------------------------------------------------------------ runner CLI
def test_runner_cli_selftest(runner):
    rc = runner.selftest()
    assert type(rc) is int, f"selftest() returned {type(rc).__name__}"
    assert rc == 0, f"runner selftest failed with {rc}"
    proc = _run_cli("selftest")
    assert proc.returncode == 0, (
        proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:])


def test_runner_cli_version_and_usage():
    if not RUNNER_PATH.is_file():
        pytest.skip(f"differential runner not written yet: {RUNNER_PATH}")
    version = _run_cli("--version")
    assert version.returncode == 0, (version.returncode, version.stderr)
    assert version.stdout.strip(), "runner --version produced no output"
    usage = _run_cli("definitely-not-a-subcommand-8u")
    assert usage.returncode == 2, (
        usage.returncode, usage.stdout[-2000:], usage.stderr[-2000:])


# ------------------------------------------------------------- fixture manifest
def test_fixture_manifest_hashes_match():
    if not MANIFEST_PATH.is_file():
        pytest.skip(f"no fixture manifest yet: {MANIFEST_PATH}")
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    entries = data.get("fixtures")
    assert isinstance(entries, list) and entries, "manifest lists no fixtures"
    mismatches = []
    for entry in entries:
        raw = str(entry["path"])
        candidates = [REPO_ROOT / raw, FIXTURES_DIR / raw, Path(raw)]
        target = next((p for p in candidates if p.is_file()), None)
        assert target is not None, f"manifest fixture missing on disk: {raw}"
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        declared = str(entry["sha256"]).split(":", 1)[-1].lower()
        if actual != declared:
            mismatches.append((raw, declared, actual))
    assert mismatches == [], f"manifest sha256 mismatch: {mismatches}"


# ------------------------------------------------------- inventory coverage
def test_all_inventory_rows_executed_or_operator_only(runner):
    # Hermetic: every input row must have a result, or inventory_coverage fires.
    rows = [
        _row(capability_id="cli.coverage.one", disposition="OPEN"),
        _row(capability_id="cli.coverage.two", disposition="OPEN"),
    ]
    terminal = [
        _result(row, disposition="OPERATOR_ONLY", executed=False,
                operator_prerequisite="covered by operator")
        for row in rows
    ]
    gates = _gate_ok_map(runner.apply_fail_closed_gates(rows, terminal))
    assert gates["inventory_coverage"] is True, (
        "every input row has a result; inventory_coverage must be green")

    partial = _gate_ok_map(runner.apply_fail_closed_gates(rows, terminal[:1]))
    assert partial["inventory_coverage"] is False, (
        "an input row with no result object must trip inventory_coverage")

    # The real generated inventory/report, when present, is also checked.
    inventory = _read_first_json(_inventory_candidates())
    report = _read_first_json(_report_candidates())
    if inventory is not None and report is not None:
        real_rows = inventory.get("rows") if isinstance(inventory, dict) else inventory
        if isinstance(real_rows, list) and real_rows:
            by_id = {}
            for result in report.get("rows", []) or []:
                cid = result.get("capability_id", result.get("id"))
                if cid is not None:
                    by_id[str(cid)] = result
            missing, bad = [], []
            for row in real_rows:
                cid = str(row.get("capability_id", row.get("id")))
                result = by_id.get(cid)
                if result is None:
                    missing.append(cid)
                    continue
                disposition = result.get("disposition", result.get("status"))
                if disposition not in TERMINAL_DISPOSITIONS:
                    bad.append((cid, disposition))
            assert missing == [], (
                f"inventory rows silently omitted from report: {missing[:20]}")
            assert bad == [], (
                f"rows without a terminal disposition: {bad[:20]}")


# ------------------------------------------------------------- secret scanning
def test_no_secret_shaped_value_in_report():
    def walk(node, path, offenders):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}", offenders)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]", offenders)
        elif isinstance(node, str) and _looks_secret(node):
            offenders.append((path, f"<redacted len={len(node)}>"))

    # The scanner must never miss a secret-shaped value (synthetic proof).
    fake_key = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2"
    synthetic = {"rows": [{"qubit_payload": {"authorization": f"Bearer {fake_key}"}}]}
    synthetic_offenders = []
    walk(synthetic, "$", synthetic_offenders)
    assert synthetic_offenders, "scanner failed to detect a secret-shaped value"

    # The real report, when present, must be clean.
    report = _read_first_json(_report_candidates())
    if report is not None:
        offenders = []
        walk(report, "$", offenders)
        assert offenders == [], f"secret-shaped values in report: {offenders[:10]}"


# =========================================================================== #
# v1.2 TRANSPORT ADAPTER CONTRACT
#   FROZEN in /tmp/qubit-differential/INTERFACE.md ("TRANSPORT ADAPTER CONTRACT
#   (v1.2)"). Adapter.execute(spec) -> CallResult, spec.kind in
#   {cli, http, ws, sse, rpc}; CallResult carries http_status/headers/json/
#   events/persisted_state/duration_ms/transport.
#
# The tests below REQUIRE the runner's v1.2 transport code. They are written
# against the frozen contract, NOT the current runner, so if the transport
# adapters are absent or fake they are EXPECTED TO FAIL (no skipif hides that).
#
# Hermetic: the only sockets opened are to a stdlib http.server bound to
# 127.0.0.1 and a minimal RFC6455 loopback server created inside the fixtures
# below. No external network is touched.
# =========================================================================== #
TRANSPORT_KINDS = ("cli", "http", "sse", "ws", "rpc")


def _qubit_transport_adapter(runner_mod):
    """An adapter exposing the frozen ``Adapter.execute`` surface.

    Prefers the runner's own Qubit adapter (the v1.2 contract makes it
    transport-aware), then any constructible ``Adapter`` subclass. Returns None
    when the runner exposes no transport-capable adapter at all.
    """
    candidates = []
    factory = getattr(runner_mod, "default_adapters", None)
    if callable(factory):
        adapters = None
        for kwargs in ({"offline": True}, {}):
            try:
                adapters = factory(**kwargs)
            except TypeError:
                continue
            except Exception:
                adapters = None
                break
            if isinstance(adapters, dict) and adapters:
                break
            adapters = None
        if isinstance(adapters, dict) and "qubit" in adapters:
            candidates.append(adapters["qubit"])
    cls = getattr(runner_mod, "QubitAdapter", None)
    if isinstance(cls, type):
        try:
            candidates.append(cls())
        except Exception:
            pass
    base = getattr(runner_mod, "Adapter", None)
    if isinstance(base, type):
        for _name, obj in sorted(vars(runner_mod).items()):
            if isinstance(obj, type) and issubclass(obj, base) and obj is not base:
                try:
                    candidates.append(obj())
                except Exception:
                    pass
    for candidate in candidates:
        if candidate is not None and callable(getattr(candidate, "execute", None)):
            return candidate
    return None


def _http_spec(kind, path, **over):
    spec = {"kind": kind, "method": "GET", "path": path, "headers": {}, "body": None}
    spec.update(over)
    return spec


def _transport_fixture(fixture_id, family, request, expected, normalizers=None):
    return {
        "schema_version": 1,
        "fixture_id": fixture_id,
        "family": family,
        # These probes are read-only: an in-process JSON-RPC list/error read may
        # use POST, and the HTTP/WS probes are GETs. Declaring read_only keeps
        # the read-only probe executable under the hardened non-GET guard so the
        # error envelope/response is exercised rather than skipped OPERATOR_ONLY.
        "read_only": True,
        "command": None,
        "request": dict(request),
        "normalizers": list(normalizers or []),
        "expected": dict(expected),
        "qubit": {"path": "voicebridge/webui.py:1", "caller": "create_app",
                  "approval": None},
        "hermes": {"path": "hermes/x.py:1", "symbol": "s"},
        "openclaw": {"path": "openclaw/x.ts:1", "symbol": "s"},
        "notes": "v1.2 transport contract test fixture",
    }


def _ws_text_frame(payload: bytes) -> bytes:
    """An unmasked (server -> client) RFC6455 text frame."""
    header = bytes([0x81])
    length = len(payload)
    if length < 126:
        header += bytes([length])
    elif length < 65536:
        header += bytes([126]) + length.to_bytes(2, "big")
    else:
        header += bytes([127]) + length.to_bytes(8, "big")
    return header + payload


def _closed_loopback_url() -> str:
    """A loopback URL guaranteed to have nothing listening on it."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return f"http://127.0.0.1:{port}"


@pytest.fixture()
def local_http():
    """A real stdlib HTTP server bound to 127.0.0.1 for transport tests."""
    state = {"requests": [], "expected_token": None}

    class _Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "differential-8u"

        def log_message(self, *args):  # keep test output clean
            return

        def _record(self, body):
            state["requests"].append({
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers.items()),
                "body": body,
            })

        def _send_json(self, status, payload, extra_headers=None):
            raw = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            self._record(None)
            if path == "/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                for name, data in (("start", "1"), ("delta", "2"), ("done", "3")):
                    self.wfile.write(
                        f"event: {name}\ndata: {data}\n\n".encode("utf-8"))
                    self.wfile.flush()
                self.close_connection = True
                return
            if path == "/nonce":
                self._send_json(200, {"ok": True, "value": "stable"},
                                {"X-Request-Id": uuid.uuid4().hex})
                return
            if path == "/token":
                supplied = self.headers.get("X-Api-Token")
                self._send_json(
                    200 if supplied == state["expected_token"] else 401,
                    {"authorized": supplied == state["expected_token"]})
                return
            self._send_json(200, {"ok": True, "path": path})

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            text = raw.decode("utf-8", "replace")
            try:
                parsed = json.loads(text) if text else None
            except ValueError:
                parsed = None
            self._record(parsed if parsed is not None else text)
            path = urllib.parse.urlsplit(self.path).path
            if path == "/rpc":
                method = (parsed or {}).get("method")
                message_id = (parsed or {}).get("id", 1)
                if method == "missing.method":
                    self._send_json(200, {
                        "type": "res", "id": message_id, "ok": False,
                        "error": {"code": -32601, "message": "method not found"}})
                else:
                    self._send_json(200, {
                        "type": "res", "id": message_id, "ok": True,
                        "payload": {"panels": []}})
                return
            self._send_json(200, {"ok": True, "received": parsed})

    class _QuietServer(http.server.ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            # A client (e.g. the raw WS handshake probe) may close mid-request;
            # that is expected hermetically and must not dump a traceback.
            exc = sys.exc_info()[1]
            if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
                return
            super().handle_error(request, client_address)

    server = _QuietServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield {
            "base_url": f"http://127.0.0.1:{port}",
            "state": state,
            "host": host,
            "port": port,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def local_ws():
    """A minimal real RFC6455 server that completes one handshake + hello."""
    state = {"handshake": None}
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    _host, port = listener.getsockname()

    def _serve():
        try:
            conn, _ = listener.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(5)
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
            head = buf.split(b"\r\n\r\n", 1)[0].decode("latin-1")
            state["handshake"] = head
            key = ""
            for line in head.split("\r\n"):
                name, _, value = line.partition(":")
                if name.strip().lower() == "sec-websocket-key":
                    key = value.strip()
                    break
            accept = base64.b64encode(hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
            ).digest()).decode("ascii")
            conn.sendall((
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n"
                "\r\n").encode("latin-1"))
            hello = json.dumps(
                {"type": "hello", "scopes": ["gateway:read"]}).encode("utf-8")
            conn.sendall(_ws_text_frame(hello))
            time.sleep(0.05)
            conn.sendall(bytes([0x88, 0x02]) + (1000).to_bytes(2, "big"))
            time.sleep(0.05)

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    try:
        yield {"base_url": f"http://127.0.0.1:{port}", "state": state, "port": port}
    finally:
        listener.close()
        thread.join(timeout=5)


def test_adapter_supports_kinds(runner, local_http, monkeypatch):
    """REQUIRES v1.2: Adapter.execute accepts cli/http/sse/ws/rpc."""
    monkeypatch.setenv("QUBIT_BIN", sys.executable)
    monkeypatch.setenv("QUBIT_DIFFERENTIAL_QUBIT_URL", local_http["base_url"])
    monkeypatch.delenv("QUBIT_DIFFERENTIAL_HERMES_URL", raising=False)
    monkeypatch.delenv("QUBIT_DIFFERENTIAL_OPENCLAW_URL", raising=False)
    adapter = _qubit_transport_adapter(runner)
    assert adapter is not None, "runner exposes no adapter with an execute() surface"

    specs = {
        "cli": {"kind": "cli", "argv": ["--version"], "env": {}},
        "http": _http_spec("http", "/json"),
        "sse": _http_spec("sse", "/events"),
        "ws": {"kind": "ws", "path": "/ws", "headers": {}, "messages": []},
        "rpc": _http_spec("rpc", "/rpc", method="POST",
                          body={"type": "req", "id": 1,
                                "method": "ui.panels", "params": {}}),
    }
    accepted = {}
    for kind, spec in specs.items():
        try:
            call = adapter.execute(spec)
        except NotImplementedError:
            continue
        except Exception as exc:  # accepted the kind; transport/exec failed
            accepted[kind] = exc
            continue
        assert isinstance(call, runner.CallResult), (
            f"{kind}: execute returned {type(call).__name__}, not CallResult")
        accepted[kind] = call

    missing = sorted(set(TRANSPORT_KINDS) - set(accepted))
    assert missing == [], f"transport adapter does not accept kind(s): {missing}"
    for kind in ("http", "sse", "ws", "rpc"):
        outcome = accepted[kind]
        if isinstance(outcome, runner.CallResult):
            assert outcome.transport == kind, (
                f"{kind}: CallResult.transport={outcome.transport!r}")


def test_http_adapter_requires_resolvable_url(runner, local_http, tmp_path, monkeypatch):
    """REQUIRES v1.2: no QUBIT_DIFFERENTIAL_*_URL -> OPERATOR_ONLY, never PASS."""
    monkeypatch.setenv("QUBIT_BIN", sys.executable)
    for name in ("QUBIT_DIFFERENTIAL_QUBIT_URL", "QUBIT_DIFFERENTIAL_HERMES_URL",
                 "QUBIT_DIFFERENTIAL_OPENCLAW_URL",
                 "QUBIT_DIFFERENTIAL_REFERENCE_URL"):
        monkeypatch.delenv(name, raising=False)
    adapter = _qubit_transport_adapter(runner)
    assert adapter is not None

    # Adapter level: no resolvable URL must not fabricate a success, and must
    # not invent a network path (the server sees nothing).
    requests_before = len(local_http["state"]["requests"])
    outcome = None
    try:
        outcome = adapter.execute(_http_spec("http", "/json"))
    except NotImplementedError:
        outcome = None
    except Exception as exc:
        unavailable = getattr(runner, "TransportUnavailable", RuntimeError)
        assert isinstance(exc, (unavailable, RuntimeError, OSError, ValueError)), exc
        outcome = None
    if isinstance(outcome, runner.CallResult):
        assert outcome.http_status != 200, (
            "http transport reported success with no URL configured")
        assert outcome.json != {"ok": True, "path": "/json"}, (
            "http transport fabricated a success body with no URL configured")
    assert len(local_http["state"]["requests"]) == requests_before, (
        "http transport invented a network path despite no resolvable URL")

    # Row level: the request row is OPERATOR_ONLY and never PASS.
    fixture = _transport_fixture(
        "api.http.no_url_8u", "api",
        {"kind": "http", "method": "GET", "path": "/json", "headers": {},
         "body": None, "argv": []},
        expected={"http_status": 200})
    path = _write_fixture(tmp_path, fixture)
    row = _row(capability_id="api.http.no_url_8u", family="api",
               disposition="OPEN", fixture=str(path))
    result = runner.run_inventory([row], tmp_path,
                                  adapters={"qubit": adapter})[0]
    assert result["disposition"] == "OPERATOR_ONLY", (
        f"no-URL http row must be OPERATOR_ONLY, got {result['disposition']!r} "
        f"(diagnostics={result.get('diagnostics')!r})")
    assert result["disposition"] != "PASS"
    assert result["qubit_executed"] is False
    assert result["operator_prerequisite"], "OPERATOR_ONLY needs a prerequisite"

    # Positive control: a resolvable loopback URL really executes the request.
    monkeypatch.setenv("QUBIT_DIFFERENTIAL_QUBIT_URL", local_http["base_url"])
    adapter = _qubit_transport_adapter(runner)
    result = runner.run_inventory([row], tmp_path,
                                  adapters={"qubit": adapter})[0]
    assert result["qubit_executed"] is True, (
        "http transport did not execute against a resolvable URL: "
        f"{result.get('operator_prerequisite')!r}")
    assert result["qubit_payload"]["http_status"] == 200
    assert any(record["path"] == "/json"
               for record in local_http["state"]["requests"]), (
        "loopback server never received the http request")


def test_http_token_never_in_result(runner, local_http, tmp_path, monkeypatch):
    """REQUIRES v1.2: an env-ref auth token never enters the result/report."""
    token = "tok-8u-differential-9f3a2b7c"
    local_http["state"]["expected_token"] = token
    monkeypatch.setenv("QUBIT_BIN", sys.executable)
    monkeypatch.setenv("QUBIT_DIFFERENTIAL_QUBIT_URL", local_http["base_url"])
    monkeypatch.setenv("QUBIT_DIFFERENTIAL_QUBIT_TOKEN", token)
    adapter = _qubit_transport_adapter(runner)
    assert adapter is not None

    # The server only returns 200 when the resolved token really arrived, and
    # never echoes it — a 200 proves resolution without a leak.
    spec = _http_spec(
        "http", "/token",
        auth={"header": "X-Api-Token",
              "value_ref": "env:QUBIT_DIFFERENTIAL_QUBIT_TOKEN"})
    call = adapter.execute(spec)
    assert call.http_status == 200, (
        "auth token was not resolved from env and sent "
        f"(status={call.http_status!r})")
    adapter_blob = json.dumps(call.to_payload(), sort_keys=True, default=str)
    assert token not in adapter_blob, "token leaked into CallResult payload"
    assert token not in json.dumps(getattr(call, "meta", {}) or {},
                                   sort_keys=True, default=str), (
        "token leaked into CallResult.meta")

    # Row level: the documented v1.2 fixture additions (transport/base_url_ref/
    # auth) must resolve from env and still never leak the token.
    fixture = _transport_fixture(
        "api.http.token_8u", "api",
        {"kind": "http", "method": "GET", "path": "/token",
         "headers": {}, "body": None, "argv": []},
        expected={"http_status": 200})
    fixture["transport"] = "http"
    fixture["base_url_ref"] = "env:QUBIT_DIFFERENTIAL_QUBIT_URL"
    fixture["auth"] = {"header": "X-Api-Token",
                       "value_ref": "env:QUBIT_DIFFERENTIAL_QUBIT_TOKEN"}
    path = _write_fixture(tmp_path, fixture)
    row = _row(capability_id="api.http.token_8u", family="api",
               disposition="OPEN", fixture=str(path))
    result = runner.run_inventory([row], tmp_path,
                                  adapters={"qubit": adapter})[0]
    report_blob = json.dumps(result, sort_keys=True, default=str)
    assert token not in report_blob, "token leaked into row result/report"
    assert result["qubit_payload"]["http_status"] == 200, (
        "row-level auth token was not resolved/sent: "
        f"{result.get('qubit_payload')!r}")


def test_sse_event_order_preserved(runner, local_http, monkeypatch):
    """REQUIRES v1.2 (sse): server event order is preserved and reported."""
    monkeypatch.setenv("QUBIT_BIN", sys.executable)
    monkeypatch.setenv("QUBIT_DIFFERENTIAL_QUBIT_URL", local_http["base_url"])
    adapter = _qubit_transport_adapter(runner)
    assert adapter is not None
    call = adapter.execute(_http_spec("sse", "/events"))
    assert call.transport == "sse", call.transport
    assert call.http_status == 200, call.http_status
    names = [event.get("event") for event in (call.events or [])]
    assert names == ["start", "delta", "done"], (
        f"SSE server order not preserved: {names!r}")

    payload = call.to_payload()
    expected = {"events": [{"event": "start"}, {"event": "delta"},
                           {"event": "done"}]}
    assert runner.compare_expected_actual(expected, payload) == [], (
        "in-order SSE events were reported as different")
    reordered = {"events": [{"event": "done"}, {"event": "delta"},
                            {"event": "start"}]}
    assert runner.compare_expected_actual(reordered, payload), (
        "out-of-order SSE events were not reported")
    normalized = runner.normalize({"events": call.events}, [])
    assert [event.get("event") for event in normalized["events"]] == names


def test_rpc_error_envelope_is_failure_not_success(runner, local_http, tmp_path,
                                                   monkeypatch):
    """REQUIRES v1.2 (rpc): an error envelope cannot be PASS/SAFE_EQUIVALENT."""
    monkeypatch.setenv("QUBIT_BIN", sys.executable)
    monkeypatch.setenv("QUBIT_DIFFERENTIAL_QUBIT_URL", local_http["base_url"])
    adapter = _qubit_transport_adapter(runner)
    assert adapter is not None
    request = {"kind": "rpc", "method": "POST", "path": "/rpc", "headers": {},
               "body": {"type": "req", "id": 1, "method": "missing.method",
                        "params": {}}}
    call = adapter.execute(dict(request))
    envelope = call.to_payload().get("json")
    assert isinstance(envelope, dict) and envelope.get("ok") is False, (
        f"rpc error envelope not captured: {envelope!r}")
    assert "error" in envelope, f"rpc error detail missing: {envelope!r}"

    fixture = _transport_fixture(
        "rpc.methodregistry.error_8u", "rpc", dict(request),
        expected={"http_status": 200,
                  "json_schema": {"type": "res", "id": 1, "ok": True}})
    path = _write_fixture(tmp_path, fixture)
    row = _row(capability_id="rpc.methodregistry.error_8u", family="rpc",
               disposition="OPEN", fixture=str(path))
    result = runner.run_inventory([row], tmp_path,
                                  adapters={"qubit": adapter})[0]
    assert result["disposition"] not in ("PASS", "SAFE_EQUIVALENT"), (
        "an rpc error envelope was classified as success: "
        f"{result['disposition']!r}")
    assert result["disposition"] == "FAIL", (
        f"rpc error envelope should FAIL, got {result['disposition']!r} "
        f"(diagnostics={result.get('diagnostics')!r})")


def test_transport_nondeterministic_headers_normalized(runner, local_http,
                                                       monkeypatch):
    """REQUIRES v1.2: Date/nonce/duration do not change result_hash."""
    monkeypatch.setenv("QUBIT_BIN", sys.executable)
    monkeypatch.setenv("QUBIT_DIFFERENTIAL_QUBIT_URL", local_http["base_url"])
    adapter = _qubit_transport_adapter(runner)
    assert adapter is not None
    spec = _http_spec("http", "/nonce")
    first = adapter.execute(spec)
    second = adapter.execute(spec)
    first_payload = first.to_payload()
    second_payload = second.to_payload()
    assert "duration_ms" not in first_payload, (
        "duration_ms is volatile and must be excluded from the hashed payload")
    assert runner.result_hash(first_payload) == runner.result_hash(second_payload), (
        "Date/nonce/duration changed result_hash across identical inputs")
    assert first.headers.get("Date") is not None, (
        "test server did not emit a Date header; normalization untested")
    assert first.headers == second.headers, (
        "volatile transport headers were not normalized: "
        f"{first.headers!r} != {second.headers!r}")


def test_ws_handshake_recorded_or_operator_only(runner, local_ws, tmp_path,
                                                monkeypatch):
    """REQUIRES v1.2 (ws): reachable records handshake/scopes; else OPERATOR_ONLY."""
    monkeypatch.setenv("QUBIT_BIN", sys.executable)

    # Reachable: a real RFC6455 handshake followed by a hello frame.
    monkeypatch.setenv("QUBIT_DIFFERENTIAL_QUBIT_URL", local_ws["base_url"])
    adapter = _qubit_transport_adapter(runner)
    assert adapter is not None
    call = adapter.execute({"kind": "ws", "path": "/ws", "headers": {},
                            "messages": []})
    assert call.transport == "ws", call.transport
    assert call.http_status == 101, (
        f"ws handshake not recorded (status={call.http_status!r})")
    persisted = call.persisted_state or {}
    assert persisted.get("handshake_status") == 101, (
        f"ws handshake not recorded in persisted_state: {persisted!r}")
    header_names = {str(name).lower() for name in (call.headers or {})}
    assert "upgrade" in header_names, (
        f"ws handshake headers not captured: {call.headers!r}")
    messages = [event for event in (call.events or [])
                if event.get("event") == "message"]
    assert messages, f"ws frames not captured: {call.events!r}"
    assert "gateway:read" in str(messages[0].get("data")), (
        f"ws scopes not recorded: {messages[0]!r}")

    # Unreachable: OPERATOR_ONLY, never PASS.
    monkeypatch.setenv("QUBIT_DIFFERENTIAL_QUBIT_URL", _closed_loopback_url())
    adapter = _qubit_transport_adapter(runner)
    fixture = _transport_fixture(
        "ws.handshake.unavailable_8u", "ws",
        {"kind": "ws", "method": "GET", "path": "/ws", "headers": {},
         "body": None, "argv": []},
        expected={"handshake": {"type": "hello", "protocol": "qubit-gateway/1"}})
    path = _write_fixture(tmp_path, fixture)
    row = _row(capability_id="ws.handshake.unavailable_8u", family="ws",
               disposition="OPEN", fixture=str(path))
    result = runner.run_inventory([row], tmp_path,
                                  adapters={"qubit": adapter})[0]
    assert result["disposition"] == "OPERATOR_ONLY", (
        f"unreachable ws row must be OPERATOR_ONLY, got {result['disposition']!r} "
        f"(diagnostics={result.get('diagnostics')!r})")
    assert result["disposition"] != "PASS"


# =========================================================================== #
# v1.5 PINNED-REFERENCE HONESTY + TRANSPORT SECRET REDACTION
#
# The runner supports pinned references via
# QUBIT_DIFFERENTIAL_<PROJECT>_BIN/_COMMIT and a real tri-way PASS exists for
# the cli help fixture (see /tmp/qubit-differential/runner/REPORT_PINNED.json).
# These tests lock the honesty of that claim:
#   * PASS only when the executed reference commit equals CONTRACTUAL_PINS;
#   * bin/version/commit/pinned provenance is recorded per project;
#   * a pinned synthetic help differential yields stable, populated hashes;
#   * an env-resolved token never reaches argv/env-dump/stdout/stderr/result;
#   * an OPEN row keeps verify/report_exit_code non-zero; and
#   * the pinned/coverage gate names are all present.
#
# Hermetic: fake adapters return fixed CallResults; the only subprocesses are
# the current interpreter running a generated --version probe. No network.
# =========================================================================== #
def _write_version_script(tmp_path, project, version_text):
    """A tiny executable whose ``--version`` prints ``version_text``."""
    script = tmp_path / f"{project}-version-probe"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "if '--version' in sys.argv:\n"
        f"    print({version_text!r})\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return str(script)


def _scripted_reference_adapter(runner_mod, project, binary, **payload):
    """A reference adapter with a real ``binary`` (so provenance is probed).

    Unlike ``_fake_adapter`` the binary points at a generated executable, which
    lets ``_probe_version`` capture a real version line while ``execute`` still
    returns a deterministic synthetic reference payload.
    """

    class _ScriptedReferenceAdapter:
        is_mock = False

        def __init__(self):
            self.name = project
            self.project = project
            self.binary = binary

        def available(self):
            return True

        def execute(self, request):
            return runner_mod.CallResult(**payload)

    return _ScriptedReferenceAdapter()


def test_pinned_reference_pass_requires_commit_match(runner, tmp_path, monkeypatch):
    doc = _cli_fixture(
        runner,
        "cli.pinned.commit.match",
        references={"hermes": {"argv": ["hermes", "--help"], "env": {}}},
    )
    path = _write_fixture(tmp_path, doc)
    row = _row(
        capability_id="cli.pinned.commit.match", disposition="OPEN", fixture=str(path))
    adapters = {
        "qubit": _fake_adapter(runner, "qubit", exit_code=0, stdout="usage"),
        "hermes": _fake_adapter(runner, "hermes", exit_code=0, stdout="usage"),
    }

    # The exact contractual pin is the ONLY commit that can yield PASS.
    monkeypatch.setenv("QUBIT_DIFFERENTIAL_HERMES_COMMIT", PIN_QUBIT)
    matched = runner.run_inventory([row], tmp_path, adapters=adapters)[0]
    assert matched["references"]["hermes"]["reference_commit"] == PIN_QUBIT
    assert matched["disposition"] == "PASS", matched["diagnostics"]
    clean = _gate_ok_map(runner.apply_fail_closed_gates([matched], [matched]))
    assert clean["unpinned_reference_pass"] is True

    # A wrong commit still executes the reference but can never claim PASS.
    wrong = "d" * 40
    monkeypatch.setenv("QUBIT_DIFFERENTIAL_HERMES_COMMIT", wrong)
    mismatched = runner.run_inventory([row], tmp_path, adapters=adapters)[0]
    assert mismatched["reference_executed"] is True
    assert mismatched["references"]["hermes"]["reference_pinned"] is False
    assert mismatched["references"]["hermes"]["reference_commit"] == wrong
    assert mismatched["disposition"] in ("SAFE_EQUIVALENT", "FAIL"), (
        f"a wrong reference commit must not PASS; got {mismatched['disposition']!r}")
    assert mismatched["disposition"] != "PASS"
    gates = _gate_ok_map(
        runner.apply_fail_closed_gates([mismatched], [mismatched]))
    assert gates["unpinned_reference_pass"] is True, (
        "unpinned_reference_pass must only fire on an actual PASS claim")

    # And it DOES fire when a PASS result carries an unpinned reference.
    fake_pass = _result(
        _row(capability_id="cli.pinned.commit.match", disposition="PASS"),
        disposition="PASS", reference_executed=True,
        unpinned_references=["hermes"],
        references={"hermes": {"project": "hermes", "executed": True,
                               "reference_pinned": False}})
    fired = _gate_ok_map(runner.apply_fail_closed_gates([fake_pass], [fake_pass]))
    assert fired["unpinned_reference_pass"] is False, (
        "a PASS that relies on an unpinned reference must trip "
        "unpinned_reference_pass")


def test_reference_provenance_recorded(runner, tmp_path, monkeypatch):
    monkeypatch.delenv("QUBIT_DIFFERENTIAL_HERMES_COMMIT", raising=False)
    monkeypatch.delenv("QUBIT_DIFFERENTIAL_OPENCLAW_COMMIT", raising=False)
    qubit_version = f"Hermes Agent v0.21.1 ({PIN_QUBIT})"
    openclaw_version = f"OpenClaw 2026.9.4 ({PIN_OPENCLAW})"
    qubit_bin = _write_version_script(tmp_path, "hermes", qubit_version)
    openclaw_bin = _write_version_script(tmp_path, "openclaw", openclaw_version)

    try:
        doc = _cli_fixture(
            runner,
            "cli.provenance.recorded",
            references={
                "hermes": {"argv": ["hermes", "--help"], "env": {}},
                "openclaw": {"argv": ["openclaw", "--help"], "env": {}},
            },
        )
        path = _write_fixture(tmp_path, doc)
        row = _row(
            capability_id="cli.provenance.recorded", disposition="OPEN",
            fixture=str(path))
        adapters = {
            "qubit": _fake_adapter(runner, "qubit", exit_code=0, stdout="usage"),
            "hermes": _scripted_reference_adapter(
                runner, "hermes", qubit_bin, exit_code=0, stdout="usage"),
            "openclaw": _scripted_reference_adapter(
                runner, "openclaw", openclaw_bin, exit_code=0, stdout="usage"),
        }
        result = runner.run_inventory([row], tmp_path, adapters=adapters)[0]

        expected = {
            "hermes": (qubit_bin, qubit_version, PIN_QUBIT),
            "openclaw": (openclaw_bin, openclaw_version, PIN_OPENCLAW),
        }
        for project, (binary, version, commit) in expected.items():
            info = result["references"][project]
            assert info["executed"] is True, info
            assert info["binary"] == binary, info
            assert info["reference_version"] == version, info
            assert info["reference_commit"] == commit, info
            assert info["reference_pinned"] is True, info
            assert info["argv"], info
            assert str(info["result_hash"]).startswith("sha256:"), info
        assert result["unpinned_references"] == []
        assert result["disposition"] == "PASS", result["diagnostics"]

        # Provenance also survives into the assembled report per project.
        report = runner.build_report(
            [result], runner.apply_fail_closed_gates([result], [result]))
        report_refs = report["rows"][0]["references"]
        assert report_refs["hermes"]["reference_commit"] == PIN_QUBIT
        assert report_refs["openclaw"]["reference_commit"] == PIN_OPENCLAW
        assert report_refs["hermes"]["binary"] == qubit_bin
        assert report_refs["openclaw"]["reference_pinned"] is True
    finally:
        runner._VERSION_CACHE.pop(qubit_bin, None)
        runner._VERSION_CACHE.pop(openclaw_bin, None)


def test_pinned_help_differential_hashes(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("QUBIT_DIFFERENTIAL_HERMES_COMMIT", PIN_QUBIT)
    monkeypatch.setenv("QUBIT_DIFFERENTIAL_OPENCLAW_COMMIT", PIN_OPENCLAW)

    canonical = runner.CANONICAL_QUBIT_COMMAND
    qubit_help = f"usage: {canonical} [-h] [--json] COMMAND ..."
    qubit_help = "Usage: hermes [OPTIONS] COMMAND [ARGS]...\n  Hermes Agent CLI"
    openclaw_help = "OpenClaw 2026.9.4\nUsage: openclaw [options] [command]"

    doc = _cli_fixture(
        runner,
        "cli.pinned.help.hashes",
        references={
            "hermes": {"argv": ["hermes", "--help"], "env": {}},
            "openclaw": {"argv": ["openclaw", "--help"], "env": {}},
        },
    )
    path = _write_fixture(tmp_path, doc)
    row = _row(
        capability_id="cli.pinned.help.hashes", disposition="OPEN", fixture=str(path))

    def _fresh_adapters():
        return {
            "qubit": _fake_adapter(runner, "qubit", exit_code=0, stdout=qubit_help),
            "hermes": _fake_adapter(runner, "hermes", exit_code=0, stdout=qubit_help),
            "openclaw": _fake_adapter(
                runner, "openclaw", exit_code=0, stdout=openclaw_help),
        }

    first = runner.run_inventory([row], tmp_path, adapters=_fresh_adapters())[0]
    second = runner.run_inventory([row], tmp_path, adapters=_fresh_adapters())[0]

    assert first["disposition"] == "PASS", first["diagnostics"]
    assert first["upstream_result_hash"], "upstream_result_hash not populated"
    assert first["qubit_result_hash"], "qubit_result_hash not populated"
    assert first["upstream_result_hash"].startswith("sha256:")
    assert first["qubit_result_hash"].startswith("sha256:")
    assert first["upstream_result_hash"] == second["upstream_result_hash"], (
        "upstream_result_hash is not stable across two pinned runs: "
        f"{first['upstream_result_hash']} != {second['upstream_result_hash']}")
    assert first["qubit_result_hash"] == second["qubit_result_hash"], (
        "qubit_result_hash is not stable across two pinned runs: "
        f"{first['qubit_result_hash']} != {second['qubit_result_hash']}")
    assert first["references"]["hermes"]["reference_pinned"] is True
    assert first["references"]["openclaw"]["reference_pinned"] is True
    assert first["unpinned_references"] == []
    assert first["upstream_result_hash"] != first["qubit_result_hash"], (
        "upstream and qubit hashes collapsed to one value; tri-way proof is vacuous")


def test_transport_adapters_do_not_leak_token_in_argv(runner, local_http, tmp_path,
                                                      monkeypatch):
    token = "tok-8u-pinned-leak-4d2f9a1c"
    local_http["state"]["expected_token"] = token
    monkeypatch.setenv("QUBIT_BIN", sys.executable)
    monkeypatch.setenv("QUBIT_DIFFERENTIAL_QUBIT_URL", local_http["base_url"])
    monkeypatch.setenv("QUBIT_DIFFERENTIAL_QUBIT_TOKEN", token)

    # --- http: the token resolves from env, is sent, and never leaks -------- #
    adapter = _qubit_transport_adapter(runner)
    assert adapter is not None
    spec = _http_spec(
        "http", "/token",
        auth={"header": "X-Api-Token",
              "value_ref": "env:QUBIT_DIFFERENTIAL_QUBIT_TOKEN"})
    call = adapter.execute(spec)
    assert call.http_status == 200, (
        f"env token was not resolved/sent (status={call.http_status!r})")
    http_blobs = {
        "argv": json.dumps(getattr(adapter, "argv", None), default=str),
        "env_dump": json.dumps(getattr(adapter, "env", None), default=str),
        "meta": json.dumps(call.meta, sort_keys=True, default=str),
        "payload": json.dumps(call.to_payload(), sort_keys=True, default=str),
        "stdout": str(call.stdout or ""),
        "stderr": str(call.stderr or ""),
    }
    leaked = sorted(name for name, blob in http_blobs.items() if token in blob)
    assert leaked == [], f"http token leaked into {leaked}"
    assert token not in json.dumps(spec, sort_keys=True, default=str), (
        "the resolved token was written back into the request spec")

    # --- cli: the token is passed via env (proved by hash), never in argv --- #
    cli_probe = (
        "import hashlib,os;"
        "print(hashlib.sha256(os.environ.get('MY_TOKEN','').encode()).hexdigest())")
    cli_adapter = runner.QubitAdapter()
    cli_call = cli_adapter.execute({
        "kind": "cli",
        "argv": ["-c", cli_probe],
        "env": {"MY_TOKEN": "env:QUBIT_DIFFERENTIAL_QUBIT_TOKEN"},
    })
    assert cli_call.exit_code == 0, (cli_call.exit_code, cli_call.stderr)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert digest in (cli_call.stdout or ""), (
        "env token never reached the child process; redaction test is vacuous")
    cli_argv = json.dumps(cli_call.meta.get("argv"), default=str)
    assert token not in cli_argv, "token leaked into the adapter argv"
    assert token not in json.dumps(cli_call.meta, sort_keys=True, default=str), (
        "token leaked into the adapter meta dump")
    assert token not in (cli_call.stdout or ""), "token leaked into stdout"
    assert token not in (cli_call.stderr or ""), "token leaked into stderr"
    assert token not in json.dumps(cli_call.to_payload(), sort_keys=True,
                                   default=str), "token leaked into CallResult payload"

    # --- row level: the full result/report never carries the env token ------ #
    fixture = _transport_fixture(
        "api.http.argv_token_8u", "api",
        {"kind": "http", "method": "GET", "path": "/token", "headers": {},
         "body": None, "argv": []},
        expected={"http_status": 200})
    fixture["auth"] = {"header": "X-Api-Token",
                       "value_ref": "env:QUBIT_DIFFERENTIAL_QUBIT_TOKEN"}
    path = _write_fixture(tmp_path, fixture)
    row = _row(capability_id="api.http.argv_token_8u", family="api",
               disposition="OPEN", fixture=str(path))
    result = runner.run_inventory([row], tmp_path,
                                  adapters={"qubit": adapter})[0]
    assert token not in json.dumps(result, sort_keys=True, default=str), (
        "env token leaked into the row result")
    assert token not in (result.get("qubit_payload", {}) or {}).get("stdout", ""), (
        "env token leaked into the captured stdout")


def test_verify_exit_nonzero_when_open_rows(runner, tmp_path):
    row = _row(capability_id="cli.verify.open", disposition="OPEN")
    result = _result(row, disposition="OPEN", executed=False)
    gates = runner.apply_fail_closed_gates([row], [result])
    report = runner.build_report([result], gates)
    assert runner.report_exit_code(report) != 0, (
        "an OPEN row must make report_exit_code non-zero")

    report_path = tmp_path / "open_report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    proc = _run_cli("verify", "--report", str(report_path))
    assert proc.returncode != 0, (
        f"verify must fail closed on OPEN rows; rc={proc.returncode} "
        f"stdout={proc.stdout[-500:]!r} stderr={proc.stderr[-500:]!r}")


def test_gate_names_include_pinned_and_coverage(runner):
    required = (
        "unpinned_reference_pass",
        "inventory_coverage",
        "unexecuted_open_row",
        "pass_without_reference",
    )
    declared = tuple(getattr(runner, "GATE_NAMES", ()))
    for name in required:
        assert name in declared, f"GATE_NAMES omits {name!r}: {declared}"

    row = _row(capability_id="cli.gate.names")
    emitted = _gate_ok_map(runner.apply_fail_closed_gates([row], [_result(row)]))
    for name in required:
        assert name in emitted, (
            f"runner did not emit gate {name!r}; emitted: {sorted(emitted)}")


# =========================================================================== #
# v1.6 FIXTURE INTEGRITY + REFERENCE-ONLY HONESTY
#
# The read-only transport audit
# (/tmp/qubit-differential/inventory/qubit_transport/QUBIT_TRANSPORT_NOTES.md)
# enumerated seven INVENTED fixture routes. The fixtures must not target them in
# any executable field (request.path, sequence paths, dashboard_route,
# dashboard_control, base_url) — only free-text notes/prerequisites may name a
# dead route as a warning. The `--references-only` diagnostic executes the
# upstream reference only and therefore can never prove parity (never PASS).
#
# These tests fail closed: an absent manifest / runner feature fails the test,
# it is never skipped away. They are hermetic (stdlib only, no network).
# =========================================================================== #
DEAD_ROUTE_AUDIT_NOTES = Path(
    "/tmp/qubit-differential/inventory/qubit_transport/QUBIT_TRANSPORT_NOTES.md")

# Literal seed of the audit's "Confirmed dead fixture paths (7)". Kept in-tree
# so the scan is hermetic; merged with the audit file when it is present.
DEAD_FIXTURE_ROUTES = (
    "/agent/api/qubit/browser/status",
    "/agent/api/qubit/dashboard/op",
    "/agent/api/qubit/device/profile",
    "/agent/api/qubit/recovery/status",
    "/agent/api/qubit/gateway/rpc",
    "/agent/api/qubit/gateway/ws",
    "/agent/api/qubit/voice/turn",
)

_NOTE_EXEMPT_KEY_MARKERS = ("note", "prerequisit")


def _manifest_fixture_path(entry) -> Path | None:
    """Resolve a MANIFEST entry's ``path`` to an existing file on disk."""
    raw = str(entry.get("path"))
    candidates = [REPO_ROOT / raw, FIXTURES_DIR / raw, Path(raw)]
    if raw.startswith("tests/fixtures/differential/"):
        candidates.append(
            FIXTURES_DIR / raw.split("tests/fixtures/differential/", 1)[1])
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _audited_dead_routes() -> set[str]:
    """The dead/invented route set, from the audit notes + the literal seed."""
    routes = set(DEAD_FIXTURE_ROUTES)
    if DEAD_ROUTE_AUDIT_NOTES.is_file():
        text = DEAD_ROUTE_AUDIT_NOTES.read_text(
            encoding="utf-8", errors="replace")
        marker = "Confirmed dead fixture paths"
        index = text.find(marker)
        if index != -1:
            section = text[index:]
            stop = re.search(r"\n#{1,6} ", section)
            if stop:
                section = section[: stop.start()]
            # The dead list ends at the em-dash; the remainder of the paragraph
            # names the *real* routes and must not be treated as dead.
            section = re.split(r"\u2014|--", section, maxsplit=1)[0]
            routes.update(re.findall(r"`(/[^`\s]+)`", section))
    return routes


def _iter_non_note_strings(node, path="$"):
    """Yield ``(json_path, value)`` for every string outside notes/prereqs."""
    if isinstance(node, dict):
        for key, value in node.items():
            if any(marker in str(key).lower()
                   for marker in _NOTE_EXEMPT_KEY_MARKERS):
                continue
            yield from _iter_non_note_strings(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _iter_non_note_strings(value, f"{path}[{index}]")
    elif isinstance(node, str):
        yield path, node


def _fixture_scan_paths() -> list[Path]:
    """MANIFEST plus every fixture instance (schema/ is not a fixture)."""
    paths = [MANIFEST_PATH] if MANIFEST_PATH.is_file() else []
    paths += [p for p in sorted(FIXTURES_DIR.glob("*/*.json"))
              if p.parent.name != "schema"]
    return paths


def test_no_fixture_targets_dead_route():
    entries = _manifest_fixture_entries()
    assert entries is not None, f"fixture manifest missing: {MANIFEST_PATH}"
    dead_routes = sorted(_audited_dead_routes(), key=len, reverse=True)
    assert dead_routes, "no dead-route list resolved from audit notes or seed"

    offenders = []
    scanned = 0
    for entry in entries:
        fixture_path = _manifest_fixture_path(entry)
        assert fixture_path is not None, (
            f"manifest fixture missing on disk: {entry.get('path')}")
        doc = json.loads(fixture_path.read_text(encoding="utf-8"))
        scanned += 1
        for json_path, value in _iter_non_note_strings(doc):
            for route in dead_routes:
                if route in value:
                    offenders.append((entry["fixture_id"], json_path, route))

    assert scanned == len(entries) and scanned >= 41, (
        f"only scanned {scanned} of {len(entries)} manifest fixtures")
    assert offenders == [], (
        "fixtures target dead/invented routes in non-note fields (request.path, "
        f"sequence paths, dashboard_route, dashboard_control, base_url): "
        f"{offenders}")


# ------------------------------------------------------- references-only mode
def _declares_references_only(runner) -> bool:
    parser = getattr(runner, "build_parser", None)
    if callable(parser):
        try:
            built = parser()
        except Exception:  # noqa: BLE001 - introspection only
            built = None
        if built is not None:
            for action in getattr(built, "_actions", []):
                if "--references-only" in getattr(action, "option_strings", []):
                    return True
                choices = getattr(action, "choices", None)
                if isinstance(choices, dict):
                    for sub in choices.values():
                        for sub_action in getattr(sub, "_actions", []):
                            if "--references-only" in getattr(
                                    sub_action, "option_strings", []):
                                return True
    proc = _run_cli("run", "--help")
    return "--references-only" in ((proc.stdout or "") + (proc.stderr or ""))


def _references_only_probe_binary(tmp_path) -> str:
    script = tmp_path / "references-only-probe"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "if '--version' in sys.argv:\n"
        "    print('hermes-agent 0.0.0')\n"
        "else:\n"
        "    print(json.dumps({'state': 'OK', 'surface': 'reference'}))\n",
        encoding="utf-8")
    script.chmod(0o755)
    return str(script)


def test_references_only_mode_is_not_pass(runner, tmp_path, monkeypatch):
    """REQUIRES `--references-only` (no skipif hides its absence)."""
    if not _declares_references_only(runner):
        pytest.fail(
            "runner does not implement the required `--references-only` "
            "diagnostic; a reference-only run cannot claim PASS and must "
            "record reference hashes")

    monkeypatch.delenv("QUBIT_DIFFERENTIAL_HERMES_COMMIT", raising=False)
    monkeypatch.delenv("QUBIT_DIFFERENTIAL_OPENCLAW_COMMIT", raising=False)
    qubit_bin = _references_only_probe_binary(tmp_path)

    fixture = {
        "schema_version": 1,
        "fixture_id": "cli.references.only",
        "family": "cli",
        "command": {"argv": ["voicebridge", "--help"], "stdin": None, "env": {}},
        "request": None,
        "normalizers": ["product_branding"],
        "expected": {"exit_code": 0},
        "qubit": {"path": "voicebridge/cli.py:256", "caller": "build_parser",
                  "approval": None},
        "hermes": {"path": "qubit_cli/_parser.py:280",
                   "symbol": "build_top_level_parser",
                   "mapping_status": "VERIFIED"},
        "openclaw": {"path": None, "symbol": None, "mapping_status": "UNVERIFIED"},
        "references": {
            "hermes": {"argv": ["hermes", "--help"], "env": {}, "bin": qubit_bin},
        },
        "notes": "hermetic --references-only diagnostic probe",
    }
    fixture_path = tmp_path / "references_only.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")

    row = {
        "capability_id": "cli.references.only",
        "family": "cli",
        "disposition": "OPEN",
        "fixture": str(fixture_path),
        "qubit_path": "voicebridge/cli.py:256",
        "qubit_symbol": "build_parser",
        "rollback_ref": "git revert " + "0" * 40,
    }
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps([row]), encoding="utf-8")
    out = tmp_path / "references_only_report.json"

    base = ["run", "--inventory", str(inventory), "--fixtures-dir", str(tmp_path),
            "--out", str(out)]
    proc = None
    for args in (base + ["--references-only"], ["--references-only"] + base):
        proc = _run_cli(*args)
        if out.is_file():
            break
    assert out.is_file(), (
        "`--references-only` produced no report; rc="
        f"{proc.returncode} stdout={proc.stdout[-500:]!r} "
        f"stderr={proc.stderr[-500:]!r}")

    report = json.loads(out.read_text(encoding="utf-8"))
    rows = report.get("rows") if isinstance(report, dict) else report
    assert isinstance(rows, list) and rows, f"unexpected report shape: {report!r}"

    passes = [(r.get("capability_id"), r.get("disposition"))
              for r in rows if str(r.get("disposition")) == "PASS"]
    assert passes == [], (
        f"a reference-only diagnostic yielded PASS: {passes}")

    executed = [r for r in rows if r.get("reference_executed")]
    assert executed, "references-only diagnostic executed no reference"
    for result in executed:
        assert str(result.get("upstream_result_hash", "")).startswith("sha256:"), (
            f"reference hash not recorded: {result.get('capability_id')}")
        refs = result.get("references") or {}
        assert any(isinstance(info, dict) and str(info.get("result_hash", ""))
                   .startswith("sha256:") for info in refs.values()), (
            f"per-project reference hash not recorded: {result.get('capability_id')}")
    assert proc.returncode != 0 or not passes, (
        "a references-only run cannot exit clean while it proves no parity")


# ------------------------------------------------ reference CLI execution
def test_reference_cli_execution_recorded(runner, monkeypatch):
    """A declared CLI reference executes and records its hash; unpinned != PASS."""
    monkeypatch.delenv("QUBIT_DIFFERENTIAL_HERMES_COMMIT", raising=False)
    monkeypatch.delenv("QUBIT_DIFFERENTIAL_OPENCLAW_COMMIT", raising=False)
    adapters = {
        "qubit": _fake_adapter(runner, "qubit", exit_code=0, stdout="usage"),
        "hermes": _fake_adapter(runner, "hermes", exit_code=0, stdout="usage"),
        "openclaw": _fake_adapter(runner, "openclaw", exit_code=0, stdout="usage"),
    }
    results = runner.run_inventory(
        [], FIXTURES_DIR, adapters=adapters, families=["cli"])
    reference_rows = [r for r in results if r.get("references")]
    assert reference_rows, "no manifest cli fixture declares an upstream reference"

    for result in reference_rows:
        assert result["reference_executed"] is True, result.get("references")
        assert str(result.get("upstream_result_hash", "")).startswith("sha256:"), (
            f"upstream_result_hash not recorded for {result['capability_id']}")
        assert result.get("reference_result_hash") == result.get("upstream_result_hash")
        for project, info in result["references"].items():
            assert info["executed"] is True, (result["capability_id"], project)
            assert str(info["result_hash"]).startswith("sha256:"), (
                result["capability_id"], project)
        # No pin env is set in this hermetic test: the reference is floating, so
        # the row must never be claimed PASS.
        assert result["unpinned_references"], result["capability_id"]
        assert result["disposition"] != "PASS", (
            f"an unpinned CLI reference yielded PASS: {result['capability_id']}")


# --------------------------------------------------- fixture secret scanning
def test_all_fixtures_have_no_secret_literals():
    # The scanner must detect a secret-shaped literal (non-vacuous proof).
    assert _looks_secret("Bearer sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2"), (
        "secret scanner failed on a synthetic key")

    def walk(node, path, offenders):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}", offenders)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]", offenders)
        elif isinstance(node, str) and _looks_secret(node):
            offenders.append((path, f"<redacted len={len(node)}>"))

    paths = _fixture_scan_paths()
    assert len(paths) >= 41, f"not enough fixture files to scan: {len(paths)}"
    offenders = []
    for path in paths:
        doc = json.loads(path.read_text(encoding="utf-8"))
        walk(doc, f"{path.name}$", offenders)
    assert offenders == [], (
        f"secret-shaped literals in fixtures/MANIFEST: {offenders[:10]}")


# --------------------------------------------------------- manifest integrity
def test_manifest_matches_disk():
    assert MANIFEST_PATH.is_file(), f"fixture manifest missing: {MANIFEST_PATH}"
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    entries = data.get("fixtures")
    assert isinstance(entries, list) and entries, "manifest lists no fixtures"

    mismatches, missing, listed = [], [], []
    for entry in entries:
        raw = str(entry["path"])
        listed.append(raw)
        target = _manifest_fixture_path(entry)
        if target is None:
            missing.append(raw)
            continue
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        declared = str(entry["sha256"]).split(":", 1)[-1].lower()
        if actual != declared:
            mismatches.append((raw, declared, actual))

    duplicates = sorted({p for p in listed if listed.count(p) > 1})
    disk = {
        f"tests/fixtures/differential/{p.relative_to(FIXTURES_DIR)}"
        for p in sorted(FIXTURES_DIR.glob("*/*.json"))
        if p.parent.name not in ("schema", "negative", "closure")
    }
    unlisted = sorted(disk - set(listed))
    outside = sorted(set(listed) - disk)

    assert missing == [], f"manifest fixture missing on disk: {missing}"
    assert mismatches == [], f"manifest sha256 mismatch: {mismatches}"
    assert duplicates == [], f"duplicate manifest paths: {duplicates}"
    assert unlisted == [], f"fixture files not listed in MANIFEST: {unlisted}"
    assert outside == [], (
        f"manifest lists fixtures outside the family directories: {outside}")


# =========================================================================== #
# v1.7 GENERATED-REPORT INVARIANT + SEQUENCE DEAD-ROUTE COVERAGE
#
# Two fail-closed invariants over produced evidence:
#   * every fixture-derived PASS in a generated report is fully proven: a
#     non-null qubit_result_hash AND upstream_result_hash, executed=True, and
#     no unpinned references; and
#   * no fixture's executable ``sequence[].path`` steps target a dead/invented
#     route (the v1.6 scan already walks every non-note string; this locks the
#     specific sequence shape explicitly so a regression cannot slip through).
#
# The report invariant skips ONLY when no generated report exists anywhere
# (in-repo contract locations first, then the canonical /tmp runner reports).
# It is never skipped because a report is merely empty or carries no PASS.
# =========================================================================== #
_TMP_GENERATED_REPORTS = (
    Path("/tmp/qubit-differential/runner/REPORT_FINAL.json"),
    Path("/tmp/qubit-differential/runner/REPORT_FINAL2.json"),
    Path("/tmp/qubit-differential/runner/REPORT_FINAL_A.json"),
    Path("/tmp/qubit-differential/runner/REPORT_PINNED.json"),
    Path("/tmp/qubit-differential/runner/REPORT_REFERENCES_ONLY.json"),
)


def _iter_generated_reports():
    """Candidate generated reports, in-repo contract locations first."""
    seen: set[str] = set()
    candidates = [p for p in _report_candidates() if p.is_file()]
    candidates += [p for p in _TMP_GENERATED_REPORTS if p.is_file()]
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        yield path


def _is_fixture_derived(result) -> bool:
    if not isinstance(result, dict):
        return False
    return bool(result.get("fixture_derived") or result.get("manifest_fixture")
                or result.get("fixture") or result.get("fixture_hash"))


def _load_generated_report():
    """Return ``(path, doc)`` for a generated report, preferring one that
    actually carries fixture-derived results. ``(None, None)`` when none exists.
    """
    fallback = None
    for path in _iter_generated_reports():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = doc.get("rows") if isinstance(doc, dict) else None
        if not (isinstance(rows, list) and rows):
            continue
        if any(_is_fixture_derived(row) for row in rows):
            return path, doc
        if fallback is None:
            fallback = (path, doc)
    if fallback is not None:
        return fallback
    return None, None


def test_generated_report_pass_rows_are_fully_proven():
    path, report = _load_generated_report()
    if report is None:
        pytest.skip("no generated differential report present")

    rows = report.get("rows") or []
    derived = [row for row in rows if _is_fixture_derived(row)]
    passes = [row for row in derived if str(row.get("disposition")) == "PASS"]
    for result in passes:
        cid = result.get("capability_id", "<unknown>")
        qubit_hash = str(result.get("qubit_result_hash") or "")
        upstream_hash = str(result.get("upstream_result_hash") or "")
        assert qubit_hash.startswith("sha256:"), (
            f"{cid}: PASS without a non-null qubit_result_hash "
            f"(got {result.get('qubit_result_hash')!r}) in {path}")
        assert upstream_hash.startswith("sha256:"), (
            f"{cid}: PASS without a non-null upstream_result_hash "
            f"(got {result.get('upstream_result_hash')!r}) in {path}")
        assert result.get("executed") is True, (
            f"{cid}: PASS with executed={result.get('executed')!r} in {path}")
        unpinned = result.get("unpinned_references") or []
        assert not unpinned, (
            f"{cid}: PASS with unpinned references {unpinned!r} in {path}")
        for project, info in (result.get("references") or {}).items():
            if isinstance(info, dict) and info.get("executed"):
                assert info.get("reference_pinned") is not False, (
                    f"{cid}: PASS executed unpinned reference {project!r} in {path}")


def _iter_sequence_step_paths(node, path="$"):
    """Yield ``(json_path, value)`` for every ``sequence[].path`` step."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "sequence" and isinstance(value, list):
                for index, step in enumerate(value):
                    step_path = f"{path}.sequence[{index}]"
                    if isinstance(step, dict) and isinstance(step.get("path"), str):
                        yield f"{step_path}.path", step["path"]
                    yield from _iter_sequence_step_paths(step, step_path)
            else:
                yield from _iter_sequence_step_paths(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _iter_sequence_step_paths(value, f"{path}[{index}]")


def test_fixture_sequence_paths_are_not_dead_routes():
    entries = _manifest_fixture_entries()
    assert entries is not None, f"fixture manifest missing: {MANIFEST_PATH}"
    dead_routes = sorted(_audited_dead_routes(), key=len, reverse=True)
    assert dead_routes, "no dead-route list resolved from audit notes or seed"

    # Prove the scanners really see sequence[].path: both the dedicated walker
    # and the generic non-note walker used by test_no_fixture_targets_dead_route.
    probe = {"sequence": [
        {"step": "connect", "path": "/agent/api/qubit/gateway/ws"},
        {"step": "reconnect", "attempts": 1},
    ]}
    probe_paths = list(_iter_sequence_step_paths(probe))
    assert probe_paths == [("$.sequence[0].path", "/agent/api/qubit/gateway/ws")], (
        f"sequence[].path scanner is broken: {probe_paths}")
    generic_paths = dict(_iter_non_note_strings(probe))
    assert generic_paths.get("$.sequence[0].path") == "/agent/api/qubit/gateway/ws", (
        f"non-note dead-route walker does not cover sequence[].path: {generic_paths}")

    offenders = []
    scanned_paths = 0
    for entry in entries:
        fixture_path = _manifest_fixture_path(entry)
        assert fixture_path is not None, (
            f"manifest fixture missing on disk: {entry.get('path')}")
        doc = json.loads(fixture_path.read_text(encoding="utf-8"))
        for json_path, value in _iter_sequence_step_paths(doc):
            scanned_paths += 1
            for route in dead_routes:
                if route in value:
                    offenders.append((entry["fixture_id"], json_path, route))

    assert scanned_paths > 0, (
        "no sequence[].path steps found across the manifest; coverage unproven")
    assert offenders == [], (
        "fixture sequence[].path targets a dead/invented route: "
        f"{offenders}")


# =========================================================================== #
# v1.7 E-STOP SAFETY GUARD + BYTE-PARITY GATE (fail closed)
#
# The runner must never auto-execute a state-changing step (e-stop latch,
# /api/power, /api/ops/confirm, ...) unless the operator opts in with
# ``--allow-state-changing``. These tests fail closed: an absent guard is a
# test failure, never a skip. Hermetic: a loopback sink asserts zero sockets.
# =========================================================================== #
# Executable markers that make a fixture/step state-changing. Path markers are
# matched against request.path and sequence[].path; the action markers against
# the JSON body. (/agent/api/ops/run is deliberately excluded: it is a generic
# dispatch whose ops may be read-only.)
STATE_CHANGING_MARKERS = (
    "/api/power",
    "/api/ops/confirm",
    "emergency-stop",
    "emergency_stop",
)


def _fixture_executable_nodes(doc):
    """Yield every request/sequence node that would open a socket."""
    request = doc.get("request")
    if isinstance(request, dict):
        yield request
    for step in doc.get("sequence") or []:
        if isinstance(step, dict):
            yield step


def _fixture_state_changing_markers(doc) -> list[str]:
    """Every state-changing marker carried by an executable request/step."""
    found = []
    for node in _fixture_executable_nodes(doc):
        path = str(node.get("path") or "")
        body = (json.dumps(node.get("body"), sort_keys=True)
                if node.get("body") is not None else "")
        for marker in STATE_CHANGING_MARKERS:
            if marker in path or marker in body:
                found.append(marker)
    return sorted(set(found))


def _iter_state_changing_fixtures() -> list[tuple[str, dict]]:
    """Every in-tree fixture carrying an executable state-change marker."""
    out = []
    for path in sorted(FIXTURES_DIR.glob("**/*.json")):
        if path.name == "MANIFEST.json" or "schema" in path.parts:
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict) or not doc.get("fixture_id"):
            continue
        if _fixture_state_changing_markers(doc):
            out.append((str(path.relative_to(REPO_ROOT)), doc))
    return out


def _estop_fixture(fixture_id: str) -> dict:
    """A synthetic state-changing fixture (POST /api/ops/confirm)."""
    return {
        "schema_version": 1,
        "fixture_id": fixture_id,
        "family": "recovery",
        "command": None,
        "request": {
            "kind": None, "method": "POST", "path": "/api/ops/confirm",
            "headers": {},
            "body": {"id": "runtime_recover", "confirmed": True},
            "event": None, "argv": [],
        },
        "transport": "http",
        "base_url_ref": "env:QUBIT_DIFFERENTIAL_QUBIT_URL",
        "auth": {"header": "X-Api-Token",
                 "value_ref": "env:QUBIT_DIFFERENTIAL_QUBIT_TOKEN"},
        "normalizers": [],
        "expected": {"http_status": 200},
        "disposition": "OPERATOR_ONLY",
        "operator_prerequisite": (
            "State-changing e-stop/confirm sequence; requires "
            "--allow-state-changing and a live operator instance."),
        "qubit": {"path": "voicebridge/power.py:1514",
                  "caller": "power.emergency-stop", "approval": None},
        "hermes": {"path": "agent/estop.py:122", "symbol": "check_paused"},
        "openclaw": {"path": "src/gateway/server-methods/suspend.ts:32",
                     "symbol": "suspendHandlers"},
        "notes": "synthetic e-stop safety fixture (tests only; never a live target)",
    }


def _guard_inventory_row(fixture_id: str, fixture_path) -> dict:
    return {
        "capability_id": fixture_id,
        "family": "recovery",
        "upstream_project": "qubit",
        "upstream_commit": None,
        "upstream_path": "voicebridge/power.py:1514",
        "upstream_symbol": "power.emergency-stop",
        "upstream_hash": None,
        "qubit_path": "voicebridge/power.py:1514",
        "qubit_symbol": "power.emergency-stop",
        "disposition": "OPERATOR_ONLY",
        "command": None,
        "fixture": str(fixture_path),
        "fixture_hash": None,
        "upstream_result_hash": None,
        "qubit_result_hash": None,
        "test_ids": [
            "tests/test_differential_parity8u.py::test_state_changing_requires_explicit_flag"],
        "trace_refs": ["/tmp/qubit-differential/evidence/estop_safety.json"],
        "operator_prerequisite": (
            "State-changing; requires --allow-state-changing on the run command."),
        "rollback_ref": "git revert " + "0" * 40,
        "normalizations": [],
    }


@pytest.fixture()
def local_sink():
    """A loopback HTTP sink that records every request (asserts zero when off)."""
    state = {"requests": []}

    class _Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "differential-8u-sink"

        def log_message(self, *args):
            return

        def _handle(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            state["requests"].append({
                "method": self.command,
                "path": urllib.parse.urlsplit(self.path).path,
                "body": raw.decode("utf-8", "replace"),
                "headers": dict(self.headers.items()),
            })
            payload = json.dumps({"ok": True, "state": "OK"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
            self.close_connection = True

        do_GET = _handle
        do_POST = _handle
        do_PUT = _handle
        do_PATCH = _handle
        do_DELETE = _handle

    class _QuietServer(http.server.ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            exc = sys.exc_info()[1]
            if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
                return
            super().handle_error(request, client_address)

    server = _QuietServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield {"base_url": f"http://127.0.0.1:{port}", "state": state,
               "host": host, "port": port}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _run_guard_cli(runner_mod, fixture_doc, workdir, sink, monkeypatch, *, allow):
    """Run the real runner CLI over one fixture; return (exit_code, report|None)."""
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QUBIT_DIFFERENTIAL_QUBIT_URL", sink["base_url"])
    monkeypatch.setenv("QUBIT_DIFFERENTIAL_QUBIT_TOKEN", "tok-differential-8u-estop")
    monkeypatch.setenv("QUBIT_DIFFERENTIAL_OFFLINE", "1")
    fixture_path = workdir / (str(fixture_doc["fixture_id"]).replace(".", "_") + ".json")
    fixture_path.write_text(json.dumps(fixture_doc), encoding="utf-8")
    inventory_path = workdir / "inventory.json"
    inventory_path.write_text(
        json.dumps({"schema_version": 1,
                    "rows": [_guard_inventory_row(fixture_doc["fixture_id"], fixture_path)]}),
        encoding="utf-8")
    out = workdir / ("report_allow.json" if allow else "report_default.json")
    argv = ["run", "--inventory", str(inventory_path),
            "--fixtures-dir", str(workdir), "--out", str(out)]
    if allow:
        argv.append("--allow-state-changing")
    rc = runner_mod.main(argv)
    report = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else None
    return rc, report


def _report_row(report, fixture_id):
    for row in (report or {}).get("rows") or []:
        if row.get("capability_id") == fixture_id:
            return row
    return None


def test_state_changing_fixture_is_operator_only_by_default(
        runner, local_sink, tmp_path, monkeypatch):
    """REQUIRES e-stop guard: no --allow-state-changing => no state change, no socket."""
    fixture_id = "recovery.estop.8u.default"
    rc, report = _run_guard_cli(
        runner, _estop_fixture(fixture_id), tmp_path / "default",
        local_sink, monkeypatch, allow=False)

    assert local_sink["state"]["requests"] == [], (
        "a state-changing fixture opened a socket without --allow-state-changing: "
        f"{local_sink['state']['requests']}")

    row = _report_row(report, fixture_id)
    assert row is not None, (
        "runner produced no report row for the state-changing fixture "
        f"(exit={rc}); the guard/report path is missing")
    assert row.get("disposition") == "OPERATOR_ONLY", (
        "a state-changing fixture must be OPERATOR_ONLY by default, got "
        f"{row.get('disposition')!r}")
    assert row.get("operator_prerequisite"), (
        "OPERATOR_ONLY state-changing row must carry an operator_prerequisite")
    assert not row.get("qubit_executed"), (
        "a guarded state-changing fixture must not report qubit_executed")
    assert rc == 0, f"OPERATOR_ONLY is allowed and must exit 0, got {rc}"


def test_state_changing_requires_explicit_flag(
        runner, local_sink, tmp_path, monkeypatch):
    """REQUIRES e-stop guard: --allow-state-changing attempts the step against the sink."""
    fixture_id = "recovery.estop.8u.allow"
    before = len(local_sink["state"]["requests"])
    rc, report = _run_guard_cli(
        runner, _estop_fixture(fixture_id), tmp_path / "allow",
        local_sink, monkeypatch, allow=True)

    assert rc != 2, (
        "runner rejected --allow-state-changing as a usage error; the flag is not "
        "implemented (fail closed rather than silently ignoring it)")
    after = len(local_sink["state"]["requests"])
    assert after > before, (
        "with --allow-state-changing the state-changing step must be attempted "
        "against the loopback sink; it was not")

    row = _report_row(report, fixture_id)
    assert row is not None, "runner produced no report row with the opt-in flag"
    assert not row.get("state_changing_skipped"), (
        "with --allow-state-changing the row must not be marked "
        f"state_changing_skipped: {row.get('state_changing_skipped')!r}")


def test_synthesized_row_honors_declared_disposition(runner, tmp_path):
    """REQUIRES: synthesize_row_from_fixture keeps OPERATOR_ONLY + prerequisite."""
    fixture_id = "recovery.estop.8u.synth"
    doc = _estop_fixture(fixture_id)
    path = tmp_path / "synth.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    row = runner.synthesize_row_from_fixture(fixture_id, doc, path)
    assert row.get("disposition") == "OPERATOR_ONLY", (
        "synthesized row hardcoded/ignored the fixture's declared disposition; "
        f"got {row.get('disposition')!r}")
    assert row.get("operator_prerequisite") == doc["operator_prerequisite"], (
        "synthesized row dropped the fixture's operator_prerequisite: "
        f"{row.get('operator_prerequisite')!r}")


def test_byte_parity_gate_present_and_fails_on_nonidentical(runner):
    """REQUIRES: byte_parity is a fail-closed gate and rejects a non-IDENTICAL entry.

    Contract under test: an actionable byte-parity manifest entry carries a
    ``classification``; the gate fails when any entry is neither ``IDENTICAL``
    nor ``NOT_APPLICABLE`` (the documented exemption). A non-IDENTICAL entry
    (e.g. ``PATCHED_WITH_REASON``) must be reported as an offender.
    """
    declared = tuple(getattr(runner, "GATE_NAMES", ()))
    assert "byte_parity" in declared, (
        f"GATE_NAMES omits the byte_parity gate: {declared}")

    # The gate itself must be emitted by apply_fail_closed_gates (fail closed on
    # whatever manifest state the environment presents), so verify_ always sees
    # a byte_parity result rather than silently omitting it.
    baseline = _gate_ok_map(runner.apply_fail_closed_gates([], []))
    assert "byte_parity" in baseline, (
        "apply_fail_closed_gates did not emit the byte_parity gate; emitted: "
        f"{sorted(baseline)}")

    evaluate = getattr(runner, "evaluate_byte_manifest", None)
    assert callable(evaluate), (
        "runner exposes no evaluate_byte_manifest() to classify manifest entries")

    offenders = evaluate({
        "artifact": "HERMES_BYTE_MANIFEST",
        "manifest": [
            {"path": "qubit_runtime/voicebridge_cli.py",
             "classification": "PATCHED_WITH_REASON", "reason": None},
            {"path": "qubit_runtime/unchanged.py",
             "classification": "IDENTICAL"},
            {"path": ".bytecode-fingerprint",
             "classification": "NOT_APPLICABLE",
             "reason": "generated artifact"},
        ],
    })
    assert offenders == ["qubit_runtime/voicebridge_cli.py"], (
        "a non-IDENTICAL actionable entry must be reported as an offender "
        f"(NOT_APPLICABLE exempt); got {offenders!r}")


def test_no_estop_fixture_autoexecutes(
        runner, local_sink, tmp_path, monkeypatch):
    """Scan real fixtures: no state-changing marker may execute without the flag."""
    found = _iter_state_changing_fixtures()
    assert found, (
        "scan found no fixture with an executable state-changing marker; the "
        "e-stop fixtures are missing or the scanner is blind")

    for fixture_path, doc in found:
        workdir = tmp_path / str(doc["fixture_id"]).replace(".", "_")
        before = len(local_sink["state"]["requests"])
        rc, report = _run_guard_cli(
            runner, doc, workdir, local_sink, monkeypatch, allow=False)
        after = len(local_sink["state"]["requests"])
        assert after == before, (
            f"{fixture_path} executed a state-changing step without "
            "--allow-state-changing: "
            f"{local_sink['state']['requests'][before:after]}")
        row = _report_row(report, doc["fixture_id"])
        assert row is not None and row.get("disposition") == "OPERATOR_ONLY", (
            f"{fixture_path} must be OPERATOR_ONLY without the flag, got "
            f"{None if row is None else row.get('disposition')!r} (exit={rc})")
        assert not row.get("qubit_executed"), (
            f"{fixture_path} reports qubit_executed without the flag")


# =========================================================================== #
# v1.8 HARDENED RUNNER BEHAVIOUR
#
#   * non-GET HTTP methods are state-changing and never execute by default;
#   * mutating CLI / reference argv is blocked (not classified read-only);
#   * an absolute-URL request path to another host is refused (SSRF), and a
#     literal base_url to a non-allowlisted host is refused;
#   * a secret-shaped Qubit payload is redacted in the report while the
#     secret_in_artifact gate still fires;
#   * two runs yield identical upstream_result_hash for reference rows (a
#     dynamic reference must be OPERATOR_ONLY, never an unstable PASS).
#
# These tests are written against the hardened contract, NOT the current
# implementation: an absent guard is a test FAILURE, never a skip. Hermetic:
# only stdlib http.server instances bound to 127.0.0.0/8 are used.
# =========================================================================== #
# Non-GET HTTP methods mutate canonical state regardless of path/body.
STATE_CHANGING_HTTP_METHODS = ("POST", "PUT", "PATCH", "DELETE")

# CLI / reference argv tokens that name a mutating operation.
STATE_CHANGING_ARGV_TOKENS = (
    "pause", "power", "reset", "engage", "confirm", "create", "delete", "send",
)


def _ok_responder(method, path, raw):
    return 200, {"Content-Type": "application/json"}, json.dumps(
        {"ok": True, "state": "OK"}, sort_keys=True).encode("utf-8")


@contextlib.contextmanager
def _loopback_http(responder, bind_host="127.0.0.1"):
    """A loopback HTTP sink driven by ``responder(method, path, raw_bytes)``.

    Yields ``{"base_url", "state", "host", "port"}``; every request is recorded
    in ``state["requests"]``. No external interface is ever bound.
    """
    state = {"requests": []}

    class _Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "differential-8u-8u"

        def log_message(self, *args):
            return

        def _handle(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            state["requests"].append({
                "method": self.command,
                "path": urllib.parse.urlsplit(self.path).path,
                "raw_path": self.path,
                "headers": dict(self.headers.items()),
                "body": raw.decode("utf-8", "replace"),
            })
            status, headers, body = responder(self.command, self.path, raw)
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        do_GET = _handle
        do_POST = _handle
        do_PUT = _handle
        do_PATCH = _handle
        do_DELETE = _handle

    class _QuietServer(http.server.ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            exc = sys.exc_info()[1]
            if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
                return
            super().handle_error(request, client_address)

    server = _QuietServer((bind_host, 0), _Handler)
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    _host, port = server.server_address
    try:
        yield {
            "base_url": f"http://{bind_host}:{port}",
            "state": state,
            "host": bind_host,
            "port": port,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_non_get_method_blocked_by_default(runner, tmp_path, monkeypatch):
    """REQUIRES hardened guard: POST/PUT/PATCH/DELETE never execute by default.

    A non-GET request mutates state even when its path/body carry no denylisted
    marker, so it must be OPERATOR_ONLY with no socket opened until the operator
    passes ``--allow-state-changing``.
    """
    with _loopback_http(_ok_responder) as sink:
        for method in STATE_CHANGING_HTTP_METHODS:
            fixture_id = f"api.method.{method.lower()}.8u"
            doc = {
                "schema_version": 1,
                "fixture_id": fixture_id,
                "family": "api",
                "command": None,
                "request": {
                    "kind": "http", "method": method,
                    "path": "/agent/api/qubit/echo",
                    "headers": {}, "body": {"payload": 1}, "argv": [],
                },
                "transport": "http",
                "normalizers": [],
                "expected": {"http_status": 200},
                "disposition": "OPERATOR_ONLY",
                "operator_prerequisite": f"{method} is state-changing",
                "qubit": {"path": "voicebridge/webui.py:1",
                          "caller": "create_app", "approval": None},
                "hermes": {"path": "hermes/x.py:1", "symbol": "s"},
                "openclaw": {"path": "openclaw/x.ts:1", "symbol": "s"},
                "notes": "non-GET method guard probe (tests only)",
            }
            rc, report = _run_guard_cli(
                runner, doc, tmp_path / method.lower(), sink,
                monkeypatch, allow=False)

            assert sink["state"]["requests"] == [], (
                f"{method} fixture opened a socket without "
                "--allow-state-changing; sink saw "
                f"{sink['state']['requests']!r}")
            row = _report_row(report, fixture_id)
            assert row is not None, (
                f"runner produced no report row for {method} (exit={rc})")
            assert row.get("disposition") == "OPERATOR_ONLY", (
                f"{method} must be OPERATOR_ONLY by default, got "
                f"{row.get('disposition')!r}")
            assert (row.get("state_changing_skipped") is True
                    or row.get("state_changing") is True), (
                f"{method} was not classified state-changing: {row!r}")


def test_cli_state_changing_argv_blocked(runner, tmp_path):
    """REQUIRES hardened guard: mutating CLI argv is blocked, not read-only."""
    for token in STATE_CHANGING_ARGV_TOKENS:
        fixture = {
            "schema_version": 1,
            "fixture_id": f"cli.argv.{token}.8u",
            "family": "cli",
            "command": {"argv": ["voicebridge", token],
                        "stdin": None, "env": {}},
            "request": None,
            "normalizers": [],
            "expected": {"exit_code": 0},
            "qubit": {"path": "voicebridge/cli.py:1", "caller": "main",
                      "approval": None},
            "hermes": {"path": "qubit_cli/_parser.py:1",
                       "symbol": "build_top_level_parser"},
            "openclaw": {"path": "openclaw/x.ts:1", "symbol": "s"},
            "notes": "cli argv state-changing probe (tests only)",
        }
        reasons = runner.state_changing_reasons(fixture)
        assert any(token in reason for reason in reasons), (
            f"CLI argv token {token!r} was not classified state-changing "
            f"(reread as read-only); reasons={reasons!r}")

    # End-to-end: the guard must leave the subprocess unspawned.
    class _CountingAdapter:
        project = "qubit"
        binary = "/nonexistent/voicebridge"

        def __init__(self):
            self.calls = 0

        def available(self):
            return True

        def execute(self, request):
            self.calls += 1
            return runner.CallResult(exit_code=0, stdout="ok")

    fixture_id = "cli.argv.pause.8u.e2e"
    doc = {
        "schema_version": 1,
        "fixture_id": fixture_id,
        "family": "cli",
        "command": {"argv": ["voicebridge", "pause"], "stdin": None, "env": {}},
        "request": None,
        "normalizers": [],
        "expected": {"exit_code": 0},
        "qubit": {"path": "voicebridge/cli.py:1", "caller": "main",
                  "approval": None},
        "hermes": {"path": "qubit_cli/_parser.py:1", "symbol": "parser"},
        "openclaw": {"path": "openclaw/x.ts:1", "symbol": "s"},
        "notes": "cli argv e2e guard probe (tests only)",
    }
    path = tmp_path / "cli_argv_pause.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    row = _row(capability_id=fixture_id, disposition="OPEN", fixture=str(path))
    adapter = _CountingAdapter()
    result = runner.run_inventory(
        [row], tmp_path, adapters={"qubit": adapter})[0]

    assert adapter.calls == 0, (
        "a mutating CLI argv was executed by default (subprocess spawned)")
    assert result["disposition"] == "OPERATOR_ONLY", (
        f"mutating CLI argv produced {result['disposition']!r}, not "
        "OPERATOR_ONLY")
    assert result.get("state_changing_skipped") is True, (
        f"mutating CLI argv was not marked state_changing_skipped: {result!r}")


def test_reference_argv_is_classified(runner, tmp_path, monkeypatch):
    """REQUIRES hardened guard: a mutating reference argv is blocked, not PASS."""
    monkeypatch.setenv("QUBIT_DIFFERENTIAL_HERMES_COMMIT", PIN_QUBIT)
    doc = _cli_fixture(
        runner, "cli.reference.mutating.8u",
        references={"hermes": {"argv": ["hermes", "delete", "--all"],
                               "env": {}}},
    )
    path = _write_fixture(tmp_path, doc)
    row = _row(capability_id="cli.reference.mutating.8u", disposition="OPEN",
               fixture=str(path))

    class _CountingReference:
        def __init__(self):
            self.calls = 0

        def available(self):
            return True

        def execute(self, request):
            self.calls += 1
            return runner.CallResult(exit_code=0, stdout="ok")

    reference = _CountingReference()
    reference.name = "hermes"
    reference.project = "hermes"
    reference.binary = "/nonexistent/hermes"
    adapters = {
        "qubit": _fake_adapter(runner, "qubit", exit_code=0, stdout="ok"),
        "hermes": reference,
    }
    result = runner.run_inventory([row], tmp_path, adapters=adapters)[0]

    assert reference.calls == 0, (
        "a mutating reference argv was executed by default")
    assert result["reference_executed"] is False, (
        "a mutating reference argv still executed the reference")
    assert result["disposition"] != "PASS", (
        "a mutating reference argv was classified PASS (read-only)")
    assert result["disposition"] == "OPERATOR_ONLY", (
        f"a mutating reference argv must be OPERATOR_ONLY, got "
        f"{result['disposition']!r}")
    assert result.get("state_changing_skipped") is True, (
        f"reference state-change not recorded: {result!r}")


def test_ssrf_absolute_url_rejected(runner, tmp_path, monkeypatch):
    """REQUIRES hardened SSRF guard: an absolute-URL path to another host fails.

    The path is an absolute URL whose host differs from the configured base URL;
    it must be refused before any socket opens, the error must name the host
    mismatch, and the auth token must never leave for the foreign host.
    """
    # Must match the token _run_guard_cli exports, so the leak check is real.
    token = "tok-differential-8u-estop"
    with _loopback_http(_ok_responder) as allowed, \
            _loopback_http(_ok_responder, bind_host="127.0.0.2") as foreign:
        fixture_id = "api.ssrf.absolute.8u"

        def _fixture(path_value):
            return {
                "schema_version": 1,
                "fixture_id": fixture_id,
                "family": "api",
                "command": None,
                "request": {
                    "kind": "http", "method": "GET", "path": path_value,
                    "headers": {}, "body": None, "argv": [],
                },
                "transport": "http",
                "normalizers": [],
                "expected": {"http_status": 200},
                "disposition": "OPERATOR_ONLY",
                "operator_prerequisite": "ssrf probe (tests only)",
                "auth": {"header": "X-Api-Token",
                         "value_ref": "env:QUBIT_DIFFERENTIAL_QUBIT_TOKEN"},
                "qubit": {"path": "voicebridge/webui.py:1",
                          "caller": "create_app", "approval": None},
                "hermes": {"path": "hermes/x.py:1", "symbol": "s"},
                "openclaw": {"path": "openclaw/x.ts:1", "symbol": "s"},
                "notes": "ssrf absolute-url probe (tests only)",
            }

        # Positive control: a relative path really executes against the host.
        _rc, _report = _run_guard_cli(
            runner, _fixture("/agent/api/qubit/echo"), tmp_path / "ok",
            allowed, monkeypatch, allow=False)
        assert allowed["state"]["requests"], (
            "positive control never reached the allowlisted sink; the SSRF "
            "test would be vacuous")

        before = len(foreign["state"]["requests"])
        foreign_path = (
            f"http://127.0.0.2:{foreign['port']}/agent/api/qubit/echo")
        rc, report = _run_guard_cli(
            runner, _fixture(foreign_path), tmp_path / "ssrf",
            allowed, monkeypatch, allow=False)

        assert len(foreign["state"]["requests"]) == before, (
            "an absolute-URL request path reached a different host; the SSRF "
            f"guard is absent: {foreign['state']['requests'][before:]!r}")
        row = _report_row(report, fixture_id)
        assert row is not None, f"ssrf fixture produced no report row (exit={rc})"
        assert row.get("disposition") != "PASS", (
            "an SSRF request was classified as PASS")
        blob = json.dumps(row, sort_keys=True, default=str).lower()
        assert "127.0.0.2" in blob, (
            "SSRF rejection does not name the offending host: "
            f"{row.get('operator_prerequisite')!r} "
            f"diagnostics={row.get('diagnostics')!r}")
        assert "host" in blob or "mismatch" in blob, (
            "SSRF rejection is not a host-mismatch diagnostic: "
            f"{row.get('operator_prerequisite')!r}")
        assert token not in json.dumps(report, default=str), (
            "auth token leaked into the report of a rejected SSRF request")


def test_literal_base_url_host_validated(runner, tmp_path, monkeypatch):
    """REQUIRES hardening: a literal base_url to a non-allowlisted host fails."""
    with _loopback_http(_ok_responder) as allowed, \
            _loopback_http(_ok_responder, bind_host="127.0.0.2") as foreign:
        fixture_id = "api.base_url.literal.8u"
        doc = {
            "schema_version": 1,
            "fixture_id": fixture_id,
            "family": "api",
            "command": None,
            "request": {
                "kind": "http", "method": "GET",
                "path": "/agent/api/qubit/echo",
                "headers": {}, "body": None, "argv": [],
            },
            "transport": "http",
            "base_url": foreign["base_url"],
            "normalizers": [],
            "expected": {"http_status": 200},
            "disposition": "OPERATOR_ONLY",
            "operator_prerequisite": "literal base_url probe (tests only)",
            "qubit": {"path": "voicebridge/webui.py:1",
                      "caller": "create_app", "approval": None},
            "hermes": {"path": "hermes/x.py:1", "symbol": "s"},
            "openclaw": {"path": "openclaw/x.ts:1", "symbol": "s"},
            "notes": "literal base_url host-validation probe (tests only)",
        }
        before = len(foreign["state"]["requests"])
        rc, report = _run_guard_cli(
            runner, doc, tmp_path / "literal", allowed,
            monkeypatch, allow=False)

        assert len(foreign["state"]["requests"]) == before, (
            "a literal base_url to a non-allowlisted host was executed: "
            f"{foreign['state']['requests'][before:]!r}")
        row = _report_row(report, fixture_id)
        assert row is not None, (
            f"literal base_url fixture produced no report row (exit={rc})")
        assert row.get("disposition") != "PASS", (
            "a non-allowlisted literal base_url was classified PASS")
        assert row.get("operator_prerequisite"), (
            "a rejected literal base_url must carry a rejection prerequisite")
        assert row.get("qubit_executed") is not True, (
            "a rejected literal base_url must not report qubit_executed")


def test_qubit_payload_secret_is_redacted(runner, tmp_path, monkeypatch):
    """REQUIRES hardening: a secret-shaped Qubit payload is redacted in the
    report while the secret_in_artifact gate still fires."""
    secret = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2"

    def _secret_responder(method, path, raw):
        body = json.dumps(
            {"state": "OK", "token": secret}, sort_keys=True).encode("utf-8")
        return 200, {"Content-Type": "application/json"}, body

    fixture_id = "api.secret.redaction.8u"
    with _loopback_http(_secret_responder) as sink:
        doc = {
            "schema_version": 1,
            "fixture_id": fixture_id,
            "family": "api",
            "command": None,
            "request": {
                "kind": "http", "method": "GET",
                "path": "/agent/api/qubit/echo",
                "headers": {}, "body": None, "argv": [],
            },
            "transport": "http",
            "normalizers": [],
            "expected": {"http_status": 200},
            "disposition": "OPERATOR_ONLY",
            "operator_prerequisite": "secret redaction probe (tests only)",
            "qubit": {"path": "voicebridge/webui.py:1",
                      "caller": "create_app", "approval": None},
            "hermes": {"path": "hermes/x.py:1", "symbol": "s"},
            "openclaw": {"path": "openclaw/x.ts:1", "symbol": "s"},
            "notes": "secret-shaped payload redaction probe (tests only)",
        }
        rc, report = _run_guard_cli(
            runner, doc, tmp_path / "redact", sink,
            monkeypatch, allow=False)
        assert report is not None, f"runner produced no report (exit={rc})"
        row = _report_row(report, fixture_id)
        assert row is not None, f"no report row for {fixture_id} (exit={rc})"
        assert row.get("qubit_executed") is True, (
            "the secret sink was never reached; the redaction test is vacuous")

        blob = json.dumps(report, sort_keys=True, default=str)
        assert secret not in blob, (
            "a secret-shaped value returned by the Qubit sink was written "
            "unredacted into the report")
        assert "<redacted>" in blob, (
            "the report carries no redaction marker for the secret-shaped "
            "value")

        gates = {g.get("gate"): g for g in report.get("gates") or []}
        assert "secret_in_artifact" in gates, (
            "report omits the secret_in_artifact gate")
        assert gates["secret_in_artifact"]["ok"] is False, (
            "secret_in_artifact must still fire for a secret-shaped payload")
        assert fixture_id in gates["secret_in_artifact"].get("rows", []), (
            "secret_in_artifact gate did not name the offending row: "
            f"{gates['secret_in_artifact'].get('rows')!r}")


def test_reference_determinism(runner, tmp_path, monkeypatch):
    """REQUIRES hardening: two runs yield identical upstream_result_hash.

    Rows that remain references must hash identically across runs; a dynamic
    (non-reproducible) reference must be OPERATOR_ONLY, never an unstable PASS.
    """
    monkeypatch.setenv("QUBIT_DIFFERENTIAL_HERMES_COMMIT", PIN_QUBIT)
    doc = _cli_fixture(
        runner, "cli.reference.determinism.8u",
        references={"hermes": {"argv": ["hermes", "--help"], "env": {}}},
    )
    path = _write_fixture(tmp_path, doc)
    row = _row(capability_id="cli.reference.determinism.8u",
               disposition="OPEN", fixture=str(path))

    def _run_deterministic():
        adapters = {
            "qubit": _fake_adapter(runner, "qubit", exit_code=0, stdout="usage"),
            "hermes": _scripted_reference_adapter(
                runner, "hermes", "/nonexistent/hermes",
                exit_code=0, stdout="usage: hermes\n"),
        }
        return runner.run_inventory([row], tmp_path, adapters=adapters)[0]

    first = _run_deterministic()
    second = _run_deterministic()
    assert first["reference_executed"] is True, (
        "declared reference did not execute; determinism is untested")
    assert first.get("upstream_result_hash"), (
        "no upstream_result_hash recorded for an executed reference")
    assert first["upstream_result_hash"] == second["upstream_result_hash"], (
        "two identical runs produced different upstream_result_hash: "
        f"{first['upstream_result_hash']} != {second['upstream_result_hash']}")

    # A genuinely dynamic reference cannot yield a stable hash. The documented
    # contract is that the runner either honors an explicit
    # ``nondeterministic: true`` (this probe) or detects the instability by
    # re-execution; either way the row must never be PASS. The output is proven
    # unstable across the two runs below so the enforcement is not vacuous.
    dynamic_doc = _cli_fixture(
        runner, "cli.reference.determinism.8u.dynamic",
        references={"hermes": {"argv": ["hermes", "--help"], "env": {},
                               "nondeterministic": True}},
    )
    dynamic_path = tmp_path / "fixture_dynamic.json"
    dynamic_path.write_text(json.dumps(dynamic_doc), encoding="utf-8")
    dynamic_row = _row(capability_id="cli.reference.determinism.8u.dynamic",
                       disposition="OPEN", fixture=str(dynamic_path))

    class _TickingReference:
        name = "hermes"
        project = "hermes"
        binary = "/nonexistent/hermes"
        is_mock = False
        ticks = {"n": 0}

        def available(self):
            return True

        def execute(self, request):
            _TickingReference.ticks["n"] += 1
            return runner.CallResult(
                exit_code=0,
                stdout=json.dumps({"tick": _TickingReference.ticks["n"]}))

    def _run_dynamic():
        adapters = {
            "qubit": _fake_adapter(runner, "qubit", exit_code=0, stdout="usage"),
            "hermes": _TickingReference(),
        }
        return runner.run_inventory(
            [dynamic_row], tmp_path, adapters=adapters)[0]

    dynamic_first = _run_dynamic()
    dynamic_second = _run_dynamic()
    first_payload = dynamic_first["reference_payloads"]["hermes"]["stdout"]
    second_payload = dynamic_second["reference_payloads"]["hermes"]["stdout"]
    assert first_payload != second_payload, (
        "the dynamic reference probe produced identical output; reference "
        "determinism enforcement was not actually exercised")
    for result in (dynamic_first, dynamic_second):
        assert result["disposition"] != "PASS", (
            "a reference with unstable output produced a PASS: "
            f"{result['disposition']!r}")
        assert result["disposition"] == "OPERATOR_ONLY", (
            "an unstable/nondeterministic reference must be OPERATOR_ONLY, got "
            f"{result['disposition']!r}")
        assert "hermes" in (result.get("nondeterministic_references") or []), (
            "the unstable reference was not recorded as nondeterministic: "
            f"{result.get('nondeterministic_references')!r}")


# ===========================================================================
# Regression tests for the concurrently-landing runner fixes.
#
# Evidence for the intended behavior (read-only, outside the repo):
#   /tmp/qubit-differential/inventory/dispositions/OPENCLAW_GATE_ACCOUNTING.md
#   /tmp/qubit-differential/evidence/security/SECURITY_AUDIT_4.md
# Each test asserts the CONTRACT, not the current implementation; a failure
# here is a runner mismatch to report, never a reason to weaken the assertion.
# ===========================================================================
def _shadow_row(cid, disposition, **over):
    """A minimal inventory row (same capability_id across generations)."""
    row = {
        "capability_id": cid,
        "family": "gateway",
        "upstream_project": "openclaw",
        "upstream_commit": "43286efa75666f29030bdd9c93873410698dc79c",
        "upstream_path": "src/gateway/x.ts:1",
        "upstream_symbol": "x",
        "upstream_hash": None,
        "qubit_path": None,
        "qubit_symbol": None,
        "disposition": disposition,
        "command": None,
        "fixture": None,
        "fixture_hash": None,
        "upstream_result_hash": None,
        "qubit_result_hash": None,
        "test_ids": [],
        "trace_refs": [],
        "operator_prerequisite": None,
        "rollback_ref": "n/a",
        "normalizations": [],
    }
    row.update(over)
    return row


def test_scratch_inventory_does_not_shadow(runner, tmp_path):
    """REQUIRES F1/F2: a `_pre_retriage/` scratch generation must not shadow
    the canonical merged row carrying the same capability_id.

    The retriage-merged artifact holds the real disposition; the scratch clone
    still holds an all-OPEN row. First-wins `rglob` over a scan root that mixes
    generations picks the stale OPEN row and the proof is lost.
    """
    cid = "gateway.method.models.authRefresh.shadow8u"
    scratch_dir = tmp_path / "_pre_retriage"
    scratch_dir.mkdir()
    scratch_path = scratch_dir / "openclaw_delta_latest.json"
    canonical_path = tmp_path / "openclaw_delta_latest.json"

    scratch = {"schema_version": 1,
               "rows": [_shadow_row(cid, "OPEN",
                                    delta_role="new_in_latest_main")]}
    canonical = {"schema_version": 1,
                 "rows": [_shadow_row(
                     cid, "SAFE_EQUIVALENT",
                     delta_role="new_in_latest_main",
                     qubit_path="voicebridge/qubit_compat.py:4105",
                     qubit_symbol="h_auth_native_refresh",
                     disposition_proof="voicebridge/qubit_compat.py:4105")]}
    scratch_path.write_text(json.dumps(scratch), encoding="utf-8")
    canonical_path.write_text(json.dumps(canonical), encoding="utf-8")

    # `_` sorts before letters: scratch is walked first, so first-wins would
    # select it unless the loader excludes scratch generations.
    walked = sorted(
        str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*.json"))
    assert walked and walked[0].startswith("_pre_retriage"), (
        f"test setup: scratch does not sort first; shadowing untested: {walked}")

    rows = runner.load_inventory(tmp_path)
    matches = [r for r in rows if r.get("capability_id") == cid]
    assert len(matches) == 1, (
        f"load_inventory returned {len(matches)} rows for {cid!r}; the scratch "
        "generation and the canonical merged row must collapse to the canonical")
    chosen = matches[0]
    assert chosen.get("disposition") == "SAFE_EQUIVALENT", (
        "an OPEN row from the `_pre_retriage/` scratch generation shadowed the "
        f"canonical non-OPEN row: disposition={chosen.get('disposition')!r}")
    assert chosen.get("qubit_symbol") == "h_auth_native_refresh", (
        "the canonical merged row's proof was not retained: "
        f"qubit_symbol={chosen.get('qubit_symbol')!r}")


def test_declared_disposition_preserved(runner, tmp_path):
    """REQUIRES F2: a declared SAFE_EQUIVALENT row with proof keeps its
    disposition; without proof it stays OPEN and the gate fires.

    `_run_row` currently rewrites any declared terminal disposition to OPEN when
    no fixture is bound, discarding the retriage's disposition. The proof is the
    row-level disposition mapping (`disposition_proof` / `qubit_path` /
    `qubit_symbol`); execution honesty stays with the unexecuted_row gate.
    """
    cid = "gateway.method.models.authRefresh.preserved8u"
    proof = {
        "disposition_proof": "voicebridge/qubit_compat.py:4105",
        "qubit_path": "voicebridge/qubit_compat.py:4105",
        "qubit_symbol": "h_auth_native_refresh",
    }
    row = _row(capability_id=cid, disposition="SAFE_EQUIVALENT",
               fixture=None, **proof)
    result = runner.run_inventory([row], tmp_path, adapters={})[0]
    assert result["declared_disposition"] == "SAFE_EQUIVALENT", (
        "runner dropped the declared disposition before resolution: "
        f"{result['declared_disposition']!r}")
    assert result["disposition"] == "SAFE_EQUIVALENT", (
        "a declared SAFE_EQUIVALENT row carrying proof was rewritten to "
        f"{result['disposition']!r} (must be preserved, not OPEN)")

    good = _gate_ok_map(runner.apply_fail_closed_gates([row], [result]))
    assert "safe_equivalent_requires_proof" in good, (
        "runner does not emit the safe_equivalent_requires_proof gate; "
        f"emitted: {sorted(good)}")
    assert good["safe_equivalent_requires_proof"] is True, (
        "safe_equivalent_requires_proof fired for a proven SAFE_EQUIVALENT row")

    # No proof: the declared claim cannot be honored; it stays OPEN and the
    # dedicated gate names the omission.
    unproven_row = _row(
        capability_id=cid + ".unproven", disposition="SAFE_EQUIVALENT",
        fixture=None, disposition_proof=None, qubit_path=None,
        qubit_symbol=None, trace_refs=[], test_ids=[])
    unproven = runner.run_inventory([unproven_row], tmp_path, adapters={})[0]
    assert unproven["declared_disposition"] == "SAFE_EQUIVALENT", (
        "declared disposition must be recorded even when it cannot be honored")
    assert unproven["disposition"] == "OPEN", (
        "a declared SAFE_EQUIVALENT row without proof must not be honored: got "
        f"{unproven['disposition']!r}")

    bad = _gate_ok_map(
        runner.apply_fail_closed_gates([unproven_row], [unproven]))
    assert "safe_equivalent_requires_proof" in bad, (
        "runner does not emit the safe_equivalent_requires_proof gate; "
        f"emitted: {sorted(bad)}")
    assert bad["safe_equivalent_requires_proof"] is False, (
        "safe_equivalent_requires_proof must fire when proof is absent: "
        f"{bad['safe_equivalent_requires_proof']!r}")


def test_safe_equivalent_proof_gate_recognizes_recon_keys(runner, tmp_path):
    """The recon/retriage proof keys must satisfy the SAFE_EQUIVALENT gate.

    The retriage-merged inventory proves a SAFE_EQUIVALENT with
    ``disposition_proof``, ``qubit_path``/``qubit_symbol`` and a ``verification``
    block. A row carrying those recon keys plus a real test id must pass the
    ``safe_equivalent_requires_proof`` gate; a row without any proof must trip
    it. This is the gate-accounting half of the false-PASS regression above.
    """
    cid = "gateway.method.models.authRefresh.recon8u"
    test_id = (
        "tests/test_differential_parity8u.py"
        "::test_safe_equivalent_proof_gate_recognizes_recon_keys")
    proof = {
        "disposition_proof": "voicebridge/qubit_compat.py:4105",
        "qubit_path": "voicebridge/qubit_compat.py:4105",
        "qubit_symbol": "h_auth_native_refresh",
        "retriage_status": "DISPOSED_REFRESHED",
        "retriage_reason": "Already dispositioned; file:line/symbol re-verified.",
        "evidence_confidence": "name_level_production_path",
        "verification": {
            "path": "voicebridge/qubit_compat.py:4105",
            "file": "voicebridge/qubit_compat.py",
            "line": "4105",
            "exists": True,
            "symbol_present": True,
            "line_in_range": True,
            "symbol": "h_auth_native_refresh",
        },
    }
    row = _row(capability_id=cid, family="provider",
               disposition="SAFE_EQUIVALENT", fixture=None,
               trace_refs=[], test_ids=[test_id], **proof)
    result = runner.run_inventory([row], tmp_path, adapters={})[0]
    assert result["declared_disposition"] == "SAFE_EQUIVALENT", (
        f"declared disposition dropped: {result.get('declared_disposition')!r}")
    assert result["disposition"] == "SAFE_EQUIVALENT", (
        "a recon-proven SAFE_EQUIVALENT row was not preserved: "
        f"{result['disposition']!r}")

    good = _gate_ok_map(runner.apply_fail_closed_gates([row], [result]))
    assert "safe_equivalent_requires_proof" in good, (
        "runner does not emit the safe_equivalent_requires_proof gate; "
        f"emitted: {sorted(good)}")
    assert good["safe_equivalent_requires_proof"] is True, (
        "the recon proof keys (disposition_proof/qubit_path/qubit_symbol/"
        "verification + a real test id) were not recognized; gate fired")

    unproven_row = _row(
        capability_id=cid + ".unproven", disposition="SAFE_EQUIVALENT",
        fixture=None, disposition_proof=None, qubit_path=None,
        qubit_symbol=None, test_ids=[], trace_refs=[])
    unproven = runner.run_inventory([unproven_row], tmp_path, adapters={})[0]
    assert unproven["declared_disposition"] == "SAFE_EQUIVALENT", (
        "declared disposition must be recorded even when it cannot be honored")
    assert unproven["disposition"] != "SAFE_EQUIVALENT", (
        "a SAFE_EQUIVALENT row without proof was honored: "
        f"{unproven['disposition']!r}")

    bad = _gate_ok_map(
        runner.apply_fail_closed_gates([unproven_row], [unproven]))
    assert "safe_equivalent_requires_proof" in bad, (
        "runner does not emit the safe_equivalent_requires_proof gate; "
        f"emitted: {sorted(bad)}")
    assert bad["safe_equivalent_requires_proof"] is False, (
        "safe_equivalent_requires_proof must fire when proof is absent: "
        f"{bad['safe_equivalent_requires_proof']!r}")


def test_no_unexecuted_pass_survives(runner, tmp_path):
    """A declared PASS with no bound fixture must never survive as PASS.

    Regression for the ``api.webui.get.root`` false PASS: the inventory declares
    PASS but binds no fixture, so neither the Qubit side nor a reference can be
    executed and the result carries no hashes. A PASS is a parity claim; without
    an executed comparison it must be refused (downgraded) and the
    ``pass_without_proof`` fail-closed gate must name the claim.
    """
    cid = "api.webui.get.root"
    row = _row(
        capability_id=cid, family="api", disposition="PASS",
        upstream_project="qubit", upstream_commit="f" * 40,
        upstream_path="voicebridge/webui.py:1557", upstream_symbol="/",
        qubit_path="voicebridge/webui.py:1557", qubit_symbol="/",
        fixture=None, fixture_hash=None, upstream_hash=None,
        upstream_result_hash=None, qubit_result_hash=None,
        test_ids=[], trace_refs=[],
    )

    result = runner.run_inventory([row], tmp_path, adapters={})[0]
    assert result["declared_disposition"] == "PASS", (
        "the declared PASS claim must be recorded, not silently dropped: "
        f"declared_disposition={result.get('declared_disposition')!r}")
    assert result.get("executed") is False, (
        f"test setup: unexecuted PASS expected, got executed={result.get('executed')!r}")
    assert not result.get("qubit_result_hash"), (
        "test setup: a PASS with a qubit hash is not the unexecuted case: "
        f"{result.get('qubit_result_hash')!r}")
    assert not result.get("upstream_result_hash"), (
        "test setup: a PASS with an upstream hash is not the unexecuted case: "
        f"{result.get('upstream_result_hash')!r}")
    assert result["disposition"] != "PASS", (
        "the runner emitted PASS for a row it never executed (executed=false, no "
        "result hashes): this is the api.webui.get.root false PASS")

    gates = runner.apply_fail_closed_gates([row], [result])
    gate_map = _gate_ok_map(gates)
    assert "pass_without_proof" in gate_map, (
        "runner does not emit the pass_without_proof gate; "
        f"emitted: {sorted(gate_map)}")
    assert gate_map["pass_without_proof"] is False, (
        "an unexecuted PASS claim did not trip pass_without_proof")
    firing = next(g for g in gates if g.get("gate") == "pass_without_proof")
    assert cid in (firing.get("rows") or []), (
        f"pass_without_proof did not name the false PASS row: {firing!r}")
    diag_kinds = {d.get("kind") for d in (result.get("diagnostics") or [])
                  if isinstance(d, dict)}
    assert "pass_without_proof" in diag_kinds, (
        "the refused PASS carried no pass_without_proof diagnostic: "
        f"{result.get('diagnostics')!r}")
    assert runner.build_verdict([result], gates) == "INCOMPLETE", (
        "an unexecuted PASS claim did not force an INCOMPLETE verdict")


def _openclaw_delta_row(cid, **over):
    """A row tracking the OpenClaw latest-main delta (needs a disposition)."""
    row = _row(
        capability_id=cid, disposition="OPEN", upstream_project="openclaw",
        upstream_commit="43286efa75666f29030bdd9c93873410698dc79c",
        qubit_path=None, qubit_symbol=None, trace_refs=[],
        delta_role="new_in_latest_main", delta_vs_pin_commit=PIN_OPENCLAW)
    row.update(over)
    return row


def test_scanner_exclusion_rows_not_counted(runner):
    """REQUIRES F3: SCANNER_EXCLUSION / test_support_only rows are not
    undisposed OpenClaw additions, while a genuine one still is.

    The retriage drops `*.test-support.ts` / `*.test-harness.ts` modules as
    non-capabilities but exports them only as exclusions; without the exclusion
    concept `is_openclaw_delta_row` still classifies them and the gate demands a
    disposition the merge deliberately never assigns.
    """
    excluded_markers = {
        "retriage_status": "SCANNER_EXCLUSION",
        "scanner_exclusion": "SCANNER_EXCLUSION",
        "test_support_only": True,
        "retriage_reason": "test_support_only",
        "reason": "test_support_only",
    }
    excluded = _openclaw_delta_row(
        "dashboard.control_source.chat_github_publication_test_support.8u",
        **excluded_markers)
    excluded2 = _openclaw_delta_row(
        "dashboard.control_source.composer_test_support.8u",
        **excluded_markers)
    genuine = _openclaw_delta_row("gateway.method.models.authRefresh.8u")

    rows = [excluded, excluded2, genuine]
    results = []
    for row in rows:
        result = _result(row)
        result["disposition"] = "OPEN"
        result["executed"] = False
        results.append(result)

    gate = {g.get("gate"): g for g in runner.apply_fail_closed_gates(
        rows, results)}["openclaw_undisposed_addition"]
    flagged = set(gate.get("rows") or [])
    assert genuine["capability_id"] in flagged, (
        "the gate stopped flagging a genuinely undisposed OpenClaw addition: "
        f"flagged={sorted(flagged)}")
    assert excluded["capability_id"] not in flagged, (
        "a SCANNER_EXCLUSION/test_support_only row was counted as undisposed: "
        f"{excluded['capability_id']}")
    assert excluded2["capability_id"] not in flagged, (
        "a SCANNER_EXCLUSION/test_support_only row was counted as undisposed: "
        f"{excluded2['capability_id']}")


def _readonly_mutating_fixture(fixture_id, method):
    """A fixture that declares `read_only: true` with a mutating HTTP verb."""
    return {
        "schema_version": 1,
        "fixture_id": fixture_id,
        "family": "api",
        "command": None,
        "request": {
            "kind": "http", "method": method,
            "path": "/api/sessions/abc123",
            "headers": {}, "body": None, "argv": [],
        },
        "transport": "http",
        "read_only": True,
        "normalizers": [],
        "expected": {"http_status": 200},
        "auth": {"header": "X-Api-Token",
                 "value_ref": "env:QUBIT_DIFFERENTIAL_QUBIT_TOKEN"},
        "disposition": "OPERATOR_ONLY",
        "operator_prerequisite": "read_only guard probe (tests only)",
        "qubit": {"path": "voicebridge/webui.py:1",
                  "caller": "create_app", "approval": None},
        "hermes": {"path": "hermes/x.py:1", "symbol": "s"},
        "openclaw": {"path": "openclaw/x.ts:1", "symbol": "s"},
        "notes": "read_only + mutating method guard probe (tests only)",
    }


def test_read_only_does_not_permit_mutating_method(
        runner, local_sink, tmp_path, monkeypatch):
    """REQUIRES N1 fix: `read_only:true` must not suppress a non-GET method.

    Only GET/HEAD are structurally read-only. DELETE/PUT must stay blocked
    (zero requests, no token) until --allow-state-changing is passed.
    """
    token = "tok-differential-8u-estop"
    for method in ("DELETE", "PUT"):
        fixture_id = f"api.readonly.{method.lower()}.8u"
        before = len(local_sink["state"]["requests"])
        rc, report = _run_guard_cli(
            runner, _readonly_mutating_fixture(fixture_id, method),
            tmp_path / method.lower(), local_sink, monkeypatch, allow=False)

        assert len(local_sink["state"]["requests"]) == before, (
            f"`read_only:true` + {method} executed and sent the token without "
            f"--allow-state-changing: {local_sink['state']['requests'][before:]!r}")
        row = _report_row(report, fixture_id)
        assert row is not None, f"no report row for {fixture_id} (exit={rc})"
        assert row.get("disposition") == "OPERATOR_ONLY", (
            f"`read_only:true` + {method} must be OPERATOR_ONLY, got "
            f"{row.get('disposition')!r}")
        assert row.get("state_changing_skipped") is True, (
            f"`read_only:true` + {method} was not marked state_changing_skipped: "
            f"{row!r}")
        assert token not in json.dumps(report, default=str), (
            f"token leaked into the report for a blocked {method}")


def test_literal_base_url_port_validated(runner, tmp_path, monkeypatch):
    """REQUIRES #3 fix: a literal base_url on the same host but a different
    port than the resolved/operator base is rejected before any request.

    Host-only loopback allowlisting lets a fixture pointed at any other local
    service (including the live gateway) receive the operator token.
    """
    token = "tok-differential-8u-estop"
    with _loopback_http(_ok_responder) as allowed, \
            _loopback_http(_ok_responder) as alt:
        assert allowed["port"] != alt["port"], (
            "test setup: the two sinks share a port; port validation untested")
        fixture_id = "api.base_url.port.8u"
        doc = {
            "schema_version": 1,
            "fixture_id": fixture_id,
            "family": "api",
            "command": None,
            "request": {
                "kind": "http", "method": "GET",
                "path": "/agent/api/qubit/echo",
                "headers": {}, "body": None, "argv": [],
            },
            "transport": "http",
            "base_url": alt["base_url"],
            "normalizers": [],
            "expected": {"http_status": 200},
            "auth": {"header": "X-Api-Token",
                     "value_ref": "env:QUBIT_DIFFERENTIAL_QUBIT_TOKEN"},
            "disposition": "OPERATOR_ONLY",
            "operator_prerequisite": "literal base_url port probe (tests only)",
            "qubit": {"path": "voicebridge/webui.py:1",
                      "caller": "create_app", "approval": None},
            "hermes": {"path": "hermes/x.py:1", "symbol": "s"},
            "openclaw": {"path": "openclaw/x.ts:1", "symbol": "s"},
            "notes": "literal base_url same-host different-port probe (tests only)",
        }
        before = len(alt["state"]["requests"])
        rc, report = _run_guard_cli(
            runner, doc, tmp_path / "literal-port", allowed,
            monkeypatch, allow=False)

        assert len(alt["state"]["requests"]) == before, (
            "a literal base_url on the same host but a different port was "
            f"executed and the token sent: {alt['state']['requests'][before:]!r}")
        row = _report_row(report, fixture_id)
        assert row is not None, f"no report row for {fixture_id} (exit={rc})"
        assert row.get("disposition") != "PASS", (
            "a same-host/different-port literal base_url was classified PASS")
        assert row.get("operator_prerequisite"), (
            "a rejected literal base_url must carry a rejection prerequisite")
        assert row.get("qubit_executed") is not True, (
            "a rejected literal base_url must not report qubit_executed")
        assert token not in json.dumps(report, default=str), (
            "token leaked into the report of a rejected literal base_url")


def test_reference_pin_verified_from_version_output(
        runner, tmp_path, monkeypatch):
    """REQUIRES: a reference whose --version contains the contractual pin is
    pinned without env; one that cannot be verified is not PASS."""
    monkeypatch.delenv("QUBIT_DIFFERENTIAL_HERMES_COMMIT", raising=False)
    monkeypatch.delenv("QUBIT_DIFFERENTIAL_OPENCLAW_COMMIT", raising=False)

    pinned_dir = tmp_path / "pinned"
    pinned_dir.mkdir()
    pinned_version = f"Hermes Agent v0.21.1 ({PIN_QUBIT})"
    pinned_bin = _write_version_script(pinned_dir, "hermes", pinned_version)

    floating_dir = tmp_path / "floating"
    floating_dir.mkdir()
    floating_bin = _write_version_script(
        floating_dir, "hermes", "Hermes Agent (unverifiable build)")

    try:
        doc = _cli_fixture(
            runner, "cli.reference.version.pin.8u",
            references={"hermes": {"argv": ["hermes", "--help"], "env": {}}})
        path = _write_fixture(tmp_path, doc)
        row = _row(capability_id="cli.reference.version.pin.8u",
                   disposition="OPEN", fixture=str(path))

        pinned_adapters = {
            "qubit": _fake_adapter(runner, "qubit", exit_code=0, stdout="usage"),
            "hermes": _scripted_reference_adapter(
                runner, "hermes", pinned_bin, exit_code=0, stdout="usage"),
        }
        result = runner.run_inventory([row], tmp_path, adapters=pinned_adapters)[0]
        info = result["references"]["hermes"]
        assert info["executed"] is True, info
        assert PIN_QUBIT in (info.get("reference_version") or ""), (
            "the probed --version was not recorded: "
            f"{info.get('reference_version')!r}")
        assert info["reference_pinned"] is True, (
            "a --version carrying the contractual pin must pin the reference "
            "without any commit env")
        assert result["unpinned_references"] == []
        assert result["disposition"] == "PASS", result["diagnostics"]

        unpinned_adapters = {
            "qubit": _fake_adapter(runner, "qubit", exit_code=0, stdout="usage"),
            "hermes": _scripted_reference_adapter(
                runner, "hermes", floating_bin, exit_code=0, stdout="usage"),
        }
        result2 = runner.run_inventory(
            [row], tmp_path, adapters=unpinned_adapters)[0]
        info2 = result2["references"]["hermes"]
        assert info2["reference_pinned"] is False, (
            "an unverifiable --version must not be treated as pinned")
        assert "hermes" in (result2.get("unpinned_references") or []), (
            "the unverifiable reference was not recorded as unpinned: "
            f"{result2.get('unpinned_references')!r}")
        assert result2["disposition"] != "PASS", (
            "an unverifiable reference produced a PASS claim")
    finally:
        runner._VERSION_CACHE.pop(pinned_bin, None)
        runner._VERSION_CACHE.pop(floating_bin, None)


# --------------------------------------------------------------------------- #
# Voice transport (INTERFACE v1.2 `voice` kind)
#
# The frozen Adapter contract (INTERFACE.md §TRANSPORT ADAPTER CONTRACT) is the
# public seam for a new transport kind. A `voice` turn resolves an utterance to
# the SAME canonical operation id the CLI/dashboard use, without touching
# audio/TTS/STT, dispatching a backend, or mutating state. These tests drive
# that contract through the public `run_inventory` entry point with a
# voice-capable adapter and exercise the runner's voice family diagnostics
# (`voice_operation`/`resolved_identity`/`confirmation`) plus the
# `voice_backend_mismatch` gate.
# --------------------------------------------------------------------------- #
VOICE_UTTERANCE_WITH_ARGS = "pause autonomy nightly-uploads"
VOICE_CANONICAL_ENTRY = "autonomy_pause"
VOICE_CANONICAL_ARGS = {"profile_id": "nightly-uploads"}
VOICE_CONFIRM_UTTERANCE = "pause everything"
VOICE_CONFIRM_ENTRY = "pause"

# The audio surfaces resolution must never reach. Missing modules are recorded
# (not hidden): the sentinel test reports which ones were actually armed.
VOICE_AUDIO_MODULES = ("voicebridge.audio", "voicebridge.tts",
                       "voicebridge.asr")


def _voice_commands():
    """Import the canonical Qubit voice resolver (pure; stdlib-only)."""
    import importlib
    return importlib.import_module("voicebridge.voice_commands")


def _voice_transport_adapter(runner_mod):
    """A conforming ``voice``-kind transport over the canonical resolver.

    ``execute`` accepts only ``spec.kind == "voice"``, resolves the utterance
    with ``voicebridge.voice_commands.resolve`` (no side effects), and returns
    the canonical entry id + extracted args and the confirmation contract. It
    never calls ``execute``/``confirm_pending``/``cancel_pending`` and never
    touches audio, a subprocess, or the network.
    """
    from dataclasses import dataclass

    vc = _voice_commands()
    confirm_ttl = float(getattr(vc, "CONFIRM_TTL_S", 0) or 0)

    # ``CallResult`` (v1.2) carries no ``confirmation`` field, but the voice
    # fixture contract exposes it at the payload top level so the runner's
    # voice family diagnostics compare it. Extend the frozen result shape; a
    # later runner that adds the field itself yields the same payload.
    @dataclass
    class _VoiceCallResult(runner_mod.CallResult):
        confirmation: object = None

    class _VoiceTransportAdapter:
        name = "qubit"
        project = "qubit"
        is_mock = False

        def __init__(self):
            self.resolved = []

        def available(self):
            return True

        def execute(self, request):
            kind = str(request.get("kind") or "")
            if kind != "voice":
                raise NotImplementedError(
                    f"voice transport cannot execute kind {kind!r}")
            body = request.get("body")
            text = str(body.get("text") or "") if isinstance(body, dict) else ""
            resolution = vc.resolve(text)
            self.resolved.append(text)
            entry = resolution.entry
            entry_id = getattr(entry, "id", None)
            backend = getattr(entry, "backend", None)
            confirm = bool(getattr(entry, "confirm", False))
            return _VoiceCallResult(
                exit_code=0,
                confirmation={
                    "required": confirm,
                    "staged": False,
                    "cancellable": True,
                    "single_use": True,
                    "replay": False,
                    "ttl_s": confirm_ttl if confirm else None,
                    "expires_in_s": confirm_ttl if confirm else None,
                },
                json={
                    "kind": resolution.kind,
                    "entry_id": entry_id,
                    "backend": backend,
                    "matched_alias": (
                        getattr(entry, "aliases", ("",))[0] if entry else None),
                    "extracted_args": dict(resolution.args),
                    "cancel": {"required": False, "decision": "deny",
                               "executes": False},
                    "expiry": {
                        "ttl_s": confirm_ttl if confirm else None,
                        "on_expiry": "aborted",
                    },
                    "replay": {
                        "second_settle": "idempotent_noop",
                        "revalidate_args_from_journal": True,
                    },
                },
                voice_operation={
                    "entry_id": entry_id,
                    "backend": backend,
                    "args": dict(resolution.args),
                },
                persisted_state={
                    "entry_id": entry_id,
                    "journal": "shared confirmation journal",
                    "staged": False,
                    "cancelled": False,
                    "expired": False,
                    "replayed": False,
                },
                transport="voice",
                meta={"adapter": "voice", "mock": False},
            )

    return _VoiceTransportAdapter()


def _voice_fixture(fixture_id, *, utterance, expected, references=None):
    doc = {
        "schema_version": 1,
        "fixture_id": fixture_id,
        "family": "voice",
        "command": None,
        "request": {
            "kind": "voice",
            "method": "POST",
            "path": "/api/type",
            "headers": {},
            "body": {"text": utterance},
            "event": None,
            "argv": [],
        },
        "normalizers": ["timestamp", "uuid", "session_token",
                        "product_branding"],
        "expected": dict(expected),
        "qubit": {"path": "voicebridge/voice_commands.py:329",
                  "caller": "resolve", "approval": None},
        "hermes": {"path": "qubit_cli/web_routers/audio.py:78",
                   "symbol": "transcribe_audio_upload"},
        "openclaw": {"path": "src/gateway/server-methods/talk.ts:827",
                     "symbol": "talkHandlers"},
        "notes": "v1.2 voice transport contract fixture (8u)",
    }
    if references is not None:
        doc["references"] = references
    return doc


def _voice_row(fixture_id, fixture_path):
    return _row(
        capability_id=fixture_id,
        family="voice",
        disposition="OPEN",
        fixture=str(fixture_path),
        qubit_path="voicebridge/voice_commands.py:329",
        qubit_symbol="resolve",
    )


def test_voice_behavior_pass_covers_dashboard_ops_surface(runner):
    """A dashboard-ops canonical op is disposed by a real voice-registry round-trip.

    The registry entry's own declared example must resolve back to the row's
    exact entry id. A row that maps neither the voice registry nor the
    dashboard-ops module stays OPEN (never a name-level coincidence).
    """
    try:
        vc = runner._import_voice_commands()
    except Exception:  # noqa: BLE001
        pytest.skip("voice registry unavailable")
    entry_id = None
    for name in ("REGISTRY", "PLUGIN_FLEET_REGISTRY", "MODEL_PROFILE_REGISTRY"):
        registry = getattr(vc, name, None)
        if not isinstance(registry, dict):
            continue
        for eid in sorted(registry):
            entry = registry[eid]
            for ex in sorted(str(x) for x in (getattr(entry, "examples", ()) or ())):
                try:
                    res = vc.resolve(ex)
                except Exception:  # noqa: BLE001
                    continue
                if (str(getattr(res, "kind", "")) in ("match", "clarify")
                        and getattr(getattr(res, "entry", None), "id", None) == eid):
                    entry_id = eid
                    break
            if entry_id:
                break
        if entry_id:
            break
    if not entry_id:
        pytest.skip("no voice entry with a resolving example")

    row = _row(
        capability_id="op.voicecoverage8u", family="recovery",
        upstream_project="qubit", disposition="OPEN",
        qubit_path="voicebridge/dashboard_ops.py:157", qubit_symbol=entry_id,
        test_ids=[], trace_refs=[])
    proofs = runner._voice_behavior_pass([row], {}, None, None)
    assert row["capability_id"] in proofs, (
        "a dashboard-ops canonical op was not disposed by its real voice "
        "registry round-trip")

    unrelated = _row(
        capability_id="op.nocoverage8u", family="recovery", disposition="OPEN",
        qubit_path="qubit_runtime/tests/test_qubit_state.py:636",
        qubit_symbol=entry_id, test_ids=[], trace_refs=[])
    assert runner._voice_behavior_pass([unrelated], {}, None, None) == {}, (
        "a row whose qubit_path is not a voice/dashboard-ops surface must not "
        "be disposed by a name coincidence")


def test_voice_transport_resolves_canonical_op(runner, tmp_path):
    """An utterance resolves to the canonical operation id with extracted args;
    the result hash is the canonical payload hash and is deterministic."""
    fixture_id = "voice.intent.canonical_op.8u"
    doc = _voice_fixture(
        fixture_id,
        utterance=VOICE_UTTERANCE_WITH_ARGS,
        expected={
            "voice_operation": {
                "entry_id": VOICE_CANONICAL_ENTRY,
                "backend": VOICE_CANONICAL_ENTRY,
                "args": VOICE_CANONICAL_ARGS,
            },
            "confirmation": {"required": True},
        },
    )
    row = _voice_row(fixture_id, _write_fixture(tmp_path, doc))
    adapter = _voice_transport_adapter(runner)

    first = runner.run_inventory(
        [row], tmp_path, adapters={"qubit": adapter},
        allow_state_changing=True)[0]
    second = runner.run_inventory(
        [row], tmp_path, adapters={"qubit": adapter},
        allow_state_changing=True)[0]

    assert first["qubit_executed"] is True, (
        "the voice transport did not resolve the fixture: "
        f"{first.get('operator_prerequisite')!r}")
    payload = first.get("qubit_payload") or {}
    operation = payload.get("voice_operation") or {}
    assert operation.get("entry_id") == VOICE_CANONICAL_ENTRY, (
        f"canonical operation id mismatch: {operation!r}")
    assert operation.get("args") == VOICE_CANONICAL_ARGS, (
        f"extracted args mismatch: {operation.get('args')!r}")

    assert first["qubit_raw_result_hash"] == runner.result_hash(payload), (
        "reported raw hash is not the canonical hash of the resolved payload")
    assert first["qubit_result_hash"] == second["qubit_result_hash"], (
        "voice resolution is not deterministic across two calls")
    assert first["qubit_raw_result_hash"] == second["qubit_raw_result_hash"], (
        "voice raw result hash drifted across two calls")
    assert adapter.resolved == [VOICE_UTTERANCE_WITH_ARGS,
                                VOICE_UTTERANCE_WITH_ARGS]


def test_voice_reference_unavailable_is_operator_only(runner, tmp_path):
    """With no reference resolver the voice row is OPERATOR_ONLY, never PASS."""
    fixture_id = "voice.intent.no_reference.8u"
    doc = _voice_fixture(
        fixture_id,
        utterance=VOICE_CONFIRM_UTTERANCE,
        expected={
            "voice_operation": {
                "entry_id": VOICE_CONFIRM_ENTRY,
                "backend": VOICE_CONFIRM_ENTRY,
            },
            "confirmation": {"required": True},
        },
    )
    row = _voice_row(fixture_id, _write_fixture(tmp_path, doc))
    adapter = _voice_transport_adapter(runner)

    result = runner.run_inventory(
        [row], tmp_path, adapters={"qubit": adapter},
        allow_state_changing=True)[0]

    assert result["disposition"] == "OPERATOR_ONLY", (
        "a voice row with no reference resolver must be OPERATOR_ONLY, got "
        f"{result['disposition']!r}")
    assert result["disposition"] != "PASS"
    assert result.get("operator_prerequisite"), (
        "an OPERATOR_ONLY voice row must carry an operator_prerequisite")
    assert result.get("reference_executed") is False
    assert result.get("voice_mismatch") is False, (
        "the canonical op matched; voice_mismatch must stay False")

    gates = _gate_ok_map(runner.apply_fail_closed_gates([row], [result]))
    assert gates.get("unexecuted_row") is True, (
        "an OPERATOR_ONLY voice row is not an unexecuted PASS claim")
    assert gates.get("unexecuted_open_row") is True, (
        "an OPERATOR_ONLY voice row is terminal; it must not trip "
        "unexecuted_open_row")
    assert gates.get("voice_backend_mismatch") is True


def test_voice_confirm_cancel_semantics(runner, tmp_path):
    """A confirm-gated intent reports its confirmation requirement and carries
    the cancellation/expiry/replay semantics."""
    vc = _voice_commands()
    fixture_id = "voice.confirm.cancel_semantics.8u"
    doc = _voice_fixture(
        fixture_id,
        utterance=VOICE_CONFIRM_UTTERANCE,
        expected={
            "voice_operation": {"entry_id": VOICE_CONFIRM_ENTRY},
            "confirmation": {"required": True},
        },
    )
    row = _voice_row(fixture_id, _write_fixture(tmp_path, doc))
    adapter = _voice_transport_adapter(runner)

    result = runner.run_inventory(
        [row], tmp_path, adapters={"qubit": adapter},
        allow_state_changing=True)[0]
    assert result["qubit_executed"] is True
    assert result.get("voice_mismatch") is False, (
        "the confirmation requirement matched; voice_mismatch must stay False")
    payload = result.get("qubit_payload") or {}
    voice_json = payload.get("json") or {}

    entry = vc._lookup_entry(VOICE_CONFIRM_ENTRY)
    assert entry is not None and entry.confirm is True, (
        "the canonical 'pause' entry is confirm-gated")
    confirmation = payload.get("confirmation") or {}
    assert confirmation.get("required") is True, (
        f"confirm-gated intent did not report its requirement: {confirmation!r}")
    for field in ("cancellable", "single_use", "replay", "ttl_s",
                  "expires_in_s"):
        assert field in confirmation, (
            f"confirmation requirement lacks {field!r}: {confirmation!r}")
    assert confirmation["ttl_s"] == float(vc.CONFIRM_TTL_S)
    assert confirmation["expires_in_s"] == float(vc.CONFIRM_TTL_S)
    assert confirmation["replay"] is False
    assert confirmation["cancellable"] is True

    cancel = voice_json.get("cancel") or {}
    assert cancel.get("executes") is False, (
        f"cancellation must execute nothing: {cancel!r}")
    assert cancel.get("decision") == "deny"
    assert (voice_json.get("expiry") or {}).get("ttl_s") == float(
        vc.CONFIRM_TTL_S), "the confirmation contract must expose expiry TTL"
    assert (voice_json.get("expiry") or {}).get("on_expiry") == "aborted"
    replay = voice_json.get("replay") or {}
    assert replay.get("second_settle") == "idempotent_noop", (
        f"replay must be a single-use no-op: {replay!r}")
    assert replay.get("revalidate_args_from_journal") is True

    persisted = payload.get("persisted_state") or {}
    for field in ("staged", "cancelled", "expired", "replayed"):
        assert field in persisted, (
            f"persisted voice state lacks {field!r}: {persisted!r}")


def test_voice_never_executes_hardware(runner, tmp_path, monkeypatch):
    """Resolution never calls audio/TTS/STT, a backend, or a subprocess, and
    mutates no state: every such entry point is armed as a recording sentinel
    and the result is asserted to be side-effect free."""
    vc = _voice_commands()
    fired = []

    def _sentinel(label):
        def _record(*args, **kwargs):
            fired.append(label)
            return None
        return _record

    monkeypatch.setattr(vc, "execute", _sentinel("voice_commands.execute"),
                        raising=False)
    monkeypatch.setattr(vc, "confirm_pending",
                        _sentinel("voice_commands.confirm_pending"),
                        raising=False)
    monkeypatch.setattr(vc, "cancel_pending",
                        _sentinel("voice_commands.cancel_pending"),
                        raising=False)
    monkeypatch.setattr(vc, "_speak", _sentinel("voice_commands._speak"),
                        raising=False)
    monkeypatch.setattr(vc, "_estop", _sentinel("voice_commands._estop"),
                        raising=False)

    monkeypatch.setattr(subprocess, "Popen", _sentinel("subprocess.Popen"))
    monkeypatch.setattr(subprocess, "run", _sentinel("subprocess.run"))

    # Importing any audio/TTS/STT module during resolution is a hardware
    # dependency: record it (without raising, so every hit is reported).
    import importlib.abc

    class _AudioImportGuard(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.startswith(tuple(VOICE_AUDIO_MODULES)):
                fired.append(f"import:{fullname}")
            return None

    monkeypatch.setattr(sys, "meta_path", [_AudioImportGuard(), *sys.meta_path])
    for name in VOICE_AUDIO_MODULES:
        # delitem (not a raw pop) snapshots and restores the module object at
        # teardown, so a later test that touches a submodule attribute
        # (``voicebridge.audio.mic``/``.playback``) cannot break on a stale
        # parent package that no longer carries its submodule attributes.
        monkeypatch.delitem(sys.modules, name, raising=False)

    fixture_id = "voice.hardware.sentinel.8u"
    doc = _voice_fixture(
        fixture_id,
        utterance=VOICE_CONFIRM_UTTERANCE,
        expected={
            "voice_operation": {"entry_id": VOICE_CONFIRM_ENTRY},
            "confirmation": {"required": True},
        },
    )
    row = _voice_row(fixture_id, _write_fixture(tmp_path, doc))
    adapter = _voice_transport_adapter(runner)

    result = runner.run_inventory(
        [row], tmp_path, adapters={"qubit": adapter},
        allow_state_changing=True)[0]

    assert fired == [], f"voice resolution triggered side effects: {fired}"
    assert result["qubit_executed"] is True
    assert adapter.resolved == [VOICE_CONFIRM_UTTERANCE], (
        f"resolution did not happen purely in-process: {adapter.resolved!r}")
    payload = result.get("qubit_payload") or {}
    assert (payload.get("confirmation") or {}).get("staged") is False, (
        "resolution staged a confirmation (state was mutated)")
    assert "receipt" not in payload, (
        "resolution produced an execution receipt (a backend ran)")
    # No audio/TTS/STT module was imported during resolution either.
    assert not any(name.startswith("import:") for name in fired), (
        f"resolution imported a hardware module: {fired}")


def test_safe_equivalent_requires_proof_zero_after_generator_fix(runner):
    """The regenerated (proof-aligned) inventory clears the
    `safe_equivalent_requires_proof` gate. Skips ONLY when no regenerated
    inventory exists — a missing artifact is never treated as a pass."""
    candidates = []
    declared = os.environ.get("QUBIT_DIFFERENTIAL_REGENERATED_INVENTORY")
    if declared:
        candidates.append(Path(declared))
    root = Path("/tmp/qubit-differential")
    if root.is_dir():
        candidates += sorted(root.glob("**/*.proof_aligned.json"))
    existing = sorted({p for p in candidates if p.is_file()})
    if not existing:
        pytest.skip(
            "no regenerated (proof-aligned) inventory present under "
            f"{root} / QUBIT_DIFFERENTIAL_REGENERATED_INVENTORY")

    for path in existing:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            pytest.fail(f"regenerated inventory {path} is unreadable: {exc}")
        rows = doc.get("rows") if isinstance(doc, dict) else doc
        assert isinstance(rows, list) and rows, (
            f"regenerated inventory {path} carries no rows")
        gates = runner.apply_fail_closed_gates(rows, [])
        gate = next(
            (g for g in gates
             if g.get("gate") == "safe_equivalent_requires_proof"), None)
        assert gate is not None, (
            "runner did not emit safe_equivalent_requires_proof")
        assert gate["ok"] is True, (
            f"{path}: safe_equivalent_requires_proof is red on "
            f"{len(gate['rows'])} rows: {gate['rows'][:5]}")
        assert gate["rows"] == [], (
            f"{path}: gate green with non-empty rows: {gate['rows'][:5]}")


# ---------------------------------------------------------- proof-level semantics
# A SAFE_EQUIVALENT claim must declare its proof level:
#   executed -> a real executed comparison (executed result + both hashes), or
#   wiring   -> a resolved Qubit production mapping plus a resolvable test id.
# A missing/unknown level, a wiring proof with a bogus/absent test id, and an
# OpenClaw delta row whose only "proof" is a mapping (no real test reference)
# must all fail closed.
_REAL_TEST_ID = (
    "tests/test_provider_migration.py"
    "::test_run_role_refuses_non_executable_profile")
_BOGUS_TEST_ID = (
    "tests/test_differential_parity8u.py"
    "::test_bogus_wiring_test_id_that_never_exists_8u")
_PROOF_GATE_NAMES = (
    "safe_equivalent_requires_proof",
    "proof_level",
    "safe_equivalent_proof_level",
    "proof_level_requires_resolvable_test_id",
)


def _proof_gates(runner_mod, rows, results):
    return _gate_ok_map(runner_mod.apply_fail_closed_gates(rows, results))


def _safe_equivalent_accepted(runner_mod, row, result) -> bool:
    """A claim survives only as SAFE_EQUIVALENT with every proof-level gate
    green (fail closed: a missing gate is not evidence of acceptance)."""
    if str(result.get("disposition") or "OPEN") != "SAFE_EQUIVALENT":
        return False
    gates = _proof_gates(runner_mod, [row], [result])
    for name in _PROOF_GATE_NAMES:
        if name in gates and gates[name] is not True:
            return False
    return True


def _proof_claim_refused(runner_mod, row, tmp_path) -> bool:
    """The runner must refuse the claim: validation downgrades the disposition,
    or a proof-level fail-closed gate fires."""
    result = runner_mod.run_inventory([row], tmp_path, adapters={})[0]
    if str(result.get("disposition") or "OPEN") != "SAFE_EQUIVALENT":
        return True
    gates = _proof_gates(runner_mod, [row], [result])
    return any(gates.get(name) is False for name in _PROOF_GATE_NAMES)


def test_safe_equivalent_requires_proof_level(runner, tmp_path):
    """A SAFE_EQUIVALENT row must carry ``proof_level`` in {executed, wiring}.

    ``executed`` is a real executed comparison; ``wiring`` is a resolved Qubit
    mapping plus a real test reference. A missing or unknown level must fail
    closed (validation downgrade or gate failure), never survive as
    SAFE_EQUIVALENT.
    """
    cid = "provider.resolver.proof_level.8u"
    common = dict(
        capability_id=cid,
        family="provider",
        disposition="SAFE_EQUIVALENT",
        fixture=None,
        qubit_path="voicebridge/providers/resolver.py:1",
        qubit_symbol="EffectiveProfile",
        disposition_proof="voicebridge/providers/resolver.py:1",
        trace_refs=[],
    )

    executed_row = _row(proof_level="executed", test_ids=[_REAL_TEST_ID], **common)
    executed_res = _result(
        executed_row, disposition="SAFE_EQUIVALENT",
        declared_disposition="SAFE_EQUIVALENT", executed=True,
        qubit_executed=True, reference_executed=True,
        qubit_result_hash="sha256:" + "a" * 64,
        upstream_result_hash="sha256:" + "b" * 64)
    assert _safe_equivalent_accepted(runner, executed_row, executed_res), (
        "a SAFE_EQUIVALENT row declaring proof_level='executed' with a real "
        "executed result must be honored")

    wiring_row = _row(proof_level="wiring", test_ids=[_REAL_TEST_ID], **common)
    wiring_res = _result(wiring_row, disposition="SAFE_EQUIVALENT",
                         declared_disposition="SAFE_EQUIVALENT", executed=False)
    assert _safe_equivalent_accepted(runner, wiring_row, wiring_res), (
        "a SAFE_EQUIVALENT row declaring proof_level='wiring' with a resolved "
        "mapping and a real test reference must be honored")

    missing_row = _row(test_ids=[_REAL_TEST_ID], **common)
    missing_row.pop("proof_level", None)
    assert _proof_claim_refused(runner, missing_row, tmp_path), (
        "a SAFE_EQUIVALENT row with no declared proof_level survived")

    unknown_row = _row(proof_level="vibes", test_ids=[_REAL_TEST_ID], **common)
    assert _proof_claim_refused(runner, unknown_row, tmp_path), (
        "a SAFE_EQUIVALENT row with an unknown proof_level 'vibes' survived")


def test_wiring_proof_requires_resolvable_test_id(runner, tmp_path):
    """A ``wiring`` proof must name a resolvable test id (a real
    ``file::function``). A bogus id, or no id at all, must fail closed."""
    cid = "provider.resolver.wiring_proof.8u"
    common = dict(
        capability_id=cid,
        family="provider",
        disposition="SAFE_EQUIVALENT",
        fixture=None,
        qubit_path="voicebridge/providers/resolver.py:1",
        qubit_symbol="EffectiveProfile",
        disposition_proof="voicebridge/providers/resolver.py:1",
        proof_level="wiring",
        trace_refs=[],
    )

    valid_row = _row(test_ids=[_REAL_TEST_ID], **common)
    valid_res = _result(valid_row, disposition="SAFE_EQUIVALENT",
                        declared_disposition="SAFE_EQUIVALENT", executed=False)
    assert _safe_equivalent_accepted(runner, valid_row, valid_res), (
        "a wiring proof carrying the real test id was not honored")

    bogus_row = _row(test_ids=[_BOGUS_TEST_ID], **common)
    assert _proof_claim_refused(runner, bogus_row, tmp_path), (
        "a wiring proof naming the non-resolvable test id "
        f"{_BOGUS_TEST_ID!r} survived")

    no_id_row = _row(test_ids=[], **common)
    assert _proof_claim_refused(runner, no_id_row, tmp_path), (
        "a wiring proof with no test id survived")


def _openclaw_proof_row(cid, **over):
    """OpenClaw latest-main delta row with only a mapping/disposition_proof."""
    row = _openclaw_delta_row(
        cid, disposition="SAFE_EQUIVALENT",
        qubit_path="voicebridge/providers/gateway.py:1",
        qubit_symbol="refresh_auth", production_caller="refresh_auth",
        disposition_proof="voicebridge/providers/gateway.py:1",
        test_ids=[], trace_refs=[])
    row.update(over)
    return row


def test_openclaw_disposed_row_without_test_ref_stays_open(runner):
    """A declared-disposition OpenClaw delta row with no real test reference is
    not disposed: the mapping / disposition_proof is not a test, so the
    ``openclaw_undisposed_addition`` gate must flag it."""
    cid = "gateway.method.models.authRefresh.disposed_no_test.8u"
    row = _openclaw_proof_row(cid)
    result = _result(row, disposition="SAFE_EQUIVALENT",
                     declared_disposition="SAFE_EQUIVALENT", executed=False)
    gate = {g.get("gate"): g for g in runner.apply_fail_closed_gates(
        [row], [result])}["openclaw_undisposed_addition"]
    assert gate["ok"] is False, (
        "an OpenClaw delta row whose only proof is a mapping (no real test "
        "reference) was counted as disposed")
    assert cid in (gate.get("rows") or []), (
        f"{cid} not named by openclaw_undisposed_addition: {gate!r}")


def test_openclaw_disposed_row_with_real_test_ref_is_disposed(runner):
    """The same row with a real, resolvable test reference is disposed and must
    not be counted by ``openclaw_undisposed_addition``."""
    cid = "gateway.method.models.authRefresh.disposed_with_test.8u"
    row = _openclaw_proof_row(cid, test_ids=[_REAL_TEST_ID])
    result = _result(row, disposition="SAFE_EQUIVALENT",
                     declared_disposition="SAFE_EQUIVALENT", executed=False)
    gate = {g.get("gate"): g for g in runner.apply_fail_closed_gates(
        [row], [result])}["openclaw_undisposed_addition"]
    assert gate["ok"] is True, (
        "an OpenClaw delta row with a real test reference was counted "
        f"undisposed: {gate!r}")
    assert cid not in (gate.get("rows") or []), (
        f"{cid} was flagged as undisposed despite a real test reference")


# ===========================================================================
# Proof-resolver linkage hardening (wave 8u)
#
# The resolver must never dispose a row on a generic-symbol name collision. A
# test that merely mentions a generic symbol (`main`, `status`, ...) -- e.g.
# `gap_ledger.main` -- is not proof that the mapped Qubit module is exercised.
# Disposal requires genuine linkage: the test imports the mapped module or
# calls `<module>.<symbol>`. A failure here is a runner mismatch to report,
# never a reason to weaken the assertion.
# ===========================================================================
# Four Qubit gap-ledger rows that declare OPERATOR_ONLY (terminal) but carry an
# interface-level SAFE_EQUIVALENT claim, all mapped to
# ``voicebridge/terminal.py:382`` / ``main``. The pre-hardening resolver proved
# all four with ``tests/test_ledger_ingestion_wave3.py::test_omissions_...``
# (``gap_ledger.main`` + the incidental bare name ``terminal``), a false
# disposal. (See /tmp/qubit-differential/inventory/dispositions/
# OPENCLAW_GATE_RECHECK2.md and prooffix2/INVENTORY.json.)
_TERMINAL_LEDGER_ROWS = (
    "dashboard.control_source.route_location",
    "dashboard.control_source.terminal.route",
    "dashboard.control_source.terminal_page",
    "dashboard.route.terminal",
)
_LEDGER_INGESTION_TEST_FILE = "tests/test_ledger_ingestion_wave3.py"
_TERMINAL_QUBIT_PATH = "voicebridge/terminal.py:382"
_TERMINAL_QUBIT_SYMBOL = "main"


def _file_imports_module(rel_path: str, module: str) -> bool:
    """True when a test file imports ``module`` (or a submodule/symbol of it).

    Independent of the runner's own AST index, so the assertion proves the cited
    test is genuinely linked to the mapped Qubit module rather than trusting the
    resolver's own bookkeeping.
    """
    import ast
    try:
        tree = ast.parse((REPO_ROOT / rel_path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return False
    parent, _, leaf = module.rpartition(".")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module or alias.name.startswith(module + "."):
                    return True
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == module or node.module.startswith(module + "."):
                return True
            if parent and node.module == parent and any(
                alias.name == leaf for alias in node.names
            ):
                return True
    return False


def _load_single(runner, tmp_path, row):
    """Load one inventory row through the resolver (``load_inventory``).

    An explicitly passed single inventory file is honored (the directory-scan
    allowlist only constrains directory scans), so a hermetic one-row probe does
    not depend on a canonical filename.
    """
    path = tmp_path / "single_row.json"
    path.write_text(
        json.dumps({"schema_version": 1, "rows": [row]}), encoding="utf-8")
    loaded = runner.load_inventory(path)
    assert len(loaded) == 1, (
        f"loader returned {len(loaded)} rows for {row['capability_id']!r}")
    return loaded[0]


def _undisposed_gate(runner, rows):
    return {g.get("gate"): g for g in runner.apply_fail_closed_gates(
        rows, [])}["openclaw_undisposed_addition"]


def test_generic_symbol_name_collision_is_not_proof(runner, tmp_path):
    """A generic symbol discovered only by name collision is not proof.

    ``tests/test_ledger_ingestion_wave3.py`` calls ``gap_ledger.main`` and its
    text contains the bare name ``terminal``; the pre-hardening whole-file tie
    accepted that as proof for ``voicebridge/terminal.py:382`` / ``main``. A
    generic-symbol collision must not dispose the row: it stays OPEN and the
    OpenClaw delta gate counts it undisposed.
    """
    cid = "dashboard.control_source.terminal.route.collision8u"
    row = _openclaw_delta_row(
        cid, disposition="OPERATOR_ONLY",
        interface_disposition="SAFE_EQUIVALENT",
        qubit_path=_TERMINAL_QUBIT_PATH,
        qubit_symbol=_TERMINAL_QUBIT_SYMBOL)

    resolved = _load_single(runner, tmp_path, row)
    assert resolved["disposition"] != "SAFE_EQUIVALENT", (
        "a generic `main` name collision disposed the row: "
        f"disposition={resolved['disposition']!r} "
        f"test_ids={resolved.get('test_ids')!r}")
    cited = [str(t) for t in (resolved.get("test_ids") or [])]
    assert not any(_LEDGER_INGESTION_TEST_FILE in t for t in cited), (
        "the unrelated ledger-ingestion test was cited as proof: "
        f"{cited!r}")

    gate = _undisposed_gate(runner, [resolved])
    assert gate["ok"] is False, (
        "the collision row was counted as disposed (gate green)")
    assert cid in (gate.get("rows") or []), (
        f"{cid} was not named as undisposed: {gate!r}")


def test_linked_test_disposes_row(runner, tmp_path):
    """A test that imports the mapped module (or calls the symbol) disposes it.

    ``tests/test_computer_use8tf.py`` imports ``voicebridge.computer_use`` and
    calls ``CU.status``, so the resolver may cite it and the OpenClaw delta gate
    no longer flags the row as undisposed. This is the positive counterpart to
    the generic-symbol collision above.
    """
    cid = "gateway.method.computer.status.linked8u"
    module = "voicebridge.computer_use"
    row = _openclaw_delta_row(
        cid, disposition="OPEN",
        interface_disposition="SAFE_EQUIVALENT",
        qubit_path="voicebridge/computer_use.py:83",
        qubit_symbol="status")

    resolved = _load_single(runner, tmp_path, row)
    assert resolved["disposition"] == "SAFE_EQUIVALENT", (
        "a genuinely linked test did not dispose the row: "
        f"disposition={resolved['disposition']!r} "
        f"status={resolved.get('wiring_proof_status')!r}")
    assert resolved.get("proof_level") == "wiring", (
        f"linked row proof_level={resolved.get('proof_level')!r}")
    cited = [str(t) for t in (resolved.get("test_ids") or [])]
    assert cited, "the resolver attached no test id to a linked row"
    assert all(runner.resolves_test_id(t) for t in cited), cited
    assert all(
        _file_imports_module(t.split("::", 1)[0], module) for t in cited), (
        f"a cited test does not import the mapped module {module}: {cited!r}")

    gate = _undisposed_gate(runner, [resolved])
    assert cid not in (gate.get("rows") or []), (
        f"a linked, disposed row was still flagged undisposed: {gate!r}")


# ===========================================================================
# Frontend linkage proof type (dashboard Control-UI rows, wave 17)
#
# The 64 OpenClaw dashboard delta rows were all mapped to
# ``frontend/qubit-web/src`` modules and therefore invisible to the Python
# ``tests/`` AST resolver. A real colocated vitest suite that IMPORTS the mapped
# component and references the mapped symbol is a genuine wiring proof
# (``wiring_proof_status="frontend_linkage"``, still ``proof_level="wiring"``).
# It must never be fabricated: an absent component, an absent symbol, a
# non-frontend path or a prose-only mention keeps the row OPEN.
# ===========================================================================
def _frontend_test_imports_component(
    runner_mod, test_rel: str, component_rel: str
) -> bool:
    """Independent check: the cited test really imports the mapped component."""
    entry = runner_mod._frontend_test_index().get(test_rel)
    if not entry:
        return False
    return any(
        runner_mod._resolve_frontend_specifier(test_rel, specifier)
        == component_rel
        for specifier in entry["imports"]
    )


def test_frontend_import_scan_ignores_prose_and_mocks(runner):
    """Only real ``import`` specifiers count; prose / ``vi.mock`` do not.

    ``clipboard-usage.test.ts`` mentions ``copyTextToClipboard from
    "@/lib/clipboard"`` inside an error string. The pre-hardening loose regex
    read that prose as an import and cited the wrong test; the anchor on the
    ``import`` keyword must reject it.
    """
    prose = (
        'throw new Error("use copyTextToClipboard from '
        '\"@/lib/clipboard\" instead");'
    )
    assert runner._frontend_import_specifiers(prose) == frozenset(), (
        "a prose 'from' substring was read as an import specifier")
    assert runner._frontend_import_specifiers(
        'vi.mock("@/lib/clipboard", () => ({}));') == frozenset(), (
        "a vi.mock factory argument was read as an import specifier")
    assert "react" in runner._frontend_import_specifiers(
        'import { act } from "react";')
    assert "./clipboard" in runner._frontend_import_specifiers(
        'import { copyTextToClipboard } from "./clipboard";')
    assert "@/lib/x" in runner._frontend_import_specifiers(
        'const { X } = await import("@/lib/x");')


def test_frontend_linkage_disposes_dashboard_row(runner, tmp_path):
    """A real vitest suite that imports the mapped component disposes the row.

    ``dashboard.control_source.login_controller`` maps to
    ``OAuthLoginModal.tsx`` / ``OAuthLoginModal`` and the colocated
    ``OAuthLoginModal.test.tsx`` imports it and references the symbol, so the
    row becomes SAFE_EQUIVALENT (proof_level=wiring, proof kind
    ``frontend_linkage``) and the OpenClaw delta gate stops flagging it.
    """
    cid = "dashboard.control_source.login_controller.frontend8u"
    component = "frontend/qubit-web/src/components/OAuthLoginModal.tsx"
    symbol = "OAuthLoginModal"
    row = _openclaw_delta_row(
        cid, disposition="OPEN", interface_disposition="SAFE_EQUIVALENT",
        qubit_path=component + ":1", qubit_symbol=symbol)

    resolved = _load_single(runner, tmp_path, row)
    assert resolved["disposition"] == "SAFE_EQUIVALENT", (
        "a genuinely frontend-linked dashboard row was not disposed: "
        f"disposition={resolved['disposition']!r} "
        f"status={resolved.get('wiring_proof_status')!r}")
    assert resolved.get("proof_level") == "wiring", (
        f"frontend-linked row proof_level={resolved.get('proof_level')!r}")
    assert resolved.get("proof_kind") == "frontend_linkage"
    assert resolved.get("wiring_proof_status") == "frontend_linkage"

    test_rel = str(resolved.get("frontend_test_ref") or "")
    assert test_rel.endswith(".test.tsx"), test_rel
    assert (REPO_ROOT / test_rel).is_file(), (
        f"cited frontend test is not on disk: {test_rel!r}")
    assert _frontend_test_imports_component(runner, test_rel, component), (
        f"{test_rel} does not import the mapped component {component}")
    assert symbol in (REPO_ROOT / test_rel).read_text(encoding="utf-8"), (
        f"{test_rel} does not reference the mapped symbol {symbol}")

    gates = _proof_gates(runner, [resolved], [resolved])
    assert gates.get("proof_level") is True, gates
    assert gates.get("proof_level_requires_resolvable_test_id") is True, gates

    gate = _undisposed_gate(runner, [resolved])
    assert cid not in (gate.get("rows") or []), (
        f"a frontend-linked, disposed row was still flagged undisposed: {gate!r}")


def _frontend_component_without_test(runner):
    """First real mapped frontend component that has NO genuine vitest linkage.

    The frontend test tree evolves independently of this runner, so the
    fail-closed case is discovered at run time rather than hard-coded to one
    component (a hard-coded "has no test" premise goes stale the moment a
    colocated suite is authored). Returns ``(rel_path, symbol)`` or ``None``.
    """
    root = REPO_ROOT / "frontend" / "qubit-web" / "src"
    if not root.is_dir():
        return None
    for path in sorted(root.rglob("*.tsx")) + sorted(root.rglob("*.ts")):
        rel = str(path.relative_to(REPO_ROOT))
        symbol = path.stem
        if not symbol:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        candidate = {"qubit_path": f"{rel}:1", "qubit_symbol": symbol}
        if runner._frontend_component_path(candidate["qubit_path"]) is None:
            continue
        word = re.compile(
            r"(?<![A-Za-z0-9_$])" + re.escape(symbol) + r"(?![A-Za-z0-9_$])")
        if not word.search(text):
            continue
        if runner._frontend_linkage(candidate) is None:
            return rel, symbol
    return None


def test_frontend_linkage_absent_test_stays_open(runner, tmp_path):
    """A mapped frontend component with no vitest suite stays OPEN.

    The specific untested component is resolved from the live frontend tree so
    the test cannot go stale; the assertion (no genuine linkage => OPEN with
    ``wiring_proof_status="no_linkage"`` and the OpenClaw delta gate still
    naming it) is unchanged.
    """
    found = _frontend_component_without_test(runner)
    if found is None:
        pytest.skip("frontend tree has no unmapped component with an absent test")
    component, symbol = found
    cid = "dashboard.control_source.account_usage.frontend8u"
    row = _openclaw_delta_row(
        cid, disposition="OPEN", interface_disposition="SAFE_EQUIVALENT",
        qubit_path=f"{component}:1", qubit_symbol=symbol)

    resolved = _load_single(runner, tmp_path, row)
    assert resolved["disposition"] == "OPEN", (
        "a component with no frontend test was falsely disposed: "
        f"disposition={resolved['disposition']!r}")
    assert resolved.get("wiring_proof_status") == "no_linkage", resolved.get(
        "wiring_proof_status")
    assert not resolved.get("frontend_test_ref")

    gate = _undisposed_gate(runner, [resolved])
    assert cid in (gate.get("rows") or []), (
        f"the unlinked row was not counted undisposed: {gate!r}")


def test_frontend_linkage_never_fabricates(runner, tmp_path):
    """An absent symbol, a non-frontend path or an absent file links nothing."""
    absent_symbol = _openclaw_delta_row(
        "dashboard.control_source.chat_queue_reconnect.frontend8u",
        disposition="OPEN", interface_disposition="SAFE_EQUIVALENT",
        qubit_path="frontend/qubit-web/src/lib/events-reconnect.ts:1",
        qubit_symbol="eventsReconnect")
    assert runner._frontend_linkage(absent_symbol) is None, (
        "a symbol absent from the mapped component was linked")

    non_frontend = _openclaw_delta_row(
        "dashboard.control_source.nonfrontend.8u",
        disposition="OPEN", interface_disposition="SAFE_EQUIVALENT",
        qubit_path="voicebridge/webui.py:1", qubit_symbol="main")
    assert runner._frontend_linkage(non_frontend) is None, (
        "a non-frontend qubit_path was linked as a frontend proof")

    absent_file = _openclaw_delta_row(
        "dashboard.control_source.absentfile.8u",
        disposition="OPEN", interface_disposition="SAFE_EQUIVALENT",
        qubit_path="frontend/qubit-web/src/pages/NopePage.tsx:1",
        qubit_symbol="NopePage")
    assert runner._frontend_linkage(absent_file) is None, (
        "an absent frontend component was linked")

    for row in (absent_symbol, non_frontend, absent_file):
        resolved = _load_single(runner, tmp_path, row)
        assert resolved["disposition"] == "OPEN", (
            f"{row['capability_id']} was disposed without a genuine linkage: "
            f"{resolved['disposition']!r}")


# ---------------------------------------------------------------------------
# Served plugin-bundle linkage proof type (dashboard/plugin compiled bundles)
#
# A dashboard/plugin page served as a compiled plugin bundle has no
# ``*.tsx`` source, so the vitest linkage cannot see it. ``plugin_bundle_linkage``
# disposes such a row only when a REAL Python test FUNCTION reads the served
# bundle and references the row's control, and the bundle exists with a recorded
# sha256. Every absence (no bundle, symbol absent, no reading test) fails closed.
# ---------------------------------------------------------------------------
_ACHIEVEMENTS_BUNDLE = (
    "frontend/qubit-web/public/plugins/qubit-achievements/dist/index.js")
_ACHIEVEMENTS_BUNDLE_TEST = (
    "tests/test_qbpv2_agent3_achievements.py"
    "::test_served_bundle_wires_the_real_workflow_controls")


def test_plugin_bundle_linkage_disposes_bundle_row(runner, tmp_path):
    """A served bundle read by a real Python test disposes the row.

    ``drift.achievements.share`` maps to the compiled achievements bundle; the
    real test ``test_served_bundle_wires_the_real_workflow_controls`` reads that
    bundle and asserts ``card.share_title``, so the row becomes SAFE_EQUIVALENT
    (proof_level=wiring, proof kind ``plugin_bundle_linkage``) and the OpenClaw
    delta gate stops flagging it.
    """
    cid = "drift.achievements.share.plugin8u"
    row = _openclaw_delta_row(
        cid, disposition="OPEN", interface_disposition="SAFE_EQUIVALENT",
        qubit_path=_ACHIEVEMENTS_BUNDLE + ":1",
        qubit_symbol="card.share_title")

    resolved = _load_single(runner, tmp_path, row)
    assert resolved["disposition"] == "SAFE_EQUIVALENT", (
        "a served-bundle-linked row was not disposed: "
        f"disposition={resolved['disposition']!r} "
        f"status={resolved.get('wiring_proof_status')!r}")
    assert resolved.get("proof_level") == "wiring"
    assert resolved.get("proof_kind") == "plugin_bundle_linkage"
    assert resolved.get("wiring_proof_status") == "plugin_bundle_linkage"

    bundle_rel = str(resolved.get("plugin_bundle_ref") or "")
    assert bundle_rel == _ACHIEVEMENTS_BUNDLE, bundle_rel
    bundle_abs = REPO_ROOT / bundle_rel
    assert bundle_abs.is_file(), f"recorded bundle is not on disk: {bundle_rel!r}"
    expected_hash = "sha256:" + hashlib.sha256(bundle_abs.read_bytes()).hexdigest()
    assert resolved.get("plugin_bundle_hash") == expected_hash, (
        f"bundle hash not recorded correctly: {resolved.get('plugin_bundle_hash')!r}")
    assert resolved.get("qubit_result_hash") == expected_hash, (
        "the bundle hash must be the proof's evidence hash")

    test_ids = [str(t) for t in (resolved.get("test_ids") or [])]
    assert _ACHIEVEMENTS_BUNDLE_TEST in test_ids, test_ids
    assert runner.resolves_test_id(_ACHIEVEMENTS_BUNDLE_TEST), (
        "the cited bundle-reading test does not resolve")
    test_text = (REPO_ROOT / _ACHIEVEMENTS_BUNDLE_TEST.split("::", 1)[0]).read_text(
        encoding="utf-8")
    assert "card.share_title" in test_text, (
        "the cited test does not reference the mapped control")
    assert _ACHIEVEMENTS_BUNDLE in test_text or "qubit-achievements" in test_text, (
        "the cited test does not reference the served bundle")

    gates = _proof_gates(runner, [resolved], [resolved])
    assert gates.get("proof_level") is True, gates
    assert gates.get("proof_level_requires_resolvable_test_id") is True, gates

    gate = _undisposed_gate(runner, [resolved])
    assert gate["ok"] is True, gate
    assert cid not in (gate.get("rows") or []), (
        f"a bundle-linked, disposed row was still flagged undisposed: {gate!r}")


def test_plugin_bundle_linkage_never_fabricates(runner, tmp_path, monkeypatch):
    """An absent bundle, an absent symbol or no reading test links nothing."""
    bundle_sym = {
        "qubit_path": _ACHIEVEMENTS_BUNDLE + ":1",
        "qubit_symbol": "card.share_title",
    }
    # No reading test at all (inject an empty function index).
    monkeypatch.setattr(runner, "_PLUGIN_BUNDLE_TEST_CACHE", {})
    assert runner._plugin_bundle_linkage(bundle_sym) is None, (
        "a bundle with no reading test was linked")
    monkeypatch.undo()

    # A non-bundle source path is never a served plugin bundle.
    assert runner._plugin_bundle_linkage({
        "qubit_path": "frontend/qubit-web/src/App.tsx:1",
        "qubit_symbol": "App",
    }) is None, "a non-bundle frontend source was linked as a plugin bundle"

    # A non-JS asset under the plugin root is not a bundle.
    assert runner._plugin_bundle_linkage({
        "qubit_path": "frontend/qubit-web/public/plugins/fleet.json:1",
        "qubit_symbol": "qubit-achievements",
    }) is None, "a non-JS plugin asset was linked as a bundle"

    # The bundle exists but the row's symbol is absent from it.
    assert runner._plugin_bundle_linkage({
        "qubit_path": _ACHIEVEMENTS_BUNDLE + ":1",
        "qubit_symbol": "no.such.control.label",
    }) is None, "a symbol absent from the served bundle was linked"

    # An absent bundle file.
    assert runner._plugin_bundle_linkage({
        "qubit_path": "frontend/qubit-web/public/plugins/nope/dist/index.js:1",
        "qubit_symbol": "card.share_title",
    }) is None, "an absent bundle file was linked"

    # Every fabricated row stays OPEN (never disposed).
    for symbol, path in (
        ("no.such.control.label", _ACHIEVEMENTS_BUNDLE + ":1"),
        ("card.share_title", "frontend/qubit-web/public/plugins/nope/dist/index.js:1"),
    ):
        cid = f"drift.fabricated.{symbol}.8u"
        row = _openclaw_delta_row(
            cid, disposition="OPEN", interface_disposition="SAFE_EQUIVALENT",
            qubit_path=path, qubit_symbol=symbol)
        resolved = _load_single(runner, tmp_path, row)
        assert resolved["disposition"] == "OPEN", (
            f"{cid} was disposed without a genuine bundle linkage: "
            f"{resolved['disposition']!r}")


def test_plugin_bundle_function_reads_requires_a_real_read(runner):
    """A prose mention of the bundle path is not a read; a real read is."""
    bundle = _ACHIEVEMENTS_BUNDLE
    prose = {
        "reads_file": False,
        "consts": {},
        "names": frozenset(),
        "strings": frozenset({bundle}),
    }
    assert not runner._plugin_bundle_function_reads(
        prose, bundle, "qubit-achievements", "index.js"), (
        "a bare string mention (no file read) was accepted as a bundle read")

    real = {
        "reads_file": True,
        "consts": {"BUNDLE": bundle},
        "names": frozenset({"BUNDLE"}),
        "strings": frozenset(),
    }
    assert runner._plugin_bundle_function_reads(
        real, bundle, "qubit-achievements", "index.js"), (
        "a real bundle read through a resolved path constant was rejected")


def test_drift_fixtures_resolve_with_real_linkage(runner):
    """The achievements/dashboard drift fixtures carry a real linkage.

    ``drift.achievements.share`` is disposed by ``plugin_bundle_linkage`` (the
    compiled achievements bundle read by a real Python test);
    ``drift.plugins.catalog`` is disposed by a real linked Python test. Both are
    in the manifest and neither is OPEN.
    """
    entries = _manifest_fixture_entries()
    assert entries is not None, "no fixture manifest found"
    ids = {str(entry["fixture_id"]) for entry in entries}
    assert {"drift.achievements.share", "drift.plugins.catalog"} <= ids, (
        f"drift fixtures missing from the manifest: {sorted(ids)!r}")

    results = runner.run_inventory([], FIXTURES_DIR, adapters={})
    by_id = {str(r.get("capability_id")): r for r in results}

    achievements = by_id["drift.achievements.share"]
    assert achievements["disposition"] == "SAFE_EQUIVALENT"
    assert achievements.get("proof_level") == "wiring"
    assert achievements.get("proof_kind") == "plugin_bundle_linkage"
    bundle = REPO_ROOT / str(achievements.get("plugin_bundle_ref"))
    assert bundle.is_file()
    assert achievements.get("plugin_bundle_hash") == "sha256:" + hashlib.sha256(
        bundle.read_bytes()).hexdigest()

    catalog = by_id["drift.plugins.catalog"]
    assert catalog["disposition"] == "SAFE_EQUIVALENT"
    assert catalog.get("proof_level") == "wiring"
    assert any(runner.resolves_test_id(t) for t in (catalog.get("test_ids") or [])), (
        f"catalog fixture cites no resolvable test: {catalog.get('test_ids')!r}")


def test_no_false_disposal_for_terminal_rows(runner, tmp_path):
    """The four terminal ``voicebridge/terminal.py:main`` rows are not made
    SAFE_EQUIVALENT by the ledger-ingestion test.

    Each declares OPERATOR_ONLY (terminal) with an interface-level
    SAFE_EQUIVALENT claim. The pre-hardening resolver treated ``gap_ledger.main``
    in ``tests/test_ledger_ingestion_wave3.py`` as proof and flipped all four to
    SAFE_EQUIVALENT; a generic-symbol collision must not dispose any of them.
    """
    rows = [
        _openclaw_delta_row(
            cid, disposition="OPERATOR_ONLY",
            interface_disposition="SAFE_EQUIVALENT",
            qubit_path=_TERMINAL_QUBIT_PATH,
            qubit_symbol=_TERMINAL_QUBIT_SYMBOL)
        for cid in _TERMINAL_LEDGER_ROWS
    ]
    path = tmp_path / "terminal_rows.json"
    path.write_text(
        json.dumps({"schema_version": 1, "rows": rows}), encoding="utf-8")
    loaded = runner.load_inventory(path)
    assert {r["capability_id"] for r in loaded} == set(_TERMINAL_LEDGER_ROWS), (
        f"loader dropped or added rows: {sorted(r['capability_id'] for r in loaded)!r}")
    by_id = {r["capability_id"]: r for r in loaded}

    for cid in _TERMINAL_LEDGER_ROWS:
        resolved = by_id[cid]
        assert resolved["disposition"] != "SAFE_EQUIVALENT", (
            f"{cid} was falsely disposed via the ledger-ingestion test: "
            f"disposition={resolved['disposition']!r} "
            f"test_ids={resolved.get('test_ids')!r}")
        cited = [str(t) for t in (resolved.get("test_ids") or [])]
        assert not any(_LEDGER_INGESTION_TEST_FILE in t for t in cited), (
            f"{cid} cites the unrelated ledger-ingestion test: {cited!r}")

    gate = _undisposed_gate(runner, loaded)
    flagged = set(gate.get("rows") or [])
    for cid in _TERMINAL_LEDGER_ROWS:
        assert cid in flagged, (
            f"{cid} was not counted undisposed: {sorted(flagged)!r}")

    # The whole-file substring tie is rejected at the predicate level too: the
    # ledger-ingestion file's docstring contains the English word ``terminal``
    # and it calls ``gap_ledger.main``, but it never imports
    # ``voicebridge.terminal``.
    terminal_row = _openclaw_delta_row(
        "dashboard.route.terminal.predicate8u", qubit_path=_TERMINAL_QUBIT_PATH,
        qubit_symbol=_TERMINAL_QUBIT_SYMBOL, dashboard_route="/terminal",
        request={"kind": "ui-route", "path": "/terminal", "routeId": "terminal"})
    assert not runner._test_references_row(
        f"{_LEDGER_INGESTION_TEST_FILE}::test_omissions_summary_in_json_output",
        terminal_row), (
        "a docstring substring / bare `main` attribute was accepted as proof "
        "for voicebridge/terminal.py:main")
    assert runner._ast_wiring_test_ids(terminal_row) == [], (
        "the AST wiring resolver still cites an unrelated test for terminal.main")


def test_declared_substring_citation_is_not_proof(runner, tmp_path):
    """A row that DECLARES the ledger-ingestion test id is still refused.

    The four fabricated rows carried no declared test id, but a raw generation
    (or a hand-edit) could cite ``test_omissions_summary_in_json_output``
    directly. Even then the citation is not proof for
    ``voicebridge/terminal.py:main`` (the file never imports the module and
    ``main`` is generic), so the row is downgraded to OPEN with
    ``wiring_proof_status="no_row_linkage"`` -- never a fabricated
    SAFE_EQUIVALENT.
    """
    cited = (
        f"{_LEDGER_INGESTION_TEST_FILE}::test_omissions_summary_in_json_output")
    rows = [
        _openclaw_delta_row(
            cid, disposition="SAFE_EQUIVALENT",
            interface_disposition="SAFE_EQUIVALENT",
            qubit_path=_TERMINAL_QUBIT_PATH,
            qubit_symbol=_TERMINAL_QUBIT_SYMBOL,
            dashboard_route="/terminal",
            test_ids=[cited], trace_refs=[cited])
        for cid in _TERMINAL_LEDGER_ROWS
    ]
    path = tmp_path / "declared_terminal_rows.json"
    path.write_text(
        json.dumps({"schema_version": 1, "rows": rows}), encoding="utf-8")
    loaded = runner.load_inventory(path)
    assert {r["capability_id"] for r in loaded} == set(_TERMINAL_LEDGER_ROWS)
    for resolved in loaded:
        assert resolved["disposition"] == "OPEN", (
            f"{resolved['capability_id']} kept a fabricated SAFE_EQUIVALENT "
            f"claim: {resolved['disposition']!r}")
        assert resolved.get("wiring_proof_status") == "no_row_linkage", (
            f"{resolved['capability_id']} status="
            f"{resolved.get('wiring_proof_status')!r}")
        assert not resolved.get("test_ids"), (
            f"{resolved['capability_id']} kept the fabricated citation: "
            f"{resolved.get('test_ids')!r}")


# ===========================================================================
# Gate distinctness, route-level linkage and hermetic inventory (wave 8u
# verifier follow-up)
#
# Three regression surfaces:
#   * GATE_NAMES must not carry two gates with the same trigger condition (the
#     historical `proof_level` / `safe_equivalent_proof_level` pair was a
#     literal duplicate).
#   * A class-level surface test (one that iterates a constant route table)
#     must not dispose an individual route row unless the row's route/symbol is
#     a member of what the cited test actually exercises; membership disposes.
#   * An inventory directory scan must ingest only canonical SOURCE artifacts:
#     two runs over the same sources are byte-identical, and a stray
#     REPORT.json / `_pre_*` scratch dir never changes the merged inventory.
# Failures here are runner mismatches to report, never a reason to weaken the
# assertion.
# ===========================================================================

# A real class-level surface test that iterates the constant ``NEW_ROUTES``
# table: it exercises every route in that table and nothing else.
_ROUTE_TABLE_SURFACE_TEST = (
    "tests/test_qubit_read_routes8u.py"
    "::test_all_new_routes_dispatch_typed_never_404_500")
# A real surface test that iterates a DIFFERENT constant route set; its file
# carries none of the row's route/symbol/module tokens.
_OTHER_SURFACE_TEST = (
    "tests/test_qbpv2_rebrand_public_surface.py"
    "::test_public_route_table_has_no_upstream_names")
# Route/handler the first surface test really iterates (both are members of
# ``NEW_ROUTES``).
_MEMBER_ROUTE = "/api/cron/jobs/1/runs"
_MEMBER_SYMBOL = "h_cron_job_runs"


def _gate_scenarios():
    """Deterministic ``(rows, results)`` battery that fires each gate once.

    The battery is the behavioural oracle for "two gates have the same trigger
    condition": a duplicate pair produces an identical membership vector across
    every scenario. Each scenario is crafted so its gate fires, and so the
    other gates stay out unless they are genuinely part of the same input.
    """
    scenarios: list[tuple[list, list]] = []

    # clean: no gate fires.
    clean = _row(capability_id="gate.clean")
    scenarios.append(([clean], [_result(clean)]))

    # unexecuted_row: an unexecuted SAFE_EQUIVALENT claim with a wireproof.
    row = _row(
        capability_id="gate.unexecuted", disposition="SAFE_EQUIVALENT",
        qubit_path="voicebridge/terminal.py:374", qubit_symbol="build_parser",
        test_ids=[_ROUTE_TABLE_SURFACE_TEST],
        trace_refs=[_ROUTE_TABLE_SURFACE_TEST])
    scenarios.append(([row], [_result(
        row, disposition="SAFE_EQUIVALENT",
        declared_disposition="SAFE_EQUIVALENT", executed=False,
        fixture_present=False, reference_executed=True,
        proof_level="wiring")]))

    # missing_production_caller: a proven PASS with no caller mapping.
    row = _row(capability_id="gate.nocaller", qubit_path=None,
               qubit_symbol=None)
    scenarios.append(([row], [_result(row, production_caller=None)]))

    # skipped_fixture: a fixture is present but the row was never executed.
    row = _row(capability_id="gate.skipped", disposition="FAIL",
               fixture="tests/fixtures/differential/cli/x.json")
    scenarios.append(([row], [_result(
        row, disposition="FAIL", declared_disposition="FAIL",
        fixture_present=True, executed=False, reference_executed=False)]))

    # mock_replaced_real_path.
    row = _row(capability_id="gate.mock")
    scenarios.append(([row], [_result(row, adapter_mock=True)]))

    # refusal_counted_as_success.
    row = _row(capability_id="gate.refusal")
    scenarios.append(([row], [_result(row, refused=True)]))

    # unsafe_normalizer.
    row = _row(capability_id="gate.unsafe", disposition="OPERATOR_ONLY")
    scenarios.append(([row], [_result(
        row, disposition="OPERATOR_ONLY",
        declared_disposition="OPERATOR_ONLY",
        unsafe_normalizers=["timestamp"])]))

    # visual_only_control.
    row = _row(capability_id="gate.visual")
    scenarios.append(([row], [_result(row, visual_only=True)]))

    # voice_backend_mismatch.
    row = _row(capability_id="gate.voice", disposition="OPERATOR_ONLY")
    scenarios.append(([row], [_result(
        row, disposition="OPERATOR_ONLY", declared_disposition="OPERATOR_ONLY",
        voice_mismatch=True)]))

    # persistence_or_recovery_diff.
    row = _row(capability_id="gate.persistence")
    scenarios.append(([row], [_result(row, persistence_diff=True)]))

    # secret_in_artifact.
    row = _row(capability_id="gate.secret", disposition="OPERATOR_ONLY")
    scenarios.append(([row], [_result(
        row, disposition="OPERATOR_ONLY", declared_disposition="OPERATOR_ONLY",
        secret_in_artifact=True)]))

    # openclaw_undisposed_addition: a delta row with no Qubit disposition/proof.
    row = _row(
        capability_id="gate.ocundisposed", upstream_project="openclaw",
        upstream_commit="43286efa75666f29030bdd9c93873410698dc79c",
        delta_role="new_in_latest_main", qubit_path=None, qubit_symbol=None,
        disposition="FAIL", test_ids=[], trace_refs=[])
    scenarios.append(([row], [_result(
        row, disposition="FAIL", declared_disposition="FAIL",
        production_caller=None, executed=False, fixture_present=False,
        reference_executed=False)]))

    # safe_equivalent_requires_proof: mapping present, no recon proof key.
    row = _row(capability_id="gate.seproof", disposition="SAFE_EQUIVALENT",
               test_ids=[], trace_refs=[])
    scenarios.append(([row], [_result(
        row, disposition="SAFE_EQUIVALENT",
        declared_disposition="SAFE_EQUIVALENT", executed=True,
        qubit_result_hash="sha256:" + "1" * 64, reference_executed=True,
        proof_level="executed")]))

    # proof_level: an explicit but unknown level with a resolvable citation.
    row = _row(capability_id="gate.level", disposition="SAFE_EQUIVALENT",
               test_ids=[_ROUTE_TABLE_SURFACE_TEST],
               trace_refs=[_ROUTE_TABLE_SURFACE_TEST])
    scenarios.append(([row], [_result(
        row, disposition="SAFE_EQUIVALENT",
        declared_disposition="SAFE_EQUIVALENT", executed=True,
        qubit_result_hash="sha256:" + "1" * 64, reference_executed=True,
        proof_level="bogus")]))

    # proof_level_requires_resolvable_test_id: wiring level, dead citation.
    row = _row(capability_id="gate.resolvable", disposition="SAFE_EQUIVALENT",
               test_ids=["tests/nope.py::test_nope"], trace_refs=[])
    scenarios.append(([row], [_result(
        row, disposition="SAFE_EQUIVALENT",
        declared_disposition="SAFE_EQUIVALENT", executed=True,
        qubit_result_hash="sha256:" + "1" * 64, reference_executed=True,
        proof_level="wiring")]))

    # unexecuted_open_row.
    row = _row(capability_id="gate.open", disposition="OPEN", test_ids=[],
               trace_refs=[])
    scenarios.append(([row], [_result(
        row, disposition="OPEN", declared_disposition="OPEN", executed=False,
        reference_executed=False)]))

    # pass_without_reference.
    row = _row(capability_id="gate.noref")
    scenarios.append(([row], [_result(row, reference_executed=False)]))

    # pass_without_proof only: reference ran but no Qubit result hash.
    row = _row(capability_id="gate.noproof")
    scenarios.append(([row], [_result(
        row, reference_executed=True, qubit_result_hash=None)]))

    # unpinned_reference_pass: an executed reference that is not the pin.
    row = _row(capability_id="gate.unpinned")
    scenarios.append(([row], [_result(row, unpinned_references=["hermes"])]))

    # declaration_integrity: a typed declaration with no prerequisite/basis.
    row = _row(capability_id="gate.declaration", disposition="OPERATOR_ONLY",
               declared_by="fixture_declaration", operator_prerequisite=None,
               disposition_basis=None)
    scenarios.append(([row], [_result(
        row, disposition="OPERATOR_ONLY",
        declared_disposition="OPERATOR_ONLY", declared_by="fixture_declaration",
        operator_prerequisite=None, disposition_basis=None)]))

    # inventory_coverage: an input row with no result object at all.
    row = _row(capability_id="gate.coverage", disposition="OPERATOR_ONLY")
    scenarios.append(([row], []))

    return scenarios


def _gate_membership_vectors(runner_mod):
    """``gate -> tuple`` of the row ids it flags across the battery."""
    gates = tuple(runner_mod.GATE_NAMES)
    vectors = {name: [] for name in gates}
    for rows, results in _gate_scenarios():
        returned = runner_mod.apply_fail_closed_gates(rows, results)
        by_name = {g.get("gate"): g for g in returned}
        for name in gates:
            assert name in by_name, (
                f"apply_fail_closed_gates did not emit gate {name!r}")
            vectors[name].append(tuple(sorted(by_name[name].get("rows") or [])))
    return vectors


def test_gate_names_have_no_duplicate(runner):
    """Every gate trigger condition is distinct; no duplicate pair survives.

    Two gates with the same trigger condition flag the same rows for every
    input, so their membership vectors across the battery are identical. The
    historical ``proof_level`` / ``safe_equivalent_proof_level`` pair tripped
    exactly this check.
    """
    gates = tuple(getattr(runner, "GATE_NAMES", ()))
    assert gates, "GATE_NAMES is empty"
    assert len(gates) == len(set(gates)), (
        f"GATE_NAMES repeats a name: {sorted(gates)}")

    vectors = _gate_membership_vectors(runner)
    duplicates = []
    for index, first in enumerate(gates):
        for second in gates[index + 1:]:
            if vectors[first] == vectors[second]:
                duplicates.append((first, second))
    assert not duplicates, (
        "gates share an identical trigger condition (duplicate pair): "
        f"{duplicates}")

    # A manifest-level gate is legitimately never row-triggered; every other
    # gate must actually fire somewhere in the battery.
    never = [name for name in gates
             if all(not members for members in vectors[name])]
    assert never in ([], ["byte_parity"]), (
        f"gates never fired by the battery: {never}")


def test_class_level_surface_test_does_not_dispose_specific_row(
        runner, tmp_path):
    """A cited surface test is not proof for a route it does not carry.

    ``tests/test_qubit_read_routes8u.py::test_all_new_routes_dispatch_typed_
    never_404_500`` iterates ``NEW_ROUTES``; the cited
    ``test_public_route_table_has_no_upstream_names`` iterates a different
    constant route set that carries none of the row's route/symbol/module. The
    row must stay OPEN with ``wiring_proof_status == "no_row_linkage"`` and no
    invented citation.
    """
    cid = "dashboard.control_source.run_transcript.nomember8u"
    row = _openclaw_delta_row(
        cid, disposition="SAFE_EQUIVALENT",
        qubit_path="voicebridge/qubit_compat.py:1000",
        qubit_symbol=_MEMBER_SYMBOL,
        dashboard_route=_MEMBER_ROUTE,
        request={"method": "GET", "path": _MEMBER_ROUTE},
        test_ids=[_OTHER_SURFACE_TEST], trace_refs=[_OTHER_SURFACE_TEST],
        interface_disposition="SAFE_EQUIVALENT")

    resolved = _load_single(runner, tmp_path, row)
    assert resolved["disposition"] == "OPEN", (
        "a class-level surface test that does not iterate the row's route "
        "disposed it: "
        f"disposition={resolved['disposition']!r} "
        f"test_ids={resolved.get('test_ids')!r}")
    assert resolved.get("wiring_proof_status") == "no_row_linkage", (
        "the refused citation was not recorded as no_row_linkage: "
        f"{resolved.get('wiring_proof_status')!r}")
    assert not [t for t in (resolved.get("test_ids") or [])
                if _OTHER_SURFACE_TEST in str(t)], (
        "the unlinked surface test was still cited as proof: "
        f"{resolved.get('test_ids')!r}")

    gate = _undisposed_gate(runner, [resolved])
    assert gate["ok"] is False, (
        "the no_row_linkage row was counted as disposed (gate green)")
    assert cid in (gate.get("rows") or []), (
        f"{cid} was not named as undisposed: {gate!r}")


def test_route_membership_disposes_row(runner, tmp_path):
    """The same route row is disposed when the cited test iterates the route.

    The cited test iterates ``NEW_ROUTES``, which contains both the row's route
    path and its handler symbol, so the wiring proof is a real route-level
    linkage and the row is disposed SAFE_EQUIVALENT.
    """
    cid = "dashboard.control_source.run_transcript.member8u"
    row = _openclaw_delta_row(
        cid, disposition="SAFE_EQUIVALENT",
        qubit_path="voicebridge/qubit_compat.py:1000",
        qubit_symbol=_MEMBER_SYMBOL,
        dashboard_route=_MEMBER_ROUTE,
        request={"method": "GET", "path": _MEMBER_ROUTE},
        test_ids=[_ROUTE_TABLE_SURFACE_TEST],
        trace_refs=[_ROUTE_TABLE_SURFACE_TEST],
        interface_disposition="SAFE_EQUIVALENT")

    resolved = _load_single(runner, tmp_path, row)
    assert resolved["disposition"] == "SAFE_EQUIVALENT", (
        "a route row whose cited surface test iterates its route was not "
        "disposed: "
        f"disposition={resolved['disposition']!r} "
        f"status={resolved.get('wiring_proof_status')!r}")
    assert resolved.get("proof_level") == "wiring", (
        f"linked row proof_level={resolved.get('proof_level')!r}")
    cited = [str(t) for t in (resolved.get("test_ids") or [])]
    assert any(_ROUTE_TABLE_SURFACE_TEST in t for t in cited), (
        f"the iterating surface test was not cited as proof: {cited!r}")
    assert all(runner.resolves_test_id(t) for t in cited), cited

    gate = _undisposed_gate(runner, [resolved])
    assert cid not in (gate.get("rows") or []), (
        f"a route-membership-proven row was still flagged undisposed: {gate!r}")


def test_gate_row_proof_requires_typed_denial_code(runner, tmp_path):
    """A security-gate row is not disposed by an unrelated route mention.

    ``tests/test_dashboard_families8u.py`` only lists ``/api/credentials/pool``
    in a console-error suppression allowlist -- it never exercises the gate.
    The engine must refuse that as proof. A test that names the gate's typed
    denial code (``SECRET_NEVER_SERVED``) is a real gate proof.
    """
    weak = (
        "tests/test_dashboard_families8u.py"
        "::test_family_routes_render_qubit_shell")
    strong = (
        "tests/test_qubit_routes_parity8u.py"
        "::test_every_gated_route_is_typed")
    gate = {
        "command": "GET /api/credentials/pool",
        "qubit_symbol": "__gate__",
        "error_codes": "SECRET_NEVER_SERVED",
    }
    assert not runner._test_references_row(weak, gate), (
        "a route string that appears only in an error-suppression allowlist "
        "was accepted as gate proof")
    assert runner._test_references_row(strong, gate), (
        "a test that names the gate's typed denial code was rejected")

    cid = "api.route.get-credentials-pool.gateproof8u"
    row = _row(capability_id=cid, family="api",
               disposition="SAFE_EQUIVALENT", command=gate["command"],
               qubit_symbol="__gate__", error_codes="SECRET_NEVER_SERVED",
               test_ids=[weak], trace_refs=[weak])
    resolved = _load_single(runner, tmp_path, row)
    assert resolved["disposition"] != "SAFE_EQUIVALENT", (
        "a gate row backed only by an allowlist route mention stayed "
        f"SAFE_EQUIVALENT: {resolved['disposition']!r}")
    cited = [str(t) for t in (resolved.get("test_ids") or [])]
    assert not any(weak in t for t in cited), (
        f"the non-exercising allowlist test was cited as proof: {cited!r}")

    strong_row = dict(row, test_ids=[strong], trace_refs=[strong])
    resolved2 = _load_single(runner, tmp_path, strong_row)
    assert resolved2["disposition"] == "SAFE_EQUIVALENT", (
        "a gate row whose test names the typed denial code was not disposed: "
        f"{resolved2['disposition']!r} status={resolved2.get('wiring_proof_status')!r}")
    assert resolved2.get("proof_level") == "wiring", (
        f"linked gate row proof_level={resolved2.get('proof_level')!r}")


def test_credentials_pool_get_route_is_gated(runner):
    """SECURITY: GET /api/credentials/pool resolves to a typed gate.

    The differential row for this route is a gate row; the production compat
    router must never proxy the GET to the vendored fork (which returns a live
    ``token_preview``). This anchors the gate-row proof rule above to the real
    gated route.
    """
    from voicebridge import qubit_compat as HC

    route, _ = HC.match("GET", "/api/credentials/pool")
    assert route is not None, (
        "GET /api/credentials/pool is not a registered route")
    assert route.get("disposition") == "gated", (
        f"GET /api/credentials/pool is not gated: {route.get('disposition')!r}")
    assert (route.get("gate") or {}).get("code") == "SECRET_NEVER_SERVED", (
        f"unexpected gate code for GET /api/credentials/pool: {route.get('gate')!r}")


def test_inventory_is_hermetic(runner, tmp_path):
    """Same sources -> byte-identical inventory; strays never change it.

    Two runs over one scan tree must be byte-identical, and a stray
    ``REPORT.json`` or ``_pre_*`` scratch generation carrying extra/shadowing
    rows must not change the merged inventory (only canonical source artifacts
    are ingested).
    """
    source = tmp_path / "scan"
    source.mkdir()
    canonical = {
        "schema_version": 1,
        "rows": [
            {"capability_id": "cli.hermetic.alpha", "family": "cli",
             "disposition": "OPEN"},
            {"capability_id": "cli.hermetic.beta", "family": "cli",
             "disposition": "OPEN"},
        ],
    }
    (source / "hermes_capabilities.json").write_text(
        json.dumps(canonical), encoding="utf-8")

    baseline = _canonical(runner.load_inventory(source))
    assert _canonical(runner.load_inventory(source)) == baseline, (
        "two in-process inventory generations differ")

    out_a = tmp_path / "inventory-a.json"
    out_b = tmp_path / "inventory-b.json"
    first = _run_cli("inventory", "--source", str(source), "--out", str(out_a))
    second = _run_cli("inventory", "--source", str(source), "--out", str(out_b))
    assert first.returncode == 0, (first.returncode, first.stderr[-2000:])
    assert second.returncode == 0, (second.returncode, second.stderr[-2000:])
    assert out_a.read_bytes() == out_b.read_bytes(), (
        "two CLI inventory generations over the same sources differ")

    # Stray scratch + report generations must be ignored outright.
    scratch = source / "_pre_retriage"
    scratch.mkdir()
    (scratch / "openclaw_delta_latest.json").write_text(json.dumps({
        "schema_version": 1,
        "rows": [
            {"capability_id": "cli.hermetic.gamma", "family": "cli",
             "disposition": "OPEN"},
            # Shadows the canonical row with a different disposition.
            {"capability_id": "cli.hermetic.alpha", "family": "cli",
             "disposition": "SAFE_EQUIVALENT"},
        ],
    }), encoding="utf-8")
    (source / "REPORT.json").write_text(json.dumps({
        "schema_version": 1,
        "rows": [{"capability_id": "cli.hermetic.delta", "family": "cli",
                  "disposition": "OPEN"}],
    }), encoding="utf-8")

    after = runner.load_inventory(source)
    assert _canonical(after) == baseline, (
        "a stray REPORT.json/_pre_* generation changed the merged inventory")
    ids = {r["capability_id"] for r in after}
    assert ids == {"cli.hermetic.alpha", "cli.hermetic.beta"}, (
        f"stray rows leaked into the inventory: {sorted(ids)}")

    out_c = tmp_path / "inventory-c.json"
    third = _run_cli("inventory", "--source", str(source), "--out", str(out_c))
    assert third.returncode == 0, (third.returncode, third.stderr[-2000:])
    assert out_c.read_bytes() == out_a.read_bytes(), (
        "a stray REPORT.json/_pre_* generation changed the CLI inventory")


def test_inventory_allowlist_rejects_stray_json(runner, tmp_path):
    """A directory scan ingests ONLY the explicit canonical allowlist.

    The loader is an allowlist, not a marker blocklist: a stray JSON with an
    innocuous name (``stray.json``), or one parked under a non-canonical
    subdirectory (``qubit/evidence.json``), can never shift the merged counts
    or shadow a canonical row.
    """
    assert tuple(runner.CANONICAL_INVENTORY_INPUTS) == (
        "hermes_capabilities.json",
        "hermes_delta_latest.json",
        "openclaw_capabilities.json",
        "openclaw_delta_latest.json",
        "qubit/qubit_backend.json",
        "qubit_transport/qubit_transport_surface.json",
        "dispositions/OPENCLAW_DELTA_RETRIAGE.json",
        "dispositions/openclaw_delta_dispositions.json",
        "dispositions/OPENCLAW_SCANNER_EXCLUSIONS.json",
    ), "the canonical inventory allowlist drifted from the 9 documented inputs"

    source = tmp_path / "scan"
    source.mkdir()
    canonical_path = source / "openclaw_delta_latest.json"
    canonical_path.write_text(json.dumps({
        "schema_version": 1,
        "rows": [{"capability_id": "cli.allowlist.alpha", "family": "cli",
                  "disposition": "OPEN"}],
    }), encoding="utf-8")

    # A stray root JSON and a stray nested JSON, both of which the old
    # blocklist heuristic would not have excluded.
    (source / "stray.json").write_text(json.dumps({
        "schema_version": 1,
        "rows": [
            {"capability_id": "cli.allowlist.stray", "family": "cli",
             "disposition": "OPEN"},
            {"capability_id": "cli.allowlist.alpha", "family": "cli",
             "disposition": "SAFE_EQUIVALENT"},
        ],
    }), encoding="utf-8")
    (source / "qubit").mkdir()
    (source / "qubit" / "evidence.json").write_text(json.dumps({
        "schema_version": 1,
        "rows": [{"capability_id": "cli.allowlist.evidence", "family": "cli",
                  "disposition": "OPEN"}],
    }), encoding="utf-8")

    picked = runner._inventory_scan_files(source)
    assert picked == [canonical_path], (
        f"a directory scan picked non-allowlisted files: "
        f"{[str(p.relative_to(source)) for p in picked]!r}")

    rows = runner.load_inventory(source)
    ids = {r["capability_id"] for r in rows}
    assert ids == {"cli.allowlist.alpha"}, (
        f"stray JSON leaked into the inventory: {sorted(ids)!r}")
    assert rows[0]["disposition"] == "OPEN", (
        "a stray JSON shadowed the canonical row's disposition")

    # Determinism: two scans over the same sources are byte-identical.
    assert _canonical(runner.load_inventory(source)) == _canonical(
        runner.load_inventory(source))


# =========================================================================== #
# B-wave closure: vendor-parity adapter (B1), typed declarations (B2/B3/B5/B7),
# operator holds, and OpenClaw scanner hygiene (B9). All hermetic.
# =========================================================================== #
def test_vendor_parity_adapter_executes_vendored_module(runner):
    """B1: the adapter REALLY imports a vendored module via ensure_path()."""
    adapter = runner.VendorParityAdapter()
    assert adapter.name == runner.VENDOR_TRANSPORT
    assert adapter.available() is True, "vendored Hermes fork root did not resolve"
    assert adapter.fork_root() is not None

    first = adapter.probe_vendored("qubit_constants", "get_qubit_home")
    assert first.get("imported") is True, first
    assert str(first.get("names_hash", "")).startswith("sha256:"), first
    assert first.get("public_names"), "vendored module reported no public names"
    assert first.get("symbol_kind") in ("function", "class", "constant"), first
    second = adapter.probe_vendored("qubit_constants", "get_qubit_home")
    assert first == second, "vendored behavior probe is not deterministic"


def test_vendor_parity_requires_reference_root(runner, monkeypatch):
    """B1: no pinned reference root -> fail-closed (never a success claim)."""
    monkeypatch.delenv(runner.DEFAULT_VENDOR_REF_ROOT_ENV, raising=False)
    adapter = runner.VendorParityAdapter()
    assert adapter.available() is True
    with pytest.raises(runner.TransportUnavailable):
        adapter.execute({"kind": "vendor", "module": "qubit_constants"})


def test_vendor_parity_compares_behavior_not_bytes(runner, monkeypatch):
    """B1: behavior comparison drives the verdict, not a byte count."""
    adapter = runner.VendorParityAdapter(reference_root="/tmp")
    equal_probe = {"module": "m", "imported": True, "names_hash": "sha256:" + "a" * 64}
    monkeypatch.setattr(adapter, "_probe", lambda mode, module, symbol, root: dict(equal_probe))
    result = adapter.execute({"kind": "vendor", "module": "m"})
    assert result.meta.get("reference_executed") is True
    assert result.meta.get("behavior_equal") is True
    assert result.exit_code == 0
    assert result.json["behavior_equal"] is True

    def divergent(mode, module, symbol, root):
        payload = dict(equal_probe)
        payload["names_hash"] = "sha256:" + ("b" if mode == "reference" else "a") * 64
        return payload

    monkeypatch.setattr(adapter, "_probe", divergent)
    result = adapter.execute({"kind": "vendor", "module": "m"})
    assert result.meta.get("behavior_equal") is False
    assert result.exit_code == 1, "a behavioral difference must not be exit 0"
    assert result.meta.get("reference_hash") != result.meta.get("qubit_hash")


def test_vendor_parity_real_reference_comparison(runner, tmp_path):
    """B1: an end-to-end behavioral comparison against a real reference root."""
    import shutil
    vendored = REPO_ROOT / "qubit_runtime" / "qubit_constants.py"
    if not vendored.is_file():
        pytest.skip("vendored qubit_constants.py is absent")
    reference = tmp_path / "ref"
    reference.mkdir()
    shutil.copy(vendored, reference / "qubit_constants.py")
    adapter = runner.VendorParityAdapter(reference_root=str(reference))
    result = adapter.execute(
        {"kind": "vendor", "module": "qubit_constants", "symbol": "get_qubit_home"})
    assert result.exit_code == 0
    assert result.meta["behavior_equal"] is True
    assert result.meta["reference_executed"] is True
    assert result.meta["reference_hash"] == result.meta["qubit_hash"], (
        "a byte-identical reference must yield the same behavioral hash")

    # Mutating the reference's PUBLIC surface must be detected as a difference.
    extra = reference / "qubit_constants.py"
    extra.write_text(vendored.read_text(encoding="utf-8")
                     + "\n\ndef _extra_probe_marker():\n    return 1\n",
                     encoding="utf-8")
    mutated = adapter.execute(
        {"kind": "vendor", "module": "qubit_constants", "symbol": "get_qubit_home"})
    assert mutated.meta["behavior_equal"] is False
    assert mutated.exit_code == 1
    assert mutated.meta["reference_hash"] != mutated.meta["qubit_hash"]


# =========================================================================== #
# B1 closure: vendor BEHAVIOR proof for vendored Hermes source rows.
#
# A row whose `qubit_path` maps a `qubit_runtime/**` module is disposed
# SAFE_EQUIVALENT (proof_level=wiring) ONLY when the vendored module imports and
# its behavioral descriptor equals the pinned reference. A byte/descriptor match
# alone is NOT a PASS; a failed import or an unresolved reference leaves the row
# OPEN. The adapter is faked here so the tests stay hermetic (the real pinned
# comparison is covered by test_vendor_parity_real_reference_comparison).
# =========================================================================== #
def _vendor_descriptor(module, symbol=None, **over):
    desc = {
        "module": module,
        "symbol": symbol,
        "imported": True,
        "file": f"{module}.py",
        "source_hash": "sha256:" + "a" * 64,
        "public_names": ["public_a", "public_b"],
        "names_hash": "sha256:" + "b" * 64,
    }
    if symbol:
        desc["symbol_kind"] = "function"
        desc["signature_hash"] = "sha256:" + "d" * 64
    desc.update(over)
    return desc


def _faked_vendor_adapters(runner, monkeypatch, behavior_equal=True):
    adapter = runner.VendorParityAdapter()
    monkeypatch.setattr(adapter, "available", lambda: True)
    monkeypatch.setattr(
        adapter, "resolve_reference_root", lambda *a, **k: Path("/tmp/pinned-ref"))

    def fake_compare(specs, reference_root):
        out = {}
        for module, symbol in specs:
            que = _vendor_descriptor(module, symbol)
            ref = _vendor_descriptor(module, symbol)
            if not behavior_equal:
                ref["names_hash"] = "sha256:" + "c" * 64
                ref["public_names"] = ["public_a", "public_b", "extra"]
            out[f"{module}\x1f{symbol or ''}"] = {
                "vendored": que,
                "reference": ref,
                "behavior_equal": behavior_equal,
                "qubit_hash": runner.result_hash(que),
                "reference_hash": runner.result_hash(ref),
            }
        return out

    monkeypatch.setattr(adapter, "compare_specs", fake_compare)
    return {"vendor": adapter}


def _vendored_row(runner, **over):
    row = _row(
        capability_id="cli.module.qubit_constants.b1",
        family="cli", upstream_project="hermes", disposition="OPEN",
        fixture=None, test_ids=[], trace_refs=[],
        qubit_path="qubit_runtime/qubit_constants.py:1",
        qubit_symbol="qubit_constants.py",
    )
    row.update(over)
    return row


def test_vendor_module_spec_extraction(runner):
    assert runner._vendored_module_spec(
        {"qubit_path": "qubit_runtime/qubit_constants.py:1",
         "qubit_symbol": "qubit_constants.py"}) == ("qubit_constants", None)
    assert runner._vendored_module_spec(
        {"qubit_path": "qubit_runtime/agent/side_question.py:12",
         "qubit_symbol": "answer_side_question"}) == (
        "agent.side_question", "answer_side_question")
    assert runner._vendored_module_spec(
        {"qubit_path": "qubit_runtime/acp_adapter/__init__.py:1",
         "qubit_symbol": "acp_adapter/__init__.py"}) == ("acp_adapter", None)
    # A non-vendored path, a non-.py path or an absent path is never a spec.
    assert runner._vendored_module_spec(
        {"qubit_path": "voicebridge/cli.py:1", "qubit_symbol": "main"}) is None
    assert runner._vendored_module_spec(
        {"qubit_path": "frontend/qubit-web/src/pages/X.tsx:1",
         "qubit_symbol": "X"}) is None
    assert runner._vendored_module_spec({}) is None


def test_vendor_behavior_disposes_only_on_equal_descriptors(runner, monkeypatch):
    adapters = _faked_vendor_adapters(runner, monkeypatch, behavior_equal=True)
    row = _vendored_row(runner)
    result = runner.run_inventory(
        [row], FIXTURES_DIR, adapters=adapters, row_ids=[row["capability_id"]])[0]
    assert result["disposition"] == "SAFE_EQUIVALENT", result["disposition"]
    assert result["proof_level"] == "wiring"
    assert result["proof_kind"] == runner.VENDOR_PROOF
    assert result["executed"] is True and result["reference_executed"] is True
    assert result["qubit_result_hash"].startswith("sha256:")
    assert result["upstream_result_hash"].startswith("sha256:")
    assert result["reference_result_hash"] == result["upstream_result_hash"]
    gates = _gate_ok_map(runner.apply_fail_closed_gates([row], [result]))
    for name in ("unexecuted_row", "pass_without_reference", "proof_level",
                 "proof_level_requires_resolvable_test_id",
                 "safe_equivalent_requires_proof"):
        assert gates.get(name) is True, (name, gates.get(name))

    divergent = _faked_vendor_adapters(runner, monkeypatch, behavior_equal=False)
    result = runner.run_inventory(
        [row], FIXTURES_DIR, adapters=divergent,
        row_ids=[row["capability_id"]])[0]
    assert result["disposition"] == "OPEN", (
        "a diverging vendored module must never be disposed")
    assert result.get("proof_kind") != runner.VENDOR_PROOF


def test_vendor_behavior_requires_reference_root(runner, monkeypatch):
    """No resolved pinned reference root => the row stays OPEN (fail-closed)."""
    adapter = runner.VendorParityAdapter()
    monkeypatch.setattr(adapter, "available", lambda: True)
    monkeypatch.setattr(adapter, "resolve_reference_root", lambda *a, **k: None)
    monkeypatch.setattr(
        adapter, "compare_specs",
        lambda *a, **k: pytest.fail("compare must not run without a reference"))
    row = _vendored_row(runner)
    result = runner.run_inventory(
        [row], FIXTURES_DIR, adapters={"vendor": adapter},
        row_ids=[row["capability_id"]])[0]
    assert result["disposition"] == "OPEN", result["disposition"]


def test_vendor_behavior_never_passes_and_rejects_byte_only(runner, monkeypatch):
    """A vendor proof is wiring-level SAFE_EQUIVALENT, never PASS; and a result
    that claims vendor behavior without the reference evidence is rejected."""
    assert runner._vendor_behavior_valid(
        {"qubit_path": "x", "qubit_symbol": "y"},
        {"proof_kind": runner.VENDOR_PROOF,
         "qubit_result_hash": "sha256:" + "a" * 64,
         "upstream_result_hash": "sha256:" + "b" * 64,
         "vendor_reference": {"reference_executed": True, "behavior_equal": True,
                              "reference_hash": "sha256:" + "b" * 64,
                              "qubit_hash": "sha256:" + "a" * 64}}) is True
    # Byte identity alone (no vendor_reference / no behavior_equal) is not proof.
    assert runner._vendor_behavior_valid(
        {}, {"proof_kind": runner.VENDOR_PROOF,
             "qubit_result_hash": "sha256:" + "a" * 64,
             "upstream_result_hash": "sha256:" + "a" * 64}) is False
    assert runner._vendor_behavior_valid(
        {}, {"vendor_reference": {"reference_executed": True,
                                  "behavior_equal": False}}) is False

    # A symbol row whose symbol is absent from the module is not disposed.
    adapter = runner.VendorParityAdapter()
    monkeypatch.setattr(adapter, "available", lambda: True)
    monkeypatch.setattr(adapter, "resolve_reference_root",
                        lambda *a, **k: Path("/tmp/pinned-ref"))

    def compare(specs, reference_root):
        out = {}
        for module, symbol in specs:
            que = _vendor_descriptor(module, symbol, symbol_kind="missing",
                                     signature_hash=None)
            ref = _vendor_descriptor(module, symbol, symbol_kind="missing",
                                     signature_hash=None)
            out[f"{module}\x1f{symbol or ''}"] = {
                "vendored": que, "reference": ref, "behavior_equal": True,
                "qubit_hash": runner.result_hash(que),
                "reference_hash": runner.result_hash(ref)}
        return out

    monkeypatch.setattr(adapter, "compare_specs", compare)
    row = _vendored_row(
        runner, capability_id="cli.symbol.absent.b1",
        qubit_path="qubit_runtime/qubit_constants.py:1",
        qubit_symbol="not_a_real_symbol")
    result = runner.run_inventory(
        [row], FIXTURES_DIR, adapters={"vendor": adapter},
        row_ids=[row["capability_id"]])[0]
    assert result["disposition"] == "OPEN", (
        "a symbol the module does not export must not be disposed")


def test_declarations_never_manufacture_success(runner, tmp_path):
    """B2/B3: a declaration can only produce a non-success terminal hold."""
    payload = {
        "schema_version": 1,
        "declarations": [
            {"capability_id": "gateway.env.x.1", "disposition": "OPERATOR_ONLY",
             "operator_prerequisite": "operator-held secret", "basis": "E_operator",
             "declaration_class": "E_operator"},
            {"capability_id": "rpc.method.y.2", "disposition": "FAIL",
             "operator_prerequisite": "C6 owner policy", "basis": "B_missing"},
            {"capability_id": "bad.pass.3", "disposition": "PASS",
             "operator_prerequisite": "nope", "basis": "forged"},
            {"capability_id": "bad.se.4", "disposition": "SAFE_EQUIVALENT",
             "operator_prerequisite": "nope", "basis": "forged"},
        ],
    }
    (tmp_path / runner.DECLARATIONS_FILENAME).write_text(
        json.dumps(payload), encoding="utf-8")
    declarations = runner.load_declarations(tmp_path)
    assert set(declarations) == {"gateway.env.x.1", "rpc.method.y.2"}, (
        f"a declaration forged a success disposition: {sorted(declarations)}")
    rows = [
        {"capability_id": "gateway.env.x.1", "family": "gateway", "disposition": "OPEN"},
        {"capability_id": "rpc.method.y.2", "family": "rpc", "disposition": "OPEN"},
        # A terminal row must never be overwritten by a declaration.
        {"capability_id": "gateway.env.x.1", "family": "gateway",
         "disposition": "SAFE_EQUIVALENT"},
    ]
    declared = runner.apply_declarations(rows, declarations)
    assert declared[0]["disposition"] == "OPERATOR_ONLY"
    assert declared[0]["operator_prerequisite"] == "operator-held secret"
    assert declared[0]["declared_by"] == "fixture_declaration"
    assert declared[1]["disposition"] == "FAIL"
    assert declared[2]["disposition"] == "SAFE_EQUIVALENT", (
        "a declaration overwrote an already terminal row")


def test_declaration_integrity_gate_is_fail_closed(runner):
    """A typed declaration without a prerequisite/basis is refused by the gate."""
    good = _row(capability_id="gate.decl.good", disposition="OPERATOR_ONLY",
                declared_by="fixture_declaration",
                operator_prerequisite="operator-held secret",
                disposition_basis="E_operator")
    bad = _row(capability_id="gate.decl.bad", disposition="OPERATOR_ONLY",
               declared_by="fixture_declaration", operator_prerequisite=None,
               disposition_basis=None)
    gates = _gate_ok_map(runner.apply_fail_closed_gates(
        [good, bad], [_result(good, disposition="OPERATOR_ONLY"),
                      _result(bad, disposition="OPERATOR_ONLY")]))
    assert gates["declaration_integrity"] is False, (
        "a declaration without a prerequisite/basis must trip declaration_integrity")
    rows = next(g for g in runner.apply_fail_closed_gates(
        [good, bad], [_result(good, disposition="OPERATOR_ONLY"),
                      _result(bad, disposition="OPERATOR_ONLY")])
        if g["gate"] == "declaration_integrity")["rows"]
    assert rows == ["gate.decl.bad"], rows


def test_real_declarations_cover_e_operator_and_b_missing(runner):
    """B2/B3: the shipped declarations cover the 100 E_operator + 30 B_missing rows."""
    declarations = runner.load_declarations(FIXTURES_DIR)
    assert declarations, "DECLARATIONS.json is absent or empty"
    by_class: dict[str, int] = {}
    for entry in declarations.values():
        by_class[entry.get("declaration_class") or entry.get("class")] = (
            by_class.get(entry.get("declaration_class") or entry.get("class"), 0) + 1)
    assert by_class.get("E_operator") == 100, by_class
    assert by_class.get("B_missing") == 30, by_class
    for entry in declarations.values():
        assert str(entry["disposition"]).upper() in runner.DECLARED_DISPOSITIONS
        assert entry.get("operator_prerequisite"), entry


def test_operator_only_declarations_have_prerequisites(runner):
    """The operator-only declaration classes each carry a prerequisite and basis.

    A declaration can never manufacture success; every added operator-only row
    must say exactly what operator resource unblocks it.
    """
    declarations = runner.load_declarations(FIXTURES_DIR)
    assert declarations, "DECLARATIONS.json is absent or empty"
    classes = ("E_pairing", "E_oauth", "E_credential", "E_hardware", "E_estop")
    counts = {name: 0 for name in classes}
    for entry in declarations.values():
        cls = entry.get("declaration_class") or entry.get("class")
        if cls not in counts:
            continue
        counts[cls] += 1
        assert str(entry["disposition"]).upper() == "OPERATOR_ONLY", (cls, entry)
        assert entry.get("operator_prerequisite"), (cls, entry)
        assert entry.get("basis"), (cls, entry)
    assert all(counts[name] > 0 for name in classes), counts


def test_vendor_probe_signature_is_deterministic(runner):
    """The vendored descriptor must be byte-identical across probe processes.

    Regression guard for the signature nondeterminism bug: ``repr(frozenset)``
    iterates in per-process hash order and default-object reprs embed ASLR
    addresses, so a structural, canonical signature hash is required. No
    reference root is needed (vendored side only), so this is hermetic.
    """
    adapter = runner.VendorParityAdapter()
    if not adapter.available():
        pytest.skip("vendored Hermes fork root is absent")
    specs = [("qubit_cli.urllib_security", "SafeCredentialRedirectHandler")]
    first = adapter._probe_many("vendored", specs, None)
    first_desc = next(iter(first.values()), {})
    if not first_desc.get("imported"):
        pytest.skip("vendored probe module did not import in this environment")
    second = adapter._probe_many("vendored", specs, None)
    assert runner.canonical_json(first) == runner.canonical_json(second), (
        "vendored behavioral descriptor is not deterministic across probes")


def test_operator_hold_fixture_is_not_executed(runner, tmp_path):
    """B5/B7: an operator-hold fixture is terminal without any execution."""
    fixture = {
        "schema_version": 1,
        "fixture_id": "api.route.hold.probe",
        "family": "api",
        "transport": "http",
        "operator_hold": True,
        "operator_prerequisite": "A3 pending (locked product work)",
        "disposition": "OPERATOR_ONLY",
        "command": None,
        "request": None,
        "normalizers": [],
        "expected": {},
        "qubit": {"path": "voicebridge/qubit_compat.py:1", "caller": "GATED",
                  "approval": None},
        "hermes": {"path": "qubit_cli/web.py:1", "symbol": "route"},
        "openclaw": {"path": None, "symbol": None},
        "notes": "operator-hold probe",
    }
    fixture_path = tmp_path / "hold.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
    row = runner.synthesize_row_from_fixture(
        "api.route.hold.probe", fixture, fixture_path)
    calls = {"n": 0}

    class _Counter:
        name = "qubit"
        project = "qubit"

        def available(self):
            return True

        def execute(self, request):
            calls["n"] += 1
            return runner.CallResult(exit_code=0, stdout="should-not-run")

    result = runner.run_inventory([row], tmp_path, adapters={"qubit": _Counter()})[0]
    assert calls["n"] == 0, "an operator-hold fixture opened an execution path"
    assert result["disposition"] == "OPERATOR_ONLY"
    assert result["executed"] is False
    assert "A3 pending" in (result.get("operator_prerequisite") or "")


def test_b5_b7_fixtures_are_operator_holds(runner):
    """B5/B7: the shipped fixtures bind the exact rows as typed holds."""
    entries = _manifest_fixture_entries() or []
    by_id = {str(e["fixture_id"]): e for e in entries}
    b5_ids = [fid for fid in by_id if ".route." in fid and fid.startswith(("api.", "ws."))]
    assert len(b5_ids) >= 77, f"expected >=77 compat-handler fixtures, got {len(b5_ids)}"
    b7_ids = [
        "gateway.event.models.snapshot",
        "gateway.event.plugins.changed",
        "gateway.method.controlUi.sessionPullRequests.checks",
        "gateway.method.diagnostics.cpuProfile",
        "gateway.method.sessions.activitySummary.ensure",
        "provider.plugin.radius",
        "skill.bundled.visualize",
        "gateway.method.canvas.document.preview",
    ]
    for fid in b7_ids:
        assert fid in by_id, f"B7 fixture missing: {fid}"
        doc = json.loads(_manifest_fixture_path(by_id[fid]).read_text(encoding="utf-8"))
        assert doc.get("operator_hold") is True, fid
        assert str(doc.get("disposition")) == "OPERATOR_ONLY", fid
        assert doc.get("operator_prerequisite"), fid
        assert doc["qubit"]["path"], fid


def test_scanner_hygiene_excludes_test_support_modules(runner):
    """B9: *.test-support.ts / *.test-harness.ts rows are dropped, not "undisposed"."""
    assert runner._is_scanner_excluded({
        "upstream_path": "ui/src/components/x.test-support.ts:1"})
    assert runner._is_scanner_excluded({
        "upstream_path": "ui/src/components/x.test-harness.ts:1"})
    assert runner._is_scanner_excluded({"upstream_is_test_support": True})
    assert runner._is_scanner_excluded({"capability_id": "dashboard.control_source.page_test_support"})
    assert not runner._is_scanner_excluded({"upstream_path": "ui/src/components/x.ts:1"})


# =========================================================================== #
# v1.8 READ-ONLY ANALYST OPEN-CLOSURE OVERLAYS (wave 22 closure)
#
# The three analysts each audited the still-OPEN differential rows and emitted a
# manifest. The runner consumes frozen copies (``closure/*.json``) as a
# fail-closed overlay: a success claim becomes SAFE_EQUIVALENT ONLY with a
# genuinely row-linked resolvable test; everything else is a typed
# OPERATOR_ONLY hold (or FAIL for a recorded defect). Nothing is upgraded to a
# fake success and no manifest assertion is trusted on its own.
# =========================================================================== #
CLOSURE_DIR = FIXTURES_DIR / "closure"
CLOSURE_FILES = (
    "QUBIT_OPEN_CLOSURE.json",
    "HERMES_OPEN_CLOSURE.json",
    "OPENCLAW_OPEN_CLOSURE.json",
)
_LINKED_TEST = (
    "tests/test_provider_migration.py"
    "::test_run_role_refuses_non_executable_profile"
)


def _closure_row(cid, **overrides):
    row = {
        "capability_id": cid,
        "family": "api",
        "upstream_project": "qubit",
        "upstream_path": "voicebridge/x.py:1",
        "command": None,
        "disposition": "OPEN",
        "qubit_path": "voicebridge/x.py:1",
        "qubit_symbol": "apply_open_closures",
        "test_ids": [],
        "trace_refs": [],
    }
    row.update(overrides)
    return row


def _closure_manifest(tmp_path, name, row):
    doc = {"schema_version": 1, "rows": [row]}
    (tmp_path / name).write_text(json.dumps(doc), encoding="utf-8")


def test_shipped_closure_manifests_load_and_are_nonempty(runner):
    """Every frozen manifest loads with an exact disposition per row."""
    assert CLOSURE_DIR.is_dir(), "frozen closure manifests missing"
    for name in CLOSURE_FILES:
        assert (CLOSURE_DIR / name).is_file(), name
    closures = runner.load_open_closures(CLOSURE_DIR)
    assert len(closures) >= 3000, f"closure overlay too small: {len(closures)}"
    sources = {entry["closure_source"] for entry in closures.values()}
    assert sources == set(CLOSURE_FILES)
    for cid, entry in closures.items():
        assert entry["capability_id"] == cid
        assert entry["disposition"], cid
        assert isinstance(entry["tests"], list)
    assert "QUBIT_OPEN_CLOSURE.json" in sources


def test_closure_success_requires_row_linked_resolvable_test(runner, tmp_path):
    """A success claim with a real, linked test -> SAFE_EQUIVALENT/wiring."""
    cid = "api.closure.linked"
    _closure_manifest(tmp_path, "QUBIT_OPEN_CLOSURE.json", {
        "id": cid,
        "disposition": "SAFE_EQUIVALENT",
        "qubit_path": "voicebridge/providers/resolver.py:1",
        "qubit_symbol": "EffectiveProfile",
        "test": _LINKED_TEST,
        "basis": "manifest proof",
    })
    closures = runner.load_open_closures(tmp_path)
    row = _closure_row(
        cid, qubit_path="voicebridge/providers/resolver.py:1",
        qubit_symbol="EffectiveProfile")
    result = runner._new_result(row)
    enriched = runner.apply_open_closures([row], [result], closures)[0]
    assert result["disposition"] == "SAFE_EQUIVALENT"
    assert result["proof_level"] == "wiring"
    assert result["test_ids"] == [_LINKED_TEST]
    assert result["production_caller"]
    assert enriched["test_ids"] == [_LINKED_TEST]
    gates = {g["gate"]: g for g in runner.apply_fail_closed_gates([enriched], [result])}
    assert gates["unexecuted_row"]["ok"], gates["unexecuted_row"]["rows"]
    assert gates["proof_level"]["ok"], gates["proof_level"]["rows"]
    assert gates["proof_level_requires_resolvable_test_id"]["ok"]
    assert gates["pass_without_reference"]["ok"]


def test_closure_success_without_link_fails_closed_to_operator_only(runner, tmp_path):
    """A success claim with no row-linked resolvable test is never SAFE_EQUIVALENT."""
    cid = "api.closure.unlinked"
    _closure_manifest(tmp_path, "QUBIT_OPEN_CLOSURE.json", {
        "id": cid,
        "disposition": "IMPLEMENTED",
        "qubit_path": "voicebridge/nothing_here.py:1",
        "qubit_symbol": "zzz_absent_symbol_qubit",
        "test": "tests/test_differential_parity8u.py::test_does_not_exist_zzz",
        "basis": "analyst asserts implemented",
    })
    closures = runner.load_open_closures(tmp_path)
    row = _closure_row(
        cid, qubit_path="voicebridge/nothing_here.py:1",
        qubit_symbol="zzz_absent_symbol_qubit")
    result = runner._new_result(row)
    runner.apply_open_closures([row], [result], closures)
    assert result["disposition"] == "OPERATOR_ONLY"
    assert result["operator_prerequisite"]
    assert result["proof_level"] is None
    assert result["executed"] is False


def test_closure_success_without_qubit_mapping_is_not_safe_equivalent(runner, tmp_path):
    """Even a resolvable test cannot dispose a row with no Qubit mapping."""
    cid = "api.closure.nomapping"
    _closure_manifest(tmp_path, "OPENCLAW_OPEN_CLOSURE.json", {
        "capability_id": cid,
        "disposition": "ADAPTED",
        "qubit_path": None,
        "qubit_symbol": None,
        "test": _LINKED_TEST,
        "basis": "route-token match only",
    })
    closures = runner.load_open_closures(tmp_path)
    row = _closure_row(cid, qubit_path=None, qubit_symbol=None)
    result = runner._new_result(row)
    runner.apply_open_closures([row], [result], closures)
    assert result["disposition"] == "OPERATOR_ONLY"
    assert result["operator_prerequisite"]


def test_closure_defect_becomes_fail_and_operator_hold_is_preserved(runner, tmp_path):
    """A recorded defect -> FAIL; an operator-only manifest -> exact prerequisite."""
    defect_id = "api.closure.defect"
    hold_id = "api.closure.hold"
    _closure_manifest(tmp_path, "QUBIT_OPEN_CLOSURE.json", {
        "id": defect_id,
        "disposition": "DEFECT",
        "qubit_path": "voicebridge/x.py:1",
        "basis": "live probe returned a bare 404",
    })
    _closure_manifest(tmp_path, "HERMES_OPEN_CLOSURE.json", {
        "capability_id": hold_id,
        "disposition": "OPERATOR_ONLY",
        "operator_prerequisite": "operator must provide a live device",
        "basis": "hardware gate",
    })
    closures = runner.load_open_closures(tmp_path)
    rows = [_closure_row(defect_id), _closure_row(hold_id)]
    results = [runner._new_result(r) for r in rows]
    runner.apply_open_closures(rows, results, closures)
    by = {r["capability_id"]: r for r in results}
    assert by[defect_id]["disposition"] == "FAIL"
    assert by[defect_id]["diagnostics"]
    assert by[hold_id]["disposition"] == "OPERATOR_ONLY"
    assert by[hold_id]["operator_prerequisite"] == "operator must provide a live device"


def test_closure_downgrades_unproven_pre_existing_safe_equivalent(runner, tmp_path):
    """A pre-existing SAFE_EQUIVALENT with no valid proof is refused fail-closed."""
    cid = "api.closure.unproven_se"
    closures = {}
    row = _closure_row(cid, qubit_symbol="zzz_absent_symbol_qubit")
    row["disposition"] = "SAFE_EQUIVALENT"
    row["proof_level"] = "wiring"
    row["test_ids"] = ["tests/test_differential_parity8u.py::test_does_not_exist_zzz"]
    result = runner._new_result(row)
    result.update({
        "disposition": "SAFE_EQUIVALENT",
        "proof_level": "wiring",
        "test_ids": ["tests/test_differential_parity8u.py::test_does_not_exist_zzz"],
    })
    runner.apply_open_closures([row], [result], closures)
    assert result["disposition"] == "OPERATOR_ONLY"
    assert result["declared_disposition"] == "OPERATOR_ONLY"
    assert result["proof_level"] is None


def test_closure_overlay_is_deterministic_and_leaves_terminal_rows_untouched(runner):
    """Same input -> same dispositions; PASS/FAIL/executed rows are untouched."""
    closures = runner.load_open_closures(CLOSURE_DIR)
    assert closures
    open_row = _closure_row("api.closure.det.open")
    pass_row = _closure_row("api.closure.det.pass")
    pass_row["disposition"] = "PASS"
    pass_result = runner._new_result(pass_row)
    pass_result["executed"] = True
    rows_a = [open_row, pass_row]
    results_a = [runner._new_result(open_row), dict(pass_result)]
    rows_b = [dict(open_row), dict(pass_row)]
    results_b = [runner._new_result(dict(open_row)), dict(pass_result)]
    runner.apply_open_closures(rows_a, results_a, closures)
    runner.apply_open_closures(rows_b, results_b, closures)
    assert [r["disposition"] for r in results_a] == [r["disposition"] for r in results_b]
    assert results_a[1]["disposition"] == "PASS"
    assert results_a[0]["disposition"] in ("SAFE_EQUIVALENT", "OPERATOR_ONLY")


def test_closure_manifest_never_claims_pass(runner):
    """No frozen closure entry is PASS, and no overlay output is ever PASS."""
    closures = runner.load_open_closures(CLOSURE_DIR)
    assert all(entry["disposition"] != "PASS" for entry in closures.values())
    row = _closure_row("api.closure.no_pass")
    result = runner._new_result(row)
    runner.apply_open_closures([row], [result], closures)
    assert result["disposition"] != "PASS"
