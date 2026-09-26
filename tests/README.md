# Tests

Hermetic gates for Qubit. Run from the repo root with the project venv:

```bash
.venv/bin/python -m pytest tests/
```

`pytest.ini` excludes the `live`, `dashboard`, `monitor`, and `ui` markers by
default; `qubit_runtime/` and `skills/` are not recursed into by these gates.

## Temp/disk policy

`tests/conftest.py` (with the pure logic in `tests/tmp_policy.py`) makes a test
run unable to fill `/tmp`:

- **Bounded basetemp.** If `--basetemp` is not given, the session creates one
  per-run directory at `<tmp-root>/qubit-pytest-<pid>-<token>` and removes it on
  exit. `TMPDIR` is pointed at that directory, so scratch goes there instead of
  accumulating in `/tmp/pytest-of-*`.
- **Temp root override.** Set `QUBIT_PYTEST_TMPROOT` to steer runs onto a
  larger/quota-managed filesystem.
- **Free-space guard.** Before any test runs, the session checks free space on
  the filesystem behind the temp root and aborts with a clear message (pytest
  exit code 4) when it is below the floor. Configure with
  `--tmp-free-min-mb` (default 2048 MiB). `--tmp-keep` preserves the per-run
  directory for debugging.

```bash
.venv/bin/python -m pytest tests/ --tmp-free-min-mb=4096
QUBIT_PYTEST_TMPROOT=/mnt/scratch .venv/bin/python -m pytest tests/
.venv/bin/python -m pytest tests/ --tmp-keep
```

The policy itself is covered by `tests/test_tmpdir_policy.py`.

## Reclaiming space after a failed run

```bash
scripts/tmp_reclaim.sh              # disposable /tmp scratch only
scripts/tmp_reclaim.sh --aggressive
```

`scripts/tmp_reclaim.sh` removes only regenerable scratch under `/tmp` (pytest
temp roots, throwaway `HOME`/`QUBIT_HOME` probe dirs, seeded reference clones,
audit logs). It never touches the live repo, `~/.hermes`, or
`~/.local/state`, and is safe to run repeatedly.
