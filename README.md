# OpenClaw Voice Bridge

A **production-style local voice interface for OpenClaw** — push-to-talk, natural
speech in, spoken results out, built for an **RTX 3070 Ti 8 GB** machine.
It routes commands into your *existing* OpenClaw gateway, agents, and APIs
(no OpenClaw config changes), summarizes long coding output, and can be
interrupted mid-speech.

```
                     ┌──────────────────────────────────────────────┐
                     │ Vast.ai (disposable compute, Tailscale-only) │
                     │  PersonaPlex (nvidia/personaplex-7b-v1)      │
                     │  wss://personaplex:8998/api/chat             │
                     └──────────────▲───────────────────────────────┘
                                    │ opus audio (full duplex)
┌───────────────────────────────────┴────────────────────────────────────┐
│ YOUR PC                                                                │
│  mic ──┬─► PersonaPlex lane ────────────────► speakers (persona chat) │
│        │      (continuous, PP does barge-in)                           │
│        └─► whisper tap ─► intent router                                │
│                 ├─ CHAT ──────────────► PersonaPlex answers            │
│                 ├─ OPENCLAW_TASK ─────► OpenClaw gateway (loopback)    │
│                 ├─ HERMES_TASK ───────► hermes chat -q (optional)      │
│                 ├─ LOCAL_PC_TASK ─────► whitelisted actions            │
│                 ├─ CONFIRMATION ──────► safety policy (spoken yes/no)  │
│                 └─ STATUS ────────────► bridge status                  │
│  result ──► summarizer ──► TTS (piper/edge) ──► speakers               │
│  memory: ~/.openclaw/workspace (MEMORY.md + memory/YYYY-MM-DD.md)      │
└────────────────────────────────────────────────────────────────────────┘
```
(whisper-pipeline mode, without a PersonaPlex server, keeps working unchanged:
`mic → ASR → intent → gateway → TTS`.)

PersonaPlex verdict: **runs remotely on Vast.ai** — this 8 GB 3070 Ti cannot
run the 15.6 GB bf16 weights locally, so the model lives on a rented 24 GB GPU
and the bridge talks to it over **Tailscale**. Two modes:

- **whisper pipeline** (default, offline): mic → ASR → intent → OpenClaw → TTS.
- **PersonaPlex full-duplex** (`personaplex.enabled: true`): the remote model
  is the front-facing voice — natural conversation, barge-in, low latency. The
  bridge taps the same mic stream with whisper, classifies intent, and routes
  tasks to OpenClaw / Hermes / local PC actions, speaking results back in
  character. Vast.ai stays disposable; memory lives on this PC.

---

## Architecture (why this design)

| Layer | Component | Why |
|---|---|---|
| Voice input | `audio/mic.py` + `audio/vad.py` | sounddevice capture via PipeWire, webrtcvad endpointing (silence timeout 1.2 s), hold-to-talk |
| ASR | `asr/whisper.py` (active), `asr/personaplex.py` (plug) | faster-whisper `base` on CUDA ≈ 180× realtime and fits in ~0.5 GB VRAM *alongside* a loaded agent model |
| Intent | `intent/classifier.py` + `intent/llm.py` | fast deterministic router (system/confirm/cancel/coding/local/hermes/conversation) + local qwen3-vl:8b JSON classifier when unsure |
| Execution | `openclaw/gateway_ws.py` (primary), `openclaw/cli_backend.py` (fallback), `hermes/backend.py`, `local/actions.py` | the **documented** Gateway WS protocol v4 (`sessions.create/send`, `agent.wait`, `chat.history`, `sessions.abort`); CLI fallback; optional Hermes delegation; whitelisted local PC actions |
| Speech out | `tts/piper.py` (active), `tts/edge.py`, `tts/gateway_tts.py` (optional) | piper is fully local, CPU-only (never fights the GPU), faster than real time; gateway TTS reuses your configured OpenAI/Edge providers if you want |
| Safety | `safety/policy.py` | destructive-pattern confirmation, allow/deny list, dry-run-first, spoken confirmations |
| Summaries | `summarizer.py` | long agent output → 2–3 spoken sentences (ollama or extractive fallback) |
| Observability | `logging_setup.py` | human logs + structured `events.jsonl` (transcript, routing decision, request, status, result, failures, reconnects, timing) |

