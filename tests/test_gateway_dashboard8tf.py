"""Functional Gateway tab (Hermes-first architecture, OpenClaw addition).

Messaging accounts + gateway records are a real command surface: add
account, add gateway, typed link check, remove — same backend modules as
CLI/voice, same confirmation journal. Records only (process supervision
stays refused); key references only (raw values fail fast before staging,
never journaled); outputs carry the kind only.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DIST = REPO_ROOT / "frontend" / "qubit-dist"
needs_dist = pytest.mark.skipif(
    not (DIST / "index.html").is_file(),
    reason="hermes-dist not built (npm build step)")


def _start(tmp_path):
    from voicebridge.config import Config
    from voicebridge.webui import WebUI
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(f"logging:\n  jsonl_path: '{state}'\nwebui:\n  token: test-token\n")
    cfg = Config.load(p)

    class _FakeOrch:
        _route = "gateway"
        _web_active = 0
        task = type("T", (), {"active": False})()

        def __init__(self):
            self.cfg = cfg
            self._t0 = time.time()

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    return ui, ui._server.server_address[1]


def _http(port, method, path, body=None, token="test-token"):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 method=method, data=data)
    if token:
        req.add_header("X-Qubit-Session-Token", token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _stage_confirm(port, path, body):
    status, out = _http(port, "POST", path, body)
    assert status == 409, (path, status, out)
    assert out.get("state") == "NEEDS_CONFIRMATION", out
    status, done = _http(port, "POST", "/agent/api/ops/confirm",
                          {"confirmation_id": out["confirmation_id"]})
    assert status == 200 and done.get("ok"), (path, status, done)
    return done


@needs_dist
def test_gateway_lifecycle_over_http(tmp_path):
    ui, port = _start(tmp_path)
    try:
        _stage_confirm(port, "/agent/api/ops/msg-account-add",
                       {"account_id": "team", "provider": "slack",
                        "label": "Team", "key_ref": "env:SLACK_TOKEN"})
        done = _stage_confirm(port, "/agent/api/ops/gateway-add",
                              {"gateway_id": "gw1", "provider": "slack",
                               "account": "team", "channel": "webhook",
                               "key_ref": "env:SLACK_TOKEN"})
        gw = ((done.get("receipt") or {}).get("result") or {}).get(
            "gateway", {})
        assert gw.get("id") == "gw1", done
        assert gw.get("key_ref") == "env", gw  # kind only, never the ref
        assert gw.get("process") == "UNMANAGED", gw
        status, out = _http(port, "GET",
                             "/agent/api/ops/gateway-link?gateway_id=gw1")
        assert status == 200
        assert (out.get("result") or {}).get("state") == "NO_CREDENTIAL", \
            out  # honest: env var unset in test
        _stage_confirm(port, "/agent/api/ops/gateway-remove",
                       {"gateway_id": "gw1"})
        status, out = _http(port, "GET", "/agent/api/ops/gateways")
        assert status == 200
        assert (out.get("result") or {}).get("gateways", []) == [], out
    finally:
        ui.stop()


@needs_dist
def test_raw_secret_value_fails_before_staging(tmp_path):
    ui, port = _start(tmp_path)
    try:
        from voicebridge.approvals import ApprovalJournal
        from voicebridge.config import Config as _C
        status, out = _http(port, "POST", "/agent/api/ops/msg-account-add",
                             {"account_id": "bad", "provider": "slack",
                              "label": "Bad", "key_ref": "xoxb-raw-value"})
        assert status == 400, (status, out)
        assert out.get("confirmation_id", None) is None, out
        # Nothing journaled: the raw value never reaches the journal file.
        state = tmp_path / "state"
        jp = state / "approvals.jsonl"
        assert "xoxb-raw-value" not in (jp.read_text() if jp.exists()
                                        else ""), "raw secret journaled"
    finally:
        ui.stop()


@needs_dist
def test_gateway_tab_drives_backend(tmp_path):
    pw = pytest.importorskip("playwright.sync_api")
    ui, port = _start(tmp_path)
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/qubit-gateway",
                      timeout=30000)
            page.wait_for_timeout(2500)
            page.get_by_label("New account id").fill("team")
            page.get_by_label("New account label").fill("Team")
            page.get_by_label("New account key ref").fill("env:SLACK_TOKEN")
            page.get_by_role("button", name="Add messaging account").click()
            page.wait_for_timeout(1200)
            region = page.get_by_role("region",
                                      name="Pending gateway confirmation")
            assert region.count() == 1
            region.get_by_role("button", name="Confirm").click()
            page.wait_for_timeout(2000)
            assert region.count() == 0
            assert "team" in page.inner_text("body")
            page.get_by_label("New gateway id").fill("gw1")
            page.get_by_label("New gateway account").fill("team")
            page.get_by_label("New gateway key ref").fill("env:SLACK_TOKEN")
            page.get_by_role("button", name="Add gateway").click()
            page.wait_for_timeout(1200)
            assert region.count() == 1
            region.get_by_role("button", name="Confirm").click()
            page.wait_for_timeout(2000)
            body = page.inner_text("body")
            assert "gw1" in body, body[-500:]
            page.get_by_role("button", name="Check link gw1").click()
            page.wait_for_timeout(1500)
            assert "NO_CREDENTIAL" in page.inner_text("body")
            page.get_by_role("button", name="Remove gateway gw1").click()
            page.wait_for_timeout(1200)
            assert region.count() == 1
            region.get_by_role("button", name="Confirm").click()
            page.wait_for_timeout(2000)
            assert not errors, errors[:3]
            browser.close()
        status, out = _http(port, "GET", "/agent/api/ops/gateways")
        assert status == 200
        assert (out.get("result") or {}).get("gateways", []) == [], out
    finally:
        ui.stop()
