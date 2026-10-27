# PHASE 8 — owner sign-off pack (re-measured)

**Status:** read-only decision aid. **Owner sign-off still required.** No product code, docs,
env vars, paths, globals, or services were changed, deleted, or renamed by this lane. This
document does not claim `VERIFIED`, parity, or completion.

- Worktree: `/tmp/opencode/wt-ready-p8`
- Branch: `feat/ready-p8`
- Measured commit: `dfe2f82c5` (`docs: plan the readiness wave (8 disjoint lanes) before the gate`)
- Inventory under review: `docs/PHASE8_INVENTORY.md` (measured at `6bc665780`, 2026-10-27)
- Drift window: **69 commits** (`git rev-list --count 6bc665780..HEAD`)
- Commands run with `nice -n 10`; all searches use `git grep` over **git-tracked files only**
  (untracked build output and `__pycache__` are not counted).
- `docs/archive/` was neither read nor written.

This pack re-measures the `PHASE8_INVENTORY.md` headline counts on the current HEAD, records the
drift, and restates §8 row by row with current evidence and the owner decision each row needs.
**It recommends no blanket rename and no blanket deletion.** Every option below is scoped to one
row, and two rows (attribution; plugin-SDK compatibility) are **non-negotiable exceptions**.

## 0. Measurement conventions

`GLOBS` (code surface) and `PROD` (production filter) are the inventory's own definitions:

```bash
GLOBS=( '*.py' '*.ts' '*.tsx' '*.js' '*.mjs' '*.cjs' '*.sh' '*.bash' '*.zsh' '*.yaml' '*.yml' \
        '*.toml' '*.service' '*.env' '.env.example' )
PROD=( ':!**/tests/**' ':!tests/**' ':!**/tests-js/**' ':!**/test_*.py' ':!**/*.test.ts' \
       ':!**/*.test.tsx' ':!**/__tests__/**' ':!qubit_runtime/website/**' )
```

Note the two distinct pathspecs `:!**/tests/**` and `:!tests/**`: the first does not match a
top-level `tests/` directory, so both are required. Omitting `:!tests/**` over-counts production.

## 1. Re-measured headline counts (doc number vs HEAD)

| Surface | Measure | `PHASE8_INVENTORY.md` (`6bc665780`) | Observed (`dfe2f82c5`) | Drift | Command |
|---|---|---:|---:|---:|---|
| `HERMES_*` tokens | distinct, all tracked | **850** | **850** | 0 | `git grep -ohE '\bHERMES_[A-Z0-9_]+' \| sort -u \| wc -l` |
| `HERMES_*` tokens | occurrences, all tracked | **12,150** | **12,216** | **+66** | `git grep -ohE '\bHERMES_[A-Z0-9_]+' \| wc -l` |
| `HERMES_*` tokens | distinct, code surface | **810** | **810** | 0 | `git grep -ohE '\bHERMES_[A-Z0-9_]+' -- "${GLOBS[@]}" \| sort -u \| wc -l` |
| `HERMES_*` tokens | occurrences, code surface | **9,895** | **9,895** | 0 | same pipeline without `sort -u \|` |
| `HERMES_*` tokens | distinct, production | **688** | **688** | 0 | code surface + `"${PROD[@]}"` + `sort -u \| wc -l` |
| `HERMES_*` tokens | occurrences, production | **3,697** | **3,697** | 0 | same pipeline without `sort -u \|` |
| `.hermes` token | occurrences / files, all tracked | **8,166 / 2,305** | **8,289 / 2,309** | **+123 / +4** | `git grep -oF '.hermes' \| wc -l` · `git grep -lF '.hermes' \| wc -l` |
| `~/.hermes` | occurrences / files, all tracked | **2,917 / 860** | **2,954 / 862** | **+37 / +2** | `git grep -oF '~/.hermes' \| wc -l` · `git grep -lF '~/.hermes' \| wc -l` |
| `~/.hermes` | occurrences, production | **819** | **819** | 0 | `git grep -oF '~/.hermes' -- "${PROD[@]}" ':!docs/**' \| wc -l` |
| `/root/.hermes` | occurrences / files, all tracked | **164 / 47** | **184 / 49** | **+20 / +2** | `git grep -oF '/root/.hermes' \| wc -l` · `git grep -lF '/root/.hermes' \| wc -l` |
| `/root/.hermes` | occurrences / files, production | **164 / 47** | **60 / 18** | **−104 / −29**¹ | `git grep -oF '/root/.hermes' -- "${PROD[@]}" \| wc -l` · `… -lF …` |
| Plugin-SDK legacy globals (`__HERMES_PLUGIN_SDK__` + `__HERMES_PLUGINS__`) | occurrences | **91** | **105** | **+14** | `git grep -oF '__HERMES_PLUGIN_SDK__' \| wc -l` + `…'__HERMES_PLUGINS__'…` (56 + 49) |
| Attribution markers (`NousResearch` + `nousresearch.com` + `Nous Research`) | occurrences | **1,901** | **1,920** | **+19** | `git grep -oF 'NousResearch' \| wc -l` (933) + `…'nousresearch.com'…` (708) + `…'Nous Research'…` (279) |
| Brand writer/reader guard | `check_brand_half_rename.py` | **OK (46 globals)** | **OK (46 globals)** | 0 | `nice -n 10 /home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python scripts/check_brand_half_rename.py` → exit 0 |

