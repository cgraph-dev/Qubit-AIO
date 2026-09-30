#!/usr/bin/env bash
# Ensure the Qubit Chromium profile browser is running with CDP on 9222.
# Usage: browser_ensure.sh   (starts it if not running)
set -euo pipefail

CDP_URL="http://127.0.0.1:9222/json"
PROFILE="/home/trick/snap/chromium/common/chromium"

if curl -s -o /dev/null -m 2 "$CDP_URL"; then
  echo "browser already running with CDP"
  exit 0
fi

# kill any instance holding the profile without CDP
pkill -f "user-data-dir=$PROFILE" 2>/dev/null || true
sleep 2

echo "starting Chromium with CDP on 9222..."
nohup env DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 \
  /snap/bin/chromium \
  --user-data-dir="$PROFILE" \
  --profile-directory=Default \
  --remote-debugging-port=9222 \
  --remote-allow-origins=* \
  --new-window "https://www.messenger.com/" \
  > /tmp/hermes-cdp-browser.log 2>&1 &

for i in $(seq 1 15); do
  sleep 2
  if curl -s -o /dev/null -m 2 "$CDP_URL"; then
    echo "browser up after ${i}x2s"
    exit 0
  fi
done
echo "browser did not come up; check /tmp/hermes-cdp-browser.log" >&2
exit 1
