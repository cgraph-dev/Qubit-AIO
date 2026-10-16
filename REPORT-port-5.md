# REPORT — port-5 (eval harnesses)

Worked example for `PORT-METHOD.md`. Branch `port/chunk-5`, worktree
`/tmp/port-5`, base `9c2f782e1`.

## Outcome

| | |
|---|---|
| Upstream ref read | `origin/main` = `53bec2a311979ad6f77580ba491eca261ab988e0` (2026-10-16) |
| Vendored pin (provenance, untouched) | `517b5e10febd619ce30bb22580e29b160266eb43` |
| Chunk files | **5 / 5 ported**, 0 shims, 0 dep-missing |
| Companions ported | 3 (`cases.json`, 2 × `README.md`) |
| Commits | `df9007e7e` (5 files), `9c8ea8cb5` (companions) |

All 5 upstream files were line-for-line the same length after porting; only brand
lines changed (0 / 2 / 4 / 8 / 10 changed lines respectively). Nothing was
reflowed or stubbed.

## Per-file

| upstream (`origin/main`) | → qubit_runtime | lines | status | imports | brand diff |
|---|---|---|---|---|---|
| `evals/reasoning_replay/harness.py` | `qubit_runtime/evals/reasoning_replay/harness.py` | 995 | ported | clean | 4 |
| `evals/shared_gateway/normal_boot.py` | `qubit_runtime/evals/shared_gateway/normal_boot.py` | 100 | ported | clean | 2 |
| `evals/shared_gateway/search_readonly_ab.py` | `qubit_runtime/evals/shared_gateway/search_readonly_ab.py` | 98 | ported | clean | 10 |
| `evals/shared_gateway/session_readers_cli_ab.py` | `qubit_runtime/evals/shared_gateway/session_readers_cli_ab.py` | 64 | ported | top-level-script¹ | 8 |
| `evals/shared_gateway/verify_receipt.py` | `qubit_runtime/evals/shared_gateway/verify_receipt.py` | 63 | ported | clean | 0 (verbatim) |

¹ `session_readers_cli_ab.py` runs `argparse` at module top level (no
`if __name__` guard), so `import` executes it and exits on missing argv — a
property of the file, not a port defect. Proven instead with `py_compile`,
`--help`, and an `importlib` load that reached the real `git rev-parse` call
(`CalledProcessError` from cwd, i.e. the code body ran).

## Brand changes applied (per `PORT-METHOD.md` §3)

- `HERMES_HOME` → `QUBIT_HOME` (canonical reader `get_process_qubit_home` reads
  **only** `QUBIT_HOME`; 102 files under `qubit_runtime/evals/` already write it).
- `Path.home() / ".hermes"` and bare `.hermes` fixture dirs → `.qubit`.
- `hermes_state` → `qubit_state`; `hermes_cli.main` → `qubit_cli.main`.
- tempfile prefixes `hermes-reader-*` → `qubit-reader-*`.
- `verify_receipt.py` and `cases.json` had **zero** brand tokens — ported verbatim.

## Proof (real runs)

```sh
PY=/home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python
cd /tmp/port-5/qubit_runtime
PYTHONPATH=/tmp/port-5/qubit_runtime "$PY" -c 'import evals.shared_gateway.verify_receipt'   # OK
PYTHONPATH=/tmp/port-5/qubit_runtime "$PY" -c 'import evals.shared_gateway.normal_boot'     # OK
PYTHONPATH=/tmp/port-5/qubit_runtime "$PY" -c 'import evals.shared_gateway.search_readonly_ab'  # OK
PYTHONPATH=/tmp/port-5/qubit_runtime "$PY" -c 'import evals.reasoning_replay.harness'       # OK
```

End-to-end (not just import): `verify_receipt.py` against the ported `cases.json`
—

- complete receipt (all 47 cases named): **`PASS: shared gateway receipt coverage`**
- same receipt with one case dropped: **`FAIL: case mismatch: missing=['cron-policy-parity']`, exit 1**

That proves the companion `cases.json` resolves and the checker still
discriminates.

## Obstacles hit (named, not hidden)

1. **Companion data is an invisible dependency.** `verify_receipt.py` defaults
   `--manifest` to `cases.json` beside it. It is not in the `.py` 299-list;
   without it the checker cannot run. Ported with the code.
2. **Top-level-script files can't be "import-proved".** See ¹ above.
3. **`HERMES_HOME` is not fully dead in the tree** — ~30 production files still
   name it as prose, docstrings, or a *legacy* read
   (`qubit_cli/update_lock.py:100`). Porting with `QUBIT_HOME` is correct; I did
   **not** touch those, because a partial rename is the documented hazard
   (`docs/REBRAND-SWEEP-PLAN.md`: 152 dual-spelled symbols).
4. **Shim rule demonstrated on a sibling chunk's file** (method validation):
   `gateway/session_admission.py` (8 lines) re-exports `admission_fingerprint`
   from `hermes_state_keys`, which is **itself absent** (line 242 of
   `/tmp/prod-files.txt`; `grep -rn admission_fingerprint qubit_runtime/` → none).
   Verdict: `shim-skipped`, dependency `hermes_state_keys.py`, ordering noted.
   Not part of my chunk — recorded because it is the cleanest worked example.

## Dependencies missing

None for this chunk's 5 files. All their imports resolve in-tree or are stdlib.

## Machine-readable

`PORTED.json` (chunk files + companions, per-file status/deps/import verdict).
