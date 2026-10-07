"""Pasted images are handled by the AGENT, not described into the local brain.

Owner fix: ``POST /api/qubit/chat/image`` (``h_chat_image``) describes the image
with the canonical vision model AND submits ONE agent turn on the session. The
turn is submitted with ``force_agent=True`` because a vision description is
CONTEXT, not an instruction — the fast-path classifier otherwise hijacks it
(an "image"/"screenshot" phrase matches the local screenshot action; a question
matches the coding build worker), keeping the turn off the tool-capable agent.

These tests pin, through the real production functions:
* the route is registered to the real handler;
* a valid image submits a forced agent turn carrying the description;
* the forced-agent path bypasses the classifier that would hijack the text;
* a vision failure is typed and submits nothing;
* ``submit=false`` keeps the describe-to-composer review path.
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# 1x1 transparent PNG.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _data_url(raw: bytes = _PNG, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    return C.Config({})


@pytest.fixture(autouse=True)
def _no_estop(monkeypatch):
    from voicebridge import qubit_compat as HC

    monkeypatch.setattr(HC, "_estop", lambda cfg: False)


def test_image_route_is_registered_to_the_real_handler():
    from voicebridge import qubit_compat as HC

    route, _ = HC.match("POST", "/api/qubit/chat/image")
    assert route and route["handler"] == "h_chat_image"
    assert HC.HANDLERS.get("h_chat_image") is not None


def test_image_submits_one_forced_agent_turn(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge import qubit_compat as HC
    from voicebridge import vision as V

    monkeypatch.setattr(
        V, "describe",
        lambda cfg, image, **kw: {"description": "a bar chart",
                                  "model": "qwen3-vl:4b", "image": {}})
    seen: Dict[str, Any] = {}

    def fake_turn(cfg, text, **kw):
        seen["text"] = text
        seen["kw"] = kw
        return {"ok": True, "reply": "AGENT_REPLY", "session_id": "s1",
                "route": "agent", "receipt": {"model": "qwen3-vl:4b"}}

    monkeypatch.setattr(CT, "turn", fake_turn)

    cfg = _cfg(tmp_path, monkeypatch)
    code, body = HC.dispatch(
        cfg, "POST", "/api/qubit/chat/image", {},
        {"confirm": True, "session": "dashboard-chat",
         "prompt": "what does this show?", "data_url": _data_url()})

    assert code == 200, body
    assert body["ok"] is True
    assert body["reply"] == "AGENT_REPLY"
    assert body["description"] == "a bar chart"
    assert body["vision_model"] == "qwen3-vl:4b"
    # The turn carried the user's prompt AND the vision description, and it was
    # forced through the agent (never the classifier).
    assert "what does this show?" in seen["text"]
    assert "a bar chart" in seen["text"]
    assert seen["kw"].get("force_agent") is True
    assert seen["kw"].get("session") == "dashboard-chat"


def test_image_uses_a_default_ask_with_no_prompt(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge import qubit_compat as HC
    from voicebridge import vision as V

    monkeypatch.setattr(
        V, "describe",
        lambda cfg, image, **kw: {"description": "a cat", "model": "m",
                                  "image": {}})
    seen: Dict[str, Any] = {}

    def fake_turn(cfg, text, **kw):
        seen["text"] = text
        return {"ok": True, "reply": "ok", "session_id": "s1"}

    monkeypatch.setattr(CT, "turn", fake_turn)
    cfg = _cfg(tmp_path, monkeypatch)
    code, _ = HC.dispatch(
        cfg, "POST", "/api/qubit/chat/image", {},
        {"confirm": True, "session": "dashboard-chat", "data_url": _data_url()})
    assert code == 200
    assert "What is in this image" in seen["text"]
    assert "a cat" in seen["text"]


def test_forced_agent_bypasses_the_classifier_that_hijacks_vision_text(
        monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge import qubit_compat as HC

    text = HC._vision_turn_text("", "A screenshot of a dashboard.")
    # Sanity: the classifier WOULD hijack this text (the bug being fixed).
    assert CT._decide_branch(text, None)["action"] == "local_action"

    called: Dict[str, Any] = {}

    def fake_agent_chat(cfg, st, sid, clean, **kw):
        called["clean"] = clean
        return {"ok": True, "reply": "AGENT_SAW_IMAGE", "session_id": sid,
                "route": "agent"}

    monkeypatch.setattr(CT, "_estop", lambda cfg: False)
    monkeypatch.setattr(CT, "_agent_enabled", lambda cfg: True)
    monkeypatch.setattr(CT, "store", lambda cfg: object())
    monkeypatch.setattr(CT, "resolve_route",
                        lambda *a, **k: {"ok": True, "model": "m",
                                         "kind": "local"})
    monkeypatch.setattr(CT, "_resolve_session",
                        lambda *a, **k: {"state": "OK",
                                         "session": {"id": "s1"}})
    monkeypatch.setattr(CT, "_agent_chat", fake_agent_chat)

    res = CT.turn(None, text, session="s1", force_agent=True)
    assert res["ok"] is True
    assert res["reply"] == "AGENT_SAW_IMAGE"
    assert called["clean"] == text


def test_vision_failure_is_typed_and_submits_nothing(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge import qubit_compat as HC
    from voicebridge import vision as V

    class Boom(Exception):
        code = "VISION_VRAM_SHORT"
        detail = "not enough VRAM"

    def _raise(cfg, image, **kw):
        raise Boom()

    monkeypatch.setattr(V, "describe", _raise)
    submitted = {"n": 0}
    monkeypatch.setattr(CT, "turn",
                        lambda *a, **k: submitted.__setitem__("n", 1) or {})
    cfg = _cfg(tmp_path, monkeypatch)
    code, body = HC.dispatch(
        cfg, "POST", "/api/qubit/chat/image", {},
        {"confirm": True, "session": "dashboard-chat", "data_url": _data_url()})
    assert code == 502
    assert body["ok"] is False
    assert body["code"] == "VISION_VRAM_SHORT"
    assert submitted["n"] == 0


def test_submit_false_keeps_the_describe_to_composer_review_path(
        tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT
    from voicebridge import qubit_compat as HC
    from voicebridge import vision as V

    monkeypatch.setattr(
        V, "describe",
        lambda cfg, image, **kw: {"description": "a chart", "model": "m",
                                  "image": {}})
    submitted = {"n": 0}
    monkeypatch.setattr(CT, "turn",
                        lambda *a, **k: submitted.__setitem__("n", 1) or {})
    cfg = _cfg(tmp_path, monkeypatch)
    code, body = HC.dispatch(
        cfg, "POST", "/api/qubit/chat/image", {},
        {"confirm": True, "session": "dashboard-chat", "submit": False,
         "data_url": _data_url()})
    assert code == 200
    assert body["submitted"] is False
    assert body["description"] == "a chart"
    assert submitted["n"] == 0
