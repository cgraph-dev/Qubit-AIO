"""Qubit-owned messaging-gateway daemon entry point (Owner Decision #16).

The vendored messaging gateway lives at ``qubit_runtime/gateway/run.py``. Its
``main()`` resolves identity files (PID, runtime lock, ``gateway_state.json``,
``gateway.sock``) from ``QUBIT_HOME`` at import time. Qubit must never let that
resolution fall back to the operator's read-only reference home, so this module
is the *only* supported launch surface: it pins a Qubit-owned ``QUBIT_HOME``
(:func:`voicebridge.qubit_runtime.pin_qubit_home`) and puts the vendored fork on
``sys.path`` (:func:`voicebridge.qubit_runtime.ensure_path`) **before** the
gateway package is imported, then hands control to the real daemon loop.

Two modes:

* default — run the vendored :func:`gateway.run.main` (the real multi-platform
  messaging loop, with its own SIGINT/SIGTERM/SIGUSR1 handling and graceful
  shutdown/teardown). Nothing here re-implements the loop.
* ``--check`` — a preflight that validates home pinning and importability only.
  It never starts the loop, opens a socket, or claims a PID; it is safe for
  ``qubit doctor``/CI.

Process supervision belongs to systemd via ``qubit-gateway.service``
(``systemd/qubit-gateway.service``), whose ExecStart is
``<venv>/bin/python -m voicebridge.gateway_daemon``. ``systemctl restart`` is
dispatched by :mod:`voicebridge.gateway_supervisor`; lifecycle mutations run
through the one broker/policy/audit/confirmation/e-stop path. This module owns
none of that; it is the launch boundary only.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

__all__ = ["service_unit_name", "preflight", "run_gateway"]


def service_unit_name() -> str:
    """The systemd user unit that supervises this daemon (single source)."""
    return "qubit-gateway.service"


def preflight() -> Dict[str, Any]:
    """Validate home pinning + importability. Never starts the gateway loop.

    Fail-closed and side-effect-free beyond creating the Qubit-owned home
    directory (which ``pin_qubit_home`` does). No PID file, lock, socket, or
    runtime-status write happens here; ``gateway.run`` is never invoked.
    """
    from . import qubit_runtime as QR

    out: Dict[str, Any] = {
        "state": "OK", "mode": "check", "service_unit": service_unit_name(),
        "runtime_home": None, "runtime_root": None, "importable": False,
        "daemon_module": "gateway.run", "daemon_entry": "main",
    }
    try:
        home = QR.pin_qubit_home()
        out["runtime_home"] = str(home)
        external = QR._external_default_home()
        if QR._resolved(home) == QR._resolved(external):
            out["state"] = "FAILED"
            out["detail"] = ("QUBIT_HOME resolved to the external reference "
                             "home; refusing to launch the daemon")
            return out
    except Exception as exc:  # noqa: BLE001 - preflight reports, never raises
        out["state"] = "FAILED"
        out["detail"] = f"{type(exc).__name__}: {exc}"[:300]
        return out
    try:
        root = QR.ensure_path()
        out["runtime_root"] = str(root)
        # Presence check only: do not import the heavy gateway package here, so
        # --check never pays the fork's import-time side effects.
        found = importlib.util.find_spec("gateway.run")
        out["importable"] = found is not None
        if found is None:
            out["state"] = "FAILED"
            out["detail"] = "gateway.run is not importable from the vendored runtime"
    except Exception as exc:  # noqa: BLE001
        out["state"] = "FAILED"
        out["detail"] = f"{type(exc).__name__}: {exc}"[:300]
    return out


def run_gateway(argv: Optional[list] = None) -> int:
    """Pin the Qubit home, put the fork on ``sys.path``, run the real daemon.

    The vendored ``gateway.run.main`` parses its own argv (``--config``/
    ``--verbose``) from ``sys.argv`` and installs its own signal handlers, so
    ``argv`` is set before the import and left in place for the duration of the
    loop. ``main`` exits through ``os._exit`` on every path (its documented
    wedged-worker backstop), so this call normally never returns; if it does,
    the returned/raised code is propagated.
    """
    from . import qubit_runtime as QR

    QR.pin_qubit_home()
    QR.ensure_path()
    sys.argv = ["qubit-gateway", *(argv or [])]
    from gateway.run import main as gateway_main

    try:
        gateway_main()
    except SystemExit as exc:  # pragma: no cover - gateway owns exit codes
        code = exc.code
        return code if isinstance(code, int) else (0 if code is None else 1)
    return 0


def main(argv: Optional[list] = None) -> int:
    """``python -m voicebridge.gateway_daemon`` entry point."""
    parser = argparse.ArgumentParser(
        prog="qubit-gateway",
        description="Qubit-owned messaging-gateway daemon (supervised by "
                    f"{service_unit_name()}).")
    parser.add_argument(
        "--check", action="store_true",
        help="validate QUBIT_HOME pinning + importability and exit; never "
             "starts the daemon")
    parser.add_argument(
        "--json", action="store_true",
        help="emit the --check report as JSON (implies --check)")
    args, rest = parser.parse_known_args(argv)
    if args.check or args.json:
        report = preflight()
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(f"{report['state']}: {report.get('detail', '')}".rstrip(": "))
            print(f"  runtime_home={report.get('runtime_home')}")
            print(f"  runtime_root={report.get('runtime_root')}")
            print(f"  importable={report.get('importable')}")
        return 0 if report["state"] == "OK" else 2
    # Everything after the Qubit-dedicated flags is the vendored gateway's own
    # argv (--config/-c, --verbose/-v); we never re-parse those here.
    os.environ.setdefault("QUBIT_GATEWAY_DAEMON", "1")
    return run_gateway(rest)


if __name__ == "__main__":
    raise SystemExit(main())
