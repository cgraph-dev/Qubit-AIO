# 26 — Occlusion and depth in the tower renderer

> **Status:** analysis-and-design. This file names the **one most visible defect** in the
> Office renderer and specifies its fix. The fix is **not yet applied** —
> `frontend/qubit-app/plugins/qubit-office/qubit-office.js` is owned by another worker; the
> proposed code below is for that worker to land. Nothing here changes the payload or the
> contract; it changes only the order in which `drawTower()` paints what the payload already
> reports.

**The defect, in the owner's words:** *"it sucks."* In isometric view the office reads as a
smear of overlapping rectangles: characters and desks interpenetrate, and a desk in front of a
character is sometimes drawn over the character's face.

**The cause:** the draw order is by **loop nesting**, not by **screen depth**.

---

## 1. The current draw order, quote-for-quote

`drawTower()` (`qubit-office.js:390`) paints in four fixed passes, and the pass order is
hard-wired by which loop encloses which. Nothing sorts.

```
         ┌─ pass A ── shown.slice().reverse().forEach(floor)      (line 406)
         │             └─ forEach(room)                            (line 408)
         │                  ├─ deck plate   (fill, line 420)
         │                  ├─ wall faces   (fill, lines 429–440)
         │                  ├─ room label   (fillText, line 444)
         │                  └─ forEach(seat) ── desk rects        (line 450)
         │             (then the FLOOR label, line 472)
         └─ pass B ── forEach(character)                           (line 480)
                       ├─ drawShadow()                             (line 492)
                       ├─ drawAgent()                              (line 501)
                       └─ drawMarker()                             (line 509)
```

Restated as the four rules the file actually obeys today:

1. **Floors** are drawn far-to-near: `shown.slice().reverse()` (`:406`) — but the payload's
   `floors` are ordered by `number`, so "reverse" means *highest floor number first*. That is a
   sort — of a kind — but it sorts on **floor number**, which is only equal to screen depth by
   accident of `z = number * FLOOR_HEIGHT_UNITS` (`tower.py:161`).
2. **Rooms** inside a floor: **insertion order**, the order `tower.py` laid them out
   (`i % ROOM_COLS`, `:145`). No sort. A room at `(col 2, row 0)` is nearer the camera than one
   at `(col 0, row 0)`, and the array happens to list `(0,0)` first — so the near room paints
   first and the far room paints **over** it.
3. **Desks** inside a room: `(room.seats || []).forEach` (`:450`), insertion order, **all desks
   in the room drawn together** before the loop moves to the next room — so a desk in a far room
   paints before a desk in a near room regardless of their screen depth.
4. **Characters**: *"the characters, on top of every deck"* (`:479`). **A separate pass after
   every floor and every desk.** This is the fatal one: every character is painted after every
   desk, so **an agent standing behind a desk paints over that desk**. The comment at `:479`
   states the intent honestly — it treats "characters on top" as a feature. In isometric it is a
   bug: whether a figure covers a desk is a function of *where it stands*, not of what kind of
   thing it is.

The nesting is the whole error. The renderer asks *"what kind of thing is this?"* (floor → room
→ desk → character) when the painter's algorithm requires *"where is it, on screen?"*

**Worked example of the smear.** Desk `D` at cell `(1,1)`; agent `C` at cell `(3,3)` — in front
of `D` (larger `x + y`). Under the current order, pass A paints `D`, then pass B paints `C`
*after* it — which happens to be right. Now move `C` **behind** `D`, to cell `(0,0)`: still
pass B, still after `D`, so `C` is drawn *on top of* a desk that is nearer the camera than it is.
The figure floats over the desk in front of it. That is the interpenetration.

---

## 2. How a correct isometric renderer solves it

All three reference implementations use the **painter's algorithm** on a single flattened list,
sorted by a depth scalar. None of them sorts by object type.

### 2.1 `agent-monitor` — `src/engine/canvas.ts` (MIT)

