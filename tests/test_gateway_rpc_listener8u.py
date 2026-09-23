"""D2 — OpenClaw-dialect RPC on the single Qubit listener.

Owner decision 2026-09-18 (D2): OpenClaw-compatible RPC is served through the
existing authenticated Qubit listener, reusing the canonical in-process method
registry; no second gateway/server/authority is started. These tests exercise
the real registered routes and handlers (no mock), and assert the single
authority is preserved.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def test_routes_are_registered_on_the_existing_listener():
    from voicebridge import qubit_compat as HC

    get_route, _ = HC.match("GET", "/api/gateway/rpc")
    post_route, _ = HC.match("POST", "/api/gateway/rpc")
    assert get_route and get_route["handler"] == "h_gateway_rpc_methods"
    assert post_route and post_route["handler"] == "h_gateway_rpc"


def test_get_lists_the_real_registry_methods():
    from voicebridge import qubit_compat as HC

    code, out = HC.HANDLERS["h_gateway_rpc_methods"](None, {}, {}, {})
    assert code == 200
    methods = out["methods"]
    assert isinstance(methods, list) and methods
    assert "environments.prepare" in methods
    assert "update.report" in methods


def test_missing_method_is_typed_bad_params():
    from voicebridge import qubit_compat as HC

    code, out = HC.HANDLERS["h_gateway_rpc"](None, {}, {}, {})
    assert code == 400
    assert out["ok"] is False
    assert out["error"]["code"] == "GATEWAY_BAD_PARAMS"


def test_unknown_method_is_typed_not_found_not_fabricated_success():
    from voicebridge import qubit_compat as HC

    code, out = HC.HANDLERS["h_gateway_rpc"](
        None, {}, {"id": "1", "method": "does.not.exist", "params": {}}, {})
    assert code == 400
    assert out["type"] == "res" and out["ok"] is False
    assert out["error"]["code"] == "GATEWAY_METHOD_NOT_FOUND"


def test_route_ownership_matrix_records_the_routes_with_no_violation():
    doc = json.loads(
        (REPO_ROOT / "docs/archive/2026-09-21/QUBIT_ROUTE_OWNERSHIP.json").read_text())
    assert doc["violations"] == []
    paths = {(r["method"], r["path"]) for r in doc["rows"]}
    assert ("GET", "/api/gateway/rpc") in paths
    assert ("POST", "/api/gateway/rpc") in paths


def test_second_listener_stays_disabled():
    """D2 forbids a second gateway/server; the legacy serve() must stay inert."""
    from voicebridge import qubit_backend

    try:
        qubit_backend.serve("127.0.0.1", 9119)
    except Exception:
        pass
    else:  # pragma: no cover - a live second listener is a D2 violation
        raise AssertionError("qubit_backend.serve started a second listener")


def test_openclaw_delta_methods_are_registered():
    """The newer OpenClaw delta methods resolve to real Qubit backends."""
    from voicebridge.gateway.rpc import default_registry

    names = set(default_registry().names())
    for method in (
        "tasks.history",
        "session.publicShare.set",
        "plugins.catalog.browse",
        "plugins.catalog.categories",
        "plugins.catalog.get",
        "plugins.reload",
        "plugins.skills.read",
        "skills.workshop.read",
        "transcripts.status",
        "transcripts.export",
        "computer.status",
        "computer.invoke",
        "desktop.release",
        "talk.voice.get",
        "talk.voice.set",
        "sessions.activitySummary.ensure",
        "sessions.storage.status",
        "sessions.storage.run",
        "plugins.credentials.inspect",
        "diagnostics.cpuProfile",
        "diagnostics.heapProfile",
        "models.authLogin",
        "models.authRefresh",
        "models.authSetApiKey",
        "mcp.authLogin",
        "claws.monitors",
        "claws.packages.remove",
        "canvas.document.preview",
        "talk.voice.complete",
    ):
        assert method in names, method


def test_operator_only_diagnostics_is_typed_not_fabricated():
    from voicebridge.gateway.rpc import default_registry, handle_request

    res = handle_request(default_registry(), None,
                         {"id": "1", "method": "diagnostics.cpuProfile",
                          "params": {}})
    assert res["ok"] is True
    payload = res["payload"]
    assert payload["state"] == "UNAVAILABLE"
    assert payload["code"] == "DIAGNOSTICS_OPERATOR_REQUIRED"
    assert payload.get("qubit_counterpart")


def test_model_auth_is_manual_handoff_refusal():
    from voicebridge.gateway.rpc import default_registry, handle_request

    res = handle_request(default_registry(), None,
                         {"id": "1", "method": "models.authLogin",
                          "params": {}})
    assert res["ok"] is True
    assert res["payload"]["state"] == "REFUSED"
    assert res["payload"]["code"] == "OAUTH_MANUAL_HANDOFF"


def test_talk_voice_complete_requires_text():
    from voicebridge.gateway.rpc import default_registry, handle_request

    res = handle_request(default_registry(), None,
                         {"id": "1", "method": "talk.voice.complete",
                          "params": {}})
    assert res["ok"] is False
    assert res["error"]["code"] == "GATEWAY_BAD_PARAMS"


def test_manifest_overlay_is_fully_disposed():
    """No delta method is left OPEN; each is a terminal behavior disposition.

    Scope is the union of ``delta.latest_release.added`` and
    ``delta.latest_main.added`` (owner decision 2026-09-18 audits both), and a
    terminal disposition is IMPLEMENTED, SAFE_EQUIVALENT, or OPERATOR_ONLY. A
    bare registered-method name is not a disposition.
    """
    manifest = json.loads(
        (REPO_ROOT / "docs/generated/QUBIT_OPENCLAW_UPGRADE_MANIFEST.json").read_text())
    overlay = manifest["disposition_overlay"]
    counts = overlay["counts"]
    assert counts.get("OPEN", 0) == 0
    terminal = counts["IMPLEMENTED"] + counts.get("SAFE_EQUIVALENT", 0) \
        + counts.get("OPERATOR_ONLY", 0) + counts.get("OPERATOR_ONLY_EXTERNAL", 0)
    assert terminal == len(overlay["methods"])
    union = set(manifest["delta"]["latest_release"]["added"]) \
        | set(manifest["delta"]["latest_main"]["added"])
    assert set(overlay["methods"]) == union


def test_manifest_refusal_rows_declare_a_typed_code():
    """SAFE_EQUIVALENT / OPERATOR_ONLY rows must carry a typed code + proof."""
    manifest = json.loads(
        (REPO_ROOT / "docs/generated/QUBIT_OPENCLAW_UPGRADE_MANIFEST.json").read_text())
    for method, row in manifest["disposition_overlay"]["methods"].items():
        if row["status"] in ("SAFE_EQUIVALENT", "OPERATOR_ONLY"):
            assert row.get("code"), method
            assert row.get("evidence"), method
        if row["status"] == "IMPLEMENTED":
            assert row.get("evidence"), method


def test_manifest_overlay_matches_registry_actual():
    """Every method the manifest stamps IMPLEMENTED must be in the registry."""
    manifest = json.loads(
        (REPO_ROOT / "docs/generated/QUBIT_OPENCLAW_UPGRADE_MANIFEST.json").read_text())
    from voicebridge.gateway.rpc import default_registry

    registered = set(default_registry().names())
    for method, row in manifest["disposition_overlay"]["methods"].items():
        if row["status"] == "IMPLEMENTED":
            assert method in registered, method
