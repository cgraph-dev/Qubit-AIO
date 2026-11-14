#!/usr/bin/env bash
# Phase 4 acceptance gate: workforce lanes, threads, delegation, capability
# runtime, safety/kill switch, memory projections and the operator timeline.
#
# Requires accepted Phase 0/1/2/3 manifests. Hermetic: no live services/state,
# credentials or network. UI tests use the main checkout node_modules through a
# temporary symlink that is removed afterwards.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PY="${QUBIT_ACCEPTANCE_PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x "$HOME/venvs/qubit314/bin/python" ]; then
    PY="$HOME/venvs/qubit314/bin/python"
  elif [ -x "$ROOT/.venv/bin/python" ]; then
    PY="$ROOT/.venv/bin/python"
  else
    PY=python3
  fi
fi
NODE_MODULES="${QUBIT_OFFICE_NODE_MODULES:-$HOME/Projects/Repos/Qubit/frontend/qubit-web/node_modules}"

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export TMPDIR="${TMPDIR:-/tmp}"
WORK="$(mktemp -d "$TMPDIR/qoffice-phase4-acceptance.XXXXXX")"
RESULTS="$WORK/results"
LOGS="$WORK/logs"
mkdir -p "$RESULTS" "$LOGS"
cleanup() {
  rm -f "$ROOT/frontend/qubit-web/node_modules"
  rm -rf "$WORK"
}
trap cleanup EXIT

FAILED=0

record_result() {
  local id="$1" code="$2" evidence_csv="$3" command="$4"
  "$PY" - "$id" "$code" "$evidence_csv" "$command" "$RESULTS/$id.json" <<'PY'
import json
import sys

task_id, code, evidence, command, out = sys.argv[1:6]
json.dump({"task_id": task_id, "command": command, "exit_code": int(code),
           "evidence": [p for p in evidence.split(",") if p]},
          open(out, "w", encoding="utf-8"), indent=2, sort_keys=True)
PY
}

run_task() {
  local id="$1" evidence_csv="$2"
  shift 2
  echo "=== $id: $* ==="
  local code=0
  set +e
  "$@" >"$LOGS/$id.out" 2>"$LOGS/$id.err"
  code=$?
  set -e
  tail -n 6 "$LOGS/$id.out" || true
  if [ "$code" -ne 0 ]; then
    echo "--- $id stderr (tail) ---" >&2
    tail -n 30 "$LOGS/$id.err" >&2 || true
    FAILED=1
  fi
  record_result "$id" "$code" "$evidence_csv" "$*"
}

echo "=== earlier phase prerequisites ==="
set +e
for phase in 0 1 2 3; do
  "$PY" scripts/verify_evidence.py "evidence/phase-$phase/manifest.json" \
    >"$LOGS/phase$phase.out" 2>&1
  code=$?
  tail -n 2 "$LOGS/phase$phase.out" || true
  [ "$code" -eq 0 ] || FAILED=1
done
set -e

run_task P4-01 "evidence/phase-4/P4-01-workforce.json" \
  "$PY" scripts/verify_workforce.py --json
run_task P4-01-tests "evidence/phase-4/P4-01-workforce.json" \
  "$PY" -m pytest -q tests/phase4_workforce
run_task P4-02 "evidence/phase-4/P4-02-threads.json,evidence/phase-4/P4-03-delegation.json" \
  "$PY" scripts/verify_threads_delegation.py --json
run_task P4-02-tests "evidence/phase-4/P4-02-threads.json,evidence/phase-4/P4-03-delegation.json" \
  "$PY" -m pytest -q tests/phase4_threads_delegation
run_task P4-04 "evidence/phase-4/P4-04-capability-runtime.json,evidence/phase-4/P4-06-safety.json" \
  "$PY" scripts/verify_runtime_safety.py --json
run_task P4-04-tests "evidence/phase-4/P4-04-capability-runtime.json,evidence/phase-4/P4-06-safety.json" \
  "$PY" -m pytest -q tests/phase4_runtime_safety
run_task P4-05 "evidence/phase-4/P4-05-memory-projections.json" \
  "$PY" scripts/verify_memory_projections.py --json
