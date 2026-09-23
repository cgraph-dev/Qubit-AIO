# OpenClaw v2026.9.4 delta audit vs Qubit VoiceBridge

Read-only delta audit. The only repo file created/modified is this document.
All OpenClaw evidence is cited against the v2026.9.4 tree at commit
`3a9d69db306cd7f081e06254cb89c4bcc14a7107`; all Qubit evidence is against
`$HOME/Projects/Repos/qubit-voicebridge` at the working tree.

The predecessor audit (`docs/AUDIT_OPENCLAW_2026-09-11.md`) was anchored at
v2026.9.2 (`3928bad9…`). This document records only what changed between those
two pins and how each delta maps to Qubit's current capability modules.

---

## 0. Pin table

| Field | Predecessor audit | This delta audit |
|---|---|---|
| Upstream repo | `https://github.com/openclaw/openclaw` | same |
| Tag | `v2026.9.2` | `v2026.9.4` |
| Commit | `3928bad9badfcb6c7d140530435e806fb8092190` | `3a9d69db306cd7f081e06254cb89c4bcc14a7107` |
| Commit date | 2026-09-05 08:18:40 -0700 | 2026-09-10 15:13:19 -0700 |
| Tag object | — | `8bec206f3c1f787e1e9c45cfd34d3de2a78c7b8e` (2026-09-11) |
| Package version | `2026.9.2` | `2026.9.4` (`package.json:3`) |
| State/agent schema | — | state `17`, agent `19` (`package.json:6-8`) |
| License | MIT, Copyright (c) 2026 OpenClaw Foundation (`LICENSE:1-3`) | same |
| Local clone | `/tmp/opencode/pinned/openclaw` (shallow) | same clone, `v2026.9.4` fetched as a shallow tag object |

Qubit repo: `$HOME/Projects/Repos/qubit-voicebridge` at
`cb2619f4d412bcedc6fc89fa6b10a1b2808e6114`.
Note: the Qubit working tree already contained unrelated uncommitted edits to
`docs/NATIVE_EQUIVALENTS_8U.{json,md}`, `tests/test_hermes_readsurfaces8u.py`,
`tests/test_hermes_routes_parity8u.py`, and `voicebridge/hermes_compat.py`
before this audit began; they are not part of this work.

---

## 1. Method / repro commands

All commands below are read-only against the pinned clone. The clone is shallow
(one commit at `3928bad9`); the tag was fetched as an additional shallow root so
that tree-level diffs work without checking anything out.

```bash
# 1. Fetch v2026.9.4 into the existing shallow clone (adds objects only)
git -C /tmp/opencode/pinned/openclaw fetch --depth 1 --no-tags \
  origin tag v2026.9.4
git -C /tmp/opencode/pinned/openclaw rev-parse v2026.9.4^{commit}
# -> 3a9d69db306cd7f081e06254cb89c4bcc14a7107   (matches expected pin)

# 2. Whole-tree delta (9.2 -> 9.4); ~15,137 files, 1,174 PRs per CHANGELOG
git -C /tmp/opencode/pinned/openclaw diff 3928bad9 3a9d69db --stat
git -C /tmp/opencode/pinned/openclaw diff 3928bad9 3a9d69db -- CHANGELOG.md

# 3. Added files per area (new capability surfaces)
for a in docs extensions packages src apps skills config; do
  git -C /tmp/opencode/pinned/openclaw diff --name-status --diff-filter=A \
    3928bad9 3a9d69db -- "$a"; done

# 4. New top-level module dirs
comm -13 <(git ls-tree --name-only 3928bad9:extensions/ | sort) \
         <(git ls-tree --name-only 3a9d69db:extensions/ | sort)

# 5. Command-name delta
git -C /tmp/opencode/pinned/openclaw grep -hoE '\.command\("[^"]+"' 3928bad9 -- src/ \
  | sed 's/.*\.command("//;s/"$//' | sort -u   # and again for 3a9d69db

# 6. Citation verification (tree, not working copy)
git -C /tmp/opencode/pinned/openclaw show 3a9d69db:<path> | sed -n '<line>p'
```

