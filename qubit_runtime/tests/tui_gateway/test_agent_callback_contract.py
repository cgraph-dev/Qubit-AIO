"""Every callback the TUI passes to ``AIAgent`` must be a real constructor parameter.

The factory forwards ``**_agent_cbs(sid)`` verbatim. A callback added to the TUI table but
not to the constructor raises ``TypeError: unexpected keyword argument`` when the user opens a
session — a live-startup failure that a table-only test cannot see.
"""

from __future__ import annotations

import inspect

from run_agent import AIAgent
from tui_gateway import server


def test_every_tui_callback_is_an_aiagent_constructor_param():
    params = set(inspect.signature(AIAgent.__init__).parameters)
    missing = sorted(set(server._agent_cbs("contract-sid")) - params)
    assert not missing, f"TUI passes unsupported AIAgent kwargs: {missing}"
