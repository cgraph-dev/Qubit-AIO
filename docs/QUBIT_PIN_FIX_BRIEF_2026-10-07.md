# Per-session model pin ignored by the worker — single focused fix (2026-10-07)

## Owner directive

`/qubit-chat` and `/chat` each own their model, independently. `/qubit-chat` defaults to
the local brain; `/chat` keeps whatever the operator picks in its own panel or via
`/model`. **`/chat` must not regress — it is PROVEN WORKING and is off-limits.**

## The exact bug (measured, not inferred)

A `/qubit-chat` session pinned to a non-default local model does **not** run it. Four
layers were probed; three disagree:

| Layer | Reported model | Verdict |
|---|---|---|
| Session store (`GET .../sessions/<id>`) | `qubit-spark17` | ✅ CORRECT |
| `chat_transport.resolve_route(cfg, None, session=sid)` | `qubit-spark17`, `kind='local'`, `route='local'` | ✅ CORRECT |
| Stream receipt `"model"` field | `deepseek-v4.1-flash` | ❌ WRONG — `_branch_model()` reads the GLOBAL config |
| What Ollama actually loads during the turn (`/api/ps` after evicting all) | `qubit-brain4` | ❌ WRONG — the pin is ignored |

So the pin reaches the resolver, but the **turn itself runs somewhere that selects its own
model**. Reproduction: create a dashboard session, `PATCH .../sessions/<id> {"model":
"qubit-spark17"}`, then `POST /agent/api/qubit/chat/stream {"session":<id>,"text":"say OK"}`.
Evict all ollama models first, then read `/api/ps` after the turn — it shows `qubit-brain4`.

## Already fixed (do not redo)

- `voicebridge/qubit_session_store.py:730` — `append()` no longer overwrites an existing
  session's stored model (it previously clobbered the pin on every message).
- `voicebridge/chat_transport.py:~700` — `resolve_route` now resolves the SESSION PIN
  directly instead of the global default, because a disabled/non-allowlisted global API
  profile made the resolver fail and return early BEFORE any pin could be applied.
- `voicebridge/chat_transport.py:~723` — the pin gate now includes `"local"` kind.
- Verified: `/chat` is untouched (`TerminalChatPage.tsx` and `qubit_runtime/tui_gateway/`
  both EMPTY diffs); a tui session's model is unchanged after `ensure(identity="terminal:chat")`.

## WHERE THE BUG IS

The turn dispatches through **`orchestrator.launch_task` → a daemon thread →
`_agent_run_session` (= `agent.run_session`) → `_run_vendored_session` → `AIAgent`**.
That worker path selects its own model and never consults `_pinned_session_model`.

Key symbols:
- `voicebridge/chat_transport.py:~4322` — `_stream_task` calls `ORC.launch_task(...)`
- `voicebridge/orchestrator.py:662` — `launch_task(task_text, session, *, cfg, model, …)`
  (already accepts a `model` arg at :663 — check whether it is actually propagated)
- `voicebridge/orchestrator.py:~794` — `lambda: _agent_run_session(cfg, …)` in the worker thread
- `voicebridge/orchestrator.py:161` — `_legacy_default_model()` (falls back to the global config)
- `voicebridge/agent.py:579` `run_session(...)` → `:649` `_run_vendored_session(...)`
- `voicebridge/agent.py:~427` — `_resolve_model_and_provider(vcfg, _explicit_model(cfg) or None, None)`
  ← the model the vendored `AIAgent` actually receives; this is the likely ignore point.

## DELIVERABLES

1. **Make the worker honour the session pin.** When `launch_task`/`run_session` runs for a
   session with a stored model, that model must reach the `AIAgent` (via the existing
   `model` argument / `_explicit_model` path — do NOT invent a new routing layer). A pinned
   `qubit-spark17` must actually load and answer.
2. **Make the receipt tell the truth.** `_branch_model(cfg)` (`chat_transport.py:1196`) reads
   the GLOBAL `configured_model(cfg)`, so the stream receipt reports a model the turn never
   used. Report the RESOLVED session model instead.
3. **Keep the independent defaults:** a NEW `/qubit-chat` session → the local brain
   (`chat_transport.local_brain_model`); `/chat` unaffected (identity `terminal:chat` is
   already excluded from the dashboard default).

## PROOF REQUIRED (raw output for each)

1. Evict all ollama models, pin a session to `qubit-spark17`, run a turn, and show
   `curl http://127.0.0.1:11434/api/ps` listing **qubit-spark17** (currently `qubit-brain4`).
2. Show the stream receipt's `"model"` now matching the pinned model.
3. Show a session pinned to a DIFFERENT model (`qubit-brain`) runs that one — two sessions,
   two different models, both correct.
4. `git diff --stat frontend/qubit-web/src/pages/TerminalChatPage.tsx` and
   `git diff --stat qubit_runtime/tui_gateway/` must BOTH be EMPTY (`/chat` untouched).
5. Regression: a tui session's stored model is unchanged, and `pytest` for
   `tests/test_dashboard_model_profiles.py` + `tests/test_chat_progress_bridge.py` stays green.

## RULES

- **`/chat` must not regress.** Never edit `TerminalChatPage.tsx`, `qubit_runtime/tui_gateway/`,
  or the vendored `qubit_runtime/qubit_state_sessions.py` writer.
- Reuse the EXISTING model plumbing (`launch_task(model=)`, `_explicit_model`, the resolver) —
  no new mechanism.
- Measure before claiming: the proof is `/api/ps`, not the receipt.
- The repo is shared with an OpenCode/VSCode upstream-sync agent in `qubit_runtime/` — surgical
  edits only; re-check `git status` before restarting the service.
- `execute_code` may fail in a subagent session with a stale-module NameError — use `terminal`
  with explicit `.venv/bin/python`.
- Service: `systemctl --user restart qubit-voicebridge.service` (~18 s), port 8890.
  Token: `TOK=$(curl -sL --max-time 6 http://127.0.0.1:8890/ | grep -o '__QUBIT_SESSION_TOKEN__="[^"]*"' | sed 's/.*"\(.*\)"/\1/')`.
