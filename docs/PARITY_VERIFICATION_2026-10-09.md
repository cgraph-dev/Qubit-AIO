# Parity Verification — post z-wave (2026-10-09)

Verification of "is the port still whole, and did anything this session silently regress?"
Measured at HEAD `4f4bef6e` (`pre-sync-worktree-snapshot`), clean tree.

**METHOD NOTE (read every number this way).** Counts exclude `__pycache__`. The two
instruments that count parity (`frozen_target_progress.py`, `audit_sync_completeness.py`)
walk **`.py` only** — `audit_sync_completeness.py` filters `p.endswith(".py")` and
`RUNTIME.rglob("*.py")`. Neither walks `.tsx`/`.ts`. The z-wave's work was mostly
frontend (`ChatPage.tsx`, `SessionsPage.tsx`, `Markdown.tsx`), which is **invisible to
the parity instruments** — their green says nothing about the frontend. The gate's
`entry-point`/`lazy`/`shadowed-def` checks are also Python-static.

## 1. Parity — `scripts/frozen_target_progress.py` (FROZEN_REV `517b5e10`)

| metric                | now | base | delta |
|-----------------------|----:|-----:|------:|
| absent files          |   8 |  261 |  -253 |
| short_port files      |  75 |  111 |   -36 |
| short_port symbols    | 198 |  272 |   -74 |
| behind_head files     |  88 |  214 |  -126 |
| behind_head symbols   | 185 |  703 |  -518 |
| TOTAL missing symbols | 383 |  975 |  -592 |

Split (tests do NOT block retiring hermes): production missing **0**, tests 383/391.
**VERDICT: AT TARGET** — 592 symbols closed vs freeze; nothing production-missing.
Note: all 8 absent / 75 short-port / 88 behind-head files are TESTS. Production buckets
are all zero.

## 2. Gate — `scripts/gate_all.py` (venv-backed)

| check                  | result | detail |
|------------------------|--------|--------|
| import gate            | PASS | 29 OK / 0 FAIL (of 29 modules) |
| entry-point resolution | PASS | HARD prod=0 tests=0 |
| lazy imports           | PASS | 0 unguarded; 1 guarded (silent no-op) |
| shadowed defs          | PASS | self-test FIRES; 19 real dup(s), 19 known / 0 new |
| capability audit       | PASS (informational) | 1727 missing symbols across 384 files |

**VERDICT: PASS — all blocking checks green.**
A first run in a bare worktree reported `import gate` FAIL solely because the worktree had
no `.venv` (`sync_import_gate` refuses the system python to avoid phantom ModuleNotFound
failures). After `ln -s <main>/.venv .venv` the gate is fully green. The worktree-venv
absence is an artifact, not a parity finding.

## 3. Silent-revert scan — z-wave + follow-ups (`4de70b52..4f4bef6e`)

Window = 15 non-merge commits + 4 merges. Per-commit `--numstat`:

- **No commit deletes more than it adds** (worst: `8f9df75a` A=375 D=156, net +219).
- **No whole-file deletions** anywhere in the window.
- Largest deletions, all confirmed INTENTIONAL refactors:
  - `8f9df75a` `voicebridge/qubit_compat.py` **-136**: inline legacy `schedules`-store
    cron handlers replaced by delegation to the new `voicebridge/cron_bridge.py` (+245).
    `h_cron_jobs` still exists (qubit_compat.py:3031) — the body changed, the symbol did not.
  - `f85cc61c` `frontend/.../ChatPage.tsx` **-64**: file 2925 -> 2961 lines (net +36);
    the removed `listChatSessions`-first transcript gate was replaced by the
    `getChatSession`-first logic. Rewrite, not a clobber.
  - `3dba3b79` merge (z5) carries the same ChatPage -64 plus Markdown/test additions.
- **No silent revert. No clobber.** `docs/CLOBBER_FINDING.md` does not exist in the tree.

## 4. Worktree / branch inventory