Findings on scale: the 9.2→9.4 interval is very large (the upstream `CHANGELOG`
records **1,174 in-range PRs** and an intervening **v2026.9.3** release). Most of
the 15k changed files are iterative edits inside existing subsystems. The set of
genuinely **new top-level extensions is exactly one** (`team-reports`). New
*coded subsystems* are concentrated in cloud workers / worker environments,
MCP HTTP hosting, media-generation agent tools, the update/rollback CLI, and a
set of new gateway RPC server methods.

Diff summary by area (files changed):

| Area | Files | Insertions | Deletions |
|---|---|---|---|
| `src/` | 7,196 | 561,501 | 202,025 |
| `extensions/` | 2,554 | 124,055 | 59,286 |
| `docs/` | 1,227 | 129,849 | 94,945 |
| `packages/` | 451 | 17,826 | 7,689 |
| whole tree | 15,137 | 1,234,799 | 553,502 |

The gateway **wire protocol did not change**: `PROTOCOL_VERSION = 4`,
`MIN_CLIENT_PROTOCOL_VERSION = 4`, `MIN_NODE_PROTOCOL_VERSION = 3`
(`packages/gateway-protocol/src/version.ts:2-8`), identical to the 9.2 pin.

---

## 2. Delta inventory (with v2026.9.4 citations)

Legend: **[N]** = new; **[C]** = changed/expanded; **[P]** = protocol/API surface.

### 2.1 Plugins / extensions

| ID | Delta | Type | v2026.9.4 evidence |
|---|---|---|---|
| D1 | **Team Reports plugin** — GitHub + explicitly configured Discord activity reports, stored history, optional model summaries, scheduler, HTTP pages and gateway methods | N | `extensions/team-reports/openclaw.plugin.json:2-3`; `extensions/team-reports/src/cli.ts:25`; `extensions/team-reports/src/scheduler.ts:29`; `extensions/team-reports/src/http.ts:118` |
| D2 | **Policy plugin** — policy-backed workspace-conformance doctor checks, attestation, rules, scopes, findings | C | `extensions/policy/openclaw.plugin.json:2,4-5`; `docs/cli/policy/attestation.md:1`; `docs/cli/policy/scopes.md` |
| D3 | **File Transfer plugin** — node-host directory list/fetch/stream and resumable transfers | C | `extensions/file-transfer/openclaw.plugin.json:8,12`; `extensions/file-transfer/src/node-host/dir-fetch.ts:136`; `docs/cli/file-transfer.md:1`; `docs/nodes/file-transfers.md` |
| D4 | **Active Memory plugin** — active recall resolution, session policy, escalation, prompt injection | C | `extensions/active-memory/index.ts:84`; `extensions/active-memory/recall.ts:475`; `docs/concepts/active-memory/how-it-works.md` |
| D5 | **QA Lab / E2E automation stack** — operator flow, scenario coverage, channel QA reference, reporting | C | `docs/concepts/qa-e2e-automation/operator-flow.md:1`; `docs/concepts/qa-e2e-automation/scenario-coverage.md`; `extensions/qa-lab/src/qa-yaml.ts:1` |

### 2.2 Runtime / device / fleet

| ID | Delta | Type | v2026.9.4 evidence |
|---|---|---|---|
| D6 | **Cloud workers / worker environments** — prepared builds, admission handshake, device placement, ready workers, warm images/snapshots | C | `src/gateway/worker-environments/admission.ts:26,35,46`; `src/gateway/worker-environments/build-preparation.ts:34`; `docs/gateway/cloud-workers/warm-images.md:2,19` |
| D7 | **Node runtime update / recovery** — private runtime recovery, respawn, native hook relay; added to package `files` | C | `node-runtime-update.mjs:6`; `node-runtime-recovery.mjs:72`; `package.json` `files` adds `node-runtime-*.mjs` |
| D8 | **Secrets egress proxy** — outbound proxy with stream substitution and a secret registry | N | `src/secrets/egress-proxy/proxy-server.ts:1`; `src/secrets/egress-proxy/stream-substitution.ts`; `src/secrets/egress-proxy/registry.ts` |

