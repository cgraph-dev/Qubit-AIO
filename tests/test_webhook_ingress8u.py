"""Packet 8U — signed inbound webhook ingress (HTTP delivery endpoint).

Proves the MISSING row #24 from the OpenClaw audit: an external service can
deliver a signed event to Qubit over HTTP, with HMAC verification, timestamp
freshness, replay protection, body bounds, redacted receipts, and typed
failures. Hermetic: tmp state only, no network to real services.
"""
from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import socket
import time
from pathlib import Path

SECRET = "sup3r-s3cr3t-ingress-key"
DASH_TOKEN = "dashboard-session-token"


def _cfg(tmp_path, monkeypatch, webui_token: str = ""):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    text = "logging:\n  jsonl: false\n"
    if webui_token:
        text += f"webui:\n  token: {webui_token}\n"
    p.write_text(text)
    return C.Config.load(p)


def _add(cfg, sub_id: str = "deploy", secret: str = f"env:WH_INGRESS_SECRET"):
    from voicebridge import webhooks as WH

    return WH.add(cfg, sub_id, "http://127.0.0.1:9999/hook", ["deploy"],
                  secret, confirmed=True)


def _sign(secret: str, ts: float, body: bytes) -> str:
    return hmac.new(secret.encode(),
                    f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


def _headers(secret: str, ts: float, body: bytes, *, sig: str = "",
             delivery: str = "") -> dict:
    h = {
        "X-Qubit-Signature": sig or _sign(secret, ts, body),
        "X-Qubit-Timestamp": str(ts),
    }
    if delivery:
        h["X-Qubit-Delivery"] = delivery
    return h


def _scan(root: Path) -> str:
    out = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            try:
                out.append(p.read_text(errors="replace"))
            except OSError:
                pass
    return "\n".join(out)


# ---------------------------------------------------------------- unit: ingest
def test_ingest_valid_delivery_redacted_receipt(tmp_path, monkeypatch):
    from voicebridge import webhooks as WH

    monkeypatch.setenv("WH_INGRESS_SECRET", SECRET)
    cfg = _cfg(tmp_path, monkeypatch)
    _add(cfg)
    body = b'{"deploy": "prod-42"}'
    ts = time.time()
    out = WH.ingest(cfg, "deploy", body,
                    _headers(SECRET, ts, body, delivery="delivery-1"))
    assert out["state"] == "VALID"
    assert out["bytes"] == len(body)
    assert out["subscription"] == "deploy"
    assert "prod-42" not in json.dumps(out)
    # receipts/audit carry no secret and no raw payload
    blob = _scan(cfg.state_dir())
    assert SECRET not in blob, "signing secret must never be recorded"
    assert "prod-42" not in blob, "payload must not appear in receipts"
    log = (cfg.state_dir() / "webhook-log.jsonl").read_text()
    assert '"verdict": "VALID"' in log


def test_ingest_unknown_subscription_typed(tmp_path, monkeypatch):
    from voicebridge import webhooks as WH

    monkeypatch.setenv("WH_INGRESS_SECRET", SECRET)
    cfg = _cfg(tmp_path, monkeypatch)
    _add(cfg)
    body = b"{}"
    ts = time.time()
    try:
        WH.ingest(cfg, "ghost", body, _headers(SECRET, ts, body))
    except WH.WebhookError as exc:
        assert exc.code == "WEBHOOK_NOT_FOUND", exc.code
    else:
        raise AssertionError("unknown subscription must fail")


def test_ingest_missing_signature_typed(tmp_path, monkeypatch):
    from voicebridge import webhooks as WH

    monkeypatch.setenv("WH_INGRESS_SECRET", SECRET)
    cfg = _cfg(tmp_path, monkeypatch)
    _add(cfg)
    try:
        WH.ingest(cfg, "deploy", b"{}",
                  {"X-Qubit-Timestamp": str(time.time())})
    except WH.WebhookError as exc:
        assert exc.code == "WEBHOOK_BAD_SIGNATURE", exc.code
    else:
        raise AssertionError("missing signature must fail closed")


def test_ingest_bad_signature_typed(tmp_path, monkeypatch):
    from voicebridge import webhooks as WH

    monkeypatch.setenv("WH_INGRESS_SECRET", SECRET)
    cfg = _cfg(tmp_path, monkeypatch)
    _add(cfg)
    body = b"{}"
    ts = time.time()
    try:
        WH.ingest(cfg, "deploy", body,
                  _headers(SECRET, ts, body, sig="0" * 64))
    except WH.WebhookError as exc:
        assert exc.code == "WEBHOOK_BAD_SIGNATURE", exc.code
    else:
        raise AssertionError("forged signature must fail")


def test_ingest_stale_timestamp_typed(tmp_path, monkeypatch):
    from voicebridge import webhooks as WH

    monkeypatch.setenv("WH_INGRESS_SECRET", SECRET)
    cfg = _cfg(tmp_path, monkeypatch)
    _add(cfg)
    body = b"{}"
    ts = time.time() - 3600
    try:
        WH.ingest(cfg, "deploy", body, _headers(SECRET, ts, body))
    except WH.WebhookError as exc:
        assert exc.code == "WEBHOOK_STALE", exc.code
    else:
        raise AssertionError("stale timestamp must fail")


def test_ingest_replay_signature_and_delivery_id(tmp_path, monkeypatch):
    from voicebridge import webhooks as WH

    monkeypatch.setenv("WH_INGRESS_SECRET", SECRET)
    cfg = _cfg(tmp_path, monkeypatch)
    _add(cfg)
    body = b'{"deploy": "prod-1"}'
    ts = time.time()
    WH.ingest(cfg, "deploy", body,
              _headers(SECRET, ts, body, delivery="d-up"))
    # identical signature -> replay
    try:
        WH.ingest(cfg, "deploy", body, _headers(SECRET, ts, body))
    except WH.WebhookError as exc:
        assert exc.code == "WEBHOOK_REPLAY", exc.code
    else:
        raise AssertionError("duplicate signature must be rejected as replay")
    # fresh signature but the same delivery id -> replay
    ts2 = time.time()
    body2 = b'{"deploy": "prod-2"}'
    try:
        WH.ingest(cfg, "deploy", body2,
                  _headers(SECRET, ts2, body2, delivery="d-up"))
    except WH.WebhookError as exc:
        assert exc.code == "WEBHOOK_REPLAY", exc.code
    else:
        raise AssertionError("duplicate delivery id must be rejected")


def test_ingest_oversized_body_typed(tmp_path, monkeypatch):
    from voicebridge import webhooks as WH

    monkeypatch.setenv("WH_INGRESS_SECRET", SECRET)
    cfg = _cfg(tmp_path, monkeypatch)
    _add(cfg)
    body = b"x" * (WH.MAX_BODY_BYTES + 1)
    ts = time.time()
    try:
        WH.ingest(cfg, "deploy", body,
                  {"X-Qubit-Signature": "0" * 64,
                   "X-Qubit-Timestamp": str(ts)})
    except WH.WebhookError as exc:
        assert exc.code == "WEBHOOK_TOO_LARGE", exc.code
    else:
        raise AssertionError("oversized body must fail before signing")


# ---------------------------------------------------------------- HTTP ingress
class _Orch:
    def __init__(self, cfg):
        self.cfg = cfg
        self.task = type("T", (), {"active": False})()


def _post(port: int, path: str, body: bytes = b"", headers=None):
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


def _started_ui(tmp_path, monkeypatch):
    from voicebridge.webui import WebUI

    monkeypatch.setenv("WH_INGRESS_SECRET", SECRET)
    cfg = _cfg(tmp_path, monkeypatch, webui_token=DASH_TOKEN)
    _add(cfg)
    ui = WebUI(_Orch(cfg), host="127.0.0.1", port=0,
               asr_factory=lambda: None, state_dir=cfg.state_dir())
    ui.start()
    return cfg, ui, ui._server.server_address[1]


def test_endpoint_rejects_unsigned_and_unknown(tmp_path, monkeypatch):
    cfg, ui, port = _started_ui(tmp_path, monkeypatch)
    try:
        status, out = _post(port, "/hooks/deploy", b"{}")
        assert status == 401
        assert out["code"] == "WEBHOOK_BAD_SIGNATURE"
        # the dashboard session token does NOT authenticate this route
        status, out = _post(port, "/hooks/deploy", b"{}",
                            {"X-Api-Token": DASH_TOKEN})
        assert status == 401
        assert out["code"] == "WEBHOOK_BAD_SIGNATURE"
        # unknown subscription is typed and rejected even when signed
        body = b"{}"
        ts = time.time()
        status, out = _post(port, "/hooks/ghost", body,
                            _headers(SECRET, ts, body))
        assert status == 404
        assert out["code"] == "WEBHOOK_NOT_FOUND"
    finally:
        ui.stop()


def test_endpoint_accepts_valid_signature_and_alias(tmp_path, monkeypatch):
    cfg, ui, port = _started_ui(tmp_path, monkeypatch)
    try:
        body = b'{"deploy": "prod-42"}'
        ts = time.time()
        status, out = _post(port, "/hooks/deploy", body,
                            _headers(SECRET, ts, body, delivery="e2e-1"))
        assert status == 200, (status, out)
        assert out["state"] == "VALID"
        assert out["bytes"] == len(body)
        assert SECRET not in json.dumps(out)

        body2 = b'{"deploy": "prod-43"}'
        ts2 = time.time()
        status, out = _post(port, "/hooks/deploy", body2,
                            _headers(SECRET, ts2, body2, delivery="e2e-2"))
        assert status == 200, (status, out)
        assert out["state"] == "VALID"

        # a replay of the first delivery returns a typed conflict
        status, out = _post(port, "/hooks/deploy", body,
                            _headers(SECRET, ts, body, delivery="e2e-1"))
        assert status == 409
        assert out["code"] == "WEBHOOK_REPLAY"
        blob = _scan(cfg.state_dir())
        assert SECRET not in blob
    finally:
        ui.stop()


def test_endpoint_oversized_content_length_typed(tmp_path, monkeypatch):
    from voicebridge import webhooks as WH

    cfg, ui, port = _started_ui(tmp_path, monkeypatch)
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=10)
        try:
            req = ("POST /hooks/deploy HTTP/1.0\r\n"
                   "Host: 127.0.0.1\r\n"
                   f"Content-Length: {WH.MAX_BODY_BYTES + 1}\r\n"
                   "\r\n")
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
        assert "413" in data.split("\r\n", 1)[0], data.split("\r\n", 1)[0]
        assert "WEBHOOK_TOO_LARGE" in data
    finally:
        ui.stop()
