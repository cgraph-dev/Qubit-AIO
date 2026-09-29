#!/usr/bin/env bash
# scripts/rebase_gate.sh -- the v0.21.5 1:1 rebase gate.
#
# One command that proves the rebase is green.  It runs, in order:
#
#   1. suite     -- the rebase/rebrand/overlay/config/forensic pytest gate:
#                    tests/test_rebase_*.py tests/test_rebrand_*.py
#                    tests/test_overlay_regressions.py tests/test_config_alignment.py
#                    tests/test_forensic_audit_harness.py
#   2. artifacts -- the deterministic generated-artifact `--check`s (render in
#                    memory, no writes), plus the reference-pinned checks when a
#                    read-only reference home is configured.
#   3. audit     -- scripts/forensic_audit/run_audit.py --check
#
# Every step is reported; the script exits non-zero if ANY step fails.  On low
# disk space it points at scripts/tmp_reclaim.sh (safe, repeatable; never
# touches the live repo, ~/.hermes, or ~/.local/state).
#
# Usage:
#   scripts/rebase_gate.sh                 # run the whole gate
#   scripts/rebase_gate.sh --only suite    # one step (suite|artifacts|audit)
#   scripts/rebase_gate.sh --dry-run       # print the commands, run nothing
#   scripts/rebase_gate.sh --help
#
# Env:
#   QUBIT_GATE_PYTHON        python interpreter (default: repo .venv, then python3)
#   QUBIT_GATE_TESTS         space-separated pytest targets (default the suite)
#   QUBIT_GATE_BASETEMP      pytest --basetemp (default ${TMPDIR:-/tmp}/qubit-rebase-gate)
#   QUBIT_GATE_REFERENCE=1   run the reference-pinned artifact checks even when
#                            the reference home cannot be auto-detected
#   QUBIT_GATE_SKIP_REFERENCE=1  never run the reference-pinned artifact checks
#   QUBIT_GATE_MIN_FREE_MB   free-space threshold for the reclaim hint (default 2048)
#   QUBIT_GATE_DRY_RUN=1     same as --dry-run
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

TMP_ROOT="${TMPDIR:-/tmp}"
BASETEMP="${QUBIT_GATE_BASETEMP:-$TMP_ROOT/qubit-rebase-gate}"
MIN_FREE_MB="${QUBIT_GATE_MIN_FREE_MB:-2048}"
RECLAIM="$SCRIPT_DIR/tmp_reclaim.sh"

# Suite targets: the rebase-gate files.  The shell expands the globs when the
# command runs; a missing glob is passed literally and pytest reports it.
if [ -n "${QUBIT_GATE_TESTS:-}" ]; then
  # shellcheck disable=SC2206
  SUITE_TARGETS=(${QUBIT_GATE_TESTS})
else
  SUITE_TARGETS=(
    tests/test_rebase_*.py
    tests/test_rebrand_*.py
    tests/test_overlay_regressions.py
    tests/test_config_alignment.py
    tests/test_forensic_audit_harness.py
  )
fi

# Hermetic generated-artifact checks: source-only, no reference home required.
HERMETIC_CHECKS=(
  "page_coverage.py --check"
  "identifier_rename.py --check"
  "route_ownership_matrix.py --check"
  "compat_handler_reachability.py --check"
  "openclaw_operator_only_review.py --check"
  "rebuild_ledger_qubit.py --check"
  "rebuild_ledger_hermes.py --check"
  "rebuild_ledger_openclaw.py --check"
  "voice_inventory_coverage.py --check"
)
# Reference-pinned checks: need the read-only reference install / pinned source,
# so they run only when one is detected (or QUBIT_GATE_REFERENCE=1).
REFERENCE_CHECKS=(
  "parity_inventory.py --check"
  "hermes_vendor_manifest.py --check"
)

ONLY=""
DRY_RUN=0
[ "${QUBIT_GATE_DRY_RUN:-0}" = "1" ] && DRY_RUN=1

usage() {
  awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "$0"
}

while [ "$#" -gt 0 ]; do
  arg="$1"
  case "$arg" in
    --only)
      [ "$#" -ge 2 ] || { echo "rebase_gate: --only needs a value" >&2; exit 2; }
      ONLY="$2"
      shift 2
      ;;
    --only=*) ONLY="${arg#--only=}"; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "rebase_gate: unknown argument: $arg" >&2; exit 2 ;;
  esac
done

case "$ONLY" in
  ""|suite|artifacts|audit) : ;;
  *) echo "rebase_gate: --only must be suite|artifacts|audit (got: $ONLY)" >&2; exit 2 ;;
esac

