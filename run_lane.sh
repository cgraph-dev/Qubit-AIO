#!/usr/bin/env bash
# Baseline/fix-measurement runner for the tests/hermes_cli lane (agent/catchup-7).
set -uo pipefail
WT=/tmp/catchup-ag7
PY=/home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python
OUT=${OUT:-/tmp/catchup-ag7/lane-out.txt}
: > "$OUT"
cd "$WT/qubit_runtime" || exit 1
HERMES_PYTHON="$PY" exec env -i \
  PATH="$PATH" HOME="$HOME" \
  TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONHASHSEED=0 PYTHONUTF8=1 \
  HERMES_PYTHON="$PY" \
  PYTHONPATH="$WT/qubit_runtime" \
  "$PY" "$WT/qubit_runtime/scripts/run_tests_parallel.py" \
  --jobs "${JOBS:-6}" --file-timeout "${FILE_TIMEOUT:-600}" \
  tests/hermes_cli "$@" 2>&1 | tee "$OUT"
