"""A STOP kills the in-flight model call, not just its result (9u).

The previous lanes made a cancel return the typed ``VOICE_EDIT_ASK_CANCELLED``
promptly and refused the late reply, but the blocking model call itself kept
running to its timeout: the local ``chat_local`` ``urllib`` read (up to ~30 s)
and a remote frontier response had no cancellation hook.

This pins the real abort:

- ``local_llm.chat_local`` accepts the turn's cancel hook; when supplied the
  request runs over a socket the module owns and a watcher ``shutdown``s it,
  raising ``LocalLlmCancelled`` at once. With no hook the urllib path is kept.
- ``frontier.AbortableHTTPTransport`` owns its socket and ``abort()`` shuts it
  down mid-response; ``FrontierClient`` watches the same hook and maps the
  abort to the typed ``CANCELLED`` result.
- ``gateway._local_complete`` threads the cancel into the local adapter, so
  the service's existing per-turn event reaches the model call.

Hermetic: a loopback fake slow server only; no external network, model, mic,
audio or X.
"""
from __future__ import annotations

import contextlib
import http.server
import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voicebridge.local_llm as LLM  # noqa: E402
from voicebridge.providers import frontier as F  # noqa: E402
from voicebridge.providers import gateway as GW  # noqa: E402
from voicebridge.providers.contracts import ProviderRequest  # noqa: E402


CHAT_BODY = {"message": {"content": "ok"}}
FRONTIER_BODY = {"choices": [{"message": {"content": "cloud hello"}}],
                 "usage": {"prompt_tokens": 2, "completion_tokens": 3}}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - stdlib contract
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self.server.requested.set()  # type: ignore[attr-defined]
        if self.server.hold:  # type: ignore[attr-defined]
            self.server.gate.wait(timeout=10.0)  # type: ignore[attr-defined]
        raw = json.dumps(self.server.body).encode()  # type: ignore[attr-defined]
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *a) -> None:  # silence the test server
        pass


@contextlib.contextmanager
def _server(body, *, hold=False):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.daemon_threads = True
    srv.requested = threading.Event()  # type: ignore[attr-defined]
    srv.gate = threading.Event()  # type: ignore[attr-defined]
    srv.body = body  # type: ignore[attr-defined]
    srv.hold = hold  # type: ignore[attr-defined]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.gate.set()  # type: ignore[attr-defined]
        srv.shutdown()
        srv.server_close()


def _join_promptly(t, cancel, *, budget=0.5):
    t0 = time.monotonic()
    cancel.set()
    t.join(timeout=1.0)
    elapsed = time.monotonic() - t0
    assert not t.is_alive(), "the model call stayed blocked after the STOP"
    assert elapsed < budget, f"unblock took {elapsed:.3f}s (want < {budget}s)"
    return elapsed


# ---------------------------------------------------------------------------
# local adapter: the owned socket is shut down mid-read
# ---------------------------------------------------------------------------
def test_local_chat_cancel_unblocks_the_blocking_read():
    with _server(CHAT_BODY, hold=True) as srv:
        url = f"http://127.0.0.1:{srv.server_port}"
        cancel = threading.Event()
        out = {}

        def run():
            try:
                LLM.chat_local("hi", url=url, timeout_s=30.0,
                               cancel=cancel.is_set)
            except LLM.LocalLlmCancelled:
                out["cancelled"] = True
            except Exception as exc:  # noqa: BLE001 - surfaced in assertion
                out["error"] = exc

        t = threading.Thread(target=run, daemon=True)
        t.start()
        assert srv.requested.wait(timeout=10.0), "request never arrived"
        elapsed = _join_promptly(t, cancel)
        assert out.get("cancelled") is True
        assert "error" not in out
        # Far under the 30 s request timeout the old path would have waited.
        assert elapsed < 0.5


def test_local_chat_normal_call_is_unchanged():
    with _server(CHAT_BODY) as srv:
        url = f"http://127.0.0.1:{srv.server_port}"
        assert LLM.chat_local("hi", url=url, timeout_s=5.0) == "ok"
        assert LLM.chat_local("hi", url=url, timeout_s=5.0,
                              cancel=threading.Event().is_set) == "ok"


