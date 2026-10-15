# Web dashboard sidebar and page information architecture audit

**Date:** 2026-10-11  
**Scope:** Read-only review of the web dashboard sidebar, page overlap, and the Open Viking route group.  
**Requested outcome:** Make navigation easier to understand, decide where page consolidation is justified, and explain what pages do where users encounter them.

## Summary

The dashboard has too many destinations presented as one long navigation scan. The current source defines **45 top-level built-in links, including Chat and Qubit Chat, plus four indented Open Viking child links**. The live review also saw 12 plugin links. Open Viking is already a real parent with four indented child routes, but its destinations are repeated in global navigation, page tabs, and landing-page panels. That repetition weakens the parent/child structure and makes the area feel larger than its five routes.

There is clear duplication inside Open Viking and one verified duplicate scheduled-jobs collection exposed through both Cron and Taskflows. Several other page names sound related but use different records or actions. Group related pages for discoverability; consolidate only where the audit confirmed shared data/actions or a deeper comparison proves it.

No source files were changed for this review.

## Review method and limits

- Two independent reviewers examined the navigation and Open Viking area. One inspected a fresh browser view and source; the other ran the Impeccable detector and checked live route responses.
- The Impeccable detector returned an empty findings list for `App.tsx` and the five Open Viking page components. This means it found no detector rule violations; it does not assess whether the information architecture is easy to understand.
- Browser inspection verified the visible link count and repeated Open Viking navigation. Direct HTTP requests returned the shared SPA shell for all five routes; they confirm route serving, not the rendered behavior of each page.
- This was not a full accessibility, responsive, task-completion, or error-state audit. The findings below separate observed facts from design judgments.
- The repo instructions name `docs/README.md` and `docs/STATUS.md` as living references, but those files were absent at review time. `docs/IDENTITY.md`, `PRODUCT.md`, and `DESIGN.md` were present. No release or parity status is inferred here.

## Sidebar assessment

The source defines 45 top-level built-in destinations, including Chat, Qubit Chat, and the Open Viking parent, followed by four indented Open Viking child routes. The live review saw 12 plugin destinations. Most built-in items appear as a flat list; Open Viking is the only explicit group. The navigation therefore asks a new user to scan many unrelated concepts at once, while plugin destinations lengthen the same scan.

Labels including **MCP**, **Pairing**, **Nodes**, **Harness Router**, and **Session Ops** rely on prior product knowledge. The visible navigation does not provide a short explanation for these terms. Collapse support helps reduce width, but does not reduce the number of choices. Reviewers found no visible favorites or navigation search.

### Nielsen heuristic snapshot

Scores are 0–4, where 4 is strongest. The total is a directional critique of the reviewed sidebar and Open Viking routes, not an overall product-quality or accessibility score.

| Heuristic | Score | Evidence or issue |
|---|---:|---|
| Visibility of system status | 3/4 | Sidebar displays gateway status, active sessions, and latency; Open Viking shows local-engine status. |
| Match between system and real world | 2/4 | Several navigation terms are unexplained technical jargon. |
| User control and freedom | 3/4 | Sidebar can collapse; Open Viking children are always expanded, with no independent group collapse. |
| Consistency and standards | 2/4 | Open Viking routes appear in the sidebar, shared page tabs, and landing-page navigation panels. |
| Error prevention | 3/4 | Uploads has an explicit confirmation gate before writes are staged. |
| Recognition rather than recall | 2/4 | Page introductions help, but the long list makes it difficult to compare destinations. |
| Flexibility and efficiency of use | 2/4 | No visible search or favorites; users repeatedly scan a large list. |
| Aesthetic and minimalist design | 2/4 | The Open Viking group is clear locally, while the global navigation has many competing links. |
| Error recognition and recovery | 2/4 | Some unavailable states explain a reason; recovery guidance is not consistent across reviewed pages. |
| Help and documentation | 2/4 | Resources offers orientation but repeats route navigation instead of acting as focused contextual help. |
| **Total** | **23/40** | **Acceptable, with significant information-architecture improvements needed.** |

## Open Viking: current structure and friction

The router already models the requested parent-and-child relationship: `/open-viking` is the parent, with `/open-viking/retrieval`, `/open-viking/uploads`, `/open-viking/agent-hub`, and `/open-viking/resources` as children. The sidebar source indents those children and highlights the parent on its exact route. So the requested hierarchy exists structurally.

The hierarchy is obscured by repeated navigation:

1. Every Open Viking page shows a shared five-route switcher.
2. The landing page adds a Workspace navigation group for Playground, Retrieval, Uploads, and Agent Hub.
3. The landing page adds another Resources group. Its Documentation, About, SDK & API, and Agent Integrations links all go to `/open-viking/resources`, despite sounding like different destinations.
4. The Resources page shows another four-card Console map linking back to the other routes.

The five routes do not all represent equally clear operational tasks:

| Route | Current apparent job | Assessment |
|---|---|---|
| Open Viking (`/open-viking`) | Operational context-tree and file-preview workspace; the route title is Open Viking while its in-page tab calls it Playground | Keep this route as the requested section home, but make the overview purpose explicit. Resolve the Open Viking/Playground naming mismatch. Keep the existing context browser available from the home page or give it a clear “Browse context” action. |
| Retrieval | One-shot search of the local context store | Distinct task; keep. Explain what is searched and how results are used. |
| Uploads | Stage resource or memory writes and confirm them | Distinct task; keep. Explain what is stored, what confirmation does, and where the result appears. |
| Agent Hub | Viking bot sessions and skills | Keep under Open Viking. Clarify that these are Viking-specific bots/skills. Do not merge with global Sessions or Skills until their records and actions are proven equivalent. |
| Resources | Orientation, route map, documentation/about/API/integration explanations, engine status | This currently behaves mostly as help/index content rather than a separate resource-management task. Remove the repeated route map; keep this route only if it becomes a focused guide or a real resource view. |

