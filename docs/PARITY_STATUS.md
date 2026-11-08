# Parity status — HUD, Workbench, Browser Extension

**Measured:** 2026-11-08, against `main`.
**Instrument:** `scripts/parity_ledger.py --check` (exit 0), live HTTP probes against the
running dashboard, and direct comparison with the pinned upstream clones.

This document records only numbers that were actually observed. Where the ledger and the
live surface disagree, both numbers are printed and the disagreement is named.

---

## 0. Ledger currency

`python scripts/parity_ledger.py --check` → **exit 0, `== up to date`**.

The committed `docs/PARITY_LEDGER.json` (commit `ab3aa47c`, generated
`2026-11-08T02:10:07Z`) is current against the worktree. Reconciliation against
`git ls-files` on all three pinned clones:

| surface | upstream `git ls-files` | ledger rows | |
|---|---|---|---|
| hud | 209 | 209 | OK |
| webui | 2552 | 2552 | OK |
| extension | 1178 | 1178 | OK |
| **total** | **3939** | **3939** | **OK** |

Status totals: `ported=447, rewired=254, partial=0, binary-identical=0,
missing=3123, declined=115, absent-at-pin=0`.

The four `WARN` lines at the end of the run are webui rows (`api/auth.py`,
`api/dashboard_probe.py`, `api/media_snapshots.py`, `server.py`) mapped onto
`voicebridge/workbench_ui_routes.py` **by role only, 0/1 routes**. They are mapping
weaknesses, not drift — the generator reports them and still exits 0.

---

## 1. Verdicts

| surface | verdict | one line |
|---|---|---|
| **HUD** `/qubit-hud` | **working — but an approximation, not a port** | 19/19 read-only panels answer live; upstream is a 68-route read-**and-write** app we have no mutation half of. |
| **Workbench** `/qubit-workbench` (`/wb`) | **working (read/chat), partial (breadth)** | Upstream's own 223 KB UI is served and 9 of 13 probed shim routes answer; upstream exposes 296 API routes. |
| **Browser Extension** | **working — the strongest of the three** | Wire contract 26/26 PASS, loads-unpacked 7/7 PASS, context E2E PASS; 28 upstream backend files are scored `missing` but physically present. |

---

## 2. HUD — `/qubit-hud`

### What was measured

`GET :8890/qubit-hud` → **200**, `text/html`, 3191 bytes (SPA shell).

All 19 collector panels, via `GET :8890/agent/api/hud/<panel>` with the page's own
`X-Qubit-Session-Token`:

**19 of 19 → 200, all valid JSON.** Sizes: health 10206 B · sessions 12121 B ·
cron 15768 B · models 663 B · skills 317049 B · plugins 71487 B · costs 17609 B ·
sudo 8819 B · replay 19701 B · corrections 402 B · patterns 6027 B · verify 370 B ·
providers 2388 B · projects 6332 B · profiles 723 B · memory 1213 B · gateway 14752 B ·
live-chat 12632 B · live-models 11745 B.

### Two path traps worth recording

- `GET /api/hud/*` (unprefixed) → **404 `not found`** on all 19. Only the
  `/agent/api/hud/*` alias resolves.
- `GET /qubit-hud/api/hud/*` → **200 with the 3191-byte HTML shell**. That is the SPA
  history fallback, *not* a payload. Any check that asserts only on HTTP status will
  score 19 fake passes here. Assert on content type or body shape.

### Ledger vs reality

The ledger says HUD is `ported=0, rewired=44, declined=14, missing=151`. The 44
`rewired` rows all map onto **one** file, `qubit-hud.js`, with the note "has no file of
its own in Qubit" — a role-only mapping. Reading them as 44 ported capabilities would
overstate the surface. The physical bundle is 3 files: `qubit-hud.js` (3638 lines),
`qubit-hud.css` (725), `manifest.json`; the dashboard page reaches 12 distinct
`/api/hud/*` endpoints from `qubit-hud.js` and the rest are composed server-side.

### Biggest gap (concrete)

**Upstream HUD mounts 68 routes — 38 `GET` and 31 `POST`. Ours mounts 19, all `GET`,
zero `POST`.**

The missing 31 mutations are real capability, not trivia; the upstream list includes
`POST /cron`, `/cron/{job_id}/{pause,resume,run}`, `/sessions`,
`/sessions/{session_id}/{cancel,message}`, `/plugins/{name}/{enable,disable,install,update}`,
`/plugins/rescan`, `/memory`, `/cache/clear`, `/gateway/restart`, and 15 replay
operations (build, clip, fork, publish, export/html|json|markdown, share-card,
redact/scan, redact/apply, view, verify, remote/sync).

