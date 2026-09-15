"""Agent 2b: Qubit-owned ``/api/qubit/*`` front door + real mutation paths.

This closes the Agent 5-10 capability gap by serving each backend at a
Qubit-owned GET route that is NEVER proxied to the vendored fork, and by
converting eligible staged refusals to the real Qubit
permission/confirmation/audit path.

Proof obligations:
(a) every new route resolves in the real route table and
    ``hermes_asgi.safe_to_proxy`` refuses to hand it to the fork;
(b) each read dispatches the SAME module the CLI/voice use and returns a real
    payload (not a refusal);
(c) typed negatives stay typed (400/403/404), never 404/500-for-the-wrong-reason;
(d) the usage/receipts/logbook readers surface real seeded records;
(e) profile create and learning-node promote/remove are confirm-gated real
    writes with typed duplicate/unknown outcomes.

No skips, no xfail.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# (path, handler) -- Qubit-owned GET reads at the /api/qubit/* front door.
FRONT_DOOR = [
    ("/api/qubit/gateway/discovery", "h_qubit_gateway_discovery"),
    ("/api/qubit/runtime/discovery", "h_qubit_runtime_discovery"),
    ("/api/qubit/runtime/marketplace", "h_qubit_runtime_marketplace"),
    ("/api/qubit/runtime/history", "h_qubit_runtime_history"),
    ("/api/qubit/runtime/update-report", "h_qubit_runtime_update_report"),
    ("/api/qubit/runtime/status", "h_qubit_runtime_status"),
    ("/api/qubit/runtime/images", "h_qubit_runtime_images"),
    ("/api/qubit/runtime/doctor", "h_qubit_runtime_doctor"),
    ("/api/qubit/egress/status", "h_qubit_egress_status"),
    ("/api/qubit/egress/providers", "h_qubit_egress_providers"),
    ("/api/qubit/egress/check", "h_qubit_egress_check"),
    ("/api/qubit/policy/conformance", "h_qubit_policy_conformance"),
    ("/api/qubit/policy/attest", "h_qubit_policy_attest"),
    ("/api/qubit/hooks/doctor", "h_qubit_hooks_doctor"),
    ("/api/qubit/usage", "h_qubit_usage"),
    ("/api/qubit/receipts", "h_qubit_receipts"),
    ("/api/qubit/logbook", "h_qubit_logbook"),
    ("/api/qubit/browser/profiles", "h_qubit_browser_profiles"),
    ("/api/qubit/local-models/fallback", "h_qubit_local_models_fallback"),
]


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


# ------------------------------------------------------- resolution + proxy
def test_qubit_frontdoor_resolves_and_is_never_proxied():
    from voicebridge import hermes_asgi as HA
    from voicebridge import hermes_compat as HC

    for path, handler in FRONT_DOOR:
        route, _ = HC.match("GET", path)
        assert route is not None, path
        assert route["disposition"] == "implemented", (path, route)
        assert route["handler"] == handler, (path, route["handler"])
        # Qubit-owned: a safe read that must stay on the compat path.
        assert HA.gated_path(path) is True, path
        assert HA.safe_to_proxy("GET", path) is False, path


# --------------------------------------------------------- real read payloads
def test_qubit_frontdoor_reads_dispatch_real_payloads(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    q = {"root": str(tmp_path)}
    for path, _handler in FRONT_DOOR:
        # egress/check is deny-by-default: it is exercised in the typed-negative
        # test (403) and in the allowed test by construction below.
        # receipts is a legitimate typed 404 until a chain exists; the seeded
        # reader test below proves the real verified payload.
        if path in ("/api/qubit/egress/check", "/api/qubit/receipts"):
            continue
        code, out = HC.dispatch(cfg, "GET", path, q, {})
        assert code == 200, (path, code, str(out)[:200])
        assert isinstance(out, dict) and out.get("ok") is True, (path, out)

    code, out = HC.dispatch(cfg, "GET", "/api/qubit/gateway/discovery", {}, {})
    assert out["schema"] == "qubit-gateway-peers/1", out
    assert out["gateways"] == [] and out["peers"] == [], out
    assert isinstance(out["providers"], list), out

    code, out = HC.dispatch(cfg, "GET", "/api/qubit/runtime/discovery", {}, {})
    assert isinstance(out["gateways"], int) and isinstance(out["peers"], int), out

    code, out = HC.dispatch(cfg, "GET", "/api/qubit/runtime/marketplace", {}, {})
    assert out["state"] in ("OK", "UNAVAILABLE"), out
    assert isinstance(out["local_plugins"], int), out

    code, out = HC.dispatch(cfg, "GET", "/api/qubit/runtime/history",
                            {"name": "opencode-binary"}, {})
    assert out["image"] == "opencode-binary" and isinstance(out["entries"], list), out

    code, out = HC.dispatch(cfg, "GET", "/api/qubit/runtime/update-report", {}, {})
    assert isinstance(out["status"], dict) and isinstance(out["history"], list), out
    assert "recovery" in out, out

    code, out = HC.dispatch(cfg, "GET", "/api/qubit/runtime/status", {}, {})
    assert out["daemon"] == "none-required" and isinstance(out["runtimes"], list), out

    code, out = HC.dispatch(cfg, "GET", "/api/qubit/runtime/images", {}, {})
    assert out["schema"] == "qubit-runtime-images/1", out
    assert out["images"] and out["images"][0]["name"], out

    code, out = HC.dispatch(cfg, "GET", "/api/qubit/runtime/doctor", {}, {})
    assert isinstance(out["findings"], list), out

    code, out = HC.dispatch(cfg, "GET", "/api/qubit/egress/status", {}, {})
    assert out["schema"] == "qubit-egress-status/1", out
    assert "policy" in out and "registry" in out, out

    code, out = HC.dispatch(cfg, "GET", "/api/qubit/egress/providers", {}, {})
    assert isinstance(out["providers"], list), out
    assert isinstance(out["frontier_enabled"], bool), out

    code, out = HC.dispatch(cfg, "GET", "/api/qubit/policy/conformance",
                            {"root": str(tmp_path)}, {})
    assert out["schema"] == "qubit-policy-conformance/1", out
    assert out["state"] in ("PASS", "FAIL"), out
    assert "counts" in out and "findings" in out, out

    code, out = HC.dispatch(cfg, "GET", "/api/qubit/policy/attest",
                            {"root": str(tmp_path)}, {})
    assert out["immutable"] is True, out
    assert len(out["attestation_id"]) == 64, out

    code, out = HC.dispatch(cfg, "GET", "/api/qubit/hooks/doctor", {}, {})
    assert isinstance(out["hooks"], list) and isinstance(out["events_known"], list), out

    code, out = HC.dispatch(cfg, "GET", "/api/qubit/browser/profiles", {}, {})
    assert out["schema"] == "qubit-browser-profiles/1", out
    assert out["profiles"] == [] and out["managed_root"], out

    code, out = HC.dispatch(cfg, "GET", "/api/qubit/local-models/fallback", {}, {})
    assert out["schema"] == "qubit-local-models-fallback/1", out
    assert isinstance(out["chain"], list), out


def test_qubit_frontdoor_reads_equal_direct_backend(tmp_path, monkeypatch):
    from voicebridge import browser as BR
    from voicebridge import gateway_peers as GW
    from voicebridge import hermes_compat as HC
    from voicebridge import hooks as HK
    from voicebridge import local_models as LM
    from voicebridge import runtime_inventory as RIN

    cfg = _cfg(tmp_path, monkeypatch)

    def _direct(path, direct):
        code, out = HC.dispatch(cfg, "GET", path, {}, {})
        assert code == 200, (path, code, out)
        for key, value in direct.items():
            assert out[key] == value, (path, key, out.get(key), value)

    _direct("/api/qubit/gateway/discovery", GW.discovery(cfg))
    _direct("/api/qubit/runtime/discovery", RIN._discovery(cfg))
    _direct("/api/qubit/browser/profiles", BR.profiles(cfg))
    _direct("/api/qubit/hooks/doctor", HK.doctor(cfg))
    _direct("/api/qubit/local-models/fallback", LM.fallback_chain(cfg))


# ------------------------------------------------------------- typed negatives
def test_qubit_frontdoor_typed_negatives(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC
    from voicebridge import local_models as LM

    cfg = _cfg(tmp_path, monkeypatch)

    code, out = HC.dispatch(cfg, "GET", "/api/qubit/egress/check", {}, {})
    assert (code, out["code"]) == (400, "EGRESS_HOST_REQUIRED"), (code, out)
    assert out["ok"] is False and out["qubit_counterpart"], out

    code, out = HC.dispatch(cfg, "GET", "/api/qubit/egress/check",
                            {"host": "not-on-the-allowlist.example"}, {})
    assert code == 403 and out["ok"] is False, (code, out)
    assert out["code"] == "EGRESS_DENIED" and out["host"], out

    bad = tmp_path / "not-a-dir"
    for path in ("/api/qubit/policy/conformance", "/api/qubit/policy/attest"):
        code, out = HC.dispatch(cfg, "GET", path, {"root": str(bad)}, {})
        assert (code, out["code"]) == (400, "WORKSPACE_UNREADABLE"), (path, code, out)
        assert out["qubit_counterpart"], out

    code, out = HC.dispatch(cfg, "GET", "/api/qubit/receipts", {}, {})
    assert (code, out["code"]) == (404, "RECEIPTS_NOT_FOUND"), (code, out)

    monkeypatch.setattr(
        LM, "resolve_fallback",
        lambda cfg, model="": {"state": "UNAVAILABLE", "code": LM.UNAVAILABLE_CODE,
                               "detail": "no local model observed"})
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/local-models/fallback",
                            {"model": "qubit-brain4"}, {})
    assert code == 403 and out["ok"] is False, (code, out)
    assert out["code"] == LM.UNAVAILABLE_CODE, out


def test_qubit_egress_check_allows_allowlisted_host(tmp_path, monkeypatch):
    from voicebridge import config as C
    from voicebridge import hermes_compat as HC

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\nproxy:\n  allowed_hosts:\n"
                 "    - example.com\n")
    cfg = C.Config.load(p)

    code, out = HC.dispatch(cfg, "GET", "/api/qubit/egress/check",
                            {"host": "https://example.com/v1"}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["allowed"] is True and out["code"] == "OK", out
    assert out["qubit_counterpart"], out

    code, out = HC.dispatch(cfg, "GET", "/api/qubit/egress/check",
                            {"host": "evil.example.net"}, {})
    assert code == 403 and out["allowed"] is False, (code, out)
    assert out["code"] == "EGRESS_DENIED", out


# -------------------------------------------- evaluation readers (real records)
def test_qubit_usage_receipts_logbook_surface_real_records(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC
    from voicebridge.evaluation import logbook as LB
    from voicebridge.evaluation import receipts as RC
    from voicebridge.evaluation import usage_analytics as UA

    cfg = _cfg(tmp_path, monkeypatch)
    evaldir = Path(cfg.state_dir()) / "evaluation"
    evaldir.mkdir(parents=True, exist_ok=True)

    ledger = UA.UsageLedger(evaldir / "usage.jsonl")
    ledger.record(UA.UsageRecord(product="qubit", model="m1", provider="p1",
                                 input_tokens=3, output_tokens=4, cost_usd=0.5))
    ledger.record(UA.UsageRecord(product="qubit", model="m1", provider="p1",
                                 input_tokens=1, output_tokens=1, cost_usd=0.1))
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/usage", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["records"] == 2 and out["calls"] == 2, out
    assert out["input_tokens"] == 4 and out["output_tokens"] == 5, out
    assert out["by_model"]["m1"]["calls"] == 2, out
    assert out["rejections"] == 0, out

    ident = RC.ExecutionIdentity(run_id="run-1", actor="user")
    chain = RC.ReceiptChain(ident)
    chain.append(status="OK", inputs_digest="a", outputs_digest="b")
    (evaldir / "receipts.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in chain.receipts()))
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/receipts", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["records"] == 1 and out["rejected"] == 0, out
    assert out["verify"]["valid"] is True, out

    book = LB.Logbook(evaldir / "logbook.jsonl")
    book.append(LB.LogbookEntry.create(kind="note", summary="shipped a thing"))
    code, out = HC.dispatch(cfg, "GET", "/api/qubit/logbook", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert len(out["entries"]) == 1, out
    assert out["summary"]["entries"] == 1, out
    assert out["summary"]["by_kind"] == {"note": 1}, out


# ------------------------------------------------- real confirm-gated mutations
def test_profile_create_is_real_confirm_gated(tmp_path, monkeypatch):
    from voicebridge import brain as B
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)

    code, out = HC.dispatch(cfg, "POST", "/api/profiles", {}, {"name": "work"})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    assert out["ok"] is False and out["qubit_counterpart"], out

    code, out = HC.dispatch(cfg, "POST", "/api/profiles", {},
                            {"name": "work", "confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["principal"]["id"] == "work", out
    assert B.principal_list(cfg)["principals"][0]["id"] == "work"

    code, out = HC.dispatch(cfg, "POST", "/api/profiles", {},
                            {"name": "work", "confirm": True})
    assert code == 400 and out["code"] == B.EXISTS_CODE, (code, out)

    code, out = HC.dispatch(cfg, "POST", "/api/profiles", {}, {"confirm": True})
    assert (code, out["code"]) == (400, "MALFORMED"), (code, out)


def test_learning_node_mutations_are_real_confirm_gated(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC
    from voicebridge import learning as L

    cfg = _cfg(tmp_path, monkeypatch)
    ledger = L.LearningLedger(Path(cfg.state_dir()))
    lid = ledger.stage_model_inference("prefers concise answers",
                                       evidence_ids=["ev-1"])
    assert lid

    # unconfirmed promotion stages; confirmed promotion flips review_needed.
    code, out = HC.dispatch(cfg, "PUT", "/api/learning/node",
                            {"id": lid}, {})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    code, out = HC.dispatch(cfg, "PUT", "/api/learning/node",
                            {"id": lid}, {"confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["node"]["review_needed"] is False, out

    # unknown ids are honest typed 404s for both verbs.
    code, out = HC.dispatch(cfg, "PUT", "/api/learning/node",
                            {"id": "nope"}, {"confirm": True})
    assert (code, out["code"]) == (404, "LESSON_NOT_FOUND"), (code, out)
    code, out = HC.dispatch(cfg, "DELETE", "/api/learning/node",
                            {"id": "nope"}, {"confirm": True})
    assert (code, out["code"]) == (404, "LESSON_NOT_FOUND"), (code, out)

    # unconfirmed delete stages; confirmed delete tombstones the lesson.
    code, out = HC.dispatch(cfg, "DELETE", "/api/learning/node",
                            {"id": lid}, {})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    code, out = HC.dispatch(cfg, "DELETE", "/api/learning/node",
                            {"id": lid}, {"confirm": True})
    assert code == 200 and out["removed"] is True, (code, out)
    code, out = HC.dispatch(cfg, "GET", "/api/learning/node", {"id": lid}, {})
    assert code == 200 and out["found"] is True, (code, out)
    assert out["node"]["tombstoned"] is True, out


# ===========================================================================
# Wave 2: formerly blanket mutating refusals now run real Qubit
# permission/confirmation/audit operations (same store as CLI/voice).
# ===========================================================================
def test_custom_endpoint_crud_is_real_confirm_gated(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)

    body = {"id": "local-llm", "base_url": "http://127.0.0.1:8080"}
    code, out = HC.dispatch(cfg, "POST", "/api/providers/custom-endpoints",
                            {}, body)
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    assert out["ok"] is False and out["qubit_counterpart"], out

    code, out = HC.dispatch(cfg, "POST", "/api/providers/custom-endpoints",
                            {}, {**body, "confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["id"] == "local-llm" and out["replaced"] is False, out

    # The staged endpoint is a real persisted settings write: visible on read.
    code, out = HC.dispatch(cfg, "GET", "/api/providers/custom-endpoints",
                            {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    row = [e for e in out["endpoints"] if e["id"] == "local-llm"][0]
    assert row["source"] == "qubit-settings", row
    assert row["base_url"] == "http://127.0.0.1:8080", row
    # No secret value and no live probe is ever surfaced.
    assert row["api_key_preview"] is None and row["has_api_key"] is False, row

    # activate requires confirmation, then marks the endpoint current.
    code, out = HC.dispatch(
        cfg, "POST", "/api/providers/custom-endpoints/local-llm/activate",
        {}, {})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    code, out = HC.dispatch(
        cfg, "POST", "/api/providers/custom-endpoints/local-llm/activate",
        {}, {"confirm": True})
    assert code == 200 and out["active"] == "local-llm", (code, out)

    # activate of an unknown endpoint with confirm is an honest 404.
    code, out = HC.dispatch(
        cfg, "POST", "/api/providers/custom-endpoints/nope/activate",
        {}, {"confirm": True})
    assert (code, out["code"]) == (404, "ENDPOINT_NOT_FOUND"), (code, out)

    # delete requires confirmation, then removes the real record.
    code, out = HC.dispatch(
        cfg, "DELETE", "/api/providers/custom-endpoints/local-llm", {}, {})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    code, out = HC.dispatch(
        cfg, "DELETE", "/api/providers/custom-endpoints/local-llm", {},
        {"confirm": True})
    assert code == 200 and out["removed"] is True, (code, out)
    code, out = HC.dispatch(cfg, "GET", "/api/providers/custom-endpoints",
                            {}, {})
    assert all(e["id"] != "local-llm" for e in out["endpoints"]), out


def test_terminal_backend_and_computer_use_grants_are_real(tmp_path,
                                                           monkeypatch):
    from voicebridge import hermes_compat as HC
    from voicebridge.permissions import permissions_store

    cfg = _cfg(tmp_path, monkeypatch)

    code, out = HC.dispatch(cfg, "PUT", "/api/tools/terminal/backend", {},
                            {"backend": "local"})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    code, out = HC.dispatch(cfg, "PUT", "/api/tools/terminal/backend", {},
                            {"backend": "local", "confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["active"] == "local" and out["grant_id"], out
    caps = {g.capability: g for g in permissions_store(cfg).grants()}
    assert "terminal.backend" in caps, caps
    assert "local" in caps["terminal.backend"].scopes, caps["terminal.backend"]

    code, out = HC.dispatch(
        cfg, "POST", "/api/tools/computer-use/permissions/grant", {}, {})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    code, out = HC.dispatch(
        cfg, "POST", "/api/tools/computer-use/permissions/grant", {},
        {"scope": "desktop", "confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["grant_id"] and out["scope"] == "desktop", out
    caps = {g.capability: g for g in permissions_store(cfg).grants()}
    assert "computer_use.access" in caps, caps


def test_profile_export_is_real_confirm_gated(tmp_path, monkeypatch):
    from voicebridge import brain as B
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    B.principal_add(cfg, "work", "person", "Work", requested_by="t",
                    confirmed=True)
    B.profile_set(cfg, "work", "preferred_name", "Ada", requested_by="t",
                  confirmed=True)

    code, out = HC.dispatch(cfg, "POST", "/api/profiles/work/export", {}, {})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)

    code, out = HC.dispatch(cfg, "POST", "/api/profiles/work/export", {},
                            {"confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["principal"] == "work", out
    assert out["profiles"]["default"]["preferred_name"]["value"] == "Ada", out

    # unknown principal with confirm is an honest typed 404/400, never a fake.
    code, out = HC.dispatch(cfg, "POST", "/api/profiles/nobody/export", {},
                            {"confirm": True})
    assert code not in (200, 500), (code, out)
    assert out["ok"] is False and out["code"], out


def test_gateway_supervision_uses_real_approval_path(tmp_path, monkeypatch):
    from voicebridge import gateway_peers as GW
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)

    # No gateway id: the real module's typed single-event-path refusal.
    for action in ("start", "stop"):
        code, out = HC.dispatch(cfg, "POST", f"/api/gateway/{action}", {}, {})
        assert code == 403, (action, code, out)
        assert out["code"] == GW.NO_SUPERVISION_CODE, (action, out)
        assert out["ok"] is False and out["qubit_counterpart"], out
        assert out["owner"] == "qubit-policy", out

    # A gateway id requires explicit confirmation (real broker path).
    code, out = HC.dispatch(cfg, "POST", "/api/gateway/start", {},
                            {"gateway_id": "gw1"})
    assert code == 409 and out["code"] == GW.CONFIRM_CODE, (code, out)

    # Confirmed but unknown gateway is an honest 404, never a false success.
    code, out = HC.dispatch(cfg, "POST", "/api/gateway/start", {},
                            {"gateway_id": "gw1", "confirm": True})
    assert code == 404 and out["code"] == GW.NOT_FOUND_CODE, (code, out)
    assert out["ok"] is False and out["qubit_counterpart"], out


def test_fork_declared_safe_reads_are_served_by_bridge():
    """Safe reads the fork declares are fork-served; Qubit-owned/sensitive/
    mutating paths are never proxied to the fork."""
    import json
    import os
    import shutil
    import subprocess
    import tempfile

    home = Path(tempfile.mkdtemp(prefix="hermes-agent2b-"))
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["XDG_DATA_HOME"] = str(home / ".local" / "share")
    env.pop("PYTHONPATH", None)
    code = (
        "import json\n"
        "from voicebridge import hermes_asgi as HA\n"
        "hc, _, hb = HA.call('GET', '/api/health')\n"
        "sc, _, sb = HA.call('GET', '/api/status')\n"
        "print(json.dumps({"
        "'hc': hc, 'health': json.loads(hb),"
        "'sc': sc, 'status': json.loads(sb),"
        "'safe_fork': HA.safe_to_proxy('GET', '/api/status'),"
        "'safe_own': HA.safe_to_proxy('GET', '/api/qubit/usage'),"
        "'safe_secret': HA.safe_to_proxy('GET', '/api/config/raw'),"
        "'safe_write': HA.safe_to_proxy('POST', '/api/gateway/start')}))\n"
    )
    try:
        p = subprocess.run([sys.executable, "-c", code], cwd=str(REPO_ROOT),
                           env=env, capture_output=True, text=True, timeout=180)
        assert p.returncode == 0, p.stderr[-1500:]
        out = json.loads(p.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(home, ignore_errors=True)

    assert out["hc"] == 200 and out["health"].get("ok") is True, out
    # Real fork status payload (not the Qubit compat status envelope).
    assert out["sc"] == 200, out
    assert "overall" in out["status"] and "components" in out["status"], out
    # Safe fork read proxies; Qubit-owned, sensitive, and writes never do.
    assert out["safe_fork"] is True, out
    assert out["safe_own"] is False, out
    assert out["safe_secret"] is False, out
    assert out["safe_write"] is False, out



# ===========================================================================
# Wave 3: skills hub served from the Qubit-reviewed LOCAL catalog.
# ===========================================================================
def _local_manifest() -> dict:
    return {
        "skill_id": "local-tool", "version": "1.0.0", "name": "Local Tool",
        "description": "A locally reviewed tool", "source": "local:checkout",
        "revision": "1", "license": "MIT", "content_hash": "a" * 64,
        "capabilities": ["files.read"], "scopes": ["files:read"],
        "tier": "reversible", "sensitivity": "internal",
        "approval_policy": "confirm", "timeout_s": 30,
        "resource_limits": {}, "audit_events": ["skill.install"],
        "compatibility": "", "rollback": "remove the skill",
    }


def test_skills_hub_reads_are_real_local_catalog(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)

    code, out = HC.dispatch(cfg, "GET", "/api/skills/hub/official", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["skills"], out
    row = out["skills"][0]
    for key in ("identifier", "name", "source", "trust_level", "installed"):
        assert key in row, (key, row)

    code, out = HC.dispatch(cfg, "GET", "/api/skills/hub/sources", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["sources"][0]["id"] == "qubit-local", out
    assert out["index_available"] is False, out
    assert isinstance(out["featured"], list) and out["featured"], out

    code, out = HC.dispatch(cfg, "GET", "/api/skills/hub/search",
                            {"q": "gateway"}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["results"], out
    assert all("gateway" in (r["name"] + r["description"] + r["identifier"]).lower()
               for r in out["results"]), out
    assert out["source_counts"]["qubit-local"] == len(out["results"]), out

    code, out = HC.dispatch(cfg, "GET", "/api/skills/hub/search",
                            {"q": ""}, {})
    assert code == 200 and out["results"] == [], (code, out)

    code, out = HC.dispatch(cfg, "GET", "/api/skills/hub/preview",
                            {"identifier": "qubit-gateway"}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["identifier"] == "qubit-gateway", out
    assert isinstance(out["skill_md"], str) and out["skill_md"], out
    assert isinstance(out["files"], list) and out["files"], out

    # Typed negatives: missing, remote, and unknown local identifiers.
    code, out = HC.dispatch(cfg, "GET", "/api/skills/hub/preview", {}, {})
    assert (code, out["code"]) == (400, "MALFORMED"), (code, out)
    code, out = HC.dispatch(cfg, "GET", "/api/skills/hub/preview",
                            {"identifier": "https://evil.example/x"}, {})
    assert (code, out["code"]) == (403, "BLOCKED_REMOTE_SOURCE"), (code, out)
    assert out["qubit_counterpart"], out
    code, out = HC.dispatch(cfg, "GET", "/api/skills/hub/preview",
                            {"identifier": "nope-nope"}, {})
    assert (code, out["code"]) == (404, "SKILL_NOT_FOUND"), (code, out)


def test_skills_hub_scan_validates_local_manifest(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    good = tmp_path / "skill.json"
    good.write_text(json.dumps(_local_manifest()))
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"skill_id": "bad", "version": "x"}))

    code, out = HC.dispatch(cfg, "GET", "/api/skills/hub/scan",
                            {"identifier": str(good)}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["verdict"] == "clean" and out["policy"] == "allow", out
    assert out["findings"] == [] and out["severity_counts"]["critical"] == 0, out

    code, out = HC.dispatch(cfg, "GET", "/api/skills/hub/scan",
                            {"identifier": str(bad)}, {})
    assert code == 200 and out["verdict"] == "blocked", (code, out)
    assert out["policy"] == "block" and out["findings"], out
    assert out["severity_counts"]["high"] >= 1, out

    code, out = HC.dispatch(cfg, "GET", "/api/skills/hub/scan",
                            {"identifier": "github:evil/skill"}, {})
    assert (code, out["code"]) == (403, "BLOCKED_REMOTE_SOURCE"), (code, out)

    code, out = HC.dispatch(cfg, "GET", "/api/skills/hub/scan", {}, {})
    assert (code, out["code"]) == (400, "MALFORMED"), (code, out)


def test_skills_hub_mutations_are_real_confirm_gated(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC
    from voicebridge.permissions import permissions_store

    cfg = _cfg(tmp_path, monkeypatch)
    man = tmp_path / "skill.json"
    man.write_text(json.dumps(_local_manifest()))

    # Empty body mirrors the fork's remote-refusal for an unknown identifier.
    code, out = HC.dispatch(cfg, "POST", "/api/skills/hub/install", {}, {})
    assert (code, out["code"]) == (403, "BLOCKED_REMOTE_SOURCE"), (code, out)
    assert out["qubit_counterpart"], out

    body = {"identifier": str(man)}
    code, out = HC.dispatch(cfg, "POST", "/api/skills/hub/install", {}, body)
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    code, out = HC.dispatch(cfg, "POST", "/api/skills/hub/install", {},
                            {**body, "confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["skill_id"] == "local-tool" and out["status"] == "staged", out
    assert permissions_store(cfg).skill("local-tool") is not None

    # A duplicate install is an honest typed 400, never a false success.
    code, out = HC.dispatch(cfg, "POST", "/api/skills/hub/install", {},
                            {**body, "confirm": True})
    assert code == 400 and out["ok"] is False, (code, out)

    # update requires confirm; a staged record with no prior active version is
    # an honest SKILL_VERSIONED gate, then rollback works once enabled.
    code, out = HC.dispatch(cfg, "POST", "/api/skills/hub/update", {},
                            {"name": "local-tool"})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    code, out = HC.dispatch(cfg, "POST", "/api/skills/hub/update", {},
                            {"name": "local-tool", "confirm": True})
    assert (code, out["code"]) == (403, "SKILL_VERSIONED"), (code, out)
    assert out["qubit_counterpart"], out
    permissions_store(cfg).set_skill_status("local-tool", "active",
                                            requested_by="t", confirmed=True)
    code, out = HC.dispatch(cfg, "POST", "/api/skills/hub/update", {},
                            {"name": "local-tool", "confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["status"] == "staged", out
    code, out = HC.dispatch(cfg, "POST", "/api/skills/hub/update", {},
                            {"name": "ghost", "confirm": True})
    assert (code, out["code"]) == (404, "SKILL_NOT_FOUND"), (code, out)

    # uninstall requires confirm, then removes the real record.
    code, out = HC.dispatch(cfg, "POST", "/api/skills/hub/uninstall", {},
                            {"name": "local-tool"})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    code, out = HC.dispatch(cfg, "POST", "/api/skills/hub/uninstall", {},
                            {"name": "local-tool", "confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    assert permissions_store(cfg).skill("local-tool") is None


# ===========================================================================
# Wave 3: real memory / toolset / profile reads (PARTIAL -> IMPLEMENTED).
# ===========================================================================
def test_memory_reads_are_real(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)

    code, out = HC.dispatch(cfg, "GET", "/api/memory", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["active"] == "qubit-local", out
    assert out["providers"][0]["name"] == "qubit-local", out
    assert "store_state" in out and "status" in out, out
    assert isinstance(out["builtin_files"], dict), out

    # PUT is a real persisted local-provider selection.
    code, out = HC.dispatch(cfg, "PUT", "/api/memory/provider", {},
                            {"provider": "qubit-local"})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["active"] == "qubit-local", out

    # Cloud sync stays a typed refusal, never a fake 200.
    code, out = HC.dispatch(cfg, "PUT", "/api/memory/provider", {},
                            {"provider": "cloud"})
    assert (code, out["code"]) == (400, "MEMORY_LOCAL_ONLY"), (code, out)

    code, out = HC.dispatch(cfg, "GET",
                            "/api/memory/providers/qubit-local/config", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["name"] == "qubit-local" and "fields" in out and "config" in out, out

    code, out = HC.dispatch(cfg, "GET",
                            "/api/memory/providers/nope/config", {}, {})
    assert (code, out["code"]) == (404, "NOT_FOUND"), (code, out)


def test_toolset_reads_are_real(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)

    code, out = HC.dispatch(cfg, "GET", "/api/tools/toolsets/web/config", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["name"] == "web", out
    assert isinstance(out["providers"], list), out
    assert isinstance(out["has_category"], bool), out

    code, out = HC.dispatch(cfg, "GET", "/api/tools/toolsets/image/models", {},
                            {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["name"] == "image" and out["plugin"] == "ollama-http", out
    assert out["models"] and all("id" in m and "display" in m
                                 for m in out["models"]), out


def test_profile_setup_command_is_real(tmp_path, monkeypatch):
    from voicebridge import brain as B
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    B.principal_add(cfg, "work", "person", "Work", requested_by="t",
                    confirmed=True)

    code, out = HC.dispatch(cfg, "GET", "/api/profiles/work/setup-command",
                            {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["registered"] is True and "work" in out["command"], out

    code, out = HC.dispatch(cfg, "GET", "/api/profiles/ghost/setup-command",
                            {}, {})
    assert code == 200 and out["registered"] is False, (code, out)
    assert out["command"] and out["qubit_counterpart"], out


# ===========================================================================
# Wave 4: real brokered file / git / blueprint writes.
# ===========================================================================
def _repo(tmp_path, name="work"):
    import os
    import shutil
    import subprocess

    assert shutil.which("git"), "git is required"
    root = tmp_path / name
    root.mkdir(exist_ok=True)
    env = {"GIT_CONFIG_NOSYSTEM": "1", "HOME": str(tmp_path),
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=env)
    (root / "notes.md").write_text("hello\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, env=env)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "init"],
                   check=True, env=env)
    return root, env


def _project(cfg, folder):
    from voicebridge import projects as PJ

    created = PJ.create(cfg, "Work", [str(folder)], requested_by="t",
                        confirmed=True, use=True)
    assert created.get("state") == "OK", created
    return created


def test_fs_write_text_is_real_confined_broker(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    root, _ = _repo(tmp_path)
    _project(cfg, root)
    target = root / "notes.md"

    body = {"path": str(target), "content": "new body"}
    code, out = HC.dispatch(cfg, "POST", "/api/fs/write-text", {}, body)
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    assert target.read_text() == "hello\n", "unconfirmed must not write"

    code, out = HC.dispatch(cfg, "POST", "/api/fs/write-text", {},
                            {**body, "confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["state"] == "OK", out
    assert out["receipt"]["code"] == "FILE_PATCH_APPLIED", out
    assert target.read_text() == "new body\n", target.read_text()

    # Missing path is a typed 400, never a host write.
    code, out = HC.dispatch(cfg, "POST", "/api/fs/write-text", {},
                            {"confirm": True})
    assert (code, out["code"]) == (400, "MALFORMED"), (code, out)

    # A path outside the approved roots is a typed confinement refusal.
    outside = tmp_path / "outside.txt"
    code, out = HC.dispatch(cfg, "POST", "/api/fs/write-text", {},
                            {"path": str(outside), "content": "x",
                             "confirm": True})
    assert code == 400 and out["ok"] is False, (code, out)
    assert out["code"].startswith("FILE_PATCH_"), out
    assert not outside.exists(), "outside-root write must not happen"


def test_git_local_writes_are_real_brokered_grants(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC
    from voicebridge.permissions import permissions_store

    cfg = _cfg(tmp_path, monkeypatch)
    root, _ = _repo(tmp_path)
    _project(cfg, root)

    code, out = HC.dispatch(cfg, "POST", "/api/git/review/stage", {}, {})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)

    code, out = HC.dispatch(cfg, "POST", "/api/git/review/stage", {},
                            {"confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["grant_id"] and out["root"] == str(root), out
    caps = {g.capability: g for g in permissions_store(cfg).grants()}
    assert "git.write.stage" in caps, caps
    assert str(root) in caps["git.write.stage"].scopes, caps

    # Unknown repo is a typed 400, never a shell write.
    other = tmp_path / "not-a-repo"
    other.mkdir()
    code, out = HC.dispatch(cfg, "POST", "/api/git/review/commit",
                            {"root": str(other)}, {"confirm": True})
    assert code == 400 and out["ok"] is False, (code, out)
    assert out["code"], out

    # Network/gh verbs stay typed gates (no safe local equivalent).
    for path in ("/api/git/review/push", "/api/git/review/create-pr",
                 "/api/git/review/pr-list"):
        code, out = HC.dispatch(cfg, "POST", path, {}, {"confirm": True})
        assert code == 403, (path, code, out)
        assert out["code"] == "GIT_WRITE_BROKERED", (path, out)


def test_blueprint_instantiate_is_real_confirm_gated(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC
    from voicebridge import schedules as S

    cfg = _cfg(tmp_path, monkeypatch)
    payload = {"blueprint": "standup",
               "values": {"prompt": "summarize notes",
                          "when": "once in 10 minutes"}}

    code, out = HC.dispatch(cfg, "POST", "/api/cron/blueprints/instantiate",
                            {}, payload)
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    assert S.list_jobs(cfg)["jobs"] == [], "unconfirmed must not create"

    code, out = HC.dispatch(cfg, "POST", "/api/cron/blueprints/instantiate",
                            {}, {**payload, "confirm": True})
    assert code == 200, (code, out)
    assert out["name"] == "standup", out
    assert len(S.list_jobs(cfg)["jobs"]) == 1, S.list_jobs(cfg)

    # A blueprint with no schedule expression is a typed 400.
    code, out = HC.dispatch(cfg, "POST", "/api/cron/blueprints/instantiate",
                            {}, {"blueprint": "x", "confirm": True})
    assert (code, out["code"]) == (400, "BLUEPRINT_BAD_FILL"), (code, out)


def test_files_upload_is_real_text_broker_with_binary_gate(tmp_path,
                                                           monkeypatch):
    import base64

    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    root, _ = _repo(tmp_path)
    _project(cfg, root)
    target = root / "uploaded.txt"

    body = {"path": str(target), "content": "uploaded text"}
    code, out = HC.dispatch(cfg, "POST", "/api/files/upload", {}, body)
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    code, out = HC.dispatch(cfg, "POST", "/api/files/upload", {},
                            {**body, "confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    assert target.read_text() == "uploaded text", target.read_text()

    # Genuinely-binary content is a typed gate and is never stored.
    blob = base64.b64encode(b"\x00\x01\x02\xff").decode()
    code, out = HC.dispatch(cfg, "POST", "/api/files/upload", {},
                            {"path": str(root / "bin.dat"),
                             "content_base64": blob, "confirm": True})
    assert (code, out["code"]) == (403, "FILES_BINARY"), (code, out)
    assert out["qubit_counterpart"], out
    assert not (root / "bin.dat").exists()

    code, out = HC.dispatch(cfg, "POST", "/api/files/upload", {},
                            {"confirm": True})
    assert (code, out["code"]) == (400, "MALFORMED"), (code, out)


# ===========================================================================
# Wave 5: the last files-fs writes (mkdir / upload-stream) are real
# confirm-gated confined operations, not blanket refusals.
# ===========================================================================
def test_files_mkdir_is_real_confined_confirm_gated(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    root, _ = _repo(tmp_path)
    _project(cfg, root)
    target = root / "newdir"

    # Positive path is gated until confirmed; the negative holds (no write).
    code, out = HC.dispatch(cfg, "POST", "/api/files/mkdir", {},
                            {"path": str(target)})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    assert out["ok"] is False and out["qubit_counterpart"], out
    assert not target.exists(), "unconfirmed must not create"

    code, out = HC.dispatch(cfg, "POST", "/api/files/mkdir", {},
                            {"path": str(target), "confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["created"] is True and target.is_dir(), (out, target)

    # An existing target is a typed 400, never a silent success.
    code, out = HC.dispatch(cfg, "POST", "/api/files/mkdir", {},
                            {"path": str(target), "confirm": True})
    assert code == 400 and out["ok"] is False, (code, out)
    assert out["code"] and out.get("owner") and out.get("rollback"), out

    # Outside the approved roots: typed confinement refusal, no host write.
    outside = tmp_path / "outside"
    code, out = HC.dispatch(cfg, "POST", "/api/files/mkdir", {},
                            {"path": str(outside), "confirm": True})
    assert code == 400 and out["ok"] is False, (code, out)
    assert out["code"].startswith("FILE_PATCH_"), out
    assert not outside.exists(), "outside-root mkdir must not happen"

    code, out = HC.dispatch(cfg, "POST", "/api/files/mkdir", {},
                            {"confirm": True})
    assert (code, out["code"]) == (400, "MALFORMED"), (code, out)


def test_files_upload_stream_is_real_text_broker(tmp_path, monkeypatch):
    import base64

    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    root, _ = _repo(tmp_path)
    _project(cfg, root)
    target = root / "streamed.txt"

    body = {"path": str(target), "content": "streamed text"}
    code, out = HC.dispatch(cfg, "POST", "/api/files/upload-stream", {}, body)
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    assert not target.exists(), "unconfirmed must not write"

    code, out = HC.dispatch(cfg, "POST", "/api/files/upload-stream", {},
                            {**body, "confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    assert target.read_text() == "streamed text", target.read_text()

    # A genuinely-binary stream stays a typed gate and is never stored.
    blob = base64.b64encode(b"\x00\xff\x00").decode()
    code, out = HC.dispatch(cfg, "POST", "/api/files/upload-stream", {},
                            {"path": str(root / "b.bin"),
                             "content_base64": blob, "confirm": True})
    assert (code, out["code"]) == (403, "FILES_BINARY"), (code, out)
    assert not (root / "b.bin").exists()

    code, out = HC.dispatch(cfg, "POST", "/api/files/upload-stream", {},
                            {"confirm": True})
    assert (code, out["code"]) == (400, "MALFORMED"), (code, out)


# ===========================================================================
# Wave 5: skill content/toggle and toolset config writes are real
# confirm-gated Qubit operations (same PermissionsStore / dashboard ops as
# CLI + voice), not blanket gates.
# ===========================================================================
def test_skills_content_update_is_real_confirm_gated(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC
    from voicebridge.permissions import permissions_store

    cfg = _cfg(tmp_path, monkeypatch)
    man = tmp_path / "skill.json"
    man.write_text(json.dumps(_local_manifest()))
    code, out = HC.dispatch(cfg, "POST", "/api/skills/hub/install", {},
                            {"identifier": str(man), "confirm": True})
    assert code == 200 and out["status"] == "staged", (code, out)
    permissions_store(cfg).set_skill_status("local-tool", "active",
                                            requested_by="t", confirmed=True)

    code, out = HC.dispatch(cfg, "PUT", "/api/skills/content", {},
                            {"name": "local-tool"})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    code, out = HC.dispatch(cfg, "PUT", "/api/skills/content", {},
                            {"name": "local-tool", "confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["skill_id"] == "local-tool" and out["status"] == "staged", out

    # Unknown/missing names are honest typed refusals, never fabricated.
    code, out = HC.dispatch(cfg, "PUT", "/api/skills/content", {},
                            {"name": "nope", "confirm": True})
    assert (code, out["code"]) == (404, "NOT_FOUND"), (code, out)
    code, out = HC.dispatch(cfg, "PUT", "/api/skills/content", {},
                            {"confirm": True})
    assert (code, out["code"]) == (400, "MALFORMED"), (code, out)


def test_skills_toggle_is_real_confirm_gated(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    man = tmp_path / "skill.json"
    man.write_text(json.dumps(_local_manifest()))
    code, out = HC.dispatch(cfg, "POST", "/api/skills/hub/install", {},
                            {"identifier": str(man), "confirm": True})
    assert code == 200 and out["status"] == "staged", (code, out)

    code, out = HC.dispatch(cfg, "PUT", "/api/skills/toggle", {},
                            {"name": "local-tool", "enabled": False})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    assert out["confirmation_id"], out
    assert out["qubit_counterpart"], out

    # An unknown skill still stages a real approval request (never a fake 2xx).
    code, out = HC.dispatch(cfg, "PUT", "/api/skills/toggle", {},
                            {"name": "ghost", "enabled": True})
    assert code != 500, (code, out)
    assert out.get("ok") is not True or out.get("state") == "executed", out


def test_toolset_write_variants_are_real_confirm_gated(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC
    from voicebridge.permissions import permissions_store

    cfg = _cfg(tmp_path, monkeypatch)
    cases = [
        ("PUT", "/api/tools/toolsets/web", {"name": "web"}),
        ("PUT", "/api/tools/toolsets/web/model", {"name": "web"}),
        ("PUT", "/api/tools/toolsets/web/provider",
         {"name": "web", "scope": "openai"}),
        ("PUT", "/api/tools/toolsets/web/env", {"name": "web"}),
        ("POST", "/api/tools/toolsets/web/post-setup", {"name": "web"}),
    ]
    for method, path, body in cases:
        code, out = HC.dispatch(cfg, method, path, {}, body)
        assert code == 409 and out["code"] == "CONFIRM_REQUIRED", \
            (path, code, out)
        code, out = HC.dispatch(cfg, method, path, {},
                                {**body, "confirm": True})
        assert code == 200 and out["ok"] is True, (path, code, out)
        assert out["grant_id"], (path, out)

    caps = {g.capability for g in permissions_store(cfg).grants()}
    assert "toolset.web" in caps, caps

    # A missing toolset name is a typed 400, never a fabricated grant.
    code, out = HC.dispatch(cfg, "PUT", "/api/tools/toolsets/web/provider", {},
                            {"confirm": True})
    assert (code, out["code"]) == (400, "MALFORMED"), (code, out)


def test_memory_provider_setup_is_real_local_confirm_gated(tmp_path,
                                                           monkeypatch):
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)

    # Unconfirmed setup stages; confirmed setup persists the local provider.
    code, out = HC.dispatch(cfg, "POST",
                            "/api/memory/providers/qubit-local/setup", {},
                            {"tier": "core"})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    code, out = HC.dispatch(cfg, "POST",
                            "/api/memory/providers/qubit-local/setup", {},
                            {"tier": "core", "confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["name"] == "qubit-local" and out["config"]["tier"] == "core", out

    # The write is observable on the next read (same local store).
    code, out = HC.dispatch(cfg, "GET",
                            "/api/memory/providers/qubit-local/config", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["config"]["tier"] == "core", out

    # A non-local provider is a typed refusal, never a fake 200.
    code, out = HC.dispatch(cfg, "PUT", "/api/memory/providers/cloud/config",
                            {}, {"confirm": True})
    assert (code, out["code"]) == (403, "MEMORY_LOCAL_ONLY"), (code, out)
    assert out["qubit_counterpart"], out


def test_config_and_env_writes_are_real_confirm_gated(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)

    # PUT /api/config: real settings override write, confirm-gated.
    code, out = HC.dispatch(cfg, "PUT", "/api/config", {},
                            {"logging": {"level": "DEBUG"}})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    code, out = HC.dispatch(cfg, "PUT", "/api/config", {},
                            {"logging": {"level": "DEBUG"}, "confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["config"]["logging.level"] == "DEBUG", out
    # An unknown settings key is a typed refusal, never a fabricated success.
    code, out = HC.dispatch(cfg, "PUT", "/api/config", {},
                            {"definitely_not_a_setting": True, "confirm": True})
    assert code == 403 and out["code"] == "UNKNOWN_CONFIG_KEY", (code, out)

    # Secret-shaped keys never enter the store (reference names only).
    code, out = HC.dispatch(cfg, "PUT", "/api/config", {},
                            {"api_key": "sk-live", "confirm": True})
    assert (code, out["code"]) == (403, "SECRET_WRITE_REFUSED"), (code, out)

    # PUT /api/env: a vault reference is persisted; a raw secret is refused.
    code, out = HC.dispatch(cfg, "PUT", "/api/env", {},
                            {"name": "OPENAI_KEY", "value": "vault:openai"})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    code, out = HC.dispatch(cfg, "PUT", "/api/env", {},
                            {"name": "OPENAI_KEY", "value": "vault:openai",
                             "confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    code, out = HC.dispatch(cfg, "PUT", "/api/env", {},
                            {"name": "RAW", "value": "sk-live-123",
                             "confirm": True})
    assert (code, out["code"]) == (403, "SECRET_WRITE_REFUSED"), (code, out)

    # DELETE /api/env removes the reference (never a fabricated removal).
    code, out = HC.dispatch(cfg, "DELETE", "/api/env", {},
                            {"name": "OPENAI_KEY", "delete": True,
                             "confirm": True})
    assert code == 200 and out.get("removed") is True, (code, out)
    code, out = HC.dispatch(cfg, "PUT", "/api/env", {},
                            {"confirm": True})
    assert (code, out["code"]) == (400, "MALFORMED"), (code, out)


# ===========================================================================
# Wave 6: the ops backup/import verbs run the real passphrase-sealed broker
# (backup_create / backup_restore), not the read-only guidance/inspect rows.
# ===========================================================================
def test_ops_backup_is_real_passphrase_sealed(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    state = Path(cfg.state_dir())
    pw = "agent2b backup passphrase"

    # Unconfirmed: the shared broker stages (409), never a fabricated 2xx and
    # never a written bundle.
    code, out = HC.dispatch(cfg, "POST", "/api/ops/backup", {},
                            {"passphrase": pw, "label": "agent2b"})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    assert out["ok"] is False and out["confirmation_id"], out
    cid = out["confirmation_id"]
    assert not list(state.glob("portable-*.vbp")), \
        "unconfirmed backup must not write"

    # A confirm without the re-supplied passphrase fails closed.
    code, out = HC.dispatch(cfg, "POST", "/api/ops/confirm", {},
                            {"confirmation_id": cid})
    assert code == 400 and out["code"] == "SENSITIVE_REQUIRED", (code, out)
    assert out["ok"] is False, out
    assert not list(state.glob("portable-*.vbp")), \
        "missing passphrase must not write"

    # The confirmed passphrase performs the real sealed backup.
    code, out = HC.dispatch(cfg, "POST", "/api/ops/confirm", {},
                            {"confirmation_id": cid,
                             "sensitive": {"passphrase": pw}})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["state"] == "executed", out
    bundle = Path(out["receipt"]["result"]["path"])
    assert bundle.exists() and bundle.suffix == ".vbp", bundle
    assert pw.encode() not in bundle.read_bytes(), "bundle must stay sealed"
    assert pw not in (state / "approvals.jsonl").read_text(encoding="utf-8")


def test_ops_import_is_real_staged_restore_review(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    pw = "agent2b import passphrase"

    # Build a real sealed bundle through the same broker first.
    _, staged = HC.dispatch(cfg, "POST", "/api/ops/backup", {},
                            {"passphrase": pw, "label": "agent2b"})
    assert staged["code"] == "CONFIRM_REQUIRED", staged
    _, done = HC.dispatch(cfg, "POST", "/api/ops/confirm", {},
                          {"confirmation_id": staged["confirmation_id"],
                           "sensitive": {"passphrase": pw}})
    assert done["ok"] is True, done
    bundle = done["receipt"]["result"]["path"]

    # Staging the import is a 409; the review is not fabricated up front.
    code, out = HC.dispatch(cfg, "POST", "/api/ops/import", {},
                            {"bundle": bundle, "passphrase": pw})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    cid = out["confirmation_id"]

    # Missing passphrase at confirm is a typed refusal, not a host restore.
    code, out = HC.dispatch(cfg, "POST", "/api/ops/confirm", {},
                            {"confirmation_id": cid})
    assert code == 400 and out["code"] == "SENSITIVE_REQUIRED", (code, out)

    # Confirmed: the real bundle is decrypted into a reviewed restore plan.
    code, out = HC.dispatch(cfg, "POST", "/api/ops/confirm", {},
                            {"confirmation_id": cid,
                             "sensitive": {"passphrase": pw}})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["state"] == "executed", out
    result = out["receipt"]["result"]
    assert result["dry_run"] is True, result
    assert isinstance(result["plan"], list) and result["plan"], result