### Recommendation for Open Viking

Keep the global sidebar structure as **Open Viking → Retrieval, Uploads, Agent Hub, Resources**. Use one in-page navigation treatment, not both global navigation plus duplicated tabs and local route panels. Make `/open-viking` an obvious section home, with a short purpose statement and clear cards for the four child tasks. Move brief explanations next to the corresponding actions. Keep Resources as a child only if it has useful reference material or resource-management work that cannot be explained on the relevant pages; otherwise preserve the route as a compatibility redirect or integrate its content into the section home.

## Are there too many pages doing the same thing?

There are definitely too many **top-level choices to scan**, but this review did not establish that the wider dashboard has many duplicate implementations. Several pairs need clearer distinctions rather than immediate merging:

| Similar-looking pages | Why they may be different | Recommendation |
|---|---|---|
| Chat and Qubit Chat | The user-defined distinction is API-provider chat vs local-model chat; source also confirms distinct routes and session behavior. | Keep them as two separate routes and two individually visible menu links. Never merge them into one page or alias; describe each according to its actual provider/model behavior. |
| Sessions and Open Viking Agent Hub | General dashboard sessions may differ from Viking bot sessions. | Keep separate for now; use names and descriptions that expose the scope. Compare backing records and available actions before merging. |
| Files and Open Viking Uploads | File browsing and controlled ingestion are different tasks. | Keep distinct, link the next step between them, and explain when to use each. |
| Global Skills and Open Viking Agent Hub skills | Global skill administration may differ from Viking’s skill catalog and bot integration. | Keep distinct pending a data/action comparison. Clarify the owner and effect of each page. |

The main cleanup opportunity is to combine related items into a few **collapsible task groups**, not to delete pages simply because their names are similar. Candidate groups to validate against each route’s actual primary action are **Chat**, **Build & Work**, **Knowledge**, **Automation & Integrations**, and **System & Administration**. Put plugin destinations under a separate **Extensions** group. Keep individual routes and permissions intact while changing their presentation.

## Help users understand each page in context

Give each dashboard page a compact, consistent explanation near its title or first action:

- **What this page does** — one plain-language sentence.
- **When to use it** — the user’s trigger or goal.
- **What changes** — data written, service contacted, or whether the action is local.
- **First step / next step** — a direct action and where to go afterward.

For technical terms, add a short inline definition or keyboard-accessible help popover. Tooltips can supplement navigation labels but should not be the only explanation. Put deeper instructions in an expandable “How it works” area on the relevant page rather than routing users through a separate index. Use examples and clear empty, loading, unavailable, and success states to explain the next action.

## Prioritized findings

1. **P1 — Reduce the global navigation scan.** Group related routes into a small number of collapsible task areas; separate plugins from core pages. Consider favorites or navigation search for frequent routes. Preserve the route destinations while simplifying the menu.
2. **P1 — Remove repeated Open Viking route maps.** Keep the sidebar parent-and-children structure and choose one in-page navigation pattern.
3. **P2 — Give Resources one clear purpose.** Its current cards repeat route navigation, and four differently named landing-page links all lead to the same resource page. Make these real anchors/sections, use one descriptive link, or move guidance to the page it explains.
4. **P2 — Explain unfamiliar terms and page scope.** Add brief visible descriptions for jargon and clarify differences between global and Open Viking sessions/skills.
5. **P3 — Improve frequent-route access.** Add favorites/recent destinations or navigation search if users commonly move among many specialist pages.

## Evidence references

- Route registration and Open Viking sidebar group: `frontend/qubit-web/src/App.tsx` (around lines 296–300 and 384–405).
- Shared Open Viking route switcher: `frontend/qubit-web/src/components/viking-settings.tsx` (around lines 25–93).
- Landing-page navigation and links that converge on Resources: `frontend/qubit-web/src/pages/OpenVikingPage.tsx` (around lines 67–76 and 241–300).
- Resources route map and explanatory sections: `frontend/qubit-web/src/pages/OpenVikingResourcesPage.tsx` (around lines 76–123).

These are review references only; no implementation was made.

## Expanded dashboard review — 2026-10-11

This section extends the initial sidebar/Open Viking review to the full built-in navigation. The second pass used a Playwright accessibility-tree snapshot of `/open-viking` and source/API tracing for the related task, agent, and operations pages. It is still an information-architecture audit, not a visual styling review or a workflow-by-workflow verification of every page.

### Full menu shape

The 45 top-level built-in links are spread across several types of work: chat surfaces, sessions, agent workspaces, task management, knowledge and files, integrations, monitoring, and administration. Open Viking is currently the only built-in destination with child links. Plugin manifests add dynamic destinations; the live instance reviewed had 12.

An initial grouping proposal to validate with page owners:

| Proposed section | Current destinations that appear to fit | Keep distinct where… |
|---|---|---|
| **Chat & Sessions** | Chat, Qubit Chat, Sessions, Talk, Qubit WebChat, Qubit Bot | Keep `/chat` and `/qubit-chat` as distinct, individually visible destinations. Make the API-provider vs local-model roles explicit in their descriptions. |
| **Agent Tools** | Agents, Agent Workspace, Computer, Agent Cards, Kitchen, Qubit Canvas | These are separate tools, but several labels sound like alternate views of one agent workspace. Explain the unique action in each. |
| **Tasks & Automation** | Board Admin, Task Inbox, Qubit Taskflows, Qubit Tasks, Cron, Scheduler, Worktrees, Checkpoints | Task Inbox and Background Tasks use different APIs; Cron and Taskflows both read the cron-jobs collection. Group by workflow and consolidate the duplicate schedule surface while preserving distinct task records. |
| **Knowledge & Files** | Files, Skills, Qubit Memory Backends, Open Viking and its children | File browsing, installed skills, memory backend setup, and local context retrieval have distinct effects. Cross-link the next step instead of hiding these differences. |
| **Connections & Devices** | MCP, MCP Calls, Channels, Webhooks, Pairing, Nodes, Devices, Harness Router | Configuration, live call history, device enrollment, and routing are related but not the same task. Explain whether a page configures, monitors, or invokes a connection. |
| **Operations** | Analytics, Logs, System, Session Ops | Separate read-only monitoring from controls that can intervene in a running service or session. |
| **Administration & Help** | Models, Plugins, Profiles, Config, Keys, Documentation, Voice Editing, Skills Review, Users | Separate user preferences/account setup from the user directory and administration; make ownership and impact clear. |

These labels are candidates, not a final menu specification. Keep the global sidebar familiar, let users collapse sections, keep plugins under a clearly named **Extensions** group, and consider a navigation search or favorites for frequent destinations. Avoid showing every specialist/admin page at equal visual weight. Do not remove routes as part of an information-architecture pass.

### Additional verified overlap and naming findings

1. **Agent tools are scattered and easy to confuse.** The Agents page is a runtime roster; Agent Workspace shows workspace files and run status; Computer also exposes workspace files alongside console and desktop operations; Agent Cards presents agent work and artifacts. Put these together, then clarify their distinct purpose. Workspace and Computer are candidates for a deeper overlap review, not an automatic merge.
2. **Task and automation names hide both hand-offs and a true duplicate.** Task Inbox handles tasks/suggestions; Qubit Tasks handles background task runs; Taskflows says durable flows live there and points suggestions to Task Inbox and media scheduling to Scheduler. Cron and Taskflows both read/write the same cron jobs collection, so users encounter the same underlying scheduled jobs through two pages. Show the distinct task sources clearly and consolidate Cron/Taskflows into one Schedules area with views or filters.
3. **Operations pages need task labels.** Analytics, Logs, System, and Session Ops are all top-level, but labels alone do not say whether they report, diagnose, configure, or intervene. Distinguish read-only views from actions that change live state. Session Ops targets branches, checkpoints, goals, suggestions, and groups; general Sessions focuses on chat history, search/filter, delete/import/prune. Group them together but keep their responsibilities distinct.
4. **Technical terms and metaphors are unexplained.** In addition to MCP, Pairing, and Harness Router, labels such as Cron, Worktrees, Session Ops, and Kitchen assume familiarity. Give each a plain-language subtitle; retain the technical label secondarily where users need it.
5. **Navigation localization is inconsistent in source.** Some entries use translated label keys while many newer/specialist entries use hard-coded English labels. Ensure the whole menu follows the selected language, including plugin labels and section headings.
6. **The only visible child group is always expanded.** The Open Viking children are indented, but the menu has no reusable group/collapse pattern. Add consistent section headings and per-group collapse behavior so nesting means the same thing throughout the sidebar.
7. **Open Viking’s root is not currently an overview.** The live page presents context tree, file viewer, related-context actions, and quick-start actions. It also calls the route “Open Viking” in the page title but “Playground” in its in-page switcher. Make it the section landing page users expect, and expose the context browser as a clearly named action.
8. **Page guidance is uneven.** Open Viking’s pages have short descriptions, and Taskflows explains several hand-offs, but the workflow guidance is not consistent across the dashboard. In the inspected Open Viking flow, the intro does not consistently answer when to choose a page, what results to expect, or what limits apply. Standardize the guidance without adding another navigation layer.
9. **Models and Analytics repeat usage concepts.** Both expose token/cost/session/call totals and model-level usage, while Models also configures providers/assignments and Analytics adds daily and skill breakdowns. Keep Models focused on provider/model setup, assignment, and catalog/status; make Analytics the canonical home for trends and usage reporting. Reconcile metric definitions before moving displays; the Analytics page notes that some local counts exclude auxiliary calls and provider retries.
10. **User account surfaces mix audiences.** The Users page fetches and edits personal appearance preferences, OAuth/model account links, and operator/user roster data. Separate “Your preferences & accounts” from “User administration” or give them strong sections and explicit permission boundaries.
11. **Users has a coarse partial-failure state.** It loads seven independent data sources with `Promise.allSettled`, but any single rejection sets one shared “Operator admin failed to load” error. Fulfilled sections can still populate while the user sees a page-level failure. Report the failed section by name and provide a local retry, preserving working sections.
12. **Some status copy speaks in implementation language.** The Users action helper reports “`{verb} settled ✓`”. Replace “settled” with the user-visible result (for example, “Theme saved” or “Account disconnected”) and state any follow-up required.
13. **Model configuration depends on analytics loading.** Models fetches model usage analytics, auxiliary assignments, and the active profile in one `Promise.all`. If usage analytics fails, the shared load rejects before committing the configuration data. Keep provider/model settings usable when metrics are unavailable and report errors per section.

Suggested plain-language labels to validate with page owners:

| Current label | Possible label | Why |
|---|---|---|
| MCP | Tool connections | Names the user goal; keep “MCP” in the page’s technical details. |
| MCP Calls | Tool activity | Makes it clear this is an activity/history view, if that matches the page controls. |
| Pairing | Pair a device | Uses an action and makes the outcome clearer. |
| Nodes | Remote nodes | Adds the relationship the noun alone omits. |
| Harness Router | Agent routing | Describes the job in user language; keep the technical name in details. |
| Cron | Scheduled jobs | Names the operation without requiring scheduler terminology. |
| Worktrees | Code workspaces | Gives new users a recognizable concept while preserving “worktree” in help. |
| Session Ops | Session operations | Spells out the abbreviation and flags advanced controls. |
| Keys | API keys & secrets | Tells users what kind of keys live there. |
| Kitchen | Needs owner review | The metaphor does not reveal its function; name it only after confirming the page’s main job. |

### Merge decisions: evidence versus resemblance

The strongest immediate case is to **group** destinations and make their scope visible. There is one confirmed duplicate collection surface (Cron and Taskflows), while other pages require comparing actual records, controls, permissions, and outcomes before any consolidation:

- Agent Workspace vs Computer: both expose workspace files; Computer adds console/desktop operations.
- Cron vs Taskflows: **confirmed overlap** — Taskflows `listFlows()` reads `/api/cron/jobs`, the same collection Cron uses for job list/create/pause/update/resume/trigger/delete. Consolidate into one Schedules area with jobs, durable flows, and blueprints as views; preserve the old route as an alias or redirect if routes are consolidated.
- Taskflows contributes hooks and scope notes in addition to its filtered views of cron-backed jobs; keep hooks as their own capability while unifying the shared jobs collection.
- Scheduler vs Cron/Task Inbox: Scheduler is a mixed control page. It includes cron enable/disable-by-ID, TTS provider/profile/persona settings, suspend/resume power controls, and task cancel/retry RPCs. Re-home each action with its primary task (schedule, voice, system/power, or tasks) and avoid leaving an unexplained “Scheduler” grab bag.
- Task Inbox vs Qubit Tasks: distinct registries/APIs (task ledger and ideas vs background runs); put them under one Tasks group and label each source clearly, rather than merging the data.
- Analytics vs Logs vs Session Ops: adjacent operations surfaces; separate reporting, diagnosis, and intervention roles.
- Analytics vs Models usage: overlapping totals and model-level metrics suggest one Usage destination; keep model provider/assignment settings on Models. Confirm endpoint metric definitions (including auxiliary calls/retries and estimated vs actual costs) before merging displays.
- Skills vs Skills Review vs Agent Hub: installed skill management, review workflow, and Viking bot/skill integration may refer to different data and permissions.
- MCP vs MCP Calls: configuration/integration versus call activity; confirm through the respective page actions.
- Users / Appearance / Accounts: the Users page also contains personal theme/font preferences and model-account/provider links. Split those personal setup tasks from the user directory, or visibly divide the page by “Your settings” and “User administration” with explicit permission boundaries.

General Sessions vs Session Ops also belong together in the sidebar, but should remain distinct: Sessions handles chat history/search/filter and history management, while Session Ops targets branch/checkpoint/goal/suggestion/group operations.

### Page-level explanation standard

Apply a small orientation block consistently across the dashboard:

1. **Purpose:** what the page controls or shows.
2. **Use it when:** the user’s goal or situation.
3. **Effect:** what changes, which service/data is touched, and whether the action is reversible.
4. **Next step:** the expected follow-on page or action.

Keep the first explanation visible near the page title or primary action. Put deeper “How it works” details behind contextual help, and define unfamiliar terms on first use. Error, empty, loading, and success states should say what happened and offer a safe next step. A tooltip can help explain a menu label, but should not be the only place where a workflow is documented.

### Additional evidence references

- Primary navigation and Open Viking children: `frontend/qubit-web/src/App.tsx` (around lines 217–395).
- Plugin insertion and duplicate-path handling: `frontend/qubit-web/src/App.tsx` (around lines 438–478).
- Sidebar group rendering and translated vs hard-coded labels: `frontend/qubit-web/src/App.tsx` (around lines 1060–1194).
- Chat route distinctions: `frontend/qubit-web/src/lib/chat-routes.ts` and `frontend/qubit-web/src/components/ChatSidebar.tsx`.
- Agent pages: `frontend/qubit-web/src/pages/AgentsPage.tsx`, `AgentWorkspacePage.tsx`, `ComputerWorkspacePage.tsx`, and `AgentCardsPage.tsx`.
- Task/automation descriptions and actions: `TaskInboxPage.tsx`, `TasksPage.tsx`, `TaskflowsPage.tsx`, `CronPage.tsx`, and `SchedulerMediaPage.tsx` under `frontend/qubit-web/src/pages/`.
- Operations surfaces: `AnalyticsPage.tsx`, `LogsPage.tsx`, and `SessionOpsPage.tsx` under `frontend/qubit-web/src/pages/`.
- Cron/Taskflows shared jobs collection: `frontend/qubit-web/src/lib/ocFamilyFlowsApi.ts` (`listFlows`) and `frontend/qubit-web/src/lib/api.ts` (Cron job CRUD/actions).
- Session data/action boundaries: `frontend/qubit-web/src/pages/SessionsPage.tsx` and `SessionOpsPage.tsx`.
- Models vs Analytics and model-change scope: `frontend/qubit-web/src/pages/ModelsPage.tsx`, `AnalyticsPage.tsx`, and related response interfaces in `frontend/qubit-web/src/lib/api.ts`.
- Mixed user/settings data and feedback: `frontend/qubit-web/src/pages/UsersAdminPage.tsx`.
- Independent Models page loads: `frontend/qubit-web/src/pages/ModelsPage.tsx` (`getModelsAnalytics`, auxiliary assignments, and active profile loading).
- Plugin hub workflows and load errors: `frontend/qubit-web/src/pages/PluginsPage.tsx` (catalog, installed plugins, memory providers, context engine, and indexes).
- Reports overlap: `frontend/qubit-app/plugins/qubit-reports/qubit-reports.js` and `frontend/qubit-web/src/pages/AnalyticsPage.tsx`.

The page semantics above were source-reviewed; the proposed groups, priority, labels, and merge candidates are design recommendations. No application source was changed.

### New high-priority recommendations

1. **P1 — Consolidate Cron and Taskflows around one jobs collection.** Put scheduled jobs, durable flows, and blueprints in a single Schedules area with separate views/filters. Preserve existing route URLs as aliases during transition; verify permissions and outcomes before retiring any screen.
2. **P1 — Split Scheduler’s mixed controls by user intent.** Move schedule actions to Schedules, task cancel/retry to Tasks, voice/TTS settings to Voice, and host suspend/resume to System or Power controls. If the current route must remain, make it an explicit operations dashboard with links to the owning workflows.
3. **P2 — Make task records legible.** Put Task Inbox and Background Tasks under a Tasks group, but label their different sources and actions. Link scheduler-triggered task controls to the canonical task view rather than duplicating operations.
4. **P2 — Improve agent-workspace wayfinding.** Group Agents, Agent Workspace, Computer, and Agent Cards. Clarify the difference between the runtime roster, workspace/run view, computer/desktop controls, and agent artifacts. Consider combining Workspace and Computer only after comparing their complete actions and access rules.
5. **P2 — Give Plugins task-focused sections and independent recovery.** Plugins combines discovery/install, installed-plugin management, memory-provider setup, context-engine selection, and index management. Use clear sections or focused routes under Extensions, while keeping `/plugins` as a useful overview. The initial hub load shows a generic “loading” toast and drops the underlying error; give each domain its own error state and retry so a hub failure does not hide a usable catalog.

### Additional interaction and recovery findings

These findings focus on what happens after a user opens a page or starts an action. Better labels and grouping will not be enough if the interface hides partial failure, exposes raw identifiers, or gives unclear recovery steps.

1. **Separate personal setup from operator administration.** The Users page mixes personal appearance preferences, OAuth/model-account connections, and the user roster. Give these tasks distinct destinations or strong, permission-aware sections. Use native theme/font controls and provider/account pickers rather than raw editable values or opaque credential references. Replace manually typed session/profile IDs and numeric unlink indexes with contextual selectors and a target preview; render linked credentials as readable masked states instead of raw JSON. State who can see or change each item.
2. **Report partial failure where it occurs.** Users loads seven independent sources and uses `Promise.allSettled`, but one rejection becomes a page-level “Operator admin failed to load” alert even when other sections succeeded. Keep successful sections usable; identify the failed section, show whether its data is stale, and offer a retry scoped to that section.
3. **Keep configuration usable when analytics is down.** Models loads usage analytics, auxiliary assignments, and the active profile together. A metrics failure can block the combined load, while profile failure is caught separately. Load configuration and metrics independently; show a local retry and preserve the last known settings.
4. **Make usage analytics discoverable and consistent.** Analytics, Models, and the Reports plugin all expose some usage or activity information, and the `dashboard.show_token_analytics` setting affects them differently. Keep model catalog, provider setup, and assignments on Models; choose one canonical Usage/Analytics home for trends and cost reporting; keep only genuinely unique report generation and insights in Reports. Use one plain-language analytics setting and explanation everywhere, including whether values are estimates and what calls are excluded.
5. **Distinguish API reference from product instructions.** The Docs route is an OpenAPI operation browser. Label it **API Reference** under Developer/Advanced, then add a product guide for operator tasks and contextual help at the point of use. In the API reference, add a clear-search action and make endpoint/path copying obvious. Separate “service unavailable” from “specification could not be loaded” and retain retry.
6. **Replace raw implementation feedback with outcomes.** The Users action helper can report “`{verb} settled ✓`”. Confirm the user-visible result instead (“Theme saved”, “Account disconnected”) and identify any remaining step, such as completing provider authorization.
7. **Make sensitive actions understandable before commit.** For disconnect, delete, pause, retry, and host-control actions, identify the target by its human-readable name and explain the consequence. Avoid numeric row indexes and raw IDs as the only way to choose an entity. Where safe, offer undo; otherwise state that the action cannot be reversed.
8. **Adopt one state contract across every route.** Each independently loaded section should distinguish loading, empty, ready, stale, partial, failed, and permission-denied states. Explain what failed, the effect on visible data, the last successful update when available, and the next safe action. Preserve good data while retrying the failed part.

**Chat separation is a fixed constraint:** `/chat` and `/qubit-chat` must remain separate routes and individually visible navigation links. The API-provider experience and the local-model experience may share visual components and interaction patterns, but must not be merged into one page, hidden as tabs within a single destination, or aliased to one another. Use purpose descriptions that explain the difference before users navigate.

## Next-generation UX/UI critique — 2026-10-11

### Core diagnosis

The dashboard has many capable tools, but users must learn the route map before they can understand the product. The shared shell identifies the current route and offers optional toolbar slots; it does not provide a common page purpose, scope, effect, next action, or help entry point. Individual pages invent their own orientation. This makes a multi-capability product feel like a directory of modules.

“Next-generation” should mean the interface understands the user’s intent, preserves their context, and explains the consequence of an action. A command surface and contextual guidance would improve that more than adding visual effects. Motion should communicate real progress or state changes—connecting, streaming, waiting for approval, saved, failed—and respect reduced-motion preferences.

### Where the current product misleads

