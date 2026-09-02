# Upstream Reuse Record

Status: **baseline — Packet 0A** (provenance inventory only; **no production code was copied in Packet 0A**)
Date: 2026-08-24
Source contract: `docs/UNIFIED_AGENT_PLAN.md` sections 4.1, 6.2, and 9.

Rules enforced by this file:

- Every upstream-derived unit must be recorded here **before** production code is written.
- Pins are authoritative. Current upstream `main` was checked **only for compatibility drift**;
  floating `main` is never a source.
- "Installed", "verified at commit", and "adapted into production" are separate facts. Today
  every record below is at most **VERIFIED (source/tests inspected at the pinned commit)**;
  none is IMPLEMENTED in production yet.
- `THIRD_PARTY_NOTICES.md` is intentionally **not created yet**: Packet 0A copies no substantial
  code. It must be created in the same packet that performs the first COPY/ADAPT, with the
  notices listed in §15.

---

## R1 — Event types and SSE encoding

```text
LOCAL REQUIREMENT: Phase 2 typed event stream (lifecycle/text/tool-call/state/activity/raw
families, camelCase wire fields, null omission, SSE framing) on the existing
ThreadingHTTPServer without new frameworks.
UPSTREAM REPOSITORY + COMMIT: https://github.com/ag-ui-protocol/ag-ui @ 821b8c227c6fe7b78de2c8259173dd7cbbdf181b
SOURCE FILES + SYMBOLS:
  sdks/python/ag_ui/core/events.py → EventType(str, Enum): TEXT_MESSAGE_START/CONTENT/END/CHUNK,
    TOOL_CALL_START/ARGS/END/CHUNK/RESULT, STATE_SNAPSHOT, STATE_DELTA ("JSON Patch (RFC 6902)"),
    MESSAGES_SNAPSHOT, ACTIVITY_SNAPSHOT/DELTA, RAW, CUSTOM, RUN_STARTED/FINISHED/ERROR,
    STEP_*, THINKING_*, REASONING_*; BaseEvent(type, timestamp, raw_event).
  sdks/python/ag_ui/core/types.py → ConfiguredBaseModel: alias_generator=to_camel,
    populate_by_name=True; model_serializer drops optional-None fields on every path
    (camelCase + null-omission CONFIRMED).
  sdks/python/ag_ui/encoder/encoder.py → get_content_type()="text/event-stream";
    _encode_sse → "data: {json}\n\n".
UPSTREAM TESTS/FIXTURES: sdks/python/tests/test_encoder.py → test_encode_sse_method
  ("data: " prefix, "\n\n" suffix, camelCase decoded keys, snake_case absent),
  test_null_value_exclusion, test_round_trip_serialization, test_encoder_initialization,
  test_encode_method, test_encode_with_different_event_types.
LICENSE: MIT (Copyright (c) 2025) — VERIFIED at pinned commit (LICENSE blob fetched).
DECISION: ADAPT — reproduce enum strings, wire names, central null-omission, SSE framing, and
  the conformance tests in a stdlib-only local module. Do NOT import the pydantic-based SDK.
LOCAL DESTINATION: voicebridge/events/ (+ tests/test_events_agui_conformance.py)
DEPENDENCIES ADDED: none intended. NOTE: the upstream unit itself requires pydantic>=2
  (events.py/types.py import pydantic); adaptation avoids it because pydantic is undeclared in
  pyproject.toml and forbidden by plan rules 6.4/6.8.
BEHAVIOR KEPT: event-type families verbatim (subset the bridge emits); snake_case→camelCase wire
  keys (messageId, toolCallId, toolCallName, parentMessageId); optional-None fields omitted
  centrally, never serialized as null; STATE_DELTA = ordered RFC 6902 patch list against a
  snapshot baseline; frame format `data: <single-line json>\n\n`; content type text/event-stream.
BEHAVIOR INTENTIONALLY OMITTED: pydantic validation machinery/discriminated unions; proto media
  type constant; chunk/thinking/reasoning families not emitted by the bridge.
WHY THIS IS THE SMALLEST FIT: the protocol is a flat JSON wire contract expressible with plain
  dict serialization; vendoring the SDK would drag an undeclared dependency into production.
COMPATIBILITY DRIFT: additive on main after pin (metadata-on-events 8f61581 2026-08-18,
  TokenUsage 04f3426); claimed symbols unchanged. Pin remains authoritative.
IMPLEMENTATION STATUS: adapted into production as voicebridge/events/ (Packet 2A,
  2026-08-24) with conformance tests tests/test_events_agui_conformance.py
  (one-to-one mirrors of the five pinned test_encoder.py vectors) and local
  divergence/journal/state tests tests/test_packet2a.py; MIT notice added to
  THIRD_PARTY_NOTICES.md. Upstream-derived unit = voicebridge/events/core.py
  (274 lines ≤ 300 gate): enum subset verbatim, camelCase + central null
  omission, compact `data: <json>\n\n` SSE framing, fail-closed validation via
  explicit field specs instead of pydantic. voicebridge/events/state.py (RFC
  6902 add/remove/replace subset; arrays replaced wholesale; move/copy/test
  unsupported → typed PatchUnsupportedError so callers request a fresh
  snapshot) and voicebridge/events/journal.py (append-only ordered JSONL with
  monotonic seq, torn-tail-tolerant recovery, redaction at the write boundary)
  are LOCAL plan-mandated units (AG-UI has neither); they are not upstream
  translations. Local divergences implemented and tested: chunk/thinking/
  reasoning families omitted; RUN_CANCELLED added (plan Phase 2 terminal event;
  pinned AG-UI maps aborts onto RUN_ERROR); RUN_* wire shapes keep pinned
  fields while journal records carry extra run_id/parent_id/seq/schema/
  redacted envelope keys stripped by to_wire(); nested payloads pass through
  untransformed exactly like the pinned encoder.
```

## R2 — Human approval behavior

```text
LOCAL REQUIREMENT: Phase 1 approval flow: InProgress→Executing→Complete gating, respond exposed
only while executing, settle once, abort-listener cleanup, renderer cleanup, plus restart-safe
durable approval IDs as a documented local safety extension.
UPSTREAM REPOSITORY + COMMIT: https://github.com/CopilotKit/CopilotKit @ e9387e04835545c45744b791aee7c9c03520be31
SOURCE FILES + SYMBOLS: packages/react-core/src/v2/hooks/use-human-in-the-loop.tsx →
  useHumanInTheLoop(tool, deps); ToolCallStatus.InProgress/Executing/Complete branches
  (respond passed ONLY during Executing, else respond: undefined); exhaustive `never` check;
  single settlement via resolvePromiseRef guard before cleanupAbortRef; pre-aborted signal
  rejects immediately; listener registered {once:true} and removed on settlement; unmount calls
  copilotkit.removeHookRenderToolCall. State is in-memory promise refs only.
UPSTREAM TESTS/FIXTURES: packages/react-core/src/v2/hooks/__tests__/use-human-in-the-loop.e2e.test.tsx
  (EXISTS at pin): statusHistory [InProgress, Executing, Complete]; respondSelections===["yes"]
  (settle once); simultaneous tools; two hook instances isolated; dynamic register/unregister;
  deps-driven re-render; toolCallId+agentId attribution; reconnect/remount/late-mount visibility;
  sequential execution; run-abort rejects pending promise into an explicit error tool result.
LICENSE: MIT (Copyright (c) Atai Barkai) — VERIFIED at pinned commit.
DECISION: ADAPT — translate the tested gating/settle-once/abort-cleanup behavior into the Python
approval store and static UI; add durable restart-safe approval IDs locally (upstream hook is
in-memory — divergence documented here per plan §4.1).
LOCAL DESTINATION: voicebridge/approvals.py (+ existing static UI JS) + tests/test_approvals.py
DEPENDENCIES ADDED: none (behavioral adaptation; TSX source is reference contract only).
BEHAVIOR KEPT: three-state gating; respond live only while Executing; one settlement per approval
ID (second respond is a no-op); abort settles pending approvals as explicit error, never silent
success; cleanup when the requesting context goes away.
BEHAVIOR INTENTIONALLY OMITTED: React rendering, zod schemas, CopilotChat/agent plumbing;
in-memory-only durability (replaced by the local durable store).
WHY THIS IS THE SMALLEST FIT: only the state machine and settlement discipline transfer; the
React runtime is explicitly out of scope, so its e2e assertions become our conformance spec.
COMPATIBILITY DRIFT: pin ≈ main (post-pin top history shows no functional changes to this hook;
  abort-reject #5554 already included in pin).
IMPLEMENTATION STATUS: adapted into production as voicebridge/approvals.py
  (Packet 1B) with conformance tests tests/test_packet1b.py::test_approval_*;
  MIT notice added to THIRD_PARTY_NOTICES.md.
  Packet 2B addendum: ApprovalJournal accepts an optional injected event_sink;
  the durable state machine itself is UNCHANGED (settle-once/abort semantics
  re-proven by tests/test_packet2b.py::test_approval_* and
  test_durability_does_not_depend_on_sink_failure). Restart-safe durability
  remains the documented local extension; Packet 2B only mirrors transitions
  into the typed event stream (AG-UI CUSTOM family).
```

## R3 — Isolation capability probe, path policy, lifecycle

