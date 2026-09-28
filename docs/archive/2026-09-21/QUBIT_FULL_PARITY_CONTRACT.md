# Qubit Full Hermes + OpenClaw Parity Contract

## Purpose

This document is the owner’s acceptance contract for the finished Qubit
product. Qubit is intended to replace Hermes and expose the requested OpenClaw
capabilities through one Qubit product, one policy path, and one voice
interface.

This is stronger than a source audit, a capability inventory, a renamed
launcher, a status tile, or a passing unit test that never executes the real
operation.

Current status is tracked by `docs/QUBIT_GAP_LEDGER.md`, generated from its
machine-readable Hermes, OpenClaw, and Qubit sections. That ledger is the
completion authority for open rows; older narrow parity matrices cannot
override it. The owner-approved runtime target and execution order are in
`docs/QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md` and
`docs/QUBIT_HERMES_CUTOVER_MIGRATION.md`.

## Product requirement

For every user-facing capability present in the pinned Hermes and OpenClaw
revisions, Qubit must provide:

1. a real Qubit CLI command with equivalent arguments, help, result, error,
   exit status, persistence, and recovery behavior;
2. a real authenticated dashboard workflow with equivalent navigation,
   controls, forms, reports, loading, empty, success, error, degraded,
   confirmation, cancellation, and recovery states;
3. a natural Qubit voice command with aliases, argument extraction,
   confirmation, cancellation, expiry, replay protection, session isolation,
   redacted receipts, and the same backend operation;
4. one shared Qubit broker, permissions, audit, redaction, resource, and
   emergency-stop path across CLI, dashboard, and voice; and
5. differential tests against the pinned upstream behavior plus security,
   persistence, concurrency, resource, clean-install, upgrade, rollback, and
   failure evidence.

Qubit branding may change names, colors, fonts, icons, copy, spacing, and
visual polish. It must not remove, hide, silently rename, or alter the
behavior of an upstream capability while claiming 1:1 parity. Dashboard
visual improvements must preserve the complete Hermes/OpenClaw information
architecture and usable workflows unless an intentional compatibility mapping
is documented and tested.

## Combined dashboard requirement

The Qubit dashboard is a deliberate combination of the usable Hermes and
OpenClaw dashboard experiences:

- Hermes supplies the functional foundation, command organization, task/session
  workflows, and established controls that Qubit must preserve;
- OpenClaw UI patterns may be adapted for the OpenClaw capabilities actually
  integrated into Qubit, including gateway, sessions, approvals, skills,
  models, boards, browser, messaging, schedules, and runtime state;
- Qubit owns the final information architecture, visual system, navigation,
  typography, color tokens, spacing, icons, responsive behavior, accessibility,
  motion, empty states, error states, and onboarding;
- duplicated dashboards, conflicting routes, separate action stores, and
  separate policy surfaces are forbidden;
- every visible control must call the same Qubit backend used by CLI and voice;
- every adopted OpenClaw UI surface must be tested against its actual backend,
  not copied as static markup or represented by an inventory tile.

The design must feel like one professional Qubit product. It must not look like
Hermes with a few renamed labels, OpenClaw pasted beside Hermes, or a new
unrelated dashboard that dropped upstream workflows. Visual improvements may
change presentation, but not capability coverage or authorization semantics.

## What does not count

These are not implementations:

- a row in a matrix without an executing code path;
- a dashboard inventory tile without a working operation behind it;
- a frontend button that returns fabricated success;
- a CLI alias that only launches an external Hermes/OpenClaw process outside
  Qubit policy;
- a status or doctor probe presented as the capability itself;
- a voice alias that only describes an unavailable feature;
- a unit test that calls a mock while the production caller is missing;
- a `BLOCKED`, `DEFERRED`, `REJECTED`, `CLI_ONLY`, `DASHBOARD_ONLY`,
  `VOICE_MISSING`, or `STATUS_ONLY` row counted as parity.

## Required inventory

The parity inventory must include, at minimum:

