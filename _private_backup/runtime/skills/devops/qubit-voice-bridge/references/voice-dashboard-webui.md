# Voice Dashboard web layer — architecture, speed, pitfalls (Aug 2026)

The bridge serves a web UI (stdlib `http.server`, zero new deps) so the
user talks from any browser (phone or PC) and sees live activity. Frontend
lives in its own repo: `~/Projects/Repos/voice-dashboard`
(github.com/cgraph-dev/voice-dashboard); the bridge serves `frontend/`
from THAT repo when present (edits apply on refresh — no restart needed).

## Pages & API
- `GET /` (index.html) — chat page: PTT button (click-to-talk default,
  hold-mode toggle, persisted in localStorage `vdb_ptt_mode`), typed-task
  input, live activity strip (polls /api/activity every 2s).
- `GET /activity` (activity.html) — live feed with colored badges.
- `POST /api/talk` — raw Int16 PCM @16k mono → returns `{request_id}`
  INSTANTLY (ack), then poll `GET /api/result/<id>` for
  `{text, route, summary, audio_b64}`. Async so the phone hears the ack in
  ~30ms instead of waiting for the whole task.
- `POST /api/type` — JSON `{text}` typed tasks (same pipeline, no ASR).
- `GET /api/activity?limit=N`, `GET /api/status`.
- Auth: optional token (config `webui.token`); browsers send
  `X-Api-Token` header (or `?token=`), CORS headers are on every response
  for a future Vercel-hosted frontend.

## Summarize-first workflow (the "smarter feel")
Recurring tool tasks are `local-script` TEMPLATES: the bridge runs the
tool script itself (not Qubit), captures stdout, feeds it to the LOCAL
3B model (`summarizer.provider: ollama`), and speaks ONLY the ≤40-word
summary — raw tool output never reaches the user's ears (it shows on the
dashboard as `raw` for reference). Templates with `kind: local-script`
carry `script:` (with `{question}` placeholder) + `summary_words:`.
Search example: `scripts/web_search.py` = DuckDuckGo first (~2s, free) →
Perplexity Pro browser fallback (`perplexity_search.py` CDP script) when
DDG has <2 results or user says "use perplexity". Qubit's own web
search provider is `duckduckgo` (free) — never the paid Perplexity API.

## Speed fixes that mattered (4s roundtrip vs 15-18s)
1. **Web requests must NOT play through the PC speakers.** The
   orchestrator's `speak()`/`speak_stream()` blocked on local playback for
   the whole TTS duration. Fix: `_web_mode` flag — when set, speak()
   records text into `_web_spoken` (used as the result summary) and
   returns instantly; the browser plays the returned audio.
2. **Share the bridge's ASR instance** instead of webui loading a second
   parakeet copy (cold load was ~10-15s on the slow SSD). `_transcribe`
   reuses `orch._asr` under a lock.
3. **Async submit/poll** (see API above) — ack ~30ms, result when ready.
4. **DDG first** — the single biggest win: 2s vs 25-38s Perplexity.

## Frontend pitfalls (both bit us in real sessions)
1. **`esc()` must coerce to String first.** `(s||'').replace(...)` throws
   `(intermediate value).replace is not a function` when any feed field is
   a number or object (event-journal mapping passes objects through).
   Correct form: `String(s == null ? '' : s).replace(/[&<>]/g, ...)`.
   Symptom: fetch succeeds but page stays on "waiting for activity…" and
   shows "bridge offline" because render() crashed — looks like a network
   problem, is actually a JS type bug.
2. **Stale saved API URL in localStorage.** If `vdb_api` points at a dead
   URL (e.g. a Pinggy tunnel that rotated/expired), the page shows
   "bridge offline" even though the bridge is fine. Fix pattern: try the
   saved BASE first, fall back to same-origin, and auto-clear/overwrite
   the stale localStorage value after repeated failures.
3. Browser caches the OLD page JS — after editing frontend files, tell the
   user to hard-refresh (Ctrl+Shift+R) once; `Cache-Control: no-store` is
   sent but an already-open tab keeps running the old JS.
4. **Never inline large binary assets as base64 `data:` URIs in HTML.**
   A 30 MB GIF inlined in `frontend/index.html` blew the page to 41 MB
   (40 MB `data:image/gif;base64,…`), killed TTI on phone and swamped the
   30 MB `/static/` GIF that already exists on disk. Fix: serve as a
   normal file (`<img src="/static/Logo/ascii-magic-4.gif" loading="lazy"
   decoding="async">`) and teach the bridge's `WebUIHandler` to send
   correct `Content-Type` for `.gif/.png/.jpg/.svg/.css/.js`; otherwise
   browsers won't cache it. (Fixed 2026-08-22: 41 MB → 290 KB HTML.)
5. **BrokenPipe/ConnectionReset from client disconnect is normal, not an
   error.** Browsers abort fetch on refresh/cancel/navigate; `http.server`
   raises `BrokenPipeError` in both `end_headers()` (header flush) and
   `wfile.write()`. The handler must wrap the **entire** `send_response`
   + `send_header` + `end_headers` + `write` block in
   `except (BrokenPipeError, ConnectionResetError, OSError): pass` — not
   just the `write()` — otherwise every refresh logs a noisy traceback.
   Also add `do_HEAD` for image preloads that probe with `HEAD` (otherwise
   `501 Unsupported method ('HEAD')`).
6. **Static MIME must be correct.** The `/_use_frontend_dir` + `/static/`
   path originally sent everything as `application/octet-stream`, so images
   and CSS didn't cache/render correctly. Branch on extension:
   `.gif→image/gif`, `.png→image/png`, `.jpg/.jpeg→image/jpeg`,
   `.svg→image/svg+xml`, `.css→text/css`, `.js→application/javascript`.
7. **`raw` truncation hides the full output.** `orchestrator.py` capped
   `raw` at `[:500]` before pushing to the activity feed — the dashboard's
   `raw output` detail was a stub. Raised to `[:8000]` for both
   `local-script` and `openclaw` summaries so click-to-expand actually
   shows the real output.

.PTT modes
Click-to-talk (press = start, press again = stop) is the default — works
on desktop mouse AND touch. Hold-to-talk (press+hold, release=send) kept
as an option via the `mode: click/hold` button. The old auto-switch
(120ms timer guessing hold vs tap) was removed as unreliable.

Interrupt-on-tap (barge-in from the dashboard): tap the orb while the
assistant is speaking to interrupt mid-sentence (single tap = interrupt
with "Paused." acknowledgment, double-tap = silent stop like Esc). See
references/voice-dashboard-interrupt.md for the full implementation.

## Deployment status
Localhost-only (user's explicit preference — "I don't need that piggy
thing"). Pinggy tunnel service was REMOVED (2026-08-22): no tunnel, no
`voice-dashboard-tunnel.service`, localhost :8890 only. Vercel deploy of
`frontend/` planned later; `vercel.json` rewrites `/` → index.html,
`/activity` → activity.html. The `qubit` shell command opens the dashboard
(installed at `~/.local/bin/qubit` — a real PATH script, NOT a .bashrc
function, because a login shell reads ~/.profile and skipped .bashrc).