### 2.3 Gateway protocol / API / RPC

| ID | Delta | Type | v2026.9.4 evidence |
|---|---|---|---|
| D9 | **New gateway RPC server methods** — `environments.prepare`, task history, task suggestions, update report, workspace files, session repository artifacts/workspace access, plugin mutations, migrations | P | `src/gateway/server-methods/environments.ts:558`; `src/gateway/server-methods/task-history.ts:1`; `src/gateway/server-methods/task-suggestions.ts`; `src/gateway/server-methods/update-report.ts:164`; `src/gateway/server-methods/workspace-files.ts:36`; `src/gateway/server-methods/session-repository-artifacts.ts` |
| D10 | **MCP hosted apps** — loopback MCP HTTP server, attach grants, OAuth callback, rooted execution, standalone host | N | `src/gateway/mcp-http.ts:543`; `src/gateway/mcp-grant-store.ts:164`; `src/gateway/mcp-app-standalone-host.ts:1`; `docs/cli/mcp/apps.md:1`; `docs/cli/mcp/serve.md`; `docs/cli/mcp/registry.md` |
| D11 | **Wire protocol** | none | unchanged at v4 — `packages/gateway-protocol/src/version.ts:2-8` |

Control-UI route surface also changed: short plugin-page URLs such as `/reports`
and a publishable public session view (`docs/web/urls.md:5,18`).

### 2.4 Channels (new docs + feature code)

| ID | Delta | Type | v2026.9.4 evidence |
|---|---|---|---|
| D12 | **Discord voice channels, voice-follow, voice transcripts, rich messages** | C | `extensions/discord/src/voice/command.ts:32`; `extensions/discord/src/voice/agent-control.ts`; `docs/channels/discord/voice-channels.md:1`; `docs/channels/discord/voice-transcripts.md` |
| D13 | **Telegram mini-app + photo albums + rich messages** | C | `extensions/telegram/src/miniapp/routes.ts:40`; `extensions/telegram/src/rich-block-model.ts`; `docs/channels/telegram/mini-app.md:1` |
| D14 | Slack Enterprise Grid, transports, rich messages | C | `docs/channels/slack/enterprise-grid.md:1`; `docs/channels/slack/transports.md` |
| D15 | iMessage private API, media, rich messages | C | `docs/channels/imessage/private-api.md:1`; `docs/channels/imessage/media.md` |
| D16 | Feishu dynamic agents, advanced configuration | C | `docs/channels/feishu/dynamic-agents.md:1`; `docs/channels/feishu/advanced-configuration.md` |
| D17 | Matrix encryption + multi-account homeservers | C | `docs/channels/matrix/encryption.md:1`; `docs/channels/matrix/accounts-and-homeservers.md` |

### 2.5 Agent tools / media

| ID | Delta | Type | v2026.9.4 evidence |
|---|---|---|---|
| D18 | **Media generation tools** — image / music / video generate tools + GPT Image 2.5 (Flare/Sunburst) | C | `src/agents/tools/image-generate-tool.execution.ts:1`; `src/agents/tools/music-generate-tool.execution.ts:89`; `src/agents/tools/video-generate-tool.execution.ts:8`; `src/image-generation/runtime.ts:64` |
| D19 | **Web fetch HTML tag extraction** (structured fetch) | C | `src/agents/tools/web-fetch-html-tag.ts:3,23` |
| D20 | PDF + transcripts tools | C | `src/agents/tools/transcripts-tool-result.ts:1`; `src/agents/tools/pdf-tool.resources.test.ts` |
| D21 | **Question prompts** — keyboard-driven choices, free text, multi-question, `ask_user` | C | `src/agents/tools/question-prompt-send.ts:27,30` |

### 2.6 Automation / hooks

| ID | Delta | Type | v2026.9.4 evidence |
|---|---|---|---|
| D22 | **Cron delivery** to channels/webhooks/Gmail, payloads, delivery preview/plan | C | `src/cron/delivery.ts:1`; `src/cron/delivery-plan.ts`; `docs/automation/cron-jobs/delivery.md:1`; `docs/automation/cron-jobs/webhooks.md` |
| D23 | **Hooks** — documented event types, bundled hooks, authoring config | C | `docs/automation/hooks/event-types.md:1`; `docs/automation/hooks/bundled-hooks.md`; `docs/automation/hooks/writing-hooks.md` |

