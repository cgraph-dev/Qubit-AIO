"""Packet 8R: supervised OpenCode coding workflow adapter (optional).

See voicebridge.opencode (__init__) for the contract.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from .policy import DEFAULT_BINARY, PINNED_OPENCODE_VERSION


def _xdg_data_home() -> Path:
    """Canonical Qubit XDG data base (same rule as the Hermes provision:
    snap-confinement values must not fragment the runtime tree)."""
    try:
        from ..config import _xdg_base as _canonical_base
        return _canonical_base("XDG_DATA_HOME", ".local/share")
    except Exception:
        raw = os.environ.get("XDG_DATA_HOME", "").strip()
        return Path(raw).expanduser() if raw else Path(
            "~/.local/share").expanduser()


def owned_binary_dir(version: str = PINNED_OPENCODE_VERSION) -> Path:
    """Qubit-owned isolated home for the pinned OpenCode executable.

    Never /tmp, never ~, never the operator's install: the exact pinned
    binary is materialized here by `qubit runtime install opencode-binary`
    after --version verification, and this path is what Qubit executes."""
    return (_xdg_data_home() / "qubit-voicebridge" / "runtimes"
            / f"opencode-{version}")


def owned_binary(version: str = PINNED_OPENCODE_VERSION) -> Path:
    """Full path of the Qubit-owned pinned OpenCode executable."""
    return owned_binary_dir(version) / "bin" / "opencode"


def owned_receipt_path(version: str = PINNED_OPENCODE_VERSION) -> Path:
    """Digest receipt written at materialization (install-time proof)."""
    return owned_binary_dir(version) / "PIN.json"

def _adapter_path(cfg) -> Path:
    if cfg is not None:
        try:
            return Path(cfg.state_dir()) / "opencode_adapter.json"
        except Exception:
            pass
    return Path("/tmp/vb-opencode-adapter.json")


def _read_state(cfg) -> Dict[str, Any]:
    try:
        raw = _adapter_path(cfg).read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"user_enabled": False, "emergency_stop": False}


def _write_state(cfg, data: Dict[str, Any]) -> None:
    path = _adapter_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True),
                   encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def verify_owned_binary(version: str = PINNED_OPENCODE_VERSION) -> Dict[str, Any]:
    """Verify the Qubit-owned pinned binary without executing it.

    Returns ok=True only when the file exists, is executable, and its
    sha256 matches the install-time receipt (written after --version
    verification). No spawn: version identity is by construction, and the
    execution path re-probes --version anyway and fails closed."""
    import hashlib as _hashlib
    import json as _json

    binary = owned_binary(version)
    receipt = owned_receipt_path(version)
    if not binary.is_file() or not os.access(binary, os.X_OK):
        return {"ok": False, "reason": "MISSING", "binary": str(binary)}
    try:
        data = _json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "reason": "NO_RECEIPT", "binary": str(binary)}
    digest = _hashlib.sha256(binary.read_bytes()).hexdigest()
    if not isinstance(data, dict) or data.get("sha256") != digest:
        return {"ok": False, "reason": "DIGEST_MISMATCH",
                "binary": str(binary)}
    if data.get("version") != version:
        return {"ok": False, "reason": "VERSION_MISMATCH",
                "binary": str(binary)}
    return {"ok": True, "reason": "OK", "binary": str(binary),
            "sha256": digest, "version": version}


def _resolve_binary(ocfg: Dict[str, Any]) -> str:
    raw = str(ocfg.get("binary") or "").strip()
    default_path = os.path.expanduser(DEFAULT_BINARY)
    if raw and os.path.expanduser(raw) != default_path:
        # An explicit operator binary always wins (reported honestly by
        # status(); execution still fails closed on version mismatch).
        return os.path.expanduser(raw)
    # Default or empty: prefer the Qubit-owned verified pinned copy.
    # Anything else (operator install, PATH) is reported honestly by
    # status() and refused at execution on version mismatch.
    owned = verify_owned_binary(
        str(ocfg.get("pinned_version") or PINNED_OPENCODE_VERSION))
    if owned["ok"]:
        return str(owned["binary"])
    return default_path


def _opencode_cfg(cfg) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if cfg is None:
        return out
    for key in ("enabled", "binary", "pinned_version", "timeout_s",
                "max_timeout_s"):
        try:
            out[key] = cfg.get("intent", "opencode", key, default=None)
        except Exception:
            out[key] = None
    return out


# ---------------------------------------------------------------------------
# Runner (same broker/gate/receipt machinery as every other channel)
# ---------------------------------------------------------------------------

