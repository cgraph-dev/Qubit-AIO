# Qubit Post-Parity Pizza Bot and OpenViking Integration Plan

**Status:** deferred until Qubit parity and voice actionability are complete  
**Scope:** future optional integrations; not current parity blockers  
**Prerequisite:** Qubit must first reach independent `VERIFIED` status for the supported Hermes and OpenClaw capability surfaces.

## Purpose

After Qubit has complete text, CLI, dashboard, and voice capability coverage,
evaluate two optional integrations:

1. Pizza Bot as an asynchronous inbox and long-running-agent workflow layer.
2. OpenViking as an optional context, memory, knowledge, and skills layer.

Neither project replaces Qubit’s unified runtime, broker, policy, audit journal,
e-stop, session authority, or Qubit-branded UI. Both must be integrated through
Qubit adapters and the existing canonical operations.

## Hard prerequisite gate

Do not begin implementation until all of the following are true:

- Hermes parity is independently verified;
- contractual OpenClaw capabilities are independently verified;
- every supported capability has a text and voice path;
- `/chat` terminal behavior is verified;
- `/qubit-chat` Brain4/local/API routing is verified;
- Right-Ctrl browser/local STT switching is verified;
- all dashboard controls are verified end-to-end;
- migration, packaging, clean install, rollback, and recovery are verified;
- public Qubit branding is clean;
- no code-side `OPEN` rows remain;
- an independent read-only verifier has passed;
- licensing and provenance review approves each integration.

“100% ready” means all required code-side and behavioral gates are verified.
Operator-only prerequisites must be explicitly listed rather than silently
treated as complete.

## Source projects

### Pizza Bot

Repository: <https://github.com/pizza-bot-app/pizza-bot>  
License reported by the repository: Apache-2.0.

Pizza Bot provides a local-first inbox for long-running AI work. Relevant areas
for Qubit evaluation include:

- asynchronous runs that continue after a client disconnects;
- Unread and Action queues;
- durable human approval requests;
- conversation folders;
- checkpointed runs and resume behavior;
- cron and webhook triggers;
- specialist/subagent delegation;
- model-provider adapters;
- file permissions and explicit folder grants;
- MCP servers, skills, and plugins;
- HTTP/SSE client-server transport;
- desktop, browser, and CLI surfaces.

These features must be compared against Qubit’s existing sessions, operations,
cron, webhooks, approvals, skills, plugins, MCP, files, providers, and voice
contracts before any code is copied or adapted.

### OpenViking

Repository: <https://github.com/volcengine/OpenViking>  
License reported by the repository: AGPL-3.0.

OpenViking provides a context database for AI agents. Relevant areas for Qubit
evaluation include:

- a `viking://` virtual filesystem for resources, memories, and skills;
- hierarchical context layers and on-demand loading;
- directory summaries and progressive context expansion;
- vector and directory-structured retrieval;
- persistent user memory and reusable agent experience;
- SDK, CLI, server, and web-studio integration surfaces.

The AGPL-3.0 license requires a dedicated legal and distribution review before
embedding, linking, modifying, or shipping OpenViking as part of Qubit. An
optional separately deployed service or a different compatible implementation
must be evaluated if direct integration is not acceptable.

## Integration principles

- Qubit remains one unified agent and one source tree.
- Neither project may introduce a second agent authority or bypass Qubit policy.
- Qubit’s canonical session/history store remains authoritative for chats.
- Qubit’s broker, confirmation journal, audit journal, and e-stop remain
  authoritative for every action.
- External data stores require an explicit ownership boundary, migration plan,
  backup/rollback plan, permission model, and failure behavior.
- No public Pizza Bot, OpenViking, Hermes, Nous, or OpenClaw branding may leak
  into Qubit’s normal UI, CLI, logs, errors, banners, bundles, or voice output.
- Provider, model, memory, skills, plugins, and context retrieval remain
  replaceable through Qubit registries and adapters.
