# STT empty captures + VAD tuning — Aug 23 2026 (Scarlett 2i2, Parakeet/Moonshine)

## Symptom
Parakeet/moonshine returned `asr.transcribe chars 0` on 0.7s wavs with `peak_rms 0.26-0.37` (loud, not a gain issue). Next good turn: 40 chars `check the weather in Bucharest tomorrow.` OK — engine healthy, capture was junk.

Short captures (`Hey.` / `Mm.` 3-5 chars, `asr_ms 56-82`) classified as `filler/conversation` — not a model bug.

## Bench on one real utterance (Did you use the browser now to find that? 68KB wav)
| Engine | Load | Transcribe | Accuracy |
|---|---|---|---|
| sherpa moonshine (int8 275MB) | 614ms | 0.054s | perfect — fastest |
| sherpa parakeet v3 int8 (641MB) | 1982ms | 0.13s | perfect — most accurate |
| faster-whisper base (int8 142MB) | 250ms | 0.46s | perfect but 8× moonshine |
| small (464MB) | 530ms | 1.21s | drops words |
| large-v3-turbo (1.6GB) | 1490ms | 5.05s | same, way slower |

=> Keep parakeet primary, moonshine fallback. Don't switch to faster-whisper for speed — moonshine is strictly faster on this CPU box.

## Fixes applied
1. **Live `voice.yaml` `vad.rms_gate: 0.0018 → 0.0015`** — catches softer syllables without hissing trigger. Kept `silence_timeout_s: 1.2` (0.4 cut mid-thought).
2. **`_transcribe_with_fallback()` in `voicebridge/orchestrator.py`** — when primary (parakeet/moonshine) returns `empty` on a non-silent wav, retry once with the *other* sherpa engine (moonshine 48ms vs parakeet 172ms on same wav). Tested: 68KB wav parakeet 172ms/41 chars ↔ moonshine 48ms/40 chars; true silence (23KB wav) stays 0/0 no hallucination. Logged as `asr.fallback.success/empty/failed`.
3. **`config.py` `fallback_engine: moonshine`** — single config knob. Only retries when wav had energy (`duration>0.6s`), not pure silence. Alternative `whisper base` fallback is valid but 0.46s vs 0.05s — keep sherpa-to-sherpa for low-resource.

## Coexistence note (8GB 3070 Ti)
OLLAMA `qwen3-vl:8b` (6.2GB) + `huihui_ai/llama3.2-abliterate:3b` (2.2GB) leave ~770MB-4.4GB free depending on loaded model. Faster-whisper base/small compete for same VRAM if run on CUDA — keep STT on CPU (`provider: cpu`) so GPU stays free for the gateway.

## Debug recipe
```bash
tail -n 80 ~/.local/state/openclaw-voice-bridge/events.jsonl | grep '"event": "asr.transcribe\|routing.input\|orchestrator.timing' | tail -n 20
# look for chars 0 with peak_rms >0.05 — those are fallback candidates
# bench a wav:
HOME=/home/trick ~/Projects/Repos/openclaw-voice-bridge/.venv/bin/python -c "from voicebridge.asr.sherpa import SherpaASR; ..."
```
