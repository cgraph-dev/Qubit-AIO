# Boss-talk + humming while agent thinks

Source session: add characterful non-blocking voice while coding/qubit task runs.

## Behaviour
- On dispatch to **gateway/CLI/Qubit**, immediately `speak(random.choice(_BOSS_PHRASES))` (ack, `barge=False`) and `log_event("boss_talk", target, phrase, task[:160])`. 10+ variants required: "I'm on it boss", "Starting the task now", "Processing your orders", "You got it boss", "On my way boss", "Working on it now", "Consider it done boss", "Right away boss", "Copy that boss", "At your service boss".
- While the agent task is running, every **8–15s** (uniform random) play a random hum phrase via TTS without blocking the task. 8+ variants: "hmm~", "*whistles*", "still working...", "almost there boss", "still working boss", "still on it boss", "working away here", "hang tight boss", "still cooking boss".
- Use **threading**, stop humming when task finishes. Two loops: `hermes-hum` (guards `self._hermes_busy` + `self._stop`, skips if `playback.playing` or `_interrupt_requested`) and `openclaw-hum` in `_await_run` (non-blocking watcher that clears `_hum_stop` when `self.task.active` clears). First hum delayed 8–15s so boss phrase isn't drowned.
- Each hum: `log_event("humming", phrase, target, elapsed_s?)`.
- Keep funny/characterful but not spammy — the 8–15s jitter + `playback.playing` guard prevents overlap.
- Must not break tests: `python3 -m py_compile voicebridge/orchestrator.py voicebridge/webui.py voicebridge/hotkeys.py` passes; keep `xinput` backend.

## Wiring points in `voicebridge/orchestrator.py`
- `_BOSS_PHRASES` / `_HUM_PHRASES` as module globals, helpers `_boss_phrase()` / `_hum_phrase()`.
- `_launch_task`: gateway branch and CLI branch each pick `boss = _boss_phrase()` before `create_session`/`run_task`.
- `_handle_hermes`: `log_event("hermes.task", ...)` then boss phrase, then `_hum_loop` thread.
- `_await_run`: start `_hum_loop` + `_hum_stop` Event; blocking path joins on `task.active` clear, non-blocking path spawns `_watch_hum_stop`.
- `voicebridge/webui.py:_event_to_feed_item`: map `boss_talk` and `humming` → activity feed so dashboard shows them.
