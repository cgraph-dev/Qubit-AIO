# Starnet voice study → Qubit reuse (2026-10-17, branch fix/starnet-voice-study)

Source (READ-ONLY, never modified): `/home/trick/Projects/starnet-live`
(sibling checkouts `starnet` = empty, `starnet-incomplete-*` / `starnet-partial-*`
= git objects only). Qubit paths below are this worktree (`/tmp/wt-starnet-voice`).

## 1. Starnet pipeline map (exact file:line)

| Stage | Starnet file:line | What it does |
|---|---|---|
| Input trigger — click-to-talk | `frontend/app/voice.js:3` (header), mic button `voice.js:1836`, take loop `voice.js:1106-1180` | Click mic to start/stop; one bounded take. No wake-word engine anywhere (no openwakeword/porcupine/snowboy in sidecar or frontend — verified by grep). |
| Input trigger — hands-free loop | `voice.js:1016-1090` (`convoMode`, `maybeRearm`, `toggleConvo`) | Self-driving listen→send→speak→listen; entering it force-enables the speaker (`forcedSpeak`, `voice.js:1066`); mic never re-opens into own voice (echo guard). |
| Live voice (realtime) | `sidecar/realtime-voice.js:1-182`, routes `sidecar/index.js:11003-11004` (`/api/realtime/status`, `/api/realtime/session`) | WebRTC to provider realtime endpoint (`realtime-voice.js:108-109`, default model `gpt-realtime-2.1`, default voice `ash` male). Provider credential = the connected ChatGPT-subscription/API key (`resolveCredential`, `:108-145`); session config with semantic VAD + barge-in (`sessionConfig`, `:49-85`); per-agent prompt injected over data channel (`:58-60`). Bound to the session it opened in (`frontend/app/voice-live.js:559,1227`). |
| STT ladder | `sidecar/media-service.js:transcribeAudioBuffer` (`:517-584`), `handleStt` (`:592-622`), status (`:506-515`) | Order: Groq whisper-large-v3-turbo → OpenAI whisper-1 → OpenRouter chat-model with `input_audio` → local Whisper (`Xenova/whisper-tiny.en`, `local-voice.js:loadAsr`). `sttStatus()` reports cloud/local/native + preferred (`:506-515`). |
| STT local incremental stream | `sidecar/voice-stream.js:1-139` (`createVoiceStream`, `makeVoiceStreams`), route `/api/local-voice/stream` (`index.js:11008`) | Two-hypothesis confirmation with word timestamps; retires confirmed audio; serializes ONNX decodes; silence-gated (`local-voice.js:transcribe` RMS `<0.0025` → `''`, `:270-276`). |
| STT native fallback (Windows) | `sidecar/native-stt.js:1-162` | System.Speech dictation; **confidence-gated** (`gateTranscript`, min 0.4, `DEFAULT_MIN_CONFIDENCE`): noise tokens (`NOISE_TOKENS`) dropped at ANY confidence so room noise never spends a provider run. |
| TTS ladder (`/api/tts`) | `sidecar/media-service.js:handleTts` (`:377-487`), `ttsSynthKeyed` (`:233-305`), `synthesizeForAgent` (`:307-333`) | Order: keyed chain openrouter→gemini→openai (`TTS_KEY_PROVIDERS`, `:234`) → keyless Edge read-aloud floor (`edgetts.synth`, always on unless `EDGE_TTS=0`) → honest `{fallback:true}` JSON (never silence, `:378-382`). Voice-cache on disk (`voice-cache/`, `:171`), engine pinned per reply so the speaker never changes mid-reply (`:417-424`). |
| TTS local engine | `sidecar/local-voice.js:loadTts` (Kokoro-82M q4 ONNX, `am_onyx` default male, 18-voice catalogue with per-row Edge fallback `edgeVoiceFor`, `:29-72`) | `body.local` + `localVoice` id selects Kokoro; failure falls back to the **mapped** Edge voice, never a different provider identity (`media-service.js:427-446`). |
| TTS keyless floor | `sidecar/edgetts.js:1-191` | Hand-rolled Edge read-aloud WebSocket client (zero-dep, browser UA + trusted token + Sec-MS-GEC clock injection); default `en-US-ChristopherNeural` (`:24`); `resolveVoice` (`:46-48`). |
| Agent voice tool | `sidecar/tools/builtin/voice.js:1-71` (`voice_generate`), registered `sidecar/index.js:19197`, gated `capability/registry.js:342-346` | Speech the agent MAKES as a workspace file (consent-gated, deliverable kind `file` so chat renders a player). Explicitly NOT conversational talk-back ("your spoken replies are already handled by the station"). |
| Talk-back trigger | `frontend/app/chat.js:9093-9183` (`willSpeak`, `pushSpeech`, `closeSpeech`) | Speaker toggle 🔊 (`voice.js:39-71`, persisted `starnet.voice.speak`, **default OFF**). When on: per-turn `voiceModeRules()` appended to system prompt (`chat.js:11-22` — 1–3 short sentences, no markdown); each COMPLETE sentence streamed to `Voice.speakChunk` as tokens arrive (`pushSpeech`, sentence-boundary flush + 1200 ms stall timer); reply closed exactly once (`closeSpeech`). Background sessions never interrupt the visible one (`speechOwner`). |
| Spoken-line enforcement | `frontend/app/voice.js:speakable` (`:346-369`), `splitForTts` (`:377-395`) | Strips code fences, inline code, md links→label, bare URLs→"a link", paths→"that file", headers/bullets/emphasis, emoji; over-long replies split at sentence boundaries (never guillotined). |
| Agent voices | `local-voice.js:LOCAL_VOICES` (18 Kokoro ids, male default), `realtime-voice.js:VOICES` (10 OpenAI), `voice.js:291-305` (one locked station voice; per-agent override `AGENT_VOICE_KEY`, `voice.js:675-708`) | Selection validated against catalogue (`normalizeLocalVoice`/`normalizeVoice`); unknown → default, never passed through. |