```text
LOCAL REQUIREMENT: Phase 3 isolated runner on Bubblewrap 0.9.0 + user-systemd transient units:
startup probe with fail-closed typed unavailable result, namespace smoke test, env/path policy,
lifecycle ownership, cleanup semantics.
UPSTREAM REPOSITORY + COMMIT: https://github.com/alibaba/OpenSandbox @ 6b2023e9b7eb80a940d88e6ae05fcbc0eb0cf23f
  (GitHub now redirects to opensandbox-group/OpenSandbox; the pin resolves at the original slug.)
SOURCE FILES + SYMBOLS:
  specs/sandbox-lifecycle.yml → SandboxState: Pending, Running, Pausing, Paused, Resuming,
    Stopping, Terminated, Failed; reason codes; lastTransitionAt. CORRECTION vs plan text: there
    is NO "starting" state (create returns Running synchronously; Pausing/Resuming are the
    transitional states). Local lifecycle may simplify; divergence must be documented.
  components/execd/pkg/isolation/probe.go → Probe(cfg) ProbeResult{Available, Isolator, Version,
    SetprivAvailable, UsernsAvailable, Message}; bwrap --version parse; per-mode smoke tests run
    bwrap "--unshare-pid/--unshare-uts/--unshare-ipc/--unshare-cgroup", "--ro-bind / /",
    "--proc /proc", command `true` (probe-only whole-root read bind CONFIRMED).
  components/execd/pkg/isolation/bwrap_linux.go → findBwrap search order; isSetuidBinary;
    NewBwrapWithProbe (probe reused at startup); WrapWithLifecycle fail-closed gates.
  components/execd/pkg/isolation/bwrap.go → argv builder: namespace flags; ExtraWritable binds;
    env handling = --clearenv + explicit --setenv allowlist, execd config env always stripped,
    secret-shaped var blacklist (*_API_KEY/*_TOKEN/*_SECRET/*_PASSWORD/AWS_*); validateWrapOptions
    requires ABSOLUTE bind paths.
  components/execd/pkg/isolation/config.go → Config TOML defaults (allowed_writable
    [/workspace,/mnt,/media,/data]).
  components/execd/pkg/isolation/isolator.go → WorkloadLifecycle contract (WaitForIdentity→
    MarkReady fail-closed gate; Abort/DrainDone/Close idempotent).
  CORRECTION vs plan text: NO symlink-resolved allowlists / EvalSymlinks / realpath escape checks
  exist at this pin. Symlink resolution is a LOCAL requirement (already mandated by plan Phase 3
  task 11); do not attribute it upstream.
UPSTREAM TESTS/FIXTURES: components/execd/pkg/runtime/isolated_session_lifecycle_test.go →
  lifecycle-required error; nil-lifecycle rejected; namespaces pinned before READY; startup
  identity/ready failures reap workload; fatal drain kills workload; trust-loss rejects later
  runs; bounded stop timeout; exit barrier serializes group signal; idempotent stop.
LICENSE: Apache-2.0 (Copyright 2026 Alibaba Group Holding Ltd.) — VERIFIED at pinned commit.
DECISION: ADAPT — port probe shape, smoke-test argv, env clear+allowlist semantics, fail-closed
gate ordering, and the cleanup-test scenarios to Python. No Go server/execd code.
LOCAL DESTINATION: voicebridge/isolated.py (single flat module).
  AMENDMENT 2026-08-25, Packet 3B, before production code: the original
  destination `voicebridge/sandbox/isolated.py` would convert the working
  `voicebridge.sandbox` module into a package mid-packet; flat layout matches
  the repo convention (capabilities.py, broker.py). Re-probed at pin by 3B
  implementer: probe.go/bwrap_linux.go/config.go/lifecycle.yml/lifecycle_test.go
  claims above CONFIRMED verbatim.
DEPENDENCIES ADDED: none beyond installed bubblewrap binary + systemd (stdlib subprocess/os/pathlib).
BEHAVIOR KEPT: startup probe before accepting work; typed unavailable result with diagnostic;
exact smoke args with `true` payload; per-mode availability reporting; env cleared then
allowlisted with secret-shaped deny; one owner object drives cleanup; bounded idempotent stop;
no orphan after failed startup.
BEHAVIOR INTENTIONALLY OMITTED: overlay commit/diff/persist, seccomp BPF memfd, native gate FDs,
setpriv identity switching details, profiles, MLX/Kubernetes paths; upstream's missing symlink
resolution (implemented locally instead).
WHY THIS IS THE SMALLEST FIT: reusable content is probe/lifecycle/error semantics plus failure-mode
tests; the execd daemon, Go SDK, and control plane are explicitly rejected by plan §4.1.
COMPATIBILITY DRIFT: org moved (redirect); main continues evolving per-mode capabilities; pin intact.
```

## R4 — Large-model memory experiments

```text
LOCAL REQUIREMENT: benchmark dimensions/guards for an optional isolated deep-tier experiment;
production voice inference stays on Ollama; Torch/Transformers/bitsandbytes stay out of the bridge.
UPSTREAM REPOSITORY + COMMIT: https://github.com/lyogavin/airllm @ 6ab3b9db9b2fb595e8a4d966f0e1ba600677b1fe
SOURCE FILES + SYMBOLS: air_llm/airllm/airllm_base.py → max_pinned_layer_bytes = 2 * 1024**3
  (pinned-prefetch cap; larger layers fall back to pageable); __init__: compression enabled ⇒
  prefetching force-disabled with printed notice; executor exists only when prefetching; meta-device
  layer streaming via forward hooks; single-worker ThreadPoolExecutor prefetch.
  Dependency reality: imports torch, transformers, accelerate, tqdm; bitsandbytes REQUIRED for
  compression (ImportError otherwise).
UPSTREAM TESTS/FIXTURES: air_llm/tests/test_compression.py → fp16 CUDA tensors round-tripped via
  compress/uncompress_layer_state_dict for None/'4bit'/'8bit'; torch.equal for None, RMSE<0.1 for
  quantized. Validates compression ACCURACY, not timing; requires CUDA.
LICENSE: Apache-2.0 — VERIFIED at pinned commit.
DECISION: REJECT (for production) — zero code enters the bridge. Record keeps the two behavioral
guards and the measurement taxonomy (disk-load time, GPU-move time, compression overhead, pinned
cap) as design references for any future isolated experiment.
LOCAL DESTINATION: none in production; documentation reference only (any future experiment lives
outside the bridge venv/service).
DEPENDENCIES ADDED: would be torch+transformers+accelerate+bitsandbytes+CUDA — precisely what the
plan forbids; avoided by rejection.
BEHAVIOR KEPT: (recorded as design notes, not code) compression-disables-prefetch guard; pinned-layer memory
cap concept; disk-vs-GPU-move-vs-compression profiling split.
BEHAVIOR INTENTIONALLY OMITTED: entire layer-shard runtime, safetensors splitting, MoE expert
streaming, quantized loaders, HF download/cache logic.
WHY THIS IS THE SMALLEST FIT: only two guards and a measurement taxonomy transfer; any code reuse
would violate declared-dependency and Ollama-boundary contracts.
COMPATIBILITY DRIFT: active upstream (hook-based rearchitecture and fp8 support already present at
pin; main continues adding fp8/MoE handling). Claimed behaviors stable across pin→main.
NOTE: AirLLM layer streaming makes NOTHING about the resident Ollama model faster; STEAL_LIST.md
wording implying speed transfer is corrected in the Packet 0A mismatch report.
```

## R5 — Resident runner lifecycle and typed generation

```text
LOCAL REQUIREMENT: P0-M / Phase 4 — one resolved runner contract (digest, num_ctx, placement,
keep_alive) for every call to the resident generative model; capture load_duration/prompt-eval/
eval metrics; JSON Schema + temperature 0 for router decisions; residency proven via ollama ps.
UPSTREAM REPOSITORY + COMMIT: https://github.com/ollama/ollama @ fb30760996871fa9460115c753afd2c60d4ab0f7
SOURCE FILES + SYMBOLS:
  server/sched.go → Scheduler.getRunner: reuse iff !needsReload; schedulerModelKey =
    ModelPath else "digest:"+Digest; needsReload triggers (reflect.DeepEqual): AdapterPaths,
    ProjectorPaths, full Runner OPTIONS struct mismatch (NumCtx inside api.Options.Runner),
    contextShift change, ping timeout; auto values normalized before compare; keep_alive/
    sessionDuration NEVER triggers reload — only expiry scheduling (time.AfterFunc at refCount 0 →
    unload + waitForVRAMRecovery ≤75% VRAM or 5 s).
  server/routes.go → scheduleRunner(ctx, model, caps, requestOpts, keepAlive, shift) EXISTS;
    called by generate/embed handlers; empty prompt + KeepAlive==0 → expireRunner ("unload").
  Docs (fetched): structured outputs accept "json" OR a full JSON Schema object (temperature 0
  recommended); tool calling uses OpenAI-style tools[] and returns message.tool_calls[] with
  name/arguments.
UPSTREAM TESTS/FIXTURES: not listed in plan row; conformance relies on the documented contracts
above plus local payload-equality assertions across classifier/chat/summary/planner.
LICENSE: MIT (Copyright (c) Ollama) — VERIFIED at pinned commit.
DECISION: WRAP — keep wrapping the running ollama serve over HTTP ("preserve the existing Ollama
boundary"); sched.go semantics become the behavioral spec for our single-contract assertion tests.
LOCAL DESTINATION: voicebridge/local_llm.py (+ a small runner-contract assertion helper) + tests
asserting identical material options across all resident-model payloads.
DEPENDENCIES ADDED: none (HTTP + stdlib json to loopback Ollama API).
BEHAVIOR KEPT: treat {model identity/digest + adapter/projector + full options struct incl.
num_ctx} as the reload key; keep_alive treated purely as expiry policy; residency asserted from
`ollama ps` evidence, never inferred; capture load_duration/prompt_eval_*/eval_* metrics;
structured decisions via full JSON Schema `format` + temperature 0.
BEHAVIOR INTENTIONALLY OMITTED: scheduler internals (VRAM prediction, GPU placement, mmap/OOM
retry) — owned by the Ollama server we wrap.
WHY THIS IS THE SMALLEST FIT: Ollama is already the deployed boundary; the reusable artifact is
the precise reload-key/keep-alive/metric semantics that define the unified-contract tests.
COMPATIBILITY DRIFT: main evolves sched.go after pin (ps data-race fix, GetRunner export removal,
imagegen removal); pin remains valid for needsReload/scheduleRunner semantics.
IMPLEMENTATION STATUS (Packet 4A, 2026-08-25, before production code): the benchmark harness
  (voicebridge/benchmarking/harness.py + scripts/benchmark_transitions.py) WRAPS the running
  Ollama boundary per this record without modifying it. Applied pin facts: (a) reload key =
  model identity + full options struct (num_ctx included) — keep_alive affects expiry only;
  the harness therefore detects option-driven reload risk by diffing captured material option
  dicts across consecutive captured requests, never by timing inference alone; (b) residency is
  read from GET /api/ps (never inferred from keep_alive); (c) empty-prompt generate with
  keep_alive=0 is used as the documented expireRunner/unload path for the opt-in live cold
  phase, followed by a restore call at the pre-run observed context with keep_alive=-1 and an
  /api/ps verification (rule 32 restoration duty); (d) load_duration/prompt_eval_*/eval_* are
  captured verbatim from response bodies. No Ollama source is copied; MIT notice obligation
  unchanged (WRAP over HTTP, no code shipped).
```

