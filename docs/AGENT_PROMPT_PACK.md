# Agent Prompt Pack

Use this file with `docs/UNIFIED_AGENT_PLAN.md`.

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
docs/UPSTREAM_REUSE.md before production code, preserve licenses, and copy or
adapt only the smallest tested contract. Do not copy a framework or use a
floating branch.

Use the existing Python/static-JS/systemd/Bubblewrap/Ollama/OpenClaw/Hermes
stack. Do not add FastAPI, Uvicorn, React, Node, Docker, Kubernetes, another
database, another queue, another model server, or an undeclared dependency.
Model output is never permission. Never execute generated shell directly,
expose secrets, bake private data into a model, or change per-call Ollama
runner options outside the authorized model packet.

Implement the smallest coherent change with typed boundaries, bounded I/O,
timeouts, cancellation, redacted logs, reversible migrations, documentation,
normal/negative/degraded/concurrency/security tests, and packet benchmarks.
Run focused tests first, then affected regressions. Inspect actual output. Run
git diff --check. Rebuild Graft after substantial source changes.

Before handoff, verify every new code path has a caller and a test. Never say
done, complete, fixed, or ready. End with the exact section 5.1 handoff
template, using INCOMPLETE, BLOCKED, DEGRADED, or FAILED. Include files,
symbols, commands, pass/fail/skip/degraded counts, measurements, security
checks, untested paths, risks, and rollback. Then STOP.
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

## Packet 6C — Piper voice-quality layer, informed by Fish Audio

The goal is to make the existing lightweight Piper path sound better, not to
pretend that Piper's small acoustic model can become Fish S2 Pro by copying
unrelated model code. Fish S2 Pro is a separate large speech model. Reuse
compatible TTS engineering ideas around it—text normalization, phrase/sentence
segmentation, prosody metadata, streaming, reference handling, and evaluation—
while keeping Piper fast, offline, and the safe fallback. This packet must not
change the reasoning model, ASR, router, permissions, or memory.

### Hard resource rule

The complete local agent must remain within **5 GB VRAM maximum**, with a target
of **4 GB or less steady-state**. The resident Qubit model, ASR, TTS, CUDA
libraries, temporary allocations, and all local workers count toward the same
budget. Piper and the default ASR path should run on CPU. Loading Fish S2 Pro,
a large Whisper model, or any second GPU model is out of scope for this PC.
Any benchmark over 5 GB is a packet failure and must not be hidden by unloading
the model after the measurement.

### Implementation prompt

```text
Implement ONLY Packet 6C: create a small Piper voice-quality layer behind the
existing TTSProvider contract. Inspect the pinned Fish Audio source first and
write a source-to-feature mapping before coding. Implement only compatible,
measurable ideas: safe text normalization; punctuation-aware sentence/phrase
segmentation; optional approved prosody markers mapped to Piper-safe text;
pronunciation overrides; bounded sentence streaming; cross-fade or silence
handling where it improves joins; and deterministic audio loudness/resampling.
Do not claim that Piper supports Fish's emotion tags, voice cloning,
multi-speaker generation, or model intelligence unless a real implementation
and test proves it. Unsupported tags must be removed or spoken safely.

For this private, noncommercial school project, a pinned local Fish Speech
checkout may be inspected and its TTS source/weights may be evaluated or
adapted under the Fish Audio Research License. Do not make Fish a runtime
dependency for this packet and do not redistribute that checkout, weights, or a
derivative product. Keep the license and required attribution/notice files with
the local project. For every copied line or dependency, record the exact
upstream URL, commit/version, file, license, and required notices. Do not use
Fish outputs to create or improve a foundational generative model. If this
ever becomes a paid, hosted, or distributed product, stop and obtain a
separate commercial license before shipping Fish materials. Fish-derived
preprocessing must remain independently testable and must not require Fish at
runtime. Fish provides TTS ideas here; it does not supply our STT engine.

Keep Piper offline and default. Preserve cancellation, barge-in, bounded
buffers, output-device behavior, and the existing playback retry path. Add
configuration for the enhancement layer with a safe off switch. Add unit and
integration tests for normalization, marker handling, segmentation, ordering,
joins, cancellation, malformed text, non-English text, secret/reference
redaction, WAV validity, latency/resource limits, and unchanged raw-Piper
behavior. Add documentation and a reversible migration. Do not add Fish API
credentials or a remote dependency in this packet.
```

### Verification prompt

```text
Verify ONLY Packet 6C. Inspect the source-to-feature mapping, exact upstream
revisions, license records, notices, and diff before running tests. Prove the
enhancement layer is local, opt-in, reversible, and independent of Fish at
runtime; raw Piper still works when it is disabled. Prove normalization and
prosody handling never inject unsafe control text, unsupported markers are
handled safely, phrase streaming is ordered, joins are acceptable, barge-in
stops output, and the final audio is valid mono WAV at the playback boundary.

Measure raw Piper versus enhanced Piper for cold/warm latency, time-to-first
audio, CPU/RAM/VRAM, output duration, and human listening quality. Prove the
existing full suite and real PTT/audio smoke remain green. Do not claim Piper
matches Fish S2 quality without a documented blind listening evaluation. Do
not claim Fish source or weights are commercially redistributable without
written licensing evidence. Report automated, live-audio, and human-listening
evidence separately; skipped tests are not green.
```

## Packet 6D — sentiment-controlled single voice and STT quality

This packet is the correct home for the user's single-voice and human-like
delivery goal. Sentiment is a bounded delivery signal, not consciousness. The
agent must not silently record ambient audio; recording requires a separate
explicit permission and retention policy.

