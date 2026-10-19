# Qubit CHECKLIST — the tick-list that is machine-measured

**Status:** process index. **No item here is ticked on someone's word.** A box is ticked only
with the command and observed output that prove it, recorded in `docs/STATUS.md`.

The previous `docs/CHECKLIST.md` — a hand-maintained master tick-list with then-current route and
CLI counts — was deleted in `eafb54e26`. It is not re-created: a hand-edited list of hundreds of
items goes stale faster than anyone maintains it, and the counts it carried described a state that
no longer exists.

## The list that replaces it

| Scope | List | Refresh |
|---|---|---|
| Production symbols missing at the pin | `docs/FROZEN_TARGET_WORKLIST.md` | `.venv/bin/python scripts/frozen_target_progress.py` |
| Rebrand defects (writer/reader mismatch, categories, security boundaries) | `docs/REBRAND_INVENTORY.md` | re-run the commands embedded in that doc |
| Capability gaps vs upstream, classified | `scripts/audit_capability_gaps.py` | re-run the script |
| Work in flight / at risk | `docs/OPEN_THREADS.md` | update at every session end |
| What is true, with evidence | `docs/STATUS.md` | update every session |

## Pre-flight before claiming any item done

Each of these is a command, not a judgement:

```bash
git status --short                              # no uncommitted work of your own
git log --oneline -1                             # what you are building on
.venv/bin/python scripts/frozen_target_progress.py   # the gap, at the pin
```

Then, for the specific item: run it. A route that renders, a catalog row, a ledger entry, or a
typed refusal is **not** a working capability. `VERIFIED` is the sole release verdict; `M1`/`M2`
are implementation-progress markers only.

## Item template

```markdown
- [ ] <capability> — claim: <one sentence>
      proof: <command> → <observed output>
      recorded: docs/STATUS.md §<session>
```

If you cannot fill `proof`, the item is not done — leave it unticked and say so.
