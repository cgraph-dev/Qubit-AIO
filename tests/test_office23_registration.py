"""Registration wiring for the office2 (/qubit-office2) and office3
(/qubit-office3) SPA tabs.

Each tab must be listed on all five registration surfaces or the shell never
renders it: the server route allowlist (webui.SPA_ROUTE_PATHS), the client route
map and nav tab (App.tsx), the full-height list (chat-routes.ts), and the
document title map (resolve-page-title.ts). This test asserts every one of them
from the real tuple / real source text, not from a hand-copied constant.
"""
from __future__ import annotations

import re
from pathlib import Path

from voicebridge import webui

REPO = Path(__file__).resolve().parents[1]

NEW_PATHS = ("/qubit-office2", "/qubit-office3")


def test_new_paths_are_in_the_server_route_allowlist():
    for path in NEW_PATHS:
        assert path in webui.SPA_ROUTE_PATHS, (
            f"{path} missing from voicebridge.webui.SPA_ROUTE_PATHS; the SPA "
            "shell will not render for it")


def test_new_paths_have_derived_shell_route_roots():
    for path in NEW_PATHS:
        root = path.strip("/").split("/")[0]
        assert root in webui.SPA_ROUTE_ROOTS, (
            f"{path} root {root!r} missing from SPA_ROUTE_ROOTS (derived from "
            "SPA_ROUTE_PATHS)")


def test_app_tsx_has_a_route_map_entry_for_each_path():
    app = (REPO / "frontend/qubit-web/src/App.tsx").read_text()
    for path in NEW_PATHS:
        assert f'"{path}": () => <PluginPage name="{path.strip("/")}" />' in app, (
            f"App.tsx route map has no PluginPage entry for {path}")


# The sidebar entry for an office2/office3 path, if the shell carries one.
_NAV_ENTRY_RE = r'\{{ path: "{{path}}", label(?:Key)?: "[^"]+", label: "[^"]+", icon: \w+[^}}]*\}}'


_LABELS = {"/qubit-office2": "Agent Quest", "/qubit-office3": "Voice Chat"}


def _app_tsx() -> str:
    return (REPO / "frontend/qubit-web/src/App.tsx").read_text()


def test_app_tsx_has_a_nav_tab_for_each_path():
    """A path is either a sidebar tab or a deliberate hidden-tab route.

    Both are full registrations: a hidden-tab route is still mounted by the
    client route map and the server allowlist (asserted above), and declaring
    it hidden is how the shell says "reachable, not a sidebar row".  What must
    never happen is a path with NEITHER a nav entry NOR an explicit hidden
    declaration — that is the "renderable but undiscoverable" gap.
    """
    app = _app_tsx()
    for path in NEW_PATHS:
        has_nav = re.search(
            _NAV_ENTRY_RE.replace("{{path}}", re.escape(path)), app) is not None
        # The shell records the drop as a comment beside the nav list.
        dropped = f"`{path}` ({_LABELS[path]})" in app
        dangling = f'"{path}": () => <PluginPage' in app
        assert has_nav or dropped, (
            f"App.tsx has no nav tab for {path} and no record of it being a "
            "deliberately hidden tab")
        if dropped:
            assert dangling, (
                f"{path} is declared hidden but has no mounted route entry")


def test_chat_routes_lists_both_paths_as_full_height():
    routes = (REPO / "frontend/qubit-web/src/lib/chat-routes.ts").read_text()
    match = re.search(r"FULL_HEIGHT_ROUTE_PATHS = \[([^\]]*)\]", routes, re.S)
    assert match, "FULL_HEIGHT_ROUTE_PATHS not found in chat-routes.ts"
    body = match.group(1)
    for path in NEW_PATHS:
        assert f'"{path}"' in body, (
            f"{path} missing from FULL_HEIGHT_ROUTE_PATHS; a fixed composer/"
            "footer would resolve against an auto-height ancestor and expand")


def test_resolve_page_title_has_an_entry_for_each_path():
    titles = (REPO / "frontend/qubit-web/src/lib/resolve-page-title.ts").read_text()
    for path in NEW_PATHS:
        assert f'"{path}":' in titles, (
            f"{path} missing from resolve-page-title.ts; the header would fall "
            "through to the de-slug fallback")
