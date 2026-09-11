"""Packet 8U — OpenClaw backend tri-surface wiring.

Proves the three new backend modules (``plugin_runtime``,
``channels_inbound``, ``acp_client``) are reachable through all three
surfaces on the SAME backend:

* voice: registry entry resolves and executes (or resolves to its typed
  gate) through the shared ``voice_commands`` pipeline;
* CLI: the top-level family parses and dispatches to the same module;
* dashboard: the Tier-1 ops catalog exposes every entry and runs it on the
  same backend as voice.

Hermetic: tmp XDG state only; the channel ingress is exercised against a
real local ``WebUI`` server. No skips, no xfail.
"""
from __future__ import annotations

import hashlib
import hmac
import http.client
import io
import json
import shutil
from contextlib import redirect_stdout
from pathlib import Path

DIGEST = "b" * 64
CHANNEL_SECRET = "chan-wiring-8u-secret"
ACP_SECRET = "acp-wiring-8u-secret"

PLUGIN_VOICE = {
    "plugins_status": "qubit, plugin runtime status",
    "plugins_list": "qubit, list plugins",
    "plugins_show": "qubit, show plugin builtin.echo",
    "plugins_register": "qubit, register plugin manifest /tmp/acme.json",
    "plugins_invoke": "qubit, invoke plugin builtin.echo hello",
    "plugins_unregister": "qubit, unregister plugin acme.tool",
    "plugins_doctor": "qubit, plugin doctor",
}
CHANNEL_VOICE = {
    "channels_status": "qubit, channels status",
    "channels_catalog": "qubit, list channels",
    "channels_accounts": "qubit, list configured channels",
    "channels_configure": "qubit, configure channel discord env:q8u_secret",
    "channels_doctor": "qubit, channels doctor",
    "channels_unconfigure": "qubit, unconfigure channel discord",
}
ACP_VOICE = {
    "acp_client_status": "qubit, harness status",
    "acp_client_catalog": "qubit, list harnesses",
    "acp_client_configure": "qubit, configure harness claude-code env:acp_key",
    "acp_client_handshake": "qubit, harness handshake claude-code",
    "acp_client_receipts": "qubit, harness receipts",
    "acp_client_unconfigure": "qubit, unconfigure harness claude-code",
}
ALL_NEW = set(PLUGIN_VOICE) | set(CHANNEL_VOICE) | set(ACP_VOICE)


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p), p


def _ctx(cfg, session="voice", source="user"):
    from voicebridge import voice_commands as VC

    return VC.make_ctx(cfg, session=session, source=source)


def _manifest(pid="acme.tool"):
    return {"id": pid, "kind": "tool", "entrypoint": "acme.plugin:main",
            "digest": DIGEST, "label": "Acme", "version": "1.0.0",
            "capabilities": ["run"]}


def _speak_confirm(ctx, text):
    """Resolve + execute one voice command, consuming a confirmation if the
    entry is confirm-tier. Returns the terminal Outcome."""
    from voicebridge import voice_commands as VC

    res = VC.resolve(text)
    assert res.kind == VC.MATCH, (text, res.kind)
    out = VC.execute(ctx, res)
    if res.entry.confirm:
        assert out.kind == VC.NEEDS_CONFIRM, (text, out.kind, out.speech)
        out = VC.confirm_pending(ctx, out.confirmation_id)
    assert out.kind == VC.EXECUTED, (text, out.kind, out.speech, out.receipt)
    return out


def _cli(cfg_path, argv):
    from voicebridge import cli as CLI

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI.main([*argv, "--config", str(cfg_path)])
    body = buf.getvalue()
    try:
        parsed = json.loads(body)
    except ValueError:
        parsed = None
    return rc, parsed, body


