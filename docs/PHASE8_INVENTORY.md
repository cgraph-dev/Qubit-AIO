# PHASE 8 — vendored-internals inventory (owner decision)

**Status:** inventory only. **No product code changed, no deletions, no renames, no service
restart, no full matrix, no push.** This document does not claim `VERIFIED`, parity, or completion.

- Worktree: `/tmp/opencode/wt-p8`
- Branch: `feat/p8`
- Base / measured commit: `6bc665780` (`docs: record the monitor_operative recipe in the handoff ledger`)
- Inventory date: 2026-10-27
- Commands run with `nice -n 10`; all searches are over `git`-tracked files only.

This is a decision aid for the owner: each group has the exact command, the measured count, a
representative `file:line`, a classification, and the risk if the surface is changed. **Nothing
here recommends a blanket rename or deletion.** Where a row says "keep", it means the surface is
load-bearing until a writer+reader+external-contract migration is proven; where it says "verify",
the evidence is source-level only and a live/built check is still missing.

## 0. Classification vocabulary used below

| Label | Meaning |
|---|---|
| **internal contract** | Name crosses a process/socket/on-disk boundary; renaming one side breaks the other silently. |
| **read-old shim / adapter** | Deliberately accepts a legacy name so an existing install, external bundle, or upstream-synced call site keeps working. |
| **migration surface** | The old name is read *in order to retire/migrate it* (update recovery, product migration). |
| **security boundary** | The name carries a token, cookie, or auth-mode decision. A half-rename is an auth outage. |
| **attribution / legal** | Required copyright/license/provenance text; the documented exception to zero-legacy-brand. |
| **test / evidence fixture** | Appears only in tests, frozen JSON, or captured artifacts; not a shipped runtime surface. |
| **stale prose** | Comment/docstring/example that names the old home; not functional, but misleading. |
| **false positive** | Not an env var or path at all (CSS class, React Native Hermes engine, a filename that looks like an env name). |

## 1. Headline counts

| Surface | Measure | All tracked | Code surface¹ | Production² |
|---|---|---:|---:|---:|
| `HERMES_*` name tokens | distinct | **850** | **810** | **688** |
| `HERMES_*` name tokens | occurrences | **12,150** | **9,895** | **3,697** |
| `.hermes` token | occurrences / files | **8,166 / 2,305** | — | — |
| `~/.hermes` | occurrences / files | **2,917 / 860** | — | **819** |
| `/root/.hermes` | occurrences / files | **164 / 47** | — | **164 / 47** |
| Plugin-SDK legacy globals (`__HERMES_PLUGIN_SDK__` + `__HERMES_PLUGINS__`) | occurrences | **91** | — | — |
| Attribution markers (`NousResearch` + `nousresearch.com` + `Nous Research`) | occurrences | **1,901** | — | — |
| Brand writer/reader guard | `check_brand_half_rename.py` | **OK (46 globals consistent)** | — | — |

¹ Code surface = `*.py *.ts *.tsx *.js *.mjs *.cjs *.sh *.bash *.zsh *.yaml *.yml *.toml *.service *.env .env.example` (markdown/website excluded).
² Production = code surface minus `**/tests/**`, `tests/**`, `**/tests-js/**`, `test_*.py`, `*.test.ts(x)`, `__tests__/**`, and `qubit_runtime/website/**`.

Two counting traps are recorded so nobody reuses a raw number incorrectly:

1. **Filename tokens look like env names.** `HERMES_FRONTEND_MAP`, `HERMES_SOURCE_COMPARISON`,
   `HERMES_BYTE_MANIFEST`, `HERMES_OPEN_CLOSURE` are JSON/report filenames, not env vars. They
   account for **45 of the 9,895** code-surface matches and **21 of the 3,697** production matches.
   The env-only counts are therefore **808 distinct / 9,850** (all code) and **688 / 3,676**
   (production); the difference is small and does not change any decision.
2. **`.hermes` is not always a path.** The raw token also matches CSS classes
   (`.hermes-radio-*`), identifiers, and marker prefixes. The decision-relevant path-shaped
   measures are `.hermes/` (**2,999**), `~/.hermes` (**2,917**), `$HOME/.hermes` (**87**),
   `/root/.hermes` (**164**), `.hermes\\` (**29**), and `.hermes_history` (**45**).

## 2. `HERMES_*` environment variables

### 2.1 Commands and counts

```bash
# All tracked text (includes markdown/docs):
git grep -ohE '\bHERMES_[A-Z0-9_]+' | sort -u | wc -l          # 850
git grep -ohE '\bHERMES_[A-Z0-9_]+' | wc -l                     # 12150

# Code surface:
git grep -ohE '\bHERMES_[A-Z0-9_]+' -- '*.py' '*.ts' '*.tsx' '*.js' '*.mjs' '*.cjs' \
  '*.sh' '*.bash' '*.zsh' '*.yaml' '*.yml' '*.toml' '*.service' '*.env' '.env.example' \
  | sort -u | wc -l                                             # 810
# same pipeline without `sort -u |`                               # 9895

# Production (same globs, exclusions):
git grep -ohE '\bHERMES_[A-Z0-9_]+' -- '*.py' '*.ts' '*.tsx' '*.js' '*.mjs' '*.cjs' \
  '*.sh' '*.bash' '*.zsh' '*.yaml' '*.yml' '*.toml' '*.service' '*.env' '.env.example' \
  ':!**/tests/**' ':!tests/**' ':!**/tests-js/**' ':!**/test_*.py' ':!**/*.test.ts' \
  ':!**/*.test.tsx' ':!**/__tests__/**' ':!qubit_runtime/website/**' | sort -u | wc -l  # 688
# same pipeline without `sort -u |`                               # 3697

# Names that appear only in tests (not in the production code surface):
# (code-surface distinct set) minus (production distinct set)     # 116
```

### 2.2 Readers vs writers

Method: each matching **line** is classified by regex as a write (direct `os.environ[...] =`,
`setdefault`/`putenv`, `export`/`VAR=` shell assignment, `env.update(...)`, `Environment=`,
`process.env.X =`), a read (`.get`/`getenv`/`os.environ[...]`/`process.env`/`get_session_env`/
`env_*()`/`$HERMES_*`), both, a comment, or "other" (literal name in a dict/docstring/fixture).
This is **line-level, not dataflow**: a name can be written on one line and read on another, and
"other" includes names passed as string literals to helpers the regex does not know.

