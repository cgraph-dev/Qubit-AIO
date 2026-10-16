# REPORT — audit of landed fixes not yet audited

Lane: `agent/catchup-14` @ base `ad2e66acc`, worktree `/tmp/catchup-ag14`.
Method: verify each claim against the tree, then try to BREAK it. Two real gaps
found and fixed, each with a test that is 1-RED on the unmodified tree.

Baseline counts (all with `-o addopts=''`):

| suite | before | after |
| --- | --- | --- |
| `tests/test_check_commit_hygiene.py` | 27 passed | 27 passed (assertions added in the existing live-writer test — 1-RED before the fix) |
| `tests/test_upstream_drift_report.py` | 4 passed | 5 passed (new test — 1-RED without the fix) |
| `tests/gateway/test_supervised_marker_dual_read.py` + `test_restart_service_detection.py` | 26 passed | 26 passed |
| `tests/test_office_autowork_memory_receipts.py` + `test_office_agent_memory.py` | 24 passed | 24 passed |

No suite count went DOWN. The two suites whose counts are unchanged gained
assertions that fail before their fix and pass after, so the fix cannot hide.

---

## Claim 1 — `080514a2d` SUPERVISED_CHILD dual-read. **HOLDS.**

Claim: 5 writers emit `QUBIT_SUPERVISED_CHILD`, 10 readers accept both.

Verified by grepping the WHOLE tree (not a remembered list) for the three
spellings `SUPERVISED_CHILD`, `S6_SUPERVISED_CHILD`,
`GATEWAY_EXTERNAL_SUPERVISOR` across `*.py *.sh *.plist *.service *.ps1 *.xml
*.md` — 122 + 50 + 21 hits — and classifying every product-code site.

Writers (all emit the canonical `QUBIT_` spelling; none writes the legacy name):

- `qubit_runtime/qubit_cli/gateway.py:3340` — systemd unit `Environment="QUBIT_SUPERVISED_CHILD=1"`
- `qubit_runtime/qubit_cli/gateway_launchd.py:415` — plist `<key>QUBIT_SUPERVISED_CHILD</key>`
- `qubit_runtime/qubit_cli/gateway_windows.py:61` — `_GATEWAY_ENV` `("QUBIT_SUPERVISED_CHILD","1")`
- `qubit_runtime/qubit_cli/service_manager.py:455,458` — s6 run script `export QUBIT_S6_SUPERVISED_CHILD=1` / `export QUBIT_SUPERVISED_CHILD=1`

Verified empirically, not by reading: generating each launcher writes canonical
only and never the legacy name; the reader helper accepts both.

Readers (all route through the `*_marker_present()` helpers in
`gateway/restart.py:54,67,74`, each doing `env.get` over
`(CANONICAL, *ALIASES)`):

- `gateway/restart.py:298,318` — `is_gateway_supervisor_process` (s6 + external), `is_supervised_gateway_launch`
- `qubit_cli/main.py:577` — `_apply_profile_override`
- `qubit_cli/gateway.py:4973` — s6 recursion guard
- `qubit_cli/dashboard_procs.py:209` — fixed-identity
- `qubit_cli/venv_sync.py:319` — the tail-owed check (the original split-brain)
- `qubit_cli/update_cmd_posix_pause.py:194` — pause
- `tools/process_registry.py:312` — self-kill guard (via `is_supervised_gateway_launch`)

No product-code site reads only one spelling: an exhaustive regex for
`(environ|env|os.getenv)...(get\(|\[)"(HERMES|QUBIT)_(S6_)?(SUPERVISED_CHILD|
GATEWAY_EXTERNAL_SUPERVISOR)"` in product code returns ZERO hits. Every legacy
occurrence outside tests is either an alias tuple, a docstring, a test, or the
brand-scan allowlist (`scripts/qubit_public_surface_scan.py:169`, which lists
`HERMES_SUPERVISED_CHILD` as a *scanned public-surface token*, not a read).

**Writer/reader mismatch of the anon_auth kind: NONE.** Writers were migrated
in the same commit; the alias is read-only.

Tests that set only the legacy spelling still pass because the reader is a true
dual-read: `test_launchctl_guard_diagnostic.py` (sets `HERMES_SUPERVISED_CHILD`
only, 2 passed), `test_homeassistant.py` marker tests (1 passed/1 skipped),
`test_restart_drain.py`, `test_gateway_s6_dispatch.py`,
`test_gateway_external_supervisor.py`. None of these is the anon_auth shape
(reader migrated, writer/test left behind) — they set legacy *input*, which the
dual-reader deliberately accepts.

---

## Claim 2 — `63b458a41` upstream_drift_report + pin reconciliation. **PARTIAL → gap fixed.**

Claim: it reconciled the drift-MEASURING call sites and left provenance anchors
alone.

Verified: `audit_capability_gaps.py` now sets `FROM = _frozen_rev()`, `TO =
"origin/main"` (both resolve to `517b5e10…` / `origin/main` at import);
`audit_missing_symbols.py:50` sets `BASE = _frozen_rev()`. Both read the pin
from the single home `frozen_target_progress.FROZEN_REV`. The provenance anchor
`69948c0057` is untouched (still in `hermes_vendor_manifest`,
`hermes_byte_manifest`, `merge_v0215/freeze`, `rebuild_ledger_hermes`,
`source_inventory`) — correct, it records "vendored FROM", not drift.

**The miss:** `scripts/audit_sync_completeness.py` — the instrument both
reconciled call sites depend on, and the one `frozen_target_progress.py` shells
out to — still hardcoded its `short_port` measuring base:

    scripts/audit_sync_completeness.py:126    BASELINE = "a036b13793"

`a036b13793` is the 2026-10-06 sync waypoint, **1783 commits behind** the frozen
pin (`git rev-list --count a036b13793..517b5e10 == 1783`, verified against
`~/upstream/hermes-agent`). The `short_port` branch answers "we never even
reached our own baseline" (`audit_sync_completeness.py:433`) — and "our own
baseline" is the revision we should have synced from. Measured against a stale
base, every symbol upstream added between the waypoint and the pin is credited
to us, so a file we never ported at the current revision is mislabelled
`behind_head` instead of `short_port`: real port debt, under-reported. Same
defect class the commit fixed at the two call sites; the commit body does not
mention this constant at all.

**Fixed** (`069223321`): `BASELINE = _frozen_rev()`, read from
`frozen_target_progress.FROZEN_REV`, using the identical helper shape the two
sibling scripts use. Test `test_audit_short_port_baseline_is_the_frozen_pin`
(added to `tests/test_upstream_drift_report.py`): asserts the literal is gone
AND `BASELINE == declared FROZEN_REV`; **1-RED** on the unmodified value,
green after. Suite 4 → 5 passed.

**Reported, NOT silently moved** (entangled, would falsify a frozen record):