## R6 — Fast semantic route selection

```text
LOCAL REQUIREMENT: Phase 4C optional L1 semantic router: versioned route examples, per-route
thresholds, score/margin evidence, explicit no-match, held-out evaluation, threshold fitting —
stdlib-only, CPU-only, using the existing Ollama embedding endpoint only if it passes gates.
UPSTREAM REPOSITORY + COMMIT: https://github.com/aurelio-labs/semantic-router @ a4576168d9589397a7e0c6ff77f5d05469a56e2e
SOURCE FILES + SYMBOLS: semantic_router/routers/base.py → __call__ (L571), _pass_routes (L620),
  fit (L1683), evaluate (L1759), threshold_random_search (L1829). Per-route override CONFIRMED
  (route.score_threshold if set else global; unset ⇒ always pass). Score carried on
  RouteChoice.similarity_score (aggregation sum/mean/max). CORRECTION vs plan text: no-match
  returns an EMPTY RouteChoice(), not None — local adaptation must pick one sentinel and document it.
UPSTREAM TESTS/FIXTURES: tests/unit/test_router.py, test_route.py, test_sync.py, test_schema.py,
  test_function_schema.py (+ functional/integration suites) at pin.
LICENSE: MIT (Copyright (c) 2024 Aurelio AI) — VERIFIED at pinned commit.
DECISION: ADAPT — reimplement threshold/evaluation/search semantics in plain Python; do NOT vendor
the class (pydantic-v2 base, encoder/index plugin stack, numpy dependency tree).
LOCAL DESTINATION: voicebridge/router/ (threshold logic + evaluation harness) + held-out fixtures.
DEPENDENCIES ADDED: none (numpy/pydantic/tqdm/OpenAI-encoder stack intentionally excluded).
BEHAVIOR KEPT: per-route override > global; unset ⇒ pass-through documented; aggregated similarity
score as margin evidence; empty-result no-match sentinel; random threshold search + accuracy
evaluation semantics.
BEHAVIOR INTENTIONALLY OMITTED: dynamic LLM routes/function_schemas, remote indexes
(Pinecone/Postgres/Qdrant), auto_sync/locking, YAML/JSON route loading, async mirrors.
WHY THIS IS THE SMALLEST FIT: ~150 lines of threshold/eval semantics serve as the spec; the file
itself drags a ruled-out dependency tree.
COMPATIBILITY DRIFT: main evolved past pin; pin stays authoritative.
IMPLEMENTATION STATUS (Packet 4C, 2026-08-25, before production code): ADAPT
   executed as voicebridge/router/l1.py — per-route threshold override >
   global, aggregated cosine score carried as margin evidence alongside an
   explicit top-one/top-two margin gate, empty-result NO-MATCH sentinel,
   grid threshold calibration (fit/evaluate semantics) over the corpus
   acceptance slice with the held-out slice reserved untouched for reporting.
   Excluded exactly per record: pydantic/numpy/encoder stack, dynamic LLM
   routes, remote indexes. Route vectors precomputed once per process;
   scoring is stdlib dot-product. Embedding runs on the existing Ollama
   endpoint under measurement gates (CPU placement, warm p95, RAM delta,
   zero generative-runner eviction) — omitted entirely if any gate fails.
```

## R7 — Small local model/tool-use candidate (Qwen)

```text
LOCAL REQUIREMENT: preferred challenger to the current abliterated Qwen3-4B Q4 alias for Phase 4D
bake-off, benchmarked under identical runner/context contract with separate aliases and checksums.
UPSTREAM REPOSITORY + COMMIT: Hugging Face pinned revisions (no git repo; revision = provenance):
  https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507 @ cdbee75f17c01a7cc42f958dc650907174af0554
  https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF @ a06e946bb6b655725eafa393f4a9745d460374c9
  Experimental (evaluate only AFTER the 2507 challenger):
  https://huggingface.co/Qwen/Qwen3.5-4B @ 851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a
  https://huggingface.co/unsloth/Qwen3.5-4B-GGUF @ e87f176479d0855a907a41277aca2f8ee7a09523
SOURCE FILES + SYMBOLS: model-card/tree facts at the pinned revisions (symbols = artifact/template
  contract): (1) EXISTS; non-thinking-only instruct
("does not generate <think></think> blocks"); ctx 262,144. (2) EXISTS; Q4_K_M.gguf (≈2.5 GB) and
Q5_K_M.gguf (≈2.89 GB) PRESENT at rev + full quant ladder/template. (3) EXISTS; MULTIMODAL
(image-text-to-text pipeline, vision encoder/preprocessor configs) — text-only usage requires the
documented language-model-only mode; card documents MTP/speculative serving ONLY via SGLang MAIN
branch flags (--speculative-algo NEXTN ...) or vLLM NIGHTLY (--speculative-config qwen3_next_mtp) —
NOT automatically available in Ollama. (4) EXISTS; Q4_K_M/Q5_K_M PLUS separate mmproj-BF16/F16/F32
projector GGUFs (vision requires the mmproj file under llama.cpp-style runtimes — must NOT be
loaded for voice routing).
UPSTREAM TESTS/FIXTURES: n/a (model artifacts; card benchmark tables are supporting evidence only;
local private corpus decides per plan §4 task 12).
LICENSE: Apache-2.0 for all five repos — VERIFIED at pinned revisions.
DECISION: COPY (revision pins + behavioral contracts only) — weights are downloaded/pulled at
install/bake-off time, never vendored into Git.
LOCAL DESTINATION: docs/UPSTREAM_REUSE.md pins + Phase 4D candidate manifest (aliases kept separate
from qubit-brain4 until rollback is proven).
DEPENDENCIES ADDED: none in-repo.
BEHAVIOR KEPT: exact revision pins; non-thinking instruct template assumptions; separate aliases +
recorded hashes/license/disk cost per candidate.
BEHAVIOR INTENTIONALLY OMITTED: Qwen3.5 vision/multimodal inputs (voice bridge is text/voice);
MTP/speculative decoding claims on Ollama (unsupported surface); any in-place replacement of the
resident alias.
WHY THIS IS THE SMALLEST FIT: zero code; two behavioral contracts (non-thinking mode, projector
exclusion) plus pins are everything Packet 4D needs.
COMPATIBILITY DRIFT: quantizer builds track the Qwen revs (license inherited); SGLang-main/vLLM-
nightly MTP requirement makes that acceleration unstable — out of scope.
IMPLEMENTATION STATUS (Packet 4D, 2026-08-25, before production code): executed as
   scripts/bakeoff_4d.py — pinned-revision GGUF download (hf CLI, revision-pinned),
   SHA-256 verified against the HF LFS oid at the pinned revision, separate candidate
   aliases (qubit-cand-q4/q5) created from a contract-matching Modelfile, transition +
   planner benchmarks under the SAME runner/context contract as production, candidates
   unloaded afterwards, production alias digest verified untouched, rollback manifest
   recorded. Vision projector files deliberately not downloaded; Qwen3.5 optional
   challenger deferred until the 2507 challenger is measured (plan §4.1 row order).
```

## R8 — Personal memory lifecycle

