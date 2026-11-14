"""P6-05 accessibility phase fixtures.

Loads the deterministic audit script by path so the tests do not depend on the
root ``pytest.ini`` pythonpath order and never touch a live service, a browser,
a credential or the network.
"""
from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "frontend" / "qubit-app" / "plugins" / "qubit-office"
APP = PLUGIN / "game" / "app"
STYLES = PLUGIN / "game" / "styles"
SHELL = APP / "OfficeAppShell.tsx"
A11Y_TS = APP / "a11y.ts"
GAME_CSS = STYLES / "game.css"


def _load_audit():
    path = ROOT / "scripts" / "accessibility_audit.py"
    spec = importlib.util.spec_from_file_location("p6_accessibility_audit", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def audit():
    return _load_audit()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
