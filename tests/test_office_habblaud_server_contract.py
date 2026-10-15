"""Regression coverage for the embedded Habblaud server contract."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from voicebridge import webui as W  # noqa: E402

pytestmark = pytest.mark.unit
APP = "http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/"


def _handler(*, path="/api/access", peer="127.0.0.1", host="127.0.0.1:8890",
             referer=APP, token="configured", header=None):
    h = object.__new__(W.WebUIHandler)
    h.path = path
    h.client_address = (peer, 9000)
    h.headers = {"Host": host}
    if referer is not None:
        h.headers["Referer"] = referer
    if header is not None:
        h.headers["X-Qubit-Session-Token"] = header
    h.server = type("S", (), {"app": type("A", (), {"api_token": token})()})()
    h._api_token_ok = lambda: False
    h._qubit_token_ok = lambda: header is not None and header == token
    return h


def test_access_is_only_headerless_loopback_own_app_bootstrap():
    h = _handler()
    assert h._office_app_caller_ok(blind_probe=True)
    assert not _handler(path="/api/agents/1")._office_app_caller_ok(blind_probe=True)
    assert not _handler(peer="192.168.1.3")._office_app_caller_ok(blind_probe=True)
    assert not _handler(host="evil.example")._office_app_caller_ok(blind_probe=True)
    assert not _handler(referer="http://127.0.0.1:8890/chat")._office_app_caller_ok(blind_probe=True)


def test_agent_profile_requires_valid_header_when_token_is_configured():
    assert _handler(path="/api/agents/7", header="configured")._office_app_caller_ok()
    assert not _handler(path="/api/agents/7")._office_app_caller_ok()
    assert not _handler(path="/api/agents/7", header="wrong")._office_app_caller_ok()


def test_query_token_is_confined_to_stream_and_validated():
    assert _handler(path="/api/stream?last_seq=1&token=configured")._office_app_caller_ok()
    assert not _handler(path="/api/stream?token=wrong")._office_app_caller_ok()
    assert not _handler(path="/api/access?token=configured")._office_app_caller_ok(blind_probe=True)
    assert not _handler(path="/api/agents/7?token=configured")._office_app_caller_ok()
    assert not _handler(path="/api/stream?token=configured", token="")._office_app_caller_ok()


def test_query_token_does_not_change_general_api_policy():
    assert W.token_ok("configured", "configured", query_token_present=True) == (False, "query-token-rejected")


def test_header_token_is_checked_on_stream_and_agent_routes():
    assert _handler(path="/api/stream", header="configured")._office_app_caller_ok()
    assert _handler(path="/api/agents/1", header="configured")._office_app_caller_ok()


def test_client_schema_adapter_is_based_on_actual_habblaud_requests():
    """Pin actual shipped request contracts and the expected server adapter surface."""
    bundle = ROOT / "frontend/qubit-app/plugins/qubit-office/habblaud/bundle/app-U4Pwh2XA.js"
    js = bundle.read_text(encoding="utf-8")
    for route in ("/api/access", "/api/stream", "/api/agents/${encodeURIComponent(e)}",
                  "/api/sessions/recent", "/api/timeline/days", "/api/timeline/${e}"):
        assert route in js
    assert 'every(e=>typeof t[e]==`boolean`)' in js
    assert 'fetch(`/api/agents/${encodeURIComponent(e)}`)' in js
    # Validate that this build uses a Habblaud feed object, not the older tower
    # event format (the store deduplicates feed rows by id).
    assert "applyFeed(e){if(!e.length)return" in js
    assert "new Set(this.feed.map(e=>e.id))" in js
    assert "activity.at" in (ROOT / "voicebridge/office_stream.py").read_text()


def test_snapshot_and_feed_translate_actual_client_shape():
    from voicebridge import office_stream as OS
    t = {
        "now": 1700000000.0,
        "floorplan": {"floors": [{"number": 1, "rooms": [{"id": 2, "slug": "ops"}]}]},
        "characters": [{"agent_id": 7, "slug": "worker", "seat_type": "worker",
                        "position": {"floor": 1, "room_id": 2}, "animation": "working",
                        "last_event": {"age_s": 5}, "tasks": [], "supervisor_id": None}],
        "activity": [{"id": 9, "agent_id": 7, "ts": 1699999999.0,
                      "kind": "tool_call", "text": "used a tool"}],
    }
    snap = OS.to_snapshot(t, 3, 1699999900.0)
    agent = snap["agents"][0]
    for key in ("status", "tasks", "stats", "startedAt", "lastEventAt", "statusSince", "seed", "recent"):
        assert key in agent
    assert agent["status"] == "working" and agent["tasks"] == []
    assert agent["lastEventAt"] == 1699999995000
    feed = OS.to_feed(t)
    assert feed[0]["activity"] == {"id": "9", "kind": "tool_call", "text": "used a tool", "at": 1699999999000}
    assert feed[0]["roomId"] == "f1r2" and feed[0]["agentName"] == "worker"
