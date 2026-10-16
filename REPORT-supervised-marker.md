# Supervised-marker rebrand migration — `QUBIT_SUPERVISED_CHILD` (dual read, single write)

Branch: `agent/catchup-6` · base `6ee2adf0c` · code commit `145bf99b9`

## The defect (reproduced)

The repo was half-migrated on a supervision marker. Generated launchers WROTE the
Hermes spelling; most readers read that same spelling; one reader (`venv_sync`) already
read the Qubit spelling. Exactly one spelling worked per consumer, and they disagreed:

- a launcher that writes `HERMES_SUPERVISED_CHILD` was **invisible to `venv_sync`**
  (which looked only for `QUBIT_*`), and
- the landed contract `qubit_runtime/tests/hermes_cli/test_apply_profile_override.py`
  already expects `QUBIT_SUPERVISED_CHILD` in 7 places and was **failing (4 red)** on base.

Baseline reproduced on a clean checkout of `6ee2adf0c`:
`test_apply_profile_override.py` → `4 failed, 26 passed`.

## The fix

**Single write, dual read.** Canonical `QUBIT_` spelling is written; the legacy `HERMES_`
spelling is kept as a READ-ONLY alias so a systemd unit / launchd plist / Windows
Scheduled Task **already installed** on a machine (which exports the old name and cannot
be rewritten in place) is still recognised. Installs converge on the canonical name
whenever the unit is regenerated (`qubit gateway install` / `refresh_systemd_unit_if_needed`).

The precedent shape followed is the marker-family docstring in
`qubit_cli/main.py._apply_profile_override` and the superset
`gateway/restart.py.is_gateway_supervisor_process`. The family constants and
`*_marker_present()` helpers live in `gateway/restart.py`:

```
SUPERVISED_CHILD_ENV            = "QUBIT_SUPERVISED_CHILD"
SUPERVISED_CHILD_ENV_ALIASES    = ("HERMES_SUPERVISED_CHILD",)
S6_SUPERVISED_CHILD_ENV         = "QUBIT_S6_SUPERVISED_CHILD"
S6_SUPERVISED_CHILD_ENV_ALIASES = ("HERMES_S6_SUPERVISED_CHILD",)
EXTERNAL_GATEWAY_SUPERVISOR_ENV         = "QUBIT_GATEWAY_EXTERNAL_SUPERVISOR"
EXTERNAL_GATEWAY_SUPERVISOR_ENV_ALIASES = ("HERMES_GATEWAY_EXTERNAL_SUPERVISOR",)
supervised_child_marker_present(env)     # dual: canonical + alias
s6_supervised_child_marker_present(env)  # dual: canonical + alias
external_supervisor_marker_present(env)  # dual: canonical + alias
```

## Marker family — final state

| Member | Canonical (written) | Legacy alias (read-only) | Status |
|---|---|---|---|
| generalized marker | `QUBIT_SUPERVISED_CHILD` | `HERMES_SUPERVISED_CHILD` | renamed, dual-read |
| s6 marker | `QUBIT_S6_SUPERVISED_CHILD` | `HERMES_S6_SUPERVISED_CHILD` | renamed, dual-read |
| external-supervisor opt-in | `QUBIT_GATEWAY_EXTERNAL_SUPERVISOR` | `HERMES_GATEWAY_EXTERNAL_SUPERVISOR` | renamed, dual-read |
| systemd `INVOCATION_ID` | — | — | **untouched** — not a Qubit-branded env var, set by systemd itself and read (narrowly, gateway commands only) as before |
| launchd `XPC_SERVICE_NAME` / `HERMES_LAUNCHD_LABEL` | — | — | **untouched** — see "deliberately not changed" |

No reason to leave any of the three family members alone: all three are Qubit-written
markers and all three leak the legacy brand on a product surface.

## WRITE sites changed (single write = canonical)

