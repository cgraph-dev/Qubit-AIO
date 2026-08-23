# OpenClaw Voice Bridge

> 📘 **Read `docs/VOICE_BRIDGE_SYSTEM.md`** for the full system map, what we
> cherry-picked from open-source (CopilotKit/AirLLM/OpenSandbox), the roadmap,
> and the "smarter model" plan.
> 🧠 **Read `docs/STEAL_LIST.md`** for a focused speed / smarter / minimal-VRAM
> steal-list (specific techniques we can lift from each source and implement).

A **production-style local voice interface for OpenClaw** — push-to-talk,
natural speech in, spoken results out. Built for an **RTX 3070 Ti 8 GB** box
but runs on any Linux PC with a mic. It routes commands into your *existing*
OpenClaw gateway, agents, and APIs (no OpenClaw config changes), summarizes
long coding output, and can be interrupted mid-speech — **only by your voice**.

```
┌──────────────────────────── YOUR PC (everything local) ────────────────────────────┐
│                                                                                    │
│  mic ──hold Ctrl──► Silero VAD ──► Moonshine ASR (sherpa-onnx, CPU, ~0.07s)       │
│                          │                                                          │
│                          ▼                                                          │
│                   intent router (heuristic, ~0 ms)                                  │
│                    ├─ CONVERSATION ──► short: llama3.2:3b (local, 0.3s)             │
│                    │                   long/complex: DeepSeek API (~1s)             │
│                    │                   web/current: Perplexity API                  │
│                    ├─ CODING TASK ────► OpenClaw gateway (existing agent, tools,    │
│                    │                     ACP, VS Code, terminal, browser, repos)    │
│                    ├─ CONFIRM / CANCEL / STATUS / LOCAL ACTION                      │
│                    └─ SYSTEM ─────────► bridge control                              │
│                                                                                    │
│  result ──► summarizer ──► piper TTS (local) ──► speakers                          │
│  barge-in: eres2net voice-ID — ONLY the enrolled voice can interrupt               │
└────────────────────────────────────────────────────────────────────────────────────┘
```

## What is local vs cloud (honest)

| Component | Runs | Notes |
|---|---|---|
| ASR (Moonshine) | **local, CPU, 0 VRAM** | ~0.07s per utterance, ~41-50× realtime |
| VAD (Silero v5) | **local, CPU** | neural endpointing, far better than webrtcvad |
| Voice-ID (eres2net) | **local, CPU** | ~50ms/check; self 0.72-0.96 vs strangers ~0.3 |
| TTS (piper amy) | **local, CPU** | Kokoro-82M optional (`tts.provider: kokoro`) |
| Chat (all questions) | **local, GPU, 2.6 GB** | `huihui_ai/llama3.2-abliterate:3b` — uncensored llama3.2:3b, EN/RO only, personal-knowledge.md injected, 24/7 |
| Task execution | **your OpenClaw** | only TASKS go here (coding, files, terminal, browser, repos); "ask Hermes" → Hermes CLI |
| Cloud APIs | **fallback only** | DeepSeek/Perplexity when the local model or gateway is down |

## Personal knowledge

The bridge reads `~/.config/openclaw-voice-bridge/personal-knowledge.md` (shipped as
`config/personal-knowledge.md`, installed by `install.sh`) and injects it into every
chat turn — the local model knows your projects, preferences, and how you want answers.
Edit the file freely, then `systemctl --user restart openclaw-voice-bridge`. The model
answers **only in English or Romanian** (EN by default, RO when you write in Romanian).

Without the two API keys the bridge still fully works for local chat + all
OpenClaw task routing; only long/web conversational answers fall back.

## Install on a fresh PC

```bash
git clone https://github.com/cgraph-dev/openclaw-voice-bridge.git
cd openclaw-voice-bridge

# prereqs (one-time): uv, ollama
curl -LsSf https://astral.sh/uv/install.sh | sh
curl -fsSL https://ollama.com/install.sh | sh      # then: ollama serve

./scripts/install.sh
```

The installer (idempotent) creates the venv, installs deps, downloads the
models (piper voice, Moonshine ASR, Silero VAD, eres2net voice-ID), pulls
llama3.2:3b into ollama, and writes `~/.config/openclaw-voice-bridge/voice.yaml`
from `config/voice.example.yaml`.

Optional keys for the cloud paths:

```bash
echo 'DEEPSEEK_API_KEY=sk-...'    >> ~/.hermes/.env
echo 'PERPLEXITY_API_KEY=pplx-...' >> ~/.hermes/.env
```

## Run

```bash
voicebridge status        # gateway / ASR / TTS / voice-ID status
voicebridge run           # hotkey push-to-talk loop (Ctrl held = talk)
```

First-run checklist:

```bash
voicebridge enroll        # record 5s of YOUR voice — arms voice-ID
```

Auto-start (optional):

```bash
./scripts/install.sh --with-systemd     # renders + enables the user service
systemctl --user status openclaw-voice-bridge
```

Headless / SSH (no X11): `voicebridge run --no-hotkey` and press Enter to
start/stop capture, or `voicebridge once` for a single utterance.

