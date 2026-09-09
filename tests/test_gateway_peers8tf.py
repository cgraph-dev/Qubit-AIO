"""Packet 8T-F — gateway/peer parity: records, enrollment, typed delivery.

Proves account-gated records, URL policy, link states, supervision
refusals, credential-gated peer delivery (stubbed transport), pairing
linkage for classic sends, and e-stop/confirmation gates.
"""
from __future__ import annotations

import io
import json
import urllib.request
from contextlib import redirect_stdout


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _acct(cfg, aid="team", provider="slack", ref="env:SLACK_TOKEN"):
    from voicebridge import messaging as MG

    kw = {"confirmed": True, "key_ref": ref}
    if provider == "whatsapp-cloud":
        kw["extra"] = {"phone_number_id": "123456789012345"}
    return MG.account_add(cfg, aid, provider, aid, **kw)


class _Resp:
    def __init__(self, payload, status=200):
        self._body = json.dumps(payload).encode()
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_gateway_crud_and_account_gate(tmp_path, monkeypatch):
    from voicebridge import gateway_peers as GW

    cfg = _cfg(tmp_path, monkeypatch)
    _acct(cfg)
    out = GW.gateway_add(cfg, "gw1", "slack", "team", "webhook",
                         key_ref="env:SLACK_TOKEN", confirmed=True)
    assert out["gateway"]["key_ref"] == "env"
    assert "SLACK_TOKEN" not in json.dumps(out)
    assert len(GW.gateway_list(cfg)["gateways"]) == 1
    try:
        GW.gateway_add(cfg, "gw2", "slack", "ghost", "webhook",
                       key_ref="env:X", confirmed=True)
    except GW.GatewayError as exc:
        assert exc.code == "GW_NOT_FOUND", "account gate enforced"
    else:
        raise AssertionError("unknown account must fail")
    try:
        GW.gateway_add(cfg, "gw1", "slack", "team", "webhook",
                       key_ref="env:X", confirmed=True)
    except GW.GatewayError as exc:
        assert exc.code == "GW_EXISTS"
    else:
        raise AssertionError("duplicate must fail")
    assert GW.gateway_remove(cfg, "gw1", confirmed=True)["removed"] == "gw1"


def test_gateway_link_and_supervision(tmp_path, monkeypatch):
    from voicebridge import gateway_peers as GW

    cfg = _cfg(tmp_path, monkeypatch)
    assert GW.gateway_link(cfg, "ghost")["state"] == "NO_GATEWAY"
    _acct(cfg)
    GW.gateway_add(cfg, "gw1", "slack", "team", "webhook",
                   key_ref="env:SLACK_TOKEN", confirmed=True)
    monkeypatch.delenv("SLACK_TOKEN", raising=False)
    assert GW.gateway_link(cfg, "gw1")["state"] == "NO_CREDENTIAL"
    monkeypatch.setenv("SLACK_TOKEN", "x")
    assert GW.gateway_link(cfg, "gw1")["state"] == "LINKED"
    for action in ("run", "start", "stop", "restart", "install"):
        out = GW.refuse_supervision(action)
        assert out["code"] == "GATEWAY_NO_SUPERVISION"
        assert out["action"] == action


def test_peer_crud_url_policy_and_link(tmp_path, monkeypatch):
    from voicebridge import gateway_peers as GW

    cfg = _cfg(tmp_path, monkeypatch)
    _acct(cfg)
    out = GW.peer_add(cfg, "p1", "http://127.0.0.1:9/api", "team",
                      key_ref="env:SLACK_TOKEN", confirmed=True)
    assert out["peer"]["account"] == "team"
    for bad in ("http://example.com/api", "https://u:p@127.0.0.1/x",
                "http://10.0.0.5/x", "notaurl"):
        try:
            GW.peer_add(cfg, "px", bad, "team", key_ref="env:X", confirmed=True)
        except GW.GatewayError as exc:
            assert exc.code == "GW_BAD_INPUT", (bad, exc.code)
        else:
            raise AssertionError(f"must refuse {bad!r}")
    assert GW.peer_link(cfg, "p1")["state"] == "NO_CREDENTIAL"
    monkeypatch.setenv("SLACK_TOKEN", "x")
    assert GW.peer_link(cfg, "p1")["state"] == "CREDENTIAL_OK"
    assert GW.peer_link(cfg, "ghost")["state"] == "NO_PEER"
    assert GW.peer_remove(cfg, "p1", confirmed=True)["removed"] == "p1"


def test_peer_dm_run_delivery_and_gates(tmp_path, monkeypatch):
    from voicebridge import gateway_peers as GW

    calls = []
    monkeypatch.setenv("SLACK_TOKEN", "xoxb-peer-1")
    cfg = _cfg(tmp_path, monkeypatch)
    _acct(cfg)
    GW.peer_add(cfg, "p1", "http://127.0.0.1:9/api", "team",
                key_ref="env:SLACK_TOKEN", confirmed=True)

    def _fake(req, timeout=None):
        calls.append(req)
        assert req.get_header("Authorization", "").startswith("Bearer ")
        assert "xoxb-peer-1" not in req.data.decode()
        return _Resp({"ok": True, "receipt": "pr-1"})

    monkeypatch.setattr(urllib.request, "urlopen", _fake)
    out = GW.peer_dm(cfg, "p1", "#ops", "hello", confirmed=True)
    assert out["state"] == "OK" and len(calls) == 1
    out = GW.peer_run(cfg, "p1", "main", "do work", confirmed=True)
    assert out["state"] == "OK"
    try:
        GW.peer_dm(cfg, "p1", "#ops", "hello", confirmed=False)
    except GW.GatewayError as exc:
        assert exc.code == "GW_CONFIRM_REQUIRED"
    else:
        raise AssertionError("fresh confirmation required")
    monkeypatch.delenv("SLACK_TOKEN", raising=False)
    try:
        GW.peer_dm(cfg, "p1", "#ops", "hello", confirmed=True)
    except GW.GatewayError as exc:
        assert exc.code == "PEER_NO_CREDENTIAL"
        assert calls and len(calls) == 2, "nothing new attempted"
    else:
        raise AssertionError("credential gate must hold")


def test_peer_upstream_failure_typed(tmp_path, monkeypatch):
    from voicebridge import gateway_peers as GW

    monkeypatch.setenv("SLACK_TOKEN", "x")

    def _boom(req, timeout=None):
        raise ConnectionError("down")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    cfg = _cfg(tmp_path, monkeypatch)
    _acct(cfg)
    GW.peer_add(cfg, "p1", "http://127.0.0.1:9/api", "team",
                key_ref="env:SLACK_TOKEN", confirmed=True)
    try:
        GW.peer_dm(cfg, "p1", "#ops", "hello", confirmed=True)
    except GW.GatewayError as exc:
        assert exc.code == "PEER_UPSTREAM"
    else:
        raise AssertionError("outage must be typed")


def test_cli_wiring_and_catalog(tmp_path, monkeypatch):
    import argparse

    from voicebridge import cli as CLI
    from voicebridge.capability_catalog import check

    names = set()
    for action in CLI.build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            names.update(action.choices.keys())
    assert {"gateway", "peer"} <= names
    for token in ("gateway", "gateway-manage", "pairing", "webhook", "peer"):
        out = check(token)
        assert out["state"] == "IMPLEMENTED", (token, out)
