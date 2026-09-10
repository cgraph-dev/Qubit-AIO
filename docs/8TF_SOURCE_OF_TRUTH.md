# Packet 8T-F — Canonical Source of Truth

Status: **IMPLEMENTATION COMPLETE — pending independent verification (do not start Packet 8U)**

Date: 2026-09-08

This document resolves the architecture ambiguity that caused a supervisor-only
Hermes implementation to be mistaken for a Hermes-based Qubit product.

The detailed execution sequence is in
[8TF_COMPLETE_MIGRATION_PLAN.md](8TF_COMPLETE_MIGRATION_PLAN.md). Agents must
read both documents before changing 8T-F.

## Intended product

The target is one product:

```text
pinned Hermes source foundation
        -> Qubit rebrand
        -> Qubit VoiceBridge integration
        -> reviewed OpenClaw capability integration
        -> one Qubit broker, policy, audit, and emergency-stop authority
```

Qubit VoiceBridge is not an unrelated wrapper around a temporary Hermes
installation. The resulting Qubit foundation must actually contain or
directly import the selected pinned Hermes implementation and must prove the
executed source/module/process identity.

## Canonical names and locations

- Current reference/rollback checkout: `/home/trick/Projects/Repos/qubit-voicebridge`
- Intended canonical product identity: **Qubit Agent**
- Voice subsystem identity: **Qubit VoiceBridge**
- Intended repository/install identity: `qubit-voicebridge`
- Canonical CLI launcher: `qubit` (`voicebridge` is the documented
  compatibility alias: same parser, same backend; shell completions
  `qubit.{bash,zsh,fish}` canonical, `voicebridge.*` alias)
- Internal Python compatibility package: `voicebridge`
- `openclaw-voice-bridge`: legacy compatibility identity only
- Fresh Hermes/OpenClaw clones: isolated sibling or temporary directories;
  never the operator’s existing installations

The current checkout is dirty and must be preserved. The agent must build the
new Hermes-derived foundation in a clean worktree, with a backup and rollback
journal, and migrate it only after independent verification. Do not manually
overwrite the current checkout.

## Non-negotiable acceptance rules

8T-F is rejected if any of these is true:

- a Qubit worker merely imitates Hermes JSON or CLI behavior;
- Hermes exists only in `/tmp`, an external virtual environment, or an
  operator installation while the resulting Qubit tree remains unchanged;
- Hermes identity is proved only by a label, version string, mock, or stub;
- an OpenClaw feature marked `REUSE` or `ADAPT` remains external-only but is
  reported as integrated;
- the full Hermes source/feature inventory is not reconciled;
- Qubit has competing routers, memory stores, schedulers, voice listeners,
  TTS paths, permission systems, audit stores, or emergency stops;
- dashboard/CLI/voice use different backends or different policy decisions;
- unrelated user files, dirty changes, existing installations, credentials,
  cookies, browser profiles, or private data are overwritten or copied.

## Required implementation

The agent must:

1. Pin and audit the exact Hermes and OpenClaw commits, licenses, notices,
   dependencies, tests, models, and optional integrations.
2. Create the new Qubit foundation from the pinned Hermes source in a clean
   worktree.
3. Preserve Hermes’s supported documented capabilities unless a specific
   compatibility, platform, license, or security blocker is recorded.
4. Rebrand user-facing Hermes surfaces as Qubit Agent.
5. Integrate Qubit VoiceBridge as the only microphone, ASR, TTS, Right Ctrl,
   dashboard voice, session, cancellation, and barge-in path.
6. Integrate OpenClaw capabilities marked `REUSE` or `ADAPT` through Qubit
   contracts. Do not copy the entire OpenClaw repository blindly.
7. Keep Qubit broker, grants, confirmations, redaction, audit, resource
   limits, emergency stop, state, and rollback authoritative.
8. Preserve configuration, memory, reports, models, audio settings, browser
   settings, and user data through migration and rollback.
9. Update CLI, dashboard, installer, service, shortcuts, package metadata,
   state paths, documentation, notices, and compatibility aliases.
10. Do not implement Packet 8U browser workspace work in 8T-F.

## Complete Qubit-facing rebrand

The finished product must look and read as one Qubit product, not as Hermes
with a voice wrapper. Remove upstream branding from all user-facing surfaces:

- CLI names, banners, prompts, help, errors, completion scripts, and command
  groups;
- dashboard titles, navigation, route names, page headings, status labels,
  empty states, notifications, diagnostics, and API resource names;
- installer output, service descriptions, desktop shortcuts, package metadata,
  default directories, logs, reports, and support documentation;
- frontend colors, fonts, spacing, icons, component naming, layout hierarchy,
  route organization, onboarding language, and visual design system.

Do not perform a blind string replacement. Reorganize routes and components so
the product has a coherent Qubit information architecture, then add tests for
canonical routes, redirects/legacy aliases, navigation, branding, safe
rendering, accessibility, and visual/resource regressions.

The words “Hermes” and “OpenClaw” may remain only where legally required or
technically necessary: license/copyright notices, third-party attribution,
source/provenance records, migration notes, compatibility diagnostics, and
developer-only upstream references. They must not appear as the apparent
product name, assistant name, active runtime label, user-facing route title,
CLI brand, service brand, or dashboard brand. Do not falsify provenance or
remove required license notices.

## What is and is not manual

Only genuinely operator-dependent work may remain manual: physical hardware,
real desktop sessions, live provider credentials, live accounts, CAPTCHA,
2FA, SMS/email recovery, operator approval of services, clean-machine release
signing, and live canaries.

All source integration, feature inventory, branding, migration logic, tests,
clean-install rehearsal, rollback rehearsal, security checks, and runtime
identity checks are code-side work and must be completed by the agent.

## Gate before 8U

An independent verifier must reproduce the focused, affected, hermetic,
security, resource, clean-install, migration, upgrade, rollback, uninstall,
compileall, dependency, diff, package, systemd, and Graft checks. The report
must list every Hermes and OpenClaw capability as implemented, external-only,
missing, deferred, or rejected. External-only is not integrated.

The final verdict must be `VERIFIED` or an explicitly accepted limitation
that does not hide unfinished code-side work. If the actual Hermes-derived
foundation or required OpenClaw integrations are missing, the verdict is
`REJECTED` and 8U remains blocked.

## Required reading order for agents

1. `AGENTS.md`
2. This document
3. `docs/QUBIT_UNIFIED_AGENT_PLAN.md`
4. `docs/AGENT_PROMPT_PACK.md` Packet 8T-F
5. `docs/OPERATIONS_8T_F.md`
6. `docs/UPSTREAM_REUSE.md`
7. licensing, notices, current diff, and dirty-tree inventory

The implementation prompt in `docs/AGENT_PROMPT_PACK.md` is the executable
task. This file is the architecture and status authority.
