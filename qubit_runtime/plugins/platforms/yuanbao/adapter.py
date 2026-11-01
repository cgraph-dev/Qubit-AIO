"""Yuanbao adapter module: one class object shared with the built-in platform module.

The platform moved from ``gateway.platforms.yuanbao`` to the bundled plugin, but both
the gateway's built-in adapter tests and the plugin tool/standalone paths must see the
SAME ``YuanbaoAdapter`` class (``isinstance`` across the two import names, and the
per-home ``get_active`` slot). Re-export the canonical implementation instead of
keeping a drifted second copy.
"""

from gateway.platforms.yuanbao import *  # noqa: F401,F403
from gateway.platforms.yuanbao import WEBSOCKETS_AVAILABLE, YuanbaoAdapter  # noqa: F401
