# Non-Stop Live Logs Contract (Aug 2026)

## User correction
User said: *"make them keep track non stop when something is happening not with start and pause"*.
Pausing/visible-only-when-idle logs are a bug for this product.

## Contract
The **Pause/Resume** button is removed. While **Settings is open**, logs poll `GET /api/logs` every ~1.5s
continuously (via `startLogsPoll` + the cumulative buffer + `formatLog` tail), regardless of whether the
bridge is speaking/thinking/idle. Closing Settings pauses the poll via `if (!settingsOpen) return` in
`refreshLogs`, but there is no in-panel pause state — the stream is always live when visible.

## Where this is encoded
- `voice-dashboard/frontend/index.html` talk logs: `talkLogsTimer` + `refreshTalkLogs` with no `logsPaused` guard.
- `voice-dashboard/frontend/activity.html`: same cumulative pattern, no `logsPaused`.
