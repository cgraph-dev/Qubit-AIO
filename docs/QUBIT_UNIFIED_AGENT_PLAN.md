# Qubit Unified Agent Plan

## 0. Identity and scope

This is the plan for turning the current Voice Bridge into a unified product
called **Qubit**.

It is separate from:

- `UNIFIED_AGENT_PLAN.md`, which covers the current local Voice Bridge work;
- `SLM_FROM_SCRATCH_PLAN.md`, which covers the future Qubit learning model.

This plan combines the useful capabilities of OpenClaw, Hermes, Atomic Agent,
and Voice Bridge behind one Qubit interface. Packet 8T-F audits pinned
upstream sources and establishes the complete pinned Hermes source as the
Qubit-owned runtime foundation; it is not satisfied by supervising an external
upstream installation. OpenClaw is integrated as reviewed Qubit overlays and
capability implementations on that foundation.
OpenClaw capabilities are integrated through the feature matrix and the full
parity contract in `docs/QUBIT_FULL_PARITY_CONTRACT.md`, with provenance and
license retained. “Selective” means selecting the smallest justified source
units, not selecting only convenient features: every requested Hermes and
OpenClaw capability must eventually have a real Qubit implementation, a
tested Qubit compatibility boundary, or a clearly identified operator-only
qualification gate. Copying an entire second agent repository without
ownership boundaries is not acceptable.

The immutable owner contract is
[`QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md`](QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md).
Implementation agents may update code and evidence, but may not change the
requirements in that contract to make progress appear complete.

### Native Qubit completion authority

The owner’s stricter completion target is defined in
[`QUBIT_NATIVE_COMPLETION_PLAN.md`](QUBIT_NATIVE_COMPLETION_PLAN.md) and
[`QUBIT_NATIVE_COMPLETION_CHECKLIST.md`](QUBIT_NATIVE_COMPLETION_CHECKLIST.md).
The approved runtime amendment and
[`QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md`](QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md)
supersede wording that treats Qubit as a supervisor of an upstream assistant.
Until their gates pass, describe the product as an integration in progress,
not as a complete Qubit replacement.

### Packet 8S identity decision

The release identity migration uses **Qubit VoiceBridge** with the canonical
installed and repository identity `qubit-voicebridge`. The existing
`openclaw-voice-bridge` name remains a documented legacy compatibility
identity during migration. The internal Python import package remains
`voicebridge`; this product rename must not rename that import package.

### The goal

Qubit should provide one personal-agent experience that can:

- hear and speak through the PC;
- understand text, audio, images, and video through specialist adapters;
- answer with a local model or an approved stronger model;
- use approved files, repositories, browser sessions, inboxes, and web sources;
- run coding and long-running tasks;
- use memory, skills, schedules, reports, and background jobs;
- ask for permission before sensitive or destructive work;
- learn approved preferences and procedures without silently changing policy;
- work locally first and remain portable to another computer.

“Everything” means every capability that Qubit has implemented, tested, and
that the user has permitted. It does not mean unrestricted control of every
website or program.

### Required Hermes rebrand/parity decision

The owner’s target is not a Qubit wrapper that merely starts Hermes chat. The
8T-F foundation is incomplete until the complete pinned Hermes source is the
effective Qubit runtime and the supported Hermes user experience has
been migrated into Qubit: every supported Hermes CLI command, command group,
interactive workflow, configuration surface, session workflow,
skill/task/schedule workflow, model/provider workflow, and coding/agent
workflow must be either implemented behind a Qubit interface or explicitly
blocked/rejected in the feature matrix with a technical, licensing, platform,
or safety reason and a typed Qubit error.

An external Hermes binary, a Qubit-only `chat` supervisor, a renamed banner,
or a partial command subset is not full parity and cannot unblock the next
packet. Direct upstream execution may be used only as an internal reference
or verification oracle; it is not a supported user path because it bypasses
Qubit policy. Legal attribution and technical provenance remain required, but
they are not product branding.

### Owner full-parity acceptance contract

The owner’s final product requirement is **Qubit as a complete, voice-actionable
Hermes + OpenClaw replacement**. This is stronger than the interim 8T-F
foundation gate. The final release is not 1:1 merely because a command appears
in a matrix, a capability tile reports `IMPLEMENTED`, or an upstream process
can be launched.

For every pinned Hermes and OpenClaw user-facing capability, Qubit must prove:

1. equivalent CLI command, arguments, help, result, error, exit status, and
   persistence behavior;
2. equivalent authenticated dashboard workflow, including navigation, forms,
   reports, loading, empty, error, degraded, confirmation, cancellation, and
   recovery states;
3. a natural Qubit voice command that reaches the same backend operation,
   including argument extraction, confirmation, cancellation, expiry, replay
   rejection, redacted receipts, and session isolation;
4. the same Qubit broker, permission, audit, resource, redaction, and
   emergency-stop path across all three surfaces; and
5. differential, security, persistence, resource, clean-install, upgrade,
   rollback, and failure evidence.

Rows marked `BLOCKED`, `DEFERRED`, `REJECTED`, `CLI_ONLY`,
`DASHBOARD_ONLY`, `VOICE_MISSING`, or `STATUS_ONLY` are not parity. They must
be resolved before the final 1:1 release gate, or be explicitly classified as
genuine operator-only tests that are code-complete and manually qualified.

The current packet order remains valid: 8T-F establishes the Qubit-owned
foundation with upstream differential evidence, while 8U–8AH add
browser/desktop/files/IDE and other whole-computer
capabilities, and the final parity gate runs only after those packets. No agent
may claim that 8T-F alone is final Hermes + OpenClaw parity while those later
capability packets remain incomplete.

### Combined Hermes/OpenClaw dashboard rule

The finished dashboard must combine the functional Hermes dashboard foundation
with the adopted OpenClaw dashboard workflows. Hermes supplies the preserved
command/session/task organization; OpenClaw contributes UI patterns only for
capabilities that are genuinely integrated into Qubit. Qubit owns one final
information architecture, one navigation system, one design system, and one
backend operation path.

The owner-approved dashboard baseline is the exact pinned Hermes React/Vite
frontend. Agents must vendor that source first, preserve unchanged files by
hash, and record every Qubit integration, OpenClaw, safety, accessibility, and
branding patch. The existing Python/static-JS pages are migration fallbacks,
not the completed dashboard and must not be used to claim parity.

Agents must not ship two dashboards, paste OpenClaw pages beside Hermes pages,
or replace the Hermes workflow with a smaller custom shell. They must preserve
all functional controls while applying Qubit typography, color, spacing,
icons, responsive layout, accessibility, motion, onboarding, loading, empty,
error, degraded, approval, and recovery design. Every visible control must
call the same backend operation used by Qubit CLI and voice and must have
end-to-end evidence.

### Final parity meaning

The final Qubit release is complete only when the pinned Hermes and OpenClaw
capability inventories are behaviorally compared and every supported operation
is implemented through the same Qubit backend in all applicable surfaces:

- `qubit` CLI with equivalent arguments, help, output, errors, exit status,
  persistence, cancellation, and recovery;
- the vendored Hermes-baseline dashboard with a real page/control/workflow;
- Qubit VoiceBridge with natural commands, argument extraction, clarification,
  typed responses, confirmation, cancellation, expiry/replay rejection,
  redacted receipts, and session isolation.

A voice explanation, capability count, dashboard tile, CLI-only path,
dashboard-only path, dead control, or external upstream process is not an
implementation. Unsafe or operator-dependent behaviors require a tested Qubit
safe equivalent and a typed manual state; only the live operator action may
remain manual. Rebranding is applied after functional parity as a documented
overlay, never as a substitute for it.

