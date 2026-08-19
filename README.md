# OpenClaw Voice Bridge

A **production-style local voice interface for OpenClaw** — push-to-talk, natural
speech in, spoken results out, built for an **RTX 3070 Ti 8 GB** machine.
It routes commands into your *existing* OpenClaw gateway, agents, and APIs
(no OpenClaw config changes), summarizes long coding output, and can be
interrupted mid-speech.

```
 mic (PTT hotkey) → VAD → faster-whisper (GPU ASR)
                                    ↓ transcript
                        intent router (heuristic + local LLM)
                          /      |        \        \
                     system  confirm  cancel  conversation  coding
                                                    │         │
                                              ollama chat    safety gate
                                                              │ (confirm?)
                                                    OpenClaw gateway (WS)
                                                    │ sessions.create/send
                                                    │ agent.wait
                                                    ↓
                                              summarizer → piper TTS → speakers
```

PersonaPlex verdict up front: **not active — this 8 GB GPU cannot run it
reliably** (official weights are 15.6 GB bf16; the only community quants target
Jetson-class unified memory or an unproven moshi.cpp fork). The bridge is
PersonaPlex-*ready*: `asr/personaplex.py` implements the same provider
interface, so on a 12 GB+ machine you point `asr.provider: personaplex` at a
running PersonaPlex server and the rest of the system is unchanged.

---

## Architecture (why this design)

| Layer | Component | Why |
|---|---|---|
| Voice input | `audio/mic.py` + `audio/vad.py` | sounddevice capture via PipeWire, webrtcvad endpointing (silence timeout 1.2 s), hold-to-talk |
| ASR | `asr/whisper.py` (active), `asr/personaplex.py` (plug) | faster-whisper `base` on CUDA ≈ 180× realtime and fits in ~0.5 GB VRAM *alongside* a loaded agent model |
| Intent | `intent/classifier.py` + `intent/llm.py` | fast deterministic router (system/confirm/cancel/coding) + local qwen3-vl:8b JSON classifier when unsure |
| Execution | `openclaw/gateway_ws.py` (primary), `openclaw/cli_backend.py` (fallback) | the **documented** Gateway WS protocol v4 (`sessions.create/send`, `agent.wait`, `chat.history`, `sessions.abort`); CLI fallback when the gateway is down |
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
"quit".

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

## Upgrade path to a 4090 / 12 GB+ GPU

1. Run the PersonaPlex realtime server (nvidia/personaplex-7b-v1 stack) on the
   new machine; point `personaplex.url` at it.
2. Set `asr.provider: personaplex` — the bridge swaps input+output to the
   full-duplex realtime layer; everything else is untouched.
3. Bump `asr.model` to `small`/`medium` (VRAM is no longer scarce).
4. Optionally route conversation and heavy tasks back to the 30B MoE
   (`openclaw.model: ollama/qwen3:30b-a3b`) now that it can share VRAM.

## Project layout

```
voicebridge/
  audio/        mic capture, VAD endpointing, playback+barge-in
  asr/          whisper (active), personaplex (pluggable)
  tts/          piper (active), edge, gateway
  intent/       classifier, ollama LLM classifier, state
  openclaw/     discovery, gateway WS client, CLI fallback
  safety/       policy (confirmation, allow/deny, dry-run)
  orchestrator.py  main loop
  cli.py        voicebridge run/once/task/status/mic-test/asr-test/tts-test/roundtrip
config/voice.example.yaml
scripts/install.sh, run.sh, test_roundtrip.{py,sh}
systemd/openclaw-voice-bridge.service
tests/test_units.py
```
