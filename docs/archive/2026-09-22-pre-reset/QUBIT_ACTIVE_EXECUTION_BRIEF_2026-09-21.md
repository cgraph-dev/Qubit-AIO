# Qubit Active Execution Brief — 2026-09-21

**Status:** PRIMARY AUTHORITATIVE CONTEXT. Every future agent reads this first.
**Verdict right now:** NOT_READY. Do not claim completion, 100%, 1:1, or VERIFIED.
**Commit/push permitted:** NO (see §14).

This brief is the single entry point. Frozen contracts live under
`docs/archive/2026-09-21/` (preserved, authoritative by content). Supporting
contracts created 2026-09-21 sit next to this file. The differential interface
contract is `docs/differential/INTERFACE.md`.

---

## 1. Current project purpose

Qubit VoiceBridge is a local-first, voice-driven agent for the Qubit Agent
runtime. It vendors a fork of Nous Research's Hermes agent (pinned, MIT) under
`qubit_runtime/`, deep-rebrands it to Qubit, exposes the same canonical
operations through text (`/chat`, `/qubit-chat`), CLI, dashboard, and voice, and
must reach behavioral parity with the supported Hermes and OpenClaw capability
sets — not merely source similarity. The owner acceptance criteria and the
authority order are frozen in `docs/archive/2026-09-21/`
(`QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md`, `QUBIT_FULL_PARITY_CONTRACT.md`,
`QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md`, the `QUBIT_OWNER_AMENDMENT_*`
series). Public identity is Qubit / Qubit Labs.

Qubit is one unified agent and one source tree. Hermes and OpenClaw are
upstream reference implementations and capability sources, not separate
runtime authorities that Qubit launches or routes to as independent agents.
The renamed runtime, VoiceBridge, OpenClaw-compatible adapters, providers,
skills, plugins, tools, dashboard, CLI, and voice surfaces operate through the
same Qubit runtime, broker, policy, audit, e-stop, and state authority.

Upstream names are permitted in internal comparison, provenance, legal,
migration, and compatibility records only. They must not leak into the public
Qubit UI, CLI help, logs, errors, banners, bundles, or spoken responses.

## 2. Current architecture

- **Single agent runtime:** the vendored fork under `qubit_runtime/` is the one
  agent loop (renamed from `qubit_hermes/`). No second runtime, listener,
  router, or action store.
- **Backend API layer:** `voicebridge/qubit_compat.py` is the Qubit-owned route
  and handler layer over the vendored runtime.
- **Dashboard SPA:** `frontend/qubit-web/` (built output `frontend/qubit-dist/`).
- **Shared client library:** `frontend/qubit-shared/`.
- **Voice pipeline:** `voicebridge/orchestrator.py` (mic -> ASR -> classify ->
  route -> worker -> speak) plus `voicebridge/asr/`, `voicebridge/tts/`.
- **Right-Ctrl control:** the global voice/control trigger must connect to the
  same orchestrator, canonical operation resolver, broker, policy, audit, and
  e-stop path as `/qubit-chat`; its live state and failures must appear in the
  `/qubit-chat` UI.
- **Provider/model routing:** `voicebridge/providers/resolver.py`.
- **State:** vendored `qubit_state.SessionDB` (SQLite) behind a Qubit adapter.
- **Distributions:** `dist/` holds wheels/sdists; artifacts are regenerable.

## 3. Current repository location

`/home/trick/Projects/Repos/qubit-voicebridge` (git repo, branch `main`).

## 4. `/chat` behavior

`/chat` is a dashboard SPA route that renders the **real vendored terminal**
(`frontend/qubit-web/src/pages/TerminalChatPage.tsx`), streamed over the one
Qubit-owned WebSocket path `/agent/api/pty` (ticket minted by
`POST /agent/api/auth/ws-ticket`; server `voicebridge/webui.py`,
`voicebridge/pty_ws.py`). It spawns the vendored TUI behind a PTY. Upstream raw
WS paths (`/api/pty`, `/api/console`, `/api/ws`, `/api/events`, `/api/pub`,
`/api/audio/speak-stream`) are typed-gated/refused (`voicebridge/webui.py`).
`/chat` is a terminal surface; it is not the agent-turn API.

## 5. `/qubit-chat` behavior

