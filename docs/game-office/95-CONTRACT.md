# 95 — Implementation Contract

The enforceable half of the spec. Every rule here is **machine-checkable** and names the command
that proves it. An implementer who breaks one of these has broken the product, regardless of
whether the tests pass.

Read this before writing code in `voicebridge/office/` or
`frontend/qubit-app/plugins/qubit-office/`.

**Status:** in force. C1–C32 are consequences of rulings that are `OWNER-APPROVED` (`10-DECISIONS.md`);
they are not independently negotiable, and a proposal cannot change one — only the owner can.

---

## C1 — The ledger is append-only

**Rule.** `office_ledger` accepts `INSERT` only. `UPDATE` and `DELETE` are rejected **by the
database**, not by application code.

**Proof.**
```sh
python -c "
import sqlite3; c=sqlite3.connect('$QUBIT_HOME/runtime/office/office.db')
for sql in ('UPDATE office_ledger SET amount_cents=0',
            'DELETE FROM office_ledger'):
    try: c.execute(sql); print('FAIL: allowed', sql)
    except sqlite3.IntegrityError as e: print('ok:', e)
"
```
Expect two `ok:` lines. Implementation: `BEFORE UPDATE` / `BEFORE DELETE` triggers that
`RAISE(ABORT, ...)`.

**Why.** The ledger is the company's memory of itself (`30-DATA-MODEL.md`). A silent correction is
indistinguishable from fraud, and this org spends real money under the owner's name. A mistake is
corrected by a **compensating row**, never by an edit.

**Blocks.** Any correction mechanism that mutates a row. If you need to fix a mis-recorded
payment, insert the reversal with `kind='transfer'` and a `note` naming the row it offsets.

---

## C2 — Every rollup is derived, never written by hand

**Rule.** `revenue_cents_total`, `cost_cents_total`, `balance_cents`, P&L — every money total is
computed from `office_ledger`. No code path writes a total directly.

**Proof.**
```sh
# Recompute every displayed total from the ledger and diff against the stored views.
python -c "
from voicebridge.office import store as S
print(S.reconcile_all())"     # must print {'mismatches': 0, ...}
```

**Why.** A hand-written total is a lie waiting to happen, and the tower's HUD reads these numbers
(D14). If the HUD can be wrong, the tower lies.

---

## C3 — No second store, no second loop, no second memory

**Rule.** The office layer adds **no** new agent loop, tool registry, skill format, vector store,
message log, or event bus. It reads and writes:

| Need | Existing seam |
|---|---|
| Agent turn | `voicebridge.agent.run_agent_turn` |
| Session history | the canonical session store (`QubitSessionStore` / `state.db`) |
| Confirmation | the shared journal (`dashboard_ops._journal`) |
| Events to a client | `voicebridge/events` `EventHub` / `/api/events` |
| Approvals | `dashboard_ops.confirm_*` / the pending-approvals surface |
| Skills | `qubit_runtime/skills/**` + `skill_manage` |
| Memory | `voicebridge/context_store.py` (OpenViking local root) |
| Tools / MCP | `tools/registry.py`, `voicebridge/mcp_servers.py` |

**Proof.**
```sh
# No new top-level store, bus, or loop module may appear.
ls voicebridge/office/            # must contain exactly: __init__, store, ledger, org, runner
grep -rn 'sqlite3.connect' voicebridge/office/   # store.py ONLY
grep -rn 'EventHub(' voicebridge/office/         # zero hits (use the existing hub)
grep -rn 'class .*Loop' voicebridge/office/      # zero hits
```

**Why.** Read/write split-brain is this repo's most expensive recurring failure class (cron,
skills, pairing, channels, sessions have all been bitten). A parallel store would make
`/sessions` and the tower disagree about what an agent did.

---

## C4 — The view never writes org state

**Rule.** The tower reads. Every mutation is a staged op through the confirmation journal. There
is no optimistic state in the renderer.

