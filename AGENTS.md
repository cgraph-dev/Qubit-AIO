<!-- graft:start -->
## Graft — repo context graph

This repo is indexed in `graft/`: small linked markdown nodes that explain each
system and carry exact file:line spans, kept in sync with the code through git.

For ANY task here — understanding how something works, finding where code lives,
or scoping a change — get context from the graph before grepping or opening
source files. Re-ask freely (it's cheap) and reuse literal identifiers you
already have (symbol, error string, file name) as the query. New to this repo?
Run `graft map` first — a token-budgeted orientation (dir clusters, hubs,
hotspots), no LLM, no key.

- Run `graft ask "<your question>" --source` → ranked nodes with the relevant
  code spans inlined (each hit's ≤8-line crux by default; `--full` for whole
  definitions when the crux isn't enough). Match the tool to the task shape:
  for understanding or editing, the top node IS the answer — cite its
  `covers:` file:line spans and edit straight from `--source`. For
  exhaustive tasks ("every occurrence / every caller of this pattern"), ranked
  results are top-N, not complete — run `graft grep "<literal>"` instead
  (exhaustive over indexed files, grouped by enclosing symbol), falling back
  to raw `grep -rn` only for unindexed files.
- `graft skeleton <file>` → every definition's signature + span, ~10× cheaper
  than reading the file; use it to skim an API surface.
- `graft callers <symbol>` gives precomputed, exact edges — who calls this.
  Add `--direction out` for what it calls, or `--depth N` to walk
  transitively for the full blast radius. For structural questions, skip
  ranking and use this directly.
- Or browse: `graft/INDEX.md` lists every node; follow the links.
- Monorepos and folders of multiple repos rank fairly across sub-projects —
  hits carry `[scope/]` labels naming which one they're from. Narrow with
  `graft ask "<task>" --in <scope>/` once you know where you're working.

If a returned span is truncated ("+N more lines"), open the file at that exact
range before finalizing. Only open source files when a node genuinely lacks a
needed detail, and then at the exact file:line the node points to — never
re-read whole files.

After big code changes, refresh the graph with `graft build` (deterministic,
no API key, $0).
<!-- graft:end -->

## Documentation authority (read first)
- **Identity is canonical in `docs/IDENTITY.md`** (read it before any task that
  states what Qubit *is*): Qubit is a personal AI agent — an all-in-one assistant
  (coding agent, caretaker, teacher, personal bot, business organizer) that
  **absorbs other agents' capabilities into itself and becomes more than any one
  of them**. Do not describe Qubit as "a fork"; upstream lineage is provenance,
  named only where load-bearing, per `docs/BRAND.md`. Tagline: *"Nobody knows
  what it will become."*
- The **fresh docs set is authoritative and living**: `docs/README.md` (index +
  review checklist), `docs/STATUS.md` (current evidence-backed truth),
  `docs/PARITY.md` (the parity target + method), `docs/MIGRATION.md` (port from
  the reference home), `docs/BRAND.md` (zero legacy brand),   `docs/PLAN.md` (milestones/critical path), `docs/EXECUTION_PLAN.md` (sequenced
  workstreams W0–W9), `docs/CHECKLIST.md` (the master tick-list),
  `docs/DECISIONS.md` (owner rulings in force),
  `docs/ARCHITECTURE.md`. Read them before any Qubit task and update
  `STATUS.md` every session.
- **Owner directives in force:** everything that works on the local reference
  dashboard `http://127.0.0.1:9119/?profile=default` must work in Qubit and be
  commandable by voice (`docs/PARITY.md`); zero legacy brand on product surfaces,
  with legal attribution the required exception (`docs/BRAND.md`).
- Owner rulings in force — staged gates (`M1`/`M2` as progress markers only,
  `VERIFIED` remains the sole release verdict), the `OPERATOR_ONLY` split, and the
  three rulings — are in `docs/DECISIONS.md` and
  `docs/QUBIT_OWNER_AMENDMENT_2026-09-22_STAGED_GATES_AND_RULINGS.md`.
- Frozen contracts and owner amendments are authoritative by content and are
  preserved under `docs/archive/2026-09-21/` (see its `README.md`). Never edit a
  frozen contract; changing a requirement needs owner sign-off. Do not edit
  `docs/differential/INTERFACE.md`.
- The **pre-reset docs** (the 2026-09-21 authoritative contracts and the former
  active brief) are preserved under `docs/archive/2026-09-22-pre-reset/`. They
  remain the definition of done, **amended by `docs/DECISIONS.md`**, and must not
  override it.
- Status comes from freshly regenerated ledgers/manifests (`QUBIT_GAP_LEDGER.*`,
  `QUBIT_*_MANIFEST.json`, `QUBIT_*_INVENTORY.json`), not prose. Re-run the
  generators; do not trust any prior completion claim without verifying against
  the worktree.
- Never claim VERIFIED, M1, M2, CODE_READY, COMPLETE, 100%, or 1:1 without the
  behavioral evidence required by the archived release gate and `docs/PLAN.md`.
- **A typed refusal is never a working capability.** Preserve the
  voice-actionability, full-parity, and deep-rebrand requirements.
- Graft (v0.12.0) indexes code only; markdown docs are NOT in the graph. Doc
  authority is carried by this file and the `opencode.json` `instructions` list.

## Voice Bridge docs (read before editing audio/voice)
These were preserved under `docs/archive/2026-09-21/`:
- `docs/archive/2026-09-21/AUDIO_SETUP.md` — the WORKING HyperX audio config
  (mic = mono USB, output = HyperX digital; keep output_device EMPTY;
  speak_web_locally=false).
- `docs/archive/2026-09-21/STEAL_LIST.md` — what to implement next from
  CopilotKit/AirLLM/OpenSandbox (smarter 4B model tier, live streaming, tool
  schema, etc).
- `docs/archive/2026-09-21/VOICE_BRIDGE_SYSTEM.md` — full pipeline + roadmap.

## Licensing and upstream reuse
- `LICENSE` applies only to original Qubit material; it does not relicense
  third-party or upstream-derived code.
- Read `docs/archive/2026-09-21/LICENSING.md`, `THIRD_PARTY_NOTICES.md`, and
  `docs/archive/2026-09-21/UPSTREAM_REUSE.md` before copying or adapting
  external source.
- Record the exact upstream repository, commit, source path, license, and
  local changes before implementation. Preserve required copyright/license
  notices and stop with `BLOCKED — LICENSE REVIEW REQUIRED` when provenance or
  license compatibility is uncertain.

## Implementation-first verification cadence

Follow `docs/archive/2026-09-21/QUBIT_OWNER_AMENDMENT_2026-09-18_IMPLEMENTATION_FIRST_VERIFICATION.md`.
Finish code-side implementation and inventory work before running expensive
verification. Until the integration lead declares `IMPLEMENTATION_COMPLETE`,
do not run the full pytest suite, broad Vitest/Playwright sweeps, complete
differential parity, packaging/release waves, or independent verification.
Use Graft, upstream comparison, AST/reachability analysis, manifests, and
written handoffs during implementation. Minimal syntax/type or focused checks
are allowed only to prevent damage to files being edited; they are not proof of
completion and must not become a repeated test/fix loop. After implementation
is frozen, run the final matrix once. If it fails, repair the affected area
with focused checks and rerun the final matrix only after the repair. Do not
commit or push before independent verification passes.
