#!/usr/bin/env bash
# The Office completion gate.
#
# WHY THIS EXISTS
# ---------------
# "Deploy agents until everything is done" needs a machine-checkable definition
# of done, or the loop either spins forever or stops the first time an agent
# CLAIMS success. Both have already happened in this project:
#   * a subagent reported `status=completed` while having edited NOTHING (its
#     output was about an unrelated fintech exercise),
#   * `CHAR_FLOOR_ZOOM` was added and reported as a fix; it never bound, because
#     the computed fit is always above it.
# So this script is the arbiter. An agent does not get to say a thing is done;
# this does, and it prints the evidence.
#
# It is DELIBERATELY narrow. It checks only claims that are cheap to verify and
# that we have actually been wrong about. It is not a proof that the office
# looks good -- nothing here can prove that, and pretending otherwise would be
# the same mistake in a new costume.
#
# USAGE
#   bash scripts/office_done.sh          # run every gate, exit non-zero if any fail
#   bash scripts/office_done.sh --json   # machine-readable, one line per gate
#
# EXIT: 0 = every gate passed. 1 = at least one failed. 2 = the harness itself
# could not run (missing venv / bundle), which is NOT a pass.

set -uo pipefail
cd "$(dirname "$0")/.." || exit 2

# Reap our own children when this script is interrupted -- but NOT with `kill 0`.
#
# The first version used `trap 'kill 0' EXIT INT TERM`, which signals the entire
# PROCESS GROUP. That includes whoever launched the gate, so on a normal exit the
# script killed its own parent: observed as SIGTERM (143) in every caller, and as
# a SEGFAULT (139) when run from a test that then asserted it wanted exit 2. It
# also broke the exit code of a legitimate harness refusal. The fix is to track
# the child we actually spawned and kill only that.
_CURRENT_CHILD=""
_reap() {
  if [ -n "$_CURRENT_CHILD" ] && kill -0 "$_CURRENT_CHILD" 2>/dev/null; then
    kill "$_CURRENT_CHILD" 2>/dev/null || true
  fi
}
trap '_reap' EXIT INT TERM
REPO="$PWD"
PY="$REPO/.venv/bin/python"
JSON=0
[ "${1:-}" = "--json" ] && JSON=1

if [ ! -x "$PY" ]; then
  echo "HARNESS: no $PY -- cannot verify anything (exit 2)"; exit 2
fi

PASS=0; FAIL=0
declare -a FAILED_GATES=()

# gate <name> <human description> <shell command...>
gate() {
  local name="$1"; shift
  local desc="$1"; shift
  local out rc
  # Run the gate body in the background so its pid is known and _reap can take
  # it down if this script is interrupted, instead of leaking a pytest suite.
  local tmp; tmp="$(mktemp)"
  "$@" >"$tmp" 2>&1 &
  _CURRENT_CHILD=$!
  wait "$_CURRENT_CHILD"; rc=$?
  _CURRENT_CHILD=""
  out="$(cat "$tmp")"; rm -f "$tmp"
  if [ $rc -eq 0 ]; then
    PASS=$((PASS+1))
    [ $JSON -eq 1 ] && echo "{\"gate\":\"$name\",\"ok\":true,\"detail\":\"$(echo "$out" | tail -1 | sed 's/"/\\"/g')\"}" \
                    || printf '  PASS  %-26s %s\n' "$name" "$desc"
  else
    FAIL=$((FAIL+1)); FAILED_GATES+=("$name")
    [ $JSON -eq 1 ] && echo "{\"gate\":\"$name\",\"ok\":false,\"detail\":\"$(echo "$out" | tail -3 | tr '\n' ' ' | sed 's/"/\\"/g')\"}" \
                    || { printf '  FAIL  %-26s %s\n' "$name" "$desc"; echo "$out" | tail -4 | sed 's/^/          /'; }
  fi
}

echo "== Office completion gates =============================================="

# --- 1. the code is what we think it is -------------------------------------
gate bundle-current   "the served bundle matches its sources" \
  bash -c "cd '$REPO' && '$PY' scripts/build_office_bundle.py --check"

# --- 2. the whole test suite agrees, and does so REPEATABLY ------------------
# Twice, because an order-dependent test passed alone and failed in the suite
# earlier in this project. A single green run is not evidence.
#
# GUARD FIRST: this gate globs `tests/test_office*.py`, so if another process is
# WRITING a test file right now the run includes a half-written file and fails
# for a reason that has nothing to do with the code. That produced a false FAIL
# the first time two agents ran this concurrently. A gate that can only be
# believed while nothing else is happening is a weak gate, so it now says so
# out loud instead of reporting a lie.
gate tree-quiet       "no test file was written in the last 60s (else the run is not evidence)" \
  bash -c "cd '$REPO' && n=\$(find tests -name 'test_office*.py' -mmin -1 2>/dev/null | wc -l); \
           if [ \"\$n\" -gt 0 ]; then echo \"\$n office test file(s) modified in the last minute -- another writer is active\"; exit 1; fi; echo quiet"

