# Packet 8P — Verified skill memory + controlled evaluation

Optional, disabled-by-default capability that only "remembers" a task recipe
after an explicit OWNER-gated acceptance check passed it, plus a bounded
offline statistical comparison of prompt/model candidates with a promotion
gate. Adapted from the smallest tested concepts of **Praxis** (MIT) and
**Evolve** (Apache-2.0); see `docs/UPSTREAM_REUSE.md` R26/R27 and
`THIRD_PARTY_NOTICES.md`.

## 1. What this is

- `voicebridge/verified_skills/` — a **verified skill memory** store package
  of clearly owned sub-components, each <300 lines (`_models`, `_persist`,
  `_lifecycle`, `_recall`, `_transfer`, composed `store`), plus the shared
  `voicebridge/state_journal.py` hash-chain journal. A candidate recipe is
  staged `quarantined`; it becomes `verified` only through an explicit human
  **owner** verdict. Negative ("known failure") records, budgeted top-K recall
  with a separate negatives slot, deduplication/reinforce, retention tiers,
  and quarantine of failed/expired/revoked/stale skills. The authoritative
  fact `MemoryStore` is untouched.
- `voicebridge/experiments/` — a **controlled offline evaluation** store
  package, each file <300 lines (`_stats` pure statistics, `_models`,
  `_persist`, `_run`, `_promote` owner+gate promotion, `_transfer`, composed
  `store`): champion vs challenger comparison with explicit feedback, a seeded
  deterministic posterior, and a promotion gate that requires both owner
  approval AND a passing regression gate report. Fully offline; never changes
  production model/router/permission configuration.

Both are advisory. Neither grants a capability — a recalled skill recipe may
only run through the existing CapabilityBroker / 7E permission policy. Neither
executes model/page/tool-proposed code on the host. Both persist as private
`0700`/`0600` SQLite files with an append-only deterministic hash-chain journal.

## 2. Verified skill memory — lifecycle

Statuses: `quarantined → verified | refuted`, plus `archived` (retention).
A candidate starts `quarantined`. Only an owner verdict moves it:

- **verify** requires the acceptance test to carry a concrete expectation
  (`MIN_ACCEPTANCE_STRENGTH`); a vacuous/weak test cannot verify. The decision
  is an explicit operator action — there is **no self-verification** path.
- **refute** is terminal (implementation shown wrong).
- A verified recipe with no concrete acceptance test, a failed reinforcement,
  a revoked 7E permission, or staleness (unused for a year) is **quarantined**
  and never recalled as authority.

Negative skills (`record_failure`) record an **observed** failure with
`evidence` and are recalled in a separate `negatives` slot so the agent sees
the wall it hit before retrying.

Recall is bounded: top-K verified hot-tier recipes under a token budget, plus
a separate always-included negatives slot. Context cost is O(k), independent
of library size. Verified near-duplicates reinforce instead of adding rows;
quarantined duplicates suppress re-insertion without inflating utility.

## 3. Controlled evaluation — promotion gate

Signals are clamped to `[0,1]`, explicit weight 5x implicit; empty → neutral
0.5. Per-session scores are binarized at 0.5 and fed to a seeded
`Beta(1+wins,1+losses)` Monte Carlo posterior. The decision is:

- **NEED_MORE_DATA** until both arms have ≥ 20 sessions;
- **PROMOTE** only when `P(challenger > champion) ≥ 0.95` AND the owner
  explicitly approved AND a passing regression/security gate report is
  attached;
- **HOLD** otherwise. It never auto-promotes.

A promoted experiment is a stored decision record only; applying the candidate
to production remains a manual, separately authorized operation.

## 4. Disabled by default + no permission expansion

Both stores are inert until invoked. `voicebridge verified-skills …` and
`voicebridge experiments …` do not change the permission mode, confirmation
policy, browser/account scopes, safety rules, model/audio configuration, or
services. `voicebridge status` and the dashboard are unchanged. Changing a
model/voice/provider/browser/app does not expand skill permissions.

## 5. Privacy

Secret-shaped input is rejected before persistence in both stores. Stored task
text is bounded and redacted; recall returns redacted excerpts. Experiment
signals store typed values and a config fingerprint — never raw private
prompts, credentials, browser text, source code, or secrets. Journals are
hash-chained and verifiable (`verified-skills status`, `experiments status`).

## 6. Persistence, migration, rollback

- Stores: `<state_dir>/verified_skills/skills.db` (+ `.journal.jsonl`) and
  `<state_dir>/experiments/experiments.db` (+ `.journal.jsonl`), `0700`/`0600`,
  WAL, versioned via `PRAGMA user_version`. A newer-than-supported schema is a
  typed `degraded`/fail-closed error, never silently reset.
- Snapshot/export writes a digest-verified backup; restore verifies the digest,
  writes a pre-restore backup, and never downgrades a verified skill in merge
  mode. Both are audited and journaled.
- Removal preserves user data; there is no automatic eviction of pinned or
  actively-used skills.

## 7. CLI

```
voicebridge verified-skills status|list|list-status|show|submit|verify|refute|
    record-failure|recall|pin|unpin|reinforce|quarantine-revoked|sweep|remove|
    retier|snapshot|restore|export
voicebridge experiments status|candidates|candidate|start|list|show|trial|
    finish-trial|signal|evaluate|promote|abort|snapshot
```

Mutating commands are dry-run unless `--confirm`. `verify`/`refute`/
`reinforce`/`pin`/`remove`/`restore` and experiment `promote`/`abort` are
owner-only (`requested_by="user"`); a model/page/tool cannot issue them.

## 8. Tests

`tests/test_packet8p.py` covers: verified-before-remember; no self-verification;
weak/vacuous tests; negative skills and query-specific recall; dedup/reinforce;
retention/quarantine/sweep; secret/injection rejection; journal integrity and
tamper detection; persistence across restart; newer-schema fail-closed;
snapshot/restore/rollback with digest verification; concurrent submits; the
full aggregation/posterior/decision statistics; owner+gate promotion; signal
bounds; CLI parity and dry-run; and the no-unauthorized-imports clean-install
guard.

## 9. Limitations (honest)

- The acceptance "strength" check is a syntactic heuristic; the real gate is
  the explicit owner verdict. There is no sandboxed execution of model-proposed
  acceptance tests in this packet (execution of untrusted code is not added).
- Verified skills are advisory recipes; wiring them into an executor that runs
  through the broker is a separate, later integration and is not claimed here.
- The experiment store does not route traffic or apply promoted candidates; it
  is a decision record only.
