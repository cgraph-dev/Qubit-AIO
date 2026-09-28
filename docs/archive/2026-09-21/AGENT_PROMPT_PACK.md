# Agent Prompt Pack

Use this file with `docs/UNIFIED_AGENT_PLAN.md`.

The authoritative architecture for the current completion effort is
[`QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md`](QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md).
The complete orchestrator prompt is
[`QUBIT_FINAL_CODE_READY_PROMPT.md`](QUBIT_FINAL_CODE_READY_PROMPT.md).
For the direct byte-level upstream comparison, complete page/plugin inventory,
dead-code audit, and multi-agent repair wave, use
[`QUBIT_BYTE_PARITY_VERIFICATION_PROMPT.md`](QUBIT_BYTE_PARITY_VERIFICATION_PROMPT.md)
after the implementation base prompt. It is deliberately stricter than a
ledger-only verification and explicitly covers Hermes achievements and every
frontend workflow.
It records the owner-approved runtime decision: the complete pinned Hermes
source is the Qubit-owned runtime foundation; OpenClaw is integrated on top of
it; and CLI, dashboard, and VoiceBridge use the same real backend operations.
The gap ledger, not a narrow historical matrix, is the current status
authority. Do not call the work 1:1 while that ledger contains open code-side
rows.

### Current full-parity test cadence

For the master completion prompt, batch related implementation work and do not
repeat full pytest/UI/Playwright/upstream suites after every small edit. Use
static inspection and targeted compile/import checks while batching; run
focused and affected suites after each coherent implementation batch, then run
all final gates once the code is implementation-ready. A failure must be
repaired and rerun at the appropriate scope. This changes test timing only; it
does not waive any acceptance gate or permit weakened tests.

The workflow is always:

1. Copy the **Implementation Base Prompt** and the selected packet prompt into
   one implementation-agent message.
2. Wait for the implementation agent to stop with a handoff.
3. Give the **Verification Base Prompt** plus the same packet verification
   prompt to a different agent.
4. Do not start the next packet until the verifier returns `VERIFIED` or
   `VERIFIED WITH EXPLICIT LIMITATION` and the user approves it.

The current worktree is dirty. Every agent must preserve unrelated changes.
These prompts do not authorize live account access, service restarts, timer
enablement, model downloads, or live configuration edits unless the packet
explicitly says so and the user authorizes it.

Precedence clarification for the dashboard rebuild: older packet-specific
instructions that prohibit React/Node or require a Python/static-JS dashboard
do not apply to the owner-approved Hermes frontend vendoring work described in
the dated amendment. The amendment and the dashboard rebuild requirement are
the authority for that work. Do not use an older stack restriction as a reason
to avoid copying and serving the pinned Hermes frontend source.

## Implementation Base Prompt

```text
You are the implementation agent. Implement ONLY the packet named below.

Before any search, edit, model load/download, dependency install, browser
access, service operation, or state-changing command:

1. Read AGENTS.md completely.
2. Read docs/UNIFIED_AGENT_PLAN.md completely.
3. Read this prompt pack and the selected packet prompt completely.
4. Use Graft first: run graft map, then graft ask "<packet objective>" --source,
   and graft callers for every shared symbol before editing it.
5. Run git status --short and record every unrelated modified/untracked path.
6. Inspect the effective configuration and runtime state relevant to the packet.
   Never assume repository YAML, user YAML, systemd, Ollama, gateway, or
   Chromium agree.
7. Write a task capsule containing objective, non-goals, allowed files/symbols,
   callers, risks, tests, measurements, rollback, and definition of done.

Stay inside one lettered packet. Do not implement the next packet, do cleanup,
rename unrelated files, upgrade dependencies, or redesign the architecture.
Preserve the working HyperX audio setup. Do not edit live user configuration or
services unless this packet and the user explicitly authorize it.

For upstream work, inspect the exact pinned source and tests in section 4.1 of
the plan, compare current upstream only for compatibility, update
docs/UPSTREAM_REUSE.md before production code, preserve licenses, and record
every reused file, hash, patch, and reason. Do not use a floating branch.

Dashboard exception approved by
docs/QUBIT_OWNER_AMENDMENT_2026-09-11.md: the exact pinned Hermes frontend
source is the required dashboard baseline and may be vendored with its
React/Vite/Node build dependencies. Do not replace it with a Python/static-JS
lookalike. Keep the vendored source and Qubit integration/rebrand patches
separately auditable; preserve the Hermes license and attribution. This
exception applies to the dashboard only. All non-dashboard runtime, broker,
voice, security, and service packets retain the Qubit Python/static-JS/
systemd/Bubblewrap/Ollama stack and must not add a second runtime authority.

Outside the approved Hermes-runtime/cutover and dashboard exceptions, do not
add FastAPI, Uvicorn, React, Node, Docker, Kubernetes, another database,
another queue, another model server, or an undeclared dependency. The
Qubit-owned Hermes fork may use its pinned FastAPI/Uvicorn source in-process
behind the one existing Qubit listener; this does not authorize a second
server, listener, or policy authority.
Model output is never permission. Never execute generated shell directly,
expose secrets, bake private data into a model, or change per-call Ollama
runner options outside the authorized model packet.

Implement the smallest coherent change with typed boundaries, bounded I/O,
timeouts, cancellation, redacted logs, reversible migrations, documentation,
normal/negative/degraded/concurrency/security tests, and packet benchmarks.
For a multi-step packet, batch related implementation work before running
tests. Do not run the full suite, UI suite, Playwright suite, or expensive live
checks after every small edit. Once the batch is implementation-ready, run the
focused tests, affected regressions, and required final gates in the packet.
Inspect actual output. Run git diff --check. Rebuild Graft after substantial
source changes.

Before handoff, verify every new code path has a caller and a test. Never say
done, complete, fixed, or ready. End with the exact section 5.1 handoff
template, using INCOMPLETE, BLOCKED, DEGRADED, or FAILED. Include files,
symbols, commands, pass/fail/skip/degraded counts, measurements, security
checks, untested paths, risks, and rollback. Then STOP.
```

## Master completion prompt — current owner directive

Use this prompt when assigning the remaining full-parity work. It is an
execution prompt, not permission to change the frozen contract.

```text
You are responsible for completing Qubit against the owner-approved
QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md. Read that directive, the frozen
acceptance contract and amendments, the complete migration plan, the full
parity contract, the dashboard rebuild plan, the gap ledger, and this prompt
pack before editing.

The required architecture is fixed: vendor and execute the complete pinned
Hermes source from the Qubit-owned source/package tree; integrate OpenClaw
capabilities into that same foundation; integrate Qubit VoiceBridge; and make
CLI, dashboard, and voice call the same real backend operations. Do not build
another compatibility imitation, external-Hermes supervisor, or reduced
custom dashboard. The existing Qubit listener may invoke the fork in-process,
but there must be one listener, one backend/session/memory/scheduler, one
broker, one audit path, one emergency stop, and one resource policy.

Use docs/QUBIT_GAP_LEDGER.md as the status authority. Reconcile every row in
the pinned Hermes, pinned OpenClaw, and Qubit sections. A row is not complete
because a source file, matrix entry, status tile, route, alias, mock, or typed
refusal exists. Close it only with a real production caller, the required
CLI/dashboard/voice surface, and behavioral, negative, persistence, security,
resource, cancellation, recovery, and clean-install evidence. Replace
compatibility-only paths with the Qubit-owned Hermes backend as cutover
progresses. Do not hide ordinary implementation work under GATED, MANUAL, or
REJECTED.

Preserve the complete Hermes dashboard source and workflows first. Add the
OpenClaw workflows into that information architecture, then apply the Qubit
brand and visual system without removing controls or pages. Keep upstream
names only in legal, attribution, provenance, migration, compatibility, and
developer records. Do not copy secrets or private user data.

Work continuously through every code-side ledger row. Use disjoint subagents
only with explicit file ownership and integrate their work; do not stop after
one wave or after a green focused suite. Run focused tests after coherent
changes, affected regressions at each milestone, and the complete gates at the
end: full pytest with warnings as errors, UI/Playwright, CLI/dashboard/voice
parity, source/caller audit, provenance/license/dependency scans, compileall,
package and isolated clean-install, service/runtime identity, migration,
upgrade, rollback, uninstall, security, persistence, resource, and Graft
checks. Never weaken, skip, delete, or rewrite a test to make it pass.

Do not waste storage on repeated artifacts. Remove only disposable screenshots,
audio captures, pytest temp directories, logs, probe output, and throwaway
environments after recording their useful evidence. Preserve pinned upstream
clones until the final audit is complete, and never delete source, user state,
credentials, browser profiles, or provenance.

Preserve unrelated dirty/untracked files byte-for-byte. Never use reset --hard,
clean, broad checkout, mass formatting, or destructive deletion. Update only
source, tests, legal/provenance records, and append-only evidence. Do not edit
acceptance requirements to resolve an implementation conflict. If a true
owner decision is required, record the exact conflict and stop only there;
otherwise continue repairing the code.

Before claiming completion, regenerate the gap ledger and require zero
code-side rows with status other than IMPLEMENTED. Then obtain independent
verification. Only after the verifier reproduces all evidence may you report
VERIFIED, commit the complete intended change set, and provide exact rollback
instructions. Until then report INCOMPLETE with the exact remaining rows.
Do not start 8U or another packet while this gate is open.
```

## Verification Base Prompt

```text
You are the independent verification agent. Verify ONLY the selected packet.
Do not trust the implementation agent's summary. Do not implement repairs or
the next packet.

Perform a fresh preflight:

1. Read AGENTS.md completely.
2. Read docs/UNIFIED_AGENT_PLAN.md completely.
3. Read this prompt pack and the selected packet verification prompt.
4. Use Graft first, then inspect the complete diff and every affected caller.
5. Run git status --short and preserve unrelated user work.
6. Re-open every pinned upstream source/test used by this packet and verify
   commit, license, notices, dependencies, omitted behavior, and the 300-line
   architecture gate.
7. Rerun focused, negative, degraded, security, concurrency, clean-install,
   live, and benchmark gates applicable to the packet.
8. Inspect actual runtime/config/model/residency/resource state without
   printing secrets. A skipped live check is DEGRADED, never PASS.
9. Check documentation, migration, feature flags, rollback, Graft freshness,
   and whether any claim exceeds evidence.
10. Do not restart or modify live services, Chromium, personal accounts, or
    timers unless the packet and the user explicitly authorize it.

Return only section 5.2's verification result:

PHASE VERIFICATION: VERIFIED | REJECTED | VERIFIED WITH EXPLICIT LIMITATION
EVIDENCE:
FAILURES:
UNTESTED:
REQUIRED REPAIRS:
NEXT PHASE AUTHORIZATION: NOT GRANTED / READY FOR USER APPROVAL

The next packet remains NOT GRANTED unless every required gate passes and the
user approves it. Then STOP.
```

## Packet 0A — already implemented

### Verification prompt

```text
Verify ONLY Packet 0A. Confirm that docs/PACKET_0A_MISMATCH_REPORT.md and
docs/UPSTREAM_REUSE.md exist, are documentation-only artifacts, contain the
configuration/deployment/dependency/frontend/model-caller/language/private-
data/inbox/report/scheduling audits, classify evidence as IMPLEMENTED,
VERIFIED, DEGRADED, or PLANNED, and contain no private values. Confirm that
UPSTREAM_REUSE has 14 complete pinned records with source paths, tests or
explicitly unavailable tests, license, decision, local destination,
dependencies, kept/omitted behavior, smallest-fit reason, and compatibility
drift. Confirm Packet 0A did not authorize runtime fixes. Mark skipped live
checks and the unavailable Letta pin as explicit limitations. Do not authorize
0B until the evidence and rollback are accepted.
```

## Packet 0B — source-of-truth implementation

### Implementation prompt

```text
Implement ONLY Packet 0B: implement static/runtime config doctor modes, fix
the unambiguous environment-override path encoding, make dashboard frontend
lookup configured/repository-relative, add deployment/dependency contract
checks, reconcile portable examples/docs, and add safe migrations/tests based
on the Packet 0A mismatch report. Preserve the live user config, HyperX audio,
installed service, model aliases, and existing runtime behavior. Static doctor
must run without live services; runtime doctor must distinguish missing,
configured, healthy, active, resident, and degraded. Do not implement Phase 1
security policy, model runner unification, memory migration, or reports.
```

### Verification prompt

```text
Verify ONLY Packet 0B. Prove static doctor works with Ollama/audio/gateway/
Chromium/systemd absent; runtime doctor reports typed degraded states and
never prints secrets; double-underscore or equivalent overrides reach keys with
underscores; frontend paths have no hardcoded sibling home path; install and
systemd templates preserve machine overrides and do not overwrite live units;
fresh-install dependency checks catch undeclared imports; stale docs/config
claims are reconciled; migration and rollback are tested. Reject live config
overwrites, silent service restarts, or Phase 1/4/5 scope creep.
```

## Packet 1A — capability policy core

### Implementation prompt

```text
Implement ONLY Packet 1A: add the typed capability registry, side-effect tiers,
scope metadata, confirmation records with expiry and one-use IDs, policy
decisions, centralized redaction, and pure unit/negative tests. Keep this packet
framework-free and do not wire every legacy dispatch path yet; Packet 1B owns
integration. A model proposal cannot grant permission. Denied, malformed,
expired, replayed, out-of-scope, financial, and destructive requests must fail
closed. Do not change browser monitoring or model selection.
```

### Verification prompt

```text
Verify ONLY Packet 1A. Prove every capability has tier/scope/timeout metadata;
approval IDs are exact, expiring, single-use, and target-bound; denial cannot
fall back to shell; redaction removes secrets at journal/prompt/report
boundaries; malformed and prompt-injected proposals fail closed; concurrency
and replay tests pass; safe read-only actions remain usable. Confirm no legacy
dispatch or dashboard integration was falsely claimed.
```

## Packet 1B — dispatch, dashboard security, and HITL

### Implementation prompt

```text
Implement ONLY Packet 1B: put every OpenClaw/Hermes dispatch path behind the
temporary deny/confirm adapter, secure dashboard defaults with loopback/header
token/origin restrictions, and adapt the pinned CopilotKit HITL state behavior
into the existing static UI and durable Python state without importing React or
CopilotKit. Add bypass, reconnect, abort, settle-once, authentication, CORS,
and redaction tests. Carry the Packet 1A integration limitation (DEFERRED-003):
wire voicebridge.capabilities + voicebridge.redaction into every connected
dispatch path with per-path bypass tests proving no Decision-free execution,
user-only confirmation supply/consumption, and redacted boundaries. Do not
remove the adapter until negative tests prove every direct/fallback/retry/
legacy path is covered. Do not implement Phase 3 broker.
```

### Verification prompt

```text
Verify ONLY Packet 1B. Exercise every OpenClaw/Hermes dispatch entry and prove
the adapter cannot be bypassed. Prove LAN APIs reject empty/query-string tokens,
only configured origins pass, secrets are redacted, approvals survive abort
and reconnect, and HITL settles once. Compare the pinned CopilotKit source/tests
and verify no React/runtime/framework was added. Reject any unrestricted worker
path or dashboard security downgrade.
```

## Packet 2A — event contract and journal

### Implementation prompt

```text
Implement ONLY Packet 2A: adapt the pinned AG-UI-compatible event enums,
camelCase/null-omission wire contract, SSE framing fixtures, and append-only
ordered journal using the existing Python/static-JS stack. Preserve versioned
run_id/parent_id/seq/schema/redaction fields. Use RFC 6902 state-delta semantics
against a snapshot baseline. Add pinned conformance fixtures and local
divergence tests. Do not implement SSE replay, dashboard consumption, or
restart-safe approvals; Packet 2B owns those.
```

### Verification prompt

```text
Verify ONLY Packet 2A. Compare local events and encoding with the pinned AG-UI
source/tests. Prove camelCase, null omission, SSE framing, sequence ordering,
terminal events, snapshot/delta semantics, redaction, malformed input, and
append-only recovery. Confirm no pydantic/FastAPI/React dependency and no
Packet 2B behavior was claimed.
```

## Packet 2B — replay, snapshots, approvals, and cancellation

### Implementation prompt

```text
Implement ONLY Packet 2B: add SSE replay with Last-Event-ID/sequence recovery,
snapshots and ordered RFC 6902 deltas, dashboard event consumption, and
restart-safe approvals/cancellation on the Packet 2A contract. Preserve JSON
polling only as compatibility fallback. Add concurrent-request isolation,
reconnect gap, duplicate, stale-base, cancellation, and restart tests. Do not
add another server or frontend framework.
```

### Verification prompt

```text
Verify ONLY Packet 2B. Prove reconnects have no gaps/duplicates, stale or
missing delta bases request snapshots, concurrent runs cannot exchange events,
approvals survive restart, cancellation emits one terminal event, and the
static dashboard consumes the contract. Reject polling-only behavior,
ambiguous terminal state, or a second HTTP server.
```

## Packet 3A — trusted capability broker

### Implementation prompt

```text
Implement ONLY Packet 3A: place existing trusted host tools and OpenClaw/Hermes
worker adapters behind the capability registry; add structured argv schemas,
side-effect declarations, preview/dry-run, cancellation, timeouts, bounded
output, correlation IDs, and audit events. Keep direct safe actions fast. Model
generated shell strings are forbidden. Carry the 1B verification obligations
(DEFERRED-004/005): re-prove the worker approval round-trip against real tools
and do not present the PC as fully secured while any path bypasses the broker.
Do not implement Bubblewrap isolation; Packet 3B owns untrusted execution.
```

### Verification prompt

```text
Verify ONLY Packet 3A. Inventory every registered tool and caller. Prove
unknown tools, invalid arguments, shell injection, scope violations, timeout,
cancellation, duplicate stop, retry, and confirmation bypass all fail safely.
Prove audit events are redacted and correlated, trusted safe actions remain
low-latency, and no generated/untrusted code is falsely called isolated.
Re-prove the carried 1B worker approval round-trip on real tools and reject
any whole-PC security claim while paths remain outside the broker.
```

## Packet 3B — isolated execution

### Implementation prompt

```text
Implement ONLY Packet 3B: adapt the pinned OpenSandbox probe/lifecycle behavior
to Bubblewrap 0.9.0 and separate bounded user-systemd transient units. Add
fail-closed ISOLATION_UNAVAILABLE, namespace/path/env probes, realpath/symlink
escape rejection, minimal filesystem, private proc, network-none profile,
cgroup limits, lifecycle states, TTL, cancellation, cleanup, and hostile tests.
Do not add Docker, Kubernetes, a VM, a daemon, or a second task runner. Never
run untrusted code on the host when isolation is unavailable.
```

### Verification prompt

```text
Verify ONLY Packet 3B. Run the pinned upstream lifecycle/probe comparisons and
hostile tests for filesystem, symlink, environment, privilege, network, process,
resource, timeout, crash, and cleanup boundaries. Prove workloads run in their
own bounded systemd unit outside the bridge cgroup; missing isolation returns a
typed failure; no orphan remains. Reject soft monitoring presented as a hard
limit or any host-privileged fallback.
```

## Packet 4A — benchmark corpus and harness

### Implementation prompt

```text
Implement ONLY Packet 4A: create the fixed sanitized private evaluation corpus
and reproducible benchmark harness. Reproduce classifier→chat→classifier→
summary→chat cold/warm transitions, capture model digest/quantization/options,
load_duration, prompt/eval metrics, first content/audio/full response, CPU,
GPU, VRAM, RAM, and errors. Change no model defaults, aliases, Modelfile,
runner options, or production runtime. Separate unit/diagnostic/live outputs
and keep private data out of fixtures.
```

### Verification prompt

```text
Verify ONLY Packet 4A. Re-run the fixed corpus and repeated cold/warm transition
benchmarks, inspect raw measurements, confirm percentiles and environment are
recorded, and prove the sequence exposes option-driven reloads. Check holdout
separation, sanitized fixtures, no model-default changes, no hidden warmup, and
honest degraded/live labeling. Reject single-sample or averaged-away claims.
```

