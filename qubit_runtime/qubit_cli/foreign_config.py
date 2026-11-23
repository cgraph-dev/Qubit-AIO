"""Pure-data readers shared by the foreign configuration commands."""

import json
from pathlib import Path
from typing import Any


def load_openclaw_config(path: Path) -> dict[str, Any]:
    """Read OpenClaw's JSON5 format while keeping ordinary JSON on the fast path."""
    text = path.read_text(encoding="utf-8-sig")
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        from ._vendor.json5 import loads

        try:
            document = loads(text)
        except (ValueError, RecursionError):
            # Parser diagnostics can include fragments of a config with secrets.
            raise ValueError("Invalid OpenClaw JSON5 configuration") from None
    if not isinstance(document, dict):
        raise ValueError("OpenClaw configuration must be an object")
    return document
