# Japanese/anime ARPG art and UI direction

## Art thesis

**A contemporary Japanese-inspired corporate citadel above a luminous city**, inhabited by an original anime workforce. Warm wood, dark ink, ivory paper-like surfaces, brass/gold trim, violet/blue energy and disciplined editorial geometry. The exterior feels like a place; inside feels inhabited; the UI is precise and quiet until interaction needs attention.

Japanese is the aesthetic direction. Genshin and Honkai are anime/gacha UI references, not incorrectly described as Japanese-developed games. Product copy remains English; no new language packs.

### Reference vocabulary, not asset copying

- **Bleach-style presentation:** strong black/white contrast, bold diagonal framing, controlled orange/red accents, dramatic character framing. No faction symbols, franchise typography tracing, characters or exact HUD replicas.
- **Japanese ARPG hub:** world-first viewport, spatial interaction prompts, readable objectives, map/party hierarchy, fast menu/back flow.
- **Gacha character screen:** full-height portrait, concise identity/role, tabs and loadout slots; translate into real role/capability/evidence, not fictional rarity/stats.
- **Honkai/Genshin settings pattern:** clear left category navigation, structured settings rows, one modal footer, readable selected state. These are recommendations informed by retrieved UI-category/menu sources; blocked external screenshots are not claimed as visually inspected.
- **Smite-style utility:** intentional quick access, obvious selected target and compact action hierarchy, not a copy of combat bars or gods.

External reference links (reference only):
- https://interfaceingame.com/games/genshin-impact-mobile
- https://interfaceingame.com/games/honkai-star-rail
- https://genshin-impact.fandom.com/wiki/Character/Menu
- https://game8.co/games/Honkai-Star-Rail/archives/410996
- https://monstervine.com/2025/04/bleach-rebirth-of-souls-review/

## Environment kit

| Place | Visual identity | Required interaction landmarks |
|---|---|---|
| District exterior | Layered city skyline, headquarters facade, courtyard, weather/light variants | Selectable footprints, entrances, return point, path signage |
| Lobby | Warm wood/stone, modern reception, garden light, dramatic central foyer | Reception, directory, entrance, elevators, stairs |
| Executive/boardroom | Ivory/gold/charcoal, long meeting table, city view, statement wall | CEO seat, agenda display, request focus and exit |
| Directors | Ordered navy/copper, portfolio walls, glass/wood partitions | Business links, reports and review desk |
| Operations/studios | Blue/violet, modular work pods and production equipment | Workstations, artifact displays, team rooms |
| Intelligence/archive | Indigo/teal, shelves, data wall, quiet reading zones | Memory/archive terminals and source inspection |
| R&D/lab | Mint/purple, prototypes and projection tables | Idea intake, discussion, experiment evidence |
| Corridors/elevators | Repeatable trims, distinct floor plaques, consistent doors | Entry/exit anchors and directional readability |

Do not put a medieval hut inside every corporate room simply because a borrowed pack has buildings. Avoid every floor becoming a palette-swapped rectangle. Furniture and proportions must agree with the cast's feet/camera scale.

## Backgrounds and lighting

Layer skyline/far architecture/midground/near courtyard for parallax, bounded to camera motion and quality mode. Exterior time/weather can be a chosen cosmetic preset unless actual time data is used; never label ambient lighting as business state. Interior light zones communicate room purpose, not fabricated worker productivity.

Depth priorities: ground→architecture→furniture→characters→world labels→screen HUD. Fade foreground walls when they occlude the selected character/visitor; preserve floor geometry/hit tests. Overview cutaway and playable-floor view have separate framing rules.

## Original cast

Each character has one visual identity shared across portrait, mini portrait, world sprite, roster and dialogue. Role silhouette first, outfit palette second. Suggested families: executive formalwear, director coats, manager utility outfits, worker/studio attire, analyst/researcher lab layers, auditor charcoal/red accent. These are role styling, not claims about personality or competence.

Portrait expressions: neutral, speaking, considering, pleased, concerned, tired; optional cosmetic variations. An expression is not evidence of an inferred mental state. Real waiting/error/request states remain icon+text overlays.