Models used (all local): Whisper `Xenova/whisper-tiny.en` q8 CPU (ASR), Kokoro-82M q4 CPU (TTS),
cached per-user (`%LOCALAPPDATA%/StarNet/models`, `~/Library/Application Support/StarNet/models`;
`local-voice.js:9-20`). Cloud legs are provider-keyed (Groq/OpenAI/OpenRouter/Gemini) plus the
keyless Edge floor. No wake-word model at all.

## 2. Gap table: starnet stage → Qubit counterpart → gap

| Starnet stage | Qubit counterpart | Gap |
|---|---|---|
| Click-to-talk bounded take | `voice_edit` capture + `POST /api/audio/transcribe` (`qubit_compat.py:8262`), Right-Ctrl PTT | None structural — both push-to-talk into same turn. |
| Wake word | `voice_edit.wake` (`voice_edit/settings.py:391`: enabled default **False**, phrase "hey qubit", KWS+ASR fallback) | Qubit HAS a wake config Starnet lacks entirely. No port either way; note Starnet deliberately ships without one. |
| STT ladder (Groq→OpenAI→OR→local) | `_tts`…/`transcribeAudioBuffer` equiv: local sherpa/whisper (`asr/`), `stt_ws.py:92` streaming server | Qubit local-first; no cloud STT ladder. Architectural if wanted — not proposed (privacy posture differs). |
| STT silence/confidence gates | `asr/` VAD, `audio/vad.py` | Partial: Qubit VAD-gates input; Starnet's per-phrase confidence gate (native-stt) has no direct counterpart. Minor; no action. |
| TTS ladder keyed→Edge floor | `_tts_from_cfg` (`qubit_compat.py:8160`): piper/edge/openai/hybrid/kokoro chain; `POST /api/audio/speak` (`:8378`) | Present. Qubit Edge = `edge-tts` PyPI + ffmpeg (heavier than Starnet's zero-dep WS client). Possible future port; not this change. |
| Engine pinning per reply | — | **Missing.** Qubit has no "first audible chunk pins the voice" rule. Proposal §3 item. |
| Talk-back trigger (auto-speak streaming replies) | `ReadAloudButton` (`frontend/qubit-web/src/components/ReadAloudButton.tsx`) + `ReadAloudController` (`lib/voice/readAloud.ts`) — **manual per-message "read" button only**; ChatPage (`ChatPage.tsx:2538`) never auto-speaks; `talk.speak` (`ocFamilyTalkApi.ts:202`) is confirm-gated one-shot | **Architectural gap.** No `willSpeak`/`pushSpeech`/`voiceModeRules` equivalent: no speaker toggle, no sentence-streaming speech, no spoken-style prompt shortening. Proposal §3. |
| Spoken-line sanitizer | `orchestrator.speech_direct` (`orchestrator.py:1037`: emoji + filler only); `h_audio_speak` previously raw `text[:4000]` | **Fixed here** for the dashboard route (see §4). `orchestrator.speak` still markdown-blind — noted, not changed (desktop hot path). |
| Voice-mode prompt shortening | — | **Missing** (part of architectural proposal). |
| Live realtime call (WebRTC+barge-in) | `GET /api/audio/voice-live/status` served read-only; `POST /api/audio/voice-live/session` **typed refusal** `VOICE_LIVE_VENDOR_DIRECT` (`qubit_compat.py:10040-10050`) — Qubit forbids direct vendor calls from dashboard | Policy gap, owner-level. Not proposed for port. |
| Agent `voice_generate` tool | No agent TTS-production tool found | Possible small follow-up (synthesize-to-workspace via existing TTS chain). Listed in §3, not implemented. |

## 3. Integration proposal (architectural gaps — NOT refactored)

Ordered by touch points, smallest first:

1. **Engine pinning per reply** (small, ~1 file): record which provider served the first
   audible chunk of a `speak_stream`/read-aloud sequence and keep it for the rest;
   fail-silent rather than substituting a different speaker mid-reply (Starnet
   `media-service.js:417-424`). Touch: `orchestrator.speak_stream` or TTS call sites.
2. **Spoken-style shortening for read-aloud** (medium): when text is destined for TTS,
   prepend a short-spoken variant (Starnet `voiceModeRules`, `chat.js:11-22`) OR
   summarize-then-speak for long replies (Qubit already has `summarizer.clamp_words/
   _strip_markdown` used at `orchestrator.py:2893,3968` — wire it into the speak path).
3. **Auto talk-back: speaker toggle + sentence streaming** (large): frontend speaker
   preference → stream completed sentences of each assistant reply to `/api/audio/speak`
   as they arrive (Starnet `pushSpeech`, `chat.js:9183`), single-audio controller
   (Qubit already has it: `ReadAloudController`), plus barge-in/stop on user send.
   Touch: `ChatPage.tsx`, `readAloud.ts`, possibly `chat_transport.py`. Needs owner
   sign-off (autoplay policy + voice-actionability directive).
4. **Agent voice-production tool** (medium): `voice_generate`-equivalent writing a clip
   into the agent workspace via the existing `_tts_from_cfg` chain + deliverable card.
   Needs consent/capability design (Starnet gates it `requiresConsent`, studio scope).

## 4. What changed here (small safe port)

`voicebridge/tts/base.py`: new pure helper `speakable_text()` — Python port of Starnet
`frontend/app/voice.js:speakable()` (+ `FORK:`/`TASK_QUESTION:` suppression from
`chat.js:speakSafe`). Deliberately omits Starnet's `ULTRON→Ultron` identity rule and its
1200-char chunker (Qubit caps at 4000 in the caller).

`voicebridge/qubit_compat.py:h_audio_speak`: sanitize `text` via `speakable_text()`
before synthesis; pure-markup input → honest 400 `AUDIO_TEXT_REQUIRED` (never silent
audio). No other route touched; `orchestrator.speak` untouched.

`tests/test_audio_speak_speakable9u.py`: 7 focused hermetic tests (fake TTS captures
synthesis text): markdown/headers, URLs→"a link" w/ label kept, code-fence + choice
marker drop, path→"that file", plain-text passthrough, pure-markup 400, unit cases.

## 5. Verification

- New: `pytest tests/test_audio_speak_speakable9u.py` → **7 passed**.
- Neighbors `test_voice_e2e_hermetic9u.py + test_voice_tts_provider_cache9u.py +
  test_packet7c_audio.py` → 9 failed / 49 passed / 6 xfailed — byte-identical
  failure set with the change stashed (pre-existing: chat-transport/estop + packaging
  manifest, unrelated to TTS).
- **Unverified**: no live TTS audio rendered (no provider/microphone exercised —
  hermetic fakes only); no frontend check (`ReadAloudButton` path unchanged, npx
  vitest not run — no frontend files touched); no service restart; Starnet code was
  read-only (no Starnet tests run).
