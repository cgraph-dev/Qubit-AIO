#!/bin/sh
# Install the Qubit messaging-gateway user unit (Owner Decision #16).
#
# Thin, idempotent wrapper over scripts/install_systemd_unit.sh: it pins
# SYSTEMD_UNIT_NAME=qubit-gateway.service so the same atomic/idempotent/
# backup-divergent-unit semantics apply, then renders the template. Nothing
# here enables or starts the unit, and no host service is touched unless the
# operator runs this explicitly.
#
# Usage: install_gateway_unit.sh <repo-root>
# Env:   INSTALL_FORCE_UNIT(=1 to overwrite a divergent installed unit),
#        SYSTEMD_UNIT_DIR (default $HOME/.config/systemd/user; override for
#        hermetic tests/deploys)
set -eu

REPO="${1:?usage: install_gateway_unit.sh <repo-root>}"
UNIT_NAME="qubit-gateway.service"
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

SYSTEMD_UNIT_NAME="$UNIT_NAME" exec "$HERE/install_systemd_unit.sh" "$REPO"
