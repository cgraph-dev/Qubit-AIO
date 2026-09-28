"""Boot contract for the chaos (turn-liveness) lane.

The liveness files drive REAL gateway / agent-driver subprocesses, each of which
cold-imports the whole vendored runtime (hundreds of MB resident) and then asserts
a dispatcher heartbeat of < 3.0s *during* a deliberately wedged turn. That bound
is a real product invariant, not a slack budget: it can only be measured on a host
with scheduling headroom. On an oversubscribed runner (many concurrent test
files, other tenants, high load) the heartbeat blips past 3s and every scenario
fails for an environmental reason — the same "gateway never became ready" /
"dispatcher blocked Ns" cluster seen in wave-A.

The lane therefore has an explicit boot requirement, marked ``chaos_gateway_boot``
and gated here: it runs only when the 1-minute load average leaves the host with
headroom, and otherwise skips with the measured numbers so the disposition is
unambiguous. Override with ``CHAOS_FORCE_BOOT=1`` (run anyway) or tune
``CHAOS_BOOT_MAX_LOAD_FACTOR`` (default 1.0, i.e. load <= CPU count).

Cold starts inside the lane are also bounded (``_helpers.SCENARIO_WORKERS``) so
the lane does not starve itself. Neither the pool width nor this gate touches a
liveness assertion — deadlines, heartbeat, provider-call bound and history
integrity stay exactly as written.
"""

from __future__ import annotations

import os

import pytest

# Lanes whose tests own a bootable child process and must run with a bounded
# boot pool. Matched by module filename so the marker cannot drift from the files.
BOOT_LANES = frozenset({
    "test_agent_turn_liveness.py",
    "test_tui_gateway_turn_liveness.py",
})

_FORCE_OVERRIDE = "CHAOS_FORCE_BOOT"
_LOAD_FACTOR_ENV = "CHAOS_BOOT_MAX_LOAD_FACTOR"
_DEFAULT_LOAD_FACTOR = 1.0


def _boot_skip_reason() -> str | None:
    """Reason the chaos boot requirement is unmet, or None when the host has headroom."""
    if os.environ.get(_FORCE_OVERRIDE) == "1":
        return None
    cpus = os.cpu_count() or 1
    try:
        load1 = os.getloadavg()[0]
    except (AttributeError, OSError):  # platform without load average — cannot prove absence
        return None
    try:
        factor = float(os.environ.get(_LOAD_FACTOR_ENV, _DEFAULT_LOAD_FACTOR))
    except ValueError:
        factor = _DEFAULT_LOAD_FACTOR
    limit = cpus * factor
    if load1 <= limit:
        return None
    return (
        "OPERATOR: chaos gateway boot requires host headroom — 1-min load "
        f"{load1:.1f} exceeds {limit:.1f} on {cpus} CPUs. The lane asserts a "
        "dispatcher heartbeat < 3.0s during a wedged turn and a boot deadline; "
        "an oversubscribed host cannot honour them, so this is an environment "
        f"skip, not a liveness result. Run isolated, or set {_FORCE_OVERRIDE}=1 "
        f"to force (tune {_LOAD_FACTOR_ENV})."
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "chaos_gateway_boot: boots a real gateway/agent subprocess; requires host "
        "headroom and a bounded boot pool (CHAOS_SCENARIO_WORKERS)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    marker = pytest.mark.chaos_gateway_boot
    reason = _boot_skip_reason()
    skip = pytest.mark.skip(reason=reason) if reason else None
    for item in items:
        if item.path.name not in BOOT_LANES:
            continue
        item.add_marker(marker)
        if skip is not None:
            item.add_marker(skip)
