#!/usr/bin/env bash
# scripts/rebase_v0215.sh -- generate the v0.21.5 (69948c0057) rebrand candidate.
#
# Stages the pinned upstream, runs the two deterministic engines
# (qubit_rebrand.py then qubit_debrand.py), validates the result, and emits a
# delta manifest with the four buckets {identical, differing, only_candidate,
# only_current} comparing the candidate runtime to the current one.
#
# The candidate is always built under a temp root (default
# /tmp/qubit-v0215-candidate).  This script NEVER writes the live product tree.
#
# Usage:
#   scripts/rebase_v0215.sh            # rebuild the candidate from scratch
#   scripts/rebase_v0215.sh --check    # build if absent, then validate + delta
#   scripts/rebase_v0215.sh --force    # same as the default (explicit rebuild)
#   scripts/rebase_v0215.sh --help
#
# Env overrides:
#   QUBIT_PIN               target commit (default 69948c0057...)
#   QUBIT_PINNED_SOURCE     pre-extracted pin tree (default /tmp/qubit-1to1-*/ref)
#   QUBIT_REFERENCE_HOME    git checkout of the pin (default ~/.hermes/hermes-agent)
#   QUBIT_CANDIDATE_ROOT    temp output root (default /tmp/qubit-v0215-candidate)
#   QUBIT_CURRENT_ROOT      tree to diff against (default the repo root)
#   QUBIT_DELTA_OUT         delta manifest path (default docs/generated/audit/...)
#   QUBIT_PYTHON            python interpreter (default python3)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PIN="${QUBIT_PIN:-69948c005791d4d0e51670ca015238c2c7663749}"
REFERENCE_HOME="${QUBIT_REFERENCE_HOME:-$HOME/.hermes/hermes-agent}"
PINNED_SOURCE="${QUBIT_PINNED_SOURCE:-/tmp/qubit-1to1-20260925-082150/ref}"
CANDIDATE_ROOT="${QUBIT_CANDIDATE_ROOT:-/tmp/qubit-v0215-candidate}"
CURRENT_ROOT="${QUBIT_CURRENT_ROOT:-$REPO_ROOT}"
DELTA_OUT="${QUBIT_DELTA_OUT:-$REPO_ROOT/docs/generated/audit/V0_21_5_DELTA.json}"
PYTHON="${QUBIT_PYTHON:-python3}"
LIVE_ROOT="${QUBIT_LIVE_ROOT:-/home/trick/Projects/Repos/qubit-voicebridge}"
#: Modules the candidate must contain and import (the v0.21.5 token additions).
NEW_MODULES=(qubit_state_health qubit_yaml)

CHECK_ONLY=0

usage() {
  sed -n '2,28p' "$0"
}

for arg in "$@"; do
  case "$arg" in
    --check) CHECK_ONLY=1 ;;
    --force|--build) CHECK_ONLY=0 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "rebase_v0215: unknown argument: $arg" >&2; exit 2 ;;
  esac
done