## Packet 4B — canonical runner and context compiler

### Implementation prompt

```text
Implement ONLY Packet 4B: establish one canonical resident Ollama runner
contract, remove stale/private baked model context, implement bounded context
compilation and compact typed decisions, and prove zero option-driven reloads.
Preserve the current alias until the packet's tests pass. Use one resolved
model digest, context, placement, parallelism, and keep-alive source for every
classifier/chat/summary/planner caller. Do not add a second model server or
semantic router. Never copy private values into Git or model artifacts.
```

### Verification prompt

```text
Verify ONLY Packet 4B. Inspect every model caller and prove material runner
options are identical, context/output budgets are bounded, the baked biography
and private data are removed or quarantined with rollback, schema-invalid output
fails closed, and 100 repeated mixed transitions cause zero unintended reloads.
Run personal correction, injection, language, overflow, load-failure, and
restoration tests. Reject any per-caller context drift or secret in artifacts.
```

## Packet 4C — deterministic and semantic routing

### Implementation prompt

```text
Implement ONLY Packet 4C: implement deterministic L0 routing and optionally
evaluate a CPU-only semantic L1 using the pinned Semantic Router threshold,
margin, held-out evaluation, and explicit no-match behavior. Use precomputed
route vectors and the existing embedding endpoint only if it passes latency,
RAM, placement, and zero-eviction gates. Keep a no-semantic fallback and do not
add Semantic Router, NumPy, a vector database, or another model server.
```

### Verification prompt

```text
Verify ONLY Packet 4C. Prove 100% deterministic cases avoid model/embedding
calls, no-match/low-margin cases clarify instead of acting, L1 thresholds are
held-out and per-route, CPU/resource gates pass, and the embedding runner never
evicts/reloads the generative model. Re-run false-positive/negative and
prompt-injection cases. Reject semantic routing that adds undeclared heavy
dependencies or turns uncertainty into permission.
```

## Packet 4D — model bake-off and planner

### Implementation prompt

```text
Implement ONLY Packet 4D: benchmark pinned Qwen candidates under the same
runner/context contract, add the bounded typed L2 planner and smallest matching
tool schemas, keep candidate aliases separate, and implement VRAM guard,
fallback, deep-tier eviction/restore, and rollback. Keep production on the
current alias until independent verification. AirLLM/SGLang/vLLM/Transformers
remain isolated experiments only; do not load an 8B or vision model beside the
4B model on the 8 GB GPU.
```

### Verification prompt

```text
Verify ONLY Packet 4D. Re-run the held-out bake-off and all Phase 4 gates by
class, including safety precision/recall, schema/tool arguments, personal QA,
latency, first audio, residency, VRAM, load transitions, deep-tier restore,
and rollback. Verify model hashes/licenses and candidate/base compatibility.
Reject public-leaderboard-only selection, hidden thinking tokens, private baked
profile data, any reload, OOM, unsafe planner, or in-place alias replacement.
```

## Packet 5A — evidence and adapter contracts

### Implementation prompt

```text
Implement ONLY Packet 5A: define normalized read-only evidence and adapter
contracts for files, repos, browser, inboxes, reports, web, OpenClaw/Hermes,
and memory. Add stable IDs, source/version, freshness, scope, permissions,
sensitivity, trust, hashes, redacted excerpts, confidence, citations, and
strict read/write/remember separation. Do not implement the SQLite memory store
or context compiler yet.
```

### Verification prompt

```text
Verify ONLY Packet 5A. Prove each adapter declares read/write/remember and
scope, read-only adapters cannot mutate, evidence is cited/freshness-labeled/
redacted, IDs and hashes are stable, malformed/partial/permission failures are
typed, and no private data crosses scope. Reject prose-only or uncited evidence
contracts and new storage services.
```

## Packet 5B — SQLite/FTS5 personal memory

### Implementation prompt

```text
Implement ONLY Packet 5B: build the scoped SQLite/FTS5 personal-memory store
with core/working/recall/archive tiers, provenance/history, normalized-hash
deduplication, expiry/tombstones/supersedes, explicit remember/correct/forget/
export/purge, staged inferred memories, and contract tests adapted from the
pinned Mem0 behavior. Re-pin or reject the unavailable Letta source explicitly.
Do not inject whole memory files, add a vector database, or modify model
prompts beyond the store contract.
```

### Verification prompt

```text
Verify ONLY Packet 5B. Prove add/search/update/correct/forget/export/purge,
history, dedupe, scope, expiry, tombstone, supersedes, restart, concurrent
writes, migration, rollback, and private-file modes. Confirm inferred memories
are staged, corrected facts outrank stale ones, secrets are rejected, FTS5-only
mode works, and Letta is not silently used from an unpinned/unsupported branch.
```

## Packet 5C — context compiler and retrieval safety

### Implementation prompt

```text
Implement ONLY Packet 5C: build the bounded context compiler with deterministic
precedence, FTS5 retrieval, optional approved semantic reranking, cache TTL,
redaction, partial-source handling, citations, conflict labels, and prompt-
injection tests. Keep persona, active state, corrected facts, evidence,
tools, and output reserve separately budgeted. Do not add a new database or
rewrite the model runner contract.
```

### Verification prompt

```text
Verify ONLY Packet 5C. Prove context stays within the selected model budget,
required instructions and output reserve survive, low-score/stale/tombstoned
records are omitted, corrections win, citations and omission metadata exist,
partial failures are visible, cache TTL is safe, and retrieved hostile text
cannot change policy, tools, routing, persona, or durable memory. Test FTS5-only
fallback and cross-scope isolation.
```

## Packet 6A — lesson provenance and correction

### Implementation prompt

```text
Implement ONLY Packet 6A: add typed lesson provenance, evidence IDs, conflict
detection, confidence/decay/TTL, correction priority, explicit removal, and
user-visible explanation for learned preferences. Separate explicit user
instruction, observed outcome, and model inference. Do not train weights or
change routing automatically in this packet.
```

### Verification prompt

```text
Verify ONLY Packet 6A. Prove corrections override stale lessons, one good
interaction cannot rewrite behavior, contradictory evidence creates review,
every promoted preference shows source/confidence and can be removed, prompt
injection cannot become a preference, and audit explanations come from stored
provenance rather than model invention.
```

## Packet 6B — evaluation and poisoning gate

### Implementation prompt

```text
Implement ONLY Packet 6B: add the private held-out evaluation set, task-outcome
records, poisoning/injection tests, regression scoring, and promotion gate for
router, memory, skills, model, and safety changes. Separate quality, safety,
latency, resource, and tool-success metrics. Do not promote a model, adapter,
preference, or skill automatically.
```

### Verification prompt

```text
Verify ONLY Packet 6B. Re-run the held-out set and poisoning cases, inspect
per-class metrics and failures, prove no unsafe weighted average hides a safety
regression, verify task outcomes include approval/retry/correction/completion,
and confirm failed or unreviewable candidates cannot be promoted. Test rollback
and removal of a bad lesson.
```

## Packet 7A — operations and clean installation

### Implementation prompt

```text
Implement ONLY Packet 7A: separate unit/contract/integration/live-canary/
monitor commands, add health checks, clean-venv installation/dependency tests,
deployment-template checks, and prohibit implicit watchdog/service restarts.
Keep live tests opt-in and visibly degraded when skipped. Do not add a new
monitoring daemon or change production model behavior.
```

### Verification prompt

```text
Verify ONLY Packet 7A. Run commands independently and prove pytest never starts
watchdogs, live/monitoring tests are separated, clean installation catches
undeclared dependencies, health checks report typed states, deployment output
matches documented overrides, and no service restarts implicitly. Inspect
failure and degraded output, not just exit codes.
```

## Packet 7B — flags, rollback, smoke, documentation

### Implementation prompt

```text
Implement ONLY Packet 7B: add feature flags, rollback drills, smoke coverage,
change reports, and final runtime/documentation reconciliation for already
verified Phase 0–7A behavior. Do not introduce new capabilities or silently
enable communications scheduling. Preserve user data during rollback.
```

### Verification prompt

```text
Verify ONLY Packet 7B. Toggle every flag, execute rollback drills, run voice,
dashboard, task, cancellation, and startup smoke tests, compare docs/config/
service behavior, and prove rollback preserves memory/reports/cursors. Reject
hidden defaults, undocumented migrations, skipped smoke paths, or any new
feature smuggled into reconciliation.
```

## Packet 7C — downloadable local application and trusted device migration

### Implementation prompt

```text
Implement ONLY Packet 7C: create a verified download-and-run distribution for the
first supported Ubuntu target using the existing Python/static-JS/Ollama/systemd
stack. Add a versioned package manifest, launcher, hardware/resource doctor,
explicit model-artifact acquisition with revision/checksum/license metadata,
upgrade/uninstall behavior, and clean-install diagnostics. Do not claim a
universal single binary; keep platform-specific adapters explicit and leave
Windows/macOS as documented future targets unless separately authorized.

Add a versioned portable-state export/import contract for memory, lessons,
preferences, reports, non-secret configuration, model metadata, and migration
history. Exclude API keys, cookies, Chromium profiles, CDP debugger URLs, active
approvals, private credentials, and machine-specific audio/GPU paths. Use an
audited pinned encryption mechanism—never home-grown cryptography—and require an
explicit passphrase or approved OS key store. Stage restores atomically, verify
integrity before applying, support dry-run/preview, preserve a pre-restore
backup, and provide rollback.

Add explicit trusted-device enrollment. A new device must be authorized and must
re-check hardware, model compatibility, optional dependencies, and capabilities;
browser login, credentials, and voice enrollment must be fresh where required.
Never copy live browser profiles or secrets automatically. Do not silently enable
systemd services, expose network access, change the user's browser, or delete
old state. Add clean-install, upgrade, interrupted-install, uninstall,
wrong-passphrase, corrupted-bundle, schema-migration, rollback, low-disk,
CPU-only, GPU, missing-Ollama, missing-browser, and missing-audio tests.

If adding the optional public-web extraction adapter, implement it in the Phase 5
evidence boundary only: pin and document the exact Scrapling source/license,
prefer bounded HTTP, enforce SSRF/domain/size/time/concurrency limits, redact and
cite output, treat page text as untrusted evidence, and do not use stealth or
proxy/CAPTCHA features to defeat access controls. Do not add Scrapling to
personal-account monitoring or add a second agent framework.
```

### Verification prompt

```text
Verify ONLY Packet 7C. On a clean supported Ubuntu environment, download or
install the package, verify the manifest/model checksum/license, run the hardware
doctor, launch the application, and inspect actual resource usage. Prove the
package does not depend on the developer's home directory, undeclared packages,
hidden services, copied secrets, or an unverified model alias. Verify upgrade,
interrupted-install, uninstall, low-disk, CPU-only, GPU, missing-Ollama,
missing-browser, and missing-audio states with typed outcomes.

Create a synthetic state bundle and prove encrypted export/import restores the
documented memory, lessons, preferences, reports, non-secret config, model
metadata, and migration history. Prove API keys, cookies, Chromium profiles,
CDP URLs, active approvals, credentials, and machine-specific paths are rejected
or absent. Test wrong passphrases, corrupted bundles, schema migrations,
interrupted restore, atomicity, pre-restore backup, and rollback. Prove a new
device requires explicit enrollment and that authorized restore does not silently
copy browser sessions or enable services.

If the optional Scrapling adapter is included, inspect the pinned upstream commit,
license, source, and tests. Exercise public synthetic fixtures for bounded HTTP,
optional rendering, citations, redaction, prompt-injection resistance, SSRF and
private-network blocking, redirects, limits, cancellation, cache, partial
failure, and resource cost. Reject stealth/proxy/CAPTCHA access-control bypass,
private-account use, unbounded crawling, raw page injection, or a new runtime
framework.

Reject a claim of “portable” if only source files copy while state, migrations,
model compatibility, rollback, or device enrollment remain untested. Reject a
claim of “better SLM” unless model quality and latency are separately benchmarked;
packaging and tools improve the agent system, not the neural weights by themselves.
```

## Packet 7D — product customization, dashboard/CLI parity, and Qubit branding

This packet is a follow-up to the already implemented 7C package contract. It
must not be reported as part of 7C retroactively.

### Implementation prompt

```text
Implement ONLY Packet 7D: make Qubit Voice Agent beginner-friendly while
providing advanced, fully reversible customization through both the web
dashboard and CLI. Preserve the existing Python/static-JS/Ollama/systemd,
security, audit, rollback, OpenClaw/Hermes, and 5 GB VRAM contracts.

Create a versioned provider/profile registry for the replaceable reasoning
brain/model, STT engine/model, TTS provider, and voice artifact. Keep the
verified local Qubit model, CPU-friendly STT, and Piper voice as the default.
Every install/select/upgrade/remove operation must validate source, revision,
license, checksum, supported hardware/language, quality/safety evidence,
latency, VRAM/RAM cost, and rollback target. Keep one approved resident
generative model by default. A downloaded file or provider response is never
trusted merely because it exists, and a failed gate must leave the previous
profile active.

Add beginner onboarding with plain-language safe defaults and an advanced
settings mode that exposes technical details only when requested. Expose stable
machine-readable CLI commands for profile list/show/check/install/select/reset/
upgrade/remove/rollback; changes are dry-run by default, explicitly applied,
staged atomically, backed up, audited, and reversible. Keep voice selection
separate from TTS provider, model, and audio-device selection.

Make the authenticated web dashboard the primary control center with parity for
all supported CLI operations: onboarding, model/STT/TTS/voice selection, audio
devices, permissions, device enrollment, health, task progress, approvals,
cancellation, reports, audit/provenance, export/import, upgrade, reset, and
rollback. Every control must call a real authenticated backend contract and
display typed success, unavailable, degraded, denied, partial, or failed state.
Require confirmation for side effects, preserve loopback binding by default,
escape untrusted text, and never expose secrets, private message bodies,
prompts, credentials, or filesystem paths. Add API, negative/security,
persistence, accessibility, and local UI smoke tests for every supported
control. Reject placeholder buttons, frontend-only state, and fake success.

Brand the distributable as Qubit Voice Agent. Prepare the physical repository
rename from `openclaw-voice-bridge` to `qubit-voice-agent` as a separate release
migration, updating package metadata, docs, service/unit identifiers,
configured paths, launchers, fixtures, and compatibility aliases together.
Provide a dry-run, backup, migration, independent verification, and rollback
path. Do not move, delete, or silently re-enroll existing user state, and do
not perform the rename as incidental cleanup.

Inspect the relevant pinned upstream source/tests before coding, record
provenance and licenses, use the smallest compatible local implementation, and
stop after the 7D handoff. Do not start Packet 8A automatically.
```

### Verification prompt

```text
Verify ONLY Packet 7D. Prove beginner onboarding works with safe defaults and
that advanced users can inspect and replace the brain, STT, TTS provider, and
voice through both the authenticated web dashboard and CLI. Verify provider
artifacts are pinned and checked for license, hash, hardware/language
compatibility, quality, safety, latency, and VRAM/RAM before activation; failed
checks leave the previous profile active.

Exercise dry-run, explicit apply, preview, backup, atomicity, rollback, remove,
upgrade, reset, typed unavailable/degraded/denied/partial/failed states, and
restart persistence. Prove provider/voice/model/audio-device changes are
independent, permissions are not expanded, services are not silently restarted,
recording is not silently enabled, and memory/reports/device settings survive.

Exercise dashboard feature parity end to end: onboarding, settings, provider
replacement, audio selection, permissions, enrollment, health, task progress,
approval/cancellation, reports, audit, export/import, upgrade, reset, and
rollback. Confirm each control reaches the real authenticated backend, has
negative/security/persistence/accessibility/local-UI evidence, and never shows
fake success or leaks secrets/private data. Reject frontend-only state and
placeholder controls.

Verify the Qubit Voice Agent branding migration separately. Confirm all
repository/package/service/config/launcher references agree, no developer-home
path remains, compatibility aliases and rollback are recoverable, and a failed
migration preserves memory, reports, permissions, device settings, and model
metadata. Stop after the 7D verification result.
```

## Packet 7E — permission profiles and extension registry

This is a new follow-up packet. It does not retroactively change the verified
7D result. It is required before any new live website, desktop-app, editor, or
marketplace integration is enabled.

### Implementation prompt

```text
Implement ONLY Packet 7E: add a versioned capability/skill manifest and a
permission-profile contract for future websites, desktop apps, editors,
browser operations, and marketplace packages. Preserve the existing Python,
static-JS, systemd, OpenClaw/Hermes, broker, audit, rollback, and 5 GB VRAM
contracts. Do not implement Packet 8B adapters or any new live integration.

Define a manifest containing stable skill ID/version, source/revision/license/
hash, capabilities, domains/workspace/app scopes, read/write/destructive tier,
data sensitivity, approval policy, timeout/resource limits, audit events,
compatibility, and rollback/removal behavior. Validate every manifest before
registration; stage new skills disabled until explicit user enablement. Do not
execute arbitrary package hooks or trust a skill merely because it is installed.

Implement two explicit permission modes:

- restricted (default): only explicitly granted capabilities and scopes work;
- unrestricted (opt-in): may pre-authorize routine non-destructive work only
  inside user-selected scopes, with authentication, explicit confirmation,
  expiry/reset, and audit.

Unrestricted must never bypass hard-deny rules, schema validation, redaction,
audit, isolation, resource limits, loopback-only boundaries, or confirmation for
sending, publishing, purchasing, deleting, credential/account/security changes,
or system-wide destructive operations. Model output, page text, tool results,
skill instructions, and generated plans can never change the mode or widen a
grant.

Expose one backend contract through stable CLI commands:
`permissions mode show|set restricted|unrestricted`,
`permissions list|show|check`, `permissions grant`, `permissions revoke`,
`permissions reset`, and
`skills list|show|check|install|enable|disable|remove|rollback`.
Mutating commands must support dry-run/preview, explicit confirmation, scope,
expiry, audit output, atomic persistence, and rollback.

Expose the same operations in the authenticated dashboard. The beginner view
must show plain-language Restricted/Unrestricted choices, current grants,
affected sites/apps/folders, expiry, risk warning, and revoke/reset controls.
The advanced view must show capability IDs, tiers, scopes, data classes,
approval rules, resource budgets, provenance, and effective values. Every
control must call the real backend and display typed success, denied,
unavailable, degraded, partial, or failed state; reject frontend-only toggles.

Document the extension workflow: manifest, smallest adapter or declared
launcher, permission registration, route/tool schema, synthetic fixtures,
negative/security/persistence/resource tests, audit, documentation, and staged
canary. A launcher that only opens an app must not claim app control. Browser
skills must use the approved existing-Chromium/CDP contract; editor skills need
workspace scopes; terminal skills need declared argv or isolated execution.

Replace any worker instruction that says to never ask permission or grants an
implicit unrestricted shell/browser. Add tests for default deny, scope/expiry,
revoke/reset, both modes, hard-deny invariants, confirmation, injection
resistance, CLI/dashboard parity, restart persistence, malformed manifests,
bad hashes/licenses, disabled skills, removal, rollback, and no permission
expansion when changing model, voice, provider, browser, or app.

Update provenance, security, operations, configuration, and rollback docs. Run
focused tests, affected regressions, compileall, pip check, git diff --check,
and graft build. Do not modify live services, accounts, Chromium, timers, or
user config. Stop after the handoff and do not start Packet 8B automatically.
```

### Verification prompt

