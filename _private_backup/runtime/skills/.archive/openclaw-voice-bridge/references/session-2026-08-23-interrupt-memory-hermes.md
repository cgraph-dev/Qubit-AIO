# Session 2026-08-23 — Dashboard Interrupt, Qubit Memory & Qubit Routing Hardening

Two user asks in one session: (a) dashboard tap while "thinking/speaking" must interrupt instantly (tap=start, tap=stop, tap-while-busy=interrupt), never miss; (b) long tasks (20 min) must not crash the local 3.6B judge, and Qubit must actually remember "I am Qubit, user is Tricky."

## 1 — Interrupt: `busy` guard ordering bug

**Symptom:** tap while agent was polling (`busy=true`, `setTimeout 900ms` loop) did nothing.
**Root:** `assistantActive()` only checked `currentAudio !== null`; handlers did `if (busy) return` before the interrupt check.
**Fix (`voice-dashboard/frontend/index.html`):**
- `assistantActive() { return currentAudio !== null || busy; }`
- Reorder `pointerdown`/`click`/`touch` so `if (assistantActive()) { interrupt(); return; }` runs **before** any `if (busy) return`.
- Touch path keeps `touchSwipe` guard only on the toggle (non-interrupt) leg.
- Backend: `WebUI.interrupt()` → `orchestrator._interrupt_task(announce=)` still the single abort path (gateway `abort` + `playback.stop()` + `hermes_busy` discard).

**Verification:** `POST /api/interrupt {"mode":"interrupt"}` → `{"ok":true}` even with `web_active:1`; next tap starts recording immediately.

## 2 — Polling snappiness

Local chat answers in 0.4–0.8 s but dashboard polled every 900 ms → extra perceived lag.
**Fix:** adaptive poll — first 2 polls at 320 ms, then 900 ms (both `sendAudio` and `sendTyped` loops). Implemented via `let pollN=0; setTimeout(pollN++<2?320:900)`.

## 3 — Qubit/Tricky memory (small LLM, no bigger model)

Local model is `huihui_ai/llama3.2-abliterate:3b` (3.6B, 2.5 GB, `keep_alive -1` pinned to `expires_at 2318`). It only knows what we inject.

**Patches:**
- `~/.config/openclaw-voice-bridge/personal-knowledge.md` (and repo `config/personal-knowledge.md`): new Identity block — "assistant is Qubit, developer is Tricky (cgraph-dev)". Injected every turn via `orchestrator._load_memory_context()`.
- `voicebridge/local_llm.py:SYSTEM_PROMPT` prefix: `You are Qubit, Tricky (cgraph-dev)'s warm voice assistant... Remember: you are Qubit, your human is Tricky, main project is CGraph.`
- `ConversationState.max_turns` 4→8 (`voicebridge/intent/state.py` + `orchestrator.py` default) — 8-turn rolling memory with per-request `conv_snapshot` isolation so concurrent EN/RO don't bleed.

**Probes:**
- `Who are you?` → `I'm Qubit...` PASS
- `Who is Tricky?` → `Tricky is ... CGraph ...` PASS
- `Remember my favourite color is teal.` → `I've noted teal.` (short-term, same session). Daily `memory/YYYY-MM-DD.md` exists for cross-session but not auto-written from chat yet.

## 4 — Qubit vs Qubit routing (template shadowing)

**Symptom:** `ask qubit to organize my inbox` matched `config/task-templates.yaml:check-email` (`(check|read|...)(my|the )?(email|inbox...)->coding`) before the classifier's `HERMES_PATTERNS`, so it routed to Qubit/coding.
**Fix (`voicebridge/orchestrator.py`):** check `classifier._hermes_request(text)` **before** any `TaskTemplates.match` in both `_route_intent` (mic) and `_process_web_utterance_inner` (web/dashboard). Web qubit path now also waits on `ctx` (`_web_wait_for_summary()` checks `ctx.summary` first, then legacy global) and mirrors summary into `WebCtx` so `/api/result` prefers isolated state.

Classifier probe: `ask qubit to organize my inbox` → `kind=qubit:0.9`; `fix the cgraph message sanitizer` → `kind=coding:0.7` (no qubit hit).

## 5 — Long-task supervision (20 min)

- `task_timeout_s: 900` already; web `task_wait_s` aligned from 300→900 so dashboard poll doesn't timeout at 5 min.
- `keep_alive: -1` (Ollama `expires_at 2318`) — 3B stays resident; no eviction during 20-min agent run. Watcher uses 60 s windows up to timeout then 5-min reaper windows (`voicebridge/orchestrator.py:_spawn_waiter`).

## 6 — Perplexity research (Aug 2026) — Aug 2026 best practices synthesized

Used `PERPLEXITY_API_KEY` via `sonar-pro`:

- Supervisor = scheduled session heartbeat + polling canonical state (streaming is UX overlay) + `OLLAMA_KEEP_ALIVE=-1` resident during task window + watchdog reaper (5–10 min checks, 15 min reap window, "loaded but idle + high GPU" = hung). Our `web_search.py` already does DDG-first (~2 s) → Perplexity Pro browser fallback.
- Speaker adaptation for Sherpa Parakeet-TDT/Moonshine + Silero VAD: fixed 16 k capture, steady gain 8.0, per-user RMS/noise floor, 5–15 s enrollment clip → ECAPA/x-vector (bridge has eres2net `threshold 0.65 / window 2.0 s` already enrolled), plus per-user hotword bias list (Qubit, CGraph) fed to decoder.

## 7 — Remaining probe

- `state.db` 170 MB rejection handled prior (hermes-home push excluded via `.git/info/exclude`).
- `qubit task` via `HermesBackend.run_task(["hermes","chat","-q",..., "--continue","voice"])` still intermittently returns `code 1, error ""` — likely session `voice` not warm yet; routing fix is correct, follow-up is to warm the Qubit voice session (`qubit chat --resume voice` once) or surface stderr in `hermes.task.error`.
