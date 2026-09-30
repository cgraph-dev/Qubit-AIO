# Mic mains-hum + Sage prompt contamination (Aug 2026 session tail)

Two bugs that looked like "the model is stupid" but were input/context problems.
Both fixed in repo (`qubit-voice-bridge`, commits late Aug 2026).

## 1. 50 Hz mains hum: the "he don't understand me good" fix

### Symptom chain
- User: "he don't understand me good", "sage still not working wtf"
- Sage HEARD something (GPU spikes when the user talked) but misheard/misunderstood.
- A live per-second RMS probe (`/tmp/mic_probe.py` pattern below) showed a FLAT
  0.0011 RMS every second — no speech spikes at all — plus spectral analysis:
  energy <100 Hz was **4.1×** the 200-4000 Hz speech band.

### Root cause
The Scarlett 2i2 analog input carries strong 50 Hz electrical hum (ground loop /
cable / high physical gain). The hum dominated the signal, burying the voice so
both whisper and moshi heard mush. Digital gain only amplifies hum + voice together.

### The fix (in `voicebridge/personaplex/duplex.py`)
One-pole high-pass, no scipy needed:

```python
_HP_ALPHA = 0.987  # ~100 Hz cutoff at 48 kHz

def _hp_filter(self, x):
    a = self._HP_ALPHA
    y = np.empty_like(x)
    prev = self._hp_prev
    xp = self._hp_xprev
    for i in range(len(x)):
        y[i] = a * (prev + x[i] - xp)
        prev = y[i]
        xp = x[i]
    self._hp_prev = prev
    self._hp_xprev = xp
    return y
```

Applied to the mono mic path BEFORE `mic_gain` (`mono = self._hp_filter(mono)`).

### Measured result (on the user's real capture)
```
BEFORE: hum=3.6051 speech-band=0.8813 ratio=4.1x
AFTER:  hum=0.9659 speech-band=0.8466 ratio=1.1x
RMS:    0.00112 -> 0.00093
```
Hum killed, speech band preserved.

### Diagnostic recipe (run when a model consistently mishears)
1. Capture 10 s of mic at the bridge's settings (48k, 4ch, ch0).
2. Per-second RMS — FLAT with no spikes while the user speaks = voice not reaching
   the interface (physical) vs spikes = voice present but buried.
3. FFT the capture: `np.fft.rfft(x * np.hanning(len(x)))`, compare energy
   `<100 Hz` vs `200-4000 Hz`. Ratio >2 = hum problem; fix with the high-pass.
4. Save the WAV (`/tmp/mic_capture.wav`) so the user can hear what the model hears.

### Physical notes (Perplexity-researched)
- Scarlett 2i2 4th gen has 69 dB gain — should NOT be this quiet at high knob.
- Condenser mics need +48V phantom (button on front) or they're nearly dead.
- Input switch: Mic (plain) not Inst/Air; XLR not TRS; check the ring flashes.
- Software capture volume was fine here (95%, unmuted) — always verify with
  `pactl get-source-volume @DEFAULT_SOURCE@` before blaming the interface.

## 2. Memory-notes contamination of Sage's prompt

### Symptom
User asked "tell me a joke"; Sage answered with "turnstile is logged out" and
other stale ops trivia. User: "I think it reads chat from old session or
something cos it doesn't respond to my questions."

### Root cause
`orchestrator.py` appended `_load_memory_context()` (the Qubit agent's notes —
including tailscale/tskey "turnstile" key notes, session logs, status lines) to
`personaplex.text_prompt` at startup. Sage (moshi) treated the notes as its
context and recited them instead of answering.

### Fix
`personaplex.inject_memory: false` (default). The voice model gets a clean
persona; memory belongs to the AGENT. Side effect: the old ~60 s first-handshake
(the "big text_prompt" cost) vanished — the handshake is ~3-5 s now.

### Recognize it
Sage blurts unrelated operational trivia from your notes → check
`personaplex.inject_memory` and what's in the configured `text_prompt`.

## Bonus: direct Perplexity API for practical research (user asked for it)
When the user says "check using perplexity api how other fixed X", call
`https://api.perplexity.ai/chat/completions` directly (model `sonar-pro`,
`PERPLEXITY_API_KEY` from `$QUBIT_HOME/.env`, read via regex — never print it).
Include concrete context (device, profile, measured RMS) and ask for a practical
triage order. It returned exactly the lead we'd missed (69 dB gain, capture-volume
check, channel mapping). It's the same endpoint the bridge's `_chat_perplexity`
uses — a good example of reusing the configured backend for debugging.
