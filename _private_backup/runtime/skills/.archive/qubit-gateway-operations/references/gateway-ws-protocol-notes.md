# Qubit Gateway WS protocol — live verification notes (2026-08-19)

Session that produced these notes: built a local push-to-talk voice bridge for
Qubit (`~/Projects/Repos/openclaw-voice-bridge`) that drives the running
gateway entirely through the WS protocol. All shapes below were verified
against the live gateway 2026.7.1-2, port 18789, token auth.

## Handshake (token auth, loopback, no device pairing)

```jsonc
// server -> client (first frame)
{ "type": "event", "event": "connect.challenge",
  "payload": { "nonce": "...", "ts": 1787146800121 } }

// client -> server
{ "type": "req", "id": "c1", "method": "connect", "params": {
    "minProtocol": 4, "maxProtocol": 4,
    "client": { "id": "gateway-client", "version": "0.1.0",
                "platform": "linux", "mode": "backend" },
    "role": "operator",
    "scopes": ["operator.read","operator.write","operator.admin",
               "operator.approvals","operator.talk"],
    "caps": [], "commands": [], "permissions": {},
    "auth": { "token": "<OPENCLAW_GATEWAY_TOKEN>" },
    "locale": "en-US", "userAgent": "voice-bridge/0.1.0" } }

// server -> client
{ "type": "res", "id": "c1", "ok": true,
  "payload": { "type": "hello-ok", "protocol": 4,
               "server": { "version": "2026.7.1-2", "connId": "..." },
               "features": { "methods": ["health", ..., 229 total],
                             "events": ["connect.challenge","agent","chat",
                                        "session.message","session.operation",
                                        "session.tool","sessions.changed",
                                        "talk.event", "exec.approval.requested", ...] },
               "auth": { "role": "operator", "scopes": ["operator.read", ...] } } }
```

## Client enum discovery (brute-force result)

Allowed combos that return OK:

| client.id      | client.mode | Result                                        |
|----------------|-------------|-----------------------------------------------|
| `gateway-client` | `backend`   | **full operator scopes with shared token, loopback, no device** — the one to use |
| `cli`          | `node`      | connects, but `auth.scopes: []` → `missing scope: operator.read` on every RPC (needs a paired device to get scopes) |

Rejected: `cli/operator`, `web/*`, `node/operator`, etc. → `INVALID_REQUEST:
must be equal to one of the allowed values` (client.id and client.mode are
closed enums; backend mode requires id `gateway-client`).

## Verified RPC shapes (schema source: dist/schema-BuOFpc7K.js)

```python
# create session AND start the run in one call (nested message)
params = {"key": "agent:main:voice:task-abc", "label": "voice task abc",
          "model": "ollama/qwen3-vl:8b",          # per-session override
          "message": "the task text"}
res = call("sessions.create", params)   # -> {"ok":true,"key":...,"sessionId":...,
                                        #     "runId":"...", "status":"started"}
# label must be unique across sessions -> INVALID_REQUEST "label already in use"

# send into an existing session — field is message, NOT text
res = call("sessions.send", {"key": key, "message": "..."})  # -> {runId, status:"started"}

# wait for completion — returns status "timeout" while still running; POLL
res = call("agent.wait", {"runId": run_id, "timeoutMs": 60_000})
#   {status:"ok"} | {status:"timeout", timeoutPhase:"gateway_draining"} | {status:"failed"}

# read the transcript
res = call("chat.history", {"sessionKey": key, "limit": 30})
# messages[] entries: {"role":"user|assistant|toolResult", "text"|"content": ...}
# assistant content can be a structured list: [{"type":"text","text":"..."}]

# cancel
res = call("sessions.abort", {"key": key})  # -> {ok:true, abortedRunId, status:"aborted"}

# gateway TTS (reuse Qubit's configured providers)
res = call("tts.speak", {"text": "hello"})  # -> {audioBase64, mimeType, outputFormat}
```

## Reply-extraction lesson (empty parent transcript)

A tool-heavy run can end `status: ok` while the parent session's `chat.history`
contains ONLY the user message (work happened in an embedded/subagent session
that never wrote back; no `parentSessionKey` link, no task-ledger row). The
voice bridge's layered fallback, in order:
1. parent `chat.history` limit 30, take last meaningful assistant text
   (skip `NO_REPLY`, `<tool...>` noise)
2. `sessions.list` rows where `parentSessionKey == our key` (or `spawnedBy == runId`)
   → their history
3. `tasks.list` rows whose JSON contains our sessionKey/runId → `childSessionKey`
   history
4. if all empty: say so honestly; keep `runId` in the journal for Control-UI lookup.

## Latency on this box (RTX 3070 Ti 8 GB, agent model qwen3-vl:8b)

- Warm model, small real task (create a file + report path): **65 s** end-to-end
  from dispatch to summary (bootstrap-dominated; reply extracted + summarized).
- Cold/warm Stripe-inspection task: 383–912 s (agent loops on tools; 30B MoE
  takes 10+ min). Bridge speaks "On it." immediately, periodic "still working",
  then the summary — plus a background reaper for runs past the timeout.

## Handy diagnostics

- `openclaw docs "gateway websocket"` searches the local docs bundle.
- `curl -s https://docs.openclaw.ai/gateway/protocol | python3 html2txt` for
  offline reading (see voice-bridge `scripts/` for the extractor pattern).
- RPC param schemas: grep `dist/schema-BuOFpc7K.js` in the openclaw install
  (`~/.local/share/mise/installs/node/22/lib/node_modules/openclaw/dist/`),
  e.g. `SessionsSendParamsSchema`, `SessionsCreateParamsSchema`,
  `AgentWaitParamsSchema`, `ChatSendParamsSchema`.
- `npx @openclaw/gateway-protocol` npm package is a reserved placeholder — don't
  rely on it for schemas.
