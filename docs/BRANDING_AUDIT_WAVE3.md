# Qubit branding audit — Wave 3

Status: **WAVE 3 — one product-facing gap remains, owned outside agent F**
(machine-checked by `tests/test_branding_parity_wave3.py`).

This audit is the exhaustive, file:line classification of every remaining
`Hermes` / `OpenClaw` token on normal product-facing surfaces after commit
`f5fc5776`, plus the Wave 3 Qubit visual-identity fixes. It complements
`tests/test_branding_scan8tf.py`, `tests/test_dashboard_branding_wave1.py`,
and `tests/test_rebrand8tf.py`.

## Scope and method

Surfaces scanned (all hermetic; no browser/Playwright — see *Untested*):

| # | Surface | Scanner |
|---|---------|---------|
| 1 | CLI help tree (every subcommand/flag/epilog/dest) | `build_parser()` walk |
| 2 | Shipped shell completions | `voicebridge/_data/completions/*` |
| 3 | Voice / terminal / orchestrator / report / installer runtime strings | AST non-docstring constants under `voicebridge/` |
| 4 | Installer output | `echo` / `printf` lines in `scripts/install.sh` |
| 5 | Dashboard shell title/nav + served SPA + plugin API | live `WebUI` on `127.0.0.1:0` |
| 6 | Built bundle + SPA-shipped plugin fleet | `frontend/hermes-dist/**` |
| 7 | Overlay theme + plugin manifests/bundles | `frontend/hermes-qubit/**` |
| 8 | Minimal Qubit HTML/JS shell | `frontend/*.html`, `frontend/*.js` |
| 9 | Setup / support docs | `README.md`, `docs/AUDIO_SETUP.md`, `docs/VOICE_BRIDGE_SYSTEM.md`, `docs/HowTo.md` |
| 10 | Vendor + overlay provenance hashes | `docs/HERMES_SOURCE_COMPARISON.json` |

Allowed categories (frozen contract + owner directive 2026-09-12 §11):
legal/attribution/provenance, source-comparison, migration, compatibility,
and developer records — explicitly including module names, the
`X-Hermes-Session-Token` protocol header, `~/.hermes` operator paths, and
`HERMES_*` globals.

## Summary

- Product-facing occurrences found: **683 CLI-help + 15 SPA-plugin + 0
  docs/installer/completions**. CLI-help survivors are the single
  shared `--dry-run` migration help string and the fork-forward help; the
  15 SPA-plugin occurrences are a **new Wave 3 gap owned by agent C** (see
  §A). Everything else on product surfaces is already Qubit.
- Product-facing occurrences fixed by agent F this wave: **0** (the only
  product-facing gap lives in `frontend/hermes-web/**`, which agent F may
  not edit).
- Visual-identity fixes applied: **1 overlay theme**
  (`frontend/hermes-qubit/qubit-theme.json`) — colors, typography, spacing,
  responsive layout, accessibility, motion, and the loading / empty /
  degraded / approval / cancellation / recovery states.
- Vendor provenance: 3 existing vendored rows corrected and 6 Wave 3
  additions recorded (see §F).

## A. Product-facing — OPEN (agent C owns `frontend/hermes-web/**`)

The Wave 3 static plugin fleet ships the pinned Achievements and Kanban
dashboard bundles inside the served SPA. Their rendered text is normal
product-facing UI and still says "Hermes". Agent F detected them but may not
edit `frontend/hermes-web/**`.

