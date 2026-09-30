# Duplex mic-input fixes — session detail (Aug 2026)

The last mile of the PersonaPlex-on-8GB project: "Sage hears you (GPU spikes) but
doesn't answer" / "the local voice model is on as well". Three bridge bugs + one
physical reality. All bridge fixes are in the repo; this file records the
reasoning and the physical checklist.

## Symptom → cause map (from the events journal)

| Journal evidence | Meaning |
|---|---|
| `personaplex.tap` with multi-language garbage, confidence 0.00-0.08 | whisper hallucinating on noise/echo — DO NOT route |
| garbage tap → `routing.decision` clarify/task → `state: ducked` → `tts.speak` (piper) | **the local voice took over**: garbage ducked PersonaPlex and piper answered "I didn't catch that." — user perceives "local voice model is on as well" |
| `asr.transcribe.oom → retry on cpu` | whisper fell back to CPU (ollama holds VRAM) — slow taps (5s+), but works |
| `personaplex.tap` only during lane speech | tap not gated on `speaking` — transcribes the model's own voice |
| box GPU 78-85% while user speaks, zero local output | server generated fine → problem is LOCAL speaker path (24k rejection, see output-audio reference) |
| live RMS meter FLAT while user talks | voice never reaches the interface — physical (gain/phantom/cable/input-select), not a bridge bug |

## The three bridge bugs

### 1. 4× dilution — loudest channel, not the mean
```python
# WRONG — Scarlett 4ch profile: mic on ch0, ch1-3 ~0 → voice divided by 4
mono = mono.mean(axis=1)
# RIGHT — loudest channel, sign preserved (same as audio/mic.py _mono)
mono = np.max(np.abs(mono), axis=1) * np.sign(mono[:, 0] if mono.shape[1] else mono)
```
Measured: ch0 RMS 0.00788, ch1 0.00042, ch2/3 0.0 → mean ≈ 0.002 (useless),
loudest = 0.00788 (usable after gain).

### 2. Adaptive gate (static thresholds lose as physical gain moves the floor)
At `mic.gain 64` the Scarlett's electrical noise (raw 0.0003-0.002) lands at
0.02-0.13 RMS — ABOVE every fixed `speech_threshold` we tried (0.004, 0.008).
The model then hears constant static. Implemented in `_audio_cb`:
```python
if rms > 0.0:
    if self._noise_floor <= 0.0:
        self._noise_floor = rms
    else:
        # slow rise, fast drop — floor follows quiet periods
        self._noise_floor = min(self._noise_floor * 1.02 + 1e-5, rms)
    if rms < max(self._noise_floor * adaptive, 0.004):
        return  # noise, don't send
```
`adaptive_gate: 3.0` default. Config key `speech_threshold` now ignored.

### 3. Tap confidence gate (`tap_confidence: 0.3`)
Garbage transcriptions score ~0.0; real speech scores 0.7+. Below the gate:
log `personaplex.tap.low`, return — never call `on_transcript`. This stops the
garbage→clarify→duck→piper cascade (the "local voice took over" bug).

## Physical mic reality (Perplexity-researched, sonar-pro)

Focusrite Scarlett 2i2 **4th gen has 69 dB of preamp gain — it should NOT be
quiet.** An RMS of ~0.008 with the gain knob up means a setup problem, not the
interface. Triage order (do these BEFORE blaming the bridge):
1. Gain knob ALL the way clockwise; the ring/halo must flash green with voice.
2. **Condenser mic → press +48V phantom** (without it, condensers are nearly dead — matches every symptom here).
3. Input switch mode: mic (not Inst / not Air-only).
4. XLR into the combo jack (not a 1/4" TRS for a mic).
5. Software capture: `pactl get-source-volume` (was 95% — fine), alsamixer capture not muted.
6. Channel mapping: mic is on ch0 of the analog-surround-40 profile — read ch0 explicitly (see bug 1).

A FLAT live RMS meter while the user speaks = the voice isn't reaching the
interface (cable/phantom/selector) — no software change fixes that.

## The 15-second live mic meter (reusable diagnostic)
```python
import sounddevice as sd, numpy as np, time
buf = []
def cb(indata, frames, t, status): buf.append(np.asarray(indata, dtype=np.float32).copy())
with sd.InputStream(device=None, samplerate=48000, channels=4, blocksize=480,
                    dtype="float32", callback=cb):
    print("SPEAK NOW"); t0 = time.time()
    while time.time() - t0 < 15:
        time.sleep(1)
        d = np.concatenate(buf); raw = float(np.sqrt((d.mean(axis=1)**2).mean()))
        buf.clear()
        print(f"t={int(time.time()-t0):2d}s raw={raw:.5f} {'#'*int(min(raw/0.003,30))}")
```
Speak → level must MOVE (spikes 5-20× the quiet floor). Flat = physical.

## Perplexity-API research pattern (used to get the 2i2 facts)
Read `PERPLEXITY_API_KEY` from `$QUBIT_HOME/.env`, POST to
`https://api.perplexity.ai/chat/completions` with `{"model": "sonar-pro",
"messages": [...]}`, `Authorization: Bearer <key>`. Response:
`choices[0].message.content` + `citations[]`. Useful mid-debug when the local
heuristics are exhausted — the model answered "the interface should NOT be this
quiet" with sources, which redirected the whole diagnosis from software to
physical.
