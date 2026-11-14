from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from voicebridge.business import effective_state
from voicebridge.business.bridge import h_business_agent_effective
from voicebridge.business.errors import BusinessError
from voicebridge.business.store import BusinessStore

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_effective_state.py"


def _load_verifier():
    spec = importlib.util.spec_from_file_location(
        "verify_effective_state_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Cfg:
    def __init__(self, state_dir: Path) -> None:
        self._state_dir = state_dir

    def state_dir(self) -> str:
        return str(self._state_dir)


def test_projection_reports_explicit_inherited_denied_and_unknown(tmp_path):
    verifier = _load_verifier()
    fixture = verifier._seed_fixture(tmp_path / "business")
    store: BusinessStore = fixture["store"]
    tenant = fixture["tenant"]
    agent = fixture["agent"]

    projection = effective_state.effective_agent_state(
        store, tenant.tenant_id, agent.agent_id)

    assert projection["schema"] == effective_state.SCHEMA
    assert projection["agent_id"] == agent.agent_id
    assert projection["status"]["value"] in {
        "projection", "idle", "live", "paused", "revoked", "archived", "unknown"}
    assert projection["identity"]["explicit"], "an explicit grant is visible"
    assert projection["identity"]["inherited"], "a role grant is inherited"
    assert projection["identity"]["revoked"]
    assert projection["identity"]["expired"]
    assert projection["capabilities"]["denied"], "the deny grant wins"
    effective = projection["capabilities"]["effective"]
    assert effective
    assert any(row.get("label") in {"explicit", "inherited"} for row in effective)
    assert projection["model"]["primary"]["value"]["route"] == "provider-a/model-a"
    assert projection["model"]["fallback"]["value"]["route"] == "provider-b/model-b"
    # No memory table exists: it is a typed unknown, never an invented scope.
    assert projection["memory"].get("scopes") == [] or projection["memory"].get(
        "reason")
    assert projection["leases"]["expired"]
    assert projection["approvals"]["pending"]
    assert projection["approvals"]["expired"]
    assert projection["audit"]["events"]
    assert projection["links"]
    assert projection["generated_at"]


def test_projection_refuses_cross_tenant_agent(tmp_path):
    verifier = _load_verifier()
    fixture = verifier._seed_fixture(tmp_path / "business")
    store: BusinessStore = fixture["store"]
    other = fixture["other"]["tenant"]
    agent = fixture["agent"]

    with pytest.raises(BusinessError) as excinfo:
        effective_state.effective_agent_state(
            store, other.tenant_id, agent.agent_id)
    assert excinfo.value.code == "AGENT_NOT_FOUND"


def test_read_only_bridge_handler_returns_the_projection(tmp_path):
    verifier = _load_verifier()
    fixture = verifier._seed_fixture(tmp_path / "business")
    store: BusinessStore = fixture["store"]
    tenant = fixture["tenant"]
    agent = fixture["agent"]

    with store.read() as conn:
        before = int(conn.execute(
            "SELECT COUNT(*) FROM bos_audit_event").fetchone()[0])

    cfg = _Cfg(tmp_path)
    status, body = h_business_agent_effective(
        cfg, {"tenant_id": tenant.tenant_id}, {}, {"id": agent.agent_id})
    assert status == 200
    assert body["ok"] is True
    assert body["projection"]["agent_id"] == agent.agent_id
    assert body["projection"]["schema"] == effective_state.SCHEMA

    with store.read() as conn:
        after = int(conn.execute(
            "SELECT COUNT(*) FROM bos_audit_event").fetchone()[0])
    assert after == before, "a read projection must not write an audit row"


def test_independent_verifier_passes_its_self_check():
    verifier = _load_verifier()
    report = verifier.run_self_check()
    assert report["ok"] is True
    assert report["checks_failed"] == 0
    assert report["checks_total"] >= 16
    names = {check["name"] for check in report["checks"]}
    assert "effective_set_matches_evaluator" in names
    assert "tenant_isolation" in names
    assert "status_is_projection_not_live" in names


def test_route_registration_and_projection_shape(tmp_path):
    source = (ROOT / "voicebridge" / "qubit_compat.py").read_text(
        encoding="utf-8")
    assert "h_business_agent_effective" in source
    assert "agents/(?P<id>[^/]+)/effective" in source
    verifier = _load_verifier()
    fixture = verifier._seed_fixture(tmp_path / "business")
    projection = effective_state.effective_agent_state(
        fixture["store"], fixture["tenant"].tenant_id, fixture["agent"].agent_id)
    encoded = json.dumps(projection, sort_keys=True)
    assert '"schema"' in encoded
    assert "redacted" not in encoded.lower()
