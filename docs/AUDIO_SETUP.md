# Audio Setup — the WORKING configuration (keep; do not "help" break it)

> This is the verified-working audio config for the voice bridge on this PC.
> Other agents (Hermes, OpenClaw, any coder) MUST read this BEFORE touching any
> audio/mic/TTS setting. It took many hours to stabilise — do not reinvent it.

## Hardware (currently connected)
- **Mic**: **Focusrite Scarlett 2i2 4th Gen** USB interface into **AT2020
  (Audio-Technica) XLR condenser microphone**. The AT2020 needs **48V phantom
  power** enabled on the Scarlett (the front panel 48V switch) and the
  Scarlett's hardware gain knob set so spoken speech peaks healthily without
  clipping. The AT2020 is analog XLR, so it does NOT appear as its own device
  — it arrives as the Scarlett's USB input.
- **Output**: **Scarlett 2i2 4th Gen** USB digital (iec958) — the same sink the
  browser (YouTube) uses, so the agent + browser audio stay on the same device.
- The **HyperX Cloud III headset** is no longer the active input; the effective
  config now pins the Scarlett by stable name.

## The live config — `~/.config/openclaw-voice-bridge/voice.yaml`
```yaml
mic:
  device: null              # never a transient PortAudio index
  device_name: 'Scarlett 2i2 4th Gen: USB Audio'   # stable name pin (Packet 7C)
  sample_rate: 48000
  channels: 1               # capture the loudest channel (XLR on input 1)
  blocksize: 480
  gain: 3.0                 # digital pre-amplify; hardware gain does the rest
  min_rms: 0.0005
  normalize_target: 0.35    # peak-normalize clip to 0.35 before ASR

audio:
  output_device: ""         # EMPTY = follow the working default sink (do NOT hardcode an index)
  output_device_name: ""    # EMPTY = follow default (Scarlett iec958)
  output_volume: 100
```

To (re)pin the microphone by stable name (never by index):
```
voicebridge audio select mic "Scarlett 2i2 4th Gen: USB Audio"
voicebridge audio status
systemctl --user restart openclaw-voice-bridge.service
```

## Critical rules (do NOT break)
1. **`audio.output_device` / `output_device_name` must stay EMPTY.** Device
   indices shift on every reconnect; pinning an index is what caused "I can't
   hear the agent." The resolver (2026-08-23) follows the **system default
   sink** when nothing is pinned, and NEVER guesses a device when a pinned
   name matches nothing — guessing once routed beeps+TTS to the silent ALC892
   line-out instead of the real sink, which made PTT look dead while the web
   dashboard (browser audio) worked fine.
2. **`mic.channels: 1`** (mono). The bridge captures the loudest channel, so
   the XLR AT2020 on input 1 arrives cleanly. Do not set 4.
3. **The Scarlett hardware gain + 48V phantom** are the source of a healthy
   AT2020 signal: enable 48V (XLR condenser needs it), set the input gain so
   normal speech peaks without clipping, and keep `mic.gain: 3.0` as a modest
   digital pre-amp. Too high (8x+) amplifies hiss → ASR returns empty.
4. **The bridge peak-normalizes** the clip (`normalize_target: 0.35`) so a quiet
   mic transcribes reliably — do not remove it.
5. **`webui.speak_web_locally: false`** — the **browser plays the reply once**
   (audio_b64). Keep it false so it doesn't double-speak on the PC. Voice/PTT
   (Right Ctrl) speaks via the normal path, once.
6. **Output `swap` on unplug**: after swapping interfaces, audio "came back"
   when the active digital profile was set. If audio stops, check
   `pactl get-default-sink` = Scarlett and run `scripts/diagnose_audio_usb.py`.

## USB port speed (the silent killer)
Audio USB devices on a **USB 1.1 (12M) full-speed port** FREEZE the moment a
stream opens (mic capture, playback, browser). We hit this until the interface
was moved to a fast (480M USB2/USB3) port. Detect it:
```
python scripts/diagnose_audio_usb.py
```
A `⚠ SLOW` warning = replug the device into a USB 3.0 (blue) / direct rear port.

## Sample-rate / "robot voice" note
TTS is 22050 Hz mono. The bridge **resamples to the sink's native rate** before
playing; if it ever sounds pitch-shifted/robotic, a wrong sample rate is the cause
(fixed by the resample in `voicebridge/audio/playback.py`).

## Hotkeys (Right Ctrl PTT)
- Hold **Right Ctrl** to talk (config `hotkey.hold_key: ctrl_r`). A quick tap
  toggles listen; **Esc** cancels speech/task.
- The hotkey uses the `xinput test-xi2 --root` XI2 backend (reliable on this
  Ubuntu 24.04 X11 session). It is self-healing: if the xinput child ever goes
  stale (alive but not delivering events), the bridge restarts the listener
  automatically within ~30s — you should never have to restart the service
  just to regain PTT.
- If Right Ctrl does nothing: first check the bridge is up
  (`systemctl --user is-active openclaw-voice-bridge`) and that the mic pin is
  the Scarlett (`voicebridge audio status`). If the beep sounds but there is
  no spoken reply, the AT2020 is silent — enable 48V phantom power on the
  Scarlett front panel and confirm the input gain knob is up (the digital
  `Line In 1 Gain` is already at max 70/69 dB in ALSA).
- Troubleshoot PTT events with:
  ```
  voicebridge audio status
  journalctl --user -u openclaw-voice-bridge --since "5 min ago" | grep -i hotkey
  ```

## References
- Repo: `~/Projects/Repos/openclaw-voice-bridge`
- Playback + device resolution: `voicebridge/audio/playback.py`
- Mic capture + normalize: `voicebridge/audio/mic.py`
- Stable audio settings contract: `voicebridge/audio/settings.py`
- Bridge rebuild: `systemctl --user restart openclaw-voice-bridge`