1. **The page header is only a title/toolbar contract.** `PageHeaderProvider` supplies a title and optional after-title/end controls. It has no standard purpose, data scope, impact, or contextual-help slot. Users get different levels of guidance from page to page.
2. **“Documentation” is an API reference.** The Docs page loads OpenAPI operations and filters by path, method, or summary. That is useful for developers, but it does not teach operators how to use Sessions, Profiles, Open Viking, or other product workflows. Call it **API Reference** and provide a separate product Guide/Help surface or contextual drawer.
3. **The proven command palette is trapped in Chat.** The existing `CommandPalette` supports fuzzy search and keyboard navigation, but current use is in `ChatPage`. Promote that interaction into a global **Go to / Do** launcher for pages, sessions, and safe actions. Show its shortcut in the shell; include recent and pinned destinations. Keep destructive or sensitive actions behind normal authorization and confirmation.
4. **Navigation semantics do not yet support a full hierarchy.** The one existing group always renders its children. Collapsed nav tooltips repeat the page label; expanded links have no short task description. For future groups, use a real disclosure button with `aria-expanded`/`aria-controls`, preserve active parent and child state, and keep destination links as links.
5. **A consistent visual language is not guaranteed by a consistent shell.** The header and color tokens are shared, but this review did not establish that all page bodies use the same pattern. Build reusable page patterns for intro, section headings, status, list/table toolbar, empty/loading/error states, contextual help, and dangerous-action review. Give color tokens semantic roles—surface, text, border, focus, success, warning, danger—and keep information understandable in every theme without relying on color alone.

### A more coherent product model

Use three levels of navigation and work:

1. **Global wayfinding:** six-ish task groups, a global Go to / Do launcher, recents/pins, and a visibly separate Extensions area. Keep the most frequent user goals available immediately; collapse specialist and administrative routes by default. Validate final group names with actual users.
2. **Section navigation:** show only the children of the active work area. Open Viking keeps its sidebar children; its pages should not repeat the same full five-route map in tabs and local cards.
3. **The active workspace:** title and one-line purpose, one primary action, a clear work surface, and a contextual help/health panel that can collapse. Keep status local to the workflow it describes and preserve the user’s selected session, filters, and draft while navigating within that area.

For a distinctive Qubit identity, make the interface’s live system state the visual signature: local vs network action, model/agent activity, queues, approvals, and data flow. Use restrained transitions to show those changes and keep stable work surfaces visually quiet. Avoid a persistent layer of decorative animation over tables, transcripts, and dense controls.

### Page instructions: what to write and where

Use a shared instruction contract on every route, short enough to scan in seconds:

- **Purpose:** “This page lets you…”
- **Use it when:** the goal or situation that should bring the user here.
- **Data and effect:** what the page reads or writes; local vs network; whether the action can be undone.
- **First action:** one verb-led recommended step and the expected result.
- **Next step:** where to continue when the task is complete.

Examples of essential route-specific details: `/chat` is the API-provider experience and `/qubit-chat` is the local-model experience; keep them as separate routes and individually visible links. Model assignment applies to new sessions, so repeat that consequence beside Save and say that already-running sessions keep their current model. Usage views should define estimated vs actual cost and disclose excluded calls/retries beside the numbers.

Place the one-sentence purpose directly under the page title. Put “Use it when” and a first action in the empty/first-run state. Put data scope and reversibility beside the affected control, before the user commits. Offer a contextual **How it works** drawer that deep-links to the relevant guide section; do not force a trip to a generic index.

Use consistent state copy:

- **Empty:** explain why there is no data and give one safe way to create or find it.
- **Loading:** say what is being loaded and preserve the current page layout to reduce visual shift.
- **Error:** name what failed, whether the action may have partially succeeded, and the safest recovery action.
- **Success:** confirm the result and point to the next relevant step.
- **Terms:** define jargon on first use; keep the technical name available for experienced operators.

Avoid instructions that merely restate a control label. Replace “Manage provider” with what the user can do, which configuration it changes, and whether it affects new sessions or the active session. Prefer specific verbs such as **Search local context**, **Add a source**, **Pause this schedule**, or **Open bot session**.

### Merge and consolidation decisions

| Decision | Recommendation | Confidence / condition |
|---|---|---|
| Cron + Taskflows | Bring both into one **Schedules** area with views for jobs, durable flows, and blueprints; keep hooks as a distinct capability. | **High:** Taskflows filters the same `/api/cron/jobs` collection used for Cron CRUD. Keep old URLs as aliases and compare permissions/side effects before retiring either UI. |
| Scheduler | Break its unrelated controls into Schedules, Tasks, Voice, and System/Power; retain a summary page only if operators need one. | **High:** the current route mixes cron toggles, TTS settings, power controls, and task cancel/retry. |
| Task Inbox + Background Tasks | Keep separate views under **Tasks**. | **High:** they use different APIs and different record types; label “suggestions/inbox” vs “background runs.” |
| Sessions + Session Ops | Put Session Ops under Sessions as **Advanced operations**; keep actions separate. | **High:** shared session context, distinct workflows and effects. Make destructive/intervention actions explicit. |
| Chat + Qubit Chat | **Keep separate:** preserve two routes and two individually visible navigation links. Add purpose descriptions to make their different experiences obvious. | **High:** these are distinct experiences. The user's API-provider/local-model distinction should be visible in each description. |
| Agent Workspace + Computer | Group under **Agent tools**; consider one workspace with tabs only after a control/access comparison. | **Medium:** both show workspace files; Computer also owns console/desktop operations. |
| Open Viking Resources | Move its route map and introductory explanations into the section home and relevant pages; retain Resources only for substantial unique reference material or a true resource task. | **High:** current page mostly explains/links to the other four routes. Preserve a redirect if removed. |
| Skills / Skills Review / Agent Hub skills | Group and state scope; do not merge yet. | **Low for merge:** different catalogs/review permissions may be involved. Compare backing records and mutation actions first. |
| MCP / MCP Calls | Group as **Connections** with setup and activity views; do not merge until data ownership is checked. | **Medium:** labels imply setup vs activity, but a route/API comparison is still needed. |
| Users / personal setup | Move theme/font preferences and model account links to **Appearance & Accounts** settings; keep the directory in **Administration**. | **High:** the current Users surface mixes personal preferences, account/provider configuration, and user administration. |
| Analytics + Models usage | Make Analytics the canonical **Usage** dashboard for shared totals, trends, and per-model usage; keep Models focused on provider/model configuration, assignments, and catalog/status. | **Medium:** both pages expose overlapping token/cost/session/call summaries and per-model breakdowns, but use different response shapes. Align metric definitions, exclusions, and sources before moving reporting. |
| Analytics / Reports | Consolidate overlapping usage/activity summaries into Analytics; keep report generation and insights only where they provide a distinct workflow, perhaps as an Analytics subview. | **Medium:** the Reports plugin independently shows usage totals and recent activity alongside generated reports/insights. Compare permissions, data definitions, and actions before moving or removing its unique capabilities. |