### 2.7 Sessions / memory / subagents

| ID | Delta | Type | v2026.9.4 evidence |
|---|---|---|---|
| D24 | **Public session transcript sharing** — publish/revoke read-only session text with a public URL | N | `packages/session-url-contract/src/public-share.ts:3,19`; `src/config/sessions/session-public-share.ts:11`; `src/gateway/server-methods/sessions-sharing.ts:345` |
| D25 | **Subagent yield handoff** — `sessions_yield` continuation context and tool; recursive delegation enabled by default | C | `src/agents/sessions-yield-context.ts:2`; `src/agents/tools/sessions-yield-tool.ts:2`; `docs/concepts/subagent-yield-handoff.md:1` |
| D26 | **Session repository / managed worktrees** — cloud-session artifacts, workspace access, worktree lifecycle | C | `src/gateway/server-methods/session-repository-artifacts.ts`; `docs/concepts/managed-worktrees.md:1` |

### 2.8 Browser

| ID | Delta | Type | v2026.9.4 evidence |
|---|---|---|---|
| D27 | **Browser live panel** — screencast streaming with screenshot fallback; native Mac tabs; Annotate/Inspect | C | `extensions/browser/src/browser/screencast/session.ts:321`; `extensions/browser/src/browser/routes/agent.screencast.ts:1`; `extensions/browser/src/browser/routes/agent.act.existing-session.ts` |

### 2.9 Providers / models

| ID | Delta | Type | v2026.9.4 evidence |
|---|---|---|---|
| D28 | **Provider account controls + model fallback picker + custom providers** (Settings → Models) | C | `docs/concepts/model-providers/control-ui-and-keys.md:1`; `docs/concepts/model-providers/custom-providers.md:1`; `docs/gateway/config-tools/custom-providers.md` |

### 2.10 Skills

| ID | Delta | Type | v2026.9.4 evidence |
|---|---|---|---|
| D29 | **Skill Workshop agent-owned collection** — backup/restore/rollback, curator status, collection contracts | C | `src/skills/workshop/curator.ts:95`; `src/skills/workshop/collection-backup.ts`; `src/skills/workshop/collection-rollback.ts`; `src/skills/workshop/collection-restore.ts` |

### 2.11 CLI

| ID | Delta | Type | v2026.9.4 evidence |
|---|---|---|---|
| D30 | **Update rollback / repair / recovery** — bounded repair, package/service rollback, update status/history | C | `src/cli/update-cli/update-command-rollback.ts:47`; `src/cli/update-cli/update-command-repair.ts`; `docs/cli/update/repair-and-recovery.md:1` |
| D31 | Plugin marketplace / gateway discovery CLI docs expand (`plugins/marketplace`, `gateway/discovery`, `file-transfer`, `policy/*`, `doctor/*`, `mcp/*`) | C | `docs/cli/plugins/marketplace.md:1`; `docs/cli/gateway/discovery.md:1`; `docs/cli/policy/attestation.md:1`; `docs/cli/file-transfer.md:1` |

### 2.12 Observability

| ID | Delta | Type | v2026.9.4 evidence |
|---|---|---|---|
| D32 | **OpenTelemetry** — traces, spans/events, model-call metrics, privacy/trace-context | N | `docs/gateway/opentelemetry/configuration.md:1`; `docs/gateway/opentelemetry/spans-and-events.md`; `docs/gateway/opentelemetry/model-calls-and-metrics.md` |

Class tally of the 32 delta rows:
**new plugin 1** (D1); **changed/expanded plugin 4** (D2-D5);
**new runtime subsystem 2** (D8, D32) + **changed runtime 2** (D6-D7);
**protocol/API 2** (D9-D10) + **unchanged protocol 1** (D11);
**channel 6** (D12-D17); **tool/media 4** (D18-D21);
**automation 2** (D22-D23); **session/memory 3** (D24-D26);
**browser 1** (D27); **provider 1** (D28); **skill 1** (D29);
**CLI 2** (D30-D31); **dashboard/control-UI** is folded into D1/D24/D32
(short plugin URLs and public share).

