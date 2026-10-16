# Office CLI — completeness and wiring contract

**What this is.** The `qubit office <verb>` surface exposes **83 subcommands**,
wired across `voicebridge/cli.py` (`_cmd_office`) and `voicebridge/office/*.py`.
This document records the result of invoking *every* verb for real, and names
the guard that keeps it true: `tests/test_office_cli_completeness.py`.

**How it was measured.** Every verb was invoked through the REAL parser and the
REAL `_cmd_office` dispatcher against a fresh, hermetic tmp office store (seeded
with a CEO seat and one worker), NOT by grepping source. Each verb was invoked
WITHOUT `--confirm`, so no mutating verb could touch the store (the confirm gate
refuses first — see below).

## The four-site bug class

Wiring an office op touches **four sites**. Miss one and the verb is either
absent from the parser or dies at runtime with a `NameError`:

| # | Site | Where | Symptom if missing | How the guard catches it |
|---|------|-------|--------------------|--------------------------|
| 1 | **import** | the local `from .office import ...` block in `_cmd_office` | `NameError` / `ImportError` **only when that verb's branch runs** | invoking the verb raises; the test reports the traceback |
| 2 | **subparser** | `of_sub.add_parser("<verb>", parents=[common], ...)` in `build_parser()` | argparse rejects the verb: `invalid choice: '<verb>'` (SystemExit 2) | parser enumeration + per-verb parse |
| 3 | **dispatch branch** | the `elif cmd == "<verb>":` before the trailing `else` that returns `OFFICE_UNSUPPORTED` | the verb falls through to the catch-all: a typed `OFFICE_UNSUPPORTED` refusal — which *looks* like a refusal but means the branch is gone | the test treats `OFFICE_UNSUPPORTED` as a FAILURE, per verb |
| 4 | **parity surface** | this document (the quoted verb count) | the doc drifts from the parser | `test_doc_matches_the_parser` |

A grep proves none of these: a branch can be present yet `NameError` on a moved
import, and a parser line can exist while the dispatch branch is missing. Only
invocation settles it.

## Result: all 83 verbs are completely wired

- **83 / 83** verbs are exposed by the parser (site 2 present).
- **0** verbs returned `OFFICE_UNSUPPORTED` → every verb reaches a real dispatch
  branch (site 3 present for all).
- **0** verbs raised a Python exception / traceback → every local import
  resolves (site 1 present for all).
- **40** mutating verbs refuse with `OFFICE_CONFIRM_REQUIRED` when invoked
  without `--confirm` — a typed refusal, never a traceback, and the store is
  never touched.

### Full verb table

Exit code is what a bare invocation (no `--confirm`) returns on the seeded
store. "traceback?" is whether any Python traceback/`NameError` was produced.

| verb | kind | exit w/o --confirm | refusal code | traceback? | verdict |
|---|---|---|---|---|---|
| `adjudicate` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `admit` | read-only | 2 | `OFFICE_ADMISSION_REFUSED` | no | WIRED (read-only) |
| `approve` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `at-risk` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `attribute` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `attribution-check` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `auditor-hire` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `autowork` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `business-advance` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `business-fund` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `business-pnl` | read-only | 2 | `OFFICE_NOT_FOUND` | no | WIRED (read-only) |
| `business-propose` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `business-terms` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `businesses` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `cap-set` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `caps` | read-only | 0 | `-` | no | WIRED (read-only) |
| `chain` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `consent` | read-only | 0 | `-` | no | WIRED (read-only) |
| `department-create` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `dispatch` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `envelope-allocate` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `envelope-split` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `envelopes` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `escalate` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `escalation-resolve` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `escalation-sync` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `escalations` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `finding-close` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `findings` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `floor-create` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `floorplan` | read-only | 0 | `-` | no | WIRED (read-only) |
| `floors` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `hardline` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `headroom` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `health` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `hire` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `init` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `ledger` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `org-facts` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `orphans` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `penalty` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `pnl` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `programme` | read-only | 0 | `preview` | no | WIRED (read-only) |
| `proposals` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `propose` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `raise` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `reap` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `receipts` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `reconcile` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `record` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `reject-proposal` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `report` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `reports` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `request-decide` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `requests` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `revenue` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `reverse` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `review` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `reviews` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `room-create` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `roster` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `run` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `run-begin` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `run-finish` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `run-halt` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `runway` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `seat-move` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `separation` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `settle` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `spot-audit` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `task-claim` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `task-create` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `task-review` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `task-trail` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `tasks` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `terminate` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `tick` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `tower` | read-only | 0 | `-` | no | WIRED (read-only) |
| `trace` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `tree` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `turn-budgets` | read-only | 0 | `OK` | no | WIRED (read-only) |
| `turn-cap` | mutating | 2 | `OFFICE_CONFIRM_REQUIRED` | no | WIRED (gated) |
| `unsettled` | read-only | 0 | `OK` | no | WIRED (read-only) |

### Refusal codes seen

| code | meaning | verbs |
|------|---------|-------|
| `OFFICE_CONFIRM_REQUIRED` | the mutation was refused before touching the store (Bare mutation without `--confirm`) | the 39 mutating verbs |
| `OFFICE_NOT_FOUND` | a read-only verb named an entity this fixture has none of (`business-pnl 1`) | `business-pnl` |
| `OFFICE_ADMISSION_REFUSED` | the read-only admission gate applied the caps to the estimate | `admit` |
| `OK` / `preview` / (no state key) | the read-only verb ran | the remaining 40 read-only verbs |

`OFFICE_UNSUPPORTED` is **absent** — its presence for any parser-exposed verb is
the four-site bug (site 3).

## The guard

`tests/test_office_cli_completeness.py` is a pytest file that stays a guard:

- enumerates verbs **from the real parser** (not a hardcoded list), so a new verb
  is covered the moment it exists;
- asserts the count equals the pinned `EXPECTED_VERB_COUNT = 81`;
- invokes **every** verb (real parser + real dispatcher) and fails on any
  traceback or any `OFFICE_UNSUPPORTED`;
- asserts every mutating verb refuses typed without `--confirm`;
- asserts every read-only verb succeeds (or refuses typed on a missing entity);
- re-runs all 83 verbs bare and asserts **no row count changed** — proving a bare
  invocation never writes;
- smoke-tests the real `python -m voicebridge.cli` entrypoint on a representative
  sample, checking exit codes and traceback-free output; and
- asserts this document quotes the real verb count (site 4).

### Falsifiability

The guard was proven to fail for each invocable site (by mutating a scratch copy
of `cli.py`, then restoring it):

| mutation | site | guard output |
|----------|------|--------------|
| removed `elif cmd == "floorplan":` | 3 | `` `office floorplan` returned OFFICE_UNSUPPORTED — the dispatch branch is MISSING (site 3) `` |
| removed `from .office import tower as TWR` | 1 | `` `office floorplan` raised a Python exception — the FOUR-SITE BUG (a missing import, site 1): NameError: name 'TWR' is not defined `` |
| removed the `floorplan` subparser | 2 | `argument office_command: invalid choice: 'floorplan'` + the verb-count assertion |

### Hermeticity

The guard runs entirely against a session tmpdir (`XDG_CONFIG_HOME` /
`XDG_STATE_HOME` / `XDG_DATA_HOME` re-pointed by an autouse conftest fixture and
overridden by the test's own fixture). No network, no provider call, no agent,
and never the operator's real office store. Mutating verbs are only ever invoked
without `--confirm`, so they refuse before any write.
