# Backend handoff — existing data and proposed interfaces

**Owner-owned backend work.** This document specifies integration requirements, not implemented endpoints. Do not add a new database, session log, approval mechanism, AI runtime or event bus in the frontend.

## What is real now

`GET /agent/api/office/tower` is the external wire route; inside the plugin SDK use `/api/office/tower` so prefix/auth handling remains canonical. Captured `evidence/tower.json` is one actual response, not the whole API specification.

| Read | Existing source/field | Frontend can do now |
|---|---|---|
| Floor/room/seat layout | `floorplan.floors[].{id,number,name,role,z,rooms}`; room rectangle/seats | Overview, floor selection, derived headquarters composition |
| Character identity/hierarchy | `characters[].{agent_id,slug,seat_type,department_id,supervisor_id}` | Roster, stable selection, role/department mapping; supervisor slug via roster ID lookup |
| Character position/state | `position`, `walking`, `facing`, `animation`, `last_event`, `stalled` | Honest current-position display; no invented authoritative route |
| Money/runway | `hud`, `history`, `history_totals` | Read-only financial overlays; nullable values remain unknown |
| Activity | `activity[].{id,ts,kind,agent_id,agent_slug,text}` | Filter/group locally by actual kind, timeline and source navigation |
| Memory/dossier | `characters[].memory`, `.dossier` | Known/unknown memory summary, evidence-backed profile display |
| Preview | `preview`, optional `preview_legend`, `empty` | Explicit preview banner; empty cannot borrow a fictional live cast |

Current incremental helpers in `voicebridge/office/tower_stream.py` are not evidence of a connected production stream. A snapshot has no full multi-building/location/portal contract. No current office chat/mutation endpoint is claimed by this spec.

## Frontend adapter contract (proposed names)

Implement an `OfficeBackendAdapter` facade with these operations. Bind each to the existing service or owner-provided extension; names below are **frontend methods**, not invented HTTP URLs.

| Method | Purpose | Availability behavior |
|---|---|---|
| `loadSnapshot(signal)` | Coherent tower/world data + capabilities/revision | Existing tower adapter available |
| `subscribe(cursor, onEvent, signal)` | Ordered changes, heartbeat, gap/reseed | Unavailable until a compatible existing stream is bound; use serialized polling |
| `getCharacterSession(agentId, signal)` | Canonical recipient + session/message origin | No fictional session created locally |
| `sendCharacterMessage(target, body, requestId)` | Submit through existing agent session route | Action disabled with reason until safe binding exists |
| `answerRequest(requestId, optionId, revision)` | Answer review/capability ask | Stage/settle through canonical authority; read back exact request |
| `stageAction(action, revision, idempotencyKey)` | Preview/validate org mutation | Returns real stage/refusal, never a client-generated confirmation ID |
| `settleAction(stageId, choice)` | Apply/deny already staged operation | Use the existing action-specific settle contract, not a guessed generic endpoint |
| `getArtifacts(refs, signal)` | Read output/evidence safely | Existing artifact permissions/redaction apply |
| `previewEdit(commands, revision)` | Validate proposed geometry/capability changes | Local ghost only; unavailable server validation is not success |

## Proposed additive DTOs

Use stable IDs and one revision/cursor. Every optional capability has `available`, `reason`, and an adapter binding. Unknown values are `null` or explicit unknown; missing does not mean zero.

- `Building`: ID, display label, authoritative floor IDs, presentation asset reference, entrance/exit portal references. Department/business ownership is referenced, not independently stored by the frontend.
- `Portal`: ID, source location, destination location, entrance/landing coordinates, allowed view transition, availability/reason. Distinguish presentation-only entrance from authoritative worker route connector.
- `FloorGeometry`: floor ID, bounds, rooms, walls/doors, walkable mask or navigation geometry, collision shapes and placed-object IDs. Frontend cannot use modulo room indices as the final model.
- `Appearance`: character/appearance ID, portrait/sprite asset IDs, authored cosmetic variants. Display name can be supplied; otherwise display real slug.
- `AgentRoute`: route ID + sequence, source/destination floor/room, nodes/portal segments, server time start/end, semantic reason/event ID, authoritative current/terminal location. Client interpolation is bounded and presentation-only.
- `RunStatus`: run/request ID, recipient, actual state, timestamp/freshness, interrupt capability, output/artifact references. Percent is optional and must be measured, not elapsed-time filler.
- `OwnerRequest`: stable ID, class/urgency, asking agent, affected work/entities, evidence, options, recommendation, deadline, current state/revision and allowed response actions.
- `PlacedObject`: object ID, room ID, kind, transform, appearance reference, `decorative` flag, derived capability scope and allowed operations. A drawn crate/shelf is not a grant without this data.
- `SessionEnvelope`: canonical session ID, recipient ID, profile/scope, cursor, message IDs, statuses, attachments/artifacts, actual model/provider display. No new message history store.

The owner may reuse existing task/request/action/session DTOs instead of introducing all of these names. Freeze exact wire schemas by inspecting actual backend methods; never require an unnecessary duplicate field when existing references suffice.

## Navigation versus mutations

| Interaction | Local view only? | Backend authority |
|---|---|---|
| Enter/exit building, travel by viewpoint, select floor/room | Yes | Reads valid location/portal graph; no org writes |
| Move camera, open settings, local quality/volume | Yes | None unless explicitly scoped system setting |
| Talk/message/issue real work | No | Existing session/agent submission and policy |
| Real agent reassignment/room move | No | Org validation + canonical staged op |
| Place a cosmetic preview object | Draft only | Clearly marked uncommitted |
| Commit room/capability edit | No | Validator, stage/settle and authoritative revision |
| Approve/deny owner request | No | Real request/action-specific confirmation path |
| Pause org, change budget/autonomy/model | No | Explicit scope, permissions and existing authority |

## Streaming correctness

Every message has a stable ID. Delta updates accumulate into that ID; done finalizes/replaces it once. An event names request/run/character/session IDs so concurrent messages cannot bleed into the wrong portrait. Gap or reordered revision triggers reseed; duplicate event/message IDs are idempotent. Closing the panel must not cancel the backend run.

No worker thinking badge from a generic network spinner. “Waiting for response” tracks the submitted request; “running tool” requires its actual event. A transport failure is not a task failure unless the backend says so. Interrupt acknowledgement is not proof of cancellation until observed state confirms it.

## Staged action correctness

Frontend submits action with entity IDs, expected revision and idempotency key. Server previews/refuses/stages it. Owner confirms the exact staged action. The existing settle protocol applies; frontend reads back the exact target and updates the view only to authoritative data. E-stop must refuse regardless of modal state. Refresh during a pending stage rehydrates the same stage, not a fresh duplicate.

Mutations must be surfaced as unavailable when their adapter is absent. A decorative “Apply” animation cannot claim a room changed, employee hired or request answered.

## Visibility/performance

Suspend ordinary scene polling/subscriptions when not observed, where the existing transport supports it. This is frontend/network policy; never pause autowork or the org implicitly. Resume with cursor/revision check. Bound full snapshots and detail lazy loads; avoid fetching all transcripts/portraits for every character each frame.

## Owner handoff order

1. Confirm existing session/event/action methods and their auth/settle paths.
2. Supply building/floor/room/portal graph and arbitrary floor geometry.
3. Bind character-targeted canonical conversation and owner requests.
4. Supply authoritative agent route segments and real placed-object/capability projections.
5. Bind editor validation/staging, finance detail and optional extra character tabs.

The frontend's initial recorded/preview slice can proceed before all additions, but it is not “live integrated” until those bindings pass the relevant journeys.
