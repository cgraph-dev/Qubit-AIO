"""Which profiles does the live default multiplexer serve? One answer for the CLI/dashboard.

Ported from the reference ``hermes_cli/gateway_multiplex_served.py``. The gateway records
``served_profiles`` into the default home's ``gateway_state.json`` at startup; that record is the
truth about the running process, while the default ``config.yaml`` plus the multiplex env override
as seen by the *CLI* process is only a guess.

Scope is deliberately narrow — only the functions this fork's migration surface reads:

* :func:`live_default_gateway_pid` — the default profile's live gateway PID;
* :func:`recorded_served_profiles` — the live gateway's ``served_profiles`` record.

The URL/notification helpers of the reference module (``served_profile_ingress_urls``,
``notify_multiplexer_profiles_changed``, ...) are NOT vendored: they depend on
``gateway.control_socket`` / ``shared_listener_mirror_platforms``, which the vendored 0.21.1 tree
does not carry. Add them with their runtime dependency, not as stubs.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def live_default_gateway_pid() -> Optional[int]:
    """PID of the default profile's gateway when a verified live process owns it, else None.

    ``live_gateway_pid_for_home`` proves the PID against the live process (start-time reuse guard,
    live command line, home) rather than trusting a bare PID or a stale record.
    """
    from qubit_constants import get_default_qubit_root
    from qubit_cli.gateway_multiplex_mode import live_gateway_pid_for_home
    return live_gateway_pid_for_home(get_default_qubit_root())


def recorded_served_profiles(default_root: Optional[Path] = None) -> Optional[list[str]]:
    """``served_profiles`` the live default gateway recorded, or None when the key is absent (a record
    from before the multiplexer recorded it, or a stopped/absent gateway). Callers fall back to config
    derivation only on None: an empty list is an authoritative "serves nobody else"."""
    from qubit_constants import get_default_qubit_root
    from gateway.status import read_runtime_status
    if live_default_gateway_pid() is None:
        return None
    runtime = read_runtime_status((default_root or get_default_qubit_root()) / "gateway_state.json")
    served = (runtime or {}).get("served_profiles")
    return [str(p) for p in served] if isinstance(served, list) else None