def test_local_chat_repeated_cancel_is_safe_and_prompt():
    with _server(CHAT_BODY, hold=True) as srv:
        url = f"http://127.0.0.1:{srv.server_port}"
        cancel = threading.Event()
        cancel.set()
        for _ in range(3):
            t0 = time.monotonic()
            try:
                LLM.chat_local("hi", url=url, timeout_s=30.0,
                               cancel=cancel.is_set)
            except LLM.LocalLlmCancelled:
                pass
            else:  # pragma: no cover - must never happen
                raise AssertionError("a pre-set cancel must not call the model")
            assert time.monotonic() - t0 < 0.2
        # Nothing was sent to the server.
        assert srv.requested.wait(timeout=0.1) is False


def test_abortable_post_abort_is_idempotent_and_safe_before_connect():
    holder = LLM.AbortableHTTPPost("http://127.0.0.1:1/api/chat", b"{}")
    assert holder.abort() is False
    assert holder.abort() is False
    try:
        holder.post()
    except LLM.LocalLlmCancelled:
        pass
    else:  # pragma: no cover - a pre-aborted post must fail typed
        raise AssertionError("aborted post did not fail typed")


# ---------------------------------------------------------------------------
# streaming local adapter: a blocked chunk read is unblocked by the hook
# ---------------------------------------------------------------------------
class _StreamHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_POST(self) -> None:  # noqa: N802 - stdlib contract
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self.server.requested.set()  # type: ignore[attr-defined]
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Connection", "close")
            self.end_headers()
            for chunk in self.server.first:  # type: ignore[attr-defined]
                self.wfile.write((json.dumps(chunk) + "\n").encode())
                self.wfile.flush()
            self.server.first_sent.set()  # type: ignore[attr-defined]
            if self.server.hold:  # type: ignore[attr-defined]
                self.server.gate.wait(timeout=10.0)  # type: ignore[attr-defined]
            for chunk in self.server.rest:  # type: ignore[attr-defined]
                self.wfile.write((json.dumps(chunk) + "\n").encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *a) -> None:  # silence the test server
        pass


@contextlib.contextmanager
def _stream_server(first, rest=(), *, hold=False):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _StreamHandler)
    srv.daemon_threads = True
    srv.requested = threading.Event()  # type: ignore[attr-defined]
    srv.first_sent = threading.Event()  # type: ignore[attr-defined]
    srv.gate = threading.Event()  # type: ignore[attr-defined]
    srv.first = list(first)  # type: ignore[attr-defined]
    srv.rest = list(rest)  # type: ignore[attr-defined]
    srv.hold = hold  # type: ignore[attr-defined]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.gate.set()  # type: ignore[attr-defined]
        srv.shutdown()
        srv.server_close()


def _wait_emitted(got, *, budget=2.0):
    deadline = time.monotonic() + budget
    while not got and time.monotonic() < deadline:
        time.sleep(0.005)
    return list(got)


def _no_stream_watchers_left():
    return not any(
        t.name == "local-llm-stream-cancel" and t.is_alive()
        for t in threading.enumerate())


def test_local_stream_cancel_unblocks_a_blocked_chunk():
    first = [{"message": {"content": "First."}}]
    with _stream_server(first, hold=True) as srv:
        url = f"http://127.0.0.1:{srv.server_port}"
        cancel = threading.Event()
        got: list[str] = []
        out: dict = {}

        def run():
            try:
                LLM.chat_local_stream(
                    "hi", url=url, timeout_s=30.0, on_sentence=got.append,
                    cancel=cancel.is_set)
            except LLM.LocalLlmCancelled:
                out["cancelled"] = True
            except Exception as exc:  # noqa: BLE001 - surfaced in assertion
                out["error"] = exc

        t = threading.Thread(target=run, daemon=True)
        t.start()
        assert srv.first_sent.wait(timeout=2.0), "stream never started"
        assert _wait_emitted(got) == ["First."], got
        elapsed = _join_promptly(t, cancel)
        assert out.get("cancelled") is True
        assert "error" not in out
        assert elapsed < 0.5
        assert _no_stream_watchers_left()


