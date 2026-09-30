# Voice Dashboard — web UI for the Qubit voice bridge

Built 2026-08-22. The bridge's web UI ("voice dashboard") lets the user talk
from any browser (phone included) and watch live activity summaries. This
file is the condensed session detail; the umbrella SKILL.md carries the
pitfall summary.

## Repos
- Bridge: `~/Projects/Repos/openclaw-voice-bridge` (github cgraph-dev/openclaw-voice-bridge)
- Frontend (user-editable, Vercel-ready): `~/Projects/Repos/voice-dashboard`
  (github cgraph-dev/voice-dashboard) — `frontend/index.html` (chat) +
  `frontend/activity.html` (feed) + `vercel.json` + `open.sh` launcher.
- The bridge's `WebUI.__init__` prefers serving from the voice-dashboard repo
  (`Path.home()/Projects/Repos/voice-dashboard/frontend`), falls back to the
  bridge's own `frontend/`, then embedded page strings. Edits apply on refresh.

## API contract (port 8890)
- `POST /api/talk` — body = raw Int16 PCM @16k mono. Returns `{request_id}`
  immediately (worker thread does ASR → route → summary → TTS).
- `GET /api/result/<id>` — poll: `{status: pending|done|unknown, ...result}`.
- `POST /api/type` — JSON `{text}`. Same pipeline, no ASR step.
- `GET /api/activity?limit=N` — feed items (newest first): kinds user/routing/
  status/summary/done/fail, fields text/route/conf/summary/raw/latency_s/ts.
- `GET /api/status` — `{bridge, route, task_active, port, uptime_s}`.
- Auth: `webui.token` config → `X-Api-Token` header or `?token=` query on all
  `/api/*` routes; empty token = open on localhost/LAN. CORS `*` on everything
  (needed for the Vercel-hosted frontend calling the PC API cross-origin).

## Frontend wiring
- `BASE` = API origin: same-origin by default; override via `?api=` query or
  localStorage `vdb_api` (set through the ⚙ gear).
- `API_TOKEN` = `?token=` or localStorage `vdb_token`; sent as X-Api-Token.
- Mic capture: `getUserMedia` → `AudioContext({sampleRate:16000})` →
  ScriptProcessorNode → Int16 PCM chunks → POST binary. No codecs, no ffmpeg.
- PTT: explicit mode selector (`mode: click` default = tap-to-toggle; `mode:
  hold` = press-and-hold). Persisted in `vdb_ptt_mode`. The old 120ms
  hold-vs-tap auto-guess confused users — an explicit button is better.
- Text input: `POST /api/type`, Enter to send.

## Backend architecture (webui.py)
- `ThreadingHTTPServer` + `BaseHTTPRequestHandler` subclass. One `WebUI`
  instance owns the feed, pending/results dicts, and the ASR/TTS handles.
- Async pattern: `submit_talk(pcm)` puts a request in `_pending`, spawns a
  daemon worker thread that runs the full pipeline, stores the result in
  `_results`, prunes to last 50. The HTTP handler never blocks on slow work.
- `handle_talk` → `_transcribe` (reuses `orch._asr` when loaded — the bridge's
  parakeet instance has an internal lock; a private copy costs ~10s cold load
  on the slow Kingston SSD) → `orch.process_web_utterance(text)` → synthesize
  summary via the shared piper TTS → base64 WAV.

## Orchestrator web-mode integration
- `process_web_utterance(text)` sets `_web_mode=True`, resets `_web_spoken`,
  runs `_process_web_utterance_inner` (template match first, then classifier —
  same routing as the mic), restores `_web_mode` in a finally.
- `speak()` and `speak_stream()` check `self._web_mode`: append the text to
  `_web_spoken` and return instantly — NEVER play through PC speakers (that
  blocked web roundtrips to 15-18s; with the fix, 3.5-4.5s).
