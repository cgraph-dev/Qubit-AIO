# Qubit vs. Hermes — why it is not 1:1 (root causes)

Audited 2026-09-25 against the **working reference** `69948c0057`
(v2026.9.24-2096, v0.21.5+2053-class). The Qubit vendored runtime pin has since
moved to that same commit by owner amendment (`docs/DECISIONS.md` #25,
`QUBIT_OWNER_AMENDMENT_2026-09-25_PIN_AND_1TO1.md`): Qubit is now the
deterministic rebrand of `69948c0057`, and the earlier `d337b736` (v0.21.4)
rebase described below is superseded. Machine data:
`scripts/forensic_audit/` (Tier 0) plus the Tier-1 plan bundles in
`/tmp/qubit-1to1-*/plans/`. **No milestone/verification is claimed.**

## The model

Qubit is **not hand-ported**. It is a mechanical transform plus an overlay:

```
Qubit = rebrand(upstream @ 69948c0057 = v0.21.5+2053) + Qubit overlay (voicebridge/, systemd/, config, frontend)
```

The rebase from `d337b736` (v0.21.4) to `69948c0057` was executed deterministically
(owner amendment 2026-09-25): the engine rebrands the exact pinned source, the
result is 3-way merged with the current Qubit tree, and the overlay/fork patches
are re-applied. Cause 1 below records the pre-rebase state that motivated it.

The transform is `scripts/qubit_rebrand.py` (+ `qubit_debrand.py`,
`identifier_rename.py`), an explicit ~60-token map. It is **deterministic and
idempotent** (tree sha256 identical across two independent runs; second pass = 0
changes — verified 2026-09-25).

Therefore Qubit diverges from Hermes for exactly four reasons.

## Cause 1 — VERSION MISMATCH (the dominant cause) — **addressed by the v0.21.5 rebase**

The reference that works is **v0.21.5+2053**; Qubit originally rebranded
**v0.21.4**. Delta ≈ **3,850 files** (≈1,785 added, 407 removed, 1,658 changed).
Behaviours added after the original rebrand did not exist in Qubit, e.g. routes
`GET /api/chat/workspaces`, `WS /api/display/ws`,
`POST /api/dashboard/agent-plugins/activate`,
`POST /api/local-models/download/{pause,resume}`. The v0.21.5 rebase (owner
amendment 2026-09-25) lands the deterministic rebrand of the exact
`69948c0057` source; the residual gaps are tracked in the regenerated parity
inventory and `docs/QubitVSupstream.md`.

**Fix (mechanical, not a manual port):** rebase the deterministic rebrand onto
the exact reference commit; re-apply the overlay and fork patches; 3-way merge.

### Rebase runbook (from the engine verification)

1. Snapshot the worktree; freeze to a **single writer**.
2. Extend the engine token map — it currently does **not** map
   `hermes_state_health` or `hermes_yaml`, which on v0.21.5 produces an
   **ImportError** (`hermes_state_health` imports get rewritten to
   `qubit_state_health` while the file keeps its legacy name).
3. Stage the pristine target (`$P/ref`) in the engine layout and run
   `qubit_rebrand.py --apply` then `qubit_debrand.py`.
4. Re-apply the 52 curated product path-renames
   (`docs/archive/2026-09-21/QUBIT_REBRAND_MANIFEST.json`).
5. 3-way merge: base = engine(v0.21.4), ours = engine(v0.21.5), theirs = current
   Qubit tree. Expect ≈**2,802 mixed-file conflicts** (tests 1,080, apps 938,
   website 522, qubit_cli 314), plus the overlay-only new files (≈1,026) and the
   root overlay trees (`voicebridge/`, `systemd/`, `config/`, `frontend/`,
   `scripts/`, `skills/`, `pyproject.toml`) kept verbatim.
6. Regenerate artifacts/bundles; verify (`import` smoke, `QUBIT_OK`,
   public-surface scan, re-run engine byte-identical).

**Engine coverage today: 58.3% byte-exact** (7,824/13,411); the rest is
additional de-brand (30.1%) and structural overlay/fork (69.9%).

## Cause 2 — REBRAND ASYMMETRIES (renamed on one side only)

9 classes / 27 sites where a token was renamed on one side but not its
counterpart, so the lookup silently stops resolving. **All fixed** in the
isolated tree (26 files, 23 regression tests; 22 fail on the reverse-patched
copy).

| # | Asymmetry | Impact |
|---|---|---|
| 1 | Skill frontmatter: 7 readers + 1 writer read `metadata.hermes` (skills use `metadata.qubit`) | skill tags/conditions/config/blueprints silently empty |
| 2 | Executable identity `_QUBIT_EXECUTABLES`, `gateway/status`, `gateway/run._resolve_qubit_bin` accepted `hermes*` only | shipped `qubit*` scripts not recognised |
| 3 | Default home `~/.hermes` (`qubit_constants`, `env_loader`, `mcp_tool_config`, `mcp_serve`, `qubit_startup_watchdog`) | imports can materialize/read the reference home |
| 4 | `QUBIT_ENABLE_PROJECT_PLUGINS` documented but reader used `HERMES_*` | flag ignored |
| 5 | Project plugin dir `./.qubit/plugins` documented, discovery read `./.hermes/plugins` | plugins not found |
| 6 | Entry-point groups read `hermes_agent.*` only | Qubit entry points not discovered |
| 7 | Plugin manifest `requires_hermes` vs `hermes` key | manifest mismatch |
| 8 | Distribution manifest `qubit_requires` vs on-disk `hermes_requires` | attr/key mismatch |
| 9 | Gateway `qubit_home=` argv matcher (status used `hermes_home=` only) | control/status inconsistent |