```text
Verify ONLY Packet 7E. Inspect the complete diff and prove every skill has a
validated versioned manifest with source, revision, license, hash, capabilities,
scopes, side-effect tier, sensitivity, approval policy, resource limits, audit,
compatibility, and rollback metadata. Prove invalid, unlicensed, hash-mismatched,
or disabled skills cannot execute or become active.

Prove Restricted is the default. Prove Unrestricted requires authenticated
explicit confirmation, selected scopes, expiry/reset, and audit, and still
cannot bypass hard-deny actions, schema validation, redaction, audit,
isolation/resource/loopback boundaries, or confirmation for sending, posting,
purchasing, deleting, credential/account/security changes, or system-wide
destructive operations. Prove model/page/skill/tool text cannot alter policy.

Exercise CLI and authenticated dashboard parity for mode show/set, list/show/
check, grant/revoke/reset, skill install/enable/disable/remove/rollback,
dry-run, preview, confirmation, restart persistence, malformed input, scope and
expiry failures, and rollback. Confirm every control uses the real backend and
typed states rather than frontend-only success.

Prove the documented workflow for adding a new website/app/editor requires a
manifest, declared adapter/tools or an honestly limited launcher, narrow scope,
provenance/license, synthetic fixtures, security/resource tests, audit, and a
staged canary. Confirm no live website, account, Chromium target, service,
timer, user config, model, or audio behavior changed. Run all focused and
affected tests, inspect failures/warnings, and stop after the 7E result.
```

## Packet 8A — communications/report contracts

### Implementation prompt

```text
Implement ONLY Packet 8A: inventory existing real-Chromium CDP and report paths;
define versioned scan/account-health/item/cursor/report-manifest/asset/
notification schemas; design SQLite migration, retention, statuses, and atomic
cursor/report commit; create synthetic sanitized Gmail/Instagram/Messenger and
report fixtures. Do not read real message bodies into fixtures, change runtime,
enable timers, or implement 8B–8D.
```

### Verification prompt

```text
Verify ONLY Packet 8A. Prove schemas distinguish new/unread/unknown/stale/
partial/auth-required/no-change, stable identities and collisions are covered,
cursor advancement requires atomic report commit, fixtures contain no private
data, retention/status/rollback are explicit, upstream CDP/systemd/report
licenses are recorded, and no live runtime/service changed.
```

## Packet 8B — real Chromium read-only adapters

### Implementation prompt

```text
Implement ONLY Packet 8B: build repository-owned typed read-only Gmail,
Instagram, and Messenger adapters attaching to the user's already logged-in
real Chromium through loopback CDP. Add stable identity, baseline/new-item
transactions, bounded scans, overflow/selector/auth health, clipped CDP
screenshots, and manual --once/--dry-run fixture tests plus an explicitly
opt-in redacted canary. Never launch Playwright account profiles, click/type/
send/react/mark-read, mutate/focus/close user tabs, enable scheduling, or build
the report UI.
```

### Verification prompt

```text
Verify ONLY Packet 8B. Use synthetic fixtures and, only if explicitly
authorized, a redacted live canary. Prove loopback CDP attaches to existing
Chromium without profile cloning/focus/tab closure/mutation, adapters are
strictly read-only, stable identities and baseline/new detection are
transactional/idempotent, overflow/auth/selector/partial states are typed,
screenshots are scoped/hashed or have an explicit missing-evidence reason, and
no cursor advances on failure. Reject Playwright-owned personal profiles,
unbounded DOM extraction, send/reply paths, or hidden stale-cache success.
```

## Packet 8C — professional report bundles and UI

### Implementation prompt

```text
Implement ONLY Packet 8C: render validated canonical manifests into atomic
private JSON/Markdown/HTML bundles and evidence assets; replace the activity-log
reports API with authenticated index/detail/asset routes; index legacy Markdown
as legacy; update only the static reports UI with safe links, evidence status,
thumbnails, acknowledgement, export/print, escaping, and accessibility. Add
fixture/API/traversal/XSS/link/hash/MIME/file-mode and local Playwright UI tests.
Do not enable timers, notifications, or live account scans.
```

### Verification prompt

```text
Verify ONLY Packet 8C. Prove each report is a validated canonical bundle with
JSON/Markdown/HTML consistency, safe source links, scoped/hash-linked
screenshots or explicit evidence reasons, authenticated routes/assets, correct
private modes, traversal/XSS/CSV/link/MIME protections, legacy labeling,
acknowledgement/export/print behavior, and responsive/accessibility UI tests.
Reject activity/log records presented as reports, raw HTML, URL-token auth,
unhashed assets, permissive files, or live scheduler changes.
```

## Packet 8D — authorized hourly monitor and notifications

### Implementation prompt

```text
Implement ONLY Packet 8D after the user explicitly authorizes live activation
and Packets 8A–8C plus dependency gates pass: add deduplicated dashboard/desktop
and opt-in quiet-hours voice notifications, repository-templated bounded user
systemd oneshot/timer, overlap lease, retention, Chromium-CDP precondition and
optional approved launch integration, health/rollback commands, and separate
live canaries. Inspect every service/timer/autostart diff before installation.
Never auto-reply, expose CDP beyond loopback, start a competing browser/profile,
kill/restart the user's browser, alter account/session state, delete reports,
or hide skipped/degraded canaries. Prove at least two real timer elapses and
one controlled new-item notification, then stop for verification.
```

### Verification prompt

```text
Verify ONLY Packet 8D and only the explicitly authorized live scope. Inspect
rendered service/timer/autostart files and the enable/disable/rollback path.
Prove overlap locking, bounded resource use, catch-up/retention, Chromium CDP
preconditions, no focus/tab/account mutation, notification dedupe, quiet hours,
no-change silence, degraded/auth-required alerts, and recovery after failure.
Observe at least two real timer elapses and one controlled new-item notification
without printing message contents. Reject any send/reply capability, public CDP,
competing profile, silent deletion, hidden skipped canary, or timer enablement
outside explicit authorization.
```

## Packet 8E — website integration onboarding and observability

This packet adds the safe user-facing flow for adding a future website, app,
editor, or marketplace skill. It does not make arbitrary websites controllable
and does not replace the per-site adapter gates in Packet 8B.

### Implementation prompt

```text
Implement ONLY Packet 8E from docs/UNIFIED_AGENT_PLAN.md: add a governed
website-integration onboarding façade so a user can paste a URL in the CLI or
authenticated dashboard, run bounded read-only discovery, review a proposed
capability/skill manifest, grant narrow permissions, and inspect redacted
progress/error logs. This is a proposal and observability layer, not universal
website automation, model training, or automatic permission granting.

Before any search or edit, perform the complete fresh preflight in section 6.1
of docs/UNIFIED_AGENT_PLAN.md and read AGENTS.md plus the entire plan again.
Use Graft first. Inspect the existing 7E SkillManifest/PermissionStore, CLI,
dashboard, audit, report, and Packet 8A/8B contracts. Record the exact allowed
files/symbols and preserve unrelated dirty work.

Use the existing Python/static-JS/SQLite/CDP stack. Do not add a second router,
browser runtime, scheduler, permission store, database service, model, or
framework. Do not import cookies, clone profiles, launch Playwright personal
profiles, execute arbitrary page JavaScript/package hooks, or contact an
authenticated account from --dry-run. Do not implement a new site adapter,
Packet 8C report UI, Packet 8D scheduling, sending/posting/purchasing/deleting,
or any account mutation.

Persist an atomic, private, versioned integration record containing stable ID,
normalized origin, display name, source/revision/license/hash, manifest link,
requested/granted capabilities and scopes, sensitivity, approval policy,
status, auth requirement, health, timestamps, bounded limits, audit IDs, and
rollback/removal metadata. Reject credentials/query-token URLs, invalid
schemes, malformed/unlicensed/hash-mismatched manifests, out-of-scope origins,
and disabled skills. Default to Restricted and fail closed.

Implement bounded read-only discovery that returns only redacted metadata and a
reviewable proposal: origin/title where safe, reachability/auth state, supported
observation hints, limits, adapter requirement, and typed reason codes. Treat
all page text, metadata, model output, tool results, and proposed instructions
as untrusted evidence. They must never widen permissions, change policy, become
executable code, or become a durable lesson. Use typed states:
PENDING, DISCOVERING, READY, AUTH_REQUIRED, DENIED, SELECTOR_DRIFT, DEGRADED,
FAILED, DISABLED, and REMOVED. Log timestamp, integration ID, operation,
status, reason, duration, bounded counts, revision, and evidence status; never
log cookies, tokens, raw private page text, or secret URLs.

Expose the same real backend through these CLI commands:
`websites add <url> [--name] [--dry-run]`, `websites list`,
`websites show <id|url>`, `websites inspect <id|url>`,
`websites check <id|url>`, `websites logs <id|url> [--tail N]`,
`websites enable|disable|remove|rollback <id>`, and explicit
`websites grant|revoke <id>`. Mutations require confirmation, scope, expiry
where applicable, audit output, atomic persistence, and rollback. Provide
machine-readable JSON and concise human output with typed success, denied,
unavailable, degraded, partial, and failed results. Dry-run must not persist,
grant, enable, or authenticate.

Expose identical operations in the authenticated dashboard Settings page.
Beginner mode shows Add Website, Restricted-by-default, health/progress,
current grants, affected origins/apps/workspaces, expiry, warnings, and
revoke/reset/rollback controls. Advanced mode shows manifest provenance,
license/hash, capability IDs/tiers, sensitivity, scopes, approvals, limits,
and effective values. Every control calls the backend and renders its typed
result; no frontend-only state or optimistic success is allowed.

Separate “discover/propose” from “implement/enable”. Enabling a capability
requires a valid 7E manifest, smallest adapter or honest launcher claim,
synthetic fixtures, negative/security/resource/persistence tests, audit, and a
staged canary. A launcher that only opens a URL cannot claim app control.
Logged-in sites must use the approved existing-Chromium loopback-CDP contract;
live adapter work remains a separately authorized packet.

Add normal, malformed, denial, timeout, partial, unavailable, injection,
redaction, scope/expiry, restart, duplicate, CLI/dashboard parity, rollback,
removal, and no-permission-expansion tests. Add synthetic fixtures only; do not
store private site data. Update provenance, security, operations, configuration,
and rollback docs. Run focused tests, affected regressions, compileall, pip
check, git diff --check, and graft build. Inspect actual output and warnings.
Do not modify live services, timers, Chromium, accounts, model/audio config, or
existing 8B behavior. Stop after the handoff; do not start another packet.
```

### Verification prompt

```text
Verify ONLY Packet 8E. Perform a fresh section 6.1 preflight, reread AGENTS.md
and the entire plan, use Graft first, inspect the complete diff and every
caller, and independently verify the 7E manifest/permission linkage. Do not
trust the implementation agent's summary and do not start 8B/8C/8D or another
packet.

Prove that pasting a URL creates only a pending/disabled integration record and
never grants access, trains the model, executes page text, imports credentials,
clones a profile, launches a Playwright personal profile, or creates a hidden
skill. Test invalid schemes, credentials/query tokens, malformed manifests,
bad licenses/hashes, out-of-scope origins, disabled skills, injection text,
timeouts, unavailable/auth-required/selector-drift/partial states, bounds,
redaction, duplicate inspect/check, restart persistence, and rollback/remove.

Re-run bounded synthetic discovery and inspect actual machine-readable and
human-readable CLI output for `websites add/list/show/inspect/check/logs`,
grant/revoke, enable/disable/remove/rollback, dry-run, confirmation, scope,
expiry, and audit behavior. Verify restricted is default and unrestricted
cannot bypass hard-deny actions, schema validation, audit, isolation, or
resource limits. Prove page/model/tool text cannot alter policy or widen a
grant.

Exercise the authenticated dashboard against the same backend and prove
beginner/advanced settings, typed success/denied/unavailable/degraded/partial/
failed states, logs, expiry, revoke/reset, and rollback are not frontend-only.
Confirm no secret, raw private page text, cookie, token, or secret URL appears
in records, logs, fixtures, reports, events, or test output. Confirm a static
page produces only a bounded proposal and a launcher never claims control.

Run focused, negative, security, persistence, resource, and affected tests;
compileall, pip check, git diff --check, and graft build. Inspect warnings and
failure output, not just exit codes. Confirm no live service/timer/Chromium
target/account/model/audio/config or existing 8B behavior changed. Return only
the verification template with VERIFIED, REJECTED, or VERIFIED WITH EXPLICIT
LIMITATION, evidence, failures, untested paths, repairs, and next safe action.
```

## Packet 8F — controlled browser and desktop actions

This packet is the future action layer for ChatGPT/Grok-style computer use. It
is separate from 8B read-only monitoring and 8E website discovery. It must use
the existing broker and permission contracts; it must never become unrestricted
automation.

### Implementation prompt

```text
Implement ONLY Packet 8F from docs/UNIFIED_AGENT_PLAN.md: add typed, brokered,
confirmation-gated browser, desktop, and editor actions. Reuse the existing 7E
permission store, capability broker, 8E integration records, and approved
Chromium/CDP boundary. Add read/search/fill/draft/reversible-write/send/delete
capability separation, exact schemas, bounded previews, stable action-hash
confirmation, cancellation, idempotency, receipts, audit, typed failure
states, recovery, and CLI/dashboard/voice parity.

Restricted is the default. Sending, publishing, purchasing, deleting,
credential/account/security changes, external communication, and irreversible
system actions always require fresh human confirmation immediately before
execution. Reject arbitrary JavaScript, free-form shell, coordinate-only
scripts, page/model/skill instructions as authority, profile cloning, cookie
import, public CDP, hidden scopes, and unbounded loops.

Use existing adapters and helpers. Do not add a second router, browser runtime,
scheduler, permission store, database, or model. Add synthetic fixtures and
negative/security/resource/persistence tests for websites, email/social flows,
VS Code/editor workspaces, and desktop launchers. A launcher that only opens an
app cannot claim app control. Remote actions must report honestly when rollback
is impossible; never fake rollback for a sent message, purchase, deletion, or
account change.

Expose preview, approval, execute, cancel, receipt, retry, and recovery through
the same authenticated CLI and dashboard; voice requests must use the same
confirmation path. Bind confirmation to the exact action hash, target, account,
arguments, scope, and expiry. Page/model/skill/tool output cannot confirm for
the user or widen a grant.

Update provenance, security, operations, configuration, and rollback docs. Run
focused, affected, negative, security, persistence, resource, compileall, pip
check, git diff --check, and graft build checks. Do not enable live actions or
modify live services, Chromium, accounts, model/audio/config, or existing 8B
behavior. Stop after the handoff and do not start another packet.
```

### Verification prompt

```text
Verify ONLY Packet 8F. Perform a fresh section 6.1 preflight, reread AGENTS.md
and the entire plan, use Graft first, inspect every affected caller, and do not
trust the implementation agent's summary. Do not start another packet.

Prove every action reaches the existing broker and 7E permission check and is
bound to a validated adapter, scope, target, argument schema, resource limit,
action hash, confirmation, and audit receipt. Re-run preview/dry-run,
confirmation hash/expiry, restricted/unrestricted, hard-deny, cancellation,
duplicate/idempotency, stale-target, auth/2FA, partial execution, retry,
recovery, rollback-honesty, redaction, injection, arbitrary-JavaScript,
free-form-shell, and unbounded-loop tests.

Verify CLI, dashboard, and voice requests have identical approval semantics.
Prove a model, page, skill, or tool result cannot confirm or widen an action.
Inspect actual receipts and failure output. Confirm read, draft, reversible
write, external send, destructive, and system-wide actions have separate
capabilities and metrics; no unsafe aggregate may hide a safety failure.

Live account actions remain opt-in and separately canaried; do not perform them
without explicit authorization. Confirm no unrelated service, browser, account,
model, audio, or config changes. Return only VERIFIED, REJECTED, or VERIFIED
WITH EXPLICIT LIMITATION with evidence, failures, untested paths, repairs, and
next safe action.
```

## Production qualification packets 8G–8L

Use these only after Packet 8F has been independently verified. They exist
because green hermetic tests do not prove a particular machine's browser,
audio devices, display session, timer, notification daemon, or restart path.
The implementation agent may prepare diagnostics, fixtures, tests, and
runbooks, but must not silently enable live services or access accounts. Live
steps require explicit operator authorization and an operator-controlled test
item. Packets 8G–8K are required before claiming full production readiness;
8K-P is required for a simple-install/private-source distribution; 8L is
required for a polished public release.

### Packet 8G — real Chromium/CDP qualification

#### Implementation prompt

```text
Implement ONLY Packet 8G from docs/UNIFIED_AGENT_PLAN.md. Add a bounded,
operator-gated qualification harness and runbook for the existing user-launched
Chromium through loopback CDP. Cover target discovery, profile-lock and
browser-restart states, manual login/auth/2FA/CAPTCHA states, Gmail/Instagram/
Messenger read-only scans, clipped screenshot bytes, reconnects, selector
drift, hidden/unavailable targets, per-platform isolation, baseline, one
operator-arranged external test item (never sent by Qubit), and no-change
rescans.

Do not launch a personal Playwright profile, import cookies or credentials,
focus/navigate/close/mutate user tabs, send/reply/react/mark-read, or expose CDP
outside loopback. Live execution must be opt-in and impossible by default.
Persist only bounded redacted evidence and typed health; never store bodies,
senders, subjects, cookies, tokens, or secrets. Reuse 8B/8C/8E/7E contracts;
do not add a second browser runtime, cursor, store, or permission system.

Add focused negative/security/resource/persistence tests and an operator
runbook. During implementation and hermetic verification, do not touch live
services, accounts, Chromium, model/audio, or user configuration. Stop after
the handoff; do not start 8H or another packet.
```

#### Verification prompt

```text
Verify ONLY Packet 8G. Independently inspect the diff, callers, 8B contracts,
and 7E permission linkage. Run all hermetic tests first, then run the live
browser canary only with explicit operator authorization and an operator-
controlled account/item. Prove loopback CDP target selection, manual auth,
baseline/new/no-change behavior, bounded clipped screenshot bytes, reconnect,
selector drift, auth/2FA/CAPTCHA, missing target, profile-lock, and per-site
failure isolation.

Prove no Playwright personal profile, cookie import, public CDP, focus,
navigate, close, account mutation, send, reply, react, mark-read, or unbounded
scan occurred. Inspect redacted output and actual browser state. Return only
VERIFIED, REJECTED, or VERIFIED WITH EXPLICIT LIMITATION with evidence,
failures, untested paths, repairs, rollback, and next safe action. Do not start
another packet.
```

### Packet 8H — real audio, PTT, and Right Ctrl qualification

#### Implementation prompt

```text
Implement ONLY Packet 8H from docs/UNIFIED_AGENT_PLAN.md. Add an opt-in,
bounded qualification harness and runbook for the documented microphone,
audio sink, ASR, configured reasoning model, Qubit response, Piper playback,
web push-to-talk, and Right Ctrl path. Measure capture-to-response and
playback latency, record the actual configured provider/model identity, and
distinguish a real response from an unavailable-provider fallback. Cover
cancellation, duplicate-listener/stuck-key protection, device hot-unplug/
reconnect, service
restart recovery, and failure diagnostics without changing the working web
dashboard path. Verify DISPLAY or Wayland, XDG_RUNTIME_DIR, and Pulse/PipeWire
access without hardcoding a machine-only value; missing session context must
produce a typed failure and recovery guidance. Do not guess the highest X
display in a multi-session environment. Report the effective stable source and
sink; a missing configured pin must be `DEVICE_MISMATCH`/degraded and may not
silently pass through the aggregate default. Cover sample-rate/channel
conversion, silence, clipping, background noise, echo, VAD boundaries, and
actual audible playback.

Reuse the existing audio settings and voice contracts. Do not silently replace
Piper with Fish Audio, add training/self-modification, alter model defaults,
record private audio, or install a second daemon. Live device tests must be
explicitly gated; hermetic tests use synthetic audio/events. Add tests for
wrong sink/source, permission denial, ASR/TTS unavailable, timeout, restart,
and no audio leak. Stress concurrent web PTT and Right Ctrl turns,
cancellation/barge-in, hotplug/restart races, and temporary-audio cleanup;
prove no cross-turn answer or shared-state contamination. Automated restart
must require explicit authorization, bounded backoff, and a circuit breaker.
Do not enable live services during implementation. Stop after the handoff and
do not start 8I.
```

#### Verification prompt

