"""Audit wave 2026-09-23 (AI9) — pairing read authority, device enroll
authorization, multipart delivery, and Keys ``is_set`` presence truth.

Each test drives the real canonical path (the WebUI dispatcher / the compat
dispatcher / ``env_store``) against a hermetic ``Config`` with XDG state under
``tmp_path``. No live service, provider, credential, hardware, or browser is
touched.

Covered defects (AI9 report):
* D-1  ``/pairing`` GET served by the same store the mutations write.
* D-12 device enrollment requires explicit confirmation and is audited.
* D-3  ``/files`` multipart upload reaches the handler instead of a bare
       ``400 bad json body`` before dispatch.
* D-4  Keys ``is_set`` reflects keys actually present in the runtime ``.env``
       (names only; no value is ever read back or served).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# hermetic config + minimal WebUI harness
# ---------------------------------------------------------------------------
def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        (tmp_path / var.lower()).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QUBIT_HOME", str(home / "qh"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\nwebui:\n  token: test-token\n")
    return C.Config.load(p)


class _FakeOrch:
    def __init__(self, cfg):
        import time

        self.cfg = cfg
        self.task = type("T", (), {"active": False})()
        self._route = "conversation"
        self._t0 = time.time()
        self._web_active = 0
        self._tts = None
        self.shared = None
        self.sticky_route = None
        self.learning = None

    def _needs_answer(self):
        return False


@pytest.fixture()
def ui(tmp_path, monkeypatch):
    from voicebridge.webui import WebUI

    cfg = _cfg(tmp_path, monkeypatch)
    server = WebUI(_FakeOrch(cfg), host="127.0.0.1", port=0, state_dir=tmp_path)
    server.start()
    try:
        yield server, cfg
    finally:
        server.stop()


def _request(ui, method, path, body=None, raw=None, content_type=None,
             token="test-token"):
    import urllib.error
    import urllib.request

    data = raw
    headers = {"X-Qubit-Session-Token": token}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(
        f"http://127.0.0.1:{ui._server.server_port}{path}",
        data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            parsed = json.loads(payload.decode() or "{}")
        except ValueError:
            parsed = {"_raw": payload.decode("utf-8", "replace")}
        return exc.code, parsed
    except urllib.error.URLError as exc:
        raise AssertionError(f"request failed: {exc}")


# ---------------------------------------------------------------------------
# D-1 — /pairing is a Qubit read authority (same store as the writes)
# ---------------------------------------------------------------------------
def test_pairing_is_a_qubit_read_authority():
    from voicebridge import webui

    assert "/api/pairing" in webui.QUBIT_READ_AUTHORITY
    assert webui.qubit_read_authority("/api/pairing") is True
    assert webui.qubit_read_authority("/api/pairing?x=1") is True
    assert webui.qubit_read_authority("/api/pairing/child") is True
    assert webui.qubit_read_authority("/api/pairingX") is False


def test_pairing_get_serves_the_store_the_mutations_write(ui):
    server, cfg = ui
    from voicebridge import qubit_compat as HC

    code, created = HC.dispatch(
        cfg, "POST", "/api/pairing",
        {}, {"label": "ai9-audit", "scopes": ["chat"], "confirm": True})
    assert code == 200 and created["ok"] is True, created
    code_id = created["result"]["code_id"]

    status, listed = _request(server, "GET", "/agent/api/pairing")
    assert status == 200, listed
    pending_ids = [p.get("request_id") for p in listed.get("pending", [])]
    assert code_id in pending_ids, listed


# ---------------------------------------------------------------------------
# D-12 — device enrollment requires confirm and is audited
# ---------------------------------------------------------------------------
def test_device_enroll_requires_confirmation(ui):
    server, cfg = ui
    status, out = _request(server, "POST", "/agent/api/device/enroll", {})
    assert status == 409, out
    assert out.get("code") == "CONFIRM_REQUIRED", out
    assert out.get("confirmation_id"), out


def test_device_enroll_confirmed_is_audited(ui, monkeypatch):
    server, cfg = ui
    from voicebridge import device as Dev
    from voicebridge import qubit_compat as HC

    calls = []
    real = HC.audit_event

    def _recorder(op, actor, detail):
        calls.append((op, actor, detail))
        return real(op, actor, detail)

    monkeypatch.setattr(HC, "audit_event", _recorder)

    # enroll must not be pre-existing
    try:
        Dev.require_enrolled(cfg)
        was_enrolled = True
    except Dev.DeviceNotEnrolledError:
        was_enrolled = False
    assert was_enrolled is False

    status, out = _request(server, "POST", "/agent/api/device/enroll",
                           {"confirm": True})
    assert status == 200 and out["enrolled"] is True, out
    assert Dev.require_enrolled(cfg)["enrolled"] is True
    assert any(op == "device.enroll" for op, _a, _d in calls), calls


def test_device_status_route_is_wired_under_the_spa_prefix(ui):
    server, cfg = ui
    status, out = _request(server, "GET", "/agent/api/device/status")
    assert status == 200 and out.get("ok") is True, out
    assert out.get("machine_id"), out


# ---------------------------------------------------------------------------
# D-3 — multipart body is delivered to the handler
# ---------------------------------------------------------------------------
def test_parse_multipart_form_text_and_file_fields():
    from voicebridge.webui import parse_multipart_form

    boundary = "----qubitai9"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="path"\r\n\r\n'
        "/tmp/ai9/probe.txt\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="p.txt"\r\n'
        "Content-Type: text/plain\r\n\r\n"
        "hello multipart\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    parsed = parse_multipart_form(
        body, f"multipart/form-data; boundary={boundary}")
    assert parsed is not None
    assert parsed["path"] == "/tmp/ai9/probe.txt"
    assert parsed["file"] == b"hello multipart"
    assert parsed["content"] == "hello multipart"
    assert parsed["_files"]["file"]["filename"] == "p.txt"
    assert parse_multipart_form(b"x", "multipart/form-data") is None


def test_multipart_upload_reaches_handler_not_bad_json(ui):
    server, cfg = ui
    boundary = "----qubitai9up"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="path"\r\n\r\n'
        "/tmp/ai9-not-a-project-root/probe.txt\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="p.txt"\r\n'
        "Content-Type: text/plain\r\n\r\n"
        "upload body\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    status, out = _request(
        server, "POST", "/agent/api/files/upload-stream", raw=body,
        content_type=f"multipart/form-data; boundary={boundary}")
    # The handler ran (typed JSON refusal for an out-of-root path) rather than
    # the dispatcher's pre-dispatch bare "bad json body".
    assert isinstance(out, dict) and out.get("code"), out
    assert out.get("code") != "_raw", out
    assert "bad json body" not in json.dumps(out)


# ---------------------------------------------------------------------------
# D-4 — is_set reflects runtime .env presence (names only, never values)
# ---------------------------------------------------------------------------
def test_env_store_runtime_env_names_returns_names_only(tmp_path, monkeypatch):
    from voicebridge import config as C
    from voicebridge import env_store as ES

    cfg = _cfg(tmp_path, monkeypatch)
    path = Path(C.canonical_runtime_env_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# comment\nexport OPENCODE_GO_API_KEY=sk-canary-value\n"
        "OPENCODE_ZEN_BASE_URL=https://example.invalid\n")
    names = ES.runtime_env_names(cfg)
    assert "OPENCODE_GO_API_KEY" in names
    assert "OPENCODE_ZEN_BASE_URL" in names
    assert all("sk-canary-value" not in n for n in names)
    assert ES.is_present(cfg, "OPENCODE_GO_API_KEY") is True
    assert ES.is_present(cfg, "NOT_A_REAL_KEY") is False


def test_env_get_reports_is_set_from_runtime_env_without_leaking(ui):
    server, cfg = ui
    from voicebridge import config as C
    from voicebridge import env_store as ES

    path = Path(C.canonical_runtime_env_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("OPENCODE_GO_API_KEY=sk-canary-DO-NOT-LEAK\n")

    # a reference-only key must also read as set (unchanged behavior)
    ES.set_ref(cfg, "AI9_REF_KEY", "vault:ai9")

    status, env = _request(server, "GET", "/agent/api/env")
    assert status == 200, env
    assert env["AI9_REF_KEY"]["is_set"] is True, env.get("AI9_REF_KEY")
    assert env["OPENCODE_GO_API_KEY"]["is_set"] is True, \
        env.get("OPENCODE_GO_API_KEY")
    blob = json.dumps(env)
    assert "sk-canary-DO-NOT-LEAK" not in blob
    assert env["OPENCODE_GO_API_KEY"].get("redacted_value") is None
