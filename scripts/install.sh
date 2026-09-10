#!/usr/bin/env bash
# Install Qubit Agent (Qubit VoiceBridge) on a FRESH machine (or re-run to repair).
# Idempotent. Qubit-only: it never installs, launches, or requires a foreign assistant.
#
#   ./scripts/install.sh                    # venv, deps, models, config
#   ./scripts/install.sh --with-systemd     # also install the systemd user service
#
# Requires: bash, curl, uv (https://astral.sh/uv), ollama (https://ollama.com),
#           a working mic + speaker, and (for hotkeys) an X11/Wayland session.
# Keys (optional, for complex/web answers): DEEPSEEK_API_KEY and
# PERPLEXITY_API_KEY in ~/.config/qubit-voicebridge/.env — see .env.example.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$PROJECT_DIR/.venv"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/qubit-voicebridge"
MODELS="$DATA_DIR/models"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/qubit-voicebridge"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/qubit-voicebridge"
OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"

SR="https://github.com/k2-fsa/sherpa-onnx/releases/download"

echo "==> Qubit Agent install"
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
  faster-whisper webrtcvad setuptools pynput pytest playwright edge-tts onnx 2>/dev/null \
  || uv pip install --python "$VENV/bin/python" \
     sherpa-onnx sounddevice numpy pyyaml websockets piper-tts \
     faster-whisper webrtcvad setuptools pynput pytest playwright edge-tts onnx
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

# sherpa-onnx ASR: Moonshine base (CPU, 0 VRAM) — fallback engine
MOON="sherpa-onnx-moonshine-base-en-quantized-2026-02-27"
if [ ! -d "$MODELS/$MOON" ]; then
  echo "==> downloading Moonshine ASR (~200 MB)"
  curl -sL -o /tmp/moonshine.tar.bz2 "$SR/asr-models/$MOON.tar.bz2"
  (cd "$MODELS" && tar xjf /tmp/moonshine.tar.bz2 && rm /tmp/moonshine.tar.bz2)
else
  echo "    have   $MOON"
fi

# sherpa-onnx ASR: Parakeet-TDT 0.6B v3 int8 (CPU, 0 VRAM) — DEFAULT engine
# (asr.engine: parakeet). 25 European languages incl. Romanian, max accuracy.
PKT="sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
if [ ! -d "$MODELS/$PKT" ]; then
  echo "==> downloading Parakeet-TDT ASR (~640 MB)"
  curl -sL -o /tmp/parakeet.tar.bz2 "$SR/asr-models/$PKT.tar.bz2"
  (cd "$MODELS" && tar xjf /tmp/parakeet.tar.bz2 && rm /tmp/parakeet.tar.bz2)
else
  echo "    have   $PKT"
fi
# sherpa 1.13.6 hard-errors without vocab_size/context_size metadata on the
# decoder/joiner ONNX — patch them in if missing (idempotent). Requires the
# optional 'onnx' package; skipping the patch only risks a sherpa load error,
# never a broken install.
if ! "$VENV/bin/python" -c 'import onnx' 2>/dev/null; then
  echo "    [warn] onnx not installed — skipping Parakeet metadata patch"
else
"$VENV/bin/python" - "$MODELS/$PKT" <<'PYEOF'
import sys, onnx
from pathlib import Path
base = Path(sys.argv[1])
n_tokens = len((base / "tokens.txt").read_text().splitlines())
for fname in ("decoder.int8.onnx", "joiner.int8.onnx"):
    p = base / fname
    if not p.exists():
        continue
    m = onnx.load(str(p))
    names = {x.key for x in m.metadata_props}
    changed = False
    if "vocab_size" not in names:
        m.metadata_props.add().key = "vocab_size"; m.metadata_props[-1].value = str(n_tokens)
        changed = True
    if "context_size" not in names:
        m.metadata_props.add().key = "context_size"; m.metadata_props[-1].value = "1"
        changed = True
    if changed:
        onnx.save(m, str(p))
        print(f"    patched {fname} metadata (vocab_size={n_tokens}, context_size=1)")
PYEOF
fi

# Silero VAD v5 (sherpa-onnx)
dl "$MODELS/silero_vad_v5.onnx" "$SR/asr-models/silero_vad_v5.onnx"
# Silero VAD v6 (Aug 2025: ~16% fewer errors than v5 on noisy real data) —
# the bridge prefers v6 and falls back to v5 automatically.
dl "$MODELS/silero_vad_v6.onnx" \
   "https://raw.githubusercontent.com/snakers4/silero-vad/v6.2.1/src/silero_vad/data/silero_vad.onnx"

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
  if ! ollama list | grep -q "huihui_ai/phi4-mini-abliterated"; then
    echo "==> pulling huihui_ai/phi4-mini-abliterated (local chat, 3.1GB resident, ~0.3s)"
    ollama pull huihui_ai/phi4-mini-abliterated
  else
    echo "    have   huihui_ai/phi4-mini-abliterated"
  fi
  # uncensored 3B kept as a fast fallback model (older, more generic answers)
  if ! ollama list | grep -q "huihui_ai/llama3.2-abliterate:3b"; then
    echo "==> pulling huihui_ai/llama3.2-abliterate:3b (3B fallback, ~2.2 GB)"
    ollama pull huihui_ai/llama3.2-abliterate:3b
  else
    echo "    have   huihui_ai/llama3.2-abliterate:3b"
  fi
  # OpenClaw gateway memory embeddings need this tiny model (274 MB CPU) —
  # the gateway spins on 'memory embeddings retryable error' without it
  if ! ollama list | grep -q "nomic-embed-text"; then
    echo "==> pulling nomic-embed-text (gateway memory embeddings, 274 MB)"
    ollama pull nomic-embed-text
  else
    echo "    have   nomic-embed-text"
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
  "$PROJECT_DIR/scripts/install_systemd_unit.sh" "$PROJECT_DIR"
else
  echo
  echo "==> done. Run:"
  echo "    $VENV/bin/python -m voicebridge status"
  echo "    $VENV/bin/python -m voicebridge run        # hotkey PTT loop"
  echo "    $VENV/bin/python -m voicebridge enroll     # enroll YOUR voice (voice-ID)"
  echo
  echo "    Auto-start instead?  $0 --with-systemd"
fi
