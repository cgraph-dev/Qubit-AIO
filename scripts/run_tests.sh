#!/usr/bin/env bash
# Thin wrapper: the real runner lives at qubit_runtime/scripts/run_tests.sh and resolves
# its test paths relative to qubit_runtime/. Docs and AGENTS.md routinely say
# "scripts/run_tests.sh", and the repo root has its own scripts/ directory, so every
# invocation from the root used to fail with "No such file" or collect zero files.
#
# This forwards verbatim from anywhere and keeps the real script's semantics (per-file
# subprocess isolation, CI-parity env, --file-retries), which is the part AGENTS.md
# mandates: never run bare pytest.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(dirname "$here")"
runtime="$repo/qubit_runtime"

if [[ ! -x "$runtime/scripts/run_tests.sh" ]]; then
    echo "run_tests.sh: cannot find $runtime/scripts/run_tests.sh" >&2
    exit 127
fi

# Default the interpreter to the repo venv unless the caller pinned one; the real script
# scrubs PYTHONPATH but honours HERMES_PYTHON.
export HERMES_PYTHON="${HERMES_PYTHON:-$repo/.venv/bin/python}"

args=()
for a in "$@"; do
    # Strip a repo-root-relative runtime prefix; the runner is CWD'd into qubit_runtime.
    [[ "$a" == qubit_runtime/* ]] && a="${a#qubit_runtime/}"
    args+=("$a")
done

cd "$runtime"
exec ./scripts/run_tests.sh "${args[@]}"