For each proposed merge, confirm five things before changing routes: **same user goal, same underlying records, same permissions, same side effects, and same recovery path**. If one differs, expose the relationship through a shared section and cross-links instead of forcing the workflows into one page.

### Highest-value UI improvements, in order

1. **Remove structural overload:** create consistent collapsible task groups, separate extensions/admin tools, and add the global launcher.
2. **Unify jobs and remove mixed controls:** one Schedules surface, a clear Tasks area, and a purpose-led home for each action family.
3. **Standardize page orientation and help:** shared purpose line, scope/effect, first action, and contextual guide entry on every page.
4. **Preserve context:** global and section navigation should retain drafts, selected session, filters, and scroll position where safe; show unsaved state before a route change.
5. **Make state legible:** consistent status/error/empty/success patterns; show local/network and read/write effects at the point of action.
6. **Polish accessibility and themes:** keyboard-only route/group operation, clear focus and active states, screen-reader labels, 200% zoom, reduced motion, long translations, and contrast checked for every theme.
7. **Use animation as feedback:** brief, interruptible transitions for navigation and operation state; avoid perpetual movement in work areas and honor reduced motion.

### Further evidence references

- Shared shell/header slots: `frontend/qubit-web/src/contexts/PageHeaderProvider.tsx` and `page-header-context.ts`.
- API reference behavior: `frontend/qubit-web/src/pages/DocsPage.tsx`.
- Existing chat-only command launcher: `frontend/qubit-web/src/components/CommandPalette.tsx` and `frontend/qubit-web/src/pages/ChatPage.tsx`.
- Navigation semantics and collapsed tooltip behavior: `frontend/qubit-web/src/App.tsx` (`SidebarNavLink`, `SidebarNavGroup`).

This additional critique combines source/API review and representative browser evidence. Keyboard, zoom, screen-reader, theme-contrast, reduced-motion, and all-route task testing are recommendations for a later implementation review; they were not performed here.

---

# CORRECTION — appended 2026-10-12 (audit was written without session context)

**Status of this appendix:** the body above is preserved unedited as a point-in-time audit. It was produced by a reviewer with **no knowledge of concurrent work in the same session**, so several structural statements in it describe a tree state that had already changed. This appendix records what is now stale, what remains valid, and the verified current state. Nothing above was rewritten.

## Part 1 — Statements in the body that are now OBSOLETE

| Body says | Actual state |
|---|---|
| Open Viking is "a real parent with four indented child routes" (Retrieval / Uploads / Agent Hub / Resources) | **Replaced.** `/open-viking` now renders a NEW page, `OpenVikingStudioPage.tsx`, which embeds the upstream OpenViking Studio (the Vite SPA at `http://127.0.0.1:1933/studio`) in an iframe. The four old sub-pages were **unlisted from the sidebar** but remain routed at their original URLs. |
| "Keep the global sidebar structure as Open Viking → Retrieval, Uploads, Agent Hub, Resources" (§ Recommendation for Open Viking) | **Inverted by a later decision.** That structure was deliberately superseded: the studio embed gives the real upstream UI (Workspace / Activity / Settings / Resources, ~18 routes) instead of a reimplementation of it. |
| "45 top-level built-in links" | Counts differ by definition. A top-level-only count of `BUILTIN_NAV_REST` gives **~35 entries**; the higher figure includes plugin destinations and indented children. Neither is wrong; state the definition when citing it. |
| Open Viking children are "the only explicit group" | A second group now exists: the task/scheduler family (`/qubit-taskflows`, `/qubit-tasks`, `/qubit-task-inbox`, `/qubit-board-admin`) is nested under a parent labelled **Tasks & Scheduling** at `/qubit-scheduler`. |
| Navigation labels cited as current (`Talk`, `Qubit Memory Backends`, `Qubit WebChat`) | **Renamed.** `Talk` → **Voice & Speech** (it is the voice console, not a chat surface — see Part 3), `Qubit Memory Backends` → **Memory Providers**, `Qubit WebChat` → **WebChat**. A **Context Store** entry was added for the local context-engine console (a *different* backend from the `:1933` server — see Part 4). |
| Reports `docs/README.md` and `docs/STATUS.md` absent | Correct at review time; those two files are genuinely absent from this tree. |