```text
Verify ONLY Packet 8H. Independently run focused, affected, warning, resource,
and recovery tests, then—only with authorization—exercise the real microphone,
configured output, web PTT, Right Ctrl, ASR, response, and Piper playback.
Measure capture/ASR/reasoning/TTS/playback and end-to-end latency and prove one
key press creates at most one bounded turn, release/cancel stops capture,
audible playback uses the reported sink, reconnect recovers, and a crash does
not create a restart storm or duplicate listener. Verify stable device
identity, wrong-pin typed failure, format conversion, noise/echo/clipping/VAD,
multi-session X11/Wayland selection, concurrent web/rCtrl isolation, temporary
audio cleanup, restart backoff, and circuit breaking.

Confirm web dashboard behavior and audio configuration are unchanged except for
the documented qualification hooks. Do not change providers, train models,
store private audio, or claim success without real playback. Return the strict
verification template and stop; do not start another packet.
```

### Packet 8I — real timer, notifications, and quiet-hours qualification

#### Implementation prompt

```text
Implement ONLY Packet 8I from docs/UNIFIED_AGENT_PLAN.md. Add a bounded,
operator-gated qualification harness and runbook for the existing user-systemd
oneshot/timer and notification path. Cover the exact unit name, enable/disable
and rollback, overlap lease, bounded runtime/resource limits, no-change
silence, one controlled new-item notification, deduplication, desktop popup,
optional voice, acknowledgement, quiet hours, catch-up, timezone/DST, journal
evidence, and typed failure states. A temporary isolated accelerated test unit
may exercise failure paths, but it cannot substitute for observing the
production-equivalent hourly schedule.

Close the known configuration risks: strictly validate quiet-hours ranges and
IANA timezone; invalid input must be `CONFIG_INVALID` and suppress voice rather
than fall back to host-local time. Replace the hardcoded `DISPLAY=:1` assumption
with validated X11/Wayland user-session resolution. Make the lease renewable or
longer than the maximum oneshot runtime plus a safety margin (the current
defaults are 120 s versus a 300 s service timeout). Use monotonic time for
deadlines/leases, wall time only for scheduling, and make aggregate state/exit
code partial/degraded/failed when an account or required delivery degrades.

Keep notifications plain-language and redacted: no report IDs, URLs, senders,
subjects, message bodies, tokens, cookies, or secrets. Do not enable the timer
or alter a live unit during implementation or hermetic tests. Reuse 8C/8D
stores and policy; do not add another scheduler or notification database. Add
tests for no-change, duplicate, new item, quiet hours, missed run, overlap,
timeout, rollback, notification failure, malformed time config, DST fold/gap,
clock jump, suspend/resume, retry/dead-letter bounds, and truthful aggregate
status. Stop after handoff; do not start 8J.
```

#### Verification prompt

```text
Verify ONLY Packet 8I. Run the hermetic suite and inspect warnings, journal
output, unit contents, and failure behavior. With explicit authorization,
enable the documented unit, observe at least two real hourly executions, and
prove: no-change is silent; one controlled new item produces one redacted
desktop/optional-voice notification; duplicates do not repeat; quiet hours,
catch-up, timezone/DST, overlap, timeout, disable, and rollback are truthful.

Confirm notification delivery does not leak private data and never mutates an
account. Verify invalid time configuration fails closed, the lease cannot
expire during the permitted oneshot runtime, aggregate status matches child
failures, suspend/resume and clock changes do not duplicate delivery, retry
queues remain bounded, and the installed user session is not hardcoded.
Verify state/cursor/report/notification atomicity and bounded resource use.
Return VERIFIED, REJECTED, or VERIFIED WITH EXPLICIT LIMITATION and stop.
```

### Packet 8J — end-to-end recovery rehearsal

#### Implementation prompt

```text
Implement ONLY Packet 8J from docs/UNIFIED_AGENT_PLAN.md. Build an isolated
staging/recovery rehearsal around the already verified browser, audio, timer,
notification, report, cursor, and action contracts. Exercise process crash,
CDP loss, network failure, model/ASR/TTS unavailability, timer overlap, stale
lease, partial report, notification retry, corrupt state, backup restore,
retention, rollback, and restart. Add bounded CPU/RAM/disk/network limits,
dedupe assertions, audit continuity, and honest degraded-state output.

Create a deterministic failure matrix around write/flush/fsync/rename, lease,
cursor/report/receipt commit, notification send, backup/restore, and migration.
Include `SIGKILL`, disk-full, read-only state, truncated JSON/JSONL/SQLite,
corrupt evidence/hash, partial backups, stale temp files, symlinks, a newer
schema, and concurrent manual/timer/action access. Validate restored schema and
hashes before resume, define RPO/RTO, and prove repeated recovery is idempotent.
A failed rollback step must return a typed bounded path list and block resume;
never swallow it or claim full rollback.

Never run destructive account actions, send real messages, fake remote
rollback, or use production state as a test fixture. Reuse existing stores and
recovery code; do not add parallel persistence. Stop after handoff and do not
start 8K.
```

#### Verification prompt

```text
Verify ONLY Packet 8J. Use isolated temporary state and independently inject
each failure. Prove crash boundaries, leases, cursors, receipts, evidence,
reports, notifications, retention, backups, and audit records recover without
duplication, silent loss, secret leakage, or false success. Inspect actual
resource measurements and failure output, not exit codes only. Repeat recovery
to prove idempotency; verify fsync/rename and kill boundaries, disk-full/read-
only/corrupt/newer-schema cases, concurrent access, restored hashes/schema,
RPO/RTO, and explicit partial-rollback reporting. Confirm no production
account, browser, audio, timer, or user data changed. Return the strict
verification template and stop.
```

### Packet 8K — portability, security, and release certification

#### Implementation prompt

```text
Implement ONLY Packet 8K from docs/UNIFIED_AGENT_PLAN.md. Add clean-checkout
installation, configuration, upgrade, restart, rollback, uninstall, release
artifact, dependency/provenance/license, secret-scan, filesystem-mode,
backup/migration, resource-ceiling, and operator-runbook checks. Prove the
application does not depend on the developer's private state, machine-specific
paths, hidden files, accidental dirty files, or undocumented services. An
uninstall must preserve user state by default and require a separate explicit
confirmation for data deletion.

Build wheel and sdist, inspect both, and install each into a clean HOME outside
the checkout. Run imports, entry points, resources, units, and smoke checks with
no `PYTHONPATH`, source-tree `sys.path` insertion, editable install, or repo-
local virtualenv; repair the current clean-venv check so it cannot mask missing
package contents. Record version/commit provenance, dependency constraints,
licenses/notices, SBOM, vulnerability/secret scans, hashes/signatures, and a
reproducibility comparison. Validate exact units with `systemd-analyze verify`
and a security review.

Reuse the existing packaging, config, migration, permission, and rollback
contracts. Do not weaken security gates, pin secrets, bundle user data, or
enable live accounts/services. Add reproducible clean-clone tests and stop
after handoff; do not start 8L. Test documented Python/platform/display/audio
support, clean first run, upgrade from the previous supported release,
interrupted migration, newer-schema refusal, downgrade/rollback, bounded logs,
and uninstall with state preserved by default.
```

#### Verification prompt

```text
Verify ONLY Packet 8K. Rebuild from a clean clone using documented commands;
inspect the complete artifact and dependency/license/provenance results. Test
install, first run, upgrade, interrupted upgrade, restart, rollback,
uninstall, permissions, backups, migrations, secret handling, resource limits,
and absence of private developer state. Confirm intentional dirty files are not
shipped and all live functionality remains opt-in. Independently build and
install wheel and sdist outside the checkout with no source injection; inspect
package contents, version provenance, SBOM/licenses, vulnerabilities, secrets,
hash/signature and reproducibility evidence, unit verification/security, clean
HOME behavior, platform matrix, previous-release upgrade, newer-schema refusal,
and state-preserving uninstall. Return the strict template with evidence and
any release blocker; do not start another packet.
```

### Packet 8K-P — productized installation, model bootstrap, and desktop integration

#### Implementation prompt

```text
Implement ONLY Packet 8K-P from docs/UNIFIED_AGENT_PLAN.md. Do not start
Packet 8L or any other packet. This packet is a productization layer after 8K;
it must make installation simple without merging the voice bridge and local SLM
into one fragile process.

Read AGENTS.md, the complete plan, the complete prompt pack, the current 8K
commit/report, and the actual installer, config, service, model, and launcher
callers. Run Graft first. Inspect the complete diff before editing.

Preserve every unrelated dirty or untracked file byte-for-byte. Never use
git reset --hard, broad git checkout, destructive cleanup, or deletion of user
state. Do not enable/restart live services or access real accounts, browsers,
microphones, speakers, timers, credentials, models, or production state.

Required outcome: a new user should have one documented installer/launcher and
one first-run path. Internally keep the bridge, inference provider, model
manager, and tray controller as separate supervised components so crashes,
timeouts, model unavailability, or tray failure cannot create duplicate voice
listeners or corrupt state.

Close these observed risks:

1. Canonical identity and migration
   - Resolve the mismatch between `qubit-voice-agent` and
     `openclaw-voice-bridge` in config paths, state/data paths, service names,
     README, diagnostics, scripts, and runtime defaults.
   - Choose one canonical public identity.
   - Preserve existing installations through an explicit, reversible migration
     and compatibility alias.
   - Prove fresh install, upgrade, rollback, and existing-user startup cannot
     read one config while writing another.
   - Never silently create duplicate services or split state directories.

2. One installer, not one fragile shell command
   - Provide one supported installer for the documented platform.
   - It must work from a release artifact, not require a source checkout or
     repository-local `.venv`.
   - Detect or provision the runtime, Python/native dependencies, inference
     provider, audio prerequisites, model storage, service, desktop shortcut,
     and pairing flow.
   - Missing optional capabilities must be typed and actionable, not hidden.
   - Do not require users to discover `uv`, Ollama, PortAudio, model folders,
     systemd, or token setup on their own.
   - Do not use `curl | sh`, root privileges, or undocumented global writes.

3. Model/provider bootstrap
   - Keep the local SLM behind the existing provider contract.
   - Either manage Ollama explicitly or use a supported embedded/local runtime;
     a fresh user must not receive a misleading “installed” state when the
     model provider is absent.
   - Download model files to XDG data locations with temporary files, resume or
     safe retry, size/hash verification, license/provenance records, and
     atomic promotion only after validation.
   - Preflight disk, RAM, VRAM, CPU fallback, and model compatibility.
   - Never silently switch model, provider, GPU, microphone, output, browser,
     display, or account.
   - Keep model-weight licenses separate from application licensing.

4. Configuration and first-run health
   - Add a plain-language first-run wizard and one repair/doctor command.
   - Report runtime, provider/model, microphone/output, display/session,
     permissions, storage, dashboard authentication, service state, and
     migration state.
   - Make each result typed: ready, missing, unavailable, degraded, blocked,
     or failed, with a safe next action.
   - Preserve existing user config and merge new defaults explicitly; never
     overwrite tuned settings silently.
   - Ensure packaged defaults such as personal knowledge and task templates
     exist in artifact installs, not only source checkouts.

5. Service, shortcut, and tray
   - Keep systemd (or the documented supervisor) as the single owner of the
     bridge process and hotkey listener.
   - Add a desktop shortcut that opens the local authenticated dashboard or
     token setup without putting a token in the URL, arguments, or logs.
   - Add an optional tray/status-notifier controller that uses the authenticated
     local API or service manager. It may show status, open the dashboard, open
     diagnostics, and request start/stop/restart, but it must never spawn a
     second bridge or duplicate a listener.
   - Tray/shortcut failure must not break headless or CLI use.
   - Do not hardcode DISPLAY, Wayland/X11 session IDs, home paths, device
     indexes, or service names without validated discovery and migration.

6. Private-source distribution
   - Do not distribute a raw Python wheel as the commercial private-source
     product; it exposes the Python implementation.
   - Define the supported distribution boundary: compiled/native bundle,
     hosted private backend, or another reviewed method.
   - Preserve the proprietary application license and all third-party notices.
   - Never bundle user state, credentials, browser profiles, private paths, or
     personal model files.

7. Security and future-proofing
   - Add tests for interrupted downloads, corrupt models, wrong hashes,
     read-only directories, low disk, missing runtime, provider timeout,
     provider crash, duplicate launch, stale service, stale token, migration
     interruption, rollback, uninstall, and concurrent installer runs.
   - Verify no secrets in command lines, desktop files, unit files, logs,
     reports, artifacts, or diagnostics.
   - Keep all privileged actions authenticated, confirmed where destructive,
     audited, and opt-in.
   - Document offline behavior, backups, update channels, rollback, support
     bundle contents, retention, and escalation.

8. Portable defaults and capability boundaries
   - Define one OS-aware path resolver for config, user data, runtime state,
     model/cache data, logs, exports/backups, and packaged resources. Use XDG
     on Linux and explicit adapters or an early unsupported result on other
     systems; never use repository paths, `/home/<user>` literals, device
     indexes, or guessed application folders as defaults.
   - Create only owned directories with documented permissions. Report the
     effective path map and prove it is stable across restart, upgrade,
     rollback, uninstall, multiple users, and multiple graphical sessions.
   - Represent voice, audio, apps, browser, display, tray, and provider
     integrations as typed capabilities. Detection is not permission: do not
     silently select, reroute, launch, log into, or enable any capability.
     Every optional app adapter must be disabled until explicitly enabled and
     must fail in isolation without adding a listener or changing the core
     voice path.
   - Make first-run setup transactional and resumable with an installer lock
     and journal. An interrupted or concurrent install must converge to one
     valid state, clean partial artifacts, and never report full readiness when
     only part of the product works.

Run focused 8K-P tests, affected 0A–8K hermetic regressions with `-W error`,
compileall, pip check, git diff --check, systemd-analyze verify, package scans,
artifact scans, and Graft build. Build and inspect the release artifact from a
clean clone outside the checkout. Do not claim live audio/browser/service
success; those remain separately authorized qualification work.

Before committing, verify only 8K-P files are staged. Preserve unrelated dirty
and untracked files byte-for-byte. Commit with a clear 8K-P message and stop.
Do not implement Packet 8L automatically.
```

#### Verification prompt

```text
Verify ONLY Packet 8K-P. Rebuild from a clean clone and independently test the
one-installer flow, canonical identity migration, fresh configuration, model
bootstrap, hash/license checks, offline and missing-provider states, service
ownership, desktop shortcut, tray behavior, dashboard pairing, duplicate-launch
prevention, rollback, uninstall, permissions, logs, diagnostics, and private-
source artifact boundaries.

Use temporary HOME/data/config/state directories outside the repository. Do not
touch production services, accounts, browser profiles, audio devices, timers,
models, credentials, or user state. Do not use source-tree injection, editable
installs, repository-local virtualenvs, or tokens in URLs/arguments.

Inspect actual wheel/bundle contents, model manifests, hashes, licenses,
desktop files, units, logs, stderr, permissions, resource measurements, and
failure output. Run focused, affected, full hermetic, clean-clone, install,
rollback, and concurrency tests with warnings treated as errors. Confirm one
service owns one listener, old and new config paths migrate safely, a missing
optional tray never blocks core use, and a missing provider never reports
success.

Return only:
VERDICT: VERIFIED / VERIFIED WITH EXPLICIT LIMITATION / REJECTED
EVIDENCE:
FAILURES:
RELEASE BLOCKERS:
UNTESTED PATHS:
REPAIRS:
FILES CHANGED:
COMMIT:
ROLLBACK:
NEXT SAFE ACTION:

Do not start Packet 8L or claim full public-release readiness if any required
artifact, license, migration, source-privacy, installer, or service-ownership
gate is missing.
```

### Packet 8L — public-release UX, accessibility, mobile, and supportability

#### Implementation prompt

```text
Implement ONLY Packet 8L from docs/UNIFIED_AGENT_PLAN.md. Qualify and improve
the existing dashboard, settings, reports, activity, permissions, websites,
actions, notifications, and diagnostics for desktop and narrow mobile
viewports. Keep one shared authenticated backend and shared design primitives.
Cover onboarding, empty/loading/degraded/error states, keyboard and screen
reader access, focus order, contrast, reduced motion, touch targets,
plain-language copy, timezone/localization, export/delete/retention controls,
and a safe redacted diagnostic bundle. Destructive delete/reset actions must
remain separately authenticated, confirmed, audited, and non-default.

First inventory every route/page/component and write a concise product/design
source of truth. Consolidate only proven duplicate tasks. Move navigation,
buttons, fields, badges, dialogs, authenticated API/error handling, design
tokens, typography, spacing, color, and motion into shared primitives. Migrate
reports/activity/approvals and every remaining direct fetch to the shared typed
authenticated helper. Never parse unchecked/non-JSON responses. Render all
backend/user/page data with safe DOM APIs or an audited sanitizer; remove raw
dynamic `innerHTML` paths.

Treat WCAG 2.2 AA as required: automated checks plus manual keyboard/screen-
reader use, visible focus and restoration/traps, announced live states,
44-by-44 touch targets, 320-pixel reflow, 200% zoom, forced colors/high
contrast, and reduced-motion alternatives. Test desktop/mobile browser matrix,
session expiry/re-pairing, offline/reconnect/stale data, slow/empty/partial/
failed states, bounded DOM/history growth, performance budgets, and critical-
state visual regressions. Keep the ten nice-to-haves in the plan non-blocking.

Do not change backend safety semantics, expose secrets/private page text, add
optimistic success, or create a duplicate page/system. Use existing UI
contracts and add visual, accessibility, responsive, and interaction tests.
Stop after handoff and do not start another packet.
```

#### Verification prompt

```text
Verify ONLY Packet 8L. Test the real authenticated dashboard at desktop and
narrow mobile widths against the same backend. Inspect keyboard-only and
screen-reader semantics, focus, contrast, reduced motion, touch targets,
loading/error/degraded states, plain-language notifications, data lifecycle,
and redacted diagnostics. Verify WCAG 2.2 AA with automation and manual flows,
320-pixel reflow, 200% zoom, forced colors, 44-pixel targets, focus restoration,
live announcements, session expiry/re-pairing, offline/reconnect, bounded DOM,
performance budgets, and desktop/mobile visual regressions. Inspect the route/
component inventory and prove all API calls use the shared authenticated typed
helper and all dynamic data is safely rendered. Confirm no frontend-only state,
secret, raw page content, or duplicate route exists. Return VERIFIED, REJECTED,
or VERIFIED WITH EXPLICIT LIMITATION and stop.
```

## Phase 8 capability-expansion and release packets 8M–8T

These packets are optional extensions after 8L. They add broad, user-enabled
capabilities without allowing model/page/tool text to grant authority. Use the
existing broker, ActionBroker, 7E permissions, 8E integrations, 8F action
contracts, 8K-P installation, and 8L UI primitives. Minimal confirmations are
allowed only for low-risk read or reversible actions under a narrow,
time-limited user-issued session grant. Sending, publishing, purchasing,
deleting, account/security changes, arbitrary shell, external messages, and
irreversible actions always require a fresh confirmation.

### Packet 8M — GENOME-compatible semantic memory integration

#### Implementation prompt

```text
Implement ONLY Packet 8M from docs/UNIFIED_AGENT_PLAN.md. Evaluate and, if the
evidence justifies it, add a narrow optional semantic-memory adapter based on
the pinned GENOME contract. Do not copy the repository wholesale, replace the
existing MemoryStore, add an unauthenticated MCP bypass, or change the voice
hot path or resident model contract.

Before editing, perform the complete section 6.1 preflight, read AGENTS.md and
the entire plan, use Graft first, record dirty files and allowed paths, and
read docs/LICENSING.md, THIRD_PARTY_NOTICES.md, and docs/UPSTREAM_REUSE.md.
Pin the exact GENOME commit. Record source files, tests, Apache-2.0 license,
notices, model licenses, dependencies, and local changes before adapting code.

Preserve scope isolation, provenance/trust levels, secret rejection,
confidence, expiration, correction, deletion, retention, bounded prompt
context, redaction, crash recovery, and the rule that memory never trains or
self-modifies the model. Keep local ingest LLM-free by default. Optional
embeddings, reranking, or extraction must be explicitly configured, resource
bounded, and reported with typed missing/degraded states.

Add provider selection, migration/export compatibility, deterministic replay or
hash checks, web/tool/model provenance tests, prompt-injection tests, resource
benchmarks, restart/recovery tests, and rollback. The optional provider must
fail isolated and must not create a second store, server, listener, or policy
path. Keep it disabled by default and stop after the handoff.
```

