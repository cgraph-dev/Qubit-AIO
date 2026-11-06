"""browser-companion — the Qubit side of the Qubit Browser Extension.

Ported 1:1 from the companion plugin of ``hermes-browser-extension``
(https://github.com/abundantbeing/hermes-browser-extension) at pin
16d42430ff45d5833cb6176b401db867b591c2f2, MIT, Copyright (c) 2026 Jon Komet.
See ``PROVENANCE.json`` beside this file and ``docs/BROWSER_EXT_1TO1.md`` for the
per-file disposition and the routing decision behind every tool.

What it carries, all of it upstream's:

* **Seven context tools** — the owner-scoped Browser Context Protocol (BCP v2)
  cache the extension sends with a turn, reachable only through a one-shot lease
  granted by the ``pre_tool_call`` hook.
* **Twenty control tools** (``tab_*``) — the agent acting in the person's own
  browser through the extension. Registered and listed always; each call answers
  ``no_browser`` with nothing attached. Where Qubit's *core* extension control is
  switched on the extension attaches there instead, and these are not registered
  (upstream's rule, kept).
* **Three hooks** — ``pre_llm_call`` (cache a full BCP envelope), ``pre_tool_call``
  (authorize a tool call with a one-shot lease), ``post_tool_call`` (redacted
  diagnostic event + receipt).
* **Eight HTTP routes** wired through Qubit's ``register_platform_handler("api_server")``
  seam: readiness, the browser-context side channel (status, upload, action
  descriptors), the two controller-attach routes and the read-only profile roster
  and avatars. They are what makes the pair talk.
* **One bundled skill** — ``browser-companion``.

Fail-soft is upstream's documented contract and is kept everywhere: with no
browser context and no attached controller, every tool answers a typed
"unavailable", and no route ever raises into the host.
"""

from __future__ import annotations

from pathlib import Path

from . import context_routes, control_routes, control_tools, hooks, profile_routes, status_route, tools
from .context_store import BrowserContextStore
from .control_broker import get_control_broker
from .namespace import PLUGIN_NAME, TOOLSET

# Global store — shared between tools and hooks.
STORE = BrowserContextStore()

# What register() installs; the readiness route reports exactly these names and
# a test keeps them equal to plugin.yaml and to the real registrations.
TOOL_NAMES = (
    "browser_context_status",
    "browser_get_context",
    "browser_clear_context",
    "browser_event_log",
    "browser_control_status",
    "browser_context_journal",
    "browser_text_utility",
    *control_tools.TOOL_NAMES,
)
HOOK_NAMES = ("pre_llm_call", "pre_tool_call", "post_tool_call")
CONTEXT_TOOL_NAMES = tuple(name for name in TOOL_NAMES if name not in control_tools.TOOL_NAMES)
SKILL_NAME = "browser-companion"


def _chain(*wires):
    """One ``wire_extra`` that runs each given one; the status route's factory takes a single hook."""

    def wire(router, adapter) -> None:
        for wire_one in wires:
            wire_one(router, adapter)

    return wire


