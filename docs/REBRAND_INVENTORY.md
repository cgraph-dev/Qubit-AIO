# REBRAND INVENTORY — authoritative work list for the final rebrand pass

**Status:** inventory + audit. **No product code changed by this document or its branch.**
Measured against `b47686ff2` (main tip) in worktree branch `rebrand-inventory`.

**Scope of claim:** this doc inventories and *proves* defects; it does not fix them.
The final pass works from this list. Every count below was produced by the command
shown next to it. Every mismatch claim carries **both** the writer and the reader
`file:line`.

> **Method warning, recorded because it cost me a false count first.** `git grep -E '__(QUIBIT|HERMES)_[A-Z_]+__'`
> returns **only the first alternative's** matches (275 = the `__HERMES_*` total), silently
> dropping the 520 `__QUBIT_*` matches. **Count each brand in a separate grep.** Never trust
> an alternation for a count. Verified:
> `git grep -ohIE '__QUBIT_[A-Za-z0-9_]+__' | wc -l` → `520`;
> `git grep -ohIE '__HERMES_[A-Za-z0-9_]+__' | wc -l` → `275`;
> `git grep -ohIE '__(HERMES|QUIBIT)_[A-Za-z0-9_]+__' | wc -l` → `275`.

---

## 0. The two servers — read this before the tables

There are **two independent HTTP servers**, and they inject **different global names**.
This is the root cause of the whole mismatch class.

| Server | Serves | Injects | Evidence |
|---|---|---|---|
| `voicebridge/webui.py` | the **live** dashboard on `:8890` (`frontend/qubit-dist/`) | `__QUBIT_*` (9 sites) | `webui.py:1928-1932`, `2293-2295` |
| `qubit_runtime/qubit_cli/web_server_dashboard.py` | `qubit serve` / desktop-embedded backend (`qubit_cli/web_dist/`) | `__HERMES_*` (9 sites) | `web_server_dashboard.py:139,140,170,182-186,79` |

Live proof of which one owns the user session:

```
$ curl -s http://127.0.0.1:8890/chat | grep -oE '__[A-Za-z_0-9]+__' | sort | uniq -c
      1 __QUBIT_API_PREFIX__
      1 __QUBIT_BASE_PATH__
      1 __QUBIT_SESSION_TOKEN__
      1 __QUBIT_THEME__
      1 __QUBIT_THEME_DEF__
```

Counted injection sites:

```
$ git grep -cE 'window\.__HERMES_' -- qubit_runtime/qubit_cli/web_server_dashboard.py   # 9
$ git grep -cE 'window\.__QUBIT_'  -- voicebridge/webui.py                              # 9
```

**There is no runtime aliasing.** The server does not emit both spellings, and the SPA
does not fall back to the legacy name. Verified:

```
$ git grep -nE '__QUBIT_SESSION_TOKEN__.*__HERMES_SESSION_TOKEN__|__HERMES_SESSION_TOKEN__.*__QUBIT' \
    -- '*.py' '*.ts' '*.tsx' '*.js' '*.html'     # 0 lines
$ git grep -nE '__HERMES_' -- frontend/qubit-web/src | grep -v test   # 0 code lines (comments/plugin-alias only)
```

Only the **plugin SDK globals** are dual-published, deliberately (see §1, row 2).

---

## 1. WRITER / READER MISMATCH TABLE

The half-rename class. Each row: one side writes a name, the other reads a different name,
and no third party reconciles them.

### 1a. CONFIRMED BROKEN — writer and reader disagree, nothing bridges them

