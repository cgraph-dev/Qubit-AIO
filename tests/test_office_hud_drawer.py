"""The Office HUD drawer shows the HUD — proved in two real documents.

THE DEFECT THIS PINS
--------------------
The owner's screenshot showed the HUD drawer painting
``"The HUD is not available on this page: its bundle ... has not registered."``
The previous design looked for the HUD's registration in the OFFICE document's
``window.__QUBIT_PLUGINS__``, but the office page is a full-bleed iframe of the
vendored app — a DIFFERENT document that never loads the HUD bundle. A
registration cannot cross a document boundary, so the drawer could never show
anything but its refusal.

The fix frames the HUD's own route instead. That needs the server to permit the
frame, so there are two halves to prove, in two places:

  * ``tests/test_webui_security_hardening9u.py`` — the grant is on
    ``/qubit-hud`` and on nothing else (a recording handler, no socket).
  * ``tests/_probe_hud_http.py`` — the same grant as real response headers from
    a real server on an ephemeral port (deny everywhere else).
  * this file — the DRAWER renders a frame of ``/qubit-hud``, and the HUD's own
    served bundle, executed in that frame's document against real HTTP, renders
    its seven tabs with real data.

Hermetic: an ephemeral loopback port, a temporary dashboard dist, and the
repo's own bundle files. No external network, no provider call.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WEB = REPO / "frontend" / "qubit-web"
GAME_UI = (REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office"
           / "game" / "ui")
CHECK = GAME_UI / "hud-dock.domcheck.mjs"
HUD_BUNDLE = (REPO / "frontend" / "qubit-app" / "plugins" / "qubit-hud"
              / "qubit-hud.js")
HUD_PANEL_TS = GAME_UI / "HudPanel.ts"

from _office_toolchain import node_path, ensure_tsc  # noqa: E402

pytestmark = pytest.mark.unit


def _have_toolchain() -> bool:
    return bool(node_path()) and (WEB / "node_modules" / "jsdom").is_dir()


skip_no_toolchain = pytest.mark.skipif(
    not _have_toolchain(),
    reason="node + qubit-web node_modules (jsdom, react) are required")


# ======================================================================
# 1 — the mechanism is an iframe of the HUD's own route, not a lookup
# ======================================================================
def test_the_dock_frames_the_hud_route_instead_of_demanding_a_registration():
    """The design failure was a cross-document registry lookup. The dock must
    hand the HUD's own ROUTE to a frame; it must not silently regress to
    searching this document for a registration that can never be there."""
    src = HUD_PANEL_TS.read_text()
    assert 'HUD_PATH = "/qubit-hud"' in src, (
        "the dock no longer names the HUD's route")
    assert "createElement(\"iframe\"" in src, (
        "the dock does not render a frame — the panels cannot come from a "
        "document this one never loads")
    assert "src," in src, "the frame is not given the HUD route as its src"
    # The dead mechanism: a wrapper over the LOCAL registry, which cannot see
    # the HUD's registration in the office document.
    for gone in ("watchHudRegistration", "hudDockProps", "setHudDockProps",
                 "__hudTap"):
        assert gone not in src, (
            f"the dock still carries {gone}: that mechanism asks a document "
            f"for a registration only another document ever makes")
    # The escape hatch has to stay: the route is the authority.
    assert "Open HUD in its own tab" in src


def test_the_office_still_keeps_an_open_hud_link():
    """Rule 2: the existing 'Open HUD' affordance keeps working."""
    shell = (REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office"
             / "game" / "app" / "OfficeAppShell.tsx").read_text()
    assert '"office-game-hud-open-link"' in shell, (
        "the dock head no longer offers 'Open HUD'")
    assert '"Open HUD"' in shell


def test_the_dock_issues_no_request_of_its_own():
    """C4: the office page is read-only by construction. The dock may render a
    frame; it may not spend the office's authority on a fetch."""
    src = HUD_PANEL_TS.read_text()
    body = src[src.index("export function createHudDock"):]
    for banned in ("fetch(", "XMLHttpRequest", "sendBeacon", "axios",
                   "method:", "POST"):
        assert banned not in body, (
            f"the dock's body references {banned}: a drawer that is allowed "
            f"to request cannot guarantee the office stays read-only")


def test_the_office_entry_no_longer_wires_a_hud_registry_tap():
    src = (REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office"
           / "game" / "entry.ts").read_text()
    assert "watchHudRegistration" not in src
    assert "setHudDockProps" not in src


# ======================================================================
# 2 — the drawer renders the frame, and the HUD renders real data in it
# ======================================================================
@skip_no_toolchain
def test_the_drawer_shows_the_hud_with_real_panel_data(tmp_path):
    """THE END-TO-END PROOF, in two real DOMs against a real HTTP server.

    Compiles the dock, serves the HUD's real routes through the real compat
    dispatch, then:
      * mounts the dock with real React and asserts it renders an iframe of
        /qubit-hud with no refusal text, and
      * executes the HUD's SERVED bundle in that frame's document and asserts
        all seven tabs paint tiles or rows from live data.
    """
    # The dock's TS -> the ESM module the node check imports (`./hud-panel.mjs`).
    tsc = ensure_tsc()
    assert tsc is not None, "no usable tsc for the dock compile"
    out = tmp_path / "js"
    proc = subprocess.run(
        [node_path(), str(tsc), "--module", "es2020", "--target", "es2020",
         "--moduleResolution", "node", "--ignoreDeprecations", "6.0",
         "--outDir", str(out), "--skipLibCheck",
         "--noEmitOnError", "false", str(HUD_PANEL_TS)],
        capture_output=True, text=True, timeout=180, cwd=str(GAME_UI))
    compiled = out / "HudPanel.js"
    assert compiled.is_file(), (
        f"the dock did not compile — a FAILURE, not a skip:\n{proc.stderr[:800]}")

    # The harness stages the module + the check inside qubit-web (so jsdom and
    # react resolve), boots the REAL handler on an ephemeral port, serves the
    # HUD's routes through the real compat dispatch, and runs the node check.
    run = subprocess.run(
        [sys.executable, str(REPO / "tests" / "_probe_hud_dom.py"),
         str(compiled)],
        cwd=str(REPO), text=True, capture_output=True, timeout=600)
    assert run.returncode == 0, (
        f"the drawer check failed\nstdout:\n{run.stdout[-6000:]}\n"
        f"stderr:\n{run.stderr[-2000:]}")
    assert "ALL CHECKS PASS" in run.stdout, run.stdout[-3000:]


# ======================================================================
# 3 — the grant on the wire: real server, real response headers
# ======================================================================
def test_the_frame_is_permitted_on_the_hud_route_and_denied_elsewhere():
    """The browser enforces `X-Frame-Options`; so this reads it from a REAL
    response on an ephemeral loopback port, not from the source."""
    run = subprocess.run(
        [sys.executable, str(REPO / "tests" / "_probe_hud_http.py")],
        cwd=str(REPO), text=True, capture_output=True, timeout=240)
    assert run.returncode == 0, (
        f"header probe failed\n{run.stdout[-3000:]}\n{run.stderr[-1500:]}")
    out = run.stdout
    assert "8/8 on-the-wire header cases pass" in out, out[-2500:]