`/qubit-chat` is a **built-in** dashboard SPA route (not a plugin). Its backend
is `POST /api/qubit/chat/stream` -> `h_chat_stream`
(`voicebridge/qubit_compat.py`) -> `voicebridge/chat_transport.py`
(`stream_turn`); the non-streaming form is `POST /api/qubit/chat` ->
`h_chat_turn`. It runs an agent turn rather than a raw PTY. There is **no
`qubit-chat` plugin**; that plugin was removed by the 2026-09-17 owner chat
mapping and must not be resurrected as a shadow backend.

## 6. Brain4 local-first behavior

The default chat model is **`qubit-brain4`** (local): `voicebridge/chat_transport.py:42`
(`DEFAULT_MODEL`), `voicebridge/providers/resolver.py:46,216-226`
(`DEFAULT_LOCAL_ID`, `kind=LOCAL`, `provider="ollama"`, `egress_consent="deny"`).
`/qubit-chat` with no explicit model resolves to this local Brain4 profile.
Note the recorded provenance inconsistency: `Modelfile.qubit` derives from a
llama3.2-abliterate 3B base while `voicebridge/local_llm.py` and the resolver
describe Brain4 as Qwen3-4B-derived; `Modelfile.qubit4` is the Qwen3-4B
artifact. Treat the shipped `.qubit` base as UNKNOWN/unresolved.

## 7. API escalation behavior

Cloud is **disabled by default**: `frontier.enabled=False`, `route="local"`
(`voicebridge/config.py:392-394`). Route `auto` is opt-in escalation and the
gateway "never escalates on untrusted text" (`voicebridge/providers/gateway.py`);
explicit `frontier` fails closed. Voice has a separate escalation chain
(local reply first, then configured API models, then ollama) gated by the same
policy. Escalation must preserve session and audit continuity — never fork a
second session or silently drop audit.

## 8. Voice architecture

Pipeline: mic -> ASR -> classify -> route -> worker -> speak
(`voicebridge/orchestrator.py`). STT default **sherpa**/parakeet with moonshine
fallback (`voicebridge/config.py:256-266`); whisper is an optional adapter. TTS
default **piper** (`voicebridge/config.py:312-313`); adapters
piper/hybrid/kokoro/edge/openai_tts. The **realtime/duplex lane was removed**:
an OpenAI Realtime profile is still resolvable/logged but the orchestrator
starts no adapter (`voicebridge/orchestrator.py:659-669`). Speech roles are
replaceable, but only through the single registry.

Speech input is selected independently through `voice.input_engine` with the
values `browser`, `local`, or `auto`. Browser mode uses supported Chrome
SpeechRecognition when available and permitted; local mode uses Qubit's STT
profile; auto mode prefers browser recognition and falls back to local STT only
with an explicit visible reason. The `/qubit-chat` UI must show the active
engine, listening state, permission failure, privacy/network status, and
fallback reason. This setting changes speech input only, not the model,
session, tools, policy, audit, confirmation, or e-stop path.

Baseline voice actionability is required during parity work. Later Brain4
training and voice optimization may improve recognition, routing quality,
latency, natural-language coverage, and escalation decisions; it must not defer
the baseline voice path or create a second backend. Right-Ctrl is a first-class
`/qubit-chat` control and must be verified through the same canonical path.

## 9. Single runtime/session/store authority

- Runtime authority: the vendored `qubit_runtime/` loop, resolved by
  `voicebridge/qubit_runtime.py`.
- Session/store authority: **`QubitSessionStore`** over the vendored
  `qubit_state.SessionDB` (SQLite `state.db`) —
  `voicebridge/qubit_session_store.py` (`AUTHORITY`), enforced in
  `voicebridge/chat_transport.py` and `voicebridge/qubit_compat.py` (drift
  fails closed). TUI, `/qubit-chat`, CLI, dashboard, and voice must all read
  and write this one store. Do not add another session store, event store,
  policy surface, or broker.
- Provider/model registry authority: the single `<state_dir>/profiles.json`
  (`model_profiles` key), not per-surface registries.

## 10. Current Hermes parity status

