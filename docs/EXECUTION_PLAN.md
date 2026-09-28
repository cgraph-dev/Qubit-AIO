# Qubit EXECUTION PLAN — how everything gets working

Goal: **everything that works on the reference dashboard
(`http://127.0.0.1:9119/?profile=default`) works in Qubit, and every one of those
capabilities is commandable by voice — with zero legacy brand on product
surfaces.**

**Two phases (owner, 2026-09-22):** Phase 1 = Qubit core **working and 100%
rebranded** (W0, W1, W-MIG, W-BRAND, M2). Phase 2 = **capabilities** —
voicebridge + OpenClaw (W2, W4–W7, W3/W9). The reference dashboard and the
reference home are read-only; a fresh same-version reference install is the
clean-room oracle.

Nothing here is done. This plan is reviewed and corrected every session; progress
is recorded in [STATUS.md](STATUS.md), and the live to-do list is the parity
inventory ([PARITY.md](PARITY.md)).

## Definition of done (per capability)

A capability is done only when all three hold:

1. **Behavior** — a real invocation through the canonical authority path
   (broker / policy / audit / confirmation / e-stop), proven by a command +
   observed output. A page, catalog row, or typed refusal is **not** done.
2. **Voice** — it is reachable by a voice intent through the Qubit voice bridge.
3. **Brand** — it exposes no legacy brand on any product surface.

## Workstreams

### W0 — Stabilize and unblock  *(code-side, bounded)*
- **Do not kill the `:9119` listener** — it is the managed reference dashboard.
  Ensure the single Qubit instance (webui `8890`) is the only Qubit listener.
- **Config source of truth:** decide installed `~/.config/qubit-voicebridge/voice.yaml`
  vs repo `config/voice.yaml`; either wire `config/voice.local.yaml` or remove it.
- **Retire the duplicate** `qubit-voice-agent.service` (disabled; its config dir
  is a symlink) so there is one unit.
- **Upgrade the linked SQLite** (WAL-reset vulnerability) via `qubit update`.
- **Repair the installed console scripts**: the venv is stale vs `pyproject`
  (`qubit-agent` not installed); remove the legacy `qubit-hermes` entrypoint.
- Apply the **5 generator patches** from the staged bundle (drop the 7 doc hunks —
  superseded by the fresh docs).
- Regenerate manifest / inventory / page coverage / ledgers; re-prove
  byte-identical output.
- Resolve Ruling 1 → Option A so the two branding tests stop contradicting.
- **Exit:** one Qubit instance, one config authority, generators clean, no
  self-contradicting tests. (See `FINDINGS_2026-09-22.md` §B4–B6.)

### W1 — M1 boot baseline  *(CRITICAL — blocks everything)*
- The service already starts and serves (8890). Do **not** chase a `:9119`
  conflict: that listener is the managed reference service, and Qubit's vendored
  9119 server is inert.
- TUI `npm install`/`build` are fine (earlier failures were transient); the 18
  restarts are explicit tooling, not crashes.
- **Fix the real blocker:** the `qubit task` audio segfault — PortAudio/ALSA
  `snd_pcm_poll_descriptors_revents` (see FINDINGS §G3/G10). Add a cross-process
  audio-device lock or defer audio to the running service.
- Compare the Qubit process with the reference process across: executable,
  source path, argv, cwd, env, `QUBIT_HOME`, config, provider, gateway child,
  PTY/WebSocket, registries, session DB, logs, exit codes.
- Prove the 13 boot items with command + output.
- **Exit:** `M1 BOOT_BASELINE_PASSED` (behavioral evidence).

### W-MIG — Migrate the reference home  *(Phase 1)*
- Complete the port from the read-only reference home into Qubit: credentials,
  config, skills, cron, plugins, memories, sessions/history, kanban/projects,
  checkpoints, channel/gateway/pairing state, CLI history.
- Consolidate the **four** homes to one `QUBIT_HOME`; retire the legacy-named
  homes/files (`hermes/`, `hermes-runtime/`, `hermes-migration/`,
  `hermes_config_overrides.json`, `hermes_persona.json`).
- Prove no `~/.hermes` runtime fallback remains.
- **Exit:** coverage equals the reference per item; one home; legacy names gone.
  See [MIGRATION.md](MIGRATION.md).

### W2 — Parity inventory  *(Phase 2 — the real to-do list)*
- Enumerate the reference UI's **CLI commands**, routes, and controls from the
  live `9119` instance and its source; inventory Qubit's CLI (`qubit` +
  `qubit agent`), routes (`qubit_compat.ROUTES`, `webui.py`), controls
  (`dashboard_ops`), and voice registries.
- Classify every row: `PORTED` / `GATED` / `MISSING`; attach a voice-intent
  target; assign an owner.
- Emit `docs/parity_inventory.json` + a human checklist.
- **Exit:** complete, reviewed inventory. (Start with the **94 `GATED` routes**,
  the **3199 OPEN** controls, and the CLI gaps: `qubit chat` refusal,
  `codex-runtime`, `credentials`, `vault`.)