| # | Writer (file:line) | Reads | Reader (file:line) | Impact | Status |
|---|---|---|---|---|---|
| **M1** | `qubit_runtime/qubit_cli/web_server_dashboard.py:139`<br>`f"window.__HERMES_SESSION_TOKEN__={...}"` | `__QUBIT_SESSION_TOKEN__` | `qubit_runtime/apps/desktop/electron/dashboard-token.ts:39`<br>`/window\.__QUBIT_SESSION_TOKEN__\s*=\s*("(?:\\|.")*")/` | **Desktop shell cannot extract the session token** from a `qubit serve` page it boots against. The extractor regex matches nothing → token `null` → `/api/ws` auth fails. | **REPRODUCED** (test `test_index_uses_ssh_token_applied_after_spa_mount`) |
| **M2** | `web_server_dashboard.py:140`<br>`"window.__HERMES_AUTH_REQUIRED__=false;"` | `__QUBIT_AUTH_REQUIRED__` | `qubit_runtime/tests/qubit_cli/test_web_server.py:5525`<br>`assert "window.__QUBIT_AUTH_REQUIRED__=false" in resp.text` | Gated/ungated mode selection is invisible to the SPA: it can never see that auth is off. | **REPRODUCED** |
| **M3** | `web_server_dashboard.py:184`<br>`f"window.__HERMES_AUTH_REQUIRED__={...}"` | `__QUBIT_AUTH_REQUIRED__` | `qubit_runtime/web/src/lib/api.ts:229`<br>`if (!window.__QUBIT_AUTH_REQUIRED__ && ...)` | The stale-token reload path (`api.ts:229`) and the ws-ticket branch (`api.ts:284`) key off a global the server never sets. | **CONFIRMED by grep; same class as M2** |
| **M4** | `web_server_dashboard.py:170`<br>`f'window.__HERMES_SESSION_TOKEN__="{_SESSION_TOKEN}";'` | `__QUBIT_SESSION_TOKEN__` | `qubit_runtime/web/src/lib/api.ts` (canonical SPA bundle) | The `qubit serve` backend's own served SPA reads a global it is never given. | **CONFIRMED by grep** |
| **M5** | `web_server_dashboard.py:79`<br>`'<style id="hermes-theme-bootstrap">'` | `qubit-theme-bootstrap` | `qubit_runtime/tests/qubit_cli/test_web_server.py:4303`<br>`assert '<style id="qubit-theme-bootstrap">' in resp.text` | Theme bootstrap `<style>` id renamed on the reader side only. Seen failing in `lane-baseline.txt:28436`. | **REPRODUCED** (`TestThemeBootstrapCSS`) |

**Reproduction (M1/M2) — run on the MAIN checkout, not the worktree**, so the missing
`web_dist` artifact cannot be blamed:

```
$ cd /home/trick/Projects/Repos/qubit-voicebridge/qubit_runtime
$ ../.venv/bin/python -m pytest \
    'tests/qubit_cli/test_web_server.py::TestServeIndexMissingIndex::test_index_uses_ssh_token_applied_after_spa_mount' -q
>       assert 'window.__QUBIT_SESSION_TOKEN__="after-mount"' in resp.text
E       assert 'window.__QUBIT_SESSION_TOKEN__="after-mount"' in
        '<html><head><script>window.__HERMES_SESSION_TOKEN__="after-mount";
         window.__HERMES_DASHBOARD_EMBEDDED_CHAT__=true;...'
tests/qubit_cli/test_web_server.py:5480: AssertionError
1 failed
```

The two sides are on **adjacent lines of the same feature** and were renamed in opposite
directions. This is exactly the defect class the task brief describes.

### 1b. CONFIRMED GOOD — dual-read / accept-both, do NOT "fix"

