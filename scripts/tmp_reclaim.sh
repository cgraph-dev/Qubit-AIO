#!/usr/bin/env bash
# Reclaim scratch space from Qubit/SDD test runs and audits without touching
# anything the rebuild still needs. Safe to run repeatedly.
#
# Usage: scripts/tmp_reclaim.sh [--aggressive]
#
# Safety: only removes regenerable scratch under a temp root (default /tmp) —
# pytest temp roots (including the Qubit per-run `qubit-pytest-*` dirs),
# throwaway HOME/QUBIT_HOME probe dirs, seeded reference clones, and audit log
# dirs. Every target is re-validated against the allowed roots before removal,
# so a stale glob can never delete anything outside temp. It never touches the
# live repo, ~/.hermes, or ~/.local/state.
set -u
shopt -s nullglob

usage() {
  sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
}

aggressive=0
case "${1:-}" in
  --aggressive) aggressive=1 ;;
  "" | --safe) ;;
  -h | --help) usage; exit 0 ;;
  *) echo "tmp_reclaim: unknown option: $1" >&2; exit 2 ;;
esac

# Allowed roots. QUBIT_PYTEST_TMPROOT steers test scratch elsewhere, so clean
# there too when it is set; both are validated per-target below.
roots=("/tmp")
if [ -n "${QUBIT_PYTEST_TMPROOT:-}" ]; then
  roots+=("${QUBIT_PYTEST_TMPROOT%/}")
fi

is_reclaimable() {
  local target="$1" root
  [ "$target" = "/" ] && return 1
  for root in "${roots[@]}"; do
    case "$target" in
      "$root"/*) return 0 ;;
    esac
  done
  return 1
}

reclaim() {
  local target
  for target in "$@"; do
    if is_reclaimable "$target"; then
      rm -rf -- "$target" 2>/dev/null || true
    else
      echo "tmp_reclaim: refusing non-scratch target: $target" >&2
    fi
  done
}

before=$(df -h / | awk 'NR==2{print $4}')

# Test scratch + throwaway homes (always disposable).
always=(
  /tmp/pytest-of-"${USER:-trick}"
  /tmp/pytest-of-*
  /tmp/qubit-pytest-*
  /tmp/a[0-9]-pt
  /tmp/a[0-9]-home
  /tmp/a[0-9]-tmp
  /tmp/a[0-9]-run
  /tmp/qubit-*-home
  /tmp/qubit-*-tmp
  /tmp/qubit-*-throwaway
  /tmp/qbh*
  /tmp/hermes-*-state-*
  /tmp/hermes-test-home-*
)
if [ -n "${QUBIT_PYTEST_TMPROOT:-}" ]; then
  always+=("${QUBIT_PYTEST_TMPROOT%/}"/qubit-pytest-*)
fi
reclaim "${always[@]}"

# Seeded reference clones used only by the vendored determinism gates; they are
# re-materialized on demand from the pin. Keep unless --aggressive.
if [ "$aggressive" -eq 1 ]; then
  reclaim /tmp/a4-logs /tmp/qubit-task6-*
fi

after=$(df -h / | awk 'NR==2{print $4}')
echo "tmp_reclaim: free ${before} -> ${after}"
echo "Guard: pytest now defaults to a self-cleaning per-run basetemp and sets"
echo "TMPDIR to it (tests/conftest.py); it also refuses to start below"
echo "--tmp-free-min-mb (default 2048). Override the root with"
echo "QUBIT_PYTEST_TMPROOT=/mnt/scratch, or pass --tmp-keep to inspect a run."
