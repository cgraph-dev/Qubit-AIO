# Local CI — a deterministic local reproduction of the hosted jobs

**Status:** operational runbook for `scripts/run_local_ci.py`, added on branch
`fix5/ci` (base `b257b1b08`). This is **not** GitHub-hosted execution, not a release
approval, and not a `VERIFIED` verdict. The hosted workflows in
`.github/workflows/` remain the release authority; this runner exists because the
repository's hosted jobs are blocked account-side (see `docs/STATUS.md`).

## What it is, and what it is not

The runner reproduces the *actionable* jobs from `ci.yml`, `release.yml`,
`release-install.yml`, `install-e2e.yml` and `bootstrap-installer.yml` on the
machine that owns the checkout, with typed outcomes and an explicit local
header in every report:

```json
{"execution": "local", "hosted_equivalent": false,
 "claim": "local reproduction ... not GitHub-hosted execution"}
```

It is not equivalent to the hosted runner in these ways:

| Hosted CI | Local runner |
|---|---|
| GitHub-hosted `ubuntu-latest`, Python 3.12, Node 22 | whatever the host has (the report records both and the pinned values) |
| `actionlint` pinned as a digest Docker image | `actionlint` from `PATH`; missing is DEGRADED, never PASS |
| actions checkout/setup/upload-artifact | no GitHub Actions concepts, no artifact uploads |
| npm 10.9.8 for 3 lockfiles, 11.17.0 for the website | host npm; the website check is DEGRADED unless the host has >= 11.17.0 |
| `release.yml` creates a **draft** release | never creates, uploads, edits or publishes a release |
| no secrets on PR workflows | no secrets are read by any job |

## Commands

```bash
# catalog (no execution)
python3 scripts/run_local_ci.py --list
python3 scripts/run_local_ci.py --list --json

# the quick set: lint/security, lockfiles, runtime compile/smoke, bootstrap
python3 scripts/run_local_ci.py --job fast

# one job, machine-readable
python3 scripts/run_local_ci.py --job workflow-lint --json

# plan only, run nothing
python3 scripts/run_local_ci.py --job fast --dry-run

# all non-opt-in hosted jobs (includes the frontend suite and build)
python3 scripts/run_local_ci.py --job all

# opt-in jobs (build/install/network); refuse without --allow-opt-in
python3 scripts/run_local_ci.py --job release-qualification --allow-opt-in \
  --work-dir /tmp/qubit-local-ci-release --json
python3 scripts/run_local_ci.py --job install-e2e --allow-opt-in \
  --work-dir /tmp/qubit-local-ci-install --json

# opt-in selection still refuses unless the flag is present
python3 scripts/run_local_ci.py --job release-qualification   # exit 8, BLOCKED

# dirty-tree gate for compile/smoke
python3 scripts/run_local_ci.py --job runtime-smoke --require-clean
```

`--report PATH` writes the same JSON report to a file. `--json` prints only the
report on stdout; human progress is printed only without `--json`.

## Jobs

| Job | Opt-in | Reproduces | Notes |
|---|---|---|---|
| `workflow-lint` | no | `actionlint` + `.github/scripts/check_workflow_security.py` | missing `actionlint` is DEGRADED |
| `lockfiles` | no | `npm ci --dry-run` for the three 10.9.8 lockfiles and the website lockfile | website requires npm >= 11.17.0; otherwise DEGRADED |
| `runtime-smoke` | no | `compileall` of the runtime/voice surface, runtime test parse, root metadata smoke | `--require-clean` blocks on a dirty tree |
| `frontend` | no | `npm ci`, shared install, `typecheck`, `test`, `build` | host Node is recorded; the bundle is `frontend/qubit-dist` (git-ignored) |
| `bootstrap-tests` | no | `bash -n scripts/bootstrap.sh` + `tests/test_bootstrap_installer.py` | hermetic |
| `release-qualification` | yes | `release_qualify.py --build --install --reproduce --unsigned-ok` | never signs, never publishes; output under `--work-dir` |
| `install-e2e` | yes | dashboard build + `artifact_install_e2e.py --build --require-network` | throwaway venv/HOME; needs network and `uv` or the `build` module |

