# Qubit Dashboard Control Audit — 2026-09-21

**Status:** read-only, evidence-based audit. NOT completion evidence and NOT a
verification claim. Read alongside
[QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md](QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md)
and the page plan
[QUBIT_PAGE_BY_PAGE_PARITY_EXECUTION_PLAN_2026-09-21.md](QUBIT_PAGE_BY_PAGE_PARITY_EXECUTION_PLAN_2026-09-21.md).

**Method:** direct inspection of the live worktree (backend routes/handlers,
SPA control call sites, wire shapes) plus focused backend tests in a hermetic
XDG/HOME tree. The machine artifact
`docs/QUBIT_PAGE_COVERAGE_2026-09-21.json` was read, not regenerated or
hand-edited.

**Scope of this audit:** the System/Gateway/Update controls and the
Skills/MCP/Plugins pages (plan sections 7, 8, 9, 16).

**Verdict vocabulary.** The coverage artifact's rules are binding: no control
is `IMPLEMENTED` without an observed end-to-end chain. Below, **wired** means a
real SPA call site resolves to a real registered backend handler with the
documented wire shape (static + backend-dispatch evidence). **OPEN** is the
artifact disposition for every row (0 IMPLEMENTED) because no real-backend
browser/E2E chain has run. **Gate/absent** means the route is a typed refusal
or the handler does not exist yet.

---

## 1. Coverage artifact facts (authority)

From `docs/QUBIT_PAGE_COVERAGE_2026-09-21.json` (read, not regenerated):

- `generated_from_revision`: `f984911c81e119fe6fe1220510b8ac324c1224a4`
- `total_rows`: **3353** — **3199 OPEN**, **154 SAFE_EQUIVALENT**,
  **0 IMPLEMENTED**, 0 OPERATOR_ONLY
- `browser_or_e2e_verification_run`: **false**
- The artifact states explicitly: "NO browser, Playwright, or end-to-end
  verification was run for this artifact. All dispositions are source-level.
  No row is claimed IMPLEMENTED."
- **Count moved during this audit.** At task start the artifact read
  **3198 OPEN / 155 SAFE_EQUIVALENT**; another agent regenerated it
  (mtime 2026-09-21 22:34) and it now reads **3199 OPEN / 154
  SAFE_EQUIVALENT**. Whatever the exact figure, roughly 3.2k rows stay OPEN
  and no E2E has run. This audit did not regenerate it.

Per-area OPEN counts (current artifact):

| Plan section | Area id | OPEN rows |
| --- | --- | --- |
| 16 | `system` | 157 |
| 7 | `skills` | 81 |
| 9 | `mcp` | 38 |
| 8 | `plugins` | 67 |

The ~3199 OPEN rows are the whole-dashboard backlog (the task briefed 3198; the
regenerated artifact now says 3199). The 157/81/38/67 above are the subset for
these pages. A control is not closed by this audit: the artifact remains the
status authority and still records every one of these as OPEN.

---

## 2. System / Gateway / Update

### 2.1 Wired (real handler + real wire shape)

