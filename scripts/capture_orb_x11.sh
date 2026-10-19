#!/usr/bin/env bash
# Grab the LIVE X11 Qubit voice orb window to a PNG (owner convenience).
#
# The deterministic, machine-inspectable capture is
# tests/test_voice_orb_capture9u.py: it paints offscreen (no X server) and pins
# the frame with hashes and layer coverage. This script is only the live-view
# sibling for a human — it locates the running orb window by title and
# screenshots it to output/orb_capture/live/. NO test depends on it and it adds
# no hard dependency: it uses whichever of xdotool/import/xwd+convert exists and
# exits 0 with a note when none is present.
#
# Usage: scripts/capture_orb_x11.sh [output_dir]
set -euo pipefail

TITLE="${QUBIT_ORB_WINDOW_TITLE:-Qubit Voice Orb}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${1:-$ROOT/output/orb_capture/live}"
mkdir -p "$OUT_DIR"
OUT="$OUT_DIR/orb-$(date +%Y%m%d-%H%M%S).png"

if ! command -v xdotool >/dev/null 2>&1; then
    echo "xdotool not installed; skipping live capture"
    exit 0
fi

WID="$(xdotool search --onlyvisible --name "$TITLE" 2>/dev/null | tail -n1 || true)"
if [ -z "$WID" ]; then
    echo "no visible window titled '$TITLE'; is the orb running?"
    exit 1
fi

if command -v import >/dev/null 2>&1; then
    import -window "$WID" "$OUT"
elif command -v xwd >/dev/null 2>&1 && command -v convert >/dev/null 2>&1; then
    tmp="$(mktemp --suffix=.xwd)"
    xwd -silent -id "$WID" -out "$tmp"
    convert "$tmp" "$OUT"
    rm -f "$tmp"
else
    echo "neither ImageMagick 'import' nor 'xwd'+'convert' available; skipping"
    exit 0
fi

echo "wrote $OUT"