def test_local_stream_should_stop_unblocks_a_blocked_chunk():
    first = [{"message": {"content": "First."}}]
    with _stream_server(first, hold=True) as srv:
        url = f"http://127.0.0.1:{srv.server_port}"
        stop = threading.Event()
        got: list[str] = []
        out: dict = {}

        def run():
            try:
                out["full"] = LLM.chat_local_stream(
                    "hi", url=url, timeout_s=30.0, on_sentence=got.append,
                    should_stop=stop.is_set)
            except Exception as exc:  # noqa: BLE001 - surfaced in assertion
                out["error"] = exc

        t = threading.Thread(target=run, daemon=True)
        t.start()
        assert srv.first_sent.wait(timeout=2.0), "stream never started"
        assert _wait_emitted(got) == ["First."], got
        elapsed = _join_promptly(t, stop)
        assert "error" not in out
        # Cooperative stop keeps the spoken partial and never raises.
        assert out.get("full") == "First."
        assert elapsed < 0.5
        assert _no_stream_watchers_left()


def test_local_stream_normal_call_is_unchanged():
    first = [
        {"message": {"content": "One. "}},
        {"message": {"content": "Two."}},
    ]
    rest = [{"message": {"content": "", "done": True}}]
    with _stream_server(first, rest) as srv:
        url = f"http://127.0.0.1:{srv.server_port}"
        got: list[str] = []
        full = LLM.chat_local_stream(
            "hi", url=url, timeout_s=5.0, on_sentence=got.append,
            cancel=threading.Event().is_set)
        assert got == ["One.", "Two."]
        assert full == "One. Two."
        assert _no_stream_watchers_left()


def test_local_stream_repeated_preset_cancel_is_safe():
    with _stream_server([], hold=True) as srv:
        url = f"http://127.0.0.1:{srv.server_port}"
        cancel = threading.Event()
        cancel.set()
        for _ in range(3):
            t0 = time.monotonic()
            with pytest.raises(LLM.LocalLlmCancelled):
                LLM.chat_local_stream(
                    "hi", url=url, timeout_s=30.0,
                    on_sentence=lambda s: None, cancel=cancel.is_set)
            assert time.monotonic() - t0 < 0.2
        # A pre-set cancel must never reach the model server.
        assert srv.requested.wait(timeout=0.1) is False
        assert _no_stream_watchers_left()


# ---------------------------------------------------------------------------
# raw local adapters' blocking seam (`/api/generate`, `/api/chat`)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/api/generate", "/api/chat"])
def test_post_json_blocking_seam_unblocks_on_cancel(path):
    with _server(CHAT_BODY, hold=True) as srv:
        url = f"http://127.0.0.1:{srv.server_port}{path}"
        cancel = threading.Event()
        out: dict = {}

        def run():
            try:
                LLM.post_json(url, {"stream": False}, 30.0,
                              cancel=cancel.is_set)
            except LLM.LocalLlmCancelled:
                out["cancelled"] = True
            except Exception as exc:  # noqa: BLE001 - surfaced in assertion
                out["error"] = exc

        t = threading.Thread(target=run, daemon=True)
        t.start()
        assert srv.requested.wait(timeout=10.0), "request never arrived"
        elapsed = _join_promptly(t, cancel)
        assert out.get("cancelled") is True
        assert "error" not in out
        assert elapsed < 0.5


@pytest.mark.parametrize("path", ["/api/generate", "/api/chat"])
def test_post_json_blocking_seam_normal_call_is_unchanged(path):
    with _server(CHAT_BODY) as srv:
        url = f"http://127.0.0.1:{srv.server_port}{path}"
        raw = LLM.post_json(url, {"stream": False}, 5.0,
                            cancel=threading.Event().is_set)
        assert json.loads(raw.decode()) == CHAT_BODY


# ---------------------------------------------------------------------------
# frontier: the remote response socket is shut down mid-read
# ---------------------------------------------------------------------------
def _frontier_client(srv, cancel):
    return F.FrontierClient(
        f"http://127.0.0.1:{srv.server_port}/v1", "k", "zen", "m1",
        timeout_s=30.0, max_retries=1, transport=F.AbortableHTTPTransport(),
        cancel=cancel.is_set)


