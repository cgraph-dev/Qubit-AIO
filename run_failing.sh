#!/usr/bin/env bash
# Run each failing file, save raw output to /tmp/catchup-ag7/errs/<name>.txt
WT=/tmp/catchup-ag7
PY=/home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python
mkdir -p "$WT/errs"
cd "$WT/qubit_runtime" || exit 1
while read -r f; do
  [ -z "$f" ] && continue
  name=$(basename "$f" .py)
  env -i PATH="$PATH" HOME="$HOME" TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONHASHSEED=0 PYTHONUTF8=1 \
    PYTHONPATH="$WT/qubit_runtime" \
    "$PY" -m pytest "tests/qubit_cli/$name.py" -q -p no:cacheprovider -o addopts='' --tb=long --no-header \
    > "$WT/errs/$name.txt" 2>&1
done < "$WT/failing_files.txt"
echo ALLDONE
