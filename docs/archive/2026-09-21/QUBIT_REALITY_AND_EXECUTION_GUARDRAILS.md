# Qubit Reality Check and Execution Guardrails

Status: authoritative agent handoff
Updated: 2026-09-17
Scope: local Qubit product parity with Hermes and OpenClaw

## Purpose

This document exists to prevent future agents from confusing source-vendor parity,
route presence, catalog entries, or focused tests with a working product.

Do not claim Qubit is complete, 1:1, or verified until every requirement in this
document and the frozen acceptance documents is actually proven.

## Honest current state

Hermes works because it is one coherent application: its frontend, backend,
agent loop, stores, routes, controls, and runtime were designed and tested
together.

Qubit currently combines several partially connected layers:

```text
Hermes source
  + Hermes-compatible frontend
  + Qubit overlay plugins
  + hermes_compat handlers
  + Hermes ASGI/proxy layer
  + native Qubit chat
  + multiple historical stores/brokers
  = pages that can render while controls or workflows still fail
```

The current state is incomplete. The earlier "code-ready" reports were narrower
milestones; they did not prove product-wide parity.

The latest independent differential evidence recorded:

- 15,421 inventory rows; only 19 genuine PASS rows.
- 14,849 rows OPEN in the newest scorecard.
- 31 dashboard routes and 633 interactive controls.
- Only 4 controls reached a real request and observed canonical result.
- 513 controls were not exercised.
- 684 UI inventory rows remained OPEN.
- CLI, dashboard, voice, and provider tri-parity was not demonstrated.
- Completion conditions 3, 4, 5, and 10 were NOT MET; the other conditions were
  PARTIAL.

These numbers are evidence gaps as well as implementation findings: an OPEN row
does not always mean a completely absent feature, but it does mean the feature
must not be reported as working or complete.

The relevant evidence is under `/tmp/qubit-differential/`, especially:

- `evidence/COMPLETION_GATE_SCORECARD.md`
- `closure/CONSOLIDATED_CLOSURE_BACKLOG.md`
- `ui/QUBIT_UI_INVENTORY.json`

## What byte parity does and does not prove

The Hermes byte manifest proves that the vendored Hermes files match the selected
upstream baseline, apart from explicitly explained line-ending differences. It
does **not** prove that Qubit:

- serves every Hermes route;
- exposes every Hermes page and control;
- connects each control to a real backend operation;
- uses the correct state store;
- preserves every stream, error, retry, approval, or recovery path;
- provides equivalent CLI, dashboard, and voice behavior.

Never use "the source is byte-identical" as proof that the Qubit integration is
functionally complete.

## Source-level rebrand and future-upstream model

The shipped runtime must be a Qubit-maintained source-level rebrand of the real
Hermes implementation, with Qubit policy, provider, VoiceBridge, and OpenClaw
adapters. It must preserve real behavior and must not be replaced with thin
compatibility stubs. Hermes/OpenClaw names must be absent from executable
product code and user-facing output.

Keep separate pristine, read-only upstream baselines for byte comparison,
licensing, provenance, and upgrades. Once source names and identifiers are
rebranded, the Qubit fork cannot itself be byte-identical; prove parity through
a deterministic rename/patch manifest plus behavioral and API comparisons.

On every upstream refresh, re-inventory and integrate new skills, tools, hooks,
permissions, schemas, configuration, events, and workflows. Future Hermes and
OpenClaw skills must be loaded through Qubit’s loader, provider resolver,
broker, policy, audit, stores, CLI, dashboard, and VoiceBridge. Never assume a
future capability works because its source files exist.

## Required chat architecture

There are two intentional chat views with one shared functional authority. Keep
the working `/qubit-chat` page intact while rebuilding `/chat` as the Hermes
terminal-style view. Both routes must use the same real backend, voice path,
session state, and provider path.

### `/chat` — canonical Qubit chat with Hermes terminal UX

`/chat` is the Hermes-style terminal chat page, fully rebranded as Qubit. It
must use the Hermes terminal chat as its visual and interaction reference:
terminal information architecture, transcript and
prompt, streaming output, tool/skill activity, model/provider and reasoning
controls, session sidebar/history, reconnect/retry/cancel, approvals, files,
MCP, voice input/output, loading, empty, error, and recovery states.

The result must be fully rebranded as Qubit and execute through Qubit’s
authentication, provider resolver, Brain4/API routing, broker, policy, audit,
VoiceBridge, session store, and e-stop. Raw PTY may remain unavailable only
where Qubit policy requires it; the supported Qubit replacement must be
functional and precisely tested.

### `/qubit-chat` — working native Qubit Chat view

`/qubit-chat` is the current working native Qubit Chat page. Preserve its
existing visual design and working voice/chat behavior; do not replace it with
the Hermes terminal layout. It must still use the same real backend, sessions,
messages, providers, streaming, tools, policy, audit, and VoiceBridge as
`/chat`. It must not be a demo, empty stub, or separate authority. The two
views may differ visually, but supported chat behavior and state semantics must
remain equivalent. No `/hermes/...` product route may remain.