---

## 3. Cross-map: deltas → Qubit capability modules

Status legend is capability-level and honest:
**COVERED** = a real Qubit backend path exists and a proving test exercises it;
**PARTIAL** = a real sub-path exists but the upstream breadth/side-effect is
missing; **MISSING** = no Qubit path; **GATED** = typed refusal by policy.
Proving tests are the Qubit test file that imports the named module.

| Delta | Qubit backend (module:symbol) | Status | Proving test | Smallest honest fix | HW/creds |
|---|---|---|---|---|---|
| D1 Team Reports | `voicebridge/communications/reports.py`, `dashboard_ops.py`, `kanban.py` | PARTIAL | `tests/test_report_bundles.py`, `test_communications_monitor.py` | add a GitHub/Discord activity aggregator + report page over existing report store | GitHub/Discord creds |
| D2 Policy plugin | `voicebridge/safety/policy.py`, `voicebridge/opencode/policy.py` | PARTIAL | none specific | extend `safety/policy.py` with user-authored conformance rules + attestation output | no |
| D3 File Transfer | `voicebridge/files.py` (read), `voicebridge/node_host.py` | MISSING | `tests/test_files_ide8u.py` (read only) | add node-host dir list/fetch/stream commands to `node_host.py` + `files.py` | no |
| D4 Active Memory | `voicebridge/memory/store.py`, `memory/semantic.py`, `local_models.py` | PARTIAL | `tests/test_packet5b.py`, `test_local_models8u.py` | add active-recall triggers/session policy layer over existing memory store | no |
| D5 QA/E2E stack | test suite only | MISSING | none | build a scenario runner over the existing pytest harness + voice/CLI surface | no |
| D6 Cloud workers | `voicebridge/runtime_images.py`, `voicebridge/node_host.py` | PARTIAL | `tests/test_runtime_images8u.py`, `test_node_host8u.py` | local prepared-image reuse already exists; cloud provisioning needs a provider | cloud creds |
| D7 Node runtime recovery | `voicebridge/runtime_images.py` | PARTIAL | `tests/test_runtime_images8u.py` | add runtime compatibility check + private recovery/rollback path | no |
| D8 Secrets egress proxy | none | MISSING | none | new `voicebridge/secrets/egress.py` proxy registry; large | no |
| D9 New gateway RPC methods | `voicebridge/gateway/gateway_ws.py:40` (`FrameGuard`), `gateway_peers.py`, `hermes_compat.py` (`ROUTES`) | PARTIAL | `tests/test_gateway_frames8u.py` | Qubit has no gateway server; expose webui routes instead where meaningful | no |
| D10 MCP hosted apps | `voicebridge/plugin_runtime.py` (`PLUGIN_KINDS = tool/channel/provider/hook`, `invoke`) | PARTIAL | `tests/test_plugin_runtime8u.py` | add an MCP server/client transport + attach-grant store over the existing plugin host | no |
| D11 Protocol v4 | `voicebridge/gateway/gateway_ws.py:40` (`FrameGuard`) | COVERED | `tests/test_gateway_frames8u.py` | none (protocol unchanged) | no |
| D12 Discord voice/transcripts | `channels_inbound.py`, `messaging.py` | MISSING | `tests/test_channels_inbound8u.py` | add a Discord adapter to `messaging.PROVIDERS` + voice transport | Discord creds |
| D13 Telegram mini-app/albums | `channels_inbound.py`, `messaging.py` | MISSING | `tests/test_channels_inbound8u.py` | add a Telegram adapter (mini-app/webhook) | Telegram creds |
| D14 Slack Enterprise | `voicebridge/messaging.py` | MISSING | `tests/test_messaging8tf.py` | extend provider caps for Slack enterprise grid | Slack creds |
| D15 iMessage | `voicebridge/messaging.py` | MISSING | `tests/test_messaging8tf.py` | new provider (macOS-only private API) | macOS/creds |
| D16 Feishu | `voicebridge/messaging.py` | MISSING | `tests/test_messaging8tf.py` | new provider + dynamic-agent routing | Feishu creds |
| D17 Matrix | `voicebridge/messaging.py` | MISSING | `tests/test_messaging8tf.py` | new provider + E2EE handling | Matrix creds |
| D18 Media generation | `voicebridge/media_gen.py:68` (`KINDS=image,music,video,speech`), `generate:677` | PARTIAL | `tests/test_media_gen8u.py` | provider breadth (GPT Image 2.5 variants); kinds already modeled | provider creds |
| D19 Web fetch HTML | `voicebridge/web_tool.py:243` (`fetch`), `search:468` | COVERED | `tests/test_web_tool8u.py` | structured tag extraction is a refinement | network |
| D20 PDF/transcripts tools | `voicebridge/files.py` (read/list/inspect) | MISSING | `tests/test_files_ide8u.py` | add PDF text + transcript extraction tools bounded by `files.py` | no |
| D21 Question prompts | `voicebridge/cli.py`, voice registry | PARTIAL | `tests/test_voice_commands8tf.py` | add an `ask_user` tool/route for multi-question prompts | no |
| D22 Cron delivery | `voicebridge/schedules.py` (delivery targets, typed SENT/UNAVAILABLE) | COVERED | `tests/test_schedule_delivery8u.py` | Gmail delivery transport if desired | Gmail creds |
| D23 Hooks event types | `voicebridge/hooks.py` (`declare`, `fire`; disarmed) | PARTIAL | `tests/test_hooks8tf.py` | emit real lifecycle events into the existing hook registry | no |
| D24 Public session share | none (receipt list only) | MISSING | `tests/test_owner_session8tf.py` | add share token + revocable read-only view to the receipt store | no |
| D25 Subagent yield | `voicebridge/broker.py`, `voicebridge/agent.py` | PARTIAL | none specific | add yield/handoff continuation context to the broker turn model | no |
| D26 Session repository/worktrees | `voicebridge/opencode/workdir.py`, `voicebridge/git_scope.py` | PARTIAL | `tests/test_opencode_voice8u.py` | add managed worktree lifecycle (create/remove/restore/gc) | no |
| D27 Browser live screencast | `voicebridge/browser_tabs.py:387` (`screenshot`) | PARTIAL | `tests/test_browser_screenshot8u.py` | add a bounded screencast stream loop over CDP `Page.startScreencast` | Chromium |
| D28 Provider accounts | `voicebridge/local_models.py`, `voicebridge/brain.py`, `voicebridge/config.py` | PARTIAL | `tests/test_local_models8u.py` | multi-account add/remove/priority controls | provider creds |
| D29 Skill Workshop | `voicebridge/verified_skills/`, `voicebridge/plugin_runtime.py` | PARTIAL | `tests/test_packet8p.py`, `test_plugin_runtime8u.py` | agent-owned skill collection backup/restore/rollback lifecycle | no |
| D30 Update rollback | `voicebridge/runtime_images.py` (install/rollback) | PARTIAL | `tests/test_runtime_images8u.py` | add core/package update rollback semantics beyond runtime images | no |
| D31 CLI doc surface | `voicebridge/cli.py` families | PARTIAL | `tests/test_native_equivalents8u.py` | align CLI naming where a real capability exists; not a code delta | no |
| D32 OpenTelemetry | none | MISSING | none | add an OTLP exporter (traces/metrics) behind config | no |

