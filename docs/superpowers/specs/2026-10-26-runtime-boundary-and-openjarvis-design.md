# Runtime boundary + OpenJarvis integration — design

Date: 2026-10-26. Owner direction: Qubit absorbs other agents' capabilities;
this document scopes the first program that makes the agent runtime a boundary
instead of a vendored monolith, and uses OpenJarvis as the proof that a second
engine can plug in. Status: Phase 1b (Hermes/OpenClaw skill sources), Phase 1 (RuntimePort + native backend), Phase 2 (out-of-process OpenJarvis engine) and Phase 3 (`qubit -z` oneshot through the port) implemented; Phases 4–8 proposed. See `docs/STATUS.md` for evidence.

## Outcome

Qubit keeps its voice bridge, dashboard, gateway, messaging, cron, skills and
Office surfaces as the product front, and runs one turn through a stable
`RuntimePort`. Two backends implement that port:

- `native` — today's vendored runtime, behaviour unchanged (default).
- `openjarvis` — OpenJarvis as an out-of-process engine, with its recipes
  (morning digest, monitor, deep research) selectable from Qubit.

Later phases move the vendored runtime's `HERMES_*`/`.hermes` internals behind
the port so they stop being product wiring, and borrow OpenJarvis components
(connectors, traces/learning, evals/bench, Rust local engine) through adapters.

## Non-goals

- No merge of the OpenJarvis tree into `qubit_runtime/`. That recreates the
  vendored-runtime problem with a different upstream.
- No change to the default engine, model routing, voice path or dashboard
  behaviour in Phase 1.
- No deletion of `HERMES_*` env vars, `_LEGACY_HOME_DIR`, container
  `/root/.hermes`, remote `~/.hermes` sync home, plugin-SDK compat globals or
  attribution text. Those are contracts/legal, not leftovers (docs/BRAND.md §2).
- No OpenJarvis mining/Pearl, no duplicate agent loops, no second web server as
  a product surface.
- No `VERIFIED` claim for the program; the current milestone's verification is
  a prerequisite, not a result of this work.

## Architecture

```
voice / dashboard / gateway / messaging / cron / office
                    │  (product surfaces; unchanged)
                    ▼
             RuntimePort  (agent/runtime_port.py)
              │                    │
      native backend        openjarvis backend
   (vendored AIAgent)    (subprocess or HTTP/WS)
```

The port is a small, typed contract over one turn:

- `TurnRequest` — prompt, session id, history, system prompt, task id.
- `TurnEvent` — `delta | tool | usage | done | error` with bounded payloads.
- `RuntimeBackend` — `name`, `capabilities`, `run_turn(request, cancel)`.
- A registry selects a backend by name; unknown names are a typed refusal.

Backends cross the boundary differently: `native` in-process (today), and
`openjarvis` out-of-process (its CLI `--json` or FastAPI server/WS). The port
never imports OpenJarvis code; the adapter shells out or speaks HTTP.

Recipes are a Qubit concept: prompt + tool set + schedule + memory/approval
policy + voice command, executed on whichever backend. OpenJarvis agent files
are reference implementations, not code to copy wholesale.


## Reviewed upstream adapters (2026-10-26, `open-jarvis/OpenJarvis@0583901`, Apache-2.0)

The two adapter families are deliberately small; ours is a superset in both areas.

**Skill sources** (`src/openjarvis/skills/sources/{base,hermes,openclaw}.py`):
- `SourceResolver` clones/pulls a repo into a cache, walks `skills/<category>/<skill>/SKILL.md`
  (Hermes) or `skills/<owner>/<skill>/SKILL.md` + `_meta.json` (OpenClaw), reads frontmatter,
  and returns a lightweight `ResolvedSkill(name, source, path, category, description, commit, sidecar)`.
- Refresh is best-effort: a failed `git pull` keeps the existing cache and warns; a failed
  first clone raises. The commit SHA is recorded per result.
