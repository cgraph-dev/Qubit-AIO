#!/usr/bin/env bash
# Phase 5 acceptance gate: Etsy operations, Trading readiness, CRM/support,
# marketing, finance, legal, people/procurement/analytics and the business
# factory. Everything is hermetic: injected providers, no live network, keys,
# signatures, payments or real publication.
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

export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export TMPDIR="${TMPDIR:-/tmp}"
WORK="$(mktemp -d "$TMPDIR/qoffice-phase5-acceptance.XXXXXX")"
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
for phase in 0 1 2 3 4; do
  "$PY" scripts/verify_evidence.py "evidence/phase-$phase/manifest.json" \
    >"$LOGS/phase$phase.out" 2>&1
  code=$?
  tail -n 2 "$LOGS/phase$phase.out" || true
  [ "$code" -eq 0 ] || FAILED=1
done
set -e

run_task P5-01 "evidence/phase-5/P5-01-etsy.json" \
  "$PY" scripts/verify_etsy_operations.py --json
run_task P5-01-tests "evidence/phase-5/P5-01-etsy.json" \
  "$PY" -m pytest -q tests/phase5_etsy tests/test_etsy_listing_workflow.py \
  tests/test_business_etsy_pilot.py
run_task P5-02 "evidence/phase-5/P5-02-trading.json" \
  "$PY" scripts/verify_trading_readiness.py --json
run_task P5-02-tests "evidence/phase-5/P5-02-trading.json" \
  "$PY" -m pytest -q tests/phase5_trading
run_task P5-03 "evidence/phase-5/P5-03-crm-support.json,evidence/phase-5/P5-04-marketing.json" \
  "$PY" scripts/verify_crm_marketing.py --json
run_task P5-03-tests "evidence/phase-5/P5-03-crm-support.json,evidence/phase-5/P5-04-marketing.json" \
  "$PY" -m pytest -q tests/phase5_crm_marketing
run_task P5-05 "evidence/phase-5/P5-05-finance.json,evidence/phase-5/P5-08-business-factory.json" \
  "$PY" scripts/verify_finance_factory.py --json
run_task P5-05-tests "evidence/phase-5/P5-05-finance.json,evidence/phase-5/P5-08-business-factory.json" \
  "$PY" -m pytest -q tests/phase5_finance_factory
run_task P5-06 "evidence/phase-5/P5-06-legal.json,evidence/phase-5/P5-07-people-analytics.json" \
  "$PY" scripts/verify_legal_people_analytics.py --json
run_task P5-06-tests "evidence/phase-5/P5-06-legal.json,evidence/phase-5/P5-07-people-analytics.json" \
  "$PY" -m pytest -q tests/phase5_legal_people

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
for task_id in ("P5-01", "P5-02", "P5-03", "P5-05", "P5-06"):
    row = seen.get(task_id)
    if row is None:
        merged.append({"task_id": task_id, "command": "missing",
                       "exit_code": 1, "evidence": []})
    else:
        merged.append(row)
for task_id in ("P5-01-tests", "P5-02-tests", "P5-03-tests", "P5-05-tests",
                "P5-06-tests"):
    row = seen.get(task_id)
    if row is not None and row["exit_code"] != 0:
        merged.append(row)
with open(out, "w", encoding="utf-8") as handle:
    json.dump(merged, handle, indent=2, sort_keys=True)
PY

echo "=== verifying evidence ==="
set +e
"$PY" scripts/verify_evidence.py \
  evidence/phase-5/P5-01-etsy.json \
  evidence/phase-5/P5-02-trading.json \
  evidence/phase-5/P5-03-crm-support.json \
  evidence/phase-5/P5-04-marketing.json \
  evidence/phase-5/P5-05-finance.json \
  evidence/phase-5/P5-06-legal.json \
  evidence/phase-5/P5-07-people-analytics.json \
  evidence/phase-5/P5-08-business-factory.json
verify=$?
set -e
[ "$verify" -eq 0 ] || FAILED=1

echo "=== writing phase manifest ==="
set +e
"$PY" scripts/write_phase_manifest.py --phase 5 \
  --results "$WORK/results.json" --out evidence/phase-5/manifest.json \
  --limitation "all external providers are injected/fake; no live Etsy/social/payment/bank/legal/HR/tax API, credentials, signatures or money movement" \
  --limitation "Trading is paper/testnet readiness only; live capital is a typed refusal without an owner risk-policy sign-off" \
  --limitation "no live outbound customer/social contact and no real marketplace publish; receipts are simulated" 
manifest=$?
set -e
[ "$manifest" -eq 0 ] || FAILED=1

echo
if [ "$FAILED" -ne 0 ] || [ "$manifest" -ne 0 ]; then
  echo "PHASE 5 GATE: FAIL"
  exit 1
fi
echo "PHASE 5 GATE: PASS"
