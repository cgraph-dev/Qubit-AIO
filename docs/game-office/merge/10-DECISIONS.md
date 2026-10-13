# 10 — Decisions

**M0–M14 are all `OWNER-APPROVED` as of 2026-10-13.** Implementers may propose against a
ruling but may not quietly change one.

Each decision records the **alternative it beat**. A decision with no recorded alternative
gets relitigated by the next reader; a decision with one does not.

---

## Structural rulings

### M0 — `aphae` is dropped from `/refs` and from the merge
**Status: OWNER-APPROVED 2026-10-13.**
**Beat:** keep it read-only as a reference for the director pattern.

`aphae` ships no license file, verified against every upstream path and casing
(`rsanandres/aphae/HEAD/LICENSE`, `LICENSE.md`, `LICENSE.txt`, `COPYING` — all HTTP 404). No
license means all rights reserved, so its GDScript and assets cannot be copied, adapted, or
transliterated. The owner ruled it out and the folder is deleted.

*Consequence:* the director pattern (a component that decides what is noteworthy and
surfaces it) remains available as an **idea only** — `65-REFERENCES.md` already applies it to
the CEO weekly review without copying code. That stays true.

### M1 — Merge target is the existing `/qubit-office` page, not a new app
**Status: OWNER-APPROVED 2026-10-13 (`2a`).**
**Beat:** a new standalone app built from the sources.

`55-DASHBOARD-PAGE.md` already establishes why: a separate app needs its own auth, store,
event stream, and confirmation path — four surfaces that must agree with the existing ones,
which is this repo's most expensive recurring bug class. A tab cannot drift from the bridge it
is drawn in.

### M2 — Scope is the full build: assets + logic + UI in one programme
**Status: OWNER-APPROVED 2026-10-13 (`3d`).**
**Beat:** assets-first as a separate deliverable, then logic later.

The owner chose the whole thing. The milestones in `70-MILESTONES.md` still order it —
assets land before logic because the asset pipeline is the prerequisite for every visual
proof — but both are in scope now, not deferred.

---

## License and provenance rulings

### M3 — Assets and code from the five sources are copied freely into the merge
**Status: OWNER-APPROVED 2026-10-13.**
**Beat:** treating the sources as read-only technique references.

This is a **personal, private, local project** — the copies in `/refs` never leave the
machine and the merged result is not published. The owner has ruled that no license or notice
file is authored, and none is. (If this ever becomes a published product, the position would
need revisiting then, and only then.)

### M4 — No attribution files are authored for the merge
**Status: OWNER-APPROVED 2026-10-13.**
**Beat:** a `THIRD_PARTY_NOTICES` file and per-file provenance headers.

Same ruling as M3. The merge takes art and design laws; it does not carry a notice layer.

### M5 — Assets are copied into the plugin directory, not referenced from `/refs`
**Status: OWNER-APPROVED 2026-10-13.**
**Beat:** importing or serving assets straight out of `/refs`.

This is a **technical** ruling, not a legal one. Assets are copied into `assets/` under the
plugin and served from there (`40-ASSET-PIPELINE.md`), because a served path that reaches
outside the plugin's own directory fails the path-confinement check in
`resolve_plugin_asset` (`qubit_compat.py:10965`) — `PLUGIN_PATH_BLOCKED` — and because the
running product should not depend on a scratch folder the owner may move.

---

## Asset rulings

### M6 — Agent figures become baked pixel sprites from starnet; the procedural rectangle stack is retired
**Status: OWNER-APPROVED 2026-10-13.**
**Beat:** keeping the code-drawn figures and adding art only as furniture.

`sprites.js` already documents that four rectangles "is not a sprite; it is a rectangle stack"
— at 15 units tall the head merges into the torso and two figures on neighbouring desks smear
into one. Starnet supplies 50 crew sets with real 8-directional rotation, walk cycles, and
sit/type poses. This is the single change that most improves the page, because the measured
flaw is that the canvas is 73% empty and the figures that *are* there are unreadable.

### M7 — Room floors and walls use agent-quest CC0 Tiny Swords tiles
**Status: OWNER-APPROVED 2026-10-13.**
**Beat:** keeping the flat-shaded diamond decks.

