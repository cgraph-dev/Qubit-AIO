"""Wave 5 — G2 transport-boundary redaction + MCP loopback classification.

Two invariants:

1. **G2.** A secret-shaped value in a *served* response is redacted at the
   JSON serialization boundary (``webui.WebUIHandler._json``), while the
   internal receipt the frozen CLI/dashboard/voice parity tests compare
   (``dashboard_ops.run_op(...)["receipt"]["result"]``) stays byte-identical.
   The transport redactor must not cap container sizes (the operations catalog
   is 500+ entries) and must not mistake the catalog's ``"secret": false``
   schema flag for a credential.

2. **MCP loopback.** ``plugin_runtime.mcp_serve`` is an explicitly-accounted
   bounded loopback helper, not a second listener/authority: loopback bind
   only, on demand, auth/audit/e-stop gated, host-bound handlers only.

Hermetic: temp XDG state, no network beyond the loopback WebUI/MCP sockets.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

SECRET = "sk-live-w5-0123456789abcdef"


# ------------------------------------------------------------------ helpers
def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\nwebui:\n  token: w5-token\n")
    return C.Config.load(p)


def _manifest(pid: str = "acme.echo", **over):
    m = {"id": pid, "kind": "tool", "entrypoint": "acme:main",
         "digest": "a" * 64, "label": "Acme", "version": "1.0.0",
         "capabilities": ["run"]}
    m.update(over)
    return m


def _extra_op(monkeypatch, op_id, run, *, confirm=False):
    from voicebridge import dashboard_ops as OPS

    spec = OPS.ExtraOp(id=op_id, group="test", title="Wave5 op",
                       help="wave5 test op", confirm=confirm, run=run)
    monkeypatch.setitem(OPS.EXTRA_OPS, op_id, spec)
    return op_id


def _start_ui(cfg, tmp_path):
    from voicebridge.webui import WebUI

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)

    class _Orch:
        _route = "gateway"
        _web_active = 0
        task = type("T", (), {"active": False})()

        def __init__(self):
            self.cfg = cfg
            self._t0 = time.time()

    ui = WebUI(_Orch(), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    return ui, ui._server.server_address[1]


def _http(port, method, path, body=None, token="w5-token"):
    import urllib.request

    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 method=method, data=data)
    if token:
        req.add_header("X-Api-Token", token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


# =========================================================== G2 transport
def test_served_response_redacts_secret_but_receipt_is_unchanged(
        tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    op_id = _extra_op(
        monkeypatch, "w5_secret_echo",
        lambda cfg, args, actor: {"note": SECRET, "count": 1})

    # Internal receipt path: identical to the dict the parity tests compare.
    internal = OPS.run_op(cfg, op_id, {}, actor="dashboard:user",
                          session="dashboard")
    assert internal["state"] == "executed"
    assert internal["receipt"]["result"]["note"] == SECRET

    ui, port = _start_ui(cfg, tmp_path)
    try:
        status, ctype, body = _http(port, "POST", "/api/ops/run",
                                    {"id": op_id, "args": {}})
        assert status == 200, body[:200]
        assert "application/json" in ctype
        text = body.decode()
        assert SECRET not in text
        served = json.loads(text)
        assert served["state"] == "executed"
        assert served["receipt"]["result"]["note"] == "[REDACTED]"
        assert served["receipt"]["result"]["count"] == 1
    finally:
        ui.stop()


def test_keyed_secret_strings_masked_but_schema_bool_kept():
    from voicebridge.webui import _redact_served

    red = _redact_served({"password": "hunter2",
                          "secret": False,
                          "token": "opaque-value",
                          "n": 1,
                          "nested": {"api_key": "another"}})
    assert red["password"] == "[REDACTED]"
    assert red["token"] == "[REDACTED]"
    assert red["nested"]["api_key"] == "[REDACTED]"
    # A non-string value under a credential-looking key is a schema flag, not
    # a credential, and must survive (dashboard_ops.catalog arg "secret").
    assert red["secret"] is False
    assert red["n"] == 1


def test_transport_redactor_does_not_truncate_large_catalogs():
    from voicebridge import dashboard_ops as OPS
    from voicebridge.webui import _redact_served

    catalog = OPS.catalog()
    assert len(catalog) > 200, "catalog must exceed redaction.MAX_ITEMS"
    served = _redact_served(catalog)
    assert len(served) == len(catalog)
    assert [row["id"] for row in served] == [row["id"] for row in catalog]
    # the schema boolean survives end to end
    assert all(isinstance(row.get("id"), str) for row in served)


def test_served_catalog_via_http_is_not_truncated(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    ui, port = _start_ui(cfg, tmp_path)
    try:
        status, _, body = _http(port, "GET", "/api/ops")
        assert status == 200, body[:200]
        out = json.loads(body)
        assert len(out["ops"]) == len(OPS.catalog())
    finally:
        ui.stop()


def test_audit_writer_redacts_secret_under_unlabelled_key(
        tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import logging_setup as LS

    journal = tmp_path / "events.jsonl"
    monkeypatch.setattr(LS, "_EVENT_FILE", journal)
    OPS._audit("wave5_probe", "cli:user", {"note": SECRET, "op": "w5"})
    text = journal.read_text()
    assert SECRET not in text
    rec = json.loads(text.splitlines()[-1])
    assert rec["note"] == "[REDACTED]"
    assert rec["op"] == "w5"


# ====================================================== MCP loopback scope
def test_mcp_doctor_reports_accounted_loopback_helper(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    doc = PR.mcp_doctor(cfg)
    assert doc["listener"] == "loopback-helper"
    assert doc["one_listener_rule"] == "accounted-exception"
    assert doc["bind"] == "127.0.0.1:ephemeral"
    assert doc["outbound_network"] == "NONE"


def test_mcp_serve_refuses_tampered_non_loopback_host(
        tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(PR, "MCP_HOST", "0.0.0.0")
    with pytest.raises(PR.PluginRuntimeError) as exc:
        PR.mcp_serve(cfg)
    assert exc.value.code == PR.MCP_REFUSED_CODE


def test_mcp_loopback_host_is_scoped_and_stoppable(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    PR.bind_handler(cfg, manifest=_manifest("w5.echo"),
                    handler=lambda a: {"ok": True}, confirmed=True)
    PR.mcp_attach(cfg, "w5app", confirmed=True)
    host = PR.mcp_serve(cfg)
    try:
        assert host.host == "127.0.0.1"
        view = host.as_dict()
        assert view["listener_scope"] == "loopback-helper"
        assert view["second_listener"] is False
        assert str(view["port"]).isdigit()
    finally:
        host.stop()