## Root causes that must be fixed

1. **Rendered route mistaken for working feature.** HTTP 200, a non-empty page,
   a catalog row, or a button that opens a modal is not proof of functionality.

2. **Competing dispatch authorities.** `voicebridge/webui.py`,
   `voicebridge/hermes_compat.py`, `voicebridge/hermes_asgi.py`, plugins, and
   direct Qubit handlers must have one explicit ownership model. A route must not
   be silently shadowed by the fork or by another compatibility handler.

3. **Duplicate state authorities.** Sessions, boards, runtime homes, receipts,
   confirmations, logs, memory, and scheduled work must each have one writable
   authority. Legacy adapters may read or migrate data, but must not create a
   second writer.

4. **Controls without production callers.** Every dashboard control, CLI command,
   and voice command must reach a canonical operation and produce a real result,
   state change, event, or typed refusal.

5. **Conflicting wire contracts.** For example, the source currently contains
   different `/api/logs` response paths (`{file, lines}` in the compatibility
   handler and `{items}` in another WebUI handler). Establish one authenticated
   contract and make the frontend, CLI, voice, and tests use it.

6. **Insufficient verification scope.** Runner self-tests prove the runner, not
   the product. Focused backend tests do not prove every page, modal, form,
   keyboard action, responsive state, accessibility state, stream, or recovery
   path.

7. **Intentional refusals mixed with missing work.** A safe refusal is valid only
   when the upstream behavior is unsafe or operator-only, the refusal is typed,
   the exact prerequisite/counterpart is shown, and the refusal itself is tested
   through CLI, dashboard, and voice. A safe capability must not be relabeled as
   a refusal merely to close a ledger row.

8. **Pages without the underlying Hermes system.** Skills, sessions, history,
   and APIs must be ported as real behavior. This includes skill loading,
   dependencies, permissions, execution, results, errors, and recovery; session
   creation/resume/list/load/rename/archive/delete/search, messages, transcripts,
   metadata, sharing, attachments, persistence, migration, and restart recovery;
   and every API route, schema, auth scope, status, stream/event, error, retry,
   cancellation, approval, audit, and recovery contract. A page backed by `{}` or
   `[]`, a catalog-only skill, or an unconnected button is not implemented.

9. **New empty state mistaken for a migration.** Existing Hermes local data must
   be imported into Qubit. Inventory and migrate old sessions, messages,
   transcripts, attachments, metadata, profiles, model/provider settings, API
   configuration, skills, plugins, schedules, and supported preferences from
   the real Hermes data home. The migration must be idempotent, restart-safe,
   recoverable, and validated by counts, IDs, ordering, relationships, and
   reopening old sessions through Qubit’s dashboard, CLI, and voice surfaces.
   Secret values must move only through secret references or the approved local
   secret store; never copy plaintext keys into evidence or source. Qubit must
   stop depending on a separate live Hermes home after migration.

## Definition of a working capability

For every Hermes, OpenClaw, and Qubit capability, prove this complete chain:

```text
selector/input
  -> request validation
  -> canonical operation
  -> broker/policy/autonomy/e-stop
  -> runtime/provider/tool
  -> persistent state
  -> result/event/stream
  -> rendered UI or CLI/voice response
  -> error, retry, cancellation, recovery, and audit receipt
```

The capability is not complete if any link is a mock, placeholder, dead handler,
wrong store, uncalled helper, hard-coded model, fake success, stale route, or
untested refusal.

## Mandatory execution order for agents

### Phase 1 — Read and preserve contracts

Read before editing:

- `docs/QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md`
- `docs/QUBIT_FULL_PARITY_CONTRACT.md`
- `docs/QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md`
- `docs/QUBIT_NATIVE_COMPLETION_CHECKLIST.md`
- terminal-status owner amendments
- the current gap ledger and latest parity scorecard
- this document

Do not edit frozen acceptance criteria, safety policy, terminal statuses, required
order, or completion wording. Do not delete provenance, licenses, manifests, or
upstream references to make the result look cleaner.

### Phase 2 — Inventory before implementation

Build or refresh inventories directly from the pinned Hermes and OpenClaw trees
and the current Qubit tree. Include every:

- source file, module, class, function, command, option, route, RPC, event,
  WebSocket message, schema, migration, config key, environment variable,
  provider, model, tool, skill, plugin, hook, scheduler, job, permission,
  scope, error, retry, rollback, and recovery behavior;
- frontend route, page, layout, component, form, button, menu, modal, keyboard
  action, asset, stylesheet, font, loading state, empty state, error state,
  responsive state, and accessibility behavior;
- Qubit adapter, registry row, CLI command, dashboard operation, voice utterance,
  backend operation, store, test, and evidence record.

Every row must have exactly one disposition: implemented and executed,
safe-equivalent with a real typed proof, operator-only with an exact runbook, or
open. Never infer completion from an older matrix.

### Phase 3 — Establish one authority

Before adding breadth:

