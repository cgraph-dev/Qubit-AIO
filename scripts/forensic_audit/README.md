# Forensic audit harness

Re-runnable, deterministic Qubit-vs-upstream Hermes audit. It replaces the
one-shot 2026-09-25 forensic report with a **living findings ledger** so every
re-run produces a diff (`NEW` / `OPEN` / `FIXED` / `REGRESSED`) instead of
starting from zero.

See also: `TIER1_AGENT_PROMPTS.md` (the on-demand deep dive),
`docs/generated/audit/FORENSIC_BASELINE_2026-09-25.md` (the frozen one-shot
report this seeded from), and the generated `QubitVSupstream.md` at the repo
root.

## Quick start

```bash
# CI / pre-commit gate: fail on NEW or REGRESSED HIGH|CRITICAL
.venv/bin/python scripts/forensic_audit/run_audit.py --check

# fast subset (skips generator/import subprocess checks)
.venv/bin/python scripts/forensic_audit/run_audit.py --check --fast

# update the ledger and regenerate QubitVSupstream.md
.venv/bin/python scripts/forensic_audit/run_audit.py

# list checks
.venv/bin/python scripts/forensic_audit/run_audit.py --list-checks
```

Exit status: `0` clean, `1` gating (new/regressed HIGH|CRITICAL), `2` other.
`--check` never writes. The default mode writes only
`docs/generated/audit/ledger.json` and `QubitVSupstream.md`.

## Model

* **Tier 0** (`checks.py`) — deterministic, no LLM, no network, read-only. Each
  check encodes one recurring failure class. Findings carry `severity`,
  `classification` (the forensic enum), and `cause_type`.
* **Tier 1** (agent deep dive) — on demand, richer. Ingest with
  `ingest_agent_findings.py` so Tier-1 and Tier-0 merge into one ledger.
* **Ledger** (`docs/generated/audit/ledger.json`) — the source of truth, keyed
  by a stable `fingerprint` = `sha256(check|path|slug)` (line numbers excluded).
* **Triage overlay** (`docs/generated/audit/triage.json`) — owner decisions that
  survive runs:

  ```json
  { "triage": { "<fingerprint>": { "status": "WONTFIX", "note": "third-party provenance" } } }
  ```

  Terminal statuses: `ACCEPTED`, `WONTFIX`, `OPERATOR`.

## Lifecycle

| Was | Now | Status |
|---|---|---|
| absent | present | `NEW` |
| `OPEN`/`FIXING` | present | `OPEN` (or `FIXING` preserved) |
| `FIXED` | present | `REGRESSED` |
| any non-triage | absent | `FIXED` |
| `ACCEPTED`/`WONTFIX`/`OPERATOR` | either | preserved |

## Adding a check

Add a function `fn(repo, ctx) -> list[Finding]` in `checks.py` using
`schema.finding(...)`, then register it in `CHECKS` with a unique `id`, `tier`,
`slow`, and `description`. Keep it read-only and deterministic (no wall-clock,
no network). Anything that shells out or imports the vendored runtime must set
`HOME`/`QUBIT_HOME` to a throwaway dir and be marked `slow=True`.

## Constraints

* **Read-only** against the product tree; only `--update` writes under
  `docs/generated/audit/` plus `QubitVSupstream.md`.
* Never reads or writes `~/.hermes`.
* No milestone claim: a finding is a pointer to a defect, never parity.
* Deterministic: two runs over an unchanged tree produce identical findings.
