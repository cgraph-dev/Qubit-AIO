#!/bin/sh
# shellcheck shell=sh
# Entry-point dispatcher for runtimes that may or may not give the image
# ownership of PID 1.
#
# Normal Docker / Podman path: this script is PID 1, so we delegate to
# s6-overlay's /init exactly as before and keep the full supervision tree.
#
# Wrapped-runtime path (Fly Machines, `docker run --init`, some Nomad/K8s
# setups): the platform's own init is already PID 1 and execs the image
# entrypoint as a child. s6-overlay aborts there with "can only run as pid 1",
# so we run the stage2 bootstrap directly and then exec the main wrapper
# without /init.

set -e

# Console ownership: s6-overlay defaults to console-only logging, which leaks
# every supervised service's stdout/stderr onto an interactive terminal. When
# the container has a real TTY, default to pipe logging (services keep their
# own logs; only uncaught output reaches the catch-all) and let s6 rc.init
# hand the CMD its controlling terminal. Explicit S6_LOGGING /
# S6_CMD_USE_TERMINAL values always win, including 0.
if [ -t 0 ] && [ -t 1 ]; then
    : "${S6_LOGGING:=2}"; export S6_LOGGING
    : "${S6_CMD_USE_TERMINAL:=1}"; export S6_CMD_USE_TERMINAL
fi

if [ "$$" -eq 1 ]; then
    exec /init /opt/qubit/docker/main-wrapper.sh "$@"
fi

echo "[qubit] WARNING: container entrypoint is not PID 1; skipping s6-overlay /init and falling back to direct bootstrap. Supervised services are unavailable in this runtime, but the requested command will still run." >&2
# /init normally seeds PATH with s6's helpers; the non-PID-1 fallback skips it.
export PATH="/command:/package/admin/s6/command:${PATH}"
/opt/qubit/docker/stage2-hook.sh
exec /opt/qubit/docker/main-wrapper.sh "$@"
