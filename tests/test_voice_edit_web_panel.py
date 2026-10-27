"""Frozen transport contract for the browser Voice Edit panel.

The Tk HUD remains the default; these tests pin the opt-in local HTTP/WS
backend (token auth, loopback-only, snapshot, fanout, action routing, bounded
queues, static fallback, browser launch/fallback, and no secrets on the wire).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aiohttp
import pytest

from voicebridge.voice_edit import web_panel as wp


def _overlay(tmp_path: Path | None = None, **kwargs):
    actions = []
    static_dir = (tmp_path / "missing-out") if tmp_path is not None else None
    overlay = wp.WebPanelOverlay(
        lambda action, payload: actions.append((action, payload)),
        static_dir=static_dir,
        launch_browser=False,
        ping_interval_s=0.0,
        **kwargs,
    )
    return overlay, actions


async def _recv(ws, timeout: float = 3.0):
    raw = await asyncio.wait_for(ws.receive_str(), timeout=timeout)
    return json.loads(raw)


async def _recv_type(ws, kind: str, timeout: float = 3.0):
    while True:
        message = await _recv(ws, timeout)
        if message.get("type") == kind:
            return message


def _http_base(overlay) -> str:
    return f"http://127.0.0.1:{overlay.port}"


def _ws_base(overlay) -> str:
    return f"ws://127.0.0.1:{overlay.port}/voice-edit/ws"


def test_token_auth_loopback_and_no_cors_wildcard(tmp_path):
    overlay, _ = _overlay(tmp_path)
    try:
        async def scenario():
            async with aiohttp.ClientSession() as session:
                for url in (f"{_ws_base(overlay)}",
                            f"{_ws_base(overlay)}?token=wrong"):
                    async with session.get(url) as response:
                        assert response.status == 403
                        assert response.headers.get(
                            "Access-Control-Allow-Origin") is None

                async with session.get(f"{_http_base(overlay)}/voice-edit/") as r:
                    assert r.status == 200
                    assert r.headers.get("Access-Control-Allow-Origin") is None

                async with session.ws_connect(overlay.ws_url) as ws:
                    hello = await _recv(ws)
                    assert hello == {"type": "hello", "version": 1,
                                     "state": "idle"}

                async with session.ws_connect(
                        _ws_base(overlay),
                        headers={wp.TOKEN_HEADER: overlay.token}) as ws:
                    assert (await _recv(ws))["type"] == "hello"

                async with session.ws_connect(
                        _ws_base(overlay),
                        headers={"Authorization": f"Bearer {overlay.token}"}
                ) as ws:
                    assert (await _recv(ws))["type"] == "hello"

        asyncio.run(scenario())
    finally:
        overlay.close()
    assert wp._is_loopback("127.0.0.1")
    assert wp._is_loopback("::1")
    assert not wp._is_loopback("10.0.0.5")
    assert not wp._is_loopback(None)


def test_snapshot_stream_fanout_and_bounded_history(tmp_path):
    overlay, _ = _overlay(tmp_path, history_limit=3)
    try:
        async def scenario():
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(overlay.ws_url) as first:
                    assert (await _recv(first))["type"] == "hello"

                    overlay.set_status("listening", "speak now")
                    status = await _recv_type(first, "status")
                    assert status["state"] == "listening"
                    assert status["detail"] == "speak now"
                    assert status["level"] == 0.0

                    overlay.set_live_transcript("helo", final=False)
                    partial = await _recv_type(first, "partial")
                    assert partial == {"type": "partial", "text": "helo",
                                       "final": False}

                    overlay.set_level(0.42)
                    level = await _recv_type(first, "level")
                    assert level["value"] == 0.42

                    overlay.show_preview({
                        "original": "hello world",
                        "proposed": "Hello, world.",
                        "detected": {"action": "rewrite", "style": "formal",
                                     "language": "en"},
                        "model": "m", "provider": "p",
                        "privacy_mode": "local", "locality": "local",
                        "target_app": "code", "latency_ms": 12,
                        "injection_suspected": True,
                        "clarification_returned": False,
                        "facts_dropped": ["id-1"],
                        "api_key": "SHOULD_NOT_LEAK",
                        "token": overlay.token,
                    })
                    preview = await _recv_type(first, "preview")
                    payload = preview["payload"]
                    assert payload == {
                        "original": "hello world",
                        "proposed": "Hello, world.",
                        "detected": {"action": "rewrite", "style": "formal",
                                     "language": "en"},
                        "model": "m", "provider": "p",
                        "privacy_mode": "local", "locality": "local",
                        "target_app": "code", "elapsed_ms": 12,
                        "injection_suspected": True,
                        "clarification_returned": False,
                        "facts_dropped": ["id-1"],
                    }
                    assert "SHOULD_NOT_LEAK" not in json.dumps(preview)
                    assert overlay.token not in json.dumps(preview)

                    for index in range(5):
                        overlay.append_history_entry(
                            "answer", f"heard {index}", f"result {index}",
                            f"10:00:0{index}")
                        await _recv_type(first, "history")

                    async with session.ws_connect(overlay.ws_url) as second:
                        hello = await _recv(second)
                        assert hello == {"type": "hello", "version": 1,
                                         "state": "preview"}
                        await _recv_type(second, "status")
                        assert (await _recv_type(
                            second, "partial"))["text"] == "helo"
                        assert (await _recv_type(
                            second, "preview"))["payload"]["proposed"] == \
                            "Hello, world."
                        history = []
                        seen = 0
                        while len(history) < 3 and seen < 10:
                            message = await _recv(second)
                            seen += 1
                            if message.get("type") == "history":
                                history.append(message)
                        assert [row["heard"] for row in history] == [
                            "heard 2", "heard 3", "heard 4"]

                        overlay.set_status("done", "applied")
                        assert (await _recv_type(first, "status"))["state"] == \
                            "done"
                        assert (await _recv_type(second, "status"))["state"] == \
                            "done"

        asyncio.run(scenario())
    finally:
        overlay.close()


def test_action_routing_is_exact_and_never_dropped(tmp_path):
    overlay, actions = _overlay(tmp_path)
    try:
        async def scenario():
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(overlay.ws_url) as ws:
                    await _recv(ws)
                    await ws.send_json({"type": "action", "action": "apply",
                                        "payload": {"confirm": True}})
                    await ws.send_json({"type": "action",
                                        "action": "edit_submit",
                                        "payload": {"text": "fixed"}})
                    for index in range(60):
                        await ws.send_json({
                            "type": "action", "action": "retry",
                            "payload": {"index": index},
                        })
                    deadline = asyncio.get_running_loop().time() + 5.0
                    expected = 62
                    while len(actions) < expected:
                        if asyncio.get_running_loop().time() > deadline:
                            break
                        await asyncio.sleep(0.01)
                    assert len(actions) == expected
                    assert actions[0] == ("apply", {"confirm": True})
                    assert actions[1] == ("edit_submit", {"text": "fixed"})
                    assert [payload["index"] for _, payload in actions[2:]] == \
                        list(range(60))

                    await ws.send_json({"type": "action",
                                        "action": "destroy_everything",
                                        "payload": {}})
                    error = await _recv_type(ws, "error")
                    assert error["code"] == "bad_action"
                    assert len(actions) == expected

                    await ws.send_str("not json")
                    error = await _recv_type(ws, "error")
                    assert error["code"] == "bad_json"

        asyncio.run(scenario())
    finally:
        overlay.close()


def test_queue_bounds_coalesce_level_and_partial():
    class FakeWS:
        closed = False

    client = wp._Client(FakeWS(), max_queue=4)
    for index in range(50):
        client.enqueue({"type": "level", "value": index / 50})
    assert len(client.pending) == 1
    assert client.pending[-1]["value"] == 49 / 50
    assert client.coalesced == 49

    client = wp._Client(FakeWS(), max_queue=3)
    for index in range(10):
        client.enqueue({"type": "history", "kind": str(index), "heard": "",
                        "result": "", "ts": ""})
    assert len(client.pending) == 3
    assert client.dropped == 7

    client = wp._Client(FakeWS(), max_queue=2)
    client.enqueue({"type": "history", "kind": "a", "heard": "", "result": "",
                    "ts": ""})
    client.enqueue({"type": "level", "value": 1.0})
    client.enqueue({"type": "status", "state": "done", "detail": "",
                    "level": 1.0})
    assert [message["type"] for message in client.pending] == [
        "history", "status"]


def test_static_index_placeholder_asset_and_traversal(tmp_path):
    missing = tmp_path / "no-build"
    overlay = wp.WebPanelOverlay(lambda a, p: None, static_dir=missing,
                                 launch_browser=False, ping_interval_s=0.0)
    try:
        async def placeholder():
            async with aiohttp.ClientSession() as session:
                async with session.get(
                        f"{_http_base(overlay)}/voice-edit/") as response:
                    text = await response.text()
                    assert response.status == 200
                    assert "Qubit Voice Edit" in text
                    assert "bundle was not found" in text

        asyncio.run(placeholder())
    finally:
        overlay.close()

    build = tmp_path / "out"
    (build / "assets").mkdir(parents=True)
    (build / "index.html").write_text("<html>HUD INDEX</html>", encoding="utf-8")
    (build / "assets" / "app.js").write_text("console.log('hud')",
                                             encoding="utf-8")
    (tmp_path / "secret.txt").write_text("DO NOT SERVE", encoding="utf-8")
    overlay = wp.WebPanelOverlay(lambda a, p: None, static_dir=build,
                                 launch_browser=False, ping_interval_s=0.0)
    try:
        async def built():
            async with aiohttp.ClientSession() as session:
                async with session.get(
                        f"{_http_base(overlay)}/voice-edit/") as response:
                    assert response.status == 200
                    assert "HUD INDEX" in await response.text()
                async with session.get(
                        f"{_http_base(overlay)}/voice-edit/assets/app.js"
                ) as response:
                    assert response.status == 200
                    assert "console.log" in await response.text()
                async with session.get(
                        f"{_http_base(overlay)}/voice-edit/..%2Fsecret.txt"
                ) as response:
                    assert response.status == 404
                    assert "DO NOT SERVE" not in await response.text()

        asyncio.run(built())
    finally:
        overlay.close()


class _Cfg:
    def __init__(self, data):
        self._data = data

    def get(self, *path, default=None):
        node = self._data
        for key in path:
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                return default
        return node


def test_panel_backend_selection_defaults_to_tk():
    assert wp.resolve_panel_backend(_Cfg({})) == "tk"
    assert wp.resolve_panel_backend(_Cfg(
        {"voice_edit": {"panel_backend": "web"}})) == "web"
    assert wp.resolve_panel_backend(_Cfg(
        {"voice_edit": {"panel_backend": "nonsense"}})) == "tk"


def test_browser_launch_builds_app_command_and_falls_back(tmp_path):
    calls = []

    class Proc:
        pid = 4321

    def fake_popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return Proc()

    profile = tmp_path / "profile"
    result = wp.launch_app_window(
        "http://127.0.0.1:5555/voice-edit/?token=abc",
        which=lambda name: "/usr/bin/chromium" if name == "chromium" else None,
        popen=fake_popen,
        mkdtemp=lambda prefix: str(profile),
    )
    assert result.ok and result.backend == "chromium" and result.pid == 4321
    argv, kwargs = calls[0]
    assert argv[0] == "/usr/bin/chromium"
    assert "--app=http://127.0.0.1:5555/voice-edit/?token=abc" in argv
    assert "--window-size=900,760" in argv
    assert f"--user-data-dir={profile}" in argv
    assert kwargs["start_new_session"] is True

    missing = wp.launch_app_window("http://127.0.0.1:1/",
                                   which=lambda name: None,
                                   popen=fake_popen)
    assert not missing.ok
    assert missing.reason == "browser_not_found"


def test_browser_unavailable_is_a_typed_fallback(tmp_path):
    with pytest.raises(wp.WebPanelUnavailable) as info:
        wp.WebPanelOverlay(
            lambda a, p: None, launch_browser=True, ping_interval_s=0.0,
            launch=lambda url: wp.LaunchResult(
                ok=False, reason="browser_not_found"))
    assert info.value.code == "browser_unavailable"


def test_no_secrets_in_any_wire_message(tmp_path):
    overlay, _ = _overlay(tmp_path)
    try:
        async def scenario():
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(overlay.ws_url) as ws:
                    seen = [await _recv(ws)]
                    overlay.set_status("error", f"leaked {overlay.token}")
                    seen.append(await _recv_type(ws, "status"))
                    overlay.show_error("boom", f"token={overlay.token}")
                    seen.append(await _recv_type(ws, "error"))
                    overlay.append_history_entry(
                        "answer", f"heard {overlay.token}", "ok")
                    seen.append(await _recv_type(ws, "history"))
                    overlay.show_preview({"original": overlay.token,
                                          "proposed": "x",
                                          "api_key": "SECRET_KEY"})
                    seen.append(await _recv_type(ws, "preview"))
                    blob = json.dumps(seen)
                    assert overlay.token not in blob
                    assert "SECRET_KEY" not in blob
                    assert "***" in blob

        asyncio.run(scenario())
    finally:
        overlay.close()


def test_main_falls_back_to_tk_when_web_backend_is_unavailable(
        tmp_path, monkeypatch, capsys):
    import voicebridge.desktop_helper as dh

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    class Cfg(_Cfg):
        def __init__(self, backend):
            super().__init__({"voice_edit": {"enabled": True,
                                             "panel_backend": backend}})

    def _boom(_on_action):
        raise wp.WebPanelUnavailable("browser_unavailable", "no browser")

    tk_overlay = object()
    seen = {}

    class Helper:
        def __init__(self, _cfg, *, client, overlay, desktop, notify):
            seen["overlay"] = overlay

        def start(self):
            return False

    monkeypatch.setattr(dh, "build_cfg", lambda: Cfg("web"))
    monkeypatch.setattr(dh, "DesktopAdapter", lambda: object())
    monkeypatch.setattr(dh, "HelperClient", lambda *a, **k: object())
    monkeypatch.setattr(dh, "resolve_service_base", lambda cfg: "")
    monkeypatch.setattr(dh, "resolve_token", lambda cfg: "")
    monkeypatch.setattr(dh, "DesktopHelper", Helper)
    monkeypatch.setattr(dh, "take_over_previous_helper", lambda **k: None)
    monkeypatch.setattr(dh, "_install_term_handler", lambda helper: None)
    monkeypatch.setattr(dh, "make_overlay", lambda fn, **k: tk_overlay)
    monkeypatch.setattr(wp, "WebPanelOverlay", _boom)

    assert dh.main([]) == 3
    assert seen["overlay"] is tk_overlay
    assert "falling back to Tk" in capsys.readouterr().err

    monkeypatch.setattr(dh, "build_cfg", lambda: Cfg("tk"))
    monkeypatch.setattr(wp, "WebPanelOverlay",
                        lambda *a, **k: pytest.fail("web must not be used"))
    assert dh.main([]) == 3
    assert seen["overlay"] is tk_overlay
