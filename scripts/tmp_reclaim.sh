#!/usr/bin/env bash
# Reclaim scratch space from Qubit/SDD test runs and audits without touching
# anything the rebuild still needs. Safe to run repeatedly.
#
# Usage: scripts/tmp_reclaim.sh [--aggressive]
#
# Safety: only removes regenerable scratch under /tmp — pytest temp roots,
# throwaway HOME/QUBIT_HOME probe dirs, seeded reference clones, and audit
# log dirs. It never touches the live repo, ~/.hermes, or ~/.local/state.
set -u

aggressive=0
[ "${1:-}" = "--aggressive" ] && aggressive=1

before=$(df -h / | awk 'NR==2{print $4}')

# Test scratch + throwaway homes (always disposable).
rm -rf \
  /tmp/pytest-of-"${USER:-trick}" /tmp/pytest-of-* \
  /tmp/a[0-9]-pt /tmp/a[0-9]-home /tmp/a[0-9]-tmp /tmp/a[0-9]-run \
  /tmp/qubit-*-home /tmp/qubit-*-tmp /tmp/qubit-*-throwaway /tmp/qbh* \
  /tmp/hermes-*-state-* /tmp/hermes-test-home-* 2>/dev/null || true

# Seeded reference clones used only by the vendored determinism gates; they are
# re-materialized on demand from the pin. Keep unless --aggressive.
if [ "$aggressive" -eq 1 ]; then
  rm -rf /tmp/a4-logs /tmp/qubit-task6-* /tmp/qubit-task6-* 2>/dev/null || true
fi

after=$(df -h / | awk 'NR==2{print $4}')
echo "tmp_reclaim: free ${before} -> ${after}"
echo "Guard: run tests with an isolated basetemp, e.g."
echo "  pytest --basetemp=/tmp/pt-\$\$ -p no:cacheprovider"
echo "or set TMPDIR to a quota-managed location."
