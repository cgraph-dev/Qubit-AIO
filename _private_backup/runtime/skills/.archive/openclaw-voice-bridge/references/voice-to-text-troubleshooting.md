# Diagnosing voice-to-text failures ("bridge didn't catch me / doesn't understand my speech")

Class-level debugging recipe for the Qubit Voice Bridge ASR+capture path.
Applies whenever the user says the bridge "responds fast but doesn't understand me"
and points at "the model that does the voice to text."

## Quick check order

1. **Which ASR actually loaded?** The config says `asr.engine: parakeet`, but the runtime truth
   lives in the `asr.load` event:
   ```
   grep -a 'asr.load' ~/.local/state/openclaw-voice-bridge/events.jsonl
   # -> {"engine": "parakeet", "provider": "sherpa", "device": "cpu"}
   ```
   - `parakeet` = sherpa's most-accurate engine (NeMo parakeet-TDT 0.6B v3 int8). Good.
   - `moonshine` = fast-but-lower-accuracy. If you see this, you traded accuracy for speed.

2. **Capture health** — inspect `peak_rms` on real (non-empty) utterances:
   ```
   grep -a '"event": "mic.utterance"' ~/.local/state/openclaw-voice-bridge/events.jsonl | tail
   # -> {"peak_rms": 0.48063, ...}   (healthy)
   grep -a '"event": "mic.utterance.empty"' ~/.local/state/...
   # -> {"peak_rms": 0.0, "hint": "no audible speech (level below min_rms)"}
   ```
   - `peak_rms = 0.0` + `no audible speech` → dead capture: PTT timing, user hesitation, OR the
     fast-fail dead-stream branch (`zero_frames >= 20 and t > 0.6s` with RMS < 1e-4) aborted
     before speech began.
   - `peak_rms < 0.01` → gain too low. Raise `mic.gain` (and the physical 2i2 gain knob).
   - `peak_rms > 0.9` → clipping. `mic.py` clips to ±1.0, so high RMS = flattened waveform that
     murders ASR. Lower `mic.gain`.
   - Healthy range: **0.05 – 0.5**.

3. **Endpointing vs. WER** (the two distinct failure signatures):
   - `mic.utterance` with a **healthy `peak_rms`** (e.g. 0.2) followed by `asr.transcribe` with
     `chars: 0` → the VAD/endpointing ended the capture before parakeet ran. Audio was there; the
     turn ended too early. **This is the common "didn't catch me" case.**
   - transcription present but garbled/wrong → WER (model + decoding).

## Root cause found this session
`vad.silence_timeout_s: 0.8` + `vad.speech_start_ms: 180` cut off quick starts and mid-thought
pauses → empty `chars: 0` transcriptions and "I didn't hear anything."

Speed-neutral fix (capture tuning only, no ASR change):
```yaml
vad:
  speech_start_ms: 120    # was 180 — catches quicker starts; 200ms pre-roll covers leading edge
  silence_timeout_s: 1.1  # was 0.8  — don't end the turn on mid-thought pauses
```

## Config knobs (live config: ~/.config/openclaw-voice-bridge/voice.yaml)
| knob | default | effect |
|------|---------|--------|
| `mic.channels` | 4 | Scarlett 2i2 4ch profile: channel-max downmix can pick noise on an empty channel — try `2` |
| `mic.gain` | 8.0 | software boost before VAD; too high → clipping (peak_rms → 1.0) |
| `mic.min_rms` | 0.0008 | below this the capture is auto-aborted as silence |
| `vad.speech_start_ms` | 180 | ms voiced before "speech" begins; higher loses leading words |
| `vad.silence_timeout_s` | 0.8 | end-of-turn silence; lower cuts mid-thought pauses |
| `vad.rms_gate` | 0.004 | per-frame RMS floor so hiss/hum doesn't trigger VAD |
| `asr.engine` | parakeet | parakeet v3 = most accurate; moonshine = fastest (less accurate) |
| `asr.provider_ep` | cpu | `cuda` only if sherpa-onnx built with CUDA EP |
| `asr.model` / `asr.beam_size` | (whisper) | **whisper-path only** — see Pitfall B |

## Pitfalls
- **A — "use a better/faster ASR model."** parakeet v3 is already the most-accurate sherpa engine on this box. Don't "upgrade" until you confirm the failure signature (step 3). Most "didn't catch me" cases are endpointing, not WER.
- **B — `asr.beam_size` does NOT touch parakeet.** `voicebridge/asr/sherpa.py` hardcodes `decoding_method="greedy_search"` for the transducer path (there is no beam arg for parakeet). `asr.beam_size: 5` in the config only applies to the whisper fallback. To get beam search for parakeet, edit `sherpa.py`: set `decoding_method="beam_search"` and pass `max_active_paths=N` (NOT `beam_size`). Expect ~2x decode time.
- **C — `asr.provider_ep: cuda` is unavailable here.** `onnxruntime.get_available_providers()` returns only `CPUExecutionProvider` (sherpa-onnx installed CPU-only). CUDA needs the CUDA wheel variant — a reinstall you usually don't want. parakeet stays on CPU; tune capture instead.

## Applying changes
Config is read at startup; restart to apply:
```
systemctl --user restart openclaw-voice-bridge.service
```
Note: the terminal safety gate blocks interactive `systemctl restart` until you consent; run it yourself or grant consent. A SIGHUP does NOT reload the config (it's read at startup).

## Distinguish ASR accuracy from downstream-LLM comprehension
If `asr.transcribe` yields **correct text** but the spoken *reply* is shallow/dumb, the bottleneck is the
conversation backend, not the mic:
- `intent.local_llm.model: huihui_ai/llama3.2-abliterate:3b` answers ALL casual chat locally
  (fast, weak comprehension).
- `intent.llm_classifier: false` = heuristic routing only. If a real task landed in "conversation"
  instead of Qubit/Qubit, that's the classifier (enable `llm_classifier: true` to route better —
  but only do this if you want the bridge itself to disambiguate; manual routers leave it off).
