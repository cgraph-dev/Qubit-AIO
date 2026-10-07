# /qubit-chat work brief — agent tasks (2026-10-07)

Owner-approved scope. **`/chat` must not be modified** (owner directive, in force).

Repo: `/home/trick/Projects/Repos/qubit-voicebridge`
Verify: `cd frontend/qubit-web && npm run typecheck && npx vitest run`
Deploy: `systemctl --user restart qubit-voicebridge.service` (port 8890)

---

## TASK 1 — Fix the dead work panel (B1)

**Status: the panel is shipped and non-functional. Its data source does not exist.**

### Measured facts (do not re-derive; these are live captures)

The SSE feed `GET /api/events/stream` emits AG-UI envelopes. Live sample:

```
22  {"type":"STATE_SNAPSHOT",...}
22  {"type":"RUN_STARTED",...}
 6  {"type":"CUSTOM","name":"communications.report.ready","value":{...}}
```

The real event name is `name` **inside** a `CUSTOM` envelope — never the top-level `type`.

`frontend/qubit-web/src/hooks/useChatActivity.ts:164`:

```ts
const name = str(rec.type) || str(rec.event) || frame.event;   // reads "CUSTOM"
if (name === "todo.updated") { ... }                            // never true
else if (name.startsWith("subagent.")) { ... }                  // never true
```

### The deeper problem

Only ONE publisher writes to the typed hub (`voicebridge/webui.py:4183`): the approvals
sink. **Nothing publishes `todo.updated` or `subagent.*` to the SSE feed.** The in-process
TUI emits those to its own clients (`qubit_runtime/tui_gateway/tool_progress.py:366`), not
to this hub.

So the fix needs BOTH halves.

### Deliverable 1a — unwrap CUSTOM (client)

In `useChatActivity.ts`, resolve the name as:
`rec.type === "CUSTOM" ? rec.name : rec.type`, and take the payload from
`rec.value` for CUSTOM frames. Keep the existing fallbacks.

**Test:** feed a captured CUSTOM frame verbatim and assert the handler dispatches.

### Deliverable 1b — publish the data (server)

Decide and document ONE honest source for the panel's two datasets:

- **Preferred:** subscribe to the agent's tool-progress signal and republish
  `make_event("CUSTOM", name="todo.updated", value=<todo_state>)` and
  `make_event("CUSTOM", name="subagent.<phase>", value=<frame>)` onto the hub. Find the
  existing in-process emitter in `qubit_runtime/tui_gateway/tool_progress.py`
  (`_emit("todo.updated", sid, todo_state)` at :366) and bridge it — do NOT invent a
  second todo/task model.
- If no honest bridge exists, **stop and report** rather than fabricate a data path.

### Deliverable 1c — scope to the session (B2)

The feed is global. Add a session/run filter (the server already supports `run_id=`, see
`webui.py:_events_stream`) or match client-side. The panel must never show another
session's agents.

---

## TASK 2 — Default `/qubit-chat` sessions to the local brain (M1)

Measured: `voice-chat` uses `qubit-brain4`; every other dashboard session uses
`deepseek-v4.1-flash`. Model is per-session (`chat_transport._pinned_session_model`, :604).

Owner intent: `/qubit-chat` is the voice + report surface, so its NEW sessions should
default to the local profile.

- Set the model at `ChatSessionStore.ensure()` for `created_source='dashboard'` rows.
- Do NOT change existing sessions' stored model.
- Do NOT change `/chat` (tui) behaviour.
- Make the default read from config, not hardcoded.

**Test:** a new dashboard session gets the local model; an existing one keeps its own; a
tui session is untouched.

---

## TASK 3 — PDF extraction quality

`voicebridge/doc_extract.py::pdf_backend()` returns `stdlib` — a regex text-stream
scraper. Works on simple PDFs; fails on compressed/multi-page/encrypted.

- Add `pypdf` as a dependency and confirm `pdf_backend()` prefers it.
- Keep the stdlib fallback for installs without it.
- Prove with a real multi-page compressed PDF, not a hand-built minimal one.

---

## OUT OF SCOPE (owner has not approved)

- **Codex / Copilot / Claude Code features** (diff review, hunk staging, plan mode,
  multi-file edit). These belong to a coding surface; `/qubit-chat` is voice + reports.
  Undecided — do not build.
- **Any change to `/chat` / `TerminalChatPage.tsx`.**

---

## Rules for every agent

- **Measure before claiming.** A live capture beats a plausible reading of the code. This
  work exists because a previous pass read source and assumed it was wired.
- **Never fabricate a data path.** If the publisher does not exist, report that.
- **Do not report success without a live check** against port 8890.
- Files already known dead/inert in this area: `voicebridge/chat_stores.py` docstring,
  `ChatSessionStore` (never instantiated).
