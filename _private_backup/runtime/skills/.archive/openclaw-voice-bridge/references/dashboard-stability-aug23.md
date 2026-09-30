# Dashboard stability & perf fixes (Aug 23)

**Two stability bugs that made the dashboard unusable:**

1. **41 MB inline GIF** — `voice-dashboard/frontend/index.html` inlined `ascii-magic-4.gif` as `data:image/gif;base64,...` (≈31 MB decoded, 41 MB b64). Every page load fetched 40 MB of HTML, blocked TTI on phone, and was re-served on every refresh.
   - **Fix:** `src="data:image/gif;base64,..."` → `src="/static/Logo/ascii-magic-4.gif" loading="lazy"` . `webui.py` static handler now serves with correct MIME (`image/gif` / `image/png` ...) and `HEAD` support for browser preload. HTML is now 290 KB; GIF is cached separately (30 MB once). Path: `frontend/Logo/ascii-magic-4.gif` under `voice-dashboard` repo; also `logowhite/black.png`.
   - **Rule:** Never inline large media as base64 in served HTML. Serve from `frontend/` via `/static/` — that's what `WebUIHandler` is for.

2. **`BrokenPipeError` double-trace on refresh/cancel** — `WebUIHandler._send` only wrapped `wfile.write(body)`; `send_response`/`end_headers` also throw `BrokenPipe`/`ConnectionReset` when the client navigates away (refresh, close tab). The `except` in `_file` then tried `_send(404)` on a dead socket → second traceback, spamming `journalctl`.
   - **Fix:** Wrap *entire* `_send` (headers + body) in `try: ... except (BrokenPipeError, ConnectionResetError, OSError): pass` — client disconnect is not an error. Added `do_HEAD` for `/static/*` so image preload doesn't 501 and trigger fallback.
   - **Verified:** `journalctl` shows 0 `BrokenPipe` since restart; `curl` HEAD/GET for gif returns 200 with correct `Content-Type`.

3. **`raw` truncation `[:500]`** — `orchestrator._dispatch_local_script` and `_handle_agent_result` pushed `raw[:500]` to the feed, so Activity's click-to-raw modal (see `dashboard-activity-raw-modal.md`) showed a stub. **Fix:** `[:8000]` — summaries are still spoken as 35-40 words, but full output is recoverable via click. Paired with `psutil` install in `.venv` (`uv pip install psutil`) — `_a_system_stats` fallback was dead because `import psutil` was outside try.

See also: `right-ctrl-dashboard-sync.md` (Right Ctrl → dashboard feed sync), `voice-dashboard-webui.md` (feed + MIME + token + CORS).