¹ The inventory's production figure equalled its all-tracked figure (164 / 47). On this HEAD that
is no longer true: the same production filter now reports **60 / 18**, because 24 test files and
2 website files carry `/root/.hermes` (113 + 2 occurrences). This is a **method/scope drift** as
well as content drift; treat the inventory's 164 / 47 production cell as superseded by
all-tracked 184 / 49 and production 60 / 18.

### 1.1 Secondary measures (same commands, current HEAD)

| Measure | Doc | Observed | Drift | Command |
|---|---:|---:|---:|---|
| `$HOME/.hermes` occurrences | 87 | **91** | +4 | `git grep -oF '$HOME/.hermes' \| wc -l` |
| `.hermes/` occurrences | 2,999 | **3,013** | +14 | `git grep -oF '.hermes/' \| wc -l` |
| `.hermes/` production occurrences | 694 | **727** | +33 | `git grep -oF '.hermes/' -- "${PROD[@]}" \| wc -l` |
| `.hermes\` occurrences | 29 | **31** | +2 | `git grep -oF '.hermes\' \| wc -l` |
| `.hermes_history` occurrences | 45 | **51** | +6 | `git grep -oF '.hermes_history' \| wc -l` |
| `_LEGACY_HOME_DIR` occurrences / files | 19 / 8 | **24 / 9** | +5 / +1 | `git grep -oF '_LEGACY_HOME_DIR' \| wc -l` · `… -lF …` |
| `requires_hermes` occurrences / files | 295 / 246 | **299 / 247** | +4 / +1 | `git grep -oF 'requires_hermes' \| wc -l` · `… -lF …` |
| `.hermes.pre-retirement` occurrences / files | 34 / 14 | **38 / 16** | +4 / +2 | `git grep -oF '.hermes.pre-retirement' \| wc -l` · `… -lF …` |
| `HERMES_HOME_MODE` occurrences | 25 | **26** | +1 | `git grep -oF 'HERMES_HOME_MODE' \| wc -l` |
| `${HERMES_SKILL_DIR}` occurrences / files | 37 / 12 | **39 / 16** | +2 / +4 | `git grep -oF '${HERMES_SKILL_DIR}' \| wc -l` · `… -lF …` |
| `${HERMES_SESSION_ID}` occurrences / files | — / 12 | **14 / 12** | n/a | `git grep -oF '${HERMES_SESSION_ID}' \| wc -l` |
| `get_hermes_home` alias-family occurrences / files | 108 / 42 | **170 / 49** | +62 / +7² | `git grep -ohE 'get_hermes_home\|get_hermes_home_override\|set_hermes_home_override\|reset_hermes_home_override\|hermes_home_key' \| wc -l` |
| `THIRD_PARTY_NOTICES.md` `## ` records | 22 | **24** | +2 | `grep -c '^## ' THIRD_PARTY_NOTICES.md` |
| `Copyright (c)` occurrences | 101 | **103** | +2 | `git grep -oF 'Copyright (c)' \| wc -l` |
| `Apache-2.0` occurrences | 313 | **324** | +11 | `git grep -oF 'Apache-2.0' \| wc -l` |
| Canonical plugin globals (`__QUBIT_PLUGIN_SDK__` + `__QUBIT_PLUGINS__`) | 160 | **174** | +14 | 82 + 92 |
| Legacy plugin aliases (`__QUBIT_PLUGIN_SDK_LEGACY__` + `__QUBIT_PLUGINS_LEGACY__`) | 16 | **24** | +8 | 12 + 12 |
| Vendored website `hermes` lines / files | 7,483 / 429 | **7,483 / 429** | 0 | `git grep -E 'hermes' -- 'qubit_runtime/website/**' \| wc -l` · `… -l …` |
| Electron `hermes:*` occurrences / distinct | 751 / 127 | **766 / 293**³ | +15 / +166 | `git grep -oE 'hermes:[A-Za-z0-9_.:-]+' -- 'qubit_runtime/apps/desktop/**' \| wc -l` / `… -ohE … \| sort -u \| wc -l` |
| Electron `qubit:*` occurrences / distinct | 56 / 22 | **53 / 30**³ | −3 / +8 | same with `qubit:` |

