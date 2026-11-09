# Workbench UI Port — hermes-webui's real static frontend, into Qubit

**Status:** plan + endpoint contract. STEP 1 deliverable (write-only).

- **Upstream:** `github.com/nesquena/hermes-webui`
- **Pin:** `3d9488fca6bb248c77f988bfbffcca9d8cfd9561`
- **License:** MIT — Copyright (c) 2025 Hermes Web UI Contributors.
  Attribution is preserved in `voicebridge/workbench_ui/LICENSE` and in
  `THIRD_PARTY_NOTICES.md`; every `Hermes`/`hermes` token that survives the
  rebrand must be either that notice, an upstream-compatibility string the
  server must still accept (none are, see §4), or a comment citing upstream
  source (`// upstream:` lines are rebranded too — see §4 rule R7).
- **Source:** `hermes-webui/static/` — 95 files, 8,514,761 bytes.
- **Destination tree:** `voicebridge/workbench_ui/`
- **Server:** `voicebridge/workbench_ui_routes.py` — asset serving (the
  `_serve_plugin_app` confinement + allowlist pattern) plus the `/api/*` shim.
- **Mount:** `/agent/wb/` (assets) and `/agent/api/wb/*` is NOT used — the
  shim answers the UI's own literal `api/...` paths under the shell prefix
  (see §3, "How the prefix works").

**Why this document exists.** Two earlier attempts wrote *new* React
components from a description of hermes-webui and called the page ported. The
owner's UI is the value; the layout, the CSS, the 40-odd panels and the
message renderer are not reproducible from a description. This document is
therefore written BEFORE any code: it enumerates every file, every endpoint
their JS calls, and every identifier that has to change — so the port is a
bounded, checkable act of copying + patching, not a re-implementation.

---

## 1. File inventory — every upstream `static/` file

Disposition legend:

| Code | Meaning |
|------|---------|
| **COPY** | byte-identical copy. No edit. |
| **PATCH** | copied, then a named, minimal edit (documented in §4/§5). |
| **OMIT** | not copied, with the reason stated. |

Everything lands under `voicebridge/workbench_ui/` at the SAME relative path
it has upstream (`static/index.html` → `voicebridge/workbench_ui/index.html`).
The upstream in-file URLs are written as `static/style.css`, `static/ui.js`
etc. — i.e. the tree is served with `static/` as the URL segment below the
mount, so `static/...` resolves under `<mount>/static/...`. That keeps the
HTML untouched on its asset URLs. (Concretely: the tree is mounted at
`/agent/wb/static/`, so `static/ui.js` under base `/agent/wb/` is
`/agent/wb/static/ui.js`.)

### 1.1 Root-level files (31)

| Upstream file | Bytes | Disposition | Note |
|---|---:|---|---|
| `index.html` | 231,286 | **PATCH** | Head: inject Qubit globals, rebrand title/PWA meta, drop CDN tags (P1–P8). Body: rebrand visible strings and the `hermes-*` mark id. |
| `style.css` | 563,998 | **COPY** | No rebrand: measured zero `hermes` selectors/vars (§4 R6). |
| `ui.js` | 1,387,054 | **PATCH** | API base resolution + token header (single seam, §5); storage keys (§4 R3). |
| `sessions.js` | 725,193 | **PATCH** | storage keys; `openHermesDashboard` call sites (§4 R2). |
| `panels.js` | 718,514 | **PATCH** | storage keys; `hermes-kanban-md` class (§4 R5). |
| `messages.js` | 540,047 | **PATCH** | `_apiUrl()` seam already exists — verify it routes through the patched base. |
| `boot.js` | 225,830 | **PATCH** | storage/BroadcastChannel/global names (§4 R1–R3). |
| `commands.js` | 138,956 | **PATCH** | storage keys only. |
| `workspace.js` | 91,344 | **PATCH** | the `api()` helper — the second API seam (§5). |
| `assistant_turn_anchors.js` | 67,778 | **PATCH** | `window.HermesAssistantTurnAnchors` (§4 R1). |
| `extension_settings.js` | 32,608 | **PATCH** | `window.HermesExtensionSettings` (§4 R1). |
| `terminal.js` | 30,593 | **COPY** | xterm is now vendored (§1.3); no `hermes` token in the file. |
| `outline.js` | 20,422 | **COPY** | none. |
| `onboarding.js` | 43,525 | **PATCH** | visible strings + storage key. Endpoints are GAP (§3). |
| `a11y-helpers.js` | 18,580 | **COPY** | none. |
| `icons.js` | 11,944 | **COPY** | none. |
| `login.js` | 9,286 | **OMIT** | Its only job is the upstream password/passkey login screen. Qubit's gate is the dashboard's own (`X-Qubit-Session-Token`, loopback bootstrap), and a second auth scheme is forbidden by the task. `index.html`'s reference to it is dropped. |
| `share.js` | 3,811 | **OMIT** | Serves `/share/<token>` public conversation pages; Qubit has no share authority and inventing one is fabricated data. |
| `share.html` | 7,306 | **OMIT** | same reason. |
| `pwa-startup.js` | 3,299 | **OMIT** | Service-worker/PWA install prompt bootstrap. Qubit serves this tree from the dashboard origin, which already has its own SW scope rules; a second SW claiming `/agent/wb/` would fight the dashboard's. |
| `sw.js` | 11,623 | **OMIT** | same reason — the cache list is upstream asset names and would 404-loop. |
| `i18n.js` | 1,948,468 | **PATCH** | 601 `Hermes` occurrences across 17 locale bundles. Only the **en** bundle is rebranded; the other 16 keep their translations verbatim where they do not name the product, and where they do the product name is substituted. Scripted, verified in §4. |
| `manifest.json` | 1,249 | **PATCH** | `name`/`short_name`/`description` + the two shortcut strings. |
| `favicon.svg` | 5,365 | **PATCH** | The mark is a caduceus (Hermes' staff) — a rebranded Qubit page must not ship the old identity. Replaced with the Qubit mark. |
| `favicon-512.svg` | 5,367 | **PATCH** | same. |
| `favicon.ico` | 64,583 | **PATCH** | rasterised from the new mark. |
| `favicon-32.png` | 2,078 | **PATCH** | rasterised from the new mark. |
| `favicon-192.png` | 24,759 | **PATCH** | rasterised from the new mark. |
| `favicon-512.png` | 78,746 | **PATCH** | rasterised from the new mark. |
| `apple-touch-icon.png` | 72,452 | **PATCH** | rasterised from the new mark. |
| `__init__.py` | 56 | **OMIT** | Upstream ships this so `static/` is importable python. Qubit's tree is package data, not a module. |

### 1.2 `vendor/` (63 files)

| Upstream file(s) | Bytes | Disposition | Note |
|---|---:|---|---|
| `vendor/smd.min.js` | (small) | **COPY** | streaming-markdown 0.2.15, pinned. Imported by an ES module in `index.html`. Stays a module import (no CDN). |
| `vendor/katex/0.16.22/katex.min.js` | — | **COPY** | math rendering; loaded lazily by `ui.js`. |
| `vendor/katex/0.16.22/katex.min.css` | — | **COPY** | eagerly linked from `index.html`. |
| `vendor/katex/0.16.22/fonts/*` (60 files: woff/woff2/ttf) | — | **COPY** | KaTeX font set. All WOFF/WOFF2/TTF — already in the asset route's allowlist. |
| `vendor/js-yaml/4.1.0/js-yaml.min.js` | — | **COPY** | frontmatter rendering in messages. |

### 1.3 Assets the HTML references from a CDN (not in upstream `static/`)

`index.html` loads 7 CDN assets from `cdn.jsdelivr.net`: xterm 5.3.0 CSS + JS,
`xterm-addon-fit`, `xterm-addon-web-links`, Prism 2 components + a theme CSS.
Qubit's CSP is `connect-src 'self'` with `script-src 'self' 'unsafe-inline'` —
**a CDN `<script>` is blocked outright**, so every one of these fails and the
composer terminal + code highlighting are dead. Two of the three fix paths
are real:

| Asset | Resolution |
|---|---|
| `xterm@5.3.0` CSS + JS, `xterm-addon-fit@0.8.0` | **VENDOR by copy** into `vendor/xterm/` from the local npm cache (`@xterm/xterm` + `@xterm/addon-fit` are already installed in this repo's `frontend/qubit-web/node_modules`). `terminal.js` only reads `window.Terminal` and `window.FitAddon.FitAddon`, so the classic UMD `lib/xterm.js` drops in unchanged. |
| `xterm-addon-web-links@0.9.0` | **VENDOR by copy** from the same npm cache if present; otherwise **OMIT and leave the option off** (link detection in the terminal only). Must not be faked. |
| `prismjs@1.29.0` core + autoloader + theme CSS | **VENDOR by copy** from the npm cache if present; otherwise **OMIT**, which leaves code blocks unhighlighted but rendered (`ui.js` guards on `window.Prism`). |

Vendored-from-npm files are recorded in `vendor/VENDOR_RECORD.md` with
package name, version, upstream URL, license (all MIT) and the sha256 of the
copied file, exactly as `vendor/katex` is recorded upstream.

### 1.4 Net file count

| Disposition | Files |
|---|---:|
| COPY | 70 (style.css, terminal.js, outline.js, a11y-helpers.js, icons.js + 63 vendor + 2 smd/katex js) |
| PATCH | 22 (index.html, manifest, 11 JS, i18n, 7 icons) |
| OMIT | 6 (login.js, share.js, share.html, pwa-startup.js, sw.js, `__init__.py`) |
| ADDED locally | `LICENSE` (MIT notice), `vendor/VENDOR_RECORD.md`, `QUIRKS.md` |

---

## 2. The endpoint surface — measured, not estimated

### 2.1 How many, and how measured

```
$ grep -rhoE "['\"`](/api/[A-Za-z0-9_/.:{}$*-]*)" --include=*.js --include=*.html static/ \
    | sed -E "s/^['\"`]//" | sort -u | wc -l
188
```

188 distinct **literal** `/api/...` prefixes. Normalising the template-literal
tails (`/api/session/<id>` written as `` `/api/session/${sid}` ``) and dropping
the two non-endpoint fragments (`/api` alone, `/api/share/$`) leaves
**184 distinct endpoints**, which is the denominator for every table below.
The brief's ~165 was an estimate; 184 is the measurement.

> Note one honest caveat: an earlier route-by-route scan against
> `qubit_compat.ROUTES` reported "14 matched". That scan was too narrow — it
> only tested the LITERAL string against compat patterns, and the compat table
> matches a path instance (e.g. `/api/session` is served by
> `/api/session/(?P<id>...)` only for a concrete id). The 6 EXISTING rows in
> §2.4 were then confirmed by matching each endpoint's real request form. The
> number to trust is §2.4's 6, and `_endpoint_table.py` re-derives it.

### 2.2 Verdict summary — 99 answered / 85 GAP of 184

| Verdict | Endpoints | What it means |
|---|---:|---|
| **PORT** | 93 | No Qubit route exists; this port writes a shim handler answering from a real Qubit authority. |
| **EXISTING** | 6 | `qubit_compat.ROUTES` already serves this exact path; the shim passes through to `qubit_compat.dispatch`. |
| **GAP** | 85 | No Qubit authority. Typed refusal (`ok:false` + code + detail naming the missing capability) so the UI renders its own error path. Never an empty list — an empty list reads "you have none", which is a lie. |
| **Total** | **184** | 93 PORT (the 6 EXISTING are counted inside that 93) + 85 GAP, checked in `_endpoint_table.py`. |

**"How many of the 165 work" → 99 of 184** answer real Qubit data; 85 answer a
typed refusal. The brief's ~165 was an estimate; the
measured distinct count is 184 after normalising template tails. Every number
here is reproduced mechanically by `voicebridge/workbench_ui/_endpoint_table.py`
(which prints this very table), so the doc cannot drift from the code.

The config / extension / auth families added by the settings wave account for
the 48 → 73 growth: `settings`, `providers` (+`/delete`), `reasoning`,
`default-model`, the six `extensions/*` paths, `mcp/tools`, and
`auth/status` + `auth/logout` + the four `auth/passkey*` refusals. `auth/status`
is a truthful read of Qubit's own gate; every passkey path is a deliberate typed
refusal because Qubit has no WebAuthn credential store — see §2.5.

The workbench **command families** (`voicebridge/workbench_ui/command_family.py`)
add the five workspace-registry paths the panel had been calling since the port
landed (`/api/workspaces/add|rename|remove|reorder`, plus the refused
`/api/workspace/upload`) and re-bind the four `/api/terminal/*` rows that used to
be a blanket `PTY_NO_SHELL` refusal — see §2.3.2.

The remaining count correction is a MEASUREMENT fix, not new code: seven routes
the gateway already answered (`dashboard/config`, `dashboard/status`,
`health/agent`, `onboarding/status`, `client-events/log`,
`transcribe/capability`, `updates/check`) live in `_EXTRA_PREFIXES` rather than
`PORT_SET` and were being counted GAP. `_endpoint_table.py` now reads them, so
the receipt matches what the server actually answers.

## 2.3 The PORT set (93) — what each maps to

Every handler resolves its dependency INSIDE the call and answers a typed 503
when it is absent (the `workbench_routes.py` rule: a missing capability is a
refusal, never an ImportError at boot). `_endpoint_table.py` asserts this table
has exactly the 93 PORT entries and no others.

| Endpoint | Method | Qubit authority |
|---|---|---|
| `/api/approval/pending` | GET | `approvals.ApprovalJournal(cfg.state_dir()/approvals.jsonl).pending()` → `{pending, pending_count}`. |
| `/api/approval/respond` | POST | `ApprovalJournal.respond` → `dashboard_ops.confirm_op`. |
| `/api/clarify/pending` | GET | no Qubit clarify authority → `{pending:null, clarify_unavailable:true}` (the UI hides its card: honest absence, not a fabricated question). |
| `/api/clarify/respond` | POST | typed refusal — there is nothing to respond to. |
| `/api/crons` | GET | `voicebridge.cron_bridge.list_jobs` → `{jobs:[...]}`. |
| `/api/crons/create` | POST | `cron_bridge.create`. |
| `/api/crons/delete` | POST | `cron_bridge.delete`. |
| `/api/crons/delivery-options` | GET | config delivery channels. |
| `/api/crons/history` | GET | `cron_bridge.runs`. |
| `/api/crons/pause` | POST | `cron_bridge.pause`. |
| `/api/crons/recent` | GET | `cron_bridge.runs` across jobs. |
| `/api/crons/resume` | POST | `cron_bridge.resume`. |
| `/api/crons/run` | POST | `cron_bridge.trigger`. |
| `/api/crons/status` | GET | `cron_bridge.store_path` + job counts. |
| `/api/crons/update` | POST | `cron_bridge.update`. |
| `/api/default-model` | GET/POST | same handler, default-model projection. |
| `/api/file` | GET | `workbench_ui/file_family.file_read` → `voicebridge.files` (bounded, redacted, binary-refusing) confined to the registered project roots. |
| `/api/file/save` | POST | `file_family.file_save` → `voicebridge.file_patch.write_bytes` — the ONE brokered confined-write lane, prior bytes backed up first, receipt (with `backup`) returned. See §2.3.3. |
| `/api/mcp/tools` | GET | existing servers + tools projection. |
| `/api/memory/write` | POST | compat memory-write route. |
| `/api/model/set` | POST | pass-through to existing `/api/model/set`. |
| `/api/models` | GET | `h_config` model inventory with the per-model list (5 references). |
| `/api/personalities` | GET | config personalities list. |
| `/api/personality/set` | POST | config personality write. |
| `/api/plugins` | GET | compat plugin inventory (activation list). |
| `/api/profile/active` | GET | existing `/api/profiles` + active marker. |
| `/api/profile/switch` | POST | existing `/api/model/set` `voice.profile_select` path. |
| `/api/projects` | GET | compat projects route, workbench-shaped projection. |
| `/api/prompts` | GET | config prompt library read. |
| `/api/reasoning` | GET/POST | config reasoning level (5 references). |
| `/api/session` | GET | same adapter, single-session form (`?session_id=`) — 17 references, the most-used endpoint in the UI. |
| `/api/session/archive` | POST | store archive flag. |
| `/api/session/delete` | POST | `QubitSessionStore` delete via the workbench adapter. |
| `/api/session/draft` | GET/POST | store composer-draft field. |
| `/api/session/new` | POST | `POST /api/workbench/sessions` (delegates to `chat_transport`). |
| `/api/session/pin` | POST | store pin flag. |
| `/api/session/rename` | POST | store rename. |
| `/api/session/toolsets` | GET | store toolsets field. |
| `/api/session/update` | POST | store-owned metadata subset; anything else typed refusal. |
| `/api/sessions` | GET | `workbench.adapters_sessions.list_sessions` + `/api/projects` projection — the `sessData`/`projData` pair the rail renders. |
| `/api/settings` | GET | `h_config` + the settings subset the panel reads (13 references). |
| `/api/share/create` | POST | typed refusal `SHARE_UNAVAILABLE`. |
| `/api/skills/content` | GET | skills list + file read through `voicebridge.files` confinement. |
| `/api/skills/toggle` | POST | existing skill toggle route. |
| `/api/terminal/close` | POST | `workbench_ui/terminal_family.close` — drops the agent session (idempotent). |
| `/api/terminal/input` | POST | `terminal_family.input_text` → `chat_transport.AgentPtySession.feed` (one agent turn on Enter). |
| `/api/terminal/resize` | POST | `terminal_family.resize` — records the pane geometry. |
| `/api/terminal/start` | POST | `terminal_family.start` — an **agent-backed** terminal. No shell, no PTY: `PTY_NO_SHELL` still holds for every process-spawning path. Every payload carries `backend:"agent"`, `shell:false`. The pane's `api/terminal/output` EventSource is served as a live SSE stream from the same family. |

#### 2.3.3 The file write verbs and the composer command set

Served by `voicebridge/workbench_ui/file_family.py` and
`voicebridge/workbench_ui/handlers_commands.py`. Every file path is resolved by
`file_family.resolve_workspace_path` — `realpath` inside a REGISTERED project
root, `.` meaning that root, never the process cwd — and every WRITE then goes
through `voicebridge.file_patch`, the ONE brokered confined-write lane (e-stop
latch, `bwrap` confinement probe, atomic write, durable backup). A path that
leaves a root — `..`, an absolute escape, or a symlink pointing out — answers
`WB_WORKSPACE_OUTSIDE` (403) and nothing is read or written.

| Endpoint | Method | Qubit authority |
|---|---|---|
| `/api/file/create`, `/create-dir`, `/delete`, `/save` | POST | `file_patch.write_bytes` / `mkdir_path` / `delete_path`. `create` never overwrites (typed `FILE_PATCH_CONFLICT`); `delete` ARCHIVES into `<state>/file_patch_backups/deleted` and returns `archived_to`, so it is reversible; `save` backs up the prior bytes and returns the receipt. |
| `/api/file`, `/api/file/raw` | GET | `voicebridge.files` through `adapters_workspace.preview_file` / `files.read_bytes`. `raw` is a BYTES route (image/media/PDF/HTML/download behind `workspace.js:291`), claimed by the listener before the JSON dispatcher. |
| `/api/file/path` | POST | the confined absolute path — the value `ui.js:26780` copies to the clipboard. |
| `/api/file/move`, `/rename` | POST | **typed 403 `WB_BROKERED_WRITE`** — `file_patch` has no rename primitive and this port does not grow a second write path beside it. |
| `/api/file/open-vscode` | POST | **typed 501 `WB_CAPABILITY_UNBOUND`** — Qubit never launches an editor from the dashboard. |
| `/api/file/reveal` | POST | typed refusal naming the real path; `WB_NO_DESKTOP_SESSION` on a headless host (the measured case here), `WB_BROKERED_WRITE` where a desktop session exists but launching must still be brokered. |
| `/api/commands` | GET | `workbench_shim.system._command_rows` — the SAME catalog `GET /api/chat/slash-commands` serves (CLI registry + gateway mixins + skill catalog + plugin registry + toolsets) in upstream's row shape. |
| `/api/commands/exec` | POST | `chat_transport.run_qubitchat_slash` — the ONE slash executor `/qubit-chat` and voice use. `data.output` is a real answer or a named miss, never phantom success. |
| `/api/commands/bundles`, `/bundles/resolve` | GET/POST | **typed 501 `WB_CAPABILITY_UNBOUND`** — Qubit bundles are session-scoped runtime objects with no listable registry; an empty list would read as "you have none". |
| `/api/commands/moa/resolve` | GET | **typed 501 `WB_CAPABILITY_UNBOUND`** — no mixture-of-agents resolver exists. |
| `/api/btw` (+ `/api/btw/stream`) | POST / SSE | `chat_transport.stream_turn` on the Qubit brain path, one worker per side question. The QUESTION is appended to the parent session (sidecar marker under `display_metadata.btw`); the ANSWER is delivered only on the ephemeral `btw` stream (`done` / `apperror`), never into the transcript. |

#### 2.3.2 The git / worktree / workspace families

Served by `voicebridge/workbench_ui/command_family.py` (routes) over
`git_family.py`, `git_repo.py`, `git_format.py`, `terminal_family.py`,
`workspace_family.py`, gated by `workspace_scope.py` and executed through
`cmdrun.py`. Every path is proven inside a REGISTERED workspace root before a
command exists to run; outside is `WB_WORKSPACE_OUTSIDE` (403) and nothing
executes. The ported page itself calls only the `terminal/*` and `workspaces/*`
rows (the git family is the coding rail's contract, served here because the
workbench serves the same checkout).

| Endpoint | Method | Qubit authority |
|---|---|---|
| `/api/git/status`, `/log`, `/diff`, `/branches`, `/worktrees`, `/review/rev-parse` | GET | `git_family` over `cmdrun`/`git_repo`, parsers shared with `qubit_cli.web_git`; `.env`/`auth.json` never diffed (`GIT_SENSITIVE_PATH`). |
| `/api/git/stage`, `/unstage`, `/discard`, `/commit`, `/branch/switch`, `/push`, `/pull`, `/worktree/add`, `/worktree/remove` | POST | validate → shared confirm journal → `git.write.<verb>` grant (same journal and grant the original `/api/git/...` compat rows use), `applied:false`. `stage` requires explicit `paths` — no `add -A`. **Not exercised end to end in this port:** every mutation path is verified through the confirmation journal (staged 409 → settled 200 with a grant, nothing applied); the step that would apply the staged command through the brokered opencode worktree flow is not built, which is why `applied` is always `false`. |
| `/api/workspaces` | GET | `voicebridge.projects.list_projects` rows (one per registered folder) + `terminal_remote_backend:false`. |
| `/api/workspaces/add`, `/remove`, `/rename` | POST | `projects.add_folder` / `remove_folder` / `rename`, after the shared confirm journal settles. |
| `/api/workspaces/reorder` | POST | typed `501 WORKSPACE_ORDER_UNSUPPORTED` — the registry has no order field. |
| `/api/workspace/upload` | POST | typed `403 WB_BROKERED_WRITE` — file writes are brokered (`file_patch`). |

#### 2.3.1 The config / extension / auth wave (the 48 → 73 growth)

Each row names the ONE authority it writes through. Every config write goes
through `qubit_cli.config.atomic_config_write` (never a direct file write);
every plugin mutation goes through `plugins_cmd._admit_and_save_plugin_sets`
(the same admission gate `qubit plugins enable/disable` passes).

| Endpoint | Method | Qubit authority |
|---|---|---|
| `/api/settings` | GET | the FLAT panel record: `show_thinking`/`theme`/`default_model`/`reasoning_effort` read from `config.yaml` + `DEFAULT_CONFIG`. The raw `/api/config` tree used to be served here, so every control hydrated from `undefined`. |
| `/api/settings` | POST | `atomic_config_write`, narrow allowlist (`display.show_reasoning`, `display.streaming`, `dashboard.theme`). An unknown field is refused and NOTHING is written. |
| `/api/reasoning` | GET | `agent.reasoning_effort` + `display.show_reasoning` + the effort ladder the core accepts. |
| `/api/reasoning` | POST | `atomic_config_write` for those same two keys — the keys the CLI's `/reasoning` writes. |
| `/api/default-model` | GET | the current `model.default`/`model.provider`. |
| `/api/default-model` | POST | `providers.endpoints.select_model` — the canonical find-or-create-and-activate resolver, same as the SPA. Was answered by the models READ handler, so a pick was silently discarded. |
| `/api/providers` | GET | the `providers` tree of `/api/config`; presence facts only (`has_key`, `key_ref`), never a key value. |
| `/api/providers` | POST | **typed refusal** `WB_CREDENTIALS_NOT_IN_CONFIG` — Qubit stores a reference, never a raw key. |
| `/api/providers/delete` | POST | same refusal. |
| `/api/tools`, `/api/mcp/tools` | GET | the live MCP server set `/api/mcp/servers` serves. `mcp/tools` used to pass through to `qubit_compat`, which has no such route (404). |
| `/api/extensions/status` | GET | `web_server_dashboard._discover_dashboard_plugins` + `plugins_cmd._get_{enabled,disabled}_set`. |
| `/api/extensions/registry` | GET | the installed set, `remote_registry:false` — Qubit has no remote catalog to invent entries from. |
| `/api/extensions/toggle` | POST | `_admit_and_save_plugin_sets` — atomic, refusal-safe (AdmissionRefused → 409, state provably unchanged). |
| `/api/extensions/install`, `/api/extensions/uninstall` | POST | **typed refusal** `WB_BROKERED_INSTALL` — plugin install is `qubit plugins install <owner/repo>`, a pinned/provenance flow, not a posted `download_url`. |
| `/api/extensions/sidecar-proxy-consent` | POST | **typed refusal** — Qubit has no sidecar/proxy layer to consent to. |
| `/api/auth/status` | GET | Qubit's REAL model: `qubit_compat._status_auth_gate` (loopback session token). `password_auth_enabled`/`passkey_feature_flag` are honestly `false`. |
| `/api/auth/logout` | POST | **EXISTING authority** `qubit_compat.h_auth_logout` (revokes loopback session tokens). Routed, not re-implemented. |
| `/api/auth/passkeys`, `/api/auth/passkey/{register,register/options,delete}` | POST | **typed refusal** `WB_AUTH_MODEL_UNSUPPORTED` — Qubit has no WebAuthn credential store. See §2.5. |

### 2.4 The EXISTING set (6) — pass-through

These paths ARE in `qubit_compat.ROUTES` today, so the shim does not
re-implement them: it calls `qubit_compat.dispatch(cfg, method, path, query, body)`
and returns the result verbatim.

| Endpoint | Method | Existing compat route |
|---|---|---|
| `/api/logs` | GET | **EXISTING** `GET /api/logs`. |
| `/api/mcp/servers` | GET | **EXISTING** `GET /api/mcp/servers`. |
| `/api/memory` | GET | **EXISTING** `GET /api/memory`. |
| `/api/profiles` | GET/POST | **EXISTING** `GET/POST /api/profiles`. |
| `/api/sessions/search` | GET | `h_sessions_list` result reshaped to `{results:[...]}`. |
| `/api/skills` | GET | **EXISTING** `GET /api/skills`. |

### 2.5 The GAP set (85) — grouped, with the honest reason

The UI must render its own error for each. None of these get invented rows. Every
shim refusal carries `{ok:false, state:"UNAVAILABLE", code, detail, qubit_counterpart}`
so the panel shows a reason and a pointer, not a spinner.

**Auth is no longer a GAP group.** `auth/status` is a real read of Qubit's own
gate, `auth/logout` routes to `qubit_compat.h_auth_logout`, and the four
`auth/passkey*` paths are PORT_SET entries that answer `WB_AUTH_MODEL_UNSUPPORTED`
— a deliberate, named refusal, not an unported path. That refusal is the whole
point: Qubit's dashboard auth is a loopback session token (and model access is
provider OAuth); there is no `password_hash`, no session cookie and no WebAuthn
credential table. Implementing a passkey register/verify path here would be a
second auth system on the same listener, unreviewed — strictly worse than a 501
that names the gap. `login.js` stays OMITTED for the same reason (§1).

**Extras that ARE answered despite once being GAP:** `extensions/status`,
`extensions/registry`, `extensions/toggle` (real plugin discovery + the one
admission authority), `extensions/install`/`extensions/uninstall` (refused —
code install is `qubit plugins install`), `extensions/sidecar-proxy-consent`
(refused — no sidecar layer), `mcp/tools`, `settings`, `providers`,
`reasoning`, `default-model`.

| Group | Count | Why it is a GAP |
|---|---:|---|
| **Chat / streaming** | 4 | Qubit's turn runs `chat_transport.turn`, which is not an SSE `/api/chat/start` contract. The composer sends and the turn completes, but the token-by-token stream the UI expects has no Qubit emitter. **The single biggest real gap** — chat works only in send-and-reload mode. |
| **Sessions — upstream-only verbs** | 20 | These are upstream's `state.db` verbs (branch, fork, compress, yolo, handoff). `QubitSessionStore` has no such operations, and writing them here would be a second writer on the transcript — the corruption `workbench/__init__.py` exists to prevent. |
| **Kanban** | 12 | Qubit has a dashboard kanban plugin but no kanban REST family at these paths. |
| **Commands & bundles** | 5 | No Qubit command-bundle authority. |
| **Workspaces & project management** | 10 | Qubit's workspace seam is read-only by design; `adapters_workspace.refused_mutation` answers the writes. `/api/workspace/upload` likewise refuses. |
| **Files, mutating (read-only seam)** | 15 | Read-only workspace seam. `preview_file` and the tree ARE live (via `/api/workbench/workspace/*`); every mutation and the VS Code/reveal shell-outs are refused. |
| **Auth** | 0 | Ported. `auth/status` reads Qubit's real loopback-token gate; `auth/logout` routes to `qubit_compat.h_auth_logout`; the four passkey paths are PORT entries answering `WB_AUTH_MODEL_UNSUPPORTED`. No login or second gate is implemented. |
| **Onboarding & updates** | 12 | Qubit's setup lives on the dashboard's own System page; the upstream updater reads a different product's release feed. |
| **Extensions** | 0 | Ported: `status`/`registry`/`toggle` answer from real plugin discovery; `install`/`uninstall`/`sidecar-proxy-consent` are typed refusals naming the real path. |
| **Notes / wiki / insights / goal** | 8 | No Qubit notes/wiki/insights/goal authority. |
| **Rollback / scripts / shutdown / misc** | 8 | Qubit has its own checkpoint surface at a different path; `share` has no Qubit authority. |
| **Settings, upstream-only families** | 20 | Provider quota/cost, auxiliary-model and refresh routes, gateway, git-info and the upstream health endpoints. Provider CRUD is a typed refusal (Qubit stores key REFERENCES, never raw keys); `settings`/`reasoning`/`default-model`/`providers`(GET) are ported. |
| **Total** | **105** | |

**Chat / streaming** (3): `/api/chat/start`, `/api/chat/steer`, `/api/chat/stream/status` (`/api/btw` is answered — §2.3.3)

**Sessions — upstream-only verbs** (20): `/api/session/anchor-scene`, `/api/session/branch`, `/api/session/clear`, `/api/session/compress/start`, `/api/session/compress/status`, `/api/session/compression-recovery/start`, `/api/session/conversation-rounds`, `/api/session/duplicate`, `/api/session/handoff-summary`, `/api/session/import`, `/api/session/import_cli`, `/api/session/lineage/report`, `/api/session/move`, `/api/session/retry`, `/api/session/title/regenerate`, `/api/session/truncate`, `/api/session/undo`, `/api/session/worktree/remove`, `/api/session/worktree/status`, `/api/session/yolo`

**Kanban** (12): `/api/kanban/assignees`, `/api/kanban/board`, `/api/kanban/boards`, `/api/kanban/config`, `/api/kanban/dispatch`, `/api/kanban/events`, `/api/kanban/events/stream`, `/api/kanban/links`, `/api/kanban/links/delete`, `/api/kanban/stats`, `/api/kanban/tasks`, `/api/kanban/tasks/bulk`

**Commands & bundles** — none remain: all five are PORT rows answering real data or a typed `WB_CAPABILITY_UNBOUND` that names the missing concept (§2.3.3)

**Workspaces & project management** (5): `/api/projects/bind`, `/api/projects/create`, `/api/projects/delete`, `/api/projects/rename`, `/api/workspaces/suggest` — the four `/api/workspaces/*` writes and the listing are PORT rows (§2.3.2).

**Files, mutating** — the `file/*` verbs are answered (§2.3.3). Still GAP (5): `/api/escape/authorize`, `/api/escape/file/raw`, `/api/escape/file/read`, `/api/escape/list` (the escape-grant mechanism Qubit does not have — its confinement refuses out-of-root paths instead of granting access to them), `/api/file/office-save` and `/api/folder/download` (no Office round-trip, no folder-archive writer)

**Auth** (6): `/api/auth/logout`, `/api/auth/passkey/delete`, `/api/auth/passkey/register`, `/api/auth/passkey/register/options`, `/api/auth/passkeys`, `/api/auth/status`

**Onboarding & updates** (12): `/api/onboarding/complete`, `/api/onboarding/oauth/cancel`, `/api/onboarding/oauth/poll`, `/api/onboarding/oauth/start`, `/api/onboarding/probe`, `/api/onboarding/setup`, `/api/onboarding/status`, `/api/updates/apply`, `/api/updates/check`, `/api/updates/clear_lock`, `/api/updates/force`, `/api/updates/summary`

**Extensions** (6): `/api/extensions/install`, `/api/extensions/registry`, `/api/extensions/sidecar-proxy-consent`, `/api/extensions/status`, `/api/extensions/toggle`, `/api/extensions/uninstall`

**Notes / wiki / insights / goal** (8): `/api/goal`, `/api/insights`, `/api/notes/item`, `/api/notes/search`, `/api/notes/sources`, `/api/wiki/browse`, `/api/wiki/page`, `/api/wiki/status`

**Rollback / scripts / shutdown / misc** (8): `/api/list`, `/api/rollback/diff`, `/api/rollback/list`, `/api/rollback/restore`, `/api/scripts/list`, `/api/share`, `/api/share/revoke`, `/api/shutdown`

**Settings, upstream-only families** (24): `/api/background`, `/api/background/status`, `/api/client-events/log`, `/api/dashboard/config`, `/api/dashboard/status`, `/api/gateway`, `/api/gateway/status`, `/api/git-info`, `/api/health/agent`, `/api/health/restart`, `/api/model/auxiliary`, `/api/models/refresh`, `/api/profile/create`, `/api/profile/delete`, `/api/provider/cost-history`, `/api/provider/quota`, `/api/providers`, `/api/providers/delete`, `/api/providers/self-hosted`, `/api/skills/delete`, `/api/skills/save`, `/api/skills/usage`, `/api/system/health`, `/api/workspace/upload`

### 2.6 What this means for "does the UI work"

| Surface | State after the port |
|---|---|
| Layout, rail, sidebar, all 11 panels | Mounts — pure DOM/CSS from `index.html` + `style.css`. |
| Session list (rail) | **Live Qubit data** via `/api/sessions`. |
| Opening a session, transcript | **Live** via `/api/session` + `/api/workbench/sessions/<id>/messages`. |
| Workspace panel (file tree, preview) | **Live** via `/api/workbench/workspace/*`. |
| Settings, Models, Memory, Skills, MCP, Logs, Tasks(crons), Approvals | Mixed: the read half is live, the write half is typed-refusal. |
| Composer send | Sends through Qubit's turn; **no token stream**. |
| Kanban, Extensions, Onboarding, Wiki, Notes, Insights | Typed refusal inside their own panel error state. |

---

## 3. How the prefix and the token reach their JS

Qubit's SPA is served at origin root with its API under `/agent`. Their JS
resolves every request against `document.baseURI` and prepends nothing —
`new URL('api/sessions', baseURI)`. So the ONLY thing needed is that
`baseURI` is the shell prefix, and that the token rides a header.

- The served `index.html` gets a `<base href="/agent/wb/">` — the head script
  that computes a `<base>` from `location.pathname` is patched (P1) to use the
  injected `__QUBIT_BASE_PATH__` + the mount instead. Then `api/sessions` →
  `/agent/wb/api/sessions`, `static/ui.js` → `/agent/wb/static/ui.js`.
- The shim route table is registered in `qubit_compat` as
  `_r("GET", r"/api/wb/(?P<sub>.*)", "h_wb_gateway")` + the same for POST —
  i.e. every `/agent/wb/api/<sub>` lands in ONE handler that strips `/wb` and
  dispatches to the port table by `<sub>`. The `webui.py` listener already
  strips `/agent` before `HC.dispatch` (`webui.py:3697-3698`), so the compat
  table sees `/api/wb/<sub>` and this scheme composes with the existing
  prefix handling without touching `webui.py`.
- Token: their pre-paint script already installs a `window.fetch` wrapper that
  attaches a header to same-origin unsafe methods. It is patched (P6) to read
  `window.__QUBIT_SESSION_TOKEN__` and set `X-Qubit-Session-Token` — the SAME
  header the dashboard uses. No second scheme. GETs don't need it from
  loopback, but the wrapper attaches it to every same-origin `/api/` request so
  a non-loopback caller's 401 is uniform.

**Registration lines to add to `voicebridge/qubit_compat.py`** (delivered, not
applied — that file is coordinator-owned):

```python
# Qubit Workbench static UI (see voicebridge/workbench_ui_routes.py).
from .workbench_ui_routes import (wbui_routes as _wbui_routes,
                                  wbui_handlers as _wbui_handlers)
for _m, _p, _h in _wbui_routes():
    _r(_m, _p, _h)
HANDLERS.update(_wbui_handlers())
```

and `"/api/wb"` plus `"/api/wb/.*"` must be reachable through the SPA prefix
allowlist in `webui.py` — that file is also off-limits, so the line to add is:

```python
# in _SPA_AGENT_PROXY_PATHS (webui.py ~:1040), so /agent/wb/api/* is not proxied
# to the fork gateway:
"/api/wb",
```

**Route table (14 rows) the module exports:**

```
GET  /api/wb/api/<sub>        h_wb_gateway_get      (the shim, read)
POST /api/wb/api/<sub>        h_wb_gateway_post     (the shim, write)
GET  /api/wb/static/<path>    (served by webui.py's asset route, not compat)
```

Asset serving cannot go through the compat table (compat handlers return
`(status, json_payload)`). It needs a listener branch, exactly as
`_serve_plugin_app` has one. Since `webui.py` is off-limits, the delivered
registration is a **single branch** to add beside the `/plugin-apps/` branch
(`webui.py:3951`):

```python
if path.startswith("/wb/") or path == "/wb":
    from .workbench_ui_routes import serve_asset
    serve_asset(self, path)      # writes the response on self
    return
```

`serve_asset(handler, path)` in `workbench_ui_routes.py` implements the
confinement + allowlist pattern copied from `_serve_plugin_app` (reject `..`,
resolve, re-check prefix, allowlist by suffix, `LICENSE` by basename, inject
globals into any `.html`). It is fully testable standalone against a fake
handler, which is how the live proof in §6 runs.

---

## 4. Rebrand list — every identifier, global, key, title, string

This repo has a documented history of half-renames leaving dead screens (a
writer renamed, a reader not, or vice versa). So this section is exhaustive by
MEASUREMENT, not by memory. Counts below are from
`grep -rnoi "hermes[a-z0-9_-]*" static/`.

| Rule | What changes | Occurrences | Where | Half-rename risk |
|---|---|---:|---|---|
| **R1** | `window.__HERMES_*` globals → `window.__QUBIT_*` | 12 | `index.html` (config 8, bundle version 2, desktop-companion 1, extension-config 1) | **HIGH** — the writer is `index.html`, the readers are `boot.js`, `extension_settings.js`, `ui.js`. All four must change in one patch or the config silently becomes `undefined` and every upload/CSRF check no-ops. |
| **R2** | `window.HermesAssistantTurnAnchors`, `window.HermesExtensionSettings`, `window.HermesPWA`, `openHermesDashboard` | 32 | `assistant_turn_anchors.js` (5), `extension_settings.js` (23), `pwa-startup.js` (4, omitted), `index.html`+`sessions.js` (`openHermesDashboard`) | **HIGH** — declared in one file, called from another. `openHermesDashboard` is called from BOTH rail and sidebar buttons in `index.html`; if the function renames and the onclick does not, both buttons throw. |
| **R3** | localStorage sessionStorage keys `hermes-*` / `hermes_webui_*` → `qubit-wb-*` | 38 distinct keys | `index.html` (pre-paint: theme, skin, font-size, sidebar-collapsed, workspace-panel, tab-order, hidden-tabs, rtl), `ui.js`, `boot.js`, `sessions.js`, `panels.js`, `commands.js`, `workspace.js`, `extension_settings.js` | **HIGHEST** — the pre-paint inline scripts in `index.html` write/read the SAME keys the deferred JS reads later. A miss means the theme flashes and the sidebar forgets its state on every load. **Every key is rebranded** (not inherited) so a Qubit user's prefs cannot collide with a real hermes-webui install on the same origin. |
| **R4** | HTTP header `X-Hermes-CSRF-Token` → `X-Qubit-Session-Token` | 3 | `index.html` fetch wrapper | **HIGH** — the wrapper is the only place the header is set; the server reads `X-Qubit-Session-Token`. A miss = every POST is 401. |
| **R5** | CSS class `hermes-kanban-md`, `hermes-action-grid`, `hermesExt*`, `hermesTheme`, `hermesNotify`, `hermes_deferred_install_prompt` | 89 | `panels.js` (kanban-md 25 emits + `style.css` rules), `extension_settings.js` (`hermesExt` 9 + camelCase members 23) | **MEDIUM** — class emitted in JS, styled in CSS. A miss renders unstyled markdown. **Verify each class exists in `style.css` before renaming it** — rename only pairs that appear in both. |
| **R6** | DOM ids `hermes-mark` (1), `hermesSessionMeta` (1), `hermes-theme-color` (1) | 3 | `index.html` only (ids + their `getElementById`) | LOW-MEDIUM — `hermesSessionMeta` is read in `panels.js:9261` and defined in `index.html:990`. |
| **R7** | Visible product name `Hermes` → `Qubit` in comments, tooltips, aria-labels, i18n | 947 (case-insensitive `hermes`), of which 601 are in `i18n.js` | everywhere | **MEDIUM** — comments and `data-tooltip` are genuinely user-visible in tooltip mode; `// upstream:` provenance comments are rebranded too and re-attributed in `LICENSE` instead. |
| **R8** | `<title>Hermes</title>`, `apple-mobile-web-app-title`, `manifest.json` `name`/`short_name`/`description`/shortcut strings, `share.html` title (omitted file) | 7 | `index.html`, `manifest.json` | LOW — but a miss shows "Hermes" in the browser tab and the PWA install prompt, the two most visible places. |
| **R9** | `BroadcastChannel('hermes-webui-shutdown')` → `'qubit-wb-shutdown'` | 2 | `boot.js` (open + `shutdownServer` post) | **HIGH** — both in `boot.js`, must move together or cross-tab shutdown stops working silently. |
| **R10** | Service-worker / PWA globals and the `sw.js` registration | 2 | `index.html` (+ omitted `sw.js`) | LOW — removed with the file. |
| **R11** | Brand mark SVG (caduceus) — `aria-label="Hermes caduceus"` and the path data | 3 | `index.html:482` (empty-state logo), `index.html:126` (titlebar), `favicon*.svg` | **MEDIUM** — an old identity shipped in the header is a brand violation, not a cosmetic bug. |

### 4.1 The grep that proves it

After patching, this must print **only** the MIT attribution lines:

```
grep -rni "hermes" voicebridge/workbench_ui/ --include=*.js --include=*.html \
    --include=*.css --include=*.json | grep -viE "LICENSE|THIRD_PARTY|Copyright|hermes-webui Contributors"
```

Zero output is the target. Anything printed is a missed rename, and this doc
records the exact zero-result command that was run (§6).

### 4.2 Half-rename guard (mechanical, not by eye)

Because R1/R2/R3/R4/R9 are writer↔reader pairs, each pair gets a check script
`voicebridge/workbench_ui/_check_pairs.py` that asserts:

1. every `__QUBIT_*` global READ by any JS is WRITTEN by `index.html`;
2. every `localStorage`/`sessionStorage` key READ is WRITTEN somewhere, and
   vice versa (the same symmetric check that catches a key renamed in the
   reader but not the pre-paint writer);
3. every `window.Qubit*` function called by an `onclick=` exists as a declared
   function;
4. every CSS class emitted in JS that matches a `*.classname` pattern has a
   matching selector in `style.css` (and vice versa for the renamed family).

This is the deliverable that makes the rebrand auditable rather than claimed.

---

## 5. What gets patched, precisely (the whole diff surface)

| ID | File | Patch |
|---|---|---|
| **P1** | `index.html` | Replace the self-computing `<base>` script with one that uses `window.__QUBIT_BASE_PATH__ + '/wb/'` (falling back to its own logic when the global is absent, so the file still works standalone). |
| **P2** | `index.html` | Delete the 7 CDN `<link>`/`<script>` tags; point them at `static/vendor/xterm/...` and `static/vendor/prism/...` (§1.3). |
| **P3** | `index.html` | Replace `__MAX_UPLOAD_BYTES__`, `__CSRF_TOKEN_JSON__`, `__CSP_IMG_EXTRA_JSON__`, `__WEBUI_VERSION__` template placeholders with literal values (they are server substitutions that Qubit's injector does not perform). |
| **P4** | `index.html` | Drop the `login.js`, `pwa-startup.js`, `sw.js` references (omitted files). |
| **P5** | `index.html` | Rebrand per R6/R7/R8/R11 (title, meta, ids, mark SVG, aria-labels). |
| **P6** | `index.html` | Rewrite the `window.fetch` wrapper to read `window.__QUBIT_SESSION_TOKEN__` and set `X-Qubit-Session-Token` on same-origin requests (R4). Keep their unsafe-method gate semantics. |
| **P7** | `index.html` | Add `<script>window.__QUBIT_API_PREFIX__=...` fallbacks so a bare file open does not throw. |
| **P8** | `index.html` | Hamster: the inline pre-paint scripts renamed per R3. |
| **P9** | `workspace.js` | The `api()` helper: after building the relative URL, if the caller is on the Qubit shell, prefix the API mount. **This is the single fetch seam** — 0 raw `fetch(` in this file. |
| **P10** | `messages.js` | `_apiUrl()` (line 31) — same treatment. It is a one-line helper used by the two raw `fetch`/EventSource sites. |
| **P11** | `ui.js`, `boot.js` | The 19 raw `fetch(` sites that do not go through `api()`: each already uses `new URL('api/...', baseURI)`, so they follow P1 automatically. Only the two that build an absolute string are patched. |
| **P12** | `boot.js`, `sessions.js`, `panels.js`, `commands.js`, `extension_settings.js`, `ui.js` | R3/R9/R5/R1/R2 mechanical renames. |
| **P13** | `i18n.js` | `Hermes` → `Qubit` inside string values (all 17 bundles); key names untouched. |
| **P14** | `policy.html`? (n/a) | — |
| **P15** | `manifest.json` | R8. |

**No patch touches a component's behaviour**: no panel logic, no renderer, no
message layout, no CSS rule. The port is assets + one URL seam + one header
seam + identifier renames.

---

## 6. Verification plan (what "done" will be proven by)

1. **Mount proof (headless DOM).** Serve the tree from a standalone
   `http.server`-based harness that mounts `workbench_ui_routes.serve_asset`,
   load it in headless Chrome over CDP, and assert in the live DOM:
   - `.layout` exists with `.rail` (≥9 `.nav-tab[data-panel]`), `.sidebar`
     (`#panelChat` active), `#panelChat .composer`, `#msgInner`, the workspace
     toggle, and `#emptyState`;
   - `document.title` is `Qubit`;
   - `window.__QUBIT_SESSION_TOKEN__` is present;
   - zero console errors sourced from our own patched files (upstream
     third-party noise is recorded, not hidden).
2. **Endpoint proof.** For every PORT/EXISTING row, one live request against a
   booted dashboard with the token returns 200 + a JSON body whose top-level
   keys match what the UI reads. Recorded as a table.
3. **GAP proof.** For a sample of GAP rows, the shim returns `ok:false` with a
   code and a detail; the DOM shows the UI's own error element, not a toast
   invented by the shim.
4. **Rebrand grep.** The §4.1 command, output pasted into the final report.
5. **Pair check.** `_check_pairs.py` exits 0.

## 7. Which page wins

`/qubit-workbench` currently serves the hand-written React page. The static
port is a **separate mount** (`/agent/wb/`) until the mount + endpoint proof
above passes. Recommendation recorded here so it is decided, not drifted:
**the static port wins** once §6.1–6.3 are green, because it is the real UI
(11 panels, 40+ sub-panels, the message renderer, the workspace browser) and
the React page is a hand-built approximation of one panel's worth of it. The
React route should then redirect to `/agent/wb/` rather than be deleted, so a
bookmark does not 404.
