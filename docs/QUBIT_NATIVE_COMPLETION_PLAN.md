# Qubit Native Completion Plan

Status: **BLOCKED — native rebrand and runtime separation are not verified**

Requirements are frozen in
[`QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md`](QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md).
Agents may not edit that contract or weaken this plan/checklist to claim
completion.

The owner-approved runtime and execution authority is
[`QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md`](QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md).
Where older wording in this plan says to adapt only selected Hermes source
units, the complete pinned Hermes fork is required.

The target is one Qubit product built on the Qubit-owned fork of the exact
pinned Hermes source, with the supported Hermes and OpenClaw capabilities
exposed through Qubit CLI, dashboard, and voice. Qubit must look and operate
like a product created by Qubit, not like a renamed wrapper. The approved
runtime amendment supersedes older wording that treated Hermes only as a
reference: the complete pinned Hermes baseline is the runtime foundation.

The dashboard-specific route, workflow, and visual requirements are defined in
[`QUBIT_DASHBOARD_REBUILD_PLAN.md`](QUBIT_DASHBOARD_REBUILD_PLAN.md). A Talk
page alone is never dashboard parity.

## Definition of done

1. `qubit` is the canonical launcher, package, service, dashboard, and
   documented product identity.
2. Normal Qubit execution runs the Qubit-owned fork of the exact pinned Hermes
   source from the Qubit installation; it does not launch or expose an
   external Hermes assistant.
3. The complete pinned Hermes source baseline is vendored with its license and
   provenance. Qubit patches and OpenClaw integrations are separately
   auditable, legally permitted, and tested against the pinned references.
4. Every supported upstream workflow has one real Qubit backend and equivalent
   CLI, authenticated dashboard, and voice access.
5. All surfaces use one Qubit broker, permission, confirmation, session,
   audit, redaction, resource, cancellation, rollback, and e-stop path.
6. The dashboard combines the useful upstream workflows into one Qubit
   information architecture and one Qubit visual system.
7. No code-side capability remains `BLOCKED`, `REJECTED`, `DEFERRED`,
   `STATUS_ONLY`, `CLI_ONLY`, `DASHBOARD_ONLY`, or `VOICE_MISSING`.
8. Only genuine operator tests remain: hardware, live accounts, CAPTCHA/2FA,
   release signing, and clean-machine qualification.

### Important naming rule

“No Hermes remains” means no Hermes product branding in normal Qubit output,
commands, services, runtime labels, paths, dashboard, voice, or operational
source. Legally required attribution, provenance, comparison fixtures, and
documented migration aliases may retain the name and must not be falsified.

## Required architecture

```text
Qubit Agent / Qubit VoiceBridge
  ├── Qubit-owned fork of the pinned Hermes agent runtime and entry point
  ├── qubit CLI and one authenticated dashboard
  ├── one voice/session path
  ├── one broker, permission, audit, redaction, and e-stop authority
  ├── Hermes baseline + OpenClaw integrations behind Qubit contracts
  └── one config/state/data/release plane
```

The following is forbidden:

```text
Qubit → supervisor → external Hermes assistant
```

## Work phases

### 0. Freeze and inventory

- Read `AGENTS.md`, this plan, the checklist, the parity contract, and all
  licensing/provenance records.
- Hash every unrelated dirty or untracked file before editing.
- Pin and inspect the exact upstream revisions, licenses, notices,
  dependencies, command surfaces, dashboard workflows, and runtime paths.
- Audit every production caller of upstream names, binaries, env vars,
  services, paths, imports, routes, and subprocess arguments.

### 1. Build and activate the Qubit-owned Hermes runtime

- Vendor the complete exact pinned Hermes source baseline into the Qubit
  source/package tree, retaining its license and per-file provenance.
- Apply Qubit patches and OpenClaw integrations as separately auditable
  overlays behind Qubit contracts.
- Replace upstream executable, module, service, runtime, and home identity in
  normal product paths with Qubit identity.
- Make the fork the effective backend for the CLI, dashboard, and VoiceBridge;
  the compatibility server is migration-only and cannot remain the final
  authority.
- Remove reliance on `/tmp`, operator upstream installs, upstream home paths,
  or untracked external binaries.
- Record source revision, adapted files, license, digest, entry point, and
  effective Qubit runtime path in an identity receipt.
- Keep differential tests against the pinned upstream as a reference oracle,
  never as the user-facing runtime.

### 2. Complete CLI parity

- Preserve every supported command, subcommand, option, help contract, exit
  status, result, error, persistence, cancellation, and recovery path under
  `qubit`.
- Keep `voicebridge` only as a documented compatibility alias if necessary.
- Do not require users to invoke or install upstream commands.

### 3. Complete dashboard parity and rebrand

- Preserve task, session, project, skill, model, report, and approval workflows.
- Integrate adopted OpenClaw gateway, skills, boards, browser, messaging,
  schedules, runtime, and reports as real Qubit workflows.
- Apply one Qubit visual system: typography, colors, icons, spacing,
  responsive layout, accessibility, motion, onboarding, loading, empty,
  error, degraded, approval, cancel, and recovery states.
- Every visible control must call the same backend as CLI and voice.

### 4. Complete voice parity

- Give every implemented CLI/dashboard operation natural Qubit aliases,
  bounded argument parsing, typed errors, confirmations, cancellation,
  expiry, replay protection, session isolation, and redacted receipts.
- Preserve exactly one mic, ASR, TTS, Right-Ctrl, wake/session, barge-in, and
  cancellation path.

### 5. Complete OpenClaw integration

- Implement every approved OpenClaw capability behind Qubit contracts.
- Use the Qubit broker, scopes, grants, confirmations, audit, redaction,
  resource limits, e-stop, and rollback.
- Implement safe Qubit equivalents for unsafe upstream behavior; do not copy
  unrestricted bypasses, hooks, servers, or credential export.

### 6. Migration, release, and verification

- Migrate config, state, memory, reports, models, audio, projects, and user
  data to one canonical Qubit tree without copying secrets or browser profiles.
- Test fresh install, upgrade, rollback, uninstall, interrupted migration,
  read-only storage, disk-full, unsafe symlink, and legacy compatibility.
- Run focused, affected, full hermetic, live/dashboard/UI, security,
  persistence, resource, clean-install, package, service, and Graft gates.
- Inspect actual warnings, stderr, receipts, persisted files, and failure
  output. Never weaken a test or relabel a code failure as manual-only.

## Hard stop rules

The agent must continue repairing ordinary code, branding, parity, dashboard,
voice, test, documentation, and runtime-identity failures. It may stop only
for an operator-only action or a required owner decision. It must not claim
`100%`, `complete`, `native`, or `1:1` while any code-side gate is open.