Groups: `fast` (lint, lockfiles, runtime-smoke, bootstrap-tests), `all` (every
job; opt-in entries still refuse without `--allow-opt-in`). The default with no
`--job` is the hosted `ci.yml` non-opt-in set (lint, lockfiles, runtime-smoke,
frontend, bootstrap-tests).

## Typed outcomes

Process exit codes match the submitter-facing contract:

| Exit | Verdict | Meaning |
|---|---|---|
| 0 | `PASS` / `PLANNED` | every selected job passed, or a dry-run plan |
| 2 | `FAIL` | a real defect in a job |
| 4 | `DEGRADED` | a pinned-tool mismatch or optional tool was unavailable |
| 8 | `BLOCKED` | unsafe request, unknown job, missing required tool, missing opt-in flag, or dirty tree under `--require-clean` |

Fail-closed rules:

- a job with a missing required tool (`npm`, `node`, `bash`, `pytest`, a wheel
  builder) is BLOCKED before anything runs;
- a forbidden argv prefix (`gh release *`, `twine upload`, `npm publish`,
  `git push`, `git tag`, `systemctl start|stop|restart|enable|disable`,
  `service ...`, `launchctl bootstrap|bootout|kickstart`, `supervisorctl ...`)
  is BLOCKED before the child process starts;
- the first failing/blocked step stops the rest of that job;
- nothing starts, stops, enables or restarts a service, and nothing publishes.

## Measured results (this checkout, 2026-11-05)

Commands were run from `/tmp/qubit-fix5-ci` on Python 3.14.7, Node 22.22.3,
npm 10.9.8, actionlint 1.7.12.

| Command | Result |
|---|---|
| `--job workflow-lint` | PASS (actionlint 1.7.12 + policy OK, 13 managed workflows) |
| `--job lockfiles` | DEGRADED — the three 10.9.8 lockfiles pass; the website lockfile is skipped because the host npm is 10.9.8, not >= 11.17.0 |
| `--job runtime-smoke` | PASS (compile, test parse, metadata; Python 3.14 vs hosted 3.12 recorded as a note) |
| `--job frontend` | PASS (install, typecheck, tests, build) |
| `--job bootstrap-tests` | PASS (`bash -n` + 31 hermetic tests) |
| `--job release-qualification --allow-opt-in` | PASS (`release_qualify.py`: build/install/reproduce, byte-identical wheel rebuild) |
| `--job install-e2e --allow-opt-in` | PASS (dashboard build + artifact install E2E; `qubit 0.0.2`) |
| `--job release-qualification` (no flag) | BLOCKED, exit 8, opt-in refusal |
| `--job does-not-exist` | BLOCKED, exit 8 |

`pytest -q tests/test_run_local_ci.py` → 20 passed (selection, refusal,
fail-closed, typed state mapping, safe job table, report header).

## Concrete hosted-workflow defect fixed

`ci.yml`'s `runtime-smoke` compile list named `qubit_runtime/qubit_state`, which
does not exist: it is a module family (`qubit_runtime/qubit_state*.py`).
`python -m compileall` prints `Can't list 'qubit_runtime/qubit_state'` and still
exits 0, so the hosted job silently skipped compiling all state modules. The
workflow now compiles `qubit_runtime/qubit_state*.py`, and the local runner
expands the same glob (with a BLOCKED guard if no modules are found).

```bash
python3 -m compileall -q qubit_runtime/qubit_state   # "Can't list ...", exit 0
python3 -m compileall -q qubit_runtime/qubit_state*.py  # real modules compiled
```

## Local-runner blockers (recorded honestly)

- **Website lockfile under npm 10.9.8.** `qubit_runtime/website/package.json`
  declares npm >= 11.17.0 with `engine-strict=true`; the local runner cannot
  honestly validate that lockfile with an older npm, so it marks the step
  DEGRADED. Install npm 11.17.0 to get a full PASS.
- **Host toolchain drift.** Python 3.14 vs hosted 3.12 and a non-`ubuntu-latest`
  OS are recorded in the report; compile-only drift is a note, not a pass claim
  for hosted parity.
- **Opt-in jobs are excluded by default.** They build/install/use the network
  and must be requested with `--allow-opt-in`.
