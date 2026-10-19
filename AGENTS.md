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
- The **living docs set is authoritative**: `docs/README.md` (index + review
  checklist), `docs/STATUS.md` (current evidence-backed truth), `docs/PARITY.md`
  (the parity axes, instruments and gap docs), `docs/MIGRATION.md` (the three
  kinds of migration and their shipped commands), `docs/BRAND.md` (zero legacy
  brand), `docs/PLAN.md` (where the plan lives + claim rules),
  `docs/EXECUTION_PLAN.md` (sequencing and dispatch rules), `docs/CHECKLIST.md`
  (the machine-measured tick-list), `docs/DECISIONS.md` (owner rulings in force,
  with provenance), `docs/ARCHITECTURE.md` (layers and paths). Read them before
  any Qubit task and update `STATUS.md` every session. `docs/` was purged once
  (`eafb54e26`, 340 files under `docs/`); never re-create a deleted doc from
  memory, and never cite a doc path you have not `test -f`'d — mark the loss
  instead (`scripts/verify_doc_citations.py` checks this file).
- **Owner directives in force:** everything that works on the local reference
  dashboard `http://127.0.0.1:9119/?profile=default` must work in Qubit and be
  commandable by voice (`docs/PARITY.md`); zero legacy brand on product surfaces,
  with legal attribution the required exception (`docs/BRAND.md`).
- Owner rulings in force — staged gates (`M1`/`M2` as progress markers only,
  `VERIFIED` remains the sole release verdict), the `OPERATOR_ONLY` split, and the
  three rulings — are indexed with their sources in `docs/DECISIONS.md`, which is
  the authority. The owner amendment that first set them out
  (`QUBIT_OWNER_AMENDMENT_2026-09-22_STAGED_GATES_AND_RULINGS.md`) was purged in
  `eafb54e26` and is **not** in the tree; `docs/DECISIONS.md` ruling 5 carries the
  standing text plus the retrieval command. Cite `docs/DECISIONS.md`, not the
  amendment.
- `docs/archive/2026-09-21/` now holds **frozen `*.json` data records only**. The
  archive's own markdown — its `README.md`, plus `AUDIO_SETUP`,
  `STEAL_LIST`, `VOICE_BRIDGE_SYSTEM`, `LICENSING`, `UPSTREAM_REUSE` and the
  2026-09-18 implementation-first amendment — was purged in `eafb54e26` and is
  **not restorable as a path**: do not cite those files, do not hunt for them, and
  do not re-author them. Active tests and generators must never read or write
  `docs/archive/`; never edit a frozen record — changing a requirement needs owner
  sign-off.
- The **pre-reset docs are not in the tree**: the `2026-09-22-pre-reset` archive
  directory was purged in `eafb54e26`. Where the pre-reset contracts still apply
  they are amended by `docs/DECISIONS.md`, which is the authority; never cite a
  pre-reset path as a live requirement. For history only,
  `git show eafb54e26^:docs/archive/2026-09-22-pre-reset/` lists what it held.
- ⚠️ **Open loss — owner decision needed (differential interface contract).**
  The frozen v1 interface consumed by the live `scripts/differential_parity.py`
  (`INTERFACE.md`, and the whole `differential/` directory beside it under
  `docs/`) was purged in `eafb54e26`. The only surviving in-tree pointer is
  `tests/fixtures/differential/README.md`, which names an ephemeral
  `/tmp/qubit-differential/INTERFACE.md` that will not outlive a reboot. Do **not**
  re-author the contract or invent schema fields; recover it read-only with
  `git show eafb54e26^:docs/differential/INTERFACE.md`, and treat any change to it
  as an owner sign-off item.
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
Three docs were once listed here — `AUDIO_SETUP.md`, `STEAL_LIST.md` and
`VOICE_BRIDGE_SYSTEM.md` under `docs/archive/2026-09-21/`. All three were purged
in `eafb54e26` and are **not** in the tree; do not cite them.
- **Audio config — the operative content of `AUDIO_SETUP`** now lives in
  `config/voice.example.yaml`: `output_device` / `output_device_name` stay EMPTY
  (never pin an index) and `speak_web_locally: false` so the browser does not
  double-speak:
  ```bash
  grep -n 'output_device\|speak_web_locally' config/voice.example.yaml
  ```
- **Pipeline and routing lanes:** `docs/VOICE-BRIDGE-AUDIT.md` (live).
- **Steal list** (what to port next from CopilotKit/AirLLM/OpenSandbox) and the
  original full-system pipeline + roadmap: **purged, no live replacement**.
  Recover read-only if you need the historical text —
  `git show eafb54e26^:docs/archive/2026-09-21/STEAL_LIST.md`.

## Licensing and upstream reuse
- `LICENSE` applies only to original Qubit material; it does not relicense
  third-party or upstream-derived code.
- Read `THIRD_PARTY_NOTICES.md` (root) and `docs/BRAND.md` §2 ("What a rename
  must never touch") before copying or adapting external source. The licensing
  docs once cited here — `LICENSING.md` and `UPSTREAM_REUSE.md` under
  `docs/archive/2026-09-21/` — were purged in `eafb54e26` and have **no live
  replacement**; recover them read-only from git history if provenance is in
  doubt, and never re-author them.
- Record the exact upstream repository, commit, source path, license, and
  local changes before implementation. Preserve required copyright/license
  notices and stop with `BLOCKED — LICENSE REVIEW REQUIRED` when provenance or
  license compatibility is uncertain.

## Implementation-first verification cadence

Follow this cadence (the owner amendment that first set it out was purged in
`eafb54e26`; the rules below are restated here and are what you follow).
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

## COMMIT-AS-YOU-GO (owner rule — every agent, no exceptions)

**An agent commits its own work before it finishes its turn.** Work that exists
only in the working tree is one process restart away from being lost, and this
repo has lost work repeatedly that way: a concurrent session, a crashed agent,
or an orphaned delegation takes an uncommitted tree with it and no record of
what it contained survives.

The rule, in order of precedence:

1. **Commit each coherent unit as you finish it**, not once at the end. A commit
   that lands is durable; a plan to commit later is not. Prefer several small
   attributable commits over one large one.
2. **Never end a turn holding uncommitted work you produced.** If the work is
   unfinished, commit it as a `wip:` commit that names what is incomplete. A
   labelled `wip:` is strictly better than a dirty tree, because a dirty tree is
   not revertable, not attributable, and not visible to the next agent.
3. **Commit on behalf of a stopped writer, and attribute it.** When another
   session's uncommitted work blocks you, freeze it first (verify the writer has
   stopped by re-reading file hashes over time, never by assuming) and commit it
   under a message naming the other session. Do not merge over a live writer and
   do not discard its work.
4. **Before dispatching parallel agents, require this of them** in the task
   text, and give each one its own `git worktree` — two agents committing in one
   worktree corrupt each other (`git add -A` in one sweeps the other's files).
5. **The exception is narrow**: the implementation-first cadence above says do
   not commit before independent verification *passes* for a release gate. That
   forbids claiming a milestone as verified — it does not license leaving work
   uncommitted. Commit as `wip:`/ordinary work, and let the gate verdict come
   separately.
