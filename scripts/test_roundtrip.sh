#!/usr/bin/env bash
# Voice-to-worker roundtrip test (no microphone needed).
# Sets CUDA lib path so GPU ASR works, then runs scripts/test_roundtrip.py.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LD_LIBRARY_PATH="${VOICE_BRIDGE_CUDA_LIBS:-/usr/local/lib/ollama/cuda_v12}:${LD_LIBRARY_PATH:-}"
exec "$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/scripts/test_roundtrip.py" "$@"
