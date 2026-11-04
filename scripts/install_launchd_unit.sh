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
#        QUBIT_LAUNCH_AGENTS_DIR (pre-venv dry-run fallback only; with a venv
#        the runtime resolves the account ~/Library/LaunchAgents path)
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
PY="$REPO/.venv/bin/python"

# Standalone runs use the same runtime home default as scripts/install.sh; an
# explicit QUBIT_HOME (including a profile home) always wins.
QUBIT_HOME="${QUBIT_HOME:-$HOME/.qubit}"
export QUBIT_HOME

# The runtime owns the plist path and label: a profile or other non-default
# QUBIT_HOME installs a home-scoped `ai.qubit.gateway-<suffix>.plist` under the
# ACCOUNT home. Resolve it from the same runtime before/after installing, so a
# --prefix or profile install never reports the default-home path it did not
# write. Before the venv exists (dry-run on a fresh host) the default-home path
# is the only predictable fallback.
resolve_plist() {
    if [ -x "$PY" ]; then
        local resolved
        resolved="$("$PY" -c 'from voicebridge import qubit_runtime; qubit_runtime.ensure_path(); from qubit_cli import gateway; print(gateway.get_launchd_plist_path())' 2>/dev/null || true)"
        if [ -n "$resolved" ]; then
            printf '%s\n' "$resolved"
            return 0
        fi
    fi
    printf '%s\n' "$AGENTS_DIR/ai.qubit.gateway.plist"
}

PLIST="$(resolve_plist)"
PLIST_DIR="$(dirname "$PLIST")"

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

# A zero exit from the runtime is not proof the service exists: `gateway
# install` can return early (already installed, refused temp home) or resolve a
# different home-scoped path than the legacy default. Verify the resolved plist
# before claiming success, so the caller's DEGRADED branch is truthful.
if [ ! -f "$PLIST" ]; then
    echo "LAUNCHD_PLIST_MISSING: the gateway LaunchAgent was not written at $PLIST." >&2
    echo "  The runtime exited 0 but no plist exists; the gateway is NOT installed." >&2
    echo "  run: $REPO/.venv/bin/qubit gateway status" >&2
    exit 1
fi

LABEL="$(basename "$PLIST" .plist)"

# Report (never remove) any legacy pre-rebrand agents. The runtime still
# accepts these labels read-old, so deleting them here would break that path.
for legacy in "$PLIST_DIR"/ai.hermes.gateway*.plist; do
    [ -e "$legacy" ] || continue
    echo "legacy ai.hermes LaunchAgent (read-old, left in place): $legacy"
done

echo "==> gateway LaunchAgent installed: $PLIST"
echo "    status: launchctl print gui/$(id -u)/$LABEL"