run_task P4-05-tests "evidence/phase-4/P4-05-memory-projections.json" \
  "$PY" -m pytest -q tests/phase4_memory_projection
run_task P4-07 "evidence/phase-4/P4-07-operator-timeline.json" \
  "$PY" scripts/verify_operator_timeline.py --json
run_task P4-07-tests "evidence/phase-4/P4-07-operator-timeline.json" \
  "$PY" -m pytest -q tests/phase4_operator_ui tests/test_business_office_bridge.py

if [ -d "$NODE_MODULES" ]; then
  echo "=== Phase 4 UI Vitest ==="
  set +e
  ln -sfn "$NODE_MODULES" frontend/qubit-web/node_modules
  (
    cd frontend/qubit-web
    npx vitest run --config vitest.office-app.config.ts \
      ../qubit-app/plugins/qubit-office/game/app/timeline.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/Timeline.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/Timeline.shell.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/OfficeAppShell.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/CommandCenter.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/dock-layout.test.ts
  ) >"$LOGS/vitest.out" 2>"$LOGS/vitest.err"
  ui=$?
  set -e
  rm -f frontend/qubit-web/node_modules
  tail -n 8 "$LOGS/vitest.out" || true
  [ "$ui" -eq 0 ] || { echo "Phase 4 Vitest failed" >&2; FAILED=1; }
else
  echo "Phase 4 Vitest BLOCKED: $NODE_MODULES is absent" >&2
  ui=1
  FAILED=1
fi
record_result P4-UI "$ui" "evidence/phase-4/P4-07-operator-timeline.json" \
  "npx vitest run --config vitest.office-app.config.ts phase-4 timeline/shell suites"

"$PY" - "$RESULTS" "$WORK/results.json" <<'PY'
import glob
import json
import os
import sys

results_dir, out = sys.argv[1:3]
seen = {}
for path in sorted(glob.glob(os.path.join(results_dir, "*.json"))):
    with open(path, encoding="utf-8") as handle:
        row = json.load(handle)
    seen[row["task_id"]] = row
merged = []
for task_id in ("P4-01", "P4-02", "P4-04", "P4-05", "P4-07"):
    row = seen.get(task_id)
    if row is None:
        merged.append({"task_id": task_id, "command": "missing",
                       "exit_code": 1, "evidence": []})
    else:
        merged.append(row)
for task_id in ("P4-01-tests", "P4-02-tests", "P4-04-tests", "P4-05-tests",
                "P4-07-tests", "P4-UI"):
    row = seen.get(task_id)
    if row is not None and row["exit_code"] != 0:
        merged.append(row)
with open(out, "w", encoding="utf-8") as handle:
    json.dump(merged, handle, indent=2, sort_keys=True)
PY

echo "=== verifying evidence ==="
set +e
"$PY" scripts/verify_evidence.py \
  evidence/phase-4/P4-01-workforce.json \
  evidence/phase-4/P4-02-threads.json \
  evidence/phase-4/P4-03-delegation.json \
  evidence/phase-4/P4-04-capability-runtime.json \
  evidence/phase-4/P4-05-memory-projections.json \
  evidence/phase-4/P4-06-safety.json \
  evidence/phase-4/P4-07-operator-timeline.json
verify=$?
set -e
[ "$verify" -eq 0 ] || FAILED=1

echo "=== writing phase manifest ==="
set +e
"$PY" scripts/write_phase_manifest.py --phase 4 \
  --results "$WORK/results.json" --out evidence/phase-4/manifest.json \
  --limitation "kill-switch stoppers are injected in tests and are not wired to live services yet" \
  --limitation "threads use the injected A2A event-log seam in tests; delegation worktrees are references, not real git worktrees" \
  --limitation "operator timeline/replay is unit/component-verified; no live browser or PTY session is read"
manifest=$?
set -e
[ "$manifest" -eq 0 ] || FAILED=1

echo
if [ "$FAILED" -ne 0 ] || [ "$manifest" -ne 0 ]; then
  echo "PHASE 4 GATE: FAIL"
  exit 1
fi
echo "PHASE 4 GATE: PASS"
