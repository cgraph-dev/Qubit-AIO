"""Local authentication and media-root policy shared with the Node bridge."""

from __future__ import annotations

import os
import secrets
import tempfile
from pathlib import Path
from typing import Iterable, Optional

BRIDGE_TOKEN_HEADER = "X-Qubit-Bridge-Token"
BRIDGE_TOKEN_FILE = "bridge.token"


def bridge_token_path(session_dir: Path) -> Path:
    return Path(session_dir) / BRIDGE_TOKEN_FILE


def _write_private(path: Path, value: str) -> None:
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(directory))
    try:
        if hasattr(os, "fchmod"):
            try:
                os.fchmod(fd, 0o600)
            except OSError:
                pass
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def ensure_bridge_token(session_dir: Path) -> str:
    """Return the session's bridge token, creating it owner-only when absent."""
    try:
        path = bridge_token_path(session_dir)
    except (TypeError, ValueError):
        return ""
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        existing = ""
    if existing:
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return existing
    token = secrets.token_hex(32)
    _write_private(path, token + "\n")
    return token


def load_bridge_token(session_dir: Path) -> str:
    try:
        path = bridge_token_path(session_dir)
    except (TypeError, ValueError):
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return ""


def bridge_request_headers(session_dir: Path) -> dict[str, str]:
    token = load_bridge_token(session_dir)
    return {BRIDGE_TOKEN_HEADER: token} if token else {}


def bridge_media_roots() -> list[Path]:
    """Directories the bridge may read an outbound attachment from."""
    try:
        from gateway.platforms.base import _media_delivery_allowed_roots

        raw = list(_media_delivery_allowed_roots())
    except Exception:
        raw = []
    raw.append(Path(tempfile.gettempdir()))
    out: list[Path] = []
    for candidate in raw:
        try:
            resolved = Path(candidate).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if resolved not in out:
            out.append(resolved)
    return out


def is_media_path_allowed(file_path: str, roots: Optional[Iterable[Path]] = None) -> tuple[bool, str]:
    try:
        resolved = Path(file_path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return False, "invalid path"
    for root in roots if roots is not None else bridge_media_roots():
        try:
            resolved_root = Path(root).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if resolved == resolved_root or resolved.is_relative_to(resolved_root):
            return True, ""
    return False, "path is outside the bridge media roots"