#### Verification prompt

```text
Verify ONLY Packet 8M. Independently inspect the pinned GENOME source, tests,
Apache-2.0 license, notices, model licenses, dependency footprint, and every
local adapter caller. Prove the existing MemoryStore remains authoritative or
that the migration contract is explicit, reversible, scoped, redacted, and
bounded.

Test local ingest without an LLM/network, semantic recall, provenance/trust
levels, temporal facts, explainability, deterministic journal/replay, duplicate
and correction behavior, expiration, forget/export, crash recovery, corrupt or
newer state, concurrent access, resource limits, and rollback. Prove web/tool/
model-origin text cannot overwrite user facts or grant authority, and that no
memory path trains or self-modifies the model.

Confirm the optional provider is disabled by default, missing-model and
dependency failures are typed, no unauthenticated MCP/API bypass exists, no
secret/private page text leaks into logs or prompts, and the voice hot path and
resident model contract are unchanged. Run focused, affected, warning,
security, persistence, resource, clean-install, and rollback tests plus
compileall, pip check, diff check, and Graft build. Return the strict
verification template and stop; do not start 8N or 8O.
```

### Packet 8N — Ghost desktop and browser capability adapter

#### Implementation prompt

```text
Implement ONLY Packet 8N from docs/UNIFIED_AGENT_PLAN.md. Evaluate and, only
if justified, integrate Ghost as an optional supervised adapter through the
existing CapabilityBroker and ActionBroker. Do not bypass 7E permissions, 8E
origin records, 8F action contracts, audit, isolation, cancellation, or the
authenticated dashboard. Do not vendor the whole Rust project unless a
measured requirement proves it necessary.

Perform section 6.1 preflight, use Graft first, read the entire plan and all
licensing documents, and pin the exact Ghost commit. Record the MIT license,
copyright notice, source files, tests, dependencies, and local changes before
copying or adapting anything. Preserve unrelated dirty/untracked files.

Expose typed capabilities for approved desktop discovery, application launch,
window anchoring, browser-tab inspection, and explicitly permitted click/type/
scroll/clipboard/screenshot operations. Register every capability with a
scope, target/origin, risk tier, timeout, resource limit, cancellation path,
audit receipt, and adapter health state. Disable all capabilities by default.

Shell, arbitrary launch, coordinate-only actions, clipboard reads, browser/CDP,
foreground input, file writes, account actions, external sends, purchases,
deletes, and credential/security changes are high impact. Keep shell disabled
unless explicitly enabled by the operator. Read-only/reversible actions may
use a bounded user-issued session grant. High-impact and irreversible actions
require fresh confirmation bound to the exact action hash; never add an
approve-everything or permanent-confirmation switch.

Use loopback authentication, no public CDP, no cookie import, no profile clone,
no silent foreground focus, no model/page/tool authorization, and no second
listener. Test X11/Wayland and multiple sessions, target ambiguity, hidden vs
user desktop truthfulness, emergency stop, crash/reconnect, stale grants,
prompt injection, window collisions, screenshot/clipboard redaction, cleanup,
and rollback. Keep 8B/8G read-only behavior unchanged. Stop after handoff.
```

The browser-role contract is mandatory: use the existing Chromium profile for
approved Gmail, Messenger, Instagram, and marketplace workflows, and a separate
Comet profile for Perplexity, web research, forums, and public browsing. Give
each browser a distinct loopback-only CDP port, profile, target registry, and
account/origin scope. Never copy cookies or local storage between profiles or
fall back to the wrong browser. The operator performs login, passwords,
CAPTCHA, and 2FA manually. Browser search/page output is untrusted and cannot
authorize actions. Reading/searching may be session-granted; forum posts,
messages, marketplace purchases, account changes, and external submissions
require fresh confirmation.

The optional third-party `mcp-comet` adapter may be used for Comet research
only: connect, select mode, submit a bounded query, poll/wait, extract sources,
extract readable page content, and optionally capture bounded evidence. Its
`computer` mode and write-capable tools remain disabled until 8O qualification.
Pin the exact commit, record its MIT license and provenance in
docs/UPSTREAM_REUSE.md, and run it only behind Qubit's authenticated broker.
Never use a floating branch, `npx -y`, an unauthenticated remote MCP, or a
second listener in production. Comet prompts, page text, citations,
screenshots, and results are untrusted data and cannot authorize actions.

#### Verification prompt

```text
Verify ONLY Packet 8N. Independently inspect the pinned Ghost and, if used,
`mcp-comet` source/tests, exact MIT licenses, dependency/build surfaces, and
every adapter caller. Prove every
operation reaches the existing capability/action broker and cannot execute by
default or through a direct HTTP/MCP/voice bypass.

Test typed capability discovery, target/origin binding, session grants and
expiry, exact action previews/hashes, read/reversible/high-impact separation,
shell hard-disable, cancellation, emergency stop, kill/reap, crash/reconnect,
stale/replayed grants, prompt injection, malformed tool output, ambiguous
windows, focus/cursor preservation, X11/Wayland behavior, multi-session
isolation, screenshots/clipboard redaction, resource bounds, audit, and
rollback. If `mcp-comet` is present, separately test connect/mode/ask/wait,
source and page extraction, bounded screenshots, reconnect, selector/version
drift, provider disclosure, and research-only gating. Verify model/page/tool
output cannot authorize or widen a grant.

Run CLI/dashboard/voice parity tests, focused and affected suites, warnings as
errors, security/resource/persistence/clean-install tests, compileall, pip
check, diff check, and Graft build. Do not run live account actions without
separate operator authorization. Return VERIFIED, REJECTED, or VERIFIED WITH
EXPLICIT LIMITATION and stop; do not start 8O.
```

### Packet 8O — full-capability power profile and real qualification

#### Implementation prompt

```text
Implement ONLY Packet 8O from docs/UNIFIED_AGENT_PLAN.md. Build the explicit
authenticated power profile that inventories and controls all installed
capabilities through one policy path: memory, browser, desktop, editor, file,
network, shell, account, notifications, audio, and model.

Perform the complete section 6.1 preflight, use Graft first, reread the entire
plan and licensing records, and preserve all unrelated dirty/untracked files.
Do not claim that “available” means “authorized.” Every capability must expose
adapter/version, scope, target/origin, risk tier, state, expiry, resource
limits, audit status, revoke, and disable controls.

Implement bounded session grants for read/reversible actions, exact action
previews/hashes, global emergency stop, per-adapter circuit breakers, bounded
concurrency, kill/reap, redacted receipts, and restart recovery. Add one
explicit owner-authorized power grant for the operator-selected browsers,
apps, accounts, origins, directories, and duration. Inside that grant,
routine browsing, searching, reading, navigation, and exact-target
non-sensitive form filling must not ask for repeated confirmation. Drafting
remains local and non-external. Posting, sending, publishing, purchasing,
deleting, and modifying project code must always ask for fresh confirmation.
The grant must remain visible, revocable, expiring, target-bound, and audited,
and must never widen silently to another browser, account, origin, directory,
or unbounded shell. Use the same semantics for voice, dashboard, CLI, MCP,
tray, and automation.

The live qualification matrix must cover Chromium for Gmail, Messenger,
Instagram, and marketplaces plus Comet for Perplexity, web research, forums,
and public browsing. Prove separate ports/profiles, target and account binding,
manual authentication handoff, no cross-profile cookies, no wrong-browser
fallback, session expiry/reconnect/restart, and that search results cannot
authorize a tool. Test read, fill, draft, post/send, and purchase separately.
Reading, navigation, and exact-target non-sensitive filling may use a bounded
session grant. Drafting is local and non-external. Posting, sending,
publishing, and purchasing always require fresh approval.

If `mcp-comet` is used, qualify its research tools separately from Ghost and
Qubit's native Chromium adapter: connect, mode, ask, wait/poll, source
extraction, page-content extraction, and bounded screenshot evidence. Prove
that it cannot expose a second policy path, leak Comet context beyond the
configured provider, or turn research output into authorization.

Qualify real hardware, desktop sessions, browser targets, model, memory
provider, and optional accounts only after explicit operator authorization and
with disposable/controlled test data. Separate hermetic from live evidence.
Measure latency and CPU/RAM/disk/network/context/screenshot limits. Inject
prompt injection, confused-deputy, stale-grant, replay, target-switch,
adapter-crash, offline, rollback, and uninstall failures. Do not claim live
success from mocks. Keep the safe 8L release and existing 8B behavior intact.
Stop after handoff.
```

#### Verification prompt

```text
Verify ONLY Packet 8O. Inspect the full capability inventory and prove one
authenticated policy path across voice, dashboard, CLI, MCP, tray, and
automation. Confirm low-risk session grants are narrow, expiring, target/
origin-bound, auditable, and revocable; confirm high-impact actions always
require a fresh exact-hash confirmation. If no executing tray controller or
MCP server is shipped, report the tray/MCP surfaces as typed UNAVAILABLE/
DISABLED (status-only) and confirm the plan never claims an execution path
exists for them — availability is never fabricated.

Independently run focused, affected, security, resource, concurrency,
recovery, clean-install, rollback, and live tests where explicitly authorized.
Exercise real supported hardware, desktop sessions, browser targets, model,
memory provider, and disposable accounts only with operator authorization.
Measure actual latency and resource ceilings. Test prompt injection, confused
deputy, stale/replayed grants, target switching, malformed adapter output,
crash/reconnect, emergency stop, offline mode, audit/redaction, and uninstall.

Confirm no adapter can bypass the broker, 7E permissions, 8E scopes, 8F action
contracts, isolation, or emergency stop. Confirm the owner-authorized grant
works across the selected browser/app/account scopes without repeated prompts
for routine read/navigation/fill actions, while posts, sends, publishing,
purchases, deletions, and project-code modifications still require fresh
confirmation. Return VERIFIED, REJECTED, or VERIFIED WITH EXPLICIT LIMITATION
and stop. Do not start another packet.
```

### Packet 8P — verified skill memory and controlled agent evaluation

#### Implementation prompt

```text
Implement ONLY Packet 8P from docs/UNIFIED_AGENT_PLAN.md. Evaluate and, if
justified, adapt the smallest tested concepts from the pinned Praxis and Evolve
commits. Praxis may contribute verified-before-remember skills, negative
failure records, bounded retrieval, deduplication, retention, and acceptance
tests. Evolve may contribute offline experiments, explicit feedback, and
bounded statistical comparison of prompt or model candidates.

Before editing, perform section 6.1 preflight, use Graft first, preserve all
dirty/untracked work, and read AGENTS.md, the entire plan, docs/LICENSING.md,
THIRD_PARTY_NOTICES.md, and docs/UPSTREAM_REUSE.md. Pin exact commits and
record repositories, source paths, tests, licenses, notices, dependencies,
model licenses, and local changes before production code. Do not copy either
repository wholesale, add an unauthenticated MCP bypass, or create a second
memory store, model server, policy path, or listener.

Keep the feature optional and disabled by default. Verified skills must use the
existing 7E manifest, provenance, broker, scope, resource, audit, redaction,
rollback, and emergency-stop contracts. Failed, expired, revoked, or stale
skills must be quarantined. Retrieval must be top-K and context-budget bounded;
it must never train or self-modify the model. Keep the existing MemoryStore
authoritative unless an explicit reversible migration is verified.

Evolve-style evaluation must be offline or use explicitly configured local
Ollama by default. It may propose prompt, model, or behavior candidates, but it
must never automatically change permissions, confirmation policy, browser or
account scopes, safety rules, production model selection, or external actions.
Promotion requires explicit owner approval and passing regression, security,
latency, resource, provenance, and rollback gates.

Do not store or transmit raw private prompts, credentials, browser text, source
code, or secrets. Add typed provider states, migration/export, deterministic
replay or hashes, correction/forget behavior, crash recovery, concurrency and
resource tests, redacted audit, clean-install checks, and rollback. Do not
change the voice hot path or resident-model runner contract. Commit only Packet
8P files and stop after the handoff; do not start another packet.
```

#### Verification prompt

```text
Verify ONLY Packet 8P. Independently inspect the pinned Praxis and Evolve
source/tests, exact licenses, notices, dependency footprint, and every local
caller. Prove only the smallest justified units were adapted and that no whole
repository, second authority, unauthenticated MCP path, or hidden listener was
added.

Test verified-before-remember behavior, negative skills, acceptance-test
provenance, bounded top-K retrieval, deduplication, retention, explicit
feedback, offline experiment comparison, deterministic replay, correction,
forget/export, crash recovery, corrupt/newer state, concurrency, resource
limits, redaction, prompt-injection resistance, and rollback. Prove failed or
stale skills cannot become authority and no memory/evaluation path trains or
self-modifies the model.

Confirm Evolve-style evaluation cannot automatically change permissions,
confirmation rules, browser/account scopes, safety policy, or production model
selection. Confirm external providers receive no private prompts, source code,
credentials, browser text, or secrets by default. Verify CLI/dashboard/voice
parity where exposed, disabled-by-default behavior, typed missing/degraded
states, clean installation, audit, and removal without user-data loss.

Run focused, affected, warning, security, persistence, resource, clean-install,
and rollback tests plus compileall, pip check, diff check, and Graft build.
Return VERIFIED, REJECTED, or VERIFIED WITH EXPLICIT LIMITATION and stop; do not
start another packet.
```

### Packet 8Q — frontier-provider gateway and intelligent model routing

#### Implementation prompt

```text
Implement ONLY Packet 8Q from docs/UNIFIED_AGENT_PLAN.md. Add a narrow,
optional provider gateway for the local model, direct frontier APIs, OpenCode
Zen, and OpenRouter. OpenCode Zen is a hosted API provider; OpenCode itself is
a separate coding-agent application and is not part of this packet. Do not
start Packet 8R, add a coding-agent subprocess, or modify browser, account,
audio, timer, memory, or existing action behavior.

Perform the complete section 6.1 preflight, use Graft first, read the entire
plan and all licensing/provenance documents, inspect every current model caller,
and preserve every unrelated dirty or untracked file. Record the exact API
contracts and current provider documentation used. Do not invent a provider
protocol or hardcode an unverified model catalog.

Keep local inference as the default. Implement explicit local/frontier route
selection and an opt-in auto-escalation route. Cloud routing must be disabled
by default and must require an owner-configured provider/model allowlist,
privacy/data-egress policy, token/context/response limits, timeout, retry and
concurrency limits, and daily/monthly spending budget. Automatic escalation
must never happen merely because page, tool, model, or provider text requests
it. Unknown, stale, unavailable, over-budget, or unsupported models fail
closed or use a clearly typed local fallback.

Create one normalized provider contract for request, streaming, cancellation,
structured output, tool-call capability declarations, usage, cost metadata,
latency, and typed errors. Expose provider/model identity in results so a local
fallback cannot be mistaken for a frontier response. Make capability mismatch,
rate limit, authentication failure, timeout, outage, content-policy refusal,
and cancellation limitations visible.

Use environment variables or an OS keyring/secret reference for credentials.
Never store keys in git, packaged config, frontend state, prompts, logs,
reports, telemetry, crash dumps, or memory. Do not read OpenCode's private
credential file as an undocumented dependency. Qubit must not require a cloud
provider for local operation.

Implement minimum-necessary cloud egress. Classify and redact user, project,
browser, account, memory, credential, token, cookie, and raw-audio data before
external requests. Require explicit configuration or per-request approval for
private context. Add provider disclosure, privacy tier, model, cost estimate,
and external-data warning to CLI/dashboard output.

Add catalog/source/timestamp validation, bounded token/context/response sizes,
request-rate and spend limits, retry idempotency, circuit breakers, offline
behavior, and deterministic redacted usage records. Do not allow retries to
duplicate an external action. Preserve the Piper/ASR/voice hot path and the
resident local-model contract.

Add hermetic tests for every provider state, no-key behavior, malformed and
oversized responses, schema/tool mismatch, catalog drift, rate limits,
timeouts, cancellation, outage, cost limits, redaction, prompt injection,
local fallback, restart, rollback, and no-network local mode. Add live tests
only as a separately authorized canary. Update docs/UPSTREAM_REUSE.md with
provider documentation, licenses/terms, dependencies, endpoint decisions, and
local changes. Commit only Packet 8Q files and stop after handoff.
```

#### Verification prompt

```text
Verify ONLY Packet 8Q. Independently inspect every provider caller and prove
local/frontier/auto routing uses one normalized contract with truthful
provider/model identity, typed failures, bounded retries, cancellation,
concurrency, token/context/response limits, circuit breaking, and spend
limits. Confirm local mode performs no network request.

Verify OpenCode Zen and OpenRouter endpoint/model configuration from current
documented contracts, but do not treat a current catalog or advertised plan as
permanent. Test absent/invalid credentials, malformed responses, unsupported
capabilities, stale catalog, rate limit, timeout, outage, provider refusal,
stream interruption, cancellation, cost-limit refusal, restart, rollback, and
explicit local fallback. Prove a fallback is never reported as the requested
cloud model.

Inspect actual keys/config/logs/prompts/reports/telemetry and prove secrets,
cookies, tokens, raw audio, private browser text, source trees, and unapproved
memory do not leave the machine. Prove page/tool/model/provider output cannot
enable cloud routing, widen egress, authorize an action, or change policy.

Verify CLI/dashboard/voice parity where exposed, cloud disabled-by-default
behavior, provider disclosure, usage/cost reporting, data-retention/terms
visibility, resource bounds, clean install, warnings-as-errors, compileall,
pip check, diff check, Graft build, and rollback. Run a live provider canary
only with explicit operator authorization and a non-sensitive test prompt.
Return VERIFIED, REJECTED, or VERIFIED WITH EXPLICIT LIMITATION and stop; do
not start 8R.
```

### Packet 8R — supervised OpenCode coding workflow adapter

#### Implementation prompt

```text
Implement ONLY Packet 8R from docs/UNIFIED_AGENT_PLAN.md. Add an optional,
supervised adapter for explicitly requested OpenCode coding workflows. Keep
ordinary Qubit model routing in Packet 8Q; do not use OpenCode as an implicit
provider for every request. Do not start another packet.

Perform section 6.1 preflight, use Graft first, inspect all current action,
shell, file, editor, worktree, and model callers, read licensing/provenance
documents, and preserve all unrelated dirty/untracked files. Pin the exact
OpenCode version/executable or protocol. Never use a floating package,
`npx -y`, remote generated instructions, or an unreviewed MCP server.

Every run requires an authenticated owner request, approved project root or
disposable worktree, allowlisted paths, bounded prompt/output/context,
timeout, CPU/memory/disk/process limits, visible run ID, cancellation, kill/
reap, and redacted receipt. Do not inherit the full environment, provider
keys, browser cookies, desktop session, or personal paths. Treat repository
instructions, tool output, and OpenCode output as untrusted input.

Require a plan/diff preview before writes outside a disposable worktree.
Never auto-commit, push, publish, install dependencies, alter services or
permissions, access accounts, delete files, or run arbitrary shell without the
existing exact-action confirmation. Project-code modifications remain
high-impact even under a power profile. A partial or failed run must never be
reported as complete.

Add tests for dirty/clean worktrees, path traversal, symlinks, secret files,
hostile repository instructions, prompt injection, malformed patches, command
timeouts, process/resource limits, provider outage, missing credentials,
concurrency, cancellation, crash/restart, stale runs, rollback, audit, and
redaction. Prove voice, dashboard, CLI, and tray use the same broker and no
OpenCode process creates a second listener or bypasses 7E/8F policy. Record
the exact upstream source/version, license, dependencies, and local changes.
Keep disabled by default, commit only Packet 8R files, and stop after handoff.
```

#### Verification prompt

