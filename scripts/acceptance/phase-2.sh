#!/usr/bin/env bash
# Phase 2 acceptance gate.
#
# Requires accepted Phase 0 and Phase 1 manifests, runs the focused acceptance
# command for every P2 task (including the effective-state UI), verifies every
# evidence document and writes evidence/phase-2/manifest.json with status PASS.
#
# Hermetic: no live service, live state, credentials or network. The UI test
# temporarily symlinks the main checkout's node_modules and removes it again.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PY="${QUBIT_ACCEPTANCE_PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x /home/trick/venvs/qubit314/bin/python ]; then
    PY=/home/trick/venvs/qubit314/bin/python
  elif [ -x "$ROOT/.venv/bin/python" ]; then
    PY="$ROOT/.venv/bin/python"
  else
    PY=python3
  fi
fi
NODE_MODULES="${QUBIT_OFFICE_NODE_MODULES:-/home/trick/Projects/Repos/Qubit/frontend/qubit-web/node_modules}"

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export TMPDIR="${TMPDIR:-/tmp}"
WORK="$(mktemp -d "$TMPDIR/qoffice-phase2-acceptance.XXXXXX")"
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
json.dump(
    {
        "task_id": task_id,
        "command": command,
        "exit_code": int(code),
        "evidence": [p for p in evidence.split(",") if p],
    },
    open(out, "w", encoding="utf-8"),
    indent=2,
    sort_keys=True,
)
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
  tail -n 8 "$LOGS/$id.out" || true
  if [ "$code" -ne 0 ]; then
    echo "--- $id stderr (tail) ---" >&2
    tail -n 30 "$LOGS/$id.err" >&2 || true
    FAILED=1
  fi
  record_result "$id" "$code" "$evidence_csv" "$*"
}

echo "=== phase-0 / phase-1 prerequisites ==="
set +e
"$PY" scripts/verify_evidence.py evidence/phase-0/manifest.json \
  >"$LOGS/phase0.out" 2>&1
p0=$?
"$PY" scripts/verify_evidence.py evidence/phase-1/manifest.json \
  >"$LOGS/phase1.out" 2>&1
p1=$?
set -e
tail -n 3 "$LOGS/phase0.out" || true
tail -n 3 "$LOGS/phase1.out" || true
if [ "$p0" -ne 0 ] || [ "$p1" -ne 0 ]; then
  echo "phase-2: an earlier phase is not accepted" >&2
  FAILED=1
fi

run_task P2-01 "evidence/phase-2/P2-01-registry.json" \
  "$PY" -m pytest -q tests/phase2_registry_sessions/test_agent_profile.py
run_task P2-02 "evidence/phase-2/P2-02-sessions.json" \
  "$PY" -m pytest -q tests/phase2_registry_sessions/test_sessions.py \
  tests/phase2_registry_sessions/test_schema_v5.py
run_task P2-03 "evidence/phase-2/P2-03-adapter.json" \
  "$PY" -m pytest -q tests/phase2_adapter
run_task P2-04 "evidence/phase-2/P2-04-memory.json" \
  "$PY" -m pytest -q tests/phase2_memory
run_task P2-05 "evidence/phase-2/P2-05-handoff.json" \
  "$PY" -m pytest -q tests/phase2_registry_sessions/test_a2a_handoff.py
run_task P2-06 "evidence/phase-2/P2-06-turn.json" \
  "$PY" -m pytest -q tests/phase2_turn
run_task P2-07 "evidence/phase-2/P2-07-effective-state.json" \
  "$PY" -m pytest -q tests/phase2_effective tests/test_business_agent_controls.py
run_task P2-07-verifier "evidence/phase-2/P2-07-effective-state.json" \
  "$PY" scripts/verify_effective_state.py --json
run_task P2-04-projection "evidence/phase-2/P2-04-memory.json" \
  bash -lc "$PY -m pytest -q tests/phase2_memory/test_memory_projection.py && $PY scripts/verify_memory_projection.py --root /tmp/qoffice-memory-projection --check"

echo "=== office-app Vitest ==="
if [ -d "$NODE_MODULES" ]; then
  set +e
  ln -sfn "$NODE_MODULES" frontend/qubit-web/node_modules
  (
    cd frontend/qubit-web
    npx vitest run --config vitest.office-app.config.ts \
      ../qubit-app/plugins/qubit-office/game/app/effective-state.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/EffectiveStatePanel.test.ts
  ) >"$LOGS/vitest.out" 2>"$LOGS/vitest.err"
  vitest=$?
  set -e
  rm -f frontend/qubit-web/node_modules
  tail -n 10 "$LOGS/vitest.out" || true
  [ "$vitest" -eq 0 ] || { echo "Vitest failed" >&2; FAILED=1; }
else
  echo "Vitest BLOCKED: $NODE_MODULES is absent" >&2
  FAILED=1
  vitest=1
fi
record_result P2-07-ui "$vitest" "evidence/phase-2/P2-07-effective-state.json" \
  "npx vitest run --config vitest.office-app.config.ts effective-state tests"

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
order = ("P2-01", "P2-02", "P2-03", "P2-04", "P2-05", "P2-06", "P2-07")
for task_id in order:
    row = seen.get(task_id)
    if row is None:
        merged.append({"task_id": task_id, "command": "missing",
                       "exit_code": 1, "evidence": []})
    else:
        merged.append(row)
for task_id in ("P2-07-verifier", "P2-04-projection", "P2-07-ui"):
    row = seen.get(task_id)
    if row is not None and row["exit_code"] != 0:
        merged.append(row)
with open(out, "w", encoding="utf-8") as handle:
    json.dump(merged, handle, indent=2, sort_keys=True)
PY

echo "=== verifying evidence ==="
set +e
"$PY" scripts/verify_evidence.py \
  evidence/phase-2/P2-01-registry.json \
  evidence/phase-2/P2-02-sessions.json \
  evidence/phase-2/P2-03-adapter.json \
  evidence/phase-2/P2-04-memory.json \
  evidence/phase-2/P2-05-handoff.json \
  evidence/phase-2/P2-06-turn.json \
  evidence/phase-2/P2-07-effective-state.json
verify=$?
set -e
[ "$verify" -eq 0 ] || FAILED=1

echo "=== writing phase manifest ==="
set +e
"$PY" scripts/write_phase_manifest.py --phase 2 \
  --results "$WORK/results.json" --out evidence/phase-2/manifest.json
manifest=$?
set -e
[ "$manifest" -eq 0 ] || FAILED=1

echo
if [ "$FAILED" -ne 0 ] || [ "$manifest" -ne 0 ]; then
  echo "PHASE 2 GATE: FAIL"
  exit 1
fi
echo "PHASE 2 GATE: PASS"