- `_dispatch_local_script(tmpl)` — summarize-first: runs `tmpl.script`
  (shell, `{question}`/`{text}` placeholders) with `webui.script_timeout_s`
  cap, feeds stdout to `summarizer.summarize()` (ollama provider with the
  bridge's chat model, e.g. `huihui_ai/llama3.2-abliterate:3b`), speaks ONLY
  the ≤summary_words summary. Raw output → feed (dashboard reference only).
- `_web_wait_for_summary(timeout)` — for coding/qubit dispatches from web,
  block the request until the agent result lands (`_handle_agent_result` sets
  `_last_web_summary`) or `webui.task_wait_s` (default 300) expires.
- `ask_executor` is skipped when `_web_mode` — the website is the Qubit
  control surface; asking who should run a task over the phone UI hangs the
  request (waits for a spoken answer that never comes).

## Search speed ladder (scripts/web_search.py)
1. DuckDuckGo via `ddgs` lib (bridge venv) — ~1.5-3s. Returns titles+snippets
   as a text answer. Thin results (<2) → fall through.
2. Perplexity Pro browser session (perplexity-web-search skill) — ~25-38s but
   deep. `--perplexity` forces it.
Template `search-web` is `kind: local-script` invoking
`.venv/bin/python scripts/web_search.py "{question}" 1500`.

## Activity feed persistence
- `ActivityFeed` (deque ring, maxlen 200, newest-first) is in-memory; a bridge
  restart wipes it. `load_history(events.jsonl, limit=200)` seeds the ring at
  startup from the last 4000 journal lines via `_event_to_feed_item` (maps
  routing.input/decision/template, webui.utterance, local_script.summary,
  openclaw.dispatch/result/summary/failures, conversation.local…).
- The dashboard therefore survives restarts and shows pre-restart activity
  (observed: agent-driven bridge restarts during a "fix the activity page"
  task wiped the in-memory feed, making the page look dead).

## Classifier lesson (intent/classifier.py TASK_HINTS)
`\bfolder\b` does NOT match "folders" — word boundaries are plural-hostile.
Patterns must list plurals explicitly (folder|folders, repo|repos,
issue|issues, directory|directories…). Also add list/show/find/search/count/
report/describe as task verbs. Symptom: "list the top-level folders in
~/Projects/Repos…" scored 0 and routed to chitchat (conversation, empty
summary) instead of coding.

## Tunnel / outside access
- `scripts/voice-dashboard-tunnel.sh`: `ssh -p 443 -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null -o ServerAliveInterval=30 -R0:localhost:8890
  free@a.pinggy.io`, loops forever (restart on 60-min free-tier expiry),
  writes current URL to `~/.local/state/openclaw-voice-bridge/tunnel.url`.
  URL regex must cover `.pinggy.link`, `.pinggy-free.link`, `.pinggy.net`.
- systemd: `voice-dashboard-tunnel.service` (After=openclaw-voice-bridge.service,
  Restart=always, MemoryMax=64M, CPUQuota=10%, Nice=15, IOSchedulingClass=idle).
- Bridge service guardrails: `MemoryMax=2G MemoryHigh=1.5G Nice=-5`
  (bridge process ~950MB; the LLM lives in ollama, not this process).
- Vercel: `vercel --prod` in the voice-dashboard repo; the page works with
  `?api=<tunnel-url>&token=<secret>` or the ⚙ gear (localStorage). CORS is
  handled by the bridge.

## Shell command lesson
`.bashrc` functions are silently skipped by login shells that read
`~/.profile` first (observed: `qubit` function in .bashrc → "command not
found" in a real terminal while `bash -i -c 'type qubit'` worked). Fix:
install user commands as executable scripts in `~/.local/bin` (already on
PATH) — works in every shell, no sourcing needed.

## Verified timing (RTX 3070 Ti 8GB, this machine)
- ack (submit→request_id): ~27ms
- local search roundtrip: 3.5-4.5s (DDG fast path)
- public tunnel roundtrip: ~4.9s
- Qubit coding task via web: ~14-16s incl. agent run
- TTS (piper): ~0.2-1s; ASR (parakeet CPU): ~0.1s warm