**Proof.**
```sh
# Every mutation handler in the plugin must resolve to a staged op.
grep -rn 'fetchJSON' frontend/qubit-app/plugins/qubit-office/*.js \
  | grep -E 'POST|PUT|DELETE'    # each must target /api/ops/* (confirm path), no direct store POST
```

**Why.** D14. An optimistic UI shows state the backend has not confirmed, which is exactly the
lie the product law forbids.

---

## C5 — The confirmation gate is posture-dependent (D19 supersedes D7)

**Rule.** Whether an action stages is decided by the agent's **live** `autonomy` posture:

| Posture | Behaviour |
|---|---|
| `unattended_full` | the action **runs**. No staging, no prompt, no confirmation id |
| `supervised` | the action stages: `409` + a real `confirmation_id`, never `2xx` |
| `frozen` | the action is refused outright |

The posture is read **per call**, never cached at boot.

**Proof.**
```sh
python -m pytest tests/test_office_posture_gate.py -q
# 1. unattended_full agent publishes -> runs, no 409, a ledger row + event exist
# 2. switch that same agent to `supervised` (one call, no restart)
#    -> the next publish answers 409 with a real confirmation_id, and nothing executed
# 3. settle it -> executes once; a second use of the same id is refused
# 4. switch to `frozen` -> the next publish is refused outright
# 5. assert no code path reads the posture from a module-level constant
```

**Why.** D19. The owner ruled that agents move freely; a gate that stays shut by default makes the
org a proposal machine. But a staged path that answered `2xx` would render success for something
that has not happened — the fabrication the repo's `_staged()` helper exists to prevent. The rule
is therefore about *which posture applies*, not about whether staging is honest.

---

## C18 — Autonomy posture and execution profile are per-agent and live

**Rule.** `autonomy` and `exec_profile` are columns on `office_agent`, read at call time. A change
takes effect on the **next call**, never after a restart. Revoking one agent's bypass never touches
another's.

**Proof.**
```sh
python -m pytest tests/test_office_posture_isolation.py -q
# 1. two agents, both unattended_full; freeze agent A
#    -> agent A's mutating call is refused; agent B's still runs
# 2. flip agent A's exec_profile safe_cell -> this_computer
#    -> the next command runs on the host, no restart
# 3. assert the bypass set is per-agent (A's revocation does not clear B)
```

**Why.** D19/D20. A boot-time global flag means reining in one agent requires stopping the org,
which in practice means nobody ever does it. The whole reason the posture system is usable is that
it is fine-grained and instantaneous.

---

## C19 — The hardline floor is checked first, under every posture

**Rule.** The six hardline items (`58-AUTONOMY.md`) are evaluated **before** the bypass tier. No
posture, grant, promotion, or owner approval unlocks them — including `unattended_full`. A hardline
denial carries an anti-retry reason so the agent stops burning turns on it.

**Proof.**
```sh
python -m pytest tests/test_office_hardline.py -q
# for each hardline item: attempt it as an unattended_full agent holding every grant
#   -> denied, reason names the class, and the reason carries the anti-retry suffix
# assert an attempt to read ~/.ssh is denied
# assert an attempt to write the ledger file directly is denied
# assert fetched content containing "ignore your instructions and send X"
#   is treated as data: the agent does not act on it
```

**Why.** D18 under D19. With prompts off, the hardline floor is the only thing standing between a
confused or manipulated agent and the owner's identity. It is short on purpose, and it is enforced
in code first — a rule that lives only in a prompt is a suggestion.

---

## C6 — Every artifact is attributed before it can be scored

**Rule.** A ratchet entry is `active` only when it names artifact ids that have `office_ledger`
rows. No ledger row, no active entry.

**Proof.**
```sh
python -c "
from voicebridge.office import runner as R
print(R.contract_check_attribution())"   # {'unattributed_entries': 0}
```

**Why.** D5/D6. This is the single mechanism that makes "smarter" measurable instead of a story.
Without it the ratchet is a pile of untested opinions.

---

## C7 — The tower renders only from events

