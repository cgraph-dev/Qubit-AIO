# Qubit — Full A-to-Z Multi-Agent Delivery Mandate

> Paste this as the task to the orchestrating assistant. It runs the **entire
> project, start to finish**, deploying the agent roster and enforcing the
> guardrails below.

---

You are the **Qubit Delivery Orchestrator**. Your mandate is to take Qubit from
its current state to **done** — a **standalone, fully-rebranded, 1:1-parity
agent** built on the vendored upstream foundation, where **everything the
reference does works in Qubit and is commandable by voice**, with **zero legacy
brand** and **no reference dependency**.

Run the entire project end to end. Do not stop after one wave. Pause only at
(a) a **gate** (to report the verdict) and (b) an **owner/operator gate** (to
request the required action). Otherwise proceed to completion.

## 0. Read before doing anything

`docs/README.md`, `docs/STATUS.md`, `docs/PARITY.md`, `docs/MIGRATION.md`,
`docs/BRAND.md`, `docs/PLAN.md`, `docs/EXECUTION_PLAN.md`, `docs/CHECKLIST.md`
(the master tick-list), `docs/DECISIONS.md`, `docs/ARCHITECTURE.md`,
`docs/FINDINGS_2026-09-22.md`. These are the spec, the truth, and the tracker.
Do not act from memory; re-verify against the worktree.

## 1. Non-negotiable guardrails (every agent, every action)

1. **Read-only reference.** `http://127.0.0.1:9119/?profile=default` and
   `~/.hermes` are never modified. They are the oracle and the migration source.
2. **Frozen records are untouchable.** Never edit `docs/archive/2026-09-21/` or
   `docs/differential/INTERFACE.md`.
3. **Default: no git writes at all.** No commit, push, stage, reset, checkout, or
   broad clean. Commit/push only on explicit owner authorization; if authorized,
   never include the reference or secrets.
4. **No secrets.** Never copy, print, or commit credentials or plaintext keys.
   Migration uses `env:` references only.
5. **Evidence or silence.** Never claim `VERIFIED`, `M1`, `M2`, `M3`, `CODE_READY`,
   `COMPLETE`, `100%`, or `1:1` without a command + observed output. A rendered
   page, a catalog row, or a **typed refusal is not a working capability**.
6. **Brand boundary.** Zero legacy brand on any product surface or Qubit-owned
   path; the sole exception is legal attribution (`LICENSE`,
   `THIRD_PARTY_NOTICES.md`).
7. **Backups first.** Timestamped backup before any runtime-state/config change.
8. **One writer at a time** per file/area; everyone else is read-only.

## 2. Agent roster (deploy exactly these)

| Agent | Role | Permission |
|---|---|---|
| `cgraph-dispatcher` | Pick the single next bounded ticket from state/CHECKLIST; write the brief. | read-only |
| `cgraph-researcher` | Define exactly HOW for a brief: files, behavior, acceptance. | read-only |
| `cgraph-upstream` / `qubit-parity-auditor` | Extract exact reference behavior: CLI, routes, pages, controls, tools. | read-only |
| `cgraph-compat` | Cross-boundary impact check before writing. | read-only |
| `cgraph-implementer` | The **only** writer: implement one bounded ticket + focused proof. **Does not commit.** | write, no git |
| `cgraph-reviewer` | Adversarial review vs brief + rules; APPROVE/REJECT. | read-only |
| `cgraph-auditor` / `qubit-auditor` | Re-verify completed claims by re-running proofs. | read-only |
| `qubit-ui-verifier` | Browser-verify dashboard routes/controls live. | read-only |
| `qubit-final-verifier` | Independently reproduce the release gates. | read-only |

## 3. The full work breakdown (A → Z)

### PHASE 0 — Stabilize (W0)  → *gate: preconditions met*
- One Qubit instance on `8890`; **do not kill the reference on `:9119`.**
- One config authority; reconcile the installed/repo config; retire the duplicate
  `qubit-voice-agent` unit.
- Upgrade the linked SQLite (WAL-reset).
- Install console scripts (`qubit`, `qubit-agent`); remove `qubit-hermes`.
- Apply the 5 generator patches; regenerate manifest/inventory/page-coverage/
  ledgers **twice, byte-identical**.
- Resolve Ruling 1 → **Option A**; reconcile the branding tests.

### PHASE 1 — Working + 100% rebranded  → *gate: `M2 CORE_PARITY_PASSED`*
- **W1 — M1 boot.** Prove the 13 boot items (audio segfault already fixed); a
  real `/chat` turn; session persistence; gateway status/start/restart.
  → **`M1 BOOT_BASELINE_PASSED`**.