## 1. Source strategy

### Upstream projects

OpenClaw and Hermes already contain substantial agent infrastructure. Hermes
provides an agent loop, memory, skills, scheduling, delegation, terminal
backends, and coding workflows. OpenClaw provides a personal-agent ecosystem,
gateway, integrations, skills, and machine-facing workflows. Atomic Agent adds
useful local-first control-loop patterns: one structured tool-call decision per
step, stable-prefix/bounded-tail prompts for KV-cache reuse, parallel
read-only calls, progressive skill loading, compact browser state, explicit
task/trace failure categories, and a no-progress guard.

Earlier packets audited and bounded those projects as possible upstream
sources; that audit is not itself integration. The approved runtime amendment
now makes the complete pinned Hermes source the Qubit runtime foundation. No
Hermes or OpenClaw capability may be described as integrated merely because a
contract, label, stub, or external installation exists.

### Reuse rule

Reuse proven upstream code where it is technically and legally appropriate.
Do not blindly merge both systems or copy code without recording its origin.

For every reused file or substantial implementation:

1. pin the upstream repository, commit/tag, and source URL;
2. inspect and comply with its license and notices;
3. preserve required copyright and attribution text;
4. record the reuse in `THIRD_PARTY_NOTICES.md`;
5. integrate it into the Qubit-owned Hermes foundation or write a Qubit
   adapter/compatibility boundary where the source unit is not directly
   suitable;
6. add tests proving that Qubit’s permissions and data rules still apply;
7. document all local modifications and their maintenance burden.

Open-source availability is permission to study and potentially reuse under the
license; it is not permission to erase attribution, ignore dependencies, or
claim upstream work as entirely original.

### Latest upstream release and update policy

The parity target is the latest stable Hermes release and latest stable
OpenClaw release available at each Qubit release cut. Earlier pins are not a
permanent ceiling. Every upstream update must be handled as a reproducible,
audited parity change:

1. discover the official stable release/tag;
2. clone it read-only into a disposable directory;
3. pin the exact commit, tag, date, URL, license, and source hashes;
4. inventory every new command, page, API, backend feature, tool, plugin,
   workflow, model/provider feature, and protocol surface;
5. compare the inventory against the previous Qubit pin;
6. integrate every compatible feature into the Qubit-owned Hermes foundation;
7. provide CLI, dashboard, and VoiceBridge access where applicable;
8. add regression, security, persistence, cancellation, rollback, packaging,
   and degraded-state tests;
9. rerun the complete parity and release gates; and
10. append provenance and evidence without deleting historical records.

Unpinned branches and unrecorded “latest” claims are forbidden. A feature may
not be silently omitted. When direct exposure is unsafe, unavailable, or
legally unsuitable, Qubit must provide a tested safe equivalent with an exact
reason, owner, typed state, and rollback. Any new upstream release reopens
parity verification and invalidates an earlier full-parity claim until the
delta is reviewed and verified.

### Atomic Agent reuse boundary

The review pin for this plan is:

```text
REPOSITORY: https://github.com/AtomicBot-ai/atomic-agent
PIN: 86321a419c9e634b1b8de9fc46196858672377f7 (main observed 2026-08-27)
LICENSE: MIT, Copyright (c) 2026 Atomic Bot
```

Take the smallest compatible behavior from the pinned source and adapt it to
the existing Python Voice Bridge contracts. The approved candidate set is:

- one validated structured tool-call array per model step;
- stable prompt prefix plus bounded variable tail and explicit token budgets;
- externalized session/world state instead of pasting large tool results;
- parallel read-only calls, with writes and destructive work serialized behind
  the Qubit permission broker;
- progressive skill loading, disabled-skill invisibility, and script approval;
- per-session FIFO ordering, cancellation propagation, no-progress detection,
  and typed transport/grammar/model/tool/cancellation failures;
- compact ARIA/browser snapshots as bounded evidence.

Do not import the Atomic Agent Node/TypeScript runtime, Tauri sidecar, second
router, second memory store, second scheduler, or second permission system. Do
not enable its analytics/crash-reporting or copy its `.env`, browser-profile,
credential-import, self-update, or destructive-uninstall behavior. Do not adopt
TurboQuant or its separate `llama.cpp` fork from a README performance claim;
that is a separate model/runtime decision requiring its own pinned source,
license, VRAM, quality, and security evaluation. Atomic Agent is an input to
Qubit’s contracts, not a new runtime dependency.

### Atomic source-to-feature matrix

This matrix is the authoritative scope for the Atomic compatibility work. The
source paths and tests below were inspected at the pin above. This task changes
the plan only; it does not copy Atomic code or change the Voice Bridge runtime.

| Atomic source and evidence | Behavior to adapt | Qubit contract/owner | Decision and required proof |
|---|---|---|---|
| `src/prompt/stable-prefix.ts`, `src/prompt/build-prompt.ts`, `src/prompt/build-prompt.test.ts` | Byte-stable persona/tools/capabilities/skill prefix and mutable bounded tail for KV-cache reuse | `ModelProvider` + context compiler | ADAPT into the existing Python compiler; hash stability, invalidation, omission, and token-budget tests required |
| `src/prompt/token-budget.ts`, `src/prompt/token-budget.test.ts` | Explicit per-section budgets and deterministic truncation | `ModelProvider.chat()` context preparation | ADAPT; never exceed model context or hide dropped sections |
| `src/llm/grammar/tool-call-grammar.ts`, `src/llm/grammar/build-grammar.ts`, `src/llm/grammar/tool-call-grammar.test.ts` | One validated JSON array of one or more tool calls, with empty/invalid roots rejected | `ModelProvider` output validation + `Capability` dispatch | ADAPT; reject malformed, oversized, policy-changing, or non-schema calls before execution |
| `src/agent/batch-executor.ts`, `src/agent/parallel-tool-calls.integration.test.ts`, `src/agent/batch-executor.test.ts` | Parallel independent reads; ordered/serialized shared-state and terminal work; sibling failure isolation | `Capability.execute()` + permission broker | ADAPT; only explicitly classified read-only operations may fan out; writes, sends, and destructive work remain approval-gated and serialized |
| `src/skills/skill-loader.ts`, `src/skills/skill-manifest.ts`, `src/skills/skill-loader.test.ts`, `skill-manifest.test.ts` | Typed manifests, project-over-global precedence, platform filtering, parse-error collection | `Skill.inspect()/import()/validate()` | ADAPT; preserve provenance and reject unknown tools, permissions, paths, scripts, and network requirements |
| `src/skills/skill-registry.ts`, `src/skills/skill-registry.test.ts` | Disabled skills disappear from the model view while remaining visible to the operator; changes invalidate the prompt prefix | `Skill.enable()/disable()` + context compiler | ADAPT; prove disabled skills cannot run, become prompt-visible, or bypass broker policy |
| `src/tools/browser/aria-compressor.ts`, `src/tools/browser/aria-compressor.test.ts` | Noise removal, line/character caps, stable refs, digest, and compact browser evidence | `BrowserSession.read()` | ADAPT; retain citations/refs, treat page text as untrusted, enforce size/SSRF/privacy limits |
| `src/runtime/turn-controller.ts`, `src/runtime/turn-controller.test.ts` | Per-session FIFO, cross-session concurrency, event isolation, queue survival after failure | `AgentSession` + `Worker` | ADAPT; prove ordering, cancellation before start, cancellation during work, and no stale events |
| `src/agent/loop-detector.ts`, `src/agent/loop-detector.test.ts` | Repeat/no-progress/wandering detection, graduated warning, veto, and graceful termination | `Worker` task state machine | ADAPT; no-progress protection must run before dispatch, never weaken permission checks, and remain configurable/auditable |
| `src/llm/reliability/failure-category.ts`, `src/agent/agent-loop.ts`, related agent/tracing tests | Typed transport/grammar/model/tool/cancellation failures and observable terminal outcomes | `ModelProvider`, `Worker`, audit/event contracts | ADAPT; preserve error category, retryability, cancellation, provenance, and user-visible degraded state |

