# Voice/TTS Provider Quirks

## OpenAI TTS via Qubit Gateway

- **Supported model:** `gpt-4o-mini-tts` only
- **Unsupported:** `tts-1`, `tts-1-hd` — gateway returns:
  ```
  VALIDATION_ERROR: Unsupported managed OpenAI speech model
  Supported models: ['gpt-4o-mini-tts']
  ```
- **Voices available:** `alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer`
- **Config:**
  ```yaml
  tts:
    provider: openai
    openai:
      model: gpt-4o-mini-tts
      voice: nova
  ```

## OpenAI TTS Direct (Own API Key)

If using your own OpenAI API key outside the Qubit gateway, `tts-1` and `tts-1-hd` work normally. The restriction above only applies to gateway-routed requests.

## Edge TTS (Free, No Key)

- Uses Microsoft Azure Speech behind the scenes
- Completely free, no API key needed
- Voices: `en-US-AriaNeural` (default), plus many others
- Offline capable after first download

## ElevenLabs

- Requires `ELEVENLABS_API_KEY` in `$QUBIT_HOME/.env`
- Default model: `eleven_multilingual_v2`
- Default voice ID: `pNInz6obpgDQGcFmaJgB` (Adam)

## Local STT (faster-whisper)

- Runs entirely offline, no API key
- Falls back from GPU to CPU automatically if CUDA libs missing
- First run downloads the model (~150MB for base)
- Warning about unauthenticated HF Hub is harmless
