"""OpenClaw contractual-pin gateway handler families.

Each submodule owns exactly one family group and exposes ``register(registry)``.
Every handler it registers must delegate to the same canonical Qubit module the
CLI, dashboard, and voice surfaces already use; the transport-neutral
``voicebridge.gateway.rpc.default_registry`` composes them so Qubit's single
gateway listener keeps one authority (no second broker, scheduler, or server).

A module is added only when the family has a real local counterpart. A family
with no real counterpart stays ``OPEN`` in the generated manifest; it is never
backed by a fabricated handler that only returns success.

``scripts/openclaw_upgrade_manifest.py`` scans these modules alongside
``voicebridge/gateway/rpc.py`` so each pinned method's evidence names the file
that actually defines its handler.
"""
from __future__ import annotations

import importlib
from typing import Any

#: Family modules composed into the canonical gateway registry, in order.
#: Module names are stable; adding one requires a real ``register`` callable.
FAMILY_MODULES = ("sessions", "sessions_advanced", "sessions_compaction",
                  "security", "agents", "mcp_app", "board", "system",
                  "content", "worktrees", "surface_control",
                  "users", "skills_extra", "talk_node", "scheduler_media",
                  "misc_surface")


def register_all(registry: Any) -> None:
    """Register every implemented family module into *registry*.

    A family module that does not exist yet is skipped (its methods remain
    explicitly ``OPEN``). Any other import failure is re-raised so a broken
    handler module can never be silently ignored.
    """
    for name in FAMILY_MODULES:
        qualified = f"{__name__}.{name}"
        try:
            mod = importlib.import_module(qualified)
        except ModuleNotFoundError as exc:
            if exc.name != qualified:
                raise
            continue
        mod.register(registry)
