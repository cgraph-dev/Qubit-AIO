"""Packet 8U — bounded browser screenshot over the attached CDP session.

Hermetic: no sockets and no network. A fake attached CDP session proves the
bounded base64 payload, the hard byte cap, URL/title redaction, clip
validation, and fail-closed CDP errors. With no attached browser the call is
inert and returns the typed ``BROWSER_UNAVAILABLE`` state instead of raising.
"""
from __future__ import annotations

import base64
import json

import pytest

PAGE_URL = "https://user:hunter2@mail.example.com/inbox?token=abc123secret"
PAGE_TITLE = "Inbox — Synthetic Mail"


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


class _FakeSession:
    """Stand-in for ``cdp.WsSession`` — records calls, never opens a socket."""

    def __init__(self, payload_b64="", *, fail=None):
        self.payload_b64 = payload_b64
        self.fail = fail
        self.calls = []
        self.closed = False

    def call(self, method, params=None, *, timeout=None, session_id=""):
        self.calls.append({"method": method, "params": params,
                           "timeout": timeout})
        if self.fail is not None:
            raise self.fail
        return {"data": self.payload_b64}

    def close(self):
        self.closed = True


def _no_network(*_a, **_k):
    raise AssertionError("test attempted a real network call")


def _attach(monkeypatch, BT, *, payload=b"\x89PNG-bytes",
            fail=None, url=PAGE_URL, title=PAGE_TITLE):
    """Attach a fake already-open CDP target + session and block real I/O."""
    from voicebridge import cdp as CDP

    session = _FakeSession(base64.b64encode(payload).decode(), fail=fail)
    page = {"id": "AAA111", "title": title, "url": url, "type": "page"}
    monkeypatch.setattr(BT, "_resolve_target", lambda cfg, t="": page)
    monkeypatch.setattr(BT, "_target_session", lambda p: session)
    monkeypatch.setattr(CDP, "http_json", _no_network)
    monkeypatch.setattr(CDP.WsSession, "connect", _no_network)
    return session


# ------------------------------------------------------------ unavailable
def test_no_browser_is_typed_unavailable(tmp_path, monkeypatch):
    from voicebridge import browser_tabs as BT
    from voicebridge import cdp as CDP

    cfg = _cfg(tmp_path, monkeypatch)

    def _down(method, path, *, timeout=CDP.HTTP_TIMEOUT_S, query=""):
        raise CDP.CdpError(CDP.UNAVAILABLE_CODE, "no loopback CDP endpoint")

    monkeypatch.setattr(CDP, "http_json", _down)
    out = BT.screenshot(cfg)  # must not raise
    assert out["state"] == "UNAVAILABLE"
    assert out["code"] == "BROWSER_UNAVAILABLE"
    assert out["code"] == BT.UNAVAILABLE_CODE
    assert out["owner"] == "qubit-policy"
    assert "browser untouched" in out["rollback"]


def test_session_unavailable_is_typed_not_raised(tmp_path, monkeypatch):
    from voicebridge import browser_tabs as BT
    from voicebridge import cdp as CDP

    cfg = _cfg(tmp_path, monkeypatch)
    page = {"id": "AAA111", "title": PAGE_TITLE, "url": PAGE_URL,
            "type": "page"}
    monkeypatch.setattr(BT, "_resolve_target", lambda cfg, t="": page)

    def _refuse(_page):
        raise BT.BrowserTabsError(CDP.UNAVAILABLE_CODE, "endpoint vanished")

    monkeypatch.setattr(BT, "_target_session", _refuse)
    out = BT.screenshot(cfg)
    assert out["state"] == "UNAVAILABLE"
    assert out["code"] == BT.UNAVAILABLE_CODE


# --------------------------------------------------------------- success
def test_screenshot_returns_bounded_base64(tmp_path, monkeypatch):
    from voicebridge import browser_tabs as BT

    cfg = _cfg(tmp_path, monkeypatch)
    raw = b"\x89PNG\r\n\x1a\n" + b"A" * 64
    session = _attach(monkeypatch, BT, payload=raw)
    out = BT.screenshot(cfg)
    assert out["state"] == "OK"
    assert out["target"] == "AAA111"
    assert out["format"] == "jpeg"
    assert out["bytes"] == len(raw)
    assert out["bytes"] <= out["max_bytes"]
    assert base64.b64decode(out["image_base64"]) == raw
    assert out["trust"] == "untrusted-page-image"
    assert session.closed is True
    call = session.calls[0]
    assert call["method"] == "Page.captureScreenshot"
    assert call["params"]["captureBeyondViewport"] is False
    assert "clip" not in call["params"]
    assert call["timeout"] == BT.SCREENSHOT_TIMEOUT_S


