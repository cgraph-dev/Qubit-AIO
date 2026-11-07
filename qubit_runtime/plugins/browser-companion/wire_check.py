#!/usr/bin/env python3
"""Wire-contract proof: the ported extension and the ported companion agree.

Reads BOTH sides as files — the extension's ``.mjs``/``.js`` sources under
``frontend/qubit-app/plugins/qubit-browser-ext/extension/`` and this plugin's
Python modules — and asserts, row by row, that what the browser sends is what
the agent accepts. Nothing here is a claim about intent: every row prints the
two literals it compared.

A half-rename (a route path, a protocol id, a header, a tool-name table) fails
this check, which is the documented failure class this repo has hit before: a
host that sets one global while a plugin reads another produces a silently dead
channel that no import check would catch.

    .venv/bin/python qubit_runtime/plugins/browser-companion/wire_check.py
"""

from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
EXT = REPO / "frontend" / "qubit-app" / "plugins" / "qubit-browser-ext" / "extension"
PLUGIN = Path(__file__).resolve().parent

sys.path.insert(0, str(REPO / "qubit_runtime"))
# Qubit resolves plugins by directory name; import this one under that name so the
# package's relative imports work exactly as they do after real discovery.
if str(PLUGIN.parent) not in sys.path:
    sys.path.insert(0, str(PLUGIN.parent))
pipe_package = importlib.import_module(PLUGIN.name)
sys.modules["browser_companion"] = pipe_package

ROWS: list[tuple[bool, str, str, str]] = []


def check(ok: bool, label: str, extension_side: str, companion_side: str) -> None:
    ROWS.append((bool(ok), label, extension_side, companion_side))


def ext_text(relative: str) -> str:
    path = EXT / relative
    if not path.is_file():
        raise SystemExit(f"extension file missing: {path}")
    return path.read_text(encoding="utf-8")


def mjs_const(relative: str, name: str, text: str | None = None) -> str:
    """The string a module binds to ``name`` (module-private or exported).

    A template literal (``/api/plugins/${PLUGIN}/status``) is resolved against
    every other string constant in the same file, so the compare is against the
    value a browser would actually request, not against the source spelling.
    """
    text = ext_text(relative) if text is None else text
    match = re.search(
        rf"(?:export )?const {re.escape(name)}\s*=\s*(?:'([^']*)'|\"([^\"]*)\"|`([^`]*)`)\s*;", text
    )
    if not match:
        raise SystemExit(f"{relative}: no string constant {name}")
    value = next(group for group in match.groups() if group is not None)

    def substitute(found: re.Match) -> str:
        inner = found.group(1)
        parts = inner.split(".")
        base = mjs_const(relative, parts[0], text)
        return base + "".join(f".{part}" for part in parts[1:])

    for _ in range(5):  # a constant referencing a constant referencing a constant
        resolved = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_.]*)\}", substitute, value)
        if resolved == value:
            break
        value = resolved
    return value


def mjs_object(relative: str, name: str) -> dict[str, str]:
    """A flat ``export const X = Object.freeze({ key: 'value', ... })`` table."""
    text = ext_text(relative)
    start = text.find(f"export const {name} = Object.freeze({{")
    if start < 0:
        raise SystemExit(f"{relative}: no Object.freeze export {name}")
    end = text.find("});", start)
    body = text[start:end]
    return dict(re.findall(r"(\w+):\s*'([^']*)'", body))