# ---------------------------------------------------------------------------
# interpreter
# ---------------------------------------------------------------------------
resolve_python() {
  if [ -n "${QUBIT_GATE_PYTHON:-}" ]; then
    printf '%s\n' "$QUBIT_GATE_PYTHON"
    return 0
  fi
  if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    printf '%s\n' "$REPO_ROOT/.venv/bin/python"
    return 0
  fi
  if [ -x "$HOME/Projects/Repos/qubit-voicebridge/.venv/bin/python" ]; then
    printf '%s\n' "$HOME/Projects/Repos/qubit-voicebridge/.venv/bin/python"
    return 0
  fi
  if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
    printf '%s\n' "$VIRTUAL_ENV/bin/python"
    return 0
  fi
  command -v python3 || command -v python
}

PY="$(resolve_python)"
[ -n "$PY" ] || { echo "rebase_gate: no python interpreter found" >&2; exit 2; }

# ---------------------------------------------------------------------------
# space pressure
# ---------------------------------------------------------------------------
free_mb() {
  df -Pm "$TMP_ROOT" 2>/dev/null | awk 'NR==2{print $4}'
}

reclaim_hint() {
  echo "rebase_gate: low disk space under $TMP_ROOT ($(free_mb) MiB free;" \
       "threshold ${MIN_FREE_MB} MiB)." >&2
  echo "rebase_gate: reclaim scratch with: $RECLAIM" >&2
  echo "rebase_gate: reclaim aggressively with: $RECLAIM --aggressive" >&2
}

check_space() {
  local free
  free="$(free_mb)"
  if [ -n "$free" ] && [ "$free" -lt "$MIN_FREE_MB" ] 2>/dev/null; then
    reclaim_hint
    return 0
  fi
  return 1
}

space_low=0
check_space && space_low=1

# ---------------------------------------------------------------------------
# step plumbing
# ---------------------------------------------------------------------------
declare -a FAILED_STEPS=()

show() { printf 'DRY:'; printf ' %q' "$@"; printf '\n'; }

run_step() {
  local name="$1"; shift
  echo "== rebase_gate: $name =="
  if [ "$DRY_RUN" = "1" ]; then
    show "$@"
    return 0
  fi
  "$@"
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "rebase_gate: step '$name' FAILED (rc=$rc)" >&2
    FAILED_STEPS+=("$name")
    check_space || true
  fi
  return "$rc"
}

run_artifact() {
  # $1 = "script.py --flags"
  local spec="$1"
  # shellcheck disable=SC2206
  local parts=(${spec})
  run_step "artifact:${parts[0]}" "$PY" "$SCRIPT_DIR/${parts[0]}" "${parts[@]:1}"
}

reference_available() {
  local home
  home="${QUBIT_PARITY_REF_HOME:-$HOME/.hermes}"
  [ -d "$home/hermes-agent" ]
}

run_artifacts() {
  local spec
  for spec in "${HERMETIC_CHECKS[@]}"; do
    run_artifact "$spec"
  done
  # Reference-pinned checks are opt-in: the gate is hermetic by default and
  # never reads the operator's real reference home unless explicitly enabled.
  if [ "${QUBIT_GATE_REFERENCE:-0}" = "1" ] && [ "${QUBIT_GATE_SKIP_REFERENCE:-0}" != "1" ]; then
    for spec in "${REFERENCE_CHECKS[@]}"; do
      run_artifact "$spec"
    done
  else
    local note=""
    reference_available && note=" (a reference home is present; set QUBIT_GATE_REFERENCE=1 to include them)"
    echo "rebase_gate: skipping reference-pinned artifact checks" \
         "(${REFERENCE_CHECKS[*]})${note}."
  fi
}

run_suite() {
  local targets=()
  local pattern
  for pattern in "${SUITE_TARGETS[@]}"; do
    # Expand globs here so the printed command is exact.
    # shellcheck disable=SC2206
    if [[ "$pattern" == *"*"* ]]; then
      targets+=($pattern)
    else
      targets+=("$pattern")
    fi
  done
  run_step "suite" "$PY" -m pytest "${targets[@]}" \
    "--basetemp=$BASETEMP" -p no:cacheprovider
}

run_audit() {
  run_step "audit" "$PY" "$SCRIPT_DIR/forensic_audit/run_audit.py" --check
}

# ---------------------------------------------------------------------------
# selection + run
# ---------------------------------------------------------------------------
mkdir -p "$BASETEMP" 2>/dev/null || true

case "$ONLY" in
  suite) run_suite ;;
  artifacts) run_artifacts ;;
  audit) run_audit ;;
  "") run_suite; run_artifacts; run_audit ;;
esac

echo
if [ "$space_low" = "1" ]; then
  reclaim_hint
fi

if [ "${#FAILED_STEPS[@]}" -eq 0 ]; then
  echo "rebase_gate: PASS"
  exit 0
fi
echo "rebase_gate: FAIL (${FAILED_STEPS[*]})" >&2
exit 1
