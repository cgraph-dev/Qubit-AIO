"""Runtime event names for the browser-companion plugin.

Ported from the companion plugin of ``hermes-browser-extension``
(https://github.com/abundantbeing/hermes-browser-extension) at pin
16d42430ff45d5833cb6176b401db867b591c2f2, MIT, Copyright (c) 2026 Jon Komet.
See PROVENANCE.json beside this file. Local divergence: rebranding only.
"""

RUN_STARTED = "run.started"
RUN_COMPLETED = "run.completed"
TOOL_STARTED = "tool.started"
TOOL_PROGRESS = "tool.progress"
TOOL_FINISHED = "tool.finished"
BROWSER_CONTEXT_UPDATED = "browser.context.updated"
BROWSER_CONTEXT_CLEARED = "browser.context.cleared"
BROWSER_CONTROL_AVAILABLE = "browser.control.available"
BROWSER_CONTROL_UNAVAILABLE = "browser.control.unavailable"
BROWSER_CONTROL_APPROVAL_REQUESTED = "browser.control.approval_requested"
BROWSER_CONTROL_DETACHED = "browser.control.detached"

CONTROL_EVENTS_ENABLED = False

# Upstream emitted ``hermes.browser.control.*``; this port speaks ``qubit.browser.control.*``.
_CONTROL_EVENTS = {
    "qubit.browser.control.available": BROWSER_CONTROL_AVAILABLE,
    "qubit.browser.control.unavailable": BROWSER_CONTROL_UNAVAILABLE,
    "qubit.browser.control.approval_requested": BROWSER_CONTROL_APPROVAL_REQUESTED,
    "qubit.browser.control.detached": BROWSER_CONTROL_DETACHED,
}


def normalize_event_name(name: str) -> str:
    """Normalize a raw event name to a canonical browser-companion event name."""
    raw = str(name or "")
    if raw in _CONTROL_EVENTS:
        return _CONTROL_EVENTS[raw]
    if "control" in raw.lower():
        return "runtime.unknown"
    if raw == "qubit.tool.progress":
        return TOOL_PROGRESS
    return raw or "runtime.unknown"