### W3 — M2 core parity  *(code-side, bounded)*
- `/chat` opens the real terminal runtime, loads the real provider, shows real
  tools/skills/plugins/MCP/sessions, returns a real turn.
- `/qubit-chat` does the same by voice on the same backend (local model + API
  escalation, Right-Ctrl).
- Sessions persist; gateway status/start/restart work; core dashboard controls
  hit the real backend.
- **Exit:** `M2 CORE_PARITY_PASSED`.

### W4 — Route + CLI parity  *(disposition the 94 `GATED`, close the CLI gaps)*
- For each `GATED` route: **disposition it** — (a) owner confirms it is a
  legitimate policy refusal *and* its counterpart works (with a voice path), or
  (b) it is a genuine gap, so implement the real behavior by **porting/adapting
  the upstream source** (never a stub). Do **not** implement the refusals
  literally (raw PTY, secret reveal, remote fetch) — that violates Qubit policy.
- Make `qubit chat` open the real terminal chat (not a refusal); restore
  `codex-runtime`, `credentials`, and `vault`.
- **Exit:** every `GATED` route dispositioned with evidence; zero missing CLI
  commands.

### W5 — Control/button parity  *(close the 3199 OPEN → 0)*
- Live E2E per control against the real backend (Playwright); wire what is
  unwired; fix what fails.
- **Exit:** every control exercised and passing; no source-scan-only claims.

### W6 — Tool parity
- Skills execute; plugins load and execute; MCP servers/tools load and execute;
  browser actions, terminal, and IDE actions work — each through the canonical
  path, each proven.
- **Exit:** all tool surfaces proven with evidence.

### W7 — Voice-for-everything  *(owner requirement)*
- Every inventory capability gets a voice intent; wire Right-Ctrl; cover the
  STT/TTS/realtime matrix; ensure local model + API escalation.
- **Exit:** every capability invocable by voice with evidence. A capability
  without a voice path is OPEN.

### W8 — Brand  *(code-side, large)*
- Apply Ruling 1 Option A; de-brand the **1,638 live files** (rename
  `hermes_*`/`HERMES_HOME`/`NousResearch`/`OpenClaw` identifiers, labels, URLs,
  titles, service/dashboard/report names); rebuild `frontend/qubit-dist`.
- **Keep** the legal attribution in `LICENSE` / `THIRD_PARTY_NOTICES.md`.
- Re-run the branding scans until zero unallowlisted hits on product surfaces.
- **Exit:** `BRAND.md` satisfied.

### W9 — Vendor sync + release
- Sync `qubit_runtime` 0.21.1 → 0.21.3 under the upstream-sync policy, re-apply
  the rename map.
- Run the full gate matrix + an independent read-only verifier.
- **Exit:** `VERIFIED` (M4) — the only release approval.

## Sequencing

```
W0 ──▶ W1 ──▶ W3 ──▶ W4 ──▶ W5 ──▶ W7 ──▶ W9
                │            ▲
                └▶ W2 ───────┘ (inventory feeds W4/W5/W6/W7)
W8 (brand) runs in parallel after W0
W6 (tools) can start after W1, overlaps W4/W5
```

- **W1 is the gate.** Nothing downstream is measurable until the runtime serves.
- W2's inventory is what turns "everything" into a finite, checkable list.
- W4/W5/W6/W7 are the bulk of the work and can overlap once W3 passes.

## Owner / operator gates (not closable by code alone)

- Pinned byte-parity manifest (`QUBIT_DIFFERENTIAL_BYTE_MANIFEST`).
- A live ≥65,536-token `/chat` credential for real-provider E2E.
- Real hardware for desktop/node/voice-matrix rows.
- An independent read-only verifier for M4.

## Risks and known conflicts

- **Boot cause unknown** — W1 may be shallow (host wiring) or deep (provider
  resolver / PTY / registry init). Diagnose before estimating.
- **Frozen-vs-policy conflicts** — resolve by owner ruling (Ruling 1 does).
- **Brand scope** — 1,638 files, dominated by `qubit_runtime/`; needs the
  rename map and the vendor-sync policy.
- **Pre-existing test failures** — stale committed artifacts and source drift;
  regenerate artifacts as part of W0/W2.

## Tracking

- Update `STATUS.md` every session with the current workstream state.
- The parity inventory (`docs/parity_inventory.json`) is the master checklist.
- No milestone is claimed without its behavioral evidence.

## Immediate next actions

1. **W-MIG** — run `qubit product legacy-migrate --apply` (after a backup);
   consolidate the homes; verify per-item coverage.
2. **W-BRAND** — de-brand product surfaces and Qubit-owned paths; rebuild the
   bundle; keep legal attribution.
3. **W1** — prove the core `/chat` surface (the core turn already works).
4. **Phase 2** — fix the voicebridge `qubit task` segfault, then OpenClaw.
