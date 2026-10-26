# Runtime Boundary + OpenJarvis Program Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement the plan task-by-task. Phase 1 has its own detailed plan (linked below); later phases are written after the prior phase's interfaces exist, per the writing-plans scope check.

**Goal:** Make the agent runtime a stable boundary (`RuntimePort`) with today's vendored engine as the default backend, then plug OpenJarvis in as an optional out-of-process engine and absorb its genuinely additive capabilities through adapters.

**Architecture:** A small typed port (turn request/events/capabilities/cancel) sits between Qubit's product surfaces and the engine. `native` wraps the current `AIAgent`; `openjarvis` runs out-of-process (CLI `--json` or FastAPI/WS). Recipes are Qubit config (prompt/tools/schedule/policy/voice), not copied agent loops.

**Tech Stack:** Python 3.12 (Qubit runtime), existing `AIAgent`/`run_conversation`, argparse subcommands, OpenJarvis Python 3.10–3.13 + FastAPI + PyO3 Rust engine (external, optional), Apache-2.0.

**Spec:** `docs/superpowers/specs/2026-10-26-runtime-boundary-and-openjarvis-design.md`

**Phase 1 detailed plan:** `docs/superpowers/plans/2026-10-26-phase-1-runtime-port-native-backend.md`

## Global Constraints

- **Gate P0:** do not start P1 until the current milestone is signed off: full matrix green or every remaining failure documented and owner-approved, live gateway/bridge restarted on the pushed commit, `docs/STATUS.md` updated, `canonical/main` pushed.
- Default engine stays `native`; existing chat/oneshot/gateway behaviour is unchanged until P3.
- No `openjarvis` import inside the Qubit process; the backend crosses a process/HTTP boundary.
- No vendoring of OpenJarvis; no file enters `qubit_runtime/` without a `THIRD_PARTY_NOTICES.md` record (repo, pinned commit, paths, license, local changes).
- Never rename `HERMES_*`/`.hermes`/container `/root/.hermes`/plugin-SDK compat globals/attribution as part of this program; P8 needs per-item owner sign-off.
- One `git worktree` per implementing agent, commit each coherent unit, `wip:` when unfinished (AGENTS.md).
- Implementation-first cadence: focused checks while building; one full matrix per phase gate; no release verdict from this program.
- Voice-commandability is part of acceptance for every user-facing recipe (owner directive).
- Do not add a second product server surface; OpenJarvis's server is an engine dependency behind the port.

## Review Focus

- **Engine absent or unhealthy:** `--engine openjarvis` must refuse with a typed, actionable message (`qubit runtime engines`, install/pin hint) and never fall back silently to native.
- **Cancellation during streaming:** a cancel mid-turn must emit exactly one terminal `error` (never `done`) and leave no child process; native and OpenJarvis adapters share the contract test.
- **Session leakage across engines:** a session id created by one backend must not be silently resumed by another; the port returns a typed `unknown_session` refusal.
- **Tool/approval parity:** a borrowed recipe must use Qubit's approval/secret surfaces; it must not bypass them with OpenJarvis's own approval path.
- **Licensing drift:** any copied/adapted OpenJarvis prompt, connector or eval code must have its provenance record before merge.

---

## Phase 0 — Sign-off gate (current milestone)

- [ ] Full non-gateway matrix: every failure fixed or documented as host/load with a green alone-rerun; Windows out of scope by owner.
- [ ] Gateway matrix: complete, failures accounted for and re-verified.
- [ ] Frontend `vitest`/`tsc`, Docker 3 files, E2E 4 probes, live web/Telegram evidence recorded in `docs/STATUS.md`.
- [ ] `canonical/main` pushed; shared worktree fast-forwarded; gateway+bridge restarted on that commit; identify ready; `/chat` 200.
- [ ] `docs/OPEN_THREADS.md` carries this program as the next thread.

**Gate (owner decision 2026-10-26):** the single clean full-matrix run is **waived**; the
recorded split-pass evidence (completed full pass + every failing file re-run green alone),
plus the live browser/Telegram evidence, is accepted. Windows stays out of scope. `VERIFIED`
remains unclaimed because the matrix was not one uninterrupted pass. This plan now starts.

## Phase 1b — Hermes/OpenClaw skill sources (✅ implemented 2026-10-26, `4d768d778`)

Ported from `open-jarvis/OpenJarvis@0583901` `src/openjarvis/skills/sources/`
(Apache-2.0, record in `THIRD_PARTY_NOTICES.md`). Do **not** replace
`tools/skills_hub_*`: our hub owns auth, taps, cache, trust, SSRF guards, bundle
validation and the installer. Add two `SkillSource` adapters:

- `hermes` — clone/pull `https://github.com/NousResearch/hermes-agent.git`, walk
  `skills/<category>/<skill>/SKILL.md`, frontmatter preview, commit SHA.
- `openclaw` — clone/pull `https://github.com/openclaw/skills.git`, walk
  `skills/<owner>/<skill>/SKILL.md`, `_meta.json` sidecar, commit SHA.

Both: lazy sync, best-effort refresh (stale cache + warning on pull failure;
first clone must succeed), identifiers `hermes:<name>` and
`openclaw:<owner>/<name>` (bare `<name>` accepted when unique), community
trust, no network in tests (local git repos).

