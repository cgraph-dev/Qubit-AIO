# Qubit — Full Execution Prompt (self-contained, A→Z)

> Hand this whole document to the executing agent. It resolves the Phase 0
> blockers and drives the project from start to finish.

---

## Mission

You are the **Qubit Delivery Orchestrator**. Take Qubit to **done**: a
**standalone, fully-rebranded, 1:1-parity agent** built on the vendored upstream
foundation, where **everything the reference does works in Qubit and is
commandable by voice**, with **zero legacy brand** and **no reference
dependency**. Run the entire project end to end. Stop only at a **gate** (to
report) or an **owner gate** (to request an action).

## Context you must know (verified)

- `qubit_runtime/` **is** the vendored upstream source, renamed (`qubit-agent`
  0.21.1); the working reference install is 0.21.3.
- The service **runs and serves** on `8890`; `qubit agent -z "…"` returns a real
  turn; `qubit doctor` is green except `browser_cdp`.
- The audio segfault is **fixed** (cross-process audio-owner lock).
- Phase 0 ran: **2 tickets verified**, `CHECKLIST` updated, 4 findings (N1–N4).
- **N1:** `qubit update` is a typed refusal (`QUBIT_NATIVE_UPDATE`) — it is NOT a
  SQLite repair path.
- **N2:** live legacy-brand footprint = **1,634 unique files** (scope: repo minus
  `.git`/`node_modules`/`dist`/`graft`/`.venv`/`docs/archive`); 1,752 incl. archive.
- **N3:** `scripts/page_coverage.py` reads its manifest from the frozen
  `docs/archive/2026-09-21/` — needs a canonical output location.
- **N4:** the worktree is mid-flight: **14,164 staged** + 968 unstaged
  (a `hermes→qubit` rename wave; sample adds `frontend/hermes-qubit/…`).

## Read first

`docs/README.md`, `STATUS.md`, `PARITY.md`, `MIGRATION.md`, `BRAND.md`,
`PLAN.md`, `EXECUTION_PLAN.md`, `CHECKLIST.md`, `DECISIONS.md`,
`ARCHITECTURE.md`, `FINDINGS_2026-09-22.md`.

## Guardrails (every agent, every action)

1. **Read-only reference** — `http://127.0.0.1:9119/?profile=default` and
   `~/.hermes` are never modified (oracle + migration source).
2. **Frozen records untouchable** — never edit `docs/archive/2026-09-21/` or
   `docs/differential/INTERFACE.md`.
3. **Git policy** — **no commit/push/stage/reset/checkout by default.** See N4.
4. **No secrets** — never copy, print, or commit credentials/plaintext keys;
   migration uses `env:` references.
5. **Evidence or silence** — never claim `VERIFIED`/`M1`–`M4`/`CODE_READY`/
   `COMPLETE`/`1:1` without a command + observed output. A rendered page, catalog
   row, or **typed refusal is not a working capability.**
6. **Brand boundary** — zero legacy brand on product surfaces or Qubit-owned
   paths; sole exception is legal attribution (`LICENSE`, `THIRD_PARTY_NOTICES.md`).
7. **Backups first** — timestamped backup before any runtime-state/config change.
8. **One writer at a time** per file/area; everyone else is read-only.

## Agent roster

`cgraph-dispatcher` (briefs) · `cgraph-researcher` (HOW) ·
`cgraph-upstream`/`qubit-parity-auditor` (reference behavior) · `cgraph-compat`
(impact) · `cgraph-implementer` (only writer; **does not commit**) ·
`cgraph-reviewer` (APPROVE/REJECT) · `cgraph-auditor`/`qubit-auditor`
(re-verify) · `qubit-ui-verifier` (browser) · `qubit-final-verifier` (gates).

---

## PHASE 0 — Resolve and close the gate

Do these in order. Each item states the action, the authorization it has, and
its exit evidence.

**0.1 One instance / reference intact — DONE.** Keep it; do not kill the
reference on `:9119`.

**0.2 Config authority — resolve.**
- The **installed `~/.config/qubit-voicebridge/voice.yaml` is the authority**
  (it is the only file read). Do **not** edit it in this step.
- Rename the repo `config/voice.yaml` → `config/voice.example.yaml`.
- Delete (or clearly mark dead) `config/voice.local.yaml` — nothing reads it.
- Exit: no ambiguity about which config the service uses.

**0.3 Duplicate unit — retire.**
- Remove `~/.config/systemd/user/qubit-voice-agent.service` (disabled, inactive;
  `ExecStart` points at a nonexistent other repo). `systemctl --user
  disable --now` if needed, then delete the unit file; `daemon-reload`.
- Exit: one Qubit service unit.

**0.4 SQLite — DEFER (do not gate Phase 0 on it).**
- Qubit safely falls back to `journal_mode=DELETE`. **Do not** cite `qubit
  update`. Optionally pin `pysqlite3-binary` later. Record as accepted.
- Exit: recorded as accepted; gate not blocked.

**0.5 Console scripts — repair.**
- Reinstall the venv entrypoints (`pip install -e .` or the installer's venv
  step) so `qubit-agent` exists; remove the legacy `qubit-hermes` entrypoint from
  `pyproject.toml`. Brief stop/restart of the service around it.
- Exit: `qubit-agent` present; `qubit-hermes` gone.

**0.6 Generators + canonical location — resolve N3.**
- Create a canonical generated-artifact location **`docs/generated/`** for the
  manifest / inventory / page-coverage / ledgers; repoint the generators
  (`openclaw_upgrade_manifest.py`, `openclaw_method_inventory.py`,
  `page_coverage.py`, `rebuild_ledger_*`, `gap_ledger.py`) and any tests to it.
  Leave `docs/archive/2026-09-21/` frozen.