Atomic's browser, filesystem, shell, MCP, model, and scheduler features are
not automatically in scope merely because they exist upstream. They must map to
an existing Qubit capability and pass the same broker, scope, secret, timeout,
cancellation, audit, and rollback gates as native implementations.

### Atomic compatibility deliverables

Before any Atomic-derived behavior is enabled, the implementing packet must
produce all of the following:

1. `docs/UPSTREAM_REUSE.md` entry containing the exact commit, source paths,
   tests inspected, MIT license, local divergence, and maintenance strategy.
2. An Atomic MIT notice in `THIRD_PARTY_NOTICES.md` containing `Copyright (c)
   2026 Atomic Bot` and the required MIT permission/warranty text. A plan-only
   review does not satisfy this obligation; the notice must land in the same
   change that first adapts or copies substantial behavior.
3. A Qubit compatibility test module with sanitized fixtures covering every
   matrix row, plus negative/security cases for malformed tool calls, prompt
   injection, disabled skills, secret leakage, unauthorized capability use,
   path escape, untrusted browser text, cancellation, and loop termination.
4. A repeatable resource benchmark on the supported machine comparing the
   current Qubit path with the adapted path using the same model, prompt,
   workload, and context. Record wall latency, time to first token/audio where
   applicable, CPU, RSS, VRAM, context tokens, tool count, failures, and
   cancellation time. No Atomic README or public benchmark number is accepted
   as a Qubit performance result.
5. A rollback note that disables the Atomic-derived adapter/flag and restores
   the previous Qubit contract without deleting memory, reports, cursors,
   sessions, or user configuration. A failed compatibility test must leave the
   adapter disabled and must not promote a backend automatically.

The first implementation should be the smallest high-value slice: structured
tool-call validation, bounded prompt assembly, and safe read-only batching.
Prompt caching, skills, browser compression, FIFO, loop protection, and failure
taxonomy follow only when their contract tests can run on the existing Qubit
stack. No Node package, Tauri binary, Atomic browser profile, or Atomic model
server is added to satisfy this matrix.

### Reuse, translation, and rewrite policy

Qubit will use a three-level reuse strategy:

1. **Direct reuse:** use a compatible upstream module with the smallest
   possible wrapper when its license, dependencies, security model, and data
   behavior fit Qubit.
2. **Adaptation/translation:** copy the proven behavior behind a Qubit contract
   and translate names, types, configuration, events, and errors into Qubit’s
   conventions when direct integration is impractical.
3. **Independent rewrite:** implement the capability in Qubit when upstream
   assumptions conflict with local permissions, portability, performance,
   security, or architecture.

“Rewrite in our language” means rewrite the implementation in Qubit’s own
interfaces and conventions—not remove attribution from copied source. A clean
room rewrite still needs a behavior comparison, and any copied expression or
substantial code still needs provenance and license review.

Every translation or rewrite must include:

- an upstream behavior and feature matrix;
- compatibility tests using sanitized upstream examples;
- a documented reason for adapting or rewriting;
- source/commit/license provenance where upstream behavior was followed;
- Qubit-specific security, permission, timeout, cancellation, and rollback
  tests;
- a maintenance note stating whether future upstream updates are merged,
  cherry-picked, or reimplemented manually.

### Skill compatibility policy

Qubit must accept OpenClaw and Hermes skills through a compatibility layer.
Skills are portable procedures, not trusted executable authority.

The skill importer must:

- support each upstream skill format that is in scope;
- preserve the original skill name, author, source URL, version, commit, and
  license;
- convert metadata and instructions into the Qubit skill manifest;
- map skill tool names to Qubit capability IDs;
- reject unknown tools, undeclared permissions, unsafe paths, and hidden
  network or command requirements;
- run imported skills in a restricted workspace;
- require approval for write, send, financial, credential, or destructive work;
- keep imported skills disabled until validation passes;
- support dry-run, enable, disable, update, export, and rollback;
- record which skill and version produced every task or artifact.

Qubit should run a compatible OpenClaw or Hermes skill without requiring the
original agent runtime, whenever the skill only depends on declared contracts.
If a skill requires an upstream-only capability, Qubit may route it to the
OpenClaw/Hermes compatibility backend while clearly reporting that dependency.

## 1.1 Continuous feature and skill standard

Qubit must support frequent feature additions without turning the prompt,
launcher map, or model output into an unofficial plugin system. Every new
feature, website, desktop app, editor, worker procedure, or imported skill is a
versioned product change that passes the same lifecycle.

### One canonical skill record

The Qubit `SkillManifest` is the source of truth. A skill record must include:

- immutable skill ID and semantic version plus manifest-schema version;
- display name, description, owner, supported locales, spoken aliases, and
  example requests;
- source repository or package URL, pinned commit/release, SPDX license,
  content hash, attribution, and upstream compatibility notes;
- required Qubit capabilities, exact sites/apps/workspaces, network domains,
  data classes, read/write/send/destructive tier, and approval policy;
- input/output schemas, route/tool names, timeout, CPU/RAM/VRAM/disk/network
  budgets, concurrency, cancellation, retryability, and idempotency;
- supported platforms, model/provider requirements, authentication
  preconditions, audit events, report/artifact behavior, and rollback/removal;
- lifecycle state: `proposed`, `validated`, `staged`, `enabled`, `canary`,
  `verified`, `deprecated`, or `removed`.

Capability IDs, event names, persisted fields, and route names are stable
interfaces. They may not be silently renamed because a new model or upstream
skill uses a different name. Breaking changes require a new version, a
migration, compatibility tests, and an explicit user decision.

### Feature lifecycle: proposal to release

Every feature follows this exact path:

```text
idea/request
  -> capability and risk classification
  -> upstream/license/provenance review
  -> manifest + contract + acceptance tests
  -> smallest adapter or native implementation
  -> staged disabled install
  -> synthetic/security/resource/persistence tests
  -> local canary with explicit scope
  -> independent verification
  -> release-channel promotion
  -> documentation, monitoring, rollback, and deprecation record
```

The daily development process may create many proposals, but no proposal is
automatically enabled, promoted, or learned as policy. Each change belongs to
one feature packet with an allowed-file list, owner, dependencies, migration,
rollback, and definition of done. Agents must not combine unrelated features
to make a packet appear complete.

### Standard feature packet record

Every feature packet uses this minimum record before implementation:

```text
feature_id: QF-YYYY-NNN
title:
user_problem:
owner:
interfaces: [voice, text, cli, dashboard, worker]
skill_ids:
capability_ids:
risk_tier: read-only | personal-data | write | destructive
upstream_sources:
license_and_notice:
allowed_files:
dependencies:
manifest_schema_version:
acceptance_tests:
resource_budget:
live_canary_scope:
migration:
rollback:
deprecation:
```

The record is stored with the implementation and linked from the release
report. Missing fields are a planning failure, not a reason to guess. A packet
may omit an interface only when the manifest explicitly says it is unsupported
and the UI/CLI/voice layer reports that state honestly.

### Stable extension surface

The first stable extension surface is intentionally small:

- manifest files are validated JSON with a declared schema version;
- adapters expose typed `inspect`, `validate`, `health`, `execute`,
  `cancel`, and `rollback` behavior;
- CLI commands are machine-readable and map to the same backend used by the
  dashboard and voice layer;
