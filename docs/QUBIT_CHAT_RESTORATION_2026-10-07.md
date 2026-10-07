# /qubit-chat — how it worked, what broke it, and the restoration (2026-10-07)

Method: log/state mining (`state.db`, `agent.log*`, `errors.log`), source tracing,
and live probes against the running service at `127.0.0.1:8890`. Every claim is
measured; unverified items are marked.

## 1. The historical working state

Cross-referencing `state.db[sessions]` with `agent.log*` turn receipts, **every
`/qubit-chat` (source `dashboard`) turn that ever completed ran on a REMOTE API
model — never a local one**:

```
2026-10-07 07:23:27 INFO Turn ended: reason=text_response(finish_reason=stop)
   model=deepseek-v4.1-flash ... response_len=21 session=dashboard-5b8411b3e006
```

Stored transcript of that session: user `hi` → assistant `Hi. What do you need?`.

Every dashboard turn routed to a LOCAL model failed at the runtime guard:

```
2026-10-07 07:26:36 WARNING agent.conversation_loop: Ollama runtime context too small
   for Qubit tool use: model=qubit-brain4 provider=ollama base_url=http://localhost:11434/v1
   runtime_context=4096 minimum_context=64000 ... session=dashboard-51d080714310
   Turn ended: reason=ollama_runtime_context_too_small ... response_len=508
```

The 508-char "reply" is the guard copy (`Ollama loaded qubit-brain4 with only
4,096 tokens…`), not an answer. **Working model = `deepseek-v4.1-flash` via
`opencode-go`.** Sessions that succeeded had that model *stored on the session*
(the session pin drove routing).

## 2. What changed / the regression (three linked defects)

**D1 — new sessions were pinned to a model that cannot answer.**
`qubit_session_store.create()` sets a new dashboard session's model from
`chat_transport.local_brain_model(cfg)`, which returned `intent.local_llm.model`
verbatim. That value is `qubit-brain4`, whose registered profile deliberately
**lacks the `chat` role** (it emits all output into `thinking`, `content=''`,
`done_reason=length` — see `docs/QUBIT_LOCAL_MODEL_EMPTY_OUTPUT_2026-10-07.md`).
The `chat`-role gate existed only on the *fallback* path, so the configured path
bypassed it. Live proof (running service, before the fix):

```
POST /api/qubit/chat/sessions {"name":"proof-before-fix"}
 -> {"session":{"id":"dashboard-4113fadff0d5","model":"qubit-brain4"}}
POST /api/qubit/chat {"text":"say hello in three words", ...}
 -> text: "The model 'qubit-brain4' needs more runtime context. Increase ..."
```

**D2 — a pinned remote model routed to the LOCAL provider (404).**
`resolve_route` matched the requested id against profile **ids** only. A session
pinned to `deepseek-v4.1-flash` (the profile's *model*, id is
`opencode-go-deepseek-v4.1-flash`) raised `UNKNOWN_PROFILE`, then the fallback
copied the local active profile and patched only the model name — leaving
`provider=ollama`. Measured live in `errors.log`:

```
HTTP 404: model 'deepseek-v4.1-flash' not found
   provider=ollama base_url=http://localhost:11434/v1
```

**D3 — the persisted "active" pointer shadowed the working default.**
`resolver._pick` returns the persisted active profile before the configured
default. The active pointer is `qubit-brain` (local, 4096 ctx). So a no-pin turn
resolved to a sub-64K local model and hit the guard. `profiles.json`:

```json
"active_model_profile": {"profile": "qubit-brain", "role": "chat"}
```

All three stack to the same symptom: **a `/qubit-chat` turn cannot complete on a
local model** (every installed local brain is a 4K Qwen3-4B record, below
`MINIMUM_CONTEXT_LENGTH = 64_000`, so the tool-capable turn is refused).

## 3. The fix

### Code (applied — `voicebridge/chat_transport.py`, +75/−2)

1. `local_brain_model` now applies the **`chat`-role gate to the configured
   path too**: a configured id that maps to a *known* profile lacking `chat`
   (`qubit-brain4`) is not returned; an unregistered operator tag still passes
   through unchanged. → new sessions no longer pin a broken model.
2. `resolve_route` fallback resolves a requested **model name** through the
   registry (`_profile_for_model`) before degrading, so a `deepseek-v4.1-flash`
   pin routes through `opencode-go-deepseek-v4.1-flash` with the correct
   provider/base_url. → host-model pins no longer 404 at local Ollama.
3. `resolve_route` (no-pin branch) refuses to silently run a local profile below
   the tool-use floor and prefers the operator's configured remote default —
   the same rule `terminal_context_guard` already enforces for `/chat`.

Regression check (focused set, before/after): baseline **15 failed / 30 passed**,
with fix **13 failed / 32 passed**; failure-set diff shows **0 newly broken**,
2 fixed. The 13 remaining are pre-existing tests that encode the old contract
(including `test_model_routing_defaults_to_qubit_brain4_and_refuses_unknown`,
which asserts the exact behavior that broke the surface).

### Config (proposed — parent applies to
`/home/trick/.config/qubit-voicebridge/voice.yaml`)

The code fix alone restores the turn. To also align the *reported* default and
remove reliance on the stale active pointer, set the working model explicitly:

```yaml
agent:
  model: opencode-go-deepseek-v4.1-flash   # was: null
```

`agent.model` is the first source `resolver.configured_default_model` reads and
is the model the bypass prefers. (Optional, if a local brain is wanted later:
set `intent.local_llm.model` to a working chat model such as `qubit-brain`, and
accept that local models must be reloaded at `num_ctx >= 65536` to pass the
tool-use guard — the installed 4B records are trained at 4096.)

## 4. Live proof (post-fix, through the production turn path)

A brand-new `/qubit-chat` session, no pin:

```
NEW session id: dashboard-final-508b22   stored model: None
turn  ok=True  route='agent'  text='4'
  stored user:      'What is 2+2? Answer with just the number.'
  stored assistant: '4'
turn2 ok=True  text='...'   (real contextual follow-up)
```

`resolve_route` output confirms the routing:

```
low_brain_model: None
no-pin route: model=deepseek-v4.1-flash provider=opencode-go kind=api
              short_context_default_bypassed=qubit-brain
explicit qubit-brain: model=qubit-brain provider=ollama kind=local   (still honored)
```

## 5. Status

- Code fix: applied and verified via the real `chat_transport.turn` path.
- Service NOT restarted (per task rules) — the running process still has the old
  code; a restart loads the fix.
- Config change: proposed above, not applied.
