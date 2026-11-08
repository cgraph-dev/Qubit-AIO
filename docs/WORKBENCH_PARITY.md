# Workbench chat — the stream deadlock, and the state after fixing it

Written by driving the running server, not by reading code. Every claim has a
command or a test behind it.

## The bug: the reader took its own lock twice

Every turn returned `start: 200`, opened the SSE stream, received
`: connected`, and then delivered nothing. The pump was healthy the whole
time — it called `chat_transport`, pulled the model's reply and pushed
`token` + `done` into the run buffer in about 5 seconds.

The cause, in `chat_stream_frames`' follow loop:

```python
with run.changed:                     # Condition wrapping run.lock
    if not run.snapshot(cursor)[0]:   # snapshot() takes run.lock AGAIN
        run.changed.wait(_POLL_S)
```

`_Run.changed` is `threading.Condition(self.lock)` and `snapshot()` acquires
that same **non-reentrant** `threading.Lock`. The reader deadlocked against
itself on the first iteration and never reached `wait()`: it sat holding the
lock, so the pump's `notify_all()` had nothing to wake and `push()` blocked
behind it. One bug, two symptoms — a silent client and a wedged pump.

### How it was found, and the two diagnoses before it

Both earlier theories were wrong and are recorded so they are not repeated:

- **`_pty_lock` held across `AgentPtySession` construction.** A real defect
  (fixed in `2626f7751`), but not this bug — turns still hung after it.
- **"20 `wb-chat-*` threads deadlocked against each other."** Also wrong. Those
  were the turn's own workers; `comm` truncates at 15 chars, so they only
  looked like one thread. The pump was never stuck in the transport at all.

What settled it was tracing **both sides** into the journal:

```
[wb-read <id>] _get -> True runs=1 after_seq=0     # reader found the run
[wb-pump <id>] event: token                        # pump buffered the event
(nothing from the frame-yield trace)               # reader never left the loop
```

A reader that has found the run and a pump that has pushed an event, with no
frame crossing between them, is a lock problem inside the reader — not a model,
PTY or network problem.

## Fixed and verified

| Fix | Commit | Evidence |
|---|---|---|
| Stream reader no longer re-enters its own run lock | `3fbab89c0` | live: `token` → `done` in ~5 s; browser reply rendered in 4 s via `#btnSend` |
| A dead pump no longer locks its session out | `fb671c98b` | two consecutive turns on one session both `200` (the second was `409` before) |
| Model picker returns real models | `961cba3bb` | `/wb/api/models`: 43 entries, `active: deepseek-v4.1-flash` |
| PTY lock no longer held across construction | `2626f7751` | `tests/test_pty_lock_scope.py`: 3 passed |
| Runs that can never settle expire | `ea70402c0` | `_HARD_MAX_S` backstop, now covered by `test_workbench_run_gc.py` |
| Voice path intact | — | `test_qubitchat_voice_continuity9u.py` + `test_barge_stream.py` |

Live turn, verbatim:

```
: connected
event: token  {"text": "SHIPPED", "seq": 1}
event: done   {"status": "completed", "reply": "SHIPPED", "elapsed_ms": ~5000}
```

## Tests that pin it

`tests/test_workbench_stream_deadlock.py` and `tests/test_workbench_run_gc.py`.
Both were proven to fail against the old code before landing — reverting the
follow loop reproduces the hang, reverting `live_run_for_session` fails the
lockout test — so they fail for the right reason rather than passing slowly.

`_gc()`'s retention rules had no coverage at all before this; the run registry
is process-global and one-run-per-session, so a reap that is too eager loses a
finished turn and one that is too lazy locks the session out permanently.

## Not ported (typed 501, by design)

`/api/sessions/gateway/stream`, `/api/session/update`, `/api/session/yolo`,
`/api/profile/active` (404), `/api/list` (503), `/api/chat/status`. Each answers
a named refusal; none silently pretends to work.

## Deliberately not done

`_HARD_MAX_S` and the per-event deadline remain as visibility backstops for a
transport that genuinely stalls. They are not what makes chat stream — the lock
fix is — but they still bound a failure that no reader-side fix can see.