- skill status and permission decisions are persisted in the Qubit store, not
  in prompts, browser local storage, or model-generated files;
- release reports and audit events refer to immutable skill IDs, versions,
  decision IDs, and artifact hashes.

New UI buttons, spoken aliases, or launcher entries may expose an existing
contract, but they may not create an undocumented capability. New capability
IDs require a manifest, broker registration, tests, and a compatibility review.

### Release channels and update policy

Qubit has four states for ongoing delivery:

1. `development`: local implementation and hermetic tests only;
2. `staged`: installed but disabled, inspectable through CLI/dashboard;
3. `canary`: explicitly enabled for selected users, scopes, and task classes;
4. `stable`: independently verified and eligible for normal use.

There is no silent auto-upgrade of skills, models, permissions, browser
selectors, or worker prompts. Updates are pinned, checked for license/hash and
compatibility, staged beside the previous version, and reversible. A failed
upgrade leaves the previous stable version active. Deprecated skills remain
visible with a replacement and removal date until the user removes them.

### OpenClaw and Hermes skill catalog

Before importing skills, Qubit maintains a machine-readable catalog with one
row per skill from the pinned OpenClaw and Hermes sources:

| Classification | Meaning | Qubit action |
|---|---|---|
| `native` | Qubit already owns an equivalent contract | expose one Qubit skill; do not duplicate it |
| `adapt` | behavior fits a Qubit capability with translation | wrap the smallest proven source behind the manifest |
| `delegate` | useful but requires an upstream-only runtime | route through the approved compatibility backend and show the dependency |
| `defer` | missing contract, unsafe scope, license issue, or insufficient evidence | keep disabled and record the blocker |
| `reject` | destructive, unreviewable, unlicensed, secret-dependent, or conflicting | never import or enable |

The catalog records source, revision, license, dependencies, tools, required
permissions, data sensitivity, supported platforms, test status, resource
measurements, and maintenance owner. “Teach all skills” means catalog and
validate the agreed skill set; it does not mean blindly enabling every upstream
script or copying two complete runtimes into Qubit.

### Voice is a first-class skill interface

Every enabled skill that is available through voice must register a typed voice
entry in the same manifest. It contains spoken aliases, locale, required
entities, examples, disambiguation questions, confirmation wording, progress
events, cancellation phrase, and a safe response renderer. The same skill ID
must be callable from voice, text, dashboard, and CLI; those interfaces may not
silently use different permissions or implementations.

Voice resolution is:

```text
spoken input -> transcript + confidence
  -> intent/skill candidate shortlist
  -> schema and scope validation
  -> permission decision / spoken confirmation
  -> skill execution with progress and cancellation
  -> typed result -> voice + dashboard + audit
```

Low-confidence or ambiguous speech asks a clarification question. It never
guesses a destructive target. Voice can request a permission or confirmation,
but it cannot grant itself one. A skill with no voice entry remains available
through its permitted non-voice interfaces and is not falsely advertised as a
voice capability.

### Extension acceptance gate

No new skill or feature may reach `stable` until all applicable evidence exists:

- manifest validation and provenance/license/notice review;
- contract, schema, unit, negative/security, injection, permission, and
  persistence tests;
- real adapter or an honestly limited launcher, with no claimed capability it
  cannot perform;
- resource and latency measurements on the supported machine, including cold
  and warm behavior and the 5 GB VRAM ceiling;
- dashboard/CLI/voice parity and accessibility evidence;
- audit, redaction, cancellation, timeout, retry, and rollback evidence;
- migration, upgrade, disable, removal, and restore documentation;
- independent verification with failures, skips, warnings, and untested paths
  reported honestly.

If a required live dependency is unavailable, the skill is `degraded` or
`blocked`, not stable. A feature flag controls exposure; it does not replace
tests or authorization.

## 2. Target architecture

```text
                         ┌──────────────────────────────┐
voice / text / media ───▶│ Qubit Experience Layer       │
                         │ voice, dashboard, web, CLI    │
                         └──────────────┬───────────────┘
                                        │
                         ┌──────────────▼───────────────┐
                         │ Qubit Control Plane           │
                         │ session, router, planner,     │
                         │ context, memory, learning     │
                         └──────┬───────────┬───────────┘
                                │           │
                    ┌───────────▼───┐ ┌────▼──────────────┐
                    │ Model Gateway  │ │ Permission Broker │
                    │ local/big LLM  │ │ allow/deny/ask    │
                    └───────┬────────┘ └────┬──────────────┘
                            │               │
                  ┌─────────▼─────────────▼─────────┐
                  │ Qubit Capability Plane           │
                  │ files, repos, browser, inbox,    │
                  │ web, reports, terminal, workers  │
                  └──────────────┬───────────────────┘
                                 │
                 ┌───────────────▼────────────────┐
                 │ Execution Backends              │
                 │ Qubit-native | OpenClaw | Hermes│
                 │ optional during migration       │
                 └───────────────┬────────────────┘
                                 │
                 ┌───────────────▼────────────────┐
                 │ audit, artifacts, reports,      │
                 │ metrics, rollback, backup       │
                 └────────────────────────────────┘
```

### Ownership boundaries

| Layer | Qubit owns | Temporary upstream use |
|---|---|---|
| Experience | voice, dashboard, web UI, user feedback | none required |
| Control plane | routing, sessions, context, memory policy | import proven logic selectively |
| Model gateway | provider selection and budgets | Ollama, OpenAI-compatible APIs, other providers |
| Permissions | one canonical decision and audit system | upstream approvals must pass through it |
| Capabilities | stable Qubit tool contracts | OpenClaw/Hermes implementations behind adapters |
| Coding worker | task lifecycle and result format | Hermes first, OpenClaw fallback |
| Gateway | Qubit task protocol | OpenClaw gateway during migration |
| Skills | versioned Qubit skill format | import compatible upstream skills with provenance |

There must be one source of truth for routing, permissions, memory policy,
session identity, scheduling, and audit events. Two competing implementations
of any of these are a release blocker.

## 3. Common Qubit contracts

Before replacing upstream internals, define stable interfaces. Each contract
must have typed inputs, typed outputs, timeouts, cancellation, errors, source
provenance, and tests.

```text
ModelProvider
  chat(), stream(), capabilities(), health(), usage()

AgentSession
  create(), resume(), append(), cancel(), summarize(), export()

Capability
  describe(), validate(), authorize(), execute(), rollback()

PermissionBroker
  decide(), confirm(), deny(), audit()

MemoryStore
  remember(), search(), correct(), forget(), export(), restore()

Worker
  submit(), progress(), result(), cancel(), artifacts()

BrowserSession
  login(), read(), capture(), close(), health()

Scheduler
  create(), pause(), run_due(), history(), delete()

Report
  create(), attach_source(), attach_artifact(), finalize(), export()

Skill
  inspect(), import(), validate(), enable(), disable(), run(), export(), rollback()
```

OpenClaw and Hermes become implementations of these contracts. Later, Qubit
can replace them without changing the voice interface or user workflows.

## 4. Model strategy

Qubit is not the model itself in this plan. It is the agent system around one or
more models.

- **Fast local model:** short answers, classification, summaries, and fallback.
- **Main local model:** normal private conversations and local planning.
- **Big LLM:** difficult reasoning, research, planning, and complex coding when
  the user permits external processing.
- **Specialist models:** ASR, TTS, OCR, vision, video transcription, embeddings,
  and reranking.

The router chooses the smallest model that can safely do the job. A bigger LLM
can make Qubit more capable, but it does not receive unrestricted PC access.
It proposes structured actions; the local broker validates and authorizes them.

