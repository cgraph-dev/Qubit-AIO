# RPC boot-path trace — why the dashboard PTY never reaches the auto-continue gate

Read-only diagnosis of the measured negative: after `systemctl --user restart qubit-voicebridge`,
the dashboard `/chat` PTY resumes the session (history intact, `HERMES_TUI_RESUME` preserved) but
`_maybe_schedule_auto_continue` is never entered, so the interrupted turn is not re-issued.

All paths are relative to `qubit_runtime/`. Line numbers are the source as of commit `a221b5fd`
(dist mtime 2026-10-02 is newer than the `.ts`/`.py` sources touched 2026-09-30, so the running
`ui-tui/dist/entry.js` matches source: `dist` carries the same branches at `entry.js:70710` and
`:70720`).

## Process topology (verified live, not inferred)

`systemctl --user status qubit-voicebridge` process tree:

```
2543483  python -m voicebridge run            # dashboard backend / service main
 └─ 2544031  node .../ui-tui/dist/entry.js    # the embedded TUI (the PTY child)
     └─ 2544052  python -m tui_gateway.entry  # TUI's OWN stdio JSON-RPC gateway
```

`/proc/2544031/environ` contains `HERMES_TUI_RESUME=20261003_093120_d09dc4`, `HERMES_TUI_DASHBOARD=1`,
`HERMES_TUI_INLINE=1`, `QUBIT_HOME=…/runtime` — and **no `HERMES_TUI_GATEWAY_URL`**. So the TUI did
NOT attach to the dashboard backend's in-memory gateway over `/api/ws`; it spawned its own
`tui_gateway.entry` child. `/proc/2544052/fd` shows that child holds `state.db` (+ `-wal`/`-shm`), i.e.
it is the process that owns the live resumed session.

## Question 1 — does the client reach the `STARTUP_RESUME_ID` branch (createGatewayEventHandler.ts:743)?

**Not on a reconnect; yes on a cold PTY boot — but both paths call `resumeById`, so this is not the break.**

`handleReady` (qubit_runtime/ui-tui/src/app/createGatewayEventHandler.ts:681) is called from the
`gateway.ready` case at :815-816. Its branches, in order:

1. `:727-741` — `recoverSid = recoverSidRef?.current`; if non-null → `resumeById(recoverSid)` and **`return`**.
2. `:743-749` — `if (STARTUP_RESUME_ID) { …; resumeById(STARTUP_RESUME_ID); … return }`.
3. `:757-782` — `display.tui_auto_resume_recent` → `session.most_recent` → `resumeById(target)`, else `newSession()`.

`recoverSidRef` is populated by the exit handler in `useMainApp.ts` (`:1001`, `:1027`, `:1040`) whenever
the gateway connection drops and a recovery is planned. Reload of `/chat` in a browser whose TUI has
already lost+respawned its gateway therefore takes branch 1, which returns **before** line 743.
A freshly spawned TUI (`recoverSidRef.current === null`) takes branch 2. Either way `resumeById` runs,
so an earlier-branch return does **not** explain the missing gate.

## Question 2 — does `resumeById` issue `session.resume`?

**Yes, literally `'session.resume'`.** qubit_runtime/ui-tui/src/app/useSessionLifecycle.ts:374:

```ts
gw.request<SessionResumeResult>('session.resume', { cols: colsRef.current, session_id: id })
```

Params are exactly `{ cols, session_id }` — no `lazy`, no `defer_history`, no `eager_build`, no
`omit_messages`. (Confirmed in `dist/entry.js:78852`.)

## Question 3 — is `session.resume` registered for the dashboard PTY transport?

**Yes.** The PTY child is a plain `tui_gateway.entry` stdio process (topology above), the same gateway
the desktop/TUI use. `session.resume` is a declared method: qubit_runtime/tui_gateway/methods_session.py:917
(`@method("session.resume")`) and is in the dispatch allowlist at qubit_runtime/tui_gateway/server.py:174.
There is no separate dashboard-only resume entrypoint.

## Question 4 — is there an env/config guard that suppresses resume in dashboard mode?

**No.** `DASHBOARD_TUI_MODE` (qubit_runtime/ui-tui/src/config/env.ts:60) and `HERMES_TUI_INLINE` (:74) only
affect rendering/host behaviour; nothing in `_maybe_schedule_auto_continue` or the `session.resume`
dispatch reads them. `display.tui_auto_resume_recent` only picks branch 3 above. No config guard.

## The break

`session.resume {cols, session_id}` → all flags false → dispatch at methods_session.py:932-942:

```
932  ctx.profile_resume_cwd = ...
934  with _session_resume_lock:
935      live = _find_live_session_by_key(ctx.target, ctx.profile_home)
936  if live is not None:
937      return _resume_reuse_live(ctx, *live)          # <-- returns here; GATE NEVER CALLED
938  if ctx.lazy: return _resume_lazy(ctx)              # gate not called either
940  if ctx.eager_build: return _resume_eager(ctx)      # gate at :914
942  return _resume_deferred(ctx) if ctx.defer_history else _resume_cold(ctx)
```

`_maybe_schedule_auto_continue` has exactly three call sites in the tree
(`grep -n _maybe_schedule_auto_continue tui_gateway/*.py`):

| Call site | Path | Reached when |
|---|---|---|
| methods_session.py:**853** | `_resume_cold` | cold resume, and only past the early return at :847 |
| methods_session.py:**914** | `_resume_eager` | `eager_build=1` (not sent by `resumeById`) |
| server.py:**2788** | `_schedule_resume_hydration` (deferred thread) | `defer_history=1` (not sent by `resumeById`) |

The primary cold path guards the gate behind an opaque early return (methods_session.py:847-848):

```
847  if (reused := ctx.claim(sid, record)) is not None:
848      return reused            # <-- returns BEFORE :853; gate never entered
```

`ctx.claim` → `_claim_or_reuse_live(sid, self.target, record, None)`
(server.py:2670). Under `_session_resume_lock` it calls `_find_live_session_by_key(session_key, profile_home)`
(server.py:2863); if a live, non-finalized session with the same stored key exists **in this gateway
process**, it returns that `(sid, session)` tuple and `claim` returns it → `_resume_cold` returns it →
line 853 is skipped.

**Single line where the chain breaks:** `qubit_runtime/tui_gateway/methods_session.py:848` (`return reused`,
inside `_resume_cold`) — and its unconditional sibling at **:937** (`return _resume_reuse_live(...)`),
which has no gate in `_resume_reuse_live`/`_resume_reuse_live_locked` (:744-766) at all. Both return a
**valid, successful resume payload** (`_live_session_payload`, server.py:2930, with intact `history`/`messages`),
so the client shows a healthy resumed chat while the gate is silently never reached. This exactly matches
the measured negative: no `[ACPROBE]` at the gate's entry, and no gate-internal log — the gate was never
entered, not entered-and-refused.

### What is proven vs. inferred (honest boundary)

- **Proven from code:** params from `resumeById` are all-false (Q2); `session.resume` is dispatched and
  registered (Q3); no env/config guard (Q4); the client calls `resumeById` on both `gateway.ready`
  branches (Q1); the gate is unreachable from `_resume_reuse_live` (:937, :744-766) and is skipped by the
  `ctx.claim` early return (:848) — and these two returns both yield a valid resume response, matching
  "session resumes but no auto-continue".
- **Proven live:** the dashboard PTY runs its own `tui_gateway.entry` child that owns `state.db`, i.e. the
  live session lives in *that* gateway process.
- **NOT proven from static code:** *which* of the two gate-skipping returns fires on the operator's exact
  restart sequence. A genuinely fresh `tui_gateway.entry` has an empty `_sessions`, so its *first*
  `session.resume` would take `_resume_cold` and hit line 853 — unless a live session for that key is
  already registered in that process (a reconnect/reattach, or a second resume). I could not reproduce the
  operator's live marker state to observe this (the production marker file
  `~/.local/state/qubit-voicebridge/runtime/desktop/interrupted_turns.json` does not currently exist;
  markers are cleared on any turn conclusion). The mechanism that makes the reuse path fire is the
  Node TUI's reconnect flow re-issuing `session.resume` against a gateway that still holds the live
  session; that is consistent with the topology but not, by itself, observed here.

## Recommended minimal fix (NOT applied)

Make the interrupted-turn check independent of *which* resume branch runs, and independent of the
`ctx.claim` race. Two options, smallest first:

1. **Call the gate on the reuse path too.** In `_resume_reuse_live_locked` (methods_session.py:752),
   before `return _ok(...)`, add the same
   `auto_continue=_maybe_schedule_auto_continue(sid, session, ctx.target)` and thread it into the payload
   (mirroring :853). This covers :937 and any `ctx.claim`-winner reuse, since both funnel through here.

2. **Or hoist the gate out of the branch bodies.** Compute
   `ac = _maybe_schedule_auto_continue(...)` once in the `session.resume` handler (methods_session.py:917)
   after the live/found session is known, and attach it in `_resume_response` for all cold/eager/reuse
   paths. The gate already self-guards (`read_turn_marker` returns None when no marker, and it bails on
   stale/disabled/`_auto_continue_scheduled`), so calling it on an already-live session is safe and
   idempotent — the `session["_auto_continue_scheduled"]` latch at session_auto_continue.py:75-77 prevents
   double scheduling.

Either way the returned payload should carry the `auto_continue` descriptor (methods_session.py:783-784)
so the client can render the continuation. No change to the client is required — `resumeById` already
sends the right RPC.
