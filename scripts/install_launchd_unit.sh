#!/bin/sh
# Install the Qubit messaging-gateway LaunchAgent on macOS.
#
# Support boundary: macOS gets the core runtime, CLI, dashboard and ONE gateway
# LaunchAgent (ai.qubit.gateway[-<profile>].plist). The Qubit voice bridge,
# push-to-talk hotkey, orb and voice-typing are UNSUPPORTED on macOS and are
# never installed here.
#
# The plist body, label and QUBIT_HOME pin come from the runtime's own launchd
# implementation (qubit_cli.gateway_launchd); this script only refuses the
# wrong platform and drives that installer. Legacy ai.hermes.gateway* plists
# stay read-old and are never deleted.
#
# Usage: install_launchd_unit.sh [<repo-root>] [--dry-run] [--force]
# Env:   QUBIT_HOME (pinned into the plist),
#        QUBIT_LAUNCH_AGENTS_DIR (default ~/Library/LaunchAgents)
set -eu

DRY_RUN=0
FORCE=0
REPO=""

usage() {
    cat <<'EOF'
Install the Qubit gateway LaunchAgent on macOS.

Usage: install_launchd_unit.sh [<repo-root>] [--dry-run] [--force]

  --dry-run  print the LaunchAgent path and action; make no changes
  --force    reinstall/replace the existing ai.qubit.gateway plist

Voice bridge, hotkey, orb and voice-typing are not installed on macOS.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dry-run)
            DRY_RUN=1
            ;;
        --force)
            FORCE=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            echo "unknown option: $1" >&2
            echo "run install_launchd_unit.sh --help" >&2
            exit 2
            ;;
        *)
            if [ -n "$REPO" ]; then
                echo "unexpected argument: $1" >&2
                exit 2
            fi
            REPO="$1"
            ;;
    esac
    shift
done

if [ -z "$REPO" ]; then
    REPO="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
fi

HOST="$(uname -s 2>/dev/null || echo unknown)"
if [ "$HOST" != "Darwin" ]; then
    echo "UNSUPPORTED_PLATFORM: the gateway LaunchAgent is macOS-only (host: $HOST)." >&2
    echo "  Linux installs the systemd unit instead: scripts/install_gateway_unit.sh" >&2
    exit 2
fi
if ! command -v launchctl >/dev/null 2>&1; then
    echo "UNSUPPORTED_PLATFORM: launchctl is unavailable; a LaunchAgent cannot be loaded." >&2
    exit 2
fi

AGENTS_DIR="${QUBIT_LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
PLIST="$AGENTS_DIR/ai.qubit.gateway.plist"
PY="$REPO/.venv/bin/python"

# Standalone runs use the same runtime home default as scripts/install.sh; an
# explicit QUBIT_HOME (including a profile home) always wins.
QUBIT_HOME="${QUBIT_HOME:-$HOME/.qubit}"
export QUBIT_HOME

if [ "$DRY_RUN" = "1" ]; then
    echo "==> [dry-run] gateway LaunchAgent: $PLIST"
    if [ "$FORCE" = "1" ]; then
        echo "==> [dry-run] would install with --force and start it"
    else
        echo "==> [dry-run] would install with --if-missing and start it"
    fi
    echo "==> [dry-run] voice bridge/orb/typing: UNSUPPORTED on macOS (not installed)"
    echo "==> [dry-run] legacy ai.hermes.gateway* plists stay read-old, never deleted"
    exit 0
fi

if [ ! -x "$PY" ]; then
    echo "MISSING_VENV: $PY not found; run scripts/install.sh first." >&2
    exit 2
fi

if [ "$FORCE" = "1" ]; then
    MODE="force"
else
    MODE="if-missing"
fi

# Install/reload the LaunchAgent through the runtime's canonical launchd path.
"$PY" - "$MODE" <<'PY'
import sys

from voicebridge import qubit_runtime

mode = sys.argv[1]
argv = ["gateway", "install"]
argv.append("--force" if mode == "force" else "--if-missing")
raise SystemExit(qubit_runtime.run(argv))
PY

# Starting is separate so an already-installed but stopped agent comes back up
# (install --if-missing returns early when the plist already exists).
"$PY" - <<'PY'
from voicebridge import qubit_runtime

raise SystemExit(qubit_runtime.run(["gateway", "start"]))
PY

# Report (never remove) any legacy pre-rebrand agents. The runtime still
# accepts these labels read-old, so deleting them here would break that path.
for legacy in "$AGENTS_DIR"/ai.hermes.gateway*.plist; do
    [ -e "$legacy" ] || continue
    echo "legacy ai.hermes LaunchAgent (read-old, left in place): $legacy"
done

echo "==> gateway LaunchAgent installed: $PLIST"
echo "    status: launchctl print gui/$(id -u)/ai.qubit.gateway"