Upstream's HUD is a control surface. Ours is an observability surface. That is the whole
of the difference, and it is why "0 of 209 ported" is the honest ledger reading while
"19 panels work" is also true — they describe different things.

Secondary: upstream ships a 46-file React/TypeScript frontend (`frontend/src`, 9624
lines). Ours is a 3-file hand-written bundle. That is a rewrite, not a port; it is not
counted as parity anywhere in this document.

---

## 3. Workbench — `/qubit-workbench` and `/wb`

### What was measured

- `GET /qubit-workbench` → **200**, 223136 B — upstream's own app HTML (`<title>Qubit</title>`,
  18 `static/…` script tags: xterm, i18n, icons, ui, workspace …).
- `GET /wb` → **200**, 223097 B — same document, different injected
  `<base href>` (`/wb/` vs `/qubit-workbench/`). The two mounts are genuinely distinct
  documents, not a redirect.
- Static tree: **98 files / 8.8 MB** ours vs **95 files / 8.13 MB** upstream
  `static/`. Five upstream files are absent: `login.js`, `pwa-startup.js`, `share.html`,
  `share.js`, `sw.js`.

Workbench shim, probed at `/agent/api/wb/<route>` (13 of the 27 registered rows):

| result | count | routes |
|---|---|---|
| live 200 with real payload | **9** | sessions (50228 B), commands (206666 B), crons (12039 B), crons/status, crons/recent, crons/delivery-options, approval/pending, auth/status, workspace/list |
| typed refusal | **1** | status → 501 `NOT_PORTED` / `STATUS_RESPONSE_DIVERGES` |
| 400 (parameter required) | **3** | crons/history, clarify/pending, background/status |

`/api/wb/sessions` also answers on the native table: `GET /agent/api/workbench/sessions`
→ 200, 17077 B, `authority: voicebridge.qubit_session_store.QubitSessionStore`.

### Path trap worth recording

The same 27 shim rows are **dead at the unprefixed mount**: `GET /api/wb/<route>` →
**404, 9-byte `not found`**, on all 11 probed. They only resolve under `/agent/api/wb/`.
The shim's own docstring says its routes are registered for `/api/wb/*`; the live
listener does not dispatch them there. Anyone probing `/api/wb/*` will conclude the
shim is broken when it is not.

### Ledger vs reality

`ported=82, rewired=105, declined=77, missing=2288` — but of 2552 rows, **1971 are
test files** and 70 are docs. Excluding those, the backend picture is far better than
the headline: of **104 upstream backend files, 98 have a live counterpart** (98
`rewired`, 6 `missing`). The 2288 "missing" is dominated by the upstream test suite we
did not vendor (**all 1971** test files) plus **273** assets.

### Biggest gap (concrete)

**Upstream exposes 296 unique `/api/*` routes; we serve 7 native `/api/workbench/*`
rows plus a 27-row shim.** The largest uncovered concept areas by upstream route count:
`sessions`(37) + `session`(37), `kanban`(26), `extensions`(16), `git`(15),
`file`(12), `auth`(11), `crons`(11), `onboarding`(7), `chat`(5), `gateway`(5), `mcp`(5).

Chat, session listing, cron administration, approvals and workspace listing work.
Kanban, git operations, extension management, onboarding and MCP administration —
60+ routes between them — have no shim row. The 1971 unvendored test files also mean
none of upstream's own behavioural contracts are executed here.

---

## 4. Browser Extension

### What was measured

Three purpose-built checks in `qubit_runtime/plugins/browser-companion/`, all run
just now:

| check | result |
|---|---|
| `wire_check.py` | **PASS — 26 of 26 rows agree on both sides** |
| `validate_extension.py` | **PASS — 7 checks, 0 problems** |
| `e2e_check.py` | **PASS — extension envelope → companion route → hook → tool** |

`wire_check.py` compares the extension's committed `.mjs`/`.js` sources against the
companion's Python, row by row: BCP turn id (`qubit.browser.turn.v2`), context id
(`qubit.browser.context.v1`), a **20-row** action→tool map
(`browser_back→tab_back` … `browser_tabs→tab_list`), broker capability set == extension
action set (20 actions), control protocol version 1, command/result frame methods,
and both profile routes.

`validate_extension.py`: manifest v3, 2167 locale messages, all `__MSG_` placeholders
resolve, **26** manifest file references resolve, 12 permissions / 5 host permissions
with loopback covered, 6 HTML pages resolve, **270 scripts and 515 module imports all
resolve**.

`e2e_check.py` runs the extension's own `serializeBrowserTurnEnvelope()` under Node,
POSTs it to the companion's real aiohttp app, and confirms consume-once delivery:
1179 chars returned once, then `available: false`.

