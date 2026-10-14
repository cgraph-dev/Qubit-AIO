# `tower-recording.json` — provenance

**This file is a RECORDING, not a live backend response.** Every consumer must treat it as
`preview: true` regardless of the `"preview": false` field inside it, which is the *recorded*
payload's own field from the moment it was captured.

| Fact | Value |
|---|---|
| Source | `docs/game-office/frontend-game/evidence/tower.json` |
| Copied | 2026-10-14 |
| SHA-256 | `fbb9a00c862747809f6206eccc1e7287fc76137f52778cc930f2927842661cc4` |
| Bytes | 23,428 |
| Recorded from | `GET http://127.0.0.1:8890/api/office/tower` (captured during the F00-era audit probe) |
| Payload `schema` | `qubit-office-tower/1` |
| Payload `now` | `1791964228.8040378` (2026-10-14T11:50:28+03:00) |
| Live body | 3 floors · 10 characters · headcount 10 · net 980¢ |
| Provenance | `evidence/probe.py` (the harness that captured it) |
| Byte-identical to source | **verified** — same SHA-256 before and after the copy |

## Why it exists

F01 (see `../../../../../../docs/game-office/frontend-game/60-IMPLEMENTATION-PLAN.md`) freezes the
*real* payload shape so the rebuild's adapter is written against bytes the backend actually
produced, not against an invented shape. The adapter (F05) must round-trip this file.

## Rules for consumers

1. **Never label this live.** Any surface rendering it is a recorded preview. `70-ACCEPTANCE-AND-HANDOFF.md`
   and `frontend-game/10-DECISIONS.md` P15 both require the label.
2. **Do not regenerate it from a fresh probe without recording a new SHA-256 here.** A fixture whose
   provenance note is stale is worse than no fixture — the repo's own rule.
3. **It is not proof of live backend behaviour.** It is proof of one recorded payload. A capability
   test against it proves the adapter, never the backend.
4. Shape facts worth preserving, because the rebuild depends on them and they are easy to "fix"
   wrongly: `characters[].position` carries `floor`/`floor_role`/`room`/`room_id`/`x`/`y`/`slot`;
   `at` is a location *kind* (`desk`/`station`) distinct from `animation` (`idle`); `last_event` is
   nullable and carries `{kind, age_s}`; `dossier.known` is `false` while `dossier.beliefs` is
   non-empty — known/unknown is therefore a **field**, not "is the array empty".