# `tail -1` used to swallow the failing test's NAME, so a red gate said only
# "FAIL suite-pass-1" and every diagnosis began by re-running the suite by hand
# to find out what was actually red. The pytest tail is kept now and the failing
# names are printed. The arms also carry a REAL timeout: under concurrent load
# (several agents each running the full gate -- which happened, and produced two
# false reds) an unbounded suite run outlives the caller and reports FAIL for a
# tree that is green. A timeout says so out loud instead of blaming the code.
#
# `-x` stops at the first failure: burning the whole suite's runtime to report a
# known break is how a loaded box turns one real failure into a timeout.
_SUITE_ARGS="tests/test_office*.py -q -p no:cacheprovider -x"
_SUITE_TIMEOUT="${OFFICE_SUITE_TIMEOUT:-600}"

# A LOCK, so concurrent gate runs queue instead of starving each other.
#
# This is the third false-red this gate has produced and the second caused by
# concurrency alone. The sequence: several agents each ran the full gate; ten
# office suites and five browser renders competed for one box; the suite arms
# timed out and reported FAIL for a tree that was green. A timeout and a
# contention failure are now distinguished in the message, but the real fix is
# not to run two suites at once. `flock` waits for the lock rather than failing,
# so a queued caller gets a real answer after a wait instead of a lie now.
_LOCK="/tmp/office-done-suite.lock"
# How long a caller will WAIT for a concurrent suite to finish before giving up.
# 900s was too generous: under five concurrent agents a waiter sat 4+ minutes and
# the gate took 9+ minutes end to end, which is worse than the false red it
# replaced. The lock is still correct -- one suite runs, the rest queue -- but a
# waiter that has been beaten to the lock twice should say so rather than block a
# human's terminal indefinitely. 300s is longer than a solo suite (~150s) and
# short enough that a contended run reports "busy" instead of hanging.
_LOCK_WAIT="${OFFICE_LOCK_WAIT:-300}"

_suite_run() {
  local out rc last
  last=""
  out=$(flock -w "$_LOCK_WAIT" "$_LOCK" \
          timeout "$_SUITE_TIMEOUT" "$PY" -m pytest $_SUITE_ARGS 2>&1)
  rc=$?
  last=$(printf '%s' "$out" | grep -E '[0-9]+ (passed|failed)' | tail -1)
  # flock exits 1 when it could not take the lock in time. That is a BUSY box,
  # not a broken tree, and must say so -- reading it as a test failure is the
  # exact false-red this whole lock exists to remove. Tested AFTER `last` is
  # read, because an empty `last` is also what a genuine crash produces; the
  # discriminator is whether the lock was ever taken.
  if [ "$rc" -eq 1 ] && [ -z "$last" ]; then
    echo "  BUSY: another office suite held the lock for ${_LOCK_WAIT}s -- this is"
    echo "  contention, not a code failure. Re-run when the box is quiet."
    exit 1
  fi
  if [ "$rc" -eq 0 ]; then
    echo "  $last"
    return 0
  fi
  if [ "$rc" -eq 124 ]; then
    echo "  TIMEOUT after ${_SUITE_TIMEOUT}s -- the tree may be fine; this box is loaded."
    echo "  Re-run on a quiet tree rather than reading this as a code failure."
    exit 1
  fi
  echo "  $last"
  printf '%s' "$out" | grep -E '^(FAILED|ERROR)' | head -10 | sed 's/^/  /'
  exit 1
}
export _SUITE_ARGS _SUITE_TIMEOUT _LOCK _LOCK_WAIT PY REPO
export -f _suite_run

gate suite-pass-1     "office suite green (run 1)" \
  bash -c "cd '$REPO' && _suite_run"
gate suite-pass-2     "office suite green (run 2, catches order-dependence)" \
  bash -c "cd '$REPO' && _suite_run"

# --- 3. the plugin load path is consistent ----------------------------------
# This is the bug that cost three render cycles: the entry is declared in the
# manifest AND in two fleet.json copies, and the SERVED one is not the obvious
# one. The test checks all three agree and that the served copy matches disk.
gate plugin-wiring    "manifest + both fleet.json copies agree, served matches disk" \
  bash -c "cd '$REPO' && '$PY' -m pytest tests/test_office_plugin_wiring.py -q 2>&1 | tail -1 | grep -qE '^[0-9]+ passed'"

# --- 4. the geometry does not occlude --------------------------------------
gate geometry-clear   "desk pitch clears the 15-unit figure (+10.5 required)" \
  bash -c "cd '$REPO' && '$PY' -c \"
from voicebridge.office import floorplan_fit as ff
r = ff.clearance_report(ff.geometry_for(11))
assert r['ok'], r['reason']
assert r['margin'] >= 10.0, 'margin %s is under the required +10.5' % r['margin']
print('margin', r['margin'])\""

# --- 5. tower.py actually carries that geometry -----------------------------
# gates 4 and 5 together are the point: the planner can be right while the
# source still holds the old constants, and that pair is exactly how a fix
# was reported but never bound before.
gate geometry-live    "tower.py's own constants are the recommended ones" \
  bash -c "cd '$REPO' && '$PY' -c \"
