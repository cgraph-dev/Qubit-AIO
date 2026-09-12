# Unified Local Agent Plan

Status: planning baseline
Date: 2026-08-24
Scope: local voice bridge, PC control, browser access, scheduled communications observation, professional reporting, knowledge access, OpenClaw/Hermes execution, safety, speed, and low-resource operation.

This is the implementation contract for future agents. Complete one lettered packet at a time, verify it independently, and then continue. Do not implement a whole parent phase or the whole plan in one change.

## 0A. Qubit foundation migration precedence

When Packet 8T-F is active, the Hermes-derived Qubit foundation contract in
`docs/QUBIT_UNIFIED_AGENT_PLAN.md`, `docs/AGENT_PROMPT_PACK.md`, and
`docs/OPERATIONS_8T_F.md` takes precedence over older wording in this local
Voice Bridge plan that describes OpenClaw or Hermes as merely external worker
adapters. 8T-F is not complete when a Qubit-owned worker only imitates the
Hermes protocol or when Hermes remains only in a temporary external install.
The resulting Qubit tree must prove the pinned Hermes implementation and the
required OpenClaw REUSE/ADAPT rows are actually integrated. This note does not
change the scope of packets before 8T-F.

For the current full-parity execution, the owner-directed architecture is
defined by `docs/QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md`; its gap ledger and
batched test/storage cadence take precedence over older packet-era execution
habits. Do not run expensive full suites after every small edit, but do run all
required final gates before verification.

## 1. Non-negotiable goals

The agent must:

- respond quickly on the local PC;
- use the PC directly when the user authorizes it;
- use browser sessions, files, repositories, terminal, applications, and external agents through one controlled tool boundary;
- show progress and preserve state across voice, dashboard, and future channels;
- keep secrets private and require confirmation for irreversible or financial actions;
- consume predictable CPU, RAM, VRAM, disk, and network resources;
- fail closed, report the exact failure, and leave a recoverable state;
- be distributable as a verified local application while remaining PC-native:
  portable code and user-approved state may move to another trusted device, but
  secrets, browser sessions, and hardware assumptions never move silently;
- check configured Gmail, Instagram, and Messenger accounts hourly as a strictly read-only local capability, detect genuinely new items without repeated alerts, produce a private evidence-backed report only when needed, and notify the user locally;
- replace the activity-feed-shaped “reports” surface with professional, atomic report bundles containing canonical structured data, readable HTML/Markdown, safe source links, scoped screenshots or an explicit evidence-unavailable reason, health/provenance, and authenticated asset access.

Do not adopt complete CopilotKit, OpenSandbox, or AirLLM frameworks. Work source-first: inspect the exact upstream implementation and its tests, then copy or adapt the smallest license-compatible unit that fits this Python bridge. Do not redesign a proven contract from memory, and do not vendor an entire framework to reuse one behavior.

## 2. Current truth: what is already implemented

Implemented and covered by the last focused test run:

- `qubit-brain4` configured as the default local model;
- structured classifier tool schema;
- feedback ledger, repeated-good auto-preferences, and inline corrections;
- delta activity polling and ETag support;
- streamed local-model sentences into TTS;
- bounded local tool execution with timeout and process-group cleanup;
- task queue, shared memory, reports, browser scripts, and local whitelisted actions.

`docs/STEAL_LIST.md` is therefore an implementation inventory, not a completed acceptance record. Several listed ideas exist in partial form, but the live speed gate failed, the current event/progress surface is not the canonical protocol, the execution runner is not isolation, model residency is unstable across caller options, and personal memory is still bulk-injected. Phase 0 must replace ambiguous `DONE` wording with `IMPLEMENTED`, `VERIFIED`, `DEGRADED`, or `PLANNED` plus evidence.

The last focused steal-list suite passed. The live AirLLM-inspired test did not fully pass: the warm `qubit-brain4` median was approximately 1.27 seconds against the documented 0.70-second target. Residency also changed during the 2026-08-24 audit depending on which request path ran last. Configuration, residency, runner options, and measured performance are different facts and must be reported separately. Until the Phase 4 gates pass, do not describe the model as meeting the speed target or as permanently resident.

A read-only diagnostic benchmark on 2026-08-24 found:

- six warm generative classifier requests took 644–793 ms wall time and emitted 40–53 tokens each;
- the classifier used `num_ctx=4096`, while local chat used `num_ctx=8192`;
- changing from one context size to the other reloaded the same Ollama model; one measured classifier transition spent 3.246 seconds loading and 3.933 seconds wall-clock overall;
- a warm local chat after the runner was loaded at the matching context produced first content in 164–179 ms and completed in 364–481 ms for the two measured non-load samples;
- these are diagnostic samples, not an acceptance benchmark. Packet 4A must reproduce them with a fixed corpus, repeated runs, percentiles, and captured hardware/configuration.

A read-only inbox/report audit on 2026-08-24 found:

- `review_all_inboxes.py` only runs four snapshot scripts and concatenates their text; it has no scheduler, durable cursor, transactional new-item detection, report creation, or notification contract;
- Gmail prefers Himalaya and falls back to a user-home Hermes CDP script, while Instagram and Messenger depend directly on user-home Hermes scripts. Those external paths are not reproducible project dependencies;
- the adapters cache for five minutes, fall back to cache entries up to seven days old, swallow most errors, and exit successfully even when an account is unconfigured. A stale or failed scan can therefore look successful;
- current selectors are broad implementation-class/CSS queries. Gmail browser rows are marked unread unconditionally, Messenger browser rows are marked read unconditionally, Instagram loses source links during normalization, and none of the paths provides a complete stable identity contract;
- Playwright 1.62.0 is installed and directly declared, and Himalaya 2.1.0 is installed, but the live Himalaya shape probe returned an error object. Installed, configured, authenticated, healthy, and verified must remain separate states;
- no inbox/report user-systemd timer exists. The in-process local timer is a one-shot reminder and is not a durable hourly scheduler;
- the live probe could not connect to `127.0.0.1:9222`, so the real Chromium CDP precondition was unavailable at audit time. Saved sessions on disk do not make monitoring active unless Chromium is running with a loopback debugging endpoint;
- the state report directory contained 410 Markdown files and zero HTML, JSON, or image assets. `/api/reports` does not index those files; it merges activity/shared/log records and calls them reports;
- persisted reports were mode `0664`, and inbox cache/browser-profile directories were mode `0775`. Private communications and evidence require `0700` directories and `0600` files plus authenticated HTTP access.

### 2.1 Verified deployment stack and constraints

The implementation plan must run on this existing stack unless a phase explicitly proves and approves a change:

- Ubuntu/Linux user session, Python 3.12.3 virtual environment; `pyproject.toml` supports Python 3.11+;
- the bridge runs as `python -m voicebridge run` in a user-level systemd service;
- the installed service currently has `MemoryHigh=1.5G`, `MemoryMax=2G`, `Restart=always`, and machine-specific graphical-session variables, while the repository service template does not match it;
- the web UI is a Python `ThreadingHTTPServer` serving static HTML/JavaScript; no React, FastAPI, Uvicorn, Node server, or separate application server is required;
- the current dashboard lookup contains an absolute `/home/trick/.../voice-dashboard/frontend` path and must become configured or repository-relative;
- OpenClaw uses the existing WebSocket gateway, Hermes uses the existing backend, and browser automation uses Playwright/Chromium; preserve these boundaries rather than adding another agent runtime;
- Playwright 1.62.0 remains the declared library for isolated browser tasks and local report-UI tests, but it must not launch or own the monitored Gmail/Instagram/Messenger sessions. Hourly inbox observation attaches through loopback Chrome DevTools Protocol to the user's already running, logged-in real Chromium profile. It must reuse existing tabs or open a background tab only when allowed, never close user tabs, steal focus, clear cookies, or start a competing profile;
- user-systemd 255 is the durable local scheduler for hourly activation. Do not add cron wrappers, Celery, APScheduler, a second browser daemon, or another application server;
- Himalaya 2.1.0 may remain an explicitly enabled fallback Gmail adapter, but it is not the default for this user's monitor. Any fallback must health-check its actual JSON contract and preserve stable envelope/message identity. Mere binary presence is not a pass;
- Ollama 0.32.14 is the local model server; model residency must be observed with `ollama ps`, not inferred from `keep_alive` configuration;
- the machine has a Ryzen 7 5700X, 62 GB RAM, and an RTX 3070 Ti with 8 GB VRAM. At audit time, `qubit-brain4` occupied about 3.2 GB at 100% GPU offload; an 8B model must not be co-resident;
- `nomic-embed-text` is already installed in Ollama, but it is not approved as a hot router until Packet 4C proves CPU/GPU placement, warm latency, RAM/VRAM cost, and that it does not evict or reload the generative runner;
- the bundled Python SQLite is 3.45.1 with FTS5 enabled, so personal recall and lexical search can use the standard library instead of a new vector database;
- Bubblewrap 0.9.0 is installed and its user-namespace smoke test passes; user-systemd transient units accept `MemoryMax` and `TasksMax`, so the first isolated-run implementation should use Bubblewrap plus systemd cgroup limits instead of Docker, Kubernetes, or a VM;
- packages visible in the virtual environment but absent from `pyproject.toml` are transitive or incidental. Production code must not rely on them until they are declared and justified. This currently includes Pydantic, FastAPI, Uvicorn, and psutil;
- the default implementation language remains Python plus small static JavaScript. A TypeScript/React/Go rewrite is out of scope.
- a downloadable build must preserve the host integration boundary: Ubuntu/Linux
  is the first supported target; Windows/macOS require explicit platform adapters
  and separate acceptance evidence. A single universal binary is not assumed.
- local model artifacts are separate, verified payloads rather than opaque files
  silently embedded in the application. The package records model revision,
  checksum, license, disk cost, hardware profile, and rollback alias.

## 3. Known configuration and architecture problems

These are the first fixes, before adding capabilities.

### P0-A — repository configuration is stale and contradictory

- `config/voice.yaml` still describes the Scarlett and four microphone channels.
- `config/voice.local.yaml` still uses four channels, gain 8.0, and the old 3B model.
- `config/voice.example.yaml` still documents the old 3B model and outdated output-device guidance.
- `docs/AUDIO_SETUP.md` and the resolved user config describe the actual HyperX mono microphone and empty output-device pins.
- `README.md` contains older dashboard/audio statements that disagree with the resolved config.

The live resolved user config is currently the authoritative working setup. Repository examples must be made machine-agnostic and must not claim to be the live HyperX setup.

### P0-B — environment overrides cannot address keys containing underscores

`VOICE_BRIDGE_INTENT_LOCAL_LLM_MODEL` is parsed as `intent.local.llm.model`, not `intent.local_llm.model`. This silently creates the wrong config branch. Replace the convention with an unambiguous path encoding, such as double underscores for nesting, and add a test for `intent.local_llm.model`.

### P0-C — dashboard security defaults are unsafe for LAN exposure

The dashboard binds to `0.0.0.0`; an empty token makes APIs open; CORS is `*`; and query-string tokens can leak through browser history, logs, and referrers. The safe default is loopback plus an explicit opt-in for LAN access. LAN mode must require a token sent in a header, and CORS must allow only configured origins.

### P0-D — worker instructions conflict with the safety boundary

The task prompt currently tells workers to use the full terminal, install dependencies, control the system, and “never ask the user for permission.” That bypasses the intended confirmation policy. Workers must request capabilities through the broker, and the broker—not the worker prompt—must decide whether confirmation or isolation is required.

### P0-E — logging and browser output need redaction

The structured journal accepts arbitrary fields and tools can send raw browser/tool output to the dashboard. Add centralized redaction before journaling, before prompt injection, and before report persistence. Never record API keys, cookies, authorization headers, `.env` contents, payment data, full URLs containing secrets, or raw form fields.

### P0-F — the current tool runner is bounded execution, not isolation

`voicebridge.sandbox.run_tool()` provides timeout, bounded output, and process-group cleanup. It does not restrict filesystem, network, CPU, memory, credentials, or privileges. Treat it as the trusted-tool runner only. Add an isolated runner for generated or untrusted code.

### P0-G — live progress is not yet a real event protocol

The gateway event callback is minimal and the dashboard relies mainly on polling. Implement a typed event stream before adding more UI or channels.

### P0-H — live-test and watchdog boundaries are mixed

Unit tests, live model tests, dashboard probes, and a long-running watchdog are in the same project surface. A watchdog may restart the bridge. Tests must never restart services by default. Separate unit, integration, live-canary, and monitoring commands.

### P0-I — implementation sequencing and upstream contracts need explicit guardrails

- Phase 1 must install a temporary deny/confirm adapter around the existing OpenClaw/Hermes dispatch before removing any unrestricted worker path. The old path may be removed only after the adapter's negative tests pass; Phase 3 then replaces the adapter with the full capability broker.
- `config doctor` must have separate `--static` (CI-safe, no live dependencies) and `--runtime` modes. A runtime dependency that is unavailable must produce `DEGRADED` evidence, not a false pass.
- The dashboard frontend must send tokens in an `X-Api-Token`-style header; URL tokens are rejected and must not be copied into links, browser state, referrers, or logs.
- The event wire contract must use canonical AG-UI-compatible lifecycle, text, tool-call, state, activity, and raw/special-event semantics, or a versioned adapter with round-trip tests. State deltas must preserve an ordered snapshot baseline and use RFC 6902 JSON Patch semantics.
- The isolated executor must fail closed when the required OS isolation primitive is unavailable. It must return a typed `ISOLATION_UNAVAILABLE` result and never silently run untrusted code with host privileges.

### P0-J — repository deployment files do not describe the installed stack

The installed user service and `systemd/openclaw-voice-bridge.service` disagree on restart policy, dependencies, resource limits, CUDA library path, and graphical-session variables. The dashboard also contains a machine-specific absolute frontend path. Phase 0 must define which settings are portable defaults, which are generated machine overrides, and how installation updates them without overwriting the working user service. The repository template must include safe resource guardrails, but hardware/session values must remain local overrides.

### P0-K — upstream reuse and license provenance are not enforceable yet

`STEAL_LIST.md` says code can be copied but does not require an exact commit, source path, copied test, license notice, or compatibility decision. It also conflates AirLLM's layer-streaming runtime with Ollama residency and contains performance claims that conflict with the live gate. Every upstream-derived change must create a reuse record and preserve required MIT or Apache-2.0 notices. “Inspired by” is not enough when a small tested upstream implementation can be adapted directly; copying an entire framework is not allowed when only a schema or state transition is needed.

### P0-L — dependency and process boundaries are easy to misread

FastAPI, Uvicorn, Pydantic, and psutil are installed today but are not direct project dependencies. An agent may incorrectly build on them and create a fresh-install failure. The Ollama process is outside the bridge's 2 GB cgroup, while ordinary child tools inherit the bridge cgroup unless launched in a separate transient unit. Phase 0 must add dependency-contract and deployment-contract checks; Phase 3 must launch isolated workloads in their own bounded systemd unit rather than consuming the bridge's memory budget.

### P0-M — the same Ollama model is being reloaded by conflicting hot-path options

The generative classifier explicitly requests `num_ctx=4096` and omits `keep_alive`; local chat requests `num_ctx=8192` and `keep_alive=-1`. Ollama's scheduler reloads a runner when material launch options differ, and the audit reproduced a multi-second reload when alternating these paths. A model cannot be called “resident” while its own callers continually request incompatible runner configurations. Phase 4 must establish one resolved model-runner contract—model digest, quantization, context, GPU placement, parallelism, and keep-alive—and use it for every request to the resident generative model.

### P0-N — prompt, language, and personal-memory sources are duplicated and contradictory

`Modelfile.qubit4` embeds a large mutable biography containing private identifiers, stale hardware/audio facts, old model/tool claims, and conflicting language rules. `voicebridge/local_llm.py` separately sends a smaller runtime system prompt, loads dialog examples, and injects slices of `personal-knowledge.md`, `MEMORY.md`, and a daily note into every chat turn. The source comments disagree about English-only versus English-and-Romanian behavior. Even where an API request replaces the Modelfile system value, the baked profile remains active for direct `ollama run`, is easy to export accidentally, and has no provenance or freshness controls.

Replace this with a context compiler: a tiny immutable persona and safety prompt, explicit current-session state, a bounded tool shortlist, and only query-relevant personal/evidence records. Keep secrets and direct identifiers out of model artifacts. Private volatile facts stay in local user-controlled storage with provenance, sensitivity, expiration, correction, export, and deletion.

### P0-O — one 4B generation path is doing too many jobs without separate quality gates

The same model performs ambiguous intent classification, conversation, summarization, and prospective planning. The classifier asks it to emit explanatory fields that cost generation time, while deterministic routing already handles many requests. “Abliterated” or “uncensored” is not a quality metric for routing, tool arguments, factuality, or personal-assistant behavior. Phase 4 must create a cascade with independent evaluations: deterministic state/rules first, an optional measured semantic router second, the resident 4B model only for unresolved ambiguity or bounded planning, and OpenClaw/Hermes/cloud specialists for work beyond the local model's proven envelope.

### P0-P — the current reports API is not a report store

`generate_report.py` extracts files, URLs, screenshots, and test lines with regexes, writes only Markdown, and lists screenshot paths as code text. `_persist_full_report()` writes raw Markdown separately. `/api/reports` reads neither report directory and exposes no report manifest, HTML renderer, screenshot route, integrity metadata, acknowledgement, or bundle download. It currently returns recent activity/log records, so a working HTTP 200 does not prove that a saved report is discoverable or complete. Phase 8 must establish one canonical report contract and migrate the dashboard to it without deleting old reports.

### P0-Q — inbox snapshots cannot prove “new” and hide degraded state

The current inbox scripts conflate recent, unread, unknown-read-state, cached, and new. They have no account-scoped cursor, idempotency key, baseline mode, scan lease, or commit boundary. Broad selectors can return page chrome as messages; errors often become empty output or stale cache with exit code zero. Hardcoded user-home adapter paths and permissive state permissions make deployment and privacy worse. Until Phase 8 passes, hourly monitoring and professional inbox reports are `PLANNED`, not implemented.

### P0-R — integrations need a real permission and extension contract

Adding a website, desktop application, editor, or marketplace skill by editing a
router prompt or adding a launcher name is unsafe and incomplete. The current
worker instructions are broader than the capability policy and can be read as
permission to use the whole shell or a personal browser profile. Future
integrations require a versioned manifest, declared capabilities, scopes,
side-effect tiers, data sensitivity, approval policy, provenance/license,
resource limits, audit events, and rollback behavior. A user-selectable
“unrestricted” mode may broaden routine pre-authorized scopes, but it must never
disable hard safety denials, schema validation, redaction, audit, isolation,
resource limits, loopback boundaries, or per-action confirmation for sending,
publishing, purchasing, deleting, credential/account changes, or system-wide
destructive operations.

## 4. Target architecture

```text
Voice / dashboard / future channels
                |
        ingress + identity
                |
       routing cascade
  L0 state + deterministic rules
  L1 optional CPU semantic match
  L2 resident 4B ambiguity/planner
                |
   policy + approval decisions
                |
       capability broker
   +------------+-------------+----------------+----------------+
   |            |             |                |                |
 host tools   personal      isolated        knowledge      OpenClaw/Hermes
 terminal     browser       local runner     adapters       worker adapters
 files/apps   sessions      generated code   repos/web      external execution
   |            |             |                |                |
   +------------+-------------+----------------+----------------+
                |
       typed event journal + SSE
                |
       TTS + UI + audit log
```

Routing and execution are separate decisions. A router may select a route group or propose a tool, but only the policy/capability broker may authorize and execute it. The resident model receives a bounded shortlist of relevant tool schemas; it never receives the unrestricted shell or the entire registry, and its natural-language output is never executable authority.

The routing cascade is:

1. `L0` handles pending confirmation/cancellation state, stop/mute/system commands, exact whitelisted local actions, strong coding/task indicators, and other high-confidence deterministic cases. Target: no model call and less than 10 ms p95 in-process.
2. `L1` is optional semantic matching over versioned route examples. It must run without consuming generative-model VRAM, use per-route thresholds plus a top-one/top-two margin, and return `None` when uncertain. Target: less than 75 ms warm p95 on this machine. If the existing `nomic-embed-text` runner cannot meet the placement and latency gates, omit L1 rather than adding a heavy embedding framework.
3. `L2` uses the one resident local 4B model only for unresolved ambiguity, short conversation, memory extraction candidates, or a bounded typed plan. It uses the same runner options on every path, a JSON Schema for structured decisions, a small output budget, and no hidden side effects.
4. `L3` delegates hard coding, broad research, long-context reasoning, or specialist work to OpenClaw/Hermes/cloud adapters through the same policy boundary. The local model routes and summarizes; it is not expected to replace stronger workers.

Personal context is compiled per turn instead of concatenating files:

```text
immutable persona + safety rules
          + active request/session state
          + relevant personal/core records
          + relevant recall/evidence records
          + bounded tool schemas
          = token-budgeted model context
```

Every context section has a source, trust level, sensitivity, timestamp, and token/character budget. Retrieved web pages, browser text, emails, reports, and old conversations are evidence, not instructions. A context-budget test must fail if required system/tool/output reserve cannot fit the selected Ollama context.

Scheduled personal observation is a separate read-only path, not an agent-generated task:

```text
user-systemd hourly timer
          |
single-run lease + account health
          |
real logged-in Chromium on 127.0.0.1 CDP
          |
Gmail / Instagram / Messenger read adapters
          |
normalized items + durable per-account cursor
          |
atomic private report bundle + cursor commit
          |
typed event -> dashboard + desktop notification + optional voice
```

The scan process never receives a send/reply tool. Browser page text is untrusted evidence. A no-change run records compact health only; it does not create or announce another full report. A partial or authentication-required run is visible as degraded and never advances the failed adapter's cursor.

OpenClaw and Hermes are external worker adapters, not children of the local Bubblewrap executor. They must pass through the same policy/approval boundary, but only generated or explicitly untrusted local code belongs in the isolated runner. Personal browser sessions remain host capabilities because isolation would discard the authenticated profile; their side effects are controlled by policy and confirmation instead.

Every action, regardless of whether it is local, browser-based, OpenClaw, or Hermes, must have:

- a stable tool name and version;
- a typed input schema;
- a typed result/error schema;
- declared side effects;
- a permission tier;
- timeout, output, CPU, memory, and network budgets;
- an audit event and correlation ID;
- a cancellation path;
- a rollback or recovery description where possible.

### 4.1 Upstream source reuse matrix

These commits were inspected during the 2026-08-24 plan audit. They are review snapshots, not floating dependencies. An implementation must record the exact commit it actually uses and recheck the license and tests at that commit.