Contractual Hermes pin `2237be355906fbe6065ce1815711eee52b2d646e` (tag
v2026.9.7). Latest Hermes revision observed 2026-09-21 via live `git ls-remote`:
tag `v2026.9.14` = `345cd2b057a452236de401d3534b8502a7465e8d`, main
`bb359ec5c1d5da6a66f00ee11f0ad7a86defaee2` (moving ref; the earlier recorded
`5bb314fa…` is superseded — it is 5 commits behind the live head). A
latest-Hermes comparison **now exists** as
`QUBIT_HERMES_LATEST_DELTA_2026-09-21.{md,json}`: 118 capabilities classified
(1 IMPLEMENTED, 104 PARTIAL, 13 MISSING in that artifact's delta vocabulary — a
documented delta, **not** behavioral parity). Its measured volume: pin -> release
3695 files / 2025 commits; pin -> main 7220 files / 7070 commits. Latest-Hermes
behavioral parity remains **NOT VERIFIED** — Qubit has no counterpart for most of
the new surface. The delta artifact also measures 66 (pin) / 67 (release) / 69
(main) add_parser-derived top-level CLI families; the brief's "72" (§13 item 5)
uses a different counting method. Ledger: 573 rows, 461 IMPLEMENTED, 112
SAFE_EQUIVALENT, 0 OPERATOR_ONLY (reported code-side open 0). This is a ledger
claim, not independently verified; the byte-parity gate currently fails closed
because the pinned byte manifest is absent.

## 11. Current OpenClaw parity status

Contractual pin `3928bad9badfcb6c7d140530435e806fb8092190` (v2026.9.2);
observed ledger pin `3a9d69db306cd7f081e06254cb89c4bcc14a7107` (v2026.9.4);
latest release v2026.9.5 `ec9c1a13db8938e5a3eaa51fca2e981cde2395a9`; latest main
`395690e83c32a8e0547211a9b88a37470cf30d8b`. Ledger: 104 rows, 94 IMPLEMENTED,
2 SAFE_EQUIVALENT, 8 OPERATOR_ONLY. **Contractual-pin method surface
(regenerated 2026-09-21):** `QUBIT_OPENCLAW_UPGRADE_MANIFEST.json`
`pinned_surface_disposition` = IMPLEMENTED **252**, SAFE_EQUIVALENT **87**,
OPERATOR_ONLY **95**, OPEN **0** (434/434 disposed). Real handlers now delegate
to canonical Qubit modules from `voicebridge/gateway/openclaw_handlers/`
(`sessions`, `sessions_advanced`, `sessions_compaction`, `security`, `agents`,
`mcp_app`, `board`, `system`, `content`, `worktrees`, `surface_control`,
`users`, `skills_extra`, `talk_node`, `scheduler_media`, `misc_surface`), and
the deterministic generator scans those files alongside `rpc.py`. The
Phase-2 increment (2026-09-21) disposed the final 102: 47 SAFE_EQUIVALENT
(real canonical counterpart, differing contract), 54 OPERATOR_ONLY (typed
refusal naming an exact prerequisite + local counterpart), 1 IMPLEMENTED
(`tts.setProvider`, real persisted `tts.provider`). The 54 OPERATOR_ONLY rows
split into external-prerequisite rows (GitHub/OAuth device flows, an OpenAI
Realtime transport, paired node devices, systemd/launchd process ownership, a
hosted portal service, a live native-harness relay, a remote verdict registry)
and architecture-gated rows where Qubit intentionally runs no upstream server
registry/transport (cron scratch, task-suggestion registry, mention inbox,
chunked skill-upload staging, wake-trigger store). The architecture-gated
subset is a terminal disposition per the manifest policy but is flagged for
owner review: it is not the same as an externally-provisioned prerequisite.
The merged ledger's "code-side open 0" now also covers the method surface.
The advanced-sessions increment (2026-09-21) disposed 21 methods: goals,
suggestions, typing, single-operator owner/plugin metadata, reset/rewind/cleanup
(real `SessionDB` primitives), session-group catalog, and the request-scoped
subscription/viewer registry (`SAFE_EQUIVALENT`, upstream server-push).
The sessions-compaction increment (2026-09-21) then disposed the final 7,
closing the whole sessions family to **0 OPEN**: `sessions.compact` (real
`archive_and_compact` + vendored `ContextCompressor`), checkpoint
`branch`/`restore` and `branches.switch` (Qubit checkpoint registry in the same
`state.db`, `SAFE_EQUIVALENT`), and `companion.ask/state/reset` (real companion
session answered through `chat_transport.turn`).
The delta overlay (`disposition_overlay`, scope = latest_release ∪ latest_main
additions) regenerated 2026-09-21 shows **OPEN 0** (26 IMPLEMENTED, 12
SAFE_EQUIVALENT, 7 OPERATOR_ONLY), so the earlier "13 latest-main additions
undisposed" claim is stale. Treat OpenClaw as NOT code-complete.

