# Hybrid voice — save ~70% on OpenAI TTS (2026-08-23)

Live box had `tts.provider: openai / gpt-4o-mini-tts nova` with sexy-Japanese `instructions`. OpenAI TTS is ~1.04s per utterance (network) vs Piper 0.08s and billed per char (gpt-4o-mini-tts ~$0.60/1M input tokens, still cheaper than tts-1 $15/1M chars but not free). User: "isnt it expensive?" -> hybrid saves ~70%.

## Design
`voicebridge/tts/hybrid.py` — threshold routing (default 18 words):
- short (<=18w, most filler/chat) -> local Piper (free, offline, 0.08s)
- long / reports / heartbeat -> OpenAI `gpt-4o-mini-tts` nova with `DEFAULT_INSTRUCTIONS` = "warm, soft and sexy with subtle Japanese accent ... caring Japanese girlfriend" (only gpt-4o-mini-tts respects `instructions`; tts-1 cannot do accent and is more expensive)

Both paths `fsync`-style never silence: `orchestrator.speak()` and `webui._synthesize()` hot-fallback Piper if OpenAI key/network fails, logged as `tts.hybrid.*_failed_fallback_piper`.

## Config
`voicebridge/config.py` DEFAULTS adds `hybrid: {threshold_words, piper_voice, openai_voice, openai_model}`. Live `~/.config/openclaw-voice-bridge/voice.yaml` must be flipped independently of `config/voice.yaml` (repo defaults don't propagate to live). `orchestrator._make_tts()` handles `provider == "hybrid"`.

## Pitfall
User later said "agent was talking good, switch back to cheap" — confused TTS (agent -> user, fine) with STT (user -> agent, "works like shit"). Don't flip TTS when complaint is ASR quality. `sherpa/parakeet CPU, beam 5` gives `asr_ms 56-82ms, chars 3-5` on short noisy clips ("Hey."/"Mm.") — that's mic/VAD (gain 8.0, rms_gate 0.0018) not the voice. Verify via `tail events.jsonl | grep asr.transcribe` before touching `tts.provider`.

Revert: live `voice.yaml` -> `provider: piper`, repo DEFAULTS -> `piper` (keep hybrid code for opt-in). Verify `curl /api/status | grep tts` flips after `systemctl --user restart`.
