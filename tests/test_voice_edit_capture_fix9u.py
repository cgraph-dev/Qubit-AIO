"""Capture-path wiring fixes: noise_suppression / mic_target_peak / auto_send.

In-process only: no mic, no PulseAudio, no display needed. Fake cfg objects
and a fake DesktopAdapter transport stand in for hardware.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from voicebridge.voice_edit import desktop as D
from voicebridge.voice_edit import noise
from voicebridge.voice_edit import wake as W
from voicebridge.voice_edit.desktop import DesktopAdapter
from voicebridge.voice_edit.schema import SelectionSnapshot
from voicebridge.voice_edit.stt_client import ParecMic, SttClient


class FakeCfg:
    def __init__(self, data):
        self._d = data

    def get(self, *p, default=None):
        node = self._d
        for seg in p:
            if isinstance(node, dict) and seg in node:
                node = node[seg]
            else:
                return default
        return node


def _noisy_pcm(n: int = 4800) -> bytes:
    """Loud-ish voiced signal riding on a DC offset (what NS must change)."""
    t = np.arange(n, dtype=np.float64)
    sig = 0.25 * np.sin(2 * np.pi * 440 * t / 16000.0) + 0.15  # DC offset
    sig += 0.01 * np.sin(2 * np.pi * 7000 * t / 16000.0)  # hiss-ish HF
    return np.clip(sig * 32767.0, -32768, 32767).astype("<i2").tobytes()


# ---------------------------------------------------------------------------
# 1. noise_suppression ON changes capture behavior observably
# ---------------------------------------------------------------------------

def test_capture_params_branch_on_noise_suppression():
    off = noise.capture_params(FakeCfg({"voice_edit": {}}))
    on = noise.capture_params(
        FakeCfg({"voice_edit": {"noise_suppression": True}}))
    assert off == {"device": "", "ns": False, "stage": "none"}
    assert on == {"device": "", "ns": True,
                  "stage": noise.SOFTWARE_STAGE}
    assert off != on


def test_apply_filter_off_returns_input_unchanged():
    pcm = _noisy_pcm()
    assert noise.apply_capture_filter(pcm, FakeCfg({"voice_edit": {}})) == pcm
    assert noise.apply_capture_filter(pcm, False) == pcm


def test_apply_filter_on_changes_frames():
    pcm = _noisy_pcm()
    out = noise.apply_capture_filter(
        pcm, FakeCfg({"voice_edit": {"noise_suppression": True}}))
    assert out != pcm
    assert len(out) == len(pcm)
    # DC offset must be visibly reduced by the high-pass.
    dc_in = abs(float(np.frombuffer(pcm, dtype="<i2").astype(
        np.float64).mean()))
    dc_out = abs(float(np.frombuffer(out, dtype="<i2").astype(
        np.float64).mean()))
    assert dc_out < dc_in


def test_apply_filter_never_raises():
    assert noise.apply_capture_filter(b"", True) == b""
    assert noise.apply_capture_filter(b"\x01", True) == b"\x01"  # odd length
    assert noise.apply_capture_filter(b"\x00" * 64, True)  # silence ok


def test_resolve_capture_device_unchanged(monkeypatch):
    monkeypatch.setattr(noise, "ensure", lambda cfg: "qubit_aec")
    assert noise.resolve_capture_device(FakeCfg({"voice_edit": {}})) == ""
    assert noise.resolve_capture_device(
        FakeCfg({"voice_edit": {"asr_device": "my_mic"}})) == "my_mic"
    assert noise.resolve_capture_device(
        FakeCfg({"voice_edit": {"capture_source": "aec"}})) == "qubit_aec"


# ---------------------------------------------------------------------------
# 2. mic_target_peak is consumed
# ---------------------------------------------------------------------------

def test_wake_target_peak_default_and_clamp():
    assert W._mic_target_peak(FakeCfg({"voice_edit": {}})) == 0.6
    assert W._mic_target_peak(None) == 0.6
    assert W._mic_target_peak(
        FakeCfg({"voice_edit": {"mic_target_peak": 0.8}})) == 0.8
    assert W._mic_target_peak(
        FakeCfg({"voice_edit": {"mic_target_peak": 5.0}})) == 0.95
    assert W._mic_target_peak(
        FakeCfg({"voice_edit": {"mic_target_peak": -2.0}})) == 0.05
    assert W._mic_target_peak(
        FakeCfg({"voice_edit": {"mic_target_peak": "junk"}})) == 0.6


def test_wake_normalize_honours_configured_peak():
    n = 1600
    quiet = (np.ones(n, dtype=np.float32) * 2000).astype("<i2").tobytes()
    lo = W._normalize_pcm(quiet, target_peak=W._mic_target_peak(
        FakeCfg({"voice_edit": {}})))
    hi = W._normalize_pcm(quiet, target_peak=W._mic_target_peak(
        FakeCfg({"voice_edit": {"mic_target_peak": 0.9}})))
    peak = lambda b: float(np.abs(
        np.frombuffer(b, dtype="<i2").astype(np.float32)).max()) / 32768.0
    assert peak(hi) > peak(lo)


def test_stt_client_target_peak_default_and_clamp():
    c = SttClient(url="ws://x/ws", ticket="t")
    assert c.target_peak == 0.5
    c2 = SttClient(url="ws://x/ws", ticket="t", target_peak=0.8)
    assert c2.target_peak == 0.8
    assert SttClient(url="ws://x/ws", ticket="t",
                     target_peak=9.0).target_peak == 0.95
    assert SttClient(url="ws://x/ws", ticket="t",
                     target_peak=-1.0).target_peak == 0.05


def test_parec_mic_level_follows_target_peak():
    assert ParecMic(target_peak=0.8).target_rms == min(0.8 * 0.25, 0.15)
    assert (ParecMic(target_peak=0.8).target_rms
            > ParecMic(target_peak=0.5).target_rms)


# ---------------------------------------------------------------------------
# 3. auto_send consumer on the desktop insert path
# ---------------------------------------------------------------------------

class _Keys:
    def __init__(self):
        self.key_calls: list = []

    def run(self, argv, **kw):
        if argv and argv[0] == "xdotool" and "key" in argv:
            self.key_calls.append(argv[-1])
        return 0, b"", b""


def _adapter(keys: _Keys, env: dict) -> DesktopAdapter:
    ad = DesktopAdapter(
        run=keys.run,
        popen=lambda *a, **k: None,
        which=lambda name: f"/usr/bin/{name}"
        if name in ("xclip", "xdotool") else None,
        env=dict(env))
    ad.verify_focus = lambda snap: True  # isolate the insert path
    ad.save_clipboard = lambda: D.ClipboardBackup(complete=True)
    ad.set_clipboard = lambda text: True
    ad.restore_clipboard = lambda bak=None: True
    return ad


def _snap() -> SelectionSnapshot:
    return SelectionSnapshot(available=True, window_id="42",
                             app_class="gedit", app_name="gedit",
                             focused=True)


def test_auto_send_on_presses_enter_once():
    keys = _Keys()
    ad = _adapter(keys, {"DISPLAY": ":0"})
    cfg = FakeCfg({"voice_edit": {"auto_send": True}})
    res = ad.insert_text("hello", _snap(), cfg)
    assert res.ok is True
    assert keys.key_calls.count("Return") == 1
    assert ad.last_auto_send == "sent"
    assert "auto_send" in res.reason


def test_auto_send_off_never_presses_enter():
    keys = _Keys()
    ad = _adapter(keys, {"DISPLAY": ":0"})
    cfg = FakeCfg({"voice_edit": {"auto_send": False}})
    res = ad.insert_text("hello", _snap(), cfg)
    assert res.ok is True
    assert "Return" not in keys.key_calls
    assert ad.last_auto_send == "disabled"
    assert res.reason == ""


def test_auto_send_absent_cfg_never_presses_enter():
    keys = _Keys()
    ad = _adapter(keys, {"DISPLAY": ":0"})
    res = ad.insert_text("hello", _snap(), None)
    assert res.ok is True
    assert "Return" not in keys.key_calls
    assert ad.last_auto_send == "disabled"


def test_auto_send_typed_path_also_sends():
    keys = _Keys()
    ad = _adapter(keys, {"DISPLAY": ":0"})
    ad._type_text = lambda text: True
    cfg = FakeCfg({"voice_edit": {"auto_send": True}})
    res = ad.insert_text("hello", _snap(), cfg, method="typed")
    assert res.ok is True and res.method == "typed"
    assert keys.key_calls.count("Return") == 1
    assert ad.last_auto_send == "sent"


def test_auto_send_headless_typed_refusal_never_crashes():
    keys = _Keys()
    ad = _adapter(keys, {})  # no DISPLAY, no WAYLAND_DISPLAY
    cfg = FakeCfg({"voice_edit": {"auto_send": True}})
    outcome = ad.maybe_auto_send(cfg)  # must not raise
    assert outcome.startswith("headless:")
    assert D.AUTO_SEND_UNAVAILABLE in outcome
    assert "Return" not in keys.key_calls
    assert ad.last_auto_send == outcome
    # Flag off headless is still just disabled, never an error.
    assert ad.maybe_auto_send(None) == "disabled"


# ---------------------------------------------------------------------------
# 1b. noise_suppression is consumed on the wake capture path (new wiring)
# ---------------------------------------------------------------------------

def _wake_monitor(cfg) -> "W.WakeMonitor":
    return W.WakeMonitor(cfg, on_command=lambda cmd: None,
                         is_busy=lambda: False,
                         transcribe=lambda pcm: pcm)


def test_wake_ns_filter_off_leaves_pcm_untouched():
    pcm = _noisy_pcm()
    assert W._maybe_ns_filter(pcm, FakeCfg({"voice_edit": {}})) == pcm
    assert W._maybe_ns_filter(pcm, None) == pcm
    assert W._maybe_ns_filter(b"", FakeCfg(
        {"voice_edit": {"noise_suppression": True}})) == b""


def test_wake_ns_filter_on_changes_noisy_utterance():
    pcm = _noisy_pcm()
    out = W._maybe_ns_filter(
        pcm, FakeCfg({"voice_edit": {"noise_suppression": True}}))
    assert out != pcm
    assert len(out) == len(pcm)


def test_wake_do_transcribe_receives_filtered_pcm_when_ns_on():
    pcm = _noisy_pcm()
    seen: list = []
    mon_on = W.WakeMonitor(
        FakeCfg({"voice_edit": {"noise_suppression": True}}),
        on_command=lambda cmd: None, is_busy=lambda: False,
        transcribe=lambda got: seen.append(got) or "")
    mon_on._do_transcribe(pcm)
    assert seen and seen[0] != pcm  # filter ran before transcription
    seen_off: list = []
    mon_off = W.WakeMonitor(
        FakeCfg({"voice_edit": {}}),
        on_command=lambda cmd: None, is_busy=lambda: False,
        transcribe=lambda got: seen_off.append(got) or "")
    mon_off._do_transcribe(pcm)
    assert seen_off and seen_off[0] == pcm  # flag off: byte-identical


def test_wake_new_mic_honours_configured_target_peak():
    mon_cfg = _wake_monitor(FakeCfg({"voice_edit": {"mic_target_peak": 0.9}}))
    mic = mon_cfg._new_mic()
    assert abs(mic.target_peak - 0.9) < 1e-9
    mon_default = _wake_monitor(FakeCfg({"voice_edit": {}}))
    assert mon_default._new_mic().target_peak == 0.5


# ---------------------------------------------------------------------------
# 1c. Right-Ctrl path (voicebridge.desktop_helper): NS frame filter +
#     configured target_peak on the mic and STT factories
# ---------------------------------------------------------------------------

class _FakeMic:
    def __init__(self, frames):
        self._frames = list(frames)
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def read_frame(self, timeout=0.5):
        return self._frames.pop(0) if self._frames else None


def test_ns_filter_mic_passthrough_off_by_construction():
    from voicebridge.desktop_helper import _NsFilterMic
    pcm = _noisy_pcm()
    mic = _FakeMic([pcm, b""])
    filt = _NsFilterMic(mic)
    assert filt.read_frame() != pcm  # wrapper always filters (only installed when ON)
    assert filt.started is False
    filt.start()
    assert mic.started is True
    filt.stop()
    assert mic.stopped is True


def test_ns_filter_mic_never_raises_and_preserves_empty():
    from voicebridge.desktop_helper import _NsFilterMic
    mic = _FakeMic([None, b"", b"\x00"])
    filt = _NsFilterMic(mic)
    assert filt.read_frame() is None
    assert filt.read_frame() == b""
    assert filt.read_frame() == b"\x00"


def test_desktop_helper_factories_honour_configured_target_peak():
    from voicebridge.desktop_helper import _mic_target_peak
    assert _mic_target_peak(
        FakeCfg({"voice_edit": {"mic_target_peak": 0.9}})) == 0.9
    assert _mic_target_peak(FakeCfg({"voice_edit": {}})) == 0.5
    assert _mic_target_peak(FakeCfg(
        {"voice_edit": {"mic_target_peak": 99}})) == 0.95  # clamped
    assert _mic_target_peak(None) == 0.5


def test_default_mic_factory_passes_target_peak():
    import voicebridge.desktop_helper as DH
    seen = {}

    class _Mic:
        def __init__(self, **kw):
            seen.update(kw)

    mon = DH.DesktopHelper.__new__(DH.DesktopHelper)
    mon.cfg = FakeCfg({"voice_edit": {"mic_target_peak": 0.9}})
    orig = DH.ParecMic
    try:
        DH.ParecMic = _Mic
        mon._default_mic_factory()
    finally:
        DH.ParecMic = orig
    assert abs(seen.get("target_peak", 0) - 0.9) < 1e-9