### Implementation prompt

```text
Implement ONLY Packet 6D: add one canonical user-selected voice profile and a
small deterministic sentiment/prosody controller. The controller may classify
the reply into neutral, warm, calm, concerned, urgent, or excited using typed
rules/metadata, then map it to controls that the selected Piper voice actually
supports. Use Fish Speech source only as a documented TTS design reference;
do not run Fish, use its brain/memory, or add its model weights to production.
Piper must remain the one voice and must degrade to neutral when a control is
unsupported. Do not promise literal feelings, voice cloning, or Fish-level
quality without evidence.

Improve STT separately using the existing Sherpa Moonshine/Parakeet and
Faster-Whisper contracts: clean input audio, preserve VAD and interruptions,
support user hotwords, and use an explicit resource-bounded fallback. Fish is
not an STT dependency. Never load a second GPU model if doing so could exceed
the whole-agent 5 GB VRAM limit; CPU placement is preferred.

Do not add always-on recording. If background capture is implemented later,
make it a separate opt-in capability with a visible active indicator, bounded
local ring buffer, pause/delete controls, retention expiry, no upload by
default, and redacted audit events. Do not feed ambient audio into memory or
training without a separate user approval.

Add a repeatable consented speech benchmark covering ordinary speech, names,
commands, numbers, silence, noise, accents, and interruptions. Measure STT
WER/CER, command accuracy, false activation, TTS intelligibility/naturalness,
voice consistency, emotional-fit rating, end-to-end latency, CPU/RAM/VRAM, and
time-to-first-audio. Add regression tests for voice immutability, sentiment
validation, unsupported controls, STT fallback, recording consent/retention,
redaction, and unchanged HyperX capture/playback. Document rollback.
```

### Verification prompt

```text
Verify ONLY Packet 6D. Prove all replies use exactly one configured voice,
sentiment values are bounded and safe, unsupported prosody controls degrade
without changing voice, and no Fish runtime/model/brain/memory is required.
Run the consented speech benchmark and report STT WER/CER, command accuracy,
false activations, TTS latency, resource use, naturalness, intelligibility,
voice consistency, and emotional-fit scores separately.

Prove ASR fallback is resource-bounded, VAD handles silence and interruptions,
hotwords improve names without inventing content, and the HyperX path stays
clean. If recording exists, prove it is visibly opt-in, local, bounded,
deletable, expired on schedule, and absent from logs after deletion. Skipped
human or live-audio checks are limitations, not passes. Do not claim real
feelings or Fish-equivalent quality without measured evidence.
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

Include a user-facing audio settings contract. On first run, the bridge must use
the operating-system default microphone and default headphones/output. Settings
must let the user view available input/output devices, choose a device, reset to
OS default, and see typed unavailable/degraded states. Persist stable device
names/roles or approved backend identifiers, never transient numeric indices or
guessed raw ALSA hardware paths. An unavailable selected device must not silently
switch to a different physical device; it must offer an explicit reset-to-default
action. Preserve the verified HyperX mono-input and HyperX output defaults in
the supported Ubuntu profile, keep microphone permission and output selection
visible, and do not silently restart or rewrite the live service/config when a
setting is edited. Add hot-plug, missing-device, permission-denied, invalid
selection, reset-to-default, and restart-persistence tests without recording
ambient audio.

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

Verify the audio settings contract: a clean first run selects the OS default
microphone and headphones/output; the settings path lists only valid devices,
supports explicit selection and reset-to-default, persists across restart, and
reports permission denied, device unavailable, and degraded states honestly.
Prove device reconnects cannot misroute audio, transient indices and raw ALSA
paths are not persisted, an unavailable selection is not guessed, the supported
HyperX mono/USB profile remains intact, and editing settings does not implicitly
restart a service or enable recording.

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
settings mode that exposes the technical details only when requested. Expose
stable machine-readable CLI commands for profile list/show/check/install/
select/reset/upgrade/remove/rollback; changes are dry-run by default, explicitly
applied, staged atomically, backed up, audited, and reversible. Keep voice
selection separate from TTS provider, model, and audio-device selection.

Make the authenticated web dashboard the primary control center with parity for
all supported CLI operations: onboarding, model/STT/TTS/voice selection, audio
devices, permissions, device enrollment, health, task progress, approvals,
cancellation, reports, audit/provenance, export/import, upgrade, reset, and
rollback. Every control must call a real backend contract and display typed
success, unavailable, degraded, denied, partial, or failed state. Require
confirmation for side effects, preserve loopback binding by default, escape
untrusted text, and never expose secrets, private message bodies, prompts,
credentials, or filesystem paths. Add API, negative/security, persistence,
accessibility, and local UI smoke tests for every supported control. Reject
placeholder buttons, frontend-only state, and fake success.

Brand the distributable as Qubit Voice Agent. Prepare the physical repository
rename from `qubit-voice-agent` to `qubit-voice-agent` as a separate release
migration, updating package metadata, docs, service/unit identifiers, configured
paths, launchers, fixtures, and compatibility aliases together. Provide a
dry-run, backup, migration, independent verification, and rollback path. Do not
move, delete, or silently re-enroll existing user state, and do not perform the
rename as incidental cleanup.

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

## Final handoff reminder

Every agent must end with evidence and rollback. “Implemented” means code
exists. “Verified” means an independent agent reproduced the relevant behavior.
“Degraded” means a limitation is visible. “Done” is not a valid implementation
handoff status.