| Measure (production, clean filter) | Count |
|---|---:|
| Matching lines | **3,295** |
| Read-only lines | **1,004** |
| Write-only lines | **229** |
| Both read and write on one line | **12** |
| **Read-bearing lines** | **1,016** |
| **Write-bearing lines** | **241** |
| Other (literal/docstring/fixture) | **1,777** |
| Comment lines | **273** |
| Distinct names any / read / write | **688 / 424 / 142** |
| Names read but never written in-tree | **304** |
| Names written but never read in-tree | **22** |
| Names both read and written in-tree | **120** |

The **304 read-only names** are the strongest evidence that the env contract is set by an external
parent (shell, systemd, desktop, gateway parent, test harness): the reader exists here, the writer
does not. The 22 write-only names are not "dead" by that measurement alone — a child process or an
external consumer may read them; see row D21.

### 2.3 Grouped by subsystem (production occurrences)

```bash
git grep -cE '\bHERMES_[A-Z0-9_]+' -- <production globs + exclusions> \
| awk -F: '{split($1,a,"/"); k=a[1]; if(a[1]=="qubit_runtime" && a[2]!="") k="qubit_runtime/"a[2];
            s[k]+=$2; f[k]++} END{for(k in s) printf "%-38s occ=%-6d files=%d\n",k,s[k],f[k]}'
```

| Subsystem | Occurrences | Files | Notable role |
|---|---:|---:|---|
| `qubit_runtime/qubit_cli` | 735 | 141 | home/update/profile/env plumbing; session/token/payload wiring |
| `qubit_runtime/apps` | 408 | 95 | desktop/electron, bootstrap installer, SPA/runtime env |
| `qubit_runtime/tools` | 365 | 70 | terminal sandboxes, kanban, credentials, MCP |
| `qubit_runtime/scripts` | 297 | 55 | build/desktop-update/termux/dev scripts |
| `qubit_runtime/agent` | 267 | 71 | session id, kanban delegation, model/timezone, redaction |
| `qubit_runtime/ui-tui` | 189 | 34 | TUI launch env, theme, background |
| `qubit_runtime/gateway` | 183 | 50 | gateway session, lock dir, profile, container mounts |
| `qubit_runtime/.github` | 158 | 18 | CI/e2e env (fixtures) |
| `qubit_runtime/plugins` | 146 | 34 | bundled plugin env |
| `qubit_runtime/tui_gateway` | 111 | 26 | TUI gateway URL/resume/theme |
| `scripts` | 105 | 20 | audits, runners, sync |
| `qubit_runtime/evals` | 55 | 43 | eval fixtures/conformance |
| `qubit_runtime/cron` | 54 | 8 | cron session/mode |
| `qubit_runtime/docker` | 42 | 5 | UID/GID/home in containers |
| `voicebridge` | 27 | 12 | bridge/PTY/QUBIT_HOME fallback |
| `qubit_runtime/qubit_constants.py` | 20 | 1 | `HERMES_HOME` resolver + aliases |
| `qubit_runtime/web` | 15 | 7 | vendored SPA build env |

Tests are the largest single consumer in the *unfiltered* code surface
(`qubit_runtime/tests` 4,941 occurrences / 1,157 files), which is why test fixtures must not be
used as evidence that a name is still a live product contract.

### 2.4 Top production names by occurrence (cached-env-like, filename tokens excluded)

`HERMES_PAYLOAD_TAG` 86 · `HERMES_KANBAN_TASK` 77 · `HERMES_BUILD_COMMIT` 64 · `HERMES_PYTHON` 58 ·
`HERMES_SESSION_ID` 50 · `HERMES_HOME` 47 · `HERMES_DESKTOP_VARIANT` 41 ·
`HERMES_DESKTOP_USER_DATA_DIR` 37 · `HERMES_RUNTIME_DIR` 35 · `HERMES_MAX_ITERATIONS` 35 ·
`HERMES_TUI_BACKGROUND` 35 · `HERMES_SESSION_PLATFORM` 34 · `HERMES_KANBAN_BOARD` 32 ·
`HERMES_TUI_THEME` 32 · `HERMES_DASHBOARD_SESSION_TOKEN` 31 · `HERMES_INTERACTIVE` 30 ·
`HERMES_KANBAN_DB` 30 · `HERMES_DESKTOP` 29 · `HERMES_UID` 28 · `HERMES_PROFILE` 27.

Top **read** names (line-level, production): `HERMES_PAYLOAD_TAG` (47), `HERMES_KANBAN_TASK` (47),
`HERMES_BUILD_COMMIT` (28), `HERMES_SESSION_ID` (20), `HERMES_SESSION_PLATFORM` (19),
`HERMES_PYTHON` (16), `HERMES_DESKTOP_USER_DATA_DIR` (15), `HERMES_TUI_THEME` (15).

Top **write** names (line-level, production): `HERMES_PYTHON` (7), `HERMES_INTERACTIVE` (7),
`HERMES_RUNTIME_DIR` (7), `HERMES_TUI_BACKGROUND` (7), `HERMES_YOLO_MODE` (6),
`HERMES_PAYLOAD_TAG` (6), `HERMES_REDACT_SECRETS` (5), `HERMES_DESKTOP_VARIANT` (5),
`HERMES_CWD` (5), `HERMES_SESSION_ID` (4).

### 2.5 Representative writer / reader pairs

| Name | Writer | Reader |
|---|---|---|
| `HERMES_HOME` | `qubit_runtime/qubit_cli/gateway_runtime_start.py:55` — `env.update(QUBIT_HOME=str(home), HERMES_HOME=str(home), …)` | `qubit_runtime/qubit_cli/update_lock.py:100` — `Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")` |
| `HERMES_SESSION_ID` | `qubit_runtime/agent/agent_init.py:1181` — `os.environ["HERMES_SESSION_ID"] = session_id` | `qubit_runtime/gateway/media_fetch.py:75` — `get_session_env("HERMES_SESSION_ID") or …` |
| `HERMES_KANBAN_TASK` | (set by kanban dispatcher/child; see `delegation_context.py`) | `qubit_runtime/agent/activity_tracking.py:74` — `if os.environ.get("HERMES_KANBAN_TASK")` |
| `HERMES_PYTHON` | `qubit_runtime/qubit_cli/main_tui_launch.py:372` — `env["HERMES_PYTHON"] = sys.executable` | `qubit_runtime/qubit_cli/main_tui_launch.py:363` — `env.get("HERMES_PYTHON")` |
| `HERMES_DASHBOARD_SESSION_TOKEN` | `qubit_runtime/apps/desktop/electron/main.ts:11705` | `qubit_runtime/apps/desktop/electron/dashboard-token.ts:4` + spawn credential |
| `.env.example` contract | `qubit_runtime/.env.example:128,229,445-447` (5 commented vars) | `qubit_runtime/docker-compose*.yml` (17 uses: UID/GID/host) |

