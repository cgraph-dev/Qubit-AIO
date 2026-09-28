# Qubit OpenClaw OPERATOR_ONLY Disposition Review — 2026-09-21

**Status: NOT VERIFIED — review input for the owner ruling.**
This document classifies the 95 `OPERATOR_ONLY` rows of the OpenClaw contractual-pin surface; it does not verify behavior, does not change any disposition, and is not a parity claim.

## Scope and method

- **Source of the OPERATOR_ONLY set:** `docs/generated/QUBIT_OPENCLAW_UPGRADE_MANIFEST.json` (`pinned_surface_disposition.methods`, `status == OPERATOR_ONLY`).
- **Evidence:** each row's typed refusal is located in the handler source by AST (the registered handler function that emits the declared code, or the shared gate it delegates to), reported as `file:line`.
- **Regeneration:** `python scripts/openclaw_operator_only_review.py --write`; `--check` verifies this file is byte-identical. The machine-readable index is in the HTML comment at the end.
- **Classification is a review judgement**, not a manifest field. Ambiguous hybrid rows are called out in the notes.

## Classification rule

- **`external-prerequisite`** — the refusal names a resource *outside* Qubit's codebase (a credential, a remote service/registry, a paired device, a process supervisor, or a provider transport) that the operator can provision. Qubit's architecture does not forbid supporting it; a live adapter is the only gap.
- **`architecture-gated`** — supporting the method would require Qubit to add or reverse an intentional architecture decision: an upstream server-side registry/store/runtime/sandbox Qubit deliberately does not run (single authority, local-first), or an intentional single-agent / single-operator design. Provisioning an external credential alone would not enable it.

## Summary

- Total `OPERATOR_ONLY` methods: **95**
- `external-prerequisite`: **68**
- `architecture-gated`: **27**

### Prerequisite families

- **GitHub credential / OAuth** (10): `tools.github.authorize.cancel`, `tools.github.authorize.poll`, `tools.github.authorize.start`, `tools.github.configure`, `tools.github.status`, `users.github.authorize.cancel`, `users.github.authorize.poll`, `users.github.authorize.start`, `users.github.disconnect`, `users.github.status`
- **MCP App sandbox runtime** (1): `mcp.app.updateModelContext`
- **board widget sandbox runtime** (2): `board.action`, `board.event`
- **chunked upload staging** (3): `skills.upload.begin`, `skills.upload.chunk`, `skills.upload.commit`
- **cloud-worker transport** (5): `environments.create`, `environments.destroy`, `sessions.dispatch`, `sessions.move`, `sessions.reclaim`
- **deliberate no-self-evaluation path** (1): `skills.proposals.evaluate`
- **detached-task completion registry** (1): `tasks.dismiss`
- **email / identity service** (1): `users.linkEmail`
- **ephemeral task-suggestion registry** (3): `taskSuggestions.accept`, `taskSuggestions.create`, `taskSuggestions.dismiss`
- **external MCP server** (1): `mcp.app.listResourceTemplates`
- **external OAuth provider** (4): `users.authConnect.answer`, `users.authConnect.cancel`, `users.authConnect.start`, `users.authConnect.status`
- **git forge credential / API** (5): `controlUi.githubPreview`, `sessions.github.confirm`, `sessions.github.options`, `sessions.github.publish`, `sessions.github.status`
- **git forge credential / network** (1): `projects.searchRemote`
- **human-mention inbox service** (2): `mentions.dismiss`, `mentions.list`
- **inbound channel transport** (3): `channels.logout`, `channels.start`, `channels.stop`
- **intentional single-agent architecture** (3): `agents.create`, `agents.delete`, `agents.update`
- **intentional single-operator architecture** (2): `users.setDisplayName`, `users.setRole`
- **live native-harness relay** (1): `nativeHook.invoke`
- **messaging provider transport** (3): `message.action`, `poll`, `send`
- **operator avatar/media store** (1): `users.setAvatar`
- **operator service restart** (1): `gateway.restart.request`
- **operator update hold** (1): `update.hold`
- **paired node agent** (1): `exec.approvals.node.set`
- **paired node device** (10): `node.event`, `node.invoke.progress`, `node.invoke.result`, `node.pending.ack`, `node.pending.drain`, `node.pending.enqueue`, `node.pending.pull`, `node.pluginTools.update`, `node.runnerInventory.update`, `node.skills.update`
- **per-job scratch store** (2): `cron.scratch.get`, `cron.scratch.set`
- **portal hosting service** (3): `portal.close`, `portal.list`, `portal.open`
- **provider realtime transport** (9): `talk.client.close`, `talk.client.create`, `talk.session.acknowledgeMark`, `talk.session.appendAudio`, `talk.session.cancelOutput`, `talk.session.close`, `talk.session.create`, `talk.session.steer`, `talk.session.submitToolResult`
- **remote verdict registry** (1): `skills.securityVerdicts`
- **service manager process ownership** (1): `gateway.suspend.handoff`
- **session-history scanner** (1): `skills.proposals.historyScan`
- **signed board view-ticket runtime** (1): `board.prompt.authorize`
- **upstream server/transport (MCP loopback attach)** (2): `attach.grant`, `attach.revoke`
- **wake-trigger/routing store** (1): `voicewake.routing.get`
- **web-push service** (7): `push.test`, `push.web.preferences.get`, `push.web.preferences.set`, `push.web.subscribe`, `push.web.test`, `push.web.unsubscribe`, `push.web.vapidPublicKey`
- **writable wake-trigger store** (1): `voicewake.set`

## 1. `external-prerequisite` (68)

Provisioning the named external resource is the remaining gap; no Qubit architecture change is implied.