- No automatic import of user data, memories, skills, plugins, or credentials.
  Import must be opt-in, previewable, reversible, and audited.

## Required future voice coverage

Every integrated capability must be actionable through `/qubit-chat` voice and
must use the same canonical operation as text, CLI, and dashboard.

### Pizza Bot voice capabilities

Evaluate voice intents for:

- start a background task;
- schedule a task;
- list running, unread, action-required, and completed work;
- open a task or conversation;
- pause, resume, cancel, retry, or recover a run;
- approve or reject a consequential action;
- move or organize a conversation;
- assign or invoke a specialist;
- inspect activity and progress;
- configure providers, skills, plugins, MCP, and explicit file grants.

### OpenViking voice capabilities

Evaluate voice intents for:

- remember a fact or preference;
- search memory or knowledge;
- list, browse, read, and summarize context resources;
- retrieve only the context needed for a task;
- add, update, archive, or forget a memory;
- inspect context provenance and source;
- enable, disable, or select context/skill providers;
- report unavailable, unauthorized, stale, or ambiguous context.

Every mutating, privacy-sensitive, destructive, or externally visible voice
operation requires the same confirmation, authorization, audit, and e-stop
behavior as its CLI and dashboard equivalent.

## Staged implementation plan

### Stage 1 — source, license, and architecture audit

- Pin exact commits for both projects.
- Read source, schemas, storage, transports, providers, plugins, skills, and
  security documentation.
- Produce capability inventories and dependency/license reports.
- Compare each capability with existing Qubit operations.
- Decide whether each feature is an adapter, safe equivalent, optional service,
  or not suitable for Qubit.

### Stage 2 — isolated prototypes

- Run each project in an isolated data root and process boundary.
- Do not connect production Qubit state.
- Test provider switching, approvals, files, memory, skills, plugins, MCP,
  persistence, restart, rollback, and failure behavior.
- Measure latency, memory, storage, network traffic, and resource growth.

### Stage 3 — Qubit adapter layer

Implement only through canonical Qubit adapters. Each adapter must declare:

- capability and version;
- source commit and license;
- configuration schema;
- permissions and file/network scope;
- provider/model requirements;
- storage authority;
- migration and rollback behavior;
- CLI/dashboard operation;
- voice intents;
- confirmation and audit requirements;
- failure and unavailable behavior.

### Stage 4 — UI and voice integration

- Add Qubit-branded pages and controls only after backend operations work.
- Verify every control against real state transitions.
- Add voice intents and Right-Ctrl/browser/local STT coverage.
- Preserve `/chat` terminal behavior and `/qubit-chat` local/API routing.

### Stage 5 — data and memory migration

- Provide read-only inspection and migration previews.
- Import only with explicit operator approval.
- Preserve IDs, timestamps, provenance, permissions, and source references.
- Support idempotence, interruption recovery, backup, rollback, and deletion.
- Never import credentials or private data silently.

### Stage 6 — verification

Run the complete Qubit final matrix again, including:

- text, CLI, dashboard, and voice parity;
- provider/model/Brain4/API switching;
- approvals, policy, audit, and e-stop;
- async runs, checkpointing, memory, context retrieval, skills, plugins, and
  MCP;
- privacy, permission, data-boundary, secret, and licensing checks;
- packaging, clean install, migration, rollback, restart, and recovery;
- independent read-only verification.

## Completion rules

Do not claim either integration is complete because a package installs, a page
renders, a provider connects, or a voice intent exists. Each capability must
pass the full canonical behavioral chain:

```text
voice/text/control
→ authenticated request
→ canonical Qubit operation
→ policy/broker/confirmation/e-stop
→ real integrated service or adapter
→ persistent state/event
→ correct UI/CLI/voice result
→ error/retry/cancel/recovery/audit proof
```

Do not commit or ship an integration until the independent verifier passes and
the license/provenance review is complete.
