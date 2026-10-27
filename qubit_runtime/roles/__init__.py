"""Qubit-native agent roles (one agent, many postures)."""
from roles.base import AgentRole, CONFIRMATION_MODES, MODEL_TIERS
from roles.registry import (
    DuplicateRoleError,
    UnknownRoleError,
    discover_roles,
    get_role,
    list_roles,
)

__all__ = [
    "AgentRole",
    "CONFIRMATION_MODES",
    "DuplicateRoleError",
    "MODEL_TIERS",
    "UnknownRoleError",
    "discover_roles",
    "get_role",
    "list_roles",
    "run_role",
]


def __getattr__(name: str):
    """Keep the runner's recipe/runtime imports out of the package import path."""
    if name == "run_role":
        from roles.runner import run_role

        return run_role
    raise AttributeError(name)