Long-term memory belongs to Qubit’s local store. It is not assumed that a
model provider will remember or learn Qubit’s private data.

## 5. Permission and security model

Permissions are selected by the user and default to deny. They are independent
for:

- microphone, camera, screen, and media capture;
- reading and writing files;
- repository access and command execution;
- browser sessions and inboxes;
- sending, deleting, or changing messages;
- network access and external model use;
- long-term memory and learning;
- scheduling unattended work;
- destructive operations.

Rules:

1. The model cannot grant itself permission.
2. Read and write capabilities are separate.
3. Destructive actions require explicit confirmation.
4. Every execution has a decision ID and audit record.
5. Secrets never enter prompts, reports, screenshots, or logs.
6. Retrieved web, browser, inbox, and model text is untrusted evidence.
7. Untrusted evidence cannot modify policy, tools, routing, persona, or
   permissions.
8. Browser profiles and cookies remain user-controlled; passwords are never
   given to the model.
9. A failed or unavailable adapter reports failure; it never reports success.
10. Cloud routing requires a separate data-sharing permission.

## 6. Phased implementation

Every phase ends with an independent verification gate. The next phase cannot
start merely because an agent says “done.”

### Phase Q0 — Inventory, licensing, and freeze

Objective: establish exactly what exists before combining anything.

Tasks:

- inventory Voice Bridge, OpenClaw, and Hermes capabilities;
- inventory Atomic Agent's pinned control loop, prompt builder, tool grammar,
  skill loader, browser adapter, task controller, traces, and bundling paths;
- pin upstream commits/tags and collect licenses and notices;
- map duplicate routers, planners, memories, schedulers, gateways, and tools;
- create a capability matrix: reuse, wrap, rewrite, or defer;
- inventory OpenClaw and Hermes skill formats, manifests, tool references,
  permissions, licenses, and runtime assumptions;
- define Qubit naming and package boundaries;
- record current tests, benchmarks, configuration, and rollback points.

Acceptance:

- no unknown upstream code is copied;
- every capability has one planned owner;
- license and notice review is complete;
- baseline tests and performance numbers are recorded.
- imported skill sources and their licenses are recorded.
- Atomic Agent reuse decisions identify each behavior as adapt, reject, or
  defer; no unpinned Atomic code is used.

### Phase Q1 — Qubit shell and contracts

Objective: create the unified interface without changing production behavior.

Tasks:

- add Qubit package namespaces and contract types;
- define model, session, worker, capability, memory, approval, browser,
  scheduler, report, and event schemas;
- add adapter registries and version negotiation;
- define one Qubit turn protocol supporting a validated array of independent
  tool calls, deterministic per-session ordering, cancellation, and bounded
  max-step/no-progress termination;
- route existing Voice Bridge behavior through contracts in shadow mode;
- add contract tests and compatibility fixtures.

Acceptance:

- existing Voice Bridge behavior is unchanged;
- contract tests pass;
- no duplicate permission or memory store is active;
- rollback is one configuration switch.

### Phase Q2 — Unified model and session gateway

Objective: allow Qubit to choose local, OpenClaw, Hermes, or a big LLM through
one model/session interface.

Tasks:

- wrap Ollama and external OpenAI-compatible providers;
- preserve streaming, cancellation, timeouts, usage, and error semantics;
- normalize conversation/session IDs;
- keep system policy separate from retrieved evidence;
- add stable-prefix and bounded-tail prompt budgeting with cache invalidation
  only when tools/capabilities/skills actually change;
- implement model budgets and local-only mode;
- add provider health and fallback logic.

Acceptance:

- one Qubit request can use each approved provider;
- local-only mode sends no private content externally;
- provider failure is visible and recoverable;
- streaming and cancellation tests pass.

### Phase Q3 — Memory, skills, and learning unification

Objective: make Qubit’s memory and procedural learning the single source of
truth.

Tasks:

- import compatible OpenClaw/Hermes memories through a dry-run converter;
- preserve source, timestamp, scope, confidence, and provenance;
- support remember, search, correct, forget, export, and restore;
- import skills with version, license, permissions, and test metadata;
- build OpenClaw/Hermes skill importers and a Qubit skill manifest;
- build the machine-readable OpenClaw/Hermes skill catalog and classify every
  candidate as `native`, `adapt`, `delegate`, `defer`, or `reject`;
- map imported skill tools to declared Qubit capabilities;
- validate imported skills in dry-run/restricted mode before enabling them;
- adopt progressive skill loading and keep disabled skills invisible to the
  model while still visible to the user as disabled;
- add typed voice entries to enabled skills: spoken aliases, locale, required
  entities, clarification, confirmation, progress, cancellation, and response
  rendering;
- make voice, text, dashboard, and CLI resolve the same skill ID and execute
  through the same backend and permission decision;
- record successful procedures as reviewable recipes;
- forbid automatic policy changes from learned content.

Acceptance:

- stale and tombstoned facts are excluded;
- corrections outrank old facts;
- memory export/restore works on a clean machine;
- imported skills cannot bypass the broker;
- the catalog has source/revision/license/dependency/test/resource evidence for
  every imported skill, including rejected and deferred entries;
- at least one OpenClaw skill and one Hermes skill run through Qubit’s
  compatibility layer;
- a skill enabled for voice has the same behavior and permission result through
  text, dashboard, and CLI;
- user can inspect and delete learned information.

### Phase Q4 — One permission and capability plane

Objective: ensure every upstream and native tool passes through Qubit policy.

Tasks:

- register files, repos, web, browser, inbox, reports, workers, terminal, and
  memory as capabilities;
- wrap OpenClaw and Hermes tools behind `Capability`;
- enforce path, site, network, argument, scope, and sensitivity checks;
- add approval UI and spoken confirmation;
- add redaction and audit IDs;
- enforce manifest lifecycle transitions and prevent direct `proposed` or
  `staged` execution;
- make routine feature updates explicit, pinned, staged beside the previous
  version, and reversible; no model output or package hook may approve an
  update or widen a grant;
- add destructive-action deny tests and rollback tests.

Acceptance:

- no direct model-to-shell or model-to-browser path exists;
- all worker actions carry a Qubit decision;
- denied and confirmed operations are auditable;
- hostile retrieved text cannot change policy or tools;
- security tests pass with zero unexpected skips.

### Phase Q5 — Qubit-native execution and coding worker

Objective: replace the required dependency on Hermes/OpenClaw for core task
execution while retaining them as fallbacks.

Tasks:

- implement a Qubit task state machine: plan, authorize, run, observe, pause,
  resume, cancel, complete, fail, rollback;
- build repository inspection, patch, test, and artifact workflows;
- add isolated workspaces and resource limits;
- import proven Hermes coding behavior behind the Worker contract;
- translate reusable OpenClaw/Hermes task-loop behavior into Qubit contracts
  where direct reuse is not suitable;
- preserve OpenClaw compatibility for tasks Qubit cannot yet execute;
- add progress events and resumable task sessions.
- add typed transport/grammar/model/tool/cancellation failure categories and a
  no-progress guard for repeated identical calls;

Acceptance:

- Qubit can complete a representative coding task without Hermes;
- failed tasks do not leave untracked destructive changes;
- cancel, timeout, retry, and rollback work;
- worker outputs have artifacts and provenance;
- Qubit and Hermes produce equivalent results for the agreed task matrix.
- imported coding skills retain provenance and cannot escape their workspace.

### Phase Q6 — Browser, inbox, media, and PC capabilities

Objective: expose the personal-computer abilities through Qubit contracts.

Tasks:

- use persistent headed Chromium for user-managed logged-in sessions;
- implement allowlisted Gmail/social/browser read adapters;
- keep read and send/change operations separate;
- add OCR, image, audio, and video extraction adapters;
- connect files, repositories, web research, and reports;
- preserve URLs, timestamps, screenshots, source IDs, and freshness;
- use compact accessibility/ARIA snapshots as the default browser evidence;
  screenshots remain bounded supplemental evidence rather than the full page
  prompt;
- register each browser, desktop-app, and editor skill with a real adapter or
  honestly limited launcher; opening an application alone must not claim that
  Qubit can control it;
- make login expiry and partial failure visible.

Acceptance:

- each adapter passes unit, security, and real live smoke tests;
- no password or cookie is stored in model context;
- the assistant never claims an inbox check succeeded when login failed;
- image/video evidence is bounded and cited;
- send/delete/change actions require confirmation.
- the same registered skill works consistently from voice, text, CLI, and
  dashboard, or its unsupported interface is explicitly shown to the user.

### Phase Q7 — Scheduling, reports, and background agent

Objective: allow Qubit to work while the user is away without losing control.

Tasks:

- implement hourly inbox/social checks as read-only jobs;
- add deduplication checkpoints and idempotent runs;
- generate professional reports with links, screenshots, timestamps, counts,
  failures, omissions, and privacy notes;
- add notification channels and quiet hours;
- add backup, retention, retry, and pause controls;
- add resource budgets and a global stop switch.

Acceptance:

- repeated jobs do not duplicate reports or messages;
- unreachable sources produce degraded reports, never false “nothing new”;
- every unattended action is visible in the audit log;
- user can pause, inspect, export, and delete job state.

### Phase Q8 — Delegation and parallel work

Objective: replace upstream multi-agent behavior with controlled Qubit workers.

Tasks:

- define isolated child-task contracts;
- implement bounded parallel read-only work;
- execute independent read-only calls in parallel after one model decision;
  serialize writes, sends, and destructive actions through the broker;
- merge results through citations and conflict handling;
- enforce per-child permissions, budgets, deadlines, and cancellation;
- use OpenClaw/Hermes delegation only through the same interface during
  migration.

Acceptance:

- child agents cannot escalate permissions;
- partial failures remain visible;
- parallel work is faster than serial work under measured workloads;
- cancellation stops all descendants;
- result merging is deterministic and cited.

### Phase Q9 — Compatibility, canary, and migration

Objective: prove that Qubit can replace upstream paths safely.

Tasks:

- run a fixed feature-parity matrix against OpenClaw, Hermes, and Qubit;
- include Atomic Agent's representative local-loop tasks in the same matrix,
  comparing correctness, tool-call validity, context size, latency, VRAM/RAM,
  cancellation, and safety—not just public benchmark scores;
- run a skill-parity matrix against representative OpenClaw and Hermes skills;
- run the complete catalog through import, validation, staging, enablement,
  canary, rollback, disablement, export, and removal checks;
- replay sanitized real task traces;
- compare correctness, latency, resource use, failures, and safety decisions;
- run Qubit in shadow mode first;
- enable Qubit for a small canary percentage or selected task classes;
- keep instant rollback to the last working backend;
- migrate memory, skills, schedules, and sessions with dry-run previews.

Acceptance:

- no critical capability is silently lost;
- Qubit meets defined safety and latency thresholds;
- canary rollback is tested;
- migration is reversible and backup restore is proven;
- imported skills can be exported, disabled, rolled back, and revalidated;
- user explicitly approves each backend promotion.

### Phase Q10 — Upstream removal and Qubit release

Objective: make OpenClaw and Hermes optional rather than required.

Tasks:

- disable one upstream backend at a time;
- run the complete compatibility and live suite after each removal;
- remove unused dependencies only after import and runtime audits;
- verify all required skills run through Qubit without hidden OpenClaw/Hermes
  runtime dependencies;
- retain an optional compatibility package if legally and technically useful;
- produce a clean installer, model manifest, migration guide, and backup tool;
- publish third-party notices and Qubit changes clearly.

Final acceptance:

- Qubit can run the agreed capability matrix without OpenClaw or Hermes;
- Qubit can import, validate, run, update, disable, export, and roll back the
  agreed OpenClaw/Hermes skill matrix;
- local-only mode works on a clean installation;
- voice, text, media, browser, coding, reports, memory, scheduling, and
  permissions pass their real acceptance tests;
- rollback and recovery are documented;
- no known critical issue is hidden behind a skip or warning;
- the release is reproducible from a clean checkout.

### Phase Q11 — continuous feature factory and lifecycle operations

Objective: keep adding daily features without destabilizing the unified agent.

This is the permanent post-release process. It is not permission to bypass the
Q0–Q10 gates or to enable unreviewed functionality in the stable channel.

For every new feature or skill:

1. Create a feature record with a unique ID, owner, user problem, supported
   interfaces, dependencies, risk tier, affected contracts, allowed files,
   acceptance criteria, migration, rollback, and deprecation plan.
2. Search the Qubit catalog and pinned upstream sources before coding. Reuse
   the smallest compatible proven implementation; record why a feature is
   native, adapted, delegated, deferred, or rejected.
3. Add or extend a versioned manifest and schemas before implementation. A
   prompt example, launcher entry, or UI toggle is not a feature contract.
4. Implement behind a disabled flag or staged registry entry. Keep the prior
   stable version active until independent verification succeeds.
5. Test every applicable interface: voice, text, CLI, dashboard, and worker
   execution. Add contract, negative/security, injection, permission, resource,
   persistence, cancellation, and rollback tests.
6. Run a local canary with narrow scopes and explicit approval. Record latency,
   CPU/RAM/VRAM, error rate, tool validity, user-visible result quality, and
   audit events on the supported machine.
7. Promote only after independent verification. Publish documentation,
   provenance, notices, configuration, migration, known limitations, and
   rollback with the release.

Daily feature delivery uses three lanes:

- `stable`: verified, documented, and safe for normal enabled use;
- `canary`: explicitly enabled for selected scopes or users and automatically
  revertible;
- `experimental`: disabled by default and never advertised as supported.

Required permanent services:

- a catalog linter that rejects missing manifest fields, unpinned sources,
  unknown capabilities, broad scopes, missing licenses, or missing rollback;
- a compatibility checker that detects breaking schema, CLI, dashboard, voice,
  and event changes before merge;
- a release report containing changed skills, permissions, interfaces, tests,
  resource measurements, warnings, and rollback target;
- a deprecation registry with migration guidance and removal dates;
- a periodic audit of imported OpenClaw/Hermes skills against their pinned
  upstream revisions and licenses.

Q11 acceptance:

- a new skill can be proposed, reviewed, staged, enabled, canaried, promoted,
  downgraded, disabled, exported, and removed without editing prompts or
  hand-written launcher maps;
- every enabled voice skill has parity with its other supported interfaces;
- stable updates never widen permissions, change model/provider/audio choices,
  or enable recording without a separate user decision;
- a failed update leaves the previous stable version usable and restores state;
- catalog, manifests, docs, audit, tests, and release reports remain
  consistent after repeated feature additions;
- no daily feature process can bypass independent verification or the Qubit
  safety, provenance, resource, and rollback rules.

### Post-8T whole-computer capability track (8U–8AH)

The Qubit roadmap continues after identity, release, and setup work. The
current product is not yet a universal desktop agent: existing browser, Ghost,
OpenCode, file, and provider surfaces are bounded adapters with different
availability states. The following packets make those capabilities coherent
without creating unrestricted control or a second authority.

#### Upstream reuse and delivery strategy

