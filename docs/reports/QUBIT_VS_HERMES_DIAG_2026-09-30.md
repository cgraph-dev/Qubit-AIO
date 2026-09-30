# QUBIT vs HERMES diagnostic — 2026-09-30

READ-ONLY pass. No product code edited. No reference mutation (reference
dashboard `:9119` and `~/.hermes` touched by GET/file-list only). No secret
values printed anywhere (names only). Scratch report only, per task rules.

- Qubit tree: `/home/trick/Projects/Repos/qubit-voicebridge`, branch
  `voice-final`, HEAD moved `212428e5` → `d6e805a0` mid-pass by a concurrent
  session (see D9). Tree was dirty (voice_edit + tests) at pass start, clean
  after the concurrent commit.
- Services during pass: `qubit-voicebridge active` (MainPID 3487723, started
  2026-09-30 07:16:34 EEST), `qubit-gateway active`, `hermes-dashboard active`
  (pid 19994, untouched).
- Listeners: `0.0.0.0:8890` = Qubit python; `127.0.0.1:9119` = reference
  `hermes`. `curl /` → Qubit `302 Location: /chat`; reference `200`.
- Pins: vendored pin `69948c0057` (v0.21.5) in all four generator constants;
  reference install git HEAD is also `69948c00579`. Tree and reference agree
  on the pin.

## Reference baseline (read-only, all GETs)

| Probe (command) | Observed |
|---|---|
| `find /home/trick/.hermes -maxdepth 4 -name SKILL.md \| wc -l` | 205 |
| `ls ~/.hermes/skills \| wc -l` | 32 category dirs |
| `ls ~/.hermes/hermes-agent/optional-skills \| wc -l` | 24 |
| `ls ~/.hermes/hermes-agent/optional-mcps \| wc -l` | 65 |
| `ls ~/.hermes/hermes-agent/plugin-catalog \| wc -l` | 299 |
| `GET :9119/api/skills?profile=default` (session token) | 153 skills (list) |
| `GET :9119/api/model/info` | `opencode-zen/laguna-s-2.1-free`, eff ctx 256000 |
| `GET :9119/api/model/options` | 8 providers: nous, moa, openai-api, copilot, deepseek, kimi-coding, opencode-zen, opencode-go |
| `grep model/set web_routers/models.py:275` | `@router.post("/api/model/set")` — POST-only, no PUT |
| Reference `qubit_runtime` equivalent dirs | `skills/` 14 dirs, same optional-skills set as Qubit tree |

## Qubit behavior, same commands

