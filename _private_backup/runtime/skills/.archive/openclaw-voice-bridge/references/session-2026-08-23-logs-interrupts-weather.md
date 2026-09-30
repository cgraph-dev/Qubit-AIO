# Session 2026-08-23 — Strict live logs + interrupts + weather/filler/identity

Source: full `events.jsonl` audit 1:17:54–1:38:25 AM (user pasted 1:33:22 "What's my name?" regression, filler spam `Yeah./Okay.`, mangled `Search was the weather in the Corest now.` -> hallucinated 10-day forecast, 38x `tts.synthesize` paste artifact at 1:38:25).

## 1. Live logs: cumulative buffer (vanishing bug)
- **Bug:** `/api/logs?since=` incremental poll replaced panel with last 200 rows. Quiet second -> 0 new events -> overwritten with `(no logs)` — logs "keeps disappearing".
- **Fix (both `frontend/activity.html` + `frontend/index.html`):** Cumulative buffer `logsBuffer` / `talkLogsBuffer` (max 400). Each `since` poll **appends**; empty poll keeps buffer; errors keep buffer; filter `q` still shows last 200 matching (`hasFilter` branch). Reset `logsSince=0; logsBuffer=[]` on Settings open and on filter input.
- **Verify:** Open Gear -> Settings -> Live logs, watch `GET /api/logs?limit=200&since=` polls append; filter `qubit` still works.

## 2. Logs placement (user prefs)
- **Pref:** "move logs in settings", "logs show only when settings are up", "make logs show in talk page too and make it be over connection settings box", "make them keep track non stop when something is happening not with start and pause".
- **Activity:** Removed standalone Logs tab (`activity-tabs`); Logs now lives inside `#settings` panel (`.settings-logs` over activity feed). Gear toggles `#settings` via `hidden` + `is-open` (both needed; CSS has `display:none` + `.is-open{display:block}` + `[hidden]{display:none!important}`).
- **Talk:** Added `talk-logs-body` / `talk-logs-q` / `talk-logs-copy` inside `#settings` **over** `Connection settings` box (filter + Copy, no Pause). Gear toggles `panel.classList.toggle('is-open')`; on open resets `talkLogsSince=0; talkLogsBuffer=[]; refreshTalkLogs()`.
- **Non-stop:** Removed `logsPaused` / `logs-pause` button and `if (logsPaused||!settingsOpen)` guard. While `settingsOpen` poll every 1.5s (`startLogsPoll` / `startTalkLogsPoll`); when closed polling short-circuits but timer stays. No Start/Pause UX.
- **Pitfall:** Activity gear was guarded with `if (btn.closest('.topbar'))` — never matched on Activity page, so Settings never opened. Fixed to unconditional `e.preventDefault(); e.stopPropagation(); setSettingsOpen(!settingsOpen)`.

## 3. Interrupts: always logged + non-blocking (freeze on idle)
- **Bug:** `POST /api/interrupt` while idle hit `not interrupted_anything -> pass` -> nothing journaled ("I don't see logs", "interrupt doesn't work"). When task/TTS active, `_interrupt_task` called `self.speak("Paused. Go ahead.")` **blocking** on HTTP thread -> dashboard freeze + `busy` stuck on idle.
- **Fix `voicebridge/orchestrator.py::_interrupt_task`:** Always `log_event("interrupt.request", announce, had_task, had_playback, was_idle, had_hermes)` + `feed_push({kind:"status", route:"interrupt"})` even when idle (rate-limited 2s to avoid spam). `Paused. Go ahead.` now threaded: `threading.Thread(target=lambda: self.speak(...), daemon=True).start()` so POST returns `{"ok":true}` instantly.
- **Verify:** `curl -X POST /api/interrupt` even idle -> `interrupt.request was_idle:true` in `GET /api/logs` + `status/interrupt` in `GET /api/activity`; `qubit-dev /interrupt` + `/logs` same.

## 4. Weather routing (Bucharest / Corest mangling)
- **Bug:** `what's weather in Bucharest` ASR mangled to `Search was the weather in the Corest now.` -> `routing.template search-web` -> `scripts/web_search.py` DDG returned generic weather-site boilerplate (664 chars) -> summarizer hallucinated `The agent has updated the weather section...`.
- **Fix:**
  - `scripts/weather.py` (wttr.in `?format=j1`, free, no key, handles mangled `corest` -> `Bucharest` fallback, extracts `Bucharest: 29°C (feels 28°C), Clear, wind 10 km/h E, humidity 33%. Today 25-35°C.`).
  - `~/.config/openclaw-voice-bridge/task-templates.yaml`: new `weather-bucharest` template **priority 0** (before `search-web`) matching `bucharest/bucuresti/corest/weather...today...now`.
  - `scripts/web_search.py` weather guard: if query contains `weather|temperature|forecast` try `weather.py` first before DDG.
- **Verify:** `python scripts/weather.py "Search was the weather in the Corest now."` -> `Bucharest: 29°C...`; `POST /api/type "whats weather in Bucharest"` -> `route=local-script:weather-bucharest`, `raw_len 82`.

## 5. Filler + identity regressions
- **Filler spam:** `Yeah./Okay./hi` burned 0.6-0.7s via 3B LLM every 30s, polluted history (1:18:06, 1:32:04, 1:33:29).
  - Fix: `_handle_conversation` early `_filler_re = re.compile(r"^(?:yeah|yep|okay|ok|hello|hi|hey|bruski|...)\b[.!?]*$", re.I)` -> instant `I'm functioning well. What do you need?` / `Sunt bine...` with `_cur_ctx` mirroring (`summary/route/done` + `feed_push` + `log_event("conversation.filler")`) -> 0.25s. Pitfall: must set `_cur_ctx` else `/api/result` times out.
- **Identity:** `What's my name?` at 1:33:22 flipped to `What's your name?` (per-request `_WebCtx` snapshot lost `personal-knowledge.md`).
  - Fix: deterministic hotword `what is my name / what's my name / who am i` -> `Your name is Tricky.` with `_cur_ctx` + `_web_lock` + `conversation.add` + `log_event("conversation.identity")`; also ensured `chat_local(context=_mem_ctx)` fallback.

## 6. Other fixes carried
- `tts.speak` in `_web_mode` now also `log_event(..., provider="web")` + `routing.decision source="mic"` + `openclaw.full_reply/hermes.full_reply/conversation.*.full` + `local.action.exec/result` so `events.jsonl` is single source of truth for `GET /api/logs` = dashboard + `qubit-dev`.
- `voice-dashboard` GIF externalized to `/static/Logo/ascii-magic-4.gif` via `INLINE_GIF` flag.
- Auto-push: `$QUBIT_HOME/scripts/auto-push.sh` now 4 repos; `state.db` excluded via `.git/info/exclude`.

## How to reproduce / verify after pull
```bash
systemctl --user restart qubit-voice-bridge
curl -s http://127.0.0.1:8890/api/status | jq .
# filler: should be local 0.25s, not LLM
curl -s -X POST http://127.0.0.1:8890/api/type -H "Content-Type: application/json" -d '{"text":"Yeah."}' | jq .
curl -s http://127.0.0.1:8890/api/logs?limit=5 | jq '.items[] | .event'
# open Talk -> Gear -> Settings -> Live logs appears over Connection settings; Activity same; filter + Copy work, no Pause, cumulative.
```
