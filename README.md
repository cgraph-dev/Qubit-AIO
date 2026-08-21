# OpenClaw Voice Bridge

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
| Short replies (llama3.2:3b) | **local, GPU, 2.6 GB** | stays resident 24/7 (`keep_alive: -1`) |
| Long/complex answers | DeepSeek API | optional — needs `DEEPSEEK_API_KEY` |
| Current-info/web answers | Perplexity API | optional — needs `PERPLEXITY_API_KEY` |
| Task execution | **your OpenClaw** | gateway auto-discovered, agents untouched |

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
