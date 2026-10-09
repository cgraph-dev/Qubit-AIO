# REBRAND AUDIT — Qubit Agent (assets)

**Question answered:** "verify if rebrand is complete."
**Verdict:** **NO — the rebrand is not complete on user-visible surfaces.** The CLI `--help`
surface is fully clean; the dashboard (`frontend/qubit-web`) is clean for user-visible strings;
but the **desktop app's i18n packs** and the **bundled-skill prose** still ship upstream product
names to users, and the repo's own branding test suite fails (7 failed / 157 passed).

- Worktree: `/tmp/qb-v2`, branch `v2-rebrand-audit`, commit **`4f4bef6e`** (main tip).
- `git status --porcelain` at start: **0** lines. Nothing stashed. Only this doc committed.
- **`python3 scripts/gate_all.py` → PASS 5/5** (with the main checkout's `.venv` symlinked into the
  worktree; the gate refuses to run under system python by design).
- All `git grep` below excludes `__pycache__` implicitly (`-I` skips binary; tracked-files only).

---

## 0. Raw totals (METHOD + RAW COUNT)

| Metric | Method | Raw count |
|---|---|---|
| Files containing `hermes` (case-insensitive, tracked, excl. `*.lock`) | `git grep -ilI hermes -- . ':(exclude)*.lock'` | **7526 files** |
| Match-lines (case-insensitive) | `git grep -inI hermes -- . ':(exclude)*.lock'` | **54136 lines** |

These totals are dominated by vendored upstream mirrors and tests — see the buckets. **No percentage is
quoted without the raw counts beside it.**

---

## 1. FOUR-BUCKET CLASSIFICATION

Every `hermes` match-line was classified programmatically (`/tmp/hermes-matches.txt`, 54136 lines) by
path category, token shape, and a known-deliberate wire-contract allowlist. Buckets:

### (i) PROSE / UI STRING still saying hermes where it should say Qubit — **FIX CANDIDATES**
- **Raw count (screened):** the residual prose bucket after removing wire-contracts, module names,
  tests, and the upstream docs mirror is **12198 match-lines** across source files. The overwhelming
  majority of those are **code comments and internal identifiers**, not user-visible output. The
  *user-visible* subset is enumerated in §2 and is small (**~40 strings**).
- Representative genuine user-visible leftovers (see §2 for the full list).

### (ii) WIRE CONTRACT / external key — **LEAVE, documented**
- **Raw count:** **3310 match-lines** matched the deliberate allowlist.
- Deliberate set (task brief, verified present): `hermes-gateway*` units, `ai.hermes.gateway*` launchd,
  `hermes-update-<pid>.scope`, `HERMES_HOME`, `HERMES_UPDATE_PAUSED`, `.hermes-update-*`,
  `_HERMES_USER_AGENT`, `HERMES_OVERLAYS`, `HermesCLI = QubitCLI`, `hermes_platform/resolver/app.py`.
