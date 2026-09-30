# Qubit Plugin & MCP Invocation — 2026-09-21

**Scope:** the plugin/MCP workstream only. Files touched:
`voicebridge/plugin_runtime.py`, `voicebridge/mcp_servers.py`,
`voicebridge/integration_status.py`,
`voicebridge/gateway/openclaw_handlers/mcp_app.py`,
`tests/test_plugin_invocation9u.py`, `tests/test_mcp_invocation9u.py`,
this document.

**Status vocabulary:** `invocable` | `unsupported` + exact reason |
`operator-only`. Nothing here is a behavioral-parity or VERIFIED claim; all
client invocation of external MCP servers remains `operator-only`.

---

## 1. Per-item disposition table

| Item | What it actually is | Disposition | Exact reason / evidence |
|---|---|---|---|
| `~/.hermes/plugins/hermes-achievements/` (source catalog entry) | Three durable-state JSON files only: `state.json`, `scan_checkpoint.json`, `scan_snapshot.json`. No `manifest.json`, no `plugin_api.py`, no `dist/`, no `register()`. | **unsupported** (not a plugin package) | It is the *legacy data directory* of the vendored dashboard plugin (`plugin_api.py` migrates legacy `$QUBIT_HOME/plugins/<name>/` state into `$QUBIT_HOME/plugin-data/<name>/` — `qubit_runtime/plugins/hermes-achievements/dashboard/plugin_api.py:158-167`). There is no code or manifest to import. |
| `hermes-achievements` implementation (vendored) | Dashboard plugin: FastAPI router (`dashboard/plugin_api.py`) + frontend bundle (`dashboard/dist/index.js`) scanning the real `qubit_state.SessionDB`. | **invocable** (already framed) | Qubit's canonical backend is `voicebridge/qubit_plugins.py:275` `_achievements_engine` → `:323` `h_plugin_achievements` (snapshot), `:335` scan-status, `:379` rescan; served via `voicebridge/qubit_compat.py` routes `/api/plugins/hermes-achievements/*`. |
| `builtin.achievements` (new host adapter) | In-process adapter behind `plugin_runtime` that delegates to the canonical `qubit_plugins` backend; actions `snapshot`/`status`/`rescan`. | **invocable** | `voicebridge/plugin_runtime.py:420` `_achievements_adapter`; registry `:475` `_HOST_MANIFESTS`, `:488` `_HOST_HANDLERS`; reached through `:951` `invoke` (e-stop → shared durable journal confirmation → exists → bound handler → audit), `:741` `host_capabilities`. It does **not** load a second engine. |
| Migration of source plugin enable/disable state | `qubit_migration._import_plugin_state` | **unsupported** (by design, unchanged) | `voicebridge/qubit_migration.py:721`: `plugin_runtime` has no public API to record a name + enabled/disable state, and no second store is created. The source `hermes-achievements/state.json` has only `unlocks`, so `_source_plugin_states` reports `UNKNOWN` anyway (`:688`). |
| MCP server `cgraph-graft` (source: `config.yaml:641`) | stdio server: `command: graft`, `args: ["mcp", "/home/trick/Projects/Repos/CGraph Umbrella"]`, `enabled: true`. | **operator-only** for client invocation; **invocable** as inventory/config | Inventory read is real (`list_servers`); calling a tool on it would spawn the `graft` binary, which no sanctioned Qubit client transport does. |
| MCP server `openclaw-voice-bridge-graft` (source: `config.yaml:647`) | stdio server: `command: graft`, `args: ["mcp", "/home/trick/Projects/Repos/openclaw-voice-bridge"]`, `enabled: true`. | **operator-only** for client invocation; **invocable** as inventory/config | Same as above. |
| Any configured stdio/http MCP server (registry + `mcp.servers` config) | Definition only; `connected=false`, `invocable=false`. | **operator-only** | `voicebridge/mcp_servers.py:403` `invocation_status` and `:433` `invoke` return the typed `MCP_CLIENT_INVOCATION_OPERATOR_REQUIRED` refusal. No connect, no spawn. |

---

## 2. MCP read authority — one function, traced per surface

The canonical read authority is `voicebridge/mcp_servers.list_servers`
(`voicebridge/mcp_servers.py:203`). Every Qubit surface resolves through it:

| Surface | Call chain (file:line) |
|---|---|
| Dashboard (`GET /api/mcp/servers`) | `voicebridge/qubit_compat.py:1668` `h_mcp_servers` → `:1679` `MS.list_servers`; route at `:5455`. Catalog/dialect reads: `:1711` `h_mcp_catalog_live`, `:6886` `mcp_auth_status`. |
| CLI (`qubit mcp status`) | `voicebridge/cli.py:3802` → `:4213` `_cmd_mcp_status` → `voicebridge/integration_status.py:13` `mcp_status` → `:28` `MS.list_servers`. |
| Voice (`mcp` backend) | `voicebridge/voice_commands.py:867` `_b_mcp` → `voicebridge/integration_status.py:13` `mcp_status` → `MS.list_servers`. |
| `/qubit-chat` (SPA) | `frontend/qubit-web/src/pages/ChatPage.tsx:630` `api.getRuntime()` → `frontend/qubit-web/src/lib/api.ts:743-744` `GET /api/runtime` → `voicebridge/webui.py:1983` → `voicebridge/runtime_inventory.py:259` `runtime_inventory` → `:278` `mcp_status` → `MS.list_servers`. (Rendered in the debug overlay; the button-style control path is shared.) |
| `/chat` (SPA) | `/chat` renders the **vendored TUI** over the one PTY path (`frontend/qubit-web/src/pages/TerminalChatPage.tsx:34`, `/agent/api/pty`); it exposes no MCP read route of its own. **Divergence:** the agent inside that TUI uses the vendored MCP tool layer (`qubit_runtime/tools/mcp_tool.py`), which reads the vendored runtime `mcp_servers:` config directly and does **not** call `voicebridge.mcp_servers.list_servers`. See §5. |
| Gateway OpenClaw MCP family | `voicebridge/gateway/openclaw_handlers/mcp_app.py:54` `_mcp_servers` → `:58` `MS.list_servers`; `mcp.app.callTool` at `:75`. |