² The inventory counted a narrower alias set; this run also matches `get_hermes_home_override` and
the setter/resetter/key helpers. The direction of drift is upward but the two numbers are not
strictly comparable.
³ The inventory's distinct count appears to use a narrower IPC-channel grammar than
`[A-Za-z0-9_.:-]+`; occurrences are the comparable measure and drifted +15.

### 1.2 Drift summary

- **Stable:** the `HERMES_*` code-surface and production counts are unchanged (810/9,895 and
  688/3,697), and all-tracked distinct is unchanged (850). 69 commits added 66 all-tracked
  occurrences, all outside the code surface (docs/tests/fixtures).
- **Up:** `.hermes` raw token (+123 occurrences, +4 files), `~/.hermes` (+37 / +2; production
  unchanged at 819), `/root/.hermes` (+20 / +2 all tracked), legacy plugin globals (+14),
  attribution (+19), update markers (see D6), and the vendored-adjacent anchors above.
- **Reclassified by scope:** `/root/.hermes` production is 60/18 under the inventory's own
  production filter, not 164/47. The difference is test/website coverage that the inventory's
  production cell did not reflect.
- **Guard:** still green, same 46 globals (`scripts/check_brand_half_rename.py`, exit 0).
- No headline count moved enough to change a Keep/Migrate/Archive recommendation in §8.

## 2. Decision table — `PHASE8_INVENTORY.md` §8, row by row

§8 currently contains **D1–D22** (not only D1–D20). All rows present are reproduced. Evidence is
re-measured on `dfe2f82c5` unless marked otherwise. **Recommended decisions are per-row and are
not a blanket policy.** The owner ticks exactly one option per row.

Legend: **Keep** = leave as-is (load-bearing or non-product). **Migrate later** = an owner-approved,
single-change migration is possible but out of scope now. **Archive-delete after sign-off** = the
surface is evidence/docs only and may be retired *after* the owner signs; never a product rename.

