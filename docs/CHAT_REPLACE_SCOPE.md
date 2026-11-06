# Scope — replacing `/qubit-chat` with hermes-webui, absorbed natively

**Status:** scope committed alone at `060ff9232`. Implementation has begun per
step (e) — see **Implementation status** at the end of this document.
**Owner instruction under evaluation:** "fully replace `/qubit-chat` with
hermes-webui (MIT, `github.com/nesquena/hermes-webui`)."
**Source surveyed (read-only):**
`~/.local/state/qubit-voicebridge/runtime/cache/scratch/repo-survey/hermes-webui/`
at pin `3d9488fca6bb248c77f988bfbffcca9d8cfd9561` (verified: `git log -1` =
`3d9488f Merge pull request #8871 from nesquena/stage/1105-8866`).
**Local tree surveyed:** this worktree at `bbc73c68c`.

## Verdict up front

**A drop-in replacement is NOT safe, and I will not perform one.** Two facts,
both read from source, decide it:

1. **hermes-webui is a second agent stack, not a UI.** It eagerly imports
   `run_agent.AIAgent` into its long-lived server process
   (`api/streaming.py:1413` → `api/agent_runtime.py:439-451`) and 10 call
   sites resolve that class; it shells the `hermes` binary to restart a
   gateway (`api/gateway_restart.py:27-35`, command built at `:118-124`); and
   it opens the agent `state.db` for **writing** with a bare
   `sqlite3.connect` (`api/routes.py:35146`). Qubit has exactly one
   chat-transcript authority (`voicebridge/qubit_compat.py:848`,
   `CHAT_SESSION_AUTHORITY = "voicebridge.qubit_session_store.QubitSessionStore"`)
   with a **second-writer refusal** already implemented
   (`voicebridge/qubit_session_store.py:61-93`, `:219-226`). Dropping the
   foreign server in means a second writer of the same `state.db` — precisely
   the split-brain the store refuses by construction.
2. **hermes-webui has no voice path at all.** There is no push-to-talk, no
   `ControlRight` handling, and no TTS anywhere in `static/`. `/qubit-chat`
   is where Qubit's voice-to-voice claim lives; replacing the surface with a
   stack that cannot speak deletes the core product capability.

What *is* worth taking is presentation: the composer context ring, the
three-panel workspace browser, the composer footer control cluster, and the
Control Center. Those absorb into Qubit's own React surface as components,
reading Qubit's own endpoints, with the session authority untouched.

---

## (a) What `/qubit-chat` has that hermes-webui does not

Enumerated from `frontend/qubit-web/src/pages/ChatPage.tsx` (3193 lines) and
`frontend/qubit-web/src/pages/TerminalChatPage.tsx` (1766 lines).

