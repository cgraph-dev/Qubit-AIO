# Verifying the dashboard terminal's state after a backend restart

Scope: the `/chat` terminal (`frontend/qubit-web/src/pages/TerminalChatPage.tsx`)
embedding the real TUI over a PTY (`qubit_runtime/qubit_cli/pty_bridge.py`,
`pty_session.py`, `web_routers/chat_ws.py`). **Read-only** — nothing here edits
code or restarts the service.

## Verdict

**There IS a dead-end state, but it is narrow.** The terminal auto-recovers
cleanly from a *typical* restart (`voicebridge run` bounce shorter than the
client's reconnect budget, ~7s). If the browser tab is held open through a
backend outage **longer than ~7s**, the client lands in a state where it shows a
banner and **never retries again without a reload / New chat**. This is proven
by code-reading; the *timing* boundary needs a live restart to confirm exactly.

## The client reconnect ladder (proven by reading `TerminalChatPage.tsx`)

1. Socket drops → `ws.onclose` (line 258) → `setState("closed")` → arms
   `setTimeout(…, 1500)` → `connect()` again. **`onclose` takes no event and
   reads NO close code** — a clean server teardown and a user/network close are
   indistinguishable to this page.
2. `connect()` (line 190) close-ticket path: `withBackoff(() => api.wsTicket())`
   → **5 attempts over ~7s total** (`withBackoff`, `lib/api.ts:792`, retries only
   transient `TypeError`/NetworkError, i.e. "backend unreachable").
   - On success → new `WebSocket` with the **same `attachRef` token** → server
     reuses the live PTY and replays its buffer (reload re-attaches, no kill).
3. If the whole ladder fails → `.catch` (line 270) → `setState("error")` +
   `setNotice("[WS_TICKET] …")`. **The catch schedules nothing.** No socket
   exists, so `onclose` never re-arms the 1500ms timer. The page is now parked
   until `?resume=` changes (`ChatSessionList` row / New chat) or a reload.

State note: `setState` is `const [, setState]` (line 96) — the value is
**discarded and never rendered**. The only visible feedback is `notice` and the
raw xterm. The rich banner machinery in `lib/pty-close-copy.ts`
(`PTY_RECONNECTING_BANNER`, `PTY_GAVE_UP_BANNER`, and the 4401 copy
*"the dashboard server was restarted"*) is **imported nowhere** — dead code from
the port. So the user sees a bare `[WS_TICKET] …` line, not the intended copy.

## State table

| # | State | Produced by | Verdict |
|---|-------|-------------|---------|
| a | Clean auto-reconnect, output replayed | close→1500ms→`connect()`; ticket mints within ~7s; server reuses `?attach=` PTY and replays buffer (`pty_session.py::attach`, `chat_ws.py:537`) | **Works** |
| b | Reconnected but empty/stale | Only if the PTY died but the registry kept a stale key — registry drops dead remnants (`attach_or_spawn`, `pty_session.py:193`), so **not reachable** in the normal path | Not reproducible |
| c | Stuck on error, no retry | `.catch` (line 270) runs; nothing re-arms the ladder | **Dead-end** |
| d | Stuck/loading, backend down | Same catch, with `withBackoff` exhausted while backend still down | **Dead-end** |
| e | Dead, manual reload required | Any of c/d persists past the 1500ms window — no timer exists to fire | **Dead-end** |

## WS close codes and whether the client distinguishes them

| Code | Meaning | Server site | Client handling |
|------|---------|-------------|-----------------|
| 1000 | normal | superseded socket close observed live | only `close_code` attr; `TerminalChatPage` ignores |
| 4410 | PTY child EOF (agent exited) | `pty_session.py:13,68` | ignored — treated as a plain close, reconnects |
| 4409 | socket superseded by a new attach | `pty_session.py:14,109` | ignored |
| 1011 | PTY start failed / Windows | `chat_ws.py:435,457` | ignored here (`QubitConsoleModal` handles codes; this page does not) |
| 4401/4403/4404/4408 | pre-accept refusals | `chat_ws.py:_ws_gate` | **not read** — `ptyRejectionBanner` exists but is unused |

**Conclusion:** `TerminalChatPage.tsx` does **not** distinguish a restart-close
from a user-close. Every code funnels into the same `onclose` → reconnect path.

## Proven vs unproven

| Claim | Basis |
|---|---|
| `?attach=` reuse replays the buffer, spawns no 2nd PTY | **Proven live** (script, below: `conn1 after=6`, `conn2 after=6`, `reused=yes replay=yes`) |
| Reconnect ladder = 1500ms + `withBackoff` ~7s, catch schedules nothing | **Proven by code-reading** |
| `onclose` ignores the close code | **Proven by code-reading** (`ws.onclose = () => {…}`) |
| `pty-close-copy.ts` is unused | **Proven by grep** (no import outside its own file) |
| The ~7s timing boundary between (a) and (c/d/e) | **Unproven** — needs a live restart with the tab held open; the script reports current reachability, not the browser timer |
| State (b) reachability | **Unproven / likely unreachable** — registry removes dead remnants |

## The verification script

`scripts/verify_terminal_restart_state.sh` (executable). It never restarts
anything; it inspects the *current* state and prints a PASS/FAIL/STATE verdict
per item. Token is read at runtime from the served page — never hardcoded.

```bash
# default BASE=http://127.0.0.1:8890, token auto-read from the page:
scripts/verify_terminal_restart_state.sh

# explicit:
scripts/verify_terminal_restart_state.sh http://127.0.0.1:8890 <session-token>
```

What it checks, in order:
1. reads `__QUBIT_SESSION_TOKEN__` from `/chat` (no token printed);
2. `GET /agent/api/health` → backend up? (state d if down);
3. `POST /agent/api/auth/ws-ticket` → ticket mints? (a hard refusal = state c/e);
4. **attach-reuse round-trip**: two `?attach=` connections on the same token,
   counting `ui-tui/dist/entry.js` processes before/after — proves state (a) is
   reachable (`conn2` replays, PTY count unchanged);
5. prints the verdict table and exit code (0 = definite, 1 = partial).

To exercise the (a)→(e) boundary: run the script before a restart (expect a
PASS on item 4), hold a browser tab on `/chat` **open** through the restart,
wait >8s, then re-run the script — it will report whether the backend is back and
whether a fresh attach would work, i.e. whether the stuck tab needed a reload.

## Confirmed exhaustion behaviour (client half, 2026-10-04)

**The client half of the dead-end now has a unit test.** `withBackoff` is proven
to *reject* once its bounded budget is spent — it does not retry forever and
does not swallow the error. Test: `exhausts the bounded budget and REJECTS (no
infinite retry, no swallow)` in `frontend/qubit-web/src/lib/api-backoff.test.ts`
(+ `fails a typed 409-style refusal immediately without consuming the budget`).
Run: `cd frontend/qubit-web && NODE_ENV=test npx vitest run src/lib/api-backoff.test.ts`
→ 11 passed (shell exports `NODE_ENV=production`; the override is required).
This confirms the state (c/d/e) mechanism: the exhausted ladder rejects, the
`wsTicket` `.catch` (TerminalChatPage line ~270) shows the banner and schedules
nothing, no socket exists, and `ws.onclose` never re-arms — wedged until reload.
The live boundary still needs a held-open tab through a >~7s outage (unproven).

## FIX APPLIED (2026-10-04) — the dead-end is closed

The exhausted-ladder `.catch` now schedules a **bounded outer retry** instead of
nothing, so a backend that comes back later re-attaches without a reload.

- **`TerminalChatPage.tsx`** — added `outerAttemptRef` (`useRef(0)`) and, in the
  `api.wsTicket()` `.catch`, a cancel-safe re-arm:
  `attempt = min(attempt+1, 20)`, `delay = min(30000, 2000 * 2**min(attempt,4))`
  (2s → 4s → 8s → 16s → 30s cap), `reconnectTimerRef = setTimeout(connect, delay)`.
  Guarded by `!disposedRef.current`; the timer is cleared on entry and
  `outerAttemptRef` is reset to 0 in `ws.onopen` (a live socket proves recovery).
- **Why this shape** — reuses the existing `disconnect`/`onclose` timer ref
  (`reconnectTimerRef`) and the existing `disposedRef` cancel pattern; no second
  retry mechanism, no new deps, no change to user-visible copy.
- **Verified:** `npx tsc -b` exit 0 · `NODE_ENV=test npx vitest run
  src/lib/api-backoff.test.ts` → 11 passed · `node node_modules/vite/bin/vite.js
  build` → built into `frontend/qubit-dist` · served shell references the new
  `index-B7zbOwCG.js` · `TerminalChatPage-C9eSSXIo.js` returns **200** from
  `http://127.0.0.1:8890`. Confirmed present in the shipped chunk:
  `.catch(e=>{…N.current&&clearTimeout(N.current);let e=P.current;P.current=Math.min(e+1,20);let
  t=Math.min(3e4,2e3*2**Math.min(e,4));N.current=setTimeout(()=>{N.current=null,Z.current()},t)})`.
- **Still unproven:** the live held-open-tab-through->7s-outage walkthrough. The
  unit + build evidence proves the retry is armed and shipped; only a real >7s
  outage with a tab open proves the recovery end-to-end.
