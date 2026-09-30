#!/usr/bin/env bash
# Install the Qubit rolling full-backup as a systemd user timer (every 15 min).
#
#   scripts/install_autobackup.sh [WORKSPACE]
#
# WORKSPACE defaults to this checkout; the timer will snapshot that workspace
# plus the runtime home into the single force-updated 'autobackup' branch.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${1:-$REPO}"
UNIT_DIR="$HOME/.config/systemd/user"
BIN="$HOME/.local/bin/qubit-autobackup.sh"
mkdir -p "$UNIT_DIR" "$(dirname "$BIN")"

cp "$REPO/scripts/backup_all.sh" "$BIN"
chmod +x "$BIN"

cat > "$UNIT_DIR/qubit-autobackup.service" <<EOF
[Unit]
Description=Qubit rolling full backup (private GitHub snapshot)
Wants=network-online.target

[Service]
Type=oneshot
Environment=QUBIT_BACKUP_SRC=$SRC
ExecStart=$BIN
EOF
cp "$REPO/systemd/qubit-autobackup.timer" "$UNIT_DIR/qubit-autobackup.timer"

systemctl --user daemon-reload
systemctl --user enable --now qubit-autobackup.timer
echo "Installed for workspace: $SRC"
systemctl --user list-timers qubit-autobackup.timer --no-pager
