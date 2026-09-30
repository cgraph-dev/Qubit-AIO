# OpenAI Realtime duplex lane — protocol, gotchas, verification

Sibling of the PersonaPlex duplex lane (`voicebridge/personaplex/realtime.py`,
class `RealtimeDuplex`). Same interface: `start()/stop()/duck()/unduck()`,
`state`/`live` props, `on_transcript(text, confidence)` callback. Selected via
`personaplex.enabled: true` + `personaplex.provider: realtime`; model/key under
the `realtime:` config section (`model`, `key_env` = env file holding
`OPENAI_API_KEY`).

## Wire protocol (what actually worked, Aug 2026)

- Endpoint: `wss://api.openai.com/v1/realtime?model=<model>` — model names are
  `gpt-4o-realtime-preview`, `gpt-4o-mini-realtime-preview` (cheap lane).
- Headers: `Authorization: Bearer <key>` + `OpenAI-Beta: realtime=v1`.
- **websockets 17 API**: the connect kwarg is `additional_headers=` — the old
  `extra_headers=` (v13 and earlier) raises `TypeError: create_connection()
  got an unexpected keyword argument 'extra_headers'`.
- After connect, send ONE `session.update` to configure:
  - `modalities: ["audio","text"]`
  - `turn_detection: {type: server_vad, threshold: 0.5, prefix_padding_ms: 300,
    silence_duration_ms: 600}`
  - `input_audio_transcription: {model: "whisper-1"}` — this gives clean user
    transcripts via `conversation.item.input_audio_transcription.completed`,
    so NO local whisper tap is needed for routing.
- Send mic audio as `{"type":"input_audio_buffer.append","audio":"<base64
  pcm16 mono 24 kHz>"}` — 48 kHz → 24 kHz is exact `/2` decimation.
- Receive: `response.audio.delta` (base64 pcm16 24k → speakers),
  `response.audio_transcript.delta` (log), the transcription-completed event
  (→ `on_transcript` → orchestrator routes tasks; conversation intents are
  ignored — the model already answered natively), `response.done`, `error`.
- Ducking during tasks: `response.cancel` stops the model's reply; stop
  feeding mic audio; mute output. (Same duck/unduck contract as PersonaPlex.)

## Gotchas

- **`beta_api_shape_disabled`** (WS close code 4000,
  `invalid_request_error.beta_api_shape_disabled`): auth PASSED but the
  account/key cannot use Realtime. Account-side fix, not code: add billing
  credits at platform.openai.com (free-tier keys get this), and/or create a
  full-scope key. The lane detects the error text and backs off 300 s with one
  `realtime.account_blocked` event instead of retry-spamming.
- `input_audio_buffer.speech_started/stopped` fire per server-VAD turns; the
  transcript event is what matters for routing.
- Realtime is METERED (~$0.06/min audio in, ~$0.24/min out on the full model,
  mini cheaper) — leave `personaplex.enabled: false` unless actually using it.
- Account-blocked or bad model names surface as a 4000 close, not an HTTP
  error — look at the WS close reason.
- Do NOT run the Realtime lane while the PTT loop is also capturing: both
  would own the mic. The duplex lane replaces PTT for conversation when live.

## Verification recipe (costs ~$0.01, real check)

```bash
cd ~/Projects/Repos/openclaw-voice-bridge
LD_LIBRARY_PATH=/usr/local/lib/ollama/cuda_v12 timeout 45 .venv/bin/python - <<'EOF'
import asyncio, json, websockets
from voicebridge.personaplex.realtime import _read_env_key
key = _read_env_key("$QUBIT_HOME/.env", "OPENAI_API_KEY")
async def main():
    url = "wss://api.openai.com/v1/realtime?model=gpt-4o-mini-realtime-preview"
    headers = {"Authorization": f"Bearer {key}", "OpenAI-Beta": "realtime=v1"}
    async with websockets.connect(url, additional_headers=headers, open_timeout=15.0) as ws:
        await ws.send(json.dumps({"type": "session.update", "session": {
            "modalities": ["audio", "text"],
            "turn_detection": {"type": "server_vad", "threshold": 0.5,
                               "prefix_padding_ms": 300, "silence_duration_ms": 600},
            "input_audio_transcription": {"model": "whisper-1"}}}))
        got = []
        for _ in range(8):
            ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            got.append(ev.get("type"))
            if ev.get("type") == "session.updated":
                break
        print("OK" if "session.updated" in got else got)
asyncio.run(main())
EOF
```
`session.updated` → account enabled, lane ready. Close 4000
`beta_api_shape_disabled` → account-side fix (credits / key scope).

## Framing (what the user cares about)

PersonaPlex in this setup is NOT a naked 7B voice — Qubit wraps it as the
orchestrator (intent → memory → backend dispatch). Realtime keeps that same
orchestration (transcript tap → same classifier → DeepSeek/Qubit/Qubit)
and only swaps the mouth. When the OpenAI account has Realtime enabled, it's
the full-duplex voice on this 8 GB box today; PersonaPlex remains the local
option once a 24 GB GPU exists.
