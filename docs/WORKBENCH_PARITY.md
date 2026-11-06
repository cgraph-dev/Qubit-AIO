# Workbench — what works, what does not

Live surface: `http://127.0.0.1:8890/qubit-workbench` (alias `/wb`).
Every claim below was taken from the running server, not from reading code.

## Works

| Surface | Evidence |
|---|---|
| Page + assets | headless Chrome: `failed requests: 0`, base `/qubit-workbench/` |
| Session list | real data: WebUI 38 / CLI 2, grouped TODAY / YESTERDAY, real cron names |
| Transcript render | `S.messages` populated, DOM shows real user + assistant rows |
| `POST /api/chat/start` | 200 with `stream_id`, routes through `chat_transport.stream_turn` |
| `GET /api/chat/stream` | real SSE: `event: token` (`BUFTEST`) then `event: done` with usage |
| Persistence | turn lands in the store: user + assistant rows with content |
| `/api/session/stream` | `initial` frame, unblocks the transcript pane |

## Broken — the turn hangs and the pane shows a blank "Processed"

**This is the one that matters.** A turn can sit `active: true` for 120s+
producing no events at all, and the watchdog in `chat_stream._pump` cannot
fire because the generator is blocked *inside* `next()` — the deadline check
lives in the loop body, which is never reached.

Root cause, measured: the pump thread sits in `futex_do_wait` holding
`chat_transport._pty_lock`. `_get_pty_session` (`chat_transport.py:5449`)
holds that lock across `AgentPtySession(cfg, sid, model=model)` construction,
so one slow/hung constructor blocks **every** other turn process-wide, and the
blocked turn holds nothing that would ever time it out.

Why this is not fixed here: it is core `chat_transport`, shared with `/chat`
and every other turn surface. Changing lock scope inside it is not a
workbench-lane edit and must not be done blind; it needs its own change with
the `/chat` path under test.

Fix sketch, for whoever takes it:
1. Construct the session **outside** `_pty_lock`, then install it under the
   lock with a double-check (`if key in _pty_sessions: return existing`).
   Never hold a module lock across a network/agent constructor.
2. Give the pump a real deadline that fires while blocked: run the generator
   in a worker and `join(timeout)`, or have `stream_turn` honour its own
   `timeout_s` parameter — it currently accepts `timeout_s` and never applies
   it (`chat_transport.py:4841`).

## Broken — the model picker is empty

`/api/wb/api/models` answers `{models: []}`. The client then falls back to
upstream's hardcoded names ("GPT-5.4 Mini"), sends one to `/api/chat/start`,
and the turn fails with `CIRCUIT_OPEN` while the pane shows the same blank
"Processed" row.

A fix is committed (`155751907`) deriving the list from per-provider
`default_model`. **It is unverified**: every local probe passed `cfg=None` and
`/api/config` degrades to a voice-only subset without a live cfg, so probes
read 0 models regardless of the code. Re-read the live endpoint with the
server's own config handle before calling it fixed.

## Broken — the reply is one chunk, never a token stream

`chat_transport.stream_turn` calls `_generate_remote`, waits for the whole
reply, then emits a single `delta` (`chat_transport.py:4677`,
`streaming: False`). The SSE transport is correct and replays faithfully;
upstream of it there is nothing to stream. The assistant row stays empty until
the turn completes. Streamed text requires a real streaming path in the core
transport, not a workbench change.

## Not ported (typed 501, by design)

`/api/sessions/gateway/stream`, `/api/session/update`, `/api/session/yolo`,
`/api/profile/active` (404), `/api/list` (503). Each answers a named refusal;
none silently pretends to work.