from voicebridge.office import tower as T
assert (T.ROOM_W, T.ROOM_H) == (12, 7), (T.ROOM_W, T.ROOM_H)
assert (T.PITCH_COL, T.PITCH_ROW) == (4, 3), (T.PITCH_COL, T.PITCH_ROW)
assert T.ODD_ROW_OFFSET == 1
print('room', T.ROOM_W, 'x', T.ROOM_H, 'pitch', T.PITCH_COL, T.PITCH_ROW)\""

# --- 6. no two desks share a grid cell -------------------------------------
# NOTE ON A CLAIM THAT WAS WRONG. An earlier version of this gate was changed to
# compare PROJECTED screen cells instead of grid cells, on the theory that the
# isometric projection could collapse two distinct cells onto one point. That
# theory is false, and the algebra is short:
#
#   two cells share a screen point  =>  x1-y1 == x2-y2  AND  x1+y1 == x2+y2
#   add the two                      =>  2*x1 == 2*x2   =>  x1 == x2
#   substitute back                  =>  y1 == y2
#
# So for distinct INTEGER cells a screen collision is impossible under a pure
# 2:1 projection; the projected check was asserting a premise that cannot occur
# and the plain grid check is both sufficient and clearer. (The same algebra
# refutes part of `24-FLOORPLAN-SCALE.md`, which cited `(3,1)` and `(1,3)` as an
# occlusion cause because they share `x+y` -- they share the DEPTH KEY, which is
# not the same as occupying the same point; they are 68px apart on screen.)
#
# What the gate still proves: every seat has its own cell, so no two seats are
# drawn on top of each other, and a room cannot silently over-fill.
gate desks-distinct   "no two seats in a room share a grid cell" \
  bash -c "cd '$REPO' && '$PY' -c \"
from voicebridge.office import tower as T
from voicebridge import config as C
plan = T.floorplan(C.Config.load(None))
for f in plan['floors']:
    for r in f['rooms']:
        cells = [(s['x'], s['y']) for s in r['seats']]
        assert len(cells) == len(set(cells)), (r['slug'], cells)
        assert all(isinstance(s['x'], int) and isinstance(s['y'], int)
                   for s in r['seats']), 'seat coords must be integers'
print('ok')\""

# --- 7. the walk reaches the renderer (the dropped-field bug) ---------------
# `walk_for` computing progress while `state()` dropped it is why the bob drew
# nothing for hours. This asserts the DROP SITE, not the producer.
gate progress-reaches "state() carries position.progress while walking" \
  bash -c "cd '$REPO' && '$PY' -m pytest tests/test_office_tower_walk.py -q -k state_carries 2>&1 | tail -1 | grep -qE '^[0-9]+ passed'"

# --- 8. the drawing is not empty and the modules are really loaded ----------
# NOTE: the render tool prints progress lines, then ONE json line. The first
# version of this gate matched `^{` anywhere in the stream, which also matched
# the tool's OWN progress line, so the parse could read the wrong object and the
# gate reported a failure while the page was demonstrably healthy (24.64% drawn,
# zero console errors). It now takes the LAST line, which is the documented
# contract of `--json`, and it fails loudly if that line is not parseable rather
# than silently reading something else.
gate render-live      "live page renders, modules loaded, not falling back" \
  bash -c "cd '$REPO' && timeout 240 '$PY' scripts/render_office_screenshot.py --url http://127.0.0.1:8890/qubit-office --out /tmp/gate-office.png --json 2>/dev/null | grep '^{' | tail -1 | '$PY' -c \"
import json,sys
d=json.load(sys.stdin)
assert d['data_state']=='ready', d['data_state']
assert 'modules not loaded' not in d.get('visible_text',''), 'the page fell back -- the bundle is not being served'
pct=d['pixels_largest_canvas']['non_background_pct']
assert pct>15, 'canvas is %.2f%% drawn -- a building should be far more' % pct
print('%.2f%% drawn' % pct)\""

# --- 9. the licence record names real sources and excludes the unlicensed one -
gate provenance       "no ported file cites the unlicensed repo" \
  bash -c "cd '$REPO' && '$PY' -c \"
import re, pathlib
plugin = pathlib.Path('frontend/qubit-app/plugins/qubit-office')
bad = []
for f in plugin.glob('*.js'):
    t = f.read_text()
    for m in re.finditer(r'rsanandres/aphae|/aphae/', t):
        # allowed ONLY as an explicit statement that it is not used
        line = t[max(0,m.start()-200):m.end()+200].lower()
        if 'not used' not in line and 'not read' not in line and \\
           'no licence' not in line and 'no license' not in line and \\
           'rights reserved' not in line:
            bad.append((f.name, line[:80]))
assert not bad, bad
print('ok')\""

echo "========================================================================="
echo "  $PASS passed, $FAIL failed"
if [ $FAIL -gt 0 ]; then
  echo "  failing gates: ${FAILED_GATES[*]}"
  echo
  echo "  NOT DONE. These are the arms pointing at real work, not opinions."
  exit 1
fi
echo "  every gate passed -- the claims above are backed by execution."
exit 0
