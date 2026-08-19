#!/usr/bin/env bash
# Run PersonaPlex in tmux (alternative to systemd) — handy for testing.
# Usage: ./run_personaplex.sh [tailnet-ip]
set -euo pipefail

TS_IP="${1:-$(tailscale ip -4 2>/dev/null | head -1)}"
: "${TS_IP:?tailscale IP not found — is tailscale up?}"
: "${HF_TOKEN:?set HF_TOKEN}"

SSL_DIR=/etc/personaplex/ssl
INSTALL_DIR=/opt/personaplex

tmux kill-session -t personaplex 2>/dev/null || true
tmux new-session -d -s personaplex \
  "cd $INSTALL_DIR && HF_TOKEN=$HF_TOKEN .venv/bin/python -m moshi.server \
   --host $TS_IP --port 8998 --ssl $SSL_DIR 2>&1 | tee /var/log/personaplex.log"

echo "PersonaPlex starting in tmux session 'personaplex' on ${TS_IP}:8998"
echo "  tmux attach -t personaplex   (logs)"
