# Workbench chat — live status after the v5 lane

Written after driving the running server, not by reading code. Every claim has a
command behind it.

## Fixed and verified

| Fix | Commit | Evidence |
|---|---|---|
| Model picker returns 43 real models | `961cba3bb` | live `/wb/api/models`: `models: 43`, `active: deepseek-v4.1-flash`, `provider: opencode-go`; the old `[]` is gone |
| PTY lock no longer held across construction | `2626f7751` | `tests/test_pty_lock_scope.py`: 2 failed → 3 passed |
| `stream_turn` honours `timeout_s` | `2626f7751` | same suite; previously `out.get()` blocked forever |
| Runs that can never settle expire | `ea70402c0` | absolute `_HARD_MAX_S` backstop |
| Voice path intact | — | `test_qubitchat_voice_continuity9u.py` + `test_barge_stream.py`: 9 passed |

Still working, verified live: the page and its assets load with
`failed requests: 0`, the session list renders real data, `/api/session` returns
real user + assistant rows, and `/api/session/stream` answers `initial`.

## NOT fixed — chat turns still hang, and it is not the lock

A turn posted to `/wb/api/chat/start` returns `start: 200`, then sits
`active: true` while `/wb/api/chat/stream` sends only `: connected`.

New measurement, and the reason the earlier diagnosis was wrong: while the run
is stuck the bridge process holds **20 threads all named `wb-chat-37b0a7e`** —
the same run — every one parked in `futex_do_wait`. `comm` is truncated at 15
characters, so that is one pump plus the worker threads its turn spawned, all
deadlocked against each other.

Releasing `_pty_lock` across construction was a real defect and is fixed, but it
is not what blocks the turn. The block happens *after* the pump is running,
among threads the turn itself spawns. `timeout_s` cannot fire there because the
pump is inside the generator: the clock is only checked between events.

### What would find it

Dump all 20 stacks at the moment of the stall — that names the exact lock and
both holders in one shot. Three sessions have inferred this from `wchan`; none
has read the Python frames, because py-spy needs elevated permissions here.

```bash
sudo env "PATH=$PATH" .venv/bin/py-spy dump --pid <bridge-pid>
```

## Open — the ported client may not paint the tokens

Not re-tested since the transport changed. Measured earlier: `S.messages` ended
up with the user turn only and the assistant row read "Processed" with an empty
body, while `/api/session` returned the reply fine — so that failure was
client-side consumption, not persistence.

## Not ported (typed 501, by design)

`/api/sessions/gateway/stream`, `/api/session/update`, `/api/session/yolo`,
`/api/profile/active` (404), `/api/list` (503). Each answers a named refusal;
none silently pretends to work.

## Deliberately not done

The `_HARD_MAX_S` backstop and the timeout make the failure *visible and
bounded* rather than correct. They do not make chat stream. Do not read this
document as "chat works".
