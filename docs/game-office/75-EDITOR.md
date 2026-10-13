# 75 — Floorplan Editor (M7)

D12 puts the editor in scope. This file specs it. **Nothing here is needed for M4** — the tower
must exist and be truthful before anything is allowed to edit it.

Design source: `androoAGI/starnet` `frontend/app/build.js` (diegetic BUILD mode over the same
procedural art: pan/zoom, snap grid, ghost preview tinted by the model's own validators, an
in-fiction toolbar) and `sidecar/permgrants.js` (standing grants with a deliberately tiny,
local-only grantable catalog, persisting before committing).

---

## The load-bearing insight: editing the floor IS editing capability

StarNet's model, which we adopt wholesale: **an object placed on the floor is a capability grant.**
Their `CAP_REGISTRY` maps `objectType → [{capId, tool, scope, requiresConsent, network}]`, and
`resolveTools(agentId, station)` derives the agent's tool list *fresh every turn* from the objects
in the agent's assigned room. `room.capabilities` is **derived, never hand-stored**. Their comment
is the whole argument:

> Called fresh every turn so placing/reclaiming an object changes the agent's reach on the very
> next model call.

So our editor is not a decoration tool. Dragging a `vault` prop into a room changes what those
agents may do on their next turn. That single fact determines every rule below.

### What we adopt

| Their mechanism | Ours |
|---|---|
| `CAP_REGISTRY`: objectType → grant[] with `{capId, tool, scope, requiresConsent, network}` | `office_cap_registry` — prop kind → the tools/MCPs/skills it grants |
| `resolveTools` projected per turn from the assigned room's objects | `office.grant_for(agent)` resolved each turn from the agent's `room_id` |
| `attenuate(resolved, subset)` — a delegate's tools = **intersection**, never union | an agent's toolset is `decode(seat_type) ∩ grant(room)`, monotonic down |
| `canRun()` = the COMPUTE GATE — a model turn requires a computer in the room | same: `computer` is the freebie present in every room; without it an agent cannot think |
| `disabledCaps` — a toolset kill-switch layered above object=capability | the existing `config.yaml` toolset toggles; **`compute` is never filterable** |
| `permgrants`: curated `GRANTABLE` catalog, local-only, persist-before-commit, revoke-any | same: the editor may only place **grantable** props, and persist precedes commit |
| A `deferred` list (advertised vs granted) | not needed in v1 — our tool schema is not the bottleneck |

**The two StarNet rules we must not lose:**

1. **Monotonic attenuation.** A delegated worker's tools are the *intersection* of its parent's and
   its own room's — never a union. Their comment says why it is load-bearing: the `deferred` list is
   filtered by the same subset, "because an unfiltered copy would let a delegated worker discover —
   and be told it may now call — a tool the gate then denies." **Attenuation has to hold for what
   the agent can FIND, not just what it may run.** Our equivalent: `tool_search` results and the
   prompt's tool list must be filtered by the same resolved set, or the agent gets told it has a
   capability the gate will refuse.
2. **`compute` is never filterable.** Every other capability can be switched off; the ability to
   think cannot, or a mis-click bricks a room full of agents. Their exact framing: compute is the
   single freebie so a brand-new agent "works out of the box — never a dead wall."

---

## Where the capability table lives

A new table, and it is **data, not code**:

### `office_cap_registry`

| column | type | notes |
|---|---|---|
| `object_type` | TEXT | the prop kind: `computer`, `cabinet`, `dish`, `workbench`, `notebook`, `studio`, `vault`, `connector` |
| `cap_id` | TEXT | the family: `compute`, `files`, `web`, `terminal`, `memory`, `studio`, `ledger`, `mcp` |
| `tool` | TEXT | the exact tool / MCP server / skill name granted |
| `scope` | TEXT | `read` \| `write` \| `execute` |
| `requires_consent` | INTEGER | 0/1 — 1 means the call routes to a **mandatory review** (D22) regardless of posture |
| `network` | INTEGER | 0/1 |
| `grantable` | INTEGER | 0/1 — **may this be placed from the editor?** (the StarNet `GRANTABLE` boundary) |

Two hard rules enforced in the store, not the UI:

- **`grantable = 0` for anything `scope='execute'` or `network=1` with an outward effect.** The
  editor may not hand out a capability that reaches the world. Those are granted only by the org
  (hire/promotion) or by the owner.
- **`compute` is present in every room by construction** and has no `grantable` row — it is the
  floor, not a prop.

### `office_room_object`

The placed objects. This is what `resolveTools` reads each turn.

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `room_id` | INTEGER | FK |
| `object_type` | TEXT | FK `office_cap_registry.object_type` |
| `instance_id` | TEXT | stable handle, unique per room: `pc1`, `cab1`, `dish2` |
| `tile_x` / `tile_y` | INTEGER | render position only — **never** a capability input |
| `connector_id` | TEXT | for `connector`: which MCP server this portal binds |
| `placed_by` | TEXT | `owner` (editor) or an agent slug |
| `placed_at` | REAL | |

