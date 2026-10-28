"""Packet 8T-F — Hermes --image parity: approved vision analysis path.

Proves validation (missing/malformed/oversized/unsupported/symlink),
capability checks (no backend), VRAM guard refusal, stub-backed success
with resident restore, redaction, e-stop pre/post behavior, timeouts,
temp hygiene, confirmation, and status/dashboard parity — all hermetic
via a stub Ollama server (no model is ever loaded).
"""
from __future__ import annotations

import base64
import io
import json
import threading
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# 1x1 transparent PNG (real magic bytes).
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class _Stub:
    """Minimal Ollama-shaped server: tags/ps/generate/chat."""

    def __init__(self, *, vision_size_mib=100.0, hang_chat=False,
                 vision_reply="A red square. token=abc123secret"):
        self.vision_size_mib = vision_size_mib
        self.hang_chat = hang_chat
        self.vision_reply = vision_reply
        self.hits = []
        self.restored_seen = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()

    def _handler(self):
        stub = self

        class _H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, obj):
                body = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/api/tags":
                    models = []
                    if stub.vision_size_mib > 0:
                        models.append({"name": "stub-vision:8b",
                                       "size": int(stub.vision_size_mib * 1024 * 1024),
                                       "digest": "sha256:stub"})
                    return self._json({"models": models})
                if self.path == "/api/ps":
                    if stub.restored_seen:
                        return self._json({"models": [{"name": "qubit-brain:latest"}]})
                    return self._json({"models": []})
                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b""
                stub.hits.append(self.path)
                if self.path == "/api/generate":
                    return self._json({"response": "ok"})
                if self.path == "/api/chat":
                    try:
                        payload = json.loads(raw or b"{}")
                    except ValueError:
                        payload = {}
                    msgs = payload.get("messages", [])
                    text = ""
                    if msgs and isinstance(msgs[0], dict):
                        text = str(msgs[0].get("content", ""))
                    if stub.hang_chat:
                        import time as _t
                        _t.sleep(30)
                    if text.strip() == "warm probe":
                        stub.restored_seen = True
                        return self._json({"message": {"content": "warm ok"}})
                    if any("images" in str(m) for m in msgs):
                        assert payload.get("keep_alive") == 0, \
                            "vision must never stay resident"
                        return self._json({"message": {"content": stub.vision_reply}})
                    return self._json({"message": {"content": "chat ok"}})
                self.send_response(404)
                self.end_headers()

        return _H

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _cfg(tmp_path, monkeypatch, stub=None, extra=""):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    url = f"http://127.0.0.1:{stub.port}" if stub else "http://127.0.0.1:11434"
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n"
                 "intent:\n  runner:\n"
                 f"    url: \"{url}\"\n    alias: \"qubit-brain\"\n"
                 "  vision:\n    model: \"stub-vision\"\n" + extra)
    return C.Config.load(p)


def _png(tmp_path, name="img.png", payload=PNG_1X1):
    p = tmp_path / name
    p.write_bytes(payload)
    return str(p)


def _no_guard(monkeypatch):
    from voicebridge import vision as VI

    monkeypatch.setattr(VI, "_guard", lambda size_mib, url="": 99999)


# ------------------------------------------------- validation
def test_validate_missing_malformed_oversized_unsupported(tmp_path):
    from voicebridge import vision as VI

    for bad in ("", "/nonexistent-8tf/x.png"):
        try:
            VI.validate_image(bad, 20)
        except VI.VisionError as exc:
            assert exc.code == "VISION_BAD_IMAGE"
        else:
            raise AssertionError(f"must refuse {bad!r}")
    txt = tmp_path / "t.txt"
    txt.write_text("not an image")
    try:
        VI.validate_image(str(txt), 20)
    except VI.VisionError as exc:
        assert exc.code == "VISION_BAD_IMAGE", "magic bytes enforced"
    else:
        raise AssertionError("text file must be refused")
    big = tmp_path / "big.png"
    big.write_bytes(PNG_1X1)
    import os as _os
    _os.truncate(big, 3 * 1024 * 1024)
    try:
        VI.validate_image(str(big), 2)
    except VI.VisionError as exc:
        assert exc.code == "VISION_BAD_IMAGE", "size cap enforced"
    else:
        raise AssertionError("oversized must be refused")
    link = tmp_path / "link.png"
    link.symlink_to(tmp_path / "t.txt")
    try:
        VI.validate_image(str(link), 20)
    except VI.VisionError as exc:
        assert exc.code == "VISION_BAD_IMAGE", "symlinks refused"
    else:
        raise AssertionError("symlink must be refused")
    assert VI.detect_format(PNG_1X1) == "png"
    assert VI.detect_format(b"GIF89a....") == "gif"
    assert VI.detect_format(b"BM....") == "bmp"
    assert VI.detect_format(b"RIFF1234WEBP") == "webp"
    assert VI.detect_format(b"\x00\x01\x02") is None


def test_no_backend_without_model():
    from voicebridge import vision as VI

    stub = _Stub(vision_size_mib=0)
    try:
        try:
            VI.find_vision_model(f"http://127.0.0.1:{stub.port}", "stub-vision")
        except VI.VisionError as exc:
            assert exc.code == "VISION_NO_BACKEND"
        else:
            raise AssertionError("missing model must fail")
    finally:
        stub.stop()


def test_upstream_down_is_typed():
    from voicebridge import vision as VI

    try:
        VI.find_vision_model("http://127.0.0.1:9", "stub-vision", timeout_s=2)
    except VI.VisionError as exc:
        assert exc.code == "VISION_UPSTREAM"
    else:
        raise AssertionError("dead endpoint must fail typed")


def test_real_guard_refuses_blind_load():
    from voicebridge import vision as VI
    from voicebridge.vram_guard import VRAMGuardError

    try:
        VI._guard(10_000_000)
    except VRAMGuardError:
        pass
    else:
        raise AssertionError("absurd size must trip the real guard")


# ------------------------------------------------- success path (stubbed)
def test_describe_success_restores_resident(tmp_path, monkeypatch):
    from voicebridge import vision as VI

    stub = _Stub()
    try:
        cfg = _cfg(tmp_path, monkeypatch, stub)
        _no_guard(monkeypatch)
        img = _png(tmp_path)
        out = VI.describe(cfg, img, prompt="What is this?",
                          confirmed=True, timeout_s=30)
        assert out["state"] == "OK", out
        assert out["image"]["format"] == "png"
        assert out["resident_restored"] is True, "swap cycle restores"
        assert out["model"].startswith("stub-vision")
        assert "abc123secret" not in json.dumps(out), "reply redacted"
        assert "A red square" in out["description"]
        assert "/api/chat" in stub.hits and "/api/generate" in stub.hits
    finally:
        stub.stop()


def _files(root):
    return {str(p.relative_to(root)) for p in Path(root).rglob("*")
            if p.is_file()}


def test_describe_temp_hygiene_and_confirm(tmp_path, monkeypatch):
    from voicebridge import vision as VI

    stub = _Stub()
    try:
        cfg = _cfg(tmp_path, monkeypatch, stub)
        _no_guard(monkeypatch)
        img = _png(tmp_path)
        before = _files(tmp_path)
        try:
            VI.describe(cfg, img, confirmed=False)
        except VI.VisionError as exc:
            assert exc.code == "VISION_CONFIRM_REQUIRED"
        else:
            raise AssertionError("confirm required")
        VI.describe(cfg, img, confirmed=True, timeout_s=30)
        assert _files(tmp_path) == before, \
            "no temp files by design (empty state dirs are benign)"
    finally:
        stub.stop()


def test_describe_timeout_typed(tmp_path, monkeypatch):
    from voicebridge import vision as VI

    stub = _Stub(hang_chat=True)
    try:
        cfg = _cfg(tmp_path, monkeypatch, stub)
        _no_guard(monkeypatch)
        try:
            VI.describe(cfg, _png(tmp_path), confirmed=True, timeout_s=10)
        except VI.VisionError as exc:
            assert exc.code == "VISION_TIMEOUT", exc.code
        else:
            raise AssertionError("hang must time out typed")
    finally:
        stub.stop()