### 2.6 Interpretation

- The env surface is **an internal contract, not branding**. Every top name has a writer and/or a
  reader on the other side of a process boundary. A rename must move writer, reader, subprocess
  environment, systemd/desktop launch, and tests **in one change**.
- The 304 read-only names are the ones most likely to be set by an external parent. They are the
  worst candidates for a "rename the reader" cleanup.
- The 22 write-only names are candidates for **verification**, not deletion: confirm no child,
  plugin, or external consumer reads them before removing a writer.
- `qubit_runtime/.github` (158) and tests are fixtures; they inflate the count but are useful as
  migration checklists, not as product evidence.

## 3. `.hermes` / `~/.hermes` paths

### 3.1 Commands and counts

```bash
# Raw token (includes CSS classes/identifiers):
git grep -oF '.hermes' | wc -l                                  # 8166
git grep -lF '.hermes' | wc -l                                  # 2305

# Path-shaped measures:
git grep -oF '~/.hermes' | wc -l                                # 2917
git grep -lF '~/.hermes' | wc -l                                # 860
git grep -oF '$HOME/.hermes' | wc -l                            # 87
git grep -oF '/root/.hermes' | wc -l                            # 164
git grep -lF '/root/.hermes' | wc -l                            # 47
git grep -oF '.hermes/' | wc -l                                 # 2999
git grep -oF '.hermes\' | wc -l                                 # 29
git grep -oF '.hermes_history' | wc -l                          # 45
```

`~/.hermes` split (this one sums exactly):

```bash
git grep -oF '~/.hermes' -- 'qubit_runtime/tests/**' 'tests/**' '**/*.test.ts' \
  '**/*.test.tsx' '**/__tests__/**' | wc -l                     # 485  (tests)
git grep -oF '~/.hermes' -- 'qubit_runtime/website/**' | wc -l   # 1558 (vendored upstream docs)
git grep -oF '~/.hermes' -- 'docs/**' | wc -l                    #   55 (Qubit docs)
git grep -oF '~/.hermes' -- ':!**/tests/**' ':!tests/**' ':!**/*.test.ts' \
  ':!**/*.test.tsx' ':!**/__tests__/**' ':!qubit_runtime/website/**' ':!docs/**' | wc -l
                                                                 #  819 (production)
```

Path-like `.hermes/` in production, by directory:

```bash
git grep -oF '.hermes/' -- <production exclusions> \
| awk -F: '{split($1,a,"/"); k=a[1]; if(a[1]=="qubit_runtime" && a[2]!="") k="qubit_runtime/"a[2]; print k}' \
| sort | uniq -c | sort -rn
# qubit_runtime/qubit_cli 155 · qubit_runtime/web 73 · qubit_runtime/tools 73
# qubit_runtime/plugins 60 · qubit_runtime/apps 56 · qubit_runtime/agent 55
# qubit_runtime/evals 24 · scripts 21 · qubit_runtime/gateway 11 …
# 694 path-shaped occurrences in production (non-test, non-website, non-docs)
```

### 3.2 Classification by category

| Category | Representative `file:line` | Count / evidence | Classification | Risk |
|---|---|---|---|---|
| Home resolver fallback | `qubit_runtime/qubit_constants.py:54-62` (`_QUBIT_HOME_DIR = ".qubit"`, `_LEGACY_HOME_DIR = ".hermes"`), `:137` | `_LEGACY_HOME_DIR` 19 occ / 8 files | read-old shim | **High** — renaming orphans every pre-rename install's state/secrets at once. |
| Home resolver implementation | `qubit_runtime/qubit_cli/gateway.py:3105-3117,3284-3286` | uses both dir names | read-old shim | High — same as above. |
| Update/home plumbing | `qubit_runtime/qubit_cli/update_lock.py:100`; `qubit_runtime/qubit_cli/update_host_obligation.py:5,44` | `~/.hermes` production 819 | internal contract / migration surface | High — update marker root must match what the old build and desktop hand-off read. |
| Desktop / remote SSH home | `qubit_runtime/apps/desktop/electron/remote-lifecycle.ts:40,226,285` (`REMOTE_LOCK_DIR = '~/.hermes/desktop-ssh'`, fallback `~/.hermes/hermes-agent/venv/bin/hermes`, `${QUBIT_HOME:-$HOME/.hermes}`) | 26-33 refs in that file | internal contract | **High** — a remote host with an existing `~/.hermes` install is reattached by this path; changing one side strands the remote session. |
| Bootstrap installer | `qubit_runtime/apps/bootstrap-installer/src-tauri/src/paths.rs:40,205` (`home.join(".hermes")`, `get_hermes_home()`) | 23 refs in `update.rs`; 11 in `bootstrap.rs` | read-old shim / adapter | **High** — the installer and the Python resolver must agree on the same home. |
| Container bind target | `qubit_runtime/agent/file_safety.py:521-522`; `qubit_runtime/agent/terminal_env_provider.py:36`; `qubit_runtime/tools/credential_files.py:66,346-374`; `qubit_runtime/gateway/platforms/base.py:1083` | `/root/.hermes` 164 occ / 47 files | internal contract | **High** — host-side name and image-side mount path must agree or path translation breaks for every sandboxed command. |
| Remote sync backends | `qubit_runtime/tools/credential_files.py:346-374` (`ssh`, `daytona`, `vercel_sandbox` use `~/.hermes`) | 73 refs in `tools` | internal contract | Medium-high — bytes are synced under the remote user's `~/.hermes`; the remote shell expands it. |
| Legacy agent-home adapter | `voicebridge/agent_compat/adapters/hermes.py:55,97-103` (`content_roots=("~/.hermes/plugins", …)`, `skill_roots=("~/.hermes/skills",)`, `mcp_config_paths=("~/.hermes/config.yaml",)`) | 81 refs in `voicebridge` (many are comments) | adapter | High — this adapter exists specifically to read a pre-existing Hermes home. |
| Product migration | `voicebridge/qubit_migration.py:88` (`Path.home() / ".hermes"`), `:58,68,142,1798-1813` (`~/.hermes`, `.hermes_history`) | 14 refs | migration surface | High — read-only source; renaming the source means migration silently finds nothing. |
| Update markers / state | `.hermes-update-in-progress`, `.hermes-update-result`, `.hermes-update-old`, `.hermes-update-staging`, `.hermes-bootstrap-complete`, `hermes-update.lock` | see §5/D6 counts | internal contract / migration surface | High — an in-flight update written by the previous build must be recognised. |
| Legacy data filenames | `hermes_config_overrides.json` (5/5), `hermes_persona.json` (4/4), `.hermes_history` (45) | `voicebridge/qubit_compat.py:14911`; `voicebridge/qubit_migration.py:1798-1813` | read-old shim | Medium-high — existing installs read them; a new canonical name needs dual-read. |
| Vendored upstream docs | `qubit_runtime/website/**` | `~/.hermes` 1,558; `.hermes` token 1,765 (7483 `hermes` lines / 429 files) | vendored docs / attribution-adjacent | Low product risk; owner decision (row D16). |
| Qubit docs / reports | `docs/**` | `~/.hermes` 55; `INDEPENDENCE_VERDICT.md` 64 `~/.hermes` / 72 `.hermes` | evidence/report | Low; keep or archive by owner call. |
| Internal docstrings/comments | `qubit_runtime/agent/curator.py:85`, `qubit_runtime/agent/anthropic_credentials.py:6,738,819`, `qubit_runtime/tools/approval_detection.py:24,415,594` | part of the 819 production `~/.hermes` | stale prose | Low functional risk; misleading to maintainers; do not mass-rewrite (some describe the real legacy fallback). |
| Bundled optional-skill scripts/prose | `qubit_runtime/optional-skills/migration/*/scripts/openclaw_to_hermes.py`, `security/*/scripts/*.py` | 10 `.hermes` occurrences in 7 files | migration utility / stale prose | Low; the migration scripts intentionally read old sources. |