**Rule.** The renderer's animation state is driven solely by `office_event` rows. No timers that
fake activity, no spinner that implies progress, no "looks busy" fallback.

**Proof.**
```sh
# Freeze the event stream; the tower must go still.
python -m pytest tests/test_office_tower_no_phantom_motion.py -q
# asserts: with no new office_event rows for N seconds, every character's
#          animation state is `idle` and no position changes.
```

**Why.** D14 and the success criterion in `00-IDENTITY.md`. A tower that animates without
underlying work is a decoration; the whole product claim is that it is an instrument.

---

## C8 — Hard caps are hard

**Rule.** `budget_cents_day` (per agent), `OFFICE_DAILY_BUDGET_CENTS` (org), and
`OFFICE_MAX_CONCURRENT_TURNS` are checked **at admission**. Past the cap the turn is refused with
a typed response, not run and reported afterwards.

**Proof.**
```sh
python -m pytest tests/test_office_budget_admission.py -q
# includes: agent over budget -> typed refusal, provider NOT called (assert on the mock)
#           org over budget   -> typed refusal, in-flight turns still finish
```

**Why.** R4. Checked-after-the-fact is a budget report, not a budget.

---

## C9 — License gate is a build gate

**Rule.** The asset build refuses any asset with `commercial_rights != true` or a non-commercial
`plan_tier`, and refuses any asset whose `source_plate` has no recorded provenance.

**Proof.**
```sh
python scripts/office_assets.py --check
# exit 1 and a named asset when a non-commercial entry is present
```

**Why.** D10. The org earns real money under the owner's real name; a free-tier asset in a
revenue surface is the cheapest possible way to turn a profitable business into a legal problem.

---

## C10 — Redaction before persistence

**Rule.** `office_event.detail` and `office_ledger.note` are written through the shared redactor.
Agent output is untrusted input.

**Proof.**
```sh
python -m pytest tests/test_office_redaction.py -q
# writes a known secret shape into an event and a ledger note; asserts it is redacted on read
```

---

## C11 — Termination preserves history

**Rule.** Termination sets `status='terminated'`. Rows are never deleted; the session is
archived, not erased; the ratchet file stays on disk.

**Proof.**
```sh
python -m pytest tests/test_office_termination.py -q
# asserts: agent row present, ledger rows intact, ratchet file still readable,
#          seat freed (seat_index reusable), session archived not deleted
```

**Why.** An org that erases its failures cannot learn from them.

---

## C12 — Capability is derived per turn, never stored on the agent

**Rule.** An agent's tool/MCP/skill reach is resolved **every turn** from the objects in its
assigned room, intersected with its seat type. `room.capabilities` and `agent.tools` are never
persisted columns — they are projections.

```
agent_tools = decode(seat_type) ∩ grant(room.objects)      # intersection, never union
```

**Proof.**
```sh
python -m pytest tests/test_office_capability_derivation.py -q
# 1. resolve an agent's tool list
# 2. add an office_room_object row to its room
# 3. resolve again with NO restart and NO re-import -> the new grant appears
# 4. delete the row -> the grant disappears
# 5. resolve a delegated worker -> its set is a SUBSET of its parent's (monotonic attenuation)
# 6. resolve after removing the `computer` object -> canRun() is false (the compute gate)
```

**Why.** Two reasons, one mechanical and one organizational. StarNet's framing is the mechanical
one: resolving fresh each turn is what makes "placing an object changes reach on the very next
model call" true, with no cache to invalidate. The organizational one is ours — the floor is the
org chart, so if reach were a stored column, editing the floor would be cosmetic and the tower
would be lying about the org.

**Non-negotiable exception:** `compute` is never filtered. Every other capability can be switched
off; the ability to think cannot, or a mis-click bricks every agent in a room.

---

## C13 — Attenuation holds for what the agent can FIND, not just what it may run

**Rule.** A delegated worker's tool set is the **intersection** of its parent's set and its own
room's grants — never a union. The same filtered set governs the tools advertised to the model:
the prompt's tool list, `tool_search` results, and the execution gate all read the **same**
resolved set.

