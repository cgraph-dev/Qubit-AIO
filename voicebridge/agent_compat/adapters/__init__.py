"""Built-in foreign-agent adapters.

Each module here is pure data describing one foreign agent's naming conventions. The engine
lives in ``voicebridge.agent_compat.framework``; adding an agent means adding a module, not
modifying the framework.

Import them explicitly so a missing optional dependency never breaks the package::

    from voicebridge.agent_compat.adapters import hermes
    registry.register(hermes.ADAPTER)

:func:`default_adapters` returns the ones that are safe to assume present (they carry no
imports of their own), which is what auto-detection uses.
"""
from __future__ import annotations

from typing import List

from ..framework import ForeignAdapter


def default_adapters() -> List[ForeignAdapter]:
    """Adapters to register by default for detection.

    Every module in this package is self-contained data, so this import is safe even when the
    foreign agent's own software is not installed.
    """
    from . import hermes

    return [hermes.ADAPTER]


__all__ = ["default_adapters"]
