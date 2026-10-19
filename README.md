# Qubit VoiceBridge

> 📘 **Docs index:** `docs/README.md` lists every authoritative doc and what each is for.
> 🔊 **Audio:** read `config/voice.example.yaml` (`output_device` stays EMPTY,
> `speak_web_locally: false`) **before** touching any audio/mic/TTS setting.
>
> The three docs previously linked here (`docs/VOICE_BRIDGE_SYSTEM.md`,
> `docs/STEAL_LIST.md`, `docs/AUDIO_SETUP.md`) were purged in `eafb54e26` and no
> longer exist. Do not cite them; retrieve from git history
> (`git show eafb54e26^:<path>`) only if you need the historical text.

A **production-style local voice interface for Qubit Agent (Qubit VoiceBridge)** — push-to-talk,
natural speech in, spoken results out. Built for an **RTX 3070 Ti 8 GB** box
but runs on any Linux PC with a mic. It routes commands into the native Qubit worker (agent turns, brokered
tools, supervised coding runs), summarizes
long coding output, and can be interrupted mid-speech — **only by your voice**.

```
┌──────────────────────────── YOUR PC (everything local) ────────────────────────────┐
│                                                                                    │
│  mic ──hold Ctrl──► Silero VAD ──► Moonshine ASR (sherpa-onnx, CPU, ~0.07s)       │
│                          │                                                          │
│                          ▼                                                          │
│                   intent router (heuristic, ~0 ms)                                  │
│                    ├─ CONVERSATION ──► short: qubit-brain4 (local, ~0.3s)           │
│                    │                   long/complex: DeepSeek API (~1s)             │
│                    │                   web/current: Perplexity API                  │
│                    ├─ CODING TASK ────► Qubit worker (agent turns, tools,        │
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
| Chat (all questions) | **local, GPU, ~3 GB** | `qubit-brain4` (Qwen3-4B) — English-only replies, 24/7 resident |
| Task execution | **Qubit worker** | only TASKS go here (coding, files, terminal, browser, repos), brokered through Qubit policy |
| Cloud APIs | **fallback only** | DeepSeek/Perplexity when the local model or gateway is down |

## Personal knowledge

The bridge reads `~/.config/qubit-voicebridge/personal-knowledge.md` (shipped as
`config/personal-knowledge.md`, installed by `install.sh`) and serves it through the
bounded context compiler (Packet 4B): each chat turn gets a secret-stripped,
size-capped excerpt of your facts — no whole-file injection and no restart needed,
since the file is re-read per turn. The model knows your projects, preferences, and
how you want answers, and replies **only in English** (documented language policy;
see docs/VOICE_BRIDGE_SYSTEM.md). Nothing personal is baked into the model artifact.

Without the two API keys the bridge still fully works for local chat + all
agent task routing; only long/web conversational answers fall back.

## Install on a fresh PC

The **one supported installer** is the packaged product surface
(`docs/OPERATIONS_8KP.md`). It works from a release artifact — no source
checkout or repository-local virtualenv — and is lock + journal guarded,
idempotent, and typed:

```bash
pip install qubit-voicebridge        # from the release artifact
qubit product install          # typed prerequisite probe (dry-run, zero writes)
qubit product install --apply  # stage config/resources + compat aliases
qubit product first-run        # beginner-friendly status wizard (read-only)
qubit product repair           # one diagnostic/repair command (read-only)
```

The canonical launcher is `qubit`. `voicebridge` remains as a
compatibility alias (same commands, same backend); `python -m voicebridge`
works when neither entry point is on `PATH`.

The desktop shortcut and `qubit webui --open` open the real local
dashboard when the bridge is running — they never start a second bridge or
listener, and a token is never placed in a URL, argument, or file. Dashboard
pairing is reported truthfully (`REQUIRES_PAIRING` / `PAIRED`), never as a
fabricated "token generated".

Existing installations on legacy install paths keep working
and migrate with a reversible, backed-up flow:

```bash
qubit product migration        # status (FRESH / LEGACY_ONLY / MIGRATED)
qubit product migrate --apply  # backed-up migration + compat alias
```

For a developer checkout the classic scripted installer remains available:

```bash
git clone https://github.com/cgraph-dev/Qubit-AIO.git
cd Qubit-AIO

# prereqs (one-time): uv, ollama
curl -LsSf https://astral.sh/uv/install.sh | sh
curl -fsSL https://ollama.com/install.sh | sh      # then: ollama serve