### 3.3 What this means

- The 2,917 `~/.hermes` occurrences are not one surface. **485 are tests, 1,558 are vendored
  upstream docs, 55 are Qubit docs, and 819 are production.** Only the production subset is a
  runtime decision, and within it the high-risk functional paths are concentrated in home
  resolution, update, desktop/remote/installer, container bind, and the legacy-agent adapter.
- The raw `.hermes` token count is **not** a path count: `qubit_runtime/apps` has ~812 raw
  `.hermes` matches (non-test/non-spec) but only **56** `.hermes/` paths; the rest are identifiers
  and 127 `.hermes-` CSS/class tokens (`.hermes-radio-*`).
- The correct review question is not "how many `.hermes` strings remain" but "which of the 819
  production `~/.hermes` references are read by a process we do not control?" The rows above name
  those.

## 4. `/root/.hermes` container bind paths

```bash
git grep -nF '/root/.hermes' | wc -l        # 164 occurrences
git grep -lF '/root/.hermes' | wc -l        # 47 files
git grep -nF '/root/.hermes' -- ':!**/tests/**' ':!qubit_runtime/website/**' | head -40
```

| Representative `file:line` | Role |
|---|---|
| `qubit_runtime/agent/file_safety.py:521-522` | `classify_container_mirror_target` — the bind mount strips `sandboxes/`, so the agent sees `/root/.hermes/…` |
| `qubit_runtime/agent/terminal_env_provider.py:36` | `cache_path_base` for container backends (`"~/.hermes"` / `"/root/.hermes"`) |
| `qubit_runtime/tools/credential_files.py:66,109,147,200,237,302,315,338,358,389` | default `container_base="/root/.hermes"` for credential/cache/skill mounts |
| `qubit_runtime/gateway/platforms/base.py:1083,1111,1125-1127` | auto-mounted cache dirs and the `/root` vs `/root/.hermes` containment check |
| `qubit_runtime/tools/environments/file_sync.py:132-139` | remaps `/root/.hermes` to the backend's `container_base` |
| `qubit_runtime/qubit_cli/gateway.py:589,3403`; `qubit_runtime/gateway/status.py:742-743` | compares a systemd `QUBIT_HOME=/root/.hermes/` spelling to the resolved home |
| `qubit_runtime/qubit_cli/main.py:643` | comment: systemd hardcodes `QUBIT_HOME=/root/.hermes` |

**Classification:** internal contract (host↔container path translation), not brand prose.
**Risk:** high. Changing the string on one side only makes every sandboxed file/credential/cache
path dangle; tests in `qubit_runtime/tests/tools/test_docker_environment.py` and
`tests/tools/test_docker_environment.py` pin the pairing. The owner should treat this row as
"keep unless the image mount path and every host-side mapper change together, with a container
test".

## 5. Plugin-SDK and window-global compatibility

### 5.1 Commands and counts

```bash
for n in __HERMES_PLUGIN_SDK__ __HERMES_PLUGINS__ __QUBIT_PLUGIN_SDK__ __QUBIT_PLUGINS__ \
         __QUBIT_PLUGIN_SDK_LEGACY__ __QUBIT_PLUGINS_LEGACY__; do
  printf '%-30s occ=%-5s files=%s\n' "$n" "$(git grep -oF "$n" | wc -l)" "$(git grep -lF "$n" | wc -l)"
done
# __HERMES_PLUGIN_SDK__        48 / 22
# __HERMES_PLUGINS__           43 / 23
# __QUBIT_PLUGIN_SDK__         73 / 39
# __QUBIT_PLUGINS__            87 / 40
# __QUBIT_PLUGIN_SDK_LEGACY__   8 / 4
# __QUBIT_PLUGINS_LEGACY__      8 / 4
```

### 5.2 Host writers (who exposes what)

| Host source | Canonical `__QUBIT_*` | Legacy `__HERMES_*` | Evidence |
|---|---:|---:|---|
| Live dashboard SPA (`frontend/qubit-web`) | **yes** | **yes (alias)** | `frontend/qubit-web/src/plugins/registry.ts:194-212` — canonical first, then explicit legacy aliases; comment names `agent-analytics` as the unrebuildable reader. |
| Vendored SPA (`qubit_runtime/web`, builds `qubit_cli/web_dist` for `qubit serve`/desktop) | **no** | **yes** | `qubit_runtime/web/src/plugins/registry.ts:113-118` writes only `__HERMES_PLUGINS__` / `__HERMES_PLUGIN_SDK__`; `git grep -nE '__QUBIT_PLUGIN_SDK__|__QUBIT_PLUGINS__' -- qubit_runtime/web` → 0 files. |
| Desktop runtime loader | no | yes | `qubit_runtime/apps/desktop/src/sdk/runtime.ts:28,63` installs `__HERMES_PLUGIN_SDK__` / `__HERMES_REACT__` / JSX globals; `sdkImportMap()` maps `@qubit/plugin-sdk` to that global. |

