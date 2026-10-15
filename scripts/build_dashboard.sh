#!/usr/bin/env bash
# Reproducible build of the vendored upstream dashboard served by Qubit.
#
# The upstream React/Vite source lives in frontend/qubit-web/ (pinned
# hermes-agent@2237be3); the generated bundle is written to
# frontend/qubit-dist/ and is served by voicebridge.webui from Qubit-owned
# root paths (base "/") and packaged into wheel/sdist via pyproject
# `artifacts`. Qubit overlay additions live in frontend/qubit-app/ and are
# NOT part of this build -- they are served as runtime plugins from
# frontend/qubit-app/plugins/.
#
# Usage:  scripts/build_dashboard.sh [--check]
#
# Typed exit codes (never a silent skip):
#   0  bundle present (--check) or build succeeded
#   3  DASHBOARD_UNAVAILABLE: node/npm missing and the bundle is not built
#   4  DASHBOARD_BUILD_FAILED: dependency install or vite build failed
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="$REPO_ROOT/frontend/qubit-web"
APPS_DIR="$REPO_ROOT/frontend/apps"
DIST_DIR="$REPO_ROOT/frontend/qubit-dist"
DIST_INDEX="$DIST_DIR/index.html"

unavailable() {
  echo "DASHBOARD_UNAVAILABLE: $*" >&2
  echo "  run scripts/build_dashboard.sh on a host with node/npm + network," >&2
  echo "  or install from a release archive that ships frontend/qubit-dist" >&2
  exit 3
}
build_failed() {
  echo "DASHBOARD_BUILD_FAILED: $*" >&2
  exit 4
}

# --check is a hermetic preflight: it never installs or builds anything, it
# only reports whether the bundle exists or can be built on this host.
if [ "${1:-}" = "--check" ]; then
  if [ -f "$DIST_INDEX" ]; then
    echo "DASHBOARD_PRESENT: $DIST_INDEX"
    exit 0
  fi
  if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
    echo "DASHBOARD_BUILDABLE: bundle missing; node+npm available to build it"
    exit 0
  fi
  unavailable "frontend/qubit-dist is not built and node/npm are not installed"
fi

command -v node >/dev/null 2>&1 || \
  unavailable "node is required to build the dashboard"
command -v npm >/dev/null 2>&1 || \
  unavailable "npm is required to build the dashboard"

# The shared workspace package is imported as @qubit/shared; the monorepo
# layout expects frontend/qubit-shared -> ../qubit-shared.
mkdir -p "$APPS_DIR"
ln -sfn ../qubit-shared "$APPS_DIR/shared"

cd "$WEB_DIR"
# A globally-exported NODE_ENV=production makes npm default to `omit=dev`, so
# `npm install` silently installs ONLY the production dependencies: no
# typescript, no vite. `npx tsc` then dies with "This is not the tsc command you
# are looking for" and -- with `set -e`/`|| build_failed` -- the build aborts
# before vite ever runs. The bundle keeps serving whatever stale build is on
# disk, which is how source edits can go missing for days. This is a build
# host, not a runtime host: force dev dependencies in for the build only.
export NODE_ENV=development
export npm_config_omit=
export npm_config_include=dev
npm install --include=dev --legacy-peer-deps --no-audit --no-fund \
  || build_failed "npm install failed (network/registry unavailable?)"
# Guard against a silently dev-less install regressing to the broken state.
if [ ! -x "$WEB_DIR/node_modules/.bin/tsc" ] || \
   [ ! -x "$WEB_DIR/node_modules/.bin/vite" ]; then
  build_failed "npm install omitted devDependencies (tsc/vite missing from node_modules/.bin); check NODE_ENV/npm_config_omit in the build environment"
fi
npx tsc -p . --noEmit || build_failed "TypeScript typecheck failed"
npx vite build --base=/ --outDir "$DIST_DIR" --emptyOutDir \
  || build_failed "vite build failed"

# node_modules is a build-only dependency, but pyproject.toml force-includes
# the whole frontend/ tree into the wheel; leaving it in place would ship
# ~400 MB of build tooling and trip the release content scan. Remove it after
# a successful build unless KEEP_NODE_MODULES=1 (faster incremental rebuilds).
if [ "${KEEP_NODE_MODULES:-0}" != "1" ]; then
  rm -rf "$WEB_DIR/node_modules"
  echo "removed $WEB_DIR/node_modules (KEEP_NODE_MODULES=1 to keep)"
fi

echo "DASHBOARD_BUILT: $DIST_DIR"
echo "dashboard built -> $DIST_DIR"
