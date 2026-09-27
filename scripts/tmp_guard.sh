#!/usr/bin/env bash
# Hard guard: refuse to start heavy work when / is below MIN_FREE_GB, and
# auto-reclaim disposable scratch first. Source or call before test waves.
set -u
MIN_FREE_GB="${MIN_FREE_GB:-8}"
ROOT="${TMP_GUARD_ROOT:-/}"
avail_kb=$(df -Pk "$ROOT" | awk 'NR==2{print $4}')
avail_gb=$(( avail_kb / 1024 / 1024 ))
if [ "$avail_gb" -lt "$MIN_FREE_GB" ]; then
  echo "tmp_guard: ${avail_gb}G free < ${MIN_FREE_GB}G — reclaiming..." >&2
  rm -rf /tmp/pytest-of-* /tmp/pt-* /tmp/vt* /tmp/qubit-a[0-9]* /tmp/qubit-*-home \
         /tmp/qubit-*-tmp /tmp/hermes-*-state-* /tmp/hermes-test-home-* 2>/dev/null || true
  bash "$(dirname "$0")/tmp_reclaim.sh" >/dev/null 2>&1 || true
  avail_kb=$(df -Pk "$ROOT" | awk 'NR==2{print $4}')
  avail_gb=$(( avail_kb / 1024 / 1024 ))
fi
if [ "$avail_gb" -lt "$MIN_FREE_GB" ]; then
  echo "tmp_guard: ABORT — still only ${avail_gb}G free (need ${MIN_FREE_GB}G)" >&2
  exit 3
fi
echo "tmp_guard: ${avail_gb}G free — ok"
