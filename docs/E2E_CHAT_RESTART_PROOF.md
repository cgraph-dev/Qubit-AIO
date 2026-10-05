# E2E proof: chat restart survival (SSE surface)

Date: 2026-10-04. Harness built and run by a subagent; re-run and confirmed by the parent.

**Scope boundary, stated up front:** this proves the **SSE** surface (`/qubit-chat`,
`voicebridge/chat_transport.py`) — NOT the dashboard PTY page (`/chat`), which is the
operator's actual symptom and remains unsolved (`docs/PTY_AUTO_CONTINUE_GAP.md`).

## Deliverable

`tests/test_chat_restart_survival_e2e.py` — real backend boots, real HTTP, a real marker
file on disk. No mocks of the code under test.

## Raw output

```
$ TMPDIR=/tmp/pytest-q .venv/bin/python -m pytest tests/test_chat_restart_survival_e2e.py -v -p no:warnings
tests/test_chat_restart_survival_e2e.py::test_abandoned_stream_persists_reply_exactly_once PASSED [ 50%]
tests/test_chat_restart_survival_e2e.py::test_process_death_mid_turn_surfaces_interrupted_turn PASSED [100%]
============================== 2 passed in 9.21s ===============================
```

## What each test proves

### `test_abandoned_stream_persists_reply_exactly_once`

Claim A — a turn cut off mid-stream still persists its reply, exactly once.

- The worker is driven to the point where it has entered the model (`model.entered.wait(10)`).
- The client then **abandons the stream**.
- Assertions (`:243-257`):
  - `rows` is non-empty — "reply was NOT persisted after the client abandoned the stream"
  - `len(rows) == 1` — no duplicate
  - `rows[0]["content"] == model.reply` — the right content
  - `len(_assistant_rows(...)) == 1` — no duplicate assistant row
  - the crash marker is **cleared** on conclusion — "a concluded turn must not leave a crash marker"

This is the regression that motivated the worker-thread `saved_box` / `_persist_reply` change
in `chat_transport.py`: the save previously ran only after the SSE drain, so a client disconnect
closed the generator before the save line and the reply was lost.

### `test_process_death_mid_turn_surfaces_interrupted_turn`

Claim B — after the backend dies mid-turn and comes back, the session-show route reports
`interrupted_turn` with the original prompt.

- Boot 1 of a real backend against its own temp `QUBIT_HOME`; a turn is started and the marker
  is confirmed written with the exact prompt (`:366-367`).
- The process is killed (process death, not a clean stop) — the marker survives.
- Boot 2 of a fresh backend against the same home.
- Assertions (`:374-384`):
  - `turn_marker.read_turn_marker(base / "runtime", session) is not None` — the marker is on disk
  - the session-show payload carries `interrupted_turn`
  - `payload["interrupted_turn"]["prompt"] == prompt` — the ORIGINAL prompt, not a wrapped note

## PROVEN

- Abandoned-stream replies persist exactly once (SSE path).
- A process death mid-turn leaves a durable marker that survives a restart.
- The session-show route surfaces `interrupted_turn` with the original prompt afterward.

## NOT PROVEN (be explicit)

- **No browser was driven.** The recovery was verified over HTTP, not by clicking in a real
  browser. The page's own auto-resume effect (`ChatPage.tsx`) is typechecked and unit-tested
  but not exercised end-to-end in a browser.
- **The dashboard PTY page (`/chat`) is not covered at all.** Its auto-continue gate is never
  entered — see `docs/PTY_AUTO_CONTINUE_GAP.md`. This harness says nothing about that page.
- **The backoff window limit holds:** recovery rides out ~7s. A slower restart still surfaces an
  error (one manual refresh, then auto-resume). Not exercised here.

## Reproduce

```
cd /home/trick/Projects/Repos/qubit-voicebridge
TMPDIR=/tmp/pytest-q .venv/bin/python -m pytest tests/test_chat_restart_survival_e2e.py -v -p no:warnings
```

Uses a temp `QUBIT_HOME` and a free port; it does not touch the production service or port 9119.
