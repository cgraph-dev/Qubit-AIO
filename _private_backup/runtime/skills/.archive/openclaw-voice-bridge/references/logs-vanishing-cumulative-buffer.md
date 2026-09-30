# Logs Vanishing — Cumulative Buffer Fix (Aug 2026)

## Symptom
Opening Settings → Live logs would show events, then blank to `(no logs)` after the next poll while idle.

## Cause
The panel fetched incrementally via `&since=<maxTs>` but **replaced** `textContent` with only the newest batch.
When nothing happened, the server returned 0 items and the panel overwrote the visible buffer.

## Fix
Both Activity (`activity.html`) and Talk (`index.html`) now keep a **cumulative buffer** (up to 400 events) while
Settings is open:
- Fetch `&since=logsSince`; on success **append** `items` to `logsBuffer` and render the buffer.
- Empty polls keep the buffer; errors do not blank it.
- Filter mode (`?q=`) still renders the server's last 200 matching as a fresh view (no append).
- On Settings open: reset `logsSince=0, logsBuffer=[]` and do an immediate poll; `startLogsPoll` ticks `refreshLogs` every 1.5s.
