# /qubit-chat model panel: two remaining bugs (2026-10-07)

Owner report (verbatim): *"i changed to qubit brain and it didnt update live in ui, I had to
refresh"*.

## Bug A — the panel does not re-read the session model after a change

**Symptom:** pick a model in `/qubit-chat`; the panel keeps showing the old one until a page
refresh.

**Measured wiring (all present, so the bug is in the refresh, not the plumbing):**
- `ChatPage.tsx:556` — `const [sessionModel, setSessionModel] = useState<string>("")`
- `ChatPage.tsx:2841` — `terminalModel={sessionModel ? { model: sessionModel } : null}`
- `ChatPage.tsx:2844` — `onSessionModelChange={setSessionModel}`
- `ChatPage.tsx:707-715` — loads the stored model ONCE per session change:
  `withBackoff(() => api.getChatSession(session)).then(info => setSessionModel(String(info.session?.model || "")))`
- `ChatSidebar.tsx:155-175` — `refreshEffectiveModel` is a `useCallback` whose deps include
  `terminalModel?.model`, so it SHOULD refetch when the prop changes.

So the state exists, the prop is passed, and the callback is reactive — yet the UI does not
update. **Find where the chain breaks** (a stale closure, `sessionModel` not actually set by
the panel's own write path, the write going to a different session id, or `ChatSidebar`
keeping `effectiveModel` after the prop changes). Reproduce first in a real browser
(Playwright is installed: `.venv/bin/python`, chromium headless) — change the model through
the UI, wait, and log what `model/info` is requested and what the panel displays.

**Note a real observation from the live probe:** on a fresh load of `/qubit-chat` the panel
issues `GET /agent/api/model/info?model=deepseek-v4.1-flash` — the GLOBAL default — and the
session it loads (`dashboard-chat`) genuinely stores `deepseek-v4.1-flash`. So for that
session the display was correct. Determine whether the owner's change landed on a DIFFERENT
session than the one displayed, or whether the write succeeded and only the display lagged.
Do not assume; show the session id, the stored model before/after, and the displayed value.

## Bug B — the picker offers 3 local models that cannot answer (owner chose option 2: hide them)

**Owner decision: mark the broken ones non-chat-capable so the picker stops offering them.**

Measured with the probe in `docs/QUBIT_LOCAL_MODEL_EMPTY_OUTPUT_2026-10-07.md`
(`content` non-empty AND `done_reason == "stop"` = PASS):

| Model | Verdict |
|---|---|
| `qubit-brain` | ✅ PASS |
| `qubit-cand-q4` | ✅ PASS |
| `qubit-cand-q5` | ✅ PASS |
| `qubit-brain4-pre4b` | ✅ PASS |
| `qubit-spark17` | ❌ FAIL — empty content, `done_reason=length` |
| `qubit-spark4` | ❌ FAIL — same |
| `qubit-brain4` | ❌ FAIL — same |

**Deliverable:** remove the `chat` role from exactly `qubit-spark17`, `qubit-spark4`,
`qubit-brain4` (they stay registered for any other role they legitimately hold), so
`_ollama_options_row` (`qubit_compat.py:~1903`, which filters on
`kind==LOCAL and enabled and "chat" in (p.roles or ["chat"])`) no longer lists them.
**Do NOT touch** `qubit-brain`, `qubit-cand-q4`, `qubit-cand-q5`, `qubit-brain4-pre4b` — all
four genuinely work and must stay selectable.

Also: the workspace default model must not be a hidden one. The global `model.default` is
currently `qubit-brain` (works) — keep it.

## Also worth a look (low priority, report only)

When a pinned model returns empty, the agent's overflow-recovery path misreports it as
*"This conversation has grown too long for <model> to read…"* even on a fresh 1-message
session. The real cause is empty output, not an over-long conversation
(`qubit_runtime/agent/turn_failure_copy.py:269` copy, chosen by
`turn_overflow.py`). Fixing the classification would stop the operator chasing a phantom
context problem. Change only the reporting/copy — do NOT change overflow behaviour itself.

## RULES

- **`/chat` must not regress** — never edit `frontend/qubit-web/src/pages/TerminalChatPage.tsx`
  or `qubit_runtime/tui_gateway/`. Both must stay EMPTY diffs.
- Do not change an existing session's stored model except where its own panel explicitly does.
- Service: `systemctl --user restart qubit-voicebridge.service` (~18 s), port 8890. Token:
  `TOK=$(curl -sL --max-time 6 http://127.0.0.1:8890/ | grep -o '__QUBIT_SESSION_TOKEN__="[^"]*"' | sed 's/.*"\(.*\)"/\1/')`.
- Frontend: `cd frontend/qubit-web && npx vitest run` (1049 pass today) +
  `npx tsc -p tsconfig.json --noEmit` + `npm run build`.
- `execute_code` may fail in a subagent with a stale-module NameError — use `terminal` with
  explicit `.venv/bin/python`.
- The repo is shared with an OpenCode/VSCode upstream-sync agent in `qubit_runtime/` —
  surgical edits; re-check `git status` before restarting.