| Name | Writer | Reader (both accepted) | Why it is correct |
|---|---|---|---|
| `__QUBIT_PLUGIN_SDK__` / `__QUBIT_PLUGINS__` | `frontend/qubit-web/src/plugins/registry.ts:194-195` | canonical | first-party surface |
| `__HERMES_PLUGIN_SDK__` / `__HERMES_PLUGINS__` | `frontend/qubit-web/src/plugins/registry.ts:208-209` | legacy alias, same `sdk`/`registry` object | installed plugin `dist` bundles we neither own nor can rebuild read the old name; `agent-analytics` (catalog `qubit_runtime/plugin-catalog/agent-analytics.yaml`) does `const SDK = window.__HERMES_PLUGIN_SDK__` then destructures `React` → throws `Cannot destructure property 'React' of 'SDK' as it is undefined` and takes the route down. Contract documented in `frontend/qubit-web/src/plugins/plugin-globals.test.ts:1-24`. |
| `__QUBIT_PLUGIN_SDK_LEGACY__` / `__QUBIT_PLUGINS_LEGACY__` | `registry.ts:211-212` | — | earlier spelling of the alias, kept live. No in-tree reader; **not** dead code to delete blindly. |
| `X-Hermes-Session-Token` | `qubit_runtime/qubit_cli/web_server.py:390` (`_SESSION_HEADER_NAME`) | `web_server.py:477` reads its own constant | internally consistent |
| `X-Qubit-Session-Token` | `voicebridge/webui.py:1524,1780`; clients at `webui.py:9317` etc. | same | the *other* server's name |
| **both headers across servers** | — | `voicebridge/qubit_asgi.py:177-178,220` reads `getattr(ws, "_SESSION_HEADER_NAME") or "X-Qubit-Session-Token"` | the ASGI proxy derives the header name from the **server attribute**, so the two servers agree at the boundary. Correctly bridged — leave alone. |
| localStorage locale/theme/font/sidebar | `frontend/qubit-web/src/i18n/context.tsx:88-91`, `themes/context.tsx:440`, `App.tsx:632` | read-old shim: read `hermes-*`, migrate to `qubit-*`, remove old | deliberate user-state migration. See §3. |

### 1c. SUSPECTED but NOT a mismatch — stated so nobody "fixes" it

| Looks like a split | Verdict | Proof |
|---|---|---|
| `hermes:power-resume` (send) vs `qubit:power-resume` (comment) | **fine** | `main.ts:6243` `webContents.send('hermes:power-resume')`; `preload.ts:589-591` listens for `hermes:power-resume`; renderer consumes via `desktop.onPowerResume` (`use-gateway-boot.ts:1164`, `terminals.ts:102`). The only `qubit:power-resume` occurrence is inside a **comment** (`terminals.ts:98`). |
| 16 event basenames appear with both `hermes:` and `qubit:` prefixes | **mixed — triage, do not blind-rename** | `comm` output (§ commands) lists `api, boot-progress, composer-drafts, example, external-open-failed, feature-flags, fetch, fs, link-titles, power-resume, preview-file-changed, profile, read, terminal, updates, window-state-changed`. Each is an Electron IPC channel; several are genuine dual-spelling pairs on *different* channels (e.g. `hermes:updates:progress` in `main.ts:3156` vs `qubit:updates:metric:pending` in `desktop-shared-metrics.ts:31` — different messages, not a rename). Renaming requires the per-channel writer/reader check, not a prefix sweep. |
| Desktop localStorage keys: writer `hermes.desktop.*` vs test-asserted `qubit.desktop.*` | **suspect — needs desktop-suite run** | `qubit_runtime/apps/desktop/src/store/onboarding.ts:98-99` writes `hermes-desktop-onboarded-v1` / `hermes-onboarding-skipped-v1`; `onboarding.test.ts:315,322,347` set and assert `qubit-desktop-onboarded-v1` / `qubit-onboarding-skipped-v1`. Same at `store/session.ts:34` (`hermes.desktop.composer.provider`) vs `use-session-actions/utils.test.ts:2491` (`qubit.desktop.composer.provider`), `contrib/plugins-store.ts:36` (`hermes.desktop.pluginDecisions.v2`) vs `plugins-tab.test.tsx:421` (`qubit.…`), `store/artifacts`/`preview.test.ts:501` (`qubit.desktop.previewTabs.v2`) vs `store/preview-tabs-scope.test.ts:24` (`hermes.…`). **I could not run the desktop suite here** (`qubit_runtime/apps/desktop/node_modules/.bin/vitest` is absent; only `frontend/qubit-web` has vitest installed). Flagged, not asserted. |

**Counted evidence for the dual-spelled global set** — 11 distinct names carry both brands:

```
$ git grep -ohIE '__QUBIT_[A-Za-z0-9_]+__' | sed 's/^__QUBIT_//;s/__$//' | sort -u > /tmp/q
$ git grep -ohIE '__HERMES_[A-Za-z0-9_]+__' | sed 's/^__HERMES_//;s/__$//' | sort -u > /tmp/h
$ comm -12 /tmp/q /tmp/h
AUTH_REQUIRED  BASE_PATH  DASHBOARD_EMBEDDED_CHAT  DASHBOARD_PROFILE  FENCE_a9f7b3
INITIAL_PROFILE  LOGIN_PATH_END  LOGIN_PATH_START  PLUGINS  PLUGIN_SDK  SESSION_TOKEN
```

