# Local models that emit NOTHING — the reasoning-template defect (2026-10-07)

## The defect

Several installed local models return `content=''` on every request. Their entire output
goes into the `thinking` field and generation ends with `done_reason=length` instead of
`stop`. **No invocation flag fixes it** — measured earlier with `qubit-brain4`:
`think:true` and `think:false` produce byte-identical output (the flag is ignored).

This is a **Modelfile template defect**: the template renders a reasoning block but has no
path that promotes the finished thought into assistant `content`.

## Which models work and which do not (measured, same prompt)

| Model | `content` | `thinking` | `done_reason` | Verdict |
|---|---|---|---|---|
| **`qubit-brain`** | `'Hello, how can I assist?'` | empty | `stop` | ✅ WORKS |
| **`qubit-cand-q4`** | `'Hello there, how can I help?'` | empty | `stop` | ✅ WORKS |
| `qubit-spark17` | `''` | `'We need to respond with a greeting…'` | `length` | ❌ BROKEN |
| `qubit-spark4` | `''` | (same pattern) | `length` | ❌ BROKEN |
| `qubit-brain4` | `''` | `'Okay, the user asked…'` | `length` | ❌ BROKEN |

Raising `num_predict` (100/300/600) does not help — it just thinks for longer. `think:false`
does not help. `num_ctx` does not help. All verified.

## The misleading symptom

When a pinned model returns empty, the agent's overflow-recovery path misreads it and the
user sees:

> *"This conversation has grown too long for qubit-spark17 to read… Start a new session with
> /new…"*

**That message is a misdiagnosis** — it fires on a brand-new one-message session. The real
cause is empty output, not an over-long conversation. Worth fixing the copy/classification,
because it sends the operator chasing a context problem that does not exist.

## A separate, real data bug found and fixed

`~/.local/state/qubit-voicebridge/profiles.json` seeded the two LOCAL spark profiles with
`context_tokens = 1048576` — a 1M window copied from the remote **muse-spark** API models
(which legitimately have one). Ollama serves the local models with no `num_ctx`, i.e. its
4K default. Fixed to `4096` (matching the sibling local profiles `qubit-brain`,
`qubit-brain4`, `qubit-cand-*`). Backup: `profiles.json.bak-context-fix-*`.

That fix was correct but NOT the cause of the empty output — the two are independent.

## Consequence for the model picker

The picker now lists 7 local models (registration fixed 2026-10-07), but **only 2 of them
actually produce output**. A model that loads, runs, and returns nothing is worse than one
that is absent — so either:

1. rebuild the broken models' Modelfiles so thinking promotes to content, or
2. mark them non-chat-capable so the picker does not offer them.

Do not present a model as selectable when it cannot answer.

## How to verify a local model is usable (do this before offering it)

```bash
curl -s http://127.0.0.1:11434/api/chat -d '{"model":"<name>","messages":[{"role":"user",
  "content":"say hello in 5 words"}],"stream":false,"options":{"num_predict":80}}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); m=d['message']; \
print('content=',repr(m.get('content')), 'done=',d.get('done_reason'))"
```

Pass = non-empty `content` **and** `done_reason == "stop"`. An empty `content` with
`done_reason == "length"` means the model is broken, regardless of what the catalog says.
