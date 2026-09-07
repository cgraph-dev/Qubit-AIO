#!/bin/sh
# Atomic systemd user-unit installation for qubit-voicebridge (Packet 0B/8S).
#
# Semantics:
#   - renders the repository template and installs it ATOMICALLY (mktemp + mv);
#   - a divergent existing unit is PRESERVED unless INSTALL_FORCE_UNIT=1;
#     the rendered template lands beside it as <unit>.template-new for review;
#   - every overwrite of an existing unit first creates <unit>.bak.<timestamp>;
#     restoring that backup is the documented rollback.
#
# Usage: install_systemd_unit.sh <repo-root>
# Env:   HOME, INSTALL_FORCE_UNIT(=1 to force), SYSTEMD_UNIT_NAME (default
#        qubit-voicebridge.service; prior install names remain readable via
#        the Packet 8S compat-alias migration)
set -eu

REPO="${1:?usage: install_systemd_unit.sh <repo-root>}"
UNIT_NAME="${SYSTEMD_UNIT_NAME:-qubit-voicebridge.service}"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/$UNIT_NAME"
TEMPLATE="$REPO/systemd/$UNIT_NAME"

[ -f "$TEMPLATE" ] || { echo "FAIL template missing: $TEMPLATE" >&2; exit 3; }
mkdir -p "$UNIT_DIR"

TMP_RENDER="$(mktemp "$UNIT_DIR/.$UNIT_NAME.render.XXXXXX")"
trap 'rm -f "$TMP_RENDER"' EXIT
sed "s|__PROJECT_DIR__|$REPO|g" "$TEMPLATE" > "$TMP_RENDER"

if [ -f "$UNIT" ]; then
    if diff -q "$UNIT" "$TMP_RENDER" >/dev/null 2>&1; then
        echo "    unit already matches template (idempotent, untouched)"
        exit 0
    fi
    BACKUP="$UNIT.bak.$(date +%Y%m%d-%H%M%S)"
    cp -p "$UNIT" "$BACKUP"
    if [ "${INSTALL_FORCE_UNIT:-0}" != "1" ]; then
        mv -f "$TMP_RENDER" "$UNIT.template-new"
        trap - EXIT
        echo "    Existing tuned unit PRESERVED (machine overrides kept live)."
        echo "    Backup: $BACKUP"
        echo "    Review/apply: $UNIT.template-new"
        echo "    Force overwrite: INSTALL_FORCE_UNIT=1 $0 \"$REPO\""
        exit 0
    fi
    echo "    FORCED overwrite requested; previous unit backed up: $BACKUP"
fi

mv -f "$TMP_RENDER" "$UNIT"
trap - EXIT
echo "    installed atomically: $UNIT"