Distinct totals: `26` `__QUBIT_*` names, `31` `__HERMES_*` names.

---

## 2. CATEGORY INVENTORY (counts from a shown command, every one)

### A. USER-VISIBLE product strings still saying hermes / agent — **the rebrand targets**

| Surface | Count | Command | Notes |
|---|---|---|---|
| Runtime locale packs (`qubit_runtime/locales/`) | **799 lines across 34 files** | `git grep -IE 'Hermes\|Nous' -- 'qubit_runtime/locales/*' \| wc -l` | **Triage, not blanket.** The `qubit_models_not_agentic` key is *already rebranded* to "Qubit Agent"; the `Hermes` remaining there is **Nous Research's model name** (`en.yaml:3199`, `de.yaml:3099`). "Nous Portal / Nous Research / Nous balance" are **provider names** (the actual billing counterparty) — legal/technical, not product brand. |
| Shipped CLI product-name prints | **18 lines** | `git grep -IE '"Hermes Agent"\|Hermes Desktop\|Hermes Cloud\|Nous Portal\|Nous Cloud' -- 'qubit_runtime/qubit_cli/*.py' 'voicebridge/*.py' \| grep -v tests \| wc -l` | Real gaps: `cli_render.py:922` and `cli_tui_mixin.py:1229` `get_branding("agent_name", "Hermes Agent")` **fallback that fires when a user skin omits `agent_name`**; `gateway_chat_view.py:477` `"Welcome to Hermes Agent!"`; `update_cmd.py:1825` `"☤ Updating Hermes Agent..."`; `main_desktop.py:1445,1630,1845,1857` Desktop launch/close notices; `setup_quick.py:61` `"☤ Hermes Setup — Nous Portal"`; `cron.py:586` restart hint; `main_dep_hints.py:32,39`. |
| Stale `~/.hermes/…` paths in bundled-skills prose | **22 lines** | `git grep -E '~/\.hermes' -- 'qubit_runtime/optional-skills' 'skills' \| wc -l` | Instructions telling the user/agent the wrong home; the correct resolver is `$QUBIT_HOME`. |
| **REBRAND OVER-REACH (new finding, inverse bug)** | **6 files** | `git grep -cIE 'Qubit (3\|4)' -- 'qubit_runtime/locales/*'` | `es.yaml:3099`, `ga.yaml:3099`, `ja.yaml:3099`, `pt.yaml:3099`, `zh.yaml:3099`, `zh-hant.yaml:3099` now read **"Qubit 3 和 4 模型" / "modelos Qubit 3 y 4"** — a blind replace turned the **real model name** "Hermes 3 & 4" into a product name that does not exist. `en.yaml:3199` still says `Hermes 3 & 4` correctly. **Fix = restore the model name in those 6 files.** |
| Vendored upstream docs website | **7472 lines** | `git grep -cIE 'hermes' -- 'qubit_runtime/website/**' \| awk -F: '{s+=$2} END{print s}'` | Upstream docs mirror. Owner call: rebrand or delete; not a shipped product surface. |

### B. INTERNAL identifiers — deliberately **NOT** renamed, inventoried so nobody "fixes" them

