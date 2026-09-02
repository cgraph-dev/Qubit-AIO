#!/usr/bin/env bash
# Run the voice bridge. Sets the CUDA lib path (Ollama-bundled cu12) so GPU ASR works.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LD_LIBRARY_PATH="${VOICE_BRIDGE_CUDA_LIBS:-/usr/local/lib/ollama/cuda_v12}:${LD_LIBRARY_PATH:-}"
export DISPLAY="${DISPLAY:-:0}"
exec "$PROJECT_DIR/.venv/bin/python" -m voicebridge "$@"
