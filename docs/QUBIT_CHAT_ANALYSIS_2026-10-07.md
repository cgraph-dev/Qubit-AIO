# /qubit-chat — comprehensive analysis

Date: 2026-10-07. Method: source reading + live probes against the running service
(`127.0.0.1:8890`). Every claim below is either measured or marked unverified.

---

## 1. Bugs found (real, reproduced)

### B1 — the work panel's data path: bridge BUILT and PROVEN, but unreachable on the main route

Three findings, in order of how deep each goes.

**B1a — client unwrap (FIXED, tested).** The SSE feed emits AG-UI envelopes; the real event
name lives in `rec.name` inside a `CUSTOM` frame, not in `rec.type`. `useChatActivity.ts:164`
read `rec.type`, got the literal `"CUSTOM"`, and its two branches could never match.
`resolveActivityFrame()` now unwraps correctly. Verified live: feed emits
`{"type":"CUSTOM","name":"communications.report.ready","value":{...}}`.

**B1b — the producer bridge (BUILT and PROVEN in isolation).** A new module
`voicebridge/events/chat_progress.py` implements `ChatProgressBridge`, republishing the
vendored runtime's tool-progress feed as `CUSTOM` frames. It is threaded
`run_agent_turn(..., progress_sink=)` → `AIAgent(tool_progress_callback=...)`, which is the
exact seam the vendored runtime reads at
`qubit_runtime/tools/delegate_tool_progress.py:426`
(`parent_cb = getattr(parent_agent, "tool_progress_callback", None)`).

Proven directly — feeding a real subagent call yields the correct frame:
```
type=CUSTOM name=subagent.start
keys=['depth','model','parent_id','preview','run_id','session_id','subagent_id','tool_count']
```
That is exactly the shape the frontend unwraps. `tests/test_chat_progress_bridge.py` = 7/7 green.

**B1c — the bridge is now REACHABLE (FIXED and proven live).**

The gap was one missing argument. `_run_vendored_session` already accepted
`progress_sink` (and passed it to `AIAgent(tool_progress_callback=...)`), and
`run_agent_turn` passed it — but `agent.run_session()` did not, and that is the
function the real `/qubit-chat` turn path calls. Chain, measured:

```
h_chat_stream (qubit_compat.py:6270)
 -> chat_transport._stream_task -> ORC.launch_task (chat_transport.py:4343, route "agent_task")
 -> daemon thread _run() (orchestrator.py:777)
 -> WorkerGuard.run("agent.session", lambda: _agent_run_session(...))
 -> agent.run_session (agent.py:579)  <-- sink was dropped here
 -> _run_vendored_session(..., progress_sink=) -> AIAgent(tool_progress_callback=)
```

Fix (2 files): `run_session` gained a `progress_sink` parameter and forwards it;
`launch_task` builds the sink once via `build_progress_sink(cfg, key)` on the
worker thread (None headlessly, so voice/CLI paths are byte-identical) and passes
it through the daemon thread.

**Live proof** — real `CUSTOM` frames captured on `/api/events/stream` during a
`/qubit-chat` turn that delegated:

```
subagent.start      2
subagent.progress   4
subagent.complete   4
subagent.tool       3
subagent.thinking   9
subagent.text    1006
```

Payloads carry `subagent_id`, `child_session_id`, `delegation_id`, `task_index`,
`task_count`, `goal`, and `run_id` = the dashboard session (scoping intact).

**Important limitation, stated plainly:** subagent frames fire only when the turn
actually delegates. A plain `ls` turn emits nothing — `subagent.*` comes from the
child relay (`delegate_tool_progress.py:426`, `getattr(agent,
"tool_progress_callback")`), and `todo.updated` only when a todo tool runs. An
ordinary non-delegating turn legitimately has no agents to show.

`components/ChatWorkPanel.tsx` + `hooks/useChatActivity.ts` were **dead code**.

The SSE feed (`GET /api/events/stream`) does not emit raw event names. Measured on the
live feed, the only types present are:

```
22  "type":"STATE_SNAPSHOT"
22  "type":"RUN_STARTED"
 6  "type":"CUSTOM"
```

The real event name is nested inside `CUSTOM`:

```json
{"type":"CUSTOM","name":"communications.report.ready","value":{...}}
```

`useChatActivity.ts:164` read `str(rec.type) || str(rec.event) || frame.event`, so it
saw `"CUSTOM"` and never `rec.name`. Its two branches (`name === "todo.updated"`,
`name.startsWith("subagent.")`) could therefore never match.

**FIXED (client half).** `resolveActivityFrame()` now unwraps CUSTOM:
`rec.type === "CUSTOM" ? rec.name : rec.type`, payload from `rec.value`. Covered by tests.

**NOT FIXED (server half) — and this is now precisely understood.** The panel's data does
not exist on this feed, and building it is a real integration, not a one-liner:

- The vendored `AIAgent` accepts `event_callback(name, payload)` and **does invoke it** —
  but the only name it ever emits is `"run_tool"` (measured: one `event_callback("run_tool"`
  site in `qubit_runtime/`).
- `todo.updated` is emitted only at `qubit_runtime/tui_gateway/tool_progress.py:366`, into
  the **TUI's own JSON-RPC transport** (`_emit`).
- `subagent.*` is emitted only at `qubit_runtime/tools/delegate_tool_progress.py:375`, via
  `self._relay(...)` → `parent_cb`, a **per-agent callback the TUI's `prompt.submit` path sets**.