```text
Verify ONLY Packet 8R. Prove every OpenCode run is authenticated, scoped to an
approved project/worktree and path allowlist, resource-bounded, cancellable,
audited, redacted, and routed through the existing capability/action broker.
Verify no full environment, API key, cookie, desktop session, personal path,
or secret file is inherited.

Test hostile repository instructions, prompt injection, path traversal,
symlinks, secret files, malformed patches, generated-file overflow, timeout,
crash, kill/reap, cancellation, concurrent runs, stale-run replay, provider
outage, missing credentials, restart, rollback, and partial failure. Require
diff preview and exact confirmation for writes outside a disposable worktree.
Prove no automatic commit/push/publish/install/service/permission/account or
destructive action occurs.

Verify CLI/dashboard/voice/tray parity, no second listener, no direct shell or
MCP bypass, license/provenance records, disabled-by-default behavior, clean
install, resource measurements, warnings-as-errors, compileall, pip check,
diff check, Graft build, and rollback. Live coding runs require explicit
operator authorization and a disposable worktree. Return VERIFIED, REJECTED,
or VERIFIED WITH EXPLICIT LIMITATION and stop.
```

### Packet 8R closure repair before Packet 8S

#### Repair prompt

```text
Repair ONLY the rejected Packet 8R closure findings. Do not start Packet 8S,
8T, or any other packet. Read AGENTS.md, the complete plan, this prompt pack,
OPERATIONS_8R.md, the previous 8R verification, licensing/provenance records,
and the entire 8R diff. Use Graft first. Preserve every unrelated dirty or
untracked file byte-for-byte and use no reset --hard, git clean, broad
checkout, or destructive cleanup.

Close every finding with implementation plus regression tests:

- status/version probing must never execute an arbitrary configured binary
  outside the broker, must use a strict sanitized environment, and must not
  run while the adapter is disabled unless the operation is explicitly typed
  and authorized;
- all disposable worktrees and generated temporary data must be cleaned in a
  finally path after success, cancellation, timeout, transport exception,
  worker crash, and partial failure, with orphan recovery and truthful output;
- malformed OpenCode JSON/events/patches must fail closed rather than fall
  back to a successful raw-text result;
- task files must be inside the approved project/worktree scope, reject
  symlink escapes and secret-target files, and never read an arbitrary path;
- environment passthrough must be a small explicit allowlist and reject HOME,
  PYTHONPATH, LD_PRELOAD, BASH_ENV, XDG config/runtime, Git override, shell,
  display/session, credential, token, cookie, and API-key variables;
- enforce generated-file count/bytes/disk/process/resource limits, bounded and
  validated diffs, path/symlink containment, and truthful partial failure;
- add real broker callers or explicitly revise the Packet 8R parity contract
  for voice and tray; never claim parity from a unit test that calls the
  adapter directly;
- enforce the pinned executable/version policy or document and test an
  explicit operator-gated mismatch state.

Keep OpenCode disabled by default. Do not add a second listener, MCP bypass,
automatic commit/push/publish/install/service/permission/account action, cloud
egress, or arbitrary shell. Run focused 8R tests repeatedly, affected and
full-hermetic tests with warnings as errors, security/resource/persistence/
clean-install tests, compileall, pip check, diff check, and Graft build.
Inspect warnings and failure output. Commit only the 8R repair files and
return the strict verification template with evidence, failures, untested
paths, repairs, rollback, and next safe action. Stop after the handoff.
```

### Packet 8S — Qubit VoiceBridge identity and release migration

#### Implementation prompt

```text
Implement ONLY Packet 8S from docs/UNIFIED_AGENT_PLAN.md. Perform the final
public-identity migration after Packet 8R. The public product name is Qubit
VoiceBridge, the canonical installed identity is qubit-voicebridge, and the
canonical repository folder is qubit-voicebridge. Keep the internal Python
import package voicebridge unchanged. The existing openclaw-voice-bridge name
is legacy compatibility only.

The physical repository-folder rename is in scope. Do not move the active
dirty checkout during implementation. Rehearse and verify the rename from a
clean clone/release directory, prove Git metadata, packaging, scripts, and
documentation work from the new qubit-voicebridge path, and document the
operator step needed to move an existing checkout safely.

Before editing, verify that Packet 8R has an independent VERIFIED closure. If
the 8R closure is rejected or has unresolved security/boundary findings, stop
and use the Packet 8R closure-repair prompt first; do not mask those findings
inside the identity migration.

Perform section 6.1 preflight, use Graft first, inspect all current product,
installer, packaging, service, shortcut, config, state, cache, data, CLI,
dashboard, documentation, and release callers. Preserve every unrelated
dirty or untracked file byte-for-byte. Do not modify Packet 8R behavior or
capability policy.

Inventory and consistently update user-facing branding in package metadata,
CLI help, dashboard titles, installer output, systemd units, desktop shortcuts,
release artifacts, documentation, examples, diagnostics, and support text.
Record intentional legacy references instead of blindly replacing them.

Implement a reversible, idempotent, atomic migration from
openclaw-voice-bridge config/state/data/cache/log/service paths to canonical
Qubit VoiceBridge paths. Never read one tree while writing another. Preserve a
tested legacy compatibility alias where practical. Never overwrite a real legacy
directory, follow unsafe symlinks, delete user data, expose tokens in URLs or
shortcuts, or copy secrets into logs, reports, artifacts, or metadata.

Test fresh canonical install, legacy-only install, mixed/split trees, partial
and interrupted migration, repeated migration, upgrade, rollback/downgrade,
uninstall, read-only and disk-full failures, symlink/path traversal attempts,
service restart, shortcut launch, and preservation of configs, state, memory,
reports, browser settings, and audio settings. Prove exactly one effective
config/state tree is used after each transition.

Do not rename the voicebridge import package, add dependencies, add a listener,
change browser/account/audio/model/timer behavior, weaken permissions, or
change Packet 8R execution boundaries. Use safe rollback with git revert or
reviewed file-specific patches; never use reset --hard, git clean, or broad
checkout. Commit only Packet 8S files and stop after the handoff.

Run focused migration/branding tests, affected release/install/unit/UI tests,
warnings-as-errors, security/path/redaction/persistence/resource tests,
clean-install and rollback tests, compileall, pip check, git diff --check,
and Graft build. Inspect actual output and preserve exact counts.
```

#### Verification prompt

```text
Verify ONLY Packet 8S. Independently inspect the complete identity and
migration diff and every product, installer, packaging, service, shortcut,
config, state, data, CLI, dashboard, documentation, and release caller.
Confirm Qubit VoiceBridge is the canonical public identity,
qubit-voicebridge is the canonical installed identity and repository folder,
voicebridge remains the internal import package, and
openclaw-voice-bridge is only a documented legacy compatibility identity.

Independently verify the physical folder migration from a clean clone. Confirm
the project builds, installs, launches, resolves resources, and performs
upgrade/rollback correctly from qubit-voicebridge, while an existing
openclaw-voice-bridge checkout remains safely readable during migration.

Test fresh install, canonical install, legacy-only install, mixed/split trees,
partial/interrupted migration, repeated migration, upgrade, rollback,
uninstall, read-only and disk-full failures, unsafe symlinks, path traversal,
service restart, desktop shortcut launch, and preservation of config, state,
memory, reports, browser settings, audio settings, and user data. Prove the
runtime never reads one tree while writing another and that exactly one
effective config/state tree exists.

Verify no real legacy directory is overwritten, no secret/token appears in a
URL, shortcut, log, report, artifact, or diagnostic, no duplicate service or
listener is created, and no migration silently changes browser, account,
audio, model, timer, permission, or Packet 8R behavior. Confirm compatibility
aliases are atomic, idempotent, fail closed, and safely reversible.

Run focused, affected, security, persistence, resource, clean-install,
upgrade, rollback, warnings-as-errors, compileall, pip check, git diff --check,
and Graft build. Inspect actual warnings and failure output. Confirm all
unrelated dirty/untracked files are byte-identical and no unsafe rollback
command was used. Return VERIFIED, REJECTED, or VERIFIED WITH EXPLICIT
LIMITATION and stop; do not start another packet.
```

### Packet 8T — one-click setup, installation health, and lifecycle dashboard

#### Implementation prompt

```text
Implement ONLY Packet 8T from docs/UNIFIED_AGENT_PLAN.md, after Packet 8S has
an independent VERIFIED result. Do not start another packet. Perform section
6.1 preflight, use Graft first, inspect the existing installer, CLI, config,
profiles, release/upgrade/rollback, service, model, provider, audio, pairing,
dashboard, and diagnostics callers. Preserve every unrelated dirty or
untracked file byte-for-byte.

Build one authenticated, backend-authoritative setup and operations surface.
Provide a staged one-click setup flow with platform detection, prerequisite
checks, standard per-user directories, declared component install/repair,
local model/runtime setup, audio selection/testing, optional desktop
integration, resumable progress, dry-run, cancellation, bounded logs, typed
states, and safe rollback. Network downloads, service changes, desktop
integration, provider enablement, and data migration require explicit owner
confirmation. Never silently elevate privileges, start a service, overwrite
user configuration, delete state, or copy secrets.

Expose authenticated dashboard and CLI inventory for actual installed version,
package/source provenance, OS/session, dependencies, model files and hashes,
resident local model, provider/catalog state, credential-reference state,
ASR/TTS, microphone/output, browser/CDP, timer, permissions, storage, and
service state. Return references and typed statuses only: never key values,
cookies, tokens, private paths, raw page text, or raw audio. The frontend must
render backend states and may not invent readiness or repair success.

Add dry-run and exact-confirmation controls for safe credential references,
voice/audio and model/provider configuration, device tests, upgrade, rollback,
missing-component repair, redacted diagnostics, and documentation. Preserve
user data and tuned configuration by default; never silently restart/enable a
service. Make partial repair/rollback visible and resumable.

Include these ten improvements: resumable first-run checklist; redacted
diagnostic export; provenance/update-channel view; model capability matrix;
configuration profiles with diff/reset/import/export; microphone/output wizard;
offline degraded mode with reconnect; ordered repair planner; migration and
backup center; and local audit/change timeline.

Test desktop/mobile onboarding, keyboard/screen-reader semantics, 320px
reflow, 200% zoom, forced colors, reduced motion, 44px targets, offline/
reconnect, session expiry, stale health, concurrent setup, crash/resume,
cancellation, read-only/disk-full/permission failures, missing models, bad
hashes, unavailable providers, wrong audio devices, rollback, uninstall, and
clean install. Prove CLI/dashboard parity, backend ownership of every state,
redacted diagnostics, bounded DOM/logs/resources, no duplicate route/listener,
and no unapproved voice-hot-path change. Record provenance for reused code.
Keep cloud, browser, timer, and tray features disabled unless explicitly
enabled. Run focused, affected, security, persistence, resource, clean-install
and rollback tests with warnings as errors, compileall, pip check, diff check,
and Graft build. Commit only Packet 8T files and stop after the handoff.
```

#### Verification prompt

```text
Verify ONLY Packet 8T. Independently inspect the complete setup, installer,
health, inventory, configuration, model/provider, credential-reference,
voice/audio, upgrade/rollback, repair, diagnostics, CLI, dashboard, mobile,
service, and migration diff plus every caller.

Prove one authenticated backend state is shared by CLI and dashboard. Test
fresh install, one-click setup, resume, cancel, dry-run, missing/degraded/
blocked/failed/success states, dependency/model/provider inventory, safe key
reference status, local model identity, ASR/TTS/device selection, wrong-device
guidance, upgrade, rollback, repair, uninstall, and redacted diagnostics.
Test desktop and narrow mobile widths, 320px reflow, 200% zoom, keyboard and
screen reader flows, focus, contrast, forced colors, reduced motion, 44px
targets, offline/reconnect, session expiry, stale data, concurrent setup,
crash/resume, read-only/disk-full/permission failures, path traversal, unsafe
symlinks, duplicate services/listeners, and partial rollback.

Confirm no key/token/cookie/private path/raw page text/raw audio appears in
records, logs, reports, diagnostics, URLs, or frontend state. Confirm no
frontend-only success, no silent service restart/enable, no hidden network
download, no privilege escalation, no data deletion, no duplicate route, and
no voice/audio/model/timer/permission behavior changed outside the packet.
Run focused, affected, accessibility/UI, security, persistence, resource,
clean-install, upgrade, rollback, warnings-as-errors, compileall, pip check,
git diff --check, and Graft build. Inspect warnings and failure output, prove
the ten listed improvements, and return VERIFIED, REJECTED, or VERIFIED WITH
EXPLICIT LIMITATION with evidence, failures, untested paths, repairs,
rollback, and next safe action. Stop; do not start another packet.
```

## Final handoff reminder

Every agent must end with evidence and rollback. “Implemented” means code
exists. “Verified” means an independent agent reproduced the relevant behavior.
“Degraded” means a limitation is visible. “Done” is not a valid implementation
handoff status.

### Packet 8T-F — Qubit Agent foundation migration before Packet 8U

#### Implementation prompt

```text
Implement ONLY Packet 8T-F. Do not start Packet 8U or any later packet.
Read AGENTS.md, docs/8TF_SOURCE_OF_TRUTH.md, docs/QUBIT_FULL_PARITY_CONTRACT.md,
the complete plan, this prompt pack, docs/OPERATIONS_8T_F.md,
docs/8TF_COMPLETE_MIGRATION_PLAN.md,
the current Qubit source, the current dirty-tree state, and all relevant
licensing/provenance records.

Use docs/8TF_COMPLETION_CHECKLIST.md as the required execution checklist.
Check every item with inspectable evidence. Do not report completion while a
required box is unchecked.

NON-NEGOTIABLE OWNER REQUIREMENT: this packet is a complete Hermes
rebrand/parity migration, not a supervisor-only integration. Do not stop after
proving that Qubit can launch the real Hermes binary or complete Hermes chat
turns. Read the full parity contract and inventory the pinned Hermes and
OpenClaw CLI, dashboard, gateway, protocol, skill, memory, model, session,
schedule, plugin, messaging, browser, project, board, and coding surfaces.
Implement every requested capability behind Qubit’s public CLI, dashboard, and
voice interface as appropriate. Preserve behavior while routing execution
through Qubit’s broker, permissions, grants, confirmation, audit, redaction,
resource, and emergency-stop paths.

For anything that cannot safely or legally be exposed, add it to the matrix as
BLOCKED or REJECTED with a typed Qubit error, user-facing explanation, owner,
and regression test. Do not silently omit commands, expose only `chat`, tell
the user to invoke an upstream Hermes binary, or count a renamed banner as
parity. A partial command subset or external-only Hermes installation is a
hard failure and requires continuing repair; do not return a limitation and
do not start 8U.

CLI and dashboard parity is mandatory. Do not count a dashboard inventory
tile, capability count, read-only status endpoint, frontend placeholder, or
catalog entry as dashboard implementation. Every applicable capability must
have a real Qubit dashboard page, control, form, report, settings view, or
action wired to the same backend operation used by CLI and voice. Test
loading, empty, error, degraded, confirmation, cancellation, redaction, and
emergency-stop states. Prove the dashboard cannot claim success without a
real backend result.

The seven currently rejected Hermes rows must also be adapted into Qubit-native
features before this packet is complete:

- safety bypass flags → scoped owner-authorized sessions that retain e-stop,
  audit, expiry, and high-impact confirmation;
- proxy → restricted target-bound proxy with secret isolation and resource
  limits;
- pets/skin → optional Qubit cosmetics/themes;
- sync → opt-in encrypted selective profile/vault sync with conflicts and
  rollback;
- computer-use → bounded brokered desktop control;
- serve/dashboard/desktop/gui → Qubit-owned UI without a second listener;
- hooks → signed or allowlisted brokered event subscriptions.

Do not expose raw `--yolo`, arbitrary hooks, unrestricted computer control,
unbounded proxying, or a second Hermes server. Implement the useful behavior
through Qubit contracts and add positive, negative, redaction, resource,
persistence, rollback, and emergency-stop tests. The parity matrix must not
retain these rows as `REJECTED` merely because implementation is unfinished.

Create the new foundation from a fresh official Hermes repository clone outside
the current Qubit checkout and outside any Hermes/OpenClaw installation used
by the operator. Pin an exact reviewed Hermes commit. Do not treat a moving
main branch as reproducible. Record repository URL, commit, date, source
paths, tests, license, copyright, notices, dependency lockfiles, models,
plugins, browser drivers, and behavior differences.

The resulting Qubit foundation must be a Qubit-owned source tree containing
only legally permitted, reviewed adaptations of the selected upstream source
units. A Qubit-owned worker that merely imitates the upstream protocol, a
label that says Hermes, or a temporary external upstream installation
supervised by an otherwise unchanged Qubit tree is not sufficient. The final
runtime identity, import/entry-point inspection, subprocess argv, module paths,
and receipts must prove that Qubit-owned code is the implementation being
executed. Pinned upstream code is a differential reference, not a normal
runtime dependency.

Make Qubit Agent the public product identity and Qubit VoiceBridge the voice
subsystem identity. Preserve the internal voicebridge import package until a
separate compatibility migration is proven. Do not destructively rename or
overwrite the current dirty checkout. Build and test the migration in a clean
worktree with a reversible backup and rollback journal.

Complete the user-facing rebrand. The apparent product name must be Qubit
Agent/Qubit VoiceBridge everywhere: CLI banners, prompts, help, errors,
completion, dashboard titles and routes, navigation, API resource labels,
installer output, service descriptions, shortcuts, package metadata, default
paths, logs, reports, diagnostics, and support docs. Rework dashboard route and
component organization, colors, fonts, spacing, icons, typography, layout,
onboarding language, and design tokens so it is a coherent Qubit product, not
Hermes with a voice wrapper. Add route, branding, accessibility, and visual
regression tests. Do not use blind string replacement.

The dashboard must combine the functional Hermes dashboard foundation with the
adopted OpenClaw dashboard workflows. Preserve Hermes command/session/task
organization and controls; incorporate OpenClaw UI patterns only for OpenClaw
capabilities genuinely integrated into Qubit; then unify everything into one
Qubit information architecture and design system. Do not ship two dashboards,
paste OpenClaw pages beside Hermes pages, or replace the full Hermes workflow
with a smaller custom shell. Every visible control must call the same real
backend operation used by CLI and voice. Apply the frontend design standards
for hierarchy, typography, color, spacing, responsive behavior, accessibility,
motion, onboarding, loading, empty, error, degraded, approval, and recovery
states, and prove those states with rendered and behavioral tests.

Do not erase legally required Hermes/OpenClaw license, copyright, attribution,
provenance, migration, or developer-only upstream references. Those references
must be separated from product-facing branding and must never be presented as
the assistant or product name. Any user-facing Hermes/OpenClaw branding left
outside those permitted records is an 8T-F failure.

Integrate the existing Qubit VoiceBridge into the Qubit-owned runtime as the
only voice/audio/session path: microphone, ASR, Piper/TTS, Right Ctrl,
dashboard voice, cancellation, barge-in, temp-audio cleanup, and typed
degraded states. Disable or route around any duplicate Hermes microphone,
wake-word, TTS, router, or voice-session path. Prove one listener, one active
voice session, one effective TTS path, and one audit/policy path.

Adapt and integrate every requested OpenClaw capability identified by the
feature matrix and the full parity contract; each capability must be present
in the new Qubit foundation or have a documented, tested compatibility
boundary that preserves the complete user workflow. Do not use “selected” as
an excuse to omit difficult capabilities. A capability is not complete if it
exists only as a status tile, matrix row, external process, CLI-only path,
dashboard-only path, or voice description.
Do not copy the whole OpenClaw repository. Do not create a second
gateway, router, memory store, tool registry, permission system, browser
authority, scheduler, audit store, or event loop. Every adapted capability
must call the existing Qubit broker, grants, confirmation, redaction, audit,
resource, emergency-stop, and rollback contracts.

Do not stop with blocked or rejected rows when the reason is merely unfinished
implementation. Resolve every code-side Hermes/OpenClaw gap, add the matching
CLI/dashboard/voice path, and rerun the complete suite. Only genuine
operator-only work may remain, and it must have a complete hermetic code path,
typed state, tests, and an exact manual qualification step.

Migrate the product identity, dashboard shell, CLI branding, installer,
shortcuts, service templates, config defaults, state paths, documentation,
package metadata, notices, and compatibility aliases. Preserve user config,
memory, reports, models, audio selections, browser settings, and state. Never
copy cookies, browser profiles, API keys, tokens, credentials, raw audio, or
private page/file text.

Add a REUSE/ADAPT/MISSING/REJECTED feature matrix covering voice, browser,
desktop, files, IDE/OpenCode, memory, models/providers, dashboard, skills,
tasks, schedules, and background work. Add regression tests for every bridge
and migration boundary. The matrix must distinguish “implemented in the new
Qubit foundation” from “available only in an external runtime”; an
external-only capability cannot be counted as integrated. Do not implement
the 8U browser workspace yet.

Preserve every unrelated dirty or untracked file byte-for-byte. Never use
git reset --hard, git clean, broad checkout, mass formatting, or destructive
deletion. Commit only 8T-F files after complete diff inspection.

Run focused and affected tests, full hermetic tests with warnings as errors,
source/caller audits, license/dependency scans, compileall, pip/uv check, git
diff --check, package build, isolated clean install, systemd verification,
state migration, upgrade, rollback, and uninstall rehearsal. Inspect actual
warnings, stderr, receipts, persisted files, and failure output. Do not claim
live hardware, browser, account, model, timer, or clean-machine success when
not performed.

Return the strict 8T-F handoff and stop. Do not start Packet 8U. If the final
Qubit tree does not execute the pinned Hermes implementation, or if requested
OpenClaw ADAPT/REUSE rows remain external-only, return REJECTED and continue
repairing 8T-F rather than claiming a limitation.
```

