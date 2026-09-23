# Qubit DECISIONS — owner rulings in force

This file records decisions that bind current work. Full text:
`QUBIT_OWNER_AMENDMENT_2026-09-22_STAGED_GATES_AND_RULINGS.md`. Where a decision
conflicts with an archived contract, the owner amendment governs by content; the
archived contract is preserved, not edited.

## In force

1. **Staged gates.** `VERIFIED` remains the only release verdict (M4, full gate
   matrix + independent verifier). Added as implementation-progress markers only:
   `M1 BOOT_BASELINE_PASSED`, `M2 CORE_PARITY_PASSED`. A scoped "Qubit v0.1,
   Unified Agent Core" may be released at M2 with `CODE_READY_OPERATOR_ACTIONS_REMAIN`
   once deferred items are enumerated.
2. **`OPERATOR_ONLY` split.** `OPERATOR_ONLY_EXTERNAL` (terminal once the
   external prerequisite is named) vs `OPERATOR_ONLY_ARCHITECTURE` (**OPEN until
   individually approved**). A typed refusal is never, by itself, a working
   capability. Current: 75 external, 20 architecture OPEN.
3. **Ruling 1 → Option A.** Public branding policy wins; upstream references
   remain only in legal/provenance notices. De-brand the rendered surface and
   regenerate the shipped bundle. (Not yet applied.)
4. **Ruling 2 → Option B.** Split as in (2); architecture rows stay OPEN.
5. **Ruling 3 → Option A.** `/` redirects to `/chat`.
6. **Parity target (2026-09-22).** The working local reference dashboard at
   `http://127.0.0.1:9119/?profile=default` is the behavioral reference.
   **Everything that works there must work in Qubit** — every route, page,
   button, control, skill, MCP, browser, terminal, and IDE action — and
   **every one of them must be commandable by voice** through the Qubit voice
   bridge. A `GATED`/refusal route is not parity. See [PARITY.md](PARITY.md).
7. **Zero legacy brand (2026-09-22).** No `NousPortal` / `Hermes` / `OpenClaw` /
   `Nous Research` reference may remain on any shipped/user-facing surface.
   **Required exception:** `LICENSE` and `THIRD_PARTY_NOTICES.md` keep the
   upstream copyright/license attribution. Frozen records and
   `docs/differential/INTERFACE.md` are preserved as-is. See [BRAND.md](BRAND.md).
8. **Two-phase strategy (2026-09-22).** **Phase 1** — Qubit core working and
   **100% rebranded**: migrate everything from the read-only reference home into
   Qubit ([MIGRATION.md](MIGRATION.md)), then de-brand product surfaces and
   Qubit-owned paths. **Phase 2** — make the voicebridge and OpenClaw
   capabilities work on that core. The reference dashboard
   (`http://127.0.0.1:9119/?profile=default`) and the reference home are
   **read-only and must never be modified**; a fresh same-version reference
   install is the clean-room oracle.
9. **Independence (2026-09-22).** At the end, Qubit is a **standalone product —
   everything, not only the installer.** Qubit owns: its **installer** and
   packaging; its **1:1 `qubit` command surface** (every reference command
   works); its **home/state/config** (`QUBIT_HOME`, one `state.db`); its
   **service units**; its **updates** and rollback; its **diagnostics**
   (`qubit doctor`); its **skills/plugins/MCP registries**; its **model/provider
   config** and credential refs; its **sessions/history**; its **voice
   pipeline**; and its **dashboard/UI**. It has **no runtime dependency on the
   reference install or service** (`~/.hermes` is migration input only, never a
   fallback). Completely rebranded to Qubit; only legal attribution is retained.
   See [CHECKLIST.md](CHECKLIST.md) §10.

## Owner decisions in force (2026-09-22, session 2)

10. **Real Qubit-owned gateway (authoritative).** Qubit must provide a real
    Qubit-owned gateway with Hermes-equivalent **status, start, stop, restart,
    supervision, audit, confirmation, e-stop, failure, and recovery** behavior.
    The gateway is Qubit-owned — **Hermes must not be the authority** — and it
    must **not** create a separate product authority: all gateway control flows
    through the one broker / policy / audit / confirmation / e-stop path.
11. **Sanctioned update interface.** The update surface is exactly:
    `qubit update --check`, `qubit update --apply`, `qubit update --rollback`.
    `qubit update` is no longer a refusal. Branded Qubit.
12. **Gateway route fixes authorized.** Fix and wire `qubit gateway status` and
    the gateway restart status route (N8, `gateway-restart/status` 404) to the
    real Qubit gateway supervisor.
13. **No premature milestone.** Do **not** claim `M1`, `M2`, `CODE_READY`,
    `VERIFIED`, or `COMPLETE` until the real gateway and update paths are
    **behaviorally tested** (command + observed output).

