# Sync completeness — CORRECTED audit (rebrand-aware)

**This supersedes the figures in the first version of this document. My original
audit was wrong in a specific, measurable way, and an independent agent caught it.**

## The bug

The audit routed the FILE PATH through the rename map (`hermes_cli/` ->
`qubit_cli/`) but compared **SYMBOL NAMES literally**. So upstream's `_hermes_home`
read as missing while our tree has `_qubit_home` -- which it does, 51 references
against upstream's 43. Pure artifact.

## The measurement

Re-derived every reported production gap and re-checked it under the rebrand
transform (`/tmp/fp_check.py`, 218 production files, 584 re-derived symbols):

| | count |
|---|---|
| re-derived missing symbols | 584 |
| **FALSE POSITIVE** (present under rebranded name) | **200 (34%)** |
| **REAL missing** | **384 (66%)** |

An independent agent hand-checked 3 files, found all 3 to be artifacts, and
estimated ~87% false-positive / ~32 real. **That estimate was too aggressive** --
it extrapolated from a small, favourable sample. Measured across all 584, the rate
is 34%. Both directions of error matter: 87% would have hidden real work.

## Corrected audit output (baseline `a036b13793`)

```
upstream .py files : 8161
  EXISTS           : 7906
  ABSENT           :  255
  CURRENT          : 7530   (was 7266 before the rebrand fix)
  SHORT-PORT       :  376 files / 1254 missing symbols   (was 640 / 1624)
```

| | files | missing symbols |
|---|---|---|
| tests | 251 | 832 |
| **PRODUCTION** | **125** | **422** |

## ABSENT bucket (255) — classified

- test files never ported: **95**
- deliberate omissions: **0**
- **genuinely missing production code: 0**

## The number to use

**~422 missing production symbols across 125 files.** That is the real remaining
work at the baseline. Not 1,624 (my first figure -- inflated 34% by rebrands), and
not ~32 (the hand-checked estimate -- deflated by an unrepresentative sample).

Top production files (corrected):

```
18  tui_gateway/server.py
14  hermes_cli/observability/relay_shared_metrics.py
14  tui_gateway/methods_tools.py
13  hermes_cli/profiles.py
12  plugins/plugin_loader.py
11  hermes_cli/tips.py
11  hermes_cli/uninstall.py
10  cron/scheduler.py
10  hermes_cli/kanban_db_workspace.py
10  tui_gateway/methods_config.py
```

## Also true, and separate

Closing every short-port still leaves the tree NOT current: upstream is **953
commits / 644 .py files** past the baseline, with **160 BEHIND-HEAD files / 691
symbols**. Short-ports are "we never finished the revision we chose"; behind-head is
"upstream moved on after we chose it". Both are real; they are different jobs.

## Lesson

Two audits disagreed, and the resolution was neither of them -- it was the specific
false-positive measurement. When two measurements conflict, measure the disputed
quantity directly rather than picking the more recent or more confident claim.