Placing or removing a row changes the room's derived grants immediately. `tile_x/tile_y` are
deliberately *not* part of the resolution — where a prop sits in the room never changes what it
grants, only where it is drawn.

---

## Editor scope — what the owner can and cannot do

| Action | Allowed | Notes |
|---|---|---|
| Place / move / delete a **room** | Yes | resizes the floor's room set; staged |
| Set room `kind`, `name`, `function`, `capacity` | Yes | `capacity` is a headcount — staged |
| Place / move / delete a **grantable prop** | Yes | changes agent reach on the next turn; staged |
| Place a `connector` (MCP portal) | Yes, but only to an **already-configured** MCP server | the editor picks from existing servers, it does not add credentials |
| Undo / redo the last edit | Yes | local until staged |
| **Hire, fire, promote an agent** | **No** | that is an org decision (`org.py`), not a floorplan edit |
| **Place a non-grantable prop** | **No** | the `grantable` gate — the editor cannot hand out execute/network reach |
| **Edit a floorplan for a terminated/dissolved department** | **No** | dissolved floors are read-only history |
| **Edit a running department without staging** | **No** | every structural edit stages (C4) |

---

## Interaction — the diegetic editor

Borrowed shape from StarNet's BUILD mode, because it is right:

| Element | Behaviour |
|---|---|
| Mode toggle | From the HUD dock. Dims the live sim; agents keep working underneath, visibly |
| Camera | Same pan/zoom as the tower — the editor is an overlay, not a new scene |
| Grid | Snaps to floor tiles; the same tiles the tower renders |
| Ghost preview | Follows the cursor, snaps, and **tints from the store's own validators** — green when the room fits the floor and the caps are grantable, red when it violates a cap (D13), overlaps, or names a non-grantable prop |
| Toolbar | SELECT · ROOM · PROP · MOVE · DELETE · UNDO — verb-labelled, with the hint line under it, exactly like their tool set |
| Property panel | For the selected room: name, kind, function, capacity, occupants, its derived grant list (**read-only** — grants come from props, never typed) |
| Everyday exit | ESC returns to SELECT; leaving edit mode stages whatever is pending |

**Validators live in the store, not the view.** `office.validate_edit(proposed)` returns the
typed reason an edit is refused (floor cap reached, capacity exceeded, room overlap, non-grantable
prop). The editor colours the ghost from that answer, so the UI cannot disagree with the backend
about what is legal.

---

## The write path

```
editor gesture
  └─► office.validate_edit(proposed)        # store-side validators, typed refusal
      └─► staged: 409 + confirmation_id     # the shared journal, same as everywhere
          └─► owner settles (panel, banner, or voice)
              └─► apply: mutate floorplan + office_room(_object) rows
                  └─► re-resolve grants for affected agents  (next turn, no restart)
                      └─► office_event(kind='floor_edited')  → the tower re-bakes
```

**No direct writes.** The editor never touches the store outside that path (C4). A pending edit is
visible as a ghost with a confirmation marker, not as an applied change.

---

## Undo

Undo is **staged-applied**, not a local buffer: an undo is another staged edit that inverts the
last applied one. That keeps one history with one authority, and means an undo of a live-room
move stages and settles like the move did. A local undo stack that could diverge from the store
would be a second source of truth for the floor — the failure this whole plan is written against.

---

## M7 proof

**Behavioral, quoted:**

1. Enter the editor on a live floor; the ghost preview shows **red** when placing a 13th room
   (D13 cap) and names the cap. Quote the refusal.
2. Place a `cabinet` prop in a room; stage and settle it; then run an agent in that room and show
   its resolved tool list **before and after** — the file tools appear, with no restart.
3. Delete a `cabinet`; show the tools disappear on the next turn (the converse, which proves it is
   derived rather than cached).
4. Attempt to place a non-grantable prop (an execute-scope one): the editor refuses and the store
   returns a typed reason. Quote it.
5. Attempt an edit with **no confirmation**: `409` + a real `confirmation_id`, and the floorplan on
   disk is unchanged. Then settle it and show the change applied.
6. Undo the last applied edit: it stages, settles, and the store returns to the prior state with a
   second `floor_edited` event — history appended, not rewritten.

**Cost:** $0 (no models involved).

---

## Why this is M7 and not earlier

The editor edits the thing the tower renders. If M4's tower can lie, an editor lets the owner
write the lie deliberately and mistake it for truth. The tower must be truthful before anything
gets to change it — which is the same reason the ledger (M0) precedes everything.