| Need | Upstream source to inspect first | Local decision | Explicitly do not take |
|---|---|---|---|
| Event types and SSE encoding | AG-UI `821b8c227c6fe7b78de2c8259173dd7cbbdf181b` (MIT): `sdks/python/ag_ui/core/events.py`, `sdks/python/ag_ui/encoder/encoder.py`, `sdks/python/tests/test_encoder.py` | Copy/adapt the event enum, wire field names, camelCase serialization, `data: <json>\n\n` framing, and conformance tests into a small local module. Use standard-library structures unless a direct dependency is separately approved. | CopilotKit runtime, React packages, GraphQL runtime, or the complete AG-UI SDK merely to send SSE |
| Human approval behavior | CopilotKit `e9387e04835545c45744b791aee7c9c03520be31` (MIT): `packages/react-core/src/v2/hooks/use-human-in-the-loop.tsx` and `packages/react-core/src/v2/hooks/__tests__/use-human-in-the-loop.e2e.test.tsx` | Adapt its tested `InProgress → Executing → Complete` state gating, expose `respond` only while executing, settle once, clean abort listeners, and remove dead renderers. Add durable restart-safe approval IDs in the Python store as a documented local safety extension; CopilotKit's hook itself is in-memory. | React conversion, CopilotKit runtime, hosted services, or a second frontend state store |
| Isolation capability probe, path policy, and lifecycle | OpenSandbox `6b2023e9b7eb80a940d88e6ae05fcbc0eb0cf23f` (Apache-2.0): `specs/sandbox-lifecycle.yml`, `components/execd/pkg/isolation/probe.go`, `bwrap_linux.go`, `config.go`, and `components/execd/pkg/runtime/isolated_session_lifecycle_test.go` | Adapt the startup probe, symlink-resolved readable/writable allowlists, fail-closed result, lifecycle ownership, cleanup tests, and error semantics to Python. Use installed Bubblewrap plus a separate user-systemd transient unit for cgroup limits. The probe may read-only bind `/` only for its short `true` smoke test; real workloads receive a minimal filesystem view. | OpenSandbox server, execd daemon, Go SDK, Docker/Kubernetes control plane, overlay commit/diff, or network sidecars in the first local version |
| Large-model memory experiments | AirLLM `6ab3b9db9b2fb595e8a4d966f0e1ba600677b1fe` (Apache-2.0): `air_llm/airllm/airllm_base.py` and `air_llm/tests/test_compression.py` | Reuse benchmark dimensions and guards. Its source confirms compression currently disables prefetching and caps pinned prefetched-layer memory. Keep production voice inference on Ollama; evaluate AirLLM only in an isolated optional experiment. | Torch/Transformers/bitsandbytes in the bridge service, AirLLM imports on startup, or claims that AirLLM makes the resident Ollama model faster |
| Resident runner lifecycle and typed generation | Ollama `fb30760996871fa9460115c753afd2c60d4ab0f7` (MIT): `server/sched.go` (`getRunner`, `needsReload` call sites, runner expiration), `server/routes.go` (`scheduleRunner`), and official structured-output/tool-calling/API documentation | Preserve the existing Ollama boundary. Unify context and keep-alive across all resident-model calls; capture `load_duration`, prompt/eval counts and durations; use full JSON Schema plus temperature zero for router decisions; benchmark native tool calls versus schema output for the planner. | A second production model server, changing `num_ctx` per request, prompt-only JSON without validation, or assuming `keep_alive` proves residency |
| Fast semantic route selection | Semantic Router `a4576168d9589397a7e0c6ff77f5d05469a56e2e` (MIT): `semantic_router/routers/base.py` (`__call__`, `_pass_routes`, `fit`, `evaluate`, `threshold_random_search`) and router tests | Adapt route examples, per-route thresholds, score/margin evidence, an explicit no-match result, held-out evaluation, and threshold fitting into small native code. Use the existing Ollama embedding endpoint only if its CPU/resource benchmark passes; implement tiny cosine scoring with the standard library. | Installing Semantic Router, NumPy, Pydantic, a vector database, remote encoders, dynamic LLM routes, or returning a route when no threshold passes |
| Small local model/tool-use candidate | Qwen `Qwen3-4B-Instruct-2507` revision `cdbee75f17c01a7cc42f958dc650907174af0554` (Apache-2.0) and Qwen3 source `7a2f61ffc7a20d47efcd2bf97f6f2bf52729042e`; GGUF candidate `unsloth/Qwen3-4B-Instruct-2507-GGUF` revision `a06e946bb6b655725eafa393f4a9745d460374c9` | Treat the official non-thinking 4B instruct model as the preferred challenger to the current abliterated Q4 model. Benchmark Q4_K_M and Q5_K_M under identical contexts; validate the official Hermes-like tool template and Ollama parsing. Keep separate aliases and checksums until rollback is proven. | Declaring a winner from public benchmarks, overwriting the current alias during evaluation, downloading random “function calling” fine-tunes, or treating uncensoring as intelligence |
| Experimental newer 4B candidate | Qwen `Qwen3.5-4B` revision `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a` and `unsloth/Qwen3.5-4B-GGUF` revision `e87f176479d0855a907a41277aca2f8ee7a09523` (Apache-2.0) | Evaluate only after the Qwen3-2507 challenger. Use text-only/non-thinking behavior and no multimodal projector for voice routing. Record that advertised MTP/speculative acceleration is documented for SGLang/vLLM and is not automatically available in Ollama. | Adding SGLang/vLLM/Transformers to production for MTP, loading the 675 MB vision projector when vision is not requested, speaking thinking tokens, or assuming a newer model is faster |
| Personal memory lifecycle and context tiers | Mem0 `8d5b7865bd0521e75fe9ab1b260eeccc83f8a378` (Apache-2.0): `mem0/memory/main.py` add/search/update/delete/history behavior and related tests; Letta `4511fa0bc91f68fbab32b91f694617271ea9012b` (Apache-2.0): `letta/schemas/memory.py` memory blocks and archival/recall separation | Adapt scoped records, normalized-hash deduplication, provenance/history, expiration, correction/deletion, core-versus-recall tiers, and bounded rendering. Implement with the existing SQLite/FTS5 and project journal. Explicit memory commands write directly; inferred memories are staged and evaluated before promotion. | Installing Mem0, Letta, Qdrant, SQLAlchemy, a graph database, autonomous prompt rewriting, or placing secrets/raw history in the always-on prompt |
| Existing-session browser observation and screenshots | Chrome DevTools Protocol `5ee78b32ae561f7830853c089459200e6a3467e4` (BSD-3-Clause): stable 1.3/current official `Target`, `Runtime`, `DOM`, and `Page.captureScreenshot` contracts; inspect the installed Chromium `/json/version` and `/json/protocol` before implementation | Wrap the already running real Chromium CDP endpoint on `127.0.0.1`. Reuse exact logged-in Gmail/Instagram/Messenger targets, extract stable item/link fields, and capture a clipped relevant-message region with `Page.captureScreenshot`. Keep browser reads side-effect-free and version the site adapters/selectors. | Launching Playwright persistent profiles for these accounts, copying cookies, opening a remote CDP listener, calling `Target.activateTarget`/`Page.bringToFront`, closing user targets, or adding Selenium/Puppeteer |
| Hourly local activation | systemd v255 commit `db11bab38ccf1ed257f310d29070843d4c58ea01` (LGPL-2.1-or-later): `man/systemd.timer.xml` for `OnCalendar`, `Persistent`, `AccuracySec`, service activation, and overlap behavior; installed version 255 | Add one repository-templated user oneshot service plus matching timer after dry-run verification. Use an application lease/flock as a second overlap guard, bounded runtime/resources, explicit environment, and one-command disable/rollback. | An in-process forever thread, cron, APScheduler, Celery, a watchdog restart loop, or enabling/editing the live timer before Packet 8D authorization |
| Gmail incremental identity option | Google Gmail API `users.history.list` official contract: increasing `historyId`, pagination, message/thread IDs, typed 404 full-resync behavior, and read-only/metadata scopes | Keep real Chromium CDP as the required default. Use this contract only as an optional future adapter or as a design reference for cursor/full-resync semantics; adding OAuth/API credentials requires a separately authorized task. | Quietly replacing the user's browser session, requesting modify scope, storing OAuth secrets in Git/reports, or claiming that recent/unread rows are equivalent to mailbox history |
| Local report-UI rendering and visual tests | Playwright Python v1.62.0 commit `3b7c24c3e67dc84f7b0eddd0c5fd2ca685705021` (Apache-2.0): official locator/screenshot APIs and installed package | Use Playwright only against the local authenticated report UI for link/asset/print/visual regression tests. Prefer role-based locators and deterministic fixtures. | Using Playwright to launch the monitored social/email profiles or treating a screenshot as proof that cursor, dedupe, and report integrity passed |

For every copied or adapted unit:

1. Copy the smallest useful upstream test or fixture before writing local production code.
2. Record repository URL, commit, source path, symbols, license, local destination, and modifications in `docs/UPSTREAM_REUSE.md`.
3. Preserve copyright/license headers when copying substantial code and add required notices to `THIRD_PARTY_NOTICES.md`.
4. Add a local conformance test that compares the local wire output or lifecycle result with the pinned upstream fixture/contract.
5. If the upstream language or dependency model does not fit, copy the public contract and tested behavior—not a line-by-line translation of the framework internals.

## 5. Staged implementation plan

### Phase 0 — establish one source of truth

Tasks:

1. Reconcile repository examples, README, `VOICE_BRIDGE_SYSTEM.md`, `STEAL_LIST.md`, `AUDIO_SETUP.md`, `pyproject.toml`, the repository systemd template, and the installed user service.
2. Keep machine-specific values out of portable examples.
3. Add `voicebridge config doctor` or `scripts/config_doctor.py`.
4. Give the doctor two explicit modes: `--static` validates files/contracts without live services; `--runtime` validates the resolved config, installed service environment, model availability and actual residency, audio devices, gateway connectivity, browser CDP, Bubblewrap/systemd isolation capability, frontend path, and token policy.
5. Fix the environment override path syntax and document it.
6. Add a config-contract test that catches stale model, microphone, output, and `speak_web_locally` values.
7. Correct the steal-list latency wording to reflect the failed live gate.
8. Replace the hardcoded dashboard home-directory path with an explicit optional config path plus a repository-relative fallback. The doctor must report which frontend is active.
9. Reconcile the installed systemd guardrails with a portable repository template and document generated machine overrides; do not overwrite the installed service automatically.
10. Add a fresh-install dependency check that fails if production code imports an undeclared package. Do not treat packages present only through another dependency as part of the supported stack.
11. Create `docs/UPSTREAM_REUSE.md` with the reviewed source matrix and create `THIRD_PARTY_NOTICES.md` when the first substantial upstream code is copied.
12. Inventory every local-model caller and report its effective model alias/digest, `num_ctx`, keep-alive, temperature, output limit, API endpoint, and system/context source. Do not fix the runtime in Packet 0A; make the conflict impossible to miss in the mismatch report.
13. Choose and document one language policy. Reconcile code comments, retry behavior, prompt text, examples, and tests; do not silently change the user's spoken-language behavior during a documentation-only packet.
14. Classify every personal fact currently baked into a Modelfile or repository file as public, private, secret-reference, volatile, or stale. Record a migration/rollback plan; never copy the actual private values into the mismatch report.

Acceptance gate:

- `config doctor` prints the loaded path and every effective value that matters;
- static mode works without Ollama, audio hardware, gateway, browser, or systemd services running;
- runtime mode distinguishes configured, available, active, resident, passed, and degraded states rather than collapsing them into “working”;
- it fails non-zero for invalid audio/model/security combinations;
- it never prints secret values;
- the installed and repository service differences are either reconciled or explicitly generated/documented;
- no production import relies on an undeclared transitive package;
- no source path contains a hardcoded user home or sibling-repository requirement;
- contradictory “DONE”, latency, model-residency, test-count, and two-model claims are removed from all source-of-truth docs;
- the model-caller inventory exposes every context/keep-alive mismatch and the private-data migration is documented without reproducing private values;
- the selected language policy is stated once and every remaining disagreement is listed as incomplete work;
- unit tests pass;
- `graft build` completes;
- no audio code is changed without re-reading `docs/AUDIO_SETUP.md`.

Rollback: restore only the changed repository config/docs files; never overwrite the live user config automatically.

### Phase 1 — security and capability policy

Tasks:

1. Add a capability registry with tiers:
   - `read_only`: inspect files, status, tabs, reports;
   - `reversible`: open app, adjust volume, create note, pause media;
   - `mutating`: edit files, send messages, install packages, submit forms;
   - `destructive`: delete, overwrite, publish, pay, force-push, shutdown.
2. Require explicit confirmation for mutating actions with external side effects and all destructive/financial actions.
3. Make confirmation describe the exact target, command/tool, scope, and expected effect. Give it an expiry and one-use ID.
4. Add the temporary deny/confirm adapter around every existing OpenClaw/Hermes dispatch path before deleting or weakening the unrestricted path.
5. Prove with bypass tests that direct, fallback, retry, legacy, and error-recovery dispatch paths cannot skip the adapter.
6. Only after tasks 4–5 pass, remove “never ask permission” from worker prompts. Workers must call broker tools and obey broker decisions.
7. Add safe dashboard defaults: loopback unless LAN mode is explicit; mandatory header token for LAN; configured-origin CORS; no query-string token authentication.
8. Add secret redaction at the logging, event, prompt, browser-result, and report boundaries.
9. Keep browser personal-profile actions separate from isolated automation. Never reuse personal cookies in isolated tasks.
10. Inspect the pinned CopilotKit human-in-the-loop source and tests first. Reuse its `InProgress → Executing → Complete` gating, one-settlement behavior, abort cleanup, and renderer cleanup in the existing static UI; implement restart durability in the existing Python state store and document that local extension. Do not add React or a CopilotKit runtime.

Acceptance gate:

- security tests prove unauthenticated LAN API calls fail;
- query-string tokens are rejected;
- logs and reports contain redaction markers rather than secrets;
- destructive and payment actions cannot execute without a matching approval ID;
- prompt injection text cannot change the capability tier;
- OpenClaw and Hermes cannot bypass the temporary policy adapter through a legacy dispatch path;
- **Packet 1A integration limitation carried here (per DEFERRED-003, docs/MIGRATIONS.md):**
  1A's registry / PolicyEngine / ConfirmationStore ship deliberately unwired. Packet 1B's
  bypass suite must therefore prove, for EVERY direct/fallback/retry/error-recovery dispatch
  entry it connects: (a) the call routes through the 1A policy core and cannot skip it;
  (b) confirmation records are issued only by the user path and consumed only by user-originated
  requests (`CONFIRMATION_SOURCE_FORBIDDEN` semantics preserved); (c) redaction from
  `voicebridge/redaction.py` is applied at every newly connected journal/prompt/output boundary.
  1B is not acceptable if any legacy path reaches execution without a recorded Decision;
- existing safe local actions still work.

Rollback: feature-flag the broker, but never fall back from a denied action to raw shell execution.

### Phase 2 — typed event bus and shared state

Tasks:

1. Define versioned events: `run.started`, `step.started`, `tool.started`, `tool.output`, `approval.required`, `state.snapshot`, `state.delta`, `tool.finished`, `run.finished`, `run.failed`, and `run.cancelled`.
2. Include `run_id`, `parent_id`, monotonic `seq`, timestamp, actor, tool, redacted payload, and schema version.
3. Add SSE for browser clients with `Last-Event-ID`/sequence replay and heartbeat. Keep JSON polling as a compatibility fallback.
4. Convert gateway callbacks, local actions, browser scripts, task queue, and TTS progress into the same event stream.
5. Make approval/resume state durable and restart-safe.
6. Remove reliance on legacy global web state once per-request context and event state are proven equivalent.
7. Align the wire contract with AG-UI lifecycle, text-message, tool-call, state, activity, and special/raw event families. Use `STATE_SNAPSHOT` as the replay baseline and `STATE_DELTA` as ordered RFC 6902 JSON Patch; if internal names remain, provide a tested bidirectional adapter.
8. Detect a missing, stale, or invalid delta base after reconnect and request or emit a fresh snapshot before applying further deltas.
9. Start from the pinned AG-UI Python event enum, encoder, and encoder tests. Adapt the smallest local event/encoder module to the existing `ThreadingHTTPServer`; do not install the CopilotKit runtime, add FastAPI/Uvicorn, or convert the dashboard to React.
10. Add pinned upstream-compatible fixture tests for camelCase fields, omission of null optional fields, SSE framing, event ordering, and round-trip conversion. Any JSON Patch library must be a declared direct dependency with a measured/import-reviewed cost; otherwise use a documented, tested local subset only for operations the bridge emits and recover with snapshots for unsupported input.

Acceptance gate:

- reconnecting clients receive no duplicate events and no gaps;
- two concurrent requests cannot exchange summaries, audio, approvals, or memory;
- cancelling a run emits one terminal event;
- the dashboard can render progress without polling the entire history;
- a client can reconstruct state from a snapshot plus ordered deltas and recover by requesting a fresh snapshot after a gap;
- internal events round-trip through the documented AG-UI-compatible adapter without losing terminal ordering or redaction;
- event payloads pass redaction tests.

### Phase 3 — unified PC capability broker

Tasks:

1. Move local actions, terminal sessions, files, window/app control, screenshots, media, browser operations, and external workers behind the registry.
2. Use argv/structured calls by default; shell strings are prohibited for model-generated commands.
3. Add persistent per-task shell/PTY sessions with working-directory, environment, timeout, output cursor, and cleanup state.
4. Add cancellation and resource budgets to every tool.
5. Add dry-run/preview for file edits, package installs, browser form submissions, and system changes.
6. Keep whitelisted direct actions as the fastest path; do not route volume or pause commands through a large model.
7. Let OpenClaw and Hermes request declared tools instead of receiving an unrestricted “full PC” instruction.
8. Define isolated-run lifecycle states (`pending`, `starting`, `running`, `paused`, `stopping`, `terminated`, `failed`) with run ID, TTL, cancellation, and terminal reason.
9. Enforce CPU, memory, disk/file, output, process-count, and time budgets; allow only declared readable paths, writable paths, and environment variables. Real untrusted workloads must not read-only bind the whole host, user home, service environment, browser profiles, SSH/GPG directories, or unrelated `/proc` entries. The first untrusted profile supports `network: none` only. Do not advertise destination allowlists until an enforceable egress mechanism exists and passes tests; networked work must use declared host tools through the broker.
10. Support warm sessions without sharing personal profiles, cookies, credentials, or secret environment variables. Verify the selected bubblewrap/cgroup/systemd isolation primitive at startup; return `ISOLATION_UNAVAILABLE` and refuse untrusted execution when it is missing.
11. Adapt the pinned OpenSandbox probe and lifecycle tests to this stack: run the exact Bubblewrap namespace smoke path, report available UID modes, resolve every requested bind through `realpath`, reject symlink escapes, construct a minimal workload filesystem, use a private `/proc`, clear the environment before applying an allowlist, and own cleanup through one lifecycle object.
12. Launch each isolated workload in a separate named user-systemd transient service with explicit `MemoryMax`, `TasksMax`, CPU quota, timeout, and cleanup. Use a dedicated workspace plus enforceable file-size/workspace limits; if a requested hard disk quota cannot be enforced, refuse that profile instead of reporting a soft monitor as isolation. Do not leave untrusted tools inside the bridge service's 2 GB cgroup, and do not add Docker/Kubernetes/VM orchestration.

Acceptance gate:

- every tool has schema, side-effect tier, timeout, and tests;
- unknown tools fail closed;
- shell injection tests pass;
- a cancelled process group and its children are gone;
- a tool cannot read or write outside its declared scope;
- isolated runs have no orphan after timeout, cancellation, or bridge crash;
- hostile filesystem, environment, privilege, and network tests prove the isolation boundary, or the run is refused;
- fixture secrets placed in the user home, service environment, sibling directories, and unrelated process environment are unreadable from the untrusted profile;
- a clean machine without Bubblewrap returns `ISOLATION_UNAVAILABLE`; a machine with Bubblewrap passes the copied/adapted upstream capability probe before accepting work;
- cgroup evidence proves the workload is outside the bridge service's cgroup and inside its own bounded transient unit;
- local safe actions remain low-latency.
- **Carried from 1B verification:** the live worker approval round-trip (Decision
  id issuance → consumption → fail-closed replay/expiry/target-mismatch at the
  broker boundary) must be re-proven against REAL trusted local tools, with
  redacted audit events (DEFERRED-004);
- **Carried from 1B verification:** no whole-PC security claim is valid until
  this packet has routed trusted local tools and every remaining execution path
  through the registry (DEFERRED-005);

### Phase 4 — resource and model scheduler

Tasks:

1. Build one reproducible benchmark harness before changing defaults. Record model name/digest/source revision, quantization, Modelfile hash, Ollama version, resolved options, prompt/context token counts, GPU driver, hardware, ambient VRAM, and warm/cold state.
2. Benchmark the exact transition sequence `classifier → chat → classifier → summary → chat`. Capture wall time, `load_duration`, prompt/eval duration and counts, first content, first complete sentence, first audio, complete response, tokens/sec, CPU/GPU split, VRAM/RAM, and errors. A hot-path runner reload is a failed result even if isolated warm requests are fast.
3. Create a labeled private evaluation corpus with at least: deterministic routing, ambiguous routing, contextual confirm/cancel, local-versus-coding safety, malformed speech, short chat, personal facts with conflicting stale history, tool selection, required/invalid arguments, refusal/fallback, language policy, and prompt-injection attempts. Keep a held-out set; do not tune thresholds on the test set.
4. Establish one resolved runner contract for every call to the resident generative model: immutable model digest/alias, one measured `num_ctx`, one GPU placement/parallelism policy, and `keep_alive=-1`. Remove per-caller context drift. Assert at startup and in tests that classifier, chat, summary, and planner payloads agree.
5. Select the smallest context that fits the measured context compiler budget plus output reserve; compare at least 4096 and 8192 rather than preserving 8192 because a stale comment says so. A larger context must justify its VRAM and prompt-evaluation cost.
6. Replace the private/stale baked Modelfile biography with a minimal model artifact. Runtime code owns the persona/safety prompt; personal and volatile facts come from the local context compiler. Preserve no secret or direct private identifier in model layers, Modelfiles, fixtures, logs, or benchmark outputs.
7. Shorten hot-path structured decisions. Use Ollama JSON Schema, temperature zero, validation, and an explicit max-output budget. Do not generate a free-text `reason` or duplicate task text unless diagnostics prove it is needed. Invalid output returns a typed no-decision and never executes a default action.
8. Implement the routing cascade in order: deterministic state/rules; optional semantic matching; resident-model ambiguity resolver; specialist delegation. The optional semantic route must benchmark the already-installed `nomic-embed-text` with CPU-only placement first, the model card's `classification:` prefix for both route examples and queries, precomputed route vectors, route-specific thresholds, a score margin, and an explicit no-match result. Reject it if it reloads/evicts the generative runner, misses the warm latency gate, or adds an undeclared heavy dependency.
9. For L2 planning, send only the top relevant tool groups and then the smallest matching tool schemas. The output is a typed proposal containing tool/version, validated arguments, confidence, and whether clarification/confirmation is required. The broker independently decides permission and execution.
10. Run a controlled model bake-off under the same runner/context/evaluation contract:
    - baseline: current abliterated Qwen3 4B Q4_K_M;
    - preferred challenger: official Qwen3-4B-Instruct-2507-derived Q4_K_M and Q5_K_M at the pinned revisions in section 4.1;
    - optional challenger: Qwen3.5-4B Q4_K_M/Q5_K_M in text-only non-thinking mode, only after the preferred challenger is measured.
11. Keep candidate aliases separate and record downloaded file hashes, source revision, license, disk cost, and deletion/rollback command. Never replace `qubit-brain4` in place during evaluation. Public leaderboards are supporting evidence only; the local private corpus and measured runtime decide.
12. Gate model selection in this order: safety/permission and schema validity; route/tool/personal-memory quality; no regressions by class; then latency and resources. Do not hide a safety regression inside a weighted average. Prefer a Pareto improvement; Q5 is accepted only if its measured quality gain is worth its VRAM/latency cost.
13. Keep exactly one resident generative model by default. A lightweight CPU embedding runner may coexist only after its independent resource gate passes. Do not load an 8B or vision model beside the 4B model on the 8 GB GPU.
14. Add a VRAM guard before any model load and an explicit eviction/restore policy for an optional deep tier. A deep-tier task must finish or fail, evict cleanly, restore the fast model at the canonical options, run a warm probe, and report restoration latency.
15. Use AirLLM-style layer streaming only for a deliberately slow isolated deep-tier experiment; do not use it for short voice turns. Keep AirLLM, Torch, Transformers, bitsandbytes, model shards, SGLang, and vLLM out of the bridge virtual environment/service.
16. Keep local Piper/low-latency TTS as the default and preserve sentence streaming. Network TTS is optional and must have a local fallback. Never speak thinking/tool-call markup or unvalidated partial JSON.
17. Record whether each benchmark uses compression, prefetching, CPU offload, disk streaming, speculative/MTP support, flash attention, warm/cold caches, and context size. Do not claim Qwen3.5 MTP or AirLLM speedups on Ollama unless that exact runtime path was measured.

Minimum Phase 4 gates on the held-out corpus and this machine:

| Metric | Required gate |
|---|---|
| L0 deterministic route latency | p95 < 10 ms; 100% of labeled deterministic cases avoid model/embedding calls |
| Optional L1 semantic latency/resource | warm p95 < 75 ms; CPU-only; <= 500 MB additional resident RAM; zero generative-runner reloads/evictions |
| End-to-end route quality | macro-F1 >= 0.97; safety-critical `local`, `confirm`, `cancel`, and `coding` precision = 1.00 and recall >= 0.95 on the acceptance corpus |
| Abstention safety | every below-threshold/margin, malformed, unknown, or denied case becomes clarify/no-decision; zero action executions |
| L2 router format/latency | 100% of accepted decisions validate against schema; malformed output fails closed; warm p95 <= 750 ms with `load_duration` effectively zero |
| L2 planner quality | correct tool/version and all required arguments >= 0.95; unknown/out-of-scope tool acceptance = 0; broker bypass = 0 |
| Personal/correction QA | >= 0.95 exact factual score; corrected fact wins over stale conflict in 100% of conflict cases; unsupported private fact invention = 0 |
| Warm local voice path | first content p95 <= 300 ms; first audio p95 <= 800 ms; complete 1–3 sentence local turn p95 <= 1.5 s |
| Runner stability | zero unintended loads in 100 repeated mixed-path transitions; canonical alias/digest/options restored after every deep-tier or failure test |
| Fast-tier resources | steady-state total GPU use <= 6.5 GiB on the 8 GiB GPU, 100% model GPU offload, no OOM/load thrash, and no unapproved second generative model |

If corpus size is too small for a defensible percentile or per-class metric, status is `INCOMPLETE — EVALUATION CORPUS TOO SMALL`; an agent may not lower a gate or report a pass.

Acceptance gate:

- no model claim is marked complete without a recorded benchmark;
- alternating classifier/chat/summary/planner calls causes zero unintended model reloads in the repeated transition benchmark;
- deterministic L0 routing is under 10 ms p95; optional semantic L1 is under 75 ms warm p95 or is omitted; generative L2 is called only for corpus cases that remain ambiguous after L0/L1;
- the complete minimum-gate table passes without averaging away a failed row, and no denied/uncertain sample executes an action;
- structured router/planner output is schema-valid in 100% of the acceptance corpus or fails closed as a typed no-decision; tool name and required-argument accuracy meet the recorded gate before tool planning is enabled;
- the selected default meets the agreed warm first-content/first-audio/full-turn budgets and does not OOM; cold/reload latency is reported separately and is never averaged into warm performance;
- VRAM remains below the configured safety ceiling;
- a failed model load falls back once, without repeated load thrashing;
- no private identifier or stale mutable biography remains baked into the selected model artifact;
- personal-fact, correction, language, routing, tool-use, non-empty-response, injection, and regression tests pass;
- model selection includes a one-command alias/config rollback and proves the previous fast model can be restored.

### Phase 5 — information fabric

Tasks:

1. Define one normalized evidence record for repositories/files, browser tabs, inboxes, local reports, web search, OpenClaw/Hermes history, and personal memory. It includes stable ID, source adapter/version, source reference, captured/updated time, freshness/TTL, scope/owner, permission, sensitivity, trust level, content hash, redacted excerpt, confidence, and citation.
2. Make `read/search`, `write/act`, and `remember/forget` distinct tool contracts. An adapter registered as read-only cannot navigate, submit, upload, download, message, mutate a file, or promote a memory.
3. Implement a small personal-memory store with the existing SQLite/FTS5 support—no new database service. Required tiers are:
   - `core`: compact identity, durable user preferences, language/accessibility choices, and active project pointers;
   - `working`: current goals, pending decisions, task state, and short session summary;
   - `recall`: searchable conversation/task outcomes and corrected facts;
   - `archive`: documents and evidence loaded only when relevant.
4. Memory records require scope, kind, normalized hash, source/provenance, confidence, sensitivity, created/updated/expiry times, active/tombstoned status, and optional `supersedes` linkage. Keep append-only change history for add/update/correct/expire/delete. Filter scope and permission before retrieval scoring.
5. Add explicit user operations: `remember <fact>`, `forget <fact/id>`, `correct <old> to <new>`, `what do you know about me?`, private export, and permanent purge. Explicit operations are deterministic and immediately visible. Never require the model to guess whether the user asked to store something.
6. Stage model-inferred memories as candidates with supporting source IDs. Do not promote an inferred secret, credential, third-party personal detail, medical/financial claim, prompt instruction, or one-off statement automatically. Promotion requires the Phase 6 quality policy and remains inspectable/removable.
7. Build hybrid retrieval with FTS5 as the mandatory baseline and optional semantic reranking only if Packet 4C approved it. Store compact embeddings locally when approved; use standard-library numeric storage/scoring for this small corpus. Do not add Qdrant, Chroma, FAISS, NumPy, or a graph database without a new measured architecture decision.
8. Implement the context compiler with a hard budget and deterministic precedence: immutable persona/safety; active state; corrected core facts; top relevant recall/evidence; bounded tool schemas; reserved output tokens. Deduplicate by source/hash, prefer newer corrections, label stale/conflicting evidence, and omit low-score records rather than truncating required instructions.
9. Treat retrieved content as quoted untrusted data. Prompt injection inside pages, emails, reports, repository text, memories, or past model replies cannot modify policy, tools, persona, routing thresholds, or future memory.
10. Cache safe read results with TTL; do not cache credentials, cookies, authorization headers, private form contents, or secret-bearing URLs. Invalidate or label stale entries when the source changes.
11. Parallelize independent read-only sources with per-adapter deadlines and a total deadline. Preserve successful partial results, identify failed/timed-out adapters, and summarize only bounded redacted evidence.
12. Preserve raw evidence in protected storage only when allowed. The model/UI receives minimal redacted excerpts and stable citations; public reports never inherit private scope.
13. Add memory/context observability without content leakage: selected record IDs, source classes, scores, age, token counts, omitted-count reason, conflict flags, and retrieval latency.

Acceptance gate:

- every answer that uses external information has source references;
- stale data is labeled;
- a failed adapter does not erase successful results from other adapters;
- private browser data never enters a public report or unrelated prompt;
- no adapter can silently perform a write during a read operation;
- personal-memory add/search/correct/forget/export/purge and append-only history pass normal, malformed, concurrent, restart, scope, and rollback tests;
- corrected facts outrank stale facts, tombstoned/expired records are not retrieved, and cross-scope records never appear;
- every compiled prompt stays within the selected model context with the required output reserve and reports what was omitted without leaking content;
- injection text retrieved from any source cannot change policy, invoke a tool, or become durable memory;
- FTS5-only mode works on a clean standard-library installation; optional semantic reranking can be disabled without losing memory correctness.

#### Optional public-web extraction adapter

The information fabric may use a pinned Scrapling-derived adapter for authorized
public-web extraction. Scrapling is a tool and parser, not part of the model
weights and not a replacement for the authenticated real-Chromium path used by
personal Gmail/Instagram/Messenger monitoring.

Rules:

1. Inspect and pin the exact upstream commit, license, source files, and tests in
   `docs/UPSTREAM_REUSE.md` before adding it. Copy/adapt only the smallest parser,
   fetcher, or Markdown-cleaning behavior that fits the existing Python stack.
2. Try bounded HTTP extraction first; use browser/CDP fetching only when the
   evidence contract proves JavaScript rendering is required.
3. Return typed, bounded, cited, redacted evidence. Page text is untrusted data,
   never instructions, permissions, or executable tool input.
4. Enforce domain allowlists, SSRF/private-network blocking, redirects,
   timeouts, maximum bytes/pages/concurrency, robots policy where applicable,
   cancellation, and cache TTLs.
5. Do not use stealth, proxy rotation, CAPTCHA solving, or XHR capture to defeat
   access controls, evade site rules, or access private accounts. Stop on login,
   2FA, CAPTCHA, consent, or ambiguous authorization.
6. Keep the adapter optional and removable. It must not add an MCP server,
   browser profile, second model server, or new application framework.

Acceptance gate:

- public-page fixtures produce stable evidence records with citations and no
  prompt-injection escalation;
- limits, SSRF protection, redaction, partial failures, cancellation, and cache
  behavior pass negative/degraded tests;
- Scrapling is not used for personal-account monitoring and no private page body
  enters fixtures or handoffs;
- measured parser/resource cost is recorded before enabling it by default.

### Phase 6 — learning and quality loop

Tasks:

1. Keep corrections higher priority than old preferences.
2. Require repeated consistent positive feedback before distilling a preference.
3. Add conflict detection, decay/TTL, provenance, and a user-visible way to remove a lesson.
4. Never train, rewrite prompts, or change routing automatically from one interaction.
5. Build a small private evaluation set from accepted/corrected tasks and run it before model, router, or safety changes.
6. Store task outcome, approval, retry, correction, and completion—not just the final text.
7. Separate three signals: explicit user instruction, observed task outcome, and model inference. Explicit correction wins; outcomes may adjust confidence; model inference alone cannot rewrite core persona, policy, permissions, or routing.
8. Consolidation runs only as a bounded low-priority/idle job. It uses the canonical resident-model options if a model call is required, cannot trigger a context-size reload, yields to voice requests, and has a strict batch/time/output budget.
9. A preference is promoted only from explicit storage or repeated consistent evidence across separate interactions. Store the evidence IDs and confidence calculation. Contradictory or stale evidence creates a review item instead of silently choosing.
10. Evaluate the assistant as a system, not only as a model: route decision, tool shortlist, permission outcome, execution result, citation quality, memory retrieval, response quality, latency, and resource cost. A smarter model does not compensate for a less safe broker or worse routing.
11. Provide a local “why did you do/remember this?” explanation from structured provenance and decisions. Never ask the model to invent the audit explanation after the fact.

Acceptance gate:

- a bad lesson can be removed;
- corrections override stale answers;
- prompt injection cannot become a permanent preference;
- a model/router update must improve or preserve every safety-critical gate and the agreed quality/latency score before rollout;
- idle consolidation cannot delay first audio, reload the model runner, exceed its resource budget, or promote an unreviewable memory;
- every promoted learned preference can show its evidence and be reverted without deleting unrelated history.

### Phase 7 — rollout and operations

Tasks:

1. Split tests into `unit`, `contract`, `integration`, `live-model`, `live-dashboard`, and `monitor` commands.
2. Long-running monitoring must never run during normal pytest collection.
3. Watchdogs must not restart services unless an explicit operator flag is set.
4. Add health checks for config, model, gateway, browser CDP, audio, disk, VRAM, and event-journal writeability.
5. Roll out each feature behind a config flag with a one-command rollback.
6. Keep a change report containing files, tests, benchmarks, risks, and rollback instructions.
7. Rebuild Graft after any substantial change.
8. Add a clean-venv installation test from `pyproject.toml`; installed-but-undeclared packages must not hide missing dependencies.
9. Test the generated systemd unit against the repository template, including restart policy, resource limits, paths, environment overrides, and secret-safe output.
10. Build a verified download-and-run distribution for the first supported Ubuntu
    target: self-contained application/runtime bundle, explicit model-artifact
    acquisition, hardware/resource doctor, launcher, upgrade path, and uninstall
    behavior. Do not silently enable services or mutate live browser state.
11. Define a versioned portable-state bundle for memory, lessons, preferences,
    reports, non-secret configuration, model metadata, and migration history.
    Exclude API keys, cookies, browser profiles, active approvals, private
    debugger URLs, and machine-specific credentials by construction.
12. Add encrypted export/import with an audited, pinned encryption mechanism;
    never invent cryptography. Require an explicit user passphrase or approved
    OS key store, verify integrity before restore, stage atomically, support
    dry-run/preview, and preserve a recoverable pre-restore backup.
13. Add explicit trusted-device enrollment and device profiles. A restore to a
    new device must require user authorization, re-check hardware/capabilities,
    and require fresh browser login, credentials, and voice enrollment where
    needed. Never clone a live Chromium profile or secrets automatically.
14. Test clean-install, upgrade, interrupted-install, uninstall, export/import,
   wrong-passphrase, corrupted-bundle, schema-migration, rollback, low-disk,
   CPU-only, GPU-present, missing-Ollama, missing-browser, and missing-audio
   states. A missing optional dependency is typed degradation, not a false pass.
15. Add a beginner-first onboarding layer with safe defaults and plain-language
    status, while keeping an advanced settings surface for expert control. The
    beginner path must not require knowledge of Ollama, runner contracts, CDP,
    model digests, or systemd. Every advanced setting must show permissions,
    resource cost, effective value, health state, and rollback/reset behavior.
16. Define a versioned provider/profile registry for the replaceable reasoning
    model, STT engine/model, TTS provider, and voice artifact. The default
    profile remains the verified local Qubit model, CPU-friendly STT, and Piper.
    A replacement is installable/selectable only when its source, revision,
    license, checksum, supported hardware/language, quality/safety evidence,
    latency, and VRAM/RAM cost are recorded and its gates pass. Keep one
    approved resident generative model by default; never activate a downloaded
    file or provider merely because it is present.
17. Expose the provider registry through the static settings UI and stable CLI
    commands for list/show/check/install/select/reset/upgrade/remove/rollback.
    Changes are dry-run by default, explicitly applied, staged atomically,
    backed up, audited, and reversible. A voice choice is independent from the
    TTS provider, model choice, and audio-device choice. No provider change may
    silently restart a service, enable recording, alter permissions, or change
    memory/reports/device settings.
18. Make the web dashboard the primary user interface with feature parity for
    supported CLI operations: beginner onboarding, advanced settings,
    provider/model/STT/TTS/voice selection, audio devices, permissions, device
    enrollment, package/health status, task progress, approvals, cancellation,
    reports, audit/provenance, export/import, upgrade, reset, and rollback. Every
    control must call a real authenticated backend contract and display typed
    success, unavailable, degraded, denied, partial, or failed state. Require
    confirmation for side effects, keep loopback binding by default, escape all
    untrusted text, and never expose secrets, private message bodies, prompts,
    credentials, or filesystem paths. Add API, negative/security, persistence,
    accessibility, and local UI smoke tests for every supported control.
19. Brand the distributable as Qubit Voice Agent. Plan the physical repository
    rename from `openclaw-voice-bridge` as a separate release migration that
    updates package metadata, docs, service/unit identifiers, configured paths,
    launchers, fixtures, and compatibility aliases together. It must include a
    dry-run, backup, migration, verification, and rollback path. Do not perform
    the rename as incidental cleanup in another packet.
20. Add a versioned capability/skill manifest for every website, desktop app,
    editor, browser operation, and marketplace package. The manifest must record
    stable ID/version, source/revision/license/hash, capabilities, domains or
    workspace scopes, read/write/destructive tier, data sensitivity, approval
    policy, timeout/resource limits, audit event names, compatibility, and
    rollback/removal behavior. Installing a skill must validate the manifest and
    stage it disabled until explicitly enabled.
21. Add explicit permission profiles with `restricted` as the default and
    `unrestricted` as an opt-in convenience mode. Restricted mode permits only
    explicitly granted capabilities and scopes. Unrestricted mode may
    pre-authorize routine non-destructive work inside user-selected scopes, but
    is never a universal bypass: hard-denied operations and sensitive
    side-effects still require confirmation or remain denied. Mode changes must
    be authenticated, explicitly confirmed, time-bounded or resettable, audited,
    reversible, and impossible for model output or page text to trigger.
22. Expose the same permission controls through stable CLI commands and the
    authenticated dashboard. Minimum CLI surface:
    `permissions mode show|set restricted|unrestricted`,
    `permissions list|show|check`, `permissions grant`, `permissions revoke`,
    `permissions reset`, `skills list|show|check|install|enable|disable|remove`,
    and `skills rollback`. Every mutating command must support dry-run/preview,
    explicit confirmation, scope, expiry, audit output, and rollback.
23. Add a beginner settings view with plain-language Restricted/Unrestricted
    choices, current grants, affected apps/sites/folders, expiry, risk warning,
    and reset/revoke controls. Add an advanced view with capability IDs,
    side-effect tiers, scopes, data classes, approval requirements, resource
    budgets, provenance, and effective-value diagnostics. Every control must
    call a real authenticated backend; frontend-only toggles are forbidden.
24. Define the extension path for a new site/app/editor: manifest first,
    smallest adapter or declared launcher second, permission registration third,
    route/tool schema fourth, then synthetic fixtures, negative/security tests,
    persistence, audit, resource tests, documentation, and staged canary. A
    launcher that only opens an application must not claim that the application
    is controllable. Browser skills must use the approved existing-Chromium/CDP
    contract; editor skills must use explicit workspace scopes; terminal skills
    must use declared argv/isolated execution contracts.
25. Add tests proving default deny, scope matching, expiry, revoke/reset,
    restricted versus unrestricted behavior, hard-deny invariants, confirmation
    for sensitive actions, prompt/page injection resistance, dashboard/CLI
    parity, restart persistence, migration, rollback, skill removal, malformed
    manifests, bad hashes/licenses, disabled skills, and no permission expansion
    when a provider, model, voice, browser, or app changes.
26. Replace any worker instruction that says to “never ask permission” or grants
    an unrestricted shell/browser implicitly. Workers must request a declared
    capability and receive a policy decision; model output, a skill manifest,
    retrieved page text, or a user-selected convenience mode cannot invent or
    silently widen authority.

Acceptance gate:

- unit and contract suites pass on a clean environment;
- live tests report `degraded` separately from `passed`;
- the bridge starts with the documented user config;
- rollback restores the previous model/config/feature flag without deleting user data;
- a fresh supported Python environment can install and start the bridge without relying on the developer machine's transitive packages;
- the deployed systemd service is reproducible from the repository template plus documented local overrides;
- the dashboard, voice path, task path, and cancellation path have smoke coverage.
- a fresh supported Ubuntu environment can download, verify, install, launch, and
  diagnose the application without relying on the developer's home directory;
- model artifacts are checksum/license verified, hardware-selected, separately
  removable, and rollback can restore the previous working alias/configuration;
- export/import restores only the documented portable state, rejects secrets and
  browser-session material, preserves reports/memory/history, and has an atomic
  rollback drill;
- a new device cannot impersonate the existing identity without explicit
  enrollment, while a user-authorized restore can recover the agent state;
- the package consumes no more resources than the measured standalone stack for
  the same model/configuration, apart from documented installer/update overhead.
- a first-time user can install and operate the safe default profile without
  understanding implementation terms, while advanced users can inspect and
  replace the brain, STT, TTS provider, and voice from both UI and CLI;
- provider changes are pinned, licensed, resource/quality/safety-gated,
  reversible, and independently replaceable; a failed change leaves the prior
  working profile active;
- a separately verified Qubit Voice Agent branding migration updates every
  repository/package/service/config/launcher reference and preserves state,
  permissions, reports, memory, device settings, and rollback aliases.
- the web dashboard is feature-complete for the supported CLI surface, with
  real authenticated backend calls, typed failure/degraded states, confirmations,
  persistence, accessibility, security coverage, and local UI smoke evidence;
  placeholder or frontend-only success is rejected.
- every installed website/app/editor skill has a validated manifest and remains
  disabled until explicitly enabled;
- Restricted mode is the default, Unrestricted mode is explicit and auditable,
  and neither mode bypasses hard-deny, confirmation, redaction, audit, resource,
  isolation, or loopback rules;
- CLI and dashboard permission/skill settings have real backend parity,
  persistence, scope/expiry/revoke/reset behavior, rollback, and negative tests;
- adding a new site/app/editor requires a manifest, declared adapter/tools,
  narrow scopes, provenance/license, and staged verification rather than a
  prompt edit or a launcher-only claim.

### Phase 8 — communications, evidence reports, and production qualification

Dependency gate: Phase 8 may start only after Packets 1A–1B, 2A–2B, 3A, 5A, 5C, and 7A are independently verified. Packet 8A may inventory and define contracts earlier only if it changes no runtime behavior, reads no private message bodies into fixtures, and does not install or enable a service. Packet 8B and every later live website/app/editor integration additionally require Packet 7E permission/skill contracts to be independently verified. Packet 8E may be implemented as a non-live onboarding layer after 7E and 8A, but it must not grant browser access or claim site control by itself. Packet 8A remains valid because it is schema/store work only and does not grant capabilities.

Tasks:

1. Replace text snapshots with versioned typed records for `scan`, `account_health`, `communication_item`, `cursor`, `evidence_asset`, `report_manifest`, and `notification`. Required item fields include platform, account-scope pseudonym, stable source/thread/item ID when available, deterministic fallback fingerprint, sender/display name, normalized timestamp plus raw timestamp, subject/preview, `is_new`, `unread: true|false|unknown`, safe source URL, captured time, content hash, adapter/version, evidence status, and redaction/sensitivity labels.
2. Define `new` as “first observed after the last committed account cursor,” not “currently unread.” Keep unread and unknown read-state separate. Use platform item/thread IDs first; a fallback hash must include account scope, normalized sender/thread, timestamp, and bounded content, be versioned, and record collision handling.
3. Store monitor state in the existing project SQLite/state boundary with schema migration and atomic transactions—no new database service. Track per-account baseline, cursor/watermark, seen-item identity, first/last seen, content hash, report ID, notification/acknowledgement, adapter health, and lease. Commit a cursor only after every new item and the corresponding report manifest/assets are durably committed. Failed/partial accounts keep their prior cursor.
4. First activation defaults to `baseline`: record currently visible items as already seen and produce a private baseline/health result without announcing old conversations as new. Historical backfill requires an explicit bounded operator option and must never be enabled silently.
5. Move the monitored adapters into this repository and remove production dependence on `$HOME/.hermes/...` script paths. The required adapter attaches only to the user's existing real Chromium at loopback CDP, finds already logged-in Gmail/Instagram/Messenger targets by verified origin, and never starts Playwright profiles for these accounts. Missing browser, missing target, logged-out state, 2FA, CAPTCHA, checkpoint, selector drift, or consent screen is a typed degraded/auth-required result—not an empty successful inbox.
6. Make monitoring mechanically read-only with respect to accounts and user tabs. Its base CDP allowlist may inspect targets, evaluate repository-owned versioned extraction code, query DOM/layout, and capture a screenshot; it may not execute model/message-provided JavaScript, click, type, submit, send, react, mark read/unread, download attachments, activate/focus/close user targets, clear storage, modify cookies, navigate an existing user tab, or execute page-provided instructions. If virtualized lists require navigation/scrolling, Packet 8A must define a separate reversible `monitor_tab` capability: create or reuse dedicated non-activated tabs in the same real Chromium profile, mutate only those tabs, never close/replace a user-owned tab, and fail degraded if Chromium cannot guarantee that boundary.
7. Version each site adapter and selector set. Prefer semantic/ARIA/text contracts where the real site exposes them, then short tested CSS fallbacks. Broad class fragments such as `div.x1lliihq`, blanket `[role=listitem]`, and “all rows are unread/read” are forbidden acceptance implementations. Add sanitized DOM/ARIA fixtures for each known layout and a selector-drift result when minimum required fields cannot be proved.
8. Bound every scan: one cross-process lease, one CDP connection per target, per-adapter and total deadlines, item/page/scroll caps, bounded excerpts, and cancellation. Scan until the committed cursor is reached or a configured bound is hit. Scroll or navigate only in an approved monitor-owned tab, never in a user-owned tab. If the bound is hit before the cursor, return `OVERFLOW/PARTIAL`, preserve captured evidence, and do not advance past unseen data.
9. Capture evidence through the real Chromium CDP `Page.captureScreenshot`, clipped to the relevant row/thread region without calling `Page.bringToFront` or `Target.activateTarget`. Do not capture unrelated sidebar conversations or secret form fields. Every report item must link to a scoped screenshot or carry a typed reason such as `NOT_AVAILABLE`, `TARGET_HIDDEN`, `CLIP_FAILED`, or `PRIVACY_REDACTED`; screenshot failure does not erase the text item.
10. Make canonical JSON the source of truth for a report bundle. Derive Markdown and self-contained, responsive, print-friendly HTML deterministically from the same manifest. The optional local-model summary may prioritize items, but it must be visibly labeled as generated analysis, cite item IDs, fit a strict budget, use the canonical runner options, and never invent facts or block report creation.
11. Write bundles atomically under a private state root, for example `reports/<report_id>/manifest.json`, `report.md`, `index.html`, and `assets/<asset_id>.webp`. Stage, fsync as appropriate, validate hashes/schema/links, then rename into place. Directories are `0700`; files are `0600`; filenames are generated IDs, not senders/subjects. Store MIME type, dimensions, byte count, SHA-256, capture time, source item ID, and redaction status for each asset.
12. A professional inbox report must include: report ID and exact Europe/Bucharest time window; overall `SUCCESS|PARTIAL|DEGRADED`; executive summary; new-item counts; account health and freshness; items grouped by platform/account with sender, time, unread state, safe clickable source link, screenshot thumbnail/link, priority plus deterministic reason, and suggested follow-up; action-item checklist; adapter timing/count metrics; partial failures and recovery; evidence/provenance manifest; privacy/retention note; and generated-analysis disclaimer when applicable. Never include cookies, auth headers, tokens, full account identifiers, or secret-bearing URLs.
13. Replace the false `/api/reports` behavior with a versioned report index/detail/asset contract backed by the manifest store. Keep activity/log APIs separate. Asset and bundle routes must require the same header-token authorization, validate report/asset IDs, prevent traversal/symlink escapes, set correct MIME/CSP/cache headers, and never expose filesystem paths. Preserve old Markdown files read-only through a clearly labeled `legacy` index or migration; do not delete them during migration.
14. Update the repository-owned static reports UI to render real report cards and details from the new API: status/freshness, platform filters, new/unread counts, thumbnails, safe links, evidence availability, account failures, acknowledge/read state, search, export/download, print view, retention controls, empty/no-change state, and accessible keyboard/mobile behavior. Render all message-derived text as escaped text; never inject report HTML or Markdown as trusted DOM.
15. Emit a typed `communications.report.ready` event only after bundle commit. Notify locally through the dashboard and `notify-send` when new items exist. Voice announcement is opt-in, obeys quiet hours/do-not-disturb and current audio availability, speaks only a short count/priority summary, and never reads private message text aloud by default. No-change scans are silent; authentication failures and repeated scan failures produce one deduplicated actionable alert.
16. Add a repository-templated user-systemd oneshot service and matching hourly timer in Packet 8D. Use `OnCalendar=hourly`, `Persistent=true`, explicit bounded service resources/runtime/environment, no automatic restart loop, and the application lease as an overlap guard. The timer catches up once after downtime; it does not wake a suspended machine. Installation must show the rendered diff and require explicit Packet 8D/user authorization before enabling the live timer.
17. Treat real Chromium CDP availability as an explicit deployment precondition. Packet 8D may add a separately approved graphical-session launch/autostart integration using the user's existing Chromium executable and saved profile, bound to loopback only. It must detect an existing process/profile lock, never start a competing instance, never kill or restart the user's browser, and notify `BROWSER_RESTART_REQUIRED` when Chromium is already running without CDP. Timer execution itself must not launch a surprise second browser.
18. Add configurable retention separately for scan health, item metadata, reports, and screenshots. Default proposal: compact health 90 days, reports 90 days, screenshots 30 days; Packet 8A must confirm or revise this without deleting existing data. Retention runs only after a successful protected-store backup/index check, records what it removed, and never follows symlinks.

