# Packet 8T-F Complete Migration Plan

Status: **BLOCKED — native Qubit rebrand/runtime separation is not yet independently verified**

This is the execution plan for the product migration the owner requested. It
is more specific than the ordinary 8T-F packet gate because the target is not
merely “Qubit can call an upstream assistant.” The target is one coherent
Qubit-owned product whose runtime, CLI, dashboard, voice path, and OpenClaw
capabilities do not depend on an exposed upstream assistant.

The current owner-directed architecture and document precedence are recorded
in [`QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md`](QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md).
If older wording in this plan says to adapt only a subset of Hermes source,
the approved complete pinned Hermes fork is authoritative.

Reports that prove only an upstream subprocess, upstream chat turns, or a
Qubit supervisor are reference evidence, not completion. They must not be used
to claim that Qubit is a complete native product.

The step-by-step acceptance list is [8TF_COMPLETION_CHECKLIST.md](8TF_COMPLETION_CHECKLIST.md).
The implementing agent must keep it current and may not mark a box without
inspectable evidence.

## 1. Final architecture

The required result is:

```text
audited pinned upstream source
                 │
                 ▼
        Qubit Agent product and identity
                 │
                 ├── Qubit VoiceBridge: mic, ASR, TTS, Right Ctrl, dashboard
                 ├── Qubit broker, grants, confirmations, audit, e-stop
                 ├── required OpenClaw capabilities behind Qubit contracts
                 └── one config, state, memory, session, route, and release plane
```

The unacceptable result is:

```text
Qubit VoiceBridge
    └── launches or supervises a separate Hermes installation
```

The exact pinned Hermes source is the Qubit-owned runtime baseline under the
approved runtime amendment. It must be present in the packaged Qubit tree and
executed in-process by Qubit. Qubit patches, OpenClaw integrations, and policy
overlays remain separately auditable. The pinned source is also used for
differential testing, but an external Hermes installation must never be the
normal runtime. A protocol-shaped worker, renamed banner, or supervised
upstream installation is not the final product.

## 2. Canonical locations and names

- Reference checkout: `/home/trick/Projects/Repos/qubit-voicebridge`
- Canonical product: **Qubit Agent**
- Voice subsystem: **Qubit VoiceBridge**
- Canonical repository/install identity: `qubit-voicebridge`
- Internal Python compatibility package: `voicebridge`
- `openclaw-voice-bridge`: legacy compatibility identity only
- Fresh upstream clones: isolated clean worktrees outside existing operator
  installations
- Qubit runtime source: part of the resulting Qubit foundation or its
  verified packaged runtime, never an untracked `/tmp` or upstream dependency

## 3. Non-negotiable rules

1. The complete pinned Hermes source must be the effective Qubit runtime
   foundation, with Qubit and OpenClaw overlays behind Qubit policy—not a
   supervised external upstream backend.
2. The full Hermes capability inventory must be reconciled. Every capability
   is preserved, adapted, blocked with a written reason, or rejected with a
   written reason.
3. The complete supported Hermes command and interface inventory must be
   reconciled. Every supported user-facing command/workflow must be exposed
   through Qubit or explicitly rejected/blocked with a typed reason; a
   chat-only wrapper is a hard failure.
4. Every OpenClaw capability marked `REUSE` or `ADAPT` must be integrated and
   tested before 8T-F passes. External-only is not integrated.
5. Qubit owns one broker, permission system, session authority, memory policy,
   audit path, emergency stop, configuration plane, and release plane.
6. VoiceBridge owns one microphone/ASR/TTS/Right Ctrl/dashboard voice path.
7. No upstream output can grant authority or widen permissions.
8. No duplicate listener, router, gateway, scheduler, memory store, TTS path,
   audit store, or event loop may operate.
9. Existing user files, dirty work, installations, credentials, cookies,
   browser profiles, and private data must be preserved and never copied into
   the product artifact.
10. Legal attribution and provenance must remain. Upstream names may not remain
   as the apparent product, assistant, service, CLI, dashboard, or route name.
11. Code-side limitations are blockers. Only genuinely operator-dependent
    tests may remain after implementation.

## 4. Work phases

### Phase A — Freeze and inventory

