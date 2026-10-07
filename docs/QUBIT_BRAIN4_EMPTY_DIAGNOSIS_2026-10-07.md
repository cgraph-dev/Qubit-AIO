# qubit-brain4 — why it returns empty (diagnosed 2026-10-07)

## Verdict

**The model's Modelfile template is broken. It cannot be fixed by any invocation flag.**

`qubit-brain4:latest` emits its entire output into the `thinking` field and leaves
`content` empty, then stops on `done_reason=length`. It never terminates with a real
answer. This is baked into the model, not a caller mistake.

## The evidence

Identical output with and without the `think` flag:

```
think=False   content=''  thinking="Okay, the user asked to say hello in five words..."
think=True    content=''  thinking="Okay, the user asked to say hello in five words..."
```

Both give the SAME `thinking` prefix, both hit `done=length`, both leave `content=''`.
**The flag is ignored** — the template has no path that promotes a completed thought into
`content`.

Token budget makes it worse, not better (measured earlier):

| num_predict | result |
|---|---|
| 100 | empty, `done=length` |
| 300 | empty, `done=length` (12.7 s) |
| 600 | empty, `done=length` (27.0 s) |

`/api/show` confirms the model carries a system prompt (`"You are Qubit, a warm local voice
assistant."`), `num_ctx 4096`, ChatML-ish stops, and a tools template — but the render path
never yields assistant content.

## Comparison on one identical prompt ("Say hello in exactly 5 words.")

| Model | Latency | Speed | Result |
|---|---|---|---|
| **`qubit-brain`** | 3.45 s | **178.1 tok/s** | ✅ `"Hello, I'm Qubit, your assistant."` |
| `qubit-cand-q4` | 18.5 s | 42.9 tok/s | ✅ `"Hello there, how can I help?"` |
| `qubit-brain4` | 4.75 s | 37.2 tok/s | ❌ `''` (`done=length`) |
| `qubit-spark4` | 9.17 s | 43.0 tok/s | ❌ `''` (`done=length`) |

`qubit-brain4` and `qubit-spark4` share the SAME broken behaviour — both loop in thinking
and never emit content. This is a shared template defect, not a per-model tuning issue.

## What was tried

`voicebridge/providers/gateway.py` gained a default `think: False` on the raw chat path
(gateway.py:279-290). **That change is correct and worth keeping** — it stops the runaway
27-second thinking loop (654 ms instead of 12.7 s). But it does NOT restore `content`,
because the defect is in the model template, not the request.

## Recommendation (owner decision required — do NOT change the default silently)

`qubit-brain4` is the registered default for BOTH voice and chat
(`runner_contract.py:23 DEFAULT_ALIAS`, `chat_transport.py:42 DEFAULT_MODEL`,
`profiles.py:158`). It cannot answer.

**`qubit-brain` is the drop-in replacement**: 178 tok/s (4.8× faster than brain4's 37),
correct answers, Llama 3.2 3.6B Q4_K_M, smaller (2.24 GB vs 2.50 GB), and it is already
registered in the picker as of 2026-10-07.

Alternatives if a Qwen-family model is preferred: `qubit-cand-q4` (works, but 18.5 s cold).

## Open question

Did `qubit-brain4` ever work? If a prior Modelfile had a no-think template that promoted
thinking→content, a rebuild may have dropped it. Check the model's build history before
assuming it was always broken. (`config.py:524` comment claims "baked EN-only brain,
no-think template" — the template is present but does not emit content.)