## Part 2 — Findings that remain VALID and independently useful

These do not depend on the changed route structure and were confirmed by separate measurement in the same session:

1. **Cron and Taskflows share one jobs collection.** `TaskflowsPage` reads `/api/cron/jobs` via `ocFamilyFlowsApi.listFlows()`, the same collection `CronPage` performs CRUD against. This is a **true duplicate surface** and the single strongest consolidation case in this audit.
2. **The Scheduler page is a grab-bag.** It mixes cron enable/disable, TTS provider/profile/persona settings, host suspend/resume power controls, and task cancel/retry. Re-homing each control with its owning workflow is correct.
3. **`DocsPage` is an OpenAPI browser, not product documentation.** The rename to **API Reference** is accurate.
4. **`UsersAdminPage` mixes audiences** — personal appearance preferences, OAuth/model account links, and the operator roster in one surface.
5. **Children render always-expanded.** Independently verified: `SidebarNavGroup` (App.tsx ~L1164-1198) renders `item.children` inline with no disclosure control. This has a consequence the body under-states: **grouping does not reduce visible row count.** Only relabelling, unlinking, or adding real collapse changes what a user scans.
6. **No dead pages.** Every OpenViking/context page audited here wires to a live backend. The suspected-useless candidates were each verified load-bearing (see Part 3).

## Part 3 — Pages verified load-bearing (do not delete)

An operator request to "delete the useless pages" was tested against each candidate before acting. All three were load-bearing:

| Page | Why it must stay |
|---|---|
| `/qubit-talk` (**now labelled Voice & Speech**) | The **only UI for the voice stack** — talk mode, speech catalog/config, wake-word status (read-only), local TTS. Deleting it removes the operator's only surface for mic/TTS/wake settings. |
| `/webchat` | Target of the `/webchat` slash command **and three voice commands** (`webchat_list` / `webchat_show` / `webchat_resume`). Deleting the page breaks that voice phrase path and ~6 test files. |
| The five `OpenViking*Page`s | The **only UI for Qubit's local context engine** (`voicebridge/context_store.py`, `connection: "local"`) — see Part 4. |

## Part 4 — Two distinct backends that must not be conflated in labels

A recurring source of confusion, worth stating explicitly because the audit body treats "Open Viking" as one thing:

- **Qubit's local context engine** — `voicebridge/context_store.py`. Served by the `OpenViking*Page`s under `/api/qubit/viking/*` with `connection: "local"`. Holds MEMORY.md / USER.md / skills / memories. **No server required.**
- **The OpenViking server** — a systemd user unit (`openviking.service`) on `127.0.0.1:1933`, now also wired as Qubit's memory provider (`memory.provider: openviking`). Its own studio SPA is what `/open-viking` embeds.

`/open-viking/context-store` was added so the local-engine console keeps a labelled sidebar home instead of being lost when `/open-viking` was repointed. Any future IA pass should preserve that distinction.

## Part 5 — Verified environment state at time of this appendix

- `openviking.service`: active + enabled on `127.0.0.1:1933`; `/health` returns `{"status":"ok","healthy":true,"version":"0.5.0","auth_mode":"dev"}`.
- Its VLM was changed from `qwen3-vl:4b` to **`qwen3-vl:2b`** in `~/.openviking/ov.conf`. Reason, measured: on an 8 GB RTX 3070 Ti the 4b loaded **30% CPU / 70% GPU** with 469 MiB free VRAM and timed out at **600 s**; the 2b loads **100% GPU** with ~2.8 GB free. Provider prefetch latency dropped from **4.0 s to 0.00 s**.
- **OpenViking's store is still functionally empty** — `viking://user/default/memories` holds only `identity.md` (blank template) and `soul.md` (stock persona). Prefetch returning 0 characters is *correct given an empty store*, not a fault. Populating it requires the session-commit → server-side-extraction path, which had not yet been proven end-to-end when this appendix was written.
- **Obsidian plugin works; "2 nodes" is correct.** `/agent/api/plugins/obsidian-memory/{vaults,notes,tags,graph}` all answer 200. The graph reports exactly the vault's real contents: two sample notes (`Welcome.md`, `Second Note.md`) created during setup, one edge. The graph will not grow until real notes are written into `~/Documents/ObsidianVault` (or `OBSIDIAN_VAULT_PATH` is repointed at an existing vault). This is expected behaviour, not a defect.
- **Codebase knowledge graph (graphify)**: `graphify-out/graph.json` is 216 MB / **139,459 nodes / 386,330 edges / 1,769 communities**. A query for "how does the gateway route a message to a platform adapter" returned **production** files (`qubit_runtime/gateway/run.py`, `platforms/base.py`, `plugins/platforms/*/adapter.py`) — 31 of 65 nodes in the gateway package, 18 in platform adapters, 1 test-path node. Semantic enrichment remains incomplete (reasoning-token truncation on the build model), so the graph is AST-complete with partial semantic coverage.

## Part 6 — Tooling installed during the same session (for completeness)

`graphify` (PyPI `graphifyy[all]`), `openviking` (+ `openviking-server`, `ov`, `vikingbot`), and the `obsidian-memory` dashboard plugin. `yt-dlp` predates this work. `sandbox/` was left untouched throughout, as required.

## Part 7 — What a future IA pass should do with this document

Take **Part 2** forward as the live findings list, and re-anchor Part 1's obsolete items against the current `App.tsx`. The consolidation plan that should actually be executed is the one produced against the live tree, not the grouping tables in the body above — those were drafted against a route structure that no longer exists. Every merge proposal in the body that is not in Part 2 was already self-rated Low/Medium confidence by its own author and remains unvalidated.