## 12. Current deep-rebrand status

Deep source rebrand is authorized (owner amendment 2026-09-19_DEEP_REBRAND) and
in progress: `qubit_hermes/` -> `qubit_runtime/`, `HERMES_HOME` -> `QUBIT_HOME`,
frontend packages `qubit-*`, primary launcher `qubit` (alias `voicebridge`).
Preserved wire identifiers (allowed, must not leak to users):
`__HERMES_BASE_PATH__`, `__HERMES_SESSION_TOKEN__`, `X-Hermes-Session-Token`,
`hermes_kanban`, `HERMES_QWEN_`, `qubit-hermes`. **Re-audited 2026-09-21:**
`qubit_runtime/pyproject.toml` is now `name = "qubit-agent"` (Qubit Labs
authors; `qubit`/`qubit-agent`/`qubit-acp` scripts); `banner.py` renders a plain
Qubit version label (upstream URL retained only for the update/provenance path);
`debug.py` upstream URLs are unused provenance. Two real user-visible leaks were
fixed (`qubit_cli/relaunch.py` error string; the spoken `hermes kanban`
alias). The debrand manifest and classification were regenerated
deterministically (`scripts/qubit_debrand.py --report-only` / `--classify`).
`QUBIT_IDENTIFIER_RENAME.json` is now reproduced by the in-repo deterministic
generator `scripts/identifier_rename.py` (`--check` passes at the current
revision: 33 file renames; guarded by
`tests/test_identifier_rename_generator9u.py`). Remaining rebrand gap: the
frozen allowlist (rows 72–75) permits the DocsPage/kanban upstream docs URLs and
the achievements `@NousResearch` handle, which the 2026-09-21 policy §1.2
forbids as URLs/labels on public surfaces. `tests/test_branding_bundle_scan9u.py`
fails on this conflict and `tests/test_branding_leaks_fixes.py` asserts the label
present; it needs an owner ruling
(`QUBIT_OWNER_RULING_REQUEST_2026-09-21.md`).
See `QUBIT_REBRAND_AND_PUBLIC_SURFACE_POLICY_2026-09-21.md`.

## 13. Known blockers

Code-side (must be closed before code readiness can be re-asserted):
1. OpenClaw contractual-pin methods: **434/434 disposed, 0 OPEN** (252
   IMPLEMENTED, 87 SAFE_EQUIVALENT, 95 OPERATOR_ONLY) as of 2026-09-21. This is
   a code-side disposition, not behavioral parity: the 95 OPERATOR_ONLY rows are
   typed refusals naming a prerequisite, and the architecture-gated subset (no
   upstream server registry/transport) needs owner review. The latest-main
   delta additions remain disposed (delta overlay OPEN 0).
2. Rebrand leaks — **largely repaired 2026-09-21** (§12): pyproject/banner/debug
   re-audited clean, two real leaks fixed, debrand manifests regenerated, and the
   missing deterministic `scripts/identifier_rename.py` generator added
   (`--check` current, 33 file renames). Remaining: the frozen-allowlist-vs-policy
   conflict on the DocsPage/kanban upstream URLs and the achievements
   `@NousResearch` handle — an owner ruling
   (`QUBIT_OWNER_RULING_REQUEST_2026-09-21.md`), not a code fix.
3. `_agent_tool` planner dispatch — **repaired 2026-09-21**: now dispatches the
   true origin `source="model"` through the shared broker (`voicebridge/
   orchestrator.py`), so reversible planner tools run with correct audit
   attribution and mutating/destructive tools fail closed
   (`CONFIRMATION_SOURCE_FORBIDDEN`) until a real user confirmation exists.
   Regression: `tests/test_agent_tool_source8u.py`.
4. Context-floor guard — **repaired 2026-09-21**: `terminal_context_guard` now
   cross-checks the real model window via the vendored resolver
   (`terminal.context_window_verification`: `when_available` default / `required`
   / `declared`), refusing a profile whose real window is below the floor and
   flagging unverifiable windows. **Further repaired 2026-09-21:** the vendored
   resolver's generic fallback window (`DEFAULT_FALLBACK_CONTEXT`) was being
   trusted as a real window, so an unknown model failed *open* in `required`
   mode; `voicebridge/providers/resolver.py` now treats that fallback as
   unverified (typed `TERMINAL_CONTEXT_UNVERIFIED`, HTTP 503 at both gates).
   Tests: `tests/test_terminal_context_floor.py`,
   `tests/test_chat_terminal_parity9u.py`.