Sprite clips: idle breath/blink, walk/run for visitor, seated idle, real-work typing, tool use, carry, thinking/waiting response, meeting, receiving message, success/error/request attention. Worker clips require real states/events; ambient idle and visitor locomotion carry no work meaning.

Full 3D rig/GLB ingestion remains an optional later upgrade behind the same appearance/animation adapter. Do not promise a portrait or GLB already exists.

## UI kit

Primitives: panels with ink/ivory surfaces, thin metallic trim, clipped-corner accent variant, restrained depth/shadow. Header typography is expressive; body text is a clear sans. Pixel fonts stay optional world signs, not long transcript/settings text.

Required families: buttons (primary/secondary/danger/icon), tabs, segmented controls, sliders with value fields, toggles, select/listbox, tooltip, dialog, drawer, card, portrait ring, role badge, resource readout, progress/unknown state, task/request item, toast, empty/error state, skeleton/loading for actual pending reads, artifact preview.

Every control defines default/hover/focus/pressed/disabled/loading/error. Settings rows align labels and values; panels share margins and footer actions. Gold trim is hierarchy, not everywhere. No emoji-shaped fallback icons mixed with unrelated pixel glyphs.

## Token proposal

Three layers: primitives→semantic→component. Values below are proposed design constants, not an existing Qubit palette.

| Primitive | Proposed value | Semantic use |
|---|---|---|
| Ink | #101523 | Game chrome/background |
| Deep violet | #252044 | Secondary surface |
| Ivory | #F0EADF | Foreground and light inset panels |
| Gold | #C6A56B | Primary emphasis/selection |
| Teal | #67C9BE | Information/connection accent |
| Vermilion | #D86458 | Danger/error, never all unknown states |
| Muted slate | #9BA4B5 | Secondary text |

Spacing base: 4 px; body text target 16 px desktop, not smaller than 14 px in ordinary mobile panels; UI targets at least 44×44 CSS px on touch. Validate contrast, including hover/disabled states, before approving final tokens.

Motion proposals: 120–180 ms hover/tab changes; 180–280 ms panel opening; 400–700 ms skippable building/floor transitions. These are targets, not measured behavior. Reduced motion removes travel/camera tween, particles and typewriter; immediate state transitions remain clear.

## Asset catalogue and intake

Proposed manifest per asset: `id`, `kind`, source/path, author/provenance/license-or-owner-permission evidence, bytes/hash, dimensions, anchor, style family, scene tags, animation frames/directions/fps, collision/occlusion metadata and fallback ID. No license assertion inferred merely from a repo name.

Store original source assets separately from exported atlases. Per-scene texture groups load at scene entry; shared UI/portraits stay cached within a bounded budget. High/low quality variants share stable IDs. Validate transparent margins and feet anchors using rendered scenes, not image edge assumptions.

Minimum vertical-slice kit (proposed deliverables): one headquarters facade/exterior, one lobby, elevator/stair/door set, one playable floor with two distinct rooms, one complete original character identity, matching portrait set, essential furniture, one UI token/component kit and optional short original/cleared UI sounds. This is a production task list, not a claim those assets have been made.

## Asset serving and reliability

Existing paths must remain valid: bundle via `/dashboard-plugins/qubit-office/`; images/fonts via `/plugins/qubit-office/assets/`. Public source survives builds; dist copies are generated. Loader reports progress for actual decoded assets, named failures and retry. A network 200 without a decoded texture does not prove render readiness.

Do not introduce remote CDN font/art dependencies or paid generation APIs under this plan. Audio unlocks on user gesture; show muted state when autoplay is unavailable. Volume/control settings are local unless an explicit backend-scope action is requested.

## What to keep/discard from local refs

Keep behavioral ideas and lawful/owner-authorized neutral assets as a temporary style scaffold if useful. Do not replace every texture merely for novelty. Replace inconsistent medieval/franchise art where it conflicts with the original anime office thesis. Old source comments and the current StarNet NOTICE disagree about artwork rights; publication needs provenance review, not a false blanket MIT claim.

## Art acceptance

A viewer can identify building entrances, floor landing/exit, room purpose, selected character and primary action without reading technical notes. No important label overlaps a face or doorway. Character portrait/world sprite match. Still frames remain legible with particles and bloom disabled. Small panels remain usable at text zoom 200% and on a 390px viewport.
