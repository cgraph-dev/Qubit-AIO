# Right Ctrl (PTT mic) ↔ Dashboard sync

**Problem (Aug 22):** Right Ctrl hotkey and dashboard (`:8890/activity`) acted as two separate agents. Mic turns (hold Right Ctrl → `_process_utterance` → `_route_intent` → `_handle_*`) never appeared in `/api/activity` while gaming, so Activity couldn't be used to review old conversations. After a bridge restart, even web history was patchy.

**Root cause:** Two pipelines, one feed.

- **Web path:** `webui.handle_talk` / `process_web_utterance` pushes directly to `ActivityFeed` + `events.jsonl` (`webui.utterance`, `routing.decision`). Always visible.
- **Mic path:** `_process_utterance` pushes `routing.input` (`_push_input`) and `routing.decision` via `_route_intent`, but *individual handlers* did NOT push summaries:
  - `_handle_conversation` ✅ pushed `summary` (local)
  - `_handle_agent_result` (Qubit) ✅ pushed `summary` + `raw[:8000]`
  - `_handle_local` ❌ spoke only — no feed, no journal
  - `_handle_hermes` ❌ background thread spoke only
  - `_handle_system` ❌ spoke only (`status/mute/help/...`)
  - `_dispatch_local_script` truncated `raw[:500]`

Live `ActivityFeed` is in-memory; `load_history()` rehydrates from `events.jsonl` via `_event_to_feed_item`. Missing event types → invisible after restart.

**Fix (Aug 22-23):**

`orchestrator.py`:
- `_handle_local` now: `log_event("local.action.summary", action, text, summary)` + `feed_push({"kind":"summary","route":"local","text":f"local:{name}","summary":msg[:300],"raw":msg[:8000]})`
- `_handle_hermes` background `_run()` now: `feed_push(kind=summary, route=qubit, text="Qubit task", raw=reply[:8000])` + `log_event("hermes.summary")` + `_last_web_summary = summary` (keeps web poll consistent)
- `_handle_system` now: `log_event("system.command")` + `feed_push(kind=status, route=system)`
- `_a_system_stats` psutil import moved *inside* try (was top-level in function → ImportError killed fallback)
- `raw` truncation raised `[:500]` → `[:8000]` in both `_dispatch_local_script` and `_handle_agent_result`

`webui.py` — `_event_to_feed_item` now maps on restart:
```
local.action.summary → {kind: summary, route: local}
hermes.summary       → {kind: summary, route: qubit}
system.command       → {kind: status,  route: system}
hermes.result.discarded → {kind: status, route: qubit}
```

`psutil` installed into `.venv` via `uv pip install psutil` (was missing → fallback path never reached).

**Verified:**
`POST /api/type {"text":"system stats"}` → `/api/activity` now contains `local:system_stats` with full raw immediately (2s poll). `events.jsonl` tail shows `local.action.summary` → survives restart. Dashboard click-to-raw modal (see `dashboard-activity-raw-modal.md`) shows the 8k raw.

**Rule for future handlers:** *Every* mic/web handler that speaks must also `feed_push` (live) *and* `log_event` (persist) with a type that `_event_to_feed_item` maps. Otherwise it will be invisible in Activity — especially while gaming on a separate screen.

See also: `voice-dashboard-webui.md` (feed architecture), `dashboard-activity-raw-modal.md` (summary→raw UX).