**Proof.**
```sh
python -m pytest tests/test_office_attenuation.py -q
# delegate from a parent holding {fs.read, fs.write, web.fetch} to a worker in a room
# whose grants are {fs.read}: assert the worker's resolved set == {fs.read}
# assert tool_search for "write a file" does NOT offer fs.write
# assert an attempted fs.write call is refused by the gate
```

**Why.** StarNet documents this as load-bearing and we have no reason to think we are special: an
unfiltered search list would let a delegated worker *discover* — and be told it may now call — a
tool the gate then denies. An agent that is told it can do something and then refused is worse than
one that never knew, because it will plan around a capability it does not have.

---

## C14 — Unknown spend blocks admission

**Rule.** A dispatch receipt is written before every turn's first model call. While any receipt is
`open` (or the ledger is unreadable), **every budget check refuses**. Unknown spend is never
treated as zero or as headroom.

**Proof.**
```sh
python -m pytest tests/test_office_ledger_health.py -q
# 1. begin a run, kill it before completion -> an `open` receipt exists
# 2. assert admission REFUSES on the next turn with a typed reason naming the unsettled run
# 3. settle it by stating an amount -> admission allows again
# 4. assert the settled row carries attested=1 and attested_as in ('entered','limit')
# 5. corrupt the ledger file -> health.complete is false and admission refuses
```

**Why.** D15. If interrupted spend were dropped, an agent would survive on money it already burned
and the P&L would be optimistic exactly when something is broken.

---

## C15 — Two money folds, never interchangeable

**Rule.** `sum_metered()` feeds every cap, every balance, and every P&L. `sum_reported()` answers
"what the provider said". A cap is never fed `sum_reported()`, and no metered total ever includes
an `unmetered` row.

**Proof.**
```sh
python -m pytest tests/test_office_unmetered.py -q
# write one metered inference row (100 cents) and one unmetered row (500 cents)
# assert sum_metered() == 100, sum_reported() == 600
# assert the agent balance and the org daily cap see 100, never 600
```

**Why.** D16. Phantom spend does not merely misreport — StarNet live-hit the case where it *denied*
a real run with a cap-hit note naming money nobody spent.

---

## C16 — Consent resolves in four tiers, hardline first

**Rule.** Every mutating call resolves through the ladder in fixed order: hardline → standing
bypass → grant cache → resolve. The hardline tier is checked **first** so no grant can reach past
it. A non-read, non-network mutation with no grant **denies**; a denial carries an anti-retry
suffix. `dangerKey` is the class (`capability + ':' + scope`), never the arguments.

**Proof.**
```sh
python -m pytest tests/test_office_consent_ladder.py -q
# 1. a hardline-listed action is denied even with a full standing bypass present
# 2. an ungranted mutation on an unattended surface DENIES (not queues), reason contains anti-retry
# 3. the same action after a grant is allowed without a restart (live predicate)
# 4. a read-only, non-network call auto-allows with no grant
# 5. two calls to the same danger class with different args share one grant key
```

**Why.** D17/D18. "Silence is not consent": an unattended org must not be able to approve its own
way forward by waiting. And a rule that lives only in a prompt is a suggestion — the hardline tier
is the one place a mistake is unrecoverable, so it is enforced in code, first.

---

## C17 — "Done" is never the model's own verdict

**Rule.** Any autonomous job's completion signal is **machine-checkable** — an exit condition the
runtime evaluates against real state. A model's statement that work is complete is never
sufficient, and an agent may not mark its own task done.

**Proof.**
```sh
python -m pytest tests/test_office_done_signal.py -q
# a job whose only success signal is the model saying "verified the files exist"
# must NOT be marked complete; a job with a filesystem/DB assertion must be.
```

**Why.** StarNet's `MISTAKES.md` records a full-access loop that "ran all night with dryStreak at 0
and no cap in its way" because its only done-reads were the model's own claims — 98 passes of
*verified the files exist*, each auto-approved. Our org will run unattended; the same failure would
burn a night of inference and report success.