```text
LOCAL REQUIREMENT: Phase 5B personal memory on SQLite/FTS5: scoped records, dedup, provenance/
history, expiration, correction/deletion, core-vs-recall tiers, bounded rendering; explicit user
operations; inferred memories staged, never auto-promoted with secrets.
UPSTREAM REPOSITORY + COMMIT: https://github.com/mem0ai/mem0 @ 8d5b7865bd0521e75fe9ab1b260eeccc83f8a378
SOURCE FILES + SYMBOLS: mem0/memory/main.py → add (L760), get_all (L1255), search (L1379),
  update (L1815), delete (L1869), history (L1946). Dedup CONFIRMED: md5 content hash stored in
  payload["hash"], duplicate adds skipped ("hash match"). Provenance CONFIRMED: attributed_to /
  actor_id / role metadata; caller-set identity scopes stripped (_strip_identity_keys); session
  scope built from user_id/agent_id/run_id.
UPSTREAM TESTS/FIXTURES: tests/test_memory.py, tests/test_main.py, tests/test_memory_integration.py,
  tests/memory/, tests/vector_stores/, tests/configs/ at pin.
LICENSE: Apache-2.0 (Copyright [2023] [Taranjeet Singh]) — VERIFIED at pinned commit.
DECISION: ADAPT — copy the method-set shape and hash-dedup/provenance contract into a minimal local
SQLite/FTS5 store; reject the framework (LLM extraction pipeline, factory system, telemetry).
LOCAL DESTINATION: voicebridge/memory/ (store facade over stdlib sqlite3 FTS5) + contract tests
mirroring add/search/update/delete/history behavior incl. duplicate-skip vectors.
DEPENDENCIES ADDED: none (no mem0 package; no vector database).
BEHAVIOR KEPT: method names/signature spirit; MD5-normalized content-hash dedup; provenance fields
written alongside each record; scoped queries.
BEHAVIOR INTENTIONALLY OMITTED: LLM fact-extraction/infer path, BM25/entity stores, telemetry,
20+ vector-store backends, platform/project features, expiration semantics (local requirement
extends upstream: expiry/tombstone/correction are LOCAL additions).
WHY THIS IS THE SMALLEST FIT: the durable contract is the method set + hash-dedup + provenance
payload; the rest is plugin infrastructure the plan forbids adding.
COMPATIBILITY DRIFT: upstream moves fast; pin governs.
IMPLEMENTATION STATUS (Packet 5B, 2026-08-25, before production code): ADAPT
   executed as voicebridge/memory/store.py — method-set contract
   (add/search/update/delete/history semantics), MD5→sha256 normalized-hash
   deduplication (duplicate adds skipped, "hash match"), and provenance
   payload carried per record (attributed_to/actor via source fields).
   Everything else (LLM extraction, vector stores, telemetry) excluded per
   the record. Local extensions added and tested: tiers
   core/working/recall/archive, scope isolation, expiry, tombstones,
   supersedes/correction, append-only history, staged inferred memories
   (never auto-promoted), private-file modes.
   LETTA (R9): REJECT AFFIRMED — the pinned tree remains a docs-only stub;
   the unpinned archive branch is explicitly unsupported, so NO Letta code
   or branch is used anywhere. The core/archival/recall TIER TAXONOMY is
   referenced as a concept only (plan §4.1 row 10). No re-pin was performed
   because the verifier/user accepted R9's reject record.
```

## R9 — Memory blocks / core-vs-recall tiers (Letta)

```text
LOCAL REQUIREMENT: reference semantics for labeled memory blocks and core-versus-recall/archival
separation (plan §4.1 row 10 cites letta/schemas/memory.py at a pinned commit).
UPSTREAM REPOSITORY + COMMIT: https://github.com/letta-ai/letta @ 4511fa0bc91f68fbab32b91f694617271ea9012b
SOURCE FILES + SYMBOLS: letta/schemas/memory.py → **FETCH_FAILED (HTTP 404)**. Root cause verified:
the pinned commit RESOLVES (signature dated 2026-08-23) but its tree is a DOCS/POLICY STUB ONLY
(README/LICENSE/TERMS/PRIVACY/AI_POLICY/AGENTS.md/.github — no code directories). The repo was
archived/stripped after the plan pinned it; the V1 server survives only on an UNPINNED `archive`
branch (there: Memory(blocks: List[Block]), BasicBlockMemory, ChatMemory(persona, human, limit),
core_memory_append/replace; archival/recall summaries), explicitly unsupported ("receives no fixes
or security updates").
UPSTREAM TESTS/FIXTURES: none available at the pinned ref (no code in tree).
LICENSE: Apache-2.0 (LICENSE blob fetched at pin; "Copyright 2023, Letta authors") — license itself
VERIFIED, but there is no code at the pin to reuse.
DECISION: REJECT as currently pinned — re-pin required before ANY Letta-derived reuse. If a future
packet re-pins an archive-branch commit: ADAPT concepts only (block model + core/archival/recall
taxonomy); never vendor (deep server coupling: otel, openai types).
LOCAL DESTINATION: none yet; conceptual reference only.
DEPENDENCIES ADDED: none.
BEHAVIOR KEPT: nothing at present (concept notes only: labeled blocks with limits; core in-context
vs archival/recall external split — already covered by Mem0 record R8 plus local tier design).
BEHAVIOR INTENTIONALLY OMITTED: prompt rendering machinery, git-backed memory, skills sections,
server APIs.
WHY THIS IS THE SMALLEST FIT: nothing smaller than "not now"; the pinned ref cannot satisfy the row.
COMPATIBILITY DRIFT: SEVERE — upstream archived/stripped post-pin. This is the one plan §4.1 entry
whose cited source no longer exists at the recorded commit; flag raised to the user for a re-pin
decision in a later packet (Packet 5B), not silently switched to a floating branch.
```

## R10 — Existing-session browser observation (CDP)

```text
LOCAL REQUIREMENT: attach read-only to the user's already running real Chromium on loopback CDP;
reuse logged-in Gmail/Instagram/Messenger targets; extract stable item fields; clipped screenshots
via Page.captureScreenshot; never focus/close/mutate user tabs.
UPSTREAM REPOSITORY + COMMIT: https://github.com/ChromeDevTools/devtools-protocol @ 5ee78b32ae561f7830853c089459200e6a3467e4
SOURCE FILES + SYMBOLS: pinned tree resolves; reused surface is the stable 1-3 protocol slice —
Target, Runtime, DOM, Page domains; Page.captureScreenshot params format (jpeg|png|webp), quality
(jpeg), clip {x,y,scale,width,height}; returns base64 data. fromSurface/captureBeyondViewport/
optimizeForSpeed flagged Experimental → omitted. Pre-implementation step per plan: inspect installed
Chromium `/json/version` + `/json/protocol` (NOT performed in Packet 0A — browser not running with
CDP; recorded as DEGRADED in the mismatch report).
UPSTREAM TESTS/FIXTURES: n/a (protocol JSON specs; browser behavior contract lives in official docs).
LICENSE: BSD-3-Clause (Copyright 2014 The Chromium Authors) — VERIFIED at pinned commit.
DECISION: WRAP — consume CDP through the pinned stable slice; cite the contract; vendor nothing.
LOCAL DESTINATION: repository-owned read-only CDP client (Packet 8B) + docs pin entry.
DEPENDENCIES ADDED: none (raw WebSocket/HTTP CDP client over stdlib).
BEHAVIOR KEPT: captureScreenshot param semantics incl. clip rectangle; stability assumptions
limited to 1-3-stable domains.
BEHAVIOR INTENTIONALLY OMITTED: experimental params, printToPDF, tip-of-tree domains;
Target.activateTarget / Page.bringToFront (forbidden by plan rules regardless of availability).
WHY THIS IS THE SMALLEST FIT: a protocol contract needs citation, not vendoring; the stable slice
covers observation + screenshot needs.
COMPATIBILITY DRIFT: tip-of-tree adds domains/methods; pinned 1-3 slice frozen.
```

## R11 — Hourly local activation (systemd timer)

```text
LOCAL REQUIREMENT: durable hourly activation for the communications monitor via ONE templated user
oneshot service + timer (Packet 8D, only after authorization); overlap lease as second guard.
UPSTREAM REPOSITORY + COMMIT: https://github.com/systemd/systemd (v255) @ db11bab38ccf1ed257f310d29070843d4c58ea01
SOURCE FILES + SYMBOLS: man/systemd.timer.xml at pin → OnCalendar=, Persistent= (catch-up applies
to OnCalendar timers), AccuracySec= (default 1 min coalescing window), RemainAfterElapse=,
WakeSystem= (omitted locally); SPDX-License-Identifier: LGPL-2.1-or-later.
UPSTREAM TESTS/FIXTURES: upstream calendar-spec unit tests not fetched — man page is the normative
reference for this documentation-contract reuse.
LICENSE: LGPL-2.1-or-later — VERIFIED at pinned commit (LICENSE.LGPL2.1 fetched; §13 "or later"
option). NOTE: unit-file authorship wraps systemd; no systemd source is copied, so LGPL obligations
are limited to attribution in docs (no derivative-of-systemd code is shipped).
DECISION: WRAP — author .timer/.service unit files against the pinned man page; ship no systemd code.
LOCAL DESTINATION: systemd/openclaw-communications-monitor.{service,timer} (repository templates,
Packet 8D) + docs pin entry.
DEPENDENCIES ADDED: none (user-systemd 255 present on target machine — confirmed in mismatch report).
BEHAVIOR KEPT: OnCalendar=hourly; Persistent=true catch-up-once semantics; AccuracySec jitter/
coalescing awareness.
BEHAVIOR INTENTIONALLY OMITTED: WakeSystem (never wake a suspended machine), monotonic On*Sec
variants, transient timers.
WHY THIS IS THE SMALLEST FIT: two declarative files + one citation; zero code ownership.
COMPATIBILITY DRIFT: v255 directives stable across supported systemd versions.
```

## R12 — Gmail incremental identity option (history API)