./scripts/install.sh
```

The installer (idempotent) creates the venv, installs deps, downloads the
models (piper voice, Moonshine ASR, Silero VAD, eres2net voice-ID), builds
`qubit-brain4` into ollama from the pinned Modelfile, and writes
`~/.config/qubit-voicebridge/voice.yaml` from `config/voice.example.yaml`.

Optional keys for the cloud paths:

```bash
echo 'DEEPSEEK_API_KEY=sk-...'    >> ~/.config/qubit-voicebridge/.env
echo 'PERPLEXITY_API_KEY=pplx-...' >> ~/.config/qubit-voicebridge/.env
```

## Run

```bash
qubit status        # gateway / ASR / TTS / voice-ID status
qubit run           # hotkey push-to-talk loop (Ctrl held = talk)
```

First-run checklist:

```bash
qubit enroll        # record 5s of YOUR voice — arms voice-ID
```

Auto-start (optional):

```bash
./scripts/install.sh --with-systemd     # renders + enables the user service
systemctl --user status qubit-voicebridge
```

Restarting (after a config edit, or to recover a wedged unit):

```bash
qubit restart                           # PREVIEW: names the units, touches nothing
qubit restart --confirm                 # restart the whole fleet
qubit restart --only voicebridge --confirm   # just the push-to-talk bridge
```

`qubit restart` is a convenience fan-out over the same systemd units
(`qubit-voicebridge`, `qubit-gateway`, `qubit-voice-orb`, `qubit-voice-agent`).
It goes through the same authority path as `qubit gateway restart` (e-stop gate,
confirmation, audit) and is a preview unless `--confirm` — a restart replaces
the serving process and drops LIVE voice/terminal sessions. The raw equivalent
is `systemctl --user restart qubit-voicebridge.service`.

Headless / SSH (no X11): `qubit run --no-hotkey` and press Enter to
start/stop capture, or `qubit once` for a single utterance.

## Tests and disk hygiene

The hermetic gates run with the project venv; every session gets a **bounded,
self-cleaning** basetemp and has `TMPDIR` pointed at it, so scratch never
accumulates in `/tmp/pytest-of-*`:

```bash
.venv/bin/python -m pytest tests/            # per-run basetemp, removed on exit
.venv/bin/python -m pytest tests/ --tmp-keep # keep it for debugging
```

A free-space guard refuses to start a session when the filesystem behind the
temp root has less than `--tmp-free-min-mb` (default **2048** MiB):

```bash
.venv/bin/python -m pytest tests/ --tmp-free-min-mb=4096
QUBIT_PYTEST_TMPROOT=/mnt/scratch .venv/bin/python -m pytest tests/  # bigger disk
```

If `/tmp` is already full (the 97% incident that killed a run), reclaim only
disposable test scratch — it never touches the repo, `~/.hermes`, or
`~/.local/state`:

```bash
scripts/tmp_reclaim.sh              # safe: pytest temp roots, throwaway homes
scripts/tmp_reclaim.sh --aggressive # + seeded reference clones/audit logs
```

See `tests/README.md` for the policy's details.

## Usage

- **Hold Ctrl, talk** — release to send. TTS speech stops the moment you
  start talking (verified by voice-ID — other people/TV do NOT interrupt).
- **Tap Ctrl** — toggle hands-free listening (talks until you tap again or
  go silent).
- **Esc** — stop speech / cancel the current task.
- Say things like: *"open the current project, inspect the Stripe webhook
  flow, and tell the agent to patch the signature verification and add
  structured logs"* — it routes to the Qubit agent runtime, tracks progress, and
  speaks a short summary at the end.
- *"status"*, *"stop"*, *"mute"*, *"unmute"*, *"use turbo / normal / slow /
  hard / pro"* are built-in system commands.

## Capabilities (voiced)

The local agent (`qubit-brain4` model) + the bridge can do these by
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
  "Yes" / "No" / "Voice runtime" / "Agent runtime") AND the mic keeps listening so you can
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
  ASR → route → agent runtime/local → **the local brain's (qubit-brain4) ≤40-word summary** → TTS.
- **Audio on the PC (opt-in)**: web/dashboard requests play in the browser once;
  they also speak through the PC speakers ONLY when `webui.speak_web_locally:
  true` is explicitly set. The tuned setup keeps it `false` so nothing
  double-speaks (docs/AUDIO_SETUP.md rule 5).
- **Activity dashboard**: `http://<this-pc-ip>:8890/activity` — live feed
  of what the agent did: recognized text, routing decision, status, the
  final spoken summary, latency, and (for tool runs) the raw output behind
  the summary. Auto-refreshes every 2s.
- **API** (for scripts): `POST /api/talk` with raw Int16 PCM @16k mono
  returns `{request_id}` instantly, then poll `GET /api/result/<id>` for
  `{text, route, summary, audio_b64}`. `GET /api/activity` = the feed.
  `GET /api/status` = health. Every API route is authenticated with the
  `X-Api-Token` header (`webui.token` in `~/.config/qubit-voicebridge/voice.yaml`).
  Query-string tokens are always rejected (Packet 1B) — the token never appears
  in URLs, history, or logs.

Find your PC's LAN IP with `ip -4 addr show | grep inet`. The dashboard
binds `0.0.0.0:8890` by default (`webui.host` / `webui.port` in config).

### Hosted frontend (when you're ready)

