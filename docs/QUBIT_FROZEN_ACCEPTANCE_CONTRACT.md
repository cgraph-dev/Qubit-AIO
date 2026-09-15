# Qubit Frozen Acceptance Contract

Status: **FROZEN — owner approval required for requirement changes**

This file is the immutable acceptance contract for the Qubit completion work.
It is not a progress log and must not be rewritten to make unfinished work
look complete.

## Product promise

Qubit must be one unified, completely rebranded product containing the complete
supported Hermes and OpenClaw capability set, plus Qubit VoiceBridge and Qubit
native capabilities.

For every supported capability, Qubit must provide:

- a real Qubit backend;
- a Qubit CLI operation;
- an authenticated dashboard page/control/workflow;
- a natural voice command and response;
- shared broker, permissions, confirmation, audit, redaction, resource,
  cancellation, rollback, and emergency-stop behavior;
- persistence, recovery, security, and differential evidence.

“100% parity” means no supported capability is silently missing. Unsafe
upstream behavior must have a Qubit-safe equivalent, not an unrestricted
bypass. Operator-only live tests do not excuse incomplete code.

## UI promise

The original Hermes dashboard is the functional UI source of truth. Preserve
its complete pages, sidebar, navigation, chat/workspace, sessions, files,
models, logs, cron, skills, plugins, MCP, channels, webhooks, pairing,
profiles, config, keys, system controls, tool state, reports, forms, dialogs,
and state/recovery workflows 1:1.

Then add the missing OpenClaw workflows and rebrand the combined product as
Qubit. Qubit may improve colors, fonts, icons, spacing, layout, accessibility,
motion, responsiveness, and copy, but may not remove, hide, collapse, or
replace original functionality with a smaller custom shell.

## Identity promise

The normal product identity is Qubit Agent / Qubit VoiceBridge / `qubit`.
Normal Qubit commands, services, runtime labels, paths, logs, reports,
dashboard, voice, and operational source must not expose Hermes or OpenClaw as
the product or assistant. Required legal attribution, provenance, comparison
fixtures, and migration compatibility records may retain those names.

The normal Qubit runtime must be Qubit-owned. A supervised external upstream
assistant is not completion.

## Frozen-plan rules

Agents may modify:

- production code;
- tests;
- required licensing/provenance records;
- a separate append-only evidence report;
- checklist evidence fields, without changing checklist requirements.

Agents must not:

- rewrite, weaken, delete, reorder, or reinterpret this contract;
- remove a requirement because implementation is difficult;
- turn a code failure into a manual-only limitation;
- change `BLOCKED`, `REJECTED`, `DEFERRED`, or missing rows into success without
  real implementation and tests;
- change the required Hermes UI parity into a smaller custom design;
- claim 100%, complete, native, or 1:1 from a matrix or status tile.

If the implementation conflicts with this contract, the agent must stop and
report the conflict. Requirement changes require explicit owner approval and a
separate dated amendment; agents may not approve their own amendment.

## Final gate

Completion requires the frozen checklist, source/caller audit, CLI/dashboard/
voice parity, original-Hermes UI preservation audit, OpenClaw integration
audit, security and lifecycle tests, clean install, package/service checks,
full test output, and independent verification.

Only physical hardware, live accounts, CAPTCHA/2FA, release signing, and
clean-machine qualification may remain operator-only after code completion.

## Owner amendment 2026-09-11 (append-only, requirements unchanged)

The owner approved vendoring/adapting the pinned Hermes frontend source (MIT,
Copyright (c) 2025 Nous Research) as the functional and structural dashboard
baseline, permitted React/Vite/Node dashboard dependencies, and required per-file
revision/hash/patch records with intact legal notices, OpenClaw additions without
removing Hermes functionality, shared CLI/dashboard/voice backend operations, and
Qubit permission/confirmation/audit/emergency-stop gating of unsafe actions.
Full text: `docs/QUBIT_OWNER_AMENDMENT_2026-09-11.md`.

## Owner amendment 2026-09-11 (runtime — append-only, supersedes native-runtime rule)

The owner approved replacing the Qubit-native runtime with a Qubit-owned
fork/vendor of the exact pinned Hermes source, executed from this source tree
(no external Hermes installation supervised or required), then adding OpenClaw
capabilities and Qubit VoiceBridge on top of that foundation, preserving Qubit
safety controls and all licensing/provenance records. This amends only the
`Identity promise` sentence that forbade a vendored upstream runtime; every
other requirement above remains in force. Full verbatim text:
`docs/QUBIT_OWNER_AMENDMENT_2026-09-11_HERMES_RUNTIME.md`.

## Owner amendment 2026-09-15 (provider, voice, autonomy, and plan integrity)

The owner approved the configurable-provider, Qubit-Brain4 lifecycle, advanced
local-model research, and scoped-autonomy requirements in
`docs/QUBIT_OWNER_AMENDMENT_2026-09-15_PROVIDER_AND_VOICE_PLUGGABILITY.md`.
The current master execution/correction plan is
`docs/QUBIT_PRODUCT_PARITY_AND_AUTONOMY_PLAN_2026-09-15.md`; the plan-integrity
findings are recorded in `docs/QUBIT_PLAN_INTEGRITY_AUDIT_2026-09-15.md`.

This amendment clarifies that a byte-verified vendor baseline and a fully
Qubit-branded product are complementary requirements: byte identity applies to
unchanged vendor files and provenance, while Qubit overlays are reviewed by
patch manifest and behavioral parity tests. It also clarifies that one voice
path means one session/broker/policy authority, not one permanently embedded
model or voice provider. All original parity, safety, rebrand, and final-gate
requirements remain in force.