- **Additionally discovered and confirmed deliberate** (removing would break an external contract):
  - `requires_hermes` — plugin-manifest field read by `qubit_runtime/qubit_cli/plugin_validate.py:104-108`
    (explicit read-old: `manifest.get("requires_hermes") or manifest.get("hermes")`). **202 catalog files**
    use it (`qubit_runtime/plugin-catalog/*.yaml`, 443 files total).
  - `${HERMES_SKILL_DIR}` / `${HERMES_SESSION_ID}` — the loader regex
    (`qubit_runtime/agent/skill_preprocessing.py:16`) matches these *literal* tokens; skills are
    authored against them.
  - `__HERMES_BASE_PATH__`, `__HERMES_SESSION_TOKEN__`, `__HERMES_PLUGINS__`, `__HERMES_PLUGIN_SDK__`,
    `X-Hermes-Session-Token` / `x-hermes-session-token` — server→browser injection contract, listed in
    `_DEBRAND_PRESERVE_STRINGS` (`qubit_runtime/agent/skill_utils.py:953-960`).
  - `HERMES_QWEN_`, `hermes_flow`, `hermes.tokenReloadAttempted`, `hermes-kanban`, `hermes-flow`,
    `hermes_achievements`, `hermes-achievements`, `qubit-hermes` — same preserve list (migrating them
    would orphan existing user secrets/state).
  - Legacy localStorage keys `hermes-locale`, `hermes-dashboard-theme`, `hermes-dashboard-font`,
    `hermes-sidebar-collapsed` — read-old for existing browser sessions.
  - `HERMES_DESKTOP_*`, `HERMES_DASHBOARD_*`, `HERMES_SERVE_HEADLESS`, `HERMES_BUNDLED_LOCALES`,
    `HERMES_MAX_ITERATIONS` — env bridge vars.
  - IPC channels `hermes:get-remote-display-reason`, deep-link `hermes://plugin/install`,
    `hermes-plugin-pick`, `hermes-skill-pick` — preload↔renderer / OS-handler contract.
  - `hermes.coder` host block — external honcho API key shape.

### (iii) MODULE / PACKAGE / SYMBOL NAME — **planned migration, NOT a gap; COUNT ONLY**
- **Raw count:** **1821 match-lines** match module/symbol patterns (`hermes_cli`, `hermes_platform`,
  `hermes_runtime`, `hermes_state`, `hermes_agent`, `import hermes`, function names like
  `waitForHermesReady`/`resolveDesktopHermesHome`, `_HERMES_BRANDING`, `HermesGateway` type).
- These are the rename target (`qubit_runtime`/`qubit_cli` already exist as siblings). Reported only.

### (iv) TEST asserting a hermes name — **report, do not edit**
- **Raw count:** **19051 match-lines** under `tests/**` + `qubit_runtime/tests/**` (22315 including
  `.test.ts(x)` files). These assert legacy names on purpose (compat contracts).

### False positives (documented so they are not miscounted)
- **183 match-lines** are **React Native's Hermes JS engine** (`skills/ui-ux-pro-max/...`:
  "bundled Hermes", `hermes_enabled`, `react-native ... Hermes`) — an unrelated Meta technology.
  **NOT a rebrand token.**
- **11827 match-lines** live under `qubit_runtime/website/**` — a **vendored UPSTREAM docs mirror**
  (Docusaurus `title: 'Hermes Agent'`, `url: https://hermes-agent.nousresearch.com`,
  `organizationName: NousResearch`); see §3.
- **2376 match-lines** are in `scripts/` tooling (`hermes_vendor_manifest.py`, `rebuild_ledger_hermes.py`,
  etc.) — internal dev tooling, not shipped to users.

---

## 2. THE THREE USER-SHIPPING SURFACES (checked separately)

### (a) CLI `qubit --help` — **CLEAN**
- METHOD: ran `python3 -m voicebridge.cli --help` **and every one of the 115 subcommands' `--help`**,
  grep `-i hermes`.
- RESULT: **0 user-visible `hermes` strings**. `qubit --version` prints `qubit 1.0.0`.
- (`voicebridge.cli:main` is the `qubit` entry point per `pyproject.toml:173`.)

### (b) Dashboard `frontend/qubit-web/src/**/*.tsx` — **CLEAN for user-visible text**
- METHOD: `git grep -inI hermes -- 'frontend/qubit-web/src/**/*.tsx'` → **22 lines** total (173 tsx files).
- Of those, **13 are in `.test.tsx` files** (bucket iv). The **9 non-test** lines are ALL non-visible:
  - `i18n/context.tsx:78` `LEGACY_STORAGE_KEY = "hermes-locale"` — compat
  - `themes/context.tsx:39,47` `LEGACY_STORAGE_KEY/FONT` — compat
  - `pages/EnvPage.tsx:54,56` `HERMES_QWEN_` prefix — wire contract
  - `pages/LogsPage.tsx:97,104,112,186` — code **comments** (developer-only)
  - `frontend/ui.js:191` `hermes: 'Qubit agent'` — this is the **fix** (maps internal token → Qubit label)
