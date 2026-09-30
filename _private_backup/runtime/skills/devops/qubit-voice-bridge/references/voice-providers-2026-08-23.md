# Voice providers — switching to higher quality (2026-08-23)

## Options (wired in `voicebridge/orchestrator.py:_make_tts` + `voicebridge/config.py:DEFAULTS["tts"]`)

| `tts.provider` | Family | Quality | Needs | Selection |
|---|---|---|---|---|
| `piper` | Local, CPU, offline | Good | `en_US-amy-medium.onnx` in `~/.local/share/openclaw-voice-bridge/models/` | default |
| `kokoro` | Local, CPU (~1× realtime) | Best offline | `kokoro-int8-en-v0_19/` | `kokoro.voice_sid: 0..10` |
| `edge` | Network (Microsoft neural) | Great, free | `edge-tts` + `ffmpeg` | `edge.voice: en-US-JennyNeural` |
| `gateway` | Reuses Qubit speech | Varies | Gateway WS connected | `gateway.model` |
| `openai` | OpenAI `audio/speech` | Best (premium) | `OPENAI_API_KEY` or `VOICE_TOOLS_OPENAI_KEY` in `$QUBIT_HOME/.env` | `openai.voice: nova/alloy/...`, `openai.model: gpt-4o-mini-tts` |

New provider `voicebridge/tts/openai_tts.py:OpenAITTS` uses stdlib `urllib` (no new pip deps), `response_format: wav`, optional `instructions` for style. `available()` is key-presence check; `_make_tts` falls back to `piper` if key missing and logs `tts.openai.unavailable`.

## Dashboard switching
`frontend/index.html` settings panel: voice selector (`localStorage vdb_voice`) persists choice to `voice.yaml` on PC. Also `frontend/activity.html` + `frontend/reports.html` badge sticky/VS Code. No bridge restart required for selector — `voice.yaml` is re-read on next TTS load, or say `route to ...` for agent lane.

## Troubleshooting
- No speech? `journalctl --user -u qubit-voice-bridge -n 20` shows `tts.load` + `tts.openai.unavailable` → check `$QUBIT_HOME/.env` key and `tts.provider: openai`.
- Quality not changing? Hard-refresh dashboard (Ctrl+Shift+R) after editing frontend; `Cache-Control: no-store` is sent but old tab caches JS.

## Weight-level upgrades (beyond TTS)
For editing the voice's *brain* (not file prompts): see `docs/BRAIN_UPGRADE.md` + `brain-upgrade` skill — Modelfile SYSTEM bake, Obliteratus abliteration, Unsloth LoRA on `shared_memory.jsonl`/`events.jsonl` (300 examples, 8 GB VRAM). Dashboard switching is for output voice; brain edits are for decision quality.
