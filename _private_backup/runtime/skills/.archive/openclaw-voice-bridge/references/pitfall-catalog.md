# Voice Bridge bug catalog (2026-08 session)

Each entry: symptom -> root cause -> fix. All fixed and committed in the project
repo (local only, no remote). These are the traps that made the bridge "not
work" for the user; if behavior regresses, check this list first.

## 1. Nothing is ever captured (the "it's not working" blocker)
- Symptom: PTT press produces `mic.utterance.empty` with `ended_by=max_duration`, peak RMS ~0; user's voice never transcribed.
- Root cause: resample math bug. blocksize=480 @48 kHz -> 160 samples @16 kHz per read, but FRAME_SIZE=480 (30 ms). `n_full = len(mono16) // FRAME_SIZE` == 0 every block -> frame loop never ran -> zero audio accumulated. Synthetic-WAV roundtrips passed because they feed whole files, never the streaming path.
- Fix: `FrameSplitter` in `audio/vad.py` buffers partial frames and yields complete 30 ms int16 frames regardless of incoming block size. Used by both capture and the barge-in monitor (which had the identical bug).
- Lesson: the mic path must be verified with a REAL capture test (PTT sim / `voicebridge once`), never just synthetic roundtrips.

## 2. PTT "works once then dies" (2nd/3rd press does nothing)
- Symptom: first press->talk->release->response works; next press gives no beep, no capture.
- Root cause: one-shot flag race. Loop polls `_ptt_started` (a threading.Event), clears it and `_ptt_released` after processing. A press arriving while the loop is busy (agent wait, TTS) sets the flags, then the loop's clear() wipes them -> the press is eaten; or capture starts with release already cleared -> 30 s silent capture.
- Fix (commit 8e56d41):
  - LEVEL-based hold: loop waits on `self._ptt_hold` (bool) and captures until release; stale presses during busy work are dropped cleanly.
  - NON-BLOCKING waiter: `_await_run` spawns `_spawn_waiter` (background thread polling `agent.wait`, 60 s windows up to `task_timeout_s`, then 5 min reaper windows) and returns; the PTT loop stays live during multi-minute agent runs. CLI `task`/`once`/roundtrip keep `blocking_tasks=True`; interactive `run` sets it False.
- Also fixed in the same pass: `log_event("hotkey", event=event, ...)` clobbered the JSONL record name — grep `"event": "hotkey"` found nothing. Use `action=event` (field name) instead.

## 3. "Sorry, I didn't catch that" on PERFECT transcripts
- Symptom: user speaks clearly, whisper transcribes correctly, bridge replies "didn't catch that".
- Root cause: `asr.min_confidence: 0.15` gate. Whisper's avg_logprob for REAL mic speech is ~0.05-0.2 (synthetic piper audio scored 0.27-0.34). The gate rejected valid speech.
- Fix: `min_confidence: 0.02` (~off). Real silence is handled by empty-text + filler-word checks.

## 4. VAD fires on ambient hiss after gain boost
- Symptom: with `mic.gain: 4.0`, webrtcvad aggressiveness 1 calls room noise "speech"; PTT with no speech saves a hiss clip or runs full window.
- Fix: `vad.rms_gate: 0.004` — a frame is "voiced" only if webrtcvad agrees AND frame RMS >= gate (Endpointer + BargeInDetector both). Plus `min_speech_s: 0.25` guard on voiced duration; capture keeps the WHOLE buffer when VAD never entered speech (ASR is the final judge).

## 5. Gateway restarts mid-session
- Symptom: user has to wait; bridge momentarily offline.
- Cause: Qubit's own provisioning (sudo apt/tailscale) can trigger the gateway restart handoff; port 18789 dead ~2 min, systemd restores it.
- Behavior: bridge logs `openclaw.gateway.reader.drop` then auto-`connect` — designed resilience, no action needed. Diagnose with `journalctl --user -u openclaw-gateway` + `~/.openclaw/logs/gateway-restart.log`.

## 6. Segfault (exit 139) in `once` after capture
- Symptom: `voicebridge once` crashed after real speech was captured; standalone capture + ASR both worked.
- Root cause: never reproduced deterministically; best hypothesis = ctranslate2 native crash when VRAM is stolen mid-inference by concurrent GPU work (Ollama model load / the other agent). Not reproduced in 4 later runs.
- Mitigations in place: whisper 3-tier load fallback (cuda fp16 -> cuda int8 -> cpu); run with `python -X faulthandler` so any future native crash prints the C stack.

## 8. Sherpa-onnx NeMo transducer crashes on startup — "Unsupported decoding method: beam_search"
- Symptom: `systemctl --user restart openclaw-voice-bridge.service` → service fails to start, restart counter climbs (3, 6, 9…), journal shows `exit-status=255` and `Unsupported decoding method: beam_search` from `offline-recognizer-transducer-nemo-impl.h:70`. Port 8890 never opens, dashboard unreachable.
- Root cause: sherpa-onnx 1.13.6's NeMo transducer backend accepts only `decoding_method="modified_beam_search"` (not `"beam_search"` or `"modified_beam"`). The string is validated in C++ at `OfflineRecognizerTransducerNeMoImpl` construction time — the first real ASR load triggers the crash. `max_active_paths=4` is the correct companion parameter name (no change needed there).
- Fix: in `voicebridge/asr/sherpa.py`, `from_transducer(...)`:
  ```python
  decoding_method="modified_beam_search",   # NOT "beam_search"
  max_active_paths=4,                        # correct parameter name, unchanged
  ```
- Verification: after restart, `systemctl --user status openclaw-voice-bridge.service` shows `active (running)`; `ss -tlnp | grep 8890` shows port listening; `curl http://127.0.0.1:8890` returns HTTP 200.
- See references/sherpa-asr-tuning.md for the full decoding-method reference table by engine.

## 7. Two agents editing one working tree
- Symptom: "file modified since you last read" warnings; new dirs/commits appear under a different author (trick vs cgraph-dev).
- Practice: `git status --short` + `git diff HEAD -- <file>` before patching; commit only the files you touched (`git add <paths>`); coordinate ownership — personaplex/qubit/vast + duplex lane are Qubit's, audio/asr/tts/safety are the local-stack side.
