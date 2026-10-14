# Game experience and interaction specification

## The experience

Open `/qubit-office` into a coherent world, not a dashboard of boxes. The owner sees a headquarters district, enters a building, moves through lobby/elevators/rooms, approaches characters and opens purpose-built game panels. Every working agent, request and business readout still resolves to existing domain records.

### World hierarchy

`district → building exterior → entrance portal → lobby → elevator/stair portal → floor → room → character/workstation`

- The initial live data source is one tower. Its floors are authoritative; no fake extra departments or personnel are added.
- Headquarters exterior/lobby are presentation spaces around the tower, visibly distinct from numbered org floors. Do not conflate the lobby with the intake pipeline's “Floor 0”.
- Additional office buildings/annexes appear as live only when the backend supplies building IDs and membership. A visually authored building may appear in **labelled design preview**, not as a fabricated live business.
- Suggested future district places: headquarters, business studios, R&D annex, archive/knowledge center and services courtyard. These are art/navigation proposals, not existing backend entities.
- Active floor defaults to the last valid visited floor; roster/request links override it to their target. Unknown/deleted floor returns to the lobby with a named explanation.

## Enter/exit buildings — complete journey

1. Outside: click/tap a building or move the viewpoint to its entrance. A name card shows the building's real label and available summary, with an **Enter** action.
2. Walk/camera to door: show a short cosmetic transition. It must not move a real agent or dispatch a worker.
3. Entrance: preload the lobby/floor assets; retain the exterior if loading fails. Show retry/cancel, not a blank world.
4. Inside lobby: reception, directory, entrance behind the viewpoint and elevator/stairs ahead. The directory offers direct floor access for accessibility.
5. Exit: approach the door and choose **Exit building**, or select it from the location menu. Return to the same exterior entrance/camera, not the default world origin.
6. Browser Back follows visited view locations. Closing a panel is NOT exiting a building. Reload restores a valid location from route + local view preference.

A building footprint and its entrance must be selectable by mouse, keyboard and touch. Decorative facades must not impersonate live org buildings; inaccessible buildings name why they cannot be entered.

## Switch floors — spatial and accessible

- Elevator: approach/click, open a floor-selection panel with number, name, department summary and urgent-request markers sourced from data.
- Confirm a destination: leave the current room context, perform a short transition and arrive at that floor's landing. The camera faces into the floor with a clear way back.
- Stairs: same navigation contract using an authored connector; adjacent-floor shortcut may not invent a floor that does not exist.
- Fast travel: map/directory/roster/request **Go to** uses the same state transition, so click, voice and shortcut cannot disagree.
- A floor is a **scene context**, not a cropped slice of the same stacked diagram. Whole-building cutaway remains an optional overview map.
- Do not require watching travel animations: Skip transition/reduced motion arrives instantly; data/request state is unaffected.
- Route includes building/floor/room view IDs. Entering/leaving does not mutate floor assignments or org permissions.

## Enter rooms and interact with workstations

- Doors are portals within the floor; opening a room changes viewpoint/camera and room context. Work can still be visible through authored cutaway walls.
- Each room has a readable purpose, varied furniture, signage, silhouette and light palette. Avoid copy-pasting one bedroom/medieval hut for all department types.
- An actual workstation object opens an interaction ring: Inspect, relevant evidence/artifacts, available action, location/scope. Capability props show the real grant and affected agents.
- Decorative objects can show a label or cosmetic inspection, but no granted tools or operational “Run” action.
- Returning through the door restores the corridor location; leaving a room preserves the building/floor.

## Owner viewpoint and camera

The default is an **owner viewpoint proxy**, not a new employee/CEO record. A small original visitor avatar can embody it; hide-avatar camera mode remains available. Viewpoint position is a local preference with no ledger/account/tool identity.

Movement: WASD/arrow keys when the game context owns focus; click-to-walk on clear floor; drag/middle button to pan in inspection mode; wheel/pinch cursor-anchored zoom; F centers; M map; E interacts; Esc backs out one UI layer. Touch has tap-to-move and an optional virtual stick. Input mappings are remappable and help is visible. Never hijack arrows/WASD/Enter from text fields.

World collision/navigation for the local viewpoint uses authored floor geometry. Real agent travel is rendered only from backend locations/routes. No collision animation can change a real worker's room or supervisor.

## HUD hierarchy

| Region | Contents | Rule |
|---|---|---|
| Top-left | Qubit identity, current building/floor/room, back/exit | Compact location breadcrumb; does not consume a full dashboard header |
| Top-right | Snapshot/connection status, real money summary, urgent requests, settings | Numbers trace to ledger; unknown stays unknown |
| Right edge | Small minimap + tracked work/request objective | Collapse on mobile; markers use semantic icon + text, not color alone |
| Bottom-center | Map, Characters, Work, Boardroom, Knowledge, Build, Settings | One button per destination; selected panel visibly active |
| Lower-left | Selected character/party quick card | Stable ID; live state + available interactions |
| World-space | Names, actual request/blocked indicators and interaction prompt | Fade labels by distance/zoom; don't cover faces or doors |

Default keeps world visible. Detailed finance/activity becomes a deliberate panel, not permanent boxes consuming the scene. Emergency stop remains reachable from every context through the existing authority.

## Panel catalogue

