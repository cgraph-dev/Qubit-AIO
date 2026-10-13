# 60 — Evolution Engine (the ratchet)

For the business to compound without the owner in the loop, agents must get better at their jobs
on their own. That is D4 (daily reflection) and D5 (baseline vs ratchet). This file is the
mechanism.

## The two halves

```
baseline/            ratchet/
  prompt.md            notes.md          ← learned heuristics
  tools.yaml           playbooks/*.md    ← proven multi-step procedures
  model tier           skills/*          ← authored skills (real skill tree)
  tier: best|cheap     scores.json       ← the evidence for every entry
```

**Baseline is fixed.** It is the agent's hire contract: instructions, allowed tools, model tier.
It changes only through a manager-approved promotion, which is a recorded event with a reason and
a cost (D5). An agent cannot edit its own baseline — this is the single most important rule in
this file. An org where every agent can rewrite its own instructions has no way to tell
improvement from drift.

**Ratchet only grows.** Every entry is `{id, added_at, source_artifact_ids, score, active}`.
Entries are never deleted and never rewritten in place — a bad entry is marked `active: false`
with a reason and kept. This is what lets the org look back and answer "why does this agent
believe that?" six months later, which is the same reason the ledger is append-only.

## The daily turn

One turn per agent per day, cheap tier, budget-capped. It is a *reflection*, not a work turn. It
sees only:

1. its own `office_event` rows from the last 24h,
2. its own `office_ledger` rows from the last 24h (what it earned, what it burned),
3. any manager review comments on its last N artifacts,
4. its current ratchet file,
5. its own artifact list with the outcome of each (accepted / rejected / sold / ignored).

It produces **exactly one** of:

- a ratchet entry (a heuristic, a playbook, or a skill), with the artifact ids that justify it;
- a request for a tool it does not have (goes to the manager as a normal escalation);
- nothing, with a stated reason ("no new signal").

**The cap is on the output, not just the tokens.** One change per day per agent. An agent that
proposes five changes a day is not learning, it is thrashing, and the cap is what stops the
ratchet from becoming noise.

## Scoring — the part that makes "smarter" not a story

A ratchet entry is only *active* if it has a score derived from artifacts, and artifacts have
ledger rows. `45-ECONOMY.md` defines the attribution; this is what it is for.

```
score(entry) = outcome_delta(artifacts_after_entry) - outcome_delta(artifacts_before_entry)
```

The comparison is deliberately **within-agent and over its own history** — this agent's earned
cents per artifact before the entry vs after it — not against other agents. That avoids the two
classic failure modes: an agent in a rich department looking "smarter" than one in a poor one,
and two agents being scored against each other for doing different work.

Entries whose score stays at or below zero across a full evaluation window are deactivated with a
recorded reason. The ratchet keeps growing; the *active* set stays honest.

## The three ways it compounds

| Mechanism | Cadence | Who | What it produces |
|---|---|---|---|
| **Per-agent ratchet** | daily | every agent | notes, playbooks, skills that improve that agent's own output |
| **Cross-pollination** | weekly | managers | the best entries from an agent's ratchet get copied into the department playbook and, if broadly useful, promoted to the shared skill tree |
| **Org-level review** | weekly | CEO | reviews department playbooks, kills what does not work, and revises the org's own `docs/` — the company learns about itself |

The weekly cross-pollination is what makes a whole floor better rather than one desk better. The
weekly CEO review is what keeps the org from converging on one playbook that no longer fits the
market.

## "Faster" — the second axis

Speed is a first-class outcome, not a side effect, because it is money: a faster agent burns fewer
tokens per artifact. The runner measures **wall-clock and tokens per artifact**, both recorded on
the artifact's ledger rows. A ratchet entry that reduces tokens per artifact at equal outcome is
scored positively even if revenue is flat.

The three levers, in the order the org should reach for them:

1. **Fewer tokens** — a tighter prompt, a better playbook, a cached context. Cheapest win.
2. **Cheaper tier** — a proven playbook that works on the cheap model is a permanent cost cut.
   This is also how agents earn promotion: an agent whose playbook lets a cheaper model do the
   job is worth more than one that needs the expensive model.
3. **Parallelism** — the manager splits work across more desks. This costs money; it is a last
   resort for a proven business, not a default.

## Failure modes this design is trying to prevent

| Failure | What stops it |
|---|---|
| Agent rewrites its own instructions into something unrecognizable | Baseline is immutable without approval (D5) |
| Ratchet fills with untested opinions | An entry only activates with artifact-backed score |
| Everything gets optimized to zero cost, quality collapses | Reputation is **measured and fed to the daily reflection** (D25): an agent whose refunds and complaints rise sees it as evidence against its own ratchet entries, and deactivates them itself — or does not, and takes the consequences |
| One floor's playbook is copy-pasted to another where it is wrong | Cross-pollination is a manager decision with a reason, not automatic |
| Improvement is claimed but unmeasurable | Every entry is tied to artifact ids that have ledger rows |
| The reflection loop quietly becomes the biggest cost | Hard daily cadence, hard budget cap, capped at one output |
