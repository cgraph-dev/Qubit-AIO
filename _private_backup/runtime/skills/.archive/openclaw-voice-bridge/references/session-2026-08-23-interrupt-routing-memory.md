# Session 2026-08-23 — Interrupt, Routing, Memory & Supervisor

## Interrupt (dashboard tap)
- Bug: `assistantActive()` only checked `currentAudio !== null`. While thinking (`busy=true` polling every 900ms) tap was blocked by `if (busy) return` before interrupt check.
- Fix: `assistantActive() => currentAudio !== null || busy`; reorder `pointerdown`/`click` handlers so `if (assistantActive()) { interrupt(); return; }` runs **before** any `if (busy) return` guard. Touch path needs same priority.
- Polling: keep 900ms but add adaptive 320ms for first 2 polls after submit — local replies (0.4-0.7s) feel instant. No extra server cost.
- Bridge side: `_interrupt_task()` already handles Ctrl mid-task; `/api/interrupt` is token-gated same as `/api/activity`.

## Routing (Qubit vs Qubit vs Templates)
- Bug: `task-templates.yaml:check-email` (`(check|…)(my|the )?(email|inbox…) → coding`) shadowed `ask qubit to organize my inbox` — template matched before classifier, so `_hermes_request()` never ran. Probe showed `ask qubit … → check-email/coding` instead of `qubit:0.9`.
- Fix: In both `_route_intent` and `_process_web_utterance_inner`, check `classifier._hermes_request(text)` **before** `self._templates.match(text)`. Explicit `ask qubit / use qubit / qubit agent` wins. Verified: `fix the cgraph … → coding`, `ask qubit … → qubit`.
- Also: keep dashboard `ask_executor=false` (no "Qubit or Qubit?" prompt on web) — website is the Qubit surface.

## Memory (Qubit remembers Tricky)
- Local 3B was stateless: `ConversationState.max_turns=4` too short, `SYSTEM_PROMPT` generic, `personal-knowledge.md` lacked identity.
- Fix: `intent/state.py` + `orchestrator.py` default `max_turns 4→8`; `local_llm.py SYSTEM_PROMPT` now opens as `You are Qubit, Tricky (cgraph-dev)'s warm voice assistant … Remember you are Qubit, human is Tricky, main project CGraph`; `~/.config/openclaw-voice-bridge/personal-knowledge.md` (and repo `config/personal-knowledge.md`) got an Identity block (Qubit/Tricky). Verified: `Who are you? → I'm Qubit…`, `Who is Tricky? → developer CGraph`.

## Long-task supervisor (20-min, no crash)
- Perplexity `sonar-pro` research (Aug 2026, via `$QUBIT_HOME/.env:PERPLEXITY_API_KEY`): heartbeat as scheduled session turn, polling canonical state not streaming, `OLLAMA_KEEP_ALIVE=-1` pinned during task window, watchdog reaper (5-10m checks, 15m reap, `loaded+high GPU` = hung).
- Applied: `ollama` model `huihui_ai/llama3.2-abliterate:3b` pinned (`expires_at 2318-12-03`), `task_timeout 900s` aligned with `webui task_wait_s 300→900`, waiter reaper `300_000ms` windows, `keep_alive` resident. `web_search.py` DDG-first (~2s) → Perplexity fallback stays.

## Voice understanding (single-user adaptation)
- Perplexity checklist: fixed 16k capture + steady gain, per-user RMS/noise floor, 5-15s enrollment → ECAPA/x-vector embedding (bridge already has `eres2net` voice-ID, threshold 0.65/window 2.0s), bias list for `Qubit/CGraph` hotwords to ASR decoder.

## Repro
- `qubit-dev /status` → `dashboard: bridge=up`, `ollama: … keep_alive …`
- `qubit-dev "ask qubit to …"` → route qubit vs `qubit-dev "fix the cgraph …"` → coding
- Concurrent EN/RO still 8/8 PASS after fixes.