### 5.3 In-tree bundle readers (who consumes what)

All four checked-in bundles read **canonical only**:

```bash
git grep -nE '__HERMES_PLUGIN_SDK__|__QUBIT_PLUGIN_SDK__' -- '**/dist/index.js'
```

| Bundle | Read |
|---|---|
| `qubit_runtime/plugins/kanban/dashboard/dist/index.js:8,15` | `window.__QUBIT_PLUGIN_SDK__` |
| `qubit_runtime/plugins/qubit-achievements/dashboard/dist/index.js:8` | `window.__QUBIT_PLUGIN_SDK__` |
| `frontend/qubit-web/public/plugins/kanban/dist/index.js:10,17` | `window.__QUBIT_PLUGIN_SDK__` |
| `frontend/qubit-web/public/plugins/qubit-achievements/dist/index.js:8` | `window.__QUBIT_PLUGIN_SDK__` |

**Finding (source-level, needs verification):** the vendored SPA host in `qubit_runtime/web`
exposes only the legacy globals, while the in-tree bundles shipped beside it read only the
canonical globals. On a `qubit serve`/desktop build from `qubit_runtime/web`, those bundles would
evaluate `SDK === undefined` and bail (`if (!SDK || !window.__QUBIT_PLUGINS__) return;`). The live
dashboard is not affected because `frontend/qubit-web` exposes both. `qubit_runtime/qubit_cli/web_dist`
and `frontend/qubit-dist` are gitignored/absent in this worktree, so this was **not** verified
against a built bundle or a running `qubit serve`; it is a source-level mismatch, not a claimed
live outage. This is the highest-value item to verify before any plugin-global cleanup.

### 5.4 Session/auth window globals (security boundary)

| Global | Legacy count | Canonical count | Current writer/reader |
|---|---:|---:|---|
| `__HERMES_SESSION_TOKEN__` vs `__QUBIT_SESSION_TOKEN__` | 30 / 15 files | 204 / 87 files | `qubit_runtime/qubit_cli/web_server_dashboard.py:140,173` now writes **`__QUBIT_SESSION_TOKEN__`**; `frontend/qubit-web/src/lib/api.ts:477,613,639` and `frontend/qubit-web/src/lib/events-sse.ts:129` read it. The legacy name survives in docs/evidence and one captured chunk (`docs/e2e/evidence-api-chunk-served.js:2`). |
| `__HERMES_AUTH_REQUIRED__` vs `__QUBIT_AUTH_REQUIRED__` | 10 / 6 files | 37 / 21 files | `qubit_runtime/qubit_cli/web_server_dashboard.py:141,187` writes QUBIT; `frontend/qubit-web/src/lib/api.ts:229,284`, `AuthWidget.tsx`, `dashboard-auth-reload.ts` read QUBIT. |
| `__HERMES_BASE_PATH__` vs `__QUBIT_BASE_PATH__` | 7 / 7 files | 52 / 27 files | `qubit_runtime/qubit_cli/web_server_dashboard.py:186` writes QUBIT; SPA reads QUBIT. |

**Classification:** security boundary. **Guard:** `python3 scripts/check_brand_half_rename.py` →
`OK (46 global name(s) consistent)` (exit 0). The old incident is not present at this commit; the
legacy token occurrences are historical docs/evidence, not a live writer. Any future change must
keep the writer/reader pair and the gated/no-token condition at
`qubit_runtime/qubit_cli/web_server_dashboard.py:133-143` intact.

### 5.5 Legacy plugin aliases kept on purpose

`frontend/qubit-web/src/plugins/registry.ts:208-212` also publishes
`__QUBIT_PLUGIN_SDK_LEGACY__` / `__QUBIT_PLUGINS_LEGACY__` (8/4 each). The contract test
`frontend/qubit-web/src/plugins/plugin-globals.test.ts:30-33,80-104` asserts the host exposes
whatever an installed bundle reads; it deliberately does **not** assert bundles are clean. This is
accept-both, not a leftover.

## 6. Upstream attribution / legal exceptions

### 6.1 Commands and counts

```bash
ls -l LICENSE THIRD_PARTY_NOTICES.md qubit_runtime/LICENSE
grep -c '^## ' THIRD_PARTY_NOTICES.md                    # 22 attribution records
git grep -oF 'NousResearch' | wc -l                       # 926
git grep -lF 'NousResearch' | wc -l                       # 403
git grep -oF 'nousresearch.com' | wc -l                   # 703
git grep -lF 'nousresearch.com' | wc -l                   # 298
git grep -oF 'Nous Research' | wc -l                      # 272
git grep -lF 'Nous Research' | wc -l                      # 144
git grep -oF 'Copyright (c)' | wc -l                      # 101
git grep -lF 'Copyright (c)' | wc -l                      # 62
git grep -oF 'Apache-2.0' | wc -l                         # 313
git grep -lF 'Apache-2.0' | wc -l                         # 79
git grep -oF 'Hermes 3 & 4' | wc -l                       # 8 (6 locale files: real model name)
git grep -nF '@nous-research/ui' -- '**/package.json'     # 4 deps: frontend/qubit-web, desktop, web, bootstrap-installer
```

### 6.2 What is load-bearing

| Artifact | Evidence | Classification |
|---|---|---|
| Root `LICENSE` | `LICENSE:1-9` — Qubit original material only; explicitly excludes third-party/upstream, points to `THIRD_PARTY_NOTICES.md` and `docs/UPSTREAM_REUSE.md` | legal scope |
| `qubit_runtime/LICENSE` | `qubit_runtime/LICENSE:1-4` — MIT, Copyright © 2025 Nous Research | **required attribution** |
| `THIRD_PARTY_NOTICES.md` | 22 records, including retired Hermes vendoring (`:502`), Hermes dashboard vendored baseline (`:540`), OpenJarvis Apache-2.0 records (`:632-730`) | **required attribution** |
| Upstream URLs / repo names | `NousResearch` 926 occ / 403 files; `nousresearch.com` 703 / 298; `github.com/NousResearch` 545 | attribution / provenance |
| Real model-name prose | `qubit_runtime/locales/{de,en,ga,ko}.yaml:3101/3201` — "Nous Research Hermes 3 & 4 models are NOT agentic"; 8 occurrences | **not a bug** — this is the model family's real name (see `docs/BRAND.md:42,46`) |
| `@nous-research/ui` npm dependency | `frontend/qubit-web/package.json:20`, `qubit_runtime/apps/desktop/package.json:99`, `qubit_runtime/web/package.json:19`, `qubit_runtime/apps/bootstrap-installer/package.json:22` | third-party dependency name |
| Retired vendoring record | `THIRD_PARTY_NOTICES.md:502-538` — two files removed, MIT record retained in `voicebridge/upstream/ATTRIBUTION.md` | **required attribution** |

