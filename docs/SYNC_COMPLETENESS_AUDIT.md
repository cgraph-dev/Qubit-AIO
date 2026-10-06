# Sync completeness audit — did the 5,441 actually land?

**Verdict: NO. 640 upstream files are short-ported — present, but stopped at an
earlier revision than the baseline we supposedly synced.**

## Method

`scripts/audit_sync_completeness.py`. For every upstream `.py` file at our baseline
`a036b13793`, route it to our path (rename map applied), then compare the set of
top-level `def`/`class`/assigned names. A file is:

* **CURRENT** — we have every symbol upstream has at the baseline
* **SHORT-PORT** — present, but missing symbols that exist in the *baseline* blob.
  We never even reached the revision we claimed to sync from.
* **ABSENT** — no counterpart at all

Symbol-level, not byte-level: a byte diff is dominated by `hermes_*`/`qubit_*`
rebranding churn, which is why the earlier "95% present" file-count test looked fine.

## Result

```
upstream .py files at baseline : 8161
  EXISTS                       : 7906
  ABSENT                       :  255
  CURRENT                      : 7266
  SHORT-PORT                   :  640 files / 1624 missing symbols
  BEHIND-HEAD                  :    0 files (we are at the baseline, as expected)
```

Split of the 640:

| | files | missing symbols |
|---|---|---|
| tests | 422 | 1,040 |
| **PRODUCTION** | **218** | **584** |

## Production short-ports by subsystem

| symbols | files | subsystem |
|---|---|---|
| **313** | **113** | `hermes_cli` (-> `qubit_cli`) |
| 66 | 13 | `tui_gateway` |
| 51 | 26 | `tools` |
| 39 | 11 | `plugins` |
| 23 | 14 | `gateway` |
| 22 | 13 | `agent` |
| 17 | 5 | `cron` |
| 15 | 1 | `hermes_constants.py` |
| 11 | 4 | `pm` |
| 5 | 5 | `optional-skills` |
| 4 | 1 | `hermes_state_holders.py` |
| 4 | 1 | `providers` |
| 2 | 1 | `toolsets.py` |

## The evidence that started this

`qubit_cli/update_lock.py` exists with **10** top-level defs. Upstream at the SAME
baseline had grown it to **70**. Four sibling modules in that feature
(`update_pause_record.py`, `update_cmd_commit.py`, `update_custody.py`,
`anon_challenge.py`) are missing outright.

One file stopping short is a bug. 640 files stopping short is a systematic property of
how the sync was applied — which means the earlier "95% present / ~85-90% complete"
figures were measuring presence, not currency, and the real completion number is lower.

## Hazard recorded while producing this

The first attempt to get a wider JSON output ran a COPY of the script from `/tmp`.
`REPO = Path(__file__).parent.parent` then resolved to `/tmp`, and the audit reported
**8,161 absent / 0 present** — a plausible-looking result that is a pure artifact of
where the script sat. This is hazard class 10 (a tool resolving paths relative to the
wrong checkout) reappearing. The script now REFUSES to run when its computed repo root
is not a Qubit checkout.
