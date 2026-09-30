# Latency tuning + AT2020 condenser staging (measured 2026-08-21)

Session where the voice stack moved from "responds in 2-3s and mishears" to
"~1.2s and understands". All values verified on this box (RTX 3070 Ti 8GB,
Focusrite Scarlett 2i2 4th Gen, AT2020).

## Latency: where the 2-3s went

Roundtrip = PTT release → capture tail → whisper → DeepSeek → piper → speaker.

| Stage | Before | After | Knob |
|---|---|---|---|
| Post-release tail | fixed 0.6s sleep | 0.2s | `hotkey.tail_s` (was hardcoded in `_capture_utterance`) |
| VAD trailing silence | 1.2s | 0.4s | `vad.silence_timeout_s` |
| Whisper | small | small (kept) | `asr.model` — small is 4-6x more accurate, +~0.2s vs base |
| DeepSeek | full reply, then speak | **streamed** | `intent.stream_speech: true` (default) |
| piper | synthesize whole reply | sentence-wise | `speak_stream()` |

Expected: ~2.5-3s → ~1.2s. The DeepSeek API roundtrip (~0.6-1s) is the floor —
overlap it with speech rather than trying to shrink it.

## Streaming pipeline (the real speed lever)

- `_chat_deepseek_stream(text, on_sentence)` — `stream: True` SSE; accumulates
  deltas; emits a sentence when the buffer matches `[.!?…]\s*`; flushes the
  tail at the end. Returns the full reply for conversation history.
- `speak_stream(sentences)` — caller thread synthesizes each sentence (piper
  `synthesize_wav`) while a background player drains a queue sequentially;
  first sentence plays as soon as it's ready. Checks `_interrupt_requested`
  between plays so Ctrl still cuts speech.
- Verified standalone: "Say exactly: The quick brown fox jumps. Over the lazy
  dog." → 2 sentences emitted, full response 1.1s, first sentence available
  ~0.5s earlier than the non-streaming path.
- Fallback chain intact: streaming empty → `_chat_deepseek` (non-stream) →
  `_chat_ollama`.

## AT2020 + 2i2 staging (physical, in order)

1. XLR into Input 1.
2. +48V button ON (LED lit) — the AT2020 is a condenser; without phantom the
   capsule is nearly dead (that was the original ~0.0004 RMS + 50Hz hum).
3. INST switch OFF (it's for guitars; lit = instrument-level input).
4. Gain knob to ~2 o'clock; halo ring flashes GREEN on speech, never red.
5. Speak 15-30 cm away, slightly off-axis.

Measured progression (ch0, 48k float32):
- Unpowered/knob-low: rms 0.0014-0.0022, peak 0.006-0.018 (whisper-level)
- Properly staged: peak 0.098, loudest-1s rms 0.0248 (GOOD, 15x hotter)

## The clipping trap (why "it doesn't understand good")

`mic.gain: 64` was tuned for the quiet stage. After the physical gain knob went
up, 64× on a 0.098-peak signal = 6.2 → hard clip to ~1.0 → distorted garbage
into whisper → perfect transcripts became garbage ("doesn't understand good"
was CLIPPING, not the mic). Fixed with `mic.gain: 8.0` (0.098 × 8 = 0.78,
healthy headroom).

Rule: physical gain and digital gain interact directly — any change to the
knob requires re-measuring and re-tuning the digital gain.

## Measurement scripts (reusable pattern)

- Probe: record 5-30s (`sounddevice`, float32, 4ch), per-channel RMS/peak +
  50Hz-vs-speech-band ratio (`np.fft.rfft`, bands 45-55 / 90-300 Hz).
- Verdict for the 30s version: `peak > 0.05` = GOOD (staged correctly).
- Prefer the 30s "adjust whenever you're ready" probe over timed 5s windows —
  users miss tight windows while reading instructions.
