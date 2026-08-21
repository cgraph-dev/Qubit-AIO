#!/usr/bin/env bash
# voice-dashboard-tunnel.sh — expose the voice dashboard (port 8890) via Pinggy.
#
# Free tier: 60-min tunnels, random URL. This script auto-restarts the SSH
# session when it dies and prints the CURRENT public URL to stdout + a file
# so the frontend can always find the live endpoint.
#
# Usage:
#   ./voice-dashboard-tunnel.sh            # run forever (systemd/terminal)
#   ./voice-dashboard-tunnel.sh --once     # single tunnel, print URL, exit
#
# Config:
#   VOICE_TUNNEL_TOKEN   bearer token gate (Pinggy k:). Empty = no gate.
#   VOICE_TUNNEL_URLFILE where to write the current URL
#                        (default: ~/.local/state/openclaw-voice-bridge/tunnel.url)
set -euo pipefail

PORT="${VOICE_TUNNEL_PORT:-8890}"
TOKEN="${VOICE_TUNNEL_TOKEN:-}"
URLFILE="${VOICE_TUNNEL_URLFILE:-$HOME/.local/state/openclaw-voice-bridge/tunnel.url}"
LOG="/tmp/voice-dashboard-tunnel.log"
mkdir -p "$(dirname "$URLFILE")"

# Build the pinggy username: optional bearer gate + free tier
USERARG="free"
if [ -n "$TOKEN" ]; then
  USERARG="k:${TOKEN}+free"
fi

start_tunnel() {
  : > "$LOG"
  ssh -p 443 \
      -o StrictHostKeyChecking=no \
      -o UserKnownHostsFile=/dev/null \
      -o ServerAliveInterval=30 \
      -o ServerAliveCountMax=3 \
      -o ExitOnForwardFailure=yes \
      -R0:localhost:"$PORT" "$USERARG@a.pinggy.io" \
      > "$LOG" 2>&1 &
  SSH_PID=$!
}

wait_for_url() {
  for _ in $(seq 1 20); do
    URL=$(grep -oE 'https://[a-z0-9-]+\.[a-z0-9-]+\.(pinggy\.link|pinggy-free\.link|pinggy\.net)' "$LOG" | head -1 || true)
    if [ -n "${URL:-}" ]; then
      echo "$URL" > "$URLFILE"
      echo "voice dashboard public URL: $URL"
      echo "(token: ${TOKEN:-none})"
      return 0
    fi
    sleep 1
  done
  echo "WARNING: no URL yet — check $LOG" >&2
  return 1
}

if [ "${1:-}" = "--once" ]; then
  start_tunnel
  wait_for_url || true
  echo "$SSH_PID" > /tmp/voice-dashboard-tunnel.pid
  wait "$SSH_PID"
  exit 0
fi

echo "voice dashboard tunnel: port $PORT, auto-restart on"
while true; do
  start_tunnel
  if wait_for_url; then
    wait "$SSH_PID" || true   # keep alive; loop restarts if SSH dies
  fi
  echo "tunnel died $(date +%H:%M:%S); restarting in 3s" >&2
  sleep 3
done
