"""Supervisor marker env names and presence probes (no config imports).

Leaf module: ``qubit_cli.main``'s pre-import profile override and the early-recovery
bootstrap run before site-packages/damaged dependencies are available, so they must be
able to answer "is this a supervised child?" without importing ``gateway.restart``
(which reads ``DEFAULT_CONFIG`` and therefore ruamel).
"""

from __future__ import annotations

from collections.abc import Mapping

SUPERVISED_CHILD_ENV = "QUBIT_SUPERVISED_CHILD"
SUPERVISED_CHILD_ENV_ALIASES = ("HERMES_SUPERVISED_CHILD",)
S6_SUPERVISED_CHILD_ENV = "QUBIT_S6_SUPERVISED_CHILD"
S6_SUPERVISED_CHILD_ENV_ALIASES = ("HERMES_S6_SUPERVISED_CHILD",)
EXTERNAL_GATEWAY_SUPERVISOR_ENV = "QUBIT_GATEWAY_EXTERNAL_SUPERVISOR"
EXTERNAL_GATEWAY_SUPERVISOR_ENV_ALIASES = ("HERMES_GATEWAY_EXTERNAL_SUPERVISOR",)


def supervised_child_marker_present(env: Mapping[str, str]) -> bool:
    """True when *env* carries any supervised-child marker, canonical or legacy."""
    return any(env.get(name) for name in (SUPERVISED_CHILD_ENV, *SUPERVISED_CHILD_ENV_ALIASES))


def s6_supervised_child_marker_present(env: Mapping[str, str]) -> bool:
    """True when *env* carries any s6 supervised-child marker, canonical or legacy."""
    return any(env.get(name) for name in (S6_SUPERVISED_CHILD_ENV, *S6_SUPERVISED_CHILD_ENV_ALIASES))


def external_supervisor_marker_present(env: Mapping[str, str]) -> bool:
    """True when *env* opts into ``--external-supervisor``, canonical or legacy."""
    return any(env.get(name) for name in (EXTERNAL_GATEWAY_SUPERVISOR_ENV, *EXTERNAL_GATEWAY_SUPERVISOR_ENV_ALIASES))