| # | Surface | Current measured evidence | Classification | Risk | Required owner decision |
|---|---|---|---|---|---|
| **D1** | `HERMES_*` env contract | 688 distinct / 3,697 occ production; 810 / 9,895 code surface; 850 / 12,216 all tracked. Writer `qubit_runtime/qubit_cli/gateway_runtime_start.py:55`; reader `qubit_runtime/qubit_cli/update_lock.py:100` | internal contract | High — reader-only rename is a silent half-rebrand / launch failure | **Keep as-is.** If ever migrated, it must be one atomic writer+reader+launch+systemd/desktop+tests change, never a partial rename. **Migrate later** only with that plan. |
| **D2** | `_LEGACY_HOME_DIR` home fallback | 24 occ / 9 files; `qubit_runtime/qubit_constants.py:55,61-62,137` | read-old shim | High — orphans every existing install's state/secrets | **Keep.** A future canonical-only mode needs an explicit owner migration; not this lane. |
| **D3** | `HERMES_HOME` + resolver | Part of D1; `qubit_runtime/qubit_constants.py:198` (`get_qubit_home`), `qubit_runtime/qubit_cli/update_lock.py:100` | internal contract | High — wrong home → wrong credentials/state | **Keep.** Add a compatibility test if any change is attempted. |
| **D4** | Functional `~/.hermes` paths (update/desktop/remote/installer) | 819 production `~/.hermes`; `qubit_runtime/qubit_cli/update_lock.py:100`; `qubit_runtime/apps/desktop/electron/remote-lifecycle.ts:40,226,285`; `qubit_runtime/apps/bootstrap-installer/src-tauri/src/paths.rs:40,205`; `voicebridge/agent_compat/adapters/hermes.py:55,97-103` | internal contract / adapter / migration | High — update/remote/installer break | **Keep.** Any change requires verifying each reader against a pre-rename install first; no change in this lane. |
| **D5** | `/root/.hermes` container bind | 184 occ / 49 files all tracked; 60 occ / 18 files production; `qubit_runtime/agent/file_safety.py:520-522`; `qubit_runtime/agent/terminal_env_provider.py:36`; `qubit_runtime/tools/credential_files.py:66` | internal contract | High — sandbox paths dangle | **Keep.** Only change with the image mount, every host mapper, and a container test in one change. |
| **D6** | Update markers / locks | `.hermes-update-in-progress` 138/51; `.hermes-update-result` 57/27; `.hermes-update-old` 44/12; `.hermes-bootstrap-complete` 45/25; `.hermes-update-staging` 41/9; `hermes-update.lock` 59/15 | migration surface / internal contract | High — in-flight update misread/lost | **Keep.** Canonical aliases only if they retain read-old support for markers written by the previous build. |
| **D7** | Legacy data filenames | `hermes_config_overrides.json` 7/6; `hermes_persona.json` 6/5; `.hermes_history` 51/17 | read-old shim | Medium-high — existing data unread | **Keep.** Any new name must dual-read old and write new under an owner migration plan. |
| **D8** | Plugin-SDK host/bundle globals | `__HERMES_PLUGIN_SDK__` 56/23, `__HERMES_PLUGINS__` 49/24 (legacy 105 total); canonical 82/40 + 92/41; live host dual `frontend/qubit-web/src/plugins/registry.ts:195,208`; vendored host legacy-only `qubit_runtime/web/src/plugins/registry.ts:118`; bundles canonical-only (`qubit_runtime/plugins/qubit-achievements/dashboard/dist/index.js:8`) | internal contract + **non-negotiable compatibility exception** | High — plugin registration fails on a served surface | **Keep legacy aliases everywhere — non-negotiable.** Verify `qubit serve`/desktop build before any cleanup; if the vendored host is served, add canonical aliases while retaining the legacy ones. **Migrate later** only as dual-publish. |
| **D9** | Session/auth window globals | Legacy: `__HERMES_SESSION_TOKEN__` 33/16, `__HERMES_AUTH_REQUIRED__` 13/7, `__HERMES_BASE_PATH__` 10/8. Canonical: 208/88, 39/22, 54/28. Writer `qubit_runtime/qubit_cli/web_server_dashboard.py:140-141,173,186-187`; guard **OK (46)** | security boundary | Highest — auth/token outage | **Keep canonical pair + guard.** Only legacy docs/evidence (no writer) may be removed, and only after sign-off; never rename one side. |
| **D10** | `requires_hermes` + skill tokens | `requires_hermes` 299/247; `${HERMES_SKILL_DIR}` 39/16; `${HERMES_SESSION_ID}` 14/12. Readers `qubit_runtime/qubit_cli/plugin_validate.py:108`, `qubit_runtime/agent/skill_preprocessing.py:17` | read-old shim / external contract | High — external plugins/skills stop loading | **Keep read-old.** Add a canonical field/token later only with dual-read. |
| **D11** | `get_hermes_home` alias family | 170 occ / 49 files (broader regex; inventory 108/42); `qubit_runtime/qubit_constants.py:247-258` | adapter / upstream compat | High — `ImportError` in synced call sites | **Keep.** Remove only after the upstream sync no longer imports the alias. |
| **D12** | systemd unit names + restart recovery | Install publishes `qubit-gateway.service` (`scripts/install_gateway_unit.sh:5`, `qubit_runtime/qubit_cli/gateway.py:2231`); recovery candidates at `qubit_runtime/qubit_cli/update_restart_recovery.py:147` are `hermes-gateway.service` / `gateway.service` / `gateway-default.service` | **suspected half-rebrand** | Medium-high — post-update restart verification may fail | **Migrate later (targeted fix):** add `qubit-gateway*` candidates to recovery. This is a code change, so it is out of scope for this read-only lane; owner must approve it separately. |
| **D13** | NixOS option names | `services.hermes-agent` 41 matching lines; `programs.hermes-agent` 17 (`.nix`); `qubit_runtime/nix/checks.nix:40,57,80-90` | external config contract | Medium — existing Nix configs break | **Keep.** A Qubit option alias with migration can be considered later. |
| **D14** | Audit paths + frozen pin | `.hermes.pre-retirement` 38/16; `FROZEN_REV` `scripts/frozen_target_progress.py:71` | audit instrument (not product) | Medium — audits measure nothing | **Keep.** Move only with every audit updated. |
| **D15** | Attribution / legal | `qubit_runtime/LICENSE:3` MIT © 2025 Nous Research; `THIRD_PARTY_NOTICES.md` 24 `## ` records; `NousResearch` 933/404, `nousresearch.com` 708, `Nous Research` 279; `docs/BRAND.md:41` names the exception | attribution / legal — **non-negotiable exception** | Legal defect if touched | **Keep — non-negotiable.** Legal review only; never a rebrand target. |
| **D16** | Vendored upstream website docs | `qubit_runtime/website/**` — 7,483 `hermes` lines / 429 files; 1,558 `~/.hermes` | vendored docs | Low runtime; size/brand surface | Owner call: **Keep** or **Archive-delete after sign-off** from the shipped tree. Do not rename in place; confirm no build references first. |
| **D17** | Internal `~/.hermes` prose in docstrings | Part of 819 production `~/.hermes`; anchors `qubit_runtime/agent/curator.py:85`, `qubit_runtime/agent/anthropic_credentials.py:6,738,819` | stale prose | Low — maintainer confusion only | **Keep**, or clean only where the prose is factually wrong. Not a functional rename; not this lane. |
| **D18** | Bundled optional-skill scripts/prose | 10 `.hermes` occurrences / 7 files (inventory; mostly migration scripts) | migration utility / stale prose | Low | **Keep.** Update non-migration prose later only if the owner wants; never rewrite migration sources. |
| **D19** | Root reports/evidence | `INDEPENDENCE_VERDICT.md` (inventory: 72 `.hermes` / 64 `~/.hermes`); `docs/e2e/evidence-api-chunk-served.js` reads the legacy token | evidence artifact | None to runtime | **Keep** or **Archive-delete after sign-off** by owner call. No product action. |
| **D20** | Electron IPC `hermes:*` channels | 766 occ / 293 distinct `hermes:*` vs 53/30 `qubit:*` in `qubit_runtime/apps/desktop/**` (inventory: 751/127 vs 56/22) | internal contract | High — preload/renderer/plugin break | **Keep.** Per-channel writer/reader migration only, with a preload+renderer+plugin test. |
| **D21** | 22 write-only env names | Inventory: 22 names; not re-derived here (line-level classification). Examples: `HERMES_DISABLE_PLUGINS`, `HERMES_EXEC_ASK`, `HERMES_GATEWAY_SESSION`, `HERMES_SECRET_KEY`, `HERMES_UPDATE_HANDOFF_PID` | needs verification (not "dead") | Medium — removing a writer a child reads | **Keep** until an external/child reader is ruled out. **Verify** first if the owner wants to touch them. |
| **D22** | Test-only env names | Inventory: 116 names absent from production surface; not re-derived here | test fixture | None | **Keep as tests.** No product action. |