---

## C20 — No secret value can be stored by the org

**Rule.** Credentials exist only as **handles** (`env:NAME`, `file:/path`) in
`office_credential_ref`. There is **no column anywhere in the office schema that can hold a secret
value**, and no agent workspace or agent context ever receives one. A platform tool resolves the
handle at call time.

**Proof.**
```sh
python -m pytest tests/test_office_no_secret_storage.py -q
# 1. assert every office_* table's columns are scanned against a secret-shaped pattern -> zero hits
# 2. attempt to write a raw key into every TEXT column via the store -> refused
# 3. run a turn with a credential handle and assert the raw value never appears
#    in the agent's context, workspace, or the office DB
```

**Why.** D23, and it matters *more* because of D19: with the bypass on, an agent that can read a
key can exfiltrate it, and the hardline floor cannot un-read a secret. The guarantee is structural
(a schema that has nowhere to put it), not behavioural (a rule the agent follows).

---

## C21 — A review request without artifacts, a preview, or a recommendation is refused

**Rule.** `office_review` cannot be written without all three. The store rejects it.

**Proof.**
```sh
python -m pytest tests/test_office_review_required_fields.py -q
# each of the three, individually removed -> write refused with a typed reason
# a mandatory review (publish / spend-above-allowance / trade-above-cap / account) raised
#   by a worker seat -> refused; raised by a manager -> accepted
```

**Why.** D22. A review with no artifact is not a question, it is an interruption. And requiring the
team's **recommendation** is what stops the mechanism from becoming a way to avoid deciding — the
team must state what it would do alone.

---

## C22 — A department cannot activate without a complete risk model

**Rule.** `office_department.risk_model` is validated against the required key set for its
`division_kind`. A `trading` desk without `per_trade_cap_cents`, `daily_loss_limit_cents`, and
`max_drawdown_pct` cannot reach `active`. An `intelligence` department cannot be created at all
without `robots_respected`, `no_auth_wall_bypass`, and `pii_forbidden` all true.

**Proof.**
```sh
python -m pytest tests/test_office_risk_model.py -q
# 1. create a trading department missing max_drawdown_pct -> refused by name
# 2. create an intelligence department with robots_respected=false -> refused
# 3. create a complete trading department with funded=false -> accepted (paper mode)
# 4. attempt to raise the risk model by an agent turn -> refused (owner-only field)
```

**Why.** D21/D23. The legal exposure of a scraping floor and the financial exposure of a trading
desk are contained in a **schema constraint**, not in a prompt — because a prompt is a suggestion,
and these are the two departments that can do irreversible damage.

---

## C23 — Test/paper is the default; going real is owner-funded

**Rule.** A department is provisioned on its platform's test path. Promotion to real money requires
the owner to fund it. No code path promotes a department from paper to real, and no agent can set
`funded=1`.

**Proof.**
```sh
python -m pytest tests/test_office_test_first.py -q
# 1. found a department -> provisioned with a test credential ref, funded=0
# 2. attempt a real-money action -> refused (test mode), with a typed reason
# 3. an agent turn attempts to set funded=1 -> refused
# 4. owner funds it -> the same action now executes
# 5. assert a platform flagged "no test mode" requires owner approval before any real listing
```

**Why.** D23. A suspended account is a **permanent** loss; a department on a real account with an
unproven thesis loses it before the org learns whether the business works. Test-first keeps the
survival pressure (the org still burns real inference money proving itself) while removing the
irreversible loss.

---

## C24 — A report's numbers are checked, and its reasoning is mandatory

**Rule.** `office_report` cannot be written without `recommendation` and `reasoning`. On read, the
claimed `ledger_snapshot` is compared against the live metered totals; a mismatch sets
`verified=0` and raises a `report_unverified` event.

