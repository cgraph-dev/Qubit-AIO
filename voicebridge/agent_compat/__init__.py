"""Foreign-agent adapter framework — run other agents' plugins/skills/MCPs inside Qubit.

WHAT THIS IS (and is not)
-------------------------
This is **Qubit source code, for Qubit**. It defines a small, generic engine that teaches
Qubit to host content written against ANOTHER agent's naming conventions.

It is NOT a Hermes shim. Hermes is the *first* adapter (``adapters/hermes.py``), and only one
of however many arrive later. Nothing here imports, requires, or references another agent's
source tree at runtime: an adapter is pure DATA plus a few optional hooks. Deleting the other
agent changes nothing in Qubit's behaviour.

WHY A FRAMEWORK INSTEAD OF A SHIM
---------------------------------
The one-off version of this file hardcoded Hermes names at module scope. That couples Qubit's
compat story to one vendor and makes the second adapter a rewrite. The framework inverts it:

    ForeignAdapter (data)          engines (behaviour)            Qubit
    ────────────────────           ──────────────────             ─────
    module_aliases                 ⎫
    prefix_aliases                 ⎬──> ImportAliaser   ──>      qubit_cli, qubit_state, ...
    symbol_aliases                 ⎪    EnvAliaser
    env_aliases                    ⎪    SymbolAliaser
    entry_point_groups             ⎭
    detection markers

Adding a new agent = adding one ``ForeignAdapter`` instance (often pure data, no new code).

LOAD-BEARING RULES (each one is a mistake that already cost this repo real breakage)
-----------------------------------------------------------------------------------
1. **Adapters are allowlists, never rewrites.** A blanket string substitution is what corrupted
   172 third-party URLs during the vendor sync. Every mapping is enumerated and reviewable.
2. **Identity is preserved.** An alias must hand back the SAME object the host uses, so a
   foreign plugin's registration reaches the live registry. A duplicate module (same
   ``__name__``, different object) is the bug this guards — see ``ImportAliaser``.
3. **Failures are loud.** An unaliased name raises a named error; we never return a placeholder
   module. A silent stub makes foreign content appear to work while doing nothing — the
   ``except Exception: return None`` class that hid several sync defects.
4. **Kept names stay kept.** Names the host deliberately retains (its entry-point group, legacy
   data paths, wire-format strings) are NOT aliased. ``ForeignAdapter.reserved`` makes that
   explicit and testable.
5. **External-only.** No Qubit module may import this package. Qubit's own code uses its real
   names; this exists solely to host foreign content. ``tests/test_agent_compat.py`` enforces it.

USAGE
-----
    from voicebridge.agent_compat import registry, install_for

    registry.register(hermes_adapter)          # adapters are data
    install_for(plugin_dir)                    # auto-detect + install for any adapter

    # or explicitly:
    from voicebridge.agent_compat.adapters import hermes
    install_for(plugin_dir, adapter=hermes.ADAPTER)
"""
from __future__ import annotations

from .framework import (
    AgentCompatUnresolved,
    ForeignAdapter,
    ImportAliaser,
    EnvAliaser,
    SymbolAliaser,
    AdapterRegistry,
    registry,
    install,
    uninstall,
    installed,
    install_for,
    active_adapters,
    reset,
)

__all__ = [
    "AgentCompatUnresolved",
    "ForeignAdapter",
    "ImportAliaser",
    "EnvAliaser",
    "SymbolAliaser",
    "AdapterRegistry",
    "registry",
    "install",
    "uninstall",
    "installed",
    "install_for",
    "active_adapters",
    "reset",
]


def _register_defaults() -> None:
    """Register the built-in adapters so detection works without a caller doing setup.

    Each built-in adapter is self-contained data (no imports of the foreign agent), so this
    cannot fail merely because the other agent is absent.
    """
    try:
        from .adapters import default_adapters
    except ImportError:  # pragma: no cover - package always ships its adapters
        return
    for adapter in default_adapters():
        registry.register(adapter)


_register_defaults()