| method | typed code | exact prerequisite | local counterpart / refusal | refusal evidence |
|---|---|---|---|---|
| `channels.logout` | `CHANNEL_LISTENER_OPERATOR_REQUIRED` | configured inbound channel transport + credential | `qubit channels configure/unconfigure` | `voicebridge/gateway/openclaw_handlers/content.py:774` |
| `channels.start` | `CHANNEL_LISTENER_OPERATOR_REQUIRED` | configured inbound channel transport + credential | `qubit channels status/configure` + channels_inbound ingress | `voicebridge/gateway/openclaw_handlers/content.py:751` |
| `channels.stop` | `CHANNEL_LISTENER_OPERATOR_REQUIRED` | configured inbound channel transport + credential | `qubit channels status/configure` + channels_inbound ingress | `voicebridge/gateway/openclaw_handlers/content.py:763` |
| `controlUi.githubPreview` | `FORGE_API_OPERATOR_REQUIRED` | a configured forge credential (e.g. a GitHub/GitLab token) and a network-reachable forge API | local change review: CLI `qubit git status\|log\|diff\|show` and the dashboard git review surface | `voicebridge/gateway/openclaw_handlers/misc_surface.py:280` |
| `environments.create` | `ENVIRONMENT_CLOUD_OPERATOR_REQUIRED` | injected cloud-worker provider transport | voicebridge.cloud_workers + local process cells (`qubit runtime cells`) | `voicebridge/gateway/openclaw_handlers/system.py:504` |
| `environments.destroy` | `ENVIRONMENT_CLOUD_OPERATOR_REQUIRED` | injected cloud-worker provider transport | voicebridge.cloud_workers + local process cells (`qubit runtime cells`) | `voicebridge/gateway/openclaw_handlers/system.py:515` |
| `exec.approvals.node.set` | `EXEC_APPROVALS_NODE_OPERATOR_REQUIRED` | a paired node agent that owns its own exec policy | `qubit node enroll/revoke` + broker confirmation policy | `voicebridge/gateway/openclaw_handlers/security.py:161` |
| `gateway.restart.request` | `GATEWAY_RESTART_OPERATOR_REQUIRED` | operator restart of the Qubit service unit | CLI `qubit setup health` + the operator runbook | `voicebridge/gateway/openclaw_handlers/system.py:710` |
| `gateway.suspend.handoff` | `SUSPEND_HANDOFF_OPERATOR_REQUIRED` | a service manager that owns process exit (systemd/launchd external restart owner) | operator `systemctl --user restart <qubit unit>` + the operator runbook | `voicebridge/gateway/openclaw_handlers/scheduler_media.py:294` |
| `mcp.app.listResourceTemplates` | `MCP_RESOURCE_TEMPLATES_OPERATOR_REQUIRED` | an MCP server/runtime that advertises resource templates | the MCP tool layer's `list_resources` (qubit_runtime/tools/mcp_tool.py) | `voicebridge/gateway/openclaw_handlers/mcp_app.py:221` |
| `message.action` | `MESSAGING_TRANSPORT_OPERATOR_REQUIRED` | configured messaging account and reachable provider transport | `qubit msg draft` -> `qubit msg approve` -> `qubit msg send --confirm` (voicebridge.messaging, e-stop/confirm gated) | `voicebridge/gateway/openclaw_handlers/sessions.py:1168` |
| `nativeHook.invoke` | `NATIVE_HOOK_RELAY_OPERATOR_REQUIRED` | a live native harness process exposing a registered hook relay (provider/relayId/generation) | voicebridge.hooks (canonical hook declarations) + voicebridge.node_host | `voicebridge/gateway/openclaw_handlers/misc_surface.py:349` |
| `node.event` | `NODE_DEVICE_RELAY_OPERATOR_REQUIRED` | a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority) | voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...` | `voicebridge/gateway/openclaw_handlers/talk_node.py:134` |
| `node.invoke.progress` | `NODE_DEVICE_RELAY_OPERATOR_REQUIRED` | a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority) | voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...` | `voicebridge/gateway/openclaw_handlers/talk_node.py:134` |
| `node.invoke.result` | `NODE_DEVICE_RELAY_OPERATOR_REQUIRED` | a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority) | voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...` | `voicebridge/gateway/openclaw_handlers/talk_node.py:134` |
| `node.pending.ack` | `NODE_DEVICE_RELAY_OPERATOR_REQUIRED` | a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority) | voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...` | `voicebridge/gateway/openclaw_handlers/talk_node.py:134` |
| `node.pending.drain` | `NODE_DEVICE_RELAY_OPERATOR_REQUIRED` | a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority) | voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...` | `voicebridge/gateway/openclaw_handlers/talk_node.py:134` |
| `node.pending.enqueue` | `NODE_DEVICE_RELAY_OPERATOR_REQUIRED` | a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority) | voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...` | `voicebridge/gateway/openclaw_handlers/talk_node.py:134` |
| `node.pending.pull` | `NODE_DEVICE_RELAY_OPERATOR_REQUIRED` | a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority) | voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...` | `voicebridge/gateway/openclaw_handlers/talk_node.py:134` |
| `node.pluginTools.update` | `NODE_DEVICE_RELAY_OPERATOR_REQUIRED` | a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority) | voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...` | `voicebridge/gateway/openclaw_handlers/talk_node.py:134` |
| `node.runnerInventory.update` | `NODE_DEVICE_RELAY_OPERATOR_REQUIRED` | a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority) | voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...` | `voicebridge/gateway/openclaw_handlers/talk_node.py:134` |
| `node.skills.update` | `NODE_DEVICE_RELAY_OPERATOR_REQUIRED` | a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority) | voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...` | `voicebridge/gateway/openclaw_handlers/talk_node.py:134` |
| `poll` | `MESSAGING_TRANSPORT_OPERATOR_REQUIRED` | configured messaging account and reachable provider transport | `qubit msg draft` -> `qubit msg approve` -> `qubit msg send --confirm` (voicebridge.messaging, e-stop/confirm gated) | `voicebridge/gateway/openclaw_handlers/sessions.py:1168` |
| `portal.close` | `PORTAL_SERVICE_OPERATOR_REQUIRED` | a browser/portal hosting service (the upstream portal HTTP proxy listening on a local port) | the canonical website-integration store (voicebridge.websites) + the operator's own browser | `voicebridge/gateway/openclaw_handlers/scheduler_media.py:308` |
| `portal.list` | `PORTAL_SERVICE_OPERATOR_REQUIRED` | a browser/portal hosting service (the upstream portal HTTP proxy listening on a local port) | the canonical website-integration store (voicebridge.websites) + the operator's own browser | `voicebridge/gateway/openclaw_handlers/scheduler_media.py:308` |
| `portal.open` | `PORTAL_SERVICE_OPERATOR_REQUIRED` | a browser/portal hosting service (the upstream portal HTTP proxy listening on a local port) | the canonical website-integration store (voicebridge.websites) + the operator's own browser | `voicebridge/gateway/openclaw_handlers/scheduler_media.py:308` |
| `projects.searchRemote` | `PROJECT_REMOTE_SEARCH_OPERATOR_REQUIRED` | configured git forge credential + network access | local projects: `qubit projects list` | `voicebridge/gateway/openclaw_handlers/content.py:740` |
| `push.test` | `PUSH_WEB_OPERATOR_REQUIRED` | configured web-push service (VAPID keypair + subscription store) + browser permission | in-app dashboard notices + the canonical messaging outbox (`voicebridge.messaging`) | `voicebridge/gateway/openclaw_handlers/content.py:794` |
| `push.web.preferences.get` | `PUSH_WEB_OPERATOR_REQUIRED` | configured web-push service (VAPID keypair + subscription store) + browser permission | in-app dashboard notices + the canonical messaging outbox (`voicebridge.messaging`) | `voicebridge/gateway/openclaw_handlers/content.py:794` |
| `push.web.preferences.set` | `PUSH_WEB_OPERATOR_REQUIRED` | configured web-push service (VAPID keypair + subscription store) + browser permission | in-app dashboard notices + the canonical messaging outbox (`voicebridge.messaging`) | `voicebridge/gateway/openclaw_handlers/content.py:794` |
| `push.web.subscribe` | `PUSH_WEB_OPERATOR_REQUIRED` | configured web-push service (VAPID keypair + subscription store) + browser permission | in-app dashboard notices + the canonical messaging outbox (`voicebridge.messaging`) | `voicebridge/gateway/openclaw_handlers/content.py:794` |
| `push.web.test` | `PUSH_WEB_OPERATOR_REQUIRED` | configured web-push service (VAPID keypair + subscription store) + browser permission | in-app dashboard notices + the canonical messaging outbox (`voicebridge.messaging`) | `voicebridge/gateway/openclaw_handlers/content.py:794` |
| `push.web.unsubscribe` | `PUSH_WEB_OPERATOR_REQUIRED` | configured web-push service (VAPID keypair + subscription store) + browser permission | in-app dashboard notices + the canonical messaging outbox (`voicebridge.messaging`) | `voicebridge/gateway/openclaw_handlers/content.py:794` |
| `push.web.vapidPublicKey` | `PUSH_WEB_OPERATOR_REQUIRED` | configured web-push service (VAPID keypair + subscription store) + browser permission | in-app dashboard notices + the canonical messaging outbox (`voicebridge.messaging`) | `voicebridge/gateway/openclaw_handlers/content.py:794` |
| `send` | `MESSAGING_TRANSPORT_OPERATOR_REQUIRED` | configured messaging account and reachable provider transport | `qubit msg draft` -> `qubit msg approve` -> `qubit msg send --confirm` (voicebridge.messaging, e-stop/confirm gated) | `voicebridge/gateway/openclaw_handlers/sessions.py:1168` |
| `sessions.dispatch` | `RUNTIME_CLOUD_UNAVAILABLE` | injected cloud-worker runtime transport | local sessions via the one Qubit runtime | `voicebridge/gateway/openclaw_handlers/sessions.py:795` |
| `sessions.github.confirm` | `FORGE_API_OPERATOR_REQUIRED` | configured git forge credential reference + network access | local change review via `qubit git` | `voicebridge/gateway/openclaw_handlers/sessions.py:786` |
| `sessions.github.options` | `FORGE_API_OPERATOR_REQUIRED` | configured git forge credential reference + network access | local git branches via `qubit git branches` | `voicebridge/gateway/openclaw_handlers/sessions.py:767` |
| `sessions.github.publish` | `FORGE_API_OPERATOR_REQUIRED` | configured git forge credential reference + network access | local change review: CLI `qubit git status\|log\|diff` and the dashboard git review surface | `voicebridge/gateway/openclaw_handlers/sessions.py:754` |
| `sessions.github.status` | `FORGE_API_OPERATOR_REQUIRED` | configured git forge credential reference + network access | local change review via `qubit git` | `voicebridge/gateway/openclaw_handlers/sessions.py:777` |
| `sessions.move` | `RUNTIME_CLOUD_UNAVAILABLE` | injected cloud-worker runtime transport | local sessions via the one Qubit runtime | `voicebridge/gateway/openclaw_handlers/sessions.py:813` |
| `sessions.reclaim` | `RUNTIME_CLOUD_UNAVAILABLE` | injected cloud-worker runtime transport | local sessions via the one Qubit runtime | `voicebridge/gateway/openclaw_handlers/sessions.py:804` |
| `skills.securityVerdicts` | `SKILLS_SECURITY_VERDICTS_OPERATOR_REQUIRED` | a reachable skill security-verdict registry/endpoint and network access | local bundled/verified skill validation (voicebridge.verified_skills._bundled + VerifiedSkillStore) | `voicebridge/gateway/openclaw_handlers/skills_extra.py:435` |
| `talk.client.close` | `TALK_REALTIME_OPERATOR_REQUIRED` | an OpenAI Realtime (or equivalent) provider transport, which Qubit does not run | voicebridge.chat_transport.stream_turn/turn over the QubitSessionStore, plus the /qubit-chat voice path (voicebridge/orchestrator.py) | `voicebridge/gateway/openclaw_handlers/talk_node.py:117` |
| `talk.client.create` | `TALK_REALTIME_OPERATOR_REQUIRED` | an OpenAI Realtime (or equivalent) provider transport, which Qubit does not run | voicebridge.chat_transport.stream_turn/turn over the QubitSessionStore, plus the /qubit-chat voice path (voicebridge/orchestrator.py) | `voicebridge/gateway/openclaw_handlers/talk_node.py:117` |
| `talk.session.acknowledgeMark` | `TALK_REALTIME_OPERATOR_REQUIRED` | an OpenAI Realtime (or equivalent) provider transport, which Qubit does not run | voicebridge.chat_transport.stream_turn/turn over the QubitSessionStore, plus the /qubit-chat voice path (voicebridge/orchestrator.py) | `voicebridge/gateway/openclaw_handlers/talk_node.py:117` |
| `talk.session.appendAudio` | `TALK_REALTIME_OPERATOR_REQUIRED` | an OpenAI Realtime (or equivalent) provider transport, which Qubit does not run | voicebridge.chat_transport.stream_turn/turn over the QubitSessionStore, plus the /qubit-chat voice path (voicebridge/orchestrator.py) | `voicebridge/gateway/openclaw_handlers/talk_node.py:117` |
| `talk.session.cancelOutput` | `TALK_REALTIME_OPERATOR_REQUIRED` | an OpenAI Realtime (or equivalent) provider transport, which Qubit does not run | voicebridge.chat_transport.stream_turn/turn over the QubitSessionStore, plus the /qubit-chat voice path (voicebridge/orchestrator.py) | `voicebridge/gateway/openclaw_handlers/talk_node.py:117` |
| `talk.session.close` | `TALK_REALTIME_OPERATOR_REQUIRED` | an OpenAI Realtime (or equivalent) provider transport, which Qubit does not run | voicebridge.chat_transport.stream_turn/turn over the QubitSessionStore, plus the /qubit-chat voice path (voicebridge/orchestrator.py) | `voicebridge/gateway/openclaw_handlers/talk_node.py:117` |
| `talk.session.create` | `TALK_REALTIME_OPERATOR_REQUIRED` | an OpenAI Realtime (or equivalent) provider transport, which Qubit does not run | voicebridge.chat_transport.stream_turn/turn over the QubitSessionStore, plus the /qubit-chat voice path (voicebridge/orchestrator.py) | `voicebridge/gateway/openclaw_handlers/talk_node.py:117` |
| `talk.session.steer` | `TALK_REALTIME_OPERATOR_REQUIRED` | an OpenAI Realtime (or equivalent) provider transport, which Qubit does not run | voicebridge.chat_transport.stream_turn/turn over the QubitSessionStore, plus the /qubit-chat voice path (voicebridge/orchestrator.py) | `voicebridge/gateway/openclaw_handlers/talk_node.py:117` |
| `talk.session.submitToolResult` | `TALK_REALTIME_OPERATOR_REQUIRED` | an OpenAI Realtime (or equivalent) provider transport, which Qubit does not run | voicebridge.chat_transport.stream_turn/turn over the QubitSessionStore, plus the /qubit-chat voice path (voicebridge/orchestrator.py) | `voicebridge/gateway/openclaw_handlers/talk_node.py:117` |
| `tools.github.authorize.cancel` | `GITHUB_OPERATOR_REQUIRED` | GitHub credential reference with required scopes + network access | CLI `qubit auth status` + an operator-managed credential reference | `voicebridge/gateway/openclaw_handlers/content.py:838` |
| `tools.github.authorize.poll` | `GITHUB_OPERATOR_REQUIRED` | GitHub credential reference with required scopes + network access | CLI `qubit auth status` + an operator-managed credential reference | `voicebridge/gateway/openclaw_handlers/content.py:838` |
| `tools.github.authorize.start` | `GITHUB_OPERATOR_REQUIRED` | GitHub credential reference with required scopes + network access | CLI `qubit auth status` + an operator-managed credential reference | `voicebridge/gateway/openclaw_handlers/content.py:838` |
| `tools.github.configure` | `GITHUB_OPERATOR_REQUIRED` | GitHub credential reference with required scopes + network access | CLI `qubit auth status` + an operator-managed credential reference | `voicebridge/gateway/openclaw_handlers/content.py:838` |
| `tools.github.status` | `GITHUB_OPERATOR_REQUIRED` | GitHub credential reference with required scopes + network access | CLI `qubit auth status` + an operator-managed credential reference | `voicebridge/gateway/openclaw_handlers/content.py:838` |
| `update.hold` | `UPDATE_HOLD_OPERATOR_ONLY` | operator-managed update hold on the runtime image | voicebridge.runtime_images.update_history + `qubit runtime upgrade` | `voicebridge/gateway/openclaw_handlers/system.py:797` |
| `users.authConnect.answer` | `USERS_AUTH_CONNECT_OPERATOR_REQUIRED` | a live external provider OAuth/connect service and network access | CLI `qubit auth login --provider <name>` (manual handoff) | `voicebridge/gateway/openclaw_handlers/users.py:366` |
| `users.authConnect.cancel` | `USERS_AUTH_CONNECT_OPERATOR_REQUIRED` | a live external provider OAuth/connect service and network access | CLI `qubit auth login --provider <name>` (manual handoff) | `voicebridge/gateway/openclaw_handlers/users.py:366` |
| `users.authConnect.start` | `USERS_AUTH_CONNECT_OPERATOR_REQUIRED` | a live external provider OAuth/connect service and network access | CLI `qubit auth login --provider <name>` (manual handoff; Qubit never performs the OAuth dance) | `voicebridge/gateway/openclaw_handlers/users.py:366` |
| `users.authConnect.status` | `USERS_AUTH_CONNECT_OPERATOR_REQUIRED` | a live external provider OAuth/connect service and network access | CLI `qubit auth login --provider <name>` (manual handoff) | `voicebridge/gateway/openclaw_handlers/users.py:366` |
| `users.github.authorize.cancel` | `GITHUB_OPERATOR_REQUIRED` | a started GitHub device-flow authorization (requires the live OAuth app) | nothing to cancel locally; remove the credential reference with the operator's own tooling | `voicebridge/gateway/openclaw_handlers/users.py:345` |
| `users.github.authorize.poll` | `GITHUB_OPERATOR_REQUIRED` | a started GitHub device-flow authorization (requires the live OAuth app) | CLI `qubit auth verify --provider github` (reference presence only) | `voicebridge/gateway/openclaw_handlers/users.py:334` |
| `users.github.authorize.start` | `GITHUB_OPERATOR_REQUIRED` | a live GitHub OAuth app/device-flow service and network access | CLI `qubit auth login --provider github` (manual handoff in the operator's browser) | `voicebridge/gateway/openclaw_handlers/users.py:323` |
| `users.github.disconnect` | `GITHUB_OPERATOR_REQUIRED` | a linked GitHub account (requires the live OAuth app) | voicebridge.credentials.remove (confirm-gated) removes the credential reference | `voicebridge/gateway/openclaw_handlers/users.py:356` |
| `users.github.status` | `GITHUB_OPERATOR_REQUIRED` | a GitHub credential reference (env:/file:) with the required scopes and network access | CLI `qubit auth status` + an operator-managed credential reference | `voicebridge/gateway/openclaw_handlers/users.py:312` |
| `users.linkEmail` | `USERS_EMAIL_OPERATOR_REQUIRED` | an email/identity verification service | voicebridge.qubit_compat.h_auth_me (loopback identity; email empty) | `voicebridge/gateway/openclaw_handlers/users.py:414` |

## 2. `architecture-gated` (27)

Each row states what Qubit would have to change and whether that is a real capability or an intentional architecture choice. These are the rows that need the owner's terminal ruling.

| method | typed code | exact prerequisite | local counterpart / refusal | refusal evidence | what Qubit would need to change | real capability or intentional choice |
|---|---|---|---|---|---|---|
| `agents.create` | `AGENTS_MULTI_AGENT_OPERATOR_REQUIRED` | an upstream multi-agent runtime; Qubit is intentionally a single unified agent | the single canonical agent `qubit-worker` managed via config + the provider registry | `voicebridge/gateway/openclaw_handlers/agents.py:173` | Add a second agent roster with create/update/delete lifecycle (registry, per-agent config, routing) alongside the single runtime, and a routing authority that selects between agents. | Real upstream capability, but supporting it contradicts the frozen single-unified-agent owner decision; it is an intentional architecture choice, not a provisioning gap. |
| `agents.delete` | `AGENTS_MULTI_AGENT_OPERATOR_REQUIRED` | an upstream multi-agent runtime; Qubit is intentionally a single unified agent | the single canonical agent `qubit-worker` managed via config + the provider registry | `voicebridge/gateway/openclaw_handlers/agents.py:173` | Add a second agent roster with create/update/delete lifecycle (registry, per-agent config, routing) alongside the single runtime, and a routing authority that selects between agents. | Real upstream capability, but supporting it contradicts the frozen single-unified-agent owner decision; it is an intentional architecture choice, not a provisioning gap. |
| `agents.update` | `AGENTS_MULTI_AGENT_OPERATOR_REQUIRED` | an upstream multi-agent runtime; Qubit is intentionally a single unified agent | the single canonical agent `qubit-worker` managed via config + the provider registry | `voicebridge/gateway/openclaw_handlers/agents.py:173` | Add a second agent roster with create/update/delete lifecycle (registry, per-agent config, routing) alongside the single runtime, and a routing authority that selects between agents. | Real upstream capability, but supporting it contradicts the frozen single-unified-agent owner decision; it is an intentional architecture choice, not a provisioning gap. |
| `attach.grant` | `ATTACH_MCP_LOOPBACK_OPERATOR_REQUIRED` | an MCP loopback attach server + a paired browser device identity | voicebridge.session_share + device tokens | `voicebridge/gateway/openclaw_handlers/security.py:243` | Run an MCP loopback attach server and a browser device-pairing handshake, then bind it to the canonical session/device authority. | Real capability of the upstream server transport; Qubit deliberately runs one in-process MCP host plus session_share + device tokens, so this is an intentional architecture choice. |
| `attach.revoke` | `ATTACH_MCP_LOOPBACK_OPERATOR_REQUIRED` | an MCP loopback attach server + a paired browser device identity | voicebridge.session_share + device tokens | `voicebridge/gateway/openclaw_handlers/security.py:243` | Run an MCP loopback attach server and a browser device-pairing handshake, then bind it to the canonical session/device authority. | Real capability of the upstream server transport; Qubit deliberately runs one in-process MCP host plus session_share + device tokens, so this is an intentional architecture choice. |
| `board.action` | `BOARD_ACTION_OPERATOR_REQUIRED` | a board widget sandbox + capability authority runtime | `qubit cron` (schedules) + the shared confirmation journal | `voicebridge/gateway/openclaw_handlers/board.py:170` | Add a board widget sandbox and a capability-authority runtime that executes widget actions under signed grants. | Real upstream capability; Qubit intentionally keeps boards as the slug-scoped kanban canvas with role grants and the shared confirmation journal, so this is architecture-gated. |
| `board.event` | `BOARD_EVENT_OPERATOR_REQUIRED` | a board widget sandbox runtime + signed view tickets | `qubit board log` (kanban board event log) | `voicebridge/gateway/openclaw_handlers/board.py:149` | Add a board widget sandbox runtime plus signed view tickets for widget-sourced events. | Real upstream capability; Qubit deliberately has no widget sandbox, so this is an intentional architecture choice. |
| `board.prompt.authorize` | `BOARD_VIEW_TICKET_OPERATOR_REQUIRED` | a signed board view-ticket runtime | `qubit board roles` + the shared confirmation journal | `voicebridge/gateway/openclaw_handlers/board.py:161` | Add a signed board view-ticket runtime and bind it to the board authorization path. | Real upstream capability; Qubit authorizes boards through kanban roles and the confirmation journal, so this is an intentional architecture choice. |
| `cron.scratch.get` | `CRON_SCRATCH_OPERATOR_REQUIRED` | an upstream per-job cron scratch store (cron_job_scratch); Qubit runs none | voicebridge.schedules job fields + memory workspace notes (`qubit memory note`) | `voicebridge/gateway/openclaw_handlers/scheduler_media.py:242` | Add a private per-job scratch column/store to the schedule authority and expose read/write over it. | Real capability, but it is a small internal state store Qubit intentionally does not keep; the local equivalent is a job field plus the memory workspace, so it is architecture-gated. |
| `cron.scratch.set` | `CRON_SCRATCH_OPERATOR_REQUIRED` | an upstream per-job cron scratch store (cron_job_scratch); Qubit runs none | voicebridge.schedules job fields + memory workspace notes (`qubit memory note`) | `voicebridge/gateway/openclaw_handlers/scheduler_media.py:242` | Add a private per-job scratch column/store to the schedule authority and expose read/write over it. | Real capability, but it is a small internal state store Qubit intentionally does not keep; the local equivalent is a job field plus the memory workspace, so it is architecture-gated. |
| `mcp.app.updateModelContext` | `MCP_APP_SANDBOX_OPERATOR_REQUIRED` | an MCP App sandbox runtime + an active app view | voicebridge.plugin_runtime.mcp_doctor (in-process MCP host diagnostics) | `voicebridge/gateway/openclaw_handlers/mcp_app.py:233` | Add an MCP App sandbox runtime with an active app view and bind it to the in-process MCP host. | Real upstream capability; Qubit deliberately runs only the in-process MCP host in plugin_runtime, so this is an intentional architecture choice. |
| `mentions.dismiss` | `MENTIONS_INBOX_OPERATOR_REQUIRED` | a human-mention inbox service backed by configured message channels | voicebridge.automation outbox/orders (`qubit channels`) | `voicebridge/gateway/openclaw_handlers/scheduler_media.py:472` | Run a human-mention inbox service fed by configured message channels and persist mentions. | Real upstream capability; Qubit intentionally runs no inbox service and uses the automation outbox/orders, so this is architecture-gated. |
| `mentions.list` | `MENTIONS_INBOX_OPERATOR_REQUIRED` | a human-mention inbox service backed by configured message channels | voicebridge.automation outbox/orders (`qubit channels`) | `voicebridge/gateway/openclaw_handlers/scheduler_media.py:472` | Run a human-mention inbox service fed by configured message channels and persist mentions. | Real upstream capability; Qubit intentionally runs no inbox service and uses the automation outbox/orders, so this is architecture-gated. |
| `skills.proposals.evaluate` | `SKILLS_PROPOSALS_EVALUATE_OPERATOR_REQUIRED` | an isolated proposal-evaluation model runtime (Qubit has no self-evaluation path) | voicebridge.verified_skills.VerifiedSkillStore.apply_verdict (human owner verdict) + voicebridge.evaluation promotion gate | `voicebridge/gateway/openclaw_handlers/skills_extra.py:406` | Add an isolated model runtime that scores skill proposals, then feed its verdict into the promotion gate. | Real upstream capability, but Qubit deliberately verifies skills only by explicit human owner verdict (no self-evaluation path); it is an intentional architecture choice. |
| `skills.proposals.historyScan` | `SKILLS_PROPOSALS_HISTORYSCAN_OPERATOR_REQUIRED` | a session-history scan runtime (isolated model over the transcript) that emits pending proposals | voicebridge.verified_skills.workshop.SkillWorkshop.author (authored/quarantined proposals) | `voicebridge/gateway/openclaw_handlers/skills_extra.py:421` | Add a session-history scanner (isolated model over the transcript) that emits pending skill proposals. | Real upstream capability; Qubit intentionally runs no history scanner and relies on the skill workshop author flow, so this is architecture-gated. |
| `skills.upload.begin` | `SKILLS_UPLOAD_OPERATOR_REQUIRED` | a staged chunked upload store/transport for skill archives | voicebridge.verified_skills.mutations (single-shot reviewed manifest) + verified_skills._transfer snapshot restore | `voicebridge/gateway/openclaw_handlers/skills_extra.py:447` | Add a staged chunked upload store/transport for skill archives plus the begin/chunk/commit protocol. | Real upstream capability, but Qubit intentionally installs only reviewed local manifests (single-shot mutation + snapshot restore), so it is architecture-gated. |
| `skills.upload.chunk` | `SKILLS_UPLOAD_OPERATOR_REQUIRED` | a staged chunked upload store/transport for skill archives | voicebridge.verified_skills.mutations (single-shot reviewed manifest) + verified_skills._transfer snapshot restore | `voicebridge/gateway/openclaw_handlers/skills_extra.py:459` | Add a staged chunked upload store/transport for skill archives plus the begin/chunk/commit protocol. | Real upstream capability, but Qubit intentionally installs only reviewed local manifests (single-shot mutation + snapshot restore), so it is architecture-gated. |
| `skills.upload.commit` | `SKILLS_UPLOAD_OPERATOR_REQUIRED` | a staged chunked upload store/transport for skill archives | voicebridge.verified_skills.mutations (single-shot reviewed manifest) + verified_skills._transfer snapshot restore | `voicebridge/gateway/openclaw_handlers/skills_extra.py:471` | Add a staged chunked upload store/transport for skill archives plus the begin/chunk/commit protocol. | Real upstream capability, but Qubit intentionally installs only reviewed local manifests (single-shot mutation + snapshot restore), so it is architecture-gated. |
| `taskSuggestions.accept` | `TASK_SUGGESTIONS_OPERATOR_REQUIRED` | an upstream model-proposed task-suggestion registry + run-control runtime; Qubit runs none | durable scheduled work via voicebridge.schedules.create_job (`qubit cron add`) + the canonical chat session | `voicebridge/gateway/openclaw_handlers/scheduler_media.py:342` | Add an ephemeral task-suggestion registry and a run-control runtime that can accept a suggestion into a live run. | Real upstream capability; Qubit deliberately keeps no ephemeral suggestion registry and uses durable schedules + the chat session, so this is architecture-gated. |
| `taskSuggestions.create` | `TASK_SUGGESTIONS_OPERATOR_REQUIRED` | an upstream model-proposed task-suggestion registry + run-control runtime; Qubit runs none | durable scheduled work via voicebridge.schedules.create_job (`qubit cron add`) + the canonical chat session | `voicebridge/gateway/openclaw_handlers/scheduler_media.py:342` | Add an ephemeral task-suggestion registry and a run-control runtime that can accept a suggestion into a live run. | Real upstream capability; Qubit deliberately keeps no ephemeral suggestion registry and uses durable schedules + the chat session, so this is architecture-gated. |
| `taskSuggestions.dismiss` | `TASK_SUGGESTIONS_OPERATOR_REQUIRED` | an upstream model-proposed task-suggestion registry + run-control runtime; Qubit runs none | durable scheduled work via voicebridge.schedules.create_job (`qubit cron add`) + the canonical chat session | `voicebridge/gateway/openclaw_handlers/scheduler_media.py:342` | Add an ephemeral task-suggestion registry and a run-control runtime that can accept a suggestion into a live run. | Real upstream capability; Qubit deliberately keeps no ephemeral suggestion registry and uses durable schedules + the chat session, so this is architecture-gated. |
| `tasks.dismiss` | `TASKS_DISMISS_OPERATOR_REQUIRED` | an upstream detached-task/subagent completion-delivery registry; Qubit keeps none | the canonical read-only task ledger (`qubit tasks`) | `voicebridge/gateway/openclaw_handlers/scheduler_media.py:456` | Add a detached-task/subagent completion-delivery registry that can be dismissed. | Real upstream capability; Qubit intentionally keeps the task ledger read-only, so this is an intentional architecture choice. |
| `users.setAvatar` | `USERS_AVATAR_OPERATOR_REQUIRED` | an operator-managed avatar/media store and hosting | voicebridge.device.profile (local device identity; no avatar field) | `voicebridge/gateway/openclaw_handlers/users.py:404` | Add an operator avatar/media store and media hosting, then bind it to the operator profile. | Real upstream capability; Qubit deliberately keeps no avatar field and hosts no media, so this is an intentional architecture choice. |
| `users.setDisplayName` | `USERS_PROFILE_OPERATOR_REQUIRED` | a multi-user profile store (Qubit is single-operator) | voicebridge.qubit_compat.h_auth_me (fixed operator display name) | `voicebridge/gateway/openclaw_handlers/users.py:425` | Add a multi-user profile and role store and a role-assignment authority. | Real upstream capability, but supporting it contradicts the single-operator design with fixed operator scopes; it is an intentional architecture choice. |
| `users.setRole` | `USERS_PROFILE_OPERATOR_REQUIRED` | a multi-user profile/role store (Qubit is single-operator) | the fixed operator scopes advertised by the gateway `connect` handshake | `voicebridge/gateway/openclaw_handlers/users.py:435` | Add a multi-user profile and role store and a role-assignment authority. | Real upstream capability, but supporting it contradicts the single-operator design with fixed operator scopes; it is an intentional architecture choice. |
| `voicewake.routing.get` | `WAKE_ROUTING_OPERATOR_REQUIRED` | a configurable wake-trigger/routing store | voicewake.get + voicebridge.wake.status (single shared audio listener) | `voicebridge/gateway/openclaw_handlers/misc_surface.py:173` | Add a configurable wake-trigger/routing store read/write layer on the shared audio listener. | Real upstream capability; Qubit deliberately routes wake detection through the one audio listener with an operator-fetched keyword bundle, so this is an intentional architecture choice. |
| `voicewake.set` | `WAKE_TRIGGERS_OPERATOR_REQUIRED` | a configurable wake-trigger store (writable keywords/model bundle) | voicewake.get + voicebridge.wake.status (single shared audio listener) | `voicebridge/gateway/openclaw_handlers/misc_surface.py:159` | Add a writable wake-trigger store bound to the wake phrase/model bundle. | Real upstream capability; Qubit's wake phrases come from the operator-fetched sherpa-onnx keyword bundle, so persisting triggers is an intentional architecture choice. |

## Notes and uncertainty

- Hybrid prerequisites (a service **and** an internal store) are classified by the primary provisionable primitive: `push.web.*` (web-push service + subscription store) and `portal.*` (hosted portal service) are `external-prerequisite`; `attach.*` (MCP loopback attach server + browser device identity) is `architecture-gated` because the missing primary is an upstream server Qubit does not run.
- `talk.*` is `external-prerequisite` because the missing primitive is an external provider transport, even though Qubit deliberately removed the realtime lane; whether to re-admit the lane is an owner decision.
- `skills.proposals.*` is `architecture-gated`: the missing primitive is Qubit's deliberate absence of a self-evaluation / history-scan runtime, not an external credential.
- `agents.*` and `users.setDisplayName` / `users.setRole` are `architecture-gated` by intentional single-agent / single-operator design.
- Classification rests on the recorded `operator_prerequisite` and the handler refusal detail; it is a judgement and should be confirmed by the owner. No disposition is changed here.

## Machine-readable index

<!-- QUBIT_OPERATOR_ONLY_REVIEW_JSON
{
  "schema": "qubit-openclaw-operator-only-review/1",
  "manifest": "docs/generated/QUBIT_OPENCLAW_UPGRADE_MANIFEST.json",
  "counts": {
    "total": 95,
    "external-prerequisite": 68,
    "architecture-gated": 27
  },
  "rows": [
    {
      "method": "agents.create",
      "code": "AGENTS_MULTI_AGENT_OPERATOR_REQUIRED",
      "prerequisite": "an upstream multi-agent runtime; Qubit is intentionally a single unified agent",
      "classification": "architecture-gated",
      "category": "intentional single-agent architecture",
      "rationale": "Qubit is intentionally one unified agent (owner decision: one runtime, one source tree, no second agent registry). A multi-agent roster is not an external resource.",
      "counterpart": "the single canonical agent `qubit-worker` managed via config + the provider registry",
      "evidence": "voicebridge/gateway/openclaw_handlers/agents.py:173",
      "basis": "",
      "would_need": "Add a second agent roster with create/update/delete lifecycle (registry, per-agent config, routing) alongside the single runtime, and a routing authority that selects between agents.",
      "choice": "Real upstream capability, but supporting it contradicts the frozen single-unified-agent owner decision; it is an intentional architecture choice, not a provisioning gap."
    },
    {
      "method": "agents.delete",
      "code": "AGENTS_MULTI_AGENT_OPERATOR_REQUIRED",
      "prerequisite": "an upstream multi-agent runtime; Qubit is intentionally a single unified agent",
      "classification": "architecture-gated",
      "category": "intentional single-agent architecture",
      "rationale": "Qubit is intentionally one unified agent (owner decision: one runtime, one source tree, no second agent registry). A multi-agent roster is not an external resource.",
      "counterpart": "the single canonical agent `qubit-worker` managed via config + the provider registry",
      "evidence": "voicebridge/gateway/openclaw_handlers/agents.py:173",
      "basis": "",
      "would_need": "Add a second agent roster with create/update/delete lifecycle (registry, per-agent config, routing) alongside the single runtime, and a routing authority that selects between agents.",
      "choice": "Real upstream capability, but supporting it contradicts the frozen single-unified-agent owner decision; it is an intentional architecture choice, not a provisioning gap."
    },
    {
      "method": "agents.update",
      "code": "AGENTS_MULTI_AGENT_OPERATOR_REQUIRED",
      "prerequisite": "an upstream multi-agent runtime; Qubit is intentionally a single unified agent",
      "classification": "architecture-gated",
      "category": "intentional single-agent architecture",
      "rationale": "Qubit is intentionally one unified agent (owner decision: one runtime, one source tree, no second agent registry). A multi-agent roster is not an external resource.",
      "counterpart": "the single canonical agent `qubit-worker` managed via config + the provider registry",
      "evidence": "voicebridge/gateway/openclaw_handlers/agents.py:173",
      "basis": "",
      "would_need": "Add a second agent roster with create/update/delete lifecycle (registry, per-agent config, routing) alongside the single runtime, and a routing authority that selects between agents.",
      "choice": "Real upstream capability, but supporting it contradicts the frozen single-unified-agent owner decision; it is an intentional architecture choice, not a provisioning gap."
    },
    {
      "method": "attach.grant",
      "code": "ATTACH_MCP_LOOPBACK_OPERATOR_REQUIRED",
      "prerequisite": "an MCP loopback attach server + a paired browser device identity",
      "classification": "architecture-gated",
      "category": "upstream server/transport (MCP loopback attach)",
      "rationale": "Requires an MCP loopback attach server + a paired browser device identity; Qubit runs no attach server and uses session_share + device tokens instead.",
      "counterpart": "voicebridge.session_share + device tokens",
      "evidence": "voicebridge/gateway/openclaw_handlers/security.py:243",
      "basis": "",
      "would_need": "Run an MCP loopback attach server and a browser device-pairing handshake, then bind it to the canonical session/device authority.",
      "choice": "Real capability of the upstream server transport; Qubit deliberately runs one in-process MCP host plus session_share + device tokens, so this is an intentional architecture choice."
    },
    {
      "method": "attach.revoke",
      "code": "ATTACH_MCP_LOOPBACK_OPERATOR_REQUIRED",
      "prerequisite": "an MCP loopback attach server + a paired browser device identity",
      "classification": "architecture-gated",
      "category": "upstream server/transport (MCP loopback attach)",
      "rationale": "Requires an MCP loopback attach server + a paired browser device identity; Qubit runs no attach server and uses session_share + device tokens instead.",
      "counterpart": "voicebridge.session_share + device tokens",
      "evidence": "voicebridge/gateway/openclaw_handlers/security.py:243",
      "basis": "",
      "would_need": "Run an MCP loopback attach server and a browser device-pairing handshake, then bind it to the canonical session/device authority.",
      "choice": "Real capability of the upstream server transport; Qubit deliberately runs one in-process MCP host plus session_share + device tokens, so this is an intentional architecture choice."
    },
    {
      "method": "board.action",
      "code": "BOARD_ACTION_OPERATOR_REQUIRED",
      "prerequisite": "a board widget sandbox + capability authority runtime",
      "classification": "architecture-gated",
      "category": "board widget sandbox runtime",
      "rationale": "Requires a board widget sandbox + capability authority runtime; Qubit's board authority is the slug-scoped kanban canvas with role grants.",
      "counterpart": "`qubit cron` (schedules) + the shared confirmation journal",
      "evidence": "voicebridge/gateway/openclaw_handlers/board.py:170",
      "basis": "",
      "would_need": "Add a board widget sandbox and a capability-authority runtime that executes widget actions under signed grants.",
      "choice": "Real upstream capability; Qubit intentionally keeps boards as the slug-scoped kanban canvas with role grants and the shared confirmation journal, so this is architecture-gated."
    },
    {
      "method": "board.event",
      "code": "BOARD_EVENT_OPERATOR_REQUIRED",
      "prerequisite": "a board widget sandbox runtime + signed view tickets",
      "classification": "architecture-gated",
      "category": "board widget sandbox runtime",
      "rationale": "Requires a board widget sandbox runtime + signed view tickets; Qubit models boards as kanban tabs/widgets by id/slug.",
      "counterpart": "`qubit board log` (kanban board event log)",
      "evidence": "voicebridge/gateway/openclaw_handlers/board.py:149",
      "basis": "",
      "would_need": "Add a board widget sandbox runtime plus signed view tickets for widget-sourced events.",
      "choice": "Real upstream capability; Qubit deliberately has no widget sandbox, so this is an intentional architecture choice."
    },
    {
      "method": "board.prompt.authorize",
      "code": "BOARD_VIEW_TICKET_OPERATOR_REQUIRED",
      "prerequisite": "a signed board view-ticket runtime",
      "classification": "architecture-gated",
      "category": "signed board view-ticket runtime",
      "rationale": "Requires a signed board view-ticket runtime; Qubit authorizes board access through kanban roles plus the shared confirmation journal.",
      "counterpart": "`qubit board roles` + the shared confirmation journal",
      "evidence": "voicebridge/gateway/openclaw_handlers/board.py:161",
      "basis": "",
      "would_need": "Add a signed board view-ticket runtime and bind it to the board authorization path.",
      "choice": "Real upstream capability; Qubit authorizes boards through kanban roles and the confirmation journal, so this is an intentional architecture choice."
    },
    {
      "method": "channels.logout",
      "code": "CHANNEL_LISTENER_OPERATOR_REQUIRED",
      "prerequisite": "configured inbound channel transport + credential",
      "classification": "external-prerequisite",
      "category": "inbound channel transport",
      "rationale": "Requires a configured inbound channel transport (webhook/poll) and its credential; Qubit stores references and runs no persistent listener.",
      "counterpart": "`qubit channels configure/unconfigure`",
      "evidence": "voicebridge/gateway/openclaw_handlers/content.py:774",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "channels.start",
      "code": "CHANNEL_LISTENER_OPERATOR_REQUIRED",
      "prerequisite": "configured inbound channel transport + credential",
      "classification": "external-prerequisite",
      "category": "inbound channel transport",
      "rationale": "Requires a configured inbound channel transport (webhook/poll) and its credential; Qubit stores references and runs no persistent listener.",
      "counterpart": "`qubit channels status/configure` + channels_inbound ingress",
      "evidence": "voicebridge/gateway/openclaw_handlers/content.py:751",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "channels.stop",
      "code": "CHANNEL_LISTENER_OPERATOR_REQUIRED",
      "prerequisite": "configured inbound channel transport + credential",
      "classification": "external-prerequisite",
      "category": "inbound channel transport",
      "rationale": "Requires a configured inbound channel transport (webhook/poll) and its credential; Qubit stores references and runs no persistent listener.",
      "counterpart": "`qubit channels status/configure` + channels_inbound ingress",
      "evidence": "voicebridge/gateway/openclaw_handlers/content.py:763",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "controlUi.githubPreview",
      "code": "FORGE_API_OPERATOR_REQUIRED",
      "prerequisite": "a configured forge credential (e.g. a GitHub/GitLab token) and a network-reachable forge API",
      "classification": "external-prerequisite",
      "category": "git forge credential / API",
      "rationale": "Requires a configured forge credential (for example a GitHub/GitLab token) and a network-reachable forge API.",
      "counterpart": "local change review: CLI `qubit git status|log|diff|show` and the dashboard git review surface",
      "evidence": "voicebridge/gateway/openclaw_handlers/misc_surface.py:280",
      "basis": "no remote GitHub preview is fetched; Qubit runs no forge service and stores no forge token by default, so the typed code is returned",
      "would_need": null,
      "choice": null
    },
    {
      "method": "cron.scratch.get",
      "code": "CRON_SCRATCH_OPERATOR_REQUIRED",
      "prerequisite": "an upstream per-job cron scratch store (cron_job_scratch); Qubit runs none",
      "classification": "architecture-gated",
      "category": "per-job scratch store",
      "rationale": "Requires an upstream per-job cron scratch store (cron_job_scratch); Qubit's schedule store has no private per-job scratch column.",
      "counterpart": "voicebridge.schedules job fields + memory workspace notes (`qubit memory note`)",
      "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py:242",
      "basis": "Qubit's schedule store has no private per-job scratch column and no scratch store exists, so nothing is read",
      "would_need": "Add a private per-job scratch column/store to the schedule authority and expose read/write over it.",
      "choice": "Real capability, but it is a small internal state store Qubit intentionally does not keep; the local equivalent is a job field plus the memory workspace, so it is architecture-gated."
    },
    {
      "method": "cron.scratch.set",
      "code": "CRON_SCRATCH_OPERATOR_REQUIRED",
      "prerequisite": "an upstream per-job cron scratch store (cron_job_scratch); Qubit runs none",
      "classification": "architecture-gated",
      "category": "per-job scratch store",
      "rationale": "Requires an upstream per-job cron scratch store (cron_job_scratch); Qubit's schedule store has no private per-job scratch column.",
      "counterpart": "voicebridge.schedules job fields + memory workspace notes (`qubit memory note`)",
      "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py:242",
      "basis": "Qubit's schedule store has no private per-job scratch column and no scratch store exists, so nothing is written",
      "would_need": "Add a private per-job scratch column/store to the schedule authority and expose read/write over it.",
      "choice": "Real capability, but it is a small internal state store Qubit intentionally does not keep; the local equivalent is a job field plus the memory workspace, so it is architecture-gated."
    },
    {
      "method": "environments.create",
      "code": "ENVIRONMENT_CLOUD_OPERATOR_REQUIRED",
      "prerequisite": "injected cloud-worker provider transport",
      "classification": "external-prerequisite",
      "category": "cloud-worker transport",
      "rationale": "Requires an injected cloud-worker provider transport and a reachable provider to create/destroy remote environments.",
      "counterpart": "voicebridge.cloud_workers + local process cells (`qubit runtime cells`)",
      "evidence": "voicebridge/gateway/openclaw_handlers/system.py:504",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "environments.destroy",
      "code": "ENVIRONMENT_CLOUD_OPERATOR_REQUIRED",
      "prerequisite": "injected cloud-worker provider transport",
      "classification": "external-prerequisite",
      "category": "cloud-worker transport",
      "rationale": "Requires an injected cloud-worker provider transport and a reachable provider to create/destroy remote environments.",
      "counterpart": "voicebridge.cloud_workers + local process cells (`qubit runtime cells`)",
      "evidence": "voicebridge/gateway/openclaw_handlers/system.py:515",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "exec.approvals.node.set",
      "code": "EXEC_APPROVALS_NODE_OPERATOR_REQUIRED",
      "prerequisite": "a paired node agent that owns its own exec policy",
      "classification": "external-prerequisite",
      "category": "paired node agent",
      "rationale": "Requires a paired node agent that owns its own exec policy; Qubit's node host is enroll/revoke/show/dispatch only.",
      "counterpart": "`qubit node enroll/revoke` + broker confirmation policy",
      "evidence": "voicebridge/gateway/openclaw_handlers/security.py:161",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "gateway.restart.request",
      "code": "GATEWAY_RESTART_OPERATOR_REQUIRED",
      "prerequisite": "operator restart of the Qubit service unit",
      "classification": "external-prerequisite",
      "category": "operator service restart",
      "rationale": "Requires the operator to restart the Qubit service unit; a gateway call never exits the process.",
      "counterpart": "CLI `qubit setup health` + the operator runbook",
      "evidence": "voicebridge/gateway/openclaw_handlers/system.py:710",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "gateway.suspend.handoff",
      "code": "SUSPEND_HANDOFF_OPERATOR_REQUIRED",
      "prerequisite": "a service manager that owns process exit (systemd/launchd external restart owner)",
      "classification": "external-prerequisite",
      "category": "service manager process ownership",
      "rationale": "Requires a service manager that owns process exit (systemd/launchd external restart owner); Qubit never exits its own process.",
      "counterpart": "operator `systemctl --user restart <qubit unit>` + the operator runbook",
      "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py:294",
      "basis": "upstream arms a host-lifecycle handoff that exits the gateway process; Qubit never exits its own process from a gateway call",
      "would_need": null,
      "choice": null
    },
    {
      "method": "mcp.app.listResourceTemplates",
      "code": "MCP_RESOURCE_TEMPLATES_OPERATOR_REQUIRED",
      "prerequisite": "an MCP server/runtime that advertises resource templates",
      "classification": "external-prerequisite",
      "category": "external MCP server",
      "rationale": "Requires an MCP server/runtime that advertises resource templates; Qubit's in-tree MCP tool layer exposes resources, not templates.",
      "counterpart": "the MCP tool layer's `list_resources` (qubit_runtime/tools/mcp_tool.py)",
      "evidence": "voicebridge/gateway/openclaw_handlers/mcp_app.py:221",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "mcp.app.updateModelContext",
      "code": "MCP_APP_SANDBOX_OPERATOR_REQUIRED",
      "prerequisite": "an MCP App sandbox runtime + an active app view",
      "classification": "architecture-gated",
      "category": "MCP App sandbox runtime",
      "rationale": "Requires an MCP App sandbox runtime + an active app view; Qubit's MCP authority is the in-process host in plugin_runtime.",
      "counterpart": "voicebridge.plugin_runtime.mcp_doctor (in-process MCP host diagnostics)",
      "evidence": "voicebridge/gateway/openclaw_handlers/mcp_app.py:233",
      "basis": "",
      "would_need": "Add an MCP App sandbox runtime with an active app view and bind it to the in-process MCP host.",
      "choice": "Real upstream capability; Qubit deliberately runs only the in-process MCP host in plugin_runtime, so this is an intentional architecture choice."
    },
    {
      "method": "mentions.dismiss",
      "code": "MENTIONS_INBOX_OPERATOR_REQUIRED",
      "prerequisite": "a human-mention inbox service backed by configured message channels",
      "classification": "architecture-gated",
      "category": "human-mention inbox service",
      "rationale": "Requires a human-mention inbox service backed by configured message channels; Qubit runs no inbox registry.",
      "counterpart": "voicebridge.automation outbox/orders (`qubit channels`)",
      "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py:472",
      "basis": "Qubit runs no human-mention inbox service, so no mention is dismissed",
      "would_need": "Run a human-mention inbox service fed by configured message channels and persist mentions.",
      "choice": "Real upstream capability; Qubit intentionally runs no inbox service and uses the automation outbox/orders, so this is architecture-gated."
    },
    {
      "method": "mentions.list",
      "code": "MENTIONS_INBOX_OPERATOR_REQUIRED",
      "prerequisite": "a human-mention inbox service backed by configured message channels",
      "classification": "architecture-gated",
      "category": "human-mention inbox service",
      "rationale": "Requires a human-mention inbox service backed by configured message channels; Qubit runs no inbox registry.",
      "counterpart": "voicebridge.automation outbox/orders (`qubit channels`)",
      "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py:472",
      "basis": "Qubit runs no human-mention inbox service, so no mention is listed",
      "would_need": "Run a human-mention inbox service fed by configured message channels and persist mentions.",
      "choice": "Real upstream capability; Qubit intentionally runs no inbox service and uses the automation outbox/orders, so this is architecture-gated."
    },
    {
      "method": "message.action",
      "code": "MESSAGING_TRANSPORT_OPERATOR_REQUIRED",
      "prerequisite": "configured messaging account and reachable provider transport",
      "classification": "external-prerequisite",
      "category": "messaging provider transport",
      "rationale": "Requires a configured messaging account (provider credential + endpoint) and a reachable provider transport.",
      "counterpart": "`qubit msg draft` -> `qubit msg approve` -> `qubit msg send --confirm` (voicebridge.messaging, e-stop/confirm gated)",
      "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py:1168",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "nativeHook.invoke",
      "code": "NATIVE_HOOK_RELAY_OPERATOR_REQUIRED",
      "prerequisite": "a live native harness process exposing a registered hook relay (provider/relayId/generation)",
      "classification": "external-prerequisite",
      "category": "live native-harness relay",
      "rationale": "Requires a live native harness process exposing a registered hook relay (provider/relayId/generation).",
      "counterpart": "voicebridge.hooks (canonical hook declarations) + voicebridge.node_host",
      "evidence": "voicebridge/gateway/openclaw_handlers/misc_surface.py:349",
      "basis": "relay invocation is a one-shot bridge into a live native harness; Qubit runs no native harness relay, so no hook is invoked",
      "would_need": null,
      "choice": null
    },
    {
      "method": "node.event",
      "code": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
      "prerequisite": "a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority)",
      "classification": "external-prerequisite",
      "category": "paired node device",
      "rationale": "Requires a paired node device/agent host running the native-harness relay; the device is external to the single local Qubit authority and can be provisioned by the operator.",
      "counterpart": "voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...`",
      "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py:134",
      "basis": "upstream carries this through a connected paired node agent relay (queue/invoke-result/event/catalog publication); Qubit's canonical device host is enroll/revoke/show/dispatch only and runs no relay, so the typed code is returned and nothing is fabricated",
      "would_need": null,
      "choice": null
    },
    {
      "method": "node.invoke.progress",
      "code": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
      "prerequisite": "a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority)",
      "classification": "external-prerequisite",
      "category": "paired node device",
      "rationale": "Requires a paired node device/agent host running the native-harness relay; the device is external to the single local Qubit authority and can be provisioned by the operator.",
      "counterpart": "voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...`",
      "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py:134",
      "basis": "upstream carries this through a connected paired node agent relay (queue/invoke-result/event/catalog publication); Qubit's canonical device host is enroll/revoke/show/dispatch only and runs no relay, so the typed code is returned and nothing is fabricated",
      "would_need": null,
      "choice": null
    },
    {
      "method": "node.invoke.result",
      "code": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
      "prerequisite": "a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority)",
      "classification": "external-prerequisite",
      "category": "paired node device",
      "rationale": "Requires a paired node device/agent host running the native-harness relay; the device is external to the single local Qubit authority and can be provisioned by the operator.",
      "counterpart": "voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...`",
      "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py:134",
      "basis": "upstream carries this through a connected paired node agent relay (queue/invoke-result/event/catalog publication); Qubit's canonical device host is enroll/revoke/show/dispatch only and runs no relay, so the typed code is returned and nothing is fabricated",
      "would_need": null,
      "choice": null
    },
    {
      "method": "node.pending.ack",
      "code": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
      "prerequisite": "a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority)",
      "classification": "external-prerequisite",
      "category": "paired node device",
      "rationale": "Requires a paired node device/agent host running the native-harness relay; the device is external to the single local Qubit authority and can be provisioned by the operator.",
      "counterpart": "voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...`",
      "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py:134",
      "basis": "upstream carries this through a connected paired node agent relay (queue/invoke-result/event/catalog publication); Qubit's canonical device host is enroll/revoke/show/dispatch only and runs no relay, so the typed code is returned and nothing is fabricated",
      "would_need": null,
      "choice": null
    },
    {
      "method": "node.pending.drain",
      "code": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
      "prerequisite": "a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority)",
      "classification": "external-prerequisite",
      "category": "paired node device",
      "rationale": "Requires a paired node device/agent host running the native-harness relay; the device is external to the single local Qubit authority and can be provisioned by the operator.",
      "counterpart": "voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...`",
      "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py:134",
      "basis": "upstream carries this through a connected paired node agent relay (queue/invoke-result/event/catalog publication); Qubit's canonical device host is enroll/revoke/show/dispatch only and runs no relay, so the typed code is returned and nothing is fabricated",
      "would_need": null,
      "choice": null
    },
    {
      "method": "node.pending.enqueue",
      "code": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
      "prerequisite": "a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority)",
      "classification": "external-prerequisite",
      "category": "paired node device",
      "rationale": "Requires a paired node device/agent host running the native-harness relay; the device is external to the single local Qubit authority and can be provisioned by the operator.",
      "counterpart": "voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...`",
      "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py:134",
      "basis": "upstream carries this through a connected paired node agent relay (queue/invoke-result/event/catalog publication); Qubit's canonical device host is enroll/revoke/show/dispatch only and runs no relay, so the typed code is returned and nothing is fabricated",
      "would_need": null,
      "choice": null
    },
    {
      "method": "node.pending.pull",
      "code": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
      "prerequisite": "a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority)",
      "classification": "external-prerequisite",
      "category": "paired node device",
      "rationale": "Requires a paired node device/agent host running the native-harness relay; the device is external to the single local Qubit authority and can be provisioned by the operator.",
      "counterpart": "voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...`",
      "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py:134",
      "basis": "upstream carries this through a connected paired node agent relay (queue/invoke-result/event/catalog publication); Qubit's canonical device host is enroll/revoke/show/dispatch only and runs no relay, so the typed code is returned and nothing is fabricated",
      "would_need": null,
      "choice": null
    },
    {
      "method": "node.pluginTools.update",
      "code": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
      "prerequisite": "a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority)",
      "classification": "external-prerequisite",
      "category": "paired node device",
      "rationale": "Requires a paired node device/agent host running the native-harness relay; the device is external to the single local Qubit authority and can be provisioned by the operator.",
      "counterpart": "voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...`",
      "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py:134",
      "basis": "upstream carries this through a connected paired node agent relay (queue/invoke-result/event/catalog publication); Qubit's canonical device host is enroll/revoke/show/dispatch only and runs no relay, so the typed code is returned and nothing is fabricated",
      "would_need": null,
      "choice": null
    },
    {
      "method": "node.runnerInventory.update",
      "code": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
      "prerequisite": "a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority)",
      "classification": "external-prerequisite",
      "category": "paired node device",
      "rationale": "Requires a paired node device/agent host running the native-harness relay; the device is external to the single local Qubit authority and can be provisioned by the operator.",
      "counterpart": "voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...`",
      "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py:134",
      "basis": "upstream carries this through a connected paired node agent relay (queue/invoke-result/event/catalog publication); Qubit's canonical device host is enroll/revoke/show/dispatch only and runs no relay, so the typed code is returned and nothing is fabricated",
      "would_need": null,
      "choice": null
    },
    {
      "method": "node.skills.update",
      "code": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
      "prerequisite": "a paired node device/agent host running the native-harness relay (not part of the single local Qubit authority)",
      "classification": "external-prerequisite",
      "category": "paired node device",
      "rationale": "Requires a paired node device/agent host running the native-harness relay; the device is external to the single local Qubit authority and can be provisioned by the operator.",
      "counterpart": "voicebridge.node_host (enroll/revoke/show/dispatch) + `qubit node ...`",
      "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py:134",
      "basis": "upstream carries this through a connected paired node agent relay (queue/invoke-result/event/catalog publication); Qubit's canonical device host is enroll/revoke/show/dispatch only and runs no relay, so the typed code is returned and nothing is fabricated",
      "would_need": null,
      "choice": null
    },
    {
      "method": "poll",
      "code": "MESSAGING_TRANSPORT_OPERATOR_REQUIRED",
      "prerequisite": "configured messaging account and reachable provider transport",
      "classification": "external-prerequisite",
      "category": "messaging provider transport",
      "rationale": "Requires a configured messaging account (provider credential + endpoint) and a reachable provider transport.",
      "counterpart": "`qubit msg draft` -> `qubit msg approve` -> `qubit msg send --confirm` (voicebridge.messaging, e-stop/confirm gated)",
      "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py:1168",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "portal.close",
      "code": "PORTAL_SERVICE_OPERATOR_REQUIRED",
      "prerequisite": "a browser/portal hosting service (the upstream portal HTTP proxy listening on a local port)",
      "classification": "external-prerequisite",
      "category": "portal hosting service",
      "rationale": "Requires a browser/portal hosting service (the upstream portal HTTP proxy listening on a local port) that the operator hosts.",
      "counterpart": "the canonical website-integration store (voicebridge.websites) + the operator's own browser",
      "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py:308",
      "basis": "Qubit runs no local-port portal HTTP-proxy service, so no portal proxy is closed",
      "would_need": null,
      "choice": null
    },
    {
      "method": "portal.list",
      "code": "PORTAL_SERVICE_OPERATOR_REQUIRED",
      "prerequisite": "a browser/portal hosting service (the upstream portal HTTP proxy listening on a local port)",
      "classification": "external-prerequisite",
      "category": "portal hosting service",
      "rationale": "Requires a browser/portal hosting service (the upstream portal HTTP proxy listening on a local port) that the operator hosts.",
      "counterpart": "the canonical website-integration store (voicebridge.websites) + the operator's own browser",
      "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py:308",
      "basis": "Qubit runs no local-port portal HTTP-proxy service, so no portal is listed",
      "would_need": null,
      "choice": null
    },
    {
      "method": "portal.open",
      "code": "PORTAL_SERVICE_OPERATOR_REQUIRED",
      "prerequisite": "a browser/portal hosting service (the upstream portal HTTP proxy listening on a local port)",
      "classification": "external-prerequisite",
      "category": "portal hosting service",
      "rationale": "Requires a browser/portal hosting service (the upstream portal HTTP proxy listening on a local port) that the operator hosts.",
      "counterpart": "the canonical website-integration store (voicebridge.websites) + the operator's own browser",
      "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py:308",
      "basis": "Qubit runs no local-port portal HTTP-proxy service, so no portal proxy is opened",
      "would_need": null,
      "choice": null
    },
    {
      "method": "projects.searchRemote",
      "code": "PROJECT_REMOTE_SEARCH_OPERATOR_REQUIRED",
      "prerequisite": "configured git forge credential + network access",
      "classification": "external-prerequisite",
      "category": "git forge credential / network",
      "rationale": "Requires a configured git forge credential and a network-reachable forge API for remote project discovery.",
      "counterpart": "local projects: `qubit projects list`",
      "evidence": "voicebridge/gateway/openclaw_handlers/content.py:740",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "push.test",
      "code": "PUSH_WEB_OPERATOR_REQUIRED",
      "prerequisite": "configured web-push service (VAPID keypair + subscription store) + browser permission",
      "classification": "external-prerequisite",
      "category": "web-push service",
      "rationale": "Requires a configured web-push service (VAPID keypair + subscription store) and browser notification permission; an external protocol endpoint plus operator-issued keys.",
      "counterpart": "in-app dashboard notices + the canonical messaging outbox (`voicebridge.messaging`)",
      "evidence": "voicebridge/gateway/openclaw_handlers/content.py:794",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "push.web.preferences.get",
      "code": "PUSH_WEB_OPERATOR_REQUIRED",
      "prerequisite": "configured web-push service (VAPID keypair + subscription store) + browser permission",
      "classification": "external-prerequisite",
      "category": "web-push service",
      "rationale": "Requires a configured web-push service (VAPID keypair + subscription store) and browser notification permission; an external protocol endpoint plus operator-issued keys.",
      "counterpart": "in-app dashboard notices + the canonical messaging outbox (`voicebridge.messaging`)",
      "evidence": "voicebridge/gateway/openclaw_handlers/content.py:794",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "push.web.preferences.set",
      "code": "PUSH_WEB_OPERATOR_REQUIRED",
      "prerequisite": "configured web-push service (VAPID keypair + subscription store) + browser permission",
      "classification": "external-prerequisite",
      "category": "web-push service",
      "rationale": "Requires a configured web-push service (VAPID keypair + subscription store) and browser notification permission; an external protocol endpoint plus operator-issued keys.",
      "counterpart": "in-app dashboard notices + the canonical messaging outbox (`voicebridge.messaging`)",
      "evidence": "voicebridge/gateway/openclaw_handlers/content.py:794",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "push.web.subscribe",
      "code": "PUSH_WEB_OPERATOR_REQUIRED",
      "prerequisite": "configured web-push service (VAPID keypair + subscription store) + browser permission",
      "classification": "external-prerequisite",
      "category": "web-push service",
      "rationale": "Requires a configured web-push service (VAPID keypair + subscription store) and browser notification permission; an external protocol endpoint plus operator-issued keys.",
      "counterpart": "in-app dashboard notices + the canonical messaging outbox (`voicebridge.messaging`)",
      "evidence": "voicebridge/gateway/openclaw_handlers/content.py:794",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "push.web.test",
      "code": "PUSH_WEB_OPERATOR_REQUIRED",
      "prerequisite": "configured web-push service (VAPID keypair + subscription store) + browser permission",
      "classification": "external-prerequisite",
      "category": "web-push service",
      "rationale": "Requires a configured web-push service (VAPID keypair + subscription store) and browser notification permission; an external protocol endpoint plus operator-issued keys.",
      "counterpart": "in-app dashboard notices + the canonical messaging outbox (`voicebridge.messaging`)",
      "evidence": "voicebridge/gateway/openclaw_handlers/content.py:794",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "push.web.unsubscribe",
      "code": "PUSH_WEB_OPERATOR_REQUIRED",
      "prerequisite": "configured web-push service (VAPID keypair + subscription store) + browser permission",
      "classification": "external-prerequisite",
      "category": "web-push service",
      "rationale": "Requires a configured web-push service (VAPID keypair + subscription store) and browser notification permission; an external protocol endpoint plus operator-issued keys.",
      "counterpart": "in-app dashboard notices + the canonical messaging outbox (`voicebridge.messaging`)",
      "evidence": "voicebridge/gateway/openclaw_handlers/content.py:794",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "push.web.vapidPublicKey",
      "code": "PUSH_WEB_OPERATOR_REQUIRED",
      "prerequisite": "configured web-push service (VAPID keypair + subscription store) + browser permission",
      "classification": "external-prerequisite",
      "category": "web-push service",
      "rationale": "Requires a configured web-push service (VAPID keypair + subscription store) and browser notification permission; an external protocol endpoint plus operator-issued keys.",
      "counterpart": "in-app dashboard notices + the canonical messaging outbox (`voicebridge.messaging`)",
      "evidence": "voicebridge/gateway/openclaw_handlers/content.py:794",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "send",
      "code": "MESSAGING_TRANSPORT_OPERATOR_REQUIRED",
      "prerequisite": "configured messaging account and reachable provider transport",
      "classification": "external-prerequisite",
      "category": "messaging provider transport",
      "rationale": "Requires a configured messaging account (provider credential + endpoint) and a reachable provider transport.",
      "counterpart": "`qubit msg draft` -> `qubit msg approve` -> `qubit msg send --confirm` (voicebridge.messaging, e-stop/confirm gated)",
      "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py:1168",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "sessions.dispatch",
      "code": "RUNTIME_CLOUD_UNAVAILABLE",
      "prerequisite": "injected cloud-worker runtime transport",
      "classification": "external-prerequisite",
      "category": "cloud-worker transport",
      "rationale": "Requires an injected cloud-worker runtime transport and a reachable provider; Qubit sessions stay on the local runtime.",
      "counterpart": "local sessions via the one Qubit runtime",
      "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py:795",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "sessions.github.confirm",
      "code": "FORGE_API_OPERATOR_REQUIRED",
      "prerequisite": "configured git forge credential reference + network access",
      "classification": "external-prerequisite",
      "category": "git forge credential / API",
      "rationale": "Requires a configured forge credential (for example a GitHub/GitLab token) and a network-reachable forge API.",
      "counterpart": "local change review via `qubit git`",
      "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py:786",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "sessions.github.options",
      "code": "FORGE_API_OPERATOR_REQUIRED",
      "prerequisite": "configured git forge credential reference + network access",
      "classification": "external-prerequisite",
      "category": "git forge credential / API",
      "rationale": "Requires a configured forge credential (for example a GitHub/GitLab token) and a network-reachable forge API.",
      "counterpart": "local git branches via `qubit git branches`",
      "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py:767",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "sessions.github.publish",
      "code": "FORGE_API_OPERATOR_REQUIRED",
      "prerequisite": "configured git forge credential reference + network access",
      "classification": "external-prerequisite",
      "category": "git forge credential / API",
      "rationale": "Requires a configured forge credential (for example a GitHub/GitLab token) and a network-reachable forge API.",
      "counterpart": "local change review: CLI `qubit git status|log|diff` and the dashboard git review surface",
      "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py:754",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "sessions.github.status",
      "code": "FORGE_API_OPERATOR_REQUIRED",
      "prerequisite": "configured git forge credential reference + network access",
      "classification": "external-prerequisite",
      "category": "git forge credential / API",
      "rationale": "Requires a configured forge credential (for example a GitHub/GitLab token) and a network-reachable forge API.",
      "counterpart": "local change review via `qubit git`",
      "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py:777",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "sessions.move",
      "code": "RUNTIME_CLOUD_UNAVAILABLE",
      "prerequisite": "injected cloud-worker runtime transport",
      "classification": "external-prerequisite",
      "category": "cloud-worker transport",
      "rationale": "Requires an injected cloud-worker runtime transport and a reachable provider; Qubit sessions stay on the local runtime.",
      "counterpart": "local sessions via the one Qubit runtime",
      "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py:813",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "sessions.reclaim",
      "code": "RUNTIME_CLOUD_UNAVAILABLE",
      "prerequisite": "injected cloud-worker runtime transport",
      "classification": "external-prerequisite",
      "category": "cloud-worker transport",
      "rationale": "Requires an injected cloud-worker runtime transport and a reachable provider; Qubit sessions stay on the local runtime.",
      "counterpart": "local sessions via the one Qubit runtime",
      "evidence": "voicebridge/gateway/openclaw_handlers/sessions.py:804",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "skills.proposals.evaluate",
      "code": "SKILLS_PROPOSALS_EVALUATE_OPERATOR_REQUIRED",
      "prerequisite": "an isolated proposal-evaluation model runtime (Qubit has no self-evaluation path)",
      "classification": "architecture-gated",
      "category": "deliberate no-self-evaluation path",
      "rationale": "Requires an isolated proposal-evaluation model runtime; Qubit deliberately has no automated evaluator and promotes only on an explicit human owner verdict.",
      "counterpart": "voicebridge.verified_skills.VerifiedSkillStore.apply_verdict (human owner verdict) + voicebridge.evaluation promotion gate",
      "evidence": "voicebridge/gateway/openclaw_handlers/skills_extra.py:406",
      "basis": "upstream runs an isolated model to evaluate a proposal; Qubit deliberately has no automated evaluator and verifies only by explicit human owner verdict",
      "would_need": "Add an isolated model runtime that scores skill proposals, then feed its verdict into the promotion gate.",
      "choice": "Real upstream capability, but Qubit deliberately verifies skills only by explicit human owner verdict (no self-evaluation path); it is an intentional architecture choice."
    },
    {
      "method": "skills.proposals.historyScan",
      "code": "SKILLS_PROPOSALS_HISTORYSCAN_OPERATOR_REQUIRED",
      "prerequisite": "a session-history scan runtime (isolated model over the transcript) that emits pending proposals",
      "classification": "architecture-gated",
      "category": "session-history scanner",
      "rationale": "Requires a session-history scan runtime (isolated model over the transcript) that emits pending proposals; Qubit runs no such scanner.",
      "counterpart": "voicebridge.verified_skills.workshop.SkillWorkshop.author (authored/quarantined proposals)",
      "evidence": "voicebridge/gateway/openclaw_handlers/skills_extra.py:421",
      "basis": "upstream scans session history to emit pending proposals; Qubit runs no session-history scanner",
      "would_need": "Add a session-history scanner (isolated model over the transcript) that emits pending skill proposals.",
      "choice": "Real upstream capability; Qubit intentionally runs no history scanner and relies on the skill workshop author flow, so this is architecture-gated."
    },
    {
      "method": "skills.securityVerdicts",
      "code": "SKILLS_SECURITY_VERDICTS_OPERATOR_REQUIRED",
      "prerequisite": "a reachable skill security-verdict registry/endpoint and network access",
      "classification": "external-prerequisite",
      "category": "remote verdict registry",
      "rationale": "Requires a reachable skill security-verdict registry/endpoint and network access; Qubit validates bundled/verified skills locally.",
      "counterpart": "local bundled/verified skill validation (voicebridge.verified_skills._bundled + VerifiedSkillStore)",
      "evidence": "voicebridge/gateway/openclaw_handlers/skills_extra.py:435",
      "basis": "upstream fetches remote ClawHub skill security verdicts; Qubit runs no verdict service and fetches no remote verdicts",
      "would_need": null,
      "choice": null
    },
    {
      "method": "skills.upload.begin",
      "code": "SKILLS_UPLOAD_OPERATOR_REQUIRED",
      "prerequisite": "a staged chunked upload store/transport for skill archives",
      "classification": "architecture-gated",
      "category": "chunked upload staging",
      "rationale": "Requires a staged chunked upload store/transport for skill archives; Qubit installs only reviewed local manifests.",
      "counterpart": "voicebridge.verified_skills.mutations (single-shot reviewed manifest) + verified_skills._transfer snapshot restore",
      "evidence": "voicebridge/gateway/openclaw_handlers/skills_extra.py:447",
      "basis": "upstream begins a staged chunked skill-archive upload; Qubit runs no upload store and installs only reviewed local manifests",
      "would_need": "Add a staged chunked upload store/transport for skill archives plus the begin/chunk/commit protocol.",
      "choice": "Real upstream capability, but Qubit intentionally installs only reviewed local manifests (single-shot mutation + snapshot restore), so it is architecture-gated."
    },
    {
      "method": "skills.upload.chunk",
      "code": "SKILLS_UPLOAD_OPERATOR_REQUIRED",
      "prerequisite": "a staged chunked upload store/transport for skill archives",
      "classification": "architecture-gated",
      "category": "chunked upload staging",
      "rationale": "Requires a staged chunked upload store/transport for skill archives; Qubit installs only reviewed local manifests.",
      "counterpart": "voicebridge.verified_skills.mutations (single-shot reviewed manifest) + verified_skills._transfer snapshot restore",
      "evidence": "voicebridge/gateway/openclaw_handlers/skills_extra.py:459",
      "basis": "upstream stores an uploaded archive chunk; Qubit runs no chunked upload store",
      "would_need": "Add a staged chunked upload store/transport for skill archives plus the begin/chunk/commit protocol.",
      "choice": "Real upstream capability, but Qubit intentionally installs only reviewed local manifests (single-shot mutation + snapshot restore), so it is architecture-gated."
    },
    {
      "method": "skills.upload.commit",
      "code": "SKILLS_UPLOAD_OPERATOR_REQUIRED",
      "prerequisite": "a staged chunked upload store/transport for skill archives",
      "classification": "architecture-gated",
      "category": "chunked upload staging",
      "rationale": "Requires a staged chunked upload store/transport for skill archives; Qubit installs only reviewed local manifests.",
      "counterpart": "voicebridge.verified_skills.mutations (single-shot reviewed manifest) + verified_skills._transfer snapshot restore",
      "evidence": "voicebridge/gateway/openclaw_handlers/skills_extra.py:471",
      "basis": "upstream commits an uploaded skill archive for install; Qubit runs no upload store and downloads nothing",
      "would_need": "Add a staged chunked upload store/transport for skill archives plus the begin/chunk/commit protocol.",
      "choice": "Real upstream capability, but Qubit intentionally installs only reviewed local manifests (single-shot mutation + snapshot restore), so it is architecture-gated."
    },
    {
      "method": "talk.client.close",
      "code": "TALK_REALTIME_OPERATOR_REQUIRED",
      "prerequisite": "an OpenAI Realtime (or equivalent) provider transport, which Qubit does not run",
      "classification": "external-prerequisite",
      "category": "provider realtime transport",
      "rationale": "Requires an OpenAI Realtime (or equivalent) provider transport to be injected; Qubit resolves the profile read-only and starts no adapter.",
      "counterpart": "voicebridge.chat_transport.stream_turn/turn over the QubitSessionStore, plus the /qubit-chat voice path (voicebridge/orchestrator.py)",
      "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py:117",
      "basis": "upstream a client-owned realtime provider session needs a live provider realtime/duplex transport; Qubit removed the realtime lane (the profile is resolved read-only and no adapter is started), so the typed code is returned and nothing is fabricated",
      "would_need": null,
      "choice": null
    },
    {
      "method": "talk.client.create",
      "code": "TALK_REALTIME_OPERATOR_REQUIRED",
      "prerequisite": "an OpenAI Realtime (or equivalent) provider transport, which Qubit does not run",
      "classification": "external-prerequisite",
      "category": "provider realtime transport",
      "rationale": "Requires an OpenAI Realtime (or equivalent) provider transport to be injected; Qubit resolves the profile read-only and starts no adapter.",
      "counterpart": "voicebridge.chat_transport.stream_turn/turn over the QubitSessionStore, plus the /qubit-chat voice path (voicebridge/orchestrator.py)",
      "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py:117",
      "basis": "upstream a client-owned realtime provider session (webrtc/provider-websocket) needs a live provider realtime/duplex transport; Qubit removed the realtime lane (the profile is resolved read-only and no adapter is started), so the typed code is returned and nothing is fabricated",
      "would_need": null,
      "choice": null
    },
    {
      "method": "talk.session.acknowledgeMark",
      "code": "TALK_REALTIME_OPERATOR_REQUIRED",
      "prerequisite": "an OpenAI Realtime (or equivalent) provider transport, which Qubit does not run",
      "classification": "external-prerequisite",
      "category": "provider realtime transport",
      "rationale": "Requires an OpenAI Realtime (or equivalent) provider transport to be injected; Qubit resolves the profile read-only and starts no adapter.",
      "counterpart": "voicebridge.chat_transport.stream_turn/turn over the QubitSessionStore, plus the /qubit-chat voice path (voicebridge/orchestrator.py)",
      "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py:117",
      "basis": "upstream a live realtime provider transport needs a live provider realtime/duplex transport; Qubit removed the realtime lane (the profile is resolved read-only and no adapter is started), so the typed code is returned and nothing is fabricated",
      "would_need": null,
      "choice": null
    },
    {
      "method": "talk.session.appendAudio",
      "code": "TALK_REALTIME_OPERATOR_REQUIRED",
      "prerequisite": "an OpenAI Realtime (or equivalent) provider transport, which Qubit does not run",
      "classification": "external-prerequisite",
      "category": "provider realtime transport",
      "rationale": "Requires an OpenAI Realtime (or equivalent) provider transport to be injected; Qubit resolves the profile read-only and starts no adapter.",
      "counterpart": "voicebridge.chat_transport.stream_turn/turn over the QubitSessionStore, plus the /qubit-chat voice path (voicebridge/orchestrator.py)",
      "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py:117",
      "basis": "upstream a gateway-owned realtime relay or transcription session needs a live provider realtime/duplex transport; Qubit removed the realtime lane (the profile is resolved read-only and no adapter is started), so the typed code is returned and nothing is fabricated",
      "would_need": null,
      "choice": null
    },
    {
      "method": "talk.session.cancelOutput",
      "code": "TALK_REALTIME_OPERATOR_REQUIRED",
      "prerequisite": "an OpenAI Realtime (or equivalent) provider transport, which Qubit does not run",
      "classification": "external-prerequisite",
      "category": "provider realtime transport",
      "rationale": "Requires an OpenAI Realtime (or equivalent) provider transport to be injected; Qubit resolves the profile read-only and starts no adapter.",
      "counterpart": "voicebridge.chat_transport.stream_turn/turn over the QubitSessionStore, plus the /qubit-chat voice path (voicebridge/orchestrator.py)",
      "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py:117",
      "basis": "upstream a gateway-owned realtime relay session needs a live provider realtime/duplex transport; Qubit removed the realtime lane (the profile is resolved read-only and no adapter is started), so the typed code is returned and nothing is fabricated",
      "would_need": null,
      "choice": null
    },
    {
      "method": "talk.session.close",
      "code": "TALK_REALTIME_OPERATOR_REQUIRED",
      "prerequisite": "an OpenAI Realtime (or equivalent) provider transport, which Qubit does not run",
      "classification": "external-prerequisite",
      "category": "provider realtime transport",
      "rationale": "Requires an OpenAI Realtime (or equivalent) provider transport to be injected; Qubit resolves the profile read-only and starts no adapter.",
      "counterpart": "voicebridge.chat_transport.stream_turn/turn over the QubitSessionStore, plus the /qubit-chat voice path (voicebridge/orchestrator.py)",
      "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py:117",
      "basis": "upstream a gateway-owned realtime/relay/transcription Talk session needs a live provider realtime/duplex transport; Qubit removed the realtime lane (the profile is resolved read-only and no adapter is started), so the typed code is returned and nothing is fabricated",
      "would_need": null,
      "choice": null
    },
    {
      "method": "talk.session.create",
      "code": "TALK_REALTIME_OPERATOR_REQUIRED",
      "prerequisite": "an OpenAI Realtime (or equivalent) provider transport, which Qubit does not run",
      "classification": "external-prerequisite",
      "category": "provider realtime transport",
      "rationale": "Requires an OpenAI Realtime (or equivalent) provider transport to be injected; Qubit resolves the profile read-only and starts no adapter.",
      "counterpart": "voicebridge.chat_transport.stream_turn/turn over the QubitSessionStore, plus the /qubit-chat voice path (voicebridge/orchestrator.py)",
      "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py:117",
      "basis": "upstream a gateway-owned realtime/relay/transcription Talk session needs a live provider realtime/duplex transport; Qubit removed the realtime lane (the profile is resolved read-only and no adapter is started), so the typed code is returned and nothing is fabricated",
      "would_need": null,
      "choice": null
    },
    {
      "method": "talk.session.steer",
      "code": "TALK_REALTIME_OPERATOR_REQUIRED",
      "prerequisite": "an OpenAI Realtime (or equivalent) provider transport, which Qubit does not run",
      "classification": "external-prerequisite",
      "category": "provider realtime transport",
      "rationale": "Requires an OpenAI Realtime (or equivalent) provider transport to be injected; Qubit resolves the profile read-only and starts no adapter.",
      "counterpart": "voicebridge.chat_transport.stream_turn/turn over the QubitSessionStore, plus the /qubit-chat voice path (voicebridge/orchestrator.py)",
      "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py:117",
      "basis": "upstream a gateway-owned agent-backed realtime Talk session needs a live provider realtime/duplex transport; Qubit removed the realtime lane (the profile is resolved read-only and no adapter is started), so the typed code is returned and nothing is fabricated",
      "would_need": null,
      "choice": null
    },
    {
      "method": "talk.session.submitToolResult",
      "code": "TALK_REALTIME_OPERATOR_REQUIRED",
      "prerequisite": "an OpenAI Realtime (or equivalent) provider transport, which Qubit does not run",
      "classification": "external-prerequisite",
      "category": "provider realtime transport",
      "rationale": "Requires an OpenAI Realtime (or equivalent) provider transport to be injected; Qubit resolves the profile read-only and starts no adapter.",
      "counterpart": "voicebridge.chat_transport.stream_turn/turn over the QubitSessionStore, plus the /qubit-chat voice path (voicebridge/orchestrator.py)",
      "evidence": "voicebridge/gateway/openclaw_handlers/talk_node.py:117",
      "basis": "upstream a gateway-owned realtime relay provider bridge needs a live provider realtime/duplex transport; Qubit removed the realtime lane (the profile is resolved read-only and no adapter is started), so the typed code is returned and nothing is fabricated",
      "would_need": null,
      "choice": null
    },
    {
      "method": "taskSuggestions.accept",
      "code": "TASK_SUGGESTIONS_OPERATOR_REQUIRED",
      "prerequisite": "an upstream model-proposed task-suggestion registry + run-control runtime; Qubit runs none",
      "classification": "architecture-gated",
      "category": "ephemeral task-suggestion registry",
      "rationale": "Requires a model-proposed task-suggestion registry + run-control runtime; Qubit keeps no ephemeral suggestion registry.",
      "counterpart": "durable scheduled work via voicebridge.schedules.create_job (`qubit cron add`) + the canonical chat session",
      "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py:342",
      "basis": "Qubit keeps no ephemeral task-suggestion registry, so no suggestion is accepted or dispatched",
      "would_need": "Add an ephemeral task-suggestion registry and a run-control runtime that can accept a suggestion into a live run.",
      "choice": "Real upstream capability; Qubit deliberately keeps no ephemeral suggestion registry and uses durable schedules + the chat session, so this is architecture-gated."
    },
    {
      "method": "taskSuggestions.create",
      "code": "TASK_SUGGESTIONS_OPERATOR_REQUIRED",
      "prerequisite": "an upstream model-proposed task-suggestion registry + run-control runtime; Qubit runs none",
      "classification": "architecture-gated",
      "category": "ephemeral task-suggestion registry",
      "rationale": "Requires a model-proposed task-suggestion registry + run-control runtime; Qubit keeps no ephemeral suggestion registry.",
      "counterpart": "durable scheduled work via voicebridge.schedules.create_job (`qubit cron add`) + the canonical chat session",
      "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py:342",
      "basis": "Qubit keeps no ephemeral task-suggestion registry, so no suggestion is created",
      "would_need": "Add an ephemeral task-suggestion registry and a run-control runtime that can accept a suggestion into a live run.",
      "choice": "Real upstream capability; Qubit deliberately keeps no ephemeral suggestion registry and uses durable schedules + the chat session, so this is architecture-gated."
    },
    {
      "method": "taskSuggestions.dismiss",
      "code": "TASK_SUGGESTIONS_OPERATOR_REQUIRED",
      "prerequisite": "an upstream model-proposed task-suggestion registry + run-control runtime; Qubit runs none",
      "classification": "architecture-gated",
      "category": "ephemeral task-suggestion registry",
      "rationale": "Requires a model-proposed task-suggestion registry + run-control runtime; Qubit keeps no ephemeral suggestion registry.",
      "counterpart": "durable scheduled work via voicebridge.schedules.create_job (`qubit cron add`) + the canonical chat session",
      "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py:342",
      "basis": "Qubit keeps no ephemeral task-suggestion registry, so no suggestion is dismissed",
      "would_need": "Add an ephemeral task-suggestion registry and a run-control runtime that can accept a suggestion into a live run.",
      "choice": "Real upstream capability; Qubit deliberately keeps no ephemeral suggestion registry and uses durable schedules + the chat session, so this is architecture-gated."
    },
    {
      "method": "tasks.dismiss",
      "code": "TASKS_DISMISS_OPERATOR_REQUIRED",
      "prerequisite": "an upstream detached-task/subagent completion-delivery registry; Qubit keeps none",
      "classification": "architecture-gated",
      "category": "detached-task completion registry",
      "rationale": "Requires a detached-task/subagent completion-delivery registry; Qubit's task ledger is canonical and read-only.",
      "counterpart": "the canonical read-only task ledger (`qubit tasks`)",
      "evidence": "voicebridge/gateway/openclaw_handlers/scheduler_media.py:456",
      "basis": "Qubit keeps no detached-task completion-delivery registry, so nothing is dismissed",
      "would_need": "Add a detached-task/subagent completion-delivery registry that can be dismissed.",
      "choice": "Real upstream capability; Qubit intentionally keeps the task ledger read-only, so this is an intentional architecture choice."
    },
    {
      "method": "tools.github.authorize.cancel",
      "code": "GITHUB_OPERATOR_REQUIRED",
      "prerequisite": "GitHub credential reference with required scopes + network access",
      "classification": "external-prerequisite",
      "category": "GitHub credential / OAuth",
      "rationale": "Requires a GitHub credential reference (env:/file:) with scopes and network access, or a live github.com OAuth app for the device flow.",
      "counterpart": "CLI `qubit auth status` + an operator-managed credential reference",
      "evidence": "voicebridge/gateway/openclaw_handlers/content.py:838",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "tools.github.authorize.poll",
      "code": "GITHUB_OPERATOR_REQUIRED",
      "prerequisite": "GitHub credential reference with required scopes + network access",
      "classification": "external-prerequisite",
      "category": "GitHub credential / OAuth",
      "rationale": "Requires a GitHub credential reference (env:/file:) with scopes and network access, or a live github.com OAuth app for the device flow.",
      "counterpart": "CLI `qubit auth status` + an operator-managed credential reference",
      "evidence": "voicebridge/gateway/openclaw_handlers/content.py:838",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "tools.github.authorize.start",
      "code": "GITHUB_OPERATOR_REQUIRED",
      "prerequisite": "GitHub credential reference with required scopes + network access",
      "classification": "external-prerequisite",
      "category": "GitHub credential / OAuth",
      "rationale": "Requires a GitHub credential reference (env:/file:) with scopes and network access, or a live github.com OAuth app for the device flow.",
      "counterpart": "CLI `qubit auth status` + an operator-managed credential reference",
      "evidence": "voicebridge/gateway/openclaw_handlers/content.py:838",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "tools.github.configure",
      "code": "GITHUB_OPERATOR_REQUIRED",
      "prerequisite": "GitHub credential reference with required scopes + network access",
      "classification": "external-prerequisite",
      "category": "GitHub credential / OAuth",
      "rationale": "Requires a GitHub credential reference (env:/file:) with scopes and network access, or a live github.com OAuth app for the device flow.",
      "counterpart": "CLI `qubit auth status` + an operator-managed credential reference",
      "evidence": "voicebridge/gateway/openclaw_handlers/content.py:838",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "tools.github.status",
      "code": "GITHUB_OPERATOR_REQUIRED",
      "prerequisite": "GitHub credential reference with required scopes + network access",
      "classification": "external-prerequisite",
      "category": "GitHub credential / OAuth",
      "rationale": "Requires a GitHub credential reference (env:/file:) with scopes and network access, or a live github.com OAuth app for the device flow.",
      "counterpart": "CLI `qubit auth status` + an operator-managed credential reference",
      "evidence": "voicebridge/gateway/openclaw_handlers/content.py:838",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "update.hold",
      "code": "UPDATE_HOLD_OPERATOR_ONLY",
      "prerequisite": "operator-managed update hold on the runtime image",
      "classification": "external-prerequisite",
      "category": "operator update hold",
      "rationale": "Requires an operator-managed update hold on the runtime image; there is no hosted update campaign to hold locally.",
      "counterpart": "voicebridge.runtime_images.update_history + `qubit runtime upgrade`",
      "evidence": "voicebridge/gateway/openclaw_handlers/system.py:797",
      "basis": "",
      "would_need": null,
      "choice": null
    },
    {
      "method": "users.authConnect.answer",
      "code": "USERS_AUTH_CONNECT_OPERATOR_REQUIRED",
      "prerequisite": "a live external provider OAuth/connect service and network access",
      "classification": "external-prerequisite",
      "category": "external OAuth provider",
      "rationale": "Requires a live external provider OAuth/connect service and network access; Qubit performs only a manual CLI handoff.",
      "counterpart": "CLI `qubit auth login --provider <name>` (manual handoff)",
      "evidence": "voicebridge/gateway/openclaw_handlers/users.py:366",
      "basis": "no external auth-connect session exists to answer; the typed code is returned",
      "would_need": null,
      "choice": null
    },
    {
      "method": "users.authConnect.cancel",
      "code": "USERS_AUTH_CONNECT_OPERATOR_REQUIRED",
      "prerequisite": "a live external provider OAuth/connect service and network access",
      "classification": "external-prerequisite",
      "category": "external OAuth provider",
      "rationale": "Requires a live external provider OAuth/connect service and network access; Qubit performs only a manual CLI handoff.",
      "counterpart": "CLI `qubit auth login --provider <name>` (manual handoff)",
      "evidence": "voicebridge/gateway/openclaw_handlers/users.py:366",
      "basis": "no external auth-connect session exists to cancel; the typed code is returned",
      "would_need": null,
      "choice": null
    },
    {
      "method": "users.authConnect.start",
      "code": "USERS_AUTH_CONNECT_OPERATOR_REQUIRED",
      "prerequisite": "a live external provider OAuth/connect service and network access",
      "classification": "external-prerequisite",
      "category": "external OAuth provider",
      "rationale": "Requires a live external provider OAuth/connect service and network access; Qubit performs only a manual CLI handoff.",
      "counterpart": "CLI `qubit auth login --provider <name>` (manual handoff; Qubit never performs the OAuth dance)",
      "evidence": "voicebridge/gateway/openclaw_handlers/users.py:366",
      "basis": "starting an external provider auth-connect session needs a live OAuth service Qubit does not run; the typed code is returned",
      "would_need": null,
      "choice": null
    },
    {
      "method": "users.authConnect.status",
      "code": "USERS_AUTH_CONNECT_OPERATOR_REQUIRED",
      "prerequisite": "a live external provider OAuth/connect service and network access",
      "classification": "external-prerequisite",
      "category": "external OAuth provider",
      "rationale": "Requires a live external provider OAuth/connect service and network access; Qubit performs only a manual CLI handoff.",
      "counterpart": "CLI `qubit auth login --provider <name>` (manual handoff)",
      "evidence": "voicebridge/gateway/openclaw_handlers/users.py:366",
      "basis": "no external auth-connect session exists to report; the typed code is returned",
      "would_need": null,
      "choice": null
    },
    {
      "method": "users.github.authorize.cancel",
      "code": "GITHUB_OPERATOR_REQUIRED",
      "prerequisite": "a started GitHub device-flow authorization (requires the live OAuth app)",
      "classification": "external-prerequisite",
      "category": "GitHub credential / OAuth",
      "rationale": "Requires a GitHub credential reference (env:/file:) with scopes and network access, or a live github.com OAuth app for the device flow.",
      "counterpart": "nothing to cancel locally; remove the credential reference with the operator's own tooling",
      "evidence": "voicebridge/gateway/openclaw_handlers/users.py:345",
      "basis": "no GitHub device-flow authorization exists to cancel; the typed code is returned",
      "would_need": null,
      "choice": null
    },
    {
      "method": "users.github.authorize.poll",
      "code": "GITHUB_OPERATOR_REQUIRED",
      "prerequisite": "a started GitHub device-flow authorization (requires the live OAuth app)",
      "classification": "external-prerequisite",
      "category": "GitHub credential / OAuth",
      "rationale": "Requires a GitHub credential reference (env:/file:) with scopes and network access, or a live github.com OAuth app for the device flow.",
      "counterpart": "CLI `qubit auth verify --provider github` (reference presence only)",
      "evidence": "voicebridge/gateway/openclaw_handlers/users.py:334",
      "basis": "Qubit polls no external OAuth service and has no device-flow session in flight; the typed code is returned",
      "would_need": null,
      "choice": null
    },
    {
      "method": "users.github.authorize.start",
      "code": "GITHUB_OPERATOR_REQUIRED",
      "prerequisite": "a live GitHub OAuth app/device-flow service and network access",
      "classification": "external-prerequisite",
      "category": "GitHub credential / OAuth",
      "rationale": "Requires a GitHub credential reference (env:/file:) with scopes and network access, or a live github.com OAuth app for the device flow.",
      "counterpart": "CLI `qubit auth login --provider github` (manual handoff in the operator's browser)",
      "evidence": "voicebridge/gateway/openclaw_handlers/users.py:323",
      "basis": "the GitHub device-flow authorization needs a live github.com OAuth app Qubit does not run; no device flow is started and the typed code is returned",
      "would_need": null,
      "choice": null
    },
    {
      "method": "users.github.disconnect",
      "code": "GITHUB_OPERATOR_REQUIRED",
      "prerequisite": "a linked GitHub account (requires the live OAuth app)",
      "classification": "external-prerequisite",
      "category": "GitHub credential / OAuth",
      "rationale": "Requires a GitHub credential reference (env:/file:) with scopes and network access, or a live github.com OAuth app for the device flow.",
      "counterpart": "voicebridge.credentials.remove (confirm-gated) removes the credential reference",
      "evidence": "voicebridge/gateway/openclaw_handlers/users.py:356",
      "basis": "Qubit holds no personal GitHub connection to disconnect; the typed code is returned",
      "would_need": null,
      "choice": null
    },
    {
      "method": "users.github.status",
      "code": "GITHUB_OPERATOR_REQUIRED",
      "prerequisite": "a GitHub credential reference (env:/file:) with the required scopes and network access",
      "classification": "external-prerequisite",
      "category": "GitHub credential / OAuth",
      "rationale": "Requires a GitHub credential reference (env:/file:) with scopes and network access, or a live github.com OAuth app for the device flow.",
      "counterpart": "CLI `qubit auth status` + an operator-managed credential reference",
      "evidence": "voicebridge/gateway/openclaw_handlers/users.py:312",
      "basis": "Qubit stores credential references only and runs no GitHub OAuth/app service, so no personal GitHub status is fetched; the typed code is returned",
      "would_need": null,
      "choice": null
    },
    {
      "method": "users.linkEmail",
      "code": "USERS_EMAIL_OPERATOR_REQUIRED",
      "prerequisite": "an email/identity verification service",
      "classification": "external-prerequisite",
      "category": "email / identity service",
      "rationale": "Requires an email/identity verification service; Qubit stores no operator email.",
      "counterpart": "voicebridge.qubit_compat.h_auth_me (loopback identity; email empty)",
      "evidence": "voicebridge/gateway/openclaw_handlers/users.py:414",
      "basis": "Qubit has no email identity service and stores no operator email; the typed code is returned",
      "would_need": null,
      "choice": null
    },
    {
      "method": "users.setAvatar",
      "code": "USERS_AVATAR_OPERATOR_REQUIRED",
      "prerequisite": "an operator-managed avatar/media store and hosting",
      "classification": "architecture-gated",
      "category": "operator avatar/media store",
      "rationale": "Requires an operator-managed avatar/media store and hosting; Qubit keeps no avatar field or media hosting.",
      "counterpart": "voicebridge.device.profile (local device identity; no avatar field)",
      "evidence": "voicebridge/gateway/openclaw_handlers/users.py:404",
      "basis": "Qubit keeps no operator avatar/media store and hosts no avatar media, so no avatar is written; the typed code is returned",
      "would_need": "Add an operator avatar/media store and media hosting, then bind it to the operator profile.",
      "choice": "Real upstream capability; Qubit deliberately keeps no avatar field and hosts no media, so this is an intentional architecture choice."
    },
    {
      "method": "users.setDisplayName",
      "code": "USERS_PROFILE_OPERATOR_REQUIRED",
      "prerequisite": "a multi-user profile store (Qubit is single-operator)",
      "classification": "architecture-gated",
      "category": "intentional single-operator architecture",
      "rationale": "Requires a multi-user profile/role store; Qubit is single-operator with fixed operator scopes, so there is no role to assign.",
      "counterpart": "voicebridge.qubit_compat.h_auth_me (fixed operator display name)",
      "evidence": "voicebridge/gateway/openclaw_handlers/users.py:425",
      "basis": "Qubit is single-operator with no mutable multi-user profile store, so no display name is written; the typed code is returned",
      "would_need": "Add a multi-user profile and role store and a role-assignment authority.",
      "choice": "Real upstream capability, but supporting it contradicts the single-operator design with fixed operator scopes; it is an intentional architecture choice."
    },
    {
      "method": "users.setRole",
      "code": "USERS_PROFILE_OPERATOR_REQUIRED",
      "prerequisite": "a multi-user profile/role store (Qubit is single-operator)",
      "classification": "architecture-gated",
      "category": "intentional single-operator architecture",
      "rationale": "Requires a multi-user profile/role store; Qubit is single-operator with fixed operator scopes, so there is no role to assign.",
      "counterpart": "the fixed operator scopes advertised by the gateway `connect` handshake",
      "evidence": "voicebridge/gateway/openclaw_handlers/users.py:435",
      "basis": "Qubit has no multi-user role store and fixed operator scopes, so no role is assigned; the typed code is returned",
      "would_need": "Add a multi-user profile and role store and a role-assignment authority.",
      "choice": "Real upstream capability, but supporting it contradicts the single-operator design with fixed operator scopes; it is an intentional architecture choice."
    },
    {
      "method": "voicewake.routing.get",
      "code": "WAKE_ROUTING_OPERATOR_REQUIRED",
      "prerequisite": "a configurable wake-trigger/routing store",
      "classification": "architecture-gated",
      "category": "wake-trigger/routing store",
      "rationale": "Requires a configurable wake-trigger/routing store; Qubit routes wake detection through the one shared audio listener.",
      "counterpart": "voicewake.get + voicebridge.wake.status (single shared audio listener)",
      "evidence": "voicebridge/gateway/openclaw_handlers/misc_surface.py:173",
      "basis": "Qubit has no voice-wake routing configuration; wake detection routes through the one audio listener, so no routing config exists to read",
      "would_need": "Add a configurable wake-trigger/routing store read/write layer on the shared audio listener.",
      "choice": "Real upstream capability; Qubit deliberately routes wake detection through the one audio listener with an operator-fetched keyword bundle, so this is an intentional architecture choice."
    },
    {
      "method": "voicewake.set",
      "code": "WAKE_TRIGGERS_OPERATOR_REQUIRED",
      "prerequisite": "a configurable wake-trigger store (writable keywords/model bundle)",
      "classification": "architecture-gated",
      "category": "writable wake-trigger store",
      "rationale": "Requires a writable wake-trigger store; Qubit's wake phrases come from the operator-fetched sherpa-onnx keyword bundle.",
      "counterpart": "voicewake.get + voicebridge.wake.status (single shared audio listener)",
      "evidence": "voicebridge/gateway/openclaw_handlers/misc_surface.py:159",
      "basis": "Qubit has no writable wake-trigger store; wake phrases come from the operator-fetched sherpa-onnx keyword bundle, so no triggers are persisted",
      "would_need": "Add a writable wake-trigger store bound to the wake phrase/model bundle.",
      "choice": "Real upstream capability; Qubit's wake phrases come from the operator-fetched sherpa-onnx keyword bundle, so persisting triggers is an intentional architecture choice."
    }
  ]
}
QUBIT_OPERATOR_ONLY_REVIEW_JSON -->