- Read the source-of-truth documents and current dirty tree.
- Record hashes for every unrelated dirty/untracked file.
- Clone the exact Hermes and OpenClaw commits into isolated clean locations.
- Record licenses, notices, dependencies, optional providers, models, tools,
  tests, runtime entry points, and platform assumptions.
- Build complete machine-readable inventories for Hermes, OpenClaw, and Qubit.
- Mark every row `PRESERVE`, `INTEGRATE`, `ADAPT`, `BLOCKED`, or `REJECTED`.

Gate: no unknown source, capability, license, or dependency remains.

### Phase B — Build and activate the Qubit-owned Hermes runtime foundation

- Create or preserve a clean Qubit worktree from the audited source material.
- Vendor the complete exact pinned Hermes source baseline into the Qubit-owned
  source/package tree, retaining licenses and per-file provenance.
- Apply Qubit patches and OpenClaw integrations as separately auditable
  overlays behind Qubit contracts.
- Make the fork the effective backend for CLI, dashboard, and VoiceBridge;
  `hermes_compat` remains a migration fallback only.
- Ensure Qubit modules/process/source paths execute from the Qubit installation.
- Remove reliance on temporary external upstream paths and binaries.
- Preserve supported behavior through differential tests against the pinned
  reference, without exposing the reference as a Qubit runtime dependency.
- Add identity receipts containing verified source commit and package hashes.

Gate: a test must fail if the Qubit runtime is replaced by an upstream binary,
wrong revision, operator install, or temporary directory.

### Phase B2 — Migrate the complete supported Hermes interface

Before VoiceBridge or OpenClaw work can be called complete, enumerate the
actual pinned Hermes CLI and user-facing interfaces from its source and help
output. For every command, subcommand, option, interactive workflow, session
operation, skill/task/schedule operation, memory/model/provider operation, and
coding/agent operation, record one of `IMPLEMENTED`, `BLOCKED`, or `REJECTED`.

`IMPLEMENTED` means the operation is callable through a Qubit command or
interface, preserves the required Hermes behavior, produces Qubit receipts,
and passes through the Qubit broker and policy path. For applicable
user-facing operations, it must also have a real Qubit dashboard page,
control, form, report, or action—not merely a status tile, count, or catalog
entry. CLI, dashboard, and voice must use the same backend operation.
`BLOCKED` or `REJECTED`
requires a typed error, user-facing explanation, owner, and regression test.
Silently omitting a command, exposing only `hermes chat`, or telling the user
to invoke the upstream binary directly is a failure.

Add parity tests that compare the pinned Hermes behavior with the Qubit CLI,
dashboard, and voice surfaces, plus negative tests proving no Qubit command or
dashboard action can bypass grants,
confirmation, audit, redaction, resource ceilings, or emergency stop.

### Phase C — Integrate Qubit VoiceBridge

Connect the existing VoiceBridge contracts to the Qubit-owned runtime:

- microphone and ASR;
- Piper/TTS and playback;
- Right Ctrl push-to-talk;
- dashboard voice;
- one active session;
- streaming, cancellation, and barge-in;
- bounded temporary audio and cleanup;
- typed unavailable/degraded states.

Disable duplicate Hermes voice, wake-word, TTS, and session paths. All voice,
dashboard, CLI, and future browser entry points must resolve the same backend
session and policy decision.

Gate: parity, cancellation, duplicate-listener, restart, and emergency-stop
tests pass through the real integrated foundation.

### Phase D — Integrate OpenClaw capabilities

For every `REUSE` or `ADAPT` row:

- preserve license and provenance;
- use a Qubit capability contract;
- route through broker, scopes, grants, confirmation, redaction, audit,
  resource limits, emergency stop, and rollback;
- add positive and negative tests;
- document dependencies and platform limits;
- expose truthful CLI/dashboard/voice status.

Do not integrate unreviewed ClawHub plugins, cloud memory, hidden listeners,
unrestricted desktop control, or capabilities that violate the Qubit policy.

### Phase D2 — Adapt the seven rejected Hermes features

The current rejected rows are not accepted as the final product boundary.
Implement Qubit-native equivalents for all seven, then update the parity
matrix from `REJECTED` to `ADAPT`/`IMPLEMENTED` only after behavioral proof:

| Hermes feature | Required Qubit-native result |
| --- | --- |
| safety bypass flags | Owner-authorized scoped sessions that retain emergency stop, audit, expiry, and high-impact confirmation; never an unprotected yolo mode |
| proxy | Restricted, allowlisted proxy with target binding, secret isolation, resource limits, and audit |
| pets/skin | Optional Qubit themes/cosmetics isolated from execution policy |
| sync | Opt-in encrypted profile/vault sync with selective data classes and conflict/rollback handling |
| computer-use | Bounded desktop adapter through the Qubit broker, not unrestricted control |
| serve/dashboard/desktop/gui | Qubit-owned dashboard/UI functionality without a second listener or authority |
| hooks | Signed or allowlisted event subscriptions executed through the broker |

Do not copy unsafe upstream bypass behavior. Implement the user-facing
functionality behind Qubit controls, document any unavoidable operator-only
boundary, and add positive, negative, persistence, redaction, resource,
rollback, and emergency-stop tests for each feature.

### Phase E — Complete Qubit rebrand

Rework the product as Qubit rather than applying a superficial label change:

- CLI command groups, banners, help, prompts, errors, completions;
- dashboard routes, navigation, titles, API labels, activity, diagnostics;
- installer, package metadata, services, shortcuts, paths, logs, reports;
- colors, fonts, typography, spacing, icons, tokens, components, layout;
- onboarding, support text, empty states, degraded states, notifications.

Keep Hermes/OpenClaw names only in license, attribution, provenance,
migration, compatibility, and developer-only technical records. Do not delete
legally required text or falsify origin.

Gate: branding scans, route tests, accessibility tests, safe-rendering tests,
and visual/resource checks pass. No upstream name remains in apparent product
identity.

### Phase F — Migration and packaging

Test and document:

- fresh Qubit install;
- existing Qubit migration;
- legacy `openclaw-voice-bridge` compatibility;
- config/state/memory/report/model/audio preservation;
- interrupted and repeated migration;
- upgrade and rollback;
- uninstall without user-data loss;
- read-only, disk-full, traversal, and unsafe symlink failures;
- wheel/sdist contents, clean install, service and shortcut launch.

Gate: exactly one effective config/state tree and one effective service/listener
exist after every transition.

### Phase G — Verification and handoff

Run focused, affected, full hermetic, warnings-as-errors, security,
redaction, resource, persistence, clean-install, migration, upgrade,
rollback, uninstall, compileall, dependency, package, systemd, diff-check,
and Graft checks. Inspect warnings, stderr, receipts, process identity,
resource measurements, files created, cleanup, and failure behavior.

An independent verifier must inspect the merged commit. A report from the
implementing agent is not independent verification.

## 5. Manual-only boundary

The agent must complete all code-side work. Only these may remain for the
operator:

- physical microphone, speaker, and Right Ctrl checks;
- real hardware and desktop-session checks;
- live provider credentials and paid/cloud canaries;
- real accounts, CAPTCHA, 2FA, SMS/email, and recovery challenges;
- operator approval of live services;
- clean-machine release signing, upgrade, rollback, and uninstall;
- live browser/account canaries.

The agent must not classify branding, source integration, feature inventory,
route organization, migration, security, packaging, or tests as manual-only.

## 6. Hard gate before Packet 8U

Packet 8U remains blocked until all are true:

- actual Qubit-owned runtime foundation is proven;
- no production dependence on `/tmp` or an operator upstream installation;
- Qubit VoiceBridge is integrated as the single voice path;
- complete supported Hermes command/interface parity is implemented or
  explicitly typed as blocked/rejected with no silent omissions;
- all seven formerly rejected features have Qubit-native implementations or
  a separately verified, safety-grounded replacement;
- required OpenClaw `REUSE`/`ADAPT` rows are integrated and tested;
- user-facing rebrand and route/component organization are complete;
- migration, packaging, rollback, and uninstall pass;
- corrected source-of-truth documents are included in the scoped commit;
- independent verification accepts the evidence.

If any code-side item is unfinished, the verdict is `REJECTED`, not
`VERIFIED WITH EXPLICIT LIMITATION`.

## 7. Required final report

The agent must report exact commits, files, upstream revisions, source hashes,
feature matrix, rebrand scan, route inventory, test counts, warnings,
failures, resource measurements, security evidence, install/migration/
rollback/uninstall evidence, untouched-file hashes, manual-only checks, and
whether 8U is unblocked.

The report must not call a deferred capability integrated and must not claim
100% completion while a code-side requirement remains unresolved.