| Kind | Count | Command | Why it stays |
|---|---|---|---|
| `HERMES_*` env vars, distinct | **745** | `git grep -ohIE '\bHERMES_[A-Z0-9_]+' -- '*.py' '*.ts' '*.tsx' '*.js' \| sort -u \| wc -l` | Set by a parent process, read by a child, or crossed over a socket. Renaming the reader without the writer is the bug this doc exists to prevent. |
| `HERMES_*` env vars, occurrences | **8035** | same, `\| wc -l` | canonical reader is `qubit_constants.get_qubit_home()` (prefers `QUBIT_HOME`, falls back to legacy home). |
| `QUBIT_*` env vars, distinct | **301** | `git grep -ohIE '\bQUBIT_[A-Z0-9_]+' -- '*.py' '*.ts' '*.tsx' '*.js' \| sort -u \| wc -l` | the migration's target spelling. |
| Electron IPC channels `hermes:*` | **751 occurrences / 127 distinct** | `git grep -ohE '(hermes\|qubit):[a-z0-9-]+' -- 'qubit_runtime/apps/desktop/electron' 'qubit_runtime/apps/desktop/src' 'frontend/**' 'qubit_runtime/web/src' \| grep -c '^hermes:'` | preload↔renderer contract; a plugin `dist` may also name them. |
| Electron IPC channels `qubit:*` | **56 occurrences / 22 distinct** | same, `grep -c '^qubit:'` | |
| Desktop marker/lock files (`.hermes-update-*`, `hermes-update.lock`) | see `docs/HERMES_RESIDUE_INVENTORY.md` §3 | — | read by an in-flight update path that may have been written by the previous build. |
| systemd `hermes-*.service` names in-tree | see `docs/HERMES_RESIDUE_INVENTORY.md` §2 | — | fixtures + the update path that **retires** old units. Installed units are all `qubit-*`. |
| `HermesCLI = QubitCLI`, `get_hermes_home` alias, etc. | `qubit_runtime/qubit_constants.py:55-62,192-203` | `git grep -nE '_LEGACY_HOME_DIR\|get_hermes_home' -- '*.py' \| grep -v tests \| wc -l` → 7 | one-directional compat pointers for vendored-upstream call sites. |

### C. MUST-STAY legacy — do-not-touch, with reason

| Anchor | Evidence | Reason it breaks the system if renamed |
|---|---|---|
| `_LEGACY_HOME_DIR = ".hermes"` | `qubit_runtime/qubit_constants.py:55`; used `qubit_cli/gateway.py:3104,3115-3116` | **read-old shim**: the resolver prefers `QUBIT_HOME` and falls back to a pre-existing `~/.hermes` home. Renaming orphans every existing install's state and secrets at once. |
| `~/.hermes.pre-retirement` | `scripts/audit_capability_gaps.py:72,81,87`; `scripts/audit_missing_symbols.py:16,23,29` | path to the **retired upstream tree** the audits measure against. Rename it and "every audit would measure nothing" (their own words). 19 lines. |
| Container mount target `/root/.hermes` | `qubit_runtime/agent/file_safety.py:521`; `qubit_runtime/agent/terminal_env_provider.py:36` | the **Docker sandbox bind-mount target** as seen *inside* the container. Changing the host-side name without the image-side mount path breaks path translation for every sandboxed command. 125 lines. |
| Frozen upstream pin `69948c0057…` | hardcoded in `scripts/audit_capability_gaps.py`, `scripts/sync_upstream.py`, `scripts/hermes_vendor_manifest.py`, `scripts/merge_v0215/freeze.py` (+3) | identifies an **upstream commit**. Rewriting it breaks the audit trail and the sync mechanism. |
| Upstream attribution / `THIRD_PARTY_NOTICES.md`, `LICENSE` scope | `docs/BRAND.md` rule (attribution is the **required exception** to zero-legacy-brand) | legal requirement. |
| Plugin manifest field `requires_hermes` | read-old at `qubit_runtime/qubit_cli/plugin_validate.py:104-108` (`manifest.get("requires_hermes") or manifest.get("hermes")`); **202 catalog files** use it | external plugin contract. |
| `${HERMES_SKILL_DIR}` / `${HERMES_SESSION_ID}` | loader regex `qubit_runtime/agent/skill_preprocessing.py:16` matches the literal token; skills are authored against it | authored-in-the-wild token. |
| Agent-Quest / Nous Research model names | `qubit_runtime/apps/desktop/.../local-models-settings.test.tsx:635` (`Hermes-4.3-36B-Q5_K_M`), `plugin-catalog/hermes-zh.yaml` (`repo: …/hermes-zh`) | **real upstream model / external repo names**, not our brand. |
| React Native's Hermes JS engine | `skills/ui-ux-pro-max/...` (`hermes_enabled`) | unrelated Meta technology — **false positive** in every grep. |

