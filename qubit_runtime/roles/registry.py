"""Auto-discovered registry of Qubit-native agent roles.

Adding a role is a new sibling module with a module-level ``ROLE`` (an
``AgentRole``); there is deliberately no central list to edit. Discovery walks
the package with ``pkgutil``/``importlib``, so an installed checkout and a
test-time module both behave the same way.
"""
from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType
from typing import Iterable, Sequence

from roles.base import AgentRole

_EXCLUDED_MODULES = frozenset({"base", "registry", "runner", "__init__"})


class UnknownRoleError(ValueError):
    """No role is registered under this id."""

    def __init__(self, role_id: str, available: Sequence[str]) -> None:
        self.role_id = role_id
        self.available = list(available)
        super().__init__(
            f"Unknown agent role {role_id!r}; available: "
            f"{', '.join(self.available) or '(none)'}")


class DuplicateRoleError(ValueError):
    """Two role modules declared the same id."""

    def __init__(self, role_id: str) -> None:
        self.role_id = role_id
        super().__init__(f"agent role {role_id!r} is declared more than once")


def discover_module_names() -> list[str]:
    """Sibling module names that may declare a role (stable order)."""
    package = importlib.import_module(__package__)
    names = []
    for info in pkgutil.iter_modules(getattr(package, "__path__", ())):
        name = info.name
        if name in _EXCLUDED_MODULES or name.startswith("_"):
            continue
        names.append(name)
    return sorted(names)


def _role_modules() -> Iterable[ModuleType]:
    for name in discover_module_names():
        yield importlib.import_module(f"{__package__}.{name}")


def _role_from(module: object) -> AgentRole | None:
    role = getattr(module, "ROLE", None)
    if role is None:
        return None
    if not isinstance(role, AgentRole):
        raise TypeError(
            f"{getattr(module, '__name__', module)!r}.ROLE must be an AgentRole, "
            f"got {type(role).__name__}")
    return role


def discover_roles(modules: Iterable[object] | None = None) -> list[AgentRole]:
    """Collect roles from *modules* (or every sibling module) sorted by id.

    Duplicate ids raise ``DuplicateRoleError``: the registry never silently
    picks one of two definitions.
    """
    source = _role_modules() if modules is None else modules
    found: dict[str, AgentRole] = {}
    for module in source:
        role = _role_from(module)
        if role is None:
            continue
        if role.id in found:
            raise DuplicateRoleError(role.id)
        found[role.id] = role
    return [found[role_id] for role_id in sorted(found)]


def list_roles() -> list[AgentRole]:
    """Every registered role, sorted by id."""
    return discover_roles()


def get_role(role_id: str) -> AgentRole:
    """Look up one role by id, raising ``UnknownRoleError`` when absent."""
    key = str(role_id or "").strip()
    roles = {role.id: role for role in list_roles()}
    try:
        return roles[key]
    except KeyError:
        raise UnknownRoleError(key, sorted(roles)) from None


__all__ = [
    "DuplicateRoleError",
    "UnknownRoleError",
    "discover_module_names",
    "discover_roles",
    "get_role",
    "list_roles",
]
