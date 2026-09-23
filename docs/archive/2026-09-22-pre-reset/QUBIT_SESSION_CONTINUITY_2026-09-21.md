# Qubit Session Continuity — 2026-09-21

**Status:** implementation record for the session-continuity workstream.
**Verdict:** NOT independently verified. This document records a code-side
change, its mapping, and its focused tests. It is not a parity or completion
claim. No `VERIFIED`, `100%`, or `1:1` claim is made.

**Scope owned by this workstream:** `voicebridge/orchestrator.py`,
`voicebridge/agent.py`, `tests/test_session_continuity9u.py`, this document.
`voicebridge/chat_transport.py`, `voicebridge/qubit_session_store.py`,
`voicebridge/qubit_compat.py`, and `voicebridge/pty_ws.py` were **not** edited.

**Governing requirement:** single session/store authority — one session and one
transcript across desktop PTT, voice, `/qubit-chat`, CLI, and terminal `/chat`,
all reading and writing `voicebridge.qubit_session_store.QubitSessionStore`
(the strict adapter over the vendored `qubit_state.SessionDB` SQLite
`state.db`). No second store.

---

## 1. Session-id derivation, before the change

(Line numbers in this section are the pre-change observations; §2 uses the
post-change numbers.)

| Surface | Derivation | Location | Effective default id |
|---|---|---|---|
| `/qubit-chat` (non-streaming turn) | `_resolve_session(st, session, ...)` → `session or DEFAULT_SESSION` | `voicebridge/chat_transport.py:41` (`DEFAULT_SESSION`), `:605` (`_resolve_session`), `:706` (`turn`) | `dashboard-chat` |
| `/qubit-chat` (streaming turn) | `_resolve_session` after `stream_preflight` | `voicebridge/chat_transport.py:987` (`stream_preflight`), `:1096` (`stream_turn`) | `dashboard-chat` |
| `/qubit-chat` (retry last) | `session or DEFAULT_SESSION` | `voicebridge/chat_transport.py:955` (`retry_last`) | `dashboard-chat` |
| `/qubit-chat` frontend pointer | `resumeParam || storageGet("qubit.chat.session") || DEFAULT_SESSION` | `frontend/qubit-web/src/pages/ChatPage.tsx:162-163,326` | `dashboard-chat` |
| Desktop PTT / voice agent delegation | hard-coded `session="qubit-voice"` passed to `agent.run_session` | `voicebridge/orchestrator.py:2927` (`_handle_agent` → `_agent_run_session`) | `qubit-voice` |
| Desktop/voice headless worker (`run_worker_task`) | explicit `session` or `cfg.session_key(f"voice-task-{uuid}")` | `voicebridge/orchestrator.py:1682-1683`, call at `:1693` | `qubit:voice:voice-task-<hex>` |
| Voice/CLI coding worker (`_launch_task` → `_spawn_session_worker`) | `cfg.session_key(f"task-{uuid}")` | `voicebridge/orchestrator.py:1629`, call at `:1829` | `qubit:voice:task-<hex>` |
| CLI | `agent.run_session` callers pass an explicit id (`voicebridge/foundation.py:215` uses `"foundation"`) | `voicebridge/agent.py:305` | caller-supplied |
| Terminal `/chat` | vendored TUI behind a PTY; the browser mints a ticket with `session=body.get("session","")` | `voicebridge/webui.py:1399-1403`, `voicebridge/pty_ws.py` | vendored-runtime-owned (not converged here) |

`cfg.session_key` (`voicebridge/config.py:650`) yields `qubit:<agent.session_prefix>`
with `agent.session_prefix` defaulting to `voice` (`voicebridge/config.py:307`).

**The divergence:** the dashboard conversation was `dashboard-chat`, while the
desktop/PTT agent path was `qubit-voice` and the worker paths were
`qubit:voice:...`. They were distinct transcripts in the same `state.db`.
Separately, `agent.run_session` persisted its turns to the canonical store but
never folded the prior transcript into its own prompt, so a voice turn could
not continue a `/qubit-chat` conversation (the chat→voice direction read
nothing).

---

## 2. After the change

### 2.1 Typed resolution (`voicebridge/agent.py`)

- `canonical_session_id(cfg)` (`agent.py:81`) returns the one canonical
  conversation id:
  - `agent.session_id` from config when set (configurable), else
  - `voicebridge.chat_transport.DEFAULT_SESSION` (`"dashboard-chat"`), else
  - the literal `"dashboard-chat"` if even the import is unavailable.
  Never raises.
- `resolve_session_id(cfg, requested=None, *, surface="voice")`
  (`agent.py:105`) maps a surface-local id:
  - empty / `voice` / `qubit:voice` / `qubit-voice` (the
    `VOICE_SESSION_ALIASES`) → `canonical_session_id(cfg)`;
  - any other explicit id → returned unchanged (explicit ids always win),
    bounded to the store's 80-char limit.
  `surface` is accepted for audit/telemetry and future per-surface policy; it
  never changes an explicit id. Never raises.
- `run_session` (`agent.py:305`) resolves its `session` through
  `resolve_session_id`, so an empty/alias id lands on the canonical
  conversation, and returns that resolved id as `session`/`session_id`
  (additive `session_id` key). An explicit id is returned unchanged.

### 2.2 Prior-transcript folding (`voicebridge/agent.py`)

`_session_context(store, session_id, cfg, pending_text)` (`agent.py:164`)
reads up to `_CONTEXT_READ_LIMIT` (200) prior messages from the canonical
store and builds a bounded prompt with `voicebridge/context_policy.py`
(`decide_context` + `build_context_prompt`): a fast window for a simple turn,
a deterministic extractive summary of older turns, instructions always
retained, and the pending turn counted for window sizing but not rendered.

