#!/usr/bin/env bash
# Prepare a git worktree for upstream-sync work by an agent.
#
# Three things every worktree needs and none of them are tracked, so a fresh
# `git worktree add` silently lacks them and the agent then measures the wrong
# environment (system python -> 23 bogus "No module named ruamel" gate failures)
# or crashes on binary blobs.
#
# Usage: scripts/sync_setup_worktree.sh /tmp/qb-sync-agentN
set -euo pipefail

WT="${1:?usage: sync_setup_worktree.sh <worktree-path>}"
MAIN="$(git -C "$(dirname "$0")/.." rev-parse --path-format=absolute --show-toplevel)"

if [ ! -d "$WT" ]; then
  echo "no such worktree: $WT" >&2
  exit 1
fi

# 1. Copy the current sync tooling so the agent runs the fixed scripts rather
#    than whatever stale copy the worktree branch carries.
cp "$MAIN"/scripts/sync_*.py "$WT"/scripts/ 2>/dev/null || true
echo "tooling copied: $(ls "$WT"/scripts/sync_*.py 2>/dev/null | wc -l) scripts"

# 2. Link the venv. It is untracked, so a worktree checkout has none. The gate
#    resolves the main worktree's venv automatically now, but tools run by hand
#    (pytest, python -c) still need it present.
if [ ! -e "$WT/.venv" ] && [ -d "$MAIN/.venv" ]; then
  ln -s "$MAIN/.venv" "$WT/.venv"
  echo "venv linked: $WT/.venv -> $MAIN/.venv"
fi

# 3. Keep the .venv symlink out of `git status` so the sync tool's dirty-tree
#    guard does not refuse to run. Exclude is local-only and never committed.
EXCLUDE="$(git -C "$WT" rev-parse --git-dir)/info/exclude"
mkdir -p "$(dirname "$EXCLUDE")"
grep -qxF '.venv' "$EXCLUDE" 2>/dev/null || echo '.venv' >> "$EXCLUDE"
echo "excluded .venv from status"

echo "ready: $WT"