Two separate sorts, and the sort key is exactly `col + row`:

**Furniture** (`canvas.ts:182–187`):
```ts
const furniture = buildFurnitureLayout(agentCount);
// Sort by depth for correct occlusion
const sorted = [...furniture].sort((a, b) => (a.row + a.col) - (b.row + b.col));
for (const item of sorted) {
  drawFurniture(ctx, item.type, item.col, item.row, tick);
}
```

**Characters** (`canvas.ts:213–254`) — a *drawable* list carrying `depth: row + col`, sorted,
then drawn:
```ts
drawables.push({
  depth: runtime.pos.row + runtime.pos.col,
  ...
});
...
// Sort by depth
drawables.sort((a, b) => a.depth - b.depth);
for (const d of drawables) { d.draw(); ... }
```

`depth` is `row + col` — the **same quantity our `iso()` already puts in `sy`**
(`sy = (x + y) * TILE_H/2`, `qubit-office.js:256`). Agent-monitor's `gridToScreen`
(`isometric.ts:29`) is `y: MAP_OFFSET_Y + (pos.col + pos.row) * (TILE_H / 2)` — the identical
term. It sorts on that. We compute it and then throw it away.

### 2.2 `starnet` — `frontend/agent-demo/proportion-review.js` (MIT)

The canonical form, and the one that most directly answers our case, because **it puts props and
characters in the SAME list** — the thing our four passes prevent:

```js
// proportion-review.js:46–49
for(const p of props)PropSprites.drawShadow(p,p.mount);
const items=props.map(p=>({y:(p.y+p.h)*12,draw:()=>PropSprites.draw(p,false,{still:true,occupied:false})}));
for(const b of bodies){b.state=moving?'walk':'idle';items.push({y:b.py,draw:()=>SPRITES.drawBody(ctx,b,time,{reducedMotion:!moving})});}
items.sort((a,b)=>a.y-b.y).forEach(i=>i.draw());
```

Line 49 is the whole rule: **`items.sort((a, b) => a.y - b.y)`** — one list of props *and*
bodies, sorted ascending by the screen y of the **point each stands on** (a prop's `(y+h)*12` is
its bottom edge; a body's `b.py` is its **foot line**, per the feet-anchoring work in
`sprites.js:66–75`). Ascending, because smaller screen y = further from the camera = painted
first.

Starnet also carries a per-entity `sortY` in `authored-surface-mounts.js:119`:
```js
hostSortY:(host.y+host.h)*TILE, sortY:(host.y+host.h)*TILE+.5+depth*.001,
```
— the `+.5+depth*.001` is a **sub-cell tiebreak** that keeps two objects on the *same* cell in a
stable, physically-meaningful order (a mount sits just after its host). That is the same problem
our same-cell desk/character case has, and it confirms the answer: the tie must be broken on a
**rule**, not on array order.

### 2.3 Our own `iso()` already defines the key

`qubit-office.js:254–259`:
```js
function iso(x, y, z, cam) {
  var sx = (Number(x) - Number(y)) * (TILE_W / 2);
  var sy = (Number(x) + Number(y)) * (TILE_H / 2) - Number(z || 0) * DECK_Z;
  return {x: (sx - cam.cx) * cam.zoom + cam.w / 2,
          y: (sy - cam.cy) * cam.zoom + cam.h / 2};
}
```

`cam.cx`, `cam.cy` and `cam.zoom` are **the same affine transform applied to every drawable**,
so they cannot change the *order* of two objects — only their positions. Therefore the depth key
can be the cheap un-zoomed `sy`, and the sort never has to run the camera:

> **Depth key `k = (x + y) · TILE_H/2 − z · DECK_Z`**
> A **larger `k` is nearer the camera** and must be painted **later**. This is exactly
> agent-monitor's `row + col` with the floor offset and the real tile aspect folded in.

---

## 3. The correct rule for our payload