def test_estop_pre_and_post_latch(tmp_path, monkeypatch):
    from voicebridge import vision as VI

    stub = _Stub()
    try:
        cfg = _cfg(tmp_path, monkeypatch, stub)
        _no_guard(monkeypatch)
        monkeypatch.setattr(VI, "estop_engaged", lambda cfg: True)
        try:
            VI.describe(cfg, _png(tmp_path), confirmed=True, timeout_s=30)
        except VI.VisionError as exc:
            assert exc.code == "VISION_ESTOP"
        else:
            raise AssertionError("latched e-stop must refuse before swap")
        assert stub.hits == [], "refused before any model traffic"
        calls = {"n": 0}

        def _flapping(cfg):
            calls["n"] += 1
            return calls["n"] > 1

        monkeypatch.setattr(VI, "estop_engaged", _flapping)
        try:
            VI.describe(cfg, _png(tmp_path), confirmed=True, timeout_s=30)
        except VI.VisionError as exc:
            assert exc.code == "VISION_ESTOP", "mid-run latch discards reply"
        else:
            raise AssertionError("mid-run latch must discard")
    finally:
        stub.stop()


def test_audit_redacted_journal(tmp_path, monkeypatch):
    from voicebridge import vision as VI
    from voicebridge.logging_setup import setup

    stub = _Stub()
    try:
        cfg = _cfg(tmp_path, monkeypatch, stub)
        _no_guard(monkeypatch)
        setup(level="INFO", jsonl=True, state_dir=cfg.state_dir())
        try:
            VI.describe(cfg, _png(tmp_path),
                        prompt="my token=abc123secret here",
                        confirmed=True, timeout_s=30)
            blob = (Path(cfg.state_dir()) / "events.jsonl").read_text()
            assert "vision.describe" in blob
            assert "abc123secret" not in blob, "prompt/audit redacted"
        finally:
            from voicebridge import logging_setup as LS

            LS._EVENT_FILE = None
    finally:
        stub.stop()


# ------------------------------------------------- status + surface
def test_status_shapes(tmp_path, monkeypatch):
    from voicebridge import vision as VI
    from voicebridge import vram_guard as VG

    stub = _Stub()
    try:
        cfg = _cfg(tmp_path, monkeypatch, stub)
        monkeypatch.setattr(VG, "free_vram_mib", lambda timeout_s=3.0: 99999)
        out = VI.status(cfg)
        assert out["schema"] == "qubit-vision-status/1"
        assert out["state"] == "READY" and out["model"].startswith("stub-vision")
        monkeypatch.setattr(VG, "free_vram_mib", lambda timeout_s=3.0: 10)
        assert VI.status(cfg)["state"] == "VRAM_SHORT"
        cfg2 = _cfg(tmp_path, monkeypatch, None)
        out = VI.status(cfg2)
        assert out["state"] in ("NO_BACKEND", "UNAVAILABLE"), out
    finally:
        stub.stop()


def test_dashboard_inventory_and_tile(tmp_path, monkeypatch):
    from voicebridge.runtime_inventory import runtime_inventory

    cfg = _cfg(tmp_path, monkeypatch, None)
    inv = runtime_inventory(cfg)
    assert inv["vision"]["schema"] == "qubit-vision-status/1"
    assert inv["vision"]["state"] in ("READY", "NO_BACKEND", "UNAVAILABLE",
                                      "VRAM_SHORT", "UNMEASURED")
    assert "sk-" not in json.dumps(inv["vision"])
    html = (Path(__file__).resolve().parents[1] / "frontend" /
            "settings.html").read_text()
    assert "body.vision" in html and "vision.state" in html


def test_cli_wiring_and_catalog(tmp_path, monkeypatch):
    from voicebridge import cli as CLI
    from voicebridge.capability_catalog import check

    cfg = _cfg(tmp_path, monkeypatch, None)

    class _A:
        def __init__(self, **kw):
            self.vision_command = kw.get("cmd", "status")
            self.__dict__.update(kw)

    import io as _io
    from contextlib import redirect_stdout as _redir

    buf = _io.StringIO()
    with _redir(buf):
        assert CLI._cmd_vision(cfg, _A(cmd="status")) == 0
    assert json.loads(buf.getvalue())["schema"] == "qubit-vision-status/1"
    buf = _io.StringIO()
    with _redir(buf):
        rc = CLI._cmd_vision(cfg, _A(cmd="describe", image=_png(tmp_path)))
    assert rc == 3, "model load needs explicit confirmation"
    assert "vision" in CLI.build_parser().format_help()
    out = check("--image")
    assert out["state"] == "IMPLEMENTED", out