**Why Python:** the OpenClaw gateway exposes a clean WebSocket JSON-RPC protocol
with published schemas (`@openclaw/gateway-protocol`), and the machine already
runs the Python voice stack (faster-whisper, sounddevice, piper). The CLI
(`openclaw agent`) remains as a second route.

**Route selection:** the bridge connects to the running gateway as a trusted
loopback backend client (`client.id: gateway-client`, `mode: backend`) using the
existing `OPENCLAW_GATEWAY_TOKEN` — no device pairing, no config writes. If the
gateway is unreachable it falls back to `openclaw agent` subprocesses, then to
"offline" with a spoken notice.

## Hardware facts measured on this machine

| Item | Value |
|---|---|
| GPU | RTX 3070 Ti, 8 GB (~7.1 GB usable after desktop) |
| ASR | whisper `base` fp16 on CUDA: 0.6 s load, **0.04 s for 7.2 s audio** |
| TTS | piper `en_US-amy-medium`: 0.85 s load, ~0.25 s per sentence, CPU |
| Agent task | qwen3-vl:8b via gateway: **1–15 min for a real tool-using task** on a fresh session (agent bootstrap = system prompt + tool docs + workspace context, processed on CPU-offloaded layers; a warm model + existing session is faster) |
| Note | GPU ASR needs `LD_LIBRARY_PATH=/usr/local/lib/ollama/cuda_v12` (Ollama bundles CUDA 12; without it whisper silently falls back to CPU) |

## Install (exact commands)

```bash
# 1. project + venv
mkdir -p ~/Projects/Repos/openclaw-voice-bridge
cd ~/Projects/Repos/openclaw-voice-bridge
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python faster-whisper sounddevice numpy webrtcvad websockets pyyaml piper-tts setuptools pynput
.venv/bin/python -m pip install 'setuptools<81' -q   # webrtcvad needs pkg_resources

# 2. piper voice (~63 MB)
mkdir -p ~/.local/share/openclaw-voice-bridge/models
curl -sL -o ~/.local/share/openclaw-voice-bridge/models/en_US-amy-medium.onnx \
  https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/amy/medium/en_US-amy-medium.onnx
curl -sL -o ~/.local/share/openclaw-voice-bridge/models/en_US-amy-medium.onnx.json \
  https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/amy/medium/en_US-amy-medium.onnx.json

# 3. config
mkdir -p ~/.config/openclaw-voice-bridge
cp config/voice.example.yaml ~/.config/openclaw-voice-bridge/voice.yaml
# edit: pick openclaw.model (null = agent default; "ollama/qwen3-vl:8b" = snappy)

# or run the whole thing:
./scripts/install.sh
```

## Run (exact commands)

```bash
./scripts/run.sh status          # gateway discovery + ASR/TTS/VRAM health
./scripts/run.sh run             # interactive push-to-talk loop
./scripts/run.sh task "inspect the Stripe webhook flow and patch signature verification"
./scripts/run.sh once            # record one utterance, process, exit
./scripts/run.sh mic-test        # verify mic levels (Focusrite: gain ~12 o'clock)
```

**Push-to-talk:** hold **Right Ctrl**, speak, release. **Esc** cancels
(stop speech / abort task). If hotkeys are unavailable (SSH/headless), run with
`--no-hotkey` and press **Enter** to start/stop recording.

**Voice commands:** "status", "help", "mute"/"unmute", "stop"/"cancel",
"use turbo|normal|slow|hard|pro" (switches the model for subsequent tasks),
"quit". In full-duplex mode, tasks come naturally in conversation:
"open the current project and inspect the Stripe webhook validation".

