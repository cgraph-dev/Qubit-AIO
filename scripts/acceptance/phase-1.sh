#!/usr/bin/env bash
# Phase 1 acceptance gate.
#
# Requires an accepted Phase 0 (manifest PASS + contract validation), runs the
# focused acceptance command for every P1 task plus the P1-10 reference lane,
# verifies every evidence document and writes
# evidence/phase-1/manifest.json with status PASS only when all of it passes.
#
# Hermetic: no live service, no live state, no credentials, no network.
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

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export TMPDIR="${TMPDIR:-/tmp}"
WORK="$(mktemp -d "$TMPDIR/qoffice-phase1-acceptance.XXXXXX")"
RESULTS="$WORK/results"
LOGS="$WORK/logs"
mkdir -p "$RESULTS" "$LOGS"
cleanup() { rm -rf "$WORK"; }
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

# Prerequisites: Phase 0 accepted and its contracts still valid.
echo "=== phase-0 prerequisite ==="
set +e
"$PY" scripts/verify_evidence.py evidence/phase-0/manifest.json \
  >"$LOGS/phase0.out" 2>&1
phase0_code=$?
"$PY" scripts/validate_domain_contract.py --json \
  >"$LOGS/contract.out" 2>&1
contract_code=$?
set -e
tail -n 5 "$LOGS/phase0.out" || true
if [ "$phase0_code" -ne 0 ] || [ "$contract_code" -ne 0 ]; then
  echo "phase-1: Phase 0 is not accepted or its contracts drifted" >&2
  FAILED=1
fi

run_task P1-01 "evidence/phase-1/P1-01-identity.json" \
  "$PY" -m pytest -q tests/phase1_platform/test_identity.py
run_task P1-02 "evidence/phase-1/P1-02-eventlog.json" \
  "$PY" -m pytest -q tests/phase1_platform/test_eventlog.py
run_task P1-03 "evidence/phase-1/P1-03-gateway.json" \
  "$PY" -m pytest -q tests/phase1_gateway tests/test_integration_tool_gateway.py \
  tests/test_business_os_foundation.py
run_task P1-04 "evidence/phase-1/P1-04-runs.json" \
  "$PY" -m pytest -q tests/phase1_platform/test_runs.py
run_task P1-05 "evidence/phase-1/P1-05-a2a.json" \
  "$PY" -m pytest -q tests/phase1_a2a_approvals/test_a2a.py
run_task P1-06 "evidence/phase-1/P1-06-approvals.json" \
  "$PY" -m pytest -q tests/phase1_a2a_approvals/test_approvals.py
run_task P1-07 "evidence/phase-1/P1-07-security.json" \
  "$PY" -m pytest -q tests/phase1_security
run_task P1-08 "evidence/phase-1/P1-08-otel.json" \
  "$PY" -m pytest -q tests/phase1_otel
run_task P1-09 "evidence/phase-1/P1-09-backup-restore.json" \
  "$PY" -m pytest -q tests/phase1_backup
run_task P1-10 "evidence/phase-1/P1-10-reference-lane.json" \
  "$PY" scripts/phase1_reference_lane.py --json --work-root "$WORK/reference"
run_task P1-10-tests "evidence/phase-1/P1-10-reference-lane.json" \
  "$PY" -m pytest -q tests/phase1_reference

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
for task_id in ("P1-01", "P1-02", "P1-03", "P1-04", "P1-05", "P1-06",
                "P1-07", "P1-08", "P1-09", "P1-10"):
    row = seen.get(task_id)
    if row is None:
        merged.append({"task_id": task_id, "command": "missing",
                       "exit_code": 1, "evidence": []})
    else:
        merged.append(row)
extra = seen.get("P1-10-tests")
if extra is not None and extra["exit_code"] != 0:
    merged.append(extra)
with open(out, "w", encoding="utf-8") as handle:
    json.dump(merged, handle, indent=2, sort_keys=True)
PY

echo "=== verifying evidence ==="
set +e
"$PY" scripts/verify_evidence.py evidence/phase-1/P1-01-identity.json \
  evidence/phase-1/P1-02-eventlog.json evidence/phase-1/P1-03-gateway.json \
  evidence/phase-1/P1-04-runs.json evidence/phase-1/P1-05-a2a.json \
  evidence/phase-1/P1-06-approvals.json evidence/phase-1/P1-07-security.json \
  evidence/phase-1/P1-08-otel.json evidence/phase-1/P1-09-backup-restore.json \
  evidence/phase-1/P1-10-reference-lane.json
verify_code=$?
set -e
[ "$verify_code" -eq 0 ] || FAILED=1

echo "=== writing phase manifest ==="
set +e
"$PY" scripts/write_phase_manifest.py --phase 1 \
  --results "$WORK/results.json" --out evidence/phase-1/manifest.json
manifest_code=$?
set -e
[ "$manifest_code" -eq 0 ] || FAILED=1

echo
if [ "$FAILED" -ne 0 ] || [ "$manifest_code" -ne 0 ]; then
  echo "PHASE 1 GATE: FAIL"
  exit 1
fi
echo "PHASE 1 GATE: PASS"
