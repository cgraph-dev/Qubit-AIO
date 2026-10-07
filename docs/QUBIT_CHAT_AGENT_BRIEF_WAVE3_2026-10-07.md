# /qubit-chat work brief — wave 3 (2026-10-07)

Owner directive: **`/chat` stays UNCHANGED. All features land in `/qubit-chat` only.**

Repo: `/home/trick/Projects/Repos/qubit-voicebridge`
Frontend: `cd frontend/qubit-web && npx vitest run` / `npx tsc -p tsconfig.json --noEmit` /
`npm run build` (build runs `tsc -b` and catches unused imports vitest misses)
Backend: `PYTHONPATH=qubit_runtime:. .venv/bin/python -m pytest tests/<f> -q`
Deploy: `systemctl --user restart qubit-voicebridge.service` (~18 s)

Token (never persist it):
`TOK=$(curl -sL --max-time 6 http://127.0.0.1:8890/ | grep -o '__QUBIT_SESSION_TOKEN__="[^"]*"' | sed 's/.*"\(.*\)"/\1/')`

---

## TASK 1 — Finish the work panel on the REAL turn route (highest value)

This is the last piece of a long investigation. The wiring gap is now known exactly.

### The full chain, measured (do not re-derive)

A plain-text `/qubit-chat` turn does **not** run the vendored agent on the request thread:

```
h_chat_stream (qubit_compat.py:6270)
  -> chat_transport.stream_turn
       -> fast-path classifier
       -> ORC.launch_task(...)                      chat_transport.py:~4343   (route "agent_task")
            -> daemon thread `_run()`               orchestrator.py:~777
                 -> WorkerGuard.run("agent.session", lambda: _agent_run_session(...))
                      -> agent.run_session(...)     agent.py:579
                           -> _run_vendored_session(cfg, clean, sid, check=..., store=..., reasoning=...)
                                                            ^^^ agent.py:~649  <-- SINK NOT PASSED
                                -> AIAgent(event_callback=event_sink)   agent.py:~495
```

`_run_vendored_session` **already accepts** `progress_sink` / `event_sink` and passes it to
`AIAgent`. `run_agent_turn` passes it. **`run_session` does not** — that is the entire gap.

### Deliverables

- **1a.** Thread a progress sink from `launch_task` down through the daemon thread and
  `agent.run_session(...)` into `_run_vendored_session(..., progress_sink=...)`.
  The hub handle is already attached to the shared `cfg`
  (`cfg._qubit_chat_hub` / `cfg._qubit_chat_make_event`, set in `webui.py:~4182`), and
  `voicebridge/events/chat_progress.py::build_progress_sink(cfg, sid)` builds it. Reuse that —
  do NOT add a second mechanism.
- **1b.** Confirm `run_session`'s other caller paths (line ~600 planner fallback) keep working
  when no hub is attached — `build_progress_sink` returns `None` headlessly and the vendored
  runtime falls back to its no-op default. Zero behaviour change when no hub.
- **1c.** Session scoping: the bridge already tags `run_id` / `session_id` onto every frame.
  Verify the frontend panel only renders frames for its own session.

### MANDATORY STOP CONDITION

If threading the sink through breaks any existing caller, or the frames still do not appear,
**STOP and report with the exact evidence**. Do NOT claim the panel works without a live
capture showing a real `todo.updated` or `subagent.*` CUSTOM frame with a payload.

### PROOF REQUIRED

Live SSE capture while a real tool-using turn runs end-to-end (see the recipe below), showing
at least one `CUSTOM` frame named `todo.updated` or `subagent.*`. Prior waves proved the bridge
correct in isolation; this wave must prove it **live**.

```bash
TOK=$(curl -sL --max-time 6 http://127.0.0.1:8890/ | grep -o '__QUBIT_SESSION_TOKEN__="[^"]*"' | sed 's/.*"\(.*\)"/\1/')
curl -sN --max-time 60 http://127.0.0.1:8890/api/events/stream \
  -H "X-Qubit-Session-Token: $TOK" -H "X-Api-Token: $TOK" > /tmp/sse.txt &
sleep 2
curl -s -X POST http://127.0.0.1:8890/agent/api/qubit/chat/stream \
  -H "X-Qubit-Session-Token: $TOK" -H 'Content-Type: application/json' \
  -d '{"session":"<id>","text":"use the terminal tool to list files in the current directory"}'
sleep 15
grep -o '"name":"[^"]*"' /tmp/sse.txt | sort | uniq -c
```

Also run: `tests/test_chat_progress_bridge.py` (7 green today) and the frontend suite.

---

## TASK 2 — Place the SourceChips component (built, tested, orphaned)

`components/SourceChips.tsx` + `lib/` helpers exist and are unit-tested, but nothing renders
them (`grep -c SourceChips src/pages/ChatPage.tsx` → **0**).

- Find where an assistant message carries sources/citations (search the message model and the
  API response for a sources/citations field — do NOT invent one; if no such field exists,
  STOP and report that rather than fabricating citations).
- Render chips under the message, keyboard-reachable, with `sourceLabel()` /`isExternalSource()`
  already provided.
- If the data genuinely does not exist, report that as the finding — a typed "no source field
  exists on the message model" is a valid, valuable outcome.

---

## TASK 3 — Verify the wave-2 features actually work in the browser

Wave 2 shipped Ctrl-K palette, read-aloud, reports list, session search. They compile and the
bundle contains them, but **nobody has exercised them in a real browser**.

- Drive `/qubit-chat` with the Playwright browser tools (or the CDP DOM skill
  `inspecting-qubit-desktop-dom`) against `http://127.0.0.1:8890/qubit-chat`.
- Prove or disprove each: Ctrl-K opens the palette and filters; a message's read-aloud button
  starts and cancels; the reports list renders and scrolls to a report; the session search box
  filters the list.
- Report a screenshot or DOM evidence per feature. A feature that does not work is a finding —
  do NOT mark it working because the code compiles.

---

## Rules for every agent

- **`/chat` is off-limits.** Never modify `frontend/qubit-web/src/pages/TerminalChatPage.tsx` or
  anything that changes TUI behaviour.
- **Do not change any EXISTING session's stored model.** New dashboard sessions default to the
  local brain (`qubit-brain4`) — do not undo that.
- **Measure before claiming.** Capture the live feed / DOM. Three prior passes reported success
  on code that was never actually reachable.
- **Never fabricate a data path or a result.** Typed refusal beats a plausible fake.
- Restart the service after backend edits, then verify in a FRESH process.
- Files known dead/inert in this area: `voicebridge/chat_stores.py` docstring,
  `ChatSessionStore` (never instantiated).
