"""Packet 8U — browser tab operations over loopback CDP (hermetic).

A fake DevTools endpoint (HTTP + websocket, stdlib only, in-thread)
proves the real code path: tab listing, current-tab ledger, bounded
redacted reads, find counts/snippets, confirmation-gated mutations,
input validation, non-loopback refusal, and CLI/dashboard/voice parity.
No real browser is touched; a closed port proves typed UNAVAILABLE.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import socket
import struct
import threading
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PAGE_TEXT = ("Welcome to the Inbox. Invoice INV-42 is due. "
             "Contact ops@example.com. bearer sk-test-abcdef1234 end.")
PAGE_TITLE = "Inbox — Synthetic Mail"
PAGE_URL = "https://mail.example.com/inbox"


# ------------------------------------------------------- fake CDP endpoint
class _FakeCDP:
    """HTTP /json/* + websocket CDP responder on 127.0.0.1:0."""

    def __init__(self):
        self.tabs = [
            {"id": "AAA111", "title": PAGE_TITLE, "url": PAGE_URL,
             "type": "page"},
            {"id": "BBB222", "title": "Inbox — Archive",
             "url": "https://mail.example.com/archive", "type": "page"},
        ]
        self.calls = []
        self._httpd = None
        self._ws_sock = None
        self.port = 0
        self.base = ""

    # ------------------------------------------------------------- HTTP
    def start(self):
        fake = self

        class _H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, payload, code=200):
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/json/version":
                    self._send({"Browser": "Fake/1.0",
                                "Protocol-Version": "1.3",
                                "webSocketDebuggerUrl":
                                    f"{fake.base}/devtools/browser/x"})
                elif self.path == "/json/list":
                    self._send([{**t, "webSocketDebuggerUrl":
                                 f"{fake.base}/devtools/page/{t['id']}"}
                                for t in fake.tabs])
                elif self.path.startswith("/json/activate/"):
                    self._send({})
                elif self.path.startswith("/json/close/"):
                    tid = self.path.rsplit("/", 1)[-1]
                    fake.tabs = [t for t in fake.tabs
                                 if t["id"] != tid]
                    self._send({})
                else:
                    self._send({"error": "nope"}, 404)

            def do_PUT(self):
                if self.path.startswith("/json/new"):
                    tid = f"NEW{len(fake.tabs)}"
                    tab = {"id": tid, "title": "New Tab",
                           "url": "about:blank", "type": "page"}
                    fake.tabs.append(tab)
                    self._send({**tab, "webSocketDebuggerUrl":
                                f"{fake.base}/devtools/page/{tid}"})
                else:
                    self._send({"error": "nope"}, 404)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        self.port = self._httpd.server_address[1]
        self.base = f"ws://127.0.0.1:{self.port}"
        threading.Thread(target=self._httpd.serve_forever,
                         daemon=True).start()
        threading.Thread(target=self._serve_ws, daemon=True).start()

    def stop(self):
        try:
            self._httpd.shutdown()
        except Exception:
            pass

    # --------------------------------------------------------------- WS
    def _serve_ws(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # NOTE: the websocket shares the HTTP port is impossible with two
        # sockets; instead the fake advertises ws URLs on the WS port and
        # the test rewrites them. Simpler: serve WS on its own port and
        # patch tab URLs after start.
        srv.bind(("127.0.0.1", 0))
        srv.listen(5)
        self.ws_port = srv.getsockname()[1]
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=self._handle_ws, args=(conn,),
                             daemon=True).start()

    def _handle_ws(self, conn):
        try:
            header = b""
            while b"\r\n\r\n" not in header:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                header += chunk
            lines = header.split(b"\r\n")[0].decode().split(" ")
            key = ""
            for line in header.decode("latin-1").split("\r\n"):
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
            accept = base64.b64encode(hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
            ).digest()).decode()
            conn.sendall(
                ("HTTP/1.1 101 Switching Protocols\r\n"
                 "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                 f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
            while True:
                msg = self._recv(conn)
                if msg is None:
                    return
                try:
                    req = json.loads(msg.decode())
                except ValueError:
                    continue
                self.calls.append(req.get("method"))
                res = self._dispatch(req)
                self._send(conn, {"id": req.get("id"), "result": res})
        except Exception:
            return
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _recv(self, conn):
        head = conn.recv(2)
        if len(head) < 2:
            return None
        length = head[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", conn.recv(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", conn.recv(8))[0]
        mask = conn.recv(4)
        payload = b""
        while len(payload) < length:
            chunk = conn.recv(length - len(payload))
            if not chunk:
                return None
            payload += chunk
        return bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

    def _send(self, conn, obj):
        payload = json.dumps(obj).encode()
        head = bytes([0x81])
        if len(payload) < 126:
            head += bytes([len(payload)])
        elif len(payload) < 65536:
            head += bytes([126]) + struct.pack(">H", len(payload))
        else:
            head += bytes([127]) + struct.pack(">Q", len(payload))
        conn.sendall(head + payload)

    def _dispatch(self, req):
        method = req.get("method", "")
        params = req.get("params", {}) or {}
        if method == "Runtime.evaluate":
            expr = params.get("expression", "")
            if "needle" in expr:
                count = PAGE_TEXT.lower().count("invoice")
                return {"result": {"value": {
                    "count": count,
                    "snippets": [PAGE_TEXT[:80]] if count else []}}}
            return {"result": {"value": {
                "title": PAGE_TITLE, "url": PAGE_URL, "text": PAGE_TEXT}}}
        if method == "DOM.getDocument":
            return {"root": {"nodeId": 1}}
        if method == "DOM.querySelector":
            if params.get("selector") == "#missing":
                return {"nodeId": 0}
            return {"nodeId": 2}
        if method == "DOM.getBoxModel":
            return {"model": {"content": [0, 0, 100, 0, 100, 20, 0, 20]}}
        if method in ("DOM.focus", "Input.insertText",
                      "Input.dispatchMouseEvent", "Input.dispatchKeyEvent",
                      "Page.navigate"):
            return {}
        return {}


@pytest.fixture()
def fake_cdp(monkeypatch):
    from voicebridge import cdp as CDP

    fake = _FakeCDP()
    fake.start()
    # The fake serves WS on its own port: rewrite advertised URLs.
    real_dispatch = fake._dispatch
    orig_list = fake.tabs
    monkeypatch.setattr(CDP, "CDP_PORT", fake.port)

    import urllib.request as _u

    orig_http = CDP.http_json

    def _patched(method, path, *, timeout=CDP.HTTP_TIMEOUT_S, query=""):
        out = orig_http(method, path, timeout=timeout, query=query)
        if isinstance(out, list):
            for item in out:
                if isinstance(item, dict) and "webSocketDebuggerUrl" in item:
                    item["webSocketDebuggerUrl"] = \
                        item["webSocketDebuggerUrl"].replace(
                            f"ws://127.0.0.1:{fake.port}",
                            f"ws://127.0.0.1:{fake.ws_port}")
        elif isinstance(out, dict) and "webSocketDebuggerUrl" in out:
            out["webSocketDebuggerUrl"] = out["webSocketDebuggerUrl"].replace(
                f"ws://127.0.0.1:{fake.port}",
                f"ws://127.0.0.1:{fake.ws_port}")
        return out

    monkeypatch.setattr(CDP, "http_json", _patched)
    yield fake
    fake.stop()


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _ctx(cfg, session="voice", source="user"):
    from voicebridge import voice_commands as VC

    return VC.make_ctx(cfg, session=session, source=source)


# ----------------------------------------------------------------- reads
def test_tabs_list_and_current_ledger(tmp_path, monkeypatch, fake_cdp):
    from voicebridge import browser_tabs as BT

    cfg = _cfg(tmp_path, monkeypatch)
    out = BT.tabs(cfg)
    assert out["state"] == "OK"
    assert len(out["tabs"]) == 2
    assert out["tabs"][0]["current"] is True
    assert out["current_target"] == "AAA111"
    # Activating the second tab moves the marker.
    BT.activate(cfg, "BBB222", confirmed=True)
    out2 = BT.tabs(cfg)
    assert out2["current_target"] == "BBB222"


def test_read_bounded_redacted_untrusted(tmp_path, monkeypatch, fake_cdp):
    from voicebridge import browser_tabs as BT

    cfg = _cfg(tmp_path, monkeypatch)
    out = BT.read(cfg, "")
    assert out["state"] == "OK"
    assert out["title"] == PAGE_TITLE
    assert out["trust"] == "untrusted-page-text"
    assert "sk-test-abcdef1234" not in out["text"]
    assert "INV-42" in out["text"]


def test_find_counts_and_snippets(tmp_path, monkeypatch, fake_cdp):
    from voicebridge import browser_tabs as BT

    cfg = _cfg(tmp_path, monkeypatch)
    out = BT.find(cfg, "invoice")
    assert out["state"] == "OK"
    assert out["count"] == 1
    assert out["snippets"] and "INV-42" in out["snippets"][0]
    assert "sk-test-abcdef1234" not in json.dumps(out)


def test_doctor_reports_bridge(tmp_path, monkeypatch, fake_cdp):
    from voicebridge import browser_tabs as BT

    cfg = _cfg(tmp_path, monkeypatch)
    out = BT.doctor(cfg)
    assert out["state"] == "OK"
    assert out["tabs"] == 2
    assert "never cloned" in out["profiles"]


def test_unavailable_without_endpoint(tmp_path, monkeypatch):
    from voicebridge import browser_tabs as BT
    from voicebridge import cdp as CDP

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(CDP, "CDP_PORT", 9)  # discard port: shut
    out = BT.tabs(cfg)
    assert out["state"] == "UNAVAILABLE"
    assert out["code"] == "CDP_UNAVAILABLE"
    assert BT.doctor(cfg)["state"] == "UNAVAILABLE"


def test_non_loopback_ws_refused():
    from voicebridge import cdp as CDP

    with pytest.raises(CDP.CdpError) as exc:
        CDP.WsSession.connect("ws://example.com/devtools/page/1",
                              timeout=1)
    assert exc.value.code == "CDP_NOT_LOOPBACK"


# ------------------------------------------------------------- validation
def test_open_rejects_bad_urls(tmp_path, monkeypatch, fake_cdp):
    from voicebridge import browser_tabs as BT

    cfg = _cfg(tmp_path, monkeypatch)
    for bad in ("javascript:alert(1)", "https://user:pw@example.com/",
                "ftp://example.com/x", "", "https://example.com/" + "x" * 3000):
        with pytest.raises(BT.BrowserTabsError) as exc:
            BT.open(cfg, bad, confirmed=True)
        assert exc.value.code == BT.BAD_INPUT_CODE, bad


def test_mutations_require_confirmation(tmp_path, monkeypatch, fake_cdp):
    from voicebridge import browser_tabs as BT

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(BT.BrowserTabsError) as exc:
        BT.open(cfg, "https://example.com/", confirmed=False)
    assert exc.value.code == BT.CONFIRM_CODE
    with pytest.raises(BT.BrowserTabsError):
        BT.fill(cfg, "", "input", "x", confirmed=False)
    with pytest.raises(BT.BrowserTabsError):
        BT.click(cfg, "", "a", confirmed=False)


def test_fragment_resolution_and_ambiguity(tmp_path, monkeypatch, fake_cdp):
    from voicebridge import browser_tabs as BT

    cfg = _cfg(tmp_path, monkeypatch)
    page = BT._resolve_target(cfg, "archive")
    assert page["id"] == "BBB222"
    with pytest.raises(BT.BrowserTabsError) as exc:
        BT._resolve_target(cfg, "inbox")
    assert exc.value.code == BT.BAD_INPUT_CODE  # two tabs match
    with pytest.raises(BT.BrowserTabsError) as exc:
        BT._resolve_target(cfg, "no-such-tab-xyz")
    assert exc.value.code == BT.NOT_FOUND_CODE


def test_fill_click_close_confirmed(tmp_path, monkeypatch, fake_cdp):
    from voicebridge import browser_tabs as BT

    cfg = _cfg(tmp_path, monkeypatch)
    assert BT.fill(cfg, "", "#q", "hello",
                   confirmed=True)["filled"] is True
    assert BT.click(cfg, "", "a",
                    confirmed=True)["clicked"] is True
    with pytest.raises(BT.BrowserTabsError):
        BT.fill(cfg, "", "#missing", "x", confirmed=True)
    assert BT.close(cfg, "BBB222", confirmed=True)["closed"] is True
    assert len(BT.tabs(cfg)["tabs"]) == 1


def test_open_and_navigate_confirmed(tmp_path, monkeypatch, fake_cdp):
    from voicebridge import browser_tabs as BT

    cfg = _cfg(tmp_path, monkeypatch)
    created = BT.open(cfg, "https://example.com/", confirmed=True)
    assert created["state"] == "OK" and created["target"].startswith("NEW")
    nav = BT.navigate(cfg, "", "https://example.org/", confirmed=True)
    assert nav["state"] == "OK"


# ------------------------------------------------------------------- CLI
class _A:
    def __init__(self, cmd, **kw):
        self.browser_command = cmd
        self.target = kw.get("target", "")
        self.query = kw.get("query", "")
        self.url = kw.get("url", "")
        self.selector = kw.get("selector", "")
        self.text = kw.get("text", "")
        self.confirm = kw.get("confirm", False)
        self.dry_run = kw.get("dry_run", False)


def _run(cfg, args):
    from voicebridge import cli as CLI

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_browser(cfg, args)
    return rc, json.loads(buf.getvalue())


def test_cli_tabs_read_find(tmp_path, monkeypatch, fake_cdp):
    cfg = _cfg(tmp_path, monkeypatch)
    rc, body = _run(cfg, _A("tabs"))
    assert rc == 0 and len(body["tabs"]) == 2
    rc, body = _run(cfg, _A("read"))
    assert rc == 0 and body["title"] == PAGE_TITLE
    rc, body = _run(cfg, _A("find", query="invoice"))
    assert rc == 0 and body["count"] == 1


def test_cli_mutations_need_confirm_and_preview(tmp_path, monkeypatch,
                                                fake_cdp):
    cfg = _cfg(tmp_path, monkeypatch)
    rc, body = _run(cfg, _A("open", url="https://example.com/"))
    assert rc == 3 and body["state"] == "denied"
    rc, body = _run(cfg, _A("open", url="https://example.com/",
                            dry_run=True))
    assert rc == 0 and body["state"] == "preview"
    rc, body = _run(cfg, _A("open", url="https://example.com/",
                            confirm=True))
    assert rc == 0 and body["state"] == "OK"
    rc, body = _run(cfg, _A("fill", selector="#q", text="hi"))
    assert rc == 3
    rc, body = _run(cfg, _A("fill", selector="#q", text="hi",
                            confirm=True))
    assert rc == 0 and body["filled"] is True


# ------------------------------------------------------------- voice+dash
def test_voice_browser_reads_execute(tmp_path, monkeypatch, fake_cdp):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    for text in ("qubit, list open tabs", "qubit, browser doctor",
                 "qubit, read the current tab",
                 "qubit, search this page invoice"):
        res = VC.resolve(text)
        assert res.kind == VC.MATCH, (text, res.kind)
        out = VC.execute(_ctx(cfg), res)
        assert out.kind == VC.EXECUTED, (text, out.kind, out.speech)
    assert "sk-test-abcdef1234" not in out.speech


def test_voice_browser_mutations_confirm(tmp_path, monkeypatch, fake_cdp):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    res = VC.resolve("qubit, open the browser")
    assert res.kind == VC.MATCH
    out = VC.execute(_ctx(cfg), res)
    assert out.kind == VC.NEEDS_CONFIRM
    out2 = VC.confirm_pending(_ctx(cfg), out.confirmation_id)
    assert out2.kind == VC.EXECUTED, (out2.kind, out2.speech)
    assert out2.receipt["result"]["browser"]["state"] == "OK"


def test_dashboard_browser_ops(tmp_path, monkeypatch, fake_cdp):
    from voicebridge import dashboard_ops as D

    cfg = _cfg(tmp_path, monkeypatch)
    out = D.run_registry_op(cfg, "browser_read", {},
                            actor="dashboard:user", session="dashboard")
    assert out["state"] == "executed", out
    assert PAGE_TITLE in out["speech"]
    staged = D.run_registry_op(cfg, "browser_open_browser", {},
                               actor="dashboard:user", session="dashboard")
    assert staged["state"] == "needs-confirm", staged
    done = D.confirm_registry_op(cfg, staged["confirmation_id"],
                                 actor="dashboard:user", session="dashboard")
    assert done["state"] == "executed", done


def test_voice_commands_respect_8a_boundary():
    text = (Path(__file__).resolve().parents[1] / "voicebridge" /
            "browser_tabs.py").read_text()
    assert "voicebridge.communications" not in text
    assert "from .communications" not in text