> **Retention decision (Packet 8A, 2026-08-27):** confirmed defaults — scan
> health 90 days, item metadata 90 days, report manifests 90 days,
> screenshots/evidence assets 30 days, notifications 90 days. Implemented as
> `voicebridge/communications/store.py::apply_retention` with dry-run + audited
> `retention_log`; the store deletes DB rows only (asset files are Packet 8C's
> concern) and Packet 8A deletes nothing at runtime.
19. Add deterministic fixture tests plus an opt-in personal-browser canary. Tests must cover baseline/no-flood, new versus unread, duplicate scans, edited previews, identical snippets in different threads, fallback-hash collisions, cursor/report crash boundaries, report-write and screenshot failure, per-account partial failure, selector drift, missing tab/browser, existing browser without CDP, profile lock/competing-launch prevention, logged-out/2FA/CAPTCHA, overflow, timeout, cancellation, concurrent timer/manual scan, restart/DST/catch-up, XSS/Markdown injection, malicious URLs, token redaction, path traversal/symlink assets, file modes, retention, notification dedupe/acknowledgement, and zero account-write or user-tab-mutating CDP commands.

Acceptance gate:

- a 100-run fixture replay and repeated manual/live rescan produce zero duplicate reports or notifications for the same item;
- the first live baseline creates no old-message alert, and a controlled newly received test item appears once after the next scan;
- `new`, unread, unknown read-state, stale cache, partial scan, authentication required, and no-change are distinct in JSON, report UI, events, and exit status;
- Gmail, Instagram, and Messenger each provide a stable source link and clipped screenshot for a controlled item, or a visible typed evidence reason; no unrelated conversation is present in the screenshot;
- a crash before bundle commit advances no cursor; a crash after atomic commit is recovered idempotently without a duplicate bundle or alert;
- a failed account does not block successful accounts and does not advance its own cursor;
- the canonical manifest validates; every HTML/Markdown link resolves; every asset hash/size/MIME matches; authenticated requests succeed and unauthenticated/traversal/symlink requests fail;
- the report UI shows a real saved bundle rather than an activity/log surrogate, and legacy Markdown remains accessible as labeled legacy data;
- project-managed report/monitor-state/cache directories are `0700`, report/state/assets are `0600`, and no secret or raw browser credential appears in Git, logs, events, manifests, URLs, notifications, or model prompts; the implementation never recursively changes permissions on Chromium's own profile tree;
- source-derived text is escaped, prompt instructions in messages cannot change policy or trigger tools, and the monitor contains no send/reply/mutation capability;
- one manual run and at least two real timer elapses prove no overlap, no user-tab focus/closure/navigation, bounded runtime/resource use, persistent catch-up semantics, notification latency under 60 seconds after commit, and quiet-hours behavior;
- no-change hourly runs create only bounded health records and no full-report/desktop/voice spam;
- all unit/contract/integration/UI tests pass; personal-browser and timer canaries are reported separately and cannot be replaced by fixture success.

Rollback: disable only the inbox timer/feature flag and restore the prior report API/UI route. Preserve cursors, report bundles, legacy reports, and evidence until the user explicitly authorizes deletion. Never roll back by clearing the Chromium profile, cookies, inbox state, or personal messages.

### Packet 8E — website integration onboarding and observability

This packet is the user-facing front door for future websites, desktop apps,
editors, and marketplace skills. Pasting a URL creates a pending, disabled
integration proposal; it does not make the page an instruction source, train
the model, grant permissions, or promise universal automation. A static page
may only produce bounded metadata and a proposed read-only capability set.
Authenticated or dynamic sites require a separately reviewed adapter and the
existing 7E manifest/permission contract before any live canary.

Dependency gate: Packet 7E must be independently verified. The implementation
may use Packet 8A schemas and Packet 8B's approved CDP boundary, but it must
not silently enable 8B/8C/8D or add a second browser, scheduler, router, or
permission store.

Required behavior:

1. Store a versioned website-integration record in the existing state boundary:
   stable integration ID, normalized origin (never credentials/query tokens),
   display name, source URL, discovery/adapter revision, manifest reference,
   status, requested and granted capabilities/scopes, auth requirement,
   sensitivity, timestamps, health, last error code, audit ID, and rollback /
   removal metadata. Persist atomically with private file modes and restart
   recovery.
2. Implement a bounded read-only discovery operation. Validate scheme and
   origin, enforce domain/scope policy, apply a deadline/byte/page limit, and
   record title, origin, reachable/auth-required state, supported observation
   hints, and a redacted capability proposal. Page text, metadata, model
   output, and tool results are untrusted evidence; none may become policy,
   permissions, executable code, or a durable lesson. No arbitrary page
   JavaScript, package hook, cookie import, profile cloning, form submission,
   or account mutation is allowed.
3. Use explicit typed lifecycle states: `PENDING`, `DISCOVERING`, `READY`,
   `AUTH_REQUIRED`, `DENIED`, `SELECTOR_DRIFT`, `DEGRADED`, `FAILED`,
   `DISABLED`, and `REMOVED`. Each transition writes a redacted audit event
   with timestamp, integration ID, operation, status, reason code, duration,
   bounded counts, adapter/manifest revision, and evidence availability.
   Logs must never contain cookies, tokens, full private page text, or secret
   URLs.
4. Add a CLI façade that compiles to the existing skill/permission backend:
   `websites add <url> [--name] [--dry-run]`, `websites list`,
   `websites show <id|url>`, `websites inspect <id|url>`,
   `websites logs <id|url> [--tail N]`, `websites check <id|url>`,
   `websites enable|disable|remove|rollback <id>`, and explicit
   `websites grant|revoke <id>`. Mutating commands require confirmation,
   selected scopes, expiry where applicable, audit output, and atomic rollback.
   Output must be machine-readable and human-readable with typed success,
   denied, unavailable, degraded, partial, and failed results. `--dry-run`
   must not contact an authenticated account or change state.
5. Add an authenticated dashboard Settings page with the same backend
   operations: Add Website, inspect/progress, proposed capabilities, current
   permissions, affected origin/app/workspace scopes, auth/health, redacted
   logs, enable/disable, grant/revoke, reset, and rollback/remove. Beginner
   mode uses plain language and Restricted-by-default; advanced mode exposes
   manifest provenance, hash/license, capability tier, sensitivity, scopes,
   approval, limits, and effective values. Every control displays the real
   typed backend result; no frontend-only toggle or optimistic success.
6. Separate discovery from implementation. “Learn what this site can do” means
   generating a reviewable proposal from bounded observations. Enabling a
   capability requires a valid 7E manifest, the smallest adapter or an honest
   launcher claim, synthetic fixtures, negative/security/resource/persistence
   tests, audit, and a staged canary. A launcher that only opens a URL cannot
   claim read, write, or app control. Existing-Chromium/CDP rules remain
   mandatory for logged-in sites; no Playwright personal profile is allowed.

Packet 8E acceptance gate:

- invalid schemes, credentials in URLs, out-of-scope origins, malformed or
  hash-mismatched manifests, unlicensed sources, disabled skills, and expired
  grants fail closed with typed output;
- adding a URL never grants access, runs page-provided instructions, changes
  model/voice/memory/policy, imports credentials, or creates a hidden skill;
- repeated inspect/check operations are bounded, idempotent, redacted, and
  auditable; failures remain visible and do not become empty successful data;
- CLI and dashboard results are parity-tested against the same backend, and
  restart/rollback/remove preserve or restore state without deleting unrelated
  reports, cursors, browser profiles, or user data;
- restricted is the default; unrestricted remains scoped and cannot bypass
  hard-deny actions, confirmation, validation, audit, isolation, or resource
  limits;
- synthetic fixtures cover static, authenticated, dynamic, selector-drift,
  timeout, denial, partial, malicious-content, and unavailable cases without
  private data; live Chromium is not required for packet verification;
- no live service, timer, browser account, model, audio path, or existing 8B
  adapter behavior changes. A live site adapter remains a separately approved
  packet.

Rollback: disable or remove only the 8E onboarding records, CLI/dashboard
routes, and generated proposal artifacts using their backups. Restore the prior
permission/skill store atomically; preserve unrelated memory, reports, cursors,
Chromium profiles, and user configuration. Never roll back by deleting browser
cookies or changing a live account.

### Packet 8F — controlled browser and desktop actions

Packet 8F is the action layer that may eventually make Qubit behave like a
local computer-use assistant. It is deliberately separate from the read-only
8B monitor and the 8E website discovery flow. It may implement typed actions
such as search, click, type, fill, draft, editor changes, and send only through
approved adapters and the existing capability broker. It must never become an
unrestricted “do anything” shell or browser controller.

Dependency gate: Packets 7E and 8E must be independently verified; the target
adapter must have a valid manifest, narrow scopes, and a tested read-only
counterpart. Packet 8F does not automatically enable any website, app, editor,
account, or destructive capability.

Required behavior:

1. Define typed action contracts with stable action ID/version, target adapter,
   origin/app/workspace/account scope, exact arguments, expected target/state,
   side-effect tier, data sensitivity, timeout/resource budget, idempotency
   key, preview, confirmation requirement, audit event, and rollback/recovery
   result. Reject free-form click scripts, arbitrary JavaScript, generated
   shell, hidden coordinates, and page-provided action instructions.
2. Route every action through the existing broker and 7E permission store.
   Restricted mode remains default. Unrestricted may authorize only explicitly
   selected routine non-destructive actions within selected scopes and expiry.
   Sending, publishing, purchasing, deleting, credential/account/security
   changes, external communication, and irreversible filesystem/system actions
   always require a fresh human confirmation immediately before execution.
3. Implement preview/dry-run as a real no-side-effect operation. Before an
   action, show the target, exact fields/recipients/content or file changes,
   permissions, risk, scope, and expiry. Confirmation must bind to a stable
   action hash and expire if arguments, target, page state, account, or scope
   changes. A model, page, skill, tool result, or generated plan cannot confirm
   for the user or widen permission.
4. Use existing logged-in Chromium through loopback CDP for browser actions and
   existing approved app/editor/terminal adapters for other surfaces. Do not
   clone profiles, import cookies, expose CDP publicly, activate/focus/close
   unrelated user tabs, or claim an app is controlled when the adapter only
   launches it. Detect stale targets, navigation/DOM changes, auth/2FA/CAPTCHA,
   confirmation expiry, and partial execution as typed failures; fail closed.
5. Add action-specific recovery. Prefer idempotent drafts and reversible edits;
   provide compensating rollback only when the remote system supports it; never
   claim rollback for an email/message already sent or an irreversible purchase
   or deletion. Persist an action receipt containing preconditions, action hash,
   result, audit ID, and evidence without storing secrets or unnecessary private
   content.
6. Expose action preview, approval, execute, cancel, receipt, retry, and
   rollback/recovery through the same authenticated CLI and dashboard. The
   interface must make read/draft/write/send/delete visibly different, display
   typed success/denied/expired/stale-target/partial/failed states, and never
   use optimistic frontend success. Voice requests must produce the same
   preview and confirmation path as text requests.

Packet 8F acceptance gate:

- synthetic tests prove default deny, manifest and scope validation, action
  schema validation, preview hash binding, confirmation expiry, cancellation,
  duplicate/idempotency handling, stale-target refusal, auth failure, partial
  execution, retry, receipt/audit redaction, and CLI/dashboard/voice parity;
- read, draft, reversible-write, external-send, destructive, and system-wide
  actions have separate capability IDs and metrics; no weighted aggregate can
  hide a safety failure;
- hostile page/model/skill text cannot create, modify, confirm, or reorder an
  action, and arbitrary JavaScript, free-form shell, coordinate-only actions,
  secret-bearing URLs, and unbounded loops are rejected;
- controlled fixtures cover Gmail/Instagram/Messenger, a generic website,
  VS Code/editor workspace, and a desktop launcher. Live account canaries are
  opt-in, redacted, separately verified, and never substituted by fixtures;
- all mutations are auditable, scoped, bounded, cancellable where possible,
  and honestly report when remote rollback is impossible. No user tab/account
  state is changed by tests unless explicitly authorized;
- no live service/timer/browser/account/model/audio/config changes occur during
  implementation or fixture verification. Enabling a real action adapter is a
  separate approved rollout step.

Rollback: disable the action capability and adapter atomically, revoke its
grants, and restore the prior manifest/permission snapshot. Preserve audit
receipts and unrelated memory/reports/cursors. Do not attempt to undo remote
messages, purchases, deletions, or account changes unless the adapter documents
and verifies a genuine compensating operation.

### Phase 8 production qualification packets (8G–8L)

Packets 8A–8F establish the contracts and hermetic behavior; they do not by
themselves prove that the installed bridge works with this machine's real
Chromium session, microphone, audio sink, desktop notification service, timer,
or restart behavior. The following packets are the explicit qualification
track. They are not permission to access an account or send anything: every
live packet requires a separate operator authorization, uses a disposable or
operator-controlled test item, and stops on any unexpected state.

Dependency order:

```text
8B + 7E + 8E ──> 8G live browser/CDP
audio/PTT foundations ──> 8H live audio and Right Ctrl
8D + 8H ──> 8I timer, notifications, and quiet hours
8G + 8H + 8I ──> 8J end-to-end recovery rehearsal
8J ──> 8K packaging, portability, security, and release certification
8K ──> 8K-P productized installation, model bootstrap, and desktop integration
8C + 8E ──> 8L UX, accessibility, mobile, and supportability
```

No packet starts another packet automatically. A packet is independently
verified only when its focused tests, affected tests, implementation diff, and
the explicitly authorized live gates all agree. A full-production verdict is
blocked until 8G–8K pass. A simple-install/private-source distribution also
requires 8K-P. A public-release verdict requires 8K-P and 8L, or a documented
product decision accepting the remaining limitations.

#### Packet 8G — real Chromium/CDP qualification

Qualify the existing user-launched Chromium through loopback CDP, including
target discovery, profile-lock handling, manual login, Gmail/Instagram/
Messenger read-only scans, bounded clipped screenshot bytes, auth/2FA/CAPTCHA,
selector drift, hidden/unavailable targets, reconnects, and per-platform
failure isolation. Prove that no target is focused, navigated, closed, or
mutated and that no competing profile or Playwright personal profile is
launched. Run a baseline, an operator-arranged external test item (never sent
by Qubit), and a no-change rescan for each approved platform; preserve
redacted evidence only.

#### Packet 8H — real audio, PTT, and Right Ctrl qualification

Qualify the documented microphone/source and HyperX output path, web push-to-
talk, Right Ctrl event capture, configured ASR, reasoning model, Qubit response,
Piper playback, cancel, latency, and recovery after a service/audio-device
restart or hot-unplug. Record the actual configured provider/model identity and
distinguish a real response from an unavailable-provider fallback. Verify the
user-session environment used by the service (DISPLAY or Wayland,
XDG_RUNTIME_DIR, and Pulse/PipeWire access) without hardcoding a machine-only
value; missing session context must be a typed failure with a recovery message.
Do not guess the highest X display in a multi-session environment. Resolve and
report the effective source/sink by stable device identity; a missing named pin
must be a visible `DEVICE_MISMATCH`/degraded state rather than silently passing
through an aggregate default. Exercise sample-rate/channel conversion, silence,
clipping, background noise, echo, VAD boundaries, actual audible playback, and
privacy cleanup of temporary audio.

Measure capture, ASR, reasoning, synthesis, playback, and end-to-end latency.
Stress concurrent web PTT and Right Ctrl turns, cancellation/barge-in, and
restart/hotplug races; prove there is no cross-turn answer, duplicate listener,
stuck key, restart storm, audio leak, or dashboard regression. Any automated
restart requires explicit authorization, bounded backoff, and a circuit breaker.
This packet may verify the current Piper path and sentiment controls, but must
not silently replace the working provider with Fish Audio or add a model/
training loop.

#### Packet 8I — real timer, notification, and quiet-hours qualification

Qualify the user-systemd oneshot/timer, overlap lease, bounded runtime,
no-change silence, one genuinely new-item notification, deduplication,
desktop-popup delivery, optional voice delivery, acknowledgement, quiet hours,
catch-up, timezone/DST behavior, and truthful typed failures. A temporary
isolated accelerated test unit may exercise failure paths, but cannot substitute
for observing the production-equivalent hourly schedule. Verify the correct
unit name, enable/disable/rollback, journal evidence, and that a timer
or notification never exposes report IDs, URLs, senders, subjects, bodies,
tokens, cookies, or secrets. Timer enablement remains an explicit operator
action.

Strictly validate quiet-hours syntax and hour/minute ranges and require a valid
IANA timezone. Invalid values must produce `CONFIG_INVALID` and suppress voice
delivery until repaired; they must not silently use host-local time or disable
quiet hours. Use monotonic elapsed time for deadlines and leases and wall time
only for schedules. Prove DST folds/gaps, clock jumps, suspend/resume, persistent
catch-up, notification retry/dead-letter bounds, and acknowledgement. The lease
must be renewable or exceed the maximum service runtime plus a safety margin,
and the aggregate state/exit code must become partial/degraded/failed whenever
any account or required delivery does. Resolve the real X11/Wayland notification
session at install/run time; do not ship a hardcoded `DISPLAY` value.

#### Packet 8J — end-to-end production rehearsal and recovery

Exercise an isolated staging state through bridge start, Chromium/CDP loss,
audio/ASR/TTS/model unavailability, network failure, timer overlap, process
crash, restart, stale lease, partial report, notification retry, state backup
restore, malformed/corrupt state, and rollback. Verify bounded memory/CPU/disk,
no duplicate reports or notifications, atomic cursors, retention, audit
continuity, and honest degraded recovery. Do not use destructive account
actions or claim remote rollback where none exists.

Inject deterministic failures before and after write, flush, fsync, rename,
lease acquisition, cursor commit, report commit, receipt commit, notification
send, backup, restore, and migration. Include `SIGKILL`, disk-full, read-only
state, truncated/malformed JSON/JSONL/SQLite, corrupt evidence bytes/hash,
partial or missing backups, stale temporary files, symlinks, a schema newer than
the runtime, and concurrent manual/timer/action access. A rollback that cannot
fully restore every touched path must be reported as a typed failure with the
exact bounded paths; it may never be swallowed or described as successful.
Validate restored schema and hashes before resuming, define measurable RPO/RTO,
and prove repeated recovery is idempotent.

#### Packet 8K — portability, packaging, security, and release certification

Prove a clean checkout can install, configure, upgrade, restart, and roll back
with documented dependencies, service units, permissions, secret handling,
state migration, uninstall behavior, and no hidden machine-specific paths. An
uninstall must preserve user state by default and require a separate explicit
confirmation for data deletion. Run dependency/license/provenance and secret
scans, filesystem-mode checks, resource ceilings, clean-clone tests,
release-artifact checks, and operator documentation checks. Confirm
intentional dirty files are not accidentally shipped and that the release can
be reproduced without the developer's private state.

Build both wheel and sdist, inspect their contents, install each into a clean
HOME outside the checkout, and run entry points/import/resource checks with no
`PYTHONPATH`, source-tree insertion, editable install, or repo-local virtualenv.
Record version/commit provenance, dependency constraints, licenses/notices,
SBOM, vulnerability and secret-scan results, artifact hashes/signatures, and a
reproducibility comparison. Test all documented Python/platform/display/audio
combinations or mark them unsupported before install. Rehearse upgrade from the
previous supported release, interrupted migration, newer-schema refusal,
rollback/downgrade behavior, and uninstall with state preservation. Validate
units with `systemd-analyze verify` and a security review, including exact unit
names, user-session environment, no home-directory literals, and bounded logs.

#### Packet 8K-P — productized installation and desktop integration

Make the application feel like one installable product without merging the
voice bridge and local SLM into one fragile process. A single supported
installer must provision or clearly report the runtime, model provider, pinned
model artifacts, configuration, permissions, service, dashboard pairing,
desktop shortcut, and optional tray controller. The bridge and inference
provider remain separate supervised components so either can restart or degrade
without creating duplicate listeners or losing state.

Resolve the current product identity split before adding convenience features:
the installer and packaged service currently use `qubit-voice-agent`, while
runtime defaults, existing user state, and several diagnostics use
`openclaw-voice-bridge`. Choose one canonical public identity, preserve the old
paths/unit through an explicit migration and compatibility alias, and prove
that a fresh install, upgrade, rollback, and existing installation cannot read
one config while writing another.

The installer must work from a release artifact rather than requiring a source
checkout or repository-local `.venv`. It must use atomic, resumable, hash-
verified model downloads with disk/RAM/VRAM preflight, clear offline and
missing-provider states, and no shell-piped remote code. Ollama may be managed
or replaced by an embedded/local inference runtime, but the user must not be
required to discover or manually install an undocumented dependency. Model
weights and runtime licenses must be recorded separately from application
code.

