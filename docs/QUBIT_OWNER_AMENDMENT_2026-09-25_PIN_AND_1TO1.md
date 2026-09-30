# Qubit owner amendment — pin move to the reference commit + 1:1 rebase

**Date:** 2026-09-25
**Authority:** owner directive ("use the latest Hermes commit and do that;
modify frozen contracts to suit this"). This amendment is the sanctioned
mechanism for changing frozen/authoritative content: per `AGENTS.md` and
`docs/DECISIONS.md`, an owner amendment **governs by content** while the archived
frozen records are **preserved, not rewritten**. The archive is intentionally not
edited; this amendment supersedes the clauses it names.

## Context

Qubit is `rebrand(upstream) + overlay`, not a hand port. It diverged from the
working reference for four reasons (see `docs/QUBIT_1TO1_ROOT_CAUSES.md`):
version mismatch, rebrand asymmetry, overlay regressions, config/environment.
The deterministic transform is `scripts/qubit_rebrand.py` (+ `qubit_debrand.py`),
verified idempotent (byte-identical across two runs).

## Decisions

1. **Pin.** The vendored runtime pin moves from `d337b736…` (v0.21.4) to
   **`69948c005791d4d0e51670ca015238c2c7663749`** (v2026.9.24-2096,
   v0.21.5+2053-class) — the newest source available on this host.
   **Recorded limitation:** GitHub was unreachable at pin time (3 retries), so
   the source of record is the read-only reference install
   `~/.hermes/hermes-agent` at that commit. If GitHub HEAD advances, this pin is
   the exact audited commit and any later move requires a new amendment.

2. **Rebase method.** Rebrand the exact pinned source via the deterministic
   engine (not manual porting). The token map is extended first — it previously
   omitted `hermes_state_health` and `hermes_yaml`, which on v0.21.5 produced an
   `ImportError`. Both are added; the rebase candidate resolves
   `from qubit_state_health import …` correctly.

3. **Frozen contracts.** `docs/differential/INTERFACE.md` and the frozen records
   under `docs/archive/2026-09-21/` were written against the old pin. This
   amendment re-baselines the 1:1 target to `69948c0057`; where the frozen
   interface states a requirement tied to `d337b736`, this amendment governs.
   The archived files remain byte-for-byte intact for provenance; the
   machine-readable interface contract will be regenerated into
   `docs/differential/` (frozen contract surface) at the merge freeze, not
   hand-edited now.

4. **No premature claims.** The rebase candidate is **not** merged, applied, or
   verified. No `VERIFIED`/`CODE_READY`/`COMPLETE`/parity claim. The merge is a
   tracked workstream against the quantified delta.

## Rebase evidence (2026-09-25)

- Staged 16,078 pristine files as `qubit_hermes/`; `qubit_rebrand.py --apply`
  changed 5,011 content files / 44 paths; `qubit_debrand.py --apply` changed
  2,035 files; candidate `qubit_runtime/` = 16,074 files.
- Candidate vs current Qubit vendored tree: **5,896 byte-identical, 7,109
  differing, 3,069 only-in-candidate (new upstream), 1,441 only-in-current
  (Qubit-only/pin-only)**.
- Candidate located at `/tmp/qubit-rebase-*/root/qubit_runtime` (isolated; the
  product tree is untouched).

## Merge policy (for the next writer)

1. Freeze to a **single writer** first; the live tree had six concurrent writers.
2. `git`-style 3-way merge: base = engine(v0.21.4), ours = engine(v0.21.5),
   theirs = current Qubit tree.
3. Re-apply the 52 curated product path-renames
   (`docs/archive/2026-09-21/QUBIT_REBRAND_MANIFEST.json`) and the overlay-only
   files; keep `voicebridge/`, `systemd/`, `config/`, `frontend/`, `docs/`,
   `skills/`, root `pyproject.toml` verbatim.
4. Re-apply the fork patches (write-guard fix, gateway payload names, context
   guard, dashboard unit, profiles, rebrand asymmetries B1).
5. Regenerate every generated artifact; run the Tier-0 audit harness
   (`docs/QubitVSupstream.md`); then the full gate + independent verifier.

## Sign-off

Owner directive 2026-09-25: use the latest Hermes commit, execute the rebase,
and adjust the frozen-contract surface accordingly. Recorded as DECISIONS #25.
