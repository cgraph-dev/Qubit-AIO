"""Focused tests for ``scripts/controls_e2e_audit.py`` + its artifact.

Two layers:

* hermetic unit tests pin the target-safety guard, the request/response
  redaction, and the classification rules (``_is_gate`` / ``_policy_of`` /
  ``Harness.disposition``) with fabricated traffic -- no browser, no network;
* artifact-structure tests validate the committed
  ``docs/generated/QUBIT_CONTROLS_E2E_2026-09-23.json`` (schema, exact counts,
  per-page coverage, required fields, no raw secrets, findings == FAIL rows);
* one ``@pytest.mark.live`` test proves representative read-only endpoints
  across all fourteen audited pages answer over HTTP against the running
  dashboard (opt-in, DEGRADED when it is down; it never restarts the service or
  mutates user data).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import controls_e2e_audit as C  # noqa: E402

ARTIFACT = REPO / "docs/generated/QUBIT_CONTROLS_E2E_2026-09-23.json"
ALLOWED = {"E2E_PASS", "E2E_GATED", "E2E_FAIL", "E2E_SKIP"}
REQUIRED_KEYS = {
    "page", "control", "action", "request", "policy",
    "state_change", "reload_persist", "disposition", "detail",
}


# ---------------------------------------------------------------------------
# Pure: target safety
# ---------------------------------------------------------------------------
def test_validate_target_refuses_reference_and_non_loopback():
    assert C.validate_target("http://127.0.0.1:8890") == "http://127.0.0.1:8890"
    for bad in ("http://127.0.0.1:9119",
                "http://localhost:9119",
                "http://example.com:8890",
                "ftp://127.0.0.1:8890"):
        with pytest.raises(C.TargetRefused):
            C.validate_target(bad)


def test_auditor_policy_is_explicit():
    assert "~/.hermes" in C.__doc__
    assert "/agent/api/ops/approvals" in C.BACKGROUND_PATHS


# ---------------------------------------------------------------------------
# Pure: redaction (never echo secrets)
# ---------------------------------------------------------------------------
def test_payload_redacts_secret_shaped_keys():
    raw = json.dumps({"name": "x", "api_key": "sk-abcdef0123456789",
                      "token": "Bearer zzz", "nested": {"password": "p"}})
    out = C._payload(raw)
    blob = json.dumps(out)
    assert "sk-abcdef0123456789" not in blob
    assert out["api_key"] == "«redacted»"
    assert out["nested"]["password"] == "«redacted»"


def test_excerpt_keeps_only_safe_fields():
    excerpt = C._excerpt({"ok": True, "code": "X", "secret_value": "nope",
                          "unrelated": [1, 2, 3]})
    assert excerpt["ok"] is True
    assert "secret_value" not in (excerpt or {})
    assert "unrelated" not in (excerpt or {})


def test_gw_pid_reads_daemon_then_process_then_top_level():
    # the served /api/gateway nests the live pid under process.daemon
    assert C._gw_pid({"process": {"daemon": {"pid": 42}}}) == 42
    assert C._gw_pid({"process": {"pid": 7}}) == 7
    assert C._gw_pid({"pid": 3}) == 3
    assert C._gw_pid({}) is None
    assert C._gw_pid(None) is None


# ---------------------------------------------------------------------------
# Pure: classification rules
# ---------------------------------------------------------------------------
def _req(method="POST", path="/agent/api/x", status=200, code=None,
         body=None):
    return {"method": method, "path": path, "status": status, "code": code,
            "url": "http://127.0.0.1:8890" + path,
            "payload": None, "body_excerpt": body}


def test_is_gate_recognizes_typed_refusals():
    assert C._is_gate(_req(status=409, code="CONFIRM_REQUIRED"))
    assert C._is_gate(_req(status=403, code="SECRET_NEVER_SERVED"))
    assert C._is_gate(_req(status=403, code="OPERATOR_GATED",
                           body={"detail": "operator gated"}))
    # a bare 4xx without a typed code is not a gate
    assert not C._is_gate(_req(status=403, code="",
                               body={"detail": "operator gated"}))
    assert not C._is_gate(_req(status=200, code="CONFIRM_REQUIRED"))
    assert not C._is_gate(_req(status=404, code="NOT_FOUND"))


def test_policy_of_handles_pending_status():
    # a request whose response has not arrived must not crash the policy map
    assert C._policy_of([_req(status=None)]) == "unexpected"


def _harness():
    h = C.Harness.__new__(C.Harness)
    h.base = "http://127.0.0.1:8890"
    h.traffic = []
    h.rows = []
    h._pending = {}
    h._force_notes = []
    h._capture_bodies = True
    return h


def test_disposition_rules():
    h = _harness()
    # client-only control with a DOM change passes without a request
    assert h.disposition([], dom_changed=True, client_only=True)[0] == "E2E_PASS"
    # client-only with no observable change fails
    assert h.disposition([], dom_changed=False, client_only=True)[0] == "E2E_FAIL"
    # a non-client control that issued nothing is a skip
    assert h.disposition([], client_only=False)[0] == "E2E_SKIP"
    # 2xx is a pass
    assert h.disposition([_req(status=200)])[0] == "E2E_PASS"
    # 5xx is a fail
    assert h.disposition([_req(status=500)])[0] == "E2E_FAIL"
    # a typed gate alone is gated
    assert h.disposition([_req(status=409, code="CONFIRM_REQUIRED")])[0] == "E2E_GATED"
    # a gated stage that then settled 2xx is a pass
    assert h.disposition(
        [_req(status=409, code="CONFIRM_REQUIRED"), _req(status=200)],
        confirmed=True)[0] == "E2E_PASS"
    # background polling is excluded from attribution
    assert h.disposition(
        [_req(path="/agent/api/ops/approvals", status=200)])[0] == "E2E_SKIP"


# ---------------------------------------------------------------------------
# Artifact structure (hermetic; validates the committed evidence)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def artifact():
    assert ARTIFACT.is_file(), f"missing artifact: {ARTIFACT}"
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def test_artifact_schema_and_target(artifact):
    assert artifact["schema"] == "qubit-controls-e2e/1"
    assert artifact["mode"].startswith("real browser")
    assert artifact["target"] == "http://127.0.0.1:8890"
    policy = artifact["target_policy"]
    assert policy["loopback_only"] is True
    assert policy["no_deletes_of_user_data"] is True
    assert policy["no_estop"] is True
    assert policy["no_service_restart"] is True
    assert set(artifact["pages"]) == set(C.PAGES)


def test_counts_are_internally_consistent(artifact):
    controls = artifact["controls"]
    counts = artifact["counts"]
    assert counts["total"] == len(controls)
    tally = {}
    for row in controls:
        tally[row["disposition"]] = tally.get(row["disposition"], 0) + 1
    for key in ALLOWED:
        assert counts.get(key, 0) == tally.get(key, 0), key
    # by-page totals sum to the grand total
    assert sum(sum(v.values()) for v in artifact["counts_by_page"].values()) \
        == len(controls)
    # findings are exactly the FAIL rows
    fail_rows = [r for r in controls if r["disposition"] == "E2E_FAIL"]
    assert len(artifact["findings"]) == len(fail_rows)


def test_every_control_has_required_evidence_fields(artifact):
    for row in artifact["controls"]:
        assert REQUIRED_KEYS <= set(row), row.get("control")
        assert row["disposition"] in ALLOWED
        assert isinstance(row["request"], list)
        assert row["action"] and row["control"] and row["page"]
        assert row["page"] in C.PAGES


def test_every_page_is_covered(artifact):
    seen = {}
    for row in artifact["controls"]:
        seen[row["page"]] = seen.get(row["page"], 0) + 1
    for page in C.PAGES:
        assert seen.get(page, 0) > 0, f"no control exercised on {page}"


def test_artifact_contains_no_raw_secrets(artifact):
    blob = json.dumps(artifact)
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", blob), "raw API key leaked"
    assert not re.search(r"AKIA[0-9A-Z]{16}", blob), "AWS key leaked"
    # explicit redaction markers are expected for secret-shaped payload keys
    # (the artifact is serialized with ensure_ascii, so match the word)
    assert "redacted" in blob.lower()


def test_gated_rows_carry_a_typed_code(artifact):
    # Any row classified E2E_GATED must show a typed refusal code on a request,
    # and the gated count must be reflected in the artifact.
    gated = [r for r in artifact["controls"] if r["disposition"] == "E2E_GATED"]
    for row in gated:
        assert any(q.get("code") for q in row["request"]), row["control"]
    assert artifact["counts"].get("E2E_GATED", 0) == len(gated)


# ---------------------------------------------------------------------------
# Artifact: the wave's target pages carry real behavioral controls
# ---------------------------------------------------------------------------
# page -> minimum number of exercised controls the audit must record.
_NEW_PAGE_MIN = {
    "/sessions": 8, "/files": 3, "/logs": 6, "/channels": 4,
    "/webhooks": 3, "/pairing": 3, "/system": 8,
}


def test_target_pages_have_behavioral_controls(artifact):
    seen: dict = {}
    for row in artifact["controls"]:
        seen[row["page"]] = seen.get(row["page"], 0) + 1
    for page, minimum in _NEW_PAGE_MIN.items():
        assert seen.get(page, 0) >= minimum, (page, seen.get(page, 0))


# control -> exact disposition proven by the live pass. This pins that each
# control was actually exercised (a rendered control is not evidence).
_EXPECTED_DISPOSITION = {
    ("/sessions", "sessions_overview"): "E2E_PASS",
    ("/sessions", "sessions_search"): "E2E_PASS",
    ("/sessions", "sessions_source_filter"): "E2E_PASS",
    ("/sessions", "sessions_pagination"): "E2E_PASS",
    ("/sessions", "sessions_export"): "E2E_PASS",
    ("/sessions", "sessions_delete_cancel"): "E2E_PASS",
    ("/logs", "logs_file_tab"): "E2E_PASS",
    ("/logs", "logs_level_filter"): "E2E_PASS",
    ("/logs", "logs_page_size"): "E2E_PASS",
    ("/files", "files_path_policy"): "E2E_GATED",
    ("/channels", "channels_test"): "E2E_PASS",
    ("/channels", "channels_enable_stage_deny"): "E2E_GATED",
    ("/webhooks", "webhooks_lifecycle"): "E2E_PASS",
    ("/webhooks", "webhooks_create_delete"): "E2E_FAIL",
    ("/pairing", "pairing_list"): "E2E_PASS",
    ("/system", "system_gateway_status"): "E2E_PASS",
    ("/system", "system_gateway_start_deny"): "E2E_PASS",
    ("/system", "system_gateway_restart_deny"): "E2E_PASS",
}


def test_each_named_control_has_expected_disposition(artifact):
    controls = {(r["page"], r["control"]): r for r in artifact["controls"]}
    for key, disp in _EXPECTED_DISPOSITION.items():
        assert key in controls, key
        assert controls[key]["disposition"] == disp, \
            (key, controls[key]["disposition"])


def test_failures_name_the_typed_backend_outcome(artifact):
    """Every FAIL must name the real typed backend outcome (not a generic
    message), so the artifact doubles as the root-cause record."""
    fails = {r["control"]: r for r in artifact["controls"]
             if r["disposition"] == "E2E_FAIL"}
    assert "mcp_invoke_tool" in fails
    assert "MCP_CONNECT_FAILED" in fails["mcp_invoke_tool"]["detail"]
    assert "webhooks_create_delete" in fails
    assert "CONFIRM_UNSUPPORTED" in fails["webhooks_create_delete"]["detail"]


# ---------------------------------------------------------------------------
# Live (opt-in): representative read-only endpoints across all 14 pages
# ---------------------------------------------------------------------------
_LIVE_BASE = "http://127.0.0.1:8890"
_PAGE_ENDPOINTS = (
    "/api/skills",
    "/api/dashboard/plugins/hub",
    "/api/mcp/servers",
    "/api/analytics/models?days=30",
    "/api/profiles",
    "/api/config",
    "/api/env",
    "/api/sessions/stats",
    "/api/files",
    "/api/logs?file=agent&limit=5",
    "/api/messaging/platforms",
    "/api/webhooks",
    "/api/pairing",
    "/api/gateway",
    "/api/system/stats",
)


@pytest.mark.live
def test_live_page_endpoints_answer_readonly():
    import urllib.error
    import urllib.request

    try:
        C.validate_target(_LIVE_BASE)
        with urllib.request.urlopen(_LIVE_BASE + "/chat", timeout=10) as resp:
            html = resp.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        pytest.skip("live dashboard not running; controls auditor is DEGRADED")
    match = re.search(r'window\.__QUBIT_SESSION_TOKEN__="([^"]+)"', html)
    if not match:
        pytest.skip("no session token served; controls auditor is DEGRADED")
    token = match.group(1)
    for path in _PAGE_ENDPOINTS:
        req = urllib.request.Request(
            _LIVE_BASE + "/agent" + path,
            headers={"X-Qubit-Session-Token": token})
        with urllib.request.urlopen(req, timeout=15) as resp:
            assert resp.status == 200, path