Provide a first-run wizard and a single health/repair command that explain
Python/runtime, GPU/CPU fallback, microphone/output, display/session,
inference-provider/model, dashboard token pairing, service state, storage, and
permissions in plain language. Do not silently select a different audio
device, model, account, browser profile, display, or service. Optional
Playwright/browser support must be explicitly marked optional and must not
download or launch a personal browser profile as a side effect.

Add a desktop shortcut that opens the authenticated local dashboard without a
token in the URL. Add an optional tray/status-notifier controller that talks
to the existing authenticated backend or user service; it must never start a
second bridge, duplicate a hotkey listener, expose tokens, or become required
on headless systems. Tray/shortcut absence must produce a typed unsupported or
unavailable state, not a failed core install.

Document and test supported Linux desktop/session combinations first. Keep all
live accounts, browser actions, model training, audio recording, and service
enablement opt-in. Treat this packet as required for a simple consumer
installation and private-source distribution; it is separate from Packet 8L's
visual/accessibility qualification.

The product must also have a portable defaults contract. Use platform-aware
per-user locations rather than repository paths, `/home/<user>` literals,
device indexes, or guessed application folders. Keep these categories separate
and discoverable in diagnostics: configuration, user data, runtime state,
downloaded model/cache data, logs, exports/backups, and installed resources.
On Linux use XDG locations; define equivalent adapters for macOS and Windows or
report those platforms as unsupported before installation. The installer must
create only the directories it owns, with documented permissions, and must
never scan or rewrite arbitrary user folders. A fresh install, upgrade,
multi-user install, interrupted install, uninstall, and rollback must resolve
the same paths deterministically.

Voice, apps, browser integrations, and optional tray features should be
capability profiles, not hidden side effects. Safe defaults may detect hardware
and installed applications, but must not silently select a microphone, speaker,
display, browser profile, account, or app permission. Each detected capability
needs a typed state and an explicit enable/repair action. An app adapter must be
disabled unless its contract, permissions, origin/account scope, and rollback
are known; adding a new app must not change the core voice path or create a
second listener.

The first-run flow must be transactional and resumable: acquire prerequisites,
write staged configuration, validate resources, install the service/shortcut,
pair the dashboard, and promote the installation only after every required
step succeeds. Interrupted or concurrent installers must recover through a
lock and journal, never leave a half-active service, and never report success
when only part of the product is usable. Diagnostics must show the effective
path map, provider/model, selected devices, session, service owner, enabled
capabilities, and exact next action without revealing secrets.

#### Packet 8L — public-release UX, accessibility, mobile, and supportability

Polish and qualify the real dashboard and notifications on narrow mobile and
desktop viewports: onboarding, settings, reports, activity, permissions,
websites, actions, empty/loading/degraded/error states, keyboard-only use,
screen-reader labels, reduced motion, contrast, focus order, touch targets,
plain-language notification copy, timezone/localization, export/delete/retention
controls, and a safe diagnostic bundle. Destructive delete/reset actions must
remain separately authenticated, confirmed, audited, and non-default. Prove
all controls use the shared authenticated backend and never expose secrets or
private page content.

Begin with a route/page/component inventory and a short product/design source
of truth. Consolidate only proven duplicates; preserve distinct user tasks.
Move navigation, buttons, fields, status badges, dialogs, authenticated API
calls, error handling, tokens, spacing, typography, color, and motion into
shared primitives. No page may bypass the shared authenticated API helper or
parse an unchecked/non-JSON response. Treat all backend/user/page text as
untrusted: render with safe DOM APIs or an audited sanitizer, never raw dynamic
HTML.

WCAG 2.2 AA is a release gate. Test automated accessibility plus manual
keyboard and screen-reader flows; visible focus, focus restoration/traps,
announced live states, 44-by-44 CSS-pixel touch targets, 320 CSS-pixel reflow,
200% zoom, forced colors/high contrast, and meaningful reduced-motion
alternatives. Test desktop and mobile browser matrices, session expiry/re-pair,
offline/reconnect/stale data, slow/empty/partial/failed states, bounded DOM and
history growth, and performance budgets. Add visual regressions for critical
states and plain-language onboarding/recovery steps without exposing technical
IDs in beginner mode.

### Phase 8 capability-expansion and release packets (8M–8Z)

Packets 8M–8Z are an optional capability expansion and release track after the release and
qualification track above. They make Qubit a more capable local application,
but they do not erase the existing broker, provenance, isolation, audit, or
operator-control contracts. “Full capability” means every supported capability
has a typed adapter and can be explicitly enabled; it does not mean that
untrusted model/page text becomes authority or that irreversible actions run
without approval.

Dependency order:

```text
8L + 0A licensing review ──> 8M semantic memory adapter
8L + 7E + 8F ──> 8N Ghost desktop/browser adapter
8M + 8N + 8K-P ──> 8O full-capability qualification and power profile
8O ──> 8P verified skill memory and controlled evaluation
8K-P + 8L + provider-governance review ──> 8Q frontier-provider gateway and routing
8Q + 8F + 8K-P ──> 8R supervised OpenCode coding adapter
8R ──> 8S Qubit VoiceBridge identity and release migration
8S ──> 8T one-click setup, health, inventory, and lifecycle dashboard
8T ──> 8U browser-first voice workspace
8U ──> 8V desktop application control
8U + 8V ──> 8W approved files and folders workspace
8W + 8R ──> 8X IDE-native engineering workspace
8U + 8V + 8W + 8X ──> 8Y unified task context and session continuity
8Y + 8T ──> 8Z capability adapter lifecycle and local extension catalog
```

The core installation must remain usable without either optional integration.

#### Packets 8U–8Z — whole-computer capability expansion

These packets close the gap between a voice bridge that can launch or qualify
selected integrations and a Qubit assistant that can work across an approved
browser, desktop application, file workspace, and IDE. They are planned work,
not evidence that those capabilities already exist. Every packet is independent,
must reuse the existing broker/policy/grant/audit/session contracts, and must
remain disabled until explicitly enabled and independently verified.

##### Packet 8U — browser-first voice workspace

Add a low-latency browser workspace using the existing microphone, ASR, TTS,
orchestrator, and capability broker. It may connect only to an existing,
user-launched Chromium/CDP or approved in-page bridge over authenticated
loopback. It should expose the current tab, selected text, bounded DOM and
accessibility state, clipped screenshots, visible page state, tab navigation,
back/forward, scroll, search, click, type, fill, and draft operations. Partial
ASR, streamed response/TTS, barge-in, reconnect, and typed auth-required,
CAPTCHA/2FA, selector-drift, unavailable, and degraded states are required.

Per-origin and per-account grants must separate read/navigation/fill/draft from
submit/send/publish/purchase/delete/account changes. Page, model, tool, and
clipboard text is untrusted and cannot create or widen a grant. Do not clone
profiles, import cookies, expose public CDP, launch a hidden browser, promise
universal website control, or automate CAPTCHA/2FA; pause with a clear manual
handoff. No second listener or browser authority is allowed.

##### Packet 8V — desktop application control

Add an opt-in desktop adapter for supported Linux sessions and declared apps,
using accessibility/semantic APIs and portals where available. It must bind to
the selected user/session/window and support bounded app roles such as launch,
focus, menu/button/text/table/dialog interaction, selection, and readback.
Coordinate-only or screenshot/OCR fallback must be visibly degraded, bounded,
and confirmation-gated. Support X11/Wayland discovery, multi-session isolation,
focus/cursor preservation, clipboard redaction, app crash/reconnect, and typed
unsupported/permission-denied states. No global keylogger, hidden listener,
arbitrary input injection, or unrestricted desktop executor is permitted.

##### Packet 8W — approved files and folders workspace

Add one brokered file workspace with explicitly approved roots, read/search/
preview, write, copy/move/rename, archive/trash/restore, diff, snapshot, and
undo contracts. Path traversal, unsafe symlinks, secret files, home-directory
wildcards, device files, quotas, disk-full/read-only errors, concurrent writes,
atomic persistence, and retention must be typed and tested. Deletes and broad
changes require exact previews and fresh confirmation. Model-generated paths or
repository instructions cannot expand roots or grant access. No whole-home
default, direct shell fallback, hidden file watcher, or raw file content in logs.

##### Packet 8X — IDE-native engineering workspace

Add declared VS Code/JetBrains/editor integration for current workspace, open
file, selection, diagnostics, tests, diffs, patch preview, and bounded test
runs. OpenCode remains an optional supervised executor, not a second authority.
Writes outside an approved disposable worktree require an exact diff/hash
preview and confirmation. Never auto-commit, push, publish, install packages,
change services, modify permissions, or inherit API keys, cookies, desktop
sessions, or the full environment. Repository instructions and generated code
are untrusted. Extension disconnect, malformed patch, cancellation, timeout,
crash/reap, concurrent runs, and partial failure must be recoverable.

##### Packet 8Y — unified task context and session continuity

Unify one task/session identity across voice, dashboard, CLI, browser, desktop,
files, and IDE. Provide an explicit context picker, source/provenance labels,
bounded summaries, redaction and consent for sensitive sources, pause/resume/
cancel/retry, progress, recovery, and durable receipts. Reuse the existing
session, journal, memory, and audit authorities; do not create a second memory,
router, scheduler, or audit store. Context switches must not leak data between
accounts, apps, projects, or sessions, and no page/model text may authorize an
action.

##### Packet 8Z — capability adapter lifecycle and local catalog

Add a versioned local adapter manifest and lifecycle surface for discover,
install, validate, enable, disable, upgrade, downgrade, rollback, and remove.
Each record must include source commit, license/notices, hash, dependencies,
supported OS/session, scopes, risk tier, health, resource limits, and rollback.
The dashboard and CLI must read the same backend state. Bad manifests, unknown
licenses, stale hashes, dependency conflicts, corrupt state, interrupted or
partial installs, and hidden listeners must fail closed. Start with a local
catalog; do not add an uncontrolled marketplace or permit plugins to bypass
the broker, grants, isolation, audit, or emergency stop.

Whole-computer acceptance for 8U–8Z means supported capabilities work through
the same policy path and have truthful disabled/available/enabled/live states,
not unrestricted access to every website, application, folder, account, or
credential. Routine low-risk work may use a narrow expiring owner grant;
external sends, publishing, purchases, deletions, account changes, and project
code changes still require fresh exact confirmation.
Each integration runs out-of-process or behind a clearly owned adapter, is
disabled until explicitly enabled, has a health/permission state, and can be
removed without deleting existing memory, reports, browser profiles, audio
configuration, or user data. A new integration must not create a second
listener, bypass the authenticated dashboard, or silently change the model,
browser, audio, service, or account configuration.

#### Packet 8M — GENOME-compatible semantic memory integration

Evaluate and, if justified, add an optional semantic-memory provider using the
GENOME contract: local embedding/retrieval, source provenance and trust levels,
temporal facts, explainable recall, deterministic journal/replay, and scoped
forget/export behavior. Prefer a narrow adapter to the existing MemoryStore;
do not replace the current bounded store or add a second uncontrolled memory
database. The default path remains local, bounded, auditable, and free of LLM
calls during ingest. Optional extraction, reranking, or model downloads must
be explicitly configured and reported.

Use a pinned GENOME commit and record its repository, exact source paths,
license, notices, dependency footprint, model license, and local divergence in
docs/UPSTREAM_REUSE.md before copying or adapting anything. GENOME is Apache-2.0
and its name/trademarks are separate from the code license. Do not copy the
whole repository or install its MCP server as an unauthenticated bypass.

Preserve current memory guarantees: tenant/scope isolation, secret-shaped
content rejection, provenance, confidence, expiration, correction, deletion,
retention, bounded prompt context, redaction, crash recovery, and no training
or self-modification. Prove that web/tool/model-origin text cannot overwrite a
user fact without the existing trust and approval rules. Add migration/export
compatibility, provider selection, disabled/missing-model/degraded states,
quality and resource benchmarks, and rollback tests. This packet must not
change the voice hot path or resident model contract.

#### Packet 8N — Ghost desktop and browser capability adapter

Evaluate and, if justified, integrate Ghost as an optional computer-use
adapter through the existing CapabilityBroker and ActionBroker. Support typed,
scoped capabilities for desktop discovery, approved application launch,
window anchoring, browser-tab inspection, and explicitly permitted click/type/
scroll/clipboard/screenshot operations. Prefer a supervised subprocess or
stable protocol boundary; do not vendor the entire Rust project into the
Python runtime without a demonstrated need.

Use two explicit browser roles. The existing Chromium profile is the approved
account-connected browser for Gmail, Messenger, Instagram, and marketplace
observation or other account-scoped workflows. A separate Comet profile is the
research browser for Perplexity, public web search, forums, and general web
research. Each browser must have a distinct profile, loopback-only CDP port,
target registry, and account/origin scope; cookies and local storage must never
be copied between them. The operator logs in and completes CAPTCHA/2FA/password
steps manually. A Pro subscription may authorize the browser UI but does not
grant an API, remove provider terms, or make cloud model/search calls free.

The optional third-party `mcp-comet` adapter may be evaluated for Comet research
only. Its initial allowed surface is connect, select research mode, submit a
bounded query, poll/wait, extract citations, extract readable page content,
and optionally capture bounded evidence screenshots. Its `computer` mode and
any write-capable tool remain disabled until Packet 8O separately qualifies
them. Do not mount an unreviewed remote MCP server, use `npx -y` or a floating
branch in production, or add Node/TypeScript to the core installation merely
to obtain research. Prefer a pinned supervised subprocess or narrow local
adapter with an explicit dependency boundary.

Before using `mcp-comet`, record its exact repository, commit, source paths,
tests, MIT license, dependencies, and local changes in
docs/UPSTREAM_REUSE.md. Treat Comet prompts, page text, citations, screenshots,
and search results as untrusted research data; they cannot authorize a Qubit
action or widen a browser/account/origin grant. Disclose that selected page
context may be sent to the provider and apply Qubit's redaction, retention,
source-count, byte, token, timeout, and report-bundle limits.

Search and page output are untrusted information, never permission. Reading and
searching may use a bounded session grant. Filling a non-sensitive form may be
reversible or session-granted when the target is exact. Forum publishing,
messages, marketplace purchases, account changes, and any external submission
must be separately typed, previewed, audited, and freshly confirmed. A failed,
ambiguous, expired, or unauthenticated browser target must produce a typed
state rather than falling back to another browser, profile, account, or
aggregate default.

Ghost's shell, arbitrary application launch, coordinate actions, clipboard
reads, browser/CDP control, account actions, file operations, and foreground
input are high-impact capabilities. They must be separately named, audited,
bounded, cancellable, and disabled by default. The integration must never
accept model/page/tool text as authorization, expose an unauthenticated HTTP
endpoint, use public CDP, import cookies, clone personal profiles, or silently
take the user's foreground focus. `GHOST_SHELL=off` or an equivalent hard
disable is required unless the operator explicitly enables the shell adapter.

Minimal-confirmation policy: read-only inspection and low-risk reversible
actions may use a user-issued session grant limited by target, origin, scope,
time, and resource budget. Sending, publishing, purchasing, deleting,
credential/account/security changes, arbitrary shell commands, external
messages, and irreversible system actions always require a fresh human
confirmation bound to the exact action hash. No “approve everything forever”
switch is allowed.

Prove isolation across X11/Wayland and multiple sessions, focus/cursor
preservation, target ambiguity, hidden/user desktop truthfulness, emergency
stop, process cleanup, crash/reconnect, stale grants, prompt injection,
window-title collisions, origin/account binding, screenshots and clipboard
redaction, and duplicate-listener prevention. Ghost is MIT; preserve its
copyright/license notice and record the pinned commit and adapted files before
implementation. Do not change existing read-only 8B/8G behavior or enable
live account actions as part of this packet.

#### Packet 8O — full-capability power profile and real qualification

Create the explicit power profile that exposes all installed, supported
capabilities in one authenticated dashboard/CLI view: memory, browser, desktop,
editor, file, network, shell, account, notifications, audio, and model
capabilities. Every capability must show owner, adapter version, scope,
target/origin, risk tier, current state, expiry, resource limits, audit status,
and disable/revoke controls. “Available” must never mean “authorized.”

The qualification matrix must include both browser roles: Chromium for
Gmail/Messenger/Instagram/marketplaces and Comet for Perplexity, web research,
forums, and public browsing. Verify independent CDP ports and profiles,
correct target discovery, account/origin binding, no cross-profile cookies,
manual login/2FA/CAPTCHA handoff, reconnect, session expiry, browser restart,
and no fallback to the wrong browser. Prove that a Comet search result or any
page text cannot authorize an action. Test read, fill, draft, post/send, and
purchase as separate capabilities. Reading, navigation, and exact-target
non-sensitive filling may receive a bounded session grant. Drafting is local
and non-external. Posting, sending, publishing, purchasing, and other external
submissions always require fresh approval.

If `mcp-comet` is used, qualify its research tools separately from Ghost and
from Qubit's native Chromium adapter: connect, mode, ask, wait/poll, source
extraction, page-content extraction, and bounded screenshot evidence. Prove
that the adapter cannot expose a second policy path, leak Comet context beyond
the configured provider, or turn research output into authorization.

Implement one policy path for voice, dashboard, CLI, MCP, tray, and automation
requests. If no executing tray controller or MCP server is shipped, the tray/
MCP surfaces report typed UNAVAILABLE/DISABLED (status-only) and are never
claimed to expose an execution path. Add session grants with narrow targets
and expiry, exact action
previews/hashes, emergency stop, global disable, per-adapter circuit breakers,
bounded concurrency, kill/reap behavior, redacted receipts, and recovery after
restart. Add an explicit owner-authorized power grant covering the operator's
selected browsers, apps, accounts, origins, directories, and duration. Within
that grant, routine browsing, searching, reading, navigation, and exact-target
non-sensitive form filling do not ask for repeated confirmation. Drafting
remains local and non-external. Posting, sending, publishing, purchasing,
deleting, and modifying project code always ask for fresh confirmation. The
grant must be visible, revocable, expiring, target-bound, and auditable; it may
not silently widen to a different browser, account, origin, directory, or
unbounded shell. The UI must make the owner grant and these confirmation
exceptions obvious in beginner and advanced modes.

Qualify the real supported hardware, desktop sessions, browser targets, model,
memory provider, and optional accounts only with explicit operator authorization
and disposable/controlled test data. Prove read, reversible, external-send,
destructive, shell, and account actions separately. Measure latency, CPU, RAM,
disk, network, model context, and screenshot/clipboard bounds. Test malformed
tool output, prompt injection, confused deputy, stale grants, replay, target
switching, process crash, adapter compromise, offline mode, rollback, and
uninstall. A live pass may not be inferred from a hermetic pass.

8O is not required to claim that the safe 8L release is complete. It is required
before claiming that Qubit has broad computer-use or “full control” capability.
If any adapter cannot meet the broker, audit, isolation, or emergency-stop
contract, report it as unavailable and keep it disabled.

#### Packet 8P — verified skill memory and controlled agent evaluation

Evaluate and, if justified, adapt the smallest tested concepts from Praxis and
Evolve. Praxis may contribute verified-before-remember skill records, negative
failure records, bounded retrieval, deduplication, retention, and acceptance
tests. Evolve may contribute offline experiment design, explicit feedback, and
bounded statistical comparison of prompt or model candidates. Neither project
may be copied wholesale or become a second authority, memory store, model
server, policy path, or listener.

Pin exact upstream commits and record repositories, source paths, tests,
licenses, notices, dependencies, and local changes in docs/UPSTREAM_REUSE.md
before production code. Preserve the upstream MIT or Apache-2.0 notices as
applicable. Do not use floating branches, undocumented generated code, or
unreviewed packages. If provenance, license compatibility, or a security
boundary is uncertain, stop with `BLOCKED — LICENSE REVIEW REQUIRED`.

Keep all changes optional and disabled by default. Verified skills must pass the
existing 7E manifest, provenance, scope, broker, resource, audit, redaction,
rollback, and emergency-stop contracts. Failed, expired, revoked, or stale
skills must be quarantined and must never become authority merely because an
LLM reports success. Retrieval must be top-K and context-budget bounded; it
must not train or self-modify the model.

Evolve-style evaluation must be offline or use explicitly configured local
Ollama by default. It may propose prompt, model, or behavior candidates, but it
must never automatically change permissions, confirmation policy, browser or
account scopes, safety rules, production model selection, or external actions.
Promotion requires explicit owner approval and passing regression, security,
latency, resource, provenance, and rollback gates. Do not store raw private
prompts, credentials, browser text, or source code in evaluation records, and
do not send them to an external challenger provider by default.

Add typed provider states, migration/export, deterministic replay or hashes,
correction/forget behavior, crash recovery, concurrent-access handling,
resource limits, redacted audit, clean-install checks, and rollback. Keep the
existing MemoryStore authoritative unless an explicit reversible migration is
verified. Do not change the voice hot path or resident-model runner contract.

#### Packet 8Q — frontier-provider gateway and intelligent model routing

Add an optional, provider-neutral gateway for difficult tasks that can route
between the local Ollama model, direct frontier providers, OpenCode Zen, and
OpenRouter. OpenCode Zen is an API provider/gateway, not the OpenCode coding
agent itself. Use the provider's documented API contract and current model
catalog; do not guess model names or depend on the OpenCode TUI. OpenRouter is
an optional aggregator and fallback layer, not a promise of free inference.
The current Zen documentation describes sign-in, billing details, and
per-request charges; product copy and defaults must never call it “free” or
assume a subscription includes unlimited API usage.

Keep `local` as the default route. Support explicit `local` and `frontier`
selection plus an opt-in `auto` route. Automatic escalation to a paid or
external provider must be disabled unless the owner enables it and sets a
provider, model allowlist, privacy tier, token limit, timeout, concurrency
limit, and spending budget. A route decision must never be based solely on
untrusted page/tool/model text, and uncertainty must fall back to local or a
typed refusal rather than silently exporting data.

Define one normalized provider contract for request, streaming, cancellation,
structured output, tool-call capability declaration, usage, cost estimate,
latency, retry policy, and typed failures. Provider capability differences
must be explicit: a model that cannot support a required schema, tool call,
context size, or streaming mode must not be selected as if it could. Catalog
and pricing metadata must be versioned or time-stamped; stale or unavailable
catalog data must be visible.

Store credentials only through an environment/keyring/OS-secret reference;
never in git, packaged config, the frontend, prompts, reports, telemetry, or
logs. Qubit must not read OpenCode's private auth database as an undocumented
integration. Use a separate Qubit credential reference or an explicit
supervised OpenCode login flow. Keys must be redacted in errors and memory.

Add a data-egress policy that classifies user, project, browser, account, and
secret content before a cloud request. Default cloud requests may contain only
the minimum sanitized task context. Private browser text, credentials,
cookies, tokens, raw audio, source trees, and memory records require an
explicit per-request or configured data policy. Show the selected provider,
model, privacy tier, estimated cost, and external-data warning before the first
frontier request. Never send a secret merely because a model asks for it.

Add bounded budgets for tokens, context bytes, response bytes, requests per
minute, concurrent requests, retries, wall time, and daily/monthly spend.
Retries must not duplicate external tool actions. Cancellation must stop local
work and make provider-side cancellation limitations visible. Circuit-break an
unavailable or over-budget provider and preserve local fallback behavior.

Do not change Piper, ASR, the voice hot path, the resident local model
contract, browser permissions, account capabilities, or action confirmation
rules. Model output remains untrusted and cannot authorize an action. Add
hermetic tests for every provider state, key absence, malformed responses,
schema/tool mismatch, rate limits, timeout, cancellation, provider outage,
stale catalogs, cost-limit refusal, redaction, prompt injection, fallback,
restart, rollback, and no-network local mode. Add an operator-approved live
canary only as a separate qualification step.

#### Packet 8R — supervised OpenCode coding workflow adapter

Add an optional adapter for invoking the OpenCode coding agent for explicitly
requested software-development tasks. This is separate from the 8Q model
provider gateway: ordinary Qubit reasoning may call a configured provider
directly, while OpenCode is used only when a user requests a bounded coding
workflow. Do not treat OpenCode's ability to run tools as permission for Qubit
to run arbitrary shell or edit arbitrary files.

