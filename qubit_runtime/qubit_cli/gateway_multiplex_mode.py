"""Does the default profile's gateway serve every profile? The read side of
``gateway.multiplex_profiles`` that CLI surfaces consult.

Reference: ``hermes_cli/gateway_multiplex_mode.py`` + ``hermes_cli/gateway_multiplex_served.py``.
Vendored 0.21.1 keeps only the gateway-boot resolver pieces it needs; this module vendors the READ
side that ``qubit sessions repair-profiles`` depends on (the routing index lives in the gateway's
home when the default gateway multiplexes, and in each store's own home when it does not).

Scope is deliberately narrow — only the functions the repair command calls:

* :func:`live_gateway_pid_for_home` — the vendored-0.21.1 stand-in for
  ``gateway.status.live_gateway_pid_for_home`` (absent from the vendored tree), composed from the
  primitives that ARE present (``get_running_pid_cached`` / ``get_runtime_status_running_pid``);
* :func:`recorded_served_profiles` — the live gateway's ``served_profiles`` record;
* :func:`explicit_multiplex_flag` / :func:`default_gateway_multiplexes` — the verdict, recorded
  record first, else the explicit flag, else False (an unset flag is decided by the gateway at
  boot, never guessed here).

The boot-time resolver (``resolve_multiplex_mode`` / ``implicit_multiplex_blocker``) is intentionally
NOT vendored: it pulls the service-manager migration preflight and is a gateway-runtime concern, not
a CLI read. Every caller of this module treats an exception as "not multiplexing" (matching the
reference ``sessions_repair_profiles._gateway_multiplexes`` guard).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def live_gateway_pid_for_home(home: Path) -> Optional[int]:
    """Verified PID of the gateway owned by ``home`` (pid file + runtime lock first, then the runtime
    status record), or None.

    Reference: ``gateway.status.live_gateway_pid_for_home``. Vendored 0.21.1 exposes the two
    primitives it is built from but not the composed helper, so the composition is kept here. Every
    reader of another home's gateway identity goes through this so they all prove the same thing: the
    PID passes the start-time reuse guard, its live command line is a gateway's belonging to ``home``,
    and the record is not ``stopped``. Bare PID existence is not identity. Never unlinks ``home``'s
    identity files."""
    from gateway.status import (
        get_running_pid_cached, get_runtime_status_running_pid, read_runtime_status,
    )
    home = Path(home)
    pid = get_running_pid_cached(home / "gateway.pid", cleanup_stale=False)
    if pid is not None:
        return pid
    return get_runtime_status_running_pid(
        read_runtime_status(home / "gateway_state.json"), expected_home=home)


def recorded_served_profiles(default_root: Optional[Path] = None) -> Optional[list[str]]:
    """``served_profiles`` the live default gateway recorded, or None when the key is absent (a record
    from before the multiplexer recorded it, or a stopped/absent gateway). Callers fall back to config
    derivation only on None: an empty list is an authoritative "serves nobody else"."""
    from qubit_constants import get_default_qubit_root
    from gateway.status import read_runtime_status
    root = default_root or get_default_qubit_root()
    if live_gateway_pid_for_home(root) is None:
        return None
    runtime = read_runtime_status(root / "gateway_state.json")
    served = (runtime or {}).get("served_profiles")
    return [str(p) for p in served] if isinstance(served, list) else None


def explicit_multiplex_flag(default_home: Path) -> Optional[bool]:
    """The operator's explicit choice for the DEFAULT profile's gateway: a recognized
    ``GATEWAY_MULTIPLEX_PROFILES``, else ``gateway.multiplex_profiles`` (or the top-level alias) as
    written in its config.yaml; ``None`` when neither is set. Raw read on purpose: the callers are
    other processes (``qubit -p X ...`` has X's config loaded) asking about the default's file."""
    from gateway.config import _bool_token, _env_multiplex_profiles_override
    env = _env_multiplex_profiles_override()
    if env is not None:
        return env
    cfg_path = Path(default_home) / "config.yaml"
    if not cfg_path.exists():
        return None
    from qubit_cli.config import read_user_config_raw
    cfg = read_user_config_raw(cfg_path) or {}
    gateway_section = cfg.get("gateway") if isinstance(cfg.get("gateway"), dict) else {}
    value = cfg.get("multiplex_profiles")
    if value is None:
        value = gateway_section.get("multiplex_profiles")
    if value is None:
        return None
    if isinstance(value, str):
        parsed = _bool_token(value)
        return True if parsed is None else parsed
    return bool(value)


def default_gateway_multiplexes(default_home: Optional[Path] = None) -> bool:
    """Does the default profile's gateway serve every profile? For CLI/dashboard processes: the LIVE
    gateway's ``served_profiles`` record when one runs (it settled the unset default itself), else
    the explicit flag, else False — an unset flag is decided by the gateway at boot, never guessed
    here."""
    from qubit_constants import get_default_qubit_root
    root = Path(default_home) if default_home is not None else get_default_qubit_root()
    recorded = recorded_served_profiles(root)
    if recorded is not None:
        return bool(recorded)
    flag = explicit_multiplex_flag(root)
    return False if flag is None else True
