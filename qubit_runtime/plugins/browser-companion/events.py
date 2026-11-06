"""Runtime event names for the Qubit Browser Extension plugin."""

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


def normalize_event_name(name: str) -> str:
    """Normalize a raw event name to a canonical companion event name."""
    raw = str(name or "")
    control_events = {
        "qubit.browser.control.available": BROWSER_CONTROL_AVAILABLE,
        "qubit.browser.control.unavailable": BROWSER_CONTROL_UNAVAILABLE,
        "qubit.browser.control.approval_requested": BROWSER_CONTROL_APPROVAL_REQUESTED,
        "qubit.browser.control.detached": BROWSER_CONTROL_DETACHED,
    }
    if raw in control_events:
        return control_events[raw]
    if "control" in raw.lower():
        return "runtime.unknown"
    if raw == "qubit.tool.progress":
        return TOOL_PROGRESS
    return raw or "runtime.unknown"