| Control / route | SPA call site | Backend handler (file:line) | Wire shape observed |
| --- | --- | --- | --- |
| Status read (gateway fields) | `api.getStatus()` — `frontend/qubit-web/src/pages/SystemPage.tsx:276`, `frontend/qubit-web/src/lib/api.ts` | `h_status` — `voicebridge/qubit_compat.py:345` | `qubit-runtime-status/1`: `gateway_running`, `gateway_state`, `gateway_pid`, `gateway_service_unit`, `gateway_detail` |
| Gateway status read | `api.getGatewayStatus()` — `SystemPage.tsx:288`, `App.tsx:999`, `api.ts:1573` | `h_gateway` — `voicebridge/qubit_compat.py:2167` | `{ok, state, supervision, process{state,service_unit,scope,host,port,available,running,active_state,sub_state,port_in_use,detail}}` |
| Gateway supervision read source | — | `gateway_supervisor.status` — `voicebridge/gateway_supervisor.py:107` | systemd `ActiveState`/`SubState`/`MainPID` + real port probe |
| Gateway Start | `runGateway("start")` — `SystemPage.tsx:326`, button `SystemPage.tsx:1186`; `api.startGateway` — `api.ts:1913` | `h_gateway_supervision("start")` — `qubit_compat.py:2226`; route `qubit_compat.py:5759` | tested: test process → 503 `GATEWAY_SUPERVISION_UNAVAILABLE` (guard `gateway_supervisor.py:188-202`) |
| Gateway Stop | `runGateway("stop")` — `SystemPage.tsx:331`, button `SystemPage.tsx:1206`; `api.stopGateway` — `api.ts:1915` | `h_gateway_supervision("stop")` — `qubit_compat.py:2226`; route `qubit_compat.py:5760` | same |
| Gateway Restart | `runGateway("restart")` — `SystemPage.tsx:335`, button `SystemPage.tsx:1196`; `api.restartGateway` — `api.ts:1575` | `h_gateway_restart` — `qubit_compat.py:2284`; route `qubit_compat.py:5761` | same |
| Update check (read) | `api.checkQubitUpdate` — `SystemPage.tsx:286`, `SystemPage.tsx:611`, `api.ts:1579` | `h_qubit_update_check` — `qubit_compat.py:8143`; route registered ahead of the gated wildcard `qubit_compat.py:5753` | `UpdateCheckResponse`: `install_method`, `current_version`, `behind=null`, `update_available`, `can_apply`, `update_command`, `message` |
| Update apply | `api.updateQubit` — `api.ts:1577`; `applyUpdate` — `SystemPage.tsx:648`; "Update now" `SystemPage.tsx:1061` | `h_qubit_update_apply` — `qubit_compat.py:8162`; route `qubit_compat.py:5754` (handler delegates to `voicebridge/update_manager.py:251`) | confirmed apply stage: 409 `CONFIRM_REQUIRED` without `confirm`; 503 test-guard under pytest; real path is one `git pull --ff-only` |
| Credential pool read/add/remove | `api.getCredentialPool` — `api.ts:1843`, add `api.ts:1847`, remove `api.ts:1863` | `h_credentials_pool` / `h_credentials_pool_add` — routes `qubit_compat.py:5714-5716` | real handlers on the `/api/qubit/credentials/pool` path the SPA calls |
| Run doctor (System ops) | `SystemPage.tsx` ops card | `/api/ops/doctor` → canonical broker | covered by `tests/test_dashboard_e2e_phase4.py::test_system_doctor_action_and_gateway_gating` |

### 2.2 Gate / absent (OPEN)

- **Update apply is now wired to `update_manager`, but is not E2E-verified.**
  During this audit another agent landed `voicebridge/update_manager.py` and
  `h_qubit_update_apply` (`voicebridge/qubit_compat.py:8162`, route
  `voicebridge/qubit_compat.py:5754`), replacing the blanket `_NO_UPDATE` gate
  for the POST path (the `_NO_UPDATE` constant remains at
  `voicebridge/qubit_compat.py:5257` for the unknown GET alias
  `qubit_compat.py:5852`). The SPA apply call (`api.updateQubit` — `api.ts:1577`;
  `applyUpdate` — `SystemPage.tsx:648`) now resolves to a real handler. It
  remains **OPEN** in the artifact: no real-backend E2E has run, the "Update
  now" button is only reachable when `updateInfo.can_apply` is `true`
  (`SystemPage.tsx:1061`), and a test process gets the fail-closed 503.
- **Gateway refusal token mismatch (controls not disabled up front on a
  systemd-less host).** `h_gateway` emits `GATEWAY_SUPERVISION_UNAVAILABLE`
  (`voicebridge/qubit_compat.py:2177-2178`) but the SPA only treats
  `GATEWAY_NO_SUPERVISION` as the refusal signal
  (`frontend/qubit-web/src/pages/SystemPage.tsx:313`,
  `frontend/qubit-web/src/App.tsx:1004`; comment `SystemPage.tsx:269`). On a
  host without `systemctl`, `GET /api/gateway` returns the new typed code, the
  SPA `gatewayRefusal` stays `null`, and Start/Restart render enabled (they
  then answer the typed 503 on click). The refusal token should agree; fixed
  owner-side, not here.
- **Curator Pause / Run now** (`SystemPage.tsx:1153-1163`): OPEN; the curator
  gate is `CURATOR_ON_DEMAND` (`qubit_compat.py:5868-5872`).
- **Upstream credential-pool alias** (`/api/credentials/pool`): still gated
  `SECRET_NEVER_SERVED` (`qubit_compat.py:5840-5843`). The SPA does not call
  this alias — it calls the Qubit path (wired above); the gate is defense in
  depth against the fork client.
- **Backup / restore / import / debug-share / hooks / memory reset**
  (`SystemPage.tsx:463-739`): wired to real ops/broker paths in places, but all
  remain OPEN in the artifact (no E2E).
