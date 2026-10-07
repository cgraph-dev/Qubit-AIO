# /qubit-chat work brief — wave 2 (2026-10-07)

Owner directive: **`/chat` must stay UNCHANGED. All features go to `/qubit-chat` only.**
Owner directive: `/qubit-chat` is the voice bridge + report-reading surface.

Repo: `/home/trick/Projects/Repos/qubit-voicebridge`
Frontend: `cd frontend/qubit-web && npx vitest run` / `npm run typecheck`
Backend: `PYTHONPATH=qubit_runtime:. .venv/bin/python -m pytest tests/<file> -q -p no:cacheprovider`
Deploy: `systemctl --user restart qubit-voicebridge.service` (port 8890), survives ~16 s.

Token for live probes (do NOT persist it anywhere):
`TOK=$(curl -sL --max-time 6 http://127.0.0.1:8890/ | grep -o '__QUBIT_SESSION_TOKEN__="[^"]*"' | sed 's/.*"\(.*\)"/\1/')`
then send header `X-Qubit-Session-Token: $TOK`.

---

## TASK 1 — Finish the work panel: wire the producers (B1 server half)

The client half is DONE (`resolveActivityFrame()` unwraps `CUSTOM` correctly, tested).
The panel is still empty because **no producer emits `todo.updated` / `subagent.*` onto the
SSE hub**.

### Exact seams (already located — do not re-derive by guessing)

1. **Subagent events.** `qubit_runtime/tools/delegate_tool_progress.py:426`:
   ```python
   parent_cb = getattr(parent_agent, "tool_progress_callback", None)
   ```
   The child relay (`_ChildProgressRelay._relay`, :329) calls
   `parent_cb(event_type, tool_name, preview, args, **identity)` with the real event types
   `subagent.start` / `subagent.complete` / `subagent.text` / `subagent.progress`
   (see `_LIFECYCLE_EVENTS` at :122). `voicebridge/agent.py` builds its own `AIAgent`
   (~line 461) and **never sets `tool_progress_callback`** — that is why nothing arrives.

2. **Todo events.** `qubit_runtime/tui_gateway/tool_progress.py:366`:
   ```python
   _emit("todo.updated", sid, todo_state)
   ```
   That publisher writes into the TUI's transport. Read how it is wired before assuming you
   can reuse it.

3. **The hub.** `voicebridge/events/hub.py::EventHub`, reached as `app._events_hub`.
   Frames are built with `make_event("CUSTOM", name=..., value=...)`.
   The working precedent is `_approval_sink` at `voicebridge/webui.py:~4183` — copy that shape.

### Deliverables

- **1a.** Pass a `tool_progress_callback` into the `AIAgent` built at `voicebridge/agent.py`
  (~461) that republishes `subagent.*` as `CUSTOM` frames on the hub. The existing
  `event_sink` parameter on `run_agent_turn` / `_run_vendored_session` is the thread to use —
  extend the same pattern rather than adding a second mechanism.
- **1b.** Bridge `todo.updated` to the hub the same way.
- **1c.** Scope frames to the session (`run_id`/session filter) so the panel never shows
  another session's agents.

### STOP condition (mandatory)

If either emitter is genuinely unreachable from `voicebridge/` (wrong process, wrong
transport, no handle), **STOP and report that** with the exact evidence. Do NOT fabricate a
data path, and do NOT claim the panel works without a live capture proving non-empty todos.
A previous pass shipped a completely dead panel by assuming a data path existed.

### Proof required

- Live SSE capture (`curl -sN --max-time 30 .../api/events/stream`) showing a `CUSTOM`
  `todo.updated` or `subagent.*` frame **with the real payload**, while an agent turn runs in
  `/qubit-chat`.
- Frontend tests still green.

---

## TASK 2 — Voice-first features that no other surface has

`/qubit-chat` is the VOICE surface, so these are the features that actually belong here.

Reuse what exists — do NOT rebuild:
- `hooks/useDesktopVoiceHelper.ts`, `hooks/useTranscriptionController.ts`
- `components/ChatWorkPanel.tsx` (+ `.css`), `ChatSessionList.tsx`, `ChatSidebar.tsx`
- The existing `/agent/api/qubit/chat/vision` and TTS routes

### Deliverables

- **2a. "Read this to me" on any message.** A per-message action that speaks that message
  through the existing TTS path. Must be cancellable mid-playback.
- **2b. Push-to-talk inside the page.** `ChatPage.tsx` already tracks `rightCtrlHeld`
  (:1513-1520) — verify whether PTT actually captures or is display-only, and make it work
  in-page if not.
- **2c. Report list.** `/qubit-chat` is for reading reports; surface recent reports as a
  first-class list that opens in the transcript.

Keep every action keyboard-reachable and cancellable. No autoplay.

---

## TASK 3 — Daily-use features (Codex/Copilot/Claude-Code-class, adapted to this surface)

The owner asked to add "what Codex, Copilot and Claude Code can do in their UI" to
`/qubit-chat`. Those are CODING tools; `/qubit-chat` is a voice + report surface. Do NOT
port diff review / hunk staging / multi-file edit / repo indexing — they belong to a coding
surface and would duplicate `git` + the terminal forever.

Instead: identify which of their UX patterns genuinely help a **voice/report** surface, and
build those. Candidates worth evaluating (pick and justify):
- **Command palette** (Ctrl-K) over actions, sessions, reports, skills — Codex/Copilot both
  have one; it is surface-agnostic and cheap.
- **Inline citations / source chips** on answers — Copilot's defining UI idea; fits report
  reading directly.
- **Plan/checklist view** for a running task — already half-built by the work panel.
- **Session/thread search** across past conversations.
- **Slash/@ autocomplete polish** (already partly done — verify it works).

For each chosen feature: say WHY it fits a voice/report surface, and skip anything that does
not. Deliver working, tested UI. Report which candidates you rejected and why.

---

## TASK 4 — Fix the two known test-environment failures

- `tests/test_doc_extract8u.py`: 12 of 15 failures pre-exist and are caused by the test
  scratch dir living under `~/.local/state/`, which the security gate correctly refuses as
  secret-bearing (`ProjectError: project root is (or is inside) a secret-bearing directory`).
  Fix the TEST to use a non-secret-bearing temp dir. Do not weaken the gate.
- `tests/test_dashboard_model_profiles.py` is GREEN (14/14) — use it as the pattern.

---

## Rules for every agent

- **`/chat` is off-limits.** Never modify `frontend/qubit-web/src/pages/TerminalChatPage.tsx`
  or anything that changes TUI behaviour.
- **No change may alter an EXISTING session's stored model** (a prior task set new dashboard
  sessions to the local brain — do not undo it).
- **Measure before claiming.** Capture the live feed/TUI/HTTP response. This work exists
  because a previous pass read source and assumed it was wired.
- **Never fabricate a data path or a result.** Typed refusal over a plausible fake.
- **Do not report success without a live check** against port 8890.
- Restart the service to pick up backend edits, then verify in a FRESH process.
