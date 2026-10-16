# Rebrand Sweep Plan — env/attr symbols HERMES_* → QUBIT_*

Status: **PLANNED — not started.** Owner ruling 2026-10-16 authorizes the direction;
this document defines the shape so the work is not done as a third half-rebrand.

## Why this document exists

The owner directive (AGENTS.md, `docs/BRAND.md`, `docs/IDENTITY.md`) is **zero legacy
brand on product surfaces**. The tree does not meet it, and past attempts made it worse:
commit `da19a480a` is literally *"fix wave (8 agents) — ... env **half-rebrands**"*.
The result is a split brain, not a clean target.

Measured on `pre-sync-worktree-snapshot` at `3301809e6` (all counts production code,
`--include=*.py`, tests excluded):

| Measure | Count |
|---|---|
| Distinct `HERMES_*` symbols in production code | **630** |
| Distinct `QUBIT_*` symbols in production code | 231 |
| Symbols carrying **BOTH** spellings already | **152** |
| Files under `qubit_runtime/website/docs/` naming a `HERMES_*` symbol | **135** |
| `HERMES_*` names in `OPTIONAL_ENV_VARS` (the `.env` contract) | 42 |
| Scripts hardcoding the frozen pin `69948c0057` | 8 |

The 152 dual-spelled symbols are the smoking gun: the rebrand was started, repeatedly,
and never finished. Any new attempt that renames a *subset* will create a fourth
inconsistency. **This plan exists to prevent that.**

## What the last attempt got wrong (do not repeat)

The parked branch `parked/catchup-1-hermes-direction` (tag points at `d054699e4`)
did the **inverse**: it rewrote ~140 symbols `QUBIT_* → HERMES_*` in *tests*, reasoning
"match what the product code reads". Locally that is a valid inference — tests should
assert what the product does. Globally it is wrong: it treats **drift as the spec**. The
product code is the thing that is behind, not the tests.

**Rule for this sweep: where a name differs, the `QUBIT_*` spelling is the target and the
`HERMES_*` spelling is legacy.** Tests may be either; code is the thing being migrated.

## The three hazards (each has already caused a real bug)

### 1. Installed services die on a naive rename
`QUBIT_SUPERVISED_CHILD` is written by generated systemd units / launchd plists / Windows
scheduled tasks that are **already installed on machines**. Renaming only the writer makes
an installed unit invisible to the new reader. This is exactly the bug that produced the
`venv_sync.py:308` split (the one reader that had migrated).

**Mitigation — one pattern, applied everywhere:** *single write, dual read.*
Write the canonical `QUBIT_*` name; read both spellings. The proven implementation is in
`qubit_runtime/gateway/restart.py` (commit `080514a2d`, tests in
`tests/gateway/test_supervised_marker_dual_read.py`, 22 cases, 7 proven red without the
change). Generalise that shape — do not invent a new one.

### 2. The frozen pin is an immutable provenance anchor
`69948c0057...` is hardcoded in `scripts/audit_capability_gaps.py`, `scripts/sync_upstream.py`,
`scripts/hermes_vendor_manifest.py`, `scripts/merge_v0215/freeze.py` (+3). It identifies an
**upstream** commit and must **never** be rewritten by a local rebrand. Renaming it breaks
the audit trail and the sync mechanism.

**Mitigation:** an explicit deny-list in the sweep tool. Files under `scripts/` that name a
pin, `FROZEN_REV`, or a `*.json` manifest are read-only to the sweep.

### 3. `HERMES_*` in-process names are not all brand — some are protocol
`HERMES_KANBAN_TASK` (247 refs), `HERMES_SESSION_KEY` (125), `HERMES_RUNTIME_DIR` (143),
`HERMES_INTERACTIVE` (157). Several are written by a parent process and read by a child, or
cross a socket. A half-rename makes two processes disagree silently — no error, just a
feature that stops firing.

**Mitigation:** classify before renaming, never blanket-replace. Per symbol:
`write-site` / `read-site` / `crosses-process-boundary?`. Cross-boundary symbols get the
dual-read treatment even when they look internal.

## Phases

Ordered so each phase is independently landable and leaves the tree **consistent**, never
half-done. Each phase is a stack of small commits; a phase is not started until the
previous one is verified green.

**Phase 0 — tooling (no renames).** Build `scripts/rebrand_sweep.py`:
enumerate symbols; classify each (write/read/boundary/doc-only/pin-adjacent); emit a
manifest `rebrand_manifest.json`; support `--check` (CI gate: fails if a symbol has both
spellings in *code* without a declared dual-read). No symbol is touched until the manifest
is reviewed. **Exit criterion:** manifest exists, `--check` runs, zero files modified.

**Phase 1 — dual-read everywhere (no writes moved yet).** For every symbol the manifest
marks cross-boundary, make readers accept both spellings. Writers keep their current name.
This phase is **behaviour-preserving by construction** and cannot break an install.
**Exit criterion:** the full suite shows no new failures vs the pre-phase baseline;
a falsification test exists per symbol group.

**Phase 2 — writers move to `QUBIT_*`.** Only now do generated units/plists/tasks change,
and Phase 1 guarantees old readers still work. Regenerate-on-refresh converges existing
installs. **Exit criterion:** a unit generated by the new code is recognised by the *old*
reader, and an installed old unit by the *new* reader (both directions proven).

**Phase 3 — brand the surfaces.** The 135 doc files, `OPTIONAL_ENV_VARS` (42), user-facing
strings, and `website/docs/`. Docs are safe to change last and are the largest visible win.

**Phase 4 — retire the legacy aliases.** Only after every install path has converged
(a release boundary). Removes the dual-read code.

## What this plan explicitly does NOT do

- It does **not** promise a calendar date. 630 symbols + 135 docs across a 1,000-file core
  is a multi-session mechanical job, and its risk is concentrated in the boundary symbols.
- It does **not** rename anything under `scripts/` that names a pin or manifest.
- It does **not** treat "tests pass" as done — a boundary symbol renamed in only one
  direction passes its own test while silently breaking a sibling process.

## Salvage note

Agent lane `catchup-1` (parked) contains one genuinely useful fix unrelated to direction:
`launchd_install` is called with `**kw` by production but a test patches it as
`lambda force, start_now:`. Carry that signature fix into Phase 1's test work.

## Provenance

- Lane 6 implementation: `080514a2d` (code), `3301809e6` (report), report at
  `REPORT-supervised-marker.md`.
- Parked inverse branch: `parked/catchup-1-hermes-direction`.
- Prior half-rebrand incident: `da19a480a`.
- Owner ruling: 2026-10-16, full direction (code becomes `QUBIT_*`, tests follow).
