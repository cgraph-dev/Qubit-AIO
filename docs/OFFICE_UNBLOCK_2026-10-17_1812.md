# Office unblock — 2026-10-17 18:12 tick (lead-quality)

Follow-up to `docs/RETRY_STORM_ROOT_CAUSE_2026-10-17.md`. Same root cause, but the
recommended fix in that document **does not work as written**, and three
escalations were closed without anything being applied.

## State of the book (verified, not remembered)

| fact | value |
|---|---|
| open receipts | 1 — this turn's own (`run_2d34191b09bb42fa`) |
| remembered blocker `run_a95437e64c3c4f2a` | settled, 0c, unmetered, 18:06 (it had been open since 13:13 — a 5-hour wedge) |
| `office_task` open | 120 — 113 `Self-directed: <slug>`, 6 `Advance task #2`, 1 real (`#2`) |
| assignee on all 120 | `NULL` |
| `office.autowork.dispatch` | false |
| seats with empty `model`/`provider` | 10 of 10 |
| escalations | #1–#3 resolved 17:11:30–35, `decided_by=0`; #4 raised 18:23:51, resolved 18:23:55 |

The book is clean. The wedge is the route, exactly as #3 said.

## The route matrix, measured this turn

`estimate_usage_cost('deepseek-v4.1-flash', CanonicalUsage(1000, 500), provider=…, base_url=…)`

| route | price verdict | credential on this box | usable? |
|---|---|---|---|
| `cheapvibecode` (live) | `unknown` | `CHEAPVIBECODE_API_KEY` set | **no** — relay, unknown by design, wedges on every turn |
| `opencode-go` | `included` ($0) | key resolves from the OpenCode CLI store | **no** — `GET /models` returns **403** on both `opencode.ai/inference/go/openai/v1` and `opencode.ai/zen/go/v1`: the key is not entitled |
| `deepseek` (direct) | `estimated` (~$0.051/turn) | `DEEPSEEK_API_KEY` **missing** | **no** — priceable but unauthenticated |
| `ollama` `qubit-brain4` @127.0.0.1:11434 | `unknown` | local, no key needed | **no** — the pricing engine does not treat the local route as free |

So the "point the seats at opencode-go, it is free" fix from escalation #3 is not
available: the only opencode-go key on this machine is rejected. **No route here
is both authenticated and priceable**, which is why hand-settling receipts never
holds.

### The owner's one action (any one)

1. **`DEEPSEEK_API_KEY`** — metered, ~$0.051 per seat turn, no code change, no new
   entitlement. Then: `UPDATE office_agent SET provider='deepseek', model='deepseek-v4.1-flash';`
2. **An entitled `OPENCODE_GO_API_KEY`** — free at the margin, no code change.
   Then: `UPDATE office_agent SET provider='opencode-go', model='deepseek-v4.1-flash';`
3. **Authorise a local-route zero-price rule** in `qubit_runtime/agent/usage_pricing.py`
   so `127.0.0.1` ollama is booked `included` — $0 forever, but it is a
   pricing-policy change, so it is not made unasked.

## Why three escalations were "resolved" and nothing changed

`dashboard_ops.confirm_office_escalation_op` calls `ESC.resolve(..., owner=True)`,
which sets `status='resolved'`, `decided_by=0`, `decided_at` — and **does nothing
else**. An escalation's ask is prose; there is no executor that turns an approved
ask into a route change. Confirming it is indistinguishable, on the board, from
having done it.

Consequences that cost real time:

- the tick keeps refusing every 16 minutes while the board says the ask was
  actioned;
- the next seat to look reads `resolved` and concludes the problem is elsewhere.

Rule for future escalations: put the **literal command** in the ask, and after the
owner confirms, verify the *effect* (here: `SELECT model, provider FROM
office_agent` still empty), not the row status.

## Done this turn (no spend)

1. `819a03626` — the tick no longer files `exit_check='true'`. A self-directed task
   now completes only when **its own** `run_id` has a settled
   `office_run_receipts` row, keyed by a nonce in the brief. It fails before the
   turn, cannot be satisfied by another turn the same seat runs, and passes once
   the claim that ran it settles. `tests/test_office_autonomous_driver.py` 19 passed.
2. `ccf9f6695` — `escalation.py:321` → `:328` citation retracked (the owner branch
   in `resolve()` added seven lines above the level rule). The citation test was
   red at HEAD; it is green again.
3. Task `#2`'s exit check **passes right now** (ran it: `exit_check=0`). The only
   real work in flight is complete and will close at 0 spend on the first
   dispatch pass once admission opens.

## Still owner-only

- abandon the 119 stale stubs (there is no `abandon` verb; deleting task rows is
  not a seat's call);
- arm `office.autowork.dispatch` — safe **after** `819a03626`, because new stubs
  can no longer be closed by a check that always passes.