**Classification:** attribution/legal. **Risk:** removing or rewriting any of these is a license
defect, not a rebrand. `docs/BRAND.md` §2 names this the required exception. No cleanup should
touch these strings without a legal review.

## 7. Declared legacy-but-load-bearing anchors

| Anchor | Count | Evidence | Why it cannot be renamed unilaterally |
|---|---:|---|---|
| `_LEGACY_HOME_DIR = ".hermes"` | 19 occ / 8 files | `qubit_runtime/qubit_constants.py:55`, `:61-62,137`; `qubit_runtime/qubit_cli/gateway.py:3105-3117,3284-3286` | read-old home fallback; renaming strands existing installs. |
| `get_hermes_home` alias family (`get_hermes_home`, `get_hermes_home_override`, `set/reset_hermes_home_override`, `hermes_home_key`) | 108 occ / 42 files | `qubit_runtime/qubit_constants.py:247-258` | upstream-synced call sites import by that name; deleting the alias is an `ImportError`. |
| `~/.hermes.pre-retirement` | 34 occ / 14 files | `scripts/audit_capability_gaps.py:85`; `scripts/audit_missing_symbols.py:23`; `scripts/audit_sync_completeness.py:116`; `scripts/sync_upstream.py:45` | path to the retired upstream tree every audit measures against. |
| Frozen pin `43faacfb8de7b29548547a7fbdedad0cc6e7391c` | 1 declaration | `scripts/frozen_target_progress.py:71` | identifies the upstream commit; rewriting breaks the audit trail. |
| `requires_hermes` (read-old) | 295 occ / 246 files; **203 catalog files** | `qubit_runtime/qubit_cli/plugin_validate.py:104-108` accepts `requires_qubit` / `qubit` first, then `requires_hermes` / `hermes` | external plugin manifests in the wild use the old field. |
| `${HERMES_SKILL_DIR}` / `${HERMES_SESSION_ID}` | 37 / 12 occurrences | `qubit_runtime/agent/skill_preprocessing.py:16` regex matches the literal tokens | skills authored in the wild use these tokens; renaming the loader breaks them. |
| systemd legacy unit-name strings | 19 distinct names in-tree | `qubit_runtime/qubit_cli/update_restart_recovery.py:147`; `qubit_runtime/qubit_cli/update_cmd_fleet.py:718,748-749`; fixtures under `qubit_runtime/tests/**` | installed `systemd/` files are all `qubit-*`; the old names are migration/recovery and fixtures. See D12 for a suspected half-rebrand. |
| NixOS module option names | `services.hermes-agent`, `programs.hermes-agent` | `qubit_runtime/nix/checks.nix:40,57,80-90`; `homeManagerModules.nix`, `nixosModules.nix` | existing NixOS user configs name the option; a rename is a config migration. |
| `HERMES_HOME_MODE` | 25 occ / 12 files | `qubit_runtime/qubit_constants.py:999,1015,1038` | container home permission mode env; read by the entrypoint/host chmod. |
| `_hermes-python` helper dir | 6 occ / 4 files | bundled helper path | read by spawned subprocesses. |
| Electron IPC channels `hermes:*` | 751 occ / 127 distinct (`qubit:*` 56 / 22) | `qubit_runtime/apps/desktop/electron` + `apps/desktop/src` | preload↔renderer contract; a plugin `dist` may also name them. Per-channel writer/reader check required. |

## 8. Decision table (owner sign-off, row by row)

Tick one option per row. "Keep" is the default when the row is a contract. "Verify" means the
source evidence is incomplete and a focused runtime/build check is required **before** deciding.