### 3.1 The sort key, stated once

A drawable's key is the screen y of the **cell it stands on** — its feet, not its sprite top:

```
k(item) = (item.gx + item.gy) * TILE_H / 2  -  item.z * DECK_Z
```

Sort **ascending** (`k` small → far → paint first; `k` large → near → paint last).

> **Why the FEET point and not the sprite's top or centre?** Because a sprite is anchored by its
> feet (`FEET_ANCHOR`, `qubit-office.js:93`; StarNet `sprites.js:66–75`). The foot line is the
> one point that is *physically on the floor*; the sprite may be 15 (`drawAgent`) or 20
> (`drawMarker`) units tall, and sorting on the top would make a tall thing sort as if it were
> further back. This is the property StarNet encodes by sorting bodies on `b.py` and props on
> their bottom edge.

### 3.2 How floors (`z`) enter it

`z` is a **negative term** in `k`. A floor whose `z` is one deck higher (`z` larger by
`FLOOR_HEIGHT_UNITS`) has a **smaller** `k`, so it sorts earlier and is painted **first**. That
is the "far floors first" rule the current code already *intends* at `:406`, and the key delivers
it as a consequence rather than as a `reverse()` of a list that was ordered by floor *number*.
Where two decks overlap on screen, the lower deck paints last and covers the upper deck's
footprint — a building seen from above-front.

`z` comes from the payload, never from the loop index: `fl.z` is
`floor.number * FLOOR_HEIGHT_UNITS` (`tower.py:161`), and the renderer already reads it
(`Number(fl.z || 0)`, `:407`). The key uses the same number the projection uses, so **the sort
and the drawing cannot disagree**.

### 3.3 How a character at a station enters it — and why a whole room must not

This is the case the owner is looking at. A character walking between two stations is at a
**fractional path position**, and the desks around it are at fixed cells. Three consequences,
and one caution about the payload:

1. **The character is its own drawable with its own `(x, y)`.** It must carry its *walking*
   point — the `p.x`/`p.y` the payload already sends (`tower.py:302–305`) — into the key. If it
   sorted by its *desk* coordinate ("at cell A, so it belongs to cell A's slot in the room")
   it would sort against the wrong desk. **It must sort against the desks around it, not against
   a whole room at once.** That is precisely what flattening buys: a walker at `(2,3)` on floor
   0 sorts between a desk at `(1,3)` and a desk at `(2,4)`, whichever rooms those desks live in.
