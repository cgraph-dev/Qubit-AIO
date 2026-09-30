# Gateway WS protocol — session detail (2026-08-19, gateway 2026.7.1-2)

Hard-won detail from building a programmatic gateway client (the Qubit voice
bridge). Everything here was verified live against the running gateway at
`ws://127.0.0.1:18789`.

## Handshake (exact, verified)

```
S->C: {"type":"event","event":"connect.challenge","payload":{"nonce":"...","ts":1787146800121}}
C->S: {"type":"req","id":"c1","method":"connect","params":{
         "minProtocol":4,"maxProtocol":4,
         "client":{"id":"gateway-client","version":"0.1.0","platform":"linux","mode":"backend"},
         "role":"operator",
         "scopes":["operator.read","operator.write","operator.admin","operator.approvals","operator.talk"],
         "caps":[],"commands":[],"permissions":{},
         "auth":{"token":"<OPENCLAW_GATEWAY_TOKEN>"},"locale":"en-US","userAgent":"x/0.1"}}
S->C: {"type":"res","id":"c1","ok":true,"payload":{"type":"hello-ok","protocol":4,
         "server":{"version":"2026.7.1-2","connId":"..."},"features":{"methods":[...229...],"events":[...]}, ...}}
```

## The scope gotcha (cost ~30 min to find)

- `client.id:"cli", mode:"node"` → handshake OK but `auth.scopes: []` → every
  RPC fails `missing scope: operator.read` (INVALID_REQUEST).
- `client.id:"gateway-client", mode:"backend"` → full operator access with just
  the shared token, loopback only. This is the documented trusted-backend path
  (protocol doc, "Trusted same-process backend clients").
- Scopes come from the DEVICE table for normal clients; the backend identity
  skips device pairing entirely.

## Methods / events of interest (from hello-ok)

Methods (229 total): health, status, sessions.{list,create,send,abort,patch,
subscribe,messages.subscribe,preview,describe,resolve}, chat.{history,send,
abort,message.get}, agent.wait, agents.{list,create,files.*,workspace.*},
tasks.{list,get,cancel}, tts.{status,providers,convert,speak}, talk.{catalog,
config,session.*,client.*,speak,mode}, exec.approvals.*, terminal.*, config.*,
tools.{catalog,effective,invoke}, models.list, skills.*, cron.*, voicewake.*,
browser.request, voicecall.*, audit.*.

Events: connect.challenge, session.message, session.operation, session.tool,
sessions.changed, agent, chat, talk.event, talk.mode, exec.approval.requested,
terminal.data, terminal.exit, presence, tick, shutdown, health, update.available.

## Verified RPC shapes

| method | params (verified) | notes |
|---|---|---|
| sessions.create | key, label, model?, message? | nested message starts run; returns runId; label must be UNIQUE |
| sessions.send | key, message | NOT `text` — wrong key = ok:false, empty payload |
| agent.wait | runId, timeoutMs | returns {status: ok\|timeout\|failed}; poll on timeout |
| chat.history | sessionKey, limit | assistant content can be list of {type:'text',text:...} |
| sessions.abort | key | cancels active run |
| tasks.list | {} | ledger; observed kinds: cli (ask script runs); NOT all runs |
| agents.list | {} | defaultId, agents[].model.primary |
| tts.speak | text | → {audioBase64, mimeType, outputFormat}; requires configured provider |

## Where the schemas live

- `node_modules/openclaw/dist/schema-BuOFpc7K.js` — every `XxxParamsSchema =
  Type.Object({...})`; grep by method name. Exact fields beat guessing.
- Docs pages are HTML; strip to text with a python html.parser pass before reading.
- `@openclaw/gateway-protocol` npm package is a PLACEHOLDER (reserved name, no artifacts).

## Latency measurements (this box)

- Fresh session, qwen3-vl:8b, cold model: ~85 s for "Reply with exactly: X".
- Warm 8B, real coding task: 60–390 s (bootstrap + tool loops dominate).
- 30B MoE cold: > 10 min (still generating at 647% CPU, 44% CPU offload).
- agent.wait timeout semantics: returns status=timeout with timeoutPhase when
  the window expires with the run still active. Loop with ms/s unit care.
- The gateway can spawn SUBAGENT sessions for tool-heavy tasks — parent
  chat.history then shows only the user message even when the run completed
  ok. Do not report "nothing to do"; look for child sessions.

## Probe / diag patterns

- Correlate responses by `id`; a client keeps a pending-dict {id: future}.
- Subscribe before send: `sessions.messages.subscribe {key}` → session.message
  events stream; simpler completion = agent.wait + chat.history tail.
- Reconnect: hello-ok re-read policy (maxPayload etc.) on every connect.
- 2026.7.1-2 allows `client.id:"gateway-client"` only over loopback; remote
  clients need device pairing (device.pair.* RPCs).