def register(ctx) -> None:
    """Register companion plugin tools, hooks, routes and bundled skill."""
    durable_receipts = ctx.get_config("durable_receipts", default=True)
    receipt_limit = ctx.get_config("receipt_limit", default=8)
    # The live PluginContext always has `state`; the catalog validator's
    # recording context does not, so a missing one means memory-only receipts.
    state = getattr(ctx, "state", None)
    if state is not None:
        STORE.configure_durable_state(
            state,
            enabled=durable_receipts is True,
            receipt_limit=receipt_limit,
        )
    # Metadata-only rotation journal, persisted under the plugin's profile-scoped
    # data dir (mode 0600 where supported) when one exists, memory-only otherwise.
    try:
        data_dir = getattr(state, "data_dir", None)
    except Exception:
        data_dir = None
    STORE.configure_journal(data_dir)
    # Tell tools which store to use
    tools.set_store(STORE)
    hooks.set_store(STORE)

    # ── Tools ──────────────────────────────────────────────────────────
    ctx.register_tool(
        name="browser_context_status",
        toolset=TOOLSET,
        schema=tools.SCHEMA_STATUS,
        handler=tools.browser_context_status,
        description="Check whether browser context is currently cached and available.",
        emoji="🌐",
    )

    ctx.register_tool(
        name="browser_get_context",
        toolset=TOOLSET,
        schema=tools.SCHEMA_GET_CONTEXT,
        handler=tools.browser_get_context,
        description="Consume this turn's cached browser context once, using its opaque context_id.",
        emoji="📄",
    )

    ctx.register_tool(
        name="browser_clear_context",
        toolset=TOOLSET,
        schema=tools.SCHEMA_CLEAR_CONTEXT,
        handler=tools.browser_clear_context,
        description="Clear the cached browser context. The next extension prompt will re-populate it.",
        emoji="🗑️",
    )

    ctx.register_tool(
        name="browser_event_log",
        toolset=TOOLSET,
        schema=tools.SCHEMA_EVENT_LOG,
        handler=tools.browser_event_log,
        description="Return recent browser companion events for diagnostics.",
        emoji="📋",
    )

    ctx.register_tool(
        name="browser_control_status",
        toolset=TOOLSET,
        schema=tools.SCHEMA_CONTROL_STATUS,
        handler=tools.browser_control_status,
        description="Return historical owner-scoped browser control metadata; never live authority.",
        emoji="🧭",
    )

    ctx.register_tool(
        name="browser_context_journal",
        toolset=TOOLSET,
        schema=tools.SCHEMA_JOURNAL,
        handler=tools.browser_context_journal,
        description="Return the metadata-only owner-scoped journal of stored browser-context deliveries; never page data.",
        emoji="📒",
    )

    ctx.register_tool(
        name="browser_text_utility",
        toolset=TOOLSET,
        schema=tools.SCHEMA_TEXT_UTILITY,
        handler=tools.browser_text_utility,
        description="Run bounded deterministic text cleanup, bullets, stats, or diff without a model call.",
        emoji="⚙️",
    )

    # ── Qubit Control tools ────────────────────────────────────────────
    # Always listed (no attach-dependent check, so the tool list never changes mid-conversation),
    # except on a Qubit whose own extension control is on: the extension attaches to that instead.
    try:
        profile = getattr(ctx, "profile_name", "default")
    except Exception:
        profile = "default"
    control_listed = control_tools.register_control_tools(
        ctx, broker=get_control_broker(), profile=profile, data_dir=data_dir
    )
    listed_tools = TOOL_NAMES if control_listed else CONTEXT_TOOL_NAMES

    # ── Hooks ──────────────────────────────────────────────────────────
    ctx.register_hook("pre_llm_call", hooks.pre_llm_call)
    ctx.register_hook("pre_tool_call", hooks.pre_tool_call)
    ctx.register_hook("post_tool_call", hooks.post_tool_call)

    # ── Routes ─────────────────────────────────────────────────────────
    # The readiness route, the browser-context side channel (context status,
    # context upload, declarative actions) and the Qubit Control routes, all
    # authenticated by the API server's own check and wired by one factory;
    # skipped (never fatal) where the host refuses the seam. See status_route.py,
    # context_routes.py and control_routes.py.
    version = status_route.plugin_version()
    status_route.register_status_route(
        ctx,
        tools=listed_tools,
        hooks=HOOK_NAMES,
        version=version,
        wire_extra=_chain(
            context_routes.make_wire_extra(STORE, version=version),
            control_routes.make_wire_extra(get_control_broker(), profile=profile),
            profile_routes.make_wire_extra(),
        ),
        routes=(*status_route.ROUTE_PATHS, *control_routes.ROUTE_PATHS, *profile_routes.ROUTE_PATHS),
    )

    # ── Bundled skill ──────────────────────────────────────────────────
    skills_dir = Path(__file__).resolve().parent / "skills"
    skill_md = skills_dir / SKILL_NAME / "SKILL.md"
    if skill_md.is_file():
        ctx.register_skill(SKILL_NAME, skill_md)

    # ── Cleanup ────────────────────────────────────────────────────────
    on_unload = getattr(ctx, "on_unload", None)
    if callable(on_unload):
        on_unload(STORE.close)
        on_unload(tools.clear_leases)


__all__ = [
    "CONTEXT_TOOL_NAMES",
    "HOOK_NAMES",
    "PLUGIN_NAME",
    "SKILL_NAME",
    "STORE",
    "TOOL_NAMES",
    "register",
]
