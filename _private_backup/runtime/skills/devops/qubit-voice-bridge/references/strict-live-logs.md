# Strict Live Logs & Always-Logged Everything (Aug 2026)

## What strict means
`events.jsonl` (`~/.local/state/openclaw-voice-bridge/events.jsonl`) is the single source of truth for
`GET /api/logs`, the dashboard Settings → Live logs panel, and `qubit-dev /logs /activity`. Every user-visible
branch must `log_event(...)` so fixes can be verified continuously without re-running tasks.

## Full writes vs. summaries
- Summaries are spoken/displayed (`summary`, `trimmed`) but the full agent/LLM text (`reply`) must also be
  journaled so a bad summary is debuggable: `openclaw.full_reply` (before `summarize`), `hermes.full_reply`,
  `conversation.local.full` / `conversation.fallback.full` (with `reply_len + reply[:2000]`).
- Local PC actions: journal both `local.action.exec {action,params}` and `local.action.result {result_len, result[:2000]}` before `local.action.summary`.

## Non-obvious probes to wire
- `speak()` in `_web_mode` appends to `_web_spoken` and returns early without hitting the real TTS path.
  Add `log_event("tts.speak", text=text[:300], provider="web")` inside that branch or web replies vanish from `/api/logs`.
- Mic routing (`_route_intent`) must mirror the web path's `log_event("routing.decision", ..., source="mic")`
  (web path already did `source="web"` via `_process_web_utterance_inner`). Without it, mic-only turns are invisible in logs.
- Explicit qubit delegation wins over templates on both paths — log with `kind="hermes"` `source="web"` / `source="mic"`.

## Endpoints that read this journal
- `GET /api/logs?limit=200&q=&since=<ts>` — `WebUI.logs()` reads the tail of `events.jsonl` (`-max(limit*6,6000)` lines), filters by `q` substring and `since` timestamp, caps to `limit` (500). Token-gated via `_api_token_ok`.
- `GET /api/activity?limit=` — `ActivityFeed.recent()` seeds from the journal at boot (`load_history`).

## Dashboard wiring
- `qubit-dev` slash commands use the same state dir (`Config.state_dir()/events.jsonl` via `logging_setup.setup`):
  `qubit-dev /logs [limit] [q]`, `qubit-dev /activity [limit] [q]`, `qubit-dev /interrupt` (see `voicebridge/terminal.py`).
- Keep `hermes.full_reply` style names stable — grep-based filters in the dashboard rely on them.
