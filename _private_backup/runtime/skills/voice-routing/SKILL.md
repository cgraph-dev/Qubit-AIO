---
name: voice-routing
description: How the voice bridge routes utterances to the right lane (coding vs conversation vs local) without burning the small model. Templates beat LLMs.
---

# Voice Routing — How the Bridge Decides

## Priority (highest first)

1. **Pending confirmation** — "yes/do it/apply" vs "no/cancel" when a destructive task asked for confirmation.
2. **Filler fast-path** — `hello/yeah/hi/hmm` → instant canned reply, `_route_intent` + `process_web_utterance` short-circuit to `routing.filler_fast` **before** `Classifier.classify()` (saves 0.5–4.8 s LLM). Never let `Yeah.` hit `intent.llm` (`huihui_ai/llama3.2-abliterate:3b`). Sticky routing and templates also bypass the LLM.
3. **Sticky routing** — `route to qubit/openclaw/browser` persists to `sticky_route.json` (8 turns / 10 min, survives reboot); `clear route` resets. Checked before templates.
4. **Explicit Qubit delegation** — `ask Qubit to ...`, `Qubit, ...`, `route to Qubit` → qubit lane (outranks templates).
5. **Task templates** (`config/task-templates.yaml`) — deterministic regex → known task text. Zero tokens, instant. This is the user'''s daily tasks (weather, inbox, cgraph status, git sync, ...). Edit this file instead of tuning prompts.
6. **System** — status/mute/stop/cancel/continue/model/quit.
7. **Classifier** — TASK_HINTS regex → `coding` (open/inspect/patch/test/fix/build/run/review/commit...), else `conversation` / `local` / `clarify`. LLM assist is opt-in (`intent.llm_classifier: true`, ~0.3–0.6 s) but filler and sticky already avoided it. Intent LLM now defaults to resident `huihui_ai/llama3.2-abliterate:3b` (≈0.5 s) not `qwen3-vl:8b` (≈4.8 s on `Yeah.`).
8. **Default → conversation** — safe. Short filler ("yeah", "hello") is handled locally without calling the LLM at all.

## Qubit is the default worker (2026-08-23)

`hermes.prefer: true` in `~/.config/openclaw-voice-bridge/voice.yaml` (`voicebridge/config.py` `DEFAULTS["hermes"]["prefer"]`). When `HermesBackend.available()` and `prefer` is true, `_dispatch_coding` routes every `coding` intent to `self._handle_hermes(final_text)` unless `sticky_route` targets `openclaw` or the task needs heavy browser `1:1 pixel-perfect / CAPTCHA bypass` work. Qubit is now strictly the **fallback** — Qubit timeout/failure → `log_event("hermes.error")` + fallback, not primary. `openclaw.ask_executor` is `false` (was `true` blocking on `Qubit or Qubit?`). Verify live: `curl /api/status` shows `sticky_route`, `grep routing.prefer ~/.local/state/openclaw-voice-bridge/events.jsonl`. Say `route to openclaw` for a one-off Qubit job, `clear route` to return to Qubit default.

## What to do when you add a daily task

Add a template — do NOT try to teach the 3B model a new phrase via prompt. Example:

```yaml
- name: my-task
  match: ["my spoken phrase (.*)"]
  kind: coding              # coding | qubit | conversation | local-script
  task: "Do X in ~/Projects/... Report per professional_report.md ..."
  ack: "On it."
```

Use `local-script` when a shell command + summarize-first (≤N words) is enough (web search, weather).

## Sticky routing — the local brain forgets (fixes 'route to qubit' amnesia)

Saying `route to qubit` must stick. The 3B local LLM is stateless per turn — without help, the *next* utterance reverts to the default lane. The bridge now has durable sticky routing:

- Say `route to qubit` / `route to openclaw` / `route to browser` — persists to `sticky_route.json` (survives reboot/provider switch), auto-expires after 8 turns / 10 min. Say `clear route` to reset.
- `StickyRouteState` + `Classifier._sticky_route()` + `_route_intent` apply the sticky target before templates/LLM. `/api/status` exposes `sticky_route` so dashboards badge it. `/api/reports` includes shared ledger so switching agents doesn't blank the feed.
- Web path (`process_web_utterance`) respects the same sticky. Local LLM prompts also carry a `Active routing preference: route to X` hint via `_mem_ctx` so the brain sees it.
- Keep Qubit sticky explicit — don't infer it from vague phrasing. Only the `STICKY_PATTERNS` trigger persistence.
- Pitfall: `route to qubit and do X` in one utterance — strip the routing prefix, persist, then dispatch the remainder as the task (don't just ack).
- Pitfall (ASR quality, 2026-08-23): short noisy captures like `"Hey."` / `"Mm."` classify as `conversation`/`filler` — that's mic/VAD, not the model. Don't paper over it with routing tricks. Check `tail events.jsonl | grep asr.transcribe` for `chars 3-5` and `asr_ms 50-80`. Fix is `mic.gain`/`vad.rms_gate` and holding Push-to-Talk ~0.5 s longer, not a new sticky rule.

## Durable shared memory (survives power loss + provider switches)

`SharedMemory` at `<state_dir>/shared_memory.jsonl` is the cross-provider ledger (append-only, `fsync` per line). Every user utterance, routing decision, and agent report (full + summary) lands there. On boot it replays into `ConversationState` so context returns after the PC shut off.

- Qubit and Qubit tasks both `append_agent_report` + inject `as_prompt()` into the *next* task — so neither dashboard blanks when you switch lane.
- Includes VS Code workspace capture (`VSCODE_CWD` / `TERM_PROGRAM=vscode` → `append_vscode`). Also bridged into `/api/reports` merged view.
- Don't treat `shared_memory.jsonl` as throwaway — it's the recovery source. Never delete it on deploy.

## Helping the small model without editing weights

The 3B model is kept via:
- `config/dialogs.md` — few-shot example dialogs injected into the system prompt.
- Templates — bypass the model entirely for recurring tasks.
- Sticky hints injected into `_mem_ctx` (`Recent shared history...`) so the brain sees routing + recent reports.
- This skill — the model sees the routing rules in context when needed.

## Pitfalls (session 2026-08-23)

- `from pathlib import Path` yet `pathlib.Path(...)` in `orchestrator._persist_full_report` crashed with `name 'pathlib' is not defined` — report never hit `<state_dir>/reports/`. Always use `Path(...)` in methods unless `import pathlib` is present.
- `"Classest five emails on C-graph."` was ASR-mangled `verify my emails` but still hit `check-email` template — don't blame routing for mangled audio. Diagnose via `tail events.jsonl | grep routing.input` and `asr.transcribe`.
- `wttr.in/<city> tomorrow` 500s — weather templates must strip `tomorrow/tommorrow/tommorow` before the wttr call (fixed in `scripts/weather.py`).
- `hybrid` TTS saves ~70% but was still billed until `live voice.yaml` was switched from `openai` → `hybrid` — patch the live file `~/.config/.../voice.yaml`, not just `config/voice.yaml` in the repo. Verify `curl /api/status` shows `"tts": "hybrid"`.
- `Yeah.` took 4.79 s via `intent.llm` (`qwen3-vl:8b`) even after switching to `3b` — add a filler fast-path **before** `Classifier.classify()` in `_route_intent` + `_process_web_utterance_inner` (`hello/yeah/hi` → `routing.filler_fast`, no LLM).
- `/activity` serving the 41 MB `voice-dashboard` React build (`.venv` slow over tunnel) — prefer bridge `frontend/` first in `voicebridge/webui.py` `_frontend_dir`.

For more power without re-training, swap `intent.local_llm.model` to `qwen3:4b` or `phi-4-mini` (EN-only) — but the per-template bypass remains more reliable than any prompt. If you want to 'edit the brain' directly, use LoRA/PEFT on-device (see `qubit-voice-bridge/references/local-llm-upgrades.md` if present) — text-file steering via this skill stays the zero-risk default.
