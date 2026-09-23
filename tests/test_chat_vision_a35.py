"""A35 — chat image attach/describe route (bounded, validated, no fake path)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    return C.Config({})


def test_vision_route_is_registered():
    from voicebridge import qubit_compat as HC

    route, _ = HC.match("POST", "/api/qubit/chat/vision")
    assert route and route["handler"] == "h_chat_vision"
    assert HC.HANDLERS.get("h_chat_vision") is not None


def test_vision_requires_confirmation(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, body = HC.dispatch(
        cfg, "POST", "/api/qubit/chat/vision", {},
        {"data_url": "data:image/png;base64,AAAA"})
    assert code == 400
    assert body["code"] == "CONFIRM_REQUIRED"


def test_vision_rejects_non_image_data_url(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, body = HC.dispatch(
        cfg, "POST", "/api/qubit/chat/vision", {},
        {"confirm": True, "data_url": "not-a-data-url"})
    assert code == 400
    assert body["code"] == "BAD_IMAGE"


def test_vision_rejects_invalid_base64(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, body = HC.dispatch(
        cfg, "POST", "/api/qubit/chat/vision", {},
        {"confirm": True, "data_url": "data:image/png;base64,!!!!"})
    assert code == 400
    assert body["code"] == "BAD_IMAGE"
