"""Voice command: "open browser" launches the shared real-profile CDP browser."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge import voice_commands as VC


def test_open_browser_resolves_to_cdp_launcher():
    r = VC.resolve("hey qubit open browser")
    assert r is not None
    assert r.entry.id == "browser_cdp_open"
    assert r.entry.backend == "browser_cdp"
    assert "browser" in r.entry.parity
    assert callable(VC._BACKENDS["browser_cdp"])


def test_open_browser_profile_still_resolves_to_profile_launcher():
    r = VC.resolve("hey qubit open browser profile work")
    assert r is not None
    assert r.entry.id == "browser_profile_open"
