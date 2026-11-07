"""Real token streaming for the remote provider path.

The defect these tests lock down: ``chat_transport._stream_remote`` used to
call the blocking ``_generate_remote`` — waiting for the whole reply, then
emitting ONE ``delta`` containing all of it, with ``streaming: False`` on the
start frame. The assistant row in the UI therefore stayed empty for the whole
turn.

The fix streams the provider's own SSE chunks: every ``choices[0].delta.content``
becomes its own ``delta`` event, and exactly ONE ``done`` carries the full reply
and receipt. Nothing is fabricated: a provider that ignores ``stream=True`` and
answers with one whole body yields one delta and is reported honestly as
``streaming: False``.

These tests run a REAL local HTTP server that speaks OpenAI-compatible SSE, so
the socket read loop (not a mock of it) is what is under test.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _preload_qubit_state() -> None:
    """Import the real ``qubit_state`` before ``tests/qubit_state/`` wins.

    ``pytest.ini`` puts ``tests/`` on ``sys.path`` (its helpers are importable
    modules). That directory holds a ``qubit_state/`` folder with no
    ``__init__.py``, so Python can bind the name to that *namespace package*
    instead of the vendored ``qubit_runtime/qubit_state.py`` — and every
    ``from qubit_state import SessionDB`` then fails. Loading the real module
    first pins the correct object in ``sys.modules``.
    """
    if "qubit_state" in sys.modules and hasattr(
            sys.modules["qubit_state"], "SessionDB"):
        return
    from voicebridge import qubit_runtime

    qubit_runtime.ensure_path()
    import importlib
    import importlib.util

    # A checkout reused from another tree can leave the fork root BEHIND a
    # stale copy in sys.path, so ``qubit_state`` resolves against the wrong
    # ``agent/`` package. Put this worktree's fork root first for the import.
    root = str(qubit_runtime.runtime_root())
    if root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)
    for name in [n for n in sys.modules if n == "agent"
                 or n.startswith("agent.")]:
        sys.modules.pop(name, None)
    mod = importlib.import_module("qubit_state")
    if not hasattr(mod, "SessionDB"):
        path = Path(root) / "qubit_state.py"
        spec = importlib.util.spec_from_file_location("qubit_state", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules["qubit_state"] = mod


_preload_qubit_state()


CHUNKS = ["Hello", " there", ", ", "world", "!"]
FULL = "".join(CHUNKS)


# ---------------------------------------------------------------------------
# A real OpenAI-compatible provider that streams SSE
# ---------------------------------------------------------------------------
class _Provider:
    """Scripted OpenAI-compatible endpoint, served over a real socket."""

    def __init__(self, *, mode="stream", delay_s=0.0, status=200):
        self.mode = mode
        self.delay_s = delay_s
        self.status = status
        self.requests: list = []
        self.sse_times: list = []
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._srv.serve_forever,
                                        daemon=True)

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self._srv.server_address[1]}/v1"

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._srv.shutdown()
        self._srv.server_close()
        return False

    def _handler(self):
        provider = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):  # silence
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                provider.requests.append(body)
                if provider.status != 200:
                    payload = json.dumps({"error": "nope"}).encode()
                    self.send_response(provider.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                if provider.mode == "whole_body" or not body.get("stream"):
                    # A provider that ignores stream=true: one JSON body.
                    payload = json.dumps({
                        "choices": [{"message": {"content": FULL},
                                     "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 7, "completion_tokens": 5},
                    }).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                # No Content-Length: the client must read frames as they land.
                self.send_header("Connection", "close")
                self.end_headers()
                for i, chunk in enumerate(CHUNKS):
                    if provider.delay_s:
                        time.sleep(provider.delay_s)
                    provider.sse_times.append(time.monotonic())
                    frame = {
                        "choices": [{"index": 0,
                                     "delta": {"content": chunk},
                                     "finish_reason": None}],
                    }
                    self.wfile.write(
                        f"data: {json.dumps(frame)}\n\n".encode())
                    self.wfile.flush()
                    if i == 0:
                        # A bare keep-alive comment must be skipped, not treated
                        # as content.
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                self.wfile.write(("data: "
                                  + json.dumps({
                                      "choices": [{"index": 0, "delta": {},
                                                   "finish_reason": "stop"}],
                                      "usage": {"prompt_tokens": 7,
                                                "completion_tokens": 5}})
                                  + "\n\n").encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        return H


# ---------------------------------------------------------------------------
# Config helpers (hermetic: temp XDG trees, no network beyond 127.0.0.1)
# ---------------------------------------------------------------------------
def _cfg(tmp_path, monkeypatch, provider_base_url: str):
    from voicebridge import config as C
    from voicebridge.providers import resolver as R

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.delenv("QUBIT_HOME", raising=False)
    p = tmp_path / "voice.yaml"
    p.write_text(
        "logging:\n  jsonl: false\n"
        "intent:\n  frontier:\n    enabled: true\n"
        "    allowlist: ['localrelay/test-model']\n"
        "    max_tokens: 1024\n"
        "    timeout_s: 30\n"
        "    max_response_chars: 8000\n")
    cfg = C.Config.load(p)
    R.upsert_model_profile(cfg, R.ModelProfile(
        id="localrelay-test", kind=R.API, provider="localrelay",
        model="test-model", base_url=provider_base_url,
        key_ref="env:TEST_RELAY_KEY", locality="external",
        egress_consent="allow", roles=["chat"], context_tokens=8000),
        confirm=True)
    monkeypatch.setenv("TEST_RELAY_KEY", "test-key-not-real")
    R._RUNTIME_MODEL_CACHE.clear()
    return cfg


@pytest.fixture(autouse=True)
def _reset_governor():
    from voicebridge.providers import policy as P

    P.reset_governor()
    yield
    P.reset_governor()


def _routed():
    return {"ok": True, "model": "test-model", "provider": "localrelay",
            "kind": "api", "context_tokens": 8000,
            "profile_id": "localrelay-test"}


# ---------------------------------------------------------------------------
# 1. The client really streams: N chunks in, N callbacks out, in order
# ---------------------------------------------------------------------------
def test_frontier_client_emits_one_delta_per_chunk(tmp_path, monkeypatch):
    from voicebridge.providers.frontier import FrontierClient
    from voicebridge.providers.contracts import ProviderRequest

    with _Provider() as prov:
        seen = []
        client = FrontierClient(prov.base_url, "test-key-not-real",
                                "localrelay", "test-model", timeout_s=10.0)
        res = client.complete_stream(
            ProviderRequest(prompt="hi", max_tokens=64, route="frontier"),
            "hi", on_delta=seen.append)

    assert res.ok, res.message
    assert seen == CHUNKS, seen
    assert res.text == FULL
    assert res.details["streaming"] is True
    assert res.details["chunks"] == len(CHUNKS)
    # The request really asked the provider to stream.
    assert prov.requests and prov.requests[0]["stream"] is True


def test_deltas_arrive_progressively_not_in_one_batch(tmp_path, monkeypatch):
    """Timestamps prove the reads are incremental, not one buffered reply."""
    from voicebridge.providers.frontier import FrontierClient
    from voicebridge.providers.contracts import ProviderRequest

    with _Provider(delay_s=0.05) as prov:
        stamps = []
        client = FrontierClient(prov.base_url, "test-key-not-real",
                                "localrelay", "test-model", timeout_s=10.0)
        res = client.complete_stream(
            ProviderRequest(prompt="hi", max_tokens=64, route="frontier"),
            "hi", on_delta=lambda s: stamps.append(time.monotonic()))

    assert res.ok, res.message
    assert len(stamps) == len(CHUNKS)
    spread = stamps[-1] - stamps[0]
    # 4 gaps x 50 ms of server-side delay between frames. A buffered read
    # would deliver everything within a millisecond of the end.
    assert spread > 0.15, f"deltas were batched (spread={spread:.3f}s)"
    # And each delta landed before the last one was even produced.
    assert prov.sse_times[-1] >= stamps[0]


# ---------------------------------------------------------------------------
# 2. End-to-end: _stream_remote yields start, N deltas, one done
# ---------------------------------------------------------------------------
def test_stream_remote_yields_three_deltas_then_one_done(tmp_path,
                                                         monkeypatch):
    from voicebridge import chat_transport as CT

    with _Provider() as prov:
        cfg = _cfg(tmp_path, monkeypatch, prov.base_url)
        events = list(CT._stream_remote(
            cfg, "say hello", session="stream-deltas", routed=_routed(),
            timeout_s=10.0, cancel=None))

    kinds = [e["event"] for e in events]
    assert kinds[0] == "start", kinds
    deltas = [e["text"] for e in events if e["event"] == "delta"]
    assert len(deltas) == len(CHUNKS) >= 3, deltas
    assert deltas == CHUNKS
    assert kinds.count("delta") == len(CHUNKS)
    assert kinds.count("done") == 1, kinds
    assert kinds[-1] == "done", kinds

    done = events[-1]
    assert done["ok"] is True
    assert done["reply"] == FULL
    assert "".join(deltas) == done["reply"]
    # Honest verdict: this provider really streamed.
    assert done["receipt"]["streaming"] is True
    # The blocking-path receipt fields are all still present.
    for key in ("runtime", "provider", "model", "identity", "profile",
                "path", "usage"):
        assert key in done["receipt"], (key, done["receipt"])
    assert done["receipt"]["provider"] == "localrelay"
    assert done["receipt"]["model"] == "test-model"


def test_streamed_turn_persists_the_same_transcript_row(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    with _Provider() as prov:
        cfg = _cfg(tmp_path, monkeypatch, prov.base_url)
        events = list(CT._stream_remote(
            cfg, "say hello", session="stream-persist", routed=_routed(),
            timeout_s=10.0, cancel=None))
    done = events[-1]
    rows = CT.store(cfg).messages("stream-persist")["messages"]
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[1]["content"] == done["reply"] == FULL
    # Same done-event contract as before: no ``message`` key on the done frame.
    assert "message" not in done


# ---------------------------------------------------------------------------
# 3. Non-streaming provider: one delta, one done, streaming reported false
# ---------------------------------------------------------------------------
def test_provider_that_ignores_stream_yields_one_delta_and_says_so(
        tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    with _Provider(mode="whole_body") as prov:
        cfg = _cfg(tmp_path, monkeypatch, prov.base_url)
        events = list(CT._stream_remote(
            cfg, "say hello", session="whole-body", routed=_routed(),
            timeout_s=10.0, cancel=None))
        # The client asked for a stream; this provider ignored it.
        assert prov.requests[0]["stream"] is True

    deltas = [e for e in events if e["event"] == "delta"]
    dones = [e for e in events if e["event"] == "done"]
    assert len(deltas) == 1, deltas
    assert len(dones) == 1, dones
    assert deltas[0]["text"] == FULL
    assert dones[0]["reply"] == FULL
    # The whole reply is never chopped into fake token deltas, and the flag
    # tells the truth.
    assert dones[0]["receipt"]["streaming"] is False


def test_non_streaming_path_is_unchanged(tmp_path, monkeypatch):
    """``_generate_remote`` keeps returning one blocking outcome dict."""
    from voicebridge import chat_transport as CT

    with _Provider(mode="whole_body") as prov:
        cfg = _cfg(tmp_path, monkeypatch, prov.base_url)
        res = CT._generate_remote(
            cfg, "whole-body-session", "say hello", "", _routed(),
            timeout_s=10.0, cancel=None)
        assert prov.requests[0]["stream"] is False

    assert res["ok"] is True
    assert res["reply"] == FULL
    assert res["session"] == res["session_id"] == "whole-body-session"
    assert "receipt" in res and "elapsed_ms" in res


# ---------------------------------------------------------------------------
# 4. Failure and cancellation are typed, never fabricated
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("provider,base_url,expected", [
    ("opencode-go", "https://opencode.ai/zen/go/v1", "session-abc"),
    ("localrelay", "http://127.0.0.1:1/v1", None),
])
def test_relay_affinity_headers_are_provider_scoped(provider, base_url,
                                                   expected):
    """A relay that requires an affinity header must get it; others must not.

    Found live: an OpenAI-compatible relay (OpenCode) rejected the streaming
    request with ``MissingSessionID`` because the blocking transport wrapped
    headers in ``_opencode_session_transport`` while the streaming client built
    its own. Both now go through ``_opencode_session_headers``.
    """
    from voicebridge.providers.gateway import _opencode_session_headers

    extra = _opencode_session_headers(provider, base_url, "session-abc",
                                      "key-1", "profile-1")
    if expected is None:
        assert extra == {}, extra
    else:
        assert extra == {"x-opencode-session": expected}, extra


def test_stream_client_actually_sends_the_extra_headers(tmp_path):
    """The header must reach the wire, not just be accepted as an argument."""
    from voicebridge.providers.frontier import FrontierClient
    from voicebridge.providers.contracts import ProviderRequest

    captured = {}

    class _Recorder(_Provider):
        def _handler(self):
            base = super()._handler()
            rec = self

            class H(base):
                def do_POST(self):
                    captured.update({k.lower(): v
                                     for k, v in self.headers.items()})
                    super().do_POST()

            return H

    with _Recorder() as prov:
        client = FrontierClient(prov.base_url, "k", "localrelay", "test-model",
                                timeout_s=10.0)
        seen = []
        res = client.complete_stream(
            ProviderRequest(prompt="hi", max_tokens=64, route="frontier"),
            "hi", on_delta=seen.append,
            extra_headers={"x-opencode-session": "session-xyz"})

    assert res.ok, res.message
    assert seen == CHUNKS
    assert captured.get("x-opencode-session") == "session-xyz", captured


def test_provider_error_is_a_typed_error_event(tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    with _Provider(status=401) as prov:
        cfg = _cfg(tmp_path, monkeypatch, prov.base_url)
        events = list(CT._stream_remote(
            cfg, "say hello", session="stream-401", routed=_routed(),
            timeout_s=10.0, cancel=None))

    kinds = [e["event"] for e in events]
    assert "delta" not in kinds, kinds
    assert kinds[-1] == "error", kinds
    assert events[-1]["code"] == "AUTH_INVALID", events[-1]


def test_cancel_mid_stream_reports_cancelled_without_the_rest(
        tmp_path, monkeypatch):
    from voicebridge import chat_transport as CT

    state = {"calls": 0}

    def _cancel():
        state["calls"] += 1
        return state["calls"] > 2

    with _Provider(delay_s=0.02) as prov:
        cfg = _cfg(tmp_path, monkeypatch, prov.base_url)
        events = list(CT._stream_remote(
            cfg, "say hello", session="stream-cancel", routed=_routed(),
            timeout_s=10.0, cancel=_cancel))

    kinds = [e["event"] for e in events]
    assert kinds[-1] == "cancelled", kinds
    # Whatever reached the client was real provider text, never the full
    # reply manufactured after a stop.
    shown = "".join(e["text"] for e in events if e["event"] == "delta")
    assert FULL.startswith(shown)
    assert shown != FULL