die() { echo "rebase_v0215: $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# guards -- the live product tree is never written
# ---------------------------------------------------------------------------
_real() { realpath -m "$1"; }

CAND_REAL="$(_real "$CANDIDATE_ROOT")"
[ -n "$CAND_REAL" ] || die "empty candidate root"
[ "$CAND_REAL" != "/" ] || die "refusing candidate root=/"
[ "$CAND_REAL" != "$(_real "$HOME")" ] || die "refusing candidate root=HOME"

case "$CAND_REAL" in
  /tmp/*|/var/tmp/*) : ;;
  *) [ "${QUBIT_ALLOW_NON_TMP:-0}" = "1" ] || \
       die "candidate root must live under /tmp (set QUBIT_ALLOW_NON_TMP=1 to override): $CAND_REAL" ;;
esac

if [ "$(_real "$REPO_ROOT")" = "$(_real "$LIVE_ROOT")" ] && \
   [ "${QUBIT_ALLOW_LIVE_WRITE:-0}" != "1" ]; then
  die "refusing to run from the live product tree $REPO_ROOT; run from the isolated worktree (set QUBIT_ALLOW_LIVE_WRITE=1 to override)"
fi
case "$CAND_REAL" in
  "$(_real "$LIVE_ROOT")"/*) die "candidate root is inside the live product tree: $CAND_REAL" ;;
esac

# ---------------------------------------------------------------------------
# pin source resolution + verification
# ---------------------------------------------------------------------------
has_ref_git() { [ -e "$REFERENCE_HOME/.git" ] || [ -e "$REFERENCE_HOME/HEAD" ]; }

fetch_pin_into() {
  local dest="$1"
  mkdir -p "$dest"
  if [ -d "$PINNED_SOURCE" ]; then
    cp -a "$PINNED_SOURCE/." "$dest/"
    echo "staged pin from $PINNED_SOURCE"
  elif has_ref_git; then
    git -C "$REFERENCE_HOME" archive --format=tar "$PIN" | tar -x -C "$dest"
    echo "staged pin via git archive $PIN"
  else
    die "no pinned source: $PINNED_SOURCE absent and $REFERENCE_HOME is not a git checkout"
  fi
}

verify_pin() {
  has_ref_git || { echo "note: skipping pin identity check (no git at $REFERENCE_HOME)"; return 0; }
  local head
  head="$(git -C "$REFERENCE_HOME" rev-parse HEAD)"
  [ "$head" = "$PIN" ] || die "reference HEAD is $head, expected pin $PIN"
  [ -d "$PINNED_SOURCE" ] || return 0
  local rel pb rb
  for rel in pyproject.toml cli.py agent/__init__.py README.md; do
    [ -f "$PINNED_SOURCE/$rel" ] || continue
    pb="$(git hash-object "$PINNED_SOURCE/$rel")"
    rb="$(git -C "$REFERENCE_HOME" rev-parse "HEAD:$rel")"
    [ "$pb" = "$rb" ] || die "pinned $rel does not match $PIN"
  done
  echo "pin identity verified against $REFERENCE_HOME"
}

# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------
candidate_valid() {
  [ -f "$CANDIDATE_ROOT/qubit_runtime/qubit_state_health.py" ] || return 1
  [ -f "$CANDIDATE_ROOT/scripts/qubit_rebrand.py" ] || return 1
  return 0
}

build_candidate() {
  verify_pin
  echo "building candidate at $CANDIDATE_ROOT"
  rm -rf "$CANDIDATE_ROOT"
  mkdir -p "$CANDIDATE_ROOT/qubit_hermes" "$CANDIDATE_ROOT/scripts"
  fetch_pin_into "$CANDIDATE_ROOT/qubit_hermes"
  cp "$REPO_ROOT/scripts/qubit_rebrand.py" "$CANDIDATE_ROOT/scripts/"
  cp "$REPO_ROOT/scripts/qubit_debrand.py" "$CANDIDATE_ROOT/scripts/"
  ( cd "$CANDIDATE_ROOT" && "$PYTHON" scripts/qubit_rebrand.py --apply )
  ( cd "$CANDIDATE_ROOT" && "$PYTHON" scripts/qubit_debrand.py --apply )
}

# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------
validate_candidate() {
  local pkg="$CANDIDATE_ROOT/qubit_runtime"
  [ -d "$pkg" ] || die "candidate runtime missing: $pkg"
  local mod
  for mod in "${NEW_MODULES[@]}"; do
    [ -f "$pkg/$mod.py" ] || die "candidate missing $mod.py (v0.21.5 token not mapped)"
  done
  local home="$CANDIDATE_ROOT/_throwaway_home"
  mkdir -p "$home"
  local imports
  imports="$(printf '%s, ' "${NEW_MODULES[@]}")"
  imports="${imports%, }"
  if ! env -u PYTHONPATH HOME="$home" QUBIT_HOME="$home/qh" \
        PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -B -c \
        "import sys; sys.path.insert(0, sys.argv[1]); import $imports; print('IMPORT_OK')" \
        "$pkg"; then
    die "candidate import smoke failed"
  fi
  [ ! -e "$home/.hermes" ] || die "candidate import created the read-only reference home"
  echo "candidate validated ($pkg)"
}

# ---------------------------------------------------------------------------
# delta manifest
# ---------------------------------------------------------------------------
write_delta() {
  "$PYTHON" - "$CANDIDATE_ROOT/qubit_runtime" "$CURRENT_ROOT/qubit_runtime" \
             "$DELTA_OUT" "$PIN" "$CANDIDATE_ROOT" "$CURRENT_ROOT" <<'PY'
import hashlib
import json
import os
import sys

cand, cur, out, pin, cand_root, cur_root = sys.argv[1:7]


def index(root):
    files = {}
    if not os.path.isdir(root):
        return files
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if name.endswith(".pyc"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            try:
                with open(path, "rb") as fh:
                    files[rel] = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                continue
    return files


c, u = index(cand), index(cur)
shared = set(c) & set(u)
identical = sorted(k for k in shared if c[k] == u[k])
differing = sorted(k for k in shared if c[k] != u[k])
only_candidate = sorted(set(c) - set(u))
only_current = sorted(set(u) - set(c))
doc = {
    "schema": "qubit-v0215-delta/1",
    "pin": pin,
    "candidate_root": os.path.abspath(cand_root),
    "current_root": os.path.abspath(cur_root),
    "counts": {
        "identical": len(identical),
        "differing": len(differing),
        "only_candidate": len(only_candidate),
        "only_current": len(only_current),
    },
    "identical": identical,
    "differing": differing,
    "only_candidate": only_candidate,
    "only_current": only_current,
}
parent = os.path.dirname(out)
if parent:
    os.makedirs(parent, exist_ok=True)
with open(out, "w", encoding="utf-8") as fh:
    json.dump(doc, fh, indent=1, sort_keys=True)
    fh.write("\n")
print("delta: identical={identical} differing={differing} "
      "only_candidate={only_candidate} only_current={only_current}".format(**doc["counts"]))
print(f"wrote {out}")
PY
}

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
if [ "$CHECK_ONLY" = "1" ] && candidate_valid; then
  echo "candidate already present, reusing $CANDIDATE_ROOT"
else
  build_candidate
fi
validate_candidate
write_delta
echo "done: candidate at $CANDIDATE_ROOT"