**systemd (optional):** the unit runs the bridge under your user session
(hotkey requires the X session; comment the `DISPLAY`/`XAUTHORITY` lines for
headless manual mode):

```bash
mkdir -p ~/.config/systemd/user
cp systemd/openclaw-voice-bridge.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now openclaw-voice-bridge
journalctl --user -u openclaw-voice-bridge -f
```

For daily use, tmux is honestly the smoothest: `tmux new -s voice && ./scripts/run.sh run`.

## Test (exact commands)

```bash
.venv/bin/python -m pytest tests/ -q            # 19 unit tests (classifier/safety/config/state)

./scripts/test_roundtrip.sh                     # full voice→OpenClaw roundtrip, no mic needed
./scripts/test_roundtrip.sh --no-dispatch       # quick: synth→ASR→classify only (~5 s)
```

`test_roundtrip.py` synthesizes the sample utterance with piper, transcribes it
with GPU whisper, classifies it as a **coding** task, dispatches it to your
existing `main` agent through the gateway, waits for completion, and renders
the spoken summary — then asserts the event journal contains the full pipeline
(`asr.transcribe → routing.decision → openclaw.request → openclaw.result →
tts.speak`). Run with `--model ollama/qwen3-vl:8b` for reasonable latency.

## Logs / observability

- Console: human-readable to stderr.
- `~/.local/state/openclaw-voice-bridge/events.jsonl` — one JSON per event:
  `asr.transcribe`, `routing.decision`, `openclaw.request/status/result/summary`,
  `openclaw.gateway.connect.retry` (reconnects), `safety.destructive`,
  `orchestrator.timing`, `tts.speak`, and failures. Timestamps + durations are
  on every request/result pair.

## Safety

- **Destructive actions** (delete/rm/drop/truncate/wipe/force-push/reset --hard/
  shutdown/kill/sudo/chmod/`>` redirects/curl|sh …) require a **spoken
  confirmation**: the bridge asks *"Do you want me to …?"* and waits for
  yes/no. `no` cancels.