- Remaining `system` rows: **157 OPEN** (all canonical ops, CLI, and voice rows
  in that area).

### 2.3 Focused test coverage added

`tests/test_dashboard_controls9u.py` drives the real dispatcher for
`GET /api/status`, `GET /api/gateway`, `POST /api/gateway/{start,stop,restart}`,
`GET /api/qubit/update/check`, `POST /api/qubit/update`, `GET /api/skills`,
`GET /api/mcp/servers`, `GET /api/dashboard/plugins`, asserting each resolves
to a real handler and returns the documented shape. It also pins the two
findings above (one `xfail` for the refusal-token mismatch; the update-apply
mapping is asserted as a real passing test). A single opt-in browser test
(`-m ui`) drives the System Gateway card.

---

## 3. Skills

### 3.1 Wired (real handler + real wire shape)

| Control / route | SPA call site | Backend handler (file:line) | Wire shape observed |
| --- | --- | --- | --- |
| Skills list | `api.getSkills` — `SkillsPage.tsx:175`, `api.ts:1295` | `h_skills` — `qubit_compat.py:1399` (via `skill_catalog.catalog_entries`) | bare `SkillInfo[]` array |
| Enable/disable toggle | `api.toggleSkill` — `SkillsPage.tsx:196`, `api.ts:1300` | `h_skills_toggle` — `qubit_compat.py:1432`; route `qubit_compat.py:5433` | staged `409 CONFIRM_REQUIRED` + `confirmation_id` on the shared journal |
| Hub search / install / update / scan | `SkillsPage.tsx:949,1003,1022,1418`; `api.ts:2021,2049,2062,2074` | local hub handlers — `qubit_compat.py:5774-5781` | local reviewed catalog; remote hub wildcard typed-gated `BLOCKED_REMOTE_SOURCE` (`qubit_compat.py:5881-5882`) |
| Create / edit / learn | `SkillsPage.tsx:559,567,751` | `POST /api/skills` (`skill_create` — `qubit_compat.py:6683`), `PUT /api/skills/content` (`skill_update` — `qubit_compat.py:6709`) | journal-backed versioned contracts |

### 3.2 OPEN

- Every skills route/control is OPEN in the artifact (81 OPEN / 83 rows, area
  `skills`): no real-backend E2E chain.
- The local runtime audit (`docs/QUBIT_LOCAL_RUNTIME_AUDIT_2026-09-21.md:154`)
  records skills enabled-state import as not yet mirrored; that is a behavioral
  gap beyond the static route wiring.
- 12 canonical skill ops (`List skills`, `Enable skill`, `Disable skill`,
  `Remove skill`, `Rollback skill`, `Show skill`, workshop ops) are all OPEN
  (`voicebridge/dashboard_ops.py` catalog rows).

---

## 4. MCP

### 4.1 Wired (real handler + real wire shape)

| Control / route | SPA call site | Backend handler (file:line) | Wire shape observed |
| --- | --- | --- | --- |
| Server list | `api.getMcpServers` — `McpPage.tsx`, `api.ts:1684` | `h_mcp_servers` — `qubit_compat.py:1668` | `{ok, servers[], state, mount_policy, detail}`; each `McpServer` has `name, transport, url, command, args, env, auth, enabled, tools, connected, status, source, policy` |
| Add server | `api.addMcpServer` — `McpPage.tsx:145`, `api.ts:1687` | `mcp_create` — `qubit_compat.py:6773` | confirm-gated; `400 MCP_BAD_INPUT` for a transport-less create |
| Test connection | `api.testMcpServer` — `McpPage.tsx:172`, `api.ts:1721` | `h_mcp_test_local` — `qubit_compat.py:1755`; route `qubit_compat.py:5921` | local inventory test; `network_probe=false` (no network) |
| Enable/disable | `api.setMcpServerEnabled` — `McpPage.tsx:211`, `api.ts:1726` | `mcp_set_enabled` — `qubit_compat.py:6831` | persisted enabled flag |
| Delete | `api.removeMcpServer` — `McpPage.tsx:229`, `api.ts:1709` | `mcp_delete` — `qubit_compat.py:6807` | confirm-gated |
| Authenticate / auth status | `McpPage.tsx:186-192`; `api.ts:1700` | `mcp_auth` — `qubit_compat.py:6857`; `mcp_auth_status` — `qubit_compat.py:6886` | credential **reference** only, never a secret |

### 4.2 Gate / OPEN

