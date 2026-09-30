# Strict Live Logs — Aug 23 2026

**Problem:** `events.jsonl` + `/api/logs` + `qubit-dev /logs` must be the single source of truth for every route/write/talk, or debugging a bad summary requires re-running the task.

**What was missing (before):**
- `speak()` in `_web_mode` appended to `_web_spoken` and returned without `log_event("tts.speak")` — web TTS never appeared in journal.
- Mic path `_route_intent` pushed `feed_push(kind=routing)` but did not `log_event("routing.decision", source="mic")` — only web path logged decisions.
- Full replies before summarization were not logged: `reply` went straight to `summarize()` then `feed_push(raw)`, but journal only had `summary`.
- Local actions logged `local.action.summary` but not `exec` params or post-execute `result`.

**Fix (voicebridge/orchestrator.py):**
```python
# speak() web path
if self._web_mode:
    self._web_spoken.append(text)
    log_event("tts.speak", text=text[:300], provider="web")
    return True

# mic routing
self.feed_push({"kind":"routing", ...})
log_event("routing.decision", text=text[:200], kind=intent.kind,
          confidence=..., reason=..., source="mic")

# full writes before summarize
log_event("openclaw.full_reply", session_key=..., reply_len=len(reply), reply=reply[:2000])
log_event("hermes.full_reply", reply_len=len(reply), reply=reply[:2000])
log_event("conversation.local.full", text=text[:300], reply_len=len(reply), reply=reply[:2000])
log_event("conversation.fallback.full", ...)
log_event("local.action.exec", action=name, text=text[:160], params=params)
log_event("local.action.result", action=name, result_len=len(msg), result=msg[:2000])
```
Keep `openclaw.full_reply`/`hermes.full_reply`/`conversation.*.full` as log-only (do not map in `webui.py:_event_to_feed_item`) so they appear only in Logs, not Activity feed.

**Dashboard (voice-dashboard/frontend/activity.html):**
- Logs moved from standalone `Activity` tabs into `#settings` panel — only polls/shows when gear is open (`settingsOpen` flag, poll every 1500ms gated by `!settingsOpen`). Hidden by default, remembers open state via `localStorage vdb_settings_open`.
- Any activity summary card is clickable → modal fetches `GET /api/item/:id` when `raw` not in feed (fallback to summary/text/detail).

**Verification:**
```bash
curl -s http://127.0.0.1:8890/api/logs?limit=30 | python -m json.tool | grep -E "tts.speak|local.action.exec|openclaw.full_reply"
qubit-dev /logs 5    # same file: ~/.local/state/openclaw-voice-bridge/events.jsonl
qubit-dev /activity 5
curl -s http://127.0.0.1:8890/api/item/<id> | python -m json.tool
```

**Pitfall:** Do not add new event kinds to `_event_to_feed_item` unless you want them on Activity — `full_reply` events must stay log-only.
