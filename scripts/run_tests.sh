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

# ── Locate python ───────────────────────────────────────────────────────────
# The real runner honours HERMES_PYTHON *before* its activation ladder, and a
# shell inherited from an installed release (or an installer-written env file)
# exports HERMES_PYTHON pointing at that install's venv
# (~/venvs/qubit-<version>/bin/python). A checkout must run its own tests under
# its own venv: the installed package's site-packages carries a different
# dependency set, so every test died with "no such test path" or ~28 fixture
# import errors (`pytest_asyncio` against an incompatible `_isolate_install_state`
# fixture) — the documented entry point was unusable.
#
# Precedence, most specific first:
#   1. a caller who pinned HERMES_PYTHON *by hand* (pointing anywhere outside the
#      known installed-release venv roots) — explicit intent wins;
#   2. the checkout's own .venv (or venv/), when it has pytest. A repo checkout
#      always prefers this over any installed-package venv. Only a real checkout
#      qualifies: the parent must be a git worktree root, or (importantly) an
#      installed wheel's bundled qubit_runtime/ could shadow its own venv, since
#      an install has no .git;
#   3. the inherited HERMES_PYTHON (an installed-package venv) — the
#      installed/wheel layout has no repo .venv, so an install still resolves;
#   4. nothing: hand the real script no HERMES_PYTHON and let its own activation
#      ladder (run-in-hermes-env) build/find one.
#
# Installed-package venv roots: <parent-of-repo>/venvs/*, ~/venvs/*,
# ~/.hermes/venvs/*, ~/.local/share/qubit/venvs/* — a HERMES_PYTHON under any of
# those is treated as the default an installer exported, not a human's pin.
_is_checkout() {
    if [ -e "$repo/.git" ]; then return 0; fi
    command -v git >/dev/null 2>&1 || return 1
    [ "$(git -C "$repo" rev-parse --show-toplevel 2>/dev/null)" = "$(cd "$repo" && pwd -P)" ]
}
_installed_venv_root() {
    local p="$1" base
    for base in "$(dirname "$repo")/venvs" "$HOME/venvs" "$HOME/.hermes/venvs" \
                "$HOME/.local/share/qubit/venvs"; do
        case "$p" in "$base"/*) return 0 ;; esac
    done
    return 1
}
_repo_python() {
    local cand
    for cand in "$repo/.venv/bin/python" "$repo/.venv/Scripts/python.exe" \
                "$repo/venv/bin/python" "$repo/venv/Scripts/python.exe"; do
        if [ -x "$cand" ] && "$cand" -c 'import pytest' 2>/dev/null; then
            printf '%s\n' "$cand"
            return 0
        fi
    done
    return 1
}
if [ -n "${HERMES_PYTHON:-}" ] && ! _installed_venv_root "$HERMES_PYTHON"; then
    # Explicitly pinned by the caller — keep it.
    :
elif [ -d "$repo/.venv" ] || [ -d "$repo/venv" ]; then
    # A checkout with its own venv runs ONLY under it: if that venv is broken (no
    # pytest), the answer is to repair the clone (`./setup-qubit.sh`), never to
    # silently test this code against an installed package's venv.
    if ! _is_checkout; then :; elif py="$(_repo_python)"; then
        export HERMES_PYTHON="$py"
    else
        echo "run_tests.sh: $repo/.venv has no pytest; run ./setup-qubit.sh, or set HERMES_PYTHON explicitly" >&2
        exit 1
    fi
else
    : # installed/wheel layout with no venv: leave the inherited HERMES_PYTHON untouched
fi
unset py

args=()
for a in "$@"; do
    # The runner is CWD'd into qubit_runtime, so a repo-root-relative path has to be
    # rebased onto it. Strip the prefix ONLY when the caller's path names no real
    # file as typed: this checkout owns BOTH tests/ trees, and qubit_runtime/tests/…
    # previously stripped down to tests/… which then resolved against the
    # unvendored root tree and reported "no such test path" for a file that exists.
    if [[ "$a" == qubit_runtime/* && ! -e "$a" ]]; then
        a="${a#qubit_runtime/}"
    fi
    args+=("$a")
done

cd "$runtime"
exec ./scripts/run_tests.sh "${args[@]}"
