"""Connector implementations behind Qubit's existing credential paths.

Phase 5 of the runtime-boundary program adds read-only connectors here. They
are deliberately application-level modules, not transports: each connector
reads the credential the owning skill already stores, returns typed refusals
when that credential is missing or under-scoped, and never invents data.

The surface is exported lazily so importing the package does not pull the
Google client stack into every process that only wants the registry names.
"""

from __future__ import annotations

import importlib

_LAZY_EXPORTS: dict[str, str] = {
    "Contact": "connectors.google_contacts",
    "DEFAULT_LIMIT": "connectors.google_contacts",
    "GoogleContactsConnector": "connectors.google_contacts",
    "GoogleContactsError": "connectors.google_contacts",
    "MAX_LIMIT": "connectors.google_contacts",
}

__all__ = sorted(_LAZY_EXPORTS)


def __getattr__(name: str):
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
