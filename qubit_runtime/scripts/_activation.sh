# Sourced, never executed: how a POSIX shell gets the Qubit dev environment.
# `activate` (interactive, reversible) and `scripts/run-in-hermes-env` (a child
# process, one-way) both build on it; neither owns any of it.
#
#   hermes_sync REPO [FLAG]         bring the install up to date (setup-qubit.sh)
#   hermes_compose_env REPO DIALECT print the composed environment, `sh` or `fish`
#   hermes_apply_env SCRIPT         evaluate a composed `sh` script into this shell
#   hermes_activation_current REPO  is the inherited environment still REPO's?
#   hermes_ensure_env REPO          sync, compose and apply unless it is current
#   hermes_ensure_activated REPO    legacy: source REPO/activate unless it is current
#
# pm records, beside the installed-state file named by __HERMES_ACTIVATED, one
# stamp per dependency input carrying the exact mtime that input had when the
# install was last verified against it (pm.environments.record_activation_inputs).
# Any input whose mtime DIFFERS from its stamp (newer or older, since a branch
# switch can move it either way) means the inherited environment may not match
# its inputs. `-nt`/`-ot` are bash builtins, so the check costs no process
# spawn, and every successful sync re-records, so one re-sync settles it.
# Missing stamps or inputs, a dangling sentinel or a literal "1" all read as
# stale, and so does an install that did not build the test environment:
# every environment this library composes includes it.

# hermes_sync REPO [--test-environment[=EXTRAS]]: run setup in a child, so a
# failure cannot exit or half-change the caller's shell and setup never
# republishes launchers or shell config. Progress goes to stderr.
hermes_sync() {
    local repo="$1" test_environment="${2:---test-environment}"
    (
        unset PYTHONHOME PYTHONPATH VIRTUAL_ENV
        bash "$repo/setup-qubit.sh" --runtime-only "$test_environment"
    ) >&2
}

# hermes_bootstrap_python REPO: print the interpreter that can run pm before
# any dependency is importable. It only emits the environment; it installs nothing.
hermes_bootstrap_python() {
    local repo="$1" store candidate
    for candidate in "$repo/.venv/bin/python" "$repo/.venv/Scripts/python.exe" \
                     "$repo/venv/bin/python" "$repo/venv/Scripts/python.exe"; do
        [ -x "$candidate" ] && { printf '%s\n' "$candidate"; return 0; }
    done
    for store in "${HERMES_RUNTIME_DIR:-}" "$repo/../tools" "${QUBIT_HOME:-$HOME/.hermes}/tools"; do
        [ -n "$store" ] || continue
        for candidate in "$store"/python-*/bin/python3 "$store"/python-*/python.exe \
                         "$store"/python-*/bin/python "$store"/python-*/bin/python.exe; do
            [ -x "$candidate" ] && { printf '%s\n' "$candidate"; return 0; }
        done
    done
    return 1
}

# hermes_compose_env REPO DIALECT: print the environment of the installed state
# as a script for a shell of DIALECT (`sh` or `fish`).
hermes_compose_env() {
    local repo="$1" dialect="$2" python script
    python="$(hermes_bootstrap_python "$repo")" || {
        echo "no bootstrap Python found; run setup-qubit.sh" >&2
        return 1
    }
    script="$(PYTHONHOME= PYTHONPATH="$repo" "$python" -m pm.environments --format "$dialect")" &&
        [ -n "$script" ] || {
        echo "could not read pm env (run ./setup-qubit.sh first)" >&2
        return 1
    }
    printf '%s\n' "$script"
}

# hermes_apply_env SCRIPT: evaluate a script from `hermes_compose_env REPO sh`.
hermes_apply_env() {
    # Resolved before the eval replaces PATH.
    local cygpath name
    cygpath="$(command -v cygpath 2>/dev/null || :)"
    eval "$1"
    # The MSYS/Cygwin runtime hands a native Windows Python these variables in
    # Windows form (`C:\a;C:\b`) and converts them back only for its own
    # children. Exported verbatim, bash splits PATH on ':' and finds no commands.
    if [ -n "$cygpath" ]; then
        PATH="$("$cygpath" -u -p "$PATH")"
        for name in HOME TMPDIR TMP TEMP; do
            if [ -n "${!name-}" ]; then
                printf -v "$name" '%s' "$("$cygpath" -u "${!name}")"
            fi
        done
    fi
}

