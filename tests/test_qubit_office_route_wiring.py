"""Audit Qubit Office route wiring and single-owner manifest behavior."""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_qubit_office_is_listed_and_full_height_without_being_a_plugin_route():
    app = (REPO / "frontend/qubit-web/src/App.tsx").read_text()
    assert '{ path: "/qubit-office", label: "Qubit Office"' in app
    routes = (REPO / "frontend/qubit-web/src/lib/chat-routes.ts").read_text()
    match = re.search(r"FULL_HEIGHT_ROUTE_PATHS = \[([^\]]*)\]", routes, re.S)
    assert match and '"/qubit-office"' in match.group(1)
    chat = re.search(r"CHAT_ROUTE_PATHS = \[([^\]]*)\]", routes, re.S)
    assert chat and "/qubit-office" not in chat.group(1)


def test_full_height_listing_is_actually_consumed_by_the_app_layout():
    """A route listed in FULL_HEIGHT_ROUTE_PATHS is inert unless App.tsx reads
    the predicate and applies the bounded layout it controls.

    Membership alone (the test above) is a string check: it stays green if the
    shell stops calling `isFullHeightPath`, or applies the flag to the wrong
    element — and the office would then expand to its content (the documented
    104,305px bug) with every listing test still passing. This asserts the two
    halves of the real contract in App.tsx: the predicate is COMPUTED from the
    pathname, and it gates the `min-h-0 flex flex-1 flex-col` chain the office's
    `height: 100%` iframe resolves against.
    """
    app = (REPO / "frontend/qubit-web/src/App.tsx").read_text()
    assert 'isFullHeightPath' in app, (
        "App.tsx does not import the full-height predicate; the office route "
        "would render in an auto-height block and expand to its content")
    # 1) it is derived from the live pathname, not statically set
    assert re.search(r"isFullHeightPath\(\s*pathname\s*\)", app), (
        "App.tsx does not compute the full-height flag from `pathname`; the "
        "flag cannot follow navigation")
    # 2) it gates the bounded layout chain the iframe's height:100% needs
    bounded = re.search(
        r"isFullHeightRoute\)\s*&&\s*\n?\s*\"min-h-0 flex flex-1 flex-col\"",
        app)
    assert bounded, (
        "the full-height flag does not gate `min-h-0 flex flex-1 flex-col`; "
        "the office iframe's `height: 100%` would resolve against an "
        "auto-height ancestor and expand")


def test_hidden_plugin_manifest_does_not_claim_the_builtin_office_path():
    fleet = json.loads((REPO / "frontend/qubit-web/public/plugins/fleet.json").read_text())
    office = next(row for row in fleet if row["name"] == "qubit-office")
    assert office["tab"]["path"] == "/qubit-office"
    assert office["tab"]["hidden"] is True
    manifest = json.loads((REPO / "frontend/qubit-app/plugins/qubit-office/manifest.json").read_text())
    assert manifest["tab"]["hidden"] is True