## 3. What must never be renamed (with current evidence)

These are the boundaries where a one-sided rename is a total outage. File:line is verified on
`dfe2f82c5`.

1. **`HERMES_*` env writer/reader pairs** — the writer and reader live in different processes:
   - `HERMES_HOME`: writer `qubit_runtime/qubit_cli/gateway_runtime_start.py:55`; reader
     `qubit_runtime/qubit_cli/update_lock.py:100`.
   - `HERMES_SESSION_ID`: writer `qubit_runtime/agent/agent_init.py:1181`; reader
     `qubit_runtime/gateway/media_fetch.py:75`.
   - `HERMES_PYTHON`: reader `qubit_runtime/qubit_cli/main_tui_launch.py:363`; writer `:372`.
   - 304 production names are read but never written in-tree (set by an external parent); a
     reader-only rename is exactly the half-rebrand bug class (`docs/BRAND.md:72-84`).
2. **`_LEGACY_HOME_DIR = ".hermes"`** — the read-old home fallback:
   `qubit_runtime/qubit_constants.py:55,61-62,137`. Renaming it orphans existing
   installs' state and secrets at once.
3. **`/root/.hermes` container bind path** — host-side and image-side must agree:
   `qubit_runtime/agent/file_safety.py:520-522`,
   `qubit_runtime/agent/terminal_env_provider.py:36`,
   `qubit_runtime/tools/credential_files.py:66` (and the other `container_base` call sites).