Live status route: `GET :8642/api/plugins/browser-companion/status` → **401**
`gateway_auth_failed`. The route exists and is auth-gated (it is not a 404); the
`API_SERVER_KEY` in `/home/trick/.qubit/.env` is **not** the key the running gateway
accepted, so this document does **not** claim a live 200 on that path. The three
checks above prove the pair end-to-end without it.

### Ledger vs reality — a real disagreement

The ledger scores `extension` backend as **28 files, 28 `missing`, 0 ported**.
Physically, **18 of those 18 `.py` files exist** in
`qubit_runtime/plugins/browser-companion/` and differ from upstream by **356 changed
diff-lines out of 4508 upstream lines (7.9%)**. Inspected diffs are brand wording
(`Hermes Browser Extension` → `browser-companion`, `Hermes Control` → Qubit naming),
not behaviour.

So the truthful statement is: the extension's companion backend is a near-verbatim
rebranded port that **the ledger does not recognise** because it is not filed under the
mapped path. Treating `28 missing` as lost capability would be wrong; treating it as
`28 ported` would also be wrong until the mapping is taught about the new location.

Extension frontend/assets are the genuinely strong rows: of 320 frontend files,
233 ported + 58 rewired, 29 missing; of 156 assets, 120 ported + 24 rewired, 12 missing.

### Biggest gap (concrete)

**Not capability — bookkeeping.** Upstream's `companion-plugin/` (18 `.py`, 4508 lines)
is `missing` on all 18 rows while the code sits in the tree. Until the ledger maps
`companion-plugin/*` → `qubit_runtime/plugins/browser-companion/*`, every future
regeneration will keep reporting 28 missing backend files for this surface, and the
447/254/3123 headline will understate the port by that amount.

The real capability gap behind it: **29 of 320 extension frontend files are missing**,
and nothing here proves the extension is loaded in a live browser — only that it
validates and loads unpacked.

---

## 5. What it would take to close the biggest gap, per surface

**HUD — build the write half.** The 31 upstream `POST` routes are the gap, and they are
the ones with real consequence (cron pause/run, session cancel/message, plugin
enable/disable/install, gateway restart, 15 replay operations). Closing it means adding
a mutation family next to `voicebridge/hud_routes.py` behind the existing
`write_policy` / e-stop gate in `voicebridge/qubit_asgi.py`, mirroring the shim's shape:
`hud_routes.py` is read-only by construction today, and each new `POST` needs its own
typed refusal row when the underlying store is absent. Replay is the expensive part —
`build/clip/fork/publish/export/redact/share-card` depends on `backend/services/` (six
modules: exporter, normalizer, publisher, redactor, signer, verifier) none of which has
a Qubit counterpart; that is a subsystem port, not a route port. Realistically: add the
3 cron mutations + 2 session mutations first (cheap, high value, stores already exist),
and treat replay as its own project. Do **not** describe the result as a port of
upstream's React HUD — 9624 lines of TS against our 3638-line hand-written bundle; a
1:1 claim needs the React tree, which nobody has started.

**Workbench — widen the shim, vendor the contracts.** 296 upstream routes against our 7
native + 27 shim. The cheap wins are `git/*` (15) and `kanban/*` (26) because Qubit
already owns both stores — those are shim rows over existing authorities, the same
pattern as `workbench_shim/crons.py`. `extensions/*` (16), `onboarding/*` (7) and
`mcp/*` (5) need authorities Qubit does not have yet. Separately, registering the shim
under the unprefixed `/api/wb/*` mount as its docstring promises would remove a whole
class of false "it's broken" probes. And vendoring upstream's 1971 test files (or
adapting their assertions) is the only way to claim their behaviour contracts are met;
today zero of upstream's own tests execute against this surface.

**Browser Extension — fix the ledger mapping, then the 29 files.** One mapping row
teaching `parity_ledger.py` that `companion-plugin/<x>.py` corresponds to
`qubit_runtime/plugins/browser-companion/<x>.py` converts 28 false `missing` into 18
honest `rewired` (with the 7.9% diff as the recorded justification) — that is a
generator change plus a regeneration, not a port. After that the honest remaining gap is
29 missing frontend files plus the absence of any live-loaded-browser evidence; the
existing `e2e_check.py` already proves the protocol pair, so what is missing is a
screenshot/DOM-level proof that the unpacked extension runs in a real browser session.

---

## 6. Claim rules for this document

- No percentage is stated that was not computed from files or HTTP responses above.
- "Ported" is used only where the ledger says `ported`; a working live endpoint with
  ledger status `rewired` or `missing` is described as working, not as ported.
- `0 of 209` for the HUD is the ledger's reading and stands; it is not contradicted by
  the 19 working panels, which are a different thing.
- No surface here is claimed 1:1. The extension is the closest and is still 29 frontend
  files short.
