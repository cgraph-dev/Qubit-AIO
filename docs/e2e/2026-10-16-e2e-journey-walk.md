# E2E journey walk — 2026-10-16

Exploratory end-to-end pass over the served Qubit dashboard
(`http://127.0.0.1:8890`), headless Chrome 149 over raw CDP. Worktree
`fix/e2e-journey` @ `4b66ad103`.

Method: every claim below is backed by a value read back from the live DOM, a
console/network event captured over CDP, or a raw HTTP response. No claim is
inferred from element presence.

---

## E1 — the dashboard cannot authenticate: every SPA API call 401s

**Severity: blocker** (this is the operator's "blank/black pane" complaint).

The served SPA bundle reads `window.__HERMES_SESSION_TOKEN__`; the server
injects `window.__QUBIT_SESSION_TOKEN__`. The bundle therefore attaches **no**
auth header to any request and every `/agent/api/*` call answers **401**.

### Evidence

Served bundle vs source:

```
$ grep -c '__QUBIT_SESSION_TOKEN__' frontend/qubit-web/src/lib/api.ts
7
$ grep -c '__HERMES_SESSION_TOKEN__' frontend/qubit-web/src/lib/api.ts
0

$ curl -s http://127.0.0.1:8890/assets/api-CJJNLXGa.js | grep -c '__QUBIT_SESSION_TOKEN__'
0
$ curl -s http://127.0.0.1:8890/assets/api-CJJNLXGa.js | grep -c '__HERMES_SESSION_TOKEN__'
3
```

The server injects only the Qubit name (`voicebridge/webui.py:1826`):

```
<script>window.__QUBIT_SESSION_TOKEN__="7caun11MRuoXc5fZ3nSMagQANDcup2SB";
window.__QUBIT_BASE_PATH__="";window.__QUBIT_API_PREFIX__="/agent";
```

In the live page, with `Page.addScriptToEvaluateOnNewDocument` hooking
`window.fetch` **before** the bundle runs, the headers the SPA actually
attached to its own API calls:

```
token global: '7caun11MRuoXc5fZ3nSMagQANDcup2SB'      <- injected, present
hermes global: None                                    <- what the bundle reads

/agent/api/gateway                     -> {}            <- NO auth header
/agent/api/status                      -> {}
/agent/api/model/info                  -> {}
/agent/api/qubit/chat/sessions...      -> {}
/agent/api/tools/toolsets              -> {}
/agent/api/skills                      -> {}
/agent/api/mcp/servers                 -> {}
/agent/api/auth/ws-ticket              -> {"content-type": "application/json"}
```

Server-side the same token authenticates fine, proving the value is valid and
the header is simply never sent:

```
X-Qubit-Session-Token  -> 200
X-Hermes-Session-Token -> 200
no header              -> 401
```

User-visible result (`document.body.innerText`, `/chat`):

```
error
[HTTP_401] ws-ticket refused (401)
events feed rejected (401) — reload the page
reconnect events feed
AGENT TOOLS
Tools   unavailable
Skills  unavailable
Plugins unavailable
MCP servers unavailable
MODEL  —   error
SESSIONS  New chat   401: unauthorized   Retry
```

Screenshot: `/tmp/e2e-shots/chat-401-empty.png` — transcript pane empty, red
error badges in the model row, events feed, and sessions panel.

### Root cause

Commit `6ccea6267` (2026-09-30) renamed the globals in
`frontend/qubit-web/src/lib/api.ts`:

```
-  const token = window.__HERMES_SESSION_TOKEN__;
+  const token = window.__QUBIT_SESSION_TOKEN__;
-    if (!window.__HERMES_AUTH_REQUIRED__ && !options?.allowUnauthorized) {
+    if (!window.__QUBIT_AUTH_REQUIRED__ && !options?.allowUnauthorized) {
```

The bundle served at `:8890` was built **before** that commit, so it still
carries the pre-rename `__HERMES_` name that the server no longer injects.

### Why it is not fixed here

The fix is a **rebuild** of `frontend/qubit-dist`, which is a shared,
non-isolated output directory that a concurrent session is actively building
into. `frontend/qubit-web/src/lib/api.ts` is already correct on disk
(verified: 7× `__QUBIT_`, 0× `__HERMES_`), so there is no source defect to
patch — the artifact is stale. Writing a build here would stomp the other
session's in-flight output.

**Verification owed:** after the concurrent build lands, re-run
`grep -c __HERMES_SESSION_TOKEN__` on the freshly served api chunk — it must be
0 — then reload `/chat` and assert the model row, events feed, and sessions
list render real values instead of `401`/`unavailable`.

### Note for the builder

Serverside, `webui.py` accepts **both** header names
(`webui.py:1519-1520`), so a bundle that sends the legacy header still works.
The break is one-directional: the bundle reads a global the server stopped
injecting. A defense that survives either build order would be for
`webui.py` to inject **both** `__QUBIT_SESSION_TOKEN__` and
`__HERMES_SESSION_TOKEN__` (the latter as a deprecated alias) — but that is a
server change and was left to the owner since it touches the token-injection
contract guarded by `scripts/qubit_public_surface_scan.py`.

---

## Observed bundle identity (for reprotraceability)

```
frontend/qubit-dist/assets/api-CJJNLXGa.js
  sha256 a2db2069a69f777d59092c54585e08003f2e6b7b9c2265a793e4cdb5039101dc
  served bytes == dist bytes (curl hash == file hash)
frontend/qubit-dist/index.html
  sha256 8493e51e78d9c5ab46071f247947f9a4ee8cf5f1df626304932538716ea86c0f
frontend/qubit-web/src/lib/api.ts
  sha256 9f988047e09e9651aed48f2cb82adabf8476b75110405091bd0a816818995004
```

Both the shell and its chunks are served `Cache-Control: no-store`.
