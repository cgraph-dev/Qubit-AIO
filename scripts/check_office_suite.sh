#!/usr/bin/env bash
#
# scripts/check_office_suite.sh — determinism guard for the office test suite.
#
# WHY THIS EXISTS
# ---------------
# The office test suite reads real files under
#   frontend/qubit-app/plugins/qubit-office/*.js
# through a `node -e` subprocess. While several agents are editing those
# modules, a `pytest` invocation can observe a module mid-rewrite (truncated or
# half-written) and fail on assertions that have nothing to do with the test's
# subject. That is not a flaky test in the ordering sense — it is a test reading
# a file that is being concurrently replaced. The symptom is
# indistinguishable from flakiness: the same command passes and fails run to run.
#
# This script settles that question mechanically. It runs the full office suite
# three times with an identical, fixed file order and compares the pass/fail SET
# of each run. Two runs that disagree prove the suite is not deterministic in
# this environment, whatever the cause; a clean diff is evidence that (at least
# during this window) nothing was mutating the modules under test.
#
# EXIT CODES
#   0  every run produced the same pass/fail set
#   1  at least one run disagreed with another (non-determinism present)
#   2  setup failure (no venv, no office tests)
#
# NOTE ON KNOWN RED TESTS
#   `tests/test_office_tower_visual.py` carries two assertions that are INTENDED
#   to fail today (they document an unfixed `seatColour` collision — see the
#   docstring at the `test_a_room_of_distinct_slugs...` case). They fail in every
#   run, so they appear in each FAILED set identically and never trip this guard.
#   The guard tests DISAGREEMENT between runs, not greenness. Add `--expect-green`
#   if you also want a non-zero exit whenever any run is red.

set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || { echo "cannot cd to $REPO" >&2; exit 2; }

PY=".venv/bin/python"
[ -x "$PY" ] || { echo "missing $REPO/$PY (run from the repo venv)" >&2; exit 2; }

RUNS="${OFFICE_SUITE_RUNS:-3}"
EXPECT_GREEN=0
for a in "$@"; do
  case "$a" in
    --expect-green) EXPECT_GREEN=1 ;;
    *) echo "unknown arg: $a" >&2; exit 2 ;;
  esac
done

shopt -s nullglob
FILES=(tests/test_office*.py)
if [ "${#FILES[@]}" -eq 0 ]; then
  echo "no tests/test_office*.py found in $REPO" >&2; exit 2
fi

OUT="$(mktemp -d "${TMPDIR:-/tmp}/office_suite_XXXXXX")"
trap 'rm -rf "$OUT"' EXIT

echo "office suite determinism check"
echo "  repo   : $REPO"
echo "  files  : ${#FILES[@]}"
echo "  runs   : $RUNS (fixed file order)"
echo "  workdir: $OUT"
echo

# --- fixed-order runs ---------------------------------------------------------
i=1
while [ "$i" -le "$RUNS" ]; do
  "$PY" -m pytest "${FILES[@]}" -q \
        > "$OUT/raw_$i.txt" 2>&1
  rc=$?
  # normalise: keep only FAILED/ERROR node ids, strip the message after ' - '
  grep -E '^(FAILED|ERROR)' "$OUT/raw_$i.txt" \
        | sed -E 's/[[:space:]]+-[[:space:]].*$//' | sort -u > "$OUT/fail_$i.txt"
  tail -n 1 "$OUT/raw_$i.txt" | sed -E 's/^[[:space:]]*//' > "$OUT/summary_$i.txt"
  echo "run $i (rc=$rc): $(cat "$OUT/summary_$i.txt")"
  i=$((i + 1))
done

# --- one order-varied run: reversed file order --------------------------------
REV=()
j=$(( ${#FILES[@]} - 1 ))
while [ "$j" -ge 0 ]; do REV+=("${FILES[$j]}"); j=$((j - 1)); done
"$PY" -m pytest "${REV[@]}" -q > "$OUT/raw_rev.txt" 2>&1
rc=$?
grep -E '^(FAILED|ERROR)' "$OUT/raw_rev.txt" \
      | sed -E 's/[[:space:]]+-[[:space:]].*$//' | sort -u > "$OUT/fail_rev.txt"
tail -n 1 "$OUT/raw_rev.txt" | sed -E 's/^[[:space:]]*//' > "$OUT/summary_rev.txt"
echo "run rev (rc=$rc): $(cat "$OUT/summary_rev.txt")"
echo

# --- compare ------------------------------------------------------------------
status=0
note() { echo "$@"; }

base="$OUT/fail_1.txt"
i=2
while [ "$i" -le "$RUNS" ]; do
  if diff -u "$base" "$OUT/fail_$i.txt" > "$OUT/d_$i.txt"; then
    note "  run 1 == run $i  (same pass/fail set)"
  else
    note "  run 1 != run $i  DIFFERS:"
    sed -n '3,$p' "$OUT/d_$i.txt" | sed 's/^/    /'
    status=1
  fi
  i=$((i + 1))
done

if diff -u "$base" "$OUT/fail_rev.txt" > "$OUT/d_rev.txt"; then
  note "  run 1 == run rev (order-independent)"
else
  note "  run 1 != run rev  ORDER DEPENDENCE:"
  sed -n '3,$p' "$OUT/d_rev.txt" | sed 's/^/    /'
  status=1
fi

# --- optional greenness gate --------------------------------------------------
if [ "$EXPECT_GREEN" -eq 1 ] && grep -qE '^(FAILED|ERROR)' "$OUT/raw_1.txt"; then
  note "  --expect-green set and run 1 has failures"
  status=1
fi

echo
if [ "$status" -eq 0 ]; then
  echo "PASS: office suite is deterministic across $RUNS fixed runs + 1 reversed run"
else
  echo "FAIL: office suite produced disagreeing runs (see diffs above)."
  echo "      Most likely cause: an agent is rewriting a module under"
  echo "      frontend/qubit-app/plugins/qubit-office/*.js while the tests run."
  echo "      Re-run when the writers are quiet; if it still disagrees, it is a"
  echo "      genuine ordering/hidden-state bug in tests/test_office*.py."
fi
exit "$status"
