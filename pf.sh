#!/usr/bin/env bash
# run one test file, cleanly
WT=/tmp/catchup-ag7
PY=/home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python
cd "$WT/qubit_runtime" || exit 1
env -i PATH="$PATH" HOME="$HOME" TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONHASHSEED=0 PYTHONUTF8=1 \
  PYTHONPATH="$WT/qubit_runtime" \
  "$PY" -m pytest "tests/hermes_cli/$1" -q -p no:cacheprovider -o addopts='' "${@:2}" 2>&1