# ------------------------------------------------------------------ plugins
def test_plugins_voice_executes_on_shared_backend(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR
    from voicebridge import voice_commands as VC

    cfg, _ = _cfg(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "tmp").mkdir()
    (tmp_path / "tmp" / "acme.json").write_text(json.dumps(_manifest()))
    ctx = _ctx(cfg)

    for eid in ("plugins_status", "plugins_list", "plugins_show",
                "plugins_register", "plugins_invoke", "plugins_unregister",
                "plugins_doctor"):
        res = VC.resolve(PLUGIN_VOICE[eid])
        assert (res.kind, res.entry.id) == (VC.MATCH, eid), eid
        spoken = _speak_confirm(ctx, PLUGIN_VOICE[eid])
        assert spoken.receipt["entry"] == eid

    # The registry is real: the unregister removed the manifest; the built-in
    # remains. An entrypoint-only plugin resolves to the typed gate.
    assert {r["id"] for r in PR.list_plugins(cfg)["plugins"]} == \
        {"builtin.echo"}
    PR.register(cfg, manifest=_manifest("acme.tool"), confirmed=True)
    gate = _speak_confirm(ctx, "qubit, invoke plugin acme.tool")
    assert gate.receipt["result"]["code"] == PR.UNAVAILABLE_CODE


def test_plugins_cli_parses_and_dispatches(tmp_path, monkeypatch):
    cfg, cfg_path = _cfg(tmp_path, monkeypatch)
    rc, out, _ = _cli(cfg_path, ["plugins", "list"])
    assert rc == 0 and any(r["id"] == "builtin.echo" for r in out["plugins"])
    rc, out, _ = _cli(cfg_path, ["plugins", "invoke", "builtin.echo",
                                 "--text", "hi", "--confirm"])
    assert rc == 0 and out["result"]["echo"] == "hi"
    # register without --confirm is typed, never a traceback
    manifest = tmp_path / "acme.json"
    manifest.write_text(json.dumps(_manifest()))
    rc, out, _ = _cli(cfg_path, ["plugins", "register", str(manifest)])
    assert rc == 3 and out["code"] == "PLUGIN_CONFIRM_REQUIRED"
    rc, out, _ = _cli(cfg_path, ["plugins", "register", str(manifest),
                                 "--confirm"])
    assert rc == 0 and out["plugin"]["id"] == "acme.tool"


def test_plugins_dashboard_exposes_and_matches_voice(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    cfg, _ = _cfg(tmp_path, monkeypatch)
    cat = {o["id"]: o for o in OPS.catalog()}
    for eid in PLUGIN_VOICE:
        assert eid in cat and cat[eid]["tier"] == "registry"
        assert cat[eid]["interfaces"] == ["cli", "dashboard", "voice"]
    probe = VC.make_ctx(cfg, actor="probe", session="probe", source="cli")
    expected = VC._b_plugin_runtime(probe, {"action": "list"})
    got = OPS.run_op(cfg, "plugins_list", {}, actor="dashboard:user",
                     session="dash")
    assert got["state"] == "executed"
    assert got["receipt"]["result"] == expected


# ----------------------------------------------------------------- channels
def test_channels_voice_executes_on_shared_backend(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg, _ = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("q8u_secret", CHANNEL_SECRET)
    ctx = _ctx(cfg)

    for eid in ("channels_status", "channels_catalog", "channels_accounts",
                "channels_configure", "channels_doctor",
                "channels_unconfigure"):
        res = VC.resolve(CHANNEL_VOICE[eid])
        assert (res.kind, res.entry.id) == (VC.MATCH, eid), eid
        out = _speak_confirm(ctx, CHANNEL_VOICE[eid])
        assert out.receipt["entry"] == eid

    # Raw-byte receive is a typed refusal that binds nothing and stages no
    # confirmation.
    res = VC.resolve("qubit, receive an inbound channel payload")
    assert res.kind == VC.MATCH and res.entry.id == "channels_receive_refusal"
    out = VC.execute(ctx, res)
    assert out.kind == VC.EXECUTED and out.confirmation_id == ""
    assert out.receipt["result"]["state"] == "refused"
    assert out.receipt["result"]["code"] == "VOICE_BYTES_UNSUPPORTED"


def test_channels_cli_parses_and_dispatches(tmp_path, monkeypatch):
    cfg, cfg_path = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("q8u_secret", CHANNEL_SECRET)
    rc, out, _ = _cli(cfg_path, ["channels", "catalog"])
    assert rc == 0 and out["state"] == "OK"
    rc, out, _ = _cli(cfg_path, ["channels", "configure", "discord",
                                 "env:q8u_secret"])
    assert rc == 3 and out["code"] == "CHANNEL_CONFIRM_REQUIRED"
    rc, out, _ = _cli(cfg_path, ["channels", "configure", "discord",
                                 "env:q8u_secret", "--confirm"])
    assert rc == 0 and out["channel"]["channel"] == "discord"
    rc, out, _ = _cli(cfg_path, ["channels", "unconfigure", "discord",
                                 "--confirm"])
    assert rc == 0 and out["removed"] == "discord"


def test_channels_dashboard_exposes_and_matches_voice(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    cfg, _ = _cfg(tmp_path, monkeypatch)
    cat = {o["id"]: o for o in OPS.catalog()}
    for eid in set(CHANNEL_VOICE) | {"channels_receive_refusal"}:
        assert eid in cat and cat[eid]["tier"] == "registry"
        assert cat[eid]["interfaces"] == ["cli", "dashboard", "voice"]
    probe = VC.make_ctx(cfg, actor="probe", session="probe", source="cli")
    expected = VC._b_channels(probe, {"action": "catalog"})
    got = OPS.run_op(cfg, "channels_catalog", {}, actor="dashboard:user",
                     session="dash")
    assert got["state"] == "executed"
    assert got["receipt"]["result"] == expected


def _sign(secret, ts, body):
    return hmac.new(secret.encode(), f"{ts}.".encode() + body,
                    hashlib.sha256).hexdigest()


class _Orch:
    def __init__(self, cfg):
        self.cfg = cfg
        self.task = type("T", (), {"active": False})()


def _post(port, path, body=b"", headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", path, body=body, headers=headers or {})
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = {"raw": raw.decode("utf-8", "replace")}
    return resp.status, payload


def test_channel_http_ingress_hmac_auth(tmp_path, monkeypatch):
    import time

    from voicebridge import channels_inbound as CI
    from voicebridge.webui import WebUI

    cfg, _ = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("q8u_secret", CHANNEL_SECRET)
    CI.configure(cfg, "discord", secret_ref="env:q8u_secret", confirmed=True)
    ui = WebUI(_Orch(cfg), host="127.0.0.1", port=0,
               asr_factory=lambda: None, state_dir=cfg.state_dir())
    ui.start()
    port = ui._server.server_address[1]
    try:
        body = b'{"hello": "prod"}'
        ts = time.time()
        hdrs = {"X-Qubit-Signature": _sign(CHANNEL_SECRET, ts, body),
                "X-Qubit-Timestamp": str(ts),
                "X-Qubit-Delivery": "wire-1"}
        # The dashboard session token does NOT authenticate this route:
        # only the per-channel HMAC does. Unsigned fails closed typed.
        status, out = _post(port, "/channels/discord", body)
        assert status == 401 and out["code"] == "CHANNEL_BAD_SIGNATURE"
        status, out = _post(port, "/channels/discord", body,
                            {"X-Api-Token": "whatever"})
        assert status == 401 and out["code"] == "CHANNEL_BAD_SIGNATURE"
        # Signed delivery is accepted but honestly resolves to UNAVAILABLE
        # (signature evidence only; no listener transport exists).
        status, out = _post(port, "/channels/discord", body, hdrs)
        assert status == 200, (status, out)
        assert out["state"] == "UNAVAILABLE"
        assert out["code"] == CI.UNAVAILABLE_CODE
        assert out["verified"] is True
        # Alias route works, and replay is a typed conflict.
        body2 = b'{"hello": "again"}'
        ts2 = time.time()
        hdrs2 = {"X-Qubit-Signature": _sign(CHANNEL_SECRET, ts2, body2),
                 "X-Qubit-Timestamp": str(ts2)}
        status, out = _post(port, "/hermes/channels/discord", body2, hdrs2)
        assert status == 200 and out["state"] == "UNAVAILABLE"
        status, out = _post(port, "/channels/discord", body, hdrs)
        assert status == 409 and out["code"] == CI.REPLAY_CODE
        # Unknown channel and missing signature are typed.
        status, out = _post(port, "/channels/ghost", b"{}", hdrs)
        assert status == 404 and out["code"] == CI.UNKNOWN_CODE
    finally:
        ui.stop()


def test_channel_ingress_oversized_body_typed(tmp_path, monkeypatch):
    import socket

    from voicebridge import channels_inbound as CI
    from voicebridge.webui import WebUI

    cfg, _ = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("q8u_secret", CHANNEL_SECRET)
    CI.configure(cfg, "discord", secret_ref="env:q8u_secret", confirmed=True)
    ui = WebUI(_Orch(cfg), host="127.0.0.1", port=0,
               asr_factory=lambda: None, state_dir=cfg.state_dir())
    ui.start()
    port = ui._server.server_address[1]
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=10)
        try:
            req = ("POST /channels/discord HTTP/1.0\r\n"
                   "Host: 127.0.0.1\r\n"
                   f"Content-Length: {CI.MAX_BODY_BYTES + 1}\r\n\r\n")
            s.sendall(req.encode())
            chunks = []
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
            data = b"".join(chunks).decode("utf-8", "replace")
        finally:
            s.close()
        assert data.startswith("HTTP/"), data[:40]
        assert "413" in data.split("\r\n", 1)[0]
        assert CI.TOO_LARGE_CODE in data
    finally:
        ui.stop()


# ---------------------------------------------------------------------- ACP
def test_acp_voice_executes_on_shared_backend(tmp_path, monkeypatch):
    from voicebridge import acp_client as AC
    from voicebridge import voice_commands as VC

    cfg, _ = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("acp_key", ACP_SECRET)
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    ctx = _ctx(cfg)

    for eid in ("acp_client_status", "acp_client_catalog",
                "acp_client_configure", "acp_client_handshake",
                "acp_client_receipts", "acp_client_unconfigure"):
        res = VC.resolve(ACP_VOICE[eid])
        assert (res.kind, res.entry.id) == (VC.MATCH, eid), eid
        out = _speak_confirm(ctx, ACP_VOICE[eid])
        assert out.receipt["entry"] == eid
    assert any(r["verdict"] == "HANDSHAKE_SHAPED"
               for r in AC.receipts(cfg)["receipts"])

    # send always resolves to the typed ACP_REFUSED gate (confirm-tier).
    _speak_confirm(ctx, ACP_VOICE["acp_client_configure"])
    send = _speak_confirm(ctx, "qubit, send to harness claude-code hello")
    assert send.receipt["result"]["code"] == AC.REFUSED_CODE


def test_acp_cli_parses_and_dispatches(tmp_path, monkeypatch):
    cfg, cfg_path = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("acp_key", ACP_SECRET)
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    rc, out, _ = _cli(cfg_path, ["acp", "status"])
    assert rc == 0 and out["state"] == "OK"
    rc, out, _ = _cli(cfg_path, ["acp", "catalog"])
    assert rc == 0 and {r["harness"] for r in out["harnesses"]}
    rc, out, _ = _cli(cfg_path, ["acp", "configure", "claude-code",
                                 "--credential-ref", "env:acp_key"])
    assert rc == 3 and out["code"] == "ACP_CONFIRM_REQUIRED"
    rc, out, _ = _cli(cfg_path, ["acp", "configure", "claude-code",
                                 "--credential-ref", "env:acp_key",
                                 "--confirm"])
    assert rc == 0
    rc, out, _ = _cli(cfg_path, ["acp", "handshake", "claude-code",
                                 "--confirm"])
    assert rc == 0 and out["executed"] is False


def test_acp_dashboard_exposes_and_matches_voice(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    cfg, _ = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    cat = {o["id"]: o for o in OPS.catalog()}
    for eid in ACP_VOICE:
        assert eid in cat and cat[eid]["tier"] == "registry"
        assert cat[eid]["interfaces"] == ["cli", "dashboard", "voice"]
    probe = VC.make_ctx(cfg, actor="probe", session="probe", source="cli")
    expected = VC._b_acp_client(probe, {"action": "catalog"})
    got = OPS.run_op(cfg, "acp_client_catalog", {}, actor="dashboard:user",
                     session="dash")
    assert got["state"] == "executed"
    assert got["receipt"]["result"] == expected


# ------------------------------------------------------------- full surface
def test_every_new_entry_reachable_on_all_three_surfaces(tmp_path,
                                                         monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    cfg, _ = _cfg(tmp_path, monkeypatch)
    registry = set(VC.REGISTRY)
    catalog = {o["id"] for o in OPS.catalog()}
    assert ALL_NEW <= registry
    assert ALL_NEW <= catalog
    for eid in ALL_NEW:
        assert VC.REGISTRY[eid].backend in VC._BACKENDS, eid
        assert VC.REGISTRY[eid].examples and VC.REGISTRY[eid].help, eid