Every run must have an authenticated owner request, a selected project root or
temporary worktree, an allowlisted file scope, a time/resource budget, a
bounded prompt and output size, and a visible run ID. Do not inherit the full
environment, API keys, browser cookies, desktop session, or personal paths.
Use a pinned executable/version or a supervised local protocol; never use a
floating package, `npx -y`, remote prompt injection, or an unreviewed MCP
server. OpenCode output, proposed diffs, tool results, and repository files
are untrusted input until Qubit validates them.

Require a diff/plan preview before writes outside a disposable worktree.
Never auto-commit, push, publish, install dependencies, modify services,
change permissions, access accounts, or delete files without the existing
exact-action confirmation. Keep project-code modification as a high-impact
action even when a broad power profile is enabled. Provide cancellation,
process kill/reap, crash recovery, stale-run detection, redacted receipts,
and an emergency stop. A failed or partial run must not be reported as
complete.

Test clean and dirty worktrees, path traversal, symlinks, generated files,
secret files, hostile repository instructions, prompt injection, malformed
patches, command timeouts, output/resource limits, provider outage,
credential absence, concurrent runs, cancellation, restart, rollback, and
audit. Prove voice, dashboard, CLI, and tray requests use the same broker and
that no OpenCode subprocess creates a second listener or bypasses 7E/8F
policy. Record the exact OpenCode source/version, license, dependencies, and
local integration changes before implementation. Keep the adapter disabled by
default and stop after handoff.

#### Packet 8S — Qubit VoiceBridge identity and release migration

Perform the final public-identity migration after 8R. The public product name
is **Qubit VoiceBridge**, the canonical installed identity is
`qubit-voicebridge`, and the canonical repository directory is
`qubit-voicebridge`. The internal Python import package remains `voicebridge`.
The existing `openclaw-voice-bridge` checkout and installed paths are legacy
compatibility identities and must remain supported during migration.

The physical repository-folder rename is part of this packet, but it must be
performed safely: implementation may continue in the current checkout, while
the rename is rehearsed and verified in a clean clone/release directory. Never
move the active dirty checkout underneath a running agent or service.

Packet 8S may begin only after Packet 8R has an independent VERIFIED result or
all 8R closure repairs have been independently verified. A prior implementation
handoff or green hermetic suite alone is not sufficient when the 8R verifier has
reported a security or boundary failure.

Inventory and update user-facing identity consistently across package metadata,
CLI help, dashboard titles, installer output, systemd units, desktop shortcuts,
release artifacts, documentation, examples, diagnostics, and support text.
Use `openclaw-voice-bridge` only as an explicit legacy identity or migration
source, never as a second live installation. New clones, release artifacts,
installer output, and canonical service/config/state/data paths must use
`qubit-voicebridge`.

Provide a reversible, idempotent, atomic migration from legacy configuration,
state, data, cache, logs, and service names to the canonical Qubit paths. A
legacy installation must either migrate completely or fail closed; the runtime
must never read one tree while writing another. Preserve a tested compatibility
alias for existing installations where practical, and never overwrite a real
legacy directory, follow an unsafe symlink, delete user data, or copy secrets
into logs, URLs, shortcuts, reports, or release metadata.

Test fresh install, canonical install, legacy-only install, mixed/split trees,
partial migration, interrupted migration, read-only and disk-full failures,
symlink/path traversal attempts, upgrade, downgrade/rollback, uninstall,
repeated migration, service restart, desktop shortcut launch, and state/data
preservation. Verify old configurations remain readable through the documented
compatibility path and that only one effective config/state tree exists.

Do not rename the `voicebridge` import package, change capability policy,
browser/account/audio/model/timer behavior, add a listener, or alter Packet 8R
execution boundaries. Do not add dependencies. Record every intentional legacy
reference and update licensing/release provenance documentation. Commit only
Packet 8S files and stop after the handoff.

#### Packet 8T — one-click setup, installation health, and lifecycle dashboard

Build a single authenticated, backend-authoritative setup and operations
surface for Qubit VoiceBridge after Packet 8S. It must make installation and
maintenance understandable to a beginner without hiding important decisions
from an advanced operator. Existing CLI, service, configuration, model, audio,
permission, release, and rollback contracts remain authoritative.

Provide a staged one-click setup flow that detects the host platform, validates
prerequisites, creates only standard per-user directories, installs or repairs
declared components, configures the local model/runtime, selects and tests
audio devices, installs optional desktop integration, and produces a resumable
health result. Every step needs typed pending/running/success/degraded/blocked/
failed states, bounded logs, dry-run, cancellation, and safe rollback. Network
downloads, service changes, desktop integration, provider enablement, and data
migration require explicit owner confirmation. Setup must never silently
elevate privileges, start a service, overwrite user configuration, delete state,
or copy secrets.

Add an authenticated installation-health dashboard and matching CLI output. It
must show actual installed version, package/source provenance, OS/session,
declared versus detected dependencies, model files and hashes, resident local
model status, provider/catalog status, safe credential-reference status, ASR,
TTS, microphone, output sink, browser/CDP, timer, permissions, storage, and
service state. API-key values, cookies, tokens, private paths, raw page text,
and raw audio must never be returned. The backend calculates every state; the
frontend renders it and never invents readiness or repair success.

Provide safe controls for credential references, voice/audio and model/provider
configuration, device tests, upgrade, rollback, missing-component repair,
redacted diagnostics, and documentation. Use dry-run previews and exact
confirmation for mutations. Preserve user data and tuned configuration by
default, never silently restart or enable services, and make partial repair or
rollback visible and resumable.

Ten additional improvements included in this packet:

1. Resumable first-run checklist with a clear next action and completion proof.
2. Redacted “copy diagnostics” bundle with schema, timestamps, states, and
   remediation hints but no secrets or private content.
3. Component provenance/update view showing version, source, license, hash,
   age, and whether an update is available.
4. Model capability matrix covering local/cloud availability, context,
   tool/structured-output support, resource needs, and truthful fallback.
5. Configuration profiles with preview, diff, validation, safe reset, import,
   export, and atomic rollback.
6. Microphone/output wizard with device identity, format conversion, latency,
   VAD checks, wrong-device guidance, and safe test tone/recording.
7. Offline-first degraded mode with cached status and reconnect backoff that
   distinguishes unavailable, disabled, misconfigured, and failed.
8. Repair planner that orders missing prerequisites without repeating successful
   work or creating duplicate services/listeners.
9. Migration/backup center with dry-run, snapshot health, schema checks,
   restore preview, retention, and separate data-deletion confirmation.
10. Local audit/change timeline showing actor, change, time, confirmation,
    reason, and rollback or recovery result.

Test desktop and mobile onboarding, keyboard/screen-reader access, 320-pixel
reflow, 200% zoom, forced colors, reduced motion, 44-pixel touch targets,
offline/reconnect, session expiry, stale health data, concurrent setup runs,
crash/resume, cancellation, read-only disks, disk-full, permission failures,
missing models, bad hashes, unavailable providers, wrong audio devices,
rollback, uninstall, and clean installation. Prove CLI/dashboard parity,
backend ownership of every state, redacted diagnostics, bounded DOM/logs/
resources, no duplicate route or listener, and no unapproved voice-hot-path
change. Record provenance for reused components. Keep optional cloud, browser,
timer, and tray features disabled unless explicitly enabled.

#### Known preflight findings that these packets must close

These are observed source/configuration risks, not optional polish:

1. **8H audio fallback:** a missing named audio pin can currently follow the
   aggregate default; qualification must expose the mismatch and actual route.
2. **8I lease/runtime mismatch:** the default monitor lease is 120 seconds but
   the oneshot permits 300 seconds; overlap safety must be corrected and tested.
3. **8I fail-open quiet hours:** malformed quiet-hours or timezone data can
   currently disable suppression or fall back to host-local time.
4. **8I session hardcoding:** the installed monitor template contains
   `DISPLAY=:1`; installation must derive or require an explicit validated user
   session instead of assuming this value.
5. **8I aggregate truthfulness:** an hourly run can return top-level `OK` while
   individual accounts are degraded; aggregate state and exit code must agree.
6. **8J rollback visibility:** restore rollback errors can currently be ignored;
   partial rollback must be visible and block resume.
7. **8K packaging isolation:** the clean-venv install check currently inserts
   the source checkout into `sys.path`, which can hide missing packaged files.
8. **8L frontend consistency:** reports/activity/approvals still duplicate raw
   fetch/JSON/error paths and some pages create dynamic `innerHTML`; migrate them
   to the shared authenticated, typed, safely-rendered UI layer.
9. **Product identity split:** `scripts/install.sh` and the packaged service use
   `qubit-voicebridge`, while `voicebridge/config.py`, existing user state,
   diagnostics, and older units use `openclaw-voice-bridge`; a fresh install can
   write one tree while runtime reads another.
10. **Installer dependency burden:** the current installer requires a source
    checkout, `uv`, `curl`, Ollama, native audio support, and manual desktop
    prerequisites; absent Ollama is warned about rather than provisioned, so a
    nominally successful install may not provide local chat.
11. **Model supply-chain gap:** model downloads use fixed temporary locations and
    direct downloads without a complete manifest/hash/atomic-promotion contract;
    interrupted or altered weights can become a confusing runtime failure.
12. **Artifact/runtime boundary:** release artifacts, source-checkout launchers,
    user-local overrides, personal knowledge, and packaged examples must have a
    single tested boundary; otherwise a wheel install can omit files that the
    repository installer expects or expose private Python implementation code.
13. **Service/session ambiguity:** the source launcher assumes a repository
    `.venv` and defaults a display, while user service names and X11/Wayland
    sessions vary; this can create duplicate services/listeners or a silent
    headless failure.
14. **Optional browser lifecycle:** Playwright is a declared dependency, but the
    supported read-only CDP flow uses an existing user browser; installation
    must not imply a browser binary/profile is available or launch one silently.
15. **Missing desktop control surface:** there is no supported first-run
    wizard, desktop shortcut, or tray/status controller that reports readiness,
    pairing, model, audio, and service failures in one place; users currently
    assemble these steps from separate commands and documents.
16. **Portable path contract:** configuration, state, data, models/cache, logs,
    exports, backups, and resources are not yet guaranteed to use deterministic
    per-user OS-aware locations across fresh install, upgrade, multi-user, and
    rollback flows.
17. **Capability-default ambiguity:** detecting an app or audio device can be
    mistaken for permission to use it; every capability needs an explicit
    disabled/available/enabled/blocked state and must not silently reroute or
    launch an account, browser, display, or listener.
18. **Installer transaction gap:** a failure halfway through dependency,
    model, service, shortcut, or pairing setup can leave a partially active
    installation; locking, journaling, resumability, and cleanup need a tested
    contract.
19. **Multi-user/session ambiguity:** per-user services, X11/Wayland sessions,
    permissions, and desktop integration must not cross users or assume one
    logged-in display.
20. **App-adapter growth risk:** future app integrations can widen the attack
    surface or introduce duplicate listeners unless adapters use a versioned,
    permissioned, opt-in capability registry with isolated failures.
21. **Frontier-provider absence:** the current local model path has no governed
    direct-provider registry, OpenCode Zen adapter, or OpenRouter adapter;
    adding ad-hoc HTTP calls would bypass routing, budgets, redaction, and
    typed failures.
22. **Cloud-egress ambiguity:** there is no single tested classification for
    what user, browser, project, memory, or secret content may leave the
    machine; a frontier route could otherwise export private context silently.
23. **Credential-boundary risk:** provider keys must not enter `voice.yaml`,
    frontend state, prompts, logs, artifacts, or OpenCode's private auth store
    through undocumented reads; each provider needs an OS-secret reference and
    redaction test.
24. **Capability mismatch:** providers differ in structured output, tool
    calling, context, streaming, cancellation, pricing, and retention; model
    selection needs a current capability/catalog contract and must fail closed
    when metadata is stale.
25. **Budget/retry risk:** cloud retries, fallbacks, streaming reconnects, and
    automatic escalation can multiply cost or duplicate work without token,
    spend, concurrency, and idempotency limits.
26. **Provider-outage ambiguity:** a failed frontier request must expose whether
    it timed out, was rate-limited, rejected, unavailable, or fell back to the
    local model; a local answer must never be presented as the selected cloud
    model's answer.
27. **OpenCode execution boundary:** invoking a coding agent with inherited
    environment, repository instructions, or broad workspace access could
    expose secrets or bypass the ActionBroker; it needs a supervised worktree,
    diff preview, process limits, and high-impact confirmation.
28. **Model-catalog drift:** hardcoded provider/model IDs, endpoints, pricing,
    or feature assumptions will become stale; catalog snapshots need source,
    timestamp, validation, and an unavailable state.
29. **Data-retention/terms gap:** each external provider's retention, training,
    regional-processing, and acceptable-use terms must be recorded and shown
    before enabling a cloud route; OpenRouter fallback/provider selection can
    change the actual processor.
30. **Release reproducibility gap:** provider adapters, SDK versions, CA/TLS
    behavior, and model metadata must be pinned or constrained and exercised
    in clean installs without making cloud access required for local use.
31. **Provider-claim drift:** Zen/OpenRouter pricing, model availability,
    endpoint behavior, and terms can change; Qubit must display “availability
    and price subject to provider account” and fail safely when metadata is
    stale instead of promising free or unlimited access.

#### Non-blocking nice-to-have backlog

These may be implemented only after every required gate above is green. They
must remain opt-in, bounded, private by default, and may not delay a safety fix:

1. A guided microphone/speaker/VAD calibration wizard with a local-only meter.
2. A compact PTT waveform/level indicator with reduced-motion alternative.
3. A unified notification center with filter, acknowledge, and retry context.
4. Exportable signed diagnostic bundles with a preview of every included field.
5. Additional UI languages and locale-aware dates after English copy stabilizes.
6. A high-contrast theme and system-theme synchronization.
7. An installable PWA shell, but no offline execution of privileged actions.
8. A staged update notifier showing version, provenance, rollback, and restart.
9. Local performance/reliability trend charts with bounded retention.
10. Optional voice-style presets using the existing provider and resource
    envelope; no emotion inference, cloning, training, or self-modification.

#### Ten additional release requirements

The following are cross-cutting requirements, not excuses to widen scope:

1. **Real-device matrix:** document supported Linux/display/audio/browser
   combinations and a clear unsupported-state message.
2. **Resource envelope:** record CPU, RAM, VRAM, disk, network, and latency
   ceilings under idle, normal, and bounded-load conditions.
3. **Crash/restart safety:** prove leases, cursors, confirmations, receipts,
   notifications, and temporary files recover without duplication or loss.
4. **Clock correctness:** test timezone, DST transitions, clock skew, missed
   runs, catch-up, and quiet-hours boundaries.
5. **Privacy lifecycle:** verify redaction at capture, storage, logs, export,
   backup, retention expiry, deletion, and support diagnostics.
6. **Upgrade/rollback:** test additive migrations, interrupted upgrades,
   downgrade refusal or safety, backup restoration, and uninstall cleanup.
7. **Observability:** provide operator-readable health, correlation IDs only in
   technical views, actionable logs, bounded log rotation, and no secret leaks.
8. **Accessibility and mobile:** treat keyboard, screen reader, reduced motion,
   contrast, touch size, and narrow layout as release gates, not decoration.
9. **Dependency and provenance:** pin or constrain dependencies, record source
   revisions/licenses/hashes, and reproduce the release from a clean checkout.
10. **Operational runbooks:** document install, login/authorization, normal
    use, recovery, disable, rollback, data deletion, and escalation paths in
    plain language.

### Productization acceptance checklist after Packet 8K

Before Packet 8L, the verifier must additionally prove:

1. A clean artifact install creates the documented per-user directory tree and
   no source-checkout or repository-local runtime dependency.
2. The same effective path map is reported before and after restart, upgrade,
   rollback, and uninstall; legacy paths are migrated or explicitly aliased.
3. OS/session detection is read-only and typed; unsupported combinations stop
   with guidance rather than guessing a device, display, account, or browser.
4. Every app/voice/browser capability is disabled or explicitly enabled, with
   its permissions, scope, owner, and rollback visible to the user.
5. Two concurrent installers and an interrupted installer converge to one
   valid installation without duplicate services, listeners, or downloads.
6. A missing optional provider, model, app, tray, display, or audio device
   cannot make the installer claim that the complete product is ready.
7. Backup, restore, update, downgrade, and uninstall preserve user data by
   default and never copy credentials, browser profiles, tokens, or raw audio.
8. The installer, desktop file, service, logs, diagnostics, and support bundle
   contain no secrets, transient tokens, private absolute paths, or shell-piped
   remote execution.

Packet 8G–8K implementation must remain additive and must not enable live
services during coding or hermetic verification. The operator performs the
machine-specific live steps after the implementation handoff; an independent
verifier then repeats or audits those steps before the release verdict.

### 5.1 Phase packet and verification protocol

Each lettered packet is independently implemented and verified. An implementation agent may work only on the current packet and may not begin the next packet or parent phase. The packet ends with a mandatory handoff and a hard stop. A parent phase passes only after every packet below is independently verified and the parent acceptance gate is rerun.

| Packet | Bounded objective |
|---|---|
| 0A | Inventory and reconcile config/docs/deployment/upstream claims; produce the mismatch report and upstream reuse record without changing runtime behavior |
| 0B | Implement both config-doctor modes, environment override fix, frontend-path fix, deployment/dependency contracts, tests, and migrations |
| 1A | Capability registry, policy decisions, approval IDs, and centralized redaction with pure unit/negative tests |
| 1B | Wrap all OpenClaw/Hermes paths, secure dashboard authentication/CORS, adapt the static HITL UI, and run bypass/integration tests |
| 2A | Adapt pinned AG-UI event types/encoder/fixtures and create the append-only ordered journal |
| 2B | Add SSE replay, snapshots/deltas, restart-safe approvals/cancellation, dashboard consumption, and concurrency tests |
| 3A | Put existing trusted host tools and worker adapters behind the broker; add structured argv, schemas, preview, cancellation, and audit |
| 3B | Add the OpenSandbox-derived Bubblewrap probe/lifecycle, separate systemd transient units, no-network untrusted profile, scope limits, and hostile tests |
| 4A | Build the fixed private corpus and benchmark harness; reproduce runner reloads and cold/warm route/chat/summary transitions; change no defaults |
| 4B | Unify the Ollama runner contract, remove baked private/stale prompt data, implement context budgets and typed compact decisions, and prove zero option-driven reloads |
| 4C | Implement/evaluate deterministic L0 plus optional CPU semantic L1 with pinned Semantic Router behavior; enable L1 only if latency, quality, and runner-placement gates pass |
| 4D | Bake off pinned 4B candidates, add the bounded typed L2 planner/tool shortlist, select/alias the winner, and implement VRAM guard, fallback, eviction/restore, and optional isolated deep-tier experiment |
| 5A | Define normalized read-only evidence/adapter contracts, permissions, scope, provenance, freshness, citations, and strict read/write/remember separation |
| 5B | Implement SQLite/FTS5 personal memory, explicit remember/correct/forget/export/purge, history/provenance, and Mem0/Letta-derived contract tests |
| 5C | Implement the token-budgeted context compiler, optional approved semantic reranking, bounded parallel reads, cache TTL, redaction, partial failures, and injection tests |
| 6A | Add lesson provenance, conflict handling, decay, correction priority, and user removal |
| 6B | Add the private evaluation set, poisoning tests, regression gate, and outcome records |
| 7A | Separate test/monitor commands, add clean-install/deployment tests and health checks, and prohibit implicit restarts |
| 7B | Add feature flags, rollback drills, smoke coverage, change reports, and final documentation/runtime reconciliation |
| 7C | Package the verified local application/model acquisition path, hardware doctor, encrypted portable-state export/import, trusted-device enrollment, and clean-install/upgrade/rollback matrix |
| 7D | Add beginner/advanced customization, replaceable model/STT/TTS/voice profiles, dashboard/CLI parity, and the separately verified Qubit branding migration |
| 7E | Add the versioned skill/capability registry, restricted/unrestricted permission profiles, dashboard/CLI parity, scoped grants, expiry/revoke/reset, and the tested extension contract for new websites/apps/editors |
| 8A | Inventory the real Chromium CDP/report paths; define scan/item/cursor/report/asset/notification schemas, SQLite migration, retention decision, sanitized fixtures, and failure semantics without reading private bodies into fixtures or changing live behavior |
| 8B | Implement repository-owned read-only real-Chromium CDP adapters, stable new-item detection, cursor transactions, bounded scans, clipped screenshot evidence, and manual-scan tests; do not enable scheduling |
| 8C | Implement atomic JSON/Markdown/HTML bundles, authenticated index/detail/asset APIs, legacy report indexing, static reports UI, escaping/redaction, acknowledgement, export/print, and visual/link/security tests |
| 8D | Implement dashboard/desktop/optional-voice notification policy, repository user-systemd oneshot/timer, overlap/resource limits, retention, rollback, opt-in personal-browser canary, and explicitly authorized live enablement |
| 8E | Add the non-live website/app/editor onboarding façade: URL validation, bounded discovery/proposal, versioned integration records, redacted typed logs, CLI/dashboard parity, 7E permission/manifest linkage, tests, and rollback; do not enable arbitrary site control |
| 8F | Add typed, brokered, confirmation-gated browser/desktop/editor actions with previews, receipts, cancellation, recovery, CLI/dashboard/voice parity, and separate live canaries; do not add unrestricted automation |
| 8G | Qualify the real user-launched Chromium/CDP read-only browser path and three approved platform canaries without mutating accounts or tabs |
| 8H | Qualify the real microphone, ASR, Piper playback, push-to-talk, Right Ctrl, latency, hotplug, and audio/service recovery paths |
| 8I | Qualify the real user timer, overlap protection, no-change silence, notifications, quiet hours, catch-up, timezone/DST, and rollback paths |
| 8J | Rehearse isolated end-to-end failure, crash, restart, backup/restore, retention, deduplication, and degraded recovery behavior |
| 8K | Certify clean-install portability, upgrade/rollback, packaging, provenance, privacy, permissions, resource ceilings, and release artifacts |
| 8K-P | Productize installation, model/runtime bootstrap, identity/path migration, first-run health, desktop shortcut, optional tray control, and private-source distribution |
| 8L | Qualify public-release UX, accessibility, responsive/mobile layouts, plain-language errors/notifications, data lifecycle, and support diagnostics |
| 8M | Integrate optional GENOME-compatible semantic memory without replacing the authoritative MemoryStore |
| 8N | Integrate optional supervised Ghost desktop/browser capabilities and research-only Comet access |
| 8O | Qualify the explicit power profile and real cross-capability/browser/hardware behavior |
| 8P | Add verified skill memory and controlled Praxis/Evolve-style evaluation |
| 8Q | Add optional direct frontier-provider, OpenCode Zen, and OpenRouter routing with egress, cost, and capability controls |
| 8R | Add an optional supervised OpenCode coding workflow with worktree, diff, process, and confirmation boundaries |
| 8S | Migrate the public product identity to Qubit VoiceBridge, rename the canonical repository folder to `qubit-voicebridge`, and provide reversible legacy-path compatibility plus release/upgrade/rollback coverage |
| 8T | Add one-click setup, backend-authoritative installation health, component/model/provider/audio inventory, safe credential-reference status, repair, upgrade/rollback controls, and mobile onboarding |
| 8U | Add the browser-first voice workspace over approved user-launched Chromium/CDP or an authenticated in-page bridge, with bounded context, streaming, barge-in, and origin/account grants |
| 8V | Add opt-in semantic desktop application control for declared Linux apps and sessions with X11/Wayland isolation, accessibility APIs, and degraded fallback states |
| 8W | Add the approved-root brokered files/folders workspace with previews, diffs, snapshots, restore, quotas, symlink/path traversal defense, and typed recovery |
| 8X | Add the bounded IDE-native workspace for context, diagnostics, tests, diffs, and supervised OpenCode patches in approved worktrees |
| 8Y | Unify task/session context across voice, dashboard, CLI, browser, desktop, files, and IDE without adding a second memory, router, scheduler, or audit authority |
| 8Z | Add the local provenance/licence-aware capability adapter catalog and lifecycle for validate, enable, disable, upgrade, rollback, and removal |

