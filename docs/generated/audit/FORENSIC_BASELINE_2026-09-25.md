# Qubit vs. upstream Hermes — forensic audit

**Independent, read-only audit.** Generated 2026-09-25T05:45:18Z.
Audit directory: `/tmp/qubit-hermes-forensic-audit-20260925-054518/`.
No product file, service, database, config, credential, or runtime home was
modified; no secret value is reproduced here.

**Final verdict: `READY_FOR_REPAIR`.**

---

## 1. Identity and baseline

| Item | Value |
|---|---|
| Hermes repo | `https://github.com/NousResearch/hermes-agent` |
| Hermes branch | `main` |
| Hermes commit (remote HEAD, cloned) | `8623cd4a8403f020090c9c3e8feae943b4dac55e` |
| Hermes version | `v0.21.5+2053.g8623cd4` (2026.9.24), `describe v2026.9.24-2053-g8623cd4a84` |
| Local Hermes install | `/home/trick/.hermes/hermes-agent` @ `8623cd4a…` (matches remote HEAD) |
| Local Hermes runtime | bundled Python 3.11.15, OpenAI SDK 2.24.0 (dashboard on `:9119`) |
| Qubit HEAD | `badbb2756dd7b24a2acb30ccc50725d82f2cda37` |
| Qubit branch | `freeze/wave12-2026-09-23` |
| Qubit dirty tree | 9 modified + 5 untracked (concurrently edited) |
| Qubit product version | `1.0.0` (`pyproject.toml`) |
| Qubit vendored runtime | `qubit_runtime/`, declared pin `d337b736…` = **v0.21.4** |
| Vendored version metadata | reports **`0.21.1`** (`qubit_runtime/pyproject.toml:5`, `qubit_cli/__init__.py:6`) — stale |
| Qubit venv Python | 3.12.3 |
| Node / npm | v22.22.3 / 10.9.8 |
| Live services | `hermes-dashboard`, `openclaw-gateway`, `qubit-gateway`, `qubit-voicebridge` (all active) |
| Live ports | `0.0.0.0:8890` Qubit (pid 1390888); `127.0.0.1:9119` reference Hermes (pid 1425712) |
| Qubit remote | `https://github.com/cgraph-dev/openclaw-voice-bridge.git` (a fork; **not** upstream) |

> **Framing correction.** The audit request assumed "Hermes works while Qubit does
> not." The evidence does not support that as a blanket statement: on this host
> both stacks run (Qubit webui + gateway + agent turn; Hermes dashboard), and in
> the clean-room install Qubit installed and ran from wheel while the source
> Hermes install failed at runtime (`ModuleNotFoundError: ruamel`, see §7). What
> *is* reproduced is a finite set of specific Qubit defects and upstream-version
> gaps, enumerated below.

---

## 2. Method and limits

- Official Hermes cloned fresh into the audit dir (network available, HEAD
  recorded). Local install used only as read-only runtime evidence.
- Five independent workstreams: (A1) source/module + CLI + gateway + agent loop;
  (A2) providers/models/context + toolsets + skills + plugins; (A3) MCP +
  sessions + config/env + cron/hooks/channels/webhooks/pairing/files;
  (A4) runtime/config forensics + fresh-install matrix + real catalog invocation;
  (A5) frontend + packaging/update + rebrand + future breakage.
- Every finding carries Hermes file:line, Qubit file:line, a repro, observed
  output, severity, classification, and cause type.
- **Limits:** the reference dashboard `:9119` is session-token gated with a
  process-memory token, so its live API was not called; reference counts come
  from source/home enumeration. The Qubit worktree is being edited concurrently,
  so generated-artifact staleness is measured against a moving tree. No network
  was used for the fresh-install runs (staged beforehand). The Hermes clean-room
  install failure may be partly an artifact of an interrupted first setup run
  (recorded, with exact output).

---

## 3. Source inventory and module differences

| Metric | Hermes `8623cd4a` | Qubit `qubit_runtime/` |
|---|---:|---:|
| Tracked files | 16,077 | 14,438 |
| Python files | 7,676 | 6,582 |
| Root Python modules | 51 | 46 |
| `agent/` modules | 308 | 301 |
| `gateway/` modules | 174 | 172 |
| CLI `add_parser` names (AST) | 180 | 176 |
| Vendored tests (`.py`) | 5,324 | 4,450 |

