# Non-Hermes upstream pins (owner ruling #27, 2026-10-03)

Hermes→Qubit is frozen at `69948c0057`. Every other ported surface tracks its
own upstream below — 1:1 layout/controls/flows, Qubit rebrand, Qubit backend.
Local clones live under the scratch dir (ephemeral); re-fetch by repo+commit.

| Family | Repo | Pinned commit | Date | License | Source paths ported from |
|---|---|---|---|---|---|
| OpenClaw console | https://github.com/openclaw/openclaw | `7ff3b3be3713751de356800167d67ce752ad0d04` | 2026-10-02 | MIT | `extensions/workboard/browser/**` (workboard UI: pages/workboard, widgets, styles); `ui/src/pages/sessions/` + `styles/sessions.css`; `ui/src/pages/devices/` + `styles/devices.css`; `ui/src/pages/cron/` + `styles/cron.css` + `src/claws/cron.ts`; `ui/src/pages/worktrees/`; `ui/src/pages/config/memory-page.ts` + `extensions/{memory-lancedb,memory-wiki,active-memory}`; `extensions/canvas`; `ui/src/pages/debug/view.ts` (MCP manual-RPC); `ui/src/pages/{skill-workshop,skills}/` (skills review); `docs/automation/{standing-orders,hooks,cron-jobs}.md` (taskflows). NOTE: `ui/src/ui/views/*` paths cited in old page headers do NOT exist at this pin (stale); real paths are `ui/src/pages/*` (headers corrected in the port). |
| Open Viking | none — Qubit-original | n/a | n/a | Qubit-owned | No viking counterpart exists anywhere in the OpenClaw tree (single fixture-data mention of a third-party `clawhub:@openviking/openclaw-plugin` slug). Console + 4 subpages brought to native Qubit DS quality; no port. |
| Harness console | https://github.com/HarnessRouter/harnessrouter | `f3192445c82da533fe83b125917efb8c3947aef0` | 2026-10-02 | Apache-2.0 | `ui/src/**` (Next.js console) |
| Bot/WebChat chat | https://github.com/CopilotKit/openmuse | `9ec439fbaa878197d9d44c2aa982cca55676dd68` | 2026-10-02 | MIT | `apps/mobile/src/chat.tsx`, `threads.tsx`, `agent-ui.tsx`, `assistant-response.tsx`, `conversation-queue.ts`, `conversation-run.ts` |
| Memory extensions | OpenClaw tree (same pin) | `7ff3b3be…` | 2026-10-02 | MIT | folded into the OpenClaw console row: `ui/src/pages/config/memory-page.ts` + `extensions/{memory-lancedb,memory-wiki,active-memory}` |
| Canvas extension | OpenClaw tree (same pin) | `7ff3b3be…` | 2026-10-02 | MIT | canvas extension dir (TBD) |

Attribution: keep upstream license/copyright text in `LICENSE` /
`THIRD_PARTY_NOTICES.md` (the one legal exception to zero-brand). No verbatim
upstream prose is reproduced in user-facing pages unless the port requires it;
where layout/copy is mirrored, headers cite repo+commit+path (as the existing
page headers already do).

Page → upstream map (initial; extended as each port lands):

| Qubit page | Upstream surface |
|---|---|
| `/qubit-task-inbox`, `/qubit-board-admin` | OpenClaw workboard (`extensions/workboard/browser`) |
| `/qubit-scheduler`, `/qubit-taskflows` | OpenClaw cron/scheduler + `docs/automation/{taskflow,standing-orders,hooks}.md` |
| `/session-ops`, `/nodes`, `/devices`, `/worktrees`, `/checkpoints` | OpenClaw sessions/nodes views + infra |
| `/harness-router` | harnessrouter `ui/src` console |
| `/qubit-bot`, `/webchat` | openmuse `apps/mobile/src` chat |
| `/qubit-memory` | memory-lancedb / memory-wiki / active-memory |
| `/canvas` | canvas extension |
| `/admin/mcp-calls`, `/admin/skills-review` | upstream MCP debug view / skill workshop board (owner to confirm which upstream per page if ambiguous) |
