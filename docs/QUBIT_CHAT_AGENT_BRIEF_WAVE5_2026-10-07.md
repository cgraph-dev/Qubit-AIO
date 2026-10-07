# /qubit-chat wave 5 — independent per-surface model panels (2026-10-07)

## Owner directive (this wave) — READ VERBATIM

> "leave qubit-brain default for `/qubit-chat` and for `/chat` leave default whatever I
> pick in model provider panel or what model I select via command. `/qubit-chat` and
> `/chat` have their own model provider panel they don't share it, cos I can pick
> different models in them if I want"

Also still in force (wave 4, unfinished):
- `/qubit-chat` must create sessions from Right-Ctrl / wake / voice commands.
- Vision: API offload WITH a mandatory in-UI approve/decline popup (owner confirmed the
  privacy tradeoff but requires the popup — never silent egress).

`docs/DECISIONS.md` #26 remains the authority: `/qubit-chat`, Right-Ctrl and the wake
command must do everything `/chat` can — and more, never less. `/chat` must NOT regress.

## Authority & env

Repo `/home/trick/Projects/Repos/qubit-voicebridge`. Service 8890
(`systemctl --user restart qubit-voicebridge.service`, ~18 s).
Token: `TOK=$(curl -sL --max-time 6 http://127.0.0.1:8890/ | grep -o '__QUBIT_SESSION_TOKEN__="[^"]*"' | sed 's/.*"\(.*\)"/\1/')`
→ header `X-Qubit-Session-Token: $TOK`. Turn route `POST /agent/api/qubit/chat/stream`.
Read `AGENTS.md`, `docs/DECISIONS.md`, `skills/voice-routing/SKILL.md`.

---

## MEASURED FACTS (verified 2026-10-07 — re-verify before acting)

**Current live state (already done, do not redo):**
- `/qubit-chat` + voice default model = **`qubit-brain`** (global `model.default` in the
  runtime config; set via `/agent/api/model/set`). Verified: a live turn returned
  `"reply":"VOICE-OK"` on `local/qubit-brain` in 2.27 s, cost $0.00.
- Model picker lists **7** local models: `qubit-brain`, `qubit-brain4`, `qubit-brain4-pre4b`,
  `qubit-cand-q4`, `qubit-cand-q5`, `qubit-spark17`, `qubit-spark4` (was 3).
- `qubit-brain4`'s bogus `vision` role removed.
- `voicebridge/providers/vision_payload.py` exists (OpenAI `image_url` translation,
  mime sniffing). NO UI popup yet.

**THE GAP THIS WAVE MUST CLOSE — model panels are NOT independent:**
- `voicebridge/qubit_compat.py:2187 h_model_set` accepts ONLY `scope` = `main` |
  `auxiliary` (line 2190-2195). **There is no per-session scope.** So a pick in EITHER
  panel rewrites the GLOBAL `model.default`, which both surfaces then follow — i.e. they
  DO share, contrary to the owner's requirement.
- `/chat`'s MODEL card calls this same global route: `TerminalChatPage.tsx:242` —
  *"model switching lives in the MODEL card (`/api/model/set`), as in the reference."*
- Per-session model WRITES are possible: `voicebridge/qubit_session_store.py:461 create()`
  and `:493 ensure(session_id, *, model=...)` both take `model=`, and the turn path already
  honours a pinned per-session model (`chat_transport._pinned_session_model:653`, used at
  :717 to route). So the missing piece is a per-session WRITE ROUTE + panel wiring, not a
  new storage mechanism.
- Each surface already has its OWN panel UI: `/qubit-chat` uses `ChatSidebar` inside
  `ChatPage.tsx` (~:2816 `qchat__model-panel`); `/chat` has its own MODEL card
  (`TerminalChatPage.tsx:1149+`). They are separate components already.

**Known-broken model (do not "fix" by flag):** `qubit-brain4` and `qubit-spark4` emit ALL
output into the `thinking` field with `content=''` and `done_reason=length`. The `think`
flag is IGNORED (identical output with `think:true` and `think:false`); token budgets
100/300/600 all end `done=length` empty. Root cause is the model template.
Full writeup: `docs/QUBIT_BRAIN4_EMPTY_DIAGNOSIS_2026-10-07.md`. Do NOT change the default
back to brain4.

---

## TASK 1 — Independent per-surface model panels (the owner's headline ask)

Make `/qubit-chat` and `/chat` each own their model choice, with no cross-talk.

- (a) Add a **per-session** model write path that does NOT touch the global
  `model.default`. Options to evaluate: extend `h_model_set` with a `scope="session"`
  (+ `session` id) variant, or add a dedicated route. Reuse
  `qubit_session_store.ensure(session_id, model=...)` — the existing write mechanism —
  rather than inventing storage.
