# Readiness wave — close the absorption gaps before the release gate

Date: 2026-10-27. Follows the 8-agent absorption (`docs/OPENJARVIS-8-AGENT-ABSORPTION.md`).
Goal: remove the known limits that stop Qubit from being honestly "release
ready", then run the gate once. **This plan does not itself claim `VERIFIED`.**

## Lanes (one worktree each, disjoint files)

| Lane | Deliverable | Owns |
|---|---|---|
| tiers | Make `AgentRole.model_tier` real: `roles.model_tiers` config map (`local`/`detail`/`api` → provider+model), resolver, runner, `--model`/`--provider` override, honest fallback when unmapped. | `qubit_runtime/roles/runner.py`, `qubit_runtime/qubit_cli/agent_cmd.py`, config default, tests |
| case | Deterministic case transforms (`uppercase`, `lowercase`, `title case`) as a real no-model action reachable from the Tk HUD and voice, with preview/diff/apply. | `voicebridge/voice_edit/{parser,schema,transform,prompts}.py`, tests |
| cold | Bound the cold-start model gap: prewarm the role/transform/classifier local models on helper start and hotkey, single-flight, non-blocking; classifier budget policy. | `voicebridge/desktop_helper.py`, `voicebridge/voice_edit/{brain,warm}.py`, tests |
| conn | Read-only connector readiness: `qubit connectors status` reports Google/Dropbox token paths + Apple `not_supported`, never prints secrets. | `qubit_runtime/qubit_cli/connector_status.py`, `subcommands/connectors.py`, tests |
| display | Voice-edit service health: detect missing `DISPLAY`/X11 in the service and expose a typed status/doctor reason, so a selection turn cannot silently degrade to dictation. | `voicebridge/voice_edit/{health.py,routes.py}`, tests |
| voice | Live role-bridge verification harness: prove each alias resolves to the real runner and that mutating roles stage confirmation; no duplicate monitor/digest ownership. | `scripts/verify_role_voice.py`, root tests |
| p8 | Owner sign-off pack: re-measure `PHASE8_INVENTORY.md` counts, list drift, row-by-row checklist; no deletions/renames. | `docs/PHASE8-SIGNOFF-PACK.md` (read-only product) |
| matrix | Baseline full-matrix run on the current HEAD in bounded chunks; record exact commands/counts/failures; no code edits. | `docs/MATRIX-BASELINE-2026-10-27.md` |

## Rules

- One worktree per lane; commit each coherent unit; never push; no main edits.
- Focused tests only in lanes; the matrix lane is the only broad run.
- No OpenJarvis import, no web HUD, no `HERMES_*`/`.hermes` rename, no secrets.
- A typed refusal is not a capability; do not weaken tests.

## Release gate (after merge)

1. Focused gate: roles, voice/HUD, connectors, recipes, runtime.
2. Full matrix once (non-gateway + gateway + frontend), every failure fixed or
   documented with a green alone-rerun; Windows out of scope.
3. Live: gateway/bridge/voice-typing active; `/chat` 200; one real role turn;
   one real Tk selection turn; connector refusals recorded.
4. `docs/STATUS.md` + `docs/OPEN_THREADS.md` updated; canonical pushed.
5. `VERIFIED` only if 1–4 are true.