| file:line | writer | before → after |
|---|---|---|
| `qubit_runtime/qubit_cli/gateway.py:3340` | systemd unit (`generate_systemd_unit`) | `Environment="HERMES_SUPERVISED_CHILD=1"` → `QUBIT_SUPERVISED_CHILD=1` |
| `qubit_runtime/qubit_cli/gateway_launchd.py:415` | launchd plist | `<key>HERMES_SUPERVISED_CHILD</key>` → `QUBIT_SUPERVISED_CHILD` |
| `qubit_runtime/qubit_cli/gateway_windows.py:61` | Windows Scheduled Task env (`_GATEWAY_ENV`) | `("HERMES_SUPERVISED_CHILD","1")` → `("QUBIT_SUPERVISED_CHILD","1")` |
| `qubit_runtime/qubit_cli/service_manager.py:455` | s6 run script (`_render_run_script`) | `export HERMES_S6_SUPERVISED_CHILD=1` → `QUBIT_S6_SUPERVISED_CHILD=1` |
| `qubit_runtime/qubit_cli/service_manager.py:458` | s6 run script (generalized marker) | `export HERMES_SUPERVISED_CHILD=1` → `QUBIT_SUPERVISED_CHILD=1` |

The external-supervisor marker has no *generated-file* writer — it is exported by the
invoking supervisor / `sudo env -i` caller when `qubit gateway run --external-supervisor`
is used, so it is read-side only. Its canonical constant changed
(`gateway/restart.py:50`) and its dual read is in place; the reader set is listed below.

## READ sites changed (dual read = canonical + legacy)

| file:line | reader | change |
|---|---|---|
| `qubit_runtime/gateway/restart.py:60-73` | new `*_marker_present()` helpers | canonical + alias |
| `qubit_runtime/gateway/restart.py:~288` | `is_gateway_supervisor_process` (exit-75 route) | s6 sentinel via `s6_supervised_child_marker_present`; external opt-in accepts both spellings |
| `qubit_runtime/gateway/restart.py:~315` | `is_supervised_gateway_launch` | generalized marker via `supervised_child_marker_present` |
| `qubit_runtime/qubit_cli/main.py:570-591` | `_apply_profile_override` | all three family members dual; docstring corrected to `QUBIT_*` |
| `qubit_runtime/qubit_cli/venv_sync.py:299-324` | `_supervised_child` (was QUBIT-only) | now canonical + alias for both generalized and s6 |
| `qubit_runtime/qubit_cli/dashboard_procs.py:204-206` | fixed-identity profile resolution | all three family members dual |
| `qubit_runtime/qubit_cli/gateway.py:4969-4971` | `_maybe_redirect_run_to_s6_supervision` | s6 sentinel dual (legacy s6 image accepted) |
| `qubit_runtime/qubit_cli/update_cmd_posix_pause.py:193` | `_supervisor_markers` (update pause) | s6 sentinel dual |
| `qubit_runtime/qubit_cli/gateway.py:4122` | `_running_under_gateway_supervisor` docstring | comment only (delegates to `is_gateway_supervisor_process`) |
| `qubit_runtime/tools/process_registry.py:312` | `_is_supervised_gateway_process` docstring | comment only (delegates to `is_supervised_gateway_launch`) |

## Deliberately NOT changed

| site | reason |
|---|---|
| `INVOCATION_ID` (`gateway.py:4629`, `restart.py` probe, `control_socket.py:98`, `shutdown_forensics.py`, `gateway_health_export.py:120`, `process_registry.py:440`, `update_cmd_posix_pause.py:209`) | Set by systemd, not by us; no brand. Consulted only for gateway commands intentionally (inherited by every descendant). Unchanged. |
| `XPC_SERVICE_NAME` | Set by launchd; not branded. |
| `HERMES_LAUNCHD_LABEL` (`gateway/restart.py:41`) and `HERMES_GATEWAY_DETACHED` (`gateway_windows.py:61`), `HERMES_GATEWAY_NO_SUPERVISE` (`gateway.py:4967`), `_HERMES_GATEWAY` (`process_registry.py:315`), `HERMES_UPDATE_POST_SWAP` (`dashboard_procs.py:197`) | **Out of scope of this defect** — different markers with no generated-file/installed-service counterpart in the same class, and this task is scoped to the supervised-marker family. They remain legacy-spelled brand leaks a separate sweep should take (they are internal process env, not installed-unit contracts). |
| `scripts/qubit_public_surface_scan.py:169` | Its list already names `HERMES_SUPERVISED_CHILD` as a *scanned public-surface token*; the scan's purpose is to detect legacy spells on surfaces. Left for the brand sweep; noting it so the next agent sees it. |
| `qubit_runtime/tests/**` other than the two listed below | Existing tests that scrub/delenv markers by the legacy name still work (the alias is still read). |