- **User-visible `hermes` in the dashboard: 0.**

### (c) Skills' frontmatter / descriptions — **NOT CLEAN**
- METHOD: `git grep -inI hermes -- 'skills/**' 'qubit_runtime/optional-skills/**'`.
- `skills/**` (12 dirs): **7 lines, all React-Native Hermes** → false positive, 0 gaps.
- `qubit_runtime/optional-skills/**`: **579 lines**. Breakdown:
  - **249 lines — stale `~/.hermes/…` paths in skill prose** (user-visible instructions telling the
    agent/user to use the wrong home). Examples: `DESCRIPTION.md:6,12` ("copies to `~/.hermes/skills/`"),
    `blackbox/SKILL.md:93`, `grok/SKILL.md:185,218`, `blockchain/evm/SKILL.md:59,66`, etc.
    **These are genuine prose gaps** (the correct resolver is `$QUBIT_HOME`).
  - **13 lines — `related_skills: [hermes-agent]`** frontmatter (points at the not-yet-renamed skill name;
    tied to the migration, see §4).
  - **27 lines — `hermes-agent` referenced as the repo/product name** in prose
    (`DESCRIPTION.md:5`, `dynamic-workflow/SKILL.md:36-37`, `openhands/SKILL.md:149`).
  - **2 lines — author attribution** `"… + Hermes Agent"` in `first-task` and `initiate-setup` SKILL.md.
  - **288 lines — OTHER**, dominated by the migration scripts
    `migration/{openclaw-migration,qubit-migration}/scripts/openclaw_to_hermes.py` (95+95 lines; a
    migration tool, arguably intentional) and `${HERMES_SKILL_DIR}` (wire contract).

---

## 2b. USER-VISIBLE LEFTOVERS — file:line list

### Desktop i18n (the default-language `en.ts` is the worst offender; keys renamed but VALUES kept)
`qubit_runtime/apps/desktop/src/i18n/en.ts` — **24 `hermes` lines**, of which these are **user-visible
string VALUES** (not keys):
- `en.ts:598` — `… run `hermes debug share --nous` …` (wrong CLI name shown to users)
- `en.ts:1610` — `'Full path to the remote hermes binary. Blank = auto-detect.'`
- `en.ts:2391` — `'Restorable via hermes curator restore.'`
- `en.ts:2972` — placeholder `'@hermes:example.org'`
- `en.ts:3104` — placeholder `'https://hermes.example.com'`
- `en.ts:3705` — `'/quit': 'exit hermes'`
- `en.ts:3734` — `'Write a markdown implementation plan to .hermes/plans/ …'` (wrong path)
- `en.ts:4184` — `'Source (install script) + hermes desktop'`
- `en.ts:4186` — `'Source + hermes desktop'`

### Desktop i18n — **non-English locale packs still say "Hermes Desktop/Agent/Cloud", "Nous Portal/Cloud"**
These are the exact strings the repo's own test flags ("desktop i18n"). Counts / file:
- `zh-hant_settings.ts` (10), `zh-hant_boot.ts` (9), `ar_settings.ts` (5), `ar_boot.ts` (5),
  `zh-hant_chat.ts` (3), `zh.ts` (2), `zh-hant_diagnostics.ts` (2), `zh-hant_chrome.ts` (2),
  `fr.ts` (2), `es.ts` (2), `en.ts` (2), `de.ts` (2), `ar.ts` (2), `ar_diagnostics.ts` (1),
  `ar_command_center.ts` (1), `ar_chat.ts` (1).
- Examples: `zh-hant_boot.ts:5` `'Hermes Desktop 已就緒'`; `zh-hant_chrome.ts:314`
  `` version => `Hermes Desktop v${version}` ``; `ar_boot.ts:87` `appName: 'Hermes Desktop'`;
  `zh-hant_settings.ts:1468-1474` `Nous Portal` ×5.

