#!/usr/bin/env bash
# Phase 8 acceptance gate: scale, security/MCP, DR/rings, observability and the
# release-evidence/decision set.
#
# This gate can prove every implementation row, but it must NOT mark Phase 8
# PASS without (a) a final matrix on the frozen SHA and (b) the owner release
# decision record. When those are missing it writes status BLOCKED and exits 2;
# it never fabricates an owner signature or a VERIFIED claim.
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
WORK="$(mktemp -d "$TMPDIR/qoffice-phase8-acceptance.XXXXXX")"
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
  tail -n 8 "$LOGS/$id.out" || true
  if [ "$code" -ne 0 ]; then
    echo "--- $id stderr (tail) ---" >&2
    tail -n 30 "$LOGS/$id.err" >&2 || true
    FAILED=1
  fi
  record_result "$id" "$code" "$evidence_csv" "$*"
}

echo "=== earlier phase prerequisites ==="
set +e
for phase in 0 1 2 3 4 5 6 7; do
  "$PY" scripts/verify_evidence.py "evidence/phase-$phase/manifest.json" \
    >"$LOGS/phase$phase.out" 2>&1
  code=$?
  tail -n 2 "$LOGS/phase$phase.out" || true
  [ "$code" -eq 0 ] || FAILED=1
done
set -e

run_task P8-01 "evidence/phase-8/P8-01-scale.json" \
  "$PY" scripts/verify_scale.py --json
run_task P8-01-tests "evidence/phase-8/P8-01-scale.json" \
  "$PY" -m pytest -q tests/phase8_scale
run_task P8-02 "evidence/phase-8/P8-02-security.json" \
  "$PY" scripts/security_matrix.py --json
run_task P8-02-tests "evidence/phase-8/P8-02-security.json" \
  "$PY" -m pytest -q tests/phase8_security tests/phase1_security tests/phase4_runtime_safety
run_task P8-03 "evidence/phase-8/P8-03-dr.json" \
  "$PY" scripts/drill_recovery.py --json
run_task P8-03-tests "evidence/phase-8/P8-03-dr.json" \
  "$PY" -m pytest -q tests/phase8_dr tests/phase1_backup
run_task P8-04 "evidence/phase-8/P8-04-observability.json" \
  "$PY" scripts/verify_observability.py --json
run_task P8-04-tests "evidence/phase-8/P8-04-observability.json" \
  "$PY" -m pytest -q tests/phase8_observability tests/phase1_otel tests/phase6_accessibility

FROZEN_JSON="$WORK/frozen.json"
"$PY" - "$FROZEN_JSON" <<'PY'
import json
import sys
from pathlib import Path
d = json.loads(Path("evidence/phase-8/final-matrix.json").read_text())
json.dump({"commit": d["commit"], "tree": d["tree"]}, open(sys.argv[1], "w"))
PY
read -r FROZEN_COMMIT FROZEN_TREE < <("$PY" - "$FROZEN_JSON" <<'PY'
import json
import sys
d = json.load(open(sys.argv[1]))
print(d["commit"], d["tree"])
PY
)
echo "frozen commit: $FROZEN_COMMIT tree: $FROZEN_TREE"
# The release tool must see the current phase-8 manifest as missing on the
# first pass; the gate writes it below with status BLOCKED until the owner
# decision exists. Otherwise stale manifest hashes make the report FAIL rather
# than READY_FOR_OWNER_DECISION.
rm -f evidence/phase-8/manifest.json
run_task P8-05 "evidence/phase-8/P8-05-release.json" \
  "$PY" scripts/release_evidence.py --commit "$FROZEN_COMMIT" --tree "$FROZEN_TREE" --json

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
for task_id in ("P8-01", "P8-02", "P8-03", "P8-04", "P8-05"):
    row = seen.get(task_id)
    if row is None:
        merged.append({"task_id": task_id, "command": "missing",
                       "exit_code": 1, "evidence": []})
    else:
        merged.append(row)
for task_id in ("P8-01-tests", "P8-02-tests", "P8-03-tests", "P8-04-tests"):
    row = seen.get(task_id)
    if row is not None and row["exit_code"] != 0:
        merged.append(row)
with open(out, "w", encoding="utf-8") as handle:
    json.dump(merged, handle, indent=2, sort_keys=True)
PY

echo "=== verifying evidence ==="
set +e
"$PY" scripts/verify_evidence.py \
  evidence/phase-8/P8-01-scale.json \
  evidence/phase-8/P8-02-security.json \
  evidence/phase-8/P8-03-dr.json \
  evidence/phase-8/P8-04-observability.json \
  evidence/phase-8/P8-05-release.json
verify=$?
set -e
[ "$verify" -eq 0 ] || FAILED=1

echo "=== writing phase manifest / release status ==="
set +e
"$PY" scripts/write_phase_manifest.py --phase 8 \
  --results "$WORK/results.json" --out evidence/phase-8/manifest.json \
  --limitation "GPU frame time, browser heap and real device/browser matrices remain not_measured" \
  --limitation "security/DR/scale assertions are hermetic with injected providers; no live MCP/OAuth/payment/broker endpoint was exercised" \
  --limitation "the final matrix is the bounded Office feature matrix, not the repo-wide matrix; the owner release decision is not signed"
manifest=$?
set -e
[ "$manifest" -eq 0 ] || FAILED=1

RELEASE_STATUS="$("$PY" - <<'PY'
import json
try:
    d = json.load(open("evidence/phase-8/release.json"))
    print(d.get("status", "UNKNOWN"))
except Exception:
    print("UNKNOWN")
PY
)"
BLOCKERS="$("$PY" - <<'PY'
import json
try:
    d = json.load(open("evidence/phase-8/release.json"))
    print("; ".join(d.get("missing", [])) or "none")
except Exception:
    print("release evidence unreadable")
PY
)"
echo "release status: $RELEASE_STATUS"
echo "release blockers: $BLOCKERS"

"$PY" - "$RELEASE_STATUS" "$BLOCKERS" "$FROZEN_COMMIT" "$FROZEN_TREE" <<'PY'
import json
import sys
from pathlib import Path

status, blockers, commit, tree = sys.argv[1:5]
path = Path("evidence/phase-8/manifest.json")
doc = json.loads(path.read_text())
doc["release_status"] = status
doc["frozen"] = {"commit": commit, "tree": tree}
if status != "PASS":
    doc["status"] = "BLOCKED"
    doc["blockers"] = [b for b in blockers.split("; ") if b and b != "none"]
path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
print(f"phase 8 manifest status: {doc['status']}")
PY

if [ "$FAILED" -ne 0 ]; then
  echo "PHASE 8 GATE: FAIL"
  exit 1
fi
if [ "$RELEASE_STATUS" != "PASS" ]; then
  echo "PHASE 8 GATE: BLOCKED — $BLOCKERS"
  exit 2
fi
echo "PHASE 8 GATE: PASS"
