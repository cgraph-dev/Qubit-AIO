# Audio Setup — the WORKING configuration (keep; do not "help" break it)

> This is the verified-working audio config for the voice bridge on this PC.
> Other agents (Hermes, OpenClaw, any coder) MUST read this BEFORE touching any
> audio/mic/TTS setting. It took many hours to stabilise — do not reinvent it.

## Hardware (currently connected)
- **Mic**: HyperX Cloud III headset — **USB, MONO** (its own USB audio device).
  The Focusrite Scarlett 2i2 / AT2020 are **unplugged**.
- **Output**: HyperX Cloud III **USB digital (iec958)** — same sink the browser
  (YouTube) uses, so the agent + browser audio stay on the same device.

## The live config — `~/.config/openclaw-voice-bridge/voice.yaml`
```yaml
mic:
  device: null        # system default = HyperX mono mic
  sample_rate: 48000
  channels: 1         # HyperX is MONO (was 4 for the unplugged Scarlett)
  blocksize: 480
  gain: 3.0
  min_rms: 0.0005
  normalize_target: 0.35   # peak-normalize clip to 0.35 before ASR (stable for quiet mic)

audio:
  output_device: ""        # EMPTY = follow the working default sink (do NOT hardcode an index)
  output_device_name: ""   # EMPTY = follow default (HyperX iec958)
  output_volume: 100
```

## Critical rules (do NOT break)
1. **`audio.output_device` / `output_device_name` must stay EMPTY.** Device
   indices shift on every reconnect; pinning an index is what caused "I can't
   hear the agent." The bridge follows the default sink (HyperX) — same as
   YouTube — so it always matches where the browser plays.
2. **`mic.channels: 1`** (mono). Setting 4 breaks capture (that was for the
   Scarlett).
3. **`mic.gain: 3.0`** — too high (8x+) amplifies hiss → ASR returns empty.
4. **The bridge peak-normalizes** the clip (`normalize_target: 0.35`) so a quiet
   mic transcribes reliably — do not remove it.
5. **`webui.speak_web_locally: false`** — the **browser plays the reply once**
   (audio_b64). Keep it false so it doesn't double-speak on the PC. Voice/PTT
   (Right Ctrl) speaks via the normal path, once.
6. **Output `swap` on unplug**: after unplugging the Scarlett, audio "came back"
   when the HyperX digital profile was active. If audio stops, check
   `pactl get-default-sink` = HyperX + run `scripts/diagnose_audio_usb.py`.

## USB port speed (the silent killer)
Audio USB devices on a **USB 1.1 (12M) full-speed port** FREEZE the moment a
stream opens (mic capture, playback, browser). We hit this with the HyperX until
it was moved to a fast (480M USB2/USB3) port. Detect it:
```
python scripts/diagnose_audio_usb.py
```
A `⚠ SLOW` warning = replug the device into a USB 3.0 (blue) / direct rear port.

## Sample-rate / "robot voice" note
TTS is 22050 Hz mono. The bridge **resamples to the sink's native rate** before
playing; if it ever sounds pitch-shifted/robotic, a wrong sample rate is the cause
(fixed by the resample in `voicebridge/audio/playback.py`).

## References
- Repo: `~/Projects/Repos/openclaw-voice-bridge`
- Playback + device resolution: `voicebridge/audio/playback.py`
- Mic capture + normalize: `voicebridge/audio/mic.py`
- Bridge rebuild: `systemctl --user restart openclaw-voice-bridge`
