"""QBPV Agent 4 (D) — real bounded screencast behavior (OC-D27).

The wave-1 proof only asserted ``hasattr(BT, "screencast")``. This drives the
real ``voicebridge.browser_tabs.screencast`` loop over an injected CDP session:
it starts ``Page.startScreencast``, drains + ACKs frames, stops the stream, and
returns the bounded decoded frames. It also pins the typed failures (bad format,
oversized capture, no-frame without fallback) so the live-panel claim is backed
by a behavioural test, not a name check.
"""
from __future__ import annotations

import base64


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


class _FakeCdp:
    def __init__(self, payloads):
        self.events = [
            {"method": "Page.screencastFrame",
             "params": {"sessionId": i + 1, "data": p}}
            for i, p in enumerate(payloads)]
        self.calls = []
        self.closed = False

    def call(self, method, params=None, timeout=None):
        self.calls.append((method, params))
        return {}

    def close(self):
        self.closed = True


def _patch(monkeypatch, cdp_session):
    from voicebridge import browser_tabs as BT

    monkeypatch.setattr(BT, "_resolve_target",
                        lambda cfg, target="": {"id": "t1", "url": "u",
                                                "title": "t"})
    monkeypatch.setattr(BT, "_target_session", lambda page: cdp_session)
    return BT


def _b64(text: bytes) -> str:
    return base64.b64encode(text).decode("ascii")


def test_screencast_drains_frames_and_acks(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    payload = _b64(b"fake-jpeg-bytes")
    fake = _FakeCdp([payload])
    BT = _patch(monkeypatch, fake)

    out = BT.screencast(cfg, frames=1, fmt="jpeg", fallback=False)
    assert out["state"] == "OK", out
    assert out["mode"] == "screencast"
    assert out["frame_count"] == 1
    assert out["frames"][0]["image_base64"] == payload
    assert out["frames"][0]["bytes"] == len(b"fake-jpeg-bytes")

    methods = [m for (m, _p) in fake.calls]
    assert "Page.startScreencast" in methods
    assert "Page.screencastFrameAck" in methods
    assert "Page.stopScreencast" in methods
    assert fake.closed is True


def test_screencast_is_bounded_by_max_frames(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    payloads = [_b64(b"x%d" % i) for i in range(20)]
    fake = _FakeCdp(payloads)
    BT = _patch(monkeypatch, fake)

    out = BT.screencast(cfg, frames=999, fallback=False)
    assert out["state"] == "OK", out
    assert out["frame_count"] == BT.SCREENCAST_MAX_FRAMES
    assert out["frame_count"] <= BT.SCREENCAST_MAX_FRAMES


def test_screencast_bad_format_is_typed(tmp_path, monkeypatch):
    import pytest

    cfg = _cfg(tmp_path, monkeypatch)
    fake = _FakeCdp([])
    BT = _patch(monkeypatch, fake)
    with pytest.raises(BT.BrowserTabsError) as exc:
        BT.screencast(cfg, fmt="gif", fallback=False)
    assert exc.value.code == BT.BAD_INPUT_CODE


def test_screencast_oversize_capture_is_denied(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    fake = _FakeCdp([_b64(b"z" * 2048)])
    BT = _patch(monkeypatch, fake)

    out = BT.screencast(cfg, frames=1, fallback=False, max_bytes=1024)
    assert out["state"] == "denied", out
    assert out["code"] == BT.SCREENSHOT_TOO_LARGE_CODE


def test_screencast_no_frame_without_fallback_is_typed_unavailable(
        tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    fake = _FakeCdp([])
    BT = _patch(monkeypatch, fake)

    out = BT.screencast(cfg, frames=1, fallback=False)
    assert out["state"] == "UNAVAILABLE", out
    assert out["code"] == BT.UNAVAILABLE_CODE