## Usage

- **Hold Ctrl, talk** — release to send. TTS speech stops the moment you
  start talking (verified by voice-ID — other people/TV do NOT interrupt).
- **Tap Ctrl** — toggle hands-free listening (talks until you tap again or
  go silent).
- **Esc** — stop speech / cancel the current task.
- Say things like: *"open the current project, inspect the Stripe webhook
  flow, and tell OpenClaw to patch the signature verification and add
  structured logs"* — it routes to your OpenClaw agent, tracks progress, and
  speaks a short summary at the end.
- *"status"*, *"stop"*, *"mute"*, *"unmute"*, *"use turbo / normal / slow /
  hard / pro"* are built-in system commands.

## Capabilities (voiced)

The local agent (cripped `qubit-brain` baked model) + the bridge can do these by
voice, fast and low-resource (no GPU training):

- **Play YouTube / YouTube Music**: `play <song/video> on youtube` resolves the
  top 5 via `yt-dlp`, picks the one with the **most views**, and opens it in a
  **Chromium tab**. First time it asks: *"in a windowed tab or quietly in the
  background?"* (audio-only via `ffplay`) — the choice is remembered.
- **Control media**: `pause / resume the youtube music`, `next video` (skip to
  the next ranked result), `stop the music` / `close the youtube tabs` (stops
  playback AND closes all `youtube.com` + `music.youtube.com` tabs).
- **Browser tabs**: `scripts/browser_auto.py` reads/scrapes ANY open Chromium
  tab (`list_tabs`, `read`, `write JS`, `open`), `scripts/tab_report.py` turns a
  tab into a professional report.
- **Inboxes**: check Gmail / Instagram DMs / Messenger / TikTok via the
  logged-in Chromium (review scripts) — fast, cached.
- **Reports**: `scripts/generate_report.py` produces deterministic
  professional reports (full detail + ≤40-word spoken summary).
- **Task queue**: give a task while one is running → it's QUEUED (never
  lost, never hard-aborted). `close task` finishes the current and asks whether
  to start the queued one.
- **Follow-ups**: when the bridge asks a question (play mode, confirm, executor
  choice), it stays **active and online** — the dashboard shows **clickable
  option buttons** under the orb (e.g. "Windowed tab" / "Background audio",
  "Yes" / "No" / "OpenClaw" / "Hermes") AND the mic keeps listening so you can
  answer by voice in the same turn. Answer with a click or by speaking — no need
  to open a fresh message.

### Recommended plugins / tools (must be installed)

| Tool | Why |
|---|---|
| `yt-dlp` | YouTube search + resolution (top-N, rank by views) |
| `ffplay` (ffmpeg) | audio-only background playback fallback |
| Playwright + snap **Chromium** | drive the real logged-in browser via CDP (no "bot secure") |
| `pactl`/`wpctl` | audio sink/volume control |

The bridge runs everything through the **real logged-in Chromium** profile — one
browser for all accounts (Gmail, Instagram, Facebook/Messenger, TikTok, Vercel,
Reddit, X) so no re-login and no captcha flapping.

## Voice Dashboard (talk from your phone)

The bridge serves a small web UI (stdlib HTTP, no extra deps) so you can
talk to the PC from any browser on the LAN — phone included — and watch
what the agent is doing live.

- **Chat page**: `http://<this-pc-ip>:8890/` — hold/tap the button, speak,
  hear the reply (audio comes back to the phone; the PC speakers stay
  silent unless `webui.speak_web_locally` is on). Same pipeline as the mic:
  ASR → route → OpenClaw/local → **the local 3B model's ≤40-word summary** → TTS.
- **Audio on the PC too**: by default a web/dashboard request ALSO speaks the
  reply through the PC speakers (`webui.speak_web_locally: true` in config), so
  replies, summaries, and action results (playing a song, a finished report,
  inbox/research results) are both shown on the dashboard AND spoken aloud.
  Set it to `false` to keep the phone the only audio output.
- **Activity dashboard**: `http://<this-pc-ip>:8890/activity` — live feed
  of what the agent did: recognized text, routing decision, status, the
  final spoken summary, latency, and (for tool runs) the raw output behind
  the summary. Auto-refreshes every 2s.
- **API** (for scripts): `POST /api/talk` with raw Int16 PCM @16k mono
  returns `{request_id}` instantly, then poll `GET /api/result/<id>` for
  `{text, route, summary, audio_b64}`. `GET /api/activity` = the feed.
  `GET /api/status` = health. All API routes accept an optional
  `X-Api-Token` header / `?token=` (config `webui.token`) for external use.

Find your PC's LAN IP with `ip -4 addr show | grep inet`. The dashboard
binds `0.0.0.0:8890` by default (`webui.host` / `webui.port` in config).

### Vercel-hosted frontend (when you're ready)

The `frontend/` dir is the deployable static app: `index.html` (chat) +
`activity.html` (dashboard). It works from Vercel too — the ⚙ settings in
the page let you paste your PC's current tunnel URL + token (saved in
localStorage), or pass them as `?api=<url>&token=<secret>` query params.
CORS is enabled on the bridge so the Vercel origin can call the PC API.