**Delta vs. latest (mapped through the `hermes_*`→`qubit_*` rename map):**

- `mapped_missing_total = 2871`; **`unexplained_missing = 0`**.
- Of the missing, `1796` are new in 0.21.5 (post-pin upstream), `1075` are
  intentionally omitted (tests + product styling).
- `extra_in_qubit_tracked = 1232` = 828 deliberate Qubit additions + 404 files
  upstream removed after 0.21.4 (stale-but-present vendored copies).
- The vendored tree is a **complete superset of its declared pin `d337b736`
  (v0.21.4)**; the gaps are against the newer 0.21.5 HEAD.

**Module differences vs. latest (representative, all post-pin):**
`pm/` (46 py), `hermes_platform/` (12 py), `qubit_yaml.py`,
`qubit_state_health.py`, `qubit_constants_scratch.py`; 65 `qubit_cli`, 22 `tools`,
8 `agent`, 7 `tui_gateway`, 2 `gateway` modules. **No Qubit source imports any of
them** (grep = 0) → feature gap, not a runtime break.

**Qubit additions:** `voicebridge/gateway_daemon.py` + `gateway_supervisor.py`
(Owner Decision #16 owned gateway); `qubit_cli/{dep_ensure,plugin_index,
plugin_python_deps,update_cmd_deps,gateway_service_unit,…}.py`.

**Retained-but-upstream-deleted:** `agent/ssl_guard.py` (imported at
`qubit_runtime/agent/agent_init.py:939`; upstream removed it at HEAD).

---

## 4. Capability totals (real code paths, not static registries)

| Surface | Hermes reference | Qubit live | Notes |
|---|---:|---:|---|
| Toolsets (dashboard) | 61 static | 29 dashboard / 60 static | Qubit static 60 vs 61 |
| Registered tool names | 101 | 101 | parity in registry |
| Default `hermes-cli` bundle | **59 tools** | **53 tools** | missing 6 (A2-TS-01) |
| Skills (`SKILL.md`) | 208 bundled / 214 home | **197 resolve** | Qubit 213 bundled+optional on disk |
| Plugins (API) | 19 bundled dirs | **10 dashboard** | only 2 in-process handlers invocable |
| MCP servers configured | 2 | **1** (`cgraph-graft`) | reference 2nd is a dead path (A4-11) |
| Providers | 47–48 | **5 live slugs** | moa, copilot, opencode-zen/go/free |
| Models (catalog) | 83 provider files | **192** | opencode-go cache 64 / zen 80 |
| Effective model / provider | `laguna-s-2.1-free` / `opencode-zen` | `deepseek-v4-flash` / `opencode-go` | Qubit context reported 200,000 |
| CLI subcommands (AST) | 180 | 176 | missing `adopt`, `trust-update-url`, `check-updates`, `screen` |
| Compat routes | ~245 literals | **449 live / 448 artifact / 69 gated** | parity artifact: 1378 rows 1249 PORTED / 129 GATED / 0 MISSING |
| Voice registry / rows | n/a | **1137 registry / 1378 rows / 1185 real (85.99%)** | 193 audited guards, 0 unresolved, 0 dead backends |
| Reasoning capability | from models.dev | **hardcoded false** | BROKEN (A4-01/02) |

**Real catalog invocations (safe):** `GET /agent/api/tools/toolsets` → 200
(29 toolsets / 78 entries); `GET /agent/api/skills` → 200 (197); `GET
/agent/api/dashboard/plugins` → 200 (10); MCP connect/list/disconnect on an
isolated fake stdio server → resources 2 / prompts 1 / resource-templates 1;
`builtin.echo` plugin invoke → `state:OK`. Heremes reference invocation could not
run (token-gated).

---

## 5. Findings by severity (107 total: 11 HIGH / 40 MED / 51 LOW / 5 INFO)

Full machine list: `CAPABILITY_COMPARISON.json` (107 rows). Repros:
`REPRODUCTIONS.md`. Summary:

### HIGH (11)

| ID | Title | Hermes | Qubit | Class |
|---|---|---|---|---|
| A1-oneshot | `qubit -z` false-success: interrupted→0, `failed`→1 not 2 | `hermes_cli/oneshot.py:89,92-107,340` | `qubit_cli/oneshot.py` (no `_oneshot_exit_code`) | BROKEN |
| A3-01 | `qubit mcp test` leaks credential material (first4+last4, raw exception) | `hermes_cli/mcp_config.py:120-180,809` | `qubit_cli/mcp_config.py:604-605,608` | MISSING |
| A4-01 | `/api/model/info` fabricates `supports_reasoning:false`, `max_output:1024`, `vision:false` | `hermes_cli/web_routers/models.py:79-82` | `voicebridge/qubit_compat.py:1273-1275` | BROKEN |
| A4-02 | Reasoning selector permanently disabled | models.py capability source | `ChatPage.tsx:493` | BROKEN |
| A2-TS-01 | Default CLI bundle omits 6 registered tools (53 vs 59) | `toolsets.py:22,39-40` | `toolsets.py:11-36` | BROKEN |
| A2-PM-01 | Context length ignores provider-declared windows | `model_metadata.py:2211` | `model_metadata.py:1849-1907` | BROKEN |
| A2-PM-02 | Stale `DEFAULT_CONTEXT_LENGTHS`; live model 128000 not 1,000,000 | `model_metadata.py:290-412` | `model_metadata.py:324-403` | MISSING |
| A4-04 | Vendored default home `~/.hermes`; import materializes it | `hermes_constants.py` default | `qubit_constants.py:43-51` | MISCONFIGURED |
| A5-04 | Update/README repo identity ≠ configured origin | n/a | `update_channel.py:58`, `README.md:98` | BROKEN |
| A5-05 | Legacy-source URL guard omits `openclaw` | n/a | `update_channel.py:72` | MISCONFIGURED |
| R-01 | Runtime error tells users to store credentials in `~/.hermes/.env` | n/a | `qubit_cli/auth.py:1465-1466` | MISSING |

### MEDIUM (40, selected)

- **MCP/config:** `tools.include: []` collapses to no filter (`mcp_config.py:63-71`);
  dashboard MCP-test body unredacted (`qubit_compat.py:2297-2320`); OAuth/catalog
  routes gated over real vendored handlers (`qubit_compat.py:7537-7548`).
- **Sessions/state:** `qubit_state_health.py` latch absent; `qubit_time.py` lacks
  surrogate-safe strftime; home 0o700 skipped under symlinked ancestors
  (`config_home.py:31`).
- **Secrets:** routed-profile credential isolation absent (`agent/secret_scope.py`
  199 vs 401 lines); 70 config keys / 4 env names absent.
- **Operations:** two webhook implementations; channel onboarding gated
  (`ONBOARDING_GUIDED`); profile-scoped webhook routes/cron modes missing.
- **Providers/models:** OpenRouter routing-variant stripping absent; Codex/Bedrock
  context handling diverged; provider registry lacks home-layer/aliases.
- **Toolsets/skills:** `setup` toolset missing; toolset memo lost per-profile scope
  key; platform bundles still `hermes-*`; skill reader only checks
  `metadata.hermes`; `requires_apps` gating absent.
- **Plugins:** only 2 of 72 catalog entries invocable; pinned index serves
  `NousResearch` URLs/maintainer.
- **Runtime/forensics:** `auto_context_length` conflated with effective; live
  config toolset `Qubit-cli` resolves 0; stale generated artifacts;
  `qubit gateway status` host-global; version metadata stale; partial updater
  (7 vs 30 modules); generators hardcode `/tmp/opencode/pinned`; unpinned deps;
  no installer checksums; two services share one `state.db` (`journal_mode=DELETE`,
  2 s busy timeout).

### LOW (51) / INFO (5)

Post-pin module/CLI/test gaps, `qubit-acp` missing, `agent/ssl_guard.py`
retention, platform installers missing, internal-doc naming, verified-OK
negatives (single session store, N17 fallback removed, bundle not stale, all 108
UI API literals resolve, MCP resources/prompts real).

---

## 6. Root causes (separation of concerns)

| Cause type | Examples |
|---|---|
| **Code defect (real bug)** | oneshot exit codes; `mcp test` leak; fabricated model capabilities; 6 missing default tools; provider-profile context lookup absent; stale context table; `tools.include:[]`; gateway status not home-scoped; plugin/platform IDs. |
| **Configuration defect** | live toolset `Qubit-cli` (invalid); `journal_mode=wal` vs actual `delete`; update repo identity; legacy-guard omission; version metadata stale. |
| **Missing dependency / port not done** | updater recovery modules; `qubit_state_health`; symlink hardening; secret-scope isolation; post-pin `pm/`, `hermes_platform/`, `qubit_yaml`. |
| **Operator prerequisite** | `browser_cdp` (`127.0.0.1:9222`) absent — `doctor` rc=1; voice ASR/TTS/voice-ID models fetched on first run. |
| **Deliberate design difference** | typed refusals for raw PTY/console/secret reveal/remote fetch; `/chat` = terminal, `/qubit-chat` = native; Qubit-owned extra-key home; `openclaw` fork remote; two-chat-surface split. |
| **Docs overclaim** | N29 "1,000,000 vs 200,000" is not reproducible from Qubit's vendored code (registry yields 128000). |

The single most consequential root cause is **upstream-version drift**: Qubit
vendors v0.21.4 while the reference is v0.21.5, so provider/model resolution,
toolset definitions, and security hardening added upstream are absent. The most
consequential *Qubit-owned* bug is the hardcoded model capability block
(`qubit_compat.py:1273-1275`), which silently breaks the reasoning surface.

---

## 7. Fresh-install comparison (clean-room, isolated HOME/QUBIT_HOME)

Full matrix: `FRESH_INSTALL_MATRIX.md`.

| Step | Hermes result | Qubit result | Match |
|---|---|---|---|
| Install | setup claimed "✓ Setup complete"; publishes `hermes`/`hermes-acp` | wheel installs 87 deps, `qubit 1.0.0` | PARTIAL |
| Status | **`ModuleNotFoundError: No module named 'ruamel'` rc=1** | runs; backend enabled; ASR/TTS/voice-ID FAILED (models absent) | **Hermes FAIL** |
| Doctor | same `ruamel` traceback rc=1 | 11 OK / 1 MISSING (`browser_cdp`), rc=1 | **Hermes FAIL** |
| Plugins/skills/MCP/agent CLI | blocked | real catalog; full 71-command agent CLI | Qubit-only |
| Gateway | n/a | `gateway status` **host-global** (reports live pid/port) — A4-10 | defect |
| Bundle | n/a | wheel ships `frontend/qubit-dist` + `ui-tui/dist/entry.js` | Qubit PASS |

**Verdict:** Qubit fresh install **PASS** (install+run); Hermes source install
**FAIL at runtime** (incomplete dep env; recorded, possibly aggravated by an
interrupted first setup). Voice is `ENVIRONMENT_OR_CREDENTIAL_REQUIRED`;
`browser_cdp` is an operator prerequisite.

---

## 8. Rebrand audit

Full data: `REBRAND_LEAKS.json`. Live file counts (repo, excluding `.git`/
`node_modules`/`.venv`/`graft`): `Hermes` 864, `HERMES` 1511, `hermes` 4311,
`NousResearch` 403, `Nous Research` 28, `NousPortal` 33, `OpenClaw` 74,
`openclaw` 136.

**Unapproved public leaks (fix):** R-01 `auth.py:1465-1466` `~/.hermes/.env`;
R-02 `uninstall.py:459` "Run 'hermes'"; R-03 `_parser.py:87` `hermes-agent-dev`;
R-04 `auth_commands.py:798`; R-05 served `frontend/qubit-dist/plugins/fleet.json`
id `hermes-achievements`; R-06 `gateway.py:5078,5388`; R-07 `proxy_cli.py:160`.

**Required compatibility identifiers (do NOT rename without an adapter):**
`X-Hermes-Session-Token`, `window.__HERMES_SESSION_TOKEN__`, `HERMES_HOME`/
`HERMES_*` env, `NOUS_*`, `hermes-index`, `/api/hermes/update*`,
`/api/plugins/hermes-achievements/*`, `metadata.hermes`, `hermes-cli` toolset id,
persisted session-source keys.

**Legal/provenance (keep):** `LICENSE`, `THIRD_PARTY_NOTICES.md`, generated
manifest `upstream_repo`/`upstream_license`, `@nous-research/ui`.

**Migration-only aliases:** legacy-named home/name adoption fallbacks — safe to
retire after a migration window. Deep rebrand is far from complete (4,311
lowercase `hermes` files; `apps/` 1,171, `website/` 565, `qubit_cli/` 255).

---

## 9. Future-breakage audit

Full report: `FUTURE_BREAKAGE_AUDIT.md`. Top items: four-way version skew
(product 1.0.0 / runtime 0.21.1 / actual pin v0.21.4 / desktop 0.17.0); update
identity + guard; partial updater (no abort/restart recovery); stale generated
manifests; two services sharing one `state.db` with DELETE journaling and a 2 s
busy timeout; generators pinned to ephemeral `/tmp/opencode`; latent `~/.hermes`
fallbacks (`env_loader.py:319,569`, CLI history `.hermes_history`); unpinned
top-level deps; installer downloads without checksums (Hermes verifies the uv
tarball by sha256); Linux-only install; catalog drift between the SPA bundle and
vendored plugin dashboards. Well-guarded (no action): service-restart race
handling, rollback reversibility, migration secret handling.

---

## 10. Repair plan (severity-ranked)

Full plan: `REPAIR_PLAN.md`. **P0** = the 11 HIGH items above. **P1** = 40
MEDIUM (redaction, isolation, metadata, version bump, updater, reproducibility,
DB policy, artifact regeneration, de-brand strings). **P2** = LOW post-pin
modules, CLI names, `qubit-acp`, platform installers.

### Files that must change

`qubit_runtime/qubit_cli/{oneshot,mcp_config,auth,uninstall,_parser,auth_commands,gateway,proxy_cli,config_home,config_defaults}.py`,
`qubit_runtime/agent/{model_metadata,secret_scope}.py`,
`qubit_runtime/{toolsets,qubit_constants}.py`,
`voicebridge/{qubit_compat,update_channel,plugin_catalog}.py`,
`voicebridge/data/plugin_index_pinned.json`, `frontend/qubit-web/src/pages/ChatPage.tsx`,
`frontend/qubit-dist/` (rebuild), `README.md`, `docs/UPDATE.md`,
`scripts/{source_inventory,hermes_vendor_manifest,hermes_byte_manifest,install.sh}`,
`pyproject.toml`, `qubit_runtime/pyproject.toml`, `qubit_runtime/qubit_cli/__init__.py`.

### Changes that must not be made

Wire/env/DB/registry identifier renames without an adapter; edits to `LICENSE`/
`THIRD_PARTY_NOTICES.md`/manifest provenance; edits to frozen archives or
`docs/differential/INTERFACE.md`; implementing raw PTY/secret-reveal/unvetted
fetch to "match" a gated route; destructive changes against the live worktree or
reference install.

### Reproduce

Every finding's command is in `REPRODUCTIONS.md`. Quick set:

```bash
rg -n '_oneshot_exit_code' qubit_runtime/qubit_cli/oneshot.py        # expect none
rg -n 'redact_mcp_probe_text' qubit_runtime/qubit_cli/mcp_config.py  # expect none
curl -s -H 'X-Api-Token: <redacted>' http://127.0.0.1:8890/agent/api/model/info
.venv/bin/python scripts/parity_inventory.py --check                 # stale
git -C /home/trick/Projects/Repos/qubit-voicebridge remote -v
```

---

## 11. Final verdict

**`READY_FOR_REPAIR`.**

- 107 differences classified; **11 HIGH reproduced** → not
  `NO_REPRODUCED_DIFFERENCE`.
- `unexplained_missing = 0`; the foundation is complete against its declared pin;
  no reproduced condition blocks repair → not `NOT_READY_FOR_REPAIR`.
- No parity, completeness, or verification is claimed. No milestone (`M1`–`M4`,
  `VERIFIED`) is asserted. The worktree was not modified during the audit; the
  only file written to the repo is this report.

### Required outputs (audit dir)

`FINAL_REPORT.md`, `SOURCE_INVENTORY.json`, `CAPABILITY_COMPARISON.json`,
`CONFIG_AND_ENV_COMPARISON.json`, `RUNTIME_FAILURES.json`,
`FRESH_INSTALL_MATRIX.md`, `REPRODUCTIONS.md`, `REBRAND_LEAKS.json`,
`FUTURE_BREAKAGE_AUDIT.md`, `REPAIR_PLAN.md`, `AUDIT_BRIEF.md`, `reports/`.