- make Qubit the only live front door;
- make the Qubit compatibility handler authoritative where Qubit implements or
  gates a route;
- prevent fork/proxy shadowing;
- unify state homes and writable stores;
- unify operation, confirmation, policy, autonomy, audit, and e-stop paths;
- route chat, planner, classifier, summarizer, vision, STT, TTS, realtime,
  scheduled work, plugins, browser, desktop, messaging, CLI, dashboard, and
  voice through the canonical provider/broker architecture;
- remove or explicitly quarantine duplicate listeners and competing services.

### Phase 4 — Implement real pages and controls

For each page, compare Hermes and Qubit side by side using DOM/accessibility
snapshots and screenshots. Match structure, navigation, typography, spacing,
dimensions, breakpoints, loading/empty/error states, copy, keyboard behavior,
forms, menus, modals, and responsive behavior.

Then exercise every control. Record:

- the exact request and authenticated route;
- the canonical operation and broker decision;
- the state before and after;
- emitted events/stream frames;
- rendered result;
- error/retry/cancel/recovery behavior;
- CLI and natural voice equivalents;
- proof artifact and test ID.

Do not leave fantasy buttons, decorative status tiles, fake tables, empty
catalogs, unconnected forms, or pages that only return a successful HTTP status.

### Phase 5 — Provider and voice parity

Keep Qubit-Brain4 replaceable. Support explicit local, API, hosted, speech, and
realtime provider profiles, including OpenRouter/OpenCode-compatible API
configuration where supported. Preserve explicit user selection; automatic
Brain4-first escalation may route harder tasks only when policy allows it and must
record provider, model, reason, fallback chain, and data-egress decision.

Voice must use the same canonical operation as CLI and dashboard. Test browser
speech input, local STT, API/remote voice, barge-in, streaming, interruption,
fallback, permissions, and typed operator-only cases. A voice registry entry by
itself is not voice support.

### Phase 6 — Verification only after implementation closure

Do not spend the work phase repeatedly rerunning the full suite after every small
edit. First close the implementation inventory by area. During implementation,
run only small targeted checks needed to avoid breaking the current work.

After all implementation work is complete, run one final verification wave:

- byte manifests and provenance for contractual and latest upstream baselines;
- complete capability and UI inventories with zero unexplained omissions;
- every page and every interactive control in Playwright;
- CLI/dashboard/voice tri-parity with shared state and policy;
- real provider/Brain4/API/speech/realtime switching;
- streaming, errors, approvals, retries, cancellation, recovery, and e-stop;
- full tests with warnings-as-errors;
- frontend typecheck, unit tests, lint, and production build;
- packaging, isolated clean install, migration, rollback, uninstall, and restart;
- dead-code, stale-config, duplicate-authority, secret, accessibility, and
  responsive audits;
- independent read-only verification that reproduces the evidence.

The final verifier must fail on any unexecuted open row, untested control,
shadowed route, conflicting wire shape, fabricated proof, or unexplained
disposition.

## Agent reporting rules

Every agent report must state:

- exact files changed and owned;
- exact upstream revision used;
- exact capabilities implemented;
- real production caller and route for each capability;
- tests and live controls actually executed;
- remaining open rows and why they remain open;
- operator-only prerequisites separately;
- known conflicts with other agents;
- rollback instructions;
- whether the result is `INCOMPLETE`, `CODE-READY`, or `VERIFIED`.

The words `100%`, `complete`, `1:1`, and `VERIFIED` are forbidden unless the
corresponding final gates actually pass. "Route loads", "catalog contains it",
"focused tests pass", and "source was copied" are not completion evidence.

## Safe concurrency rules

- Assign exclusive file ownership before deploying agents.
- Do not let agents edit frozen plans or each other's owned files.
- Reconcile all handoffs before claiming a coherent state.
- Never use `git reset --hard`, broad checkout, broad clean, or whole-file
  formatting to resolve conflicts.
- Preserve unrelated dirty work and untracked product files.
- Do not commit or push until the final independent verifier passes and the owner
  explicitly requests commit/push.

## Required final verdict

The final verdict must answer these questions with evidence, not percentages:

1. Does `/chat` match Hermes terminal UI and workflows while remaining Qubit-
   branded and Qubit-authoritative?
2. Does `/qubit-chat` remain the working native Qubit chat surface?
3. Does every page control perform a real operation or show a precise typed
   operator-only/safe-equivalent result?
4. Do CLI, dashboard, voice, scheduler, plugins, browser, desktop, and messaging
   use the same operation, state, policy, audit, and e-stop authorities?
5. Can Qubit-Brain4 route to configured APIs for eligible hard tasks without
   breaking session, tool, privacy, provider, or audit continuity?
6. Are all Hermes/OpenClaw capabilities inventoried against the latest audited
   revisions and either executed, safely disposed, operator-qualified, or still
   openly listed?
7. Did an independent verifier reproduce the result from a clean, documented
   state?

If any answer is unknown, the verdict is `INCOMPLETE`.
