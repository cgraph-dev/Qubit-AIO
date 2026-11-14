#!/usr/bin/env bash
# Phase 3 acceptance gate: full campus data, maps, room capabilities, renderer
# contract, command-center shell, exterior and Character Studio.
#
# Requires accepted Phase 0/1/2 manifests. Hermetic: no live service/state,
# credentials or network. UI tests use the main checkout's node_modules through
# a temporary symlink that is removed afterwards.
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
WORK="$(mktemp -d "$TMPDIR/qoffice-phase3-acceptance.XXXXXX")"
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
for phase in 0 1 2; do
  "$PY" scripts/verify_evidence.py "evidence/phase-$phase/manifest.json" \
    >"$LOGS/phase$phase.out" 2>&1
  code=$?
  tail -n 2 "$LOGS/phase$phase.out" || true
  [ "$code" -eq 0 ] || FAILED=1
done
set -e

run_task P3-01 "evidence/phase-3/P3-01-campus.json" \
  "$PY" scripts/validate_campus_schema.py --json
run_task P3-02 "evidence/phase-3/P3-02-room-capabilities.json" \
  "$PY" scripts/validate_room_capabilities.py --json
run_task P3-03 "evidence/phase-3/P3-03-map-schema.json" \
  "$PY" -m pytest -q tests/phase3_world/test_map_schema.py
run_task P3-04 "evidence/phase-3/P3-04-renderer.json" \
  "$PY" scripts/verify_renderer_budget.py --json
run_task P3-04-tests "evidence/phase-3/P3-04-renderer.json" \
  "$PY" -m pytest -q tests/phase3_renderer
run_task P3-05 "evidence/phase-3/P3-05-command-center.json" \
  "$PY" -m pytest -q tests/phase3_command_center tests/test_business_office_bridge.py
run_task P3-06 "evidence/phase-3/P3-06-exterior.json" \
  "$PY" scripts/validate_exterior.py --json
run_task P3-06-tests "evidence/phase-3/P3-06-exterior.json" \
  "$PY" -m pytest -q tests/phase3_exterior
run_task P3-07 "evidence/phase-3/P3-07-character-studio.json" \
  "$PY" scripts/verify_character_profile.py --json
run_task P3-07-tests "evidence/phase-3/P3-07-character-studio.json" \
  "$PY" -m pytest -q tests/phase3_character

if [ -d "$NODE_MODULES" ]; then
  echo "=== Phase 3 UI Vitest ==="
  set +e
  ln -sfn "$NODE_MODULES" frontend/qubit-web/node_modules
  (
    cd frontend/qubit-web
    npx vitest run --config vitest.office-app.config.ts \
      ../qubit-app/plugins/qubit-office/game/app/renderer-floor.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/renderer-budget.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/command-center.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/CommandCenter.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/OfficeAppShell.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/character-studio.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/CharacterStudio.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/AgentControlDrawer.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/effective-state.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/EffectiveStatePanel.test.ts
  ) >"$LOGS/vitest.out" 2>"$LOGS/vitest.err"
  ui=$?
  set -e
  rm -f frontend/qubit-web/node_modules
  tail -n 8 "$LOGS/vitest.out" || true
  [ "$ui" -eq 0 ] || { echo "Phase 3 Vitest failed" >&2; FAILED=1; }
else
  echo "Phase 3 Vitest BLOCKED: $NODE_MODULES is absent" >&2
  ui=1
  FAILED=1
fi
record_result P3-UI "$ui" \
  "evidence/phase-3/P3-04-renderer.json,evidence/phase-3/P3-05-command-center.json,evidence/phase-3/P3-07-character-studio.json" \
  "npx vitest run --config vitest.office-app.config.ts phase-3 UI suites"

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
for task_id in ("P3-01", "P3-02", "P3-03", "P3-04", "P3-05", "P3-06",
                "P3-07"):
    row = seen.get(task_id)
    if row is None:
        merged.append({"task_id": task_id, "command": "missing",
                       "exit_code": 1, "evidence": []})
    else:
        merged.append(row)
for task_id in ("P3-04-tests", "P3-06-tests", "P3-07-tests", "P3-UI"):
    row = seen.get(task_id)
    if row is not None and row["exit_code"] != 0:
        merged.append(row)
with open(out, "w", encoding="utf-8") as handle:
    json.dump(merged, handle, indent=2, sort_keys=True)
PY

echo "=== verifying evidence ==="
set +e
"$PY" scripts/verify_evidence.py \
  evidence/phase-3/P3-01-campus.json \
  evidence/phase-3/P3-02-room-capabilities.json \
  evidence/phase-3/P3-03-map-schema.json \
  evidence/phase-3/P3-04-renderer.json \
  evidence/phase-3/P3-05-command-center.json \
  evidence/phase-3/P3-06-exterior.json \
  evidence/phase-3/P3-07-character-studio.json
verify=$?
set -e
[ "$verify" -eq 0 ] || FAILED=1

echo "=== writing phase manifest ==="
set +e
"$PY" scripts/write_phase_manifest.py --phase 3 \
  --results "$WORK/results.json" --out evidence/phase-3/manifest.json \
  --limitation "three shell-CSS assertions in tests/test_office_game_shell.py fail identically on base c95366a3f; they are not caused by Phase 3 and are not part of this gate" \
  --limitation "renderer GPU frame/heap and live browser metrics remain not_measured; only the deterministic data path is checked"
manifest=$?
set -e
[ "$manifest" -eq 0 ] || FAILED=1

echo
if [ "$FAILED" -ne 0 ] || [ "$manifest" -ne 0 ]; then
  echo "PHASE 3 GATE: FAIL"
  exit 1
fi
echo "PHASE 3 GATE: PASS"