## Tests

New: `qubit_runtime/tests/gateway/test_supervised_marker_dual_read.py` — **22 cases**:

- `TestMarkerNamesAreCanonicalised` (3) — constants name canonical, alias tuple present.
- `TestReaderAcceptsBothSpellings` (5) — **the installed-service case**: a task/unit that exports
  only the legacy spelling is still a supervised launch; both spellings of the s6 sentinel and the
  external opt-in are recognised; the generalized marker is a launch and never a restart route.
- `TestApplyProfileOverrideReadsBothSpellings` (6) — a supervised child marked with **either**
  spelling keeps its fixed profile identity (the #74872 vector).
- `TestGeneratedLaunchersWriteCanonicalSpelling` (4) — systemd unit, launchd plist, Windows task env
  and s6 run script all carry the canonical name and no legacy name.
- `TestVenvSyncTailReadsBothSpellings` (1) — `s6_supervised_child_marker_present` dual.

Contract file (the direct contract of this change, touched here and flagged):
`qubit_runtime/tests/gateway/test_restart_service_detection.py::test_supervised_child_marker_is_a_launch_not_a_restart_route`
— updated from asserting `task_env["HERMES_SUPERVISED_CHILD"]` to
`task_env["QUBIT_SUPERVISED_CHILD"]`, plus a new loop asserting the legacy spelling is still a
supervised launch and still not a restart route. (This file lives in `tests/gateway/`, not
`tests/hermes_cli/`, so it does not collide with catchup-1's area.)

The `hermes_cli/test_apply_profile_override.py` contract file was **not modified** — it passes
as-is now (its 7 `QUBIT_` expectations were the contract this change satisfies).

### Falsification (red before, green after)

`gateway/restart.py` was stashed to the unmodified tree and the same test body (with the new
constants inlined so the module still collects) was run:

```
=== UNMODIFIED TREE ===
11 failed, 11 passed
FAILED ...TestReaderAcceptsBothSpellings::test_supervised_launch_recognises_either_spelling[QUBIT_SUPERVISED_CHILD]
FAILED ...TestReaderAcceptsBothSpellings::test_s6_sentinel_recognised_under_either_spelling[QUBIT_S6_SUPERVISED_CHILD]
FAILED ...TestReaderAcceptsBothSpellings::test_external_supervisor_opt_in_recognised_under_either_spelling[QUBIT_GATEWAY_EXTERNAL_SUPERVISOR]
FAILED ...TestApplyProfileOverrideReadsBothSpellings::test_supervised_child_skips_active_profile[QUBIT_SUPERVISED_CHILD]
FAILED ...TestGeneratedLaunchersWriteCanonicalSpelling::test_systemd_unit_writes_canonical_marker
FAILED ...TestGeneratedLaunchersWriteCanonicalSpelling::test_launchd_plist_writes_canonical_marker
FAILED ...TestGeneratedLaunchersWriteCanonicalSpelling::test_windows_task_env_writes_canonical_marker
FAILED ...TestGeneratedLaunchersWriteCanonicalSpelling::test_s6_run_script_writes_canonical_markers
(and 3 more)
```

After the change, the same file: **22 passed**. On the unmodified tree the new test file
cannot even import the new alias constants (`ImportError`), which is itself red evidence.

### Regression comparison vs base `6ee2adf0c`

Same test selection run on a clean `6ee2adf0c` worktree and on this branch; failure sets diffed:

- **fixed by this change**: 8 — the 4+4 `test_apply_profile_override.py` failures in both
  `tests/hermes_cli/` and `tests/qubit_cli/` (each mirrored).
- **new failures introduced**: none.
- Broad sweep `tests/gateway/ tests/qubit_cli/ tests/hermes_cli/` → identical failure set to base
  (one pre-existing collection error in `test_gateway_inline_bootstrap_identity.py`, unrelated).

## Verification commands

```
/home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python -m pytest \
  qubit_runtime/tests/gateway/test_supervised_marker_dual_read.py \
  qubit_runtime/tests/hermes_cli/test_apply_profile_override.py \
  -o addopts='' -q          # 52 passed
```