| # | Capability | Qubit evidence | hermes-webui |
|---|---|---|---|
| 1 | **Push-to-talk / live transcription** through one controller spanning browser / local / terminal STT engines | `ChatPage.tsx:586-588` (`useTranscriptionController`), `:1963-1987` (`startVoice`/`stopVoice`/`cancelVoice`/`toggleVoice`) | **absent** — no speech-recognition code in `static/` |
| 2 | **Right-Ctrl hold-to-talk hotkey** with `isActive` gating and Escape-to-cancel | `ChatPage.tsx:1990-2028` (listener; `event.code === "ControlRight"` at `:2008`) | **absent** |
| 3 | **Desktop-helper hand-off** — yields Right-Ctrl when the system-wide helper is live | `ChatPage.tsx:591` (`useDesktopVoiceHelper`), `hooks/useDesktopVoiceHelper.ts:10-33` | **absent** |
| 4 | **Per-message TTS ("read aloud")** over Qubit's own `talk.speak` route | `ChatPage.tsx:94`, `:2663` (`ReadAloudButton`); `components/ReadAloudButton.tsx:31-71`; `hooks/useReadAloud.ts:23-29` (`speakTalk` → `POST /api/qubit/talk/speak`); `lib/ocFamilyTalkApi.ts:203-215` | **absent** — no TTS of any kind |
| 5 | **Voice-engine selection persisted to config** (`voice.input_engine`), optimistic with revert | `ChatPage.tsx:595`, `:1287-1301` (`handleEngineChange`) | **absent** |
| 6 | **Live voice-session follow** — polls the `voice-*` session and appends Right-Ctrl/wake turns without reload | `ChatPage.tsx:265-297` (`isVoiceSessionId`), `:857-897` (2 s poll, unchanged), header toggle at `:2141-2166` (`voice-live` at `:2156`) | **absent** |
| 7 | **Server-backed transcript on the single canonical store** — reload restores the real conversation | `ChatPage.tsx:6-13` (module doc), `:745-824`; `GET /api/qubit/chat/sessions/:id/messages` (`lib/api.ts:1164-1167`) | has its own transcript, but on its **own** store + `state.db` writer |
| 8 | **SSE turn stream through `voicebridge.chat_transport`** — the same module CLI and voice use | `ChatPage.tsx:72` (`streamChatTurn`), `lib/api.ts:3946-3951` (`POST /api/qubit/chat/stream`) | its own streaming server (`api/streaming.py`, 438 `def`s — a second engine) |
| 9 | **Staged-action confirm banner** restored on reload from `_pending_event_field` | `ChatPage.tsx:240-254` (`extractPendingConfirm`); `voicebridge/qubit_compat.py:8039-8046` | absent (no Qubit staged-action model) |
| 10 | **Slash catalog merged from the live registry** (skills/plugins/MCP/toolsets) with a static fallback | `ChatPage.tsx:545-566`, `:1334-1350`; `lib/slashCommands.ts`; `GET /api/chat/slash-commands` (`lib/api.ts:1176-1177`) | has `/` commands but from its own CLI registry |
| 11 | **`@tool` mention insertion** from the tools panel into the composer | `ChatPage.tsx:1312-1327` (`insertToolMention`) | absent |
| 12 | **Image / file attachment by paste and picker** | `ChatPage.tsx:59` (`firstImageFromClipboard`), `:659-663` (attach state) | limited (its own upload route) |
| 13 | **Per-session model q in the work panel** + reasoning selector persisted per session | `ChatPage.tsx:600-617` (+ the ring's own state at `:622-623`), `:1269-1284` (`handleReasoningChange` → `PATCH /api/qubit/chat/sessions/:id`) | model picker exists; Qubit's per-session `model_config` persistence does not |
| 14 | **Reader/worker task-progress frames** (`/chat-worker` builds, sources, reports) | `ChatPage.tsx:135-139` (`TaskProgress`), `:573` (state), `:1123-1152` (frame handling); report rendering at `:1109-1113` and `:2675` | absent |
| 15 | **Plugin slots inside the chat surface** | `ChatPage.tsx:110` (`PluginSlot`) | has its own extension system (not loaded by Qubit) |
| 16 | **Command palette over recent reports** | `ChatPage.tsx:95`, `:1842`, `:647` | command palette exists (different data source) |
| 17 | **Embedded PTY terminal at `/chat`** with ws-ticket auth, WebGL addon, resume sanitizer | `TerminalChatPage.tsx:1-8`, `:17`, `:40`, `:180`, `:582` | has `static/terminal.js` (888 lines), not Qubit's PTY contract |
| 18 | **Qubit policy refusal of raw PTY on the chat surface** (`PTY_NO_SHELL`) | `ChatPage.tsx:3-8` (module doc) | no equivalent policy layer |

Items 1–6 are the voice path: **not optional, not replaceable by
hermes-webui**. Items 16–18 a fortiori: `/chat` is a different surface and
this scope does not touch it.

---

## (b) hermes-webui capabilities worth absorbing

Read from `api/` and `static/`. All four are **presentation**; none requires
its runtime.

1. **Context token ring** — `static/index.html:756` (`#ctxRingValue` SVG
   circle) with the redraw at `static/ui.js:9858-9870`
   (`updateCtxRing`: `stroke-dashoffset = circumference * (1 - pct/100)`,
   colour `≤50` green / `≤85` orange / `>85` red) and the honest-maths
   contract at `static/ui.js:9940-9972`: percent is derived from
   **`last_prompt_tokens` only**, never from cumulative `input_tokens`
   ("dividing it by the context window gives a nonsense percentage"), and
   falls back to `·` + a raw token count when no prompt measurement exists.
   *Absorb:* the ring + that exact honesty rule. **Qubit already serves both
   inputs** — `effective_context_length` via `getModelInfo`
   (`lib/api.ts:4522`, inside `ModelInfoResponse` at `:4517`) and
   `receipt.usage.prompt_tokens` on the SSE `done` frame
   (`voicebridge/chat_transport.py:4293-4302`, `:5307-5315`).
   No backend change is required.
2. **Three-panel layout with a workspace file browser** — the desktop/mobile
   rail at `static/index.html:171-188`, the session filter box at `:205`,
   and the Spaces/workspace browser `static/workspace.js` (2020 lines,
   tree + escape-grant handling at `:175-302`). *Absorb:* the layout idea and
   the workspace affordance only. Qubit already exposes
   `GET /api/chat/workspaces` (`lib/api.ts:1063-1066`, typed at `:3634-3651`)
   and `ChatSessionList` already accepts a `workspaceCwd` prop
   (`components/ChatSessionList.tsx:96-97`). Do **not** port
   `workspace.js`'s escape-grant model — it is a security surface Qubit did
   not design.
3. **Composer footer control cluster** — `static/index.html:694`
   (`#composerMobileConfigBtn`, "Workspace, model, quota, reasoning and
   context settings") feeding the popover render at `static/ui.js:9832-9930`
   (`_syncMobileCtxDisplay`). *Absorb:* the one-footer-cluster idea.
   `/qubit-chat` already has every control it would hold
   (`ChatPage.tsx:598-613` model/reasoning, `:1238-1270` persistence); they
   are currently split between the side panel and the header.
4. **Control Center** — `static/i18n.js:668-672`
   (`settings_heading_title: 'Control Center'`, "Preferences, conversation
   tools, and system controls") with sectioned navigation at
   `static/index.html:385-397`. *Absorb:* the grouping vocabulary for the
   chat side panel only. Qubit's `ConfigPage` is the dashboard's settings
   home and is **out of this lane's ownership** — this scope does not
   re-home it.

**Explicitly NOT worth absorbing:** `api/streaming.py` (438 `def`s — a whole
second agent loop), `agent_*` modules, `gateway_*`, `mcp_runtime`, `metering`,
`oauth`/`passkeys`/`auth_oidc`, and all of `static/ui.js` / `sessions.js` /
`panels.js` as code (84 + 72 `localStorage`/`sessionStorage` touchpoints;
a foreign client-state model that would fight Qubit's `ProfileKeyedRoutes`
remount contract at `App.tsx:1294-1310`).

---

## (c) The voice path — every symbol, and how it survives

Line numbers are `frontend/qubit-web/src/pages/ChatPage.tsx` unless stated.
**None of these symbols is modified by this lane's implementation.** The
guarantee is structural: the voice machinery sits in the page's state layer
and the composer, and the components absorbed in step (e) are *siblings* that
read props — they never touch the transcription controller, the hotkey
listener, or the read-aloud hook.

### Push-to-talk

| Symbol | Where | Survives how |
|---|---|---|
| `useTranscriptionController({ sessionId })` → `{ controller, snapshot: voice }` | `:586-588`; impl `hooks/useTranscriptionController.ts:20` | untouched — the absorbed components receive no reference to `controller` |
| `startVoice` / `stopVoice` / `cancelVoice` | `:1963-1980` | untouched |
| `toggleVoice` (composer mic button) | `:1985-1987`; button at `:3058-3065` | untouched — the mic button stays in the composer, byte-identical |
| committed-text append effect (`joinTranscript`) | `:1938-1947` | untouched |
| browser-engine auto-send effect | `:1953-1961` | untouched |
| `inputEngine` + `handleEngineChange` (`voice.input_engine`) | `:595`, `:1287-1301` | untouched |
| interim/live transcription strip | `:2948` | untouched |

### Right-Ctrl wake

| Symbol | Where | Survives how |
|---|---|---|
| `rightCtrlHeldRef`, `rightCtrlHeld` state | `:679`, `:682` | untouched |
| the `keydown`/`keyup` capture listener, `event.code === "ControlRight"`, Escape cancel, `isActive` gate | `:1990-2028` (`ControlRight` at `:2008`, `onKeyDown` at `:2003`, effect deps at `:2028`) | untouched — the effect's dependency list is unchanged, so it re-installs exactly as today |
| `useDesktopVoiceHelper(isActive)` → `desktopHelperLive` | `:591`; `hooks/useDesktopVoiceHelper.ts:10-33` | untouched — the listener still yields when the system helper is live |
| `isActive` prop from the persistent host | `App.tsx:1274` (`<ChatPage isActive={isNativeChatRoute} />`) | untouched — `App.tsx` is not in this lane's file set |

The **wake word itself** is not a frontend symbol: it is the desktop helper
(`voicebridge/desktop_helper.py:1528`, `:2779`, `:2810`, `:2888`). This lane
does not touch `voicebridge/`. Wake turns reach `/qubit-chat` through the
`voice-*` session follow (item 6 in table (a)), which stays.

### TTS

| Symbol | Where | Survives how |
|---|---|---|
| `<ReadAloudButton messageId text />` on every assistant message | `ChatPage.tsx:94`, `:2663`; `components/ReadAloudButton.tsx:31-71` | untouched — it renders inside the transcript list, which no absorbed component replaces |
| `useReadAloud(deps)` / `defaultReadAloudDeps.synthesize` → `speakTalk` | `hooks/useReadAloud.ts:23-29`, `:36-51` | untouched |
| `speakTalk` → `POST /api/qubit/talk/speak` | `lib/ocFamilyTalkApi.ts:203-215` | untouched — no new TTS route is introduced |

**Rule for implementation:** a component added under
`components/chat/` may read *display* props (`percent`, `used`, `window`,
`label`) only. If a change would need to unmount the composer, move the
hotkey listener, or route speech through anything other than `speakTalk`,
it does not get made — it gets recorded here instead.

---

## (d) Session authority — who writes `state.db`

**Confirmed: `QubitSessionStore` remains the only writer.**

- Declaration: `voicebridge/qubit_compat.py:848`
  `CHAT_SESSION_AUTHORITY = "voicebridge.qubit_session_store.QubitSessionStore"`.
  The comment above it (`:838-847`) states `/qubit-chat` and the
  terminal/TUI/agent all read/write that ONE database.
- The router *re-checks* it: `voicebridge/qubit_compat.py:874-878` refuses
  when `writable["chat_transcript"] != CHAT_SESSION_AUTHORITY`.
- The adapter is a strict wrapper over the vendored `SessionDB`:
  `voicebridge/qubit_session_store.py:360-363`
  (`BACKEND = "qubit_state.SessionDB"`), opened at `:219-226`.
- The single-writer invariant is enforced in code: `_WRITE_OWNERS`, `_WRITE_LOCK`
  (`:52-57`) and `SessionDbWriteAuthorityError` (`:61-93`), which raises
  *before any write* if a second owner registers on the canonical store.
- This lane adds no Python and no new endpoint. The frontend continues to
  reach the store only through `GET/POST/PATCH /api/qubit/chat/*`
  (`lib/api.ts:1157-1218`, `:3946`).

**hermes-webui code paths that would open `state.db` itself — the
disqualifiers:**

| Path | What it does | Why it disqualifies |
|---|---|---|
| `api/streaming.py:1413` → `api/agent_runtime.py:439-451` | eager `from run_agent import AIAgent` into the long-lived server process | a second agent runtime over the same DB; also the reason Qubit vendored its own runtime |
| `api/streaming.py:1428`, `api/routes.py:19058,31646,32607,34802,35478` | 5 more `require_ai_agent_class()` / `get_ai_agent_class()` call sites (10 total incl. `agent_runtime.py`) | each constructs agents that persist turns to `state.db` independently |
| `api/routes.py:35146` | `sqlite3.connect(str(db_path))` on `hermes_home / "state.db"` (`:35135`) — **read-write** | a bare writer on the canonical file: exactly the split-brain `_WRITE_OWNERS` exists to refuse |
| `api/routes.py:12195`, `:12277`; `api/session_discoverability.py:106`; `api/session_recovery.py:578,643`; `api/gateway_delegation_wakeup.py:89,136,186`; `api/models.py:10974`; `api/agent_sessions.py:89` | `mode=ro` reads of the agent DB | read-only, but each assumes a schema and a home Qubit does not guarantee; and they arrive with the writers above |
| `api/gateway_restart.py:27-35`, `:118-124` | resolves the `hermes` binary and spawns `hermes gateway restart` | shells the upstream CLI — forbidden by this lane's rule 3 |
| `api/startup.py:28-45`; `bootstrap.py:114-115` | resolves an agent root via the `hermes` console-script shebang | same |

On this host `~/.hermes/state.db` does not exist; the live store is
`~/.local/state/qubit-voicebridge/runtime/state.db` (790 MB). So "it points
somewhere else" does not save it — hermes-webui resolves its home from
`HERMES_HOME`/platform default (`api/config.py:276-281`), and pointing that at
the Qubit runtime is the only configuration in which it is useful at all.
**Verdict: importing it as a second stack is a data-corruption risk, not a
style preference.** It is refused.

---

## (e) Ordered implementation plan (file level)

Only what (a)–(d) justify. Each step is its own commit; the voice path and
the store contract are assertions, not side effects.

**Step 0 — this document.** `docs/CHAT_REPLACE_SCOPE.md` alone.

**Step 1 — context ring component (pure display).**
- New `frontend/qubit-web/src/components/chat/ContextRing.tsx`.
  Props: `{ percent: number | null; usedTokens?: number; windowTokens?: number; label?: string }`.
  Renders the SVG ring with hermes-webui's colour thresholds
  (`ui.js:9862-9866` mapped to tokens, not literals) and `aria-label` text.
  `percent === null` renders `·` — the honest no-data branch from
  `ui.js:9982-9984`. **No fetch, no store access, no timer.**
- New `components/chat/ContextRing.test.tsx`: null → `·`; 50/85/100
  boundaries; aria-label contains both numbers.
- Mount in `ChatPage.tsx` composer footer, fed from data the page *already
  holds*: `effective_context_length` from `getModelInfo`
  (`ChatPage.tsx:947-961`, `setContextWindow` at `:958`) and `receipt.usage.prompt_tokens` from the `done`
  frame. Additive only — no existing line moves.

**Step 2 — composer footer control cluster.**
- New `components/chat/ComposerFooterControls.tsx` hosting, as *children*
  passed by the page, the existing reasoning selector and the ring. The
  page keeps ownership of every handler (`handleReasoningChange`,
  `handleEngineChange`) — the component is layout.
- Test: renders the mic button and the read-aloud button it is handed
  unchanged; failing this test is the tripwire that voice was dropped.

**Step 3 — three-panel shell (opt-in, reversible).**
- New `components/chat/ChatPanels.tsx`: a thin layout wrapper over the
  existing `qchat__side` / transcript / composer regions with the
  workspace affordance sourced from `api.getChatWorkspaces`
  (`lib/api.ts:1063`).
- Gated behind a query flag (`?layout=3p`) first, default off; the existing
  layout stays the default until a screenshot comparison is signed off.
  **The persistent host (`App.tsx:1249-1280`) is not touched**, so a broken
  layout cannot take `/chat` or the voice host with it.

**Step 4 — Control Center grouping labels** for the side panel sections,
following `i18n.js:668-672` vocabulary, in `lib/chat-routes.ts`-adjacent
constants only if a route decision is actually involved (it is not — keep it
in the component).

**Step 5 — retire the old surface? NO.** `ChatPage.tsx` stays. It *is* the
replacement surface (steps 1–4 augment it in place). Deleting it is only
possible if a future lane ports the voice path to a new page, and that is a
different lane (rule 5 and rule 1).

### What is explicitly refused

- **Not done:** vendoring or embedding hermes-webui's Python, its `static/`
  bundle, its server, or any `api/` module. No `run_agent.AIAgent` import, no
  `hermes` binary shell-out, no second `state.db` writer.
- **Not done:** deleting or replacing `pages/ChatPage.tsx`,
  `pages/TerminalChatPage.tsx`, or `lib/chat-routes.ts`'s two-route
  contract (`CHAT_ROUTE_PATHS`, `lib/chat-routes.ts:12-36`) — `/chat` is a
  different surface and the voice path lives on this one.
- **Not done:** changing `frontend/qubit-web/src/App.tsx`'s persistent host,
  `voicebridge/`, `frontend/qubit-app/`, or the sidebar IA. Each is another
  lane's file set; the nav entry `QUBIT_CHAT_NAV_ITEM` (`App.tsx:277-286`)
  and its `/webchat` child stay reachable.
- **Not done:** reproducing hermes-webui's `workspace.js` path-escape grant
  model. Qubit does not own that security surface and will not adopt a
  foreign one.

---

## Implementation status

| Step | State | Commit |
|---|---|---|
| 0. Scope document | **done, committed alone** | `060ff9232` |
| 1. Context ring (pure display) | **done** | `977736d64` (component + unit tests), `fd299828c` (mounted) |
| 2. Composer footer cluster | not started — the ring already sits in the existing footer beside the voice-engine and reasoning pickers, so a separate layout component has not yet earned its keep |
| 3. Three-panel shell | not started — deliberately not attempted before a screenshot sign-off; the existing layout is still the default and `App.tsx` is untouched |
| 4. Control Center vocabulary | not started |
| 5. Retire `ChatPage.tsx` | **refused** — see (e) step 5; this page *is* the replacement surface |

### What step 1 actually does

- `components/chat/contextRingMath.ts` — `contextPercent`, `contextPressure`,
  `contextRingLabel`, `fmtContextTokens`. The percentage rule has exactly one
  home here.
- `components/chat/ContextRing.tsx` — the SVG ring. Props in, no fetch, no
  timer, no store access.
- `ChatPage.tsx` — adds two display-only state values (`contextWindow`,
  `lastPromptTokens`), reads the window from the `getModelInfo` call the page
  already makes, reads `receipt.usage.prompt_tokens` in the existing `onDone`
  handler, and mounts `<ContextRing>` inside the existing composer form
  footer. No existing statement was reordered or removed.
- `lib/api.ts` — adds the optional `receipt.usage` shape to
  `ChatStreamEvent`. The backend already sends those fields
  (`voicebridge/chat_transport.py:4294-4302`, `:5307-5315`); this only stops
  the client from ignoring them.

### How voice is proven intact

`pages/ChatPage.contextRing.test.tsx` (5 tests) drives the real controls with
the ring mounted:

- the real Voice button (`aria-label="Voice input"`) still starts speech
  recognition (`FakeSR.instances[0].started === true`);
- the real `ControlRight` `keydown` on `window` still starts recognition;
- the per-message read-aloud control (`[data-testid^="read-aloud-"]`) is still
  rendered in the transcript;
- with no turn receipt the ring reports "unmeasured" and never converts the
  session's cumulative `input_tokens` (900 000 in the fixture) into a
  percentage;
- after a turn it reports `50 000 / 200 000` as "25% used".

Full chat surface: **142 tests pass across 16 files**; `tsc -b` clean; eslint
reports **0 warnings on the new files** (the 7 pre-existing `ChatPage.tsx`
warnings are identical to the `main` baseline and were not introduced here).

**Falsifiability:** flipping the ring's no-measurement branch to report `0%`
(one-line mutation, reverted) fails 3 assertions across the two suites — the
tests are capable of failing, so their pass means something.

### Not claimed

No backend change, no `voicebridge/` change, no `App.tsx` change, no nav
change, and no deletion. This is an additive absorption that a reviewer can
revert per-commit. `CHAT_SESSION_AUTHORITY` is untouched and still the only
writer.
