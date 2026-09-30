# Owner policy — refused RPC dialect (2026-09-17)

Status: **explicit owner-policy record** for the 30 `B_missing` rows.

## Policy

Qubit does **not** serve the vendored Hermes/OpenClaw internal `rpc.method`
transport dialect as a competing dispatch authority. Doing so would create a
second dispatch authority beside the canonical Qubit operation contract
(`CLI/dashboard/voice -> voicebridge.dashboard_ops.run_op -> broker/policy/
audit/e-stop`), which the owner architecture decision of 2026-09-17 forbids.

Counterpart for every refused row: the canonical Qubit operation exposing the
same capability on the four supported surfaces, reachable through
`dashboard_ops.run_op`.

Disposition: `OPERATOR_ONLY`, class `owner_policy_refused_dialect`, in
`tests/fixtures/differential/DECLARATIONS.json`. These rows are never counted as
implemented; they are a typed, explicit, owner-authorized refusal.

## Refused rows (30)

| capability_id | upstream path | rationale |
| --- | --- | --- |
| `api.route.post-lease-probe-action-sid.f6fe459c` | lease-probe route | not a Qubit surface; superseded by the ops broker |
| `rpc.method.tools-mcp_tool_transport.py-20.0c142877` | `tools/mcp_tool_transport.py:20` | MCP transport method; Qubit mounts no MCP server |
| `rpc.method.tools-send_message_senders.py-446.ed390b95` | `tools/send_message_senders.py:446` | messaging sender internals; Qubit uses `msg` ops |
| `rpc.method.tools-computer_use-doctor.py-118.de62e327` | `tools/computer_use/doctor.py:118` | desktop driver internals; operator-only hardware |
| `rpc.method.apps-desktop-scripts-perf-gateway_attach_bench.py-178.0c9e1e54` | `apps/desktop/scripts/...:178` | desktop perf harness, non-product |
| `rpc.method.apps-desktop-scripts-perf-gateway_attach_bench.py-180.8d0b29c1` | `apps/desktop/scripts/...:180` | desktop perf harness, non-product |
| `rpc.method.gateway-platforms-signal.py-610.d99a0971` | `gateway/platforms/signal.py:610` | channel plugin internals; Qubit `msg` ops |
| `rpc.method.plugins-web-keyless_mcp.py-156.46f3ac9f` | `plugins/web/keyless_mcp.py:156` | plugin internals; no MCP mount |
| `rpc.method.plugins-platforms-a2a-tools.py-111.b407938a` | `plugins/platforms/a2a/tools.py:111` | A2A platform internals |
| `rpc.method.plugins-platforms-a2a-protocol.py-100.69a7c97d` | `plugins/platforms/a2a/protocol.py:100` | A2A platform internals |
| `rpc.method.plugins-platforms-a2a-protocol.py-104.c8ecaadf` | `plugins/platforms/a2a/protocol.py:104` | A2A platform internals |
| `rpc.method.tui_gateway-methods_browser_control.py-61.34f98b71` | `tui_gateway/methods/browser_control.py:61` | WS dialect; Qubit one-event-path policy |
| `rpc.method.tui_gateway-compute_host.py-277.72790ce5` | `tui_gateway/compute_host.py:277` | WS dialect; not served |
| `rpc.method.tui_gateway-compute_host.py-370.35c038ed` | `tui_gateway/compute_host.py:370` | WS dialect; not served |
| `rpc.method.tui_gateway-entry.py-259.6e9fcdff` | `tui_gateway/entry.py:259` | WS dialect; not served |
| `rpc.method.tui_gateway-entry.py-288.5193cc7e` | `tui_gateway/entry.py:288` | WS dialect; not served |
| `rpc.method.tui_gateway-ws.py-257.e71e32bd` | `tui_gateway/ws.py:257` | WS dialect; single-path policy |
| `rpc.method.tui_gateway-ws.py-273.261e89e1` | `tui_gateway/ws.py:273` | WS dialect; single-path policy |
| `rpc.method.tui_gateway-ws.py-326.2b8730de` | `tui_gateway/ws.py:326` | WS dialect; single-path policy |
| `rpc.method.tui_gateway-host_supervisor.py-429.3f7f07df` | `tui_gateway/host_supervisor.py:429` | process supervision; operator-owned |
| `rpc.method.tui_gateway-server.py-574.438d3edd` | `tui_gateway/server.py:574` | WS gateway server; not a Qubit surface |
| `rpc.method.tui_gateway-server.py-699.8c948813` | `tui_gateway/server.py:699` | WS gateway server; not a Qubit surface |
| `rpc.method.tui_gateway-server.py-704.4178d7c7` | `tui_gateway/server.py:704` | WS gateway server; not a Qubit surface |
| `rpc.method.agent-copilot_acp_client.py-130.0671f61d` | `agent/copilot_acp_client.py:130` | ACP client internals |
| `rpc.method.agent-copilot_acp_client.py-134.8ffd9780` | `agent/copilot_acp_client.py:134` | ACP client internals |
| `rpc.method.agent-copilot_acp_client.py-349.14a64c28` | `agent/copilot_acp_client.py:349` | ACP client internals |
| `rpc.method.agent-lsp-protocol.py-103.11500282` | `agent/lsp/protocol.py:103` | LSP transport internals |
| `rpc.method.agent-lsp-protocol.py-108.866ec3e9` | `agent/lsp/protocol.py:108` | LSP transport internals |
| `rpc.method.agent-lsp-protocol.py-113.90f86a1a` | `agent/lsp/protocol.py:113` | LSP transport internals |
| `rpc.method.agent-lsp-protocol.py-119.ac63ce3d` | `agent/lsp/protocol.py:119` | LSP transport internals |
