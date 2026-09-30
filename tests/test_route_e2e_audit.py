"""Focused tests for ``scripts/route_e2e_audit.py``.

Two layers:

* pure/unit tests (hermetic, always run) pin the target-safety guard, the
  parameter/query resolution, the classification rules, and the
  volatile-free deterministic digest;
* one ``@pytest.mark.live`` test proves the auditor is deterministic and
  non-destructive against the running dashboard (opt-in, DEGRADED when the
  dashboard is down -- it never restarts or mutates user data).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import route_e2e_audit as R  # noqa: E402


# ---------------------------------------------------------------------------
# Pure: target safety
# ---------------------------------------------------------------------------
def test_validate_target_refuses_reference_and_non_loopback():
    assert R.validate_target("http://127.0.0.1:8890") == "http://127.0.0.1:8890"
    for bad in ("http://127.0.0.1:9119",
                "http://localhost:9119",
                "http://example.com:8890",
                "ftp://127.0.0.1:8890"):
        with pytest.raises(R.TargetRefused):
            R.validate_target(bad)


def test_auditor_never_references_hermes_home_as_target():
    # The target policy is explicit and loopback-only.
    assert "~/.hermes" in R.__doc__
    assert R.API_PREFIX == "/agent"


# ---------------------------------------------------------------------------
# Pure: parameter / query resolution
# ---------------------------------------------------------------------------
def test_resolve_path_is_deterministic():
    cases = {
        "/api/sessions/{session_id}/messages": "/api/sessions/1/messages",
        "/api/actions/{name}/status": "/api/actions/gateway-restart/status",
        "/api/memory/providers/{name}/config":
            "/api/memory/providers/qubit-local/config",
        "/api/tools/toolsets/{name}/models": "/api/tools/toolsets/web/models",
        "/api/profiles/{name}/soul": "/api/profiles/default/soul",
        "/dashboard-plugins/{plugin_name}/{file_path:path}":
            "/dashboard-plugins/kanban/index.js",
    }
    for template, expected in cases.items():
        assert R.resolve_path(template) == expected
        assert R.resolve_path(template) == expected


def test_resolve_query_defaults():
    assert R.resolve_query("GET /api/git/status") == {"path": str(REPO)}
    assert R.resolve_query("GET /api/learning/node") == {"id": "qubit-voice-mode"}
    assert R.resolve_query("GET /api/status") == {}


# ---------------------------------------------------------------------------
# Pure: classification
# ---------------------------------------------------------------------------
def test_classify_gate_envelope_is_gated():
    gate = {"ok": False, "state": "UNAVAILABLE", "code": "CONFIRM_REQUIRED",
            "qubit_counterpart": "CLI `qubit approvals respond`"}
    assert R.classify(409, gate, True) == "E2E_GATED"
    policy = {"ok": False, "state": "FAILED", "code": "LOCAL_MODEL_UNAVAILABLE",
              "owner": "qubit-policy", "rollback": "nothing changed"}
    assert R.classify(403, policy, True) == "E2E_GATED"


def test_classify_informational_counterpart_on_success_is_pass():
    # A successful payload may carry ``qubit_counterpart`` as a pointer; that
    # is NOT a refusal.
    ok = {"ok": True, "endpoints": [], "qubit_counterpart": "CLI `qubit config`"}
    assert R.classify(200, ok, True) == "E2E_PASS"
    assert R.classify(200, [{"name": "x"}], True) == "E2E_PASS"


def test_classify_internal_error_and_transport_are_fail():
    assert R.classify(500, {"ok": False, "code": "NO_HANDLER"}, True) == "E2E_FAIL"
    assert R.classify(-1, {"code": "TRANSPORT"}, True) == "E2E_FAIL"
    assert R.classify(200, {"ok": False, "code": "NO_HANDLER"}, True) == "E2E_FAIL"
    assert R.classify(401, None, True) == "E2E_FAIL"


def test_classify_unresolved_input_is_skip():
    assert R.classify(422, {"detail": "missing"}, False) == "E2E_SKIP"
    assert R.classify(422, {"detail": "missing"}, True) == "E2E_FAIL"


# ---------------------------------------------------------------------------
# Pure: deterministic digest
# ---------------------------------------------------------------------------
def _doc(reads, mutations, generated_at="A"):
    return {"generated_at": generated_at, "read_routes": reads,
            "mutations": mutations}


def test_stable_digest_ignores_volatile_fields():
    reads = [{"id": "GET /a", "classification": "E2E_PASS",
              "detail": "x", "http": 200}]
    muts = [{"name": "m", "classification": "E2E_PASS"}]
    a = R.stable_digest(_doc(reads, muts, generated_at="2026-01-01"))
    b = R.stable_digest(_doc(reads, muts, generated_at="2099-12-31"))
    assert a == b


def test_stable_digest_changes_with_classification():
    reads = [{"id": "GET /a", "classification": "E2E_PASS"}]
    muts = [{"name": "m", "classification": "E2E_PASS"}]
    a = R.stable_digest(_doc(reads, muts))
    reads2 = [{"id": "GET /a", "classification": "E2E_FAIL"}]
    assert R.stable_digest(_doc(reads2, muts)) != a


# ---------------------------------------------------------------------------
# Live: determinism + non-destructiveness
# ---------------------------------------------------------------------------
_LIVE_BASE = "http://127.0.0.1:8890"
_ALLOWED = {"E2E_PASS", "E2E_GATED", "E2E_FAIL", "E2E_SKIP"}


def _snapshot(base: str, token: str):
    out = {}
    for path in ("/api/mcp/servers", "/api/cron/jobs", "/api/webhooks",
                 "/api/profiles", "/api/ops/hooks",
                 "/api/providers/custom-endpoints"):
        http, body = R._request(base, token, "GET", path, timeout=10.0)
        out[path] = (http, body)
    return out


def _no_temp_artifacts(snapshot) -> bool:
    blob = repr(snapshot)
    return R.TEMP_PREFIX not in blob


@pytest.mark.live
def test_live_audit_is_deterministic_and_nondestructive():
    base = R.validate_target(_LIVE_BASE)
    try:
        token = R.scrape_token(base)
    except RuntimeError:
        pytest.skip("live dashboard not running; auditor is DEGRADED")

    before = _snapshot(base, token)
    first = R.run_audit(base)
    after = _snapshot(base, token)
    second = R.run_audit(base)

    # Non-destructive: no temp artifact survives, and the registries are
    # byte-for-byte unchanged by the audit.
    assert _no_temp_artifacts(after), "auditor left a temp artifact behind"
    assert before == after, "auditor mutated user-visible state"

    # Deterministic: classification-only digest is stable across runs.
    assert first["stable_digest"] == second["stable_digest"]
    assert all(r["classification"] in _ALLOWED
               for r in first["read_routes"])
    assert all(m["classification"] in _ALLOWED
               for m in first["mutations"])
    assert first["counts"]["read_total"] > 0