Detailed plan: `docs/superpowers/plans/2026-10-26-hermes-openclaw-skill-sources.md`.

**Acceptance:** `tests/tools/test_skills_hub_git_sources.py` green; a local
Hermes/OpenClaw repo search/inspect/fetch/refresh works; `create_source_router`
lists both; `qubit skills install hermes:<name>` works against the local repo.

## Phase 1 — RuntimePort + native backend (detailed)

Self-contained and testable. Produces the typed port, the native adapter, a
registry, and a diagnostic CLI. No product caller changes.

Detailed plan: `docs/superpowers/plans/2026-10-26-phase-1-runtime-port-native-backend.md`.

**Acceptance:**
- `python -m pytest tests/runtime -q` green.
- `qubit runtime engines` lists `native`.
- `qubit runtime run --engine native --prompt "Reply with exactly: PORT_OK" --json` returns a `done` event with `PORT_OK` (live model) and a typed refusal for an unknown engine.
- Focused chat/oneshot tests remain green (no behaviour change).

## Phase 2 — OpenJarvis backend (out-of-process)

Write its own plan after P1. Scope:

- Pin an OpenJarvis commit; record repo/paths/license/local changes in `THIRD_PARTY_NOTICES.md`.
- Pin its interface: `jarvis ask --json` and/or its FastAPI agent/session/WS routes (`src/openjarvis/server/api_routes.py`, `ws_bridge.py`). Contract test against the pinned version.
- Implement `agent/runtime_openjarvis.py`: spawn/serve with an isolated home; map request/events/cancel; typed refusal when the binary/commit is absent or mismatched.
- No OpenJarvis import in-process; no cross-engine session store.

**Acceptance:** same prompt through both engines yields the port event sequence; missing/mismatched OpenJarvis refuses; no child left after cancel; focused runtime tests green.

## Phase 3 — Route a product caller through the port (native only)

- Move `qubit chat`/oneshot (choose the simpler oneshot path first) behind
  `RuntimePort` with the native backend; add `--engine` with default `native`.
- Reconcile streaming with the existing `stream_callback`/TUI event bus.
- Keep every existing focused chat/oneshot test green.

**Acceptance:** oneshot/chat tests unchanged; `--engine native` is byte-equivalent; `--engine openjarvis` works from P2.

## Phase 4 — Recipes (the 8-agent mapping)

Write one plan per recipe group. Start with `morning_digest` (cron + voice +
mail/calendar/health), then `monitor_operative`, then `deep_research` if it
beats current parity. Each recipe:

- config file/preset (prompt, tools, schedule, memory/approval policy),
- a voice command and dashboard entry,
- tests for the recipe contract and the refusal paths,
- OpenJarvis prompt/policy text reused only with provenance.

Aliases (`orchestrator`, `native_react`, `operative`, `native_openhands`,
`simple`) map to existing Qubit behaviour; no new loop.

## Phase 5 — Connector pack

Per connector: contract test, OAuth/token storage via existing secret paths,
typed missing-credential refusal, and a live probe where possible. Order:
Google Contacts → Apple Calendar → Apple Contacts → Apple Health → Apple Music
→ Dropbox. Each is a separate bounded ticket.

## Phase 6 — Traces + learning, evals/bench

- Trace store schema + retention + redaction tests.
- One optimization run over local traces (DSPy-style; reuse OpenJarvis code with
  provenance) behind an explicit command.
- Eval harness reporting accuracy/energy/FLOPs/latency/cost; opt-in, no default
  telemetry.

## Phase 7 — Rust local engine (optional provider)

Expose OpenJarvis's engine adapters (llama.cpp, Ollama, OpenAI-compatible) as
one optional local provider behind the port. Capability-gated, no default
change, typed refusal when the extension is absent.

## Phase 8 — Thin the vendored internals

Only after P1–P7:

- Measured inventory of `HERMES_*`, `.hermes`, plugin-SDK globals, container and
  remote paths, grouped `adapter | attribution | internal contract | dead`.
- For each group: dual-publish + read-old, move behind the port, or delete with
  an owner ruling recorded in `docs/DECISIONS.md`.
- No deletion without a green matrix and an owner sign-off row.

**Acceptance:** inventory regenerated; every deletion has a ruling; product
surfaces still zero-brand; no runtime regression.

---

## Effort and sequencing (rough)

| Phase | Effort | Why |
|---|---|---|
| P1 | 2–3 days | small typed surface + adapter + tests |
| P2 | 1–2 weeks | pin/observe OpenJarvis interface, process lifecycle, cancellation |
| P3 | 1 week | streaming/event reconciliation, no behaviour change |
| P4 | 1–2 weeks per recipe | cron/voice/connectors/policies; morning digest first |
| P5 | 3–5 days per connector | OAuth, storage, live probes |
| P6 | 1–2 weeks | trace schema, optimization, eval harness |
| P7 | 1 week | extension packaging + provider registration |
| P8 | weeks–months | measured, owner-gated, feature-preserving |

## Execution handoff

Phase 1 is fully specified and can start as soon as the P0 gate closes. Later
phases are intentionally not task-decomposed yet: their interfaces depend on
P1/P2 and on the pinned OpenJarvis contract.