```text
LOCAL REQUIREMENT: OPTIONAL future Gmail cursor/full-resync semantics reference; real-Chromium CDP
remains the required default adapter (plan §4.1 row 13).
UPSTREAM REPOSITORY + COMMIT: Google Workspace Gmail API v1 official REST reference (live docs,
unpinned by nature; access date recorded 2026-08-24):
https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list
(method name `users.history.list`; canonical REST page slug is `users.history/list` — the dotted
form is NOT a valid URL)
SOURCE FILES + SYMBOLS: users.history.list endpoint facts — results chronological with increasing historyId;
params startHistoryId (required), maxResults, pageToken, labelId; "History IDs increase
chronologically but are not contiguous"; invalid/out-of-date startHistoryId typically returns
HTTP 404 → client MUST perform full sync; historyId typically valid ≥ 1 week (rarely hours);
response carries nextPageToken + historyId; absent nextPageToken ⇒ persist returned historyId.
Scopes: gmail.readonly / gmail.metadata suffice for history reads.
UPSTREAM TESTS/FIXTURES: n/a (vendor API contract).
LICENSE: Google proprietary terms — NOT an OSS reuse; no notice obligation beyond citation.
DECISION: WRAP (future, separately authorized) — thin client + cursor state machine; adding OAuth
credentials is a distinct authorized task; NEVER quietly replaces the user's browser session.
LOCAL DESTINATION: future gmail sync adapter (not scheduled in current packets beyond 8A contract
notes).
DEPENDENCIES ADDED: none decided yet.
BEHAVIOR KEPT: monotonic cursor with gap tolerance; pagination until nextPageToken absent; 404 ⇒
full resync + cursor reset; persist latest historyId.
BEHAVIOR INTENTIONALLY OMITTED: push/watch notifications, label-filtered histories, modify scope
(forbidden — read-only monitoring only).
WHY THIS IS THE SMALLEST FIT: one endpoint + cursor state machine as a design reference; default
adapter stays CDP.
COMPATIBILITY DRIFT: live docs can change without notice; facts recorded with access date.
```

## R13 — Local report-UI rendering/visual tests (Playwright)

```text
LOCAL REQUIREMENT: Playwright remains the declared library for isolated local report-UI tests only
(link/asset/print/visual regression); never owns or launches the monitored Gmail/Instagram/Messenger
sessions.
UPSTREAM REPOSITORY + COMMIT: https://github.com/microsoft/playwright-python tag v1.62.0 ==
commit 3b7c24c3e67dc84f7b0eddd0c5fd2ca685705021 (tag object SHA verified EXACT match).
SOURCE FILES + SYMBOLS: reused surface is the public locator/screenshot API family consumed as a
dev tool; no upstream source vendored.
UPSTREAM TESTS/FIXTURES: upstream suites not reused; local tests target our report UI.
LICENSE: Apache-2.0 (Portions Copyright (c) Microsoft Corporation; Portions Copyright 2017 Google
Inc.) — VERIFIED at pinned SHA.
DECISION: WRAP — declared dependency (pyproject already declares playwright>=1.49; installed
1.62.0 matches the reviewed tag). Dev/test-boundary only.
LOCAL DESTINATION: local report-UI tests (Packet 8C scope) — never imported by runtime modules.
DEPENDENCIES ADDED: none new (already declared).
BEHAVIOR KEPT: locator-based interaction/assertions; element/page screenshot for visual checks;
role-based locators preferred per plan.
BEHAVIOR INTENTIONALLY OMITTED: trace viewer, component testing, codegen, API testing; ANY use
against personal accounts/profiles (rule 34/§6.8).
WHY THIS IS THE SMALLEST FIT: pure tooling boundary — pin + two API surfaces; nothing ships.
COMPATIBILITY DRIFT: none at pin; browser binaries pinned separately at install time.
```

## R14 — Optional Gmail fallback CLI (Himalaya)

```text
LOCAL REQUIREMENT: optional fallback Gmail adapter shelling out to an external CLI; must
health-check its actual JSON envelope contract; binary presence alone is NOT a pass.
UPSTREAM REPOSITORY + COMMIT: https://github.com/pimalaya/himalaya v2.1.0 @ ca88bee08ad2e92127b46dc6200d1e8201885156
(commit message "build: prepare v2.1.0"; Cargo.toml bumped 2.0.0→2.1.0 in that very commit ⇒ pin ==
release commit).
SOURCE FILES + SYMBOLS: reused surface = CLI process boundary: JSON envelope output of
message-list/read commands invoked as subprocess.
UPSTREAM TESTS/FIXTURES: upstream Rust suite not reused; local startup health-check substitutes
(run an envelope-list command, validate JSON shape; pin expectations to observed 2.1.0 shapes).
LICENSE: MIT (Copyright (c) 2022-2026 soywod) via LICENSE-MIT — VERIFIED at pinned tree (note:
repo is dual MIT/Apache; raw LICENSE 404s because filenames are LICENSE-MIT/LICENSE-APACHE).
DECISION: WRAP — feature-flagged fallback only; never a library dependency; unhealthy binary
disables only the fallback, not the product.
LOCAL DESTINATION: repository-owned himalaya fallback adapter (Phase 8 scope); replaces today's
$HOME/.hermes external-script dependency (see mismatch report INBOX-01).
DEPENDENCIES ADDED: none in-package (external user-installed binary, version-checked ≥ 2.1.0).
BEHAVIOR KEPT: JSON envelope parsing; graceful degradation when binary missing/unhealthy.
BEHAVIOR INTENTIONALLY OMITTED: IMAP/JMAP/Maildir backends, compose/send flows, config generation;
send/reply capability entirely (monitor is read-only).
WHY THIS IS THE SMALLEST FIT: process-boundary wrap keeps the bridge free of a Rust toolchain.
COMPATIBILITY DRIFT: INSTALLED ≠ HEALTHY — installed binary is 2.1.0 but the earlier live probe
returned an error object; adapter MUST health-check at startup rather than trust installation.
```

## R15 — SSE replay / Last-Event-ID recovery (Packet 2B)

```text
LOCAL REQUIREMENT: reconnecting dashboard clients resume the typed event stream with no
gaps and no duplicates; a stale/missing delta base falls back to a fresh snapshot; JSON
polling stays as compatibility fallback.
UPSTREAM REPOSITORY + COMMIT: WHATWG HTML Living Standard, server-sent events contract
(living specification, unpinned by nature; access date 2026-08-24):
https://html.spec.whatwg.org/multipage/server-sent-events.html — normative facts reused:
`Last-Event-ID` header carries the last received event id on reconnection; frames are
UTF-8 lines with `id:`/`data:`/`:` comment fields terminated by a blank line;
text/event-stream MIME type. AG-UI pin (R1) remains the wire-frame authority
(`data: <json>\n\n`); the id line is a local extension carrying the journal seq.
SOURCE FILES + SYMBOLS: none copied — protocol contract citation only.
UPSTREAM TESTS/FIXTURES: n/a (spec contract); conformance proven locally by
tests/test_packet2b.py reconnect/gap/duplicate vectors against the pinned AG-UI framing.
LICENSE: WHATWG Living Standards are published under the W3C Document License (freely
referenceable; no code shipped) — citation only, no notice obligation.
DECISION: WRAP — author a small fetch-streaming consumer and an in-process hub over the
existing ThreadingHTTPServer; zero spec code vendored.
LOCAL DESTINATION: voicebridge/events/hub.py (server fan-out/replay),
voicebridge/events/runs.py (snapshot/delta tracking + terminal-event coordinator),
frontend/index.html consumer block, /api/events/stream route.
DEPENDENCIES ADDED: none (stdlib http.server threading; browser fetch API).
BEHAVIOR KEPT: Last-Event-ID resumption semantics; blank-line frame delimiters;
text/event-stream content type; heartbeat as `:` comment lines; automatic client
reconnect after stream errors (browser EventSource semantics mirrored by the local
fetch-loop backoff).
BEHAVIOR INTENTIONALLY OMITTED: `retry:` field negotiation (fixed local interval);
CORS-for-cross-origin EventSource credentials mode (token travels only via X-Api-Token
header per Packet 1B; query-string tokens stay forbidden).
WHY THIS IS THE SMALLEST FIT: the durable ordering source is already the Packet 2A
journal; replay = journal scan past a cursor, so the hub adds only fan-out + cursor
arithmetic (~120 lines) instead of any streaming framework.
COMPATIBILITY DRIFT: living standard evolves slowly; facts above stable for years.
```

## R16 — Communications/report schemas (Packet 8A local design)

```text
LOCAL REQUIREMENT: versioned scan / account-health / item / cursor / report-
manifest / asset / notification schemas, stable identity + fallback collision
handling, SQLite migration/retention/statuses, and the atomic cursor/report
commit protocol (plan Phase 8 tasks 1–3; Packet 8A prompt).
UPSTREAM REPOSITORY + COMMIT: none — the schemas and store are LOCAL design.
The packet reuses the ALREADY-recorded upstream pins as references only:
  R10 Chrome DevTools Protocol (BSD-3-Clause) — read-only item extraction and
      Page.captureScreenshot clipping contracts (8B uses these; 8A contracts
      carry the evidence_status labels they define).
  R11 systemd timer (LGPL-2.1-or-later) — hourly activation semantics (8D);
      8A records no service/timer.
  R12 Gmail history API contract — cursor/full-resync design reference only.
  R13 Playwright (Apache-2.0) — local report-UI tests only (8C).
  R14 Himalaya (MIT) — optional fallback adapter (8B scope).
UPSTREAM TESTS/FIXTURES: n/a (local contracts; conformance proven by
  tests/test_packet8a.py + synthetic fixtures tests/fixtures/communications/).
LICENSE: none required — no upstream code copied; provenance for the referenced
  pins already recorded in R10–R14 above.
DECISION: WRAP (design reference) + LOCAL (schema/store authorship). The
  "report licenses" verification obligation is satisfied by R10/R11/R13/R14.
LOCAL DESTINATION: voicebridge/communications/{contracts.py,store.py,__init__.py}
  (standalone; NOT imported by any runtime module until 8B–8D wire it in).
DEPENDENCIES ADDED: none (stdlib sqlite3 / dataclasses / hashlib only).
BEHAVIOR KEPT: new == "first observed after last committed account cursor";
  unread/read/unknown kept separate; stable identity prefers platform
  item/thread IDs then a versioned fallback fingerprint (account scope +
  normalized sender/thread + timestamp + bounded content) with collision
  buckets; cursor advances only after new items + report manifest + assets are
  durably committed in ONE transaction; failed/partial/auth-required scans keep
  the prior cursor; retention defaults confirmed (health 90 d, items 90 d,
  reports 90 d, screenshots 30 d, notifications 90 d) with dry-run + audit log.
BEHAVIOR INTENTIONALLY OMITTED: no CDP client, no report rendering, no
  notification dispatch, no timer/service, no live state access (8B–8D).
WHY THIS IS THE SMALLEST FIT: the legacy snapshot scripts have no schema,
  identity, cursor, or atomicity; a local typed contract + store is the minimal
  durable boundary 8B/8C can build on without importing a framework.
COMPATIBILITY DRIFT: n/a.
IMPLEMENTATION STATUS (Packet 8A, 2026-08-27): contracts + store implemented
  and tested as voicebridge/communications/*; fixtures and contract tests added;
  inventory recorded in docs/PACKET_8A_INVENTORY.md. No runtime behavior,
  service, timer, or live state changed.
```