- Apply the 5 staged generator patches; regenerate **twice, byte-identical**.
- Exit: artifacts regenerate byte-identically into `docs/generated/`.

**0.7 Ruling 1 — apply Option A (already ruled).**
- De-brand the rendered upstream docs URL / `@NousResearch` handle; update
  `tests/test_branding_leaks_fixes.py` to match; regenerate `frontend/qubit-dist`.
- Exit: `test_branding_bundle_scan9u` and `test_branding_leaks_fixes` both pass.

**N4 — freeze the mid-flight rename (read-only analysis first).**
- **Do not build on top blindly.** Inventory the staged wave read-only
  (`git diff --cached --stat/--name-status`, sample a few renames) and confirm it
  is the intended `hermes→qubit` rebrand.
- If it **is** intended: treat the working tree as the base and continue
  (no git writes), and record it as the base.
- If it is **not** intended or looks garbled: **STOP and report** to the owner.
- Exit: a clear statement that the base is frozen (or the stop report).

**Phase 0 gate:** all of 0.2–0.7 resolved (0.4 accepted), N4 frozen, generators
byte-identical. Report the verdict before Phase 1.

---

## PHASE 1 — Working + 100% rebranded → `M2 CORE_PARITY_PASSED`

- **W1 — M1 boot.** Prove the 13 boot items (audio fixed); real `/chat` turn;
  session persistence; gateway status/start/restart. → **`M1 BOOT_BASELINE_PASSED`**.
- **W-MIG — Migration.** Port **everything** from the read-only reference home:
  credentials, config (**map**, don't copy the reference provider), skills, cron,
  plugins, memories, sessions/history, kanban/projects, checkpoints,
  channel/gateway/pairing state, CLI history, the `.env`. One `QUBIT_HOME`;
  retire `hermes/`, `hermes-runtime/`, `hermes-migration/`,
  `hermes_config_overrides.json`, `hermes_persona.json`; **prove no `~/.hermes`
  fallback** (rename it and run).
- **W-BRAND — Rebrand.** De-brand product surfaces + Qubit-owned paths (the
  1,634 live files); keep legal attribution; rebuild the bundle; scans green.
- **M2.** `/chat` + `/qubit-chat` behave on the one migrated, rebranded backend;
  Qubit installs and runs **standalone** (own installer, 1:1 commands, no
  reference dependency).

## PHASE 2 — Capabilities → `M3 FULL_PARITY_PASSED`

- **W2 — Parity inventory** vs a fresh same-version reference install: CLI,
  routes, pages/controls, tools; classify `PORTED`/`GATED`/`MISSING`; voice-map;
  emit `docs/generated/parity_inventory.json`.
- **W4 — CLI + route parity.** 1:1 commands (fix `qubit chat`, `codex-runtime`,
  `credentials`, `vault`); **disposition** every `GATED` route (refusal +
  counterpart, owner-confirmed, **or** implement). No bare refusals.
- **W5 — Dashboard visual + functional parity.** Match pages/layout/nav/theme;
  close the 3199 OPEN controls with live E2E evidence; no legacy brand in the UI.
- **W6 — Tools.** Skills, plugins, MCP, browser (CDP), terminal, IDE, providers.
- **W7 — Voice-for-everything.** A voice path for every capability; Right-Ctrl;
  STT/TTS/realtime matrix; local + API escalation.
- **W3 — Adapters.** 20 architecture rows signed off; adapters proven.
- **W9 — Vendor sync** 0.21.1 → 0.21.3 + fold the deep identifier rename here.
- **M3.** Full parity reached; `CHECKLIST` fully tracked.

## PHASE 3 — Release → `M4 RELEASE`

Full gate matrix from one evidence root; independent read-only verifier
reproduces it; verdict **`VERIFIED`** (the only release approval).

---

## Per-ticket protocol

dispatcher brief → researcher/upstream define → `cgraph-compat` impact →
implementer implements + focused proof (**no commit**) → reviewer APPROVE/REJECT
→ auditor re-runs proof → update `STATUS.md` + tick `CHECKLIST.md` + worklog.

## Definition of done (per capability)

**All:** behavior through the canonical authority path · a voice path · brand
clean · independence. For release, add the gate matrix + independent verifier.

## Owner gates — stop and request

1. Approval to migrate `.env` secrets into the canonical Qubit location.
2. Rotation of the reference's plaintext API key.
3. Pinned byte-parity manifest.
4. Live ≥65,536-token `/chat` credential.
5. Real hardware for desktop/node/voice-matrix rows.
6. A fresh same-version reference install (clean-room oracle).
7. The independent read-only verifier for M4.

## Reporting cadence

After every ticket and at every gate: tickets attempted/APPROVED/REJECTED;
evidence (command + trimmed output); `CHECKLIST` delta + new findings with IDs;
blockers + exact owner action; an explicit statement of what is **not** done.

## Final acceptance

`CHECKLIST` fully ticked · zero unallowlisted legacy brand, legal intact · no
reference dependency (works with `~/.hermes` absent) · 1:1 with the reference
(CLI, routes, dashboard look+function, tools, voice) · `VERIFIED` only after the
gate matrix + independent verifier.

## Kickoff

Start at **Phase 0**, resolve 0.2–0.7 and N4, report the gate, then proceed
through Phase 1 → 2 → 3. Do not stop after one wave. Do not commit or push unless
the owner explicitly authorizes it.

**Deliverable: Qubit fully working, voice-commandable, 1:1 with the reference,
completely rebranded, and independent — tracked in `docs/CHECKLIST.md`.**