#### Packet 8S closure addendum

Packet 8S is not accepted while any runtime caller still treats
`openclaw-voice-bridge` as its default writable tree. In particular,
`voicebridge/config.py` and every caller of its default config/state/data
constants must resolve the canonical XDG `qubit-voicebridge` paths, while
legacy paths are read only through an explicit, validated compatibility
alias. Empty `VOICE_BRIDGE_CONFIG` values must fail closed or fall back to the
documented default; they must never resolve to the current directory.

The physical repository-folder requirement is also separate from a staged
copy/rename rehearsal. The packet is not VERIFIED until a clean clone or
release directory named `qubit-voicebridge` builds, installs, launches, and
resolves packaged resources, and the migration/rollback contract explicitly
documents the safe operator step for an existing `openclaw-voice-bridge`
checkout. Branding helpers must not claim that a physical folder migration
occurred when they only rewrite files. Rollback must use the effective XDG
path map rather than hardcoded legacy paths, reject unsafe symlinks, and
report partial restore failures.

If a packet still exceeds the 300-line/dependency review threshold, stop and ask the user to authorize a smaller packet split. An agent may not silently widen a packet.

The workflow is:

```text
User authorizes Packet N.A
        |
Implementing agent performs fresh preflight
        |
Agent changes only Packet N.A
        |
Agent runs its phase tests and writes a handoff
        |
STOP — do not start the next packet
        |
User asks: "Verify Packet N.A"
        |
Verifier independently inspects, tests, and reports
        |
User approves Packet N.A or sends it back for repair
```

The implementing agent is never the final verifier. A green claim, screenshot, or verbal “done” is not acceptance. The verifier must inspect the actual diff and rerun the relevant checks independently.

Every phase handoff must use this template:

```text
PHASE PACKET: <number, letter, and name>
STATUS: INCOMPLETE | BLOCKED | DEGRADED | FAILED
OBJECTIVE:
FILES CHANGED:
BEHAVIOR CHANGED:
COMMANDS RUN:
TEST RESULTS: passed / failed / skipped / degraded
BENCHMARKS: latency / CPU / GPU / VRAM / disk, when relevant
SECURITY CHECKS:
DOCUMENTATION UPDATED:
KNOWN RISKS:
UNTESTED PATHS:
ROLLBACK:
NEXT SAFE ACTION: wait for independent verification
```

The verifier must:

1. Read `AGENTS.md` and this plan again.
2. Run `git status --short` and inspect the complete diff; do not trust a summary.
3. Confirm that only the phase scope changed and unrelated user work was preserved.
4. Re-run the phase’s required tests, including negative and degraded cases.
5. Inspect the relevant runtime/config state without exposing secrets.
6. Check documentation, Graft freshness, logs, resource limits, and rollback behavior.
7. For upstream-derived work, open the recorded upstream files at the pinned commit, compare the local adaptation and tests, and verify license/notice compliance.
8. Check that the implementation uses only the verified stack or contains explicit dependency/deployment evidence for a change.
9. Mark the phase only as `VERIFIED`, `REJECTED`, or `VERIFIED WITH EXPLICIT LIMITATION`.
10. Never authorize the next phase while any required acceptance item is failing or untested.

`VERIFIED`, `REJECTED`, and `VERIFIED WITH EXPLICIT LIMITATION` are verifier outcomes only. They do not replace the implementation handoff statuses `INCOMPLETE`, `BLOCKED`, `DEGRADED`, and `FAILED`.

The user’s verification request should be explicit, for example: `Verify Packet 0A`. Until that request is made and the result is accepted, the next implementation agent must wait. After all packets for a parent phase pass, the verifier reruns the parent acceptance gate before reporting that the phase is ready for user approval.

### 5.2 Phase-specific verification gates

| Phase | Independent verification must prove | Required decision |
|---|---|---|
| 0 — Source of truth | Effective config, repo examples, docs, declared dependencies, installed/repository systemd units, frontend path, model names/residency, audio rules, environment overrides, and both doctor modes agree; no live config or service was silently overwritten | Reject any stale/conflicting source, hardcoded home path, undeclared import, or secret exposure |
| 1 — Security/policy | Unauthorized dashboard calls fail; approval IDs are exact, expiring, and single-use; redaction works; denied tools cannot fall back to shell; browser personal/isolated profiles stay separate; CopilotKit behavior was adapted without importing its runtime | Reject any bypass, open LAN API, missing redaction, or unnecessary frontend/runtime framework |
| 2 — Events/state | Event sequence is ordered and replayable; reconnect has no gaps/duplicates; concurrent requests stay isolated; cancellation and approval resume survive restart; pinned AG-UI fixtures pass on the existing static UI/stdlib server stack | Reject polling-only claims, incompatible wire output, ambiguous completion, or a React/FastAPI rewrite |
| 3 — Capability broker | Every tool has schema, side-effect tier, scope, timeout, cancellation, audit event, and tests; unknown tools fail closed; generated shell cannot escape scope; the OpenSandbox-derived probe/lifecycle tests pass with Bubblewrap plus a separate bounded systemd unit | Reject any direct unrestricted worker execution, symlink escape, bridge-cgroup workload, or Docker/Kubernetes/VM addition |
| 4 — Models/resources | Fixed-corpus cold/warm and transition benchmarks are reproducible; classifier/chat/summary/planner share one runner contract with zero option-driven reloads; L0/L1/L2 class metrics, schema/tool accuracy, personal QA, first audio/full-turn latency, residency, VRAM/CPU, fallback, and rollback are proven; model artifacts contain no private profile; AirLLM/new servers remain outside production | Reject “faster/smarter” claims without per-class numbers, any hidden reload, model selection without a held-out corpus, unsafe weighted averages, baked private data, random fine-tunes, or production Torch/Transformers/SGLang/vLLM additions |
| 5 — Information fabric | Sources have scope, permissions, freshness, provenance, citations, bounded output, and read/write separation; SQLite/FTS5 memory supports explicit add/correct/forget/export/purge and history; context compilation stays in budget; partial failures and retrieval injection are visible/contained | Reject uncited or silently stale/private/cross-scope data, raw-history injection, autonomous secret memory, hidden writes, a new database service, or prompts that can overflow the selected context |
| 6 — Learning/quality | Corrections override stale lessons; preferences have provenance and removal; evaluation score does not regress; prompt injection cannot become memory | Reject automatic self-modification without evaluation |
| 7 — Operations and permissions | Unit/contract/integration/live/monitoring commands are separate; no watchdog runs in test collection; health checks and rollback work; docs match runtime; Restricted/Unrestricted permission profiles, scoped grants, and skill manifests are enforced through CLI and dashboard | Reject a release with hidden skipped tests, untested rollback, implicit authority, frontend-only permission state, or an unrestricted mode that bypasses hard safety gates |
| 8 — Communications/reports | Real logged-in Chromium CDP is used without Playwright-owned account profiles or user-tab mutation; new-item cursors/report commits are atomic and idempotent; reports are real private bundles with links/screenshots/provenance; APIs/assets are authenticated; hourly timer/notifications/retention are bounded and verified separately | Reject snapshot/unread-as-new logic, stale-cache success, external `$HOME/.hermes` runtime dependencies, missing screenshots without typed reason, activity logs mislabeled as reports, permissive private-file modes, any send/reply capability, or live timer enablement before Packet 8D approval |

After verification, the verifier must report:

```text
PHASE VERIFICATION: VERIFIED | REJECTED | VERIFIED WITH EXPLICIT LIMITATION
EVIDENCE:
FAILURES:
UNTESTED:
REQUIRED REPAIRS:
NEXT PHASE AUTHORIZATION: NOT GRANTED / READY FOR USER APPROVAL
```

If rejected, repair the same phase. Do not “work around” a failed gate in a later phase. If a limitation is accepted, record it in this plan and the relevant docs before proceeding.

## 6. Mandatory rules for every implementation agent

### 6.1 Mandatory preflight: every new task, every time

An agent must complete this preflight before it searches, edits, executes commands that change state, or proposes that implementation has started. “I already read it earlier” is not an exception: a new task, resumed task, new phase, or changed objective requires a new preflight.

The agent must:

1. Read `AGENTS.md` completely.
2. Read `docs/UNIFIED_AGENT_PLAN.md` completely, then identify the single lettered packet, its bounded objective, and its parent-phase acceptance gate.
3. Read the relevant source-of-truth docs. For audio, mic, VAD, playback, or TTS this always includes `docs/AUDIO_SETUP.md`. For upstream-derived work, read the relevant section of `docs/STEAL_LIST.md` and `docs/VOICE_BRIDGE_SYSTEM.md`. For inbox/report work, also read the complete Phase 8 contract, inspect the real Chromium CDP/report API shapes without printing message contents, and record current private-store permissions and service/timer state.
4. Run `git status --short`. Record unrelated modifications and do not overwrite them.
5. Run `graft ask "<exact task>" --source`. Use the returned `covers:` spans. Use `graft callers <symbol>` before changing shared behavior.
6. Inspect the effective configuration path and runtime state. Do not assume that repository YAML, the user YAML, systemd, Ollama, the gateway, or the browser are in agreement.
   - For model/router/memory work, also record `ollama ps`, model digest/Modelfile/options for every affected caller, current free VRAM, SQLite capabilities, and whether the task may download or load a model.
   - For communications/report work, record Chromium/CDP availability and version, target origins without titles/content, adapter health, timer enabled/active state, report format counts, file/directory modes, dashboard auth mode, and whether the task is fixture-only, manual live-read, or authorized live activation. Never put account names, senders, subjects, message text, cookies, or target WebSocket URLs in the task log.
7. Write a short task capsule before coding:
   - objective and non-goals;
   - files/symbols allowed to change;
   - risks and affected callers;
   - tests and measurements required;
   - rollback method;
   - explicit definition of done.
8. State the preflight result in the agent handoff or task log. If any preflight item cannot be completed, the agent must stop and report `BLOCKED — PREFLIGHT INCOMPLETE`.

No code change is valid if this preflight is missing.

### 6.2 Mandatory upstream reuse and anti-overengineering workflow

Before implementing any event protocol, approval flow, sandbox lifecycle, resource scheduler, agent adapter, model-loading behavior, browser observer, scheduled monitor, or report asset contract, the agent must inspect the corresponding pinned upstream source and tests in section 4.1.

The agent must write an upstream reuse record before production code:

```text
LOCAL REQUIREMENT:
UPSTREAM REPOSITORY + COMMIT:
SOURCE FILES + SYMBOLS:
UPSTREAM TESTS/FIXTURES:
LICENSE:
DECISION: COPY | ADAPT | WRAP | REJECT
LOCAL DESTINATION:
DEPENDENCIES ADDED:
BEHAVIOR KEPT:
BEHAVIOR INTENTIONALLY OMITTED:
WHY THIS IS THE SMALLEST FIT:
```

The decision order is mandatory:

1. `COPY` a small self-contained unit when language, dependencies, and architecture already match.
2. `ADAPT` the public contract plus its tests when the upstream implementation depends on a different framework or language.
3. `WRAP` an existing local capability when it already provides the required behavior.
4. `REJECT` the upstream component when its runtime/dependency/resource cost exceeds its measured benefit.

Additional rules:

- Source and tests outrank blog posts, marketing pages, screenshots, and remembered behavior.
- Copy or adapt upstream tests/fixtures before writing the implementation. A locally invented happy-path test is not a substitute for upstream conformance.
- Never copy from `main` without recording the commit hash. Never leave a floating raw URL as the only provenance.
- Never copy a whole directory, generated SDK, framework, runtime server, vendored dependency tree, lockfile, or build system for one behavior.
- Never translate Go/TypeScript/React internals line by line into Python. Preserve the external contract and tested state transitions in the smallest native implementation.
- Do not add a new service, database, queue, frontend framework, container runtime, or model server if the existing Python process, JSONL journal, static UI, systemd, Bubblewrap, Ollama, OpenClaw, or Hermes boundary can satisfy the requirement.
- If an upstream-derived component exceeds 300 new production lines or adds more than one direct runtime dependency, stop and report `BLOCKED — ARCHITECTURE REVIEW REQUIRED` with the smaller alternatives considered.
- Copied code must retain required notices. Substantial copied/adapted code without `docs/UPSTREAM_REUSE.md` provenance and `THIRD_PARTY_NOTICES.md` compliance cannot pass verification.
- If upstream behavior conflicts with this project's safety, hardware, or latency constraints, keep the local constraint and document the divergence explicitly.
- For model sources, pin both the base-model revision and the exact quantized artifact revision/hash. A model name or Hugging Face `main` branch is insufficient provenance.
- When the upstream project's own source has moved beyond an older plan snapshot, compare the pinned snapshot with current `main`, record the compatibility decision, and keep implementation tied to the reviewed commit—not whichever code a package manager retrieves later.

### 6.3 Core implementation rules

9. Preserve unrelated user changes. Never use `git reset --hard` or `git checkout --`.
10. Read `docs/AUDIO_SETUP.md` before touching audio, mic, VAD, playback, or TTS. Preserve the verified HyperX values: mono input, gain 3.0, empty output-device pins, and no duplicate web speech.
11. Do not edit the live `~/.config` file from an automated implementation unless the user explicitly asks. Change repository defaults/docs and provide a migration or doctor result.
12. Do not add a dependency or framework without measuring its startup, memory, VRAM, latency, and maintenance cost.
13. Do not claim “done” from code inspection. Add a focused test, run it, and record the result.
14. Do not let an LLM invent an undeclared tool, permission, filesystem path, network destination, or confirmation result.
15. Do not execute model-generated shell text directly. Use a declared argv tool or an isolated runner.
16. Do not log secrets, cookies, authorization headers, environment-file contents, payment details, or unredacted browser pages.
17. Do not treat browser access as read-only unless the tool contract says so. Navigation, typing, downloads, uploads, messages, purchases, and submissions have side effects.
18. Stop on CAPTCHA, 2FA, payment confirmation, or ambiguous destructive scope. Tell the user exactly what is waiting.
19. Keep cancellation idempotent. A second stop/cancel must not create a new task or corrupt the queue.
20. Use correlation IDs and locks for concurrent requests. Never use a legacy global as the source of truth for request state.
21. Keep event ordering deterministic with sequence numbers. Never infer completion from a missing poll result.
22. Do not auto-load a second large model on this 8 GB GPU. Check resident models and free VRAM before loading.
23. Do not change model defaults based on a single fast sample. Use repeated cold/warm benchmarks and a quality evaluation set.
24. Do not silently “heal” by restarting the bridge, killing unrelated processes, deleting logs, or changing hardware/audio settings.
25. Use feature flags and reversible migrations. Preserve old behavior until the replacement passes its acceptance gate.
26. After a substantial change, run `graft build`, update the relevant docs, and include exact file paths, tests, measurements, failures, and rollback steps in the handoff.
27. All calls to the resident generative Ollama model must resolve runner-critical options from one source. Never set a different `num_ctx`, GPU placement, parallelism, or keep-alive in an individual classifier/chat/summary/planner helper.
28. A local model may return text or a typed proposal; it never grants permission. Validate route/tool/arguments against the registry, then let policy decide. Unknown, malformed, low-confidence, or out-of-scope proposals fail closed.
29. Never bake phone numbers, email addresses, account identifiers, addresses, credentials, private browser data, mutable project status, or raw memory into a Modelfile, model alias, test fixture, prompt snapshot, or Git-tracked artifact.
30. Do not inject entire memory files or raw conversation history on every turn. Retrieve by scope/relevance, prefer corrections, deduplicate, enforce a context/output reserve, and record only redacted selection metadata.
31. Do not download or evaluate an unpinned model/fine-tune because its name says “agent”, “tool”, “function calling”, “uncensored”, or “smart”. Verify base model, revision, artifact hash, license, template, parser compatibility, disk size, and rollback first.
32. A diagnostic that changes model residency/options must restore the prior approved runner state or explicitly report `DEGRADED — RUNTIME STATE CHANGED`. Never leave the user's hot path cold without saying so.
33. Never auto-promote retrieved instructions, model-generated claims, emails/pages, or third-party facts into durable memory. Explicit user corrections and scoped provenance outrank model inference.
34. Gmail/Instagram/Messenger monitoring must use the user's already authenticated real Chromium through loopback CDP. Do not launch a Playwright persistent profile, clone a profile, copy cookies, or silently switch to a different account source.
35. Monitoring is observation only. Do not send, reply, react, archive, delete, mark read/unread, click message actions, download attachments, or alter account/session storage. Navigation/scroll is allowed only inside a separately approved monitor-owned tab in the same real Chromium profile; never mutate a user-owned tab. A future action on a reported item is a separate user-authorized broker task.
36. Never expose CDP beyond loopback. Do not log the debugger WebSocket URL, cookies, storage, page titles containing private text, or raw browser output. Do not use `--remote-allow-origins=*` as a substitute for an authenticated network boundary.
37. Never call a scan successful because a script exited zero. Success requires typed healthy account state, current capture time, valid adapter schema, and a committed cursor/report transaction. Cached or partial data must remain visibly stale/partial.
38. Never equate unread with new. Never advance a cursor when overflow, selector drift, authentication failure, cancellation, report write failure, or unknown identity could hide items.
39. Never create tests or screenshots from the user's real private messages. Fixture data must be synthetic and sanitized. Personal-browser canaries are opt-in, store evidence only in the private state root, and report only redacted metadata in handoffs.
40. A screenshot is evidence, not a completion claim. It must be scoped to the relevant item, hashed, linked from a manifest, access-controlled, and paired with extracted structured fields. Missing evidence must be explicit, never silently omitted.
41. Report content is hostile input. Escape HTML, sanitize/validate URLs, prevent Markdown/CSV formula and path traversal injection, and never render message-derived HTML with `innerHTML` or execute it as instructions.
42. Do not enable, restart, or edit the live inbox systemd service/timer before Packet 8D and explicit user authorization. Fixture tests and manual `--once --dry-run` work must remain separate from live scheduling.
43. Do not announce every healthy hourly scan. Notify once per newly committed report or actionable degraded transition; deduplicate repeats, obey quiet hours, and never speak private message content by default.
44. CDP `Runtime.evaluate` may run only reviewed repository-owned extraction expressions. Never interpolate message text, model output, selector text from a page, or untrusted report data into executable JavaScript.
45. A new website, desktop application, editor, browser operation, or
    marketplace skill must not be added by changing a prompt, launcher map, or
    route alone. It requires a versioned manifest, declared capability IDs,
    scopes, side-effect tier, sensitivity, approval rule, provenance/license,
    resource limits, audit events, tests, documentation, and rollback.
46. `restricted` is the default permission mode. `unrestricted` is only a
    user-authenticated, explicitly confirmed, auditable convenience profile for
    selected scopes and routine non-destructive actions. It must have expiry or
    reset, and can never bypass hard denials, schema validation, redaction,
    audit, isolation, resource caps, loopback-only boundaries, or confirmation
    for sending, publishing, purchasing, deletion, credentials, account/security
    changes, or system-wide destructive operations.
47. Permission mode, grants, scope, expiry, and skill enablement are durable
    policy state, never model output. Retrieved page text, email content,
    skill instructions, tool results, and generated plans cannot grant,
    widen, or switch authority.
48. CLI and dashboard permission/skill controls must call the same backend
    contract. Every side-effecting change needs dry-run/preview, explicit
    confirmation, authenticated scope, audit record, atomic persistence, and
    rollback/revoke behavior. A visible toggle without backend enforcement is a
    failure.

### 6.4 Meta engineering standards

The project uses this standard for every code change:

- correctness means behavior, safety, observability, documentation, tests, and rollback—not merely syntax or import success;
- make the smallest coherent change; do not rewrite whole files, reformat unrelated code, or mix refactors with features;
- preserve public behavior unless the task explicitly changes it; document every intentional behavior change;
- follow existing naming, typing, error, logging, configuration, and module-boundary conventions;
- use explicit types and structured data at boundaries; validate external input before it reaches business logic;
- keep functions small enough to test; isolate I/O from decision logic; inject clocks, executors, network clients, and model clients where tests need control;
- avoid hidden globals, mutable shared state, import-time side effects, unbounded threads, unbounded queues, and background work without cancellation;
- make operations idempotent where possible; make retries bounded and distinguish retryable from permanent errors;
- use timeouts for every network, process, browser, model, and lock operation; never use an infinite wait in a request path;
- never catch `Exception` merely to make a test pass or hide a failure; log a redacted structured error and return a typed degraded result;
- never weaken, delete, skip, or falsify an assertion to obtain green tests;
- never copy a framework wholesale when a small local interface is sufficient;
- never import a package merely because it happens to exist in the current virtual environment; every production import must be standard-library, this project, or a declared direct dependency;
- prefer adapting an upstream function, schema, fixture, or state machine over inventing a parallel abstraction, but preserve provenance and license obligations;
- treat schemas, event names, config keys, tool permissions, and persisted records as versioned interfaces;
- use atomic writes for state/config migrations and keep the previous value recoverable;
- make security decisions from structured tool metadata, not from natural-language wording alone;
- make performance claims from repeated measurements with the hardware, model, and config recorded;
- measure end-to-end transitions, not only isolated warm calls; report percentiles and load time separately so reloads cannot be hidden by averages;
- keep deterministic routing, semantic scoring, generative planning, policy authorization, execution, and response rendering as separately testable stages;
- keep core persona, personal facts, task state, retrieved evidence, and tool schemas as separately budgeted context sections;
- keep user data, secrets, and generated artifacts out of Git unless explicitly intended.

### 6.5 Documentation rules

Documentation is part of the implementation, not cleanup work. Whenever behavior, configuration, API, tool permissions, model selection, performance, or recovery changes, the agent must update the relevant documentation in the same task.

Every completed task must document:

- what changed and why;
- the current effective behavior, not the intended future behavior;
- configuration keys, defaults, valid values, and migration steps;
- security and side-effect implications;
- resource impact and measured latency/VRAM/CPU when relevant;
- failure modes, degraded behavior, and recovery;
- exact tests and commands run;
- known limitations and the next safe step.

Documentation must not claim a feature is complete when its acceptance gate is unverified. If code and docs disagree, mark the feature `INCOMPLETE — DOCUMENTATION/IMPLEMENTATION MISMATCH` and fix the source-of-truth conflict before proceeding.

### 6.6 Testing rules

For every behavior change, the agent must add or update tests for:

- the normal path;
- invalid input and malformed external data;
- dependency unavailable/timeout/degraded behavior;
- permission denial and confirmation flow;
- cancellation, retry, duplicate request, and restart recovery where applicable;
- concurrency and state isolation where shared state is involved;
- secret redaction and prompt/tool-injection resistance where data crosses a trust boundary;
- performance/resource budgets when the task affects latency or memory.

Model/router/memory work must additionally test the repeated `classifier → chat → classifier → summary → chat` sequence, canonical runner-option equality, schema-invalid output, per-class false positives/negatives, tool argument validation, stale-versus-corrected memory, cross-scope retrieval, context overflow, model load failure, and restoration of the previous alias/options.

Communications/report work must additionally test baseline/no-flood, new-versus-unread, unknown read-state, repeated scans, identity collisions, transactional cursor/report crash points, per-account partial failure, selector drift, login/2FA/CAPTCHA, bounds/overflow, CDP command allowlisting, screenshot scoping/failure, XSS and malicious links, API authentication/traversal, private file modes, acknowledgement/notification dedupe, quiet hours, timer overlap/catch-up, retention, legacy reports, and browser focus/tab preservation. Real-message contents and screenshots may never enter test output or Git.

For upstream-derived behavior, tests must also include the pinned upstream fixture or an equivalent contract vector, plus one local divergence test for every intentionally omitted upstream feature. A copied implementation with only new local happy-path tests is unverified.