The 207 CC0 files include Terrain (Ground/Water/Bridge), BuildingsCustom (8 building
exteriors), and a UI kit (Buttons/Icons/Pointers/Ribbons/Banners). Ground tiles give the deck
a surface; the building set gives each floor's rooms a read-at-a-glance identity.

### M8 — Furniture uses starnet's `furniture/` PNGs (48 files)
**Status: OWNER-APPROVED 2026-10-13.**
**Beat:** drawing desks procedurally.

The desks are currently vector rectangles. Real furniture PNGs are already 2.5D-correct for a
2:1 iso grid and were drawn for exactly this camera.

### M9 — Sound effects ship, and require one server line so `.wav` can be served
**Status: OWNER-APPROVED 2026-10-13.**
**Beat:** shipping sound now and discovering the 404s at runtime.

`_PLUGIN_ASSET_CONTENT_TYPES` (`qubit_runtime/qubit_cli/web_routers/dashboard_ui.py:379`)
allows `.png .jpg .jpeg .gif .webp .ico .woff2 .woff .ttf .otf .js .mjs .css .json .html .svg .map`
— **audio is absent**. Serving the 12 UI sounds requires adding `".wav": "audio/wav"` to that
dict (one line, in a vendored file — see `60-WIRING-TRAPS.md` §2). This one line is in scope:
the owner approved adding it.

### M10 — The VT323 pixel font is used for in-canvas labels only
**Status: OWNER-APPROVED 2026-10-13.**
**Beat:** applying it to the whole dashboard.

The plugin's chrome belongs to the dashboard's cyberpunk theme; a pixel font there would fight
it.

---

## Logic rulings

### M11 — starnet's "the layout *is* runtime state" law is adopted as the tower's law
**Status: OWNER-APPROVED 2026-10-13.**
**Beat:** treating the floorplan as decoration over an independent state.

The Office already holds a weaker form of this (C12: "the floor is the org chart", a room's
objects are its capabilities). Starnet states the stronger, testable version: a placed object
**is** a capability grant, a hallway is a handoff lane. Adopting it means `room_layout.py`'s
programme stops being inert (see `43-OPEN-ITEMS.md` A1) and becomes the grant source.

### M12 — pixel-agents' single-array z-sort is confirmed as the occlusion law (already implemented)
**Status: OWNER-APPROVED 2026-10-13.**
**Beat:** a separate depth pass per object kind.

`paintOrder()`/`sortByDepth()` already exist and already wire into the renderer. This ruling
records that the merge *keeps* that law rather than replacing it with the sprite layer — a
sprite must sort by the same tile-bottom key, not by draw order. This is the one place where
the merge risks regressing a working fix, so `26-OCCLUSION-AND-DEPTH.md` is authoritative.

### M13 — agent-monitor's boss chat maps onto the existing escalation/journal surface
**Status: OWNER-APPROVED 2026-10-13.**
**Beat:** porting agent-monitor's chat implementation.

The Office already has an escalation path on the shared confirmation journal
(`voicebridge/office/escalation.py`). Boss chat = *owner → agent* direction of the same
channel; it must reuse that surface, not add a second messaging path. Rule: one authority per
concept (repo convention).

### M14 — ai-town's "world pauses when nothing is watching" is honoured as C7, not reimplemented
**Status: OWNER-APPROVED 2026-10-13.**
**Beat:** adding a background simulation loop.

C7 ("no event, no animation") is the same rule and is already enforced server-side in
`tower.py` as a pure function of `(rows, clock)`. The merge adds nothing here; it cites C7.

---

## Rejected alternatives (recorded so they are not quietly reintroduced)

| rejected | why |
|---|---|
| Building a new standalone app from the sources | M1 — four new split-brain surfaces |
| Using `aphae`'s GDScript or assets | M0 — no license exists anywhere upstream |
| Serving assets directly from `/refs` | M5 — fails path confinement, couples product to scratch |
| Replacing the z-sort with sprite draw order | M12 — would regress a proven fix |
| Porting agent-monitor's chat as a second channel | M13 — breaks one-authority-per-concept |
| Adding a background sim loop "because ai-town has one" | M14 — C7 forbids it |
