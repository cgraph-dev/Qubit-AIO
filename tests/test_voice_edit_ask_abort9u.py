"""A STOP interrupts the in-flight ``/ask`` POST and its TTS synth (9u).

Previous lane (already merged) wired a per-turn cancel event so the SERVICE
aborts the model/agent work, but the helper's blocking ``/ask`` HTTP POST
still waited for the socket to come back (up to the 90 s timeout), and an
``/api/audio/speak`` synth already in flight for the reply was not keyed to
the turn at all.

This pins the remaining gap:

- ``HelperClient.ask_result`` / ``speak`` own their socket (``http.client``,
  not ``urllib.urlopen``) so ``HelperClient.abort`` can ``shutdown`` the live
  socket from another thread and unblock the worker AT ONCE; the aborted call
  raises the typed ``VOICE_EDIT_ASK_CANCELLED``, never a generic failure.
- ``DesktopHelper.stop_speaking`` aborts that socket (non-blocking; safe from
  the SIGUSR2 handler) in addition to the service cancel, and an idle stop
  with nothing in flight stays a no-op.
- ``speak`` carries the turn id learned from ``ask_result``; the service gate
  (``voice_edit.service.ask_turn_stopped``) makes ``h_audio_speak`` refuse to
  synthesize a stopped reply before synthesis (earliest cancellation point)
  and to ship one stopped mid-synthesis.

Hermetic: fake connections/sockets/clock; no network, model, mic, audio or X.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voicebridge.desktop_helper as dh  # noqa: E402
from voicebridge import qubit_compat as QC  # noqa: E402
from voicebridge.desktop_helper import HelperClient  # noqa: E402
from voicebridge.voice_edit import routes as R  # noqa: E402
from voicebridge.voice_edit import service as svc_mod  # noqa: E402
from voicebridge.voice_edit.service import VoiceEditService  # noqa: E402


# ---------------------------------------------------------------------------
# shared fakes
# ---------------------------------------------------------------------------
class FakeCfg:
    def __init__(self, data=None):
        self._data = data or {}
        self.path = None

    def get(self, *path, default=None):
        node = self._data
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return default
        return node


def _cfg(**ve):
    base = {"enabled": True, "routing": "local",
            "confirm_before_replace": False, "speak_answers": True}
    base.update(ve)
    return FakeCfg({"voice_edit": base})


class _Sock:
    def __init__(self, release):
        self._release = release

    def shutdown(self, how):
        self._release.set()


class _BadSock:
    def shutdown(self, how):
        raise OSError("no socket")


class _Resp:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    def read(self):
        return self._body


class _Conn:
    """A fake ``http.client`` connection.

    ``release`` present -> ``getresponse`` blocks until the socket is shut
    down, then raises (the abort case). Absent -> returns ``body`` at once.
    """

    def __init__(self, status=200, body=b"{}", *, entered=None, release=None,
                 captured=None):
        self._status = status
        self._body = body
        self._entered = entered
        self._release = release
        self._captured = captured
        self.sock = _Sock(release) if release is not None else None
        self.closed = False

    def request(self, method, path, body=None, headers=None):
        if self._captured is not None:
            self._captured.append((method, path, body, headers))

    def getresponse(self):
        if self._entered is not None:
            self._entered.set()
        if self._release is not None:
            self._release.wait(timeout=10)
            raise ConnectionResetError("connection aborted")
        return _Resp(self._status, self._body)

    def close(self):
        self.closed = True


def _client(conn):
    return HelperClient("http://127.0.0.1:8890", "t",
                        conn_factory=lambda *a, **k: conn)


# ---------------------------------------------------------------------------
# HelperClient: an abort unblocks the in-flight POST promptly
# ---------------------------------------------------------------------------
def test_abort_unblocks_blocked_ask_and_raises_typed_cancelled():
    entered, release = threading.Event(), threading.Event()
    conn = _Conn(entered=entered, release=release)
    client = _client(conn)
    result = {}

    def run():
        try:
            client.ask_result("tell me a joke", "rid-blocked")
        except dh.HelperError as exc:
            result["code"] = exc.code

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert entered.wait(timeout=2.0), "ask never reached the socket"
    t0 = time.monotonic()
    assert client.abort() is True
    t.join(timeout=1.0)
    elapsed = time.monotonic() - t0
    assert not t.is_alive(), "abort did not unblock the blocked POST"
    # Promptly = far under the 90 s timeout the call would otherwise wait out.
    assert elapsed < 0.5, f"POST took {elapsed:.3f}s to unblock"
    assert result.get("code") == "VOICE_EDIT_ASK_CANCELLED"
    assert conn.closed is True


def test_abort_before_request_is_a_noop():
    entered, release = threading.Event(), threading.Event()
    client = _client(_Conn(entered=entered, release=release))
    assert client.abort() is False, "nothing in flight -> no-op"


def test_abort_error_is_swallowed():
    class BadConn:
        sock = _BadSock()

        def close(self):
            raise OSError("already gone")

    client = _client(_Conn())
    client._live_conn = BadConn()
    assert client.abort() is True  # reports the attempt, never raises
    client._live_conn = None


def test_normal_ask_response_shape_unchanged():
    conn = _Conn(200, b'{"ok": true, "answer": "the answer", "spoken": false}')
    client = _client(conn)
    out = client.ask_result("q", "rid-ok")
    assert out == {"ok": True, "answer": "the answer", "spoken": False}
    assert client.abort() is False, "a completed request leaves nothing live"


def test_abortable_http_error_stays_typed():
    conn = _Conn(400, json.dumps(
        {"code": "VOICE_EDIT_ASK_CANCELLED", "detail": "stopped"}).encode())
    client = _client(conn)
    with pytest.raises(dh.HelperError) as ei:
        client.ask_result("q", "rid-400")
    assert ei.value.code == "VOICE_EDIT_ASK_CANCELLED"
    assert ei.value.detail == "stopped"


def test_abortable_transport_unreachable_is_typed():
    class Boom:
        sock = None

        def request(self, *a, **k):
            raise OSError("refused")

        def getresponse(self):  # pragma: no cover - request raises first
            return _Resp(200, b"{}")

        def close(self):
            pass

    client = _client(Boom())
    with pytest.raises(dh.HelperError) as ei:
        client.ask_result("q", "rid-boom")
    assert ei.value.code == "VOICE_EDIT_SERVICE_UNREACHABLE"


def test_custom_opener_seam_is_preserved_for_ask():
    seen = []

    class _Ctx:
        def __init__(self, body):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout=None):
        seen.append(req.full_url)
        return _Ctx(b'{"ok": true, "answer": "via-opener"}')

    client = HelperClient("http://127.0.0.1:8890", "t", opener=opener)
    assert client.ask("q", "rid") == "via-opener"
    assert seen == ["http://127.0.0.1:8890/agent/api/voice-edit/ask"]


# ---------------------------------------------------------------------------
# HelperClient.speak is keyed to the turn learned from ask_result
# ---------------------------------------------------------------------------
def test_speak_carries_the_ask_turn_id_and_is_abortable():
    captured = []

    def factory(*a, **k):
        return _Conn(200, b'{"data_url": ""}', captured=captured)

    client = HelperClient("http://127.0.0.1:8890", "t", conn_factory=factory)
    client.ask_result("q", "rid-42")
    assert client.speak("hello") == b""
    method, path, body, _headers = captured[-1]
    assert (method, path) == ("POST", "/agent/api/audio/speak")
    assert json.loads(body.decode()) == {"text": "hello",
                                         "request_id": "rid-42"}


def test_speak_during_synth_is_abortable():
    entered, release = threading.Event(), threading.Event()
    conn = _Conn(entered=entered, release=release)
    client = _client(conn)
    result = {}

    def run():
        try:
            client.speak("a long reply")
        except dh.HelperError as exc:
            result["code"] = exc.code

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert entered.wait(timeout=2.0), "synth request never reached the socket"
    assert client.abort() is True
    t.join(timeout=1.0)
    assert not t.is_alive(), "abort did not unblock the synth request"
    assert result.get("code") == "VOICE_EDIT_ASK_CANCELLED"


# ---------------------------------------------------------------------------
# DesktopHelper.stop_speaking aborts the live client request
# ---------------------------------------------------------------------------
class FakeOverlay:
    def set_status(self, state, detail=""):
        pass

    def show_preview(self, payload):
        pass

    def show_error(self, code, detail):
        pass

    def append_history(self, line):
        pass


class FakeDesktop:
    def session(self):
        return {"session_type": "x11", "x11": True}


class AbortClient:
    def __init__(self):
        self.aborts = 0

    def abort(self):
        self.aborts += 1
        return True


def _helper(client):
    h = dh.DesktopHelper(_cfg(), client=client, overlay=FakeOverlay(),
                         desktop=FakeDesktop(),
                         mic_factory=lambda: None)
    h._warm_event.set()
    h._warm_state = "skipped"
    return h


def test_stop_speaking_aborts_live_request_while_processing():
    client = AbortClient()
    h = _helper(client)
    h.request_id = "rid-abort"
    h._turn_seq = 1
    h.state = h.PROCESSING
    assert h.stop_speaking() is True
    assert client.aborts == 1


def test_stop_speaking_aborts_while_speaking():
    client = AbortClient()
    h = _helper(client)
    h._turn_seq = 2
    h._speaking = True
    assert h.stop_speaking() is True
    assert client.aborts == 1


def test_idle_stop_does_not_abort():
    client = AbortClient()
    h = _helper(client)
    h.state = h.IDLE
    assert h.stop_speaking() is False
    assert client.aborts == 0


def test_on_orb_stop_aborts_from_the_signal_path():
    client = AbortClient()
    h = _helper(client)
    h.request_id = "rid-sig"
    h._turn_seq = 3
    h.state = h.PROCESSING
    h.on_orb_stop()  # must never raise
    assert client.aborts == 1


def test_abort_client_request_without_abort_is_a_noop():
    class Plain:
        pass

    h = _helper(Plain())
    h.state = h.PROCESSING
    h._turn_seq = 1
    # No `abort` on the client -> quiet no-op, no exception.
    h._abort_client_request()


class BlockingAskClient:
    """``ask_result`` blocks until ``abort`` -> the helper must end at once."""

    def __init__(self):
        self.entered = threading.Event()
        self._aborted = threading.Event()
        self.aborts = 0
        self.asks = []

    def status(self):
        return {"enabled": True, "estop": False}

    def begin(self):
        return {"ok": True, "request_id": "req-1"}

    def route(self, rid, text):
        return {"ok": True, "mode": "answer", "question": text}

    def abort(self):
        self.aborts += 1
        self._aborted.set()
        return True

    def ask_result(self, question, request_id):
        self.asks.append(question)
        self.entered.set()
        if not self._aborted.wait(timeout=5.0):
            raise AssertionError("ask was never aborted")
        raise dh.HelperError("VOICE_EDIT_ASK_CANCELLED",
                             "the turn was cancelled")

    def speak(self, text):  # pragma: no cover - never reached when stopped
        return b""


def test_stop_aborts_inflight_ask_and_closes_fsm_promptly():
    client = BlockingAskClient()
    h = _helper(client)
    t = threading.Thread(target=h._process_transcript,
                         args=("what is the time",), daemon=True)
    t.start()
    assert client.entered.wait(timeout=3.0), "turn never reached the ask"
    assert h.state == h.PROCESSING
    assert h.stop_speaking() is True
    t.join(timeout=2.0)
    assert not t.is_alive(), "the ask POST must return promptly after stop"
    assert client.aborts >= 1
    types = [e.get("type") for e in h.talk_events()]
    assert "interrupt" in types
    assert "playback.stopped" in types
    assert "response.done" in types
    assert "error" not in types, "a stop is not an error"


class NormalAskClient(BlockingAskClient):
    """``ask_result`` returns normally: the unstopped path is unchanged."""

    def __init__(self, answer="A calm normal answer."):
        super().__init__()
        self.answer = answer

    def ask_result(self, question, request_id):
        self.asks.append(question)
        self.entered.set()
        return {"answer": self.answer, "spoken": False}


def test_normal_turn_still_asks_and_is_not_aborted(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "")
    client = NormalAskClient()
    h = _helper(client)
    t = threading.Thread(target=h._process_transcript,
                         args=("hello there",), daemon=True)
    t.start()
    assert client.entered.wait(timeout=3.0)
    t.join(timeout=3.0)
    assert not t.is_alive()
    assert client.aborts == 0, "an unstopped turn must never abort"
    assert client.asks == ["hello there"]


class SpeakErrorClient:
    """``speak`` raises a typed error; the reply is never played."""

    def __init__(self, code):
        self.code = code

    def speak(self, text):
        raise dh.HelperError(self.code, "synth refused")


def test_speak_abort_code_emits_stop_triple_and_does_not_play(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/paplay")
    procs = []
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda *a, **k: procs.append(object()))

    h = _helper(SpeakErrorClient("VOICE_EDIT_ASK_CANCELLED"))
    h._turn_seq = 1
    h._speak("a long reply that must never be heard")
    assert procs == [], "a stopped/aborted synth must not start playback"
    types = [e.get("type") for e in h.talk_events()]
    assert "interrupt" in types
    assert "playback.stopped" in types
    assert "response.done" in types


def test_speak_generic_error_still_does_not_fake_a_stop(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/paplay")
    h = _helper(SpeakErrorClient("VOICE_EDIT_SERVICE_UNREACHABLE"))
    h._turn_seq = 1
    h._speak("a reply whose TTS just failed")
    types = [e.get("type") for e in h.talk_events()]
    assert "interrupt" not in types, "a TTS error is not a stop"
    assert "response.done" not in types



# ---------------------------------------------------------------------------
# SERVER: the turn-keyed cancel gates a stopped reply's synthesis
# ---------------------------------------------------------------------------
def _svc(monkeypatch):
    monkeypatch.setattr(svc_mod, "_signal_helper_stop", lambda: False)
    svc = VoiceEditService(_cfg(), desktop=FakeDesktop(),
                           audit=lambda *a: None, estop=lambda cfg: False)
    return svc


def test_ask_turn_is_stopped_reflects_a_cancel(monkeypatch):
    svc = _svc(monkeypatch)
    assert svc.ask_turn_is_stopped("rid") is False
    svc.cancel("rid")
    assert svc.ask_turn_is_stopped("rid") is True
    assert svc.ask_turn_is_stopped("") is False, "empty id is never stopped"
    assert svc.ask_turn_is_stopped("other") is False


def test_module_ask_turn_stopped_reads_the_service(monkeypatch):
    svc = _svc(monkeypatch)
    monkeypatch.setattr(R, "service_for", lambda cfg: svc)
    assert svc_mod.ask_turn_stopped(_cfg(), "rid-x") is False
    svc.cancel("rid-x")
    assert svc_mod.ask_turn_stopped(_cfg(), "rid-x") is True
    assert svc_mod.ask_turn_stopped(_cfg(), "") is False


def _fake_tts(monkeypatch, calls, *, on_synth=None):
    class FakeTTS:
        name = "fake-tts"

        def load(self):
            pass

        def synthesize(self, text, out):
            calls.append(text)
            if on_synth is not None:
                on_synth(text)
            Path(out).write_bytes(b"RIFF" + b"\x00" * 32)

    monkeypatch.setattr(QC, "_tts_from_cfg", lambda cfg: FakeTTS())


def test_h_audio_speak_skips_synth_for_a_stopped_turn(monkeypatch):
    calls = []
    _fake_tts(monkeypatch, calls)
    svc = _svc(monkeypatch)
    monkeypatch.setattr(R, "service_for", lambda cfg: svc)
    svc.cancel("rid-stop")
    status, out = QC.h_audio_speak(
        _cfg(), {}, {"text": "speak this", "request_id": "rid-stop"}, {})
    assert status == 400
    assert out["code"] == "AUDIO_SPEAK_CANCELLED"
    assert calls == [], "a stopped reply must not be synthesized"


def test_h_audio_speak_normal_turn_synthesizes(monkeypatch):
    calls = []
    _fake_tts(monkeypatch, calls)
    svc = _svc(monkeypatch)
    monkeypatch.setattr(R, "service_for", lambda cfg: svc)
    status, out = QC.h_audio_speak(
        _cfg(), {}, {"text": "speak this", "request_id": "rid-ok"}, {})
    assert status == 200 and out["ok"] is True
    assert out["data_url"].startswith("data:audio/")
    assert calls == ["speak this"]


def test_h_audio_speak_stop_during_synth_is_not_shipped(monkeypatch):
    calls = []
    rid = "rid-mid"
    svc = _svc(monkeypatch)
    monkeypatch.setattr(R, "service_for", lambda cfg: svc)

    def stop_mid(text):
        svc.cancel(rid)  # the orb STOP lands while synthesis runs

    _fake_tts(monkeypatch, calls, on_synth=stop_mid)
    status, out = QC.h_audio_speak(
        _cfg(), {}, {"text": "speak this", "request_id": rid}, {})
    assert calls == ["speak this"], "synthesis had already begun"
    assert status == 400
    assert out["code"] == "AUDIO_SPEAK_CANCELLED"
    assert "data_url" not in out, "the stopped wav is never shipped"


def test_h_audio_speak_without_request_id_is_unchanged(monkeypatch):
    calls = []
    _fake_tts(monkeypatch, calls)
    svc = _svc(monkeypatch)
    monkeypatch.setattr(R, "service_for", lambda cfg: svc)
    # Even with a stop recorded for another turn, an unkeyed dashboard call
    # synthesizes exactly as before.
    svc.cancel("some-other-turn")
    status, out = QC.h_audio_speak(_cfg(), {}, {"text": "dashboard"}, {})
    assert status == 200 and out["ok"] is True
    assert calls == ["dashboard"]