| file:line | rendered string |
|---|---|
| `frontend/hermes-web/public/plugins/hermes-achievements/dist/index.js:338` | `Just unlocked {tier_part}"{name}" in Hermes Agent ☤` |
| `frontend/hermes-web/public/plugins/hermes-achievements/dist/index.js:446` | `Hermes Achievements` (hero title) |
| `frontend/hermes-web/public/plugins/hermes-achievements/dist/index.js:447` | `Scanning Hermes session history…` |
| `frontend/hermes-web/public/plugins/hermes-achievements/dist/index.js:478` | `Hermes is scanning local history once…` |
| `frontend/hermes-web/public/plugins/hermes-achievements/dist/index.js:669` | `Hermes Achievements` (hero title) |
| `frontend/hermes-web/public/plugins/hermes-achievements/dist/index.js:670` | `Collectible Hermes badges earned from real session history…` |
| `frontend/hermes-web/public/plugins/hermes-achievements/dist/index.js:681` | `run Hermes more` |
| `frontend/hermes-web/public/plugins/hermes-achievements/dist/index.js:690` | `Once Hermes sees a related signal…` |
| `frontend/hermes-web/public/plugins/kanban/dist/index.js:1782` | `Open Hermes Kanban docs in a new tab` |
| `frontend/hermes-web/public/plugins/kanban/dist/index.js:1783` | `Hermes Kanban documentation` (aria-label) |
| `frontend/hermes-web/public/plugins/kanban/dist/index.js:2446` | `Filter by assigned Hermes profile…` |
| `frontend/hermes-web/public/plugins/kanban/dist/index.js:2576` | `Reassign selected tasks to a different Hermes profile…` |
| `frontend/hermes-web/public/plugins/kanban/dist/index.js:3131` | ``Assigned to Hermes profile @${t.assignee}`` |
| `frontend/hermes-web/public/plugins/kanban/dist/index.js:3278` | `Hermes profile that will spec this task…` |
| `frontend/hermes-web/public/plugins/kanban/dist/index.js:3279` | `Hermes profile to assign…` |

Duplicate copies exist in the build artifact
`frontend/hermes-dist/plugins/**` (15 more) and are regenerated from the
`public/plugins/**` source, so fixing the source and rebuilding fixes both.

