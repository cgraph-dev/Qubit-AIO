#!/usr/bin/env bash
# Install the OpenClaw Voice Bridge: venv, deps, models, config.
# Idempotent. Does NOT touch the OpenClaw installation.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$PROJECT_DIR/.venv"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/openclaw-voice-bridge"
MODELS="$DATA_DIR/models"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/openclaw-voice-bridge"

echo "==> OpenClaw Voice Bridge install"
echo "    project : $PROJECT_DIR"
echo "    models  : $MODELS"

# 1. venv + deps
if [ ! -x "$VENV/bin/python" ]; then
  echo "==> creating venv (uv)"
  command -v uv >/dev/null || { echo "uv not found — install: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
  (cd "$PROJECT_DIR" && uv venv --python 3.12 .venv)
fi
echo "==> installing python deps"
uv pip install --python "$VENV/bin/python" \
  faster-whisper sounddevice numpy webrtcvad websockets pyyaml piper-tts setuptools sphn
# sphn (PersonaPlex duplex Opus streaming) needs libopus at runtime:
if ! ldconfig -p 2>/dev/null | grep -q libopus; then
  echo "==> NOTE: libopus missing — for PersonaPlex duplex mode run:"
  echo "    sudo apt install libopus-dev"
fi
# webrtcvad needs the legacy pkg_resources API (removed in setuptools 81+)
"$VENV/bin/python" -m pip install 'setuptools<81' -q 2>/dev/null || true
# hotkey support (needs X11 session)
uv pip install --python "$VENV/bin/python" pynput || true

# 2. piper voice
mkdir -p "$MODELS"
VOICE="en_US-amy-medium"
if [ ! -f "$MODELS/$VOICE.onnx" ]; then
  echo "==> downloading piper voice $VOICE (~63 MB)"
  curl -sL -o "$MODELS/$VOICE.onnx" \
    "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/amy/medium/$VOICE.onnx"
  curl -sL -o "$MODELS/$VOICE.onnx.json" \
    "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/amy/medium/$VOICE.onnx.json"
fi

# 3. config
mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG_DIR/voice.yaml" ]; then
  cp "$PROJECT_DIR/config/voice.example.yaml" "$CONFIG_DIR/voice.yaml"
  # voice tasks default to the fast fully-in-VRAM tier on this 8 GB box
  sed -i 's/^  model: null$/  model: ollama\/qwen3-vl:8b/' "$CONFIG_DIR/voice.yaml"
  echo "==> config created at $CONFIG_DIR/voice.yaml (voice model: qwen3-vl:8b)"
else
  echo "==> config already exists: $CONFIG_DIR/voice.yaml (leave as-is)"
fi

echo
echo "==> done. Run:"
echo "    $VENV/bin/python -m voicebridge status"
echo "    $VENV/bin/python -m voicebridge run"