### CLI user-visible prints (`qubit_runtime/qubit_cli/`)
- `cli_render.py:922` — `get_branding("agent_name", "Hermes Agent")` **fallback default**. On the built-in
  default skin this does NOT fire (skin sets `agent_name="Qubit Agent"` via `_branding("Qubit",…)`,
  `skin_engine.py:60`); it can fire for a user skin that omits `agent_name`. **Judgement call, low risk,
  1-token fix.**
- `cli_tui_mixin.py:1229` — same `"Hermes Agent"` fallback.
- `plugin_catalog.py:529` — `f"Requires Hermes {entry.requires_hermes}."` — shown at plugin install prompts.
- `backup.py:1004` — `print("Note: The hermes-agent codebase was not included in the backup.")`
- `main_dep_hints.py:39` — `"… exemption for the Hermes Desktop …"`
- `cron.py:586` — `restart_command="restart the Hermes Desktop app (…)"`

### Dev-only (NOT user-visible; listed so they are not mistaken for gaps)
- `frontend/qubit-web/vite.config.ts` `[hermes] …` console messages + `` `hermes dashboard` `` hints —
  a **dev-server** log, only printed to developers running Vite.
- `qubit_runtime/apps/desktop/electron/main.ts` `[hermes] …` `console.log/warn` prefixes — developer logs.

---

## 3. CHINESE DOCS

- METHOD (exact, as specified): `git ls-tree -r --name-only HEAD | grep -iE '\.(zh|cn)\.md$|docs/(zh|cn)/'`
- **RESULT: 0 files.** There are **no Chinese markdown docs** and **no `docs/zh/` or `docs/cn/` tree**.
- Broader search — Chinese present only as **i18n UI-string packs**, not docs:
  - **19** `zh`/`zh-hant` i18n files, **~17067 lines total**:
    - `frontend/qubit-web/src/i18n/zh.ts`, `zh-hant.ts`
    - `qubit_runtime/web/src/i18n/{zh,zh-hant}.ts`
    - `qubit_runtime/apps/desktop/src/i18n/{zh,zh-hant,zh-hant_*,intro-zh,intro-zh-hant}.ts`
  - `qubit_runtime/plugin-catalog/hermes-zh.yaml` (a Simplified-Chinese **localization plugin** catalog
    entry; `repo: https://github.com/Cody292/hermes-zh` — external, keep).
- `qubit_runtime/website/README.md` references a `zh-Hans` docs mirror, but **no `website/i18n/` directory
  exists in git** — the mirror is not in the tree.
- **NOTHING DELETED** — this is the owner's call.

---

## 4. SAMPLE-VERIFY: do renamed skills still load? (STEP 4)

METHOD: loaded each skill through the **real loader** (`tools.skills_tool.skill_view`) using the worktree
code + the live skills home (`QUBIT_HOME=/home/trick/.local/state/qubit-voicebridge/runtime`), venv python.
The rename map is `LEGACY_SKILL_NAME_RENAMES` (`qubit_runtime/agent/skill_utils.py:920-930`).

| Skill (renamed / legacy) | Result |
|---|---|
| `qubit-agent-skill-authoring` | **LOAD OK** (`success=True`) |
| `inspecting-qubit-desktop-dom` | **LOAD OK** (`success=True`) |
| `hermes-agent` (legacy name, dir still `hermes-agent`) | **LOAD OK** (`success=True`, resolves as itself) |
| `qubit-agent` (the rename-map TARGET) | **LOAD FAILS**: `Skill 'qubit-agent' not found.` |

**Finding (bug class this task exists to catch):** the rename map declares
`hermes-agent → qubit-agent` and `resolve_legacy_skill_name("hermes-agent") == "qubit-agent"`, but **no
`qubit-agent` skill directory exists** (the only installed dir is `…/autonomous-ai-agents/hermes-agent`).
So the *new* name 404s while the *old* name works — a **rename asymmetry**. The read-old fallback saves
callers that ask for the legacy name, but anything that asks for the new name fails. Same asymmetry
applies to `hermes-agent-skill-authoring` and `inspecting-hermes-desktop-dom` (their `qubit-*` siblings
DO exist and load, but the `hermes-*` dirs were not removed → duplicate skills).