- **Deny list** refuses outright (prompt-injection markers like "ignore all
  previous instructions").
- **Dry-run-first** mode (`safety.dry_run_first: true`) prepends an explicit
  dry-run instruction to every coding task: inspect + report, then wait for
  confirmation before applying.
- `--dry-run` CLI flag: never dispatch, only speak the plan.

## Limitations on this 3070 Ti (honest)

1. **Agent latency is bootstrap-dominated.** Even the fast 8B takes ~85 s cold
   through the agent (system prompt + tool docs + workspace context on 8 GB);
   the 30B MoE takes minutes. The bridge speaks "On it." immediately and
   streams a summary at the end — but don't expect chat-speed turnarounds for
   real tool-using tasks. Warm models help; `ollama stop` the 30B before
   expecting fast 8B tasks.
2. **VRAM budgeting.** whisper `small` OOMs while the 30B MoE is loaded; the
   default `base` coexists. If you need `small`/`medium`, stop Ollama models
   first (`ollama stop qwen3:30b-a3b`).
3. **Hotkeys need the X11 session** (pynput). Wayland/SSH → use `--no-hotkey`
   manual mode or run inside the graphical session.
4. **No wake word yet.** Push-to-talk first, by design. A wake word can be
   added behind the same mic pipeline later (or via PersonaPlex's full-duplex
   input once it's on bigger hardware).
5. **Agent tooling quirks are the agent's, not the bridge's.** During testing
   the agent tried a *file-transfer/paired-node* tool first and had to adapt
   to local file tools (no paired nodes on this gateway). The bridge routes
   the task; tool selection is the agent's business.
6. **Conversation class is fast local chat** (ollama) by default; set
   `intent.conversation_backend: agent` to route chat through OpenClaw (full
   memory/tools, slower).

### Known edge case: "I couldn't pull the final text"

Occasionally the gateway completes a run (`agent.wait` → `ok`) but the parent
session transcript contains only the user message — the agent's work happened
in an embedded/subagent session that never wrote back. The bridge now walks
three sources before giving up (parent history → child sessions via
`parentSessionKey` → gateway task ledger via `childSessionKey`), and if all are
empty it says so honestly instead of pretending. `openclaw.request` /
`openclaw.result` journal entries carry the `runId` so you can find the work in
the Control UI (`http://localhost:18789`). Tasks that outlive
`openclaw.task_timeout_s` get a spoken "still running" and a background
**reaper** keeps polling until completion, then delivers the final summary
anyway — so a 20-minute job still gets its spoken result without blocking the
conversation.

## PersonaPlex on Vast.ai (full-duplex mode)

### Deploy the server on Vast (one-time, ~15 min)

1. **Rent** a 24 GB GPU (see `vast/README.md` for the `vastai search offers` query).
2. **Accept the license** + create an HF read token:
   https://huggingface.co/nvidia/personaplex-7b-v1
3. **Install Tailscale on this PC** (once):
   `curl -fsSL https://tailscale.com/install.sh | sh` then `sudo tailscale up`.
4. Copy the kit to the box and run the one-shot setup:
   ```bash
   scp -r vast/ root@<vast-host>:/root/
   ssh root@<vast-host>
   cd /root/vast && HF_TOKEN=hf_xxx TS_AUTHKEY=tskey-xxx ./setup_personaplex.sh
   ```
5. Verify: `python3 /root/vast/check_personaplex.py wss://$(tailscale ip -4):8998/api/chat`

### Point the bridge at it

```yaml
# ~/.config/openclaw-voice-bridge/voice.yaml
personaplex:
  enabled: true
  url: wss://personaplex:8998/api/chat
  voice_prompt: NATF2.pt
```

```bash
voicebridge personaplex-check            # connectivity test from this PC
voicebridge status                       # duplex lane + hermes + local state
voicebridge run                          # full-duplex mode: talk naturally
```

### Test phrases (chat vs task routing)

| Say | Expected |
|---|---|
| "What do you think about this marketplace idea?" | PersonaPlex answers in character (CHAT) |
| "Open the current project and inspect the Stripe webhook validation." | routed to OpenClaw (OPENCLAW_TASK); spoken summary after |
| "Ask Hermes to review the latest PR." | Hermes backend (HERMES_TASK) |
| "Turn the volume up" / "open the terminal" | local PC action (LOCAL_PC_TASK) |
| "What's your status?" | bridge + lane health (STATUS) |
| "Delete the file foo" (dangerous) | safety gate asks spoken confirmation (CONFIRMATION) |

### Rotating instances without losing memory

Memory/session state lives in `~/.openclaw/workspace` on this PC — the Vast
box stores nothing. Destroy the instance, rent a new one, rerun
`setup_personaplex.sh` with the same Tailscale hostname, and the bridge
reconnects automatically. See `vast/README.md` for the full runbook.

## Project layout

```
voicebridge/
  audio/        mic capture, VAD endpointing, playback+barge-in
  asr/          whisper (active), personaplex (legacy plug)
  personaplex/  real full-duplex client + duplex lane (duplex.py)
  hermes/       optional Hermes backend (hermes chat -q)
  local/        whitelisted PC actions (volume, apps, screenshot, ...)
  tts/          piper (active), edge, gateway
  intent/       classifier (incl. local/hermes kinds), ollama LLM, state
  openclaw/     discovery, gateway WS client, CLI fallback
  safety/       policy (confirmation, allow/deny, dry-run)
  orchestrator.py  main loop + duplex tap routing + memory load/save
  cli.py        run/once/task/status/mic-test/asr-test/personaplex-check/...
config/voice.example.yaml
scripts/install.sh, run.sh, test_roundtrip.{py,sh}
systemd/openclaw-voice-bridge.service
tests/test_units.py
```
