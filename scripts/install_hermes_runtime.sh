#!/usr/bin/env bash
# Install the pinned Hermes runtime into Qubit-owned XDG space (Qubit Agent
# foundation). Never touches the operator's `hermes` binary or HERMES_HOME.
#
#   ./scripts/install_hermes_runtime.sh [--clone DIR] [--no-deps]
#
# Verifies the pin (git rev-parse), LICENSE, entry hashes, and live
# `hermes --version` before reporting success. Writes a local-only Ollama
# provider config (no keys, no cloud).Idempotent: reuses a valid install.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLONE=""
NO_DEPS=0
for arg in "$@"; do
  case "$arg" in
    --clone=*) CLONE="${arg#--clone=}" ;;
    --clone) shift; CLONE="${1:-}" ;;
    --no-deps) NO_DEPS=1 ;;
  esac
  shift || true
done

if [ -z "$CLONE" ]; then
  echo "ERROR: pass --clone <pinned-hermes-checkout> (never an operator install)" >&2
  exit 2
fi

export QUBIT_CHECKOUT="$PROJECT_DIR"
"$PROJECT_DIR/.venv/bin/python" - "$CLONE" "$NO_DEPS" << 'PYEOF'
import json
import os
import sys
sys.path.insert(0, os.environ["QUBIT_CHECKOUT"])
from voicebridge.runtime import provision as P

clone = sys.argv[1]
no_deps = sys.argv[2] == "1"
r = P.install_from_clone(clone, pip_install=not no_deps)
print(json.dumps({"ok": r.ok, "code": r.code,
                  "runtime_dir": str(r.runtime_dir), "detail": r.detail[-300:]}))
if r.ok:
    home = P.hermes_home()
    P.write_runtime_config(home)
    info = P.verify_installation(r.runtime_dir)
    print(json.dumps({"verify": info}))
sys.exit(0 if r.ok else 1)
PYEOF