5. Generic `qubit agent <args>` passthrough families — **re-verified
   2026-09-21:** all 72 vendored-fork top-level CLI families are classified
   (native command, typed native refusal, or classified forward), 0 generic
   passthrough (`tests/test_cli_family_forward_wave6.py`,
   `tests/test_cli_family_parity9u.py`). No new top-level command was added;
   the refusals each name a live `qubit` counterpart.
6. Dashboard/control coverage — the machine-readable artifact
   `docs/QUBIT_PAGE_COVERAGE_2026-09-21.json` (regenerated by the in-repo
   deterministic `scripts/page_coverage.py`; `--check` passes at HEAD
   `f984911`) records **3199 OPEN**, 156 SAFE_EQUIVALENT, 0 IMPLEMENTED (3355
   rows total) because no real-backend E2E chain has been executed. The
   2026-09-17 "281 open" figure and the earlier 3288-OPEN snapshot are
   superseded by this artifact. Closing it requires the final
   browser/Playwright matrix.
7. Full final matrix not run; 2 unit tests fail on the absent byte manifest and
   the operator-only exit check.
8. Canonical-authority gaps — the read-only `QUBIT_AUTHORITY_MATRIX_2026-09-21.md`
   recorded 7 reachable GAPs (plus 1 latent and 1 root cause); **G1–G6 and R1
   were repaired after that audit**: all three remaining direct web-fetch callers
   (CLI `qubit websites`, gateway `controlUi.linkPreview`, voice `_b_websites`)
   now dispatch `web.*` through the shared broker, and the dashboard file writes
   (`fs/write-text`, `upload`/`upload-stream`, `files/mkdir`) now run through the
   canonical broker with a durable single-use confirmation. The operator-facing
   CLI `qubit files patch|rollback` (G7) still calls `file_patch.apply_patch`
   directly with a boolean, and the dead `local/actions.py:execute` guard (L1)
   remains. Evidence: `tests/test_web_broker_routing9u.py`,
   `tests/test_file_write_broker_authority.py`.
9. Owner rulings pending — `QUBIT_OWNER_RULING_REQUEST_2026-09-21.md` lists the
   frozen-allowlist-vs-policy branding conflict, the `/` redirect target (`/chat`
   in policy, `/sessions` live), and confirmation of the 95 OPERATOR_ONLY
   OpenClaw rows (especially the architecture-gated subset). None may be treated
   as terminal until the owner decides.

Operator-only (not code-side; require live prerequisites):
- Provide the pinned byte manifest (`QUBIT_DIFFERENTIAL_BYTE_MANIFEST`).
- Live differential transport environment.
- A real ≥65,536-token `/chat` credential for real-provider E2E.
- Host hygiene: stop the stray `:9119` listener, run a single Qubit instance,
  remove the stale unit.
- Hardware/credential qualification of the 11 OPERATOR_ONLY rows.
- Electron desktop build.

## 14. Operator-only requirements and canonical commands

Canonical commands (do not invent others):
- `qubit` — primary launcher (alias `voicebridge`).
- `graft ask "<question>" --source`, `graft grep`, `graft skeleton`,
  `graft callers`, `graft map`, `graft build`, `graft check` — repo context.
- Differential runner: `scripts/differential_parity.py` (frozen interface
  `docs/differential/INTERFACE.md`; exit codes 0/1/2/3/4).
- Tri-surface parity: `scripts/tri_surface_parity.py`.
- Deterministic generators: `scripts/source_inventory.py`,
  `scripts/hermes_vendor_manifest.py`, `scripts/openclaw_upgrade_manifest.py`,
  `scripts/config_binding_manifest.py`, `scripts/route_ownership_matrix.py`,
  `scripts/qubit_rebrand.py`, `scripts/qubit_debrand.py`.
- Backend tests: `pytest` (per `pytest.ini`). Frontend: `npm run typecheck`,
  `npm run lint`, `npm test` (vitest), `npm run build`, Playwright.
- Graft reindex: `graft build` (deterministic, no key, $0).

## 15. Files that must not be changed

- Everything under `docs/archive/2026-09-21/` that is a frozen contract, owner
  amendment, or legal/provenance record (see its `README.md`).
- `docs/differential/INTERFACE.md` (frozen differential interface).
- `THIRD_PARTY_NOTICES.md`, `LICENSE`, and upstream license/provenance records.
- Vendored upstream trees and generated manifests, except via their
  deterministic generators.