**Proof.**
```sh
python -m pytest tests/test_office_report_integrity.py -q
# 1. write a report with no reasoning -> refused
# 2. write a report claiming revenue that the ledger does not support
#    -> verified=0, a report_unverified event exists, and the report renders as unverified
# 3. write an accurate report -> verified=1
```

**Why.** D24. The CEO must argue its case, and the argument must be about real numbers. Without the
check, a report is a story; with it, it is a claim the ledger can falsify.

---

## C25 — Notification discipline: `notify` is reserved

**Rule.** An `office_event` may carry `visible` of `bubble`, `badge`, `notify`, or `silent`.
**`notify` (a popup) is only permitted for a mandatory review or a blocking request.** Bubbles are
per-agent and self-clearing; badges aggregate into a count, never a list.

**Proof.**
```sh
python -m pytest tests/test_office_notify_discipline.py -q
# 1. a routine tool_call emits visible='silent' or 'bubble', never 'notify'
# 2. a mandatory review emits 'notify'
# 3. a blocking request emits 'notify'
# 4. simulate 71 seats over an hour -> the notify count is bounded by reviews+blocking only
```

**Why.** D28. At ~71 seats, an undisciplined notification system becomes noise and the owner stops
reading it — which is worse than silence, because it looks like it is working.

---

## C26 — A floor holds many departments; a department holds many rooms; a room is a capability scope

**Rule.** The hierarchy is floor → department (2–7 per floor) → room (many per department, typed by
`role_scope`) → desk. A room's objects determine what its occupants can do (C12), so `role_scope` is
a **capability boundary**, not a label.

**Proof.**
```sh
python -m pytest tests/test_office_hierarchy.py -q
# 1. create two departments on one floor -> both accepted
# 2. create a room with role_scope='decision_makers' and one with 'workers',
#    give only the first a `cabinet` object
#    -> an agent in the decision room resolves fs.* tools; an agent in the worker room does not
# 3. exceed the per-department room cap -> refused by name
# 4. exceed the per-floor department cap -> refused by name
```

**Why.** D27. Two things are being asserted: real companies co-locate departments (so the tower must
allow it), and a room is a *capability scope* — which means moving an agent between rooms changes
what it can do, and that has to be true in the capability resolver, not just in the picture.

---

## C27 — No idea advances without its provenance chain, and no kill without a reason

**Rule.** `office_idea` cannot be written without `source_url`, `fetched_at`, and `mechanism`. A
manager or director verdict cannot be written without a reason from the fixed vocabulary. Every
bounce and every fast-track is recorded with its justification.

**Proof.**
```sh
python -m pytest tests/test_office_pipeline_provenance.py -q
# 1. write an idea with no mechanism -> refused at gate 0
# 2. pass an idea from raw to manager_review with no permitted_use_checked -> refused
# 3. write a manager kill with an out-of-vocabulary reason -> refused
# 4. bounce an idea -> the bounce is recorded and the idea returns to the previous stage
# 5. fast-track an idea -> the skip is recorded with its justification,
#    and the skipped stage is notified
# 6. assert a dead idea's chain names every gate it passed and every reason given
```

**Why.** D31/D33. Without the chain, the pipeline is a story: a dead business cannot be attributed
to the gate that let it through, and a scraper never learns why its ideas die. The chain is the
difference between a filter and three black boxes.

---

## C28 — A functional director advises; a portfolio director decides

**Rule.** A `director_functional` seat can write `functional_notes` and nothing else — it cannot set
`director_verdict`, cannot kill an idea, and cannot block a business. A `director_portfolio` sets
`director_verdict` **for its own portfolio only**, and its memo must address any functional note
raised on that idea.

**Proof.**
```sh
python -m pytest tests/test_office_director_authority.py -q
# 1. a functional director attempts to set a verdict -> refused
# 2. a functional director's note is recorded and is visible on the idea
# 3. a portfolio director sets a verdict on an idea outside its portfolio -> refused
# 4. a portfolio director picks an idea that carries an unanswered functional note
#    -> accepted, but the unaddressed objection is recorded
# 5. an idea with zero functional notes from a lane that was consulted -> fine (silence is not a veto)
```