**Summarize-first workflow (why it feels smart):** recurring tool tasks are
`local-script` templates — the bridge runs the tool itself (Perplexity
browser search, graphify graph queries, …), feeds the raw output to the
LOCAL 3B model (`summarizer.provider: ollama`), and speaks ONLY the summary.
The phone/dashboard shows the raw result as reference, but the voice never
reads it out. Zero API cost, ~0.3s for the compression step.

## Configuration

Full reference: `config/voice.example.yaml` (installed to
`~/.config/openclaw-voice-bridge/voice.yaml`). Key knobs:

| Key | Default | Meaning |
|---|---|---|
| `asr.provider` / `asr.engine` | `sherpa` / `moonshine` | `parakeet` = max English accuracy; `whisper` = old fallback |
| `vad.backend` | `silero` | `webrtc` fallback |
| `tts.provider` | `piper` | `kokoro` = quality voice (~1s/reply CPU) |
| `intent.local_llm.model` | `llama3.2:3b` | local short-turn model (non-thinking!) |
| `speaker_verify.threshold` | `0.65` | voice-ID strictness (0.7-0.96 self / ~0.3 strangers) |
| `mic.gain` / `mic.channels` | `1.0` / `2` | tune to YOUR mic (Scarlett+AT2020: `8.0` / `4`) |
| `safety.dry_run_first` | `false` | `true` = coding tasks plan first, ask before applying |

## Safety

- **Spoken confirmation** before destructive commands (`rm`, `drop`, force
  push, installs, service restarts…).
- **Denylist** (`ignore all previous instructions`, `disable safety`) refused
  outright — see `safety` in the config.
- **Dry-run mode**: `safety.dry_run_first: true` makes coding tasks plan
  first and ask *"Do you want me to apply the patch?"* before acting.
- Everything routes through **your OpenClaw safety policy** for terminal/file
  work; the bridge never bypasses it.

## Observability

Every turn is logged as structured events to
`~/.local/state/openclaw-voice-bridge/events.jsonl`:

```bash
tail -f ~/.local/state/openclaw-voice-bridge/events.jsonl | jq -c '{t:.ts, e:.event}'
```

Watch for `mic.utterance`, `asr.transcribe`, `routing.decision`,
`conversation.local` (0.3s) vs `conversation.deepseek`/`conversation.perplexity`,
`barge.verify` (accepted/score), `task.finished`, and any `*.failed`.

## Troubleshooting

- **"It doesn't talk back"** — check the SERVICE journal first, not the
  events file: `journalctl --user -u openclaw-voice-bridge.service -n 50`.
  A crash-restart loop looks like silence; the traceback is in the journal.
- **`vad.silero.unavailable` at startup** — Silero failed to load, you're on
  webrtcvad. Check `silero_vad_v5.onnx` exists under the models dir.
- **Greetings feel slow** — verify `conversation.local` (0.3s) fires, not
  `conversation.perplexity` (2.3s). The web heuristic is deliberately strict;
  if a real web question misses it, say "search …" or "look up …".
- **Barge-in too eager / too deaf** — tune `speaker_verify.threshold`
  (down = easier to interrupt, up = stricter) and re-enroll with a clean
  5s clip.
- **Microphone too quiet** — physical gain first, then `mic.gain` (8-16 for
  quiet mics); check levels: `voicebridge mic-test`.

## Design notes / why this stack

- **Moonshine ASR on CPU** — measured 0.074s for a short utterance (41-50×
  realtime), zero VRAM, beats whisper-small accuracy. The GPU stays free for
  the local reply model (or idle).
- **Silero VAD v5** replaces webrtcvad (which missed ~half of speech in
  noise). Three hard-won pitfalls are documented in code: window size is a
  method, never hardcode `window_size` (sherpa exits 255 on mismatch), and
  hold the last window decision when frames don't align to 576-sample windows.
- **Voice-ID gates barge-in** — eres2net measured on real clips: self 0.72-0.96
  vs strangers 0.22-0.36 (wespeaker-resnet34 was unusable: 0.79-0.85 overlap).
- **One input stream at a time** — two concurrent PortAudio InputStreams on
  one Scarlett intermittently segfault ALSA; capture pauses the barge stream.

## Upgrade path (when you move to a 4090)

1. **Kokoro TTS** — `tts.provider: kokoro` (int8 is ~1× realtime on this
   CPU; on a 4090 you can run the fp32 model GPU-side via a CUDA build of
   sherpa-onnx for ~10× headroom).
2. **Parakeet-TDT 0.6B v3** — `asr.engine: parakeet` for maximum ASR accuracy.
3. **Bigger local LLM** — swap `intent.local_llm.model` to `qwen3:14b`-class
   once VRAM allows (keep it non-thinking).
4. **PersonaPlex / realtime duplex** — the bridge's route layer was
   PersonaPlex-ready and the adapter point is preserved; on a 12 GB+ GPU or
   rented box you can re-add the duplex lane without touching the local stack.

## License / scope

Personal tool. No telemetry, no cloud dependency for the core loop.