def _frontier_req():
    return ProviderRequest(prompt="hi", max_tokens=16, route="frontier")


def test_frontier_cancel_unblocks_the_blocking_response():
    with _server(FRONTIER_BODY, hold=True) as srv:
        cancel = threading.Event()
        client = _frontier_client(srv, cancel)
        out = {}

        def run():
            out["res"] = client.complete(_frontier_req(), "hi")

        t = threading.Thread(target=run, daemon=True)
        t.start()
        assert srv.requested.wait(timeout=10.0), "request never arrived"
        _join_promptly(t, cancel)
        res = out.get("res")
        assert res is not None
        assert res.ok is False
        assert res.code == "CANCELLED"


def test_frontier_normal_response_is_unchanged():
    with _server(FRONTIER_BODY) as srv:
        res = _frontier_client(srv, threading.Event()).complete(
            _frontier_req(), "hi")
        assert res.ok is True
        assert res.text == "cloud hello"


# ---------------------------------------------------------------------------
# gateway/service wiring: the turn's cancel reaches the local model call
# ---------------------------------------------------------------------------
class _Cfg:
    def get(self, *path, default=None):
        return default


def test_gateway_local_complete_threads_cancel_into_the_adapter(monkeypatch):
    with _server(CHAT_BODY, hold=True) as srv:
        url = f"http://127.0.0.1:{srv.server_port}"
        from voicebridge import runner_contract as RC

        monkeypatch.setattr(
            RC, "resolve",
            lambda cfg=None: RC.RunnerContract(
                alias="qb", url=url, num_ctx=4096))
        cancel = threading.Event()
        out = {}

        def run():
            out["res"] = GW._local_complete(
                "hi", _Cfg(), 32, timeout_s=30.0, cancel=cancel.is_set)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        assert srv.requested.wait(timeout=10.0), "request never arrived"
        _join_promptly(t, cancel)
        res = out.get("res")
        assert res is not None and res.ok is False
        assert res.code == "CANCELLED"


def test_gateway_local_complete_normal_call_is_unchanged(monkeypatch):
    with _server(CHAT_BODY) as srv:
        url = f"http://127.0.0.1:{srv.server_port}"
        from voicebridge import runner_contract as RC

        monkeypatch.setattr(
            RC, "resolve",
            lambda cfg=None: RC.RunnerContract(
                alias="qb", url=url, num_ctx=4096))
        res = GW._local_complete("hi", _Cfg(), 32, timeout_s=5.0,
                                 cancel=threading.Event().is_set)
        assert res.ok is True and res.text == "ok"


def test_gateway_local_generate_and_chat_raw_are_abortable(monkeypatch):
    # The raw `/api/generate` and `/api/chat` adapters used to inline a
    # blocking urlopen; they now route through post_json(cancel=...) so a STOP
    # aborts the live socket instead of waiting out the timeout.
    import voicebridge.providers.gateway as G
    from voicebridge import runner_contract

    with _server(CHAT_BODY, hold=True) as srv:
        url = f"http://127.0.0.1:{srv.server_port}"

        class _RC:
            alias = "test-model"
            num_ctx = 4096
            keep_alive = -1
            url = ""

        rc = _RC()
        rc.url = url
        monkeypatch.setattr(runner_contract, "resolve", lambda cfg: rc)
        for mode in ("generate", "chat_raw"):
            cancel = threading.Event()
            out = {}

            def run(mode=mode, cancel=cancel, out=out):
                out["res"] = G._local_dispatch(
                    "hi", cfg=object(), max_tokens=8,
                    local_mode=mode, cancel=cancel.is_set)

            t = threading.Thread(target=run, daemon=True)
            t.start()
            assert srv.requested.wait(timeout=10.0), f"{mode}: request never arrived"
            srv.requested.clear()
            _join_promptly(t, cancel)
            assert out["res"].ok is False, (mode, out["res"])
            assert out["res"].code == "CANCELLED", (mode, out["res"].code)
