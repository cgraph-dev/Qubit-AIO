# Interrupt Freeze & Always-Logged Interrupts (Aug 2026)

## Symptom
- Dashboard tap → `POST /api/interrupt` froze the UI; `busy` stayed stuck on idle.
- Idle interrupts produced no journal entry — Settings → Live logs and `qubit-dev /logs` showed nothing ("interrupt doesn't work").
- Rapid double-taps queued two `Paused. Go ahead.` TTS blocks.

## Root cause
`_interrupt_task(announce=True)` called `self.speak("Paused. Go ahead.")` **blocking** on the HTTP handler thread (Piper synth ~200ms + `playback.play` busy-wait). The captured `had_task/had_playback/was_idle` path also returned early on idle with `pass` (no log).

## Fix
1. **Always journal** `interrupt.request` even when idle, with fields `announce, had_task, had_playback, was_idle, had_hermes`; also push `status/interrupt` to the Activity feed. Idle interrupts are rate-limited to 2s to avoid spam from a stuck poll loop.
2. **Non-blocking ack:** `Paused. Go ahead.` is now spawned in a daemon thread (`_th.Thread(target=lambda: self.speak(...), daemon=True)`) so `/api/interrupt` returns `{"ok":true}` instantly — no TTS blocking on the request.
3. **Playback remains preemptible:** `Playback.play()` already does `sd.stop()` + `_stop.set()` under lock; barge-in monitor is torn down via `stop` Event.

## Verification
- `curl -X POST /api/interrupt` while idle → `interrupt.request` + `status/interrupt` appear in `GET /api/logs?limit=5` and `GET /api/activity?limit=6` and `qubit-dev /interrupt` + `qubit-dev /logs`.
- `qubit-dev /interrupt` returns JSON instantly (<50ms) even under load.

## Files
- `voicebridge/orchestrator.py:_interrupt_task` (non-blocking ack, always-log, rate-limit, feed push)
- `voicebridge/webui.py:WebUI.interrupt` (unchanged, now fast)
