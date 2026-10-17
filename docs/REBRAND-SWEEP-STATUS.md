# Rebrand sweep status — CORRECTED 2026-10-17

`scripts/qubit_rebrand.py` in dry-run reports **167 content files + 5 path renames**
still pending. A delegated agent was asked to sweep this and returned zeros
(`occurrences_found: 0`) with the note "No prior sweep results available in this
turn's context" — i.e. it did not do the work.

**Do not simply run `--apply`.** Every one of the 5 pending path renames has live
referrers, so applying them blind breaks callers:

| pending rename | other .py files referencing it |
|---|---|
| `qubit_runtime/tests/hermes_cli` -> `tests/qubit_cli` | 111 |
| `qubit_runtime/tests/hermes_state` -> `tests/qubit_state` | 40 |
| `qubit_runtime/apps/desktop/dist/hermes.png` -> `qubit.png` | 8 |
| `scripts/rebuild_ledger_hermes.py` -> `rebuild_ledger_qubit.py` | 10 |
| `tests/test_ledger_hermes_build.py` -> `test_ledger_qubit_build.py` | 5 |

Concrete example of the hazard: `scripts/rebuild_ledger_qubit.py` itself imports the
OLD name —

    from rebuild_ledger_hermes import _handler_kind as _hk        (line 845)
    from rebuild_ledger_hermes import build_api_index, ROUTE_EVIDENCE  (line 861)

so renaming `rebuild_ledger_hermes.py` without updating those two imports turns a
working script into an ImportError. The rename is half-done already (both files
exist), which is why this looks deceptively safe.

Related: `scripts/hermes_vendor_manifest.py` is itself a *frozen provenance* artifact
(it exists to record upstream lineage) and only mentions `hermes.png` in a comment
citing the rebrand map — that one is legitimate and must stay.

## Correct approach for the next attempt

1. For EACH path rename, first rewrite every referrer, then rename, in ONE commit —
   or leave it. A dir rename under `tests/` (111 + 40 referrers) is a large
   mechanical change and belongs in its own commit with the referrer sweep, not in a
   rebrand pass.
2. Re-run the audit and the focused test files for the moved tree afterwards; a
   rename that breaks collection is worse than a stale name.
3. The 167 "content files" are a different question from the 5 path renames — the
   engine's token map includes RENAMEABLE tokens, but the tree deliberately
   preserves HERMES_* env vars, the `hermes-gateway-v1` wire string, `.hermes` path
   anchors, systemd unit names, and the profile-name alias `hermes` -> `default`.
   Those must NOT be renamed; a prior rebrand agent verified each is load-bearing
   across the tree. Triage per-token before touching anything.