## R17 — Permission profiles, scoped grants, skill manifests (Packet 7E local design)

```text
LOCAL REQUIREMENT: restricted/unrestricted permission profiles, scoped grants
with expiry/revoke/reset, versioned skill-manifest validation (license/hash/
tier/sensitivity/approval/timeout/resource/rollback), CLI + dashboard parity,
hard-deny invariants, and the extension contract (plan Phase 7 tasks 20–26).
UPSTREAM REPOSITORY + COMMIT: none — the contract and store are LOCAL design.
It reuses the ALREADY-recorded local capability/policy core as its foundation:
  voicebridge/capabilities.py (Packet 1A, local) — Tier/CapabilitySpec/
  CapabilityRegistry/ConfirmationStore/PolicyEngine.
  voicebridge/safety/policy.py (local) — hard-deny/destructive patterns.
  voicebridge/broker.py (Packet 3A, local) — CapabilityBroker.registry used to
  validate capability existence/tiers for `permissions check` / effective values.
UPSTREAM TESTS/FIXTURES: n/a (local; conformance proven by tests/test_packet7e.py
  + a synthetic demo skill manifest in the CLI smoke).
LICENSE: none required — no upstream code copied; only local modules referenced.
DECISION: WRAP (local capability/policy core) + LOCAL (permission/skill authorship).
LOCAL DESTINATION: voicebridge/permissions.py (+ CLI/webui/frontend wiring,
  tests/test_packet7e.py, docs/PERMISSIONS_7E.md).
DEPENDENCIES ADDED: none (stdlib json/os/tempfile/hashlib/re/threading only).
BEHAVIOR KEPT: default-deny restricted mode; unrestricted only pre-authorizes
  read-only/reversible work inside user-selected scopes; hard-deny patterns and
  mutating/destructive/financial work always need user confirmation; model/page/
  skill/tool text cannot change mode or widen grants; skills install staged
  disabled; atomic persisted state with backups and a bounded audit log.
BEHAVIOR INTENTIONALLY OMITTED: no live website/app/editor integration, no
  package hooks, no 8B adapters, no permission expansion on model/voice/provider
  change, no new database/server/framework.
WHY THIS IS THE SMALLEST FIT: the existing 1A/3A capability registry + policy
  already define tiers/confirmations; 7E adds only the mode-gated decision layer,
  a small durable grants/skills store, and CLI/dashboard surfaces.
COMPATIBILITY DRIFT: n/a.
IMPLEMENTATION STATUS (Packet 7E, 2026-08-27): implemented and tested as
  voicebridge/permissions.py with CLI (permissions/skills), dashboard
  /api/permissions + panel, worker-instruction replacement in orchestrator.py,
  config defaults, docs/PERMISSIONS_7E.md, and tests/test_packet7e.py. No live
  service, account, Chromium, timer, or user config changed.
```

## R18 — Read-only CDP site adapters + scan driver (Packet 8B)

```text
LOCAL REQUIREMENT: repository-owned typed read-only Gmail/Instagram/Messenger
adapters attaching to the user's already-logged-in real Chromium on loopback
CDP; stable identity, baseline/new-item cursor transactions, bounded scans,
overflow/selector/auth health, clipped evidence, fixture + opt-in canary tests
(plan Phase 8 tasks 4–9; Packet 8B prompt).
UPSTREAM REPOSITORY + COMMIT: none copied. The CDP wire contract is the
ALREADY-recorded R10 pin (Chrome DevTools Protocol @ 5ee78b32, BSD-3-Clause);
the packet WRAPS only the read-only slice (Runtime.evaluate +
Page.captureScreenshot) over the declared `websockets` dependency (already in
pyproject). Stable-identity/cursor/store contracts come from the LOCAL 8A
records (voicebridge/communications/contracts.py + store.py).
UPSTREAM TESTS/FIXTURES: n/a (protocol contract); local conformance proven by
tests/test_communications_monitor.py + synthetic fixtures in
tests/fixtures/communications/ (extraction rows, sanitized DOM/ARIA layouts).
LICENSE: R10 BSD-3-Clause already recorded; websockets is an existing declared
dependency (its own license applies). No new notice obligation for 8B.
DECISION: WRAP (R10 read-only CDP slice + 8A store) + LOCAL (adapter/extraction
authorship). The extraction JavaScript is repository-owned, versioned, static
(no page/model/message interpolation), and bounded.
LOCAL DESTINATION: voicebridge/communications/adapters/{base,sites,registry,
extract}.py, scripts/communications_monitor.py, tests/test_communications_monitor.py.
DEPENDENCIES ADDED: none new (stdlib + websockets already declared).
BEHAVIOR KEPT: strictly read-only (no click/type/send/react/mark-read/navigate/
download; no Target.activateTarget/Page.bringToFront; no Playwright account
profiles); baseline-first new-item detection with atomic cursor/report commit;
overflow/auth/selector-drift typed and never advance the cursor; redacted
canary output.
BEHAVIOR INTENTIONALLY OMITTED: any live scheduling/timer (8D), report bundles/
UI (8C), reply/send paths, tab mutation/focus/closure, $HOME/.hermes adapter
dependence (replaced by repository adapters).
WHY THIS IS THE SMALLEST FIT: the pinned CDP read-only slice + the 8A store are
exactly the durable boundary needed; no browser framework is introduced.
COMPATIBILITY DRIFT: R10 pin is frozen; per-site extraction selectors are
versioned and a selector-drift state is explicit (typed DEGRADED, never a
stale-cache success).
IMPLEMENTATION STATUS (Packet 8B, 2026-08-27): implemented and tested; live CDP
was UNAVAILABLE at implementation time (browser not running) so the live canary
is opt-in and records DEGRADED when the endpoint is absent.
```

## R19 — Professional report bundles + authenticated report API (Packet 8C)

```text
LOCAL REQUIREMENT: canonical JSON report manifests as single source of truth,
deterministic Markdown + self-contained responsive HTML, atomic private
bundles/evidence assets, authenticated index/detail/asset/export/ack routes,
safe links + hashes/MIME + explicit missing-evidence, legacy Markdown indexing,
and the static reports UI (plan Phase 8 tasks 10–14; Packet 8C prompt).
UPSTREAM REPOSITORY + COMMIT: none copied. Playwright (R13 pin) is used ONLY
for the local report-UI smoke; the canonical payload/render/store/API is LOCAL
design built on the LOCAL 8A contracts (voicebridge/communications/
contracts.py + store.py) and the 8B scan results.
UPSTREAM TESTS/FIXTURES: n/a (local); conformance proven by
tests/test_report_bundles.py + tests/test_webui_reports.py and the synthetic
fixtures under tests/fixtures/communications/ (legacy_report.md, dom/*).
LICENSE: none required — no upstream code copied; Playwright reuse already
recorded in R13 (Apache-2.0) and used only as a dev test tool.
DECISION: WRAP (8A/8B local contracts) + LOCAL (bundle/render/store/API authorship).
LOCAL DESTINATION: voicebridge/communications/reports.py, webui report routes,
  frontend/index.html reports panel, tests/test_report_bundles.py,
  tests/test_webui_reports.py.
DEPENDENCIES ADDED: none new (stdlib html/json/os/zipfile + existing auth).
BEHAVIOR KEPT: canonical JSON source of truth; deterministic MD/HTML; atomic
  staging + fsync + validate + rename; 0700 dirs / 0600 files; authenticated
  header-token routes; traversal/symlink/XSS/MIME/hash/redaction protections;
  legacy .md indexed read-only (never deleted); retention prunes only expired
  bundles; rollback = re-render from the canonical store.
BEHAVIOR INTENTIONALLY OMITTED: hourly monitoring/notifications (8D), reply/send
  or account mutation, Chromium/Playwright personal profiles, activity-log
  reports presented as bundles, raw message bodies, secrets/tokens/paths.
WHY THIS IS THE SMALLEST FIT: the 8A manifest + 8B evidence contracts already
  define the data; 8C adds only rendering + a private atomic store + API/UI.
COMPATIBILITY DRIFT: n/a (no upstream source).
IMPLEMENTATION STATUS (Packet 8C, 2026-08-28): implemented and tested;
  retention is FAIL-CLOSED (protected backup + index integrity check before any
  deletion; never ignore_errors; symlink/traversal guards; retention.log audit).
  Live bundle bytes from real scans depend on a future 8B/8D byte-persistence
  hook — committed assets render with explicit typed missing-evidence reasons
  today; a subprocess smoke test proves a fresh process serves the canonical
  route (operator restart command documented in docs/REPORTS_8C.md).
```