Hermes and OpenClaw are approved upstream sources to audit and selectively
adapt for the capability track. Before implementing a feature, pin the exact
upstream commit, inspect its source and tests, verify its license/notices and
dependency licenses, and map the smallest reusable units to Qubit contracts.
Qubit must remain the product and policy authority: its broker, grants,
confirmations, audit, emergency stop, voice/audio session, dashboard API,
state, and release rules cannot be replaced by an upstream router or tool
registry. Whole-repository copying and unreviewed plugin import are not
acceptable substitutes for integration.

Each capability packet has four execution outcomes:

- **REUSE** — the pinned upstream implementation already satisfies the Qubit
  contract; add provenance, integration, negative tests, and verification;
- **ADAPT** — upstream behavior exists but needs Qubit policy, session, UI,
  packaging, compatibility, or resource wrapping;
- **MISSING** — implement only the smallest capability absent from both Qubit
  and the audited upstream sources;
- **REJECTED** — do not integrate behavior that is unsafe, duplicated,
  incompatible, unlicensed, or unsupported on the target system.

Therefore 8U–8AH are not a requirement to rebuild every feature. A packet may
contain no new feature code when the upstream capability is already suitable;
it still requires independent Qubit-path verification before being marked
complete. Upstream feature presence is not proof of Qubit compatibility,
permission enforcement, live availability, or production readiness.

Work may be accelerated by parallel read-only research, provenance audits,
fixture preparation, and isolated implementation branches. Dependent packets
must still be merged and independently verified in order. A green test run in
an isolated branch does not certify the merged product, and parallel agents
must never share a dirty checkout or overwrite unrelated user work.

```text
8S identity/release -> 8T setup/health
8T -> 8T-F Qubit/Hermes foundation migration
8T-F -> 8U browser-first voice
8U -> 8V desktop apps
8U + 8V -> 8W files/folders
8W + 8R -> 8X IDE workspace
8U + 8V + 8W + 8X -> 8Y unified task context
8Y + 8T -> 8Z adapter lifecycle/catalog
8Z -> 8AA always-on presence/context
8AA -> 8AB Firefox and multi-browser parity
8AB -> 8AC adapter SDK/contract
8AC -> 8AD website integration packs
8AC -> 8AE desktop application integration packs
8AE -> 8AF background/process/game coexistence
8AD + 8AE + 8AF -> 8AG coverage and production qualification
8AG -> 8AH unified Qubit dashboard
```

### Packet 8T-F — Qubit Agent foundation migration before 8U

Packet 8T-F is a prerequisite for the whole-computer capability track. It
vendors the complete pinned Hermes checkout as the Qubit-owned runtime
foundation, then applies Qubit policy, VoiceBridge, and OpenClaw integrations.
It makes Qubit Agent the product identity, integrates the
existing Qubit VoiceBridge as the only voice/audio session, and integrates all
requested OpenClaw capabilities in the current foundation scope behind Qubit
contracts. A supervisor-only wrapper around an unchanged Qubit tree is not an
acceptable substitute.
The current Qubit checkout remains preserved as a rollback/reference tree
until the cutover is independently verified. The canonical architecture
and current status are recorded in [8TF_SOURCE_OF_TRUTH.md](8TF_SOURCE_OF_TRUTH.md).
The execution sequence and hard gate are recorded in
[8TF_COMPLETE_MIGRATION_PLAN.md](8TF_COMPLETE_MIGRATION_PLAN.md).

The migration must not merge two competing agent cores, routers, memories,
permission systems, browser authorities, voice listeners, TTS providers, or
event loops. Qubit’s broker, grants, confirmations, audit, emergency stop,
state, release, and privacy contracts remain authoritative. The source,
license, dependency, and migration runbook is [OPERATIONS_8T_F.md].

Required outcomes:

- fresh official Hermes source is pinned, audited, and reproducible;
- the resulting Qubit worktree actually contains or directly imports the
  selected pinned Hermes implementation, with runtime identity and source
  paths proven by tests;
- product identity is Qubit Agent and voice subsystem identity is Qubit
  VoiceBridge;
- microphone, ASR, Piper/TTS, Right Ctrl, dashboard voice, cancellation, and
  barge-in use one Qubit voice/session path;
- Hermes duplicate voice/listener paths are disabled or routed through Qubit;
- every requested OpenClaw capability in the current foundation scope is
  integrated through Qubit contracts;
- every OpenClaw row marked REUSE or ADAPT is implemented in the new Qubit
  tree; a blocked row is allowed only for a genuine operator-only gate with a
  complete hermetic path, typed state, test evidence, and manual procedure;
- dashboard, CLI, installer, config, state, notices, and documentation use
  the canonical identity without losing legacy compatibility;
- user-facing branding, visual design, typography, colors, route/component
  organization, and navigation are Qubit-native, with upstream names limited
  to legally required attribution and technical provenance;
- clean install, upgrade, rollback, state migration, licensing, and package
  checks pass before Packet 8U begins;
- the final full-parity contract is tracked separately and cannot be declared
  complete until the cumulative Hermes/OpenClaw CLI, dashboard, and voice gates
  for 8T-F through 8AH pass.

No 8U browser workspace, desktop-app, file, IDE, or new whole-computer
capability may be implemented inside 8T-F. Packet 8U begins only after an
independent 8T-F verification result.

The following are explicit 8T-F rejection conditions: a Qubit worker that
only imitates Hermes CLI/JSON behavior; a temporary Hermes installation that
is not part of the resulting Qubit foundation; an unverifiable Hermes runtime
identity; or a report that counts external-only OpenClaw features as
integrated.

The acceptance target is one authenticated session and policy path across
voice, dashboard, CLI, browser, desktop apps, approved file roots, and IDEs.
Browser activation must be available through an approved Comet/Chromium
browser toggle or in-page bridge, while voice activation may use “Hey Qubit”
or Right Ctrl; all
of these controls must operate the same backend session and never create a
second listener, voice provider, or permission authority.
Each adapter must be disabled by default, scoped to a user/session/target,
resource-bounded, auditable, revocable, and independently rollbackable. Page,
model, tool, repository, and clipboard text is untrusted and cannot create or
widen grants. Login, CAPTCHA, 2FA, recovery challenges, and other human
identity proofs remain manual handoffs. Routine read/navigation/fill/draft
work may use a narrow expiring owner grant; sending, publishing, purchasing,
deleting, account changes, and project-code changes require fresh exact
confirmation. The detailed implementation and verification prompts live in
`docs/AGENT_PROMPT_PACK.md`.

Required product outcomes:

- 8U provides a generic browser-context bridge and voice interaction for the
  active page, selected text, readable DOM/accessibility content, visible
  state, tabs, and bounded screenshots through existing Chromium/CDP or an
  approved in-page bridge. Ordinary webpage reading must not require a custom
  adapter for every site. It must not clone cookies/profiles or create a
  hidden browser listener.
- 8V provides semantic control of declared desktop apps on supported
  X11/Wayland sessions, with explicit degraded behavior when accessibility
  APIs are unavailable and no global keylogger or arbitrary input injector.
- 8W provides an approved-root file workspace with safe previews, diffs,
  snapshots, restore, quotas, symlink/path traversal protection, and no
  whole-home default.
- 8X provides a bounded IDE workspace and supervised OpenCode integration,
  with disposable worktrees, diff/hash confirmation, and no automatic
  commit/push/publish/install/service or permission changes.
- 8Y provides one bounded, provenance-labelled task context across interfaces
  without a second memory, router, scheduler, or audit authority.
- 8Z provides a local, provenance/licence-aware adapter catalog and lifecycle;
  it is not an uncontrolled plugin marketplace.
- 8AA provides explicit always-on mode with wake-word/PTT choice, visible
  privacy state, bounded context refresh, mute/pause, and no raw-audio
  retention by default. “Hey Qubit,” Right Ctrl, the dashboard, and the
  browser toggle all arm/pause the same service and session.