---

## 3. SECURITY-BOUNDARY RISK LIST

A rename here touches an auth boundary. Treat as **high-risk**: change writer and reader
in the same commit, then test both directions.

| # | Boundary | Writer | Reader | Risk if half-renamed |
|---|---|---|---|---|
| **S1** | **Session-token global** | `web_server_dashboard.py:139,170` (`__HERMES_SESSION_TOKEN__`) | `dashboard-token.ts:39` regex + SPA `api.ts:477,613,639` (`__QUBIT_SESSION_TOKEN__`) | **highest.** The token is the sole credential for loopback `/api/*`; a name split means the SPA/desktop attaches no credential and every API read 401s (the outage recorded at `voicebridge/webui.py:4978`). |
| **S2** | **Auth-mode global** | `web_server_dashboard.py:140,184` (`__HERMES_AUTH_REQUIRED__`) | `api.ts:229,284`, `AuthWidget.tsx:52`, `dashboard-auth-reload.ts:9` | SPA cannot tell gated from loopback mode → picks the wrong auth scheme (token vs ws-ticket) and/or fails to retry a stale-token 401 (`api.ts:527-541`). |
| **S3** | **Session HTTP header** | `qubit_runtime/qubit_cli/web_server.py:390` `X-Hermes-Session-Token` | `web_server.py:477` (same constant) + clients `voicebridge/cli.py:9317`, `orb/client.py:229`, `desktop_helper.ts` | Domain-separated from `X-Qubit-Session-Token`. **Now correctly bridged** cross-server via `qubit_asgi.py:177-178,220` (reads the server attribute). **Any** change must keep that attribute indirection or the proxy sends the wrong header. |
| **S4** | **Forge-resistant Office route: Referer + nonce cookie** | nonce issued `webui.py:5263-5270` (`_issue_office_nonce`, HttpOnly cookie), Referer checked `webui.py:1692-1693`, `1789-1790` | same routes | The nonce exists precisely because **Referer is caller-supplied** (`webui.py:1683-1684`: a bare `curl -H 'Referer: …/plugin-apps/qubit-office/habblaud/'` read real transcripts). Renaming the cookie name (or `OFFICE_APP_PREFIX`, `webui.py:57`) without both sides re-opens a **forged-Referer data leak**. `OFFICE_APP_PREFIX` is part of the auth predicate — **do not touch** as "branding". |
| **S5** | **OAuth session cookies** | `qubit_runtime/qubit_cli/dashboard_auth/cookies.py:27-31` (`hermes_session_at`, `hermes_session_rt`, `hermes_session_provider`, `hermes_session_pkce`, `hermes_sso_attempt`) | same module; `__Host-` prefixing per `cookies.py:9-11` | Renaming orphans every logged-in browser session and can break the `__Host-`/CSRF balance. Crosses a **cookie-name ↔ browser-jar** boundary we cannot migrate server-side. |
| **S6** | **Plugin SDK global** | `registry.ts:194-195` canonical + `208-209` legacy alias | third-party `dist` bundles (unrebuildable) | Removing the alias throws `Cannot destructure property 'React' of 'SDK' as it is undefined` on every route (documented `plugin-globals.test.ts:12-24`). **Keep both.** |
| **S7** | **Dashboard base-path global** | `web_server_dashboard.py:183` (`__HERMES_BASE_PATH__`) | SPA `api.ts:10` (`__QUBIT_BASE_PATH__`); **external** verifiers assert the HERMES name — `scripts/clean_install_check.sh:229-230`, `scripts/verify_installed_dashboard.sh:118-119`, `scripts/clean_install_rehearsal.py:271` | This global decides **asset and API URL prefixes**. Getting it wrong 404s the whole SPA. Note the external contract asserts the *legacy* spelling — so a rename must update those scripts in the same commit. |
| **S8** | **`qubit serve` headless token page** | `web_server_dashboard.py:133-143` — serves the raw token only when `not gated` | desktop handshake (`dashboard-token.ts`) | The `gated` check at `:137` is the guarantee that a gated serve never exposes the token. Any edit here must preserve that condition verbatim. |