- (b) Wire each surface's panel to the per-session write: `/qubit-chat` (ChatPage /
  ChatSidebar) and `/chat` (TerminalChatPage MODEL card). Each pick must change ONLY that
  surface's session model.
- (c) **Independent defaults:** a NEW `/qubit-chat` session defaults to `qubit-brain`
  (local). A NEW `/chat` session defaults to the global/terminal model (currently
  `opencode-go/deepseek-v4.1-flash`) and `/chat` keeps whatever the operator selects via
  its own panel or `/model` command. Neither surface may overwrite the other's stored model.
- (d) The terminal path must still honour its own model: `TerminalChatPage.tsx:1061-1078`
  already polls and adopts the session's stored model — verify that keeps working and that
  a `/model` switch in the terminal still sticks.
- **PROOF REQUIRED (all four):** (i) set `/qubit-chat` session A to `qubit-brain`; (ii) set
  a `/chat` session B to a different model; (iii) read both back and show they differ;
  (iv) show the global `model.default` was NOT rewritten by either pick. Plus pytest for the
  new write path and its isolation.

## TASK 2 — Finish wave-4 TASK 1: voice/wake/PTT creates /qubit-chat sessions

A prior agent identified the seam but wrote no code (it was interrupted at the plan stage).
Its verified reading: `VoiceEditService.begin()` is the single seam — Right-Ctrl PTT and
the wake path both call `helper.client.begin()`; the session identity is
`voice_session_id(cfg)` (`voice-chat` single-mode, `voice-<…>` per-command mode); the store
is `chat_transport.store(cfg)` = `QubitSessionStore`.

- (a) Implement `ensure_voice_session(cfg, session_id, *, store=None, model=None)` that
  calls `store.ensure(sid, model=local_brain_model(cfg))` — creating a dashboard-source
  session so it appears in the `/qubit-chat` picker at the local-brain default.
- (b) Call it from `VoiceEditService.begin()` so every PTT/wake/voice interaction ensures
  the session. Reuse the EXISTING canonical store — never a second one.
- (c) pytest proving a voice turn associates with a dashboard-source session the picker
  lists. Note existing tests: `frontend/qubit-web/src/pages/ChatPage.voice9u.test.tsx`,
  `ChatPage.voiceSessions.test.tsx`, `ChatPage.voiceSync.test.tsx` — do not break them.
- **STOP + report** if the identity or store path turns out ambiguous.

## TASK 3 — Vision approval popup (wave-4 TASK 4 remainder)

Backend is built (`voicebridge/providers/vision_payload.py` + resolver/frontier changes).
The UI gate is missing, so remote vision must stay unusable.

- (a) When the resolved vision profile is REMOTE, `/qubit-chat` must prompt the user to
  approve or decline sending the image BEFORE any upload. Declining must produce NO network
  call. Never silent egress.
- (b) Implement as a new component/hook (surgical edits — a sibling agent edits
  ChatPage.tsx) with a pure decision helper + vitest, plus the UI.
- (c) `qwen3-vl:2b` (1.89 GB) — pull and register as a smaller local option; benchmark it
  against `qubit-brain-vision` (3.33 GB) on the same image and report which is faster.
- **PROOF REQUIRED:** decline produces no outbound request (show it), and the benchmark
  numbers. Note: vision needs ~3.7 GB free VRAM — a running game will cause `VISION_FAILED`
  legitimately; that is not a bug (verify VRAM with `nvidia-smi` before concluding).

---

## RULES FOR EVERY AGENT

- **`/chat` MUST NOT REGRESS.** Never modify `frontend/qubit-web/src/pages/TerminalChatPage.tsx`
  beyond the model-panel wiring TASK 1 explicitly requires, and never change TUI behaviour.
  Every other task lands in `/qubit-chat` and its backend paths.
- **Do not change the global default back to `qubit-brain4`** — it cannot answer.
- **Do not break existing sessions' stored models.** Changing a session's model happens only
  where that surface's panel explicitly does it.
- **Measure before claiming.** Four prior waves reported success on code that was never
  reachable. A live capture / DOM assertion / test output beats reading source. Re-verify
  the MEASURED FACTS above rather than trusting them.
- **Never fabricate a data path or result.** A typed refusal is a valid, valued outcome.
- `execute_code` may fail in a subagent with a stale-module NameError — use `terminal` with
  explicit `.venv/bin/python`.
- The repo is shared with an OpenCode/VSCode upstream-sync agent working in `qubit_runtime/`
  — make surgical edits and re-check `git status` before restarting the service.