- Any frozen contract requirement (changing one requires owner approval).

## 16. Evidence and completion rules

- Parity means behavior, not route/button/registry existence. A capability is
  complete only with an end-to-end behavioral chain: input/control ->
  authentication -> canonical Qubit operation -> policy/broker/e-stop -> real
  runtime/provider -> persistent state/event -> correct text/dashboard/voice
  result -> error/retry/cancel/recovery/audit behavior.
- Dispositions are exactly IMPLEMENTED, SAFE_EQUIVALENT, OPERATOR_ONLY, OPEN.
  A typed refusal with no working allowed counterpart is not a closure.
- No claim may be made from a route returning 200, a button existing, a catalog
  entry existing, or a test name existing.
- Unknown values are written UNKNOWN / NOT VERIFIED. Do not invent counts.
- `VERIFIED` is forbidden unless every gate executed, no code-side OPEN remains,
  no artifact is fabricated, and an independent read-only verifier reproduced
  the evidence. See `QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md`.
- Do not trust prior agent reports (including this brief's status section)
  without re-checking the current worktree, ledgers, and manifests.

## 17. Next implementation priority

The OpenClaw contractual-pin method surface is now **code-side disposed
(434/434, 0 OPEN)** and the `QUBIT_IDENTIFIER_RENAME.json` generator gap is
closed (`scripts/identifier_rename.py`). Next: close the dashboard/control E2E
coverage, then run the frozen final matrix once. Do not start release packaging
or independent verification until implementation is frozen. The 95
OPERATOR_ONLY rows (especially the architecture-gated subset), the
allowlist/policy branding conflict, and the `/` redirect target require the
owner rulings in `QUBIT_OWNER_RULING_REQUEST_2026-09-21.md` before any
disposition can be treated as behavioral parity.

## 18. Commit and push rules

NO commit and NO push now. The worktree is mid deep-rebrand and dirty, the final
matrix has not run, and the implementation-first amendment forbids commit/push
before an independent verifier passes. Commit/push only after explicit owner
instruction and a passing final matrix.

---

## Current truth (2026-09-21)

| Item | Value |
|---|---|
| HEAD | `f984911c81e119fe6fe1220510b8ac324c1224a4` |
| Branch | `main` |
| Dirty / untracked | YES — worktree mid deep-rebrand; renames, deletions, additions and untracked files present |
| Hermes contractual pin | `2237be355906fbe6065ce1815711eee52b2d646e` (v2026.9.7) |
| Hermes latest revision | determined 2026-09-21 from live `git ls-remote`: latest tag v2026.9.14 = `345cd2b057a452236de401d3534b8502a7465e8d`; latest main = `bb359ec5c1d5da6a66f00ee11f0ad7a86defaee2` (moving ref; `5bb314fa…` superseded — 5 commits behind; prior `3b7eda08…` and `782886f8…` also superseded) |
| Hermes latest-delta map | `QUBIT_HERMES_LATEST_DELTA_2026-09-21.{md,json}`: 118 capabilities — 1 IMPLEMENTED, 104 PARTIAL, 13 MISSING (delta vocabulary); pin→release 3695 files/2025 commits, pin→main 7220 files/7070 commits; 66/67/69 add_parser CLI families. Documented delta, **not** behavioral parity |
| OpenClaw contractual pin | `3928bad9badfcb6c7d140530435e806fb8092190` (v2026.9.2) |
| OpenClaw observed ledger pin | `3a9d69db306cd7f081e06254cb89c4bcc14a7107` (v2026.9.4) |
| OpenClaw latest release / main | v2026.9.5 `ec9c1a13db8938e5a3eaa51fca2e981cde2395a9` / `395690e83c32a8e0547211a9b88a37470cf30d8b` (refreshed 2026-09-21; prior recorded `c396abe6…` / `135f934d…` were stale — `135f934d…` no longer exists upstream) |
| OpenClaw latest-main method delta | live main surface 479 methods vs pin 434 → 45 added; the manifest delta union covers 31 (release) ∪ 45 (main); the regenerated delta overlay now records **OPEN 0** (26 IMPLEMENTED, 12 SAFE_EQUIVALENT, 7 OPERATOR_ONLY), so the earlier "13 latest-main additions undisposed" claim is stale |
| Stale docs/manifest paths | reorg left references unresolved: measured **72 files / 455 occurrences** of `docs/QUBIT_*`/`docs/HERMES_*`/`docs/INVENTORY_*` (audit said 62); 10 focused test failures (`test_openclaw_surface_coverage8u.py` 6, `test_branding_parity_wave3.py` 3, one unrelated branding-provenance test). Canonical location (archive vs docs/) still requires an owner decision; no archive file was moved |
| Hermes counts | total 573 — 461 IMPLEMENTED, 112 SAFE_EQUIVALENT, 0 OPERATOR_ONLY |
| OpenClaw counts | total 104 — 94 IMPLEMENTED, 2 SAFE_EQUIVALENT, 8 OPERATOR_ONLY |
| OpenClaw contractual-pin methods | total 434 — **252 IMPLEMENTED, 87 SAFE_EQUIVALENT, 95 OPERATOR_ONLY, 0 OPEN** (regenerated manifest 2026-09-21; handlers in `voicebridge/gateway/openclaw_handlers/`). Code-side disposition only; 95 OPERATOR_ONLY need owner review for behavioral parity |
| Qubit counts | total 746 — 707 IMPLEMENTED, 36 SAFE_EQUIVALENT, 3 OPERATOR_ONLY |
| Page/control coverage | `docs/QUBIT_PAGE_COVERAGE_2026-09-21.json` (generator `scripts/page_coverage.py`, `--check` passes at `f984911`): total 3355 — 3199 OPEN, 156 SAFE_EQUIVALENT, 0 IMPLEMENTED. No browser/E2E run |
| Canonical-authority matrix | `QUBIT_AUTHORITY_MATRIX_2026-09-21.md` (audit snapshot): 7 reachable GAPs + 1 latent + 1 root cause. Post-audit repair fixed G1–G6 and R1 (web fetch, dashboard file writes); G7 (CLI `qubit files patch|rollback`, operator-facing) and L1 (`local/actions.py:execute`, dead) remain. Tests: `test_web_broker_routing9u.py`, `test_file_write_broker_authority.py` |
| Branding scan status | Conflict unresolved: the shipped bundle carries the DocsPage/kanban upstream docs URLs and the achievements `@NousResearch` handle. Frozen allowlist rows 72–75 permit them; policy §1.2/§3.1 forbid. `test_branding_bundle_scan9u.py` fails; `test_branding_leaks_fixes.py` asserts them present. Owner ruling pending |
| Owner rulings pending | `QUBIT_OWNER_RULING_REQUEST_2026-09-21.md`: (1) allowlist/policy branding conflict (rows 72–75); (2) 95 OPERATOR_ONLY OpenClaw rows, esp. architecture-gated vs external-prerequisite; (3) `/` redirect target (`/chat` policy/allowlist vs `/sessions` live) |
| Merged ledger | total 1423 — 1262 IMPLEMENTED, 150 SAFE_EQUIVALENT, 11 OPERATOR_ONLY, code-side open 0 |
| Disposition counts | IMPLEMENTED 1262 / SAFE_EQUIVALENT 150 / OPERATOR_ONLY 11 / OPEN 0 (ledger) **plus 0 OPEN OpenClaw contractual-pin gateway methods** (434/434 disposed: 252/87/95, not covered by the ledger). The latest-main delta overlay is OPEN 0. |
| Test status | backend last recorded: 2 failed, 5514 passed, 7 skipped (failures: missing byte manifest; operator-only exit check). Frontend: tsc 0, eslint 0 errors/29 warnings, vitest 358 passed, build 0 |
| Build status | frontend build passes; byte-parity gate fails closed (manifest absent) |
| Latest reports | `/tmp/qubit-final-release/FINAL_REPORT.md` (2026-09-20) = CODE_READY_OPERATOR_ACTIONS_REMAIN; `/tmp/qubit-reconciliation-final/FINAL_RECONCILIATION.md` (2026-09-21) = NOT_READY — **they contradict** |
| Unresolved code-side blockers | dashboard/control rows needing real-backend E2E; operator-facing CLI file-patch (G7); 95 OPERATOR_ONLY OpenClaw rows awaiting owner confirmation; branding allowlist/policy conflict (owner ruling). **Repaired this session:** the OpenClaw pinned surface advanced 329→434/434 disposed (surface-control + Phase-2 families `users`, `skills_extra`, `talk_node`, `scheduler_media`, `misc_surface`: 252 IMPLEMENTED, 87 SAFE_EQUIVALENT, 95 OPERATOR_ONLY, 0 OPEN); added the missing in-repo deterministic `scripts/page_coverage.py` and `scripts/identifier_rename.py` generators (both `--check`-clean); routed the three remaining web-fetch callers and the dashboard file writes through the shared broker with a durable single-use confirmation; settled approvals confirm through the one shared journal; added the missing backend `voice.input_engine` config key with normalization; corrected stale latest-revision values in active docs; rebuilt the stale `frontend/qubit-dist` (now ships the /qubit-chat voice-status strip). Prior session repairs retained: `_agent_tool` planner dispatch uses `source="model"` through the broker; the `/chat` context guard fails closed on the vendored generic fallback. |
| Operator-only actions | byte manifest; differential transport; live `/chat` credential; host hygiene; 11 OPERATOR_ONLY rows; Electron desktop build |
| Commit/push permitted | NO |

Ledger `generated` metadata reads `2026-09-12` while the files' content/mtime is
2026-09-21 — treat the embedded date as stale metadata, not the data age.

## Plan reconciliation (2026-09-21)

The page-by-page plan (`QUBIT_PAGE_BY_PAGE_PARITY_EXECUTION_PLAN_2026-09-21.md`)
is the page/control execution framework. Its one-line OpenClaw item (§23) is
**gated** by the independent audit (`/tmp/qubit-verify-2026-09-21/`): the
contractual-pin surface was 433/434 OPEN at audit time, path drift breaks 10
focused tests, and the recorded latest revisions were stale. Order of work:
close the audit's code-side pre-conditions (path canonical-location decision,
upstream metadata, OpenClaw dispositions, planner/context fixes) **before**
page-by-page closure. Progress since: the method disposition advanced to 434/434
disposed (0 OPEN) and is not fabricated; the latest-revision values were
corrected and a latest-Hermes delta artifact now exists
(`QUBIT_HERMES_LATEST_DELTA_2026-09-21.{md,json}`); the identifier-rename
generator gap is closed; and the authority audit's G1–G6 and R1 were repaired
(web fetch through the broker; dashboard file writes through the broker with a
durable confirmation; approvals confirm settled through the shared journal).
Dashboard defects found and fixed this session: the `/qubit-chat` voice-status
engine/Right-Ctrl/privacy/fallback strip now renders (`ChatPage.tsx:1141`),
Right-Ctrl is gated on the active chat route (`ChatPage.tsx:997`), and the
`/chat` context guard treats the vendored generic fallback window as unverified
(`voicebridge/providers/resolver.py`). Still open before page-by-page closure:
the path canonical-location decision; the branding-scan allowlist/policy conflict
and the `/` redirect mismatch (both owner-gated); the operator-facing CLI
file-patch (G7); and E2E coverage (3199 OPEN rows). The 95 OPERATOR_ONLY rows
still need owner confirmation. All three open decisions are collected in
`QUBIT_OWNER_RULING_REQUEST_2026-09-21.md`.

