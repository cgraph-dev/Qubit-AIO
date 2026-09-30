#!/usr/bin/env bash
# Rolling FULL backup of the Qubit workspace to the private GitHub repo.
#
# Keeps exactly ONE commit on the branch named by QUBIT_BACKUP_BRANCH
# (default: autobackup), force-updated each run, so there are never PRs to
# review and the branch always holds the latest snapshot. Nothing is deleted
# from `main`.
#
# It snapshots:
#   * the whole repository working tree (tracked + untracked, junk excluded);
#   * the live runtime home under _private_backup/runtime/ (credentials, state,
#     memories, sessions, checkpoints, cron, skills, plugins, pairing, ...)
#     -- ONLY when the GitHub repo is private (a public repo would leak them).
#
# Env overrides: QUBIT_BACKUP_REMOTE (default canonical), QUBIT_BACKUP_BRANCH
# (default autobackup), QUBIT_RUNTIME_HOME, QUBIT_BACKUP_LOG.
set -u

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(git -C "$SELF_DIR/.." rev-parse --show-toplevel 2>/dev/null || git -C "$PWD" rev-parse --show-toplevel 2>/dev/null || pwd)"
REMOTE="${QUBIT_BACKUP_REMOTE:-canonical}"
BRANCH="${QUBIT_BACKUP_BRANCH:-autobackup}"
RUNTIME="${QUBIT_RUNTIME_HOME:-$HOME/.local/state/qubit-voicebridge/runtime}"
SRC="${QUBIT_BACKUP_SRC:-$REPO}"   # workspace to snapshot (default: this checkout)
LOG="${QUBIT_BACKUP_LOG:-$HOME/.cache/qubit-autobackup.log}"

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG"; }

# Resolve the remote URL and whether the repo is private.
URL="$(git -C "$REPO" remote get-url "$REMOTE" 2>/dev/null || true)"
[ -z "$URL" ] && URL="$(git -C "$REPO" remote get-url origin 2>/dev/null || true)"
PRIVATE="no"
if command -v gh >/dev/null 2>&1 && [ -n "$URL" ]; then
  SLUG="$(printf '%s' "$URL" | sed -E 's#.*github\.com[:/]##; s#\.git$##')"
  priv="$(gh api "repos/$SLUG" --jq '.private' 2>/dev/null || true)"
  [ "$priv" = "true" ] && PRIVATE="yes"
fi

TMP="$(mktemp -d)"
cleanup() { git -C "$REPO" worktree remove --force "$TMP/wt" >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT

git -C "$REPO" worktree add --force --detach "$TMP/wt" >/dev/null 2>&1 || { log "worktree add failed"; exit 2; }
cd "$TMP/wt" || exit 2
git checkout --orphan "$BRANCH" >/dev/null 2>&1 || true
git rm -rf . >/dev/null 2>&1 || true

# 1) repository working tree (everything except throwaway/regenerable junk).
rsync -a --delete \
  --exclude '.git' --exclude 'node_modules/' --exclude '.venv/' \
  --exclude '__pycache__/' --exclude '.pytest_cache/' --exclude '.pytest-cache/' \
  --exclude 'MagicMock/' --exclude 'graft/' --exclude 'dist/' --exclude '.cache/' \
  --exclude 'sandboxes/' --exclude '*.sock' --exclude '*.lock' \
  "$SRC"/ "$TMP/wt"/ 2>/dev/null || true

# 2) private runtime snapshot -- only on a private repo.
if [ "$PRIVATE" = "yes" ] && [ -d "$RUNTIME" ]; then
  mkdir -p "$TMP/wt/_private_backup/runtime"
  rsync -a --delete \
    --exclude 'cache/' --exclude 'audio_cache/' --exclude 'image_cache/' \
    --exclude 'bin/' --exclude 'sandboxes/' --exclude '*.sock' --exclude '*.lock' \
    "$RUNTIME"/ "$TMP/wt/_private_backup/runtime/" 2>/dev/null || true
  for f in config/personal-knowledge.md config/task-templates.yaml; do
    [ -f "$SRC/$f" ] && cp -a "$SRC/$f" "$TMP/wt/_private_backup/$(basename "$f")"
  done
fi

git add -A
if git diff --cached --quiet; then log "no changes since last snapshot"; exit 0; fi
git -c user.name="${GIT_AUTHOR_NAME:-cgraph-dev}" \
    -c user.email="${GIT_AUTHOR_EMAIL:-contact@cgraph.org}" \
    commit -q -m "autobackup $(date '+%F %T') @ $(hostname)"
if git push --force "$REMOTE" "$BRANCH:$BRANCH" >/dev/null 2>&1; then
  log "pushed $BRANCH -> $(git rev-parse --short HEAD) (private_data=$PRIVATE)"
else
  log "PUSH FAILED (remote=$REMOTE url=$URL)"
  exit 3
fi