## R20 — Website-integration onboarding + observability (Packet 8E local design)

```text
LOCAL REQUIREMENT: non-live website-integration onboarding façade — pasted URL
→ PENDING/disabled proposal; bounded read-only discovery with typed lifecycle
states; versioned integration records (origin/display/manifest-link/requested
caps/scopes/sensitivity/limits/health/timestamps/audit/rollback); CLI `websites
add/list/show/inspect/check/logs/enable/disable/grant/revoke/remove/rollback`;
authenticated dashboard parity; Restricted-by-default; page/model/tool text can
never widen a grant (plan Phase 8 Packet 8E; Packet 8E prompt).
UPSTREAM REPOSITORY + COMMIT: none copied. The packet WRAPs ALREADY-recorded
LOCAL contracts:
  R17 PermissionStore/SkillManifest (voicebridge/permissions.py) — scoped,
      expiring grants + manifest linkage.
  R1A/3A CapabilityRegistry/Tier (voicebridge/capabilities.py) — read-only
      proposal capabilities registered via websites_registry() with origin
      scopes.
  Packet 8A typed-state conventions (communications/contracts.py) — reason
      codes/evidence labels mirrored into the website lifecycle states.
No new upstream source or test is reused; SSRF/private-network blocking uses
stdlib ipaddress/socket against the public RFC-1918/loopback/link-local ranges.
UPSTREAM TESTS/FIXTURES: n/a (local); conformance proven by
tests/test_packet8e.py (all synthetic, hermetic; the network probe is injected,
never real).
LICENSE: none required — no upstream code copied; local modules only.
DECISION: WRAP (R17 grants/manifest + local capability registry) + LOCAL
(record store, discovery, CLI/dashboard authorship).
LOCAL DESTINATION: voicebridge/websites.py, voicebridge/cli.py (`websites`
subcommand), voicebridge/webui.py (/api/websites), frontend/settings.html
(Websites beginner + advanced), tests/test_packet8e.py,
docs/WEBSITES_8E.md.
DEPENDENCIES ADDED: none (stdlib urllib/ipaddress/socket/json/os/tempfile/
threading/dataclasses only).
BEHAVIOR KEPT: adding a URL persists only a PENDING/disabled proposal (never
grants, trains, executes page text, imports credentials, clones a profile, or
creates a hidden skill); a website stays UNLINKED until an explicit, confirmed
`manifest-link` operation binds a valid, installed, ACTIVE 7E skill with a
recognized SPDX license expression and a content_hash that matches the
canonical manifest-payload digest (SKILL_MISSING/MALFORMED/NOT_ACTIVE/
HASH_MISMATCH/INCOMPATIBLE and LICENSE_INVALID/UNKNOWN fail closed); strict
binding requires every proposal capability declared by the manifest and every
proposal scope authorized by it (exact `web:<origin>` or explicit `web`), with
tier/sensitivity/approval compatible with read-only observation
(BINDING_CAPABILITY/SCOPE/TIER/SENSITIVITY/APPROVAL_MISMATCH) — app-control/
terminal/editor/mail/destructive-only manifests cannot authorize observation;
`enable` and `grant` share one fail-closed gate that re-runs all checks at use
time, and `grant` verifies the gate BEFORE any PermissionsStore.grant call;
discovery is bounded (timeout/bytes/redirects/SSRF) and returns redacted
metadata + a fixed READ_ONLY proposal for static pages only; grants are scoped
to the origin, expiring (default 7 days), confirmed, and limited to validated
proposal capabilities; restricted is the default and unrestricted cannot widen
grants or bypass hard-deny; every transition writes a redacted audit event;
state is an atomic private JSON file with timestamped backups; rollback/remove
never auto-restore permissions or reactivate/deactivate a skill; a bounded
`websites probe --live` command (explicit opt-in, redacted, typed, persists
nothing) and an operator-authorized `inspect --fixture` synthetic READY smoke
path (probe_source=fixture recorded) exercise the same backend; an OPTIONAL
local artifact-byte SHA-256 is verified at link time only when an artifact is
supplied (no external-package claim otherwise); CLI output is JSON or concise
human (`--human`).
BEHAVIOR INTENTIONALLY OMITTED: no live site adapter, no 8C report UI, no 8D
scheduling, no send/post/purchase/delete or account mutation, no Playwright
personal profiles, no page JavaScript, no cookie import, no second router/
browser runtime/scheduler/permission store/database/model/framework.
WHY THIS IS THE SMALLEST FIT: the 7E permission store already owns grants/
mode/skills; 8E adds only a proposal record store + a bounded read-only probe +
thin CLI/dashboard surfaces, with no new framework or executor.
COMPATIBILITY DRIFT: n/a (no upstream source).
IMPLEMENTATION STATUS (Packet 8E, 2026-08-29): implemented and tested as
voicebridge/websites.py + CLI/dashboard wiring + tests/test_packet8e.py
(104 tests). Repair passes added the explicit website→7E manifest-link
operation, strict binding (every proposal capability/scope must be declared/
authorized by the linked manifest; tier/sensitivity/approval gates;
app-control/editor/terminal/mail manifests cannot authorize observation), SPDX
license validation, real artifact-hash verification (canonical manifest-payload
digest, well-shaped-but-wrong hashes fail), a bounded HTTPS-only
`websites probe --live` command (probe-result vocabulary SUCCESS/UNAVAILABLE/
DEGRADED/PARTIAL/AUTH_REQUIRED/FAILED; content-type/origin/timeout/size/
redirect/fragment limits), an operator `inspect --fixture` READY smoke path,
optional local artifact-byte hashing RE-verified at every enable/grant gate
(post-link deletion/movement → MISSING, modification → TAMPERED, unreadable →
UNREADABLE; local path never serialized), `--human` output, full dashboard
lifecycle tests, and no-Playwright/no-Chromium-mutation proof. Final repair
pass added the dashboard serving/authentication integration: the repository
frontend carries a VERSION marker and a configured external webui.frontend_dir
is honored only when current (stale/unversioned external checkouts safely fall
back to the repository frontend and report the mismatch); every page and
/static/* resolves through one repo-fallback helper; a single shared
authenticated fetch helper (frontend/vb.js) sends X-Api-Token from browser
storage only (query-string tokens never read/placed in URLs) and shows a
"Pair this dashboard" hint on 401; backend auth stays fail-closed. Also fixed
two pre-existing frontend defects (index.html missing </script>; activity.html
escaped </script>). No live service, timer, Chromium, account, model, audio,
or user config changed; no live site adapter exists yet.
```

---

## R21 — Timer/notification/quiet-hours qualification (Packet 8I)

```text
LOCAL REQUIREMENT: qualify the existing user-systemd hourly oneshot/timer +
notification path and close four configuration risks: quiet-hours/IANA-timezone
validation (fail-closed `CONFIG_INVALID`), no hardcoded `DISPLAY=:1`, a
monotonic/renewable/reboot-aware overlap lease that exceeds the oneshot runtime
plus a safety margin, and truthful aggregate state/exit code. Reuses the
already-pinned systemd timer semantics (R11) and the Packet 8C/8D stores.
UPSTREAM REPOSITORY + COMMIT: systemd v255 @ db11bab38ccf1ed257f310d29070843d4c58ea01
  — the R11 record already pins `OnCalendar=`, `Persistent=` catch-up and
  `AccuracySec=` semantics; Packet 8I adds no new systemd directive.
SOURCE FILES + SYMBOLS: R11 man/systemd.timer.xml facts (reused as-is);
  stdlib `zoneinfo`/`time.monotonic`/`threading` for IANA validation and
  monotonic deadlines (Python 3.11+, no third-party dependency).
UPSTREAM TESTS/FIXTURES: n/a — qualification harness is local-design; the
  pinned R11 facts are covered by the timer template assertions
  (`OnCalendar=hourly`, `Persistent=true`) in the 8I harness.
LICENSE: LGPL-2.1-or-later (systemd man page facts — attribution only, no
  systemd source copied; unchanged from R11). zoneinfo is CPython stdlib
  (PSF license).
DECISION: WRAP (R11 timer semantics) + LOCAL (monotonic lease with boot_id,
  strict quiet-hours/IANA validation, validated X11/Wayland session
  resolution, bounded notification retry/dead-letter, truthful aggregate).
LOCAL DESTINATION: systemd/openclaw-communications-monitor.{service,timer}
  (DISPLAY hardcode removed; accelerated failure-path templates added as
  {openclaw-communications-monitor-accel.{service,timer}), voicebridge/
  communications/{monitor,store,contracts}.py, voicebridge/display.py,
  voicebridge/communications/timer_qualify.py, scripts/timer_qualify.py,
  tests/test_timer_qualify.py, docs/OPERATIONS_8I.md.
DEPENDENCIES ADDED: none.
BEHAVIOR KEPT: hourly `OnCalendar=hourly` + `Persistent=true` catch-up-once;
  app lease as the second overlap guard; bounded resources; notify-send
  plain-language redacted delivery; 8C/8D stores unchanged in purpose.
BEHAVIOR INTENTIONALLY OMITTED: no new scheduler/notification database;
  no timer enablement or live-unit edits by this packet; accelerated unit is
  temporary and isolated and never substitutes for the hourly schedule;
  no `WakeSystem`; no monotonic `On*Sec` timer variants.
WHY THIS IS THE SMALLEST FIT: the timer semantics were already pinned (R11);
  8I adds only validated local contracts (stdlib zoneinfo + monotonic +
  boot_id lease, session resolution) and an operator-gated qualification
  harness that composes the existing 8D notification path.
COMPATIBILITY DRIFT: same as R11 (v255 directives stable).
```

