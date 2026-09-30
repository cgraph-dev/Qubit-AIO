# PersonaPlex silent output — full diagnostic (Aug 2026)

Session where the user reported "it doesn't speak / he doesn't respond" for
several rounds while PersonaPlex was actually generating the whole time. This
is the ordered debugging path that finally isolated the LOCAL speaker path.

## The evidence trail (each round eliminated one link)

1. `systemctl --user is-active openclaw-voice-bridge.service` → active.
   Lane events: `personaplex.state: live` (the WS session is up).
2. Box reachable: `curl -sk https://<tailnet-ip>:8998/` → 200; server process
   alive; VRAM ~19 GB.
3. Mic: `personaplex.tap` events fire with the user's words (whisper hears
   them) → mic + tap path OK. NOTE: taps were mostly GARBAGE multi-language
   hallucinations at confidence 0.0 — that's the tap transcribing the model's
   own output through the speakers (echo), fixed by gating `_feed_tap` on
   `not self.speaking`.
4. Send gate: live mic measurement with the bridge's exact settings
   (`sd.InputStream(device=None, samplerate=48000, channels=4)`, gain applied)
   → speech RMS after gain must exceed `speech_threshold`. With the quiet
   Scarlett (raw ~0.0004) the gate at 0.02 with gain 4 blocked everything —
   but whisper still transcribed it (whisper works at -43 dBFS, moshi does NOT
   respond at that level). Final: `mic.gain 64` + `speech_threshold 0.008`.
5. **GPU correlation (the decisive test)**: sample the box GPU every 2 s while
   the user speaks →
   ```
   03:27:30 78 %
   03:27:34 81 %
   ```
   Spikes exactly on speech = the server heard the user and generated a reply.
   From here on the model side is PROVEN working; the failure is local output.
6. Local output: `sd.OutputStream(samplerate=24000, channels=1)` — the lane's
   exact call — FAILED:
   ```
   PortAudioError: Error opening OutputStream: Invalid sample rate [PaErrorCode -9997]
   ```
   on the default (Scarlett, 48k) AND on ALC892 (44.1/48k): the ALSA backend
   does not resample, hardware rejects 24 kHz. (Confusingly, the SAME call
   succeeded ~10 min earlier — device state changes between attempts; never
   trust a single probe.)
7. Fix verified: `sd.OutputStream(device="pulse", samplerate=24000, ...)` and
   `device="pipewire"` both open — the mixers resample. Bridge now tries
   `output_device` config → pulse → pipewire → default, logs
   `personaplex.output {device: pulse, rate: 24000}`, and keeps the lane alive
   for input even when every output open fails.

## Quick checks for "generated but inaudible"

```bash
# 1) Did it generate? (run while the user speaks)
ssh -i ~/.ssh/vast_pp -p <port> root@ssh2.vast.ai \
  'nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader'   # 70%+ = generating

# 2) Which output device did the bridge pick?
grep personaplex.output ~/.local/state/openclaw-voice-bridge/events.jsonl | tail -1

# 3) Sink volume/mute
pactl get-sink-volume @DEFAULT_SINK@ && pactl get-sink-mute @DEFAULT_SINK@

# 4) Prove hardware rejects 24k vs the mixer accepts it (sanity probe)
python3 - <<'EOF'
import sounddevice as sd, numpy as np
b = (np.sin(2*np.pi*880*np.arange(24000)/24000)*0.15).astype(np.float32)
for dev in (None, "pulse", "pipewire"):
    try:
        o = sd.OutputStream(device=dev, samplerate=24000, channels=1, dtype="float32")
        o.start(); o.write(b); o.stop(); o.close()
        print(f"{dev}: OK")
    except Exception as e:
        print(f"{dev}: FAIL {str(e)[:60]}")
EOF
```

## Rules that came out of this

- **Never trust "it's not responding" without a GPU trace** — the model may be
  answering into a void. Correlate user speech → box GPU spikes first.
- **Moshi (and voice LMs generally) need real speech level (~-30 dBFS+)**: a
  signal that whisper transcribes fine can still be "silence" to the model.
  Raise physical mic gain first; digital gain second (it raises noise too).
- **24 kHz output on this box must go through pulse/pipewire** — direct ALSA
  hardware streams at 24k are rejected by Scarlett/ALC892.
- The lane must never depend on the output stream succeeding: input/ASR/routing
  keep working, output retries on the next pump cycle.