**Needs an owner decision (not fixed):** true wire-contract ids
(`hermes_action`, `hermes-bots`, `hermes_profile`, `hermes_plugin_id`,
`hermes_version`, OpenAI `error["hermes"]`, memory-provider `hermes_home` kwarg)
and the gateway lock dir / `HERMES_GATEWAY_LOCK_DIR` — these are compatibility
surfaces, not safe to rename without an adapter.

## Cause 3 — OVERLAY REGRESSIONS (the overlay changed behaviour)

| ID | Sev | Regression | Anchor |
|---|---|---|---|
| B4-01 | HIGH | `/api/model/info` fabricates caps (`supports_reasoning:false`, `max_output_tokens:1024`) → reasoning selector dead | `voicebridge/qubit_compat.py:1264-1278` vs `ref hermes_cli/web_routers/models.py:79-90` |
| B4-02 | MED | `auto_context_length = config = effective` (reference derives auto separately) | same sites |
| B4-03 | MED | `h_status` drops `gateway_platforms`/`memory`/`disk`/auth-gate shape | `qubit_compat.py:377-436` |
| B4-04 | MED | gateway status is host-global, not `QUBIT_HOME`-scoped | `gateway_supervisor.py:326-446` |
| B4-05 | MED/sec | MCP test body serves unredacted probe errors | `qubit_compat.py:2297-2320`, `mcp_servers.py:368-388` |
| B4-06 | MED | pinned plugin index renders `NousResearch`/`hermes-*` | `voicebridge/data/plugin_index_pinned.json` |
| B4-07 | MED | `Qubit-cli` unknown toolset resolves 0 tools | `qubit_asgi.py:102-107` (only lowercase `qubit-cli` aliased) |
| B3-R1 | MED | help epilogue drops the space (`qubitchat`) because it slices `len("hermes")`=6 for `"qubit "` | `voicebridge/qubit_runtime.py:75-84` |
| B3-R2 | LOW | `qubit agent --version` reports `0.21.1` though content is `0.21.4` — **fixed in the v0.21.5 rebase** (`qubit_runtime/pyproject.toml` → `0.21.5`) | `qubit_runtime/pyproject.toml:5` |
| B3-R3 | LOW | `--ignore-user-config` help still names `~/.hermes/config.yaml` | `qubit_cli/_parser.py:166,264` |

**Intentional overlay differences (not bugs, documented counterparts):**
proxy-first read authority (`qubit_asgi.py:327-370`, `webui.py:879-965`);
GATED routes shadowing real vendored handlers (MCP OAuth/catalog-install,
channel onboarding); MoA stub + auxiliary-model pin refusals; `/chat`
65,536-token floor + fail-closed egress; Qubit-owned confirm-gated gateway;
single-`state.db` session adapter; native voice/worker path; partial updater
(7 vs 38 modules); API base `/agent/api/*`.

## Cause 4 — CONFIG / ENVIRONMENT DIVERGENCE

| Item | Reference | Qubit | Effect |
|---|---|---|---|
| API base | `/api/*` | `/agent/api/*` only | bare `/api/health` → 404 |
| Model/provider | `opencode-zen/laguna-s-2.1-free` | `opencode-go/deepseek-v4-flash` | different reasoning/context/capabilities |
| Toolset config | explicit cli + 11 platform maps (`platform_toolsets`) | absent | platform defaults instead of reference selection |
| `toolsets` | valid set | `["Qubit-cli","kanban"]` (unknown name) | inert/0-tool entry |
| `command_allowlist` | 10 entries | `[]` | different command gating |
| `state.db` | WAL, 1 writer | DELETE, 2 writers (`voicebridge run` + `gateway_daemon`); config says `wal` that did not apply | lock contention risk |
| Services/ports | 1 unit `:9119` | 2 units `:8890` + per-nav `tui_gateway.entry` | lifecycle differs |
| Update source | clone origin | canonical `cgraph-dev/qubit-voicebridge` ≠ live `origin` | update identity mismatch |

Route quantification: pin v0.21.4 = 236 routes; `qubit_compat` = 354 (119 shadow
upstream, 235 compat-only); GATED = 100 (38 of which match upstream).

## What is fixed, and where

- **Fixed in the isolated rebrand pass** (`/tmp/qubit-1to1-*/repo`): the 9
  asymmetry classes (26 files + 23 tests). Directionally portable; not applied
  to the live tree.
- **Fixed in the earlier isolated repair pass**
  (`/tmp/qubit-hermes-repair-20260925-064611/repo`): B4-01/02/04/05 (model info,
  auto-context, gateway status, MCP redaction), update identity, version
  metadata, toolset bundle, context table, home defaults, and the public de-brand.
- **Not yet fixed:** the version rebase itself (Cause 1), B3-R1/R3 help strings,
  B4-03 `h_status` fields, B4-06 plugin index, B4-07 alias case, and every
  `OVERLAY_INTENTIONAL` item that the owner wants changed.

## Frozen contracts

Editing frozen contracts is forbidden without owner sign-off (`AGENTS.md`;
`docs/DECISIONS.md`). The pin move to `69948c0057` was authorized by the owner
amendment of 2026-09-25 (`docs/DECISIONS.md` #25,
`QUBIT_OWNER_AMENDMENT_2026-09-25_PIN_AND_1TO1.md`), which re-baselines the 1:1
target to that commit while the archived frozen records are preserved,
byte-for-byte. The machine-readable interface contract
(`docs/differential/byte_manifest/hermes/HERMES_BYTE_MANIFEST.json`,
`manifest.sha256`) has been regenerated at the new pin; the human-readable
`docs/differential/INTERFACE.md` and `docs/archive/2026-09-21/` were **not**
modified.

## Verification status

Focused tests pass in isolation; the full gate and an independent verifier have
not run. This document records causes and fixes, not parity.