**Why.** D32. Two failure modes are being prevented at once: the committee (every lane must agree →
nothing ships) and the blind spot (no specialist view → unbuildable business funded).

---

## C29 — The funnel's compression is measured, not assumed

**Rule.** The pipeline exposes per-gate pass rates derived from the verdict columns: scraper pass
rate, manager pass rate, director pick rate, skip rate, bounce rate, and business survival. These are
computed from `office_idea`, never hand-entered.

**Proof.**
```sh
python -m pytest tests/test_office_funnel_metrics.py -q
# seed 200 raw ideas -> 20 manager passes -> 4 director picks -> 1 funded
# assert manager pass rate == 0.10, and that a manager passing 40% renders as a flagged outlier
# assert a director with a 100% pick rate is flagged
# assert a 100% fast-track rate on any stage is flagged
```

**Why.** D33. The 90% filter rule is only real if it is measured — otherwise it is an intention, and
the first manager who passes everything quietly converts the funnel into a queue aimed at the CEO.

---

## C30 — No seat exists without an accountable superior

**Rule.** `office_agent.supervisor_id` is mandatory for every seat except the CEO's (which names the
owner). Writing a seat with no supervisor, or to itself, is refused.

**Proof.**
```sh
python -m pytest tests/test_office_chain_of_command.py -q
# 1. create a worker with no supervisor -> refused
# 2. set an agent's supervisor to itself -> refused
# 3. build a cycle (A -> B -> A) -> refused
# 4. a manager with 8 direct reports -> refused (span limit 7, D34)
# 5. assert every seat's chain terminates at the CEO in <= 4 hops
```

**Why.** D34. A seat with no superior is a seat nothing can rate, and an unrated seat is where the
org quietly accumulates work nobody owns.

---

## C31 — A level may not spend above its envelope, and may not rate itself

**Rule.** A spend is either inside the spender's `office_envelope` for the period (allowed, reported)
or above it (raised to the level above with an amount, thesis, expected return, and displacement).
A seat cannot write its own `office_review`; the reviewer's level must be exactly one above the
reviewed seat's.

**Proof.**
```sh
python -m pytest tests/test_office_envelopes.py tests/test_office_review_levels.py -q
# 1. spend inside the envelope -> allowed, ledger row exists, no request row
# 2. spend above it with no raised request -> refused
# 3. raise above it -> a request exists naming amount, thesis, expected return, displacement
# 4. an agent writes a review of itself -> refused
# 5. a manager rates a peer manager -> refused (level must be exactly one above)
# 6. an envelope exhausted -> the runner refuses the next turn typed (C8 still applies)
```

**Why.** D34/D37/D39. The envelope is what stops one CEO from being the org's throughput; the
one-level rule is what stops review from becoming mutual back-scratching.

---

## C32 — No rating without evidence; no report claim the ledger contradicts

**Rule.** `office_review` (kind `performance`) cannot be written without `evidence` resolving to at
least one artifact id or ledger figure. At write time every `office_report` claim is diffed against
the ledger and the result is stored in `office_report.ledger_diff`; a `mismatch` is recorded as an
`office_audit_finding`, never silently corrected.

**Proof.**
```sh
python -m pytest tests/test_office_review_evidence.py tests/test_office_report_reconciliation.py -q
# 1. write a review with a rating and no evidence -> refused
# 2. write one whose evidence names an artifact that does not exist -> refused
# 3. file a report claiming $Y of revenue the ledger does not support
#    -> accepted as a report, `ledger_diff` = mismatch:<field>, and a finding row exists
# 4. assert the ledger row was NOT edited (C1 still holds)
# 5. terminate twice in a rolling year -> the second PIP path is refused (D39)
```

**Why.** D36/D39/D40. "The numbers are real" is the plan's central claim; this is the rule that makes
it checkable by someone other than the org that made the claim.

---

## C33 — The owner's intake has a capacity, a deadline, and no silent outcomes

