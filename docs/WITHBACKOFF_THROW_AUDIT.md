# withBackoff throw-inside-catch audit

**File:** `frontend/qubit-web/src/lib/api.ts` — `withBackoff` (lines 792–812), `isTransientNetworkError` (775–786).
**Verdict: COSMETIC.** Line 811 is genuinely dead, but it is unreachable only *because* the inner throw already does the right thing. No caller observes a wrong object, wrong banner, or a retry that should not fire.

## 1. Actual control flow

```
for i in 0..attempts-1:
  if shouldStop()               -> throw lastErr ?? Error("aborted")   [line 801]
  try { return await fn() }                                            [line 803]
  catch (err):
    lastErr = err
    if !isTransient(err) || i == attempts-1  -> throw err              [line 806]
    await sleep(...)                                                   [line 808]
throw lastErr ?? Error("withBackoff failed")  <-- DEAD               [line 811]
```

The line-806 `throw err` throws the **original error object**, by reference — not a wrapper, not a copy. It occurs on the two terminal conditions:
- `!isTransientNetworkError(err)` — a typed/served refusal; stop immediately (i may be 0).
- `i === attempts - 1` — the budget is exhausted on a transient error; the last failure surfaces.

## 2. Reachability of line 811 — unreachable

Line 811 runs only if the loop completes all `attempts` iterations without a `return` or `throw`. Every iteration ends in exactly one of three ways: `return` (success), `throw` (line 801, 806), or fall through to sleep. The sleep path is entered **only** when both line-806 conditions are false, i.e. it is a non-final iteration (`i < attempts-1`), so it cannot be the last iteration; the final iteration (`i === attempts-1`) with a throw is always caught and re-thrown at 806. `attempts >= 1` (`Math.max(1, ...)`), so the loop always has ≥1 iteration and always exits via return/throw, never by falling past index `attempts-1`.

Instrumented trace (all exit paths):

| input | outcome | line 811 reached |
|---|---|---|
| all-transient, attempts=3 | reject `Failed to fetch` (orig) | no |
| non-transient on attempt 0 | reject `HTTP_409` (orig) | no |
| succeeds on 3rd | resolve `ok` | no |
| `shouldStop: () => true` | reject `aborted` | no |
| attempts=1, transient | reject `Failed to fetch` (orig) | no |

Reachable **only** under a hypothetical zero-iteration loop, which `Math.max(1, …)` forbids. Dead code.

## 3. Caller impact

| call site | call | catches? | receives | correct? |
|---|---|---|---|---|
| ChatPage.tsx:510 | `withBackoff(() => api.getChatSessionMessages(session), {shouldStop})` | `.catch(err)` → `setError(err.message)`, `setTranscriptState("error")` | original error object | yes — real message shown |
| ChatPage.tsx:521 | `withBackoff(() => api.getChatSession(session), {shouldStop})` | `.catch(() => {})` | original error (ignored) | yes — swallow is intentional |
| TerminalChatPage.tsx:224 | `withBackoff(() => api.wsTicket(picked), {shouldStop})` | `.catch(err)` → `err instanceof WsTicketError ? terminalRefusalCopy(err.code, err.detail, …) : "[WS_TICKET] …"` | **the original `WsTicketError` by reference** — `code`/`detail` intact | yes |

Because line 806 re-throws the original object, a typed `WsTicketError` refusal keeps its `instanceof`, `code`, and `detail`. `terminalRefusalCopy` gets the real code. Verified by identity probe: `caught === refusal` → `true`; `code`/`detail` preserved.

## 4. User-observable behaviour

None. The error banner text is correct (`err.message` = real message), the typed refusal copy is correct (`WsTicketError.code`), and retry policy is unchanged: transient errors retry exactly `attempts` times, then reject; non-transient refusals (409, typed `WsTicketError`, `AbortError`) do not retry. No wrong banner, no stray retry.

## 5. Minimal patch (optional, cosmetic)

Line 811 is unreachable but harmless. The compiler needs a non-`undefined` exit, and deleting the line risks confusing return-type inference. **Recommendation: leave it as-is.** If forced to touch it, replace with `throw lastErr;` (behaviour-identical) and add `// unreachable: loop always returns or throws`.

## Tests

`cd frontend/qubit-web && NODE_ENV=test npx vitest run src/lib/api-backoff.test.ts` → **1 file passed, 11 tests passed, 322ms**. The suite already pins the behaviour that matters: rethrow of the real error, immediate abort of typed refusals, and bounded rejection.
