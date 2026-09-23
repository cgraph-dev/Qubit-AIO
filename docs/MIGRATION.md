# Qubit MIGRATION — port everything from the reference home

**Goal:** take everything the local reference agent has — credentials, config,
skills, cron, plugins, memories, sessions/history, kanban/projects, checkpoints,
channel/gateway state, hooks, pairing, CLI history — and make it live in Qubit's
own home. The reference home (`~/.hermes`) is **read-only** source. Qubit must
never use it as a hidden runtime fallback.

This is **partially done already** and creates legacy-named homes. It must be
completed and then the legacy names retired.

## The reference home (read-only source)

`~/.hermes/` — the working install's data. **Do not touch the reference
dashboard at `http://127.0.0.1:9119/?profile=default`** (a managed service); it
is the behavioral oracle.

## Coverage today (measured 2026-09-22)

| Item | Reference `~/.hermes` | Qubit runtime home | Gap |
|---|---|---|---|
| `state.db` | 236 MB | 88 MB | messages volume short |
| sessions | 122 | 180 (own + partial) | not a clean port |
| skills | 33 dirs / 77 MB | 32 dirs | ~1 missing |
| cron | 7 entries | 3 entries | **4 missing** |
| plugins | 1 | 1 | ok |
| memories | 4 | **0** | **not ported** |
| `config.yaml` | 16,724 B | 549 B (override) | config not mapped |
| `auth.json` | 4,468 B | 1,847 B | credentials partial |

Qubit's home = `~/.local/state/qubit-voicebridge/runtime/` (resolved by
`qubit_runtime.default_home()`).

## Existing tooling (reuse, do not reinvent)

- `voicebridge/qubit_migration.py` — the real user-data migration
  (read-only source, backed up, idempotent, reversible).
- `voicebridge/qubit_runtime.py::migrate_legacy_homes`.
- CLI: `qubit product migrate` (branding) and the separate user-data migration.
- `claw` — legacy-assistant migration tools (confirm-gated).

## Problems to fix

1. **Four homes exist**, violating the "one `QUBIT_HOME`" invariant:
   `hermes/`, `hermes-runtime/`, `qubit/`, `runtime/` under
   `~/.local/state/qubit-voicebridge/`, each with its own `state.db`
   (plus a nested `runtime/runtime`).
2. **Legacy-named dirs/files in Qubit's own state**: `hermes/`,
   `hermes-runtime/`, `hermes-migration/`, `hermes_config_overrides.json`,
   `hermes_persona.json` — a brand leak and an authority confusion.
3. **Incomplete port**: memories (0/4), cron (3/7), config, credentials.
4. **Sessions are not a clean migration** (180 vs 122 rows; mixed provenance).

## Method (repeatable)

1. **Dry-run** the migration; enumerate what will be copied, skipped, or
   conflicting.
2. **Back up** both homes (never destructive).
3. **Port**: credentials (`auth.json`, `.env` refs — never commit secrets),
   config (map, don't blind-copy), skills, cron, plugins, memories, sessions,
   kanban/projects, checkpoints, channel/gateway/pairing state, CLI history.
4. **Verify** by count/row equality against the reference for each item.
5. **Consolidate** to one `QUBIT_HOME`; retire the legacy-named homes and files
   (rename or delete after backup).
6. **Prove** no `~/.hermes` runtime fallback remains (grep + a test that runs
   with `~/.hermes` renamed).

## Invariants

- Reference is read-only; the 9119 dashboard is never modified.
- No secret is committed to git; credentials migrate as files into the Qubit
  home, referenced by name only in docs.
- One home, one `state.db`, one authority.
- No legacy brand in Qubit's own paths at the end.

## Fresh-Hermes oracle

Install a **fresh** reference agent at the **same pinned version** (0.21.3) to
serve as a clean-room behavioral oracle for routes, controls, and CLI parity
(free of local customizations). It carries no data, so it validates *behavior*,
not migration. Pin the version to avoid confounds.
