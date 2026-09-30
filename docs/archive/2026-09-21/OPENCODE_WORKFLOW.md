# Qubit OpenCode workflow

These instructions are project guidance for OpenCode. The repository `AGENTS.md`
files remain authoritative for architecture, licensing, Graft usage, and safety.

For active Qubit product work, read these before assigning work:

1. `docs/QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md` and dated owner amendments;
2. `docs/QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md`;
3. `docs/QUBIT_PRODUCT_PARITY_AND_AUTONOMY_PLAN_2026-09-15.md`;
4. `docs/QUBIT_PROVIDER_VOICE_PARITY_PLAN_2026-09-15.md`; and
5. `docs/QUBIT_PLAN_INTEGRITY_AUDIT_2026-09-15.md`.

The implementation-first cadence is also mandatory:
`docs/QUBIT_OWNER_AMENDMENT_2026-09-18_IMPLEMENTATION_FIRST_VERIFICATION.md`.

The vendor baseline, Qubit overlay, and user-visible behavior are three
separate proofs. Do not call an upstream byte/hash match, a rendered route, a
ledger terminal status, or a passing mock proof of product functionality.

## Direct upstream source access

Parity agents must read the exact pinned source, not a web summary or floating
branch. Use the vendored Qubit baseline at `qubit_hermes/` for local runtime
integration and disposable read-only comparison clones for the pinned upstream
revisions recorded in the provenance documents:

- Hermes: `NousResearch/hermes-agent` at the recorded pinned commit;
- OpenClaw: `openclaw/openclaw` at the recorded pinned commit.

Provision comparison trees under a disposable path such as
`/tmp/qubit-upstream/<project>-<commit>/`, verify `git rev-parse HEAD`, source
manifest, license, and clean status, and never edit those trees. Agents may
inspect source/tests/assets directly, then implement Qubit-owned changes in
the repository with an explicit source path, commit, license, patch, and
behavior-test record. Never use an unpinned checkout, an installed external
runtime, or a web page as the final source of truth.

## Context discipline

- Use Graft context before grepping or opening source files.
- Query only the graph area relevant to the current task; never dump the whole graph.
- Load a skill only when its trigger matches the task.
- Keep reports, ledgers, and large inventories in files. Do not paste them into prompts.
- Before compaction, preserve the objective, changed files, failures, blockers, and next action.

## Shared implementation rules

- Use the existing shared backend for CLI, dashboard, and voice behavior.
- Do not create demo routes, duplicate state stores, placeholder success responses, or parallel brokers.
- Qubit-Brain4 is a default profile, not a hard-coded foundation. Use the
  canonical model/provider/voice profile resolver for every new caller.
- “One voice path” means one Qubit session/broker/policy/audit/e-stop path;
  local/API language models and local/API/realtime speech profiles are adapters
  behind it.
- An owner autonomy profile may pre-authorize scoped work, but a model, tool,
  web page, plugin, or message never expands its own authority.
- Preserve unrelated dirty and untracked worktree files.
- Do not modify frozen plans, acceptance criteria, or checklists to make a result appear complete.
- Read licensing and provenance instructions before adapting upstream material.

## Verification rules

- During implementation, use source inspection, Graft, upstream comparison,
  AST/reachability analysis, manifests, and written handoffs first. Do not run
  the full suite, broad Vitest/Playwright sweeps, full differential parity,
  packaging, or independent verification until the integration lead declares
  `IMPLEMENTATION_COMPLETE`.
- Minimal syntax/type or focused checks are allowed only to prevent damage in
  the files being edited; they are not a completion signal and must not become
  a repeated test/fix loop.
- For UI work, use Playwright against the running application and exercise controls, not only page loads.
- For parity work, compare upstream behavior, Qubit backend behavior, CLI, dashboard, and voice.
- For every UI control record route, selector, request, backend operation,
  canonical store mutation/read, visible result, recovery path, and test. A
  200 response, static markup, or screenshot alone is a failed audit method.
- Run the complete verification matrix only after the implementation wave is
  complete. If it fails, run only affected checks while repairing, then rerun
  the final matrix after the repair. Do not start another broad loop during
  implementation.
- Do not commit or push before the independent verifier passes. Any exception
  requires an explicit owner decision and a written reason.
- Report unavailable hardware, credentials, providers, and environmental failures separately from code failures.
