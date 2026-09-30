# Non-crashing task waiter — salvaged partial replies (Aug 2026)

Source session: `openclaw.*` invisible on crash — only `safety ask` logged before systemd restart.

## Salvage on `wait_run` failure
In `voicebridge/orchestrator.py: _spawn_waiter._waiter`, both `GatewayError` and generic `Exception` branches now try `_fetch_final_reply(session_key, run_id)` before declaring `failed`:
```python
salvaged = ""
try: salvaged = self._fetch_final_reply(session_key, run_id) or ""
except: salvaged = ""
if salvaged.strip():
    log_event("openclaw.result", ..., status="partial", error=... )
    self.task.clear()
    self._handle_agent_result(session_key, salvaged, "partial", time.time()-t0)
    return
```
Without this, a task that already wrote a useful report shows as `failed` with no raw and no summary. With it the dashboard gets the partial raw + a 40w summary via the normal `_handle_agent_result` path (which persists to `<state_dir>/reports/` and verifies).

## Required event sequence
Every coding intent must journal: `safety.verdict → openclaw.dispatch → openclaw.request → openclaw.status* → openclaw.result` **or** `openclaw.error/hermes.error` on crash — verified in stress tests. Heartbeats (`openclaw.status` per wait window) prove the task was alive before the crash.
