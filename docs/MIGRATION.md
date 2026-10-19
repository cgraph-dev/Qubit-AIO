# Qubit MIGRATION — the three different things called "migration"

**Status:** method + pointer index. **Not a completion claim.** This file names the shipped
surface for each kind of migration and does not assert that any of them has been fully exercised
against live state.

The previous `docs/MIGRATION.md` was deleted in `eafb54e26`. It described a 2026-09-22 coverage
table for importing the reference home, with counts measured then. Those counts are not restated
here: they were true of a state that no longer exists.

## 1. Code migration — upstream files into `qubit_runtime/`

The rule is a frozen pin, not "upstream latest": match
`43faacfb8de7b29548547a7fbdedad0cc6e7391c` literally and 1:1, then prune by owner decision.
Exclusions are declared, not skipped mid-port.

- Rule + pin + declared exclusions: `docs/FROZEN_TARGET.md`
- The per-chunk method (follow it verbatim; worked example in `REPORT-port-5.md`):
  `PORT-METHOD.md` (repo root)
- Remaining work, machine-measured: `docs/FROZEN_TARGET_WORKLIST.md`
- Chunk sequencing: `docs/PORT-PLAN.md` — **historical**: it declares status `RUNNING` against the
  superseded pin `517b5e10febd` (2026-10-08). Read it for the chunking approach, not for current
  state.

## 2. On-disk state migration — legacy product names to canonical paths

This product's own legacy directory names are declared in code, not prose:

```bash
grep -n 'PRODUCT_NAME = \\|LEGACY_NAME = \\|LEGACY_NAME_7D = ' voicebridge/config.py
# 35: PRODUCT_NAME = "qubit-voicebridge"
# 37: LEGACY_NAME = "openclaw-voice-bridge"
# 38: LEGACY_NAME_7D = "qubit-voice-agent"
```

The shipped surface (dry-run by default; `--apply` performs it):

```bash
qubit product migrate --help              # reversible legacy -> canonical, backed up, resume-able
qubit product migrate-rollback --help     # reverse a branding migration
```

```bash
grep -n 'add_parser("migrate' voicebridge/cli.py          # lines 741, 749
grep -n '^def migrate' voicebridge/product.py             # 1463 migrate, 1716 migrate_rollback
```

Behaviour, from the implementation docstrings: every legacy config/state/data tree is backed up
first (credential files are never copied into the backup; the live move preserves all bytes), a
compat alias is left behind, and a partial restore fails closed with the exact paths reported.

A hermetic rehearsal drives migrate → upgrade → rollback → uninstall against a throwaway
`/tmp` tree and `HOME`:

```bash
bash scripts/migration_rehearsal.sh
```

## 3. Importing a pre-existing agent home into Qubit's home

The runtime's canonical home directory and its legacy counterpart are declared here:

```bash
grep -n '_QUBIT_HOME_DIR = \\|_LEGACY_HOME_DIR = ' qubit_runtime/qubit_constants.py
# 54: _QUBIT_HOME_DIR = ".qubit"
# 55: _LEGACY_HOME_DIR = ".hermes"
```

Importing an existing setup (memories, skills, persona) is handled by the optional bundled skill
rather than by a core command:

```bash
ls qubit_runtime/optional-skills/migration/qubit-migration/SKILL.md
```

**Not verified here:** that this import is complete, lossless, or exercised against the local
reference home. This document does not claim it. If a task depends on it, run the skill against a
copy and record the evidence in `docs/STATUS.md` — do not infer completeness from the command
existing.