# hermes_activation_current REPO: status 0 when the inherited environment
# matches REPO's current inputs AND the install built the test environment
# (run-in-hermes-env always composes both, so a runtime-only install is stale).
hermes_activation_current() {
    hermes_activation_current_app "$1" || return 1
    local repo="$1" sentinel stamps
    [ -n "${__HERMES_ACTIVATED:-}" ] && [ -e "${__HERMES_ACTIVATED}" ] || return 1
    sentinel="${__HERMES_ACTIVATED//\\//}"
    stamps="${sentinel%/*}/inputs"
    [ -f "$stamps/.test-environment" ]
}

# hermes_activation_current_app REPO: the app-only staleness check — the
# inherited environment matches REPO's inputs, ignoring whether the test
# environment was built. scripts/_hermes-python drives an app shebang, so it
# must not re-activate just because the test env is absent.
hermes_activation_current_app() {
    local repo="$1" sentinel stamps stamp input stamped=0
    [ -n "${__HERMES_ACTIVATED:-}" ] && [ -e "${__HERMES_ACTIVATED}" ] || return 1
    # activate.ps1 records a Windows path; Git Bash accepts it with slashes.
    sentinel="${__HERMES_ACTIVATED//\\//}"
    stamps="${sentinel%/*}/inputs"
    [ -f "$stamps/.project-root" ] || return 1
    local owner="$repo" recorded
    recorded="$(< "$stamps/.project-root")"
    if command -v cygpath >/dev/null 2>&1; then
        owner="$(cygpath -am "$repo")" || return 1
        recorded="${recorded//\\//}"
        [ "${owner,,}" = "${recorded,,}" ] || return 1
    else
        [ "$(cd "$owner" && pwd -P)" = "$recorded" ] || return 1
    fi
    for stamp in "$stamps"/* "$stamps"/*/*; do
        [ -f "$stamp" ] || continue
        stamped=1
        input="$repo/${stamp#"$stamps"/}"
        if [ ! -e "$input" ] || [ "$input" -nt "$stamp" ] || [ "$input" -ot "$stamp" ]; then
            return 1
        fi
    done
    [ "$stamped" = 1 ]
}

# hermes_ensure_env REPO: leave this shell with REPO's environment, doing
# nothing when the inherited one is current.
hermes_ensure_env() {
    local repo="$1" script
    hermes_activation_current "$repo" && return 0
    hermes_sync "$repo" || {
        echo "setup failed" >&2
        return 1
    }
    script="$(hermes_compose_env "$repo" sh)" || return 1
    hermes_apply_env "$script"
}

# hermes_ensure_activated REPO: legacy prologue path used by scripts/_hermes-python.
# source REPO/activate unless the inherited environment is current. `--` passes
# no options: `source` without arguments would hand activate the CALLER's
# positional parameters.
hermes_ensure_activated() {
    hermes_activation_current_app "$1" && return 0
    # shellcheck source=/dev/null
    . "$1/activate" --
}

# Compatibility aliases: callers already in the tree (scripts/run-in-hermes-env,
# scripts/run_tests.sh) carry the qubit_-prefixed spellings. Keep both so no
# caller breaks regardless of which name it uses.
qubit_activation_current() { hermes_activation_current "$@"; }
qubit_sync() { hermes_sync "$@"; }
qubit_bootstrap_python() { hermes_bootstrap_python "$@"; }
qubit_compose_env() { hermes_compose_env "$@"; }
qubit_apply_env() { hermes_apply_env "$@"; }
qubit_ensure_env() { hermes_ensure_env "$@"; }
