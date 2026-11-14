#!/usr/bin/env bash
# Phase 0 acceptance gate.
#
# Runs every P0 task command, verifies the evidence documents (status,
# freshness, hashes, command exit codes) and writes
# evidence/phase-0/manifest.json with status PASS only when everything passes.
#
# Hermetic: no live services, no live QUBIT_HOME, no network. The asset ledger
# reads the operator's local art directory for metadata only; it never copies
# art and writes its per-file output under TMPDIR.
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
WORK="$(mktemp -d "$TMPDIR/qoffice-acceptance.XXXXXX")"
RESULTS="$WORK/results"
LOGS="$WORK/logs"
mkdir -p "$RESULTS" "$LOGS"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

ASSET_ROOT="${QUBIT_OFFICE_ASSET_ROOT:-/home/trick/Desktop/UI Office}"
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
  tail -n 12 "$LOGS/$id.out" || true
  if [ "$code" -ne 0 ]; then
    echo "--- $id stderr (tail) ---" >&2
    tail -n 40 "$LOGS/$id.err" >&2 || true
    FAILED=1
  fi
  record_result "$id" "$code" "$evidence_csv" "$*"
  return 0
}

# P0-01 — freeze the authority set, then prove it did not drift.
echo "=== P0-01: freeze + verify authority set ==="
set +e
"$PY" scripts/office_phase0_contract.py >"$LOGS/P0-01.write.out" 2>&1
write_code=$?
"$PY" scripts/office_phase0_contract.py --check >"$LOGS/P0-01.check.out" 2>&1
check_code=$?
"$PY" scripts/verify_doc_citations.py >"$LOGS/P0-01.docs.out" 2>&1
docs_code=$?
set -e
tail -n 6 "$LOGS/P0-01.write.out" || true
tail -n 6 "$LOGS/P0-01.check.out" || true
tail -n 6 "$LOGS/P0-01.docs.out" || true
if [ "$write_code" -ne 0 ] || [ "$check_code" -ne 0 ] || [ "$docs_code" -ne 0 ]; then
  echo "--- P0-01 stderr (tail) ---" >&2
  tail -n 40 "$LOGS/P0-01.write.out" >&2 || true
  tail -n 40 "$LOGS/P0-01.check.out" >&2 || true
  tail -n 40 "$LOGS/P0-01.docs.out" >&2 || true
  FAILED=1
fi
"$PY" - "$write_code" "$check_code" "$docs_code" \
  evidence/phase-0/contract.json evidence/phase-0/P0-01-contract.json \
  docs/QUBIT-OFFICE-COMPLETE-PLAN.md <<'PY'
import datetime
import hashlib
import json
import sys
from pathlib import Path

write_code, check_code, docs_code, contract_path, out_path, plan_path = sys.argv[1:7]
root = Path.cwd()


def sha(path: str) -> str:
    digest = hashlib.sha256()
    with open(root / path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def row(path: str):
    return {"path": path, "sha256": sha(path)}


evidence = {
    "task_id": "P0-01",
    "status": "PASS" if int(write_code) == 0 and int(check_code) == 0
    and int(docs_code) == 0 else "FAIL",
    "generated_at": datetime.datetime.now(datetime.timezone.utc)
    .replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "inputs": [row(plan_path)],
    "outputs": [row(contract_path)],
    "commands": [
        {"cmd": "python scripts/office_phase0_contract.py",
         "exit_code": int(write_code)},
        {"cmd": "python scripts/office_phase0_contract.py --check",
         "exit_code": int(check_code)},
        {"cmd": "python scripts/verify_doc_citations.py",
         "exit_code": int(docs_code)},
    ],
    "limitations": [
        "freezes the document authority only; implementation claims remain "
        "phase-gated",
    ],
}
Path(out_path).write_text(
    json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
p01_code=0
if [ "$write_code" -ne 0 ] || [ "$check_code" -ne 0 ] || [ "$docs_code" -ne 0 ]; then
  p01_code=1
fi
record_result P0-01 "$p01_code" "evidence/phase-0/P0-01-contract.json" \
  "office_phase0_contract (write) ; office_phase0_contract --check ; verify_doc_citations"

run_task P0-02 "evidence/phase-0/P0-02-domain-contract.json" \
  "$PY" scripts/validate_domain_contract.py --json

run_task P0-03 "evidence/phase-0/P0-03-asset-ledger.json" \
  "$PY" scripts/office_asset_ledger.py --source "$ASSET_ROOT" \
  --out "$WORK/asset-manifest.json" --check

run_task P0-04 "evidence/phase-0/P0-04-dependency-ledger.json" \
  "$PY" scripts/validate_dependency_ledger.py --json

run_task P0-05 "evidence/phase-0/P0-05-threat-catalog.json" \
  "$PY" -m pytest -q tests/office_contracts/test_threat_catalog.py \
  tests/office_contracts/test_threat_denials.py

run_task P0-06 "evidence/phase-0/P0-06-stage-replay.json" \
  "$PY" scripts/stage_replay.py --fixture synthetic-department --no-network \
  --json --work-root "$WORK/stage"

run_task P0-07 "evidence/phase-0/P0-07-campus.json" \
  "$PY" scripts/generate_campus_fixture.py --check

run_task P0-08 "evidence/phase-0/P0-08-renderer-benchmark.json" \
  "$PY" scripts/office_benchmark_renderer.py --json

run_task P0-09 "evidence/phase-0/P0-09-durable-substrate.json" \
  "$PY" -m pytest -q tests/durable_substrate

"$PY" - "$RESULTS" "$WORK/results.json" <<'PY'
import glob
import json
import os
import sys

results_dir, out = sys.argv[1:3]
rows = []
for path in sorted(glob.glob(os.path.join(results_dir, "*.json"))):
    with open(path, encoding="utf-8") as handle:
        rows.append(json.load(handle))
with open(out, "w", encoding="utf-8") as handle:
    json.dump(rows, handle, indent=2, sort_keys=True)
PY

echo "=== verifying evidence ==="
set +e
"$PY" scripts/verify_evidence.py \
  evidence/phase-0/P0-01-contract.json \
  evidence/phase-0/P0-02-domain-contract.json \
  evidence/phase-0/P0-03-asset-ledger.json \
  evidence/phase-0/P0-04-dependency-ledger.json \
  evidence/phase-0/P0-05-threat-catalog.json \
  evidence/phase-0/P0-06-stage-replay.json \
  evidence/phase-0/P0-07-campus.json \
  evidence/phase-0/P0-08-renderer-benchmark.json \
  evidence/phase-0/P0-09-durable-substrate.json
verify_code=$?
set -e
[ "$verify_code" -eq 0 ] || FAILED=1

echo "=== writing phase manifest ==="
set +e
"$PY" scripts/write_phase_manifest.py --phase 0 \
  --results "$WORK/results.json" --out evidence/phase-0/manifest.json
manifest_code=$?
set -e
[ "$manifest_code" -eq 0 ] || FAILED=1

echo
if [ "$FAILED" -ne 0 ] || [ "$manifest_code" -ne 0 ]; then
  echo "PHASE 0 GATE: FAIL"
  exit 1
fi
echo "PHASE 0 GATE: PASS"
