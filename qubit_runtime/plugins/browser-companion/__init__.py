"""Browser companion plugin — owner-scoped browser context for the agent.

Ported from the companion plugin of ``hermes-browser-extension``
(https://github.com/abundantbeing/hermes-browser-extension) at pin
16d42430ff45d5833cb6176b401db867b591c2f2, MIT, Copyright (c) 2026 Jon Komet.
See PROVENANCE.json beside this file and ``docs/EXT_COLLISION_NOTE.md`` for the
PORT / DROP decision behind every name below.

Registers three tools and two hooks that let the Qubit agent read the browser
context the extension sends with a turn. The plugin is fail-soft: when the
extension is not connected or no context has been captured, every tool
gracefully reports unavailable.

What this plugin deliberately does NOT carry, because Qubit already owns it:

* the ``tab_*`` browser-control tools — Qubit's own ``browser_*`` tools already
  route to an attached extension controller through
  ``tools/browser_extension_router.py`` and ``gateway/browser_control_broker.py``;
* the HTTP readiness / context / control routes — Qubit's plugin surface has no
  ``register_platform_handler`` seam (``plugins/AGENTS.md``: plugins never touch
  core), so the port is tool-only;
* the event log, the rotation journal and durable receipts.
"""

from __future__ import annotations

from pathlib import Path

from . import hooks, tools
from .context_store import BrowserContextStore
from .schemas import SCHEMA_CLEAR_CONTEXT, SCHEMA_GET_CONTEXT, SCHEMA_STATUS

# Global store — shared between tools and hooks.
STORE = BrowserContextStore()

# What register() installs. A test keeps this equal to plugin.yaml and to the
# real registrations, so the manifest can never promise more than it delivers.
TOOL_NAMES = (
    "browser_context_status",
    "browser_get_context",
    "browser_clear_context",
)
HOOK_NAMES = ("pre_llm_call", "pre_tool_call")
TOOLSET = "browser-companion"
SKILL_NAME = "browser-context"


def register(ctx) -> None:
    """Register companion plugin tools, hooks, and the bundled skill."""
    tools.set_store(STORE)
    hooks.set_store(STORE)

    # -- Tools ----------------------------------------------------------
    ctx.register_tool(
        name="browser_context_status",
        toolset=TOOLSET,
        schema=SCHEMA_STATUS,
        handler=tools.browser_context_status,
        description="Check whether browser context is currently cached and available.",
        emoji="🌐",
    )

    ctx.register_tool(
        name="browser_get_context",
        toolset=TOOLSET,
        schema=SCHEMA_GET_CONTEXT,
        handler=tools.browser_get_context,
        description="Consume this turn's cached browser context once, using its opaque context_id.",
        emoji="📄",
    )

    ctx.register_tool(
        name="browser_clear_context",
        toolset=TOOLSET,
        schema=SCHEMA_CLEAR_CONTEXT,
        handler=tools.browser_clear_context,
        description="Clear the cached browser context. The next extension prompt will re-populate it.",
        emoji="🗑️",
    )

    # -- Hooks ----------------------------------------------------------
    ctx.register_hook("pre_llm_call", hooks.pre_llm_call)
    ctx.register_hook("pre_tool_call", hooks.pre_tool_call)

    # -- Bundled skill --------------------------------------------------
    skill_md = Path(__file__).resolve().parent / "skills" / SKILL_NAME / "SKILL.md"
    if skill_md.is_file():
        ctx.register_skill(SKILL_NAME, skill_md)

    # -- Cleanup --------------------------------------------------------
    ctx.on_unload(STORE.close)
    ctx.on_unload(tools.clear_leases)
