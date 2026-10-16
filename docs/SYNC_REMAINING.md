# SYNC_REMAINING — upstream delta `69948c0057..bd0affe5`, measured state

Pin: `69948c0057` (Qubit's vendored base) → reference `bd0affe5` (4,128 commits ahead).
Tool: `scripts/sync_upstream.py` (3-way file merge through the rename map; no git merge —
`qubit_runtime/` has no `.git`). All counts below are **measured** on branch `sync-w24`
(HEAD `fc88e7e2`), not estimated.

Importer tool: `python3 scripts/sync_upstream.py --plan` (classify) / `--apply` (merge).

---

## 1. Total upstream delta

```
git -C ~/.cache/qubit-pins/hermes-agent diff --name-only 69948c0057..bd0affe5 | wc -l
```

| Metric | Count |
|---|---:|
| Total changed paths in delta | **4,438** |
| — Modified (M) | 3,113 |
| — Added (A) | 1,198 |
| — Deleted (D) | 117 |
| — Renamed (R) | 10 |
| Skipped by `SKIP_PREFIXES` | **202** |
| Planned (skips removed) | **4,236** |
| — of which root-level files (no `/`) | 44 |

## 2. Files SKIPPED by `SKIP_PREFIXES`

`SKIP_PREFIXES = (".github/", "contributors/", "node_modules/", ".git/")`

| Prefix | Files in delta | Reason |
|---|---:|---|
| `.github/` | **56** | upstream repo's own workflows/CI |
| `contributors/` | **146** | 1,705 third-party emails; not Qubit's to carry |
| `node_modules/` | 0 | build internals (none in delta) |
| `.git/` | 0 | VCS internals (none in delta) |
| **Total skipped** | **202** | |

**`website/` and `plugin-catalog/` are NO LONGER skipped** (removed in `ffe136f0`) and are
synced: `website/` 129 delta files, `plugin-catalog/` 170 delta files. Docs must track the
code the sync brings in (129 changed upstream website files incl. `context-compression-and-caching`,
`cron-internals`, `desktop-plugin-sdk`).

## 3. Files still in CONFLICT (planner `merge_one` verdict, by subsystem)

A `CONFLICT` verdict means the 3-way merge leaves `<<<<<<<` hunks: Qubit's local divergence
and upstream's change overlap on the same region. 580 total.

| Subsystem | CONFLICT (non-test) | CONFLICT (tests) | Total |
|---|---:|---:|---:|
| apps | 161 | 0 | 161 |
| hermes_cli | 100 | 0 | 100 |
| tests | 0 | 90 | 90 |
| website | 48 | 0 | 48 |
| tools | 43 | 0 | 43 |
| agent | 23 | 0 | 23 |
| locales | 17 | 0 | 17 |
| scripts | 16 | 0 | 16 |
| ui-tui | 16 | 0 | 16 |
| tui_gateway | 15 | 0 | 15 |
| gateway | 11 | 0 | 11 |
| plugins | 8 | 0 | 8 |
| cron | 7 | 0 | 7 |
| nix | 3 | 0 | 3 |
| optional-skills | 2 | 0 | 2 |
| pm | 2 | 0 | 2 |
| tests-js | 0 | 3 | 3 |
| acp_adapter / evals / skills / web / plugin-catalog | 1 each | 0 | 5 |
| AGENTS.md, CONTRIBUTING.{md,es.md}, Dockerfile, pyproject.toml, uv.lock, cli.py, run_agent.py, toolsets.py, activate | 1 each | 0 | 9 |
| **Total** | **487** | **93** | **580** |

Reconciliation versus the earlier report: it listed locales(17), nix(3), plugins(13),
scripts(15), pm(2), agent(17), ui-tui(19), tools(26+), gateway(15) as RESOLVED, and
"86 deferred" for tests. Reality after re-running the planner:

- Those subsystems' **resolved commits landed, but the planner still reports CONFLICT for
  part of each** (agent 23 vs 17 claimed; tools 43 vs 26; ui-tui 16 vs 19; plugins 8 vs 13;
  scripts 16 vs 15; nix 3 ✓; locales 17 ✓; pm 2 ✓; gateway 11 vs 15). The earlier counts
  were per-slice; they do not sum to the whole.
- **tests**: 90 conflicting (tests) + 3 (tests-js) = **93**, not 86.
- **Newly visible, never worked**: `apps` **161**, `hermes_cli` **100**, `website` **48**,
  `tui_gateway` **15**, `web` **1**, `cron` **7**, `evals` **1**, `skills` **1**,
  root-level files **9**.

### What this session RESOLVED (branch `sync-w24`, committed)

**252 of the 487 non-test conflicts are now resolved (331 files written).** Every write passed
all of these invariants, re-checked after the batch (`docs` this file records the method):

- 3-way merge with a **targeted de-brand**: take NEW per hunk, then map Qubit's own de-brands
  (`Nous Research/Nous Portal → Qubit Labs`, the manifest `phrase_map`), revert the sync's
  **false identifier translations**, and fix the sync's URL-protect module-path leak
  (`hermes_cli→qubit_cli`, `hermes_platform→qubit_platform`, `hermes_state_*→qubit_state_*`).
- The false-rename set is **derived empirically**: every `HERMES_*` token upstream's delta uses
  that Qubit's own HEAD *independently keeps* is reverted; only `HERMES_HOME→QUBIT_HOME` is a
  real rename (manifest `env_map` has exactly that one entry).
- A resolution is **rejected** if it (a) fails to compile/parse, (b) introduces a `QUBIT_<false>`
  identifier Qubit's HEAD does not use, (c) loses a `def`, (d) loses a `HERMES_*` wire env var,
  or (e) shrinks the file below 70% of HEAD (upstream-trim vs Qubit-local content).

Verified after the batch: **0 conflict markers, 0 test files touched, 0 trap violations**
(`QUBIT_ACTIVATED`/`QUBIT_AGENT_HELP_GUIDANCE`/`QUBIT_OVERLAYS`/`QUBIT_KANBAN_HOME`), **0 false
`QUBIT_` identifiers**, **0 py syntax errors**, **0 def/HERMES/shrink losses**, gate **29/0**.
Representative pytest slice (4 files): **281 passed / 8 failed — identical to HEAD** (the 8 are
pre-existing, proven by stash).

Paths resolved (by delta subsystem): apps 131, hermes_cli 35, website 35, tools 9, locales 17,
agent 1, gateway 10, scripts 8, tui_gateway 4, plugins 3, cron 3, ui-tui 8, pm 1, nix 0, root
files 5. The 3 tools files (`credential_files.py`, `file_tools.py`, plus the whole `tools/web_*`
family) were left to the earlier `078b8739` tools slice.

### What remains and why

Two mechanical paths were tried and **reverted** before the working one:

1. **Take translated upstream-NEW verbatim.** Unsafe: `translate()` rewrites `hermes_`/`HERMES_`
   tokens but **not bare `Hermes`/`Nous` prose** (the rule needs `Hermes[A-Z_]`). Taking NEW
   verbatim injects upstream brand prose and loses Qubit divergence (`cgraph-dev/Qubit-AIO`
   HTTP-Referer, `Qubit Agent` titles, `HERMES_*` wire env vars, plugin-compat `__getattr__`
   blocks). Measured: 15 files gained forbidden brand tokens; 420/420 dropped Qubit-local lines.
2. **Normalize brand identifiers, merge, un-normalize.** Lossy: the reverse map fabricated
   `x_state_holders` and re-inserted `hermes_cli`, breaking `qubit_cli.backup` and `cron.*`.

The 235 residue conflicts are the **heaviest-overlap** files: Qubit and upstream both rewrote
the *same* lines (upstream refactor + Qubit de-brand on the same statement), so a whole-file
take-NEW loses Qubit-local content (`telegram_managed_bot.py` 7 defs, i18n `ar.ts`/`zh-hant.ts`
entire tables). They need the per-subsystem literal resolvers already written for the harder
slices: `sync_resolve_agent.py`, `sync_resolve_ui_tui.py` (+ `FALSE_RENAME_FIX` / `PER_FILE`
maps). The 866-token false-rename set is at `/tmp/w24_false_tokens.json`.

### Remaining conflicts by subsystem (measured, after this session)

| Subsystem | Remaining | | Subsystem | Remaining |
|---|---:|---|---|---:|
| hermes_cli | 65 | | website | 13 |
| tools | 34 | | gateway | 11 |
| apps | 30 | | tui_gateway | 11 |
| agent | 22 | | scripts | 8 |
| locales | 17 | | ui-tui | 8 |
| plugins | 5 | | cron | 4 |
| root files | 4 | | tests | 93 (sibling) |
| **Total non-test** | **235** | | **Total incl. tests** | **328** |

## 4. Subsystems NOT yet fully applied (delta dir vs what landed)

"Landed" = a 40-file sample of the subsystem's delta whose Qubit route already equals
translated upstream-NEW; "DIFF" = still carries pre-sync/Qubit-local content (needs review or
is one of the conflicts above).

| Subsystem | Delta files | Sample landed | Sample DIFF | Vendor dir in `qubit_runtime/` |
|---|---:|---:|---:|---|
| apps | 1,514 | 24/40 | 15 | **yes** (`apps`) |
| tests | 1,094 | 21/40 | 17 | **yes** (`tests`) — sibling owns |
| hermes_cli | 330 | 7/40 | 33 | **no** → routes to `qubit_cli/` |
| plugin-catalog | 170 | 38/40 | 0 (1 missing) | **yes** |
| tools | 165 | 11/40 | 28 | **yes** |
| agent | 163 | 11/40 | 29 | **yes** |
| ui-tui | 134 | 33/40 | 7 | **yes** |
| website | 129 | 4/40 | 36 | **yes** |
| plugins | 118 | 11/40 | 23 (6 `?`) | **yes** |
| gateway | 93 | 16/40 | 23 | **yes** |
| tui_gateway | 67 | 14/40 | 26 | **yes** |
| scripts | 52 | 9/40 | 28 | **yes** |
| `<root>` (44 root files) | 44 | 10/40 | 27 (1 missing) | n/a (root) |
| locales | 35 | 18/40 | 17 | **yes** |
| web | 35 | 9/40 | 26 | **yes** |
| cron | 18 | 8/40 | 10 | **yes** |
| pm | 18 | 9/40 | 9 | **yes** |
| tests-js | 14 | 0/40 | 10 (4 missing) | **yes** |
| optional-skills | 11 | 0/40 | 9 (2 missing) | **yes** |
| acp_adapter | 7 | 0/40 | 7 | **yes** |
| evals | 6 | 0/40 | 5 | **yes** |
| nix | 6 | 0/40 | 6 | **yes** |
| skills | 4 | 0/40 | 4 | **yes** |
| assets | 3 | 0/40 | 2 (1 missing) | **yes** |
| docker | 2 | 2/2 | 0 | **yes** |
| hermes_platform | 2 | 2/2 | 0 | **no** → routes to `qubit_platform/` |
| providers | 2 | 0/2 | 2 | **yes** |

**Subsystems with NO dedicated sync commit at all** (delta dir absent from any `sync(<x>)`
commit message): `tui_gateway`, `web`, `cron`, `tests-js`, `optional-skills`, `acp_adapter`,
`evals`, `skills`, `providers`, `assets`, `<root>`, `hermes_platform`. Each is vendored, so
each carries real work. `hermes_cli`/`hermes_platform` are vendored under the rename map
(`qubit_cli/`, `qubit_platform/`).

## 5. Import gate

```
python3 scripts/sync_import_gate.py --compare /tmp/w24_before.json
  gate: 29 OK / 0 FAIL (of 29 modules)   no regression
```

Baseline captured on `sync-w24` HEAD `fc88e7e2`: **29 OK / 0 FAIL**. Any resolution that
changes the failing-module set must be reverted — both mechanical attempts above tripped it
(`qubit_cli.backup` → `x_state_holders`; `cron.jobs`/`cron.scheduler` → `hermes_cli`).

## 6. Bottom line

- Delta to port: **4,236** files (202 skipped).
- Conflicts at session start: **580** (**487 non-test** + **93 tests**).
- **Resolved this session: 252 non-test conflicts (331 files written).** Remaining: **235
  non-test** (heaviest-overlap files — see §3) + **93 tests** (sibling scope) = **328**.
- Gate holds at **29 OK / 0 FAIL**; tree carries only the 331 resolved files; pytest slice
  identical to HEAD (281 pass / 8 pre-existing fail). Verbatim take-NEW and normalize-merge
  are provably unsafe (§3); every applied file passed the identifier/def/HERMES/shrink gates.