4. **Plugin-SDK globals** — installed third-party bundles we cannot rebuild read the legacy names:
   - live host dual-publishes: `frontend/qubit-web/src/plugins/registry.ts:195` (canonical) and
     `:208` (legacy alias);
   - vendored host publishes legacy only: `qubit_runtime/web/src/plugins/registry.ts:118`;
   - in-tree bundles read canonical only, e.g.
     `qubit_runtime/plugins/qubit-achievements/dashboard/dist/index.js:8`.
   This is **dual-publish by contract**, not a leftover (`docs/BRAND.md:62-65`).
5. **Session/auth window globals** — a security boundary:
   `qubit_runtime/qubit_cli/web_server_dashboard.py:140-141,173,186-187` writes only
   `__QUBIT_SESSION_TOKEN__` / `__QUBIT_AUTH_REQUIRED__` / `__QUBIT_BASE_PATH__`; the guard
   `scripts/check_brand_half_rename.py` is green (46 globals).
6. **Attribution / legal** — the required exception (`docs/BRAND.md:41`):
   `qubit_runtime/LICENSE:3` (MIT © 2025 Nous Research), `THIRD_PARTY_NOTICES.md:505,520,558`,
   and upstream URLs/repo names. Removing or rewriting these is a license defect, not a rebrand.
7. **Frozen upstream pin** — `scripts/frozen_target_progress.py:71`
   (`FROZEN_REV = "43faacfb8de7b29548547a7fbdedad0cc6e7391c"`). Rewriting it destroys the audit
   trail and the sync target.
