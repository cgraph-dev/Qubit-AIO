# PORT PLAN — closing the upstream gap in finishable chunks

Status: **RUNNING** (5 lanes on chunks 1–5 of 33). Started 2026-10-16 ~11:00.

## What the gap actually is

Upstream `hermes-agent` pinned at `517b5e10febd619ce30bb22580e29b160266eb43` (2026-10-08).
Measured at base `9c2f782e1`:

| | Count |
|---|---|
| commits behind | ~3,918 (moves ~10/hour — a treadmill number) |
| files added upstream that we lack entirely | **1,180** |
| — production `.py` | **299** (58,123 lines) |
| — tests | ~880 |

**The commit count is the wrong unit.** The work unit is the 299 production files, and those
are the only thing a port produces.

### Reconciling the numbers (they disagreed, so both are written down)

`scripts/upstream_drift_report.py` reports **298 "genuine additions"**; `git diff --diff-filter=A`
against the pin reports **299 production** absent (+~880 tests). The drift tool counts added files
*among changed files*; the git diff counts every file added in the range. They agree on the
actionable set — `501`-ish is not the number, `~299 production files` is. Trust
`/tmp/prod-files.txt` (regenerate per below) over any remembered figure.

## How to regenerate the work list

```bash
cd ~/upstream/hermes-agent
git diff --diff-filter=A --name-only \
  517b5e10febd619ce30bb22580e29b160266eb43..origin/main -- '*.py' > /tmp/upstream-added.txt

cd ~/Projects/Repos/qubit-voicebridge
python3 - <<'PY'
import os, json
LOCAL = "qubit_runtime"
added = [l.strip() for l in open("/tmp/upstream-added.txt") if l.strip().endswith(".py")]
absent = [f for f in added if not os.path.exists(os.path.join(LOCAL, f))]
prod = [f for f in absent if not f.startswith("tests/") and "/tests/" not in f]
open("/tmp/prod-files.txt","w").write("\n".join(prod))
print(f"absent={len(absent)} production={len(prod)}")
PY
```

## Chunking — the fix for "agents die before finishing"

33 chunks of **~2,000 lines each**, in path order (so each chunk is directory-cohesive).
The earlier failure was chunking by *directory* (`tests/gateway/` = 976 files): no agent
finishes that, so every lane died at its iteration cap having produced nothing.

```bash
# sizes, then chunk by weight
cd ~/upstream/hermes-agent
while read -r f; do echo "$(git show origin/main:"$f" 2>/dev/null | wc -l) $f"; done \
  < /tmp/prod-files.txt > /tmp/prod-sizes.txt
```

Chunk list: `/tmp/port-chunks.json` (33 entries; each `[[lines, path], ...]`).

## The method (being written by the scout lane as `PORT-METHOD.md`)

Per file:
1. Read at upstream HEAD: `git show origin/main:<path>`.
2. **Is it a real module or a decomposition shim?** Upstream did a `facade + siblings` split —
   e.g. `gateway/session_admission.py` is 14 lines re-exporting `admission_fingerprint` from
   `hermes_state_keys`. A shim whose underlying module we LACK is useless alone: record and skip.
3. **Resolve imports.** If `qubit_runtime/X.py` does not exist for an import, the port is
   incomplete — NAME the missing dependency; do not write an unresolvable import.
4. Port to `qubit_runtime/<same relative path>`.
5. **Prove it imports** with the venv python + `PYTHONPATH=qubit_runtime`.
6. Commit, naming the upstream path.

**Never paper over a blocked file with a stub or `try/except`.** A report of "6 ported, 5 blocked,
here is why" is worth more than 11 files that do not import.

## The brand rule (the trap that bit us twice today)

Upstream is `hermes`, ours is `qubit`. **A rename has two halves**: a lane renamed an env var's
canonical name to `QUBIT_*` and added a dual-read alias — it looked correct and broke **20 tests**,
because the writers and tests still set the old name (reverted, `ad2e66acc`).

For a NEWLY ported file there is nothing to stay compatible with, so port with `QUBIT_*` naming —
then grep the whole tree for that name in an `HERMES_` spelling and reconcile. If both exist,
report the collision rather than creating a third inconsistency.

**Do NOT blanket-rename.** `hermes_state_keys`, `~/.cache/qubit-pins/hermes-agent`, and the frozen
pin `69948c0057` are PROVENANCE, not branding. Never touch the pin or the 17 deny-listed scripts.

## Lanes in flight (deleg_66573949)

| Lane | Worktree | Chunk |
|---|---|---|
| scout | `/tmp/port-5` | 5 evals files + writes `PORT-METHOD.md` |
| 1 | `/tmp/port-1` | 11 files, agent/ + acp_adapter/ |
| 2 | `/tmp/port-2` | 11 files, agent/ |
| 3 | `/tmp/port-3` | 13 files, agent/ + cron/ |
| 4 | `/tmp/port-4` | 9 files, evals/ + cron/ |

Branches: `port/chunk-1..5` off `9c2f782e1`. Remaining 28 chunks are unwritten work.

## Landed so far today (context)

33 commits, all pushed to `canonical/main`. Real fixes include the fourth branch-copy
`message_identity` path (`2c9bf297f`), the bot-desktop smoke-test env mismatch (`a660da653`), and
pseudo-platform attributes on Python 3.12 (`9c2f782e1`).
