"""P6-05 accessibility constants and hashing helpers."""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "frontend" / "qubit-app" / "plugins" / "qubit-office"
APP = PLUGIN / "game" / "app"
STYLES = PLUGIN / "game" / "styles"
SHELL = APP / "OfficeAppShell.tsx"
A11Y_TS = APP / "a11y.ts"
GAME_CSS = STYLES / "game.css"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
