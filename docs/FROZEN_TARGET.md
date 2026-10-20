# FROZEN TARGET: upstream `43faacfb8de7`

**Re-frozen 2026-10-16** (owner ruling, in force until hermes is retired). This is the
revision Qubit must match before hermes is retired. It is NOT "upstream's latest" — that
moves. It is the finish line.

> **History:** the previous `docs/FROZEN_TARGET.md` (pin `ad12263a5ed4`, frozen 2026-10-07)
> was deleted in `eafb54e26` together with 673 other docs under the owner's English-only
> ruling. That deletion was meant to retire *reference material*, not the pin's own home,
> and it left `scripts/frozen_target_progress.py` carrying a dangling pointer to this file.
> This is the restored home, restated for the current pin. The single machine-readable
> source of the pin is `scripts/frozen_target_progress.py::FROZEN_REV` — if the two ever
> disagree, the script is authoritative and this note is stale.

```
target commit : 43faacfb8de7b29548547a7fbdedad0cc6e7391c
subject       : Merge pull request #139613 from NousResearch/fix/pkg2-provider-override-route
date          : 2026-10-16
previous pin  : 517b5e10febd (2026-10-08, drifted 4,218 commits / 5,434 files behind)
```

## Why a freeze, not "follow HEAD"

Chasing live HEAD is a treadmill: upstream moves (~66 commits/day measured) faster than this
campaign closes symbols, so "same commit as them" never arrives and hermes is never retired.
The freeze makes the finish line reachable.

**Post-freeze upstream commits get ported AFTER hermes is deleted.** Measured at the
re-freeze: the pin is 1,094 commits behind upstream `origin/main` as of 2026-10-17 — that
backlog is explicitly out of scope until retirement.

## The rule

- Measure EVERYTHING against `43faacfb8de7b29548547a7fbdedad0cc6e7391c`. Never `origin/main`.
- Every audit passes `--rev 43faacfb8de7b29548547a7fbdedad0cc6e7391c` explicitly.
- Ports are 1:1 and literal against this revision — including upstream features this fork
  dropped on purpose (owner ruling 2026-10-19): no symbol is skipped as "out of scope", and
  no typed refusal is written in a symbol's place. Exclusions are pruned by the owner after
  the gap is closed, not by an agent mid-port.
- When the production gap for THIS revision reaches zero, verify, then retire hermes.
- Do not move this pin until hermes is gone.

## Declared exclusions (deliberate, do NOT re-port)

Recorded so a future port wave does not re-add these as "missing symbols". Each leaves the
audit one symbol short of zero by design; the audit's job is to report that, not to erase it.

| symbol | file | why it is gone |
|---|---|---|
| `_housekeeping_media_caches` | `gateway/run.py` | Byte-identical DEAD DUPLICATE of the live `gateway/run_housekeeping_disk.py::housekeeping_media_caches`, which the registered chore (`run.py`) already calls. A 6-lane merge re-added the copy; it never executed. Deleting it is behaviour-preserving — re-adding it would restore a dead symbol, not a capability. |
| `_import_members` | `qubit_cli/backup.py` | Superseded by inlining: `run_import` (backup.py, `with exclusive_maintenance(import_homes, purpose="import")`) reserves every affected home itself and publishes inline, and it is strictly BETTER than the upstream helper — it marks the reservation `purpose="import"` (a contender can tell this publication window from a live gateway) and drops the `gateway.lock` files the reservation created (`created_locks`), so the import leaves no seeded lock. Upstream calls its copy from `hermes_cli/backup.py:802`; here the call site is the inline block. Wiring it would re-introduce a second, weaker publish path. |
| `_import_members_exclusive` | `qubit_cli/backup.py` | The body of the same inlined publish loop in `run_import`; reachable only from `_import_members`. No behaviour is lost. |
| `_import_external_member` | `qubit_cli/backup.py` | Its only caller was `_import_members_exclusive`; the `_external/` branch of `run_import`'s inline loop is the same code (home-relative target, `relative_to(home_dir)` traversal refusal, atomic extract, `0600` for secret suffixes). Kept as a helper it would be dead code the detector flags. |
| `_llamacpp_context` | `agent/model_metadata.py` | Superseded by `agent/model_metadata_llamacpp.py::llamacpp_context`, which the live probe table calls (`model_metadata.py`, typed probe dict) and which is strictly better (passes `autoload=false` and falls back to the router's `--ctx-size`). The in-file copy was left behind by that refactor and had no call site. Deleting it is behaviour-preserving. |
| `_replay_unanswered` | `tools/bot_relay.py` | The fork's drain deliberately replaced upstream's unbounded claimed-envelope replay with `_reoffer_unanswered`: one bounded re-offer per envelope after `REOFFER_AFTER_SECONDS`, never a turn loop against a target nobody is listening for. The upstream helper had no caller; re-adding it would re-introduce a second re-delivery path beside the bounded one. |
| `_settle_pre_upgrade_claim` | `tools/bot_relay.py` | Reachable only from the deleted `_replay_unanswered`. The fork's envelope schema does not carry upstream's `canonical_delivery_v1` marker, so the pre-upgrade settlement branch cannot run either. No behaviour is lost. |
| `_normalize_newlines` | `cron/scheduler.py` | Left behind when this fork's run-document framing diverged from upstream's (`_PROMPT_FRAME`/`_RESPONSE_FRAME` are not present here), so its only upstream call site does not exist. Unused private helper; no behaviour is lost. |
| `_submit_fingerprint` | `acp_adapter/gateway_server.py` | Superseded by `acp_adapter/gateway_media.py::submit_fingerprint`, which the live `_prepare_submit` path calls. The split-module version fingerprints the editor content *before* staging, so a retry of the same prompt matches without re-fetching a remote image (whose second download could fail or return other bytes). The in-file copy was left behind by that split and had no call site; re-adding it would restore a second, weaker identity path. |

## Numbers

Baseline at the re-freeze (2026-10-17, first complete-blob read of the pin):

| metric | at freeze | now (2026-10-19) |
|---|---|---|
| upstream `.py` files | 9,503 | 9,503 |
| absent files | 974 | 901 |
| short_port files / symbols | 610 / 1,649 | 453 / 1,189 |
| behind_head files / symbols | 0 / 0 | 0 / 0 |
| **total missing symbols** | **2,623** | **1,189** |
| — production | — | **296** (78 files) |
| — tests (non-blocking) | — | 1,794 |

Closed since the freeze: **1,434 symbols** (~55% of the gap). The verdict is driven by
PRODUCTION only — tests cannot block retiring hermes.

Read it in one command:

```bash
.venv/bin/python scripts/frozen_target_progress.py
```

which prints `NOT AT TARGET -- N production missing` or `AT TARGET`, plus the delta against
the frozen baseline.

## Retirement checklist

1. **Disable `hermes-dashboard.service` FIRST** if it exists and is enabled+active — with
   `Restart=always`/`RestartSec=5` a moved-aside home makes it restart-storm every 5s.
   *(Checked 2026-10-19: no hermes-dashboard unit is loaded on this host; it was already
   handled. Re-check before retiring.)*
2. `mv ~/.hermes` aside (do not delete — that is the rollback).
3. Restart the four units: qubit-voicebridge, qubit-voice-typing, qubit-voice-orb,
   qubit-gateway.
4. Verify each surface: the dashboard, /qubit-chat, /chat, voice (Right-Ctrl + wake).
5. Only then delete.
