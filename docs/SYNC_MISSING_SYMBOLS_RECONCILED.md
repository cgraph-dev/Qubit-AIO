# The three measurements, reconciled

Three different figures were produced for "how many production symbols are missing".
They disagree, and the disagreement was worth resolving rather than averaging.

| measurement | method | missing | FP | real |
|---|---|---|---|---|
| 1. original audit | symbol names compared **within the routed file only** | 1,624 | 0 found | 584 |
| 2. agent 4 | grep the rebranded name **in the same file** | 238 | 206 (87%) | 32 |
| 3. this one | AST + **global index of every name in the tree** | 406 | 153 (38%) | **253** |

## Why each was wrong

**#1 missed the rebrand entirely.** Upstream `_hermes_home` vs our `_qubit_home`
reads as missing though both exist. That inflated everything by ~34%.

**#2 over-corrected.** It measured the rebrand rate on only the symbols it could
re-derive (238 of 583), then scaled. It also grep'd the transformed name *in the
same file*, so a symbol our tree defines in a **sibling module** still read as
missing. This codebase is a facade + `<stem>_<topic>` decomposition, so that is
common, not exotic.

**#3 (authoritative)** parses upstream with `ast`, then checks each missing name
against ONE global index of every top-level name defined anywhere under
`qubit_runtime/` plus the repo root. A symbol is missing only if **no file in the
tree defines it**.

## Verified against runtime, not name-matching

The decisive case: `qubit_cli.observability.relay_shared_metrics.rotate_segment`
is called by `agent/conversation_compression.py:2555` and referenced by tests, and
all three measurements flag it. Runtime check:

```
>>> from qubit_cli.observability.relay_shared_metrics import rotate_segment
ImportError: cannot import name 'rotate_segment' from
  'qubit_cli.observability.relay_shared_metrics'
```

Genuinely absent. The same shape recurs across the list.

## The number to use

**253 genuinely-missing production symbols.** Not 584 (34% inflation from rebrands),
not 32 (deflated by a same-file grep on a decomposed codebase). The concrete list is
in the tool's output; the top files are `tui_gateway/server.py`,
`hermes_cli/observability/relay_shared_metrics.py`, `tui_gateway/methods_tools.py`,
`hermes_cli/profiles.py`, `plugins/plugin_loader.py`, `hermes_cli/tips.py`,
`hermes_cli/uninstall.py`, `cron/scheduler.py`.

Tool: `scripts/audit_missing_symbols.py` (AST + global index).

## What this says about the campaign

Three attempts, three methods, three answers spanning 32 to 584 -- an 18x range.
Each method was defensible; each had a specific, findable flaw. The resolution came
from picking the arbiter that matches the real question ("does any file in our tree
define this?") rather than the one that was easiest to compute, plus one runtime
import to confirm the arbiter was measuring something real.

An estimate scaled from a sample was the worst of the three. Measure the population.
