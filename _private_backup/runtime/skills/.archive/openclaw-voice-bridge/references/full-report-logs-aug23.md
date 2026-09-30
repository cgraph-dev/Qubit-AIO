# Full-Report Drill-Down + Live Logs — While Gaming (Aug 23, 2026)

Goal while gaming: voice a task via Right Ctrl or dashboard PTT → Activity shows the resume/summary immediately (`You said: … → Bot: …`, compact 40-word), click it to see the 100% full report from DDG/Perplexity/Qubit/Qubit.

## 100% raw retention

- Clamp raw to `[:50000]`, not `[:8000]` — `voicebridge/orchestrator.py` pushes for `local_script` (`raw`), `local` (`msg`), `qubit`/`openclaw` (`reply`), and `conversation`/`fallback` (`reply`) all use `[:50000]`. Summary stays clamped to `trim_spoken(…, 300–500)` / 40-word.
- `voicebridge/webui.py:ActivityFeed` assigns `id = uuid hex 12` on `push()` if missing, exposes `get(id)`, and `recent()` retains `raw` verbatim. `orchestrator` generates the `id` itself for cross-references.

## Dashboard drill-down

- `voice-dashboard/frontend/activity.html`: every `.activity-summary` is clickable — `.has-raw` cards (`↗ full text` badge) plus a fallback for any card (fetches via `GET /api/item/:id` when `raw` was truncated in history, otherwise shows `summary/text/detail` as raw). Modal `#raw-modal` (860px, backdrop + Esc + Copy) indexes `activityItems` globally across pagination.
- `POST /api/talk` / `POST /api/type` → `GET /api/result/:id` path already returns full `summary`; the feed click uses `GET /api/item/:id` for historical items.

## New bridge endpoints

- `GET /api/item/:id` — lookup `ActivityFeed.get(id)` (token-gated via `_api_token_ok`), `404` when not found. Shares the same token auth as `/api/activity`.
- `GET /api/logs?limit=200&q=&since=<unix ts>` — tails `events.jsonl` (`state_dir/events.jsonl`), filters by JSON contains (`q`) and `ts >= since`, returns `{items: …}` (max 500). Token-gated, bound scan (`limit*6` lines, cap 6000). Backed by `WebUI.logs()` which reads the same journal the dashboard and `qubit-dev` use.
- Dashboard `activity.html` now has **Feed / Logs** tabs (`activity-tabs`, `logs-panel` with filter input, Pause/Resume, Copy, `logsSince` tail cursor), polling `/api/logs` every 1.5s when the Logs tab is active. Tab choice persisted in `localStorage` (`vdb_tab`).

## Terminal parity

- `qubit-dev /logs [limit] [q]` and `qubit-dev /activity [limit] [q]` read the same `events.jsonl` (`cfg.state_dir()`) via `_logs_items()` / `_activity_items()` — no divergent state between dashboard and terminal.

## Verification

- Trigger voice tasks, watch `/api/activity` gain `raw: true` items (check `id` + `len(raw)`), `curl /api/item/<id>` returns the same raw, `curl "/api/logs?limit=3&q=qubit"` returns qubit events, dashboard modal click fetches full text.
- Check `python -m py_compile voicebridge/webui.py voicebridge/orchestrator.py voicebridge/terminal.py`.
