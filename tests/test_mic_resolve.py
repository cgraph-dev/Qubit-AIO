"""Packet-5C release fix regression tests: HyperX capture resolution.

The live failure (-9993/-9985 on Pulse-managed USB mics) happened because
resolve_input_device() returned a raw ALSA ``(hw:N,M)`` index that PortAudio
cannot open. It must now prefer the stable ``pulse`` aggregate and fall back
to the default source (None) instead of a raw hw index.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.audio.mic import resolve_input_device  # noqa: E402


def _mk(name, in_ch=1):
    return {"name": name, "max_input_channels": in_ch,
            "max_output_channels": 0}


def test_resolve_prefers_pulse_aggregate_over_raw_hw(monkeypatch):
    devs = [_mk("HD-Audio ALC892 (hw:2,0)", 2),
            _mk("HyperX Cloud III: USB Audio (hw:1,0)", 1),
            _mk("pulse", 32),
            _mk("default", 64)]
    monkeypatch.setattr("sounddevice.query_devices", lambda: devs)
    idx = resolve_input_device("HyperX")
    assert idx is not None
    assert devs[idx]["name"] == "pulse", "must route via pulse aggregate"


def test_resolve_returns_none_when_only_raw_hw_matches(monkeypatch):
    devs = [_mk("HyperX Cloud III: USB Audio (hw:1,0)", 1)]
    monkeypatch.setattr("sounddevice.query_devices", lambda: devs)
    assert resolve_input_device("HyperX") is None, \
        "raw hw index must never be returned for a Pulse-managed USB mic"


def test_resolve_default_when_no_aggregate(monkeypatch):
    devs = [_mk("HD-Audio ALC892 Analog (hw:2,0)", 2),
            _mk("pipewire", 64)]
    monkeypatch.setattr("sounddevice.query_devices", lambda: devs)
    assert resolve_input_device("HD-Audio") is not None


def test_resolve_none_for_unknown_or_empty(monkeypatch):
    devs = [_mk("pulse", 32)]
    monkeypatch.setattr("sounddevice.query_devices", lambda: devs)
    assert resolve_input_device("") is None
    assert resolve_input_device("nonsense") is None


class _FakeSD:
    def __init__(self, devs):
        self._devs = devs

    def query_devices(self):
        return self._devs

def test_pin_matches_input_source_not_output_monitor(monkeypatch):
    """The HyperX OUTPUT monitor sorts before its INPUT source; the pin must
    choose alsa_input.* only, or capture silently gets zeros."""
    import voicebridge.audio.mic as M
    lines = (
        "53\talsa_output.usb-HP__Inc_HyperX_Cloud_III_000000000000-00.iec958-stereo.monitor\tPipeWire\ts24le 2ch 48000Hz\tRUNNING\n"
        "54\talsa_input.usb-HP__Inc_HyperX_Cloud_III_000000000000-00.mono-fallback\tPipeWire\ts24le 1ch 48000Hz\tSUSPENDED\n"
    )
    procs = {}

    def fake_run(cmd, capture_output=True, text=True, timeout=None):
        class R:
            returncode = 0
            stdout = lines if cmd[1] == "list" else                 ("Default Source: alsa_output.usb-HP__Inc_HyperX_Cloud_III_000000000000-00.iec958-stereo.monitor"
                 if cmd[0] == "pactl" and cmd[1] == "info" else "")
            stderr = ""
        procs[cmd[1] if len(cmd) > 1 else "?"] = cmd
        return R()

    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)
    got = M.pin_pulse_default_source("HyperX")
    assert got == "alsa_input.usb-HP__Inc_HyperX_Cloud_III_000000000000-00.mono-fallback", got
    assert procs.get("set-default-source") and         "alsa_input.usb-HP__Inc_HyperX_Cloud_III" in " ".join(
            procs["set-default-source"])


def test_pin_matches_scarlett_portaudio_name_to_pactl_name(monkeypatch):
    """Packet 7D hardware change: the stored PortAudio display name
    'Scarlett 2i2 4th Gen: USB Audio' must pin the pactl source
    '..._Scarlett_2i2_4th_Gen_...analog-surround-40' (space/underscore/
    punctuation-insensitive token match, ignoring generic words)."""
    import voicebridge.audio.mic as M
    lines = (
        "1\talsa_output.usb-Focusrite_Scarlett_2i2_4th_Gen_S2F35P9350962D-00.iec958-stereo.monitor\tPipeWire\ts32le 2ch 48000Hz\tSUSPENDED\n"
        "2\talsa_input.usb-Focusrite_Scarlett_2i2_4th_Gen_S2F35P9350962D-00.analog-surround-40\tPipeWire\ts32le 4ch 48000Hz\tRUNNING\n"
    )
    procs = {}

    def fake_run(cmd, capture_output=True, text=True, timeout=None):
        class R:
            returncode = 0
            stdout = lines if cmd[1] == "list" else (
                "Default Source: alsa_input.usb-HP__Inc_HyperX_Cloud_III_000000000000-00.mono-fallback"
                if cmd[0] == "pactl" and cmd[1] == "info" else "")
            stderr = ""
        procs[cmd[1] if len(cmd) > 1 else "?"] = cmd
        return R()

    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)
    got = M.pin_pulse_default_source("Scarlett 2i2 4th Gen: USB Audio")
    assert got == "alsa_input.usb-Focusrite_Scarlett_2i2_4th_Gen_S2F35P9350962D-00.analog-surround-40", got
    # the set-default-source was issued against the Scarlett source
    assert procs.get("set-default-source") and \
        "Scarlett_2i2_4th_Gen" in " ".join(procs["set-default-source"])