29 worktrees. Branch status vs `main` (note: `main` is NOT the session base — the session
landed on `pre-sync-worktree-snapshot` @ `4f4bef6e`, which is 1282 commits ahead of `main`).

### Merge of session wave — contained in `pre-sync-worktree-snapshot`
z1..z6, y2, y6, y7, v4, fix/artifacts, fix/dash-core, fix/dash-singles, fix/pins,
fix/routes, push/voice-linear, v5(frontend) — all ancestors of the session branch.

### UNMERGED branches with commits NOT in the session branch (AT RISK)
| branch | commits not in session | dirty | worktree |
|--------|-----------------------:|------:|----------|
| `restore/qubit-aio-full` | 3 | 1 (M tests/test_history_import_sanitized.py) | qubit-aio-restore |
| `fix/audit-remaining`    | 2 | 1 (same file) | qubit-b-fix |
| `fix/packaging`          | 1 | 0 | qubit-aio-fix-pack |
| `y1` | 1 (`5ca73d0f` salvage plugins CLI parent-parser) | 0 | qb-y1 |
| `y3` | 1 (`77856a8d` docs catalog provenance) | 0 | qb-y3 |
| `y4` | 1 (`dec5ef97` docs compat seam analysis) | 0 | qb-y4 |
| `y5` | 1 (`643dda19` de-brand pinned-index copy) | 0 | qb-y5 |

`restore/qubit-aio-full` and `fix/audit-remaining` share commits
`642c0c0b` + `bfec4e9b` and both have the same modified test file uncommitted.
`y1`'s salvage commit `5ca73d0f` is the same fix that `z4` (`39dd4b1b`) already merged —
verify before re-landing to avoid a duplicate.

### Untracked orphan work on the MAIN checkout (not authored this session)
- `frontend/qubit-web/src/components/SharedMetricsConsentBanner.tsx` (107 lines)
- `frontend/qubit-web/src/lib/model-picker-current.ts` (71 lines)
Both untracked, not in any branch, not in HEAD. At risk of a `git clean`.

### y2 worktree scratch (untracked, low value)
`/tmp/qb-y2`: `.audit_probe*.py`, `.catalog_*.json`, `.gh_cache.json`, `.probe_repos.json`
— probe scratch, not product work.

## 5. The pin

- `FROZEN_REV` (`517b5e10`) is reachable in `~/upstream/hermes-agent` (rev-parse OK).
- Local upstream checkout HEAD (`69948c00`, 2026-09-25) is **behind** the pin — the
  checkout is stale, not the pin.
- Upstream `origin/main` (`1744a19e`, 2026-10-08) is **ahead of the pin by 59 commits**
  (`PIN..origin/main = 59`; PIN is a direct ancestor). **662 files** touched, of which
  **241 are production `.py`** (tests/`__pycache__` excluded).
- The delta is **substantive, not mechanical**: gateway service-ownership writes,
  codex stream call-id correlation, plugin toolset restore on re-enable, solstice
  transport lazy-load, PM/install pinned-clone perf, Termux libc++ build fixes, plus
  ~21 desktop and ~13 CI commits. Sample real fixes:
  - `b6ab666b6de` fix(gateway): a service definition is written only by the home it pins
  - `5e24de476dc`/`c3302dd4fbf`/`a9e2716c9b9` fix(codex): call-id aliases across rotating streams
  - `05df0382a5d` fix(plugins): re-enabling restores its toolset in platform_toolsets
  - `51609d827bb` fix(solstice): load transport on first use, not at discovery
  - `d94b70f6752` fix(cli): `hermes model` offers reasoning effort when the model ID is unchanged
- **Do NOT repin** — reported only.

## Bottom line

Parity is intact and the port is whole: **AT TARGET** (0 production missing) and the gate
is **PASS** (all blocking checks green). No silent revert and no clobber in the z-wave
window — every large deletion is an intentional refactor. The real exposure is
**unmerged work at risk** (7 branches, most notably `restore/…`/`fix/audit-remaining`,
and 2 untracked frontend files on the main checkout) and the pin trailing upstream by
59 substantive commits.
