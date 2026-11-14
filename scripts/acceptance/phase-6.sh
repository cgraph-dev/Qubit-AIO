#!/usr/bin/env bash
# Phase 6 acceptance gate: Character Studio, room builder, outside-world
# presentation, panel inventory, accessibility and licence-aware asset build.
#
# Requires accepted Phase 0–5 manifests. Hermetic: no live service/state,
# credentials or network. UI tests use the main checkout node_modules through a
# temporary symlink that is removed afterwards.
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
WORK="$(mktemp -d "$TMPDIR/qoffice-phase6-acceptance.XXXXXX")"
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
for phase in 0 1 2 3 4 5; do
  "$PY" scripts/verify_evidence.py "evidence/phase-$phase/manifest.json" \
    >"$LOGS/phase$phase.out" 2>&1
  code=$?
  tail -n 2 "$LOGS/phase$phase.out" || true
  [ "$code" -eq 0 ] || FAILED=1
done
set -e

run_task P6-01 "evidence/phase-6/P6-01-character-studio.json" \
  "$PY" scripts/verify_character_profile.py --json
run_task P6-01-tests "evidence/phase-6/P6-01-character-studio.json" \
  "$PY" -m pytest -q tests/phase6_character tests/phase3_character
run_task P6-02 "evidence/phase-6/P6-02-room-builder.json" \
  "$PY" scripts/verify_room_builder.py --json
run_task P6-02-tests "evidence/phase-6/P6-02-room-builder.json" \
  "$PY" -m pytest -q tests/phase6_room_builder tests/phase3_room_caps tests/phase3_world
run_task P6-03 "evidence/phase-6/P6-03-outside-world.json" \
  "$PY" scripts/verify_outside_world.py --json
run_task P6-03-tests "evidence/phase-6/P6-03-outside-world.json" \
  "$PY" -m pytest -q tests/phase6_outside tests/phase3_exterior
run_task P6-04 "evidence/phase-6/P6-04-panel-inventory.json" \
  "$PY" scripts/verify_panel_inventory.py --json
run_task P6-04-tests "evidence/phase-6/P6-04-panel-inventory.json" \
  "$PY" -m pytest -q tests/phase6_panels tests/phase4_operator_ui tests/phase3_command_center
run_task P6-05 "evidence/phase-6/P6-05-accessibility.json" \
  "$PY" scripts/accessibility_audit.py --json
run_task P6-05-tests "evidence/phase-6/P6-05-accessibility.json" \
  "$PY" -m pytest -q tests/phase6_accessibility
run_task P6-06 "evidence/phase-6/P6-06-asset-build.json" \
  "$PY" scripts/verify_asset_build.py --json
run_task P6-06-tests "evidence/phase-6/P6-06-asset-build.json" \
  "$PY" -m pytest -q tests/phase6_assets

if [ -d "$NODE_MODULES" ]; then
  echo "=== Phase 6 UI Vitest ==="
  set +e
  ln -sfn "$NODE_MODULES" frontend/qubit-web/node_modules
  (
    cd frontend/qubit-web
    npx vitest run --config vitest.office-app.config.ts \
      ../qubit-app/plugins/qubit-office/game/app/character-studio.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/CharacterStudio.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/room-builder.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/RoomBuilder.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/outside-world.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/OutsideWorld.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/panel-inventory.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/PanelInventory.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/a11y.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/OfficeAppShell.a11y.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/OfficeAppShell.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/dock-layout.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/CommandCenter.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/Timeline.test.ts \
      ../qubit-app/plugins/qubit-office/game/app/EffectiveStatePanel.test.ts
  ) >"$LOGS/vitest.out" 2>"$LOGS/vitest.err"
  ui=$?
  set -e
  rm -f frontend/qubit-web/node_modules
  tail -n 10 "$LOGS/vitest.out" || true
  [ "$ui" -eq 0 ] || { echo "Phase 6 Vitest failed" >&2; FAILED=1; }
else
  echo "Phase 6 Vitest BLOCKED: $NODE_MODULES is absent" >&2
  ui=1
  FAILED=1
fi
record_result P6-UI "$ui" \
  "evidence/phase-6/P6-01-character-studio.json,evidence/phase-6/P6-02-room-builder.json,evidence/phase-6/P6-03-outside-world.json,evidence/phase-6/P6-04-panel-inventory.json,evidence/phase-6/P6-05-accessibility.json" \
  "npx vitest run --config vitest.office-app.config.ts phase-6 UI suites"

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
for task_id in ("P6-01", "P6-02", "P6-03", "P6-04", "P6-05", "P6-06"):
    row = seen.get(task_id)
    if row is None:
        merged.append({"task_id": task_id, "command": "missing",
                       "exit_code": 1, "evidence": []})
    else:
        merged.append(row)
for task_id in ("P6-01-tests", "P6-02-tests", "P6-03-tests", "P6-04-tests",
                "P6-05-tests", "P6-06-tests", "P6-UI"):
    row = seen.get(task_id)
    if row is not None and row["exit_code"] != 0:
        merged.append(row)
with open(out, "w", encoding="utf-8") as handle:
    json.dump(merged, handle, indent=2, sort_keys=True)
PY

echo "=== verifying evidence ==="
set +e
"$PY" scripts/verify_evidence.py \
  evidence/phase-6/P6-01-character-studio.json \
  evidence/phase-6/P6-02-room-builder.json \
  evidence/phase-6/P6-03-outside-world.json \
  evidence/phase-6/P6-04-panel-inventory.json \
  evidence/phase-6/P6-05-accessibility.json \
  evidence/phase-6/P6-06-asset-build.json
verify=$?
set -e
[ "$verify" -eq 0 ] || FAILED=1

echo "=== writing phase manifest ==="
set +e
"$PY" scripts/write_phase_manifest.py --phase 6 \
  --results "$WORK/results.json" --out evidence/phase-6/manifest.json \
  --limitation "axe-core is not installed and there is no network; the accessibility audit is deterministic/structural, not an axe run, and no real screen-reader/zoomed/touch session was performed" \
  --limitation "the Character Studio, Room Builder, Outside World and Panel Inventory are mounted in the Studio dock but no live browser E2E was run" \
  --limitation "paid art was not read or committed; asset checks use metadata/manifest digests, not decoded pixels"
manifest=$?
set -e
[ "$manifest" -eq 0 ] || FAILED=1

echo
if [ "$FAILED" -ne 0 ] || [ "$manifest" -ne 0 ]; then
  echo "PHASE 6 GATE: FAIL"
  exit 1
fi
echo "PHASE 6 GATE: PASS"
