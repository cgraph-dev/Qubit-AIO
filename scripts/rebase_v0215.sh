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
# A successful build writes a completion marker
# (<candidate>/.rebase_v0215_state.json) recording the pin, an engine
# fingerprint, the completed stages, and a runtime tree hash.  ``--check``
# reuses a candidate only when that marker matches the current engines/pin and
# the runtime hash still matches, so a crash mid-build (e.g. after rebrand but
# before debrand) is detected and rebuilt rather than silently reused.
#
# Usage:
#   scripts/rebase_v0215.sh            # rebuild the candidate from scratch
#   scripts/rebase_v0215.sh --check    # build if absent/stale, then validate
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
#   QUBIT_LIVE_ROOT         product tree the guards protect
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
LIVE_ROOT="${QUBIT_LIVE_ROOT:-$HOME/Projects/Repos/qubit-voicebridge}"
MARKER_NAME=".rebase_v0215_state.json"
#: Modules the candidate must contain and import (the v0.21.5 token additions).
NEW_MODULES=(qubit_state_health qubit_yaml)

CHECK_ONLY=0

usage() {
  # Print the leading comment block only (skip the shebang, stop at the first
  # non-comment line); stripping one "#" and an optional following space.
  awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "$0"
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
LIVE_REAL="$(_real "$LIVE_ROOT")"
[ -n "$CAND_REAL" ] || die "empty candidate root"
[ "$CAND_REAL" != "/" ] || die "refusing candidate root=/"
[ "$CAND_REAL" != "$(_real "$HOME")" ] || die "refusing candidate root=HOME"

if [ "$(_real "$REPO_ROOT")" = "$LIVE_REAL" ] && \
   [ "${QUBIT_ALLOW_LIVE_WRITE:-0}" != "1" ]; then
  die "refusing to run from the live product tree $REPO_ROOT; run from the isolated worktree (set QUBIT_ALLOW_LIVE_WRITE=1 to override)"
fi
case "$CAND_REAL" in
  "$LIVE_REAL"/*) die "candidate root is inside the live product tree: $CAND_REAL" ;;
esac
case "$CAND_REAL" in
  /tmp/*|/var/tmp/*) : ;;
  *) [ "${QUBIT_ALLOW_NON_TMP:-0}" = "1" ] || \
       die "candidate root must live under /tmp (set QUBIT_ALLOW_NON_TMP=1 to override): $CAND_REAL" ;;
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
# completion marker (engine fingerprint + debrand-completion + tree integrity)
# ---------------------------------------------------------------------------
marker_tool() {
  local mode="$1" cand="$2" scripts_dir="$3" pin="$4"
  "$PYTHON" - "$mode" "$cand" "$scripts_dir" "$pin" <<'PY'
import hashlib
import json
import os
import sys

mode, cand, scripts_dir, pin = sys.argv[1:5]
marker = os.path.join(cand, ".rebase_v0215_state.json")
ENGINES = ("qubit_rebrand.py", "qubit_debrand.py")
STAGES = ("rebrand", "debrand", "validated")


def sha_file(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def engine_fingerprint():
    h = hashlib.sha256()
    for name in ENGINES:
        path = os.path.join(scripts_dir, name)
        if not os.path.isfile(path):
            return None
        h.update(f"{name}\0{sha_file(path)}\n".encode())
    return h.hexdigest()


def runtime_hash():
    root = os.path.join(cand, "qubit_runtime")
    if not os.path.isdir(root):
        return None, 0
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if name.endswith(".pyc"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            entries.append((rel, sha_file(path)))
    entries.sort(key=lambda row: row[0])
    h = hashlib.sha256()
    for rel, digest in entries:
        h.update(f"{rel}\0{digest}\n".encode())
    return h.hexdigest(), len(entries)


fingerprint = engine_fingerprint()
runtime_sha, runtime_count = runtime_hash()

if mode == "write":
    doc = {
        "schema": "qubit-v0215-candidate/1",
        "pin": pin,
        "engine_fingerprint": fingerprint,
        "runtime_sha256": runtime_sha,
        "runtime_file_count": runtime_count,
        "stages": list(STAGES),
    }
    with open(marker, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print(f"marker: engine={str(fingerprint)[:12]} runtime={str(runtime_sha)[:12]} "
          f"files={runtime_count}")
    sys.exit(0)

# check mode: every condition must hold for the candidate to be reused
try:
    with open(marker, encoding="utf-8") as fh:
        doc = json.load(fh)
except (OSError, ValueError):
    sys.exit(1)
if fingerprint is None or doc.get("engine_fingerprint") != fingerprint:
    sys.exit(1)
if doc.get("pin") != pin:
    sys.exit(1)
if not all(stage in doc.get("stages", []) for stage in STAGES):
    sys.exit(1)
if runtime_sha is None or doc.get("runtime_sha256") != runtime_sha:
    sys.exit(1)
sys.exit(0)
PY
}

# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------
candidate_valid() {
  [ -f "$CANDIDATE_ROOT/qubit_runtime/qubit_state_health.py" ] || return 1
  [ -f "$CANDIDATE_ROOT/scripts/qubit_debrand.py" ] || return 1
  marker_tool check "$CANDIDATE_ROOT" "$REPO_ROOT/scripts" "$PIN" || return 1
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
  # Only after both engines and the smoke pass is the completion marker written;
  # a crash before this point leaves no marker and forces a rebuild.
  marker_tool write "$CANDIDATE_ROOT" "$REPO_ROOT/scripts" "$PIN"
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