---

## 15. License notice obligations (deferred until first COPY/ADAPT)

`THIRD_PARTY_NOTICES.md` must be created in the same packet as the first substantial copy. Expected
entries based on the decisions above:

| Upstream | License | Notice text anchor |
|---|---|---|
| AG-UI (R1) | MIT | Copyright (c) 2025 |
| CopilotKit (R2) | MIT | Copyright (c) Atai Barkai |
| OpenSandbox (R3) | Apache-2.0 | Copyright 2026 Alibaba Group Holding Ltd. |
| Semantic Router (R6) | MIT | Copyright (c) 2024 Aurelio AI |
| Qwen / unsloth / nomic models (R7) | Apache-2.0 | per model card LICENSE at pinned rev |
| Mem0 (R8) | Apache-2.0 | Copyright [2023] [Taranjeet Singh] |
| devtools-protocol (R10) | BSD-3-Clause | Copyright 2014 The Chromium Authors |
| Himalaya (R14) | MIT | Copyright (c) 2022-2026 soywod |

Not requiring notice files: Ollama (WRAP over HTTP, no code), AirLLM (REJECT), Letta (REJECT/no
code at pin), systemd man page (authoritative reference; no systemd code shipped), Gmail API docs
(proprietary contract citation), Playwright (declared dependency; its own distribution carries
notice).

## 16. Verification status of this baseline

- All 14 records above: source/license facts **VERIFIED at pinned commits** on 2026-08-24 via
  direct fetches (GET only). One exception: R9 Letta source file absent at pin → REJECT/re-pin
  recorded.
- Nothing is IMPLEMENTED in production from this table yet; conformance-test obligations (plan
  §4.1 items 1–5) attach to the packets that perform each COPY/ADAPT.
- Current-main comparisons were compatibility checks only; no pin was changed.

## R15 — Packet 8F action layer (WRAP of existing verified local contracts)

```text
LOCAL REQUIREMENT: typed, brokered, confirmation-gated browser/desktop/editor
  actions (8F) with preview/hash binding, cancellation, idempotency, receipts,
  recovery honesty, and CLI/dashboard/voice parity — without a second broker,
  permission store, router, scheduler, database, browser runtime, or model.
UPSTREAM REPOSITORY + COMMIT: none copied/adapted. Reused local verified units:
  CapabilityBroker (Packet 3A), ConfirmationStore/PolicyEngine (Packet 1A),
  PermissionsStore/grants/mode (Packet 7E), websites.normalize_origin /
  integration gate (Packet 8E), loopback-CDP read boundary (Packet 8B), and
  the CopilotKit-derived human-in-the-loop approval pattern already adapted in
  Packets 1B/2B (R2).
SOURCE FILES + SYMBOLS: voicebridge/broker.py (CapabilityBroker.execute,
  issue_confirmation, cancel, ExecResult), voicebridge/capabilities.py
  (ConfirmationStore.issue/consume, PolicyEngine, Tier), voicebridge/
  permissions.py (PermissionsStore grants/mode/is_hard_deny_auto),
  voicebridge/websites.py (normalize_origin resolve=False), voicebridge/
  communications/adapters/base.py (loopback CDP boundary), voicebridge/
  orchestrator.py (_handle_confirm, ConfirmationState).
UPSTREAM TESTS/FIXTURES: local conformance vectors ported from the verified
  3A/7E/8E suites; synthetic adapter fixtures (tests/fixtures/actions/) that
  control ONLY the simulated outcome while the contract/gate/hash/receipt/
  audit stay real.
LICENSE: N/A (no upstream code copied; all reused units already carry their
  recorded provenance R1–R14).
DECISION: WRAP — the 8F layer composes verified local contracts into a new
  action surface; no upstream source was imported.
LOCAL DESTINATION: voicebridge/actions.py (+ CLI actions subcommand, webui
  /api/actions, settings.html Actions section, orchestrator voice hook,
  tests/test_packet8f.py).
DEPENDENCIES ADDED: none.
BEHAVIOR KEPT: broker decision+confirmation+audit+bounded child+cancellation;
  7E restricted/unrestricted + grants + scopes; hard-deny never auto-approved;
  loopback-only CDP boundary; single confirmation consume point; model/skill/
  page text never confirms.
BEHAVIOR INTENTIONALLY OMITTED: no live action adapters (ADAPTER_DISABLED
  until a separate approved rollout step); no free-text voice parsing beyond
  three exact patterns; no changes to the 7E PermissionPolicy hard-deny
  branch (the 8F ActionGate layers confirmation-gated hard-deny semantics on
  top, documented divergence per plan §8F).
WHY THIS IS THE SMALLEST FIT: every required primitive already existed and was
  independently verified; adding a parallel policy/executor would duplicate
  verified owners. The only new broker behavior is deny-first confirmation for
  action capabilities (a ~10-line override on the same class), closing the
  direct-broker bypass for send/purchase/delete/account/system actions.
```

---

## R22 — End-to-end recovery rehearsal (Packet 8J)

```text
LOCAL REQUIREMENT: an isolated staging/recovery rehearsal that exercises crash,
  restart, backup/restore, retention, deduplication, and degraded recovery
  around the already-verified browser, audio, timer, notification, report,
  cursor, and action contracts, plus the partial-rollback-visibility repair
  (a failed rollback step must be a typed failure with the exact bounded paths
  and must block resume — never swallowed or reported as success). Includes
  deterministic child-process SIGKILL at every file/store crash boundary, real
  fsync/os.replace/write failure injection, kernel-enforced RLIMIT_FSIZE
  file-size/quota (EFBIG, labeled distinct from ENOSPC) plus deterministic
  ENOSPC, corrupt-SQLite and newer-schema fail-closed, resource enforcement
  (CPU/AS/fsize/nofile/nproc/threads/wall) with actual measurements incl. a
  measured process count and a pre-workload network probe, measurable RPO/RTO
  via the real atomic-restore path, fail-closed summary persistence with
  atomic rename, a genuine stalled-child wall-timeout, and repeated-recovery
  dedupe / no-loss / no-leak / hermetic guarantees. Hermetic only: no live
  service, account, browser, timer, or production state touched.
UPSTREAM REPOSITORY + COMMIT: none copied/adapted. Reused verified local units:
  CommunicationsStore atomic commit/rollback_cursor/lease/retention (Packet 8A/
  8D), ReportStore atomic publish + integrity (Packet 8C), portable export/
  read/plan_restore (Packet 7C), ActionStore receipt crash recovery (Packet 8F),
  monitor run_hourly/notification retry (Packet 8D/8I), and the operator-gated
  qualification harness pattern of Packet 8I (timer_qualify) / 8G (browser_qualify).
SOURCE FILES + SYMBOLS: voicebridge/communications/recovery_qualify.py
  (qualify_* stages incl. hermetic, run_rehearsal, deny_live, isolate_cfg,
  _guard_newer_schema, _persist_summary + _persist_summary_boundaries),
  voicebridge/communications/recovery_crash.py (child-process worker:
  sigkill_file/sigkill_store/fsync_fail/rename_fail/enospc/enospc_real/
  resource(store|busy|alloc|manyfiles|manyprocs|manythreads)/hang modes,
  _probe_network_blocked, _process_count),
  scripts/recovery_qualify.py, tests/test_recovery_qualify.py; repair:
  voicebridge/portable.py restore_state (PartialRollbackError.failed_paths).
UPSTREAM TESTS/FIXTURES: synthetic communications fixtures reused as-is
  (tests/fixtures/communications/gmail_items.json, qualify_external_item.json);
  failure injection is local (child subprocess + sqlite3 execute hooks + os-layer
  fault injection + RLIMIT_* + network socket probe) and the PartialRollbackError
  contract is tested directly.
LICENSE: N/A (no upstream code copied; all reused units already carry their
  recorded provenance R1–R21).
DECISION: WRAP (compose verified recovery surfaces into an isolated rehearsal)
  + LOCAL (PartialRollbackError repair in portable.restore_state; child-process
  crash/fault worker).
LOCAL DESTINATION: voicebridge/communications/recovery_qualify.py,
  voicebridge/communications/recovery_crash.py, scripts/recovery_qualify.py,
  tests/test_recovery_qualify.py, docs/OPERATIONS_8J.md, voicebridge/portable.py
  (restore_state rollback loop).
DEPENDENCIES ADDED: none.
BEHAVIOR KEPT: atomic cursor/report commit, rollback_cursor from history, lease
  monotonic/renewable/stale-recovery, report bundle atomic publish + integrity,
  portable export/read/dry-run/restore, receipt running->interrupted recovery,
  bounded notification retry/dead-letter, read-only store typed failure.
BEHAVIOR INTENTIONALLY OMITTED: no live stages (--live fails closed); no
  destructive account actions, real messages, or fake remote rollback; no
  parallel persistence; production state is never a fixture; no 8K packaging.
WHY THIS IS THE SMALLEST FIT: the recovery primitives were already verified in
  Packets 7C/8A–8I; 8J adds only a bounded isolated rehearsal plus the one
  explicit repair (partial-rollback visibility) the plan assigns to 8J.
COMPATIBILITY DRIFT: none — restore_state now raises PartialRollbackError (a
  PortableError subclass) instead of silently swallowing rollback OSErrors;
  all existing callers already treat PortableError as a typed failure.
```

---
