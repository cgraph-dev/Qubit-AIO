# Warnings classification (Packet 6B release cleanup)

Record of the 382 warnings emitted by the full test suite, classified by root
cause, with the action taken for each. No warning is blanket-suppressed.

## Summary

| # | Source | Count | Root cause | Class | Action |
|---|---|---|---|---|---|
| 1 | `sounddevice.py:2795` (dep, `tests/test_mic_health.py` + `tests/test_steal_list_strict.py`) | 72 | sounddevice 0.5.6 sets `data.shape = -1, channels`, an API deprecated in NumPy 2.5 | dependency-only | documented (see below); 0.5.6 is the latest release, no upgrade available |
| 2 | `multiprocessing/popen_fork.py:66` (`tests/test_packet3a.py`) | 309 | `voicebridge/broker.py` deliberately forks (`get_context("fork")`) while the process is multi-threaded (the full pytest run is threaded) | our code (design) | documented with the exact fix (Packet 3A follow-up); not silently changed |
| 3 | `tests/test_steal_list_strict.py:270` (`[g02]`) | 1 | our live-latency test called `warnings.warn` to surface measured samples | our test code | FIXED — converted to a stderr print; the info is still surfaced |

Total: 382 before cleanup → **381 after fixing #3** (both remaining classes are
documented, not suppressed).

## 1. sounddevice / NumPy 2.5 deprecation (72) — dependency-only

`sounddevice` 0.5.6 (the latest release) assigns `data.shape = -1, channels` at
`site-packages/sounddevice.py:2795`. NumPy 2.5 deprecated in-place shape
assignment. The warning is raised inside the installed package when tests
initialize/query audio devices — not in bridge code.

- **Upgrade not available:** `pip index versions sounddevice` shows 0.5.6 is
  the newest.
- **Documented alternative** (not applied — avoids an unmeasured dependency
  change): pin `numpy<2.5` (e.g. 2.4.x) so the deprecation does not fire.
- No action changes production audio code or configuration.

## 2. multiprocessing fork in a threaded process (309) — our code (design)

`voicebridge/broker.py::_run_callable` runs a trusted in-process tool in a
killable child via `multiprocessing.get_context("fork")`. Forking a process
that already has threads risks child deadlocks, so CPython 3.12 emits a
DeprecationWarning. It appears once when the process is single-threaded and
309 times in the full suite because the pytest process is multi-threaded by the
time `test_packet3a` runs.

- This is a **real production concern** (the bridge is multi-threaded), not a
  test artifact.
- **Safe fix identified (Packet 3A follow-up):** move the child target to a
  top-level, picklable function and switch to `forkserver`/`spawn` context. It
  is NOT done here because the current target is a closure and the registered
  tool callables are closures/lambdas — a change requires a 3A-scope refactor
  and risks altering broker behavior. Not silently weakened.
- **Not suppressed:** suppressing it would hide a genuine deadlock risk.

## 3. [g02] test warning (1) — our test code, FIXED

`tests/test_steal_list_strict.py::test_g02_warm_latency_within_budget` used
`warnings.warn(...)` to surface measured warm-latency samples. Replaced with a
stderr `print(...)`. The measured samples are still visible; no pytest warning
is emitted. The fix lives in the working tree (the file is untracked
steal-list test content and is not part of the Packet 6B commit).