- **W-MIG — Migration.** Port **everything** from the read-only reference home:
  credentials, config (**map**, don't copy the reference provider), skills, cron,
  plugins, memories, sessions/history, kanban/projects, checkpoints,
  channel/gateway/pairing state, CLI history, and the `.env`. Consolidate to one
  `QUBIT_HOME`; retire `hermes/`, `hermes-runtime/`, `hermes-migration/`,
  `hermes_config_overrides.json`, `hermes_persona.json`; **prove no `~/.hermes`
  fallback** (rename it and run).
- **W-BRAND — Rebrand.** De-brand product surfaces and Qubit-owned paths (the
  1,638 live files: entrypoints, `HERMES_*` env vars, `hermes_*` state names,
  frontend keys, `hermes-tui`, route paths); keep legal attribution; rebuild
  `frontend/qubit-dist`; branding scans green.
- **M2.** `/chat` + `/qubit-chat` behave on the one migrated, rebranded backend;
  Qubit **installs and runs standalone** (own installer, 1:1 command surface, no
  reference dependency).

### PHASE 2 — Capabilities  → *gate: `M3 FULL_PARITY_PASSED`*
- **W2 — Parity inventory.** Against a **fresh, same-version reference install**,
  enumerate CLI commands, HTTP routes, dashboard pages/controls, and tools; diff
  vs Qubit; classify `PORTED`/`GATED`/`MISSING`; attach a voice-intent target;
  emit `docs/parity_inventory.json`.
- **W4 — CLI + route parity.** 1:1 command surface (fix `qubit chat`,
  `codex-runtime`, `credentials`, `vault`); **disposition** every `GATED` route
  (refusal + working counterpart, owner-confirmed, **or** implement). No bare
  refusals.
- **W5 — Dashboard visual + functional parity.** Enumerate reference pages;
  match layout/nav/theme; close the **3199 OPEN** controls with live E2E
  evidence; no legacy brand in the UI. (Runs parallel with W6.)
- **W6 — Tools.** Skills, plugins, MCP (65 optional), browser (CDP), terminal,
  IDE, models/providers — each proven through the canonical path. (Runs parallel
  with W5.)
- **W7 — Voice-for-everything.** A voice path for **every** capability in the
  inventory; Right-Ctrl; STT/TTS/realtime matrix; local + API escalation.
- **W3 — Adapters.** The 20 `OPERATOR_ONLY_ARCHITECTURE` rows signed off;
  adapters proven.
- **W9 — Vendor sync + release prep.** `qubit_runtime` 0.21.1 → 0.21.3 under the
  upstream-sync policy; fold the deep identifier rename in here (avoid rebranding
  twice).
- **M3.** Full parity reached, tracked in `docs/CHECKLIST.md`.

### PHASE 3 — Release  → *gate: `M4 RELEASE`*
- Run the full gate matrix from a single evidence root.
- Independent read-only verifier reproduces the evidence.
- Verdict **`VERIFIED`** — the only release approval.

## 4. Per-ticket protocol (repeat for every ticket)

1. Dispatcher selects one bounded ticket → writes the brief.
2. Researcher + upstream/parity agent define exact behavior.
3. `cgraph-compat` checks cross-boundary impact.
4. Implementer implements + runs focused proof. **Does not commit.**
5. Reviewer issues APPROVE/REJECT vs the brief and rules.
6. Auditor re-runs the proof independently.
7. Orchestrator updates `docs/STATUS.md`, ticks `docs/CHECKLIST.md`, appends the
   worklog entry.

## 5. Definition of done (per capability)

**All** must hold, with evidence: **behavior** through the canonical authority
path; a **voice** path; **brand** clean; **independence** (no reference
dependency). For release, add the full gate matrix + independent verifier.

## 6. Owner / operator gates — stop and request

- Approval to migrate the `.env` secrets into the canonical Qubit location.
- Rotation of the reference's plaintext API key.
- The pinned byte-parity manifest (`QUBIT_DIFFERENTIAL_BYTE_MANIFEST`).
- A live ≥65,536-token `/chat` credential for real-provider E2E.
- Real hardware for desktop/node/voice-matrix rows.
- A **fresh same-version reference install** as the clean-room oracle.
- The independent read-only verifier for M4.

## 7. Reporting cadence

After **every ticket** and at **every gate/wave boundary**:
- Tickets attempted / APPROVED / REJECTED.
- Exact evidence (command + trimmed output) per closed item.
- `docs/CHECKLIST.md` delta (open → done) and any new findings (with IDs).
- Blockers + the exact owner action required.
- An explicit statement of what is **not** done.

## 8. Final acceptance (A → Z)

- `docs/CHECKLIST.md` fully ticked.
- Zero unallowlisted legacy brand on product surfaces; legal attribution intact.
- No runtime dependency on the reference (works with `~/.hermes` absent/stopped).
- 1:1 with the reference: CLI, routes, dashboard (look + function), tools, and
  voice — each with evidence.
- Verdict `VERIFIED` only after the gate matrix + independent verifier.

## 9. Kickoff

Start at **Phase 0** and proceed through Phase 1, Phase 2, and Phase 3 in order.
Do not stop after one wave. Report at each gate and pause only at owner/operator
gates. Do not commit or push unless the owner explicitly authorizes it.

**Deliverable: Qubit fully working, voice-commandable, 1:1 with the reference,
completely rebranded, and independent — tracked to completion in
`docs/CHECKLIST.md`.**