Tallies across the 32 delta rows:

| Status | Count | Rows |
|---|---|---|
| COVERED | 3 | D11, D19, D22 |
| PARTIAL | 17 | D1, D2, D4, D6, D7, D9, D10, D18, D21, D23, D25, D26, D27, D28, D29, D30, D31 |
| MISSING | 12 | D3, D5, D8, D12, D13, D14, D15, D16, D17, D20, D24, D32 |
| GATED | 0 | — |

---

## 4. Ranked missing/partial deltas

Ranked by (user-visible value ÷ smallest honest fix). Items completable with no
hardware/credentials come first.

1. **MCP hosted apps transport (D10, PARTIAL)** — `plugin_runtime.py` already
   models `tool/channel/provider/hook` plugins and an invoke gate; the missing
   piece is an MCP server/client transport plus an attach-grant store. No HW.
2. **Skill Workshop collection lifecycle (D29, PARTIAL)** — add
   backup/restore/rollback/curator status over `verified_skills/` +
   `plugin_runtime.py`. No HW.
3. **Team Reports aggregator (D1, PARTIAL)** — a read model over
   `communications/reports.py` and `kanban.py`, rendered via `dashboard_ops.py`.
   Live sources need GitHub/Discord credentials; the report pipeline does not.
4. **Policy/conformance engine (D2, PARTIAL)** — extend `safety/policy.py` with
   user-authored rules and an attestation/findings output. No HW.