**Required fix (agent C, in `frontend/hermes-web/**`):** rebrand the bundled
plugin strings to Qubit (e.g. "Qubit Agent", "Qubit Achievements", "Qubit
profile", "Qubit Kanban") or supply Qubit `achievements` translations through
the host i18n namespace. This conflicts with
`tests/test_dashboard_plugin_fleet.py::test_static_fleet_manifest_mirrors_pinned_plugins`,
which requires the SPA copies to be byte-identical to the pinned
`qubit_hermes/plugins/*/dashboard/dist` bundles; that assertion must be
relaxed to a documented Qubit patch (as every other rebrand patch is). Agent F
cannot close this row.

Two further integration failures are caused by the same unrecorded additions
and are **not** in agent F's owned files:

- `tests/test_hermes_vendor8tf.py::test_vendor_byte_identical_or_documented_patch`
  and `::test_vendor_contains_no_undocumented_qubit_edits` fail because the
  new `frontend/hermes-web/src/plugins/fleet.ts` and
  `frontend/hermes-web/public/plugins/fleet.json` are Qubit-added files that
  the vendored-tree equality check does not yet recognise. They are recorded
  under `wave3.added` in `docs/HERMES_SOURCE_COMPARISON.json`; the vendor test
  needs an explicit QUBIT_ADDED allowance (owner of that test).
- `tests/test_hermes_plugins.py::test_kanban_read_routes_serve_real_board`
  fails with a `_seed_kanban()` signature mismatch (new untracked test from
  another agent).

## B. Migration records (allowed)

| file:line | text | why allowed |
|---|---|---|
| `voicebridge/cli.py:146` | `OpenClaw migration tools (confirm-gated; no silent secret import)` | `qubit claw` migration family help |
| `voicebridge/cli.py:196` | `OpenClaw migration is an attended CLI/dashboard operation…` | migration refusal text |
| `voicebridge/cli.py:254` | `never dispatch to OpenClaw; only speak the plan` | shared `--dry-run` help on the migration path (672 subcommands reuse this one literal) |
| `voicebridge/profiles.py:773,781,787,795,809,817,826` | `OpenClaw Voice Bridge` / `voice bridge for OpenClaw` | legacy→Qubit service/unit/description find-replace table |
| `docs/QUBIT_MIGRATION.md:21` | `"ask Hermes to …", "route to openclaw", "use Hermes"` | legacy spoken aliases retained for migration |
| `docs/VOICE_BRIDGE_TROUBLESHOOTING.md:5` | `historical OpenClaw Voice Bridge` | migration prose |

## C. Compatibility / protocol records (allowed)

| file:line | text | why allowed |
|---|---|---|
| `voicebridge/hermes_asgi.py:162`, `voicebridge/webui.py:978` | `X-Hermes-Session-Token` | wire protocol header (explicitly allowed) |
| `voicebridge/hermes_compat.py:958` | `Qubit single-authority host; no Hermes gateway process` | typed compat refusal detail |
| `voicebridge/hermes_compat.py:4197` | `The Hermes tui_gateway WS dialect is not served…` | typed compat refusal detail |
| `voicebridge/webui.py:764` | `The Hermes WebSocket dialect is not served…` | typed compat refusal detail |
| `frontend/hermes-web/**` | `X-Hermes-Session-Token`, `__HERMES_*__` | protocol header + runtime globals |

## D. Developer records (allowed)

| file:line | text | why allowed |
|---|---|---|
| `voicebridge/cli.py:2673` | `arguments forwarded to the Qubit-owned Hermes <family> command` | fork-forward `--help` (11 families) names the runtime fork |
| `voicebridge/hermes_egress_guard.py:84,102` | `EGRESS_DENIED: in-process Hermes backend …` | egress-guard diagnostics |
| `voicebridge/hermes_compat.py:1`, `voicebridge/hermes_backend.py:1`, `voicebridge/hermes_runtime.py:1` … | module docstrings | developer/source-comparison records |
| `scripts/hermes_vendor_manifest.py`, `scripts/verify_installed_dashboard.sh:111,148`, `scripts/build_dashboard.sh` | Hermes vendor/build/verify prose | developer tooling, not product UI |
| `systemd/qubit-voicebridge.service:4`, `systemd/qubit-voice-agent.service:9` | `# OpenClaw gateway + ollama …` | unit-file comments (unit `Description=` fields are Qubit) |
| `frontend/hermes-shared/src/skin.ts:2,95`, `frontend/hermes-shared/src/json-rpc-gateway.ts:99` | `HermesSkin` interface + comments | module/API names |
| `frontend/hermes-web/src/**` | `updateHermes`, `checkHermesUpdate`, `HermesConsoleModal`, `__HERMES_SESSION_TOKEN__` | i18n keys, component/module names, protocol globals. Note `t.status.updateHermes` renders the already-rebranded English value `"Update Qubit"` (`i18n/en.ts:144`). |

## E. Legal / attribution / provenance / source-comparison (allowed)

`LICENSE`, `THIRD_PARTY_NOTICES.md`, `docs/LICENSING.md`,
`docs/UPSTREAM_REUSE.md`, `docs/UPSTREAM_COMPARISON*`, the
`HERMES_SOURCE_COMPARISON.json` rows, the gap-ledger generators, and the
vendored `qubit_hermes/**` fork retain upstream names by design (MIT
attribution and source comparison). These are not product surfaces.

## F. Fixes applied in Wave 3

1. **Overlay visual identity** — `frontend/hermes-qubit/qubit-theme.json`
   (served as the active theme by `GET /api/dashboard/themes`):
   - palette: graphite `#10141c` + signal-lime `#a3e635` + electric-cyan
     `#22d3ee`; full shadcn token overrides (card/popover/secondary/muted/
     accent/border/input/ring/success/warning/destructive);
   - typography: proportional Inter sans stack + shipped JetBrains Mono for
     commands/IDs/logs (15px base, 1.55 line-height);
   - layout: `0.625rem` radius, comfortable density, `standard` variant
     (all Hermes workflows preserved);
   - `componentStyles` for header/sidebar/tab gradients;
   - `customCSS` (~4.5 KB, under the 32 KB cap) adds: Qubit scrollbars/
     selection, `:focus-visible` ring, `prefers-reduced-motion`,
     `prefers-contrast`, `forced-colors`, `pointer:coarse` 44px touch
     targets, a narrow-width breakpoint, and state styles for
     `loading` / `empty` / `degraded` / `pending`(approval) / `cancelled`
     (emergency stop) / `recovering` (retry), plus pulse/shimmer/recover
     keyframes. The vendored wordmark hide rules are preserved
     (`display:none`, `text-midground.font-bold`).
2. **Provenance** — see §G. No vendored file was edited by agent F.

## G. Vendor provenance updates (`docs/HERMES_SOURCE_COMPARISON.json`)

Agent C's handoff `docs/HERMES_SOURCE_COMPARISON.wave3.diff` was merged. Rows
in `rows[]` (exact patch id + reason + `qubit_sha256`):

| file | patch id |
|---|---|
| `frontend/hermes-web/src/plugins/index.ts` | `wave3/plugins-fleet-exports` |
| `frontend/hermes-web/src/plugins/types.ts` | `wave3/plugins-manifest-assetBase` |
| `frontend/hermes-web/src/plugins/usePlugins.ts` | `wave3/usePlugins-static-fleet` |
| `frontend/hermes-web/public/plugins/hermes-achievements/dist/index.js` | `wave3/QUBIT_ADDED:byte-identical` (scope `hermes-agent/plugins`) |
| `frontend/hermes-web/public/plugins/hermes-achievements/dist/style.css` | `wave3/QUBIT_ADDED:byte-identical` |
| `frontend/hermes-web/public/plugins/kanban/dist/index.js` | `wave3/QUBIT_ADDED:byte-identical` |
| `frontend/hermes-web/public/plugins/kanban/dist/style.css` | `wave3/QUBIT_ADDED:byte-identical` |

Qubit-added files that have no upstream counterpart — the handoff's
`fleet.ts` row used `upstream: null`, which the current
`tests/test_hermes_vendor8tf.py::test_manifest_covers_every_upstream_file`
cannot iterate without a `TypeError` (`"/" in None`). They are therefore
recorded under `wave3.added` instead of `rows[]`, with the handoff's exact
role text: `frontend/hermes-web/src/plugins/fleet.ts` and
`frontend/hermes-web/public/plugins/fleet.json`. `wave3.handoff_merged` names
the merged diff and `wave3.vendor_patches` stays `[]` (all Wave 3 vendored
changes are already rows). The vendor test needs an explicit QUBIT_ADDED
allowance before these two files can move into `rows[]`.

The four pinned plugin bundles are noted in their `reason` as carrying
product-facing upstream strings pending the §A fix.

## H. Tests and commands

```sh
python -m pytest tests/test_branding_parity_wave3.py -q
python -m pytest tests/test_dashboard_branding_wave1.py tests/test_hermes_vendor8tf.py tests/test_rebrand8tf.py -q
git diff --check
```

`tests/test_branding_parity_wave3.py` asserts:
1. no unreplaced upstream name in the CLI help tree, completions, voice
   runtime strings, installer output, overlay, minimal shell pages, or
   setup/support docs;
2. the served SPA shell and the built bundle are Qubit-branded;
3. the SPA-shipped plugin bundles are Qubit-branded (**currently fails**, §A);
4. every vendor `qubit_sha256` matches disk and every `wave3.overlay` hash is
   valid and complete;
5. the served theme carries the full visual identity and all required states.

## I. Untested

- Playwright / rendered screenshots / desktop-tablet-mobile visual diff were
  **not run** in this environment; the visual identity is asserted statically
  against the overlay theme and the served theme API. Operators should render
  `frontend/hermes-dist` at 1440 / 1024 / 390 px to confirm.
- The built bundle is an ignored artifact; the bundle test skips when
  `frontend/hermes-dist` is absent. `npm install` + `scripts/build_dashboard.sh`
  was not run by agent F.

## J. Rollback

```sh
git checkout -- frontend/hermes-qubit/qubit-theme.json
git checkout -- docs/HERMES_SOURCE_COMPARISON.json
rm -f tests/test_branding_parity_wave3.py docs/BRANDING_AUDIT_WAVE3.md
```

No other file was modified by agent F. Commit `f5fc5776` is untouched.
