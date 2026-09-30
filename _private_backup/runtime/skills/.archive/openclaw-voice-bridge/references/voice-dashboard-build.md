# Voice Dashboard — build & speed detail (Aug 2026)

Session that added the web UI (talk-from-phone) to the bridge, made it fast,
and exposed it outside. Everything here is in the repo; this file explains
the WHY and the measured numbers so a future session doesn't re-derive them.

## Architecture (one HTTP server, two consumers)

`voicebridge/webui.py` = stdlib `ThreadingHTTPServer` started as a daemon
thread at the end of `Orchestrator.start()` (config section `webui:`,
default `0.0.0.0:8890`). It serves:

- `GET /` (chat page) + `GET /activity` (dashboard) — from `frontend/` dir
  when present, else embedded HTML strings.
- `GET /api/status` (health), `GET /api/activity` (feed ring buffer).
- `POST /api/talk` (raw Int16 PCM @16k mono) → `{request_id}`.
- `GET /api/result/<id>` → `{status: pending|done|unknown, ...result}`.

### Why async (request_id + poll) instead of a blocking POST
The first version made `/api/talk` block until the whole roundtrip finished
(ASR → route → tool script → summarize → TTS). A Perplexity search took
25-38s, so the phone sat on "working…" the whole time. Async: the POST
returns in ~30ms (ack), the page polls every 900ms, and the audio plays the
moment it's ready. Worker threads: `submit_talk()` spawns one per request;
results kept in a dict, pruned to last 50.

## The 15s → 4s fix (root cause everyone should know)

Timeline of the bug:
1. First async version still took 15-18s roundtrip while the events log
   showed only ~2s of real work (`local_script.run` → `local_script.summary`
   → `tts.speak` all within ~2s).
2. The gap was `Orchestrator.speak()`: the webui worker thread called the
   SAME speak() as the mic path, which synthesizes AND plays through the PC
   speakers — and `playback.play()` BLOCKS until the audio finishes. A
   35-word summary ≈ 10s of audio = 10s of blocking. The events log looked
   fine because `tts.speak` is logged BEFORE playback starts.
3. Fix: `_web_mode` class flag. `process_web_utterance()` sets it around the
   routing call; `speak()` checks it FIRST and, when set, appends the text
   to `self._web_spoken` and returns instantly (no synth, no playback).
   WebUI's `handle_talk` then uses `_web_spoken` as the summary source and
   synthesizes ONCE (piper) for the browser's audio_b64.
4. Bonus fix in the same pass: the webui originally built its OWN parakeet
   ASR instance → first request paid ~10s lazy model load from the slow
   DRAM-less Kingston SSD. `_transcribe()` now reuses `orch._asr` when its
   `_recognizer` is loaded (it's thread-safe, internal lock).

Measured after: local search roundtrip **3.5-4.0s**, public-tunnel
roundtrip **4.9s**, ack **27ms**.

## Search speed tiers (why DDG-first)

| Path | Latency | Notes |
|---|---|---|
| DuckDuckGo (`ddgs` lib, bridge venv) | 1.5-3s | snippets only, no API key |
| Perplexity Pro browser (CDP) | 25-38s | deep answers, $0 |
| Perplexity API (sonar-pro) | ~2.3s | paid, flat $0.005 base/req |

`scripts/web_search.py` = DDG first, Perplexity browser fallback on thin
results (<2) or explicit `--perplexity`. The `search-web` template invokes
it via the venv python (shell=True uses SYSTEM python3 otherwise →
`ModuleNotFoundError`).

## Pinggy tunnel for outside access

- Free tier: 60-min tunnels, random URL, no signup. SSH to
  `free@a.pinggy.io:443` with `-R0:localhost:8890`.
- `scripts/voice-dashboard-tunnel.sh` runs an auto-restart loop: starts SSH,
  greps the URL from the log (`https://<sub>.<sub>.run.pinggy-free.link` or
  `.free.pinggy.net`), writes it to
  `~/.local/state/openclaw-voice-bridge/tunnel.url`, and restarts when the
  SSH session dies (60-min expiry). `--once` = single shot.
- systemd user service `voice-dashboard-tunnel.service` (tracked in
  `systemd/`): `ExecStart=.../voice-dashboard-tunnel.sh`, `Restart=always`,
  `After=openclaw-voice-bridge.service`.
- Optional bearer gate: `Environment="VOICE_TUNNEL_TOKEN=<secret>"` → Pinggy
  `k:<token>+free` username. Combined with the bridge's `webui.token` gate,
  the tunneled API is unusable without the token.
- Vercel plan: static frontend in `frontend/` (index.html + activity.html),
  no build step. The ⚙ gear in the chat page saves API URL + token to
  localStorage (`vdb_api` / `vdb_token`), or pass `?api=<url>&token=<secret>`.
  CORS is on the bridge for the cross-origin Vercel → PC API calls.

## Secret-redactor hazard when editing the frontend (bit hard this session)

The toolchain's secret redactor rewrites `TOKEN=*** patterns in tool-call
arguments — INCLUDING inside heredocs and write_file content. Editing the
frontend JS (which legitimately assigns token variables) via heredoc kept
producing `const TOKEN=*** URLSearchParams(...)` on disk → broken JS with
unbalanced braces. Workarounds that held:
- Use a variable name the redactor doesn't pattern-match (`API_TOKEN` still
  got hit; eventually avoided inline assignment entirely by making the save
  handler `localStorage.setItem(...)` + `location.reload()` instead of
  assigning the module-level var).
- ALWAYS verify the actual file bytes after writing (`read_file` or
  `grep -n`), not the tool-call display — the display is redacted, the file
  may be fine (or not).
- For token plumbing in general, see the skill's existing
  secret-redactor pitfall (env-file reads via `read -r`, concatenated
  variable names, optional-dummy defaults).

## Tests

`tests/test_webui.py`: PCM→WAV roundtrip, async submit→poll (fake ASR with
`.load()`, fake orchestrator with `_web_mode`/`_web_spoken` attributes),
local-script dispatch with mocked `summarizer.summarize`. The fake ASR MUST
have `.load()` since `_transcribe` calls it explicitly on the private
fallback path. 54 tests total in the suite.