2. **Rooms must NOT be one drawable.** If a room were a single item at its centre cell, every
   desk and every walker inside it would be stuck at the room's key. The current code is wrong
   in a subtler way: it draws *all* of a room's desks as one batch before the next room, which
   is the same mistake at desk-batch granularity. The fix flattens rooms away entirely as
   candidates for sorting — a room is only its plate and wall faces, which sort on the room's
   **far corner** (`x + y` smallest, i.e. the room's top vertex), so the plate is under
   everything standing in it.
3. **Caution — the payload rounds walks to whole cells today.** `walk_for` (`tower.py:302–303`)
   does `int(round(...))` on the interpolated point, so a mid-walk character is at an
   **integer** cell, not a fractional one, even though it carries `progress` (used only for the
   walk **bob**, `qubit-office.js:497–499`). The rule above is written for a fractional
   `(x, y)` so that it stays correct if the backend ever stops rounding — the key is a linear
   function of `x + y`, so a fractional walker sorts *between* the two cells it is between,
   which is the desired behaviour and needs no special case. **This file does not ask the
   backend to change**; it only refuses to write a renderer that breaks when it does.

### 3.4 The same-cell tie: the bug the proof caught

A desk and the character standing at it occupy **one grid cell**, so they have **identical `k`**.
A stable sort then leaves them in whatever order the list was built — and if that puts the agent
first, the *desk paints over the person sitting at it*: the exact smear, reintroduced. (This is
why the first run of the proof below **failed**; see §5, check 7.) The tie must be broken by a
rule that says what covers what:

> **On equal `k`, a character paints AFTER a desk, and a desk after a room plate.**
> Encoded as `KIND_RANK = {floor: 0, room: 1, desk: 2, agent: 3, marker: 4}` — a figure standing
> on a cell always covers the furniture in that cell.

This mirrors StarNet's sub-cell `+.5 + depth*.001` tiebreak (`authored-surface-mounts.js:119`):
same problem, same shape of answer — a rule, not array order.

---

## 4. The exact code change

**Where it goes:** `drawTower()` (`qubit-office.js:390`). Replace its body's two structural
loops (the `shown.slice().reverse().forEach(...)` at `:406` and the
`(v.characters || []).forEach(...)` at `:480`) with:

1. **build** one `items` array — every deck plate, wall face, room label, desk and character
   becomes one item with `{kind, id, key, draw}`;
2. **sort** it with `paintOrder(items)`;
3. **draw** it in order.

`drawAgent`, `drawShadow`, `drawMarker` and `iso()` are **unchanged**; they keep being called,
from a `draw` closure instead of from a `forEach`. The `probe` array the click handler consumes
(`:502`, `:752–756`) is preserved by pushing in the same place.

Two items get a small, honest note:

* **Room labels** are text, not geometry. They currently draw inside pass A (`:442–445`) and the
  floor label after it (`:472–476`). Keeping them as normal items means a character can stand in
  front of a label — which is correct for a thing painted on the floor.
* **Markers** (`:505–509`) are drawn *above the head* (`drawMarker`, `:370–388`) and must stay
  attached to their character. They become a `kind: "marker"` item whose key is the character's
  key plus a hair, so it always follows its own figure and never gets left behind by a nearer
  desk.

### 4.1 The proposed code

```js
  /* ---------------------------------------------------------------- depth
   * THE PAINTER'S ALGORITHM (fix for the isometric smear).
   *
   * Ported technique: StarNet proportion-review.js:49 —
   *   items.sort((a, b) => a.y - b.y).forEach(i => i.draw())
   * — props and bodies in ONE list, sorted ascending by the screen y of the
   * point each stands on. Re-expressed for our payload, whose projection
   * already puts `(x + y) * TILE_H/2 - z * DECK_Z` in iso().y, so the key is
   * agent-monitor canvas.ts:183/254's `row + col` with the floor term folded in.
   *
   * A larger key is NEARER the camera, so it paints LATER and covers what is
   * behind it. Nothing here sorts by KIND — kind only breaks a same-cell tie
   * (see KIND_RANK), because this line's whole defect was sorting by kind.
   */
  var KIND_RANK = {floor: 0, room: 1, desk: 2, agent: 3, marker: 4};

  function paintOrder(items) {
    return items.slice().sort(function (a, b) {
      if (a.key !== b.key) return a.key - b.key;       /* feet line: far -> near */
      var ra = KIND_RANK[a.kind] === undefined ? 9 : KIND_RANK[a.kind];
      var rb = KIND_RANK[b.kind] === undefined ? 9 : KIND_RANK[b.kind];
      if (ra !== rb) return ra - rb;                   /* a figure covers its desk */
      return String(a.id).localeCompare(String(b.id)); /* deterministic, not stable */
    });
  }

  /* The key: the screen y of the cell the thing STANDS ON — its feet. The
   * camera is an affine transform shared by every drawable, so it cannot
   * reorder them and is deliberately not applied here. */
  function depthKey(x, y, z) {
    return (Number(x) + Number(y)) * (TILE_H / 2) - Number(z || 0) * DECK_Z;
  }
```

and, in `drawTower()`, replacing the two structural loops:

```js
    var items = [];

    /* ---- decks, rooms, desks: one item each, no drawing yet ------------- */
    shown.forEach(function (fl) {
      var z = Number(fl.z || 0);
      (fl.rooms || []).forEach(function (room) {
        var x0 = Number(room.x), y0 = Number(room.y);
        var x1 = x0 + Number(room.w), y1 = y0 + Number(room.h);
        var base = deckTint(fl.role, (fl.rooms || []).length);

        /* FAR corner (x0, y0) keys the plate: the room's top vertex is the one
         * point of the room that is behind everything standing in it. */
        items.push({
          kind: "room", id: "room:" + fl.number + ":" + (room.id || room.slug),
          key: depthKey(x0, y0, z),
          draw: function () {
            drawRoomPlate(ctx, cam, room, base, z);   /* plate + 2 wall faces */
            ctx.fillStyle = "#7c8aa0";
            ctx.font = "10px ui-sans-serif, system-ui, sans-serif";
            ctx.fillText(String(room.slug || ""),
                         iso(x0, y0, z, cam).x + FACE_W * 2,
                         (iso(x0, y0, z, cam).y + iso(x1, y1, z, cam).y) / 2 + 4);
          }
        });

        (room.seats || []).forEach(function (slot, n) {
          var sx = Number(slot.x) + 0.5, sy = Number(slot.y) + 0.5;
          var occupied = slot.agent_id !== null && slot.agent_id !== undefined;
          items.push({
            kind: "desk", id: "desk:" + fl.number + ":" + (room.id || room.slug) +
                  ":" + n,
            key: depthKey(slot.x + 0.5, slot.y + 0.5, z),
            draw: function () {
              var p = iso(sx, sy, z, cam);
              ctx.save();
              ctx.globalAlpha = occupied ? 1 : 0.45;
              ctx.fillStyle = occupied ? "#3d4b60" : "#2a3443";
              ctx.fillRect(p.x - 4 * cam.zoom, p.y - 4 * cam.zoom,
                           8 * cam.zoom, 5 * cam.zoom);
              ctx.restore();
            }
          });
        });
      });

      /* the deck label stays on this deck's own near corner */
      var maxX = 0, maxY = 0;
      (fl.rooms || []).forEach(function (room) {
        maxX = Math.max(maxX, Number(room.x) + Number(room.w));
        maxY = Math.max(maxY, Number(room.y) + Number(room.h));
      });
      items.push({
        kind: "room", id: "label:" + fl.number,
        key: depthKey(maxX, maxY, z),
        draw: function () {
          var lp = iso(maxX, maxY, z, cam);
          ctx.fillStyle = "#8d9ab0";
          ctx.font = "600 12px ui-sans-serif, system-ui, sans-serif";
          ctx.fillText("FLOOR " + fl.number + " \u00b7 " + String(fl.role || ""),
                       lp.x + 4, lp.y + 13);
        }
      });
    });

    /* ---- characters: the SAME list, keyed by where they stand ----------- */
    (v.characters || []).forEach(function (cs) {
      var p = cs.position || {};
      if (p.x === null || p.x === undefined || p.y === null || p.y === undefined) {
        return;
      }
      var fl = null;
      shown.forEach(function (f) { if (f.number === p.floor) fl = f; });
      if (!fl) return;
      var z = Number(fl.z || 0);
      var px = Number(p.x), py = Number(p.y);
      var colour = seatColour(cs.slug, cs.seat_type);

      /* ONE key, from the walk point itself — so a character on its way to a
       * station sorts against the desks AROUND it, never against a room. */
      items.push({
        kind: "agent", id: "agent:" + cs.agent_id, key: depthKey(px, py, z),
        draw: function () {
          var at = iso(px, py, z, cam);
          drawShadow(ctx, at, cam);
          var pr = typeof p.progress === "number" ? p.progress : null;
          var bob = (cs.walking && pr !== null)
            ? Math.abs(Math.sin(pr * Math.PI * 4)) * 3 * cam.zoom : 0;
          drawAgent(ctx, at.x, at.y - bob, cam, colour, cs.animation, cs.facing);
          probe.agents.push({kind: "agent", x: at.x, y: at.y, cs: cs,
                             colour: colour});
          var mark = null;
          if (cs.stalled) mark = "!";
          if (cs.animation === "blocked") mark = "!";
          if (cs.animation === "walking_to_manager") mark = "^";
          if (mark) drawMarker(ctx, at.x, at.y, cam, mark, colour);
        }
      });
    });

    /* ---- paint far -> near --------------------------------------------- */
    paintOrder(items).forEach(function (it) { it.draw(); });

    return probe;
```

**`drawRoomPlate`** is the extraction of lines `:411–440` — the plate polygon and the two wall
faces — into a helper, so the room item's `draw` closure stays readable. It is a pure move:
same points, same fills, same order within the room. (The room plate is drawn as one item at the
room's **far corner** so that all of the room's own floor is beneath everything standing on it;
within the plate, the plate → wall → label order is unchanged.)

**Deleted:** the comment `"the characters, on top of every deck"` (`:479`) and the
`"draw FAR floors first so a near deck overlaps the one behind it"` reverse-loop framing
(`:405–406`). Both describe the defect as if it were the design.

---

## 5. Proof

The rule is verified by a runnable probe that builds a synthetic payload, sorts it with the
proposed `paintOrder()`, and prints the resulting draw order.

**Script:** `/home/trick/.local/state/qubit-voicebridge/runtime/cache/scratch/occlusion-proof.js`
(throwaway; not committed). It copies `TILE_W`, `TILE_H` and `DECK_Z` from `qubit-office.js` and
re-implements `iso()`'s `sy`, so it is sorting on the real numbers.

**Run:** `node occlusion-proof.js`

**Real output:**

```
screen-y of every cell (smaller = further from camera, paints first)
------------------------------------------------------------------
  z=0  gx+gy=0      0.0  agent:walkBehind
  z=0  gx+gy=2     17.0  desk:deskA
  z=0  gx+gy=2     17.0  agent:seatedAtA
  z=0  gx+gy=4     34.0  desk:deskB
  z=0  gx+gy=4     34.0  agent:seatedAtB
  z=0  gx+gy=6     51.0  agent:walkInFront
  z=1  gx+gy=2     -9.0  desk:deskUpper
  z=1  gx+gy=2     -9.0  agent:agentUpper

paint order produced by paintOrder() (index -> drawable):
------------------------------------------------------------------
  0.  y=   -9.0  desk:deskUpper   (a desk)
  1.  y=   -9.0  agent:agentUpper
  2.  y=    0.0  agent:walkBehind
  3.  y=   17.0  desk:deskA   (a desk)
  4.  y=   17.0  agent:seatedAtA
  5.  y=   34.0  desk:deskB   (a desk)
  6.  y=   34.0  agent:seatedAtB
  7.  y=   51.0  agent:walkInFront

checks:
  PASS  seatedAtA paints after deskA (character at its own desk)
  PASS  seatedAtB paints after deskB
  PASS  walkInFront paints after deskA and deskB (it is nearer)
  PASS  walkInFront paints after seatedAtB (in front of the seated character too)
  PASS  walkBehind paints before deskA (it is behind it)
  PASS  the upper deck paints before the lower deck where they overlap
  PASS  within one cell the desk paints before the agent (desk is under the figure)

ALL CHECKS PASS
```

### Reading the output

* **Index 3 → 4** (`deskA` then `seatedAtA`): a figure at its desk paints over the desk. Under
  the old code this was true only by luck of the pass order — and false the moment the figure was
  behind the desk.
* **Index 4 → 7**: `seatedAtB` (cell `1,3`) paints before `walkInFront` (cell `3,3`). The walker
  is nearer, so it covers the seated character. Under the old code *all* characters were in one
  pass and this came down to array order in `v.characters`.
* **Index 2 before 3**: `walkBehind` (cell `0,0`) paints **before** `deskA` (cell `1,1`). It is
  behind the desk, so the desk covers it. **This is the case the old code got backwards** — pass B
  drew `walkBehind` on top of every desk.
* **Index 0 → 1 before everything**: the upper deck (`z=1`, `y = -9`) paints before the lower
  deck (`z=0`, `y = 17`). The negative `z` term in the key produced the far-floors-first order
  the old `reverse()` was hand-rolling.
* **Index 0 → 1** (`deskUpper` then `agentUpper`, both cell `1,1`): the **same-cell tie** is
  broken by `KIND_RANK`, so a desk never paints over the person at it.

### The proof earned its keep

The **first** run of this script **failed** one check — "within one cell the desk paints before
the agent" — because the initial `paintOrder()` broke same-cell ties on `id` alone, and
`"agentUpper"` sorts before `"deskUpper"`. That is the desk drawn over the person:
the defect, reintroduced by the fix. The `KIND_RANK` tiebreak in §3.4 is what closed it, and the
re-run passes all seven checks. The failing run is recorded here deliberately — a test that has
never failed has not been shown to test anything.

---

## 6. Why this is the right fix, stated plainly

1. **It removes the cause, not a symptom.** The defect *is* sorting by kind; the fix *is*
   sorting by depth. No `if` was added to special-case a floor or a walker.
2. **It is the technique all three references use.** agent-monitor sorts `row + col`
   (`canvas.ts:183`, `:254`); StarNet sorts one `items` list of props *and* bodies on foot-line
   `y` (`proportion-review.js:49`); ours is the same key with the floor term folded in, because
   our projection already has it (`iso()`, `:256`).
3. **It touches no contract.** No payload field, no timer, no client-side position. The bob is
   still driven only by `progress` (`:497–499`, C7); the plugin still writes nothing (C4); the
   payload still decides every pixel. Only the *order of paint calls* changed.
4. **It is cheap.** One `Array.prototype.sort` of `rooms + desks + characters` per frame — the
   same list the renderer already walks, in the same O(n log n) the references accept.

## 7. Provenance to record when the code lands

This fix adds **no new upstream dependency** — the two techniques already adopted cover it, and
`25-PORTED-CODE.md` should gain the depth-ordering row rather than a new repo:

| upstream repo | branch | commit | licence | source path | local change |
|---|---|---|---|---|---|
| `ruiqili2/agent-monitor` | (CORRECTED from a wrong `kmoulder/...` citation; owner read from the checkout's own LICENSE) | — | MIT | `src/engine/canvas.ts` | painter's-algorithm depth sort on `col + row` -> `paintOrder()` / `depthKey()` |
| `androoAGI/starnet` | `feat/harness-backend` | `54899e5ff3f2` | MIT | `frontend/agent-demo/proportion-review.js` | props + bodies in one list sorted on foot-line `y` -> `paintOrder()` |
| `androoAGI/starnet` | `feat/harness-backend` | `54899e5ff3f2` | MIT | `frontend/agent-demo/authored-surface-mounts.js` | sub-cell `sortY` tiebreak -> `KIND_RANK` same-cell tiebreak |

Record the exact agent-monitor commit at port time (the local checkout under `/tmp/repo-ports`
carries a pinned revision; cite it, not `main`). `aphae` remains **no licence = no code**, and
nothing here is derived from it.

---

## 8. What this file does not do

* It does **not** edit `qubit-office.js` — another worker owns that file; §4 is the change for
  them to apply.
* It does **not** ask the backend to stop rounding walk positions (`tower.py:302–303`). The rule
  is written to be correct either way; the rounding is a separate, non-blocking question.
* It does **not** add per-pixel depth, sprite clipping, or z-buffering. A 2:1 iso office of
  axis-aligned cells does not need one; the painter's algorithm on feet lines is exact for it.
