# Qubit dispatch observability — every path must journal

Source session: `voicebridge/orchestrator.py` "route through open claw and create a text document..." only showed `safety ask` but no `openclaw.dispatch/error` and a later systemd crash produced no `openclaw.*` log.

## Required events (events.jsonl) — all must emit via `log_event`
- `safety.verdict` — at entry of `_dispatch_coding` (allowed/deny/require_confirm/dry_run)
- `openclaw.dispatch` — when `_launch_task` builds `session_key` (task[:300], model, route)
- `openclaw.request` — after `create_session`/`send_message` with `run_id`
- `openclaw.request.failed` / `openclaw.request.exception` — on `GatewayError` / generic exception from session creation
- `openclaw.status` — heartbeat: every `progress_loop` tick and every `wait_run` window in `_spawn_waiter`/`_await_run` (elapsed/remaining), so a crash mid-wait still leaves a trail
- `openclaw.result` — terminal status (`ok`/`failed`/`aborted`/`timeout`)
- `openclaw.error` — on ANY exception in `_dispatch_coding`, `_launch_task` (CLI `run_task`), and `_spawn_waiter` `wait_run` failures
- `hermes.task` — at entry of `_handle_hermes` (task[:300])
- `hermes.error` — on `TimeoutError` / generic exception in Qubit path
- `openclaw.progress` — spoken progress after 30s

## Structural fix
Wrap `_dispatch_coding` in `try/except` → `log_event("openclaw.error"/"hermes.error", task, error)` + `speak("Something went wrong dispatching the task...")`. Never let an exception before `openclaw.dispatch` go silent. In `_launch_task` CLI path, wrap `cli.run_task` with `openclaw.error` + Qubit failover. In `_spawn_waiter`, log `openclaw.status` before each `wait_run` and `openclaw.error` alongside `openclaw.result` on failure.

## Dashboard visibility
`voicebridge/webui.py:_event_to_feed_item` must map `openclaw.error/status/progress`, `hermes.error/task`, `boss_talk`, `humming` → activity feed items, and `ActivityFeed.load_history(events.jsonl)` must surface them after restart. Without this, `events.jsonl` has the data but `GET /activity` stays blank.

## Verification
- `python3 -m py_compile voicebridge/orchestrator.py voicebridge/webui.py voicebridge/hotkeys.py` passes
- `events.jsonl` contains the sequence for every coding intent: `safety.verdict → openclaw.dispatch → openclaw.request → openclaw.status* → openclaw.result` OR `openclaw.error`/`hermes.error` on crash
- Kill gateway mid-task → still see `openclaw.status` + `openclaw.error` (no silent systemd restart)