8. **External plugin/skill contracts** — `requires_hermes` (read-old, accepted after canonical at
   `qubit_runtime/qubit_cli/plugin_validate.py:108`), and the literal `${HERMES_SKILL_DIR}` /
   `${HERMES_SESSION_ID}` tokens matched by `qubit_runtime/agent/skill_preprocessing.py:17`.

## 4. Owner sign-off still required; no changes made

- **This lane made no changes to product code, env vars, paths, globals, systemd units, docs
  under `docs/archive/`, or running services.** It only adds this file.
- Every row in §2 is a decision the **owner** must sign. The recommended defaults are per-row and
  do **not** authorize a blanket rename, a blanket deletion, or a partial `HERMES_*` migration.
- D8 (plugin-SDK compatibility) and D15 (attribution) are **non-negotiable exceptions**: they stay
  as dual-publish / legal text regardless of any future sweep. D9 (auth globals) must keep the
  canonical writer/reader pair and the guard.

### Post-sign-off re-validation command

A future agent must run this **after** the owner signs a row and before implementing it. It
re-measures the headline surface, re-checks the half-rename guard, and re-runs the AST
name-binding check:

```bash
cd /home/trick/Projects/Repos/qubit-voicebridge
nice -n 10 bash -c '
  echo "== HEAD =="; git rev-parse --short HEAD
  echo -n "env_all_distinct="; git grep -ohE "\bHERMES_[A-Z0-9_]+" | sort -u | wc -l
  echo -n "env_all_occ=";      git grep -ohE "\bHERMES_[A-Z0-9_]+" | wc -l
  echo -n "dot_hermes_occ=";   git grep -oF ".hermes" | wc -l
  echo -n "home_hermes_occ=";  git grep -oF "~/.hermes" | wc -l
  echo -n "root_hermes_occ=";  git grep -oF "/root/.hermes" | wc -l
  echo -n "legacy_plugin_globals="; expr $(git grep -oF "__HERMES_PLUGIN_SDK__" | wc -l) + $(git grep -oF "__HERMES_PLUGINS__" | wc -l)
'
nice -n 10 /home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python scripts/check_brand_half_rename.py
nice -n 10 /home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python scripts/check_half_rebrand.py
```

Expected on `dfe2f82c5`: `env_all_distinct=850`, `env_all_occ=12216`, `dot_hermes_occ=8289`,
`home_hermes_occ=2954`, `root_hermes_occ=184`, `legacy_plugin_globals=105`, and the guard prints
`OK (46 global name(s) consistent)` with exit 0. A change to any of these means the tree moved;
re-read this pack before acting.

## 5. What this lane did NOT verify

- No live service inspection/restart and no `/proc` env read; this pack cannot state what is set
  in the running gateway, desktop, voicebridge, or helper processes.
- No built-bundle inspection. `frontend/qubit-dist` and `qubit_runtime/qubit_cli/web_dist` are
  absent/gitignored; the D8 plugin-host mismatch remains source-level only.
- No installed external plugin bundles (only the four in-tree `dist/index.js` files).
- No full test matrix and no `check_half_rebrand.py`/`check_duplicate_defs.py`/frozen-target/
  capability audit run in this lane (only `check_brand_half_rename.py`).
- No per-name dataflow analysis; reader/writer classification is line-level regex, so names passed
  through unknown helpers or child processes can be misclassified.
- No remote-host, NixOS-config, or Windows-native verification.
- No legal review; attribution counts are mechanical and do not assert every obligation is met.

## Appendix — commit / scope checks

```bash
cd /tmp/opencode/wt-ready-p8
git rev-parse --abbrev-ref HEAD                      # feat/ready-p8
git rev-parse --short HEAD                           # dfe2f82c5
git rev-list --count 6bc665780..HEAD                 # 69
git ls-files | wc -l                                 # 23069
git ls-files '*.py' | wc -l                          # 11832
```

Counts are a snapshot of `dfe2f82c5`. The tree moves; re-run the command rather than quoting this
document as current.
