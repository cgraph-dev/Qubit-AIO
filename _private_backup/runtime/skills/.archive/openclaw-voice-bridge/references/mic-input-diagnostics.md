# Mic input diagnostics — "can't hear you" / "doesn't understand me" with a pro mic

Symptom: the bridge says "I didn't hear anything" OR whisper garbles speech even
though the user has a professional interface (Focusrite Scarlett 2i2 + XLR mic).

## Rule of thumb
Software is almost never the cause of DIGITAL SILENCE. A healthy pro mic shows
ambient RMS ~0.005–0.05 (raw) at 100% digital volume; speech peaks 0.05–0.3.
If a per-channel capture shows ALL channels ≈ 0.000x, the interface is sending
silence — fix the analog chain, not the bridge.

## Step 1 — software baseline (5 min, do once)
```bash
# list sources + find the interface's input
pactl list short sources
# current source volume (59% was the culprit once)
pactl list sources | grep -A1 'Volume:'
# set the interface input to 100% (digital gain is free on class-compliant gear)
pactl set-source-volume alsa_input.usb-...analog-surround-40 100%
```

## Step 2 — make the bridge read ALL channels
The Scarlett 2i2 4th Gen defaults to the `analog-surround-40` profile = **4
channels** at 48 kHz. The bridge must match:
```yaml
mic:
  channels: 4        # per-channel max picks whichever channel carries the mic
```
(2 channels on a 4ch profile still works when the mic is on ch 0/1, but 4 is
robust to profile changes. ch2/3 reading exactly 0.00000 in the 4ch profile is
NORMAL — they're unused surround channels, not a fault.)

## Step 3 — per-channel capture (the decisive probe)
Run a 6-second 4-channel capture and print per-channel RMS/peak, then have the
user speak continuously for the whole window:

```python
import time
import numpy as np
import sounddevice as sd

frames = []
def cb(indata, frames_count, t, status):
    frames.append(indata.copy())
with sd.InputStream(samplerate=48000, channels=4, callback=cb, dtype="float32"):
    time.sleep(6.0)
data = np.concatenate(frames, axis=0)
for ch in range(data.shape[1]):
    rms = float(np.sqrt(np.mean(data[:, ch] ** 2)))
    print(f"ch{ch}: rms={rms:.5f} peak={float(np.max(np.abs(data[:, ch]))):.5f}")
print("bridge view (loudest ch x4 gain):",
      float(np.max(np.sqrt(np.mean(data ** 2, axis=0))) * 4.0))
```
(Also usable as `scripts/` — keep a copy in the repo's scripts dir.)

### Reading the numbers
| Result | Meaning | Action |
|---|---|---|
| ch0/1 ≥ 0.01 while speaking | healthy | bridge config or ASR was the issue |
| all ch ≈ 0.000x, even when speaking | **analog dead** | hardware checklist below |
| one channel hot, others silent | mic on that channel | channels:4 already handles it |
| gained view < 0.02 after speech | signal too weak | raise `mic.gain` or input gain knob |

## Step 4 — hardware checklist (Scarlett 2i2 4th Gen specifics)
The 2i2 4th Gen is class-compliant: **NO ALSA mixer gain controls** — `amixer
-c <n> contents` shows the motherboard's HDA codec (Front Mic/Surround/Center/
LFE), not the interface. All input gain is ANALOG on the box:
1. **Gain knob** (the one for the input the mic is in) — start ~50% (12 o'clock);
   the ring LED should light on speech.
2. **+48V phantom power** for condenser mics — without it a condenser is nearly
   silent. Dynamics (SM7B etc.) don't need it but need high gain (~50–60 dB).
3. **Input selector** on Mic (not Inst/Line) for XLR mics.
4. XLR fully seated in the correct input; interface powered; not in
   monitor-only mode.

## Verify the fix
```bash
cd ~/Projects/Repos/openclaw-voice-bridge && ./scripts/run.sh mic-test
```
Target: gained peak RMS ≥ 0.02–0.3 (was 0.0029 when dead). When the number
jumps, whisper hears exact words and the empty-utterance rejections stop.

## If levels are HOT but the bridge still rejects
- Check `mic.channels` vs profile (Step 2).
- Check `vad.rms_gate` (0.004) — after `mic.gain` 4x, hiss can read as speech
  (gate too low) OR speech can read as silence (gate too high).
- Check `asr.min_confidence` is ~0.02 (a 0.15 gate rejects perfect transcripts).
- Check the journal: `mic.utterance.empty` with `ended_by` + `peak_rms` tells
  you whether capture or VAD dropped it.