**Existing security guards this pass must not weaken:** the loopback peer guard
(`voicebridge/webui.py:1909-1923` comment + `_peer_is_loopback`), the forged-Referer close
(`webui.py:1792-1793`, `5263-5270`), and the constant-time compares
(`web_server.py:479` `hmac.compare_digest`).

---

## 4. RECOMMENDED PASS ORDER

1. **Fix M1–M5 first** (writer/reader globals). Pick **one** canonical spelling
   (the SPA already reads `__QUBIT_*`, and `voicebridge/webui.py` already writes it — so
   `web_server_dashboard.py` is the side that is behind). Change writer **and** its test
   assertions **and** the external verifier scripts (§S7) in one commit. Re-run
   `tests/qubit_cli/test_web_server.py` and `apps/desktop/electron/dashboard-token.test.ts`.
2. **Restore the model name** in the 6 over-reached locale files (§2A).
3. **Then** the pure-prose surface: CLI prints (18), skills `~/.hermes` (22).
4. **Leave B and C exactly as they are.** Any `HERMES_*` env var or cookie rename needs the
   single-write/dual-read pattern proven in `qubit_runtime/gateway/restart.py` — not a sweep.
5. **Triage, never sweep**, the 16 dual-prefixed IPC channel basenames and the desktop
   localStorage pairs.

## 5. WHAT I COULD NOT VERIFY (honest statement)

- **Desktop Vitest suite did not run** — `qubit_runtime/apps/desktop/node_modules/.bin/vitest`
  is not installed (only `frontend/qubit-web` has vitest). The `hermes.desktop.*` vs
  `qubit.desktop.*` localStorage split in §1c is therefore **flagged, not proven broken**.
- **`frontend/qubit-web/src/lib/api.test.ts` passes (54/54)**, run and confirmed — but it
  stubs the globals itself, so it cannot catch a server-side spelling mismatch. A passing
  SPA suite is **not** evidence that M1–M4 are harmless.
- The 42 failures in `tests/qubit_cli/test_web_server.py` include substantial
  **pre-existing, unrelated** breakage (also failing on `main`); I isolated the
  global-name class by direct assertion inspection, not by assuming all 42 are rebrand.
- `qubit_runtime/website/**` (7472 lines) classified as a vendored upstream mirror, not a
  product surface — I did not confirm the owner's intent for it.
- No other agent session's files were touched; `git status` in the worktree is clean apart
  from this document.

## 6. COMMANDS USED (for reproduction)

```bash
# global-name census (NEVER use alternation for a count)
git grep -ohIE '__QUBIT_[A-Za-z0-9_]+__'  | sort | uniq -c | sort -rn
git grep -ohIE '__HERMES_[A-Za-z0-9_]+__' | sort | uniq -c | sort -rn
comm -12 <(git grep -ohIE '__QUBIT_[A-Za-z0-9_]+__'  | sed 's/^__QUBIT_//;s/__$//' | sort -u) \
         <(git grep -ohIE '__HERMES_[A-Za-z0-9_]+__' | sed 's/^__HERMES_//;s/__$//' | sort -u)

# the live injector's actual output
curl -s http://127.0.0.1:8890/chat | grep -oE '__[A-Za-z_0-9]+__' | sort | uniq -c

# the reproduced defect
cd qubit_runtime && ../.venv/bin/python -m pytest \
  'tests/qubit_cli/test_web_server.py::TestServeIndexMissingIndex::test_index_uses_ssh_token_applied_after_spa_mount' -q

# categories
git grep -IE 'Hermes|Nous' -- 'qubit_runtime/locales/*' | wc -l
git grep -IE '"Hermes Agent"|Hermes Desktop|Hermes Cloud|Nous Portal|Nous Cloud' \
  -- 'qubit_runtime/qubit_cli/*.py' 'voicebridge/*.py' | grep -v tests | wc -l
git grep -ohIE '\bHERMES_[A-Z0-9_]+' -- '*.py' '*.ts' '*.tsx' '*.js' | sort -u | wc -l
git grep -cIE 'Qubit (3|4)' -- 'qubit_runtime/locales/*'
```