| # | Surface | Evidence (count + representative) | Classification | Safe action / owner options | Risk if changed |
|---|---|---|---|---|---|
| **D1** | `HERMES_*` env contract | 688 distinct / 3,697 occ prod; 304 read-only names, 120 both, 22 write-only; `qubit_runtime/qubit_cli/gateway_runtime_start.py:55` writer, `qubit_runtime/qubit_cli/update_lock.py:100` reader | internal contract | ☐ Keep as-is ☐ Document only ☐ Plan a writer+reader+launch+test migration (one change) | High — silent half-rebrand / launch failure |
| **D2** | `_LEGACY_HOME_DIR` home fallback | 19 / 8; `qubit_runtime/qubit_constants.py:55,61-62,137` | read-old shim | ☐ Keep ☐ Add a future canonical-only mode with explicit migration | High — orphans existing installs |
| **D3** | `HERMES_HOME` + resolver | part of D1; `qubit_runtime/qubit_constants.py:198-225`, `qubit_runtime/qubit_cli/update_lock.py:100` | internal contract | ☐ Keep ☐ Add a compatibility test if any change | High — wrong home → wrong credentials/state |
| **D4** | Functional `~/.hermes` paths (update/desktop/remote/installer) | 819 prod `~/.hermes`; `qubit_runtime/qubit_cli/update_lock.py:100`, `qubit_runtime/apps/desktop/electron/remote-lifecycle.ts:40,226,285`, `qubit_runtime/apps/bootstrap-installer/src-tauri/src/paths.rs:40,205`, `voicebridge/agent_compat/adapters/hermes.py:55,97-103` | internal contract / adapter / migration | ☐ Keep ☐ Verify each reader against a pre-rename install before any change | High — update/remote/installer break |
| **D5** | `/root/.hermes` container bind | 164 / 47; `qubit_runtime/agent/file_safety.py:521-522`, `qubit_runtime/agent/terminal_env_provider.py:36`, `qubit_runtime/tools/credential_files.py:66,346-374`, `qubit_runtime/gateway/platforms/base.py:1083` | internal contract | ☐ Keep ☐ Only change with the image mount + all host mappers + container test | High — sandbox paths dangle |
| **D6** | Update markers / locks | `.hermes-update-in-progress` 136/50, `.hermes-update-result` 55/26, `.hermes-update-old` 42/11, `.hermes-bootstrap-complete` 43/24, `.hermes-update-staging` 39/8, `hermes-update.lock` 57/14 | migration surface / internal contract | ☐ Keep ☐ Add canonical aliases only with old-marker read support | High — in-flight update misread/lost |
| **D7** | Legacy data filenames | `hermes_config_overrides.json` 5/5; `hermes_persona.json` 4/4; `.hermes_history` 45 | read-old shim | ☐ Keep ☐ Dual-read old + write new (owner migration plan) | Medium-high — existing data unread |
| **D8** | Plugin-SDK host/bundle globals | `__HERMES_PLUGIN_SDK__` 48/22, `__HERMES_PLUGINS__` 43/23; live host dual (`frontend/qubit-web/src/plugins/registry.ts:194-212`), vendored host legacy-only (`qubit_runtime/web/src/plugins/registry.ts:113-118`), bundles canonical-only (`qubit_runtime/plugins/qubit-achievements/dashboard/dist/index.js:8`) | internal contract + **possible half-rebrand** | ☐ Verify `qubit serve`/desktop build ☐ Add canonical aliases to `qubit_runtime/web` if it is served ☐ Keep legacy aliases everywhere | High — plugin registration fails on a served surface |
| **D9** | Session/auth window globals | `__HERMES_SESSION_TOKEN__` 30 vs `__QUBIT_SESSION_TOKEN__` 204; `__HERMES_AUTH_REQUIRED__` 10 vs 37; `__HERMES_BASE_PATH__` 7 vs 52; writer `qubit_runtime/qubit_cli/web_server_dashboard.py:140-141,173,186-187`; guard **OK (46)** | security boundary | ☐ Keep canonical + guard ☐ Remove legacy docs/evidence only (no writer) | Highest — auth/token outage |
| **D10** | `requires_hermes` + skill tokens | 295/246 (203 catalog); 37 / 12 token occurrences; `qubit_runtime/qubit_cli/plugin_validate.py:104-108`, `qubit_runtime/agent/skill_preprocessing.py:16` | read-old shim / external contract | ☐ Keep read-old ☐ Add canonical field/token with dual-read | High — external plugins/skills stop loading |
| **D11** | `get_hermes_home` alias family | 108/42; `qubit_runtime/qubit_constants.py:247-258` | adapter / upstream compat | ☐ Keep ☐ Remove only after the upstream sync no longer imports it | High — `ImportError` in synced call sites |
| **D12** | systemd unit names + restart recovery | 19 distinct; actual install `qubit-gateway.service` (`scripts/install_gateway_unit.sh:17`, `qubit_runtime/qubit_cli/gateway.py:2231`), but `qubit_runtime/qubit_cli/update_restart_recovery.py:147` candidates are `hermes-gateway.service` / `gateway.service` | **suspected half-rebrand** | ☐ Verify restart recovery on a qubit-named unit ☐ Add `qubit-gateway*` candidates to recovery | Medium-high — post-update restart verification may fail |
| **D13** | NixOS option names | `services.hermes-agent`, `programs.hermes-agent`; `nix/checks.nix:40,57,80-90` | external config contract | ☐ Keep ☐ Add a Qubit option alias with migration | Medium — existing Nix configs break |
| **D14** | Audit paths + frozen pin | `.hermes.pre-retirement` 34/14; `FROZEN_REV` at `scripts/frozen_target_progress.py:71` | audit instrument | ☐ Keep (not product) ☐ Move only with every audit updated | Medium — audits measure nothing |
| **D15** | Attribution / legal | `qubit_runtime/LICENSE` MIT © 2025 Nous Research; `THIRD_PARTY_NOTICES.md` 22 records; `NousResearch` 926/403; `@nous-research/ui` 4 deps | attribution / legal | ☐ Keep (required exception) ☐ Legal review only | Legal defect |
| **D16** | Vendored upstream website docs | `qubit_runtime/website/**` — 7,483 `hermes` lines / 429 files; 1,558 `~/.hermes` | vendored docs | ☐ Keep ☐ Rebrand ☐ Remove from ship (owner call) | Low runtime; size/brand surface |
| **D17** | Internal `~/.hermes` prose in docstrings | part of 819 prod; e.g. `qubit_runtime/agent/curator.py:85`, `qubit_runtime/agent/anthropic_credentials.py:6,738,819`, `qubit_runtime/tools/approval_detection.py:24,415` | stale prose | ☐ Keep ☐ Clean only where the prose is wrong (no functional rename) | Low; maintainer confusion |
| **D18** | Bundled optional-skill scripts/prose | 10 `.hermes` occurrences / 7 files, mostly migration scripts | migration utility / stale prose | ☐ Keep ☐ Update non-migration prose only | Low |
| **D19** | Root reports/evidence (`INDEPENDENCE_VERDICT.md`, captured chunks) | 72 `.hermes` / 64 `~/.hermes` in `INDEPENDENCE_VERDICT.md`; `docs/e2e/evidence-api-chunk-served.js` reads legacy token | evidence artifact | ☐ Keep ☐ Archive/delete by owner call | None to runtime |
| **D20** | Electron IPC `hermes:*` channels | 751 occ / 127 distinct vs `qubit:*` 56/22 | internal contract | ☐ Keep ☐ Per-channel writer/reader migration only | High — preload/renderer/plugin break |
| **D21** | 22 write-only env names | e.g. `HERMES_DISABLE_PLUGINS`, `HERMES_EXEC_ASK`, `HERMES_GATEWAY_SESSION`, `HERMES_SECRET_KEY`, `HERMES_UPDATE_HANDOFF_PID` | needs verification (not "dead") | ☐ Verify external/child reader ☐ Keep writer if any consumer exists | Medium — removing a writer the child reads |
| **D22** | Test-only env names | 116 names absent from production surface | test fixture | ☐ Keep as tests ☐ No product action | None |

## 9. Top-risk rows (ranked)

1. **D1 — `HERMES_*` env contract.** 688 distinct production names, 304 with no in-tree writer.
   This is the single largest internal contract; renaming readers without the external writers is
   the exact half-rebrand bug class.
2. **D8 — plugin-SDK host/bundle mismatch.** The live dashboard dual-publishes, the vendored SPA
   host is legacy-only, and every checked-in bundle reads canonical-only. Source-level mismatch;
   verify the built `qubit serve`/desktop surface before touching aliases.
3. **D9 — session/auth globals.** Token/auth-mode/base-path globals are a security boundary. The
   guard is currently green; preserve the writer/reader pair and the gated-token condition.
