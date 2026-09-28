"""D4 — OpenClaw skills rows closed by real capability + negative proof.

* OC-40 plugin runtime extensibility (tools/channels/providers/hooks) —
  ``voicebridge/plugin_runtime.py`` in-process handler host with the
  confirm/estop invoke gate.
* OC-D10 MCP hosted apps — the same module's loopback MCP transport with
  hashed attach grants and no outbound network.
* OC-39 remote plugin/skill install — pinned-source policy: local discovery
  is real, remote sources are typed-refused.
* OC-41 / OC-D29 Skill Workshop — ``voicebridge/verified_skills/workshop.py``
  agent-owned collection lifecycle over the verified-skill store.
"""
from __future__ import annotations

import http.client
import json
from pathlib import Path

import pytest

from voicebridge import plugin_runtime as PR
from voicebridge import verified_skills as V

DIGEST = "a" * 64


@pytest.fixture(autouse=True)
def _isolate_handlers():
    """The in-process handler table is process-global; restore it after each
    test so binding a handler here never leaks into another suite."""
    saved = dict(PR._HANDLERS)
    yield
    PR._HANDLERS.clear()
    PR._HANDLERS.update(saved)


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _manifest(pid, kind="tool", **over):
    m = {"id": pid, "kind": kind, "entrypoint": f"{pid}:main", "digest": DIGEST,
         "label": pid, "version": "1.0.0", "capabilities": ["run"]}
    m.update(over)
    return m


def _rpc(host, token, method, params=None, req_id=1):
    body = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method,
                       "params": params or {}}).encode()
    conn = http.client.HTTPConnection(host.host, host.port, timeout=5)
    try:
        conn.request("POST", "/mcp", body=body,
                     headers={"Content-Type": "application/json",
                              "Authorization": f"Bearer {token}"})
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read().decode())
    finally:
        conn.close()


