#!/usr/bin/env bash
# Publish a rebuilt dashboard bundle into an INSTALLED Qubit package.
#
# WHY THIS EXISTS (the deployment trap):
#   voicebridge/resources.py:resource_path() resolves package data BEFORE the
#   checkout — `_PACKAGE_DATA/<name>` if it exists, else `<repo>/<name>`
#   (resources.py L24-L33). webui.py:_qubit_dist() (L2408-2414) calls it for
#   `frontend/qubit-dist`, so a bridge running from an installed wheel
#   (`<venv>/bin/python -m voicebridge run`) serves
#     <venv>/lib/pythonX.Y/site-packages/voicebridge/_data/frontend/qubit-dist
#   and NEVER the checkout at frontend/qubit-dist. Editing the checkout and
#   running scripts/build_dashboard.sh is therefore INVISIBLE to that server:
#   the rebuild sits in the checkout while the package copy keeps shadowing it.
#
# WHAT IT DOES: copies the checkout bundle over the installed one, verifying
# the destination is the tree resource_path() actually resolves. It never
# changes resolution order (a wheel install must keep winning over any stray
# checkout — that is the property that makes isolated installs work), so this
# is a publish step, not a resolver change.
#
# Usage:
#   scripts/sync_dashboard_to_install.sh            # sync into the resolved install
#   scripts/sync_dashboard_to_install.sh --python P  # sync into P's site-packages
#   scripts/sync_dashboard_to_install.sh --check    # report drift, change nothing
#
# Typed exit codes:
#   0  in sync (--check) or copied successfully
#   2  checkout bundle missing (build it: scripts/build_dashboard.sh)
#   3  the running/selected interpreter resolves the CHECKOUT, not an install
#      (nothing to shadow — no-op is the correct answer)
#   4  destination exists but is not writable / copy failed
#   5  drift detected (--check only)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$REPO_ROOT/frontend/qubit-dist"

PY=""
MODE="sync"
while [ $# -gt 0 ]; do
  case "$1" in
    --check) MODE="check"; shift ;;
    --python) PY="${2:-}"; [ -n "$PY" ] || { echo "ERROR: --python needs a path" >&2; exit 2; }; shift 2 ;;
    -h|--help) sed -n '2,32p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "ERROR: unknown arg: $1" >&2; exit 2 ;;
  esac
done
if [ -z "$PY" ]; then
  # Default to the interpreter that is meant to serve: an explicit
  # QUBIT_PYTHON, else the venv the desktop/live unit uses, else this repo's.
  if [ -n "${QUBIT_PYTHON:-}" ]; then PY="$QUBIT_PYTHON"
  elif [ -x "$REPO_ROOT/.venv/bin/python" ]; then PY="$REPO_ROOT/.venv/bin/python"
  else PY="$(command -v python3)"; fi
fi

if [ ! -f "$SRC/index.html" ]; then
  echo "ERROR: checkout bundle missing: $SRC/index.html" >&2
  echo "       build it first: scripts/build_dashboard.sh" >&2
  exit 2
fi

# Ask the interpreter itself which dist it serves — never guess the path shape.
# Run from a NEUTRAL cwd with PYTHONPATH cleared: `python -` puts the cwd on
# sys.path, so probing from the repo root imports the CHECKOUT voicebridge and
# reports the checkout as the resolution — the exact trap this script exists to
# make visible. Resolve the same way the service does: from the installed tree.
RESOLVED="$("$PY" - <<'PY'
import os
os.chdir("/")
import sys
sys.path[:] = [p for p in sys.path if p not in ("", os.getcwd())]
from voicebridge import resources
print(resources.resource_path("frontend/qubit-dist"))
PY
)" || { echo "ERROR: could not import voicebridge from $PY" >&2; exit 3; }

# Realpath both sides so a symlinked checkout/venv compares honestly.
SRC_REAL="$(readlink -f "$SRC")"
DST_REAL="$(readlink -f "$RESOLVED" 2>/dev/null || echo "$RESOLVED")"

echo "checkout : $SRC_REAL"
echo "resolved : $DST_REAL"

if [ "$SRC_REAL" = "$DST_REAL" ]; then
  echo "OK: this interpreter serves the CHECKOUT bundle — no package copy shadows it."
  exit 3
fi

case "$DST_REAL" in
  */site-packages/*) : ;;
  *) echo "WARNING: resolved path is not under site-packages: $DST_REAL" >&2 ;;
esac

if [ ! -d "$DST_REAL" ]; then
  echo "ERROR: resolved dist does not exist: $DST_REAL" >&2
  exit 4
fi

DRIFT="$(diff -rq "$SRC_REAL" "$DST_REAL" 2>/dev/null || true)"
if [ -z "$DRIFT" ]; then
  echo "OK: installed bundle is byte-identical to the checkout."
  exit 0
fi

if [ "$MODE" = "check" ]; then
  echo "DRIFT: installed bundle differs from the checkout:" >&2
  printf '%s\n' "$DRIFT" | head -40 >&2
  echo "       (the live server serves the INSTALLED copy; run without --check to publish)" >&2
  exit 5
fi

echo "== publishing checkout bundle -> installed package =="
# Copy CONTENTS of the checkout into the existing destination so stale hashed
# assets left by a previous build are replaced rather than merged forever.
# --delete keeps the install exactly equal to the checkout.
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete "$SRC_REAL"/ "$DST_REAL"/ || { echo "ERROR: rsync failed" >&2; exit 4; }
else
  # Fallback without rsync: clear then copy. Never partially merged.
  find "$DST_REAL" -mindepth 1 -maxdepth 1 -exec rm -rf {} + \
    || { echo "ERROR: could not clear $DST_REAL" >&2; exit 4; }
  cp -a "$SRC_REAL"/. "$DST_REAL"/ || { echo "ERROR: copy failed" >&2; exit 4; }
fi

if diff -rq "$SRC_REAL" "$DST_REAL" >/dev/null 2>&1; then
  echo "OK: installed bundle now matches the checkout."
  echo "    A running bridge picks this up on its next request; restart only if"
  echo "    your change added/removed routes in Python."
else
  echo "ERROR: post-copy verification failed" >&2
  exit 4
fi
