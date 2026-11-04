"""The dashboard static/classic file resolver must stay inside its roots."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge import webui as ui  # noqa: E402


class _App:
    def __init__(self, root: Path) -> None:
        self._frontend_dir = root


def _handler(root: Path):
    handler = object.__new__(ui.WebUIHandler)
    handler.server = type("_Server", (), {"app": _App(root)})()
    return handler


def test_frontend_file_rejects_absolute_and_traversal(tmp_path: Path) -> None:
    root = tmp_path / "frontend"
    root.mkdir()
    (root / "index.html").write_text("ok")
    h = _handler(root)
    assert h._frontend_file("index.html") == (root / "index.html").resolve()
    for name in ("/etc/passwd", "../etc/passwd", "../../etc/passwd",
                 "%2e%2e/%2e%2e/etc/passwd", "/static//etc/passwd"):
        assert h._frontend_file(name) is None, name


def test_frontend_file_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "frontend"
    root.mkdir()
    outside = tmp_path / "secret"
    outside.write_text("secret")
    (root / "escape").symlink_to(outside)
    assert _handler(root)._frontend_file("escape") is None