- Their `SkillImporter` adds trust classification, dangerous-capability checks, tool-reference
  translation, and opt-in scripts. Tests: `tests/skills/test_sources.py`,
  `tests/skills/test_source_refresh_fallback.py`, `test_importer.py`.

Our `tools/skills_hub_*` already has a broader hub: GitHub API auth (PAT/gh/App), taps,
index cache, trust levels, SSRF-guarded fetch, bundle validation, installer/security, and
multiple sources (`official`, `hermes-index`, `skills-sh`, `clawhub`, `github`, `lobehub`,
`well-known`, `url`, `browse-sh`). **Do not replace that hub.** Port the two resolver
semantics as new `SkillSource` adapters (`hermes`, `openclaw`) and keep our installer,
trust, and security. This is the only way to gain their Hermes/OpenClaw import without
losing the security and install layers.

**Agent/eval backends** (`src/openjarvis/evals/backends/external/{hermes_agent,openclaw,_subprocess_runner}.py`):
- The foreign framework runs as a subprocess; *"Foreign code never imports into this process;
  we cross the boundary only via stdin/stdout JSON."*
- Path + interpreter are explicit (`HERMES_AGENT_PATH`, `HERMES_AGENT_PYTHON`, `OPENCLAW_PATH`)
  and the commit is pinned/verified.
- This is the exact shape for our RuntimePort P2 `openjarvis` backend (and a future
  `hermes`/`openclaw` engine): out-of-process, pinned, no in-process import.

## Phases and gates

Each phase produces working, testable software and its own detailed plan. All
phases are gated on the current milestone being signed off (full matrix green
or documented owner-approved exceptions, live gateway/bridge on the same
commit, docs pushed).

| Phase | Deliverable | Acceptance | Depends on |
|---|---|---|---|
| P0 | Gate: current milestone signed off | `docs/STATUS.md` records matrix + live evidence; `canonical/main` pushed; services restarted | — |
| P1b | Hermes/OpenClaw skill sources in the existing hub (ported resolver semantics) | `hermes:<name>` / `openclaw:<owner>/<name>` search/inspect/fetch/install; cache refresh fallback; commit pinned; local-repo tests | P0 |
| P1 | RuntimePort + native backend + diagnostic `qubit runtime` | Contract tests green; `qubit runtime run --engine native` answers a prompt; no behaviour change to chat/gateway (focused suites) | P0 |
| P2 | OpenJarvis backend (out-of-process) | Pinned commit + Apache-2.0 notice; `qubit runtime run --engine openjarvis` answers; typed refusal when absent; no imports of `openjarvis` in-process | P1, OpenJarvis API contract pinned |
| P3 | Route `qubit chat`/oneshot through the port (native only) | Existing chat/oneshot tests green; `--engine` accepted; default unchanged | P1 |
| P4 | Recipes: morning digest, monitor, deep research | Each recipe runs on both backends where applicable; voice command + dashboard surface; cron schedule | P2, P3 |
| P5 | Connector pack (Apple Calendar/Contacts/Health/Music, Google Contacts, Dropbox) | Each connector has a typed contract test + a live probe where possible; OAuth/token storage via existing secret paths | P1 |
| P6 | Traces + learning loop, evals/bench | Trace store writes/reads; one optimization run over local traces; eval harness reports accuracy/energy/FLOPs/latency/cost | P1, P2 |
| P7 | Borrow Rust local engine as an optional provider | Provider registered, selectable, capability-gated; no default change | P1 |
| P8 | Thin vendored internals behind the port | Measured inventory; env/path contracts either dual-published or moved under the port; no feature regression; owner sign-off for each deletion | P1–P7 |

## Phase 1 file plan (detailed plan: `plans/2026-10-26-phase-1-runtime-port-native-backend.md`)

- Create `qubit_runtime/agent/runtime_port.py` — `TurnRequest`, `TurnEvent`,
  `RuntimeCapabilities`, `RuntimeBackend` protocol, `UnknownRuntimeError`.