`run_session` folds that context into the model prompt
(`"Recent conversation:\n{context}\n\nUser: {clean}"`, matching
`chat_transport._generate` at `chat_transport.py:876-878`) and persists the
user turn and assistant reply back to the same canonical store. Store reads
and writes are best-effort and non-fatal: a store fault is audited and the
typed turn result is unchanged.

### 2.3 Desktop/PTT call site (`voicebridge/orchestrator.py`)

- New helpers `_resolve_agent_session(cfg, requested)` (`orchestrator.py:360`)
  and `_canonical_agent_session(cfg)` delegate to `agent.resolve_session_id`;
  never raise.
- `_handle_agent` now passes `session=_canonical_agent_session(self.cfg)`
  instead of the hard-coded `"qubit-voice"` (`orchestrator.py:2952-2953`).
- `run_worker_task` resolves its computed `session_key` through
  `_resolve_agent_session`, so the voice-task command's `qubit-voice` fallback
  (`voice_commands.py:2852`) lands on the canonical conversation and the
  receipt names the session the store wrote (`orchestrator.py:1709`). A
  generated per-task id is not an alias and is preserved.

### 2.4 Mapping table

| Surface / path | Before | After |
|---|---|---|
| Desktop PTT agent delegation (`_handle_agent`) | `qubit-voice` | `agent.session_id` or `dashboard-chat` (canonical) |
| `/qubit-chat` default | `dashboard-chat` | `agent.session_id` or `dashboard-chat` (canonical) |
| `agent.run_session` with `session=""` / `qubit-voice` / `qubit:voice` / `voice` | alias kept verbatim | canonical conversation id |
| `agent.run_session` with any other explicit id | explicit id | explicit id (unchanged) |
| `run_worker_task` with the voice alias (`qubit-voice`, from `voice_commands.py:2852`) | `qubit-voice` | canonical conversation id |
| `run_worker_task` default (no explicit session) | `qubit:voice:voice-task-<hex>` | unchanged — a distinct per-task conversation |
| `_launch_task` / `_spawn_session_worker` | `qubit:voice:task-<hex>` | unchanged — a distinct per-task conversation |
| Terminal `/chat` (PTY / vendored TUI) | vendored-runtime-owned | unchanged (outside this workstream) |
| Plain voice conversation (`_handle_conversation`) | in-memory `ConversationHistory` + local model | unchanged (outside this workstream) |

**Configurable canonical id:** set `agent.session_id` in `voice.yaml`. With no
value the canonical conversation is `dashboard-chat`, so desktop/PTT/voice and
`/qubit-chat` converge by default. No `config.py` default key was added; the
value is read with `cfg.get("agent", "session_id", default="")`.

---

## 3. What this change does not do

- It does not merge per-task worker conversations (`qubit:voice:task-*`,
  `qubit:voice:voice-task-*`) into the canonical conversation. Each dispatched
  task remains its own session id, persisted in the same store. This is a
  deliberate mapping, not a silent rewrite.
- It does not change the terminal `/chat` PTY path, which runs the vendored TUI
  and is outside this workstream.
- It does not route the plain voice conversation path (`_handle_conversation`)
  through the canonical store; that path still uses the in-memory
  `ConversationHistory` and the local model.
- It does not add a second store, a second runtime, or a second transcript.

---

## 4. Evidence

Focused tests (hermetic; stubbed local model, isolated XDG state):

```
python -m pytest tests/test_session_continuity9u.py -q
# 6 passed
```

`tests/test_session_continuity9u.py` proves:

1. every voice/PTT default spelling (`""`, `voice`, `qubit:voice`,
   `qubit-voice`) resolves to the canonical `dashboard-chat`, while explicit
   ids (`explicit-9u`, `qubit:voice:task-abc`) are preserved;
2. the canonical id is configurable via `agent.session_id`;
3. `run_session(session="")` lands on the canonical conversation;
4. voice→`/qubit-chat` and `/qubit-chat`→voice are continuous in both
   directions on one id, and exactly one canonical store is written
   (`state.db`; no `chat_sessions.json`);
5. `run_worker_task` maps the voice-task command's `qubit-voice` alias onto
   the canonical conversation and its receipt names the stored session;
6. the desktop PTT call site uses `_canonical_agent_session` and no longer
   hard-codes `session="qubit-voice"`.

Adjacent regressions run and passing (not re-verified as parity):

```
python -m pytest tests/test_agent8u.py tests/test_orchestrator_session_authority9u.py \
  tests/test_session_continuity9u.py tests/test_chat_session_authority8u.py \
  tests/test_chat_terminal_parity9u.py tests/test_chat_voice_e2e9u.py \
  tests/test_agent_tool_source8u.py tests/test_qbpv2_agent3_chat_store.py -q
# 80 passed, 1 xfailed
```

---

## 5. Uncertainty / open items

- **Terminal `/chat` session id** is chosen by the vendored TUI via the PTY
  bridge (`voicebridge/pty_ws.py`). It writes the same `state.db`, but this
  change does not map it onto the canonical conversation id. Whether the
  vendored TUI should adopt `agent.session_id` is an owner/upstream question.
- **Plain voice conversation** (`_handle_conversation`) is not unified with the
  canonical store; only the agent/worker paths (`run_session`) are. Unifying it
  would require reading/writing `self.conversation` through the canonical store
  and is a larger behavioral change.
- **Config default:** no `agent.session_id` default was added to
  `voicebridge/config.py` (out of scope). The canonical default is resolved in
  `agent.canonical_session_id`. If the owner wants the id to appear in the
  config manifest, that is a separate change.
- **Behavioral parity** of the desktop PTT / voice / chat surfaces is not
  claimed here; only the session-id convergence and context folding are
  implemented and unit-tested.
