#!/usr/bin/env python3
"""Paint the terminal with the last resolved TUI theme before the Ink bundle loads.

The TUI's theme boot cache already prevents the app itself flashing a default
palette, but Node spends 1-2 s preparing/building the bundle first. During that
window the terminal still shows the previous session's frame. Emit the cached
canvas and foreground immediately, then clear the old frame so the launch never
flashes the old gold/default colours.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    if not sys.stdout.isatty():
        return 0
    home = os.environ.get("QUBIT_HOME") or str(
        Path.home() / ".local" / "state" / "qubit-voicebridge" / "runtime"
    )
    try:
        data = json.loads((Path(home) / "tui-theme-boot.json").read_text(encoding="utf-8"))
    except Exception:
        data = {}
    theme = data.get("theme") if isinstance(data.get("theme"), dict) else {}
    colors = theme.get("color") if isinstance(theme.get("color"), dict) else {}
    background = data.get("background") or colors.get("background")
    foreground = colors.get("text")
    out = ["\x1b[0m"]
    if isinstance(background, str) and background:
        out.append(f"\x1b]11;{background}\x07")
    if isinstance(foreground, str) and foreground:
        out.append(f"\x1b]10;{foreground}\x07")
    out.extend(["\x1b[2J", "\x1b[H", "\x1b[3J"])
    sys.stdout.write("".join(out))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