#### Verification prompt

```text
Verify ONLY Packet 8T-F. Read docs/QUBIT_FULL_PARITY_CONTRACT.md first.
Independently inspect the fresh pinned Hermes source,
selected OpenClaw source, complete migration diff, every caller, package,
service, dashboard, CLI, installer, config, state, model, audio, and identity
path. Verify exact licenses, notices, dependencies, source hashes, and
provenance.

Prove the new Qubit Agent foundation has exactly one agent/session authority,
one voice/audio path, one listener, one effective TTS path, one broker, one
permission system, one audit path, and one emergency-stop path. Test
microphone/ASR/TTS/Right Ctrl/dashboard parity, cancellation, barge-in,
restart, duplicate listener prevention, temp-audio cleanup, degraded states,
and no-bypass behavior. Verify the executed process/module identity and source
paths against the pinned Hermes clone. A Qubit worker that only imitates the
Hermes protocol, or a temporary Hermes install outside the resulting Qubit
tree, does not satisfy this proof.

Prove every requested Hermes and OpenClaw capability is available through the
Qubit CLI, authenticated dashboard, and Qubit voice path, with equivalent
behavior and the same backend. Every OpenClaw capability marked REUSE or ADAPT
in the 8T-F matrix must be actually present in the new Qubit foundation and
adapted through Qubit contracts. Capabilities marked MISSING, DEFER, BLOCKED,
REJECTED, CLI_ONLY, DASHBOARD_ONLY, VOICE_MISSING, or STATUS_ONLY are not
parity and must either be repaired or be proven genuine operator-only gates;
they must never be counted as complete. Prove OpenClaw capabilities cannot
bypass grants, confirmations, redaction, audit, resource limits, or emergency
stop. Prove no whole upstream repository, unreviewed plugin,
credential, cookie, profile, secret, raw audio, private page text, or full
environment was copied.

Verify product rebranding, canonical folder/install identity, legacy aliases,
config/state/model/audio/memory/report preservation, partial migration,
interrupted migration, repeated migration, upgrade, rollback, uninstall,
read-only, disk-full, unsafe symlink, and path traversal behavior. Verify no
second service/listener is created and no unrelated dirty/untracked file is
changed.

Run focused, affected, security, persistence, resource, clean-install,
upgrade, rollback, warnings-as-errors, compileall, pip/uv check,
git diff --check, systemd-analyze verify, package scans, and Graft build.
With explicit authorization only, run physical audio and live service checks;
do not fabricate hardware, account, browser, or model evidence.

Return VERIFIED, REJECTED, or VERIFIED WITH EXPLICIT LIMITATION and stop.
Do not start Packet 8U.
```

### Packets 8U–8AH — whole-computer capability expansion

These packets are planned after 8T. They are not implemented merely because
the prompts exist. Run them one at a time, only after the previous packet has
an independent verification result. The current broker, grants, scopes,
redaction, audit, isolation, emergency stop, session, memory, and release
contracts remain authoritative.

#### Shared implementation rules for 8U–8AH

```text
Implement ONLY the named packet. Read AGENTS.md, the entire applicable plan,
this prompt pack, the packet's operations document, licensing/provenance
records, and all affected callers. Run graft map/ask/callers first. Inspect
the complete diff before editing. Preserve every unrelated dirty or untracked
file byte-for-byte. Do not start another packet.

Reuse the existing capability registry, ActionBroker, 7E permissions, 8E
scopes, 8F action contracts, 8O power control, 8Y session/journal contracts,
redaction, audit, resource limits, and rollback mechanisms. Do not add a
second router, listener, memory store, scheduler, audit store, permission
system, browser authority, or direct shell/HTTP/MCP bypass. Every mutation
must have one authenticated policy path and a typed preview/receipt.

Before inventing a new implementation, perform an upstream-reuse preflight for
the pinned Hermes and OpenClaw repositories. Map each requested capability to
the smallest compatible upstream source and tests, record the exact commit,
license, notices, dependencies, provenance, and behavior differences, then
reuse or adapt that code behind Qubit contracts. Choose one implementation per
capability; do not merge two competing routers, memories, tool registries,
permission systems, browser authorities, or event loops. If upstream code is
not reused, document the technical reason and add an equivalent contract test.
Do not copy whole repositories, unreviewed plugins, secrets, profiles, or
provider credentials.

Every packet must classify its requirements before coding:

- REUSE: upstream behavior already satisfies the Qubit contract;
- ADAPT: upstream behavior exists but needs Qubit wrapping, policy, UI, or
  compatibility work;
- MISSING: implement only the smallest absent capability;
- REJECTED: incompatible, unsafe, duplicated, unlicensed, or unsupported.

When a requirement is REUSE, do not rewrite it. Add Qubit integration,
negative/contract tests, provenance, and independent verification instead. A
packet may therefore be verification-only or adaptation-only when upstream
already supplies the implementation. Do not report an unchanged upstream
feature as newly implemented by Qubit.

Keep the feature disabled by default. Distinguish DECLARED, AVAILABLE,
ENABLED, LIVE, DEGRADED, BLOCKED, FAILED, and VERIFIED; detection is not
permission and a UI toggle is not execution. Page, model, tool, repository,
clipboard, generated-code, and adapter output is untrusted and cannot grant,
widen, renew, or confirm a permission. Never copy cookies, browser profiles,
tokens, API keys, raw private page/file/audio text, or a full environment.
Never silently elevate, install, enable a service, publish, send, purchase,
delete, change an account, commit/push code, or modify project code without
the required fresh exact confirmation. CAPTCHA, 2FA, recovery challenges,
and login remain a manual handoff.

Use only declared dependencies and record any upstream source, exact commit,
license, notices, source path, and local changes. Add focused positive and
negative tests, warning-as-error tests, security/injection tests, resource,
persistence/concurrency/recovery, clean-install, and rollback tests. Run
compileall, pip check, git diff --check, and graft build. Inspect warnings,
stderr, receipts, persisted files, and failure output, not exit codes alone.
End with the exact handoff template: status, files, evidence/counts,
failures, untested paths, security/privacy findings, repairs, rollback, and
next safe action. Use INCOMPLETE, BLOCKED, DEGRADED, or FAILED for an
implementation handoff; never claim VERIFIED without independent verification.
```

#### Parallel-work rule for 8U–8AH

```text
To reduce elapsed time, research, source/license audits, fixture design, and
read-only test preparation may run in parallel in isolated worktrees. Do not
implement dependent production packets simultaneously in the same checkout.
Merge one packet at a time, run its focused and affected verification, inspect
the complete diff, and record the commit before starting the next dependent
packet. A coordinator may prepare all task capsules, but may not skip packet
gates, combine unrelated commits, or claim that parallel green tests prove
the merged product. Preserve unrelated dirty/untracked files in every
worktree.
```

#### Packet 8U — browser-first voice workspace

```text
Implement ONLY Packet 8U after 8T is independently verified. Build the
browser-first voice workspace on the existing microphone, ASR, TTS,
orchestrator, and broker. Connect only to an existing user-launched Chromium
CDP or approved in-page bridge over authenticated loopback. Provide an
authenticated browser-side toggle to attach, pause, or disconnect the current
browser context; it must control the same backend session as the dashboard,
Right Ctrl, and wake-word activation. Provide a generic
read bridge for ordinary supported webpages, exposing bounded current-tab,
selected-text, DOM/accessibility, visible-state, clipped-screenshot, and tab
context without requiring a custom adapter for every website. Expose
navigation, scroll, search, click, type, fill, and draft
operations with streaming partial ASR/response/TTS, barge-in, reconnect, and
typed auth-required, CAPTCHA/2FA, selector-drift, unavailable, and degraded
states. Do not clone profiles, import cookies, launch a hidden browser, expose
public CDP, or promise universal website control. Login/CAPTCHA/2FA must pause
for a manual handoff. Per-origin/account grants must separate read/navigation/
fill/draft from submit/send/publish/purchase/delete/account changes.

Test hermetic DOM/accessibility fixtures, cross-origin and iframe boundaries,
prompt injection, malformed selectors/tool output, tab switching, reconnect,
redaction, bounded screenshots, streaming latency, cancellation, barge-in,
and all typed states. With explicit authorization only, run a disposable
real-browser canary. Return the strict handoff and stop.
```

Verification prompt:

```text
Verify ONLY Packet 8U. Independently inspect every browser/voice/ASR/TTS/CDP
caller and the complete diff. Prove one authenticated broker path from
“Hey Qubit,” Right Ctrl, dashboard, CLI, and the approved Comet/Chromium
browser toggle to the same browser capability; no direct CDP/HTTP/MCP,
profile/cookie import, hidden listener, or frontend-only state. Test origin and
account binding, grant expiry/revocation, read versus draft versus high-impact
actions, exact confirmation hashes, prompt injection, hostile DOM/tool output,
iframes, selector drift, ambiguous tabs, reconnect, cancellation, barge-in,
CAPTCHA/2FA/manual handoff, screenshot/clipboard/private-text redaction,
resource bounds, audit, restart, and rollback. Run real browser steps only
with explicit operator authorization and a disposable account/session. Run
focused, affected, security, resource, persistence, clean-install,
warnings-as-errors, compileall, pip check, diff check, and Graft build.
Return VERIFIED, REJECTED, or VERIFIED WITH EXPLICIT LIMITATION and stop.
```

#### Packet 8V — desktop application control

```text
Implement ONLY Packet 8V after 8U verification. Add an opt-in adapter for
declared Linux desktop sessions and supported applications using accessibility
APIs/portals and semantic roles. Bind every operation to the selected user,
display/session, application, window, and target. Support bounded launch,
focus, menu/button/text/table/dialog interaction, selection, and readback.
Screenshot/OCR or coordinate fallback must be visibly degraded, bounded, and
confirmation-gated. Preserve focus/cursor, isolate X11/Wayland sessions,
redact clipboard, and recover from app crash/reconnect. Never add a global
keylogger, hidden listener, arbitrary event injector, or unrestricted desktop
executor.

Test multiple sessions, X11/Wayland, target ambiguity, focus/cursor changes,
accessibility permission denial, clipboard redaction, app crash/reconnect,
resource bounds, emergency stop, audit, restart, and rollback. Real apps need
explicit operator authorization. Return the strict handoff and stop.
```

Verification prompt:

```text
Verify ONLY Packet 8V. Inspect every desktop adapter caller and prove all
actions use the broker, grants, target/session binding, audit, isolation, and
emergency stop. Test semantic roles, ambiguous windows, focus/cursor
preservation, X11/Wayland selection, multi-session isolation, denied desktop
permissions, screenshot/OCR degraded mode, clipboard redaction, injection,
malformed adapter output, crash/reconnect, cancellation, resource limits,
disabled-by-default behavior, and rollback. Do not use a global listener or
coordinate-only unrestricted fallback. Run live desktop tests only with
explicit authorization. Run the full required validation gates and return
VERIFIED, REJECTED, or VERIFIED WITH EXPLICIT LIMITATION; stop.
```

#### Packet 8W — approved files and folders workspace

```text
Implement ONLY Packet 8W after 8U and 8V verification. Add one brokered file
workspace with explicit approved roots and typed list/search/read/preview,
write, copy/move/rename, archive/trash/restore, diff, snapshot, and undo
operations. Enforce path traversal and symlink containment, secret-file and
device-file refusal, quotas, file/count/size limits, read-only/disk-full
handling, atomic writes, concurrent access, retention, and recoverable state.
Deletes and broad changes require a fresh exact preview/confirmation. Model or
repository text cannot expand a root or grant access. No whole-home default,
direct shell fallback, hidden watcher, or raw file content in logs.

Test traversal, symlink races, permissions, secret patterns, device files,
large trees, disk-full/read-only, corrupt/newer state, concurrent writers,
crash/restart, restore, redaction, audit, resource limits, clean install, and
rollback. Return the strict handoff and stop.
```

Verification prompt:

```text
Verify ONLY Packet 8W. Prove root, user, project, session, and grant binding;
read/reversible/delete separation; exact previews; symlink/path traversal and
secret/device-file refusal; quotas; atomicity; fsync/rename boundaries;
concurrency; crash recovery; corrupt/newer/read-only/disk-full behavior;
redaction; audit; restart; uninstall; and rollback. Prove model, page,
repository, and tool text cannot widen access. Inspect CLI/dashboard/voice
parity and every caller, then run focused, security, resource, persistence,
clean-install, warnings-as-errors, compileall, pip check, diff check, and
Graft build. Return VERIFIED, REJECTED, or VERIFIED WITH EXPLICIT LIMITATION;
stop.
```

#### Packet 8X — IDE-native engineering workspace

```text
Implement ONLY Packet 8X after 8W verification. Add declared VS Code,
JetBrains, or editor integration for current workspace, open file, selection,
diagnostics, tests, diffs, patch preview, and bounded test runs. Keep OpenCode
as the optional supervised executor, not a second authority. Writes outside an
approved disposable worktree require an exact diff/hash preview and fresh
confirmation. Never auto-commit, push, publish, install packages, change
services/permissions, or inherit API keys, cookies, desktop sessions, or the
full environment. Repository instructions and generated code are untrusted.
Test malformed patches, hostile instructions, traversal/symlinks, overflow,
timeout, cancellation, crash/reap, concurrent runs, extension disconnect,
provider outage, partial failure, restart, and rollback. Return the handoff.
```

Verification prompt:

```text
Verify ONLY Packet 8X. Prove IDE actions are brokered, authenticated,
workspace/path bound, resource-limited, redacted, audited, cancellable, and
disabled by default. Test hostile repository instructions, prompt injection,
path traversal, symlinks, secret files, malformed/oversized patches, stale or
replayed confirmations, target switching, timeout, crash/kill/reap,
concurrency, provider outage, missing credentials, extension disconnect,
restart, partial failure, clean install, and rollback. Prove no commit/push/
publish/install/service/permission/account/destructive action is automatic;
run live coding only in an authorized disposable worktree. Run all required
gates and return VERIFIED, REJECTED, or VERIFIED WITH EXPLICIT LIMITATION;
stop.
```

#### Packet 8Y — unified task context and session continuity

```text
Implement ONLY Packet 8Y after 8U–8X verification. Unify one task/session
identity across voice, dashboard, CLI, browser, desktop, files, and IDE. Add
an explicit context picker, source/provenance labels, bounded summaries,
redaction and consent for sensitive sources, pause/resume/cancel/retry,
progress, durable receipts, recovery, and clear account/project boundaries.
Reuse the existing session, journal, memory, and audit authorities; do not add
a second router, memory store, scheduler, or audit database. Context switches
must not leak between accounts, apps, projects, or sessions, and no page/model
text can authorize an action. Test restart, reconnect, concurrent sessions,
context switching, stale receipts, prompt injection, limits, recovery, and
rollback. Return the handoff and stop.
```

Verification prompt:

```text
Verify ONLY Packet 8Y. Prove voice/dashboard/CLI/browser/desktop/files/IDE
share one authenticated session contract and that every context item has
bounded provenance, consent, redaction, retention, and deletion behavior.
Test account/project/session isolation, context switching, restart/reconnect,
pause/resume/cancel/retry, stale/replayed receipts, concurrent tasks, crash
recovery, prompt injection, raw page/file/audio leakage, resource limits,
audit, and rollback. Prove no page/model/tool text changes policy or grants.
Run all required validation and return VERIFIED, REJECTED, or VERIFIED WITH
EXPLICIT LIMITATION; stop.
```

#### Packet 8Z — capability adapter lifecycle and local catalog

```text
Implement ONLY Packet 8Z after 8Y verification. Add a versioned local adapter
manifest and backend-authoritative lifecycle for discover, validate, enable,
disable, upgrade, downgrade, rollback, and remove. Each record must include
source commit, license/notices, hash, dependencies, supported OS/session,
scopes, risk tier, health, limits, and rollback. Start with a local catalog;
do not add an uncontrolled marketplace. Bad manifests, unknown licenses,
stale hashes, dependency conflicts, corrupt state, interrupted/partial
installs, and hidden listeners must fail closed. Plugins may not bypass the
broker, grants, isolation, audit, or emergency stop. Return the handoff.
```

Verification prompt:

```text
Verify ONLY Packet 8Z. Inspect the manifest schema, catalog, installer,
loader, lifecycle state, provenance, license/notices, dependency and OS
checks, every adapter caller, and complete diff. Test malformed/newer
manifests, bad licenses/hashes, dependency conflicts, path traversal,
symlinks, secrets, corrupt/read-only/disk-full state, interrupted install,
partial upgrade, downgrade/rollback/remove, restart, concurrent lifecycle
operations, offline mode, resource limits, audit, and hidden-listener/direct
bypass attempts. Prove CLI/dashboard parity, disabled-by-default behavior,
user-data preservation, and that no plugin can widen permissions or evade
power control. Run all required gates and return VERIFIED, REJECTED, or
VERIFIED WITH EXPLICIT LIMITATION; stop.
```

#### Packet 8AA — always-on presence and context plane

```text
Implement ONLY Packet 8AA after 8Z verification. Add an explicit always-on
runtime mode with visible listening, paused, muted, and unavailable states.
Support “Hey Qubit” wake-word activation and push-to-talk selection, including
the existing Right Ctrl path. The authenticated dashboard and approved
browser-side toggle must arm, pause, mute, and disconnect the same backend
session; they must never create duplicate listeners or voice providers.
Support immediate mute, session expiry,
crash recovery, and bounded active-window/browser context refresh. Keep the
existing audio/ASR/TTS contracts. Do not store raw audio by default and do not
add a hidden microphone listener, global keylogger, screen recorder, or silent
screenshot loop. Context sources must be opt-in, redacted, rate-limited,
bounded, pausable, and removable. Add tests for idle resource use, false
activations, barge-in, mute races, suspend/resume, restart, retention, and
rollback. Return the strict handoff and stop.
```

Verification prompt:

```text
Verify ONLY Packet 8AA. Prove “Hey Qubit,” Right Ctrl, the dashboard, and the
browser toggle all use one authenticated policy/session path for always-on
mode, wake-word/PTT, privacy state, mute/pause, session expiry, and context
refresh. Measure idle/active CPU and RAM, wake latency, false activations,
refresh rate, cleanup, and audio responsiveness. Test microphone denial, mute
races, suspend/resume, crash/restart, duplicate listeners/providers, raw-audio
retention, context redaction, stale sessions, emergency stop, and rollback.
Confirm no hidden listener, keylogger, screen recorder, or unbounded capture.
Return VERIFIED, REJECTED, or VERIFIED WITH EXPLICIT LIMITATION; stop.
```

#### Packet 8AB — Firefox and multi-browser parity