Tests must be separated into unit, contract, integration, live-canary, and monitoring categories. Live tests may be skipped only with an explicit `DEGRADED` result and must never be reported as a successful verification. Monitoring/watchdog processes must never run during normal test collection.

Before handoff, run the narrowest relevant tests first, then the regression suite for affected modules. If the full suite cannot complete, report the exact command, elapsed time, blockage, and tests not executed. A partial test run is evidence of partial validation only.

### 6.7 Strict completion and handoff rules

An agent must never say `done`, `complete`, `fixed`, `ready`, or equivalent unless all of these are true:

1. The requested behavior is implemented, not merely scaffolded.
2. The relevant acceptance criteria passed.
3. Tests were actually executed and their output was observed.
4. Documentation and configuration references are updated.
5. Security and failure paths were checked.
6. Performance/resource claims have measurements when applicable.
7. Graft was rebuilt after substantial source changes.
8. No known failing test, unverified dependency, stale documentation, or unresolved risk is being hidden.
9. A rollback path exists and was documented.

If any condition is false, the only valid status is one of:

- `INCOMPLETE` — implementation remains and the next concrete step is known;
- `BLOCKED` — an external dependency or user decision is required;
- `DEGRADED` — the feature works with a known limitation;
- `FAILED` — the attempted change introduced a regression and must be rolled back or repaired.

Every handoff must contain:

- status using one of those four values;
- task capsule, packet, and parent phase;
- files changed with symbol/line references;
- tests run with pass/fail/skip/degraded results;
- benchmark numbers and environment when relevant;
- known risks and untested paths;
- rollback instructions;
- the next action, if any.

### 6.8 Rules for old, slow, or unreliable agents

Agents with limited context, slow execution, or uncertain reasoning must use smaller bounded tasks. They must not compensate by guessing or making broad changes.

- One lettered packet, one focused objective, and one owner at a time.
- Never change more than the task capsule allows without starting a new preflight.
- Do not write production code for an upstream-derived feature until the upstream reuse record is present in the handoff or task log.
- Copy/adapt the smallest upstream testable unit; do not summarize an upstream design and then invent a larger local framework around it.
- Do not create placeholder classes, pass-through wrappers, duplicate managers, `TODO` implementations, fake success responses, or compatibility layers with no current caller.
- Do not add a second HTTP server, event store, task queue, model server, browser runtime, or configuration system.
- Do not use `browser_inbox.py` Playwright persistent profiles for Gmail/Instagram/Messenger monitoring. Adapt the existing real-Chromium CDP behavior into a repository-owned, typed, read-only adapter and keep Playwright for local report-UI tests.
- Do not improve reports by adding more regex extraction or agent prose. Define canonical structured input once, render every format from it, and fail validation when a required field/evidence reason is absent.
- Do not create separate “router model”, “chat model”, and “planner model” aliases that point to the same weights with different context sizes; this reproduces the reload bug under new names.
- Do not replace the selected model alias in place. Candidate aliases remain separate until an independent verifier confirms quality, resources, rollback, and the exact digest.
- Do not solve personal memory by concatenating more Markdown into the system prompt. Implement the bounded retrieval contract in the authorized packet or stop.
- Do not depend on Pydantic, FastAPI, Uvicorn, psutil, or any other incidental installed package unless the same phase adds it directly to `pyproject.toml`, explains why standard-library/current code is insufficient, and passes the dependency gate.
- Before handoff, run `git diff --check`, compile/import the changed modules, and prove every new code path has a caller and a test.
- Do not proceed after two failed implementation attempts without reporting the evidence and changing the plan.
- Prefer a failing focused test over speculative code.
- Use existing helpers and patterns before inventing new abstractions.
- Do not perform opportunistic cleanup, dependency upgrades, renames, or architecture rewrites.
- If context is running out, stop and write a handoff; do not continue from a guessed memory of the code.
- If a requirement is ambiguous and the choice affects security, data, money, hardware, or architecture, stop and mark `BLOCKED` rather than guessing.
- Never conceal uncertainty with confident language. State what was verified and what was inferred.
- The next agent must be able to reproduce the result from the handoff alone.

### 6.9 Copy/paste prompts for implementation and verification agents

Replace `<PACKET>` and `<OBJECTIVE>` only. Do not remove the constraints.

Implementation prompt:

```text
Implement ONLY Packet <PACKET> from docs/UNIFIED_AGENT_PLAN.md: <OBJECTIVE>.

Before any search, edit, model load/download, or state-changing command, perform the complete fresh preflight in section 6.1. Read AGENTS.md and the ENTIRE plan again even if you read them in a prior task. Use Graft first. Record git status, unrelated user changes, exact allowed files/symbols, affected callers, risks, tests, measurements, rollback, and definition of done.

Stay inside this one packet. Do not start the next packet or opportunistic cleanup. Preserve the working HyperX/audio configuration and do not edit live user config/services unless this packet and the user explicitly authorize it.

For upstream-derived work, inspect the exact pinned source and tests in section 4.1, compare current upstream only for compatibility, and write the UPSTREAM_REUSE record BEFORE production code. COPY a small compatible unit or ADAPT its public contract/tests; do not invent a framework, copy a whole project, use floating main, or add an undeclared dependency. Preserve licenses/notices.

Use the existing Python/static-JS/systemd/Bubblewrap/Ollama/OpenClaw/Hermes stack. Model output is never permission. Never execute generated shell directly, expose secrets/private memory, bake PII into a model, change per-call Ollama runner options, or hide failed/skipped/live tests.

Write the smallest coherent implementation with typed boundaries, bounded I/O, timeouts, cancellation, redacted observability, reversible migration, docs, normal/negative/degraded/concurrency/security tests, and packet-specific benchmarks. Run focused tests first and affected regressions second. Inspect actual output. Run git diff --check and rebuild Graft after substantial source changes.

You may not report done/complete/ready. End with the exact handoff template from section 5.1 and status INCOMPLETE, BLOCKED, DEGRADED, or FAILED. List exact files, commands, pass/fail/skip counts, measurements, security checks, untested paths, and rollback. Then STOP and wait for independent verification.
```

Independent verification prompt:

```text
Independently verify ONLY Packet <PACKET> from docs/UNIFIED_AGENT_PLAN.md. Do not implement the next packet and do not trust the previous agent's summary.

Perform a fresh section 6.1 preflight: reread AGENTS.md and the ENTIRE plan, run git status, use Graft first, inspect the complete diff and every affected caller, and preserve unrelated user work. Re-open every pinned upstream source/test used by the packet, compare the local adaptation/fixtures, and verify commit, license, notices, omitted behavior, dependencies, and the under-300-line architecture gate.

Rerun the packet's focused, negative, degraded, security, concurrency, clean-install, live, and benchmark gates as applicable. Observe actual runtime/config/model/residency/resource state without printing secrets. For model work, prove canonical runner options and the classifier→chat→classifier→summary→chat transition; for memory work, prove scope/correction/deletion/context-budget/injection behavior. A skip is DEGRADED, not green. Do not restart or modify live services unless explicitly authorized.

Check docs, rollback, feature flags, migrations, Graft freshness, and that no acceptance claim exceeds evidence. Return only the verification template from section 5.2 with VERIFIED, REJECTED, or VERIFIED WITH EXPLICIT LIMITATION, concrete evidence, failures, untested items, repairs, and NEXT PHASE AUTHORIZATION. The next packet remains NOT GRANTED unless every required gate passes and the user approves.
```

Recommended first execution prompt:

```text
Implement ONLY Packet 0A: inventory and reconcile config, documentation, deployment, model-caller/runtime options, language policy, private-data placement, and upstream claims. Produce the mismatch report and docs/UPSTREAM_REUSE.md baseline without changing runtime behavior, live config/services, model aliases, or audio settings. Follow the full implementation prompt and stop after the 0A handoff.
```

Phase 8 implementation prompts, to use only after its dependency gate passes:

```text
Implement ONLY Packet 8A: inventory the existing real-Chromium CDP inbox adapters and the actual saved-report/API/UI paths; define versioned scan, account-health, item, cursor, report-manifest, asset, and notification schemas; design the SQLite migration, retention defaults, exit statuses, and atomic commit protocol; create synthetic sanitized Gmail/Instagram/Messenger/report fixtures and contract tests. Do not inspect or copy real message bodies into fixtures or handoffs, change runtime behavior, enable a service/timer, add a framework, or implement Packet 8B. Use the generic implementation prompt above and stop after the 8A handoff.
```

```text
Independently verify ONLY Packet 8A. Use the generic verification prompt above. Prove the contracts distinguish new/unread/unknown/stale/partial/auth-required/no-change; stable identity and fallback collisions are covered; cursor advancement depends on atomic report commit; fixtures contain no private data; no live service/timer/runtime path changed; upstream CDP/systemd/report decisions and licenses are recorded. Reject placeholders, regex-only report schemas, Playwright-owned social/email profiles, or missing failure/rollback semantics. Stop after the 8A verification result.
```

```text
Implement ONLY Packet 8B: build repository-owned, typed, read-only Gmail/Instagram/Messenger adapters that attach to the user's already logged-in real Chromium on loopback CDP; implement stable identity, baseline/new-item transactions, bounds/overflow, typed health, clipped CDP screenshots, and manual --once/--dry-run paths with synthetic tests and an explicitly opt-in redacted canary. Do not launch Playwright account profiles, click/type/send/react/mark-read, mutate/focus/close/navigate user-owned tabs, enable scheduling, build the report UI, or start Packet 8C. Any navigation/scroll must remain inside the approved non-activated monitor-owned-tab contract. Use the generic implementation prompt and stop after the 8B handoff.
```

```text
Implement ONLY Packet 8C: render validated canonical manifests into atomic private JSON/Markdown/HTML report bundles and evidence assets; replace the false activity-log reports API with authenticated report index/detail/asset routes; index legacy Markdown read-only; update only the repository-owned static reports UI with safe links, thumbnails, status/freshness, acknowledgement, export/print, escaping, and responsive/accessibility behavior. Add fixture, API, traversal/XSS, link/hash/MIME, file-mode, and Playwright local-UI tests. Do not enable the hourly timer or implement notification scheduling. Use the generic implementation prompt and stop after the 8C handoff.
```

```text
Implement ONLY Packet 8D after the user explicitly authorizes live activation: add deduplicated dashboard/desktop and opt-in quiet-hours voice notifications, repository-templated bounded user-systemd oneshot/timer, overlap lease, retention, Chromium-CDP precondition/optional approved launch integration, health/rollback commands, and separate live canaries. Render and inspect every service/timer/autostart diff before installation; never auto-reply, expose CDP beyond loopback, start a competing browser/profile, kill/restart the user's browser, alter account/session state, delete old reports, or hide skipped/degraded canaries. Prove at least two real timer elapses and one controlled new-item notification, then stop after the 8D handoff for independent verification.
```

```text
Implement ONLY Packet 8E: add the non-live website integration onboarding
façade described in docs/UNIFIED_AGENT_PLAN.md. A pasted URL must create only a
pending, disabled, versioned integration proposal. Add bounded read-only
discovery, redacted typed lifecycle/audit logs, CLI commands for add/list/show/
inspect/check/logs/grant/revoke/enable/disable/remove/rollback, and the same
authenticated dashboard Settings controls. Compile proposals through the
existing Packet 7E manifest and permission backend; Restricted is the default,
and no page/model/tool text can widen a grant. Reject credential-bearing URLs,
invalid manifests, bad hashes/licenses, out-of-scope origins, arbitrary page
JavaScript, profile cloning, cookies, Playwright personal profiles, hidden
skills, or automatic account access. Add synthetic normal/negative/degraded/
security/persistence/resource/parity tests and docs. Do not implement a live
site adapter, 8C reports, 8D scheduling, or any account mutation. Do not change
live services, Chromium, accounts, model/audio/config, or existing 8B behavior.
Use the generic implementation prompt and stop after the 8E handoff.
```

```text
Independently verify ONLY Packet 8E. Prove URL onboarding is pending/disabled
and non-authorizing; discovery is bounded, read-only, redacted, auditable, and
typed; malformed/credential-bearing/out-of-scope inputs, bad manifests,
disabled skills, injection, auth/selector/timeout/partial/unavailable cases,
and expired/revoked grants fail closed. Re-run CLI and authenticated dashboard
parity, dry-run, confirmation, scope/expiry, restart, duplicate, rollback, and
remove tests. Prove restricted defaults and unrestricted cannot bypass hard
denies, validation, isolation, audit, or resource limits. Inspect real logs and
fixtures for secrets/private text. Confirm no live Chromium/service/timer/
account/model/audio/config or existing 8B behavior changed. Use the generic
verification prompt and stop after the 8E result; do not start another packet.
```

```text
Implement ONLY Packet 8F: add typed, brokered, confirmation-gated browser,
desktop, and editor actions described in docs/UNIFIED_AGENT_PLAN.md. Reuse the
existing 7E permission store, capability broker, 8E integration records, and
approved Chromium/CDP boundary. Add read/search/fill/draft/reversible-write/
send/delete capability separation, exact schemas, bounded previews, stable
action-hash confirmation, cancellation, idempotency, receipts, audit, typed
failure states, recovery, and CLI/dashboard/voice parity. Restricted is the
default; sending, publishing, purchasing, deleting, credential/account/security
changes, and irreversible system actions always require fresh confirmation.
Reject arbitrary JavaScript, free-form shell, coordinate-only scripts,
page/model/skill instructions as authority, profile cloning, cookie import,
public CDP, hidden scopes, and unbounded loops. Add synthetic fixtures and
negative/security/resource/persistence tests for websites, email/social flows,
VS Code/editor workspaces, and desktop launchers. Do not enable live actions,
modify accounts, or change live services, Chromium, model/audio/config. Use the
generic implementation prompt and stop after the 8F handoff.
```

```text
Independently verify ONLY Packet 8F. Prove every action reaches the existing
broker and 7E permission check, is bound to a validated adapter, scope, target,
argument schema, resource limit, and audit receipt, and cannot run by default.
Re-run preview/dry-run, confirmation hash/expiry, restricted/unrestricted,
hard-deny, cancellation, duplicate/idempotency, stale-target, auth/2FA,
partial execution, retry, recovery, rollback-honesty, redaction, injection,
arbitrary-JavaScript, free-form-shell, and unbounded-loop tests. Verify CLI,
dashboard, and voice requests have identical approval semantics and that a
model/page/skill/tool result cannot confirm or widen an action. Inspect actual
receipts and failure output. Confirm read, draft, reversible write, external
send, destructive, and system-wide actions have separate metrics and no unsafe
aggregate. Live account actions remain opt-in and separately canaried; do not
perform them without explicit authorization. Confirm no unrelated service,
browser, account, model, audio, or config changes. Return only the verification
template with VERIFIED, REJECTED, or VERIFIED WITH EXPLICIT LIMITATION and stop.
```

## 7. Required validation commands

Fast local validation:

```bash
git diff --check
.venv/bin/python -m compileall -q voicebridge scripts
.venv/bin/python -m pip check
graft build
.venv/bin/python -m pytest -q tests/test_steal_list.py tests/test_steal_list_strict.py -k 'not AirLLMLive'
```

Live model validation is opt-in and must be run separately after confirming the model is resident and no competing model is loaded:

```bash
.venv/bin/python -m pytest -q tests/test_steal_list_strict.py -k AirLLMLive
```

Dashboard live probes must use the configured header token and must not run the watchdog mode during pytest. Monitoring is a separate operator process.

Packet 8 must provide and keep these stable validation surfaces; fixture validation is mandatory, while personal-browser/timer commands remain opt-in and redacted:

```bash
.venv/bin/python -m pytest -q tests/test_communications_monitor.py tests/test_report_bundles.py tests/test_webui_reports.py
.venv/bin/python scripts/communications_monitor.py --once --dry-run --fixture tests/fixtures/communications/new_items.json
.venv/bin/python scripts/communications_monitor.py --once --live   # opt-in after Packet 8B authorization; never print message bodies
systemd-analyze --user verify systemd/openclaw-communications-monitor.service systemd/openclaw-communications-monitor.timer
systemd-analyze calendar hourly
```

Packet 8D live verification may use `systemctl --user start openclaw-communications-monitor.service` and inspect the timer only after explicit authorization. Enabling the timer is never part of generic pytest or installation tests.

## 8. Definition of success

The project is ready for broad PC control only when:

- the effective configuration is validated and documented;
- a clean installation uses only declared dependencies and reproducible repository deployment files plus documented local overrides;
- the dashboard is authenticated and origin-restricted;
- all actions go through the capability broker;
- untrusted/generated code has an isolated execution path;
- event streaming, approvals, cancellation, and restart recovery are durable;
- browser, file, repo, inbox, and web information has provenance and permissions;
- deterministic/semantic/generative routing stages have separate measured gates and uncertainty never becomes authority;
- local, direct-frontier, OpenCode Zen, and OpenRouter routes use one typed
  provider contract with explicit model capabilities, data-egress policy,
  token/cost/concurrency limits, redacted credentials, and truthful fallback
  states;
- cloud routing is disabled by default, never sends private context silently,
  and remains usable offline with the local model;
- OpenCode coding runs are supervised, scoped to an approved worktree, produce
  a reviewable diff, and cannot bypass the action broker or high-impact
  confirmation rules;
- the default model has measured per-class quality, schema/tool accuracy, first-audio/full-turn latency, transition-reload, and VRAM gates;
- every resident-model caller uses one canonical runner contract and the normal request sequence does not reload the model;
- personal context is scoped, retrievable, correctable, inspectable, exportable, removable, citation-backed, and bounded instead of baked into the model or injected wholesale;
- hourly Gmail/Instagram/Messenger observation uses the user's real logged-in Chromium through loopback CDP without mutating, focusing, closing, or replacing user tabs and without any send/reply capability;
- new-item detection is durable, account-scoped, transactional, idempotent, distinct from unread state, and does not flood on first baseline or repeat alerts after restart;
- saved reports are validated private JSON/Markdown/HTML bundles with safe source links, scoped screenshots or explicit evidence reasons, hashes/provenance, authenticated index/detail/assets, legacy access, professional rendering, and tested retention/rollback;
- hourly no-change scans are silent, new reports notify locally once, degraded/auth-required transitions are actionable and deduplicated, and optional voice obeys privacy and quiet hours;
- unit, contract, integration, and live-canary results are separated;
- every upstream-derived unit has a pinned commit, source/test provenance, conformance evidence, and required license notices;
- every agent handoff contains evidence and a rollback path.

## 9. Upstream references reviewed for this plan

- AG-UI events (MIT), reviewed at `821b8c227c6fe7b78de2c8259173dd7cbbdf181b`: https://github.com/ag-ui-protocol/ag-ui/blob/821b8c227c6fe7b78de2c8259173dd7cbbdf181b/sdks/python/ag_ui/core/events.py
- AG-UI SSE encoder/tests: https://github.com/ag-ui-protocol/ag-ui/blob/821b8c227c6fe7b78de2c8259173dd7cbbdf181b/sdks/python/ag_ui/encoder/encoder.py
- CopilotKit human-in-the-loop source (MIT), reviewed at `e9387e04835545c45744b791aee7c9c03520be31`: https://github.com/CopilotKit/CopilotKit/blob/e9387e04835545c45744b791aee7c9c03520be31/packages/react-core/src/v2/hooks/use-human-in-the-loop.tsx
- OpenSandbox lifecycle specification (Apache-2.0), reviewed at `6b2023e9b7eb80a940d88e6ae05fcbc0eb0cf23f`: https://github.com/opensandbox-group/OpenSandbox/blob/6b2023e9b7eb80a940d88e6ae05fcbc0eb0cf23f/specs/sandbox-lifecycle.yml
- OpenSandbox Bubblewrap capability probe/lifecycle source: https://github.com/opensandbox-group/OpenSandbox/blob/6b2023e9b7eb80a940d88e6ae05fcbc0eb0cf23f/components/execd/pkg/isolation/probe.go
- AirLLM core (Apache-2.0), reviewed at `6ab3b9db9b2fb595e8a4d966f0e1ba600677b1fe`: https://github.com/lyogavin/airllm/blob/6ab3b9db9b2fb595e8a4d966f0e1ba600677b1fe/air_llm/airllm/airllm_base.py
- Ollama scheduler/runtime contract (MIT), reviewed at `fb30760996871fa9460115c753afd2c60d4ab0f7`: https://github.com/ollama/ollama/blob/fb30760996871fa9460115c753afd2c60d4ab0f7/server/sched.go
- Ollama structured outputs and tool calling: https://docs.ollama.com/capabilities/structured-outputs and https://docs.ollama.com/capabilities/tool-calling
- Semantic Router threshold/no-match/evaluation behavior (MIT), reviewed at `a4576168d9589397a7e0c6ff77f5d05469a56e2e`: https://github.com/aurelio-labs/semantic-router/blob/a4576168d9589397a7e0c6ff77f5d05469a56e2e/semantic_router/routers/base.py
- Qwen3-4B-Instruct-2507 model card/revision (Apache-2.0): https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507/tree/cdbee75f17c01a7cc42f958dc650907174af0554
- Qwen3-4B-Instruct-2507 GGUF candidate (Apache-2.0): https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/tree/a06e946bb6b655725eafa393f4a9745d460374c9
- Qwen3.5-4B experimental model/revision (Apache-2.0): https://huggingface.co/Qwen/Qwen3.5-4B/tree/851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a
- Nomic Embed v1.5 model/revision (Apache-2.0): https://huggingface.co/nomic-ai/nomic-embed-text-v1.5/tree/e9b6763023c676ca8431644204f50c2b100d9aab
- Mem0 memory contracts/tests (Apache-2.0), reviewed at `8d5b7865bd0521e75fe9ab1b260eeccc83f8a378`: https://github.com/mem0ai/mem0/blob/8d5b7865bd0521e75fe9ab1b260eeccc83f8a378/mem0/memory/main.py
- Letta memory-block/core-recall structure (Apache-2.0), reviewed at `4511fa0bc91f68fbab32b91f694617271ea9012b`: https://github.com/letta-ai/letta/blob/4511fa0bc91f68fbab32b91f694617271ea9012b/letta/schemas/memory.py
- Chrome DevTools Protocol source (BSD-3-Clause), reviewed at `5ee78b32ae561f7830853c089459200e6a3467e4`: https://github.com/ChromeDevTools/devtools-protocol/tree/5ee78b32ae561f7830853c089459200e6a3467e4
- Chrome DevTools Protocol `Page.captureScreenshot`: https://chromedevtools.github.io/devtools-protocol/1-3/Page/#method-captureScreenshot
- systemd timer source/man contract (LGPL-2.1-or-later), v255 commit `db11bab38ccf1ed257f310d29070843d4c58ea01`: https://github.com/systemd/systemd/blob/db11bab38ccf1ed257f310d29070843d4c58ea01/man/systemd.timer.xml
- Gmail incremental history contract: https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list
- Playwright Python v1.62.0 source (Apache-2.0), reviewed only for local report-UI tests at `3b7c24c3e67dc84f7b0eddd0c5fd2ca685705021`: https://github.com/microsoft/playwright-python/tree/3b7c24c3e67dc84f7b0eddd0c5fd2ca685705021
- Himalaya v2.1.0 source (MIT), installed revision/tag commit `ca88bee08ad2e92127b46dc6200d1e8201885156`; optional Gmail fallback only: https://github.com/pimalaya/himalaya/tree/ca88bee08ad2e92127b46dc6200d1e8201885156
- OpenCode provider and model configuration: https://opencode.ai/docs/providers and https://opencode.ai/docs/models
- OpenCode Zen API/provider contract: https://opencode.ai/docs/zen
- OpenCode Zen billing and per-request usage note: https://dev.opencode.ai/docs/zen
- OpenRouter API quickstart and OpenAI-compatible endpoint: https://openrouter.ai/docs/quickstart
- OpenRouter provider selection, fallback, and data-policy controls: https://openrouter.ai/docs/guides/routing/provider-selection
- OpenAI API-key handling guidance: https://platform.openai.com/docs/api-reference/authentication
