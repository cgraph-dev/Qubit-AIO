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
    SkillLoader,
    SkillEntry,
    McpConfigLoader,
    McpServerEntry,
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
    "SkillLoader",
    "SkillEntry",
    "McpConfigLoader",
    "McpServerEntry",
    "AdapterRegistry",
    "registry",
    "install",
    "uninstall",
    "installed",
    "install_for",
    "active_adapters",
    "reset",
    "VendoredPluginProvider",
    "register_with_vendored_runtime",
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


class VendoredPluginProvider:
    """Presents this framework to the vendored runtime's neutral extension point.

    THE DEPENDENCY DIRECTION. The vendored tree (``qubit_runtime/``) must not
    import this package — that would make a clean rename rot silently. So the
    tree exposes a plain registry (``qubit_cli.plugins_discovery
    .register_foreign_provider``) that knows nothing about adapters, and THIS
    object is pushed into it from the Qubit side. The tree runs unchanged with
    no voicebridge present; with it, foreign content becomes discoverable and
    importable.

    Duck-typed by design: the tree calls ``activate()`` and
    ``entry_point_groups()`` and never imports this class, so neither side
    depends on the other's types.
    """

    #: The three callables the vendored tree looks for are activate() and
    #: entry_point_groups(); ``name`` is informational for logs.

    @property
    def name(self) -> str:
        return "agent_compat"

    def activate(self) -> list:
        """Install the import aliaser (idempotent) and report active adapter names."""
        install()
        return [a.name for a in active_adapters()]

    def entry_point_groups(self) -> tuple:
        """Entry-point groups every registered adapter wants scanned."""
        return registry.entry_point_groups()


def register_with_vendored_runtime() -> bool:
    """Push this framework into the vendored runtime's foreign-content registry.

    Idempotent and safe to call repeatedly. Returns False (never raises) when
    the vendored tree is not importable — this package is usable standalone
    (its own tests do exactly that) and must not hard-require the tree.
    """
    try:
        from qubit_cli.plugins_discovery import register_foreign_provider
    except Exception:  # noqa: BLE001 - standalone use has no vendored tree
        return False
    try:
        register_foreign_provider(_PROVIDER)
    except Exception:  # noqa: BLE001 - never break a caller's startup
        return False
    return True


_PROVIDER = VendoredPluginProvider()

_register_defaults()