```text
Implement ONLY Packet 8AB after 8AA verification. Add a separately declared
Firefox browser-context adapter using a supported Firefox
automation/accessibility boundary. Match the generic read contract for
ordinary webpages before adding site-specific actions. Do not assume Chromium
CDP behavior is portable. Bind browser, profile,
session, account, origin, and tab explicitly. Support only the bounded 8U
operations. Never copy cookies, profiles, tokens, or browser databases.
Keep Chromium and Firefox grants isolated and report unsupported states
truthfully. Add fixtures, provenance, license records, and a per-browser
support matrix. Return the strict handoff and stop.
```

Verification prompt:

```text
Verify ONLY Packet 8AB. Inspect Firefox and Chromium callers, role binding,
origin/account/session isolation, licenses, and dependencies. Test tab and
page reads, navigation, fill/draft separation, selector drift, iframes,
reconnect, profile isolation, auth/CAPTCHA/2FA handoff, redaction, resource
limits, emergency stop, and rollback on both browsers. Prove no cookie/profile
import, wrong-browser fallback, public listener, or direct bypass. Run live
tests only with authorization and disposable sessions. Return VERIFIED,
REJECTED, or VERIFIED WITH EXPLICIT LIMITATION; stop.
```

#### Packet 8AC — adapter SDK and website/app contract

```text
Implement ONLY Packet 8AC after 8AB verification. Create the smallest local
adapter contract and fixture harness for adding websites or desktop apps
without duplicating the router, broker, grants, audit, memory, or scheduler.
Require a manifest containing identity, versions, origins/app IDs, operations,
risk tiers, dependencies, privacy rules, limits, license/provenance, health,
and rollback. Reject unknown operations and downloaded code by default. Add
schema validation, compatibility checks, fixture tests, provenance, and safe
removal. Return the strict handoff and stop.
```

Verification prompt:

```text
Verify ONLY Packet 8AC. Inspect the manifest, loader, registry, fixture
runner, dependency/license checks, provenance, callers, and complete diff.
Test malformed/newer manifests, unknown capabilities, bad hashes/licenses,
dependency conflicts, traversal, symlinks, secrets, hidden listeners, direct
broker bypasses, resource limits, disabled defaults, interrupted install,
upgrade, downgrade, removal, rollback, and concurrent lifecycle operations.
Prove adapters cannot widen grants, change policy, self-install, or execute
outside the broker. Return VERIFIED, REJECTED, or VERIFIED WITH EXPLICIT
LIMITATION; stop.
```

#### Packet 8AD — individually qualified website packs

```text
Implement ONLY Packet 8AD after 8AC verification. Add explicit website action
packs one site family at a time, initially prioritizing Gmail, Messenger,
Instagram, forums, and marketplaces. Generic ordinary webpage reading belongs
to 8U/8AB; these packs are for site-specific controls and workflows. Do not
claim universal action support. Each pack must define exact origins, supported
read/navigation/fill/draft actions,
account binding, selector drift, auth/CAPTCHA/2FA handoff, redaction, rate
limits, fixtures, live canary, license/provenance, and rollback. Sending,
publishing, purchasing, deleting, and account/security changes require fresh
exact confirmation. Return the strict handoff and stop.
```

Verification prompt:

```text
Verify ONLY Packet 8AD. Verify every included website pack independently from
manifest through broker, grants, audit, and emergency stop. Test exact
origin/account binding, current-tab and selected-content reads, navigation,
fill/draft, selector drift, login/CAPTCHA/2FA handoff, hostile page output,
rate limits, reconnect, duplicate actions, private-text/token redaction,
confirmation hashes, resource limits, and rollback. Use fixtures and only
authorized disposable live accounts. Never generalize one site’s result to
another. Return VERIFIED, REJECTED, or VERIFIED WITH EXPLICIT LIMITATION;
stop.
```

#### Packet 8AE — individually qualified desktop application packs

```text
Implement ONLY Packet 8AE after 8AC verification. Add declared desktop-app
packs one application family at a time, prioritizing IDEs, terminals, file
managers, communication apps, and media tools. Use semantic accessibility APIs
where available and report unsupported/degraded apps honestly. Each pack must
define app identity/version, session/display binding, read/reversible/mutating
operations, focus behavior, clipboard policy, limits, fixtures, provenance,
live qualification, and rollback. Do not add a global keylogger or arbitrary
coordinate injector. Return the strict handoff and stop.
```

Verification prompt:

```text
Verify ONLY Packet 8AE. Inspect every app pack, manifest, caller,
accessibility boundary, session/display binding, and broker path. Test
semantic discovery, version drift, ambiguous windows, focus/cursor
preservation, X11/Wayland and multi-session isolation, denied accessibility
permissions, clipboard/screenshot redaction, crash/reconnect, emergency
stop, cancellation, resource limits, disabled defaults, and rollback. Prove
one app’s grant cannot affect another app or session and no unrestricted
fallback exists. Return VERIFIED, REJECTED, or VERIFIED WITH EXPLICIT
LIMITATION; stop.
```

#### Packet 8AF — background processes, gaming coexistence, and performance

```text
Implement ONLY Packet 8AF after 8AE verification. Add bounded process and
resource awareness so Qubit can report its own service, model, audio, browser,
and declared-app health while demanding programs such as Steam games run.
Use explicit allowlists, sampling limits, pause-on-fullscreen policy, and
CPU/RAM/VRAM/audio budgets. Do not inspect or control arbitrary processes by
default. Never capture game input or screen content silently. Return the
strict handoff and stop.
```

Verification prompt:

```text
Verify ONLY Packet 8AF. Prove process/resource observation is allowlisted,
redacted, bounded, and brokered, with no arbitrary process control, keylogging,
screen capture, or game interference. Measure idle/background/active CPU, RAM,
VRAM, audio latency, wake latency, and fullscreen/game behavior. Test
pause/resume, suspend, process disappearance, permission denial, exhaustion,
emergency stop, restart, concurrent sessions, audit, and rollback. Return
VERIFIED, REJECTED, or VERIFIED WITH EXPLICIT LIMITATION; stop.
```

#### Packet 8AG — coverage matrix and production qualification

```text
Implement ONLY Packet 8AG after 8AD–8AF verification. Build a backend-
authoritative support matrix for every shipped browser and application pack.
Show declared, installed, available, enabled, live, degraded, blocked, and
verified states; supported versions; permissions; limitations; measured
latency/resource budgets; privacy behavior; and rollback status. Add no new
capability without an adapter contract, tests, license/provenance, live
qualification plan, and removal path. Expose the matrix through setup/CLI
without secrets or raw private content. Return the strict handoff and stop.
```

Verification prompt:

```text
Verify ONLY Packet 8AG. Reconcile every website/app manifest, implementation,
test, license, dependency, caller, support state, and rollback record. Prove
detected is never reported as enabled and hermetic testing is never reported
as live. Test stale versions, missing dependencies, denied permissions,
unavailable targets, duplicate adapters/listeners, resource ceilings,
redaction, audit, upgrade/downgrade, uninstall, and partial rollback. Run
authorized live canaries for each claimed browser/app family; do not
generalize results. Return VERIFIED, REJECTED, or VERIFIED WITH EXPLICIT
LIMITATION; stop.
```

#### Packet 8AH — unified Qubit dashboard and upstream UI adaptation

```text
Implement ONLY Packet 8AH after 8AG verification. Replace the fragmented
dashboard experience with one Qubit-native, responsive dashboard backed by the
existing authenticated typed API and action broker. Consolidate the useful
workflow categories found in the approved Hermes/OpenClaw interfaces:
conversation and streaming tool output; voice and audio status; browser and
desktop context; files and project workspace; IDE/OpenCode runs and diff
previews; sessions, memory, skills, tasks and schedules; model/provider and
safe API-key reference status; approvals and confirmations; adapter health;
diagnostics, audit, setup, upgrade, rollback, and recovery.

Do not merge a second router, session store, memory store, permission system,
model selector, action executor, or event authority. Every dashboard control
must call the existing backend contract, show truthful DECLARED/AVAILABLE/
ENABLED/LIVE/DEGRADED/BLOCKED states, and fail closed. Mutations must use the
same broker, grants, exact previews, confirmations, receipts, audit, and
emergency stop as CLI and voice. Keep raw secrets, cookies, private page/file
text, and raw audio out of the UI and diagnostics.

Reuse or adapt upstream Hermes/OpenClaw UI code only after pinning exact
commits, reviewing licenses/notices/dependencies, recording provenance, and
preserving required attribution. Do not copy unreviewed whole repositories or
their competing backend authorities. Add desktop, narrow-mobile, keyboard,
screen-reader, reduced-motion, forced-colors, loading, error, offline,
session-expiry, empty, degraded, and recovery states. Return the strict
handoff and stop.
```

Verification prompt:

```text
Verify ONLY Packet 8AH. Independently inspect the route/component inventory,
all Hermes/OpenClaw source reused or adapted, exact commits, licenses,
notices, dependencies, provenance, and every dashboard caller. Prove there is
one authenticated typed API path and one backend authority across dashboard,
voice, CLI, browser, desktop, files, IDE/OpenCode, sessions, providers,
approvals, setup, and audit. Prove no frontend-only state, secret, raw page
content, raw file/audio content, duplicate route, direct executor, second
listener, or permission bypass exists.

Test chat/tool streaming, voice/PTT status, browser and desktop context,
file/project previews, IDE/OpenCode receipts, sessions/memory/skills/tasks,
provider inventory, safe key-reference display, approvals, adapter health,
diagnostics, setup, upgrade/rollback, audit, offline/reconnect, session
expiry/re-pairing, loading/error/degraded states, bounded DOM, stale data,
concurrent updates, cancellation, emergency stop, and recovery. Test desktop
and narrow mobile layouts at 320 pixels and 200% zoom, keyboard-only and
screen-reader semantics, focus restoration, live announcements, contrast,
forced colors, reduced motion, touch targets, and performance/resource
budgets. Prove all dynamic data is safely rendered and all writes retain exact
confirmation and audit behavior.

Run focused, affected, accessibility/UI, security, resource, persistence,
clean-install, warnings-as-errors, compileall, pip check, git diff --check,
and Graft build. With explicit authorization, run the real authenticated
dashboard against the same running backend; do not claim live parity from
mock-only tests. Return VERIFIED, REJECTED, or VERIFIED WITH EXPLICIT
LIMITATION and stop.
```

#### Whole-computer capability gate

```text
Before calling the product whole-computer capable, independently verify 8U–8AH
in order. Prove the supported browser, desktop, file, and IDE matrix through
one brokered policy path, with truthful DECLARED/AVAILABLE/ENABLED/LIVE states,
bounded latency/resources, redacted audit, restart/recovery, and rollback.
Routine low-risk actions may use a narrow expiring owner grant. Sending,
publishing, purchasing, deleting, account changes, and project-code changes
still require fresh exact confirmation. Do not claim universal access or
production readiness when a live hardware/account/session canary is untested.
Return the strict verification template and stop.
```

### Latest owner instruction — finish the native Qubit rebrand before claiming completion

```text
Implement docs/QUBIT_NATIVE_COMPLETION_PLAN.md and check every item in
docs/QUBIT_NATIVE_COMPLETION_CHECKLIST.md. Do not stop at a report that only
says the matrix is complete.

The required result is a Qubit-owned product and runtime. Do not launch,
supervise, or expose an upstream Hermes assistant as normal Qubit runtime.
Use pinned Hermes/OpenClaw sources only for audited, legally permitted source
adaptation and differential comparison. Preserve required attribution and
provenance in legal/developer records, but remove upstream product branding
from normal Qubit commands, services, paths, runtime labels, logs, reports,
dashboard, voice, and operational source.

Finish real CLI, authenticated dashboard, and voice parity for every supported
Hermes and adopted OpenClaw workflow. All surfaces must call one Qubit backend
and one broker/policy/permission/confirmation/audit/redaction/resource/
cancellation/rollback/emergency-stop path. A matrix row, status tile, mock,
dead button, external subprocess, or explanatory voice response is not an
implementation. Resolve all code-side blocked, rejected, deferred, status-only,
CLI-only, dashboard-only, and voice-missing rows.

Make the dashboard one professional Qubit product: preserve useful workflow
organization, integrate adopted OpenClaw workflows, and apply Qubit
navigation, typography, colors, icons, spacing, responsive layout,
accessibility, motion, onboarding, loading, empty, error, degraded, approval,
cancellation, and recovery states. Do not ship two dashboards or frontend
fabricated success.

Preserve user data and unrelated dirty files. Never use git reset --hard,
git clean, broad checkout, mass formatting, or destructive deletion. Keep
licenses and notices. Run focused, affected, full hermetic, live/dashboard/UI,
security, persistence, resource, clean-install, package, migration, upgrade,
rollback, compileall, dependency, diff-check, secret-scan, import-audit, and
Graft gates. Inspect actual warnings, stderr, receipts, state files, and
failures. Repair every ordinary failure and rerun affected gates.

Only physical hardware, live accounts/CAPTCHA/2FA, release signing, and
clean-machine qualification may remain manual. Do not claim 100%, native,
complete, or 1:1 while any code-side checklist item is open. Update the plan,
checklist, provenance, and rollback records with exact evidence, then stop
without starting another packet.
```

### Frozen-contract rule

```text
Treat docs/QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md as immutable. Do not rewrite,
weaken, delete, reorder, or reinterpret it, the native completion plan, the
dashboard rebuild plan, or their requirements to make the work appear done.

You may change production code, tests, required provenance/licensing records,
and a separate append-only evidence report. You may add evidence to checklist
items, but you may not change what the checklist requires.

If implementation conflicts with the contract, report the conflict and repair
the implementation. Do not turn code failures into manual-only limitations,
change blocked/rejected/deferred rows into success without real proof, or
replace the original Hermes UI parity requirement with a smaller custom shell.

Do not claim 100%, complete, native, or 1:1 until the frozen contract and
checklist pass independently for Qubit runtime, complete Hermes UI/workflows,
OpenClaw additions, CLI, dashboard, voice, security, lifecycle, and tests.
Requirement changes require explicit owner approval and a dated amendment.
```

### Dashboard rebuild requirement — do not call the Talk page 1:1

```text
Read docs/QUBIT_OWNER_AMENDMENT_2026-09-11.md and
docs/QUBIT_DASHBOARD_REBUILD_PLAN.md and implement them together with the
native completion plan and checklist. Preserve the original Hermes dashboard
pages, sidebar, navigation, controls, information architecture, density,
terminal/chat workspace, forms, dialogs, reports, and workflows 1:1 first.
The current Qubit Talk page is only a front door and must not be reported as a
complete Hermes/OpenClaw dashboard.

First vendor and hash-compare the exact pinned Hermes frontend source. The
vendored Hermes React/Vite source is the structural and functional baseline;
the existing static Qubit pages are not a substitute. Then inventory every CLI
operation, voice entry, dashboard operation, matrix row, and pinned upstream
workflow. Then implement real Qubit dashboard routes
or workspaces for: chat/workspace, sessions, projects/files, models/providers,
tools/skills/plugins, tasks/cron/runs, approvals/permissions/security,
browser/desktop/IDE, messaging/channels/webhooks/pairing, memory/profiles,
reports/activity/logs, and setup/runtime/system.

Every page must call the same authenticated Qubit backend operation used by
CLI and voice. Implement loading, empty, success, error, unavailable,
degraded, offline, approval, cancellation, receipt, audit, redaction,
resource, timeout, persistence, and recovery states. No status tile, count,
static copied markup, dead button, mock success, direct shell, direct upstream
HTTP/MCP path, second listener, or second broker counts as implementation.

After preserving the Hermes source and workflows, add all missing OpenClaw workflows and
then rebrand the combined product as one distinctive Qubit command console.
Qubit may change colors, typography, icons, spacing, labels, responsive
behavior, accessibility, reduced motion, keyboard shortcuts, command palette,
terminal output, status rail, tables, timelines, forms, drawers, dialogs, and
empty states. It must not remove, hide, collapse, or weaken any original
Hermes page or workflow. The result must be a serious local agent workspace,
not a centered demo card and not an incomplete custom replacement.

Do not copy upstream logos, names, artwork, or distinctive branding. Source
code vendoring is allowed by the owner amendment; legal notices and provenance
must remain intact and outside normal product UI. Verify all routes with real
backend tests, Playwright/UI tests, narrow-mobile screenshots, accessibility
checks, security checks, and full warnings-as-errors gates. Repair every
ordinary failure and do not claim 1:1 until the complete route and workflow
inventory is proven.
```

### Final unified-agent requirement — Hermes + OpenClaw + VoiceBridge

```text
The finished Qubit product must contain every supported Hermes and OpenClaw
capability in one Qubit agent. Compare the pinned sources and behavior
1:1; do not stop at a capability count, route list, status tile, adapter name,
or passing smoke test.

For every command, command family, dashboard workflow, and adopted OpenClaw
operation, prove the same Qubit backend operation is reachable through:

- `qubit` CLI with equivalent arguments, help, output, errors, exit status,
  persistence, cancellation, and recovery;
- the vendored Hermes-baseline dashboard with a real page/control/workflow;
- Qubit VoiceBridge with a natural command, argument extraction, clarification
  when ambiguous, typed response, confirmation where required, cancellation,
  expiry/replay rejection, redacted receipt, and session isolation.

Voice descriptions, help text, inventory tiles, dashboard-only controls,
CLI-only implementations, dead buttons, and external upstream processes do not
count. Every implementation must use the one Qubit broker, permission,
confirmation, audit, redaction, resource, cancellation, rollback, and
emergency-stop path.

Do not remove or silently narrow Hermes/OpenClaw behavior. If a behavior is
unsafe, unavailable on this machine, or requires a live account, implement the
Qubit-safe equivalent and prove it with typed states and tests. Only the live
operator action may remain manual; the code path, CLI path, dashboard path,
voice path, error state, and test must already exist.

Do not rebrand early in a way that hides missing functionality. First make the
Hermes source/workflows and OpenClaw additions work; then apply the complete
Qubit rebrand as a documented overlay while retaining legal attribution and
provenance. Do not claim 100%, complete, or 1:1 until independent verification
proves source mapping, behavior, CLI/dashboard/voice parity, UI interactions,
security, lifecycle, clean install, and all required tests.
```

### Latest upstream release parity requirement

```text
The parity target is the latest stable Hermes release and latest stable
OpenClaw release available at the Qubit release-cut date, not merely the old
revisions used by an earlier packet.

For every release cut or upstream-update task:

1. Discover the official latest stable Hermes and OpenClaw tags/releases.
2. Clone them into disposable read-only directories.
3. Pin exact commits and record tag, date, URL, license, and hashes.
4. Inventory every new command, page, API, backend feature, tool, plugin,
   workflow, model/provider feature, and protocol surface.
5. Compare the new inventory with the previous Qubit inventory.
6. Integrate every compatible feature into the Qubit-owned Hermes foundation.
7. Add CLI, dashboard, and VoiceBridge coverage for every applicable feature.
8. Add tests for new behavior, negative behavior, security, persistence,
   cancellation, rollback, packaging, and degraded states.
9. Re-run the complete parity, build, install, security, and UI gates.
10. Update provenance and append-only evidence without deleting historical
    release records.

Never use an unpinned branch or claim “latest” without recording the exact
revision. Never silently omit a new upstream feature. If a feature cannot be
exposed safely or legally, implement a tested Qubit-safe equivalent and record
the exact reason, owner, and rollback. A new upstream release reopens parity
verification; it does not automatically inherit the previous VERIFIED result.
```

## Canonical voice-input decision — 2026-09-16

Read `docs/QUBIT_VOICE_TO_TEXT_WORKING_PROFILE_2026-09-16.md` before changing
voice. Preserve the working Chrome/Chromium `SpeechRecognition` dashboard path
as Qubit’s preferred online provider (`en-US`, interim results, one
alternative, final transcript through the canonical Qubit backend). Do not
replace it with Hermes ASR or claim Chrome’s private engine is embedded in
Qubit. Keep local Sherpa Parakeet as the explicit offline/private fallback.