**Sibling documents:** [QUBIT_CAPABILITY_PARITY_CONTRACT_2026-09-21.md](QUBIT_CAPABILITY_PARITY_CONTRACT_2026-09-21.md) ·
[QUBIT_VOICE_ACTIONABILITY_CONTRACT_2026-09-21.md](QUBIT_VOICE_ACTIONABILITY_CONTRACT_2026-09-21.md) ·
[QUBIT_REBRAND_AND_PUBLIC_SURFACE_POLICY_2026-09-21.md](QUBIT_REBRAND_AND_PUBLIC_SURFACE_POLICY_2026-09-21.md) ·
[QUBIT_UPSTREAM_SYNC_AND_ADAPTER_POLICY_2026-09-21.md](QUBIT_UPSTREAM_SYNC_AND_ADAPTER_POLICY_2026-09-21.md) ·
[QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md](QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md) ·
[QUBIT_HERMES_LATEST_DELTA_2026-09-21.md](QUBIT_HERMES_LATEST_DELTA_2026-09-21.md) ·
[QUBIT_AUTHORITY_MATRIX_2026-09-21.md](QUBIT_AUTHORITY_MATRIX_2026-09-21.md) ·
[QUBIT_OWNER_RULING_REQUEST_2026-09-21.md](QUBIT_OWNER_RULING_REQUEST_2026-09-21.md) ·
[QUBIT_DOCUMENTATION_INDEX_2026-09-21.md](QUBIT_DOCUMENTATION_INDEX_2026-09-21.md)
