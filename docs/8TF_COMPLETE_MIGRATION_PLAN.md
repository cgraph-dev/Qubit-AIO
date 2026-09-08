# Packet 8T-F Complete Migration Plan

Status: **BLOCKED — full Hermes parity/rebrand is not yet independently verified**

This is the execution plan for the product migration the owner requested. It
is more specific than the ordinary 8T-F packet gate because the target is not
merely “Qubit can call Hermes.” The target is one coherent Qubit product built
from the pinned Hermes foundation, with Qubit VoiceBridge, full supported
Hermes command/interface parity, and the required OpenClaw capabilities
integrated into it.

Reports that prove only a real Hermes subprocess, Hermes chat turns, or a
Qubit supervisor are foundation evidence, not completion. They must not be
used to claim that Qubit is a complete Hermes replacement.

## 1. Final architecture

The required result is:

```text
fresh pinned Hermes source foundation
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

A Qubit-owned provisioned Hermes runtime may be used as an installation
mechanism, but the resulting Qubit product must contain or directly execute
the pinned Hermes implementation as its foundation. A protocol-shaped Qubit
worker, label, stub, or temporary external installation is not integration.

## 2. Canonical locations and names

- Reference checkout: `/home/trick/Projects/Repos/qubit-voicebridge`
- Canonical product: **Qubit Agent**
- Voice subsystem: **Qubit VoiceBridge**
- Canonical repository/install identity: `qubit-voicebridge`
- Internal Python compatibility package: `voicebridge`
- `openclaw-voice-bridge`: legacy compatibility identity only
- Fresh upstream clones: isolated clean worktrees outside existing operator
  installations
- Hermes runtime source: part of the resulting Qubit foundation or its
  verified packaged runtime, never an untracked `/tmp` dependency

## 3. Non-negotiable rules

1. Hermes must be the actual source foundation, not just a supervised backend.
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

### Phase B — Build the Hermes-derived Qubit foundation

- Create a clean migration worktree from the pinned Hermes source.
- Integrate the Qubit control plane into that foundation.
- Ensure the actual Hermes modules/process/source paths execute from the
  resulting Qubit installation.
- Remove reliance on temporary external Hermes paths.
- Preserve supported Hermes behavior unless a documented blocker is approved.
- Add identity receipts containing verified source commit and package hashes.

Gate: a test must fail if the Hermes source is replaced by a Qubit imitation,
wrong commit, operator install, or temporary directory.

### Phase B2 — Migrate the complete supported Hermes interface

Before VoiceBridge or OpenClaw work can be called complete, enumerate the
actual pinned Hermes CLI and user-facing interfaces from its source and help
output. For every command, subcommand, option, interactive workflow, session
operation, skill/task/schedule operation, memory/model/provider operation, and
coding/agent operation, record one of `IMPLEMENTED`, `BLOCKED`, or `REJECTED`.

`IMPLEMENTED` means the operation is callable through a Qubit command or
interface, preserves the required Hermes behavior, produces Qubit receipts,
and passes through the Qubit broker and policy path. `BLOCKED` or `REJECTED`
requires a typed error, user-facing explanation, owner, and regression test.
Silently omitting a command, exposing only `hermes chat`, or telling the user
to invoke the upstream binary directly is a failure.

Add parity tests that compare the pinned Hermes behavior with the Qubit
surface, plus negative tests proving no Qubit command can bypass grants,
confirmation, audit, redaction, resource ceilings, or emergency stop.

### Phase C — Integrate Qubit VoiceBridge

Connect the existing VoiceBridge contracts to the Hermes-derived foundation:

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

- actual Hermes-derived Qubit foundation is proven;
- no production dependence on `/tmp` or an operator Hermes installation;
- Qubit VoiceBridge is integrated as the single voice path;
- complete supported Hermes command/interface parity is implemented or
  explicitly typed as blocked/rejected with no silent omissions;
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