`integration_status.mcp_status` records now also carry
`invocable=false` / `invocation_code`
(`voicebridge/integration_status.py:40-41,56-57`) so no surface can imply a
configured server is callable.

---

## 3. Client invocation — what was implemented

### 3.1 Configured external servers: fail-closed refusal (operator-only)

`voicebridge/mcp_servers.py`:

- `invocation_status(cfg, name)` — read-only disposition, resolves via
  `list_servers`, returns `state: OPERATOR_ONLY`,
  `code: MCP_CLIENT_INVOCATION_OPERATOR_REQUIRED`, the exact
  `MCP_NOT_CONNECTED_NO_CLIENT_INVOCATION` reason, the operator prerequisite
  (a sanctioned, broker/policy-gated stdio-spawn/http-connect transport
  authorized by the owner), and the local counterpart (the in-process loopback
  host in `plugin_runtime`). Unknown names raise typed `MCP_NOT_FOUND`.
- `invoke(cfg, name, *, tool, args, confirmed, confirmation_id, requested_by)`
  — the ONE canonical ask path. Gate order: e-stop
  (`MCP_ESTOP`) → shared durable `ApprovalJournal` confirmation
  (`MCP_CONFIRM_REQUIRED`) → server exists → typed `OPERATOR_ONLY` refusal +
  redacted audit. It never connects, spawns, or reads a credential value.

This is deliberately a refusal, not a fabricated tool call: Qubit's MCP
authority is inventory-only and the repo refuses unauthenticated mounts
(`voicebridge/mcp_servers.py:1-14`, `qubit_compat.h_mcp` mount policy).

### 3.2 In-process tools/capabilities: real invocation

The gateway `mcp.app.callTool` handler keeps its real path
(`plugin_runtime.invoke` over host-bound handlers) and now also resolves a
*configured server* name to the typed refusal instead of a generic
`PLUGIN_NOT_FOUND` (`voicebridge/gateway/openclaw_handlers/mcp_app.py:75-111`).

### 3.3 Plugin: `builtin.achievements`

`plugin_runtime.invoke(cfg, "builtin.achievements", args={"action": ...},
confirmed=True)` runs the canonical achievements engine (snapshot / status /
rescan) behind the plugin broker. e-stop, shared-journal confirmation, args
bounds, redaction, and audit all apply unchanged. The adapter is discoverable
via `plugin_runtime.host_capabilities`, appears in `status()["host_capabilities"]`,
and on the loopback MCP `tools/list`; it is intentionally **not** an installable
catalog row (so the registered-plugin catalog set is unchanged).

---

## 4. Secret handling

- Only `env:` / `file:` / `vault:` references are ever accepted or returned
  (`voicebridge/mcp_servers.py` `_REF_RE`, `_validate`, `set_auth_ref`); a raw
  secret write is refused with `SECRET_WRITE_REFUSED`.
- The invocation disposition and audit pass only the server name, transport,
  tool name, and an args-presence boolean — never env values.
- `plugin_runtime` redacts every result/error through
  `voicebridge.redaction`; the adapter result is redacted by `invoke`.

---

## 5. Uncertainty / not verified

- **`/chat` divergence (owner-gated).** The embedded vendored TUI's MCP tool
  layer does not read `voicebridge.mcp_servers`. The migration imports
  `config.yaml mcp_servers:` into the Qubit registry, so the *inventory*
  converges, but the in-TUI tool path is not routed through the Qubit
  authority. Fixing it would require editing `webui`/PTY or the vendored
  runtime (out of this workstream's scope). Treat as a documented limitation,
  not parity.
- **No sanctioned external MCP client transport exists**, so no end-to-end
  "configured server tool call" is implemented or claimed. Wiring the vendored
  `qubit_runtime/tools/mcp_tool.py` client (stdio spawn / http connect) requires
  an owner decision + a broker/policy gate; until then the disposition is
  `operator-only`.
- **The `hermes-achievements` adapter is code-side real** (it runs the real
  engine over a real `SessionDB` in tests) but is **not independently verified**
  and is not behavioral parity. The source `~/.hermes` plugin directory is only
  legacy state, so "the source plugin was imported as a plugin" is explicitly
  **not** claimed.
- Focused tests run (this session):
  `tests/test_plugin_invocation9u.py` + `tests/test_mcp_invocation9u.py`
  = 18 passed; adjacent focused regression files
  (`test_plugin_runtime8u`, `test_oc_skills_agent8_plugins`,
  `test_plugins_skills8u`, `test_openclaw_family_mcp_board9u`,
  `test_mcp_read_parity9u`, `test_qubit_plugins`, `test_invocation_matrix9u`,
  `test_plugins8tf`, `test_dashboard_agent3_plugins`,
  `test_dashboard_plugin_fleet`, `test_mcp_servers_bh1`,
  `test_openclaw_wiring8u`, `test_openclaw_d4_policy`,
  `test_openclaw_d4_skills`, `test_tool_skill_plugin_coverage_wave5`,
  `test_backend_routes_integration8u`, `test_single_confirmation_authority8u`,
  `test_transport_redaction_wave5`, `test_voice_commands8tf`,
  `test_qbpv2_agent3_achievements`, `test_security_parity_wave4`) = all passed.
  The full matrix has **not** been run.