- Create `qubit_runtime/agent/runtime_native.py` — wraps an injected
  `AIAgent`-like object; maps `stream_callback` deltas, the result envelope and
  `agent.interrupt(...)` cancellation into port events.
- Create `qubit_runtime/agent/runtime_registry.py` — `register`, `get`,
  `available`; `native` is the only built-in in P1.
- Create `qubit_runtime/qubit_cli/subcommands/runtime.py` — `qubit runtime
  engines`, `qubit runtime run --engine native --prompt/--query-file --json`.
- Tests in `qubit_runtime/tests/runtime/` — fake backend contract, native
  mapping, cancellation, unknown engine, CLI JSON.

## 8-agent mapping

| OpenJarvis agent | Decision | Qubit form |
|---|---|---|
| `morning_digest` | Borrow (P4) | Recipe: cron + voice + gmail/calendar/health; TTS through `voicebridge` |
| `monitor_operative` | Borrow (P4) | Recipe: cron/kanban + memory/compression policy |
| `deep_research` | Borrow after benchmark (P4) | Recipe over Qubit web tools; only if it beats current parity |
| `orchestrator` | Alias | Existing agent loop + delegation/MoA |
| `native_react` | Alias | Existing turn loop |
| `operative` | Alias | Existing gateway/kanban sessions |
| `native_openhands` | Alias | Existing code execution/sandbox |
| `simple` | Alias | Existing chat |

Beyond the eight: `channel_agent`/`proactive_agent` (messaging/proactive),
`claude_code`/`opencode`/`openhands` (coding adapters) are cherry-pick
candidates in P4/P5 after the engine boundary exists.

## Feature matrix (from `open-jarvis/OpenJarvis@0583901`, Apache-2.0)

Absorb (adapters/backends, never vendored): connectors (Apple Calendar/
Contacts/Health/Music, Google Contacts, Dropbox, embeddings), traces +
learning/DSPy, evals/bench (energy/FLOPs/latency/cost), recipes/workflow,
Rust local engine, GPU deploy compose, speech module (only if it beats ours).

Already covered by Qubit (do not port): agent loop/tools/MCP/skills/memory/
sessions/scheduler/channels/gateway/sandbox/A2A/approvals/desktop/telemetry/
Docker.

Skip: mining/Pearl, their server/desktop shell, duplicate agent loops.

## Licensing and provenance

- OpenJarvis is Apache-2.0. Before any code is copied or adapted, record the
  exact repo + pinned commit + source paths + local changes in
  `THIRD_PARTY_NOTICES.md`, and preserve required notices.
- Borrowed components stay out-of-process or in adapter modules whose upstream
  provenance is documented; no OpenJarvis file enters `qubit_runtime/` without
  that record.
- `LICENSE` covers original Qubit material only.

## Risks and mitigations

- **Two runtimes, two dependency stacks.** OpenJarvis needs Python 3.10–3.13,
  FastAPI and a Rust extension; keep it optional and out-of-process, never a
  default dependency.
- **Streaming semantics.** The native callback is not an async iterator. P1
  adapts it with a bounded queue/thread and pins ordering; P3 reconciles with
  the gateway's event bus before touching chat.
- **Cancellation.** Native cancellation is `agent.interrupt(...)`; the port
  must expose a single `cancel` contract and test that a cancelled turn emits
  `error` (never `done`).
- **Session/tool parity.** OpenJarvis has its own sessions/tools; the adapter
  maps only the bounded turn surface. No cross-engine session storage in P2.
- **Numeric/behaviour parity.** The native path must be byte-identical for
  existing callers until P3 changes them deliberately.
- **Brand/contract trap.** Do not rename `HERMES_*`/`.hermes` as part of this
  program; P8 needs an owner ruling per deletion.
- **Verification cost.** Each phase runs focused tests; the full matrix runs
  once at the phase gate, not per task.