14. **W9 vendored pin — target selected (2026-09-23).** Pin the vendored runtime
    at the **stable v0.21.4 release commit `d337b736aa1e8ebecfab043842d13e4a2d2f48a3`**
    (not the moving `main` HEAD `400f11bd…`), for stability. This selects the
    **code** version only; **data** (sessions, skills, plugins, MCP, cron,
    history, memories, config) is migrated separately from the live reference
    home `~/.hermes` (W-MIG) and is not carried by any commit. Two preconditions
    before the pin moves: (a) **freeze the mid-flight worktree**; (b) the
    **pristine 0.21.4 source** is already built
    (`/tmp/opencode/pinned/hermes-agent-0.21.4/`, 14,966 files). See
    `docs/QUBIT_OWNER_AMENDMENT_2026-09-23_W9_VENDOR_SYNC.md` §8.

## Owner decisions in force (2026-09-23)

15. **W9 pin move AUTHORIZED + worktree FROZEN.** Move the vendored runtime pin
    from 0.21.1 (`2237be35…`) to the stable v0.21.4 release commit
    `d337b736aa1e8ebecfab043842d13e4a2d2f48a3`. The mid-flight worktree is frozen
    at `42d64b84` on branch `freeze/wave12-2026-09-23` (clean tree, **not
    pushed**). The sync is **slice-by-slice**, starting with slice-0
    (`qubit_state*`), using the pristine source
    `/tmp/opencode/pinned/hermes-agent-0.21.4/` and the extended rename map;
    regenerate evidence into `docs/generated/` (never the frozen archive) and
    re-prove boot. `docs/QUBIT_OWNER_AMENDMENT_2026-09-23_W9_VENDOR_SYNC.md` §8
    is now satisfied.
16. **M1 items 2/12 — implement a real Qubit-owned messaging-gateway daemon.**
    The owner **rejected** the naming/disposition resolution. M1 items 2/12 stay
    **OPEN** until a Qubit-owned gateway daemon exists and is behaviorally proven
    (status/start/stop/restart through the one broker/policy/audit/confirmation/
    e-stop path). No M1 ratification until then.
17. **N17 — remove the `~/.hermes/.env` credential fallback.** `DECISIONS` #9
    governs: `~/.hermes` is never a runtime fallback. The canonical credential
    source is `~/.config/qubit-voicebridge/.env` (or the configured
    `intent.deepseek_env`) plus the process environment; the legacy
    `~/.hermes/.env` candidate is removed from `orchestrator._env_key` and
    `tts/openai_tts._read_key`, and the pinning test is updated accordingly.
18. **#16 satisfied — daemon installed and running (2026-09-23).** The owner
    authorized the operator install in their place. `qubit gateway
    install --confirm` installed `qubit-gateway.service`; `qubit gateway start
    --confirm` started it (real `voicebridge.gateway_daemon`, MainPID,
    `daemon_live:true`, stays alive NRestarts=0); `stop`/`restart` are
    confirm-gated and the restart is real. **M1 items 2/12 are behaviorally
    PASS** (no `M1` milestone claimed until owner ratification; items 3/4 remain
    PARTIAL). See `docs/STATUS.md` §25.
18. **#16 satisfied — daemon installed and running (2026-09-23).** The owner
    authorized the operator install in their place. `qubit gateway
    install --confirm` installed `qubit-gateway.service`; `qubit gateway start
    --confirm` started it (real `voicebridge.gateway_daemon`, MainPID,
    `daemon_live:true`, stays alive NRestarts=0); `stop`/`restart` are
    confirm-gated and the restart is real. **M1 items 2/12 are behaviorally
    PASS** (no `M1` milestone claimed until owner ratification; items 3/4 remain
    PARTIAL). See `docs/STATUS.md` §25.

## Preserved (do not edit)

- Frozen records: `docs/archive/2026-09-21/` (contracts, owner amendments,
  allowlist, legal/provenance). Authoritative by content; never edited.
- `docs/differential/INTERFACE.md` — frozen contract surface.
- The 2026-09-21 authoritative contracts now live in
  `docs/archive/2026-09-22-pre-reset/` (e.g. capability parity, voice
  actionability, rebrand policy, upstream sync, final verification gate) and
  remain the definition of done, amended by this file.

## Open for the next review

- The 20 architecture rows need individual sign-off (or resolution).
- Ambiguous external-side rows flagged during review: `board.*`, `attach.*`,
  `mcp.app.*`.
- Canonical location for regenerated artifacts (currently archived) to be
  re-decided.
- Parity inventory: enumerate the reference dashboard's routes/controls and give
  each a Qubit status + voice path ([PARITY.md](PARITY.md)).
- Brand: build the deny-list/allow-list split, then de-brand the 1,638 live files
  ([BRAND.md](BRAND.md)).