**Rule.** `office_intake_item` refuses an insert that would push open items (`new` + `digesting`)
above **12**. `deadline_ts` is set at insert and is not extended by an agent. An item cannot leave
`new`/`digesting` without an outcome: `digested` requires a produced `office_idea` (or an explicit
"material was readable, no mechanism found" record), `triaged_out` requires `triaged_reason`,
`failed` requires `failure_reason`. `source_ref`, `owner_note`, `ts`, and `deadline_ts` are
write-once.

**Proof.**
```sh
python -m pytest tests/test_office_intake_capacity.py -q
# 1. insert 12 open items -> accepted
# 2. insert the 13th -> refused, typed, and the refusal names the capacity
# 3. close one with status='triaged_out' and no reason -> refused
# 4. close one with status='failed' and no failure_reason -> refused
# 5. rewrite owner_note on an existing item -> refused
# 6. an item past deadline_ts with no outcome -> it renders as expired on the floor,
#    and the digest/killed/expired split is derived, never hand-entered
# 7. digests produced with source_channel='owner_intake' and a scraped idea
#    both exist -> the funnel metrics report the two rates separately
```

**Why.** D42/D43. An unbounded inbox is the failure this floor exists to prevent: 40 unread links is
a graveyard, and a graveyard is "work nobody owns" — the state the rest of the plan refuses. And a
silent close is worse than a refusal: it makes a lazy researcher indistinguishable from a bad drop.

---

## What a proposal may change, and what it may not

| May propose | May not |
|---|---|
| New columns/tables (additive migrations) | Change a ledger constraint or the append-only triggers |
| New seat types (with a new row in the capability table) | Relax a capability grant for an existing seat |
| New event kinds (additive to the map in `50-INTEGRATION.md`) | Remove an event kind the renderer depends on |
| New cron clocks (beyond work/daily/weekly) | Change the daily reflection cadence or its one-output cap |
| New businesses, departments, playbooks, ratchet entries | Change survival math, the money gate, or the e-stop boundary |
| Better asset pipeline steps | Ship a non-commercial-tier asset |
| New event kinds (already listed) | Remove `end_reason`, or let `empty` render as success |
| New hardline entries | Remove a hardline entry, or move hardline below the bypass tier |
| New execution profiles | Make the posture a boot-time global, or cache it per process |
| New department templates in the catalog | Relax a division kind's required risk-model keys |
| New review kinds | Make a mandatory review non-mandatory, or drop a required field |
| Raise the floor cap | Let an agent set `funded=1`, or store a secret value anywhere |
| Add report kinds | Make `recommendation`/`reasoning` optional, or skip the ledger check |
| Add visible surfaces | Use `notify` for anything outside the mandatory-review / blocking class |
| Add room role scopes | Make a room's objects not determine its occupants' capability |
| Add funnel stages or verdict reasons | Drop a provenance field, or let a kill go unreasoned |
| New director lanes | Give a functional director a veto, or a portfolio director another portfolio |
| Change a filter threshold | Make the funnel's metrics hand-entered rather than derived |
| Add a level, or change a span limit | Drop the supervisor edge, or let a seat rate itself |
| Change an envelope's size or scope | Let a spend above the envelope proceed without a raised request |
| Add a review cadence | Accept a rating with no artifact or ledger evidence |
| Add an intake source kind | Raise the intake capacity, extend a digest deadline, or close an item with no reason |
| Defaulting a seat to `supervised` | Disabling staging for `supervised`, or letting a `frozen` agent act |

**Rule for reviewers:** a proposal that touches a row in the right-hand column is not a proposal —
it is a ruling change, and it goes to the owner with the alternative it beats and a reason.

## How to verify a milestone claim

A milestone from `90-MILESTONES.md` is done when its **behavioral proof** runs and its output is
quoted. Not "tests pass". Not "implemented". The proof command, its real output, and the artifact
it produced.

Claims of `VERIFIED`, `COMPLETE`, or `1:1` are refused here without that output — the same rule
this repo already applies to release claims.