- 8AB provides separately qualified Firefox browser-context support with the
  same generic read contract; Chromium and Firefox sessions, profiles,
  accounts, origins, and grants remain isolated.
- 8AC provides the reusable adapter contract and fixture harness. It does not
  make unsupported websites or applications work automatically.
- 8AD provides individually qualified website action packs for operations that
  generic page reading cannot safely perform, such as unusual controls,
  account workflows, uploads, sends, publishing, and marketplace actions.
  Each site family has its own origins, selectors, capabilities, fixtures,
  live canary, and rollback. A site pack is not required merely to summarize
  an ordinary readable webpage.
- 8AE provides individually qualified desktop-application packs using
  semantic accessibility APIs where available, with honest degraded states.
- 8AF provides bounded process/resource awareness and coexistence while users
  run demanding software such as games; it does not add arbitrary process
  control, keylogging, or silent screen capture.
- 8AG provides a truthful coverage matrix and production qualification record;
  it never generalizes one website or application result to all others.
- 8AH provides one Qubit dashboard based on the vendored pinned Hermes
  frontend that consolidates chat, voice, browser,
  desktop, files, IDE/OpenCode, sessions, tools, approvals, providers,
  diagnostics, setup, upgrades, and audit without creating a second backend
  authority. The Hermes source is copied first as the baseline; OpenClaw and
  Qubit additions are explicit overlays with exact source, license, dependency,
  and provenance records. Rebranding occurs only after functional parity is
  proven.

Generic webpage readability is not universal control: login walls, CAPTCHA,
2FA, cross-origin restrictions, canvas-only content, unsupported browsers,
dynamic controls, and site-specific mutations may remain unavailable or
require a manual handoff. No packet may claim “full access” merely because a
capability is detected or a frontend toggle exists. Whole-computer readiness requires independent packet
verification plus operator-authorized live hardware, browser/session, desktop,
and disposable-account canaries where applicable. “Every website and app” is
an expanding set of separately qualified adapters, not one universal switch.
Dashboard feature parity is also qualified against the actual backend; a
frontend control is never proof that its capability is installed, enabled, or
live.

## 7. Performance and reliability targets

Targets must be measured on the supported PC, not guessed:

- fast local response: record warm and cold latency separately;
- streaming: first spoken sentence before the complete model response;
- bounded context: never exceed model context or output reserve;
- retrieval: deadline and worker count are explicit;
- background work: must not starve microphone, TTS, or interactive requests;
- memory: monitor RSS, VRAM, CPU, disk growth, and model residency;
- failures: typed, observable, retryable only when idempotent;
- cancellation: user stop reaches every child worker;
- no silent fallback from Qubit to a cloud provider.

“Faster” is accepted only when a benchmark compares the old and new path on
the same machine, model, input, and workload.

## 8. Strict agent rules

Every implementation agent must follow these rules before each new task:

1. Read `AGENTS.md`, this plan, the relevant packet, and relevant source docs.
2. Run `graft map` for unfamiliar work and `graft ask ... --source` before
   searching or opening source files.
3. Inspect the existing implementation before designing a new one.
4. Check upstream source, pinned commit, license, and notices before reuse.
5. Copy the smallest proven upstream implementation; do not over-engineer a
   replacement when a compatible implementation already exists.
6. If direct reuse is unsuitable, translate or rewrite behind the same Qubit
   contract and document the behavior comparison and provenance.
7. Treat every imported skill as untrusted until manifest, license, tool,
   permission, path, network, and workspace validation passes.
8. Never create a second router, memory store, permission system, scheduler,
   session identity, or audit format without written approval.
9. Do not place secrets in code, prompts, tests, screenshots, reports, or logs.
10. Do not give an LLM unrestricted shell, browser, filesystem, or network
   access.
11. Separate read, write, send, delete, and destructive capabilities.
12. Add tests before claiming completion: unit, contract, negative/security,
    integration, and real live tests where the packet requires them.
13. Never convert a failing live test into a skip just to obtain a green run.
14. Record exact commands, outputs, environment, model, commit, and limitations.
15. Update documentation and migration/rollback notes with every behavior change.
16. Run the full relevant suite after changes and inspect warnings and logs.
17. Do not say “done,” “working,” or “production-ready” while failures,
    unexpected skips, untested paths, runtime errors, or stale processes remain.
18. Stop and report a blocker when a required permission, source, license, or
    user decision is missing.

## 9. Verification packet format

Every phase handoff must include:

```text
PHASE: Qx
STATUS: VERIFIED / VERIFIED WITH LIMITATION / NOT VERIFIED

SOURCE:
- files and exact symbols changed
- upstream repository and pinned commit, if reused
- license/notice status

TESTS:
- exact commands
- passed / failed / skipped counts
- warnings and live-environment requirements

RUNTIME:
- process ID and start time
- active model/backend
- real smoke requests
- logs and error counts

SECURITY:
- permissions tested
- secrets/redaction tested
- injection/path/SSRF/destructive checks

ROLLBACK:
- exact command or flag
- data backup and restore status

LIMITATIONS:
- what is not proven
- what remains optional or placeholder

NEXT ACTION:
- one proposed next phase, waiting for explicit approval
```

## 10. Standard implementation prompt

```text
Work only on Phase Q[NUMBER] of docs/QUBIT_UNIFIED_AGENT_PLAN.md.

Before coding, read AGENTS.md, this plan, the relevant current Voice Bridge
docs, and the upstream source/license for every reused implementation. Use
graft first. Do not duplicate an existing router, memory, scheduler, session,
permission, or audit system. Reuse the smallest compatible upstream code and
record repository, commit, license, notices, and local modifications.

Implement only the stated phase scope. Keep all new behavior behind a safe
flag or compatibility adapter until verification passes. Preserve rollback.
Never expose secrets or give the model unrestricted computer access.

Add unit, contract, negative/security, integration, and required real live
tests. Do not hide failures as skips. Update documentation, migration notes,
third-party notices, and rollback instructions.

At the end, report the exact files, commands, results, warnings, runtime
process/backend, limitations, and rollback. Do not say done unless every
acceptance criterion is evidenced.
Stop for independent verification; do not start the next phase automatically.
```

## 11. Standard independent verification prompt

```text
Independently verify Phase Q[NUMBER] from
docs/QUBIT_UNIFIED_AGENT_PLAN.md. Do not trust the implementation agent’s
summary.

Use graft before source inspection. Check the actual diff, upstream provenance,
license notices, configuration, active process, model/backend, permissions,
logs, and rollback. Run the dedicated tests, negative/security tests, required
live smoke tests, and the relevant full suite. Reconcile every skipped test and
warning. Test the real runtime, not only mocks.

Return exactly:
- VERIFIED, VERIFIED WITH LIMITATION, or NOT VERIFIED;
- evidence with commands and counts;
- failures, skips, stale-process or configuration problems;
- security and data-privacy findings;
- rollback result;
- the smallest required correction before the next phase.

Do not promote a backend, enable a canary, or claim production readiness.
```

## 12. End state

The desired final product is not “Voice Bridge plus two copied agents.” It is
one Qubit system with:

- one user interface;
- one control plane;
- one model/provider gateway;
- one memory and learning policy;
- one permission broker;
- one capability registry;
- one worker/task protocol;
- one scheduler;
- one report and audit format;
- replaceable local, OpenClaw, Hermes, and big-LLM backends during migration.

At the end, OpenClaw and Hermes may be removed because Qubit owns the required
capabilities. Until Qubit proves parity in real tests, they remain valuable
fallbacks and sources of proven implementations.