- `frozen_target_progress.py`'s frozen `BASE` dict (261/111/272/214/703/975,
  line 67) was measured at `--rev ad12263a5ed4` — a THIRD revision, distinct
  from both `a036b13793` (the audit's old BASELINE) and `517b5e10` (FROZEN_REV).
  Moving `BASELINE` changes the short_port/behind_head split against that frozen
  dict, so the two must be re-frozen together, by the owner, not by an audit
  lane. Left untouched.
- `docs/FROZEN_TARGET.md` is **referenced but does not exist** (deleted by
  `eafb54e26`, an owner directive; only its referencing scripts remain):
  `frozen_target_progress.py:5,29,63` and `upstream_drift.py:49,62,63,69` both
  cite it, and `upstream_drift.py`'s `pinned_rev()` fallback reads it. Its
  last-resort pin source is dead. Flagged for the owner.

---

## Claim 3 — `6ee2adf0c` office memory receipt. **HOLDS** (with a weak sibling noted).

Verified `autowork._remember()` requires the completed receipt:
`voicebridge/office/autowork.py:286-292` returns `isinstance(receipt, dict) and
receipt.get("state") == "OK" and receipt.get("stored") is True`. Confirmed this
matches `agent_memory.remember()`'s real return contract
(`voicebridge/office/agent_memory.py:185-245`): it returns a dict with
`state`/`stored`, and returns `state="EMPTY"` or `state="FAILED"` with
`stored: False` WITHOUT raising — which is exactly why a non-raising call could
not be treated as success.

Sibling hunt for the same bug class ("non-raising call treated as success"):

- `agent_memory` importers `escalation.py:409`, `runner.py:118,267`,
  `receipts.py:142`, `tower.py:636` are all **event emitters** (`emit_event`,
  `tool_events`, `dossiers`) wrapped in `try/except pass`. None derives a
  "written/persisted" boolean from a non-raising call, so the bug class does not
  replicate.
- `voicebridge/voice_commands.py:1121` hardcodes `"remembered": True` after
  `sm.remember(...)`. Checked: `memory/store.py:278` `remember()` ALWAYS returns
  a record or raises (the dedup path returns the existing record), so the fact
  is durable in every non-raising branch — the overclaim does not apply.
- **Weak sibling (not a defect, worth noting):** `autowork.py:752` calls
  `_remember(...)` and **discards the returned bool**. The honest signal is now
  computed and thrown away, so the tick's `summary` never says whether memory
  was written. Not the same overclaim (nothing false is recorded), so left
  unfixed, but a caller that ignores the receipt is how the original bug
  survived.

Tests: `test_office_autowork_memory_receipts.py` (11) +
`test_office_agent_memory.py` (13) = 24 passed.

---

## Claim 4 — `scripts/check_commit_hygiene.py` accuracy. **PARTIAL → gap fixed.**

Ran it. Exit code IS 1 with violations (an initial reading of "exit 0" was a
pipe artifact from `head`; rerun without a pipe gave `EXIT=1`, matching the
documented contract). Findings spot-checked against the real system:

- Every flagged foreign worktree exists, on the branch named, with the exact
  `git status --porcelain` paths reported (ag8/10/11/12/13, w24-2, qubit-b-fix).
- The HANDS-OFF claim for `/tmp/catchup-ag13` is real: pids 2880779/…/2880783
  exist and their `/proc/<pid>/cwd` is `/tmp/catchup-ag13` (running pytest
  there).

**The finding I could DISPROVE:** the guard reported the SAME worktree as both
VIOLATION(s) and HANDS-OFF. With a live writer in `/tmp/catchup-ag13` it emitted
3 `foreign_worktree_dirty*` violations AND the `foreign_worktree_live_agent`
hands-off finding — contradicting its own module docstring ("HANDS-OFF … is NOT
a violation", line 17) and driving exit 1 for work the rule says belongs to its
owner. That is the cry-wolf failure (a guard that flags what it must ignore gets
ignored). The implementation emitted violations unconditionally, then *added*
the hands-off finding in a separate branch (`check_commit_hygiene.py:673-701`);
the test only asserted the hands-off finding existed, never that the worktree was
absent from the violations.

**Fixed** (`5c6c69d55`): decide liveness FIRST; emit EITHER the hands-off finding
OR the violations, never both. Test strengthened to assert the worktree is
absent from `foreign_worktree_dirty`/`_path` and `exit_code() == 0` —
**1-RED** before, green after. Live re-run: `/tmp/catchup-ag13` now appears once,
as HANDS-OFF, with no violations attributed to it. Genuinely-dirty worktrees
with no live writer remain violations (correct).

Suite: 27 → 27 passed (assertions added; the count is stable because the test
case already existed — the added assertions are what falsify the old behaviour).

---

## anon_auth-shape hunt (reader migrated, writer not) — clean.

For each landed fix, checked whether a reader was migrated while its writers
were not:

- `080514a2d` (SUPERVISED_CHILD): writers migrated in-commit; no product-code
  writer emits legacy. Clean.
- `63b458a41` (drift pins): writers = the constants themselves; both called
  sites and the instrument now read one home. Clean after the fix above.
- `6ee2adf0c` (office receipt): consumer and authority agree. Clean.
- `anon_auth` revert `ad2e66acc`: `GUEST_ONBOARDING_ENV = "HERMES_GUEST_ONBOARDING"`
  is the single canonical constant and every reader (`anon_auth.py:158`,
  `setup_profile.py:159-160`) reads through it, matching the writers. No residue.

No reader-migrated-writer-not mismatch remains in the audited set.

---

## Verdicts

| # | fix | verdict |
| --- | --- | --- |
| 1 | `080514a2d` SUPERVISED_CHILD dual-read | **HOLDS** |
| 2 | `63b458a41` pin reconciliation | **PARTIAL** — instrument base missed, gap fixed `069223321` |
| 3 | `6ee2adf0c` memory receipt | **HOLDS** |
| 4 | `check_commit_hygiene.py` accuracy | **PARTIAL** — cry-wolf double-classification, gap fixed `5c6c69d55` |

Fixes in this lane: `5c6c69d55`, `069223321` (branch `agent/catchup-14`).
