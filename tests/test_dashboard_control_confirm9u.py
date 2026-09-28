"""Dashboard control-confirmation contract (webhooks / profiles / MCP).

Focused, hermetic coverage for the real-backend defects found by the
Phase-4 browser E2E (``tests/test_dashboard_e2e_phase4.py``):

* ``/api/webhooks`` is a Qubit read authority (no fork-proxy split-brain);
* webhook create accepts the SPA payload and persists to the canonical store;
* webhook delete stages a settleable 409 with a REAL ``confirmation_id``;
* profile create stages a settleable 409 with a REAL ``confirmation_id``;
* MCP create requires ``transport`` and stages/settles through the journal;
* MCP enable/delete stage a settleable 409 with a REAL ``confirmation_id``.

Every assertion is a canonical dispatch response plus a persisted read-back
from the same store (``voicebridge.webhooks`` / ``brain`` / ``mcp_servers``).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

pytestmark = pytest.mark.unit


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\nwebui:\n  token: t\n")
    return C.Config.load(p)


def _spa_webhook_payload(name="phase9u-hook"):
    return {
        "name": name,
        "url": "http://127.0.0.1:9/hook",
        "events": ["task.completed"],
        "secret_ref": "env:PHASE9U_HOOK",
        # Webhook create is a confirmed mutation (e-stop + shared journal).
        "confirm": True,
    }


def test_webhooks_is_a_qubit_read_authority():
    from voicebridge import webui

    assert "/api/webhooks" in webui.QUBIT_READ_AUTHORITY
    assert webui.qubit_read_authority("/api/webhooks") is True
    assert webui.qubit_read_authority("/api/webhooks/phase9u-hook") is True
    # Segment-aware: an unrelated sibling is not captured.
    assert webui.qubit_read_authority("/api/webhooksX") is False


def test_status_is_a_qubit_read_authority():
    """The SPA status (sidebar + gateway card) must reach ``h_status``
    (systemd + port supervision), never the fork proxy's status whose
    gateway fields describe the unsupervised fork gateway (dashboard
    permanently Off). The classic raw ``/api/status`` branch is untouched."""
    from voicebridge import webui

    assert "/api/status" in webui.QUBIT_READ_AUTHORITY
    assert webui.qubit_read_authority("/api/status") is True
    assert webui.qubit_read_authority("/api/status?x=1") is True
    assert webui.qubit_read_authority("/api/statusX") is False


def test_webhook_create_accepts_spa_payload_and_persists(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import webhooks as W

    cfg = _cfg(tmp_path, monkeypatch)
    code, created = HC.dispatch(cfg, "POST", "/api/webhooks", {},
                                _spa_webhook_payload())
    assert code == 200 and created.get("ok") is True, (code, created)
    assert created.get("name") == "phase9u-hook", created
    assert created.get("url") == "http://127.0.0.1:9/hook", created
    assert created.get("secret_set") is True, created

    stored = {s["id"]: s for s in W.list_subs(cfg)["subscriptions"]}
    assert "phase9u-hook" in stored, stored
    assert stored["phase9u-hook"]["events"] == ["task.completed"], stored


def test_webhook_create_bad_url_is_typed_and_persists_nothing(
        tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import webhooks as W

    cfg = _cfg(tmp_path, monkeypatch)
    code, body = HC.dispatch(
        cfg, "POST", "/api/webhooks", {},
        {"name": "phase9u-bad", "url": "http://example.com/hook",
         "events": ["task.completed"], "secret_ref": "env:X", "confirm": True})
    assert code == 400 and body.get("code") == "WEBHOOK_BAD_URL", (code, body)
    assert W.list_subs(cfg)["subscriptions"] == [], "refusal wrote the store"


def test_webhook_delete_stages_real_id_and_settles(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import webhooks as W

    cfg = _cfg(tmp_path, monkeypatch)
    HC.dispatch(cfg, "POST", "/api/webhooks", {}, _spa_webhook_payload())

    code, staged = HC.dispatch(cfg, "DELETE", "/api/webhooks/phase9u-hook",
                               {}, {})
    assert code == 409 and staged.get("code") == "CONFIRM_REQUIRED", staged
    cid = staged.get("confirmation_id")
    assert cid, ("empty confirmation id — not settleable", staged)

    # Settle by re-issuing the same request with the id.
    code, done = HC.dispatch(cfg, "DELETE", "/api/webhooks/phase9u-hook", {},
                             {"confirmation_id": cid})
    assert code == 200 and done.get("ok") is True, (code, done)
    assert W.list_subs(cfg)["subscriptions"] == []


def test_webhook_delete_settles_via_query_confirm(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import webhooks as W

    cfg = _cfg(tmp_path, monkeypatch)
    HC.dispatch(cfg, "POST", "/api/webhooks", {}, _spa_webhook_payload())
    code, done = HC.dispatch(cfg, "DELETE", "/api/webhooks/phase9u-hook",
                             {"confirm": "true"}, {})
    assert code == 200, (code, done)
    assert W.list_subs(cfg)["subscriptions"] == []


def test_profile_create_stages_real_id_and_settles(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, staged = HC.dispatch(cfg, "POST", "/api/profiles", {},
                               {"name": "phase9u-profile"})
    assert code == 409 and staged.get("code") == "CONFIRM_REQUIRED", staged
    cid = staged.get("confirmation_id")
    assert cid, ("empty confirmation id — not settleable", staged)
    P = HC._profiles_module(cfg)
    assert not P.profile_exists("phase9u-profile"), "created before confirm"

    code, done = HC.dispatch(cfg, "POST", "/api/profiles", {},
                             {"name": "phase9u-profile",
                              "confirmation_id": cid})
    assert code == 200 and done.get("ok") is True, (code, done)
    assert P.profile_exists("phase9u-profile"), "reference profile not created"


def test_mcp_create_requires_transport_and_persists(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    # A transport-less create defaults to stdio and must fail honestly.
    code, body = HC.dispatch(cfg, "POST", "/api/mcp/servers", {},
                             {"name": "phase9u-mcp", "confirm": True,
                              "url": "https://example.com/mcp"})
    assert code == 400 and body.get("code") == "MCP_BAD_INPUT", (code, body)

    code, created = HC.dispatch(
        cfg, "POST", "/api/mcp/servers", {},
        {"name": "phase9u-mcp", "transport": "http",
         "url": "https://example.com/mcp", "confirm": True})
    assert code == 200 and created.get("ok") is True, (code, created)
    names = {s["name"] for s in MS.list_servers(cfg)["servers"]}
    assert "phase9u-mcp" in names, names


def test_mcp_create_stages_real_id_and_settles(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    body = {"name": "phase9u-mcp", "transport": "http",
            "url": "https://example.com/mcp"}
    code, staged = HC.dispatch(cfg, "POST", "/api/mcp/servers", {}, dict(body))
    assert code == 409 and staged.get("code") == "CONFIRM_REQUIRED", staged
    cid = staged.get("confirmation_id")
    assert cid, staged

    code, done = HC.dispatch(cfg, "POST", "/api/mcp/servers", {},
                             {**body, "confirmation_id": cid})
    assert code == 200 and done.get("ok") is True, (code, done)
    names = {s["name"] for s in MS.list_servers(cfg)["servers"]}
    assert "phase9u-mcp" in names, names


def test_mcp_enable_and_delete_stage_and_settle(tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    HC.dispatch(cfg, "POST", "/api/mcp/servers", {},
                {"name": "phase9u-mcp", "transport": "http",
                 "url": "https://example.com/mcp", "confirm": True})

    # Enable stages a real id and settles.
    code, staged = HC.dispatch(
        cfg, "PUT", "/api/mcp/servers/phase9u-mcp/enabled", {},
        {"enabled": True})
    assert code == 409 and staged.get("code") == "CONFIRM_REQUIRED", staged
    cid = staged.get("confirmation_id")
    assert cid, staged
    code, done = HC.dispatch(
        cfg, "PUT", "/api/mcp/servers/phase9u-mcp/enabled", {},
        {"enabled": True, "confirmation_id": cid})
    assert code == 200, (code, done)
    server = next(s for s in MS.list_servers(cfg)["servers"]
                  if s["name"] == "phase9u-mcp")
    assert server["enabled"] is True, server

    # Delete stages a real id and settles.
    code, staged = HC.dispatch(cfg, "DELETE", "/api/mcp/servers/phase9u-mcp",
                               {}, {})
    assert code == 409 and staged.get("code") == "CONFIRM_REQUIRED", staged
    cid = staged.get("confirmation_id")
    assert cid, staged
    code, done = HC.dispatch(cfg, "DELETE", "/api/mcp/servers/phase9u-mcp",
                             {}, {"confirmation_id": cid})
    assert code == 200, (code, done)
    names = {s["name"] for s in MS.list_servers(cfg)["servers"]}
    assert "phase9u-mcp" not in names, names


def test_spa_webhook_body_maps_to_canonical_store(tmp_path, monkeypatch):
    """The SPA's createWebhook body shape lands in the canonical store.

    Mirrors ``lib/api.ts createWebhook`` (id/url/events/secret_ref) so a front
    -end contract drift would fail here before the browser suite.
    """
    from voicebridge import qubit_compat as HC
    from voicebridge import webhooks as W

    cfg = _cfg(tmp_path, monkeypatch)
    code, _ = HC.dispatch(
        cfg, "POST", "/api/webhooks", {},
        {"id": "phase9u-spa", "url": "http://127.0.0.1:9/spa",
         "events": ["task.completed"], "secret_ref": "env:PHASE9U_SPA",
         "confirm": True})
    assert code == 200, code
    stored = json.loads((Path(cfg.state_dir()) / "webhooks.json").read_text())
    assert "phase9u-spa" in stored["subscriptions"], stored
    assert W.list_subs(cfg)["subscriptions"][0]["id"] == "phase9u-spa"