# ------------------------------------------------------------------ OC-40
def test_oc40_all_kinds_bind_and_invoke(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    for kind in PR.PLUGIN_KINDS:
        pid = f"acme.{kind}"
        PR.bind_handler(cfg, manifest=_manifest(pid, kind=kind),
                        handler=lambda a, k=kind: {"kind": k,
                                                   "got": a.get("x")},
                        confirmed=True)
    for kind in PR.PLUGIN_KINDS:
        out = PR.invoke(cfg, f"acme.{kind}", args={"x": 7}, confirmed=True)
        assert out["result"] == {"kind": kind, "got": 7}
    kinds = PR.status(cfg)["handler_kinds"]
    assert kinds["builtin.echo"] == "tool"
    for kind in PR.PLUGIN_KINDS:
        assert kinds[f"acme.{kind}"] == kind


def test_oc40_confirmation_required_and_unbound_entrypoint_never_runs(
        tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(PR.PluginRuntimeError) as exc:
        PR.bind_handler(cfg, manifest=_manifest("acme.tool"),
                        handler=lambda a: a)
    assert exc.value.code == PR.CONFIRM_CODE
    # a manifest entrypoint is metadata, never executable code
    PR.register(cfg, manifest=_manifest("acme.manifest"), confirmed=True)
    with pytest.raises(PR.PluginRuntimeError) as exc:
        PR.invoke(cfg, "acme.manifest", confirmed=True)
    assert exc.value.code == PR.UNAVAILABLE_CODE


def test_oc40_estop_fails_closed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge.power import power_control_store

    store = power_control_store(cfg)
    store.set(emergency_stop=True, global_disable=False, issued_by="cli:user")
    try:
        with pytest.raises(PR.PluginRuntimeError) as exc:
            PR.bind_handler(cfg, manifest=_manifest("acme.tool"),
                            handler=lambda a: a, confirmed=True)
        assert exc.value.code == PR.ESTOP_CODE
        with pytest.raises(PR.PluginRuntimeError) as exc:
            PR.invoke(cfg, "builtin.echo", args={"text": "x"}, confirmed=True)
        assert exc.value.code == PR.ESTOP_CODE
    finally:
        store.set(emergency_stop=False, global_disable=False,
                  issued_by="cli:user")


# ----------------------------------------------------------------- OC-D10
def test_ocd10_attach_token_hashed_once_and_revocable(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(PR.PluginRuntimeError) as exc:
        PR.mcp_attach(cfg, "claude")
    assert exc.value.code == PR.MCP_CONFIRM_CODE
    out = PR.mcp_attach(cfg, "claude", confirmed=True)
    token = out["bearer_token"]
    assert out["shown_once"] is True and len(token) > 20
    raw = (Path(cfg.state_dir()) / PR.MCP_STORE_NAME).read_text()
    assert token not in raw
    assert PR.mcp_authenticate(cfg, token)["app_id"] == "claude"
    assert PR.mcp_authenticate(cfg, "wrong") is None
    assert "bearer_token" not in json.dumps(PR.mcp_grants(cfg)["grants"])
    assert PR.mcp_revoke(cfg, "claude", confirmed=True)["revoked"] == "claude"
    assert PR.mcp_authenticate(cfg, token) is None


def test_ocd10_loopback_serves_only_bound_handlers(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(PR.PluginRuntimeError) as exc:
        PR.mcp_serve(cfg)
    assert exc.value.code == PR.MCP_REFUSED_CODE
    PR.bind_handler(cfg, manifest=_manifest("acme.echo"),
                    handler=lambda a: {"echo": str(a.get("text", ""))[:40]},
                    confirmed=True)
    token = PR.mcp_attach(cfg, "loopapp", confirmed=True)["bearer_token"]
    host = PR.mcp_serve(cfg)
    try:
        assert host.host == PR.MCP_HOST and host.port > 0
        code, init = _rpc(host, token, "initialize")
        assert code == 200 and init["result"]["serverInfo"]["name"] == \
            "qubit-mcp"
        code, call = _rpc(host, token, "tools/call",
                          {"name": "acme.echo", "arguments": {"text": "hi"}})
        assert call["result"]["content"] == {"echo": "hi"}
        code, bad = _rpc(host, token, "tools/call",
                         {"name": "os.system", "arguments": {}})
        assert "error" in bad
        status, unauth = _rpc(host, "not-the-token", "initialize")
        assert status == 401 and "error" in unauth
    finally:
        host.stop()


# ------------------------------------------------------------------ OC-39
def test_oc39_remote_install_refused_local_discovery_real(tmp_path,
                                                          monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    assert PR.marketplace_source_kind("owner/repo") == "remote"
    assert PR.marketplace_source_kind("https://example.test/i") == "remote"
    refused = PR.marketplace_discover(cfg, "https://example.test/index")
    assert refused["code"] == PR.MARKETPLACE_REMOTE_CODE
    assert PR.marketplace_refuse_remote("owner/repo")["code"] == \
        PR.MARKETPLACE_REMOTE_CODE
    index = tmp_path / "index"
    index.mkdir()
    (index / "one.json").write_text(json.dumps(_manifest("acme.one")))
    assert PR.marketplace_source_kind(str(index)) == "local-dir"
    local = PR.marketplace_discover(cfg, str(index))
    assert local["network"] == "NONE"
    assert {p["id"] for p in local["plugins"]} == {"acme.one"}


# ----------------------------------------------------------- OC-41 / OC-D29
def _proposal(name="add_two"):
    return V.SkillProposal(name=name, interface=f"{name}(x)",
                           implementation="return x + 2",
                           acceptance_test="assert run(3) == 5",
                           task="add two to a number")


def test_ocd29_workshop_author_backup_restore_human_only(tmp_path,
                                                         monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    store = V.verified_skills_store(cfg)
    workshop = V.verified_skill_workshop(cfg, store=store,
                                         backup_dir=tmp_path / "bk")
    try:
        authored = workshop.author(_proposal(), author_kind="agent",
                                   requested_by="agent")
        assert authored["contract"] == "quarantined until a human owner verifies"
        assert authored["skill"]["status"] == V.ST_QUARANTINED
        backup = workshop.backup(label="wave")
        assert backup["digest"] and backup["backup_id"]
        listed = workshop.backups()["backups"]
        assert any(b["backup_id"] == backup["backup_id"] for b in listed)

        with pytest.raises(V.WorkshopError):
            workshop.restore(backup["backup_id"], requested_by="agent")
        restored = workshop.restore(backup["backup_id"], requested_by="user")
        assert restored["digest"] == backup["digest"]

        contracts = workshop.contracts()
        assert contracts["curator_roles"] == list(V.CURATOR_ROLES)
        assert all(c["enforced"] for c in contracts["contracts"])
        curator = workshop.curator_status()
        assert curator["curator"] == "QUBIT-OWNED"
    finally:
        store.close()


def test_ocd29_workshop_rejects_tampered_backup(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    store = V.verified_skills_store(cfg)
    workshop = V.verified_skill_workshop(cfg, store=store,
                                         backup_dir=tmp_path / "bk")
    try:
        workshop.author(_proposal("add_three"))
        backup = workshop.backup(label="tamper")
        path = Path(backup["path"])
        payload = json.loads(path.read_text())
        payload["snapshot"]["skills"] = []
        path.write_text(json.dumps(payload))
        with pytest.raises(V.WorkshopError):
            workshop.restore(backup["backup_id"], requested_by="user")
        with pytest.raises(V.WorkshopError):
            workshop.restore("../../escape", requested_by="user")
    finally:
        store.close()
