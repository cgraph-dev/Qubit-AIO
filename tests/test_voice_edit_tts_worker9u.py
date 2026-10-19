"""A STOP genuinely interrupts a piper/sherpa synth (killable worker, 9u).

``piper``/``sherpa-onnx`` synthesis is a blocking C call, so a Python thread
cannot abort it. ``POST /api/audio/speak`` (``h_audio_speak``) now runs a
KEYED orb reply's synth in a child process owned by the service. A stop that
lands during the call terminates that child (SIGTERM -> SIGKILL) and the
request returns the typed ``AUDIO_SPEAK_CANCELLED`` instead of waiting for a
wav that would be discarded anyway.

These tests are hermetic: the worker's spawn command is a small script that
speaks the same line protocol, so no model, audio, mic or network is used.
The in-process path (dashboard calls / test doubles) must stay byte-identical.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge import qubit_compat as QC  # noqa: E402
from voicebridge.tts import synth_worker as SW  # noqa: E402
from voicebridge.voice_edit import routes as R  # noqa: E402
from voicebridge.voice_edit.service import VoiceEditService  # noqa: E402


class _DataCfg:
    """Config double the worker path accepts (has a ``data`` dict)."""

    def __init__(self, data=None, model_dir="/models"):
        self.data = data if data is not None else {"tts": {"provider": "piper"}}
        self._model_dir = model_dir
        self.path = None

    def get(self, *path, default=None):
        node = self.data
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return default
        return node

    def model_dir(self):
        return self._model_dir


class _SvcCfg(_DataCfg):
    def get(self, *path, default=None):
        if path[:1] == ("voice_edit",):
            base = {"enabled": True, "routing": "local",
                    "confirm_before_replace": False, "speak_answers": True}
            if len(path) > 1:
                return base.get(path[-1], default)
            return base
        return super().get(*path, default=default)


@pytest.fixture(autouse=True)
def _clean_worker():
    SW.shutdown()
    yield
    SW.shutdown()


FAKE_CHILD = r"""
import json, os, sys, time
first = sys.stdin.readline()
try:
    cfg = json.loads(first)
    assert isinstance(cfg.get("config"), dict)
except Exception as exc:
    print(json.dumps({"ready": False, "error": str(exc)}), flush=True)
    raise SystemExit(1)
pidlog = os.environ.get("FAKE_PIDLOG")
if pidlog:
    with open(pidlog, "a") as fh:
        fh.write(str(os.getpid()) + "\n")
if os.environ.get("FAKE_SLOW_READY"):
    time.sleep(30)
print(json.dumps({"ready": True, "provider": "fake"}), flush=True)
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    mode = os.environ.get("FAKE_MODE", "ok")
    if mode == "sleep":
        time.sleep(30)
        continue
    if mode == "error":
        print(json.dumps({"id": req["id"], "ok": False,
                          "error": "boom"}), flush=True)
        continue
    with open(req["out"], "wb") as fh:
        fh.write(b"RIFFfake")
    print(json.dumps({"id": req["id"], "ok": True}), flush=True)
