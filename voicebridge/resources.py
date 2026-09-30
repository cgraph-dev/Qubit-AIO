"""Packet 8K: portable resource resolution.

A clean wheel install ships the runtime resources (dashboard frontend, config
examples, prompt dialogs, task templates, report templates, systemd units)
under ``voicebridge/_data/``. When the package is run from a source checkout
instead, the same resources are resolved from the repository tree. This module
is the single resolution point so installed packages never depend on a
developer's private paths or on the checkout being present.

``resource_path(name)`` returns the first existing candidate. The returned
path is read-only; callers must never write into it.
"""
from __future__ import annotations

from pathlib import Path

# <installed package>/_data/<name> — populated by the wheel force-include.
_PACKAGE_DATA = Path(__file__).resolve().parent / "_data"

# <repo root>/<name> — when running from a source checkout.
_REPO_ROOT = Path(__file__).resolve().parents[1]


def resource_path(name: str) -> Path:
    """Resolve a packaged resource name (e.g. ``frontend``,
    ``config/voice.example.yaml``) to a real path, preferring the installed
    package data, then the checkout. Never raises: returns the best candidate
    even if it does not exist (callers decide how to degrade)."""
    name = name.lstrip("/")
    pkg = _PACKAGE_DATA / name
    if pkg.exists():
        return pkg
    return _REPO_ROOT / name


def resource_dir(name: str) -> Path:
    """Like :func:`resource_path` but for a directory of resources."""
    return resource_path(name)


def package_data_root() -> Path:
    """The installed ``_data`` root (may not exist in a checkout)."""
    return _PACKAGE_DATA