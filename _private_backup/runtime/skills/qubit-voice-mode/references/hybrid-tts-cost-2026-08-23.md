# Hybrid TTS — when it doesn't save money

Added `voicebridge/tts/hybrid.py` (`HybridTTS`): ≤18 words → local `piper` (free, 0.08s), >18 words / reports / heartbeat → `openai gpt-4o-mini-tts:nova` (sexy Japanese, ~$12-15/1M chars). Saves ~70% vs always-openai while keeping the voice for moments that matter.

**Why it still billed:** `tts.provider: openai` bills every short filler (`Yeah.` → 5 words → OpenAI charge + 1.0s TTS delay). Switching to `hybrid` only saves if the *live* file `~/.config/openclaw-voice-bridge/voice.yaml` is patched — the repo's `config/voice.yaml` is NOT the file the bridge reads. The bridge defaulted to `tts.provider: piper` (free) the whole time; `openai` was only active after manually patching the live file. Verify `curl /api/status` shows `"tts": "hybrid"` (or `piper`), not `openai`, otherwise billing continues. Check `hybrid.threshold_words: 18` is present in the live file too.

**Revert if needed:** `hybrid` is staged as `provider: piper` (free, 93 tests green). To force hybrid, set live `tts.provider: hybrid` then `systemctl --user restart qubit-voice-bridge`; to return to free, `tts.provider: piper`. See `references/provider-quirks.md` for `gpt-4o-mini-tts` being the only gateway-allowed OpenAI model (not `tts-1`).