| Probe (command) | Observed |
|---|---|
| `qubit skills list \| python -c len` | **189** (NOT 0 — CLI registry is healthy) |
| `find qubit_runtime/skills -maxdepth 2 -name SKILL.md \| wc -l` | 0 at depth 2 (skills live one level deeper: category/skill/SKILL.md); `find runtime/skills -name SKILL.md` = 262 |
| `ls qubit_runtime/optional-mcps \| wc -l` | 65, same names as reference |
| `ls qubit_runtime/plugin-catalog \| wc -l` | 299, same as reference |
| `qubit model` | 3 profiles: qubit-brain4 (ollama, ctx 4096), qubit-model (opencode-zen), qubit-go (opencode-go, ctx 200000) |
| `qubit mcp status` | cgraph-graft configured, invocable |
| `qubit mcp invoke cgraph-graft --tool graft_find_code --arg query=... --confirm` | **state OK**, real graft result payload |
| `qubit plugins invoke builtin.echo --text HELLO_PROBE --confirm` | **state OK**, `echo: HELLO_PROBE` |
| `qubit doctor` | green except `browser_cdp MISSING` (known) |
| All 25 SPA pages (`/chat /sessions /skills /plugins /mcp /models …`) | HTTP 200 (pages render; data comes from `/agent/api/*`) |
| `GET /agent/api/skills` (service, authed) | **`[]`** ← user-visible "0 skills" |
| `GET /agent/api/skills/content?name=antigravity-cli` (service) | **200, real SKILL.md** (detail works, list doesn't) |
| `GET /agent/api/chat/workspaces` service vs scratch | **service 404 / scratch 200** |
| `GET /agent/api/model/options` (service) | `EXECUTOR_ERROR: cannot import name 'LLAMACPP_ALIASES' from 'qubit_cli.providers'` |
| `POST /agent/api/model/set` (no confirm) | `confirm_required: true` (gate works, apply not exercised — would mutate live config) |
| `PUT /api/model/set` | 404 — correct, matches reference POST-only |
| `GET /agent/api/env` | 200, `is_set` flags only, values redacted — secret-safe, correct |
| `GET /agent/api/config` | 200, real config |
| Scratch WebUI on :8899 (HEAD code, same live config+home), `GET /agent/api/skills` | **189, first row antigravity-cli** |

## Defect ledger

| id | capability | surface(s) | symptom (observed) | evidence (command + output) | Qubit file:line root cause | reference behaviour | exact minimal fix | rollback | severity | class |
|---|---|---|---|---|---|---|---|---|---|---|
| D1 | ALL dashboard data (skills list, new routes, model picker) | dashboard `/agent/api/*`, all pages | Service serves `[]` for skills, 404 for routes that exist in tree, ImportError for a symbol that exists in the file — while a scratch instance on the same tree+data serves correctly | `curl -H token :8890/agent/api/skills` → `[]` vs `:8899` → 189 rows; `:8890/agent/api/chat/workspaces` → `404 no compat route` vs `:8899` → 200; `systemctl --user show -p ActiveEnterTimestamp` = 07:16:34, tree files rewritten 07:22–07:23 | service process PID 3487723 predates the worktree state it serves; `voicebridge/webui.py:1588 _qubit_api` + `voicebridge/qubit_compat.py:14878 dispatch` are correct at HEAD (proven by scratch) | n/a (own-goal, not parity) | `systemctl --user restart qubit-voicebridge` (operator, no data change), then re-run the three probes; if any symptom survives, it is NOT stale code — escalate to D2-pattern | restart again; no data touched | CRITICAL | stale-process / multi-writer (restore-era workflow, cf. N34/N35) |
| D2 | Models page picker (`model/options`) | dashboard Models page, `ChatSidebar` picker | `EXECUTOR_ERROR … LLAMACPP_ALIASES` — picker cannot list providers | `curl …/agent/api/model/options` → `{"code":"EXECUTOR_ERROR","detail":"cannot import name 'LLAMACPP_ALIASES' from 'qubit_cli.providers' (…/qubit_runtime/qubit_cli/providers.py)"}` | importer `qubit_runtime/qubit_cli/inventory.py:712` (`from qubit_cli.providers import LLAMACPP_ALIASES, LLAMACPP_PROVIDER_ID`); symbol EXISTS at `qubit_runtime/qubit_cli/providers.py:489` — import resolves a stale/partial module object in the old process | ref `GET /api/model/options` → 8 providers, 200 | first restart per D1 and re-probe; if it persists on fresh boot, fix the import (deferred/local import already — suspect `sys.modules` shadowing between installed `qubit_cli` and tree `qubit_runtime/qubit_cli`; run `python -c "import qubit_cli.providers as p; print(p.__file__)"` in service order to find the shadow) | revert the import edit; restart | HIGH | regression-from-restore (probable stale process; else missing-module shadow) |
| D3 | Plugin load `browser-firecrawl` | plugins (dashboard + CLI) | plugin fails to load at service boot | journal `qubit-voicebridge 07:27:03: Failed to load plugin 'browser-firecrawl': cannot import name 'normalize_scope' from 'qubit_constants' (…/qubit_runtime/qubit_constants.py)` | vendored plugin expects `normalize_scope` the renamed `qubit_runtime/qubit_constants.py` does not export (rebrand-asymmetry from the v0.21.5 rebase `4d819446`) | ref loads its plugin set (no such error; ref binary is the unrenamed tree) | port `normalize_scope` (or its exact upstream definition) into `qubit_runtime/qubit_constants.py` with the rename map, or gate the plugin import; prove with `qubit plugins list` showing the plugin loaded | revert the port; restart | HIGH | regression-from-restore (rebrand asymmetry) |
| D4 | Bare `GET /api/providers` | dashboard Providers page (if it calls the bare path) | `404 no compat route for GET /api/providers` | `curl …/agent/api/providers` → 404; tree has only `qubit_compat.py:6308 GET /api/providers/custom-endpoints` + oauth rows, no bare route | `voicebridge/qubit_compat.py:7979` area (providers section) — bare route never registered | UNVERIFIED this pass (did not enumerate ref routers for a bare `/api/providers`; frontend calls `/api/providers/oauth/*`, 4 sites) | compare `grep -rhoE '"/api/providers"' ref web_routers/`; if ref has it, port the handler; if not, close as parity-OK | n/a | MED | missing-module (to confirm) |
| D5 | `PUT /api/model/set` | dashboard (none — verified no caller) | 404 | `curl -X PUT …/agent/api/model/set` → `no compat route for PUT`; frontend `api.ts:1290` uses POST; ref `models.py:275` is POST-only | none — `qubit_compat.py:7658 _r("POST", r"/api/model/set", "h_model_set")` is correct | identical (POST-only) | none — checked, OK. Do NOT "fix" by adding PUT | n/a | — | non-defect |
| D6 | Skills count CLI 189 vs ref dashboard 153 | CLI/dashboard parity reading | counts differ; user read this as "190+ → 0" together with D1's `[]` | `qubit skills list` → 189; ref dashboard → 153; Qubit tree optional-skills = ref set; runtime home SKILL.md 262 vs ref home 215 | filters differ: vendored `_find_all_skills(skip_disabled=True)` applies `skill_matches_platform/environment/apps` (`qubit_runtime/tools/skills_tool.py`) while the dashboard count the user saw was D1's `[]`, not 189 | ref home has 205 SKILL.md files → 153 after platform filter | after D1 restart, compare `qubit skills list` names vs ref `/api/skills` names with `?profile=` equal; port any genuinely missing skill dir, do not chase the raw number | n/a | MED | migration-gap (to confirm post-restart) |
| D7 | Keys write (`PUT /api/env`), Config save (`PUT /api/config`), model apply (confirm→apply) | dashboard Keys/Settings/Models pages | write paths exist but are UNPROVEN (deliberately not exercised — they mutate live config/secrets) | routes registered `qubit_compat.py:8260/8268`; `POST /api/model/set` returns the honest `confirm_required` gate (`h_model_set`, `qubit_compat.py:1707`) | n/a | ref equivalents are live-mutating too | verify post-restart against a SCRATCH home (`QUBIT_HOME=/tmp/… quint-test` + temp config), never the live home; exact cmds in §Top-10 #4 | restore scratch (delete it) | MED | operator-only (needs isolated proof) |
| D8 | Browser/CDP | inbox/communications, `qubit doctor` | `browser_cdp MISSING 127.0.0.1:9222` | `qubit doctor` → `[MISSING] browser_cdp` | external prerequisite (no chromium/CDP on loopback), not code | ref has CDP-backed inbox | owner decision: name the prerequisite or install chromium with `--remote-debugging-port=9222`; until then it is OPERATOR_ONLY_EXTERNAL, terminal on naming | n/a | MED | operator-only |
| D9 | Worktree integrity during diagnosis | process, not product | tree committed mid-pass (`212428e5` → `d6e805a0`, dirty → clean) by a concurrent session; service predates tree files it serves | `git log` timestamps 07:15–07:23 vs service start 07:16:34; `git status` changed between probes | concurrent opencode sessions on one worktree (known N34/N35, still open) | n/a | single-writer freeze before any repair wave: `git status` clean + `systemctl restart` + re-probe D1–D3 before touching code | n/a | HIGH (process) | multi-writer hazard |

Proven WORKING (do not "repair"): MCP invoke real tool call (state OK +
graft payload); plugin `builtin.echo` invoke (byte-exact echo); `POST
/api/model/set` confirm gate; `/api/env` redaction (`is_set` only);
`/api/skills/content` detail; all 25 SPA page shells (HTTP 200); CLI skills
registry (189); optional-mcps 65/65 and plugin-catalog 299/299 identical to
reference; no `~/.hermes` runtime fallback in code (`qubit_runtime.py:214`
`pin_qubit_home` fail-closed; `orchestrator.py:2739` legacy `.env` never
consulted); editable dev install (no packaging involved in these symptoms).

## Top-10 ordered repair plan

1. **Restart `qubit-voicebridge.service` and re-probe D1** (`/api/skills`
   length, `/api/chat/workspaces` status, `/api/model/options` status). Why
   first: one no-data-change action discriminates all three headline symptoms;
   scratch already proves HEAD code serves correctly.
2. **If D2 survives restart, resolve the `qubit_cli` shadowing**
   (`inventory.py:712` vs `providers.py:489`): print `qubit_cli.providers.__file__`
   in service import order; fix the path priority in `qubit_runtime.ensure_path`.
3. **Fix D3 `normalize_scope`** in `qubit_runtime/qubit_constants.py` (port
   upstream definition through the rename map); prove `browser-firecrawl`
   loads in journal + `qubit plugins list`.
4. **Prove the write paths on a scratch home** (D7): `PUT /api/env` key round-trip
   (`is_set` flip, value never served), `PUT /api/config` save, `POST
   /api/model/set` with confirm → apply → `GET /api/model/info` reflects the new
   model and the next turn's provider matches. Never against the live home.
5. **Close D4** by enumerating the ref bare `/api/providers` route; port or close.
6. **Reconcile D6 counts** post-restart (name-level diff, equal `?profile=`).
7. **Single-writer freeze** (D9): no concurrent sessions during repair;
   regenerate `parity_inventory`/`voice_coverage` `--check` after restart.
8. **Wheel/sdist clean-venv proof** (not run this pass): `python -m build`,
   install wheel into throwaway venv, assert `qubit doctor`, skills/MCP/plugin
   loads, dashboard serves — maps each runtime load in `pyproject.toml`
   force-include to packaged `_data` (G in the task brief).
9. **Voice intents for the repaired surfaces** (model switch, skills toggle,
   MCP invoke, plugin install) — registry entries + live utterance proof.
10. **Independent re-probe of this whole report** after 1–9 (second pair of eyes
    before any milestone claim).

## Why it regressed (restore/rebase narrative)

The public `cgraph-dev/Qubit-AIO` restore put the tree on the v0.21.5 rebase
(`4d819446` replaced `qubit_runtime/` wholesale). Two rebase failure modes are
visible in this pass: (a) **rebrand asymmetry** — identifiers renamed at the
definition site but not at use sites, or vice versa (`normalize_scope` missing
from `qubit_constants.py`, D3; the `LLAMACPP_ALIASES` import failure pattern,
D2); (b) **multi-writer drift** — concurrent sessions committing and touching
files under a live service (07:15–07:23 commits + mtimes vs 07:16:34 service
start; mid-pass commit `d6e805a0`), so the running process no longer matches
any tree state and the dashboard degrades to `[]`/404s that look like data loss
but are not (D1). The skill/MCP/plugin DATA is intact everywhere (189 CLI,
65/65 MCPs, 299/299 catalog, 262 SKILL.md on disk) — nothing was deleted.

## Could not reproduce / not proven (and why)

- Live model-switch APPLY changing the next turn's provider (would mutate live
  config; needs scratch-home proof, D7).
- Keys/Settings writes (same reason, D7).
- Voice-utterance paths (no audio capture in this environment; registry only).
- Clean-install wheel/sdist behaviour (no build run this pass; dev venv is
  editable so packaging cannot explain these symptoms).
- Reference CLI reads (`hermes model`) — reference dashboard GETs used instead
  to avoid any risk to the read-only reference; sufficient for this ledger.
- Full per-control E2E of all 25 pages (page shells verified 200; control-level
  E2E is the W5 workstream, needs the M4 matrix).

## Verdict: PARTIALLY WORKING

Core is healthy (CLI registry 189 skills, MCP real-call OK, plugin invoke OK,
reads + redaction + confirm gates correct, data intact, packaging/install not
implicated). The dashboard *appears* dead because the live service process
predates the tree it serves (D1: skills `[]`, new-route 404s) and the Models
picker is down with it (D2). **Single highest-impact next change:
`systemctl --user restart qubit-voicebridge` (operator, zero data change),
then re-run the three D1 probes — expectation, from the scratch-instance
proof, is skills 189, workspaces 200, model/options 200.**
