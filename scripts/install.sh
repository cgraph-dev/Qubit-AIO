#!/usr/bin/env bash
# Install the OpenClaw Voice Bridge on a FRESH machine (or re-run to repair).
# Idempotent. Does NOT touch your OpenClaw installation, agents, or providers.
#
#   ./scripts/install.sh                    # venv, deps, models, config
#   ./scripts/install.sh --with-systemd     # also install the systemd user service
#
# Requires: bash, curl, uv (https://astral.sh/uv), ollama (https://ollama.com),
#           a working mic + speaker, and (for hotkeys) an X11/Wayland session.
# Keys (optional, for complex/web answers): DEEPSEEK_API_KEY and
# PERPLEXITY_API_KEY in ~/.hermes/.env — see .env.example.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$PROJECT_DIR/.venv"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/openclaw-voice-bridge"
MODELS="$DATA_DIR/models"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/openclaw-voice-bridge"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/openclaw-voice-bridge"
OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"

SR="https://github.com/k2-fsa/sherpa-onnx/releases/download"

echo "==> OpenClaw Voice Bridge install"
echo "    project : $PROJECT_DIR"
echo "    models  : $MODELS"
echo "    config  : $CONFIG_DIR/voice.yaml"

# ------------------------------------------------------------------ venv/deps
if [ ! -x "$VENV/bin/python" ]; then
  echo "==> creating venv (uv)"
  command -v uv >/dev/null || { echo "ERROR: uv not found — install: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
  (cd "$PROJECT_DIR" && uv venv --python 3.12 .venv)
fi
echo "==> installing python deps"
uv pip install --python "$VENV/bin/python" \
  sherpa-onnx sounddevice numpy pyyaml websockets piper-tts \
  faster-whisper webrtcvad setuptools pynput pytest 2>/dev/null \
  || uv pip install --python "$VENV/bin/python" \
     sherpa-onnx sounddevice numpy pyyaml websockets piper-tts \
     faster-whisper webrtcvad setuptools pynput pytest
# webrtcvad needs the legacy pkg_resources API (removed in setuptools 81+)
"$VENV/bin/python" -m pip install 'setuptools<81' -q 2>/dev/null || true

# ------------------------------------------------------------------ models
mkdir -p "$MODELS"
dl() { # dl <dest> <url>
  local dest="$1" url="$2"
  if [ ! -f "$dest" ]; then
    echo "==> downloading $(basename "$dest")"
    curl -sL -o "$dest" "$url"
  else
    echo "    have   $(basename "$dest")"
  fi
}

# piper voice (TTS)
VOICE="en_US-amy-medium"
dl "$MODELS/$VOICE.onnx" \
   "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/amy/medium/$VOICE.onnx"
dl "$MODELS/$VOICE.onnx.json" \
   "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/amy/medium/$VOICE.onnx.json"

# sherpa-onnx ASR: Moonshine base (CPU, 0 VRAM)
MOON="sherpa-onnx-moonshine-base-en-quantized-2026-02-27"
if [ ! -d "$MODELS/$MOON" ]; then
  echo "==> downloading Moonshine ASR (~200 MB)"
  curl -sL -o /tmp/moonshine.tar.bz2 "$SR/asr-models/$MOON.tar.bz2"
  (cd "$MODELS" && tar xjf /tmp/moonshine.tar.bz2 && rm /tmp/moonshine.tar.bz2)
else
  echo "    have   $MOON"
fi

# Silero VAD v5 (sherpa-onnx)
dl "$MODELS/silero_vad_v5.onnx" "$SR/asr-models/silero_vad_v5.onnx"

# Speaker verification (voice-ID): eres2net English
dl "$MODELS/3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx" \
   "$SR/speaker-recongition-models/3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx"

# Optional: Parakeet-TDT (max ASR accuracy) and Kokoro (quality TTS) —
# uncomment if you want them; config knobs: asr.engine / tts.provider.
# curl -sL -o /tmp/p.tar.bz2 "$SR/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2" && (cd "$MODELS" && tar xjf /tmp/p.tar.bz2 && rm /tmp/p.tar.bz2)
# curl -sL -o /tmp/k.tar.bz2 "$SR/tts-models/kokoro-int8-en-v0_19.tar.bz2" && (cd "$MODELS" && tar xjf /tmp/k.tar.bz2 && rm /tmp/k.tar.bz2)

# ------------------------------------------------------------------ ollama
echo "==> checking ollama"
if command -v ollama >/dev/null; then
  if ! ollama list >/dev/null 2>&1; then
    echo "    (starting ollama daemon…)"
    (ollama serve >/dev/null 2>&1 &) || true
    for i in $(seq 1 15); do ollama list >/dev/null 2>&1 && break; sleep 1; done
  fi
  if ! ollama list | grep -q "llama3.2:3b"; then
    echo "==> pulling llama3.2:3b (local replies, ~2 GB)"
    ollama pull llama3.2:3b
  else
    echo "    have   llama3.2:3b"
  fi
else
  echo "    WARNING: ollama not found — install https://ollama.com then re-run"
  echo "    (local short replies need llama3.2:3b; DeepSeek API still works)"
fi

# ------------------------------------------------------------------ config
mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG_DIR/voice.yaml" ]; then
  cp "$PROJECT_DIR/config/voice.example.yaml" "$CONFIG_DIR/voice.yaml"
  echo "==> config created: $CONFIG_DIR/voice.yaml"
else
  echo "==> config exists:  $CONFIG_DIR/voice.yaml (leave as-is; merge new keys from"
  echo "    config/voice.example.yaml manually if you want them)"
fi
# personal knowledge for the local chat model (user-editable)
if [ ! -f "$CONFIG_DIR/personal-knowledge.md" ]; then
  cp "$PROJECT_DIR/config/personal-knowledge.md" "$CONFIG_DIR/personal-knowledge.md"
  echo "==> personal-knowledge.md created: edit it, then restart the bridge"
else
  echo "==> personal-knowledge.md exists (leave as-is)"
fi

# ------------------------------------------------------------- systemd (opt)
if [ "${1:-}" = "--with-systemd" ] || [ "${1:-}" = "-s" ]; then
  echo "==> installing systemd user service"
  mkdir -p "$HOME/.config/systemd/user"
  sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
    "$PROJECT_DIR/systemd/openclaw-voice-bridge.service" \
    > "$HOME/.config/systemd/user/openclaw-voice-bridge.service"
  systemctl --user daemon-reload
  systemctl --user enable --now openclaw-voice-bridge.service
  echo "    enabled: systemctl --user status openclaw-voice-bridge"
else
  echo
  echo "==> done. Run:"
  echo "    $VENV/bin/python -m voicebridge status"
  echo "    $VENV/bin/python -m voicebridge run        # hotkey PTT loop"
  echo "    $VENV/bin/python -m voicebridge enroll     # enroll YOUR voice (voice-ID)"
  echo
  echo "    Auto-start instead?  $0 --with-systemd"
fi
