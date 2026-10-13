# 80 — Risks

R1–R9. Each names what it is, why it matters, and the mitigation. The **thin defence** is
called out explicitly (skill rule: name it, do not quietly delete it).

---

## R1 — The merge regresses the render while adding art — **the thin defence**
**What:** replacing vector figures/tiles with real PNGs touches the same code path that draws
every frame; a mistake can break the render or gut the frame budget, and the asset work is the
largest single change in the programme.
**Why it matters:** the tower works today (885 office tests pass, live render verified). The
merge's whole risk is trading a working render for a prettier broken one.
**Mitigation:** M1 checkpoints the working state in git *before* any asset lands, so a bad
merge is revertible; the four seams in `50-ARCHITECTURE.md` §5.2 pin the payload, the depth
key, the animation states, and the event stream; the render probe
(`scripts/render_office_screenshot.py`) measures real pixels after each asset milestone.
**This is the plan's thinnest defence** — it relies on the seams holding, and on the
screenshot probe catching a visual regression. If it does not, the render silently worsens.

## R2 — `aphae` is genuinely gone and its best ideas go with it
**What:** the director pattern and needs/drama systems were the most interesting *logic* in the
set, and they are unusable.
**Why it matters:** the Office loses a whole subsystem's worth of design.
**Mitigation:** ideas are usable without code. `65-REFERENCES.md` already applies the director
pattern to the CEO weekly review with no copying. If the owner wants the code, the only route is
to ask `rsanandres` for a grant — recorded as an open item, not designed around.

## R3 — The merge regresses a working fix (the z-sort)
**What:** swapping vector figures for sprites changes how a figure is drawn; if the sprite
sorts by draw order instead of the tile-bottom key, occlusion regresses.
**Why it matters:** the current `paintOrder()`/`sortByDepth()` is proven (72 tests pass); losing
it silently undoes `26-OCCLUSION-AND-DEPTH.md`.
**Mitigation:** M12 makes the depth key a **seam** (§5.2): the sprite keeps the *same* sort key
the vector figure used. A test asserts a sprite and a vector figure at the same `(x,y,z)` sort
identically.

## R4 — The false "everything is merged" narrative
**What:** the word "merge" invites the belief that the six repos became one program. They did
not — five became art + laws inside one plugin.
**Why it matters:** a future reader will look for a Convex backend or a Godot scene and not find
one, and conclude the work failed.
**Mitigation:** `00-INDEX.md` §"What the merge is" states the precise meaning first. The
inventory shows **0 verbatim source files copied** — the honesty is the content.

## R5 — Asset bloat
**What:** starnet carries 39,869 PNGs; naively vendoring "the sprites folder" could pull
gigabytes into a plugin that is currently 188 KB.
**Why it matters:** a served dashboard that ships 100 MB of unused art is slower and harder to
review.
**Mitigation:** the vendoring script is **allowlist-driven** (only the paths in
`30-SOURCE-INVENTORY.md`), prints the real total, and R7 gates on a size threshold the owner
sets.

## R6 — The docs drift again
**What:** the existing `docs/game-office/` set already contains fixed-but-marked-OPEN defects
and wrong counts (`README.md` says 16 modules; there are 26). The merge adds ~8 more docs that
can rot the same way.
**Why it matters:** doc drift is the failure the previous session's evidence showed most
clearly.
**Mitigation:** `tests/test_office_merge_doc_consistency.py` pins the vendored asset manifest to
the assets the renderer actually loads. Drift fails a test rather than misleads a reader.

## R7 — Asset size cap
**What:** the vendored asset tree could grow large; `40-ASSET-PIPELINE.md` §5 does not invent
byte sizes ahead of measurement.
**Why it matters:** a served dashboard shipping unnecessary megabytes is slower to load and
harder to review.
**Mitigation:** **owner-set cap = 100 MB.** The vendoring script prints the real total; if it
exceeds 100 MB the pipeline drops, in order: `sfx`, then unused UI frames. Decided 2026-10-13
(raised from 25 MB to 100 MB by the owner: "first and let's see what happens").

## R8 — Restart discipline
**What:** the running dashboard is a stale-by-design Python process (`voicebridge run`, started
2026-10-12 19:38); Python handler changes need a restart, JS/asset changes do not.
**Why it matters:** an implementer who edits a handler and reloads the page sees no change and
concludes the change did not work.
**Mitigation:** Trap 7 in `60-WIRING-TRAPS.md`; `scripts/check_stale_daemons.py` confirms
consistency after each restart.

## R9 — Building on an untracked tree
**What:** every Office file is `??`; there is no history, so there is no rollback.
**Why it matters:** the first checkpoint commit is the only thing standing between a bad merge
and an unrecoverable state.
**Mitigation:** M1 task 1 is that commit, **before** any borrowed bytes land.

---

## Non-goals (explicitly not done)

| not done | why |
|---|---|
| A standalone app | M1 — four new split-brain surfaces |
| `aphae`'s code | no license exists anywhere upstream |
| A second messaging channel for boss chat | M13 — one authority per concept |
| A background simulation loop | M14 — C7 forbids motion without an event |
| Serving assets from `/refs` | M5 — path confinement + product/scratch coupling |
| Shipping sound before resolving `.wav` serving | Trap 2 — would 404 silently |