**Loading is NOT broken** for the skills that exist (2/2 renamed siblings load). The defect is the
**half-done rename** (both old and new dirs present for some, only old for `qubit-agent`).

---

## 5. REPO'S OWN GROUND TRUTH — branding test suite

METHOD: `pytest` on 7 branding/rebrand test files (venv python).
**RESULT: 7 failed, 157 passed, 55 skipped, 3 errors.**
Failures (verbatim summary):
- `test_branding_runtime_surfaces8u.py::test_banned_public_phrases_absent_from_every_changed_surface`
  → `assert not failures` — **`'Hermes Desktop'`, `'Hermes Agent'`, `'Hermes Cloud'`, `'Nous Portal'`,
  `'Nous Cloud'` present in "desktop i18n"** (14 hits).
- `test_branding_leaks9u.py::test_debrand_manifest_regenerated_to_canonical_paths`
- `test_branding_leaks9u.py::test_debrand_classification_regenerated`
- `test_cli_branding_leaks9u.py::test_tips_fixed_entries_have_no_upstream_branding`
- `test_cli_branding_leaks9u.py::test_acp_launcher_is_qubit_branded`
- `test_cli_branding_leaks9u.py::test_update_and_tools_prints_are_debranded`
- `test_final_branding.py::test_d4_export_and_sessions_list_are_rebranded`

The repo's own suite therefore **agrees the rebrand is incomplete on the desktop i18n surface and several
CLI print paths.**

---

## 6. WHAT COULD NOT BE VERIFIED (honest statement)

- **Runtime rendering of the desktop app / dashboard**: I verified strings in source and via the loader,
  but did NOT launch the Electron desktop app or a browser to confirm which of the flagged strings
  actually render to a user in a given locale. Some (e.g. `@hermes:example.org` placeholder) are cosmetic.
- **Full subcommand help matrix beyond `--help`**: I ran every subcommand's `--help` (115/115) but did NOT
  run each command's actual execution output (e.g. `qubit update`, `qubit doctor` runtime prints), which
  the failing tests suggest still contain upstream branding.
- **The 12198-line residual "prose" bucket** was screened by path/token heuristics, not read line-by-line;
  the *user-visible* subset is enumerated in §2b, but a small number of additional comment-only hits may
  remain uncounted as visible.
- **The upstream docs mirror** (`qubit_runtime/website`, 11827 lines) was classified as vendored upstream,
  not a product surface — I did not confirm whether the owner intends to rebrand or delete it.
- **`qubit_runtime/skills/*/hermes-agent*` vs live `qubit-*` siblings**: the worktree tree and the live
  installed home are out of sync; I verified loading against the **live** home, not a fresh install.

---

## 7. BOTTOM LINE

| Surface | User-visible `hermes`? |
|---|---|
| CLI `--help` (115 subcommands) | **No — clean** |
| Dashboard `frontend/qubit-web` | **No — clean** |
| Desktop app `en.ts` string values | **Yes — 9 strings** |
| Desktop non-EN i18n packs | **Yes — "Hermes Desktop/Agent/Cloud", "Nous Portal/Cloud", 14 files** |
| Bundled-skill prose | **Yes — 249 stale `~/.hermes/…` paths + 27 product-name refs + 2 attributions** |
| CLI runtime prints (`backup`, `cron`, `plugin_catalog`, dep hints) | **Yes — 4–5 strings** |
| Repo's own branding tests | **7 FAILING** |

**Only unambiguous fix applied:** none required a code fix in this pass; `frontend/ui.js:191` and
`DocsPage` compat-group handling are already the *correct* direction. This doc records the gaps for the
owner to fix (or to authorise).