- OAuth flow reads/mutations are typed-gated `OAUTH_MANUAL_HANDOFF`
  (`qubit_compat.py:5866-5867`); catalog install is gated
  `BLOCKED_REMOTE_SOURCE` (`qubit_compat.py:5883`).
- All MCP rows are OPEN (38 OPEN / 43, area `mcp`); no real-backend E2E.
  `tests/test_dashboard_e2e_phase4.py::test_mcp_add_enable_delete_via_spa`
  exercises add/enable/delete through the SPA but the artifact does not count a
  passing test as a disposition.

---

## 5. Plugins

### 5.1 Wired (real handler + real wire shape)

| Control / route | SPA call site | Backend handler (file:line) | Wire shape observed |
| --- | --- | --- | --- |
| Plugin list | `api.getPlugins` — `PluginsPage.tsx`, `api.ts:1589` | `h_plugins` — `qubit_compat.py:6402` (manifests via `_overlay_manifests` — `qubit_compat.py:9706`) | bare `PluginManifestResponse[]` array; owns `qubit-brand`, `qubit-reports` |
| Rescan | `api.rescanPlugins` — `PluginsPage.tsx:398`, `api.ts:1591` | `plugins_rescan` — route `qubit_compat.py:5937` | manifest set (`rescanned:true`, `count`) |
| Install agent plugin | `api.installAgentPlugin` — `PluginsPage.tsx:377`, `api.ts:1596` | `h_agent_plugin_install` — route `qubit_compat.py` | OPEN (no E2E) |
| Enable / disable | `PluginsPage.tsx:992-1008`; `api.ts:1606,1616` | `agent_enable` / `agent_disable` | OPEN (no E2E) |
| Update | `PluginsPage.tsx:1037-1039`; `api.ts:1626` | typed gate (agent-plugins update wildcard) | gated |
| Visibility toggle | `PluginsPage.tsx:1055-1057`; `api.ts:1655` | `h_plugin_visibility` — `qubit_compat.py:6186`; route `qubit_compat.py:5938` | real persisted toggle |
| Remove | `PluginsPage.tsx:1122`; `api.ts:1636` | `agent_remove` | confirm-gated |
| Provider selection | `PluginsPage.tsx:432-486`; `api.ts:1646,1898` | `plugin_providers_write` / memory provider routes | OPEN (no E2E) |

### 5.2 Gate / OPEN

- Remote plugin install is gated `BLOCKED_REMOTE_SOURCE`
  (`qubit_compat.py:5884`).
- All plugin rows are OPEN (67 OPEN / 70, area `plugins`); 12 canonical plugin
  ops are OPEN. The page-level E2E in
  `tests/test_dashboard_e2e_phase4.py::test_plugins_rescan_returns_manifests`
  covers only the rescan control.

---

## 6. What this audit did not do

- It did not run the full browser/Playwright matrix. One new focused browser
  test for the Gateway card was added and run (see
  `tests/test_dashboard_controls9u.py::test_gateway_card_browser`, opt-in
  `-m ui`); the artifact's ~3199 OPEN rows are unchanged.
- It did not regenerate or edit `QUBIT_PAGE_COVERAGE_2026-09-21.json`.
- It did not exercise a live provider, real `/chat` credential, voice pipeline,
  or right-Ctrl path.
- It did not fix the remaining gateway refusal-token mismatch above (product
  code is owned by other agents). The update-apply and credential-pool wiring
  landed during this audit from another agent, so those citations reflect the
  worktree at final read, not a frozen revision.

## 7. Uncertainty

- **Concurrent edits.** `voicebridge/update_manager.py`, `h_qubit_update_apply`,
  and the Qubit-path credential-pool handlers were added by another agent while
  this audit ran. Line numbers and "wired/absent" statements for those items
  are as-of the audit's final read; re-check the worktree before relying on
  them.
- The gateway-control behavior in a *test* process is the fail-closed guard
  (503). What a real, confirmed start/stop/restart does to the host service is
  unit-tested with injected runners/spawners
  (`tests/test_gateway_supervisor9u.py`) and is not re-exercised live here.
- `GET /api/status` reads the real host systemd unit even from a hermetic
  config tree; its gateway fields are therefore host-dependent by design.
- Whether the `GATEWAY_NO_SUPERVISION` ↔ `GATEWAY_SUPERVISION_UNAVAILABLE`
  mismatch should be fixed on the backend or the SPA is an owner decision; the
  audit records the mismatch and the focused test pins it (`xfail`).