5. **File transfer dir list/fetch (D3, MISSING)** — node-host directory
   list/fetch/stream commands built on `files.py` + `node_host.py`. No HW.
6. **Browser live screencast (D27, PARTIAL)** — extend
   `browser_tabs.screenshot` (`voicebridge/browser_tabs.py:387`) into a bounded CDP
   `Page.startScreencast` loop. Needs only a running Chromium.
7. **Public session share (D24, MISSING)** — add a revocable share token and
   read-only view to the session receipt store, mirroring
   `packages/session-url-contract/src/public-share.ts`. No HW.
8. **Managed worktree lifecycle (D26, PARTIAL)** — create/remove/restore/gc over
   `opencode/workdir.py` + `git_scope.py`. No HW.
9. **PDF/transcript tools (D20, MISSING)** — bounded extractors over
   `files.py`. No HW.
10. **Hooks event fidelity (D23, PARTIAL)** — emit real lifecycle events into
    `hooks.py`'s existing registry (execution stays disarmed by policy). No HW.

Explicitly deferred, with reasons:
- **Cloud workers (D6), channels (D12–D17), media/provider breadth (D18, D28)**
  need provider credentials or specific hosts; a typed-unavailable state is the
  honest interim.
- **Secrets egress proxy (D8)** and **OpenTelemetry (D32)** are new subsystems;
  both are larger than a "smallest fix" and have no Qubit path today.
- **QA/E2E stack (D5)** overlaps the existing pytest harness and is best scoped
  as a harness refactor, not a feature.
- **iMessage (D15)** is macOS-only (private API) and likely out of scope for the
  local Linux runtime.

---

## 5. What is NOT verified

- **No OpenClaw code was executed.** All deltas are read from the v2026.9.4
  tree via `git show`/`git grep`; runtime behavior of upstream plugins (Team
  Reports, MCP apps, cloud workers) was not started or tested.
- **The clone is shallow.** `3928bad9` and `3a9d69db` are independent shallow
  roots; tree-level `git diff` is valid, but ancestry/commit-list reasoning
  (e.g. counting PRs) relies on the upstream `CHANGELOG`, not a local walk.
- **Qubit statuses are module/test-presence based.** "COVERED" means the module
  and a test that imports it exist; this audit did not re-run the Qubit tests.
  Statuses are therefore slightly more optimistic than a behavior run would be,
  though the modules inspected (`media_gen.generate`, `web_tool.fetch`,
  `schedules` delivery, `browser_tabs.screenshot`, `plugin_runtime.invoke`,
  `file_patch.apply`, `task_ledger.list_tasks`) contain real stores, transports,
  and typed refusals, not stubs.
- **Line numbers are pinned to `3a9d69db` only.** Any later upstream commit can
  shift them; re-fetch and re-check with `git show 3a9d69db:<path>`.
- **Delta selection is curated, not exhaustive.** The interval contains 1,174
  PRs and ~15k changed files; this inventory covers the capability-class
  surfaces named in the task (CLI, dashboard/routes, API/RPC, tools, plugins,
  skills, providers, channels, protocols, runtime/device) and intentionally
  omits per-file refactors, test churn, and doc reorganizations.
- **The repo was not clean at audit start.** Unrelated uncommitted edits to
  `voicebridge/hermes_compat.py` and four docs/tests already existed; their
  effects on route/parity counts are not accounted for here.
