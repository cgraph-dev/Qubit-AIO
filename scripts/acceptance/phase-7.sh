#!/usr/bin/env bash
# Phase 7 acceptance gate: action catalog, consequences, mature-content policy,
# event-to-FX, outside-life/social projection and deterministic replay audit.
#
# Requires accepted Phase 0–6 manifests. Hermetic: no live service/state,
# credentials or network. Mature content remains off by default.
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
WORK="$(mktemp -d "$TMPDIR/qoffice-phase7-acceptance.XXXXXX")"
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
for phase in 0 1 2 3 4 5 6; do
  "$PY" scripts/verify_evidence.py "evidence/phase-$phase/manifest.json" \
    >"$LOGS/phase$phase.out" 2>&1
  code=$?
  tail -n 2 "$LOGS/phase$phase.out" || true
  [ "$code" -eq 0 ] || FAILED=1
done
set -e

run_task P7-01 "evidence/phase-7/P7-01-actions.json" \
  "$PY" scripts/verify_simulation_actions.py --json
run_task P7-01-tests "evidence/phase-7/P7-01-actions.json" \
  "$PY" -m pytest -q tests/phase7_actions
run_task P7-02 "evidence/phase-7/P7-02-consequences.json,evidence/phase-7/P7-03-mature-policy.json" \
  "$PY" scripts/verify_consequences.py --json
run_task P7-02-tests "evidence/phase-7/P7-02-consequences.json,evidence/phase-7/P7-03-mature-policy.json" \
  "$PY" -m pytest -q tests/phase7_consequences
run_task P7-04 "evidence/phase-7/P7-04-fx.json" \
  "$PY" -m pytest -q tests/phase7_fx
run_task P7-05 "evidence/phase-7/P7-05-outside-life.json" \
  "$PY" scripts/verify_outside_life.py --json
run_task P7-05-tests "evidence/phase-7/P7-05-outside-life.json" \
  "$PY" -m pytest -q tests/phase7_outside_life
run_task P7-06 "evidence/phase-7/P7-06-simulation-replay.json" \
  "$PY" scripts/verify_simulation_replay.py --json
run_task P7-06-tests "evidence/phase-7/P7-06-simulation-replay.json" \
  "$PY" -m pytest -q tests/phase7_replay

if [ -d "$NODE_MODULES" ]; then
  echo "=== Phase 7 UI Vitest ==="
  set +e
  ln -sfn "$NODE_MODULES" frontend/qubit-web/node_modules
  (
    cd frontend/qubit-web
    npx vitest run --config vitest.office-app.config.ts \
      ../qubit-app/plugins/qubit-office/game/app/fx.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/OfficeAppShell.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/dock-layout.test.ts
  ) >"$LOGS/vitest.out" 2>"$LOGS/vitest.err"
  ui=$?
  set -e
  rm -f frontend/qubit-web/node_modules
  tail -n 8 "$LOGS/vitest.out" || true
  [ "$ui" -eq 0 ] || { echo "Phase 7 Vitest failed" >&2; FAILED=1; }
else
  echo "Phase 7 Vitest BLOCKED: $NODE_MODULES is absent" >&2
  ui=1
  FAILED=1
fi
record_result P7-UI "$ui" "evidence/phase-7/P7-04-fx.json" \
  "npx vitest run --config vitest.office-app.config.ts phase-7 fx/shell suites"

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
for task_id in ("P7-01", "P7-02", "P7-04", "P7-05", "P7-06"):
    row = seen.get(task_id)
    if row is None:
        merged.append({"task_id": task_id, "command": "missing",
                       "exit_code": 1, "evidence": []})
    else:
        merged.append(row)
for task_id in ("P7-01-tests", "P7-02-tests", "P7-05-tests", "P7-06-tests",
                "P7-UI"):
    row = seen.get(task_id)
    if row is not None and row["exit_code"] != 0:
        merged.append(row)
with open(out, "w", encoding="utf-8") as handle:
    json.dump(merged, handle, indent=2, sort_keys=True)
PY

echo "=== verifying evidence ==="
set +e
"$PY" scripts/verify_evidence.py \
  evidence/phase-7/P7-01-actions.json \
  evidence/phase-7/P7-02-consequences.json \
  evidence/phase-7/P7-03-mature-policy.json \
  evidence/phase-7/P7-04-fx.json \
  evidence/phase-7/P7-05-outside-life.json \
  evidence/phase-7/P7-06-simulation-replay.json
verify=$?
set -e
[ "$verify" -eq 0 ] || FAILED=1

echo "=== writing phase manifest ==="
set +e
"$PY" scripts/write_phase_manifest.py --phase 7 \
  --results "$WORK/results.json" --out evidence/phase-7/manifest.json \
  --limitation "mature content remains off by default; only policy/consent/HR/label checks were exercised, not mature rendering" \
  --limitation "simulation/FX/replay are hermetic and do not render a live canvas or produce external side effects" \
  --limitation "replay divergence is digest-based; a semantic error shared by recorder and replayer would not be caught"
manifest=$?
set -e
[ "$manifest" -eq 0 ] || FAILED=1

echo
if [ "$FAILED" -ne 0 ] || [ "$manifest" -ne 0 ]; then
  echo "PHASE 7 GATE: FAIL"
  exit 1
fi
echo "PHASE 7 GATE: PASS"