The `frontend/` dir is the deployable static app: `index.html` (chat) +
`activity.html` (dashboard). It works from Vercel too — open **Pair device**
(`/token-setup`) or the ⚙ connection settings and paste your PC's current
tunnel URL + token. The URL is validated (loopback HTTP or HTTPS only, no
credentials/query strings), the candidate token is tested with the
`X-Api-Token` header **before** it is saved, and the previous working token
is kept on any failure. An attacker-controlled `?api=`/`?token=` query string
is never honored — the stored token is only ever sent to the approved origin
as a request header. CORS is enabled on the bridge for exact configured
origins so the hosted page can call the PC API.

**Summarize-first workflow (why it feels smart):** recurring tool tasks are
`local-script` templates — the bridge runs the tool itself (Perplexity
browser search, graphify graph queries, …), feeds the raw output to the
local ollama model (`summarizer.provider: ollama`, default qubit-brain4), and speaks ONLY the summary.
The phone/dashboard shows the raw result as reference, but the voice never
reads it out. Zero API cost, ~0.3s for the compression step.

## Configuration

Full reference: `config/voice.example.yaml` (installed to
`~/.config/qubit-voicebridge/voice.yaml`). Key knobs:

| Key | Default | Meaning |
|---|---|---|
| `asr.provider` / `asr.engine` | `sherpa` / `moonshine` | `parakeet` = max English accuracy; `whisper` = old fallback |
| `vad.backend` | `silero` | `webrtc` fallback |
| `tts.provider` | `piper` | `kokoro` = quality voice (~1s/reply CPU) |
| `intent.local_llm.model` | `qubit-brain4` | local short-turn model (non-thinking) |
| `speaker_verify.threshold` | `0.65` | voice-ID strictness (0.7-0.96 self / ~0.3 strangers) |
| `mic.gain` / `mic.channels` | `1.0` / `1` | tune to YOUR mic (~3.0 mono suits USB headsets; high gain adds hiss) |
| `safety.dry_run_first` | `false` | `true` = coding tasks plan first, ask before applying |

Validate configuration any time: `.venv/bin/python scripts/config_doctor.py --static` (offline)
or `--runtime` (adds typed live probes: Ollama residency, gateway, CDP, isolation, audio).

## Safety

- **Spoken confirmation** before destructive commands (`rm`, `drop`, force
  push, installs, service restarts…).
- **Denylist** (`ignore all previous instructions`, `disable safety`) refused
  outright — see `safety` in the config.
- **Dry-run mode**: `safety.dry_run_first: true` makes coding tasks plan
  first and ask *"Do you want me to apply the patch?"* before acting.
- Everything routes through **the Qubit broker policy** for terminal/file
  work; the bridge never bypasses it.

## Observability

Every turn is logged as structured events to
`~/.local/state/qubit-voicebridge/events.jsonl`:

```bash
tail -f ~/.local/state/qubit-voicebridge/events.jsonl | jq -c '{t:.ts, e:.event}'
```

Watch for `mic.utterance`, `asr.transcribe`, `routing.decision`,
`conversation.local` (0.3s) vs `conversation.deepseek`/`conversation.perplexity`,
`barge.verify` (accepted/score), `task.finished`, and any `*.failed`.

## Troubleshooting

- **"It doesn't talk back"** — check the SERVICE journal first, not the
  events file: `journalctl --user -u qubit-voicebridge.service -n 50`.
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
  quiet mics); check levels: `qubit mic-test`.

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
3. **Smarter local LLM** — evaluate pinned 4B challengers only via the plan's
   Phase-4 bake-off gates; never co-load an 8B model on this 8 GB GPU.
4. **PersonaPlex / realtime duplex** — the bridge's route layer was
   PersonaPlex-ready and the adapter point is preserved; on a 12 GB+ GPU or
   rented box you can re-add the duplex lane without touching the local stack.

## Release certification (Packet 8K)

`docs/OPERATIONS_8K.md` is the release-certification runbook: build the wheel
and sdist, inspect and install them into a clean HOME outside the checkout,
run import / entry-point / resource / unit / smoke checks with no
`PYTHONPATH` or source-tree `sys.path` insertion, and record provenance,
dependency constraints, licenses, SBOM, secret/machine-path scans, hashes, and
a reproducibility comparison. Quick hermetic smoke:

```bash
python scripts/release_qualify.py --static --json   # no network
qubit package certify                          # same, via the CLI
python scripts/clean_venv_check.py --install         # opt-in clean-venv install
```

## License / scope

Personal tool. No telemetry, no cloud dependency for the core loop.

Licensing: original Qubit material is covered by the school-project terms in
`LICENSE`. Third-party/upstream-derived material keeps its original license;
see `docs/LICENSING.md`, `THIRD_PARTY_NOTICES.md`, and
`docs/UPSTREAM_REUSE.md`. The repository as a whole is not an OSI-approved
open-source project.

This is a **school-only submission**: artifact signing and private-source
distribution are NOT required and are reported honestly as NOT APPLICABLE — no
signing key is generated or claimed, and the wheel is never claimed private
(`qubit product boundary`).