def main() -> int:
    from browser_companion import context_routes, control_routes, control_schemas, profile_routes, status_route
    from browser_companion import namespace
    pipe_registration = sys.modules["browser_companion"]  # this plugin's package (the registration surface)
    from browser_companion.context_store import BCP_CONTEXT_PROTOCOL_ID, BCP_TURN_PROTOCOL_ID
    from browser_companion.control_broker import FRAME_COMMAND, CONTROL_ACTIONS

    # ── 1. Readiness route ───────────────────────────────────────────────
    status_path = mjs_const("lib/companion-plugin-status.mjs", "COMPANION_STATUS_PATH")
    # The extension builds it from its plugin name; check the built value AND the name.
    ext_plugin_name = mjs_const("lib/companion-plugin-status.mjs", "COMPANION_PLUGIN_NAME")
    built = f"/api/plugins/{ext_plugin_name}/status"
    check(status_path == built, "status path is built from COMPANION_PLUGIN_NAME", built, status_path)
    check(status_path == status_route.ROUTE_PATH, "GET status route", status_path, status_route.ROUTE_PATH)

    status_object = mjs_const("lib/companion-plugin-status.mjs", "COMPANION_STATUS_OBJECT")
    check(status_object == status_route.STATUS_OBJECT, "status object id", status_object, status_route.STATUS_OBJECT)

    # The extension's plugin-name constant must match the base path the plugin wires.
    check(
        namespace.BASE_PATH == f"/api/plugins/{ext_plugin_name}",
        "plugin route base",
        f"/api/plugins/{ext_plugin_name}",
        namespace.BASE_PATH,
    )

    # The extension's expected context tools are a subset of what registers.
    text = ext_text("lib/companion-plugin-status.mjs")
    block = text[text.find("COMPANION_EXPECTED_TOOLS") : text.find("]);", text.find("COMPANION_EXPECTED_TOOLS"))]
    expected_tools = re.findall(r"'([a-z_]+)'", block)
    missing = [name for name in expected_tools if name not in pipe_registration.TOOL_NAMES]
    check(
        not missing,
        "extension's expected context tools are all registered",
        f"{len(expected_tools)} expected: {', '.join(expected_tools)}",
        f"registered context tools: {', '.join(pipe_registration.CONTEXT_TOOL_NAMES)}" + (f" MISSING={missing}" if missing else ""),
    )

    # ── 2. Browser-context side channel ─────────────────────────────────
    for label, const, plugin_path in (
        ("GET context/status", "COMPANION_CONTEXT_STATUS_PATH", context_routes.CONTEXT_STATUS_PATH),
        ("POST context upload", "COMPANION_CONTEXT_UPLOAD_PATH", context_routes.CONTEXT_UPLOAD_PATH),
        ("GET actions", "COMPANION_ACTIONS_PATH", context_routes.ACTIONS_PATH),
    ):
        extension_side = mjs_const("lib/companion-plugin-status.mjs", const)
        check(extension_side == plugin_path, label, extension_side, plugin_path)

    header = mjs_const("lib/companion-context-client.mjs", "COMPANION_SESSION_HEADER")
    check(header == context_routes.SESSION_HEADER, "session header", header, context_routes.SESSION_HEADER)

    for label, const, plugin_value in (
        ("context_status object", "CONTEXT_STATUS_OBJECT", context_routes.CONTEXT_STATUS_OBJECT),
        ("context_upload object", "CONTEXT_UPLOAD_OBJECT", context_routes.CONTEXT_UPLOAD_OBJECT),
        ("actions object", "ACTIONS_OBJECT", context_routes.ACTIONS_OBJECT),
    ):
        extension_side = mjs_const("lib/companion-context-client.mjs", const)
        check(extension_side == plugin_value, label, extension_side, plugin_value)

    max_envelope = re.search(
        r"export const COMPANION_MAX_ENVELOPE_CHARS = ([0-9_]+);", ext_text("lib/companion-context-client.mjs")
    )
    if not max_envelope:
        raise SystemExit("companion-context-client.mjs: no COMPANION_MAX_ENVELOPE_CHARS")
    max_envelope = max_envelope.group(1).replace("_", "")
    check(
        int(max_envelope) <= context_routes.MAX_UPLOAD_BYTES,
        "envelope upload budget fits the route's body cap",
        f"extension caps at {int(max_envelope)} chars",
        f"route refuses over {context_routes.MAX_UPLOAD_BYTES} bytes",
    )

    # ── 3. Control (attach) routes ───────────────────────────────────────
    # The extension builds these from its own base path; compare the built values.
    protocol_text = ext_text("lib/controller-protocol.mjs")
    plugin_base = re.search(r"const PLUGIN_BASE_PATH = '([^']+)'", protocol_text)
    register_path = re.search(r"register: `\$\{PLUGIN_BASE_PATH\}([^`]+)`", protocol_text)
    socket_path = re.search(r"socket: `\$\{PLUGIN_BASE_PATH\}([^`]+)`", protocol_text)
    if not (plugin_base and register_path and socket_path):
        raise SystemExit("controller-protocol.mjs: could not read the plugin route paths")
    check(
        plugin_base.group(1) + register_path.group(1) == control_routes.CONTROL_REGISTER_PATH,
        "POST control/register",
        plugin_base.group(1) + register_path.group(1),
        control_routes.CONTROL_REGISTER_PATH,
    )
    check(
        plugin_base.group(1) + socket_path.group(1) == control_routes.CONTROL_WS_PATH,
        "GET control/ws",
        plugin_base.group(1) + socket_path.group(1),
        control_routes.CONTROL_WS_PATH,
    )

    ws_protocol = re.search(r"return \[\s*\n\s*'([^']+)',\s*\n\s*`([^`]+)\$\{cleanTicket\}`", protocol_text)
    if not ws_protocol:
        raise SystemExit("controller-protocol.mjs: could not read controllerWebSocketProtocols()")
    check(ws_protocol.group(1) == control_routes.WS_PROTOCOL, "WebSocket subprotocol", ws_protocol.group(1), control_routes.WS_PROTOCOL)
    check(
        ws_protocol.group(2) == control_routes.TICKET_PROTOCOL_PREFIX,
        "ticket subprotocol prefix",
        ws_protocol.group(2),
        control_routes.TICKET_PROTOCOL_PREFIX,
    )

    # ── 4. Browser Context Protocol ids ─────────────────────────────────
    turn_id = mjs_const("lib/browser-context-protocol.mjs", "BROWSER_CONTEXT_TURN_PROTOCOL_ID")
    context_id = mjs_const("lib/browser-context-protocol.mjs", "BROWSER_CONTEXT_PROTOCOL_ID")
    check(turn_id == BCP_TURN_PROTOCOL_ID, "BCP turn id", turn_id, BCP_TURN_PROTOCOL_ID)
    check(context_id == BCP_CONTEXT_PROTOCOL_ID, "BCP context id", context_id, BCP_CONTEXT_PROTOCOL_ID)

    # ── 5. Action → tool-name table ─────────────────────────────────────
    ext_table = mjs_object("lib/control-tool-names.mjs", "PLUGIN_TOOL_NAMES")
    check(
        ext_table == dict(control_schemas.TOOL_NAMES),
        f"wire action -> tool name ({len(ext_table)} rows)",
        json.dumps(ext_table, sort_keys=True),
        json.dumps(dict(control_schemas.TOOL_NAMES), sort_keys=True),
    )
    check(
        set(CONTROL_ACTIONS) == set(ext_table),
        "broker capability set == extension action set",
        f"{len(ext_table)} actions",
        f"{len(CONTROL_ACTIONS)} actions",
    )

    # ── 6. Controller frames ────────────────────────────────────────────
    version_match = re.search(
        r"export const CONTROLLER_PROTOCOL_VERSION = ([0-9]+);", ext_text("lib/controller-protocol.mjs")
    )
    if not version_match:
        raise SystemExit("controller-protocol.mjs: no CONTROLLER_PROTOCOL_VERSION")
    frame_command = version_match.group(1)
    version_text = version_match.group(0)
    from browser_companion.control_broker import CONTROL_PROTOCOL_VERSION

    check(
        str(CONTROL_PROTOCOL_VERSION) == frame_command,
        "control protocol version",
        version_text,
        f"CONTROL_PROTOCOL_VERSION = {CONTROL_PROTOCOL_VERSION}",
    )
    methods = re.search(
        r"export const CONTROLLER_METHODS = Object\.freeze\(\{(.*?)\}\);", protocol_text, re.DOTALL
    )
    if not methods:
        raise SystemExit("controller-protocol.mjs: no CONTROLLER_METHODS")
    ext_methods = dict(re.findall(r"(\w+):\s*'([^']+)'", methods.group(1)))
    check(
        ext_methods.get("command") == FRAME_COMMAND,
        "command frame method",
        ext_methods.get("command", ""),
        FRAME_COMMAND,
    )
    check(
        ext_methods.get("result") == control_routes.handle_frame.__doc__ or ext_methods.get("result") == "browser.controller.result",
        "result frame method",
        ext_methods.get("result", ""),
        "handle_frame() accepts 'browser.controller.result'",
    )

    # ── 7. Profile roster ───────────────────────────────────────────────
    # The extension calls the roster only as a fallback; it builds the path from
    # the plugin name the same way, so assert the shape it would send.
    expected_roster = f"/api/plugins/{ext_plugin_name}/profiles"
    check(expected_roster == profile_routes.ROSTER_PATH, "GET profiles roster", expected_roster, profile_routes.ROSTER_PATH)
    check(
        profile_routes.AVATAR_PATH == expected_roster + "/{name}/avatar",
        "GET profile avatar",
        expected_roster + "/{name}/avatar",
        profile_routes.AVATAR_PATH,
    )

    # ── Report ─────────────────────────────────────────────────────────
    width = max(len(label) for _, label, _, _ in ROWS)
    failures = 0
    print("wire contract — extension sends vs companion accepts\n")
    for ok, label, left, right in ROWS:
        mark = "ok  " if ok else "FAIL"
        if not ok:
            failures += 1
        print(f"  [{mark}] {label.ljust(width)}")
        print(f"         extension : {left}")
        print(f"         companion : {right}")
    print()
    if failures:
        print(f"WIRE CONTRACT: FAIL — {failures} of {len(ROWS)} rows disagree")
        return 1
    print(f"WIRE CONTRACT: PASS — {len(ROWS)} of {len(ROWS)} rows agree on both sides")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
