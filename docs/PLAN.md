# Qubit PLAN — milestones and critical path

No milestone below is achieved yet. A milestone is only claimed when its
**behavioral evidence exists** (a command + observed output), never because a
page, catalog, or ledger row renders.

**Sequenced work: [EXECUTION_PLAN.md](EXECUTION_PLAN.md).** **Audit:
[FINDINGS_2026-09-22.md](FINDINGS_2026-09-22.md).** **Port:
[MIGRATION.md](MIGRATION.md).**

## Strategy (owner, 2026-09-22) — two phases

- **Phase 1 — Qubit core: working and 100% rebranded.** The vendored runtime
  works end-to-end; the reference home is fully **migrated** into Qubit
  (credentials, config, skills, cron, plugins, memories, sessions/history,
  kanban/projects, checkpoints, channel/gateway state); and there is **no legacy
  brand** on any product surface or Qubit-owned path.
- **Phase 2 — capabilities.** Make the voicebridge and the OpenClaw feature set
  work, driven from the Phase-1 core.

**Reference rules.** `http://127.0.0.1:9119/?profile=default` is the read-only
behavioral oracle — **never modify it**. The reference home `~/.hermes` is
read-only source. Qubit must never use `~/.hermes` as a hidden fallback. A
**fresh** reference install at the same pinned version (0.21.3) is the
clean-room oracle for behavior.

## Milestones

### M1 — `BOOT_BASELINE_PASSED` (Phase 1; blocking; partially demonstrated)
**Core evidence exists:** `qubit agent -z "…"` returns a real model turn
(`QUBIT_OK`), the service serves on `8890`, and `qubit doctor` is green except
`browser_cdp`. Still to prove: the `/chat` terminal TUI, `/qubit-chat` voice
turn, session persistence, gateway status/start/restart, and core dashboard
controls against the real backend. The voicebridge `qubit task` segfault
(`sherpa_onnx`/`sounddevice`) is the first blocker. **Open blocker (2026-10-04):**
the `/chat` PTY does not auto-continue a turn interrupted by a service restart —
the TUI reconnect takes the gate-less live-reuse return at `methods_session.py:937`
(`_resume_reuse_live_locked` `:752-766`; gate present only on `_resume_cold` `:853`
and `_resume_eager` `:914`), so `_maybe_schedule_auto_continue` is never entered
on the PTY boot path ([STATUS.md](STATUS.md) §86, [SESSION_SURVIVAL.md](SESSION_SURVIVAL.md),
[RPC_BOOT_PATH_TRACE.md](RPC_BOOT_PATH_TRACE.md)).

### M2 — `CORE_PARITY_PASSED` (Phase 1)
`/chat` and `/qubit-chat` behave on the **one** Qubit backend (real provider,
real tools/skills/plugins/MCP, persisted sessions, voice + Right-Ctrl on the same
broker/policy/audit path), with the reference home fully migrated and zero
legacy brand on Qubit-owned paths. **Qubit installs and runs standalone** from
its own installer, with a 1:1 `qubit` command surface and no reference
dependency (`~/.hermes` is migration input only).

### M3 — `FULL_PARITY_PASSED` (Phase 2)
Per [PARITY.md](PARITY.md), everything that works on the reference dashboard
(`http://127.0.0.1:9119/?profile=default`) works in Qubit: every CLI command,
route, page, button, control, skill, MCP server/tool, browser action, terminal,
and IDE action — each with a **voice path** through the Qubit voice bridge, and
each proven with a command + output. Includes closing the 94 `GATED` routes, the
full voice STT/TTS/realtime matrix, dashboard E2E, and zero unallowlisted legacy
brand per [BRAND.md](BRAND.md).

### M4 — `RELEASE`
The full gate matrix + independent read-only verifier. Only M4 can ever be
called `VERIFIED`.

## Critical path (in order)

### Phase 1 — working + 100% rebranded
0. **Stabilize (W0).** One Qubit instance (do **not** kill the reference on
   `:9119`); one config authority; retire the duplicate `qubit-voice-agent`
   unit; upgrade the linked SQLite; repair stale entrypoints; apply the 5
   generator patches; regenerate artifacts; resolve Ruling 1.
1. **M1 boot (W1).** The service already serves; TUI build/install are fine
   (earlier failures were transient) and the restarts are explicit tooling, not
   crashes. The real blocker is the **`qubit task` audio segfault**
   (PortAudio/ALSA, no cross-process device lock). Fix that, then prove the 13
   boot items.
2. **Migrate (W-MIG).** Complete the port from the reference home; consolidate
   to one `QUBIT_HOME`; retire the legacy-named homes/files. See
   [MIGRATION.md](MIGRATION.md).
3. **Rebrand (W-BRAND).** De-brand product surfaces and Qubit-owned paths
   (1,638 live files; `qubit-hermes` entrypoint; `HERMES_*` env vars; `hermes_*`
   state names; frontend keys; `hermes-tui`); keep legal attribution; rebuild
   `frontend/qubit-dist`.
4. **M2** — real `/chat` + `/qubit-chat` turns on the migrated, rebranded core.

### Phase 2 — capabilities
5. **Parity inventory (W2)** — enumerate the fresh reference install (CLI +
   routes + controls), diff, classify, voice-map.
6. **Route/CLI/control/tool parity (W4–W6)** — **disposition** the 94 `GATED`
   routes (policy refusals with counterparts vs genuine gaps), close the 3199
   OPEN controls, close the CLI gaps (`qubit chat`, `codex-runtime`,
   `credentials`, `vault`), and prove skills/plugins/MCP/browser/terminal/IDE.
7. **Voice-for-everything (W7)** — a voice path for every capability.
8. **Adapters (W3/W9)** — the 20 architecture rows signed off; adapters proven.
9. **Vendor sync** 0.21.1 → 0.21.3 under the upstream-sync policy (fold the
   deep identifier rename in here to avoid rebranding twice).
10. **M4** — gates + independent verifier.

Track every item against the master list: **[CHECKLIST.md](CHECKLIST.md)**.
Applied fixes and their status: **[FINDINGS_2026-09-22.md](FINDINGS_2026-09-22.md) §G**.

## Owner / operator gates (not closable by code alone)

- Pinned byte-parity manifest (`QUBIT_DIFFERENTIAL_BYTE_MANIFEST`).
- A live ≥65,536-token `/chat` credential for real-provider E2E.
- Real hardware for the desktop/node/voice-matrix rows.
- An independent read-only verifier for M4.
- A **fresh reference install at 0.21.3** as the clean-room oracle.

## Immediate next action

**Phase 1:** run the **migration** (`qubit product legacy-migrate --apply`, after
a backup), consolidate the four homes, then **rebrand** product surfaces and
Qubit-owned paths, then prove the core `/chat` surface. **Phase 2** starts with
the voicebridge `qubit task` segfault (`sherpa_onnx`/`sounddevice`), then the
OpenClaw capabilities.
