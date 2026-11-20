# Manga Dashboard UX — route and subpanel verification matrix

## Intent and reference
This pass addresses the owner's **actual generated reference screenshots**, not just a palette:
- Angled layered magenta/black/white sidebar edges and frames.
- Big readable active-star selection, violet navigation plates, bolder top and right-side panels.
- Consistent body/display type hierarchy, form labels and input alignment.
- Comic impact accents and short expressive motion without disabling Sakuga's own effects.
- Human-oriented field names and contextual help rather than API or backend terminology.

## What changed on this branch
- New `manga-rail-reference.css`: sidebar plate geometry, section edges, star sizing and collision-safe icon/label layout, reduced motion.
- New `manga-ux.css`: coherent form controls and labels across shared settings pages, public Sakuga ExtraField styling, responsive layout, and a scroll-safe Cron editor.
- `CronPage.tsx`: task / schedule / delivery sections, understandable advanced options, one consistent create/edit dialog.
- `ScheduleBuilder.tsx`: clear preview such as "Run every 30 minutes" without changing the backend schedule string.
- `PageHeaderProvider.tsx`: the responsive title and action bar. The redundant theme-only explanations were removed in the dashboard UX audit.
- `sidebar-sections.ts`: plain-English destination descriptions. No routes removed.
- Vitest coverage for schedule explanations, page guides and protected route behavior.

## Explicitly protected
- `/chat` terminal TUI.
- `/qubit-workbench`, `/qubit-hud`, `/qubit-office`, `/qubit-office2`, `/qubit-office3` and descendants.
- Electron desktop code and backend.
The route gate turns off the Manga attribute on excluded surfaces. This PR changes only `frontend/qubit-web`.

## Browser QA matrix
Use **390px, 768px, 1280px and 1600px**, plus 125% and 150% browser zoom. For each page inspect the initial view, nested views, forms, inline editing, empty/error/loading states, modals, dropdowns, tooltips, focus and keyboard navigation. Screenshots at each family/state are needed before claiming visual completion.

| Family | Example routes | Required subview checks | Verified live? |
| --- | --- | --- | --- |
| Conversations | /qubit-chat, /sessions, /webchat | right inspector, model/reasoning menus, composer, transcript, history, search | No |
| Models | /models | model lists, provider dropdowns, role assignment menus, confirmation, cost/analytics | No |
| Automation | /cron, /qubit-scheduler, /qubit-taskflows, /qubit-tasks, /qubit-task-inbox, /qubit-board-admin | create/edit, schedule preview, task details, board/cards, filters and actions | No |
| Agents | /agents, /profiles, /agent-workspace, /agent-cards | profile actions, creation, settings, files, activity, nested popovers | No |
| Files & projects | /files, /worktrees, /checkpoints, /computer, /nodes, /devices | file menus, upload, confirmation, item details, paths and long names | No |
| Skills / extensions | /skills, /plugins, /mcp, /admin/skills-review, /admin/mcp-calls | search, install, connect, editing, advanced forms and permissions | No |
| System | /system, /config, /env, /logs, /admin/users | setup hints, secrets, tables, graphs, errors and destructive confirmation | No |
| Data | /analytics | cards, charts, tables, legends, small screens | No |
| Voice / media | /qubit-talk, /voice-edit, /qubit-tv | recording controls, editors, settings, overlays | No |
| Memory & retrieval | /qubit-memory, /open-viking and its nested routes | search, context store, uploads, explorer, details, agent hub | No |
| Other tools | /qubit-bot, /qubit-harness-router, /canvas, /omniroute, /kitchen, /channels, /webhooks, /pairing | nested management screens, connection menus, settings, empty states | No |
| Learning | /guide, /docs | navigation, readable explanatory text, code examples | No |

**Note:** This is a route-family inventory, not a claim that every possible state was visually tested. All "Verified live?" entries remain No until browser evidence exists.

## Static and runtime verification
Run from the repository root:
```bash
cd frontend/qubit-web
npm ci
npm run typecheck
npm run test
npm run build
```
If the package.json scripts differ, use the project's equivalent and record the commands actually executed. Verify Cron create/edit with all scheduling modes; verify the outgoing `buildScheduleString` values remain unchanged. Verify the latest Manga CSS is loaded. Check switch to non-Manga theme, reduced-motion and all excluded routes.

## Remaining engineering work
A large dashboard with plugin-based routes requires **live inspection** and targeted fixes for each nested view. The changes on this branch improve shared primitives and one complex form, but are not sufficient proof that all dozens of pages are perfect. Resolve screenshot defects and run the full matrix before reporting 100% complete.
