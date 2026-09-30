# PersonaPlex box debugging — "it doesn't speak / responds slowly" recipe (Aug 2026)

Use this when the bridge lane is `live` but the user reports no reply. Goal: pin
down WHICH link is broken (mic → gate → send → server → generation → speaker)
with two cheap probes, without guessing.

## Rule 0 — the server is SINGLE-SESSION
`moshi/server.py` runs every session under `async with self.lock` — one client at
a time. While the bridge's lane is connected, ANY second client (test script,
`check_personaplex.py` probe) connects but its `\x00` handshake waits forever
(lock held) and times out. **Stop the bridge before testing the server directly:**

```bash
systemctl --user stop openclaw-voice-bridge.service   # frees the session
... run probes ...
systemctl --user start openclaw-voice-bridge.service
```

## Probe 1 — does the server hear + respond to the REAL mic?
A standalone client that uses the bridge's actual config (`voice.yaml`:
personaplex.url / voice_prompt / text_prompt, mic.device / sample_rate / channels
/ gain), records ~5 s of live mic, sends it, waits ~25 s, prints what came back.
Script shape (in repo venv, `LD_LIBRARY_PATH=/usr/local/lib/ollama/cuda_v12`):

```python
import asyncio, time, numpy as np, sounddevice as sd, yaml
import sys; sys.path.insert(0, "~/Projects/Repos/openclaw-voice-bridge")
from voicebridge.personaplex.client import DuplexClient
cfg = yaml.safe_load(open("~/.config/openclaw-voice-bridge/voice.yaml"))
pp, mic = cfg["personaplex"], cfg["mic"]
got = {"audio": 0, "text": ""}
async def main():
    c = DuplexClient(url=pp["url"], voice_prompt=pp.get("voice_prompt", "NATF2.pt"),
                     text_prompt=pp.get("text_prompt", ""),
                     on_audio=lambda p: got.__setitem__("audio", got["audio"] + p.nbytes),
                     on_text=lambda t: got.__setitem__("text", got["text"] + t))
    await c.connect(timeout_s=90)          # ~4-6 s on a healthy host
    frames = []
    def cb(indata, f, t, st): frames.append(np.asarray(indata, dtype=np.float32).copy())
    with sd.InputStream(device=mic.get("device"), samplerate=int(mic.get("sample_rate", 48000)),
                        channels=int(mic.get("channels", 4)), blocksize=480, dtype="float32",
                        callback=cb):
        time.sleep(5)                       # USER SPEAKS HERE
    mono = np.concatenate(frames).mean(axis=1) * float(mic.get("gain", 64.0))
    print("RMS after gain:", float(np.sqrt((mono**2).mean())))  # must exceed speech_threshold
    for i in range(0, len(mono), 480):
        await c.send_pcm_async(mono[i:i+480]); await asyncio.sleep(0.02)
    await asyncio.sleep(25)
    print("RECEIVED", got["audio"]/2/24000, "s audio | text=", got["text"][:80])
    await c.close()
asyncio.run(main())
```

Positive result (audio + text back, e.g. "Hi there, this is Sage…") ⇒ server,
model, mic, gate all work → the bug is inside the bridge's continuous path
(pump / out_worker / gates), not the box.

## Probe 2 — GPU-util trace while the user speaks
Watch the box during a live attempt (bridge running):

```bash
for i in $(seq 1 40); do ssh -i ~/.ssh/vast_pp -p 11540 root@ssh2.vast.ai \
  'nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader' 2>/dev/null; sleep 2; done
```

- Spike to 50-100% right after the user speaks ⇒ generation happened (send path
  OK) → check the speaker/out path (`_out` stream, `out_worker`, duck state).
- Stays 0% ⇒ the audio never reached the model → check the speech gate (RMS vs
  `speech_threshold`) and the pump.
- Always 98%+ even when idle ⇒ shared/oversubscribed host → MIGRATE (see
  SKILL.md "PersonaPlex is LIVE" — check `uptime` + util BEFORE renting).

## Known level thresholds (Scarlett 2i2, this box)
- Raw mic RMS ~0.0004 (physically quiet — gain knob low). Digital `mic.gain`
  went 4 → 16 → 64; speech RMS after 64x ≈ 0.019-0.028.
- `personaplex.speech_threshold` 0.008 at gain 64 (0.004 was too tight at gain 16).
- **moshi ignores very quiet input**: whisper transcribes -43 dBFS fine, the
  model does not respond to it. Get speech ≥ -30 dBFS.
- Tap transcriptions that are multi-language gibberish at confidence ~0.0 =
  the tap hearing the model's own output — tap must be gated on `speaking`.
