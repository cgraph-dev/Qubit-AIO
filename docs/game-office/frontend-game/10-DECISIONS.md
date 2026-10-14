# Decisions and amendment boundary

## Owner-directed scope (in force for this work)

| ID | Directive | Source |
|---|---|---|
| O01 | Frontend only; owner handles backend | Original recovered request |
| O02 | Cohesive full-game UI, Japanese/anime ARPG and gacha-style panels/settings/backgrounds | Current request and additions |
| O03 | Original characters; Bleach/Smite are style references, not franchise cast | Original request |
| O04 | Enter and exit buildings; switch floors; enter rooms | Current explicit addition |
| O05 | Single Sol 6.1 worker, sequential, no delegated agents | Current session instruction |
| O06 | Produce audit and executable plan, not start the redesign in this turn | Current planning request |

English remains product/doc language; Japanese is the visual/art direction, not a translation-pack request.

## Proposed implementation rulings

These are concrete recommendations, not falsely stamped owner-approved. A builder must not silently substitute an alternative. Deviations require an explicit decision record.

| ID | Recommendation | Alternative it beats and why |
|---|---|---|
| P01 | One game at existing `/qubit-office`, same auth and domain stores | Separate app/embedded dashboards: creates competing auth, history and layouts |
| P02 | Japanese-anime 2.5D isometric headquarters/district; Phaser 3 world + React UI | Full 3D first: much higher art/rig/camera cost; Canvas patch-only: retains hand-rolled game lifecycle/input burden |
| P03 | Rebuild scene/HUD composition, retain verified geometry/tint/depth/math via adapters | Delete everything: discards working truth and causes backend scope creep |
| P04 | Keep plugin name/route; compile typed frontend into one registered entry using host React | Native duplicate route: conflicts with fleet/route precedence; own React bundle: split hook runtime |
| P05 | Separate domain truth from navigation and presentation | Single “no motion without events” blanket ban: prohibits normal camera/menus/idle artwork |
| P06 | Real agent work states and persistent locations require backend evidence; camera, owner-viewpoint travel and ambient scenery are presentation-only | Client-generated work/physical agent relocations: fabricates domain state |
| P07 | Character interaction uses canonical session/event/action services through a typed adapter | Office-specific chat database/agent loop: splits history and bypasses existing policies |
| P08 | One primary panel plus one contextual drawer; settings/critical confirmation are exclusive modals | Unlimited draggable windows: reproduces current scattered dashboard |
| P09 | Gacha polish without paid random pulls, fake rarity, loyalty bars or fake affection | Adding gambling/progression as implied business truth: not requested, not evidenced |
| P10 | Local view settings persist separately from org/capability settings | Editing a room decoration silently changes tool grants: dangerous ambiguous UI |
| P11 | One snapshot bootstrap + existing event transport where available; serialized adaptive polling fallback | 400ms parallel full snapshots and a new independent event bus: expensive and stale-prone |
| P12 | Exterior↔interior and elevator/stair travel are real navigation state, with deep links/history | Floor buttons only: does not satisfy entering buildings or spatial travel |
| P13 | Original consistent anime portraits + matching chibi/isometric sprites and cohesive office tiles | Mix franchise sprites, medieval buildings and corporate desks: reads as asset collage |
| P14 | Build a small end-to-end navigation/interaction slice before a huge asset production batch | Paint every room first: expensive if the camera/interaction model is wrong |
| P15 | Unavailable backend actions are explicitly unavailable in live mode; recorded preview mode is labelled | Fake successful chats, approvals, room changes or hires: misrepresents integration |
| P16 | No backend rewrites, service restarts, inference runs or automatic worker activation during frontend development | Turning on autowork to make the scene move: spends money and changes scope |

## Renderer comparison

- **Keep Canvas2D:** no dependency migration and useful baseline math, but game input, scenes, camera, loading, hit tests and animation are maintained manually. Suitable for a limited repair, not recommended for the whole brief.
- **PixiJS + React:** strong rendering/viewport reference from AI Town; still needs authored game-scene/input/lifecycle behavior. Good alternative if measured Phaser packaging fails.
- **Phaser 3 + host React (recommended):** scene lifecycle, camera, input, sprites/animation, asset pipeline and browser fallback; aligns with the existing D11 intent while acknowledging the baseline is NOT Phaser. Isometric layout and multi-floor portals remain Qubit-owned, not features assumed to come free from the engine.

Phaser is **not declared in the current web manifest**. Select/pin the version after a small compatibility/packaging spike; do not guess a dependency version or install it under this planning permission. Agent Quest declares Phaser 4; do not quietly copy its APIs into a Phaser 3 build.

> **RESOLVED 2026-10-14 — owner ruling: `phaser@4.2.1`, pinned exactly.**
> The spike was completed first (F04), the facts were put to the owner, and the owner ruled. P02's
> "Phaser 3" is superseded for the same reason it named 3 originally — the plan picked the current
> stable and the stable has moved (Phaser 4.2.1 is npm `latest`; 3.90.0 is the previous major).
> Licence MIT (Richard Davey / Phaser Studio), source unmodified, no Phaser code copied in.
> Rationale, provenance and the **measured +1.25 MB gzip cost** are in
> `frontend-game/evidence/F04-ENGINE-DECISION.md`. `CanvasSceneHost` is retained as a working
> fallback behind the `SceneHost` factory seam, so reversing this is one argument.

## Amendment required before builder execution

Append a frontend amendment to the ordinary living decision log. Do not edit frozen contracts or `docs/differential/INTERFACE.md`.

1. Reaffirm C4: **no direct org writes**; staged requests through the existing operation/confirmation authority are permitted.
2. Clarify C7: no fabricated work, unverified success, fake progress, money or agent location. Render-clock interpolation may only depict a bounded authoritative route/event; cosmetic scenery and view transitions must never carry work meaning.
3. Explicitly permit a request-loading indicator while a real request is pending, labelled “waiting for response”, not “task progress”.
4. Clarify local **viewpoint** movement versus real agent relocation; all real relocations require an accepted action/updated snapshot.
5. Replace blanket source-string/timer bans with tests proving the behavioral boundaries.
6. Supersede the seven-agent merge execution partition for THIS frontend programme: one sequential worker; no inherited autonomous run/spend permission.

Old documents remain history until this amendment is approved and appended. This new spec does not authorize builders to bypass a test by deleting it.

## Decisions deferred without blocking the first visual slice

Exact cast count, paid generation tools, soundtrack commission, full-3D upgrade and public publication rights can be decided after the first slice. No paid tools are used here. Multi-building live integration requires the owner's building/location contract; the existing single tower can still be used as a clearly derived headquarters preview.
