#!/usr/bin/env bash
# Reproducible build of the vendored Hermes dashboard served by Qubit.
#
# The Hermes React/Vite source lives in frontend/hermes-web/ (pinned
# hermes-agent@2237be3); the generated bundle is written to
# frontend/hermes-dist/ and is served by voicebridge.webui at /hermes/ (and
# packaged into wheel/sdist via pyproject `artifacts`). Qubit overlay
# additions live in frontend/hermes-qubit/ and are NOT part of this build --
# they are served as runtime plugins from frontend/hermes-qubit/plugins/.
#
# Usage:  scripts/build_dashboard.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="$REPO_ROOT/frontend/hermes-web"
APPS_DIR="$REPO_ROOT/frontend/apps"
DIST_DIR="$REPO_ROOT/frontend/hermes-dist"

command -v node >/dev/null 2>&1 || {
  echo "ERROR: node is required to build the dashboard" >&2; exit 1; }
command -v npm >/dev/null 2>&1 || {
  echo "ERROR: npm is required to build the dashboard" >&2; exit 1; }

# The shared workspace package is imported as @hermes/shared; the monorepo
# layout expects frontend/apps/shared -> ../hermes-shared.
mkdir -p "$APPS_DIR"
ln -sfn ../hermes-shared "$APPS_DIR/shared"

cd "$WEB_DIR"
npm install --legacy-peer-deps --no-audit --no-fund
npx tsc -p . --noEmit
npx vite build --base=/hermes/ --outDir "$DIST_DIR" --emptyOutDir

echo "dashboard built -> $DIST_DIR"