"""


@pytest.fixture()
def fake_child(tmp_path, monkeypatch):
    script = tmp_path / "fake_tts_child.py"
    script.write_text(FAKE_CHILD)
    monkeypatch.setattr(
        SW, "_spawn_cmd", lambda: [sys.executable, str(script)])
    return script


# ---------------------------------------------------------------------------
# feature gate + in-process compatibility
# ---------------------------------------------------------------------------
def test_worker_supported_only_for_data_configs(monkeypatch):
    assert SW.worker_supported(_DataCfg()) is True
    assert SW.worker_supported(object()) is False
    monkeypatch.setenv("QUBIT_TTS_SYNTH_WORKER", "0")
    assert SW.worker_supported(_DataCfg()) is False


def _svc(monkeypatch):
    svc = VoiceEditService(_SvcCfg())
    monkeypatch.setattr(R, "service_for", lambda cfg: svc)
    return svc


def test_keyed_speak_uses_the_killable_worker(monkeypatch, tmp_path):
    calls = []

    def _fake_synth(cfg, text, out, *, stopped=None, timeout=60.0):
        calls.append((text, Path(out)))
        Path(out).write_bytes(b"RIFFworker")
        return "worker-provider"

    monkeypatch.setattr(SW, "synthesize_cancellable", _fake_synth)
    monkeypatch.setattr(QC, "_tts_from_cfg",
                        lambda cfg: pytest.fail("in-process factory used"))

    svc = _svc(monkeypatch)
    status, out = QC.h_audio_speak(
        _SvcCfg(), {}, {"text": "speak this", "request_id": "rid-w"}, {})
    assert status == 200 and out["ok"] is True, out
    assert calls and calls[0][0] == "speak this"
    assert out["provider"] == "worker-provider"


def test_worker_cancel_maps_to_typed_stopped(monkeypatch):
    def _cancelled(cfg, text, out, *, stopped=None, timeout=60.0):
        raise SW.SynthCancelled("stopped")

    monkeypatch.setattr(SW, "synthesize_cancellable", _cancelled)
    status, out = QC.h_audio_speak(
        _SvcCfg(), {}, {"text": "hi", "request_id": "rid-c"}, {})
    assert status == 400 and out["code"] == "AUDIO_SPEAK_CANCELLED", out
    assert "data_url" not in out


def test_worker_unavailable_is_typed_tts_unavailable(monkeypatch):
    def _broken(cfg, text, out, *, stopped=None, timeout=60.0):
        raise SW.SynthWorkerUnavailable("no voice pack")

    monkeypatch.setattr(SW, "synthesize_cancellable", _broken)
    status, out = QC.h_audio_speak(
        _SvcCfg(), {}, {"text": "hi", "request_id": "rid-u"}, {})
    assert status == 403 and out["code"] == "AUDIO_TTS_UNAVAILABLE", out


def test_dashboard_call_without_rid_keeps_in_process_path(monkeypatch):
    used = []

    class _T:
        name = "inproc"

        def load(self):
            pass

        def synthesize(self, text, out):
            used.append(text)
            Path(out).write_bytes(b"RIFFinproc")

    monkeypatch.setattr(QC, "_tts_from_cfg", lambda cfg: _T())
    monkeypatch.setattr(SW, "synthesize_cancellable",
                        lambda *a, **k: pytest.fail("worker used"))
    status, out = QC.h_audio_speak(_DataCfg(), {}, {"text": "dashboard"}, {})
    assert status == 200 and out["provider"] == "inproc"
    assert used == ["dashboard"]


# ---------------------------------------------------------------------------
# real child: protocol, cancellation, reuse
# ---------------------------------------------------------------------------
def test_worker_synthesizes_and_reuses_one_child(tmp_path, fake_child,
                                                 monkeypatch):
    pidlog = tmp_path / "pids.txt"
    monkeypatch.setenv("FAKE_PIDLOG", str(pidlog))
    cfg = _DataCfg()
    out = tmp_path / "a.wav"
    provider = SW.synthesize_cancellable(cfg, "hello", out,
                                         stopped=lambda: False, timeout=10)
    assert provider == "fake"
    assert out.read_bytes() == b"RIFFfake"
    out2 = tmp_path / "b.wav"
    SW.synthesize_cancellable(cfg, "again", out2,
                              stopped=lambda: False, timeout=10)
    assert out2.read_bytes() == b"RIFFfake"
    pids = pidlog.read_text().split()
    assert len(pids) == 1, f"expected one warm child, got {pids}"


def test_worker_is_killed_mid_synth_on_stop(tmp_path, fake_child,
                                            monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "sleep")
    cfg = _DataCfg()
    stop = threading.Event()
    timer = threading.Timer(0.35, stop.set)
    timer.start()
    t0 = time.monotonic()
    try:
        with pytest.raises(SW.SynthCancelled):
            SW.synthesize_cancellable(cfg, "long", tmp_path / "x.wav",
                                      stopped=stop.is_set, timeout=20)
    finally:
        timer.cancel()
    elapsed = time.monotonic() - t0
    assert elapsed < 5.0, f"stop did not interrupt the synth ({elapsed:.1f}s)"
    assert SW._WORKER is None
    assert not (tmp_path / "x.wav").exists()


def test_worker_error_is_typed_unavailable(tmp_path, fake_child, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "error")
    with pytest.raises(SW.SynthWorkerUnavailable) as exc:
        SW.synthesize_cancellable(_DataCfg(), "x", tmp_path / "e.wav",
                                  stopped=lambda: False, timeout=10)
    assert "boom" in str(exc.value)


def test_worker_timeout_kills_the_child(tmp_path, fake_child, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "sleep")
    with pytest.raises(SW.SynthWorkerUnavailable):
        SW.synthesize_cancellable(_DataCfg(), "x", tmp_path / "t.wav",
                                  stopped=lambda: False, timeout=0.2)
    assert SW._WORKER is None


def test_bad_config_output_is_typed(tmp_path, fake_child):
    class _Bad(_DataCfg):
        @property
        def data(self):
            return 7  # not a dict -> child refuses

        @data.setter
        def data(self, value):
            pass

    with pytest.raises(SW.SynthWorkerUnavailable):
        SW.synthesize_cancellable(_Bad(), "x", tmp_path / "b.wav",
                                  stopped=lambda: False, timeout=5)


def test_stop_during_model_load_kills_the_child(tmp_path, fake_child,
                                                monkeypatch):
    monkeypatch.setenv("FAKE_SLOW_READY", "1")
    stop = threading.Event()
    timer = threading.Timer(0.3, stop.set)
    timer.start()
    try:
        with pytest.raises(SW.SynthCancelled):
            SW.synthesize_cancellable(_DataCfg(), "x", tmp_path / "l.wav",
                                      stopped=stop.is_set, timeout=20)
    finally:
        timer.cancel()
    assert SW._WORKER is None, "a cancelled load must not leave a child"