def test_screenshot_size_cap_rejects_oversize(tmp_path, monkeypatch):
    from voicebridge import browser_tabs as BT

    cfg = _cfg(tmp_path, monkeypatch)
    session = _attach(monkeypatch, BT, payload=b"B" * 128)
    out = BT.screenshot(cfg, max_bytes=8)
    assert out["state"] == "denied"
    assert out["code"] == BT.SCREENSHOT_TOO_LARGE_CODE
    assert out["max_bytes"] == 8
    assert "image_base64" not in out
    assert session.closed is True


def test_screenshot_hard_caps_user_budget(tmp_path, monkeypatch):
    from voicebridge import browser_tabs as BT

    cfg = _cfg(tmp_path, monkeypatch)
    _attach(monkeypatch, BT, payload=b"C" * 32)
    out = BT.screenshot(cfg, max_bytes=10 ** 9)
    assert out["max_bytes"] == BT.MAX_SCREENSHOT_BYTES
    assert out["bytes"] <= BT.MAX_SCREENSHOT_BYTES


def test_screenshot_redacts_url_and_title(tmp_path, monkeypatch):
    from voicebridge import browser_tabs as BT

    cfg = _cfg(tmp_path, monkeypatch)
    _attach(monkeypatch, BT, payload=b"D" * 16,
            title="Mail sk-abcdef1234567890")
    out = BT.screenshot(cfg)
    blob = json.dumps(out)
    assert "hunter2" not in blob
    assert "abc123secret" not in blob
    assert "sk-abcdef1234567890" not in blob
    assert out["url"].startswith("https://")


def test_screenshot_clip_validated_and_forwarded(tmp_path, monkeypatch):
    from voicebridge import browser_tabs as BT

    cfg = _cfg(tmp_path, monkeypatch)
    session = _attach(monkeypatch, BT, payload=b"E" * 16)
    out = BT.screenshot(cfg, clip=(10, 20, 300, 40))
    assert out["state"] == "OK"
    assert session.calls[0]["params"]["clip"] == {
        "x": 10.0, "y": 20.0, "width": 300.0, "height": 40.0, "scale": 1}
    for bad in ((10, 20, 0, 40), (10, 20, -5, 40), (1, 2, 3),
                ("a", 1, 2, 3), (1, 2, 10 ** 9, 5)):
        with pytest.raises(BT.BrowserTabsError) as exc:
            BT.screenshot(cfg, clip=bad)
        assert exc.value.code == BT.BAD_INPUT_CODE


def test_screenshot_rejects_bad_format(tmp_path, monkeypatch):
    from voicebridge import browser_tabs as BT

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(BT.BrowserTabsError) as exc:
        BT.screenshot(cfg, fmt="bmp")
    assert exc.value.code == BT.BAD_INPUT_CODE


# ------------------------------------------------------------- fail closed
def test_screenshot_cdp_error_fails_closed(tmp_path, monkeypatch):
    from voicebridge import browser_tabs as BT
    from voicebridge import cdp as CDP

    cfg = _cfg(tmp_path, monkeypatch)
    session = _attach(monkeypatch, BT,
                      fail=CDP.CdpError(CDP.METHOD_CODE, "capture refused"))
    with pytest.raises(BT.BrowserTabsError) as exc:
        BT.screenshot(cfg)
    assert exc.value.code == CDP.METHOD_CODE
    assert session.closed is True


def test_screenshot_bad_base64_fails_closed(tmp_path, monkeypatch):
    from voicebridge import browser_tabs as BT

    cfg = _cfg(tmp_path, monkeypatch)
    session = _attach(monkeypatch, BT)
    session.payload_b64 = "not!base64!"
    with pytest.raises(BT.BrowserTabsError):
        BT.screenshot(cfg)
    assert session.closed is True


def test_protocol_error_from_resolution_is_not_swallowed(tmp_path,
                                                         monkeypatch):
    from voicebridge import browser_tabs as BT
    from voicebridge import cdp as CDP

    cfg = _cfg(tmp_path, monkeypatch)

    def _bad(cfg, target=""):
        raise BT.BrowserTabsError(CDP.PROTOCOL_CODE, "no debugger URL")

    monkeypatch.setattr(BT, "_resolve_target", _bad)
    with pytest.raises(BT.BrowserTabsError) as exc:
        BT.screenshot(cfg)
    assert exc.value.code == CDP.PROTOCOL_CODE