`voicebridge/agent.py` constructs its own `AIAgent` (line ~461) and sets **no** `parent_cb`,
so neither emitter reaches it. Confirmed: `grep -n 'parent_cb|_relay' voicebridge/agent.py`
→ empty.

A bridge was implemented on the correct seam (`run_agent_turn(..., event_sink=)` →
`AIAgent(event_callback=event_sink)`) and is non-breaking (verified: 65 agent tests pass,
4 failures pre-exist and reproduce with the change stashed). But **wiring it is incomplete
until a producer emits the names the panel expects onto the hub** — either by setting
`parent_cb` on the voicebridge-constructed agent, or by republishing the TUI emitters onto
the SSE hub. That is the remaining work; the client half is honest and done.

### B2 — the panel is gated on the wrong session's feed

See B1c above: the feed is global and the bridge tags `run_id`/`session_id` onto each frame,
but the panel's data never arrives at all, so session scoping is untested in practice.

The SSE feed is global (session-agnostic in the sample: `threadId` was a voice run), and
`useChatActivity(session)` subscribes with no server-side filter. `run_id=` filtering
exists in `_events_stream` but the client never passes it. So even after B1 is fixed, the
panel would show activity from **other** sessions. Needs a `run_id`/session filter or
client-side session matching.

### B3 — `.catch()` count vs surfaced errors

`ChatPage.tsx` has 18 `.catch(` sites. Several assign to state that no longer has a
renderer (the panel's `attachError` is rendered, but other paths are not verified).
Not individually reproduced — flagged as a review item, not a claim.

### B4 — pre-existing, not mine

14 test failures in `QubitBotPage.test.tsx` / `AgentCardsPage.test.tsx` / `TaskInboxPage`
exist on the **baseline** (proven by `git stash` + rerun). Not caused by this work, but
they mean the frontend suite cannot be used as a green/red gate today.

---

## 2. Misconfigurations found

### M1 — `/qubit-chat` has no default model

`voice-chat` uses `qubit-brain4`; every other dashboard session uses `deepseek-v4.1-flash`.
Model is stored **per session** (`chat_transport._pinned_session_model`), so a new
`/qubit-chat` conversation silently gets the *configured default*, not the local brain.
If `/qubit-chat` is the voice/report surface, its new sessions should default to the local
profile. Feasible: set the model at `ChatSessionStore.ensure()` for dashboard-sourced rows.

### M2 — two agent/todo data paths, only one wired

`subagent.list` RPC exists and is the documented read for the roster
(`tui_gateway/AGENTS.md` § Shared subagent snapshots) but is exposed only on the retired
`/api/ws` socket (426). SSE carries the same information as `CUSTOM` frames. Using both
would be a second stack; pick SSE and fix B1.

### M3 — 187 sessions with a NULL `created_source`

Measured: 467 rows have no source, 187 of them with ≤2 messages. The picker now filters
the small ones, but nothing prevents their creation. Worth finding the writer.

---

## 3. What /qubit-chat does NOT have that /chat does

Measured by grepping both surfaces:

| Capability | /chat | /qubit-chat |
|---|---|---|
| Todo list | via TUI | **wired but dead (B1)** |
| Subagent roster | via TUI | **wired but dead (B1)** |
| Token/elapsed | TUI status bar | per-message only |
| Approvals | TUI prompt | partial (2 refs) |
| Checkpoints / worktree | TUI verbs | none |
| Live tool activity | TUI thinking pane | none |

Root cause of the whole class: `/chat` renders the real TUI, so every TUI feature is free.
`/qubit-chat` is React and must re-implement each one. `web/AGENTS.md` explicitly warns
against this ("Do not re-implement the primary chat experience in React").

---

## 4. On the Codex / Copilot / Claude Code request — recommend AGAINST

Those tools' UIs are built around **writing code**: inline diff review, hunk staging,
multi-file edits, repo indexing, PR review, plan mode, test-runner integration.

Adding them to `/qubit-chat` conflicts with the owner's own statement that
`/qubit-chat` is the **voice bridge and report-reading surface**, and with the
2026-09-17 ruling that the two routes stay distinct.

Worse, they are the most expensive possible features to build here: each one is a
React re-implementation of something the TUI already does, and `web/AGENTS.md` warns
against exactly that. Codex-style diff review in `/qubit-chat` would be a large
surface that duplicates `git` + the terminal, maintained forever.

**Recommendation:** if coding features are wanted, they belong in `/chat` (where the
agent's terminal and tools already live) or as TUI features — not in the voice surface.

---

## 5. What to actually improve, ranked

1. **B1** — fix the CUSTOM unwrap so the panel works at all. Small, makes shipped code real.
2. **B2** — scope the feed to the session. Small, prevents cross-session confusion.
3. **M1** — default `/qubit-chat` sessions to the local brain. Small, matches stated intent.
4. **PDF quality** — install `pypdf`; the stdlib extractor only handles simple PDFs.
5. **M3** — find the NULL-source writer.
6. **Voice-first affordances** for `/qubit-chat` specifically: press-to-talk inside the
   page, a "read this to me" action on any message, and a report list. These fit the
   surface's actual purpose and no other surface has them.

---

## 6. Verified vs not

**Verified (live):** SSE frame shapes; per-session model values; session counts;
picker filter behaviour; the two baseline test failures; service health.

**Not verified:** whether brain4 completes a live turn in `/qubit-chat`; whether the
18 `.catch` sites surface anything to the user; whether M1 is what the owner wants.