- all Hermes executable commands, subcommands, flags, help, interactive
  workflows, sessions, projects, skills, memory, models, providers,
  checkpoints, plugins, schedules, messaging, gateway, TUI, and coding flows;
- all OpenClaw gateway, session, approval, audit, secret-reference, model,
  memory, skill, onboarding, cron, browser, board, plugin, peer, messaging,
  protocol, and runtime flows;
- Qubit-native voice, dashboard, broker, permissions, installation, setup,
  lifecycle, reports, audio, and emergency-control flows.

Every row must contain source revision, license/provenance, implementation
path, CLI path, dashboard path, voice path, tests, resource limits, security
boundaries, owner, and rollback.

## Voice completeness gate

The voice registry must cover the union of all implemented CLI and dashboard
operations. Each entry must have:

- one or more natural aliases;
- deterministic or bounded argument parsing;
- typed result and error mapping;
- source/authority binding;
- confirmation level;
- cancellation and emergency-stop behavior;
- expiry and replay protection;
- redacted audit receipt;
- parity evidence showing the same backend result as CLI and dashboard.

No model, page, tool, repository, or external provider text may create a grant,
confirm an operation, or widen a scope.

## Dashboard completeness gate

The dashboard must expose every implemented Hermes/OpenClaw workflow, not just
status cards. Each operation must be reachable from the Qubit information
architecture and must show truthful state. Visual rebranding and UX
improvements are required, but they must not become a replacement for missing
Hermes/OpenClaw functionality.

## Safety and authorization

Full parity means full authorized capability, not a raw bypass. Existing Qubit
broker, permission, confirmation/pre-authorization, audit, redaction, isolation,
resource, and emergency-stop contracts remain mandatory. High-impact actions
require explicit confirmation unless the owner has already pre-authorized the
exact scope, target, time, budget, and policy in an active autonomy profile
under the 2026-09-15 owner amendment. No direct shell, HTTP, MCP, voice,
dashboard, or upstream-process path may bypass those contracts.

## Packet order

8T-F establishes the Hermes-derived Qubit foundation and parity inventory.
8U–8AH implement the browser, desktop, file, IDE, context, and application
capability families. The final full-parity gate occurs only after the relevant
packets are independently verified.

An agent must not claim “100%,” “complete,” or “1:1” while any code-side row is
blocked, rejected, deferred, voice-missing, dashboard-missing, or only
external-process behavior. Genuine operator-only tests may remain only when
the code path is complete, typed, tested hermetically, and explicitly listed
for manual qualification.

## Final evidence

The final verifier must reproduce:

- Hermes and OpenClaw differential command tests;
- CLI/dashboard/voice parity tests;
- dashboard end-to-end workflow tests;
- security, authorization, redaction, persistence, recovery, concurrency,
  resource, cancellation, replay, and rollback tests;
- clean-install, upgrade, uninstall, and launcher tests;
- compileall, dependency check, diff check, secret scan, import audit, and
  Graft build;
- actual warnings and failure output, not exit codes alone.

The verdict must be `VERIFIED`, `VERIFIED WITH EXPLICIT LIMITATION`, or
`REJECTED`. No later packet may be started from a report that merely says
“parity matrix complete” without this evidence.

## Owner amendment 2026-09-15 — provider, voice, and control-level parity

The complete local upstream inventory includes model/provider selection,
model-health, routing, credentials-reference, streaming, voice/audio, device,
session, autonomy, and provider-failure workflows. Qubit-Brain4 is the default
local profile, not an embedded product requirement. Qubit must expose real,
profile-driven local/API/realtime provider choices and independently selectable
ASR/TTS/device settings through the same CLI/dashboard/voice backend.

For parity, a page is not complete until each visible interactive control,
form, menu, dialog, stream, keyboard path, state transition, persistence, and
error/recovery path has been exercised against the production listener and
canonical store. Source/vendor hashes, route tests, screenshots, or a 200
response are supplementary evidence, not substitutes. See
`QUBIT_PROVIDER_VOICE_PARITY_PLAN_2026-09-15.md` and
`QUBIT_PRODUCT_PARITY_AND_AUTONOMY_PLAN_2026-09-15.md`.