4. **D5 — `/root/.hermes` container bind.** 164 occurrences; host and image must agree.
5. **D4 — functional `~/.hermes` paths.** Update, desktop remote, and installer all read a
   pre-rename home; changing one side strands existing installs/remotes.
6. **D15 — attribution/legal.** Required exception; never a rebrand target.
7. **D12 — restart-recovery unit candidates.** `qubit_runtime/qubit_cli/update_restart_recovery.py:147` still lists
   `hermes-gateway.service` while the install publishes `qubit-gateway.service`; suspected
   half-rebrand, needs a focused verification.
8. **D16 — vendored website docs.** 7,483 upstream-doc lines; owner decision (keep/rebrand/delete),
   not a runtime risk.

## 10. What I did NOT verify

- **No live service inspection/restart and no `/proc` env read.** The running gateway, desktop,
  voicebridge, and helper were not restarted or probed; this inventory cannot say what is set in
  the live processes.
- **No built-bundle inspection.** `frontend/qubit-dist` and `qubit_runtime/qubit_cli/web_dist` are
  absent/gitignored in this worktree. The D8 plugin-host mismatch is source-level only; I did not
  run `qubit serve`, the desktop app, or a browser to see whether in-tree bundles fail to register.
- **No installed external plugin bundles.** I inspected the four in-tree `dist/index.js` files and
  the contract test, but not the owner's installed `~/.local/state/.../plugins` or catalog
  `agent-analytics` bundle.
- **No full test matrix.** Only `python3 scripts/check_brand_half_rename.py` was run (exit 0,
  46 globals). `check_half_rebrand.py` (AST name-binding), `check_duplicate_defs.py`, the frozen
  target audit, and capability audits were not run.
- **No per-name dataflow analysis.** Reader/writer classification is line-level regex; a name
  passed to an unknown helper, assembled by concatenation, or read in a child process can be
  misclassified. "Other" (1,777 lines) includes literals and docstrings, not live reads.
- **No remote-host verification.** The `~/.hermes` remote paths for `ssh` / `daytona` /
  `vercel_sandbox` are declared in code; no remote host was probed to confirm the directory exists
  or that the lock/reattach path works.
- **No NixOS config verification.** `services.hermes-agent` / `programs.hermes-agent` are inspected
  as source; no real NixOS user configuration was evaluated.
- **No legal review.** Attribution counts are mechanical; whether every obligation is satisfied is
  a legal question outside this inventory.
- **No Windows-native execution.** Windows path spellings (`%LOCALAPPDATA%\hermes`) are counted
  from source only.
- **No exhaustive review of all 850 name tokens.** The top/subsystem/reader-writer tables are
  representative and exhaustive only in the aggregate counts; individual anomalies below the
  top-N were not triaged one by one.

## Appendix A — exact top-level commands used

```bash
# Commit / scope
git rev-parse --abbrev-ref HEAD; git rev-parse --short HEAD
git ls-files | wc -l; git ls-files '*.py' | wc -l

# Env names
git grep -ohE '\bHERMES_[A-Z0-9_]+' | sort -u | wc -l
git grep -ohE '\bHERMES_[A-Z0-9_]+' | wc -l
git grep -ohE '\bHERMES_[A-Z0-9_]+' -- '*.py' '*.ts' '*.tsx' '*.js' '*.mjs' '*.cjs' \
  '*.sh' '*.bash' '*.zsh' '*.yaml' '*.yml' '*.toml' '*.service' '*.env' '.env.example' | sort -u | wc -l
git grep -ohE '\bHERMES_[A-Z0-9_]+' -- <same globs> ':!**/tests/**' ':!tests/**' \
  ':!**/tests-js/**' ':!**/test_*.py' ':!**/*.test.ts' ':!**/*.test.tsx' \
  ':!**/__tests__/**' ':!qubit_runtime/website/**' | sort -u | wc -l

# Paths
git grep -oF '.hermes' | wc -l
git grep -oF '~/.hermes' | wc -l
git grep -oF '$HOME/.hermes' | wc -l
git grep -oF '/root/.hermes' | wc -l
git grep -oF '.hermes/' | wc -l
git grep -oF '.hermes_history' | wc -l

# Globals
for n in __HERMES_PLUGIN_SDK__ __HERMES_PLUGINS__ __QUBIT_PLUGIN_SDK__ __QUBIT_PLUGINS__ \
         __QUBIT_PLUGIN_SDK_LEGACY__ __QUBIT_PLUGINS_LEGACY__ \
         __HERMES_SESSION_TOKEN__ __QUBIT_SESSION_TOKEN__ __HERMES_AUTH_REQUIRED__ \
         __QUBIT_AUTH_REQUIRED__ __HERMES_BASE_PATH__ __QUBIT_BASE_PATH__; do
  printf '%-30s %s %s\n' "$n" "$(git grep -oF "$n" | wc -l)" "$(git grep -lF "$n" | wc -l)"
done

# Attribution / legacy anchors
git grep -oF 'NousResearch' | wc -l
git grep -oF 'nousresearch.com' | wc -l
git grep -oF 'Nous Research' | wc -l
grep -c '^## ' THIRD_PARTY_NOTICES.md
git grep -nF '_LEGACY_HOME_DIR'
git grep -nF '.hermes.pre-retirement' -- 'scripts/**' 'qubit_runtime/scripts/**'
git grep -nF 'requires_hermes'
git grep -nF '${HERMES_SKILL_DIR}'
git grep -nF '${HERMES_SESSION_ID}'
grep -n 'FROZEN_REV' scripts/frozen_target_progress.py

# Guard (only automated check run)
python3 scripts/check_brand_half_rename.py
```

## Appendix B — measurement caveats

- `git grep` counts tracked files only; untracked build outputs (including the served
  `qubit-dist`/`web_dist`) are **not** in these counts.
- A name can be both read and written; line-level read/write totals therefore overlap only in the
  `both` row (12 production lines), and name-level `read-only + write-only + both` sums to 446,
  not 688, because 242 names appear only in "other"/literal/comment lines.
- The production filter cannot perfectly exclude a test fixture that lives outside a `tests/`
  directory; the remaining "other" 1,777 lines are mostly literals and docstrings, not live reads.
- `.hermes` token counts include CSS classes and identifiers; use the path-shaped counts in §3.1
  when deciding about paths.
- Counts are a snapshot of `6bc665780`; the tree moves. Re-run the command rather than quoting this
  document as current.