| Surface | Purpose and layout | Data boundary |
|---|---|---|
| Characters | Filterable portrait roster; full profile with Overview / Work / Memory / Relationships / Equipment / History | Current snapshot supplies identity, role, supervisor, memory/dossier; other tabs declare missing sources |
| Conversation | Visual-novel/ARPG portrait, speaker name/role, readable text, composer, transcript/evidence drawer | Canonical session/message IDs and real stream frames; no office message database |
| Work/quests | Real assigned tasks, proposals, statuses, next action, output evidence | Not invented quest completion, rewards or task percentages |
| Boardroom | CEO report, agenda/participants, selected request, options/consequences, recommendation, artifact preview and answer | Real requests and canonical staged settlement |
| Map/directory | District/buildings/floors/rooms; route to selected target | Only real/explicit preview locations; legend distinguishes decorative art |
| Department | Business summary, staff, budget/runway, goals, artifacts and issues | Real folds; no fake department health score |
| Knowledge/archive | Memories, notes, source links and tools/skills dossier | Existing memory/source authorities; unknown is visible |
| Equipment/build | Catalog/ghost/inspector/stage summary | Decoration versus capability grant clearly separated |
| Activity/mail | Filtered real events and received messages; mark/read/navigation | No fake NPC inbox, synthesized achievements or unread counts |
| Settings | Full settings box described below | Local presentation versus scoped backend preferences |

One primary panel and optionally one contextual drawer. Settings and confirmations occupy one modal at a time. Preserve world state/camera underneath; trap focus inside a modal and return it to its triggering control. Esc closes drawer→panel→interaction, with unsaved-draft protection.

## Character conversation — between owner and agents

- Approach/click/roster-select a character: highlight the target and open a quick card. **Talk** opens that character's session, not a generic chat with an undisclosed target.
- Portrait expressions are authored cosmetic variants; work/thinking indicators require a matching real run/request state. Do not label arbitrary expressions as computed emotion.
- Support free text plus backend-issued answer choices. Choice payload includes the original request ID and permitted option ID; don't generate actionable options client-side.
- Show speaker attribution, recipient, session scope and connection state. Long answers scroll; optional cosmetic typewriter has Instant text/Skip and never delays request settlement.
- “Thinking” means a real request is pending or a real active run exists. Distinguish waiting for response, receiving text, running tool, awaiting owner, stopped, failed and stale/unknown. No invented progress % or private chain-of-thought display.
- Streaming updates one message by ID; done replaces/finalizes it, never appends the full text twice. Reconnect rehydrates canonical messages and resumes cursor safely.
- Owner can open artifacts, ask a follow-up, issue a task/request through available canonical actions, or close the conversation while work continues. Closing is not cancellation.
- Cancel/interrupt is explicit and names whether it stops speech, cancels this request or stops an agent. Never turn “close dialog” into emergency stop.
- Characters may approach/ask only from actual messages/requests. Routine notices aggregate; blocking capability or mandatory review asks can produce a portrait popup with options and recommendation. No fabricated idle chatter unless clearly labelled preview script.

## Settings box — ARPG/gacha presentation with honest scope

Left vertical icon+label tabs; right aligned sections; footer Reset category / Cancel / Apply; live preview only for cosmetic local changes. Dirty dot and scope label are always visible.

| Tab | Controls |
|---|---|
| Display | Fullscreen/immersive layout, resolution scale, frame-rate cap, low/balanced/high quality, DPR cap, shadows, particles, background detail, label density |
| Audio | Master/music/ambience/UI/SFX/voice volumes, mute, output availability, push-to-talk help; optional audio requires user gesture |
| Controls | Movement, interact, back, map, camera sensitivity, pinch, remapping/conflict detection, restore defaults |
| Interface | UI scale, subtitles, instant/typewriter text, panel density, HUD layout preset, larger typography, task/request tracker |
| Notifications | Request urgency, routine aggregation, quiet hours, sound selection, unread behavior; critical safety alerts cannot be hidden as routine |
| Accessibility | Reduced motion, high contrast, color-independent state icons, readable fonts, always-visible labels, screen-reader/keyboard roster |
| Connection/session | Active profile, recipient session, model/provider display and explicit scope; reconnect diagnostic, stale-data detail |
| Privacy | Hide costs/names for screen sharing, redact transcript previews; no raw credentials ever displayed |

Backend settings (model choice, agent autonomy, tool grants, voice provider, budgets) are separated with **session/profile/org scope** and staged confirmation. Changing local graphics must never change inference/autowork. A “Sol only” setting does not silently alter other sessions.

## Suggested room/environment identities

Headquarters lobby: modern wood/stone reception, courtyard light, directory and elevator. Executive: warm gold/ivory boardroom and city window. Directors: structured navy/copper offices. Operations: blue/violet collaborative studios. Intelligence: quiet indigo archive/data room. R&D: mint/teal lab and deliberation lounge. These map only to actual floor/room roles; absent reserved floors are not manufactured live.

## Mandatory empty/failure cases

No agents: furnished authored space with clear “No assigned agents”, no phantom workforce. Missing floor/building: return to valid entry. Missing portrait: original neutral silhouette. No memory: “No recorded memories”. Unavailable chat/action: show reason and owner-owned integration requirement. Stale data: retain scene with stale age and disable unsafe actions. Asset failure: named fallback/retry. Travel failure: remain at valid previous location.
