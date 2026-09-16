"""Hermetic contract tests for the differential parity runner (agent 8u).

The runner lives at ``scripts/differential_parity.py`` and is loaded
dynamically by path so these tests are robust to package layout. While the
file is absent they ``pytest.skip`` (never fail); the frozen contract is
``/tmp/qubit-differential/INTERFACE.md`` (FROZEN).

Hermetic: no network, no third-party imports, all scratch under ``tmp_path``.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
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
PIN_HERMES = "2237be355906fbe6065ce1815711eee52b2d646e"
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
        upstream_path="frontend/hermes-web/src/routes/page.tsx:1",
        upstream_symbol="#submit-button",
        qubit_path="frontend/hermes-web/src/routes/page.tsx:1",
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
        "hermes": {"path": "hermes_cli/_parser.py:280", "symbol": "build_top_level_parser"},
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


def test_reference_execution_recorded(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("QUBIT_DIFFERENTIAL_HERMES_COMMIT", PIN_HERMES)
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
    monkeypatch.setenv("QUBIT_DIFFERENTIAL_HERMES_COMMIT", PIN_HERMES)
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

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
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

    # Adapter level: no resolvable URL must not fabricate a success.
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

    # Row level: a fixture env-ref token must not leak into the report.
    fixture = _transport_fixture(
        "api.http.token_8u", "api",
        {"kind": "http", "method": "GET", "path": "/token",
         "headers": {"X-Api-Token": "env:QUBIT_DIFFERENTIAL_QUBIT_TOKEN"},
         "body": None, "argv": []},
        expected={"http_status": 200})
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
