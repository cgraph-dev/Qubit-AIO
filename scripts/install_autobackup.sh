#!/usr/bin/env bash
# Install the Qubit rolling full-backup as a systemd user timer (every 15 min).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"

chmod +x "$REPO/scripts/backup_all.sh"
sed "s#__SCRIPT__#$REPO/scripts/backup_all.sh#" \
  "$REPO/systemd/qubit-autobackup.service" > "$UNIT_DIR/qubit-autobackup.service"
cp "$REPO/systemd/qubit-autobackup.timer" "$UNIT_DIR/qubit-autobackup.timer"

systemctl --user daemon-reload
systemctl --user enable --now qubit-autobackup.timer
echo "Installed. Next runs:"
systemctl --user list-timers qubit-autobackup.timer --no-pager
