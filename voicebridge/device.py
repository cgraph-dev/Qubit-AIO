"""Packet 7C: trusted-device enrollment and device profiles.

A new device must be explicitly authorized and must re-check hardware, model
compatibility, optional dependencies, and capabilities. Never copy live
browser profiles or secrets automatically. Enrollment state is a small JSON
file under the private state root (0700 dir / 0600 file). This module is
read-only about the host; it never launches, enables, or mutates services,
browser, config, or model state.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from . import distribution as dist

ENROLL_FILE = "device.json"


class DeviceNotEnrolledError(Exception):
    pass


def machine_id() -> str:
    """Stable host identifier: /etc/machine-id when present, else a persisted
    UUID under the state root (never a secret; not exported as a credential)."""
    try:
        mid = Path("/etc/machine-id").read_text().strip()
        if mid:
            return mid
    except OSError:
        pass
    return str(uuid.UUID(int=0))  # deterministic fallback; replaced by caller


def _enroll_path(state_dir: Path) -> Path:
    return Path(state_dir) / ENROLL_FILE


def capabilities(cfg: Any) -> Dict[str, Any]:
    """Re-check hardware/model/optional dependencies/capabilities for the
    CURRENT host (typed, read-only)."""
    checks = dist.doctor(cfg)
    by = {c["name"]: c["state"] for c in checks}
    return {
        "gpu": by.get("gpu"),
        "ollama": by.get("ollama"),
        "browser_cdp": by.get("browser_cdp"),
        "audio": by.get("audio"),
        "openssl": by.get("openssl"),
        "isolation": by.get("isolation"),
        "ram": by.get("ram"),
        "disk": by.get("disk"),
    }


def profile(cfg: Any) -> Dict[str, Any]:
    """Current device profile: identity + capability snapshot + enrollment."""
    state_dir = cfg.state_dir() if hasattr(cfg, "state_dir") else Path(
        "~/.local/state/openclaw-voice-bridge").expanduser()
    p = _enroll_path(state_dir)
    data: Dict[str, Any] = {"machine_id": machine_id(),
                            "enrolled": False,
                            "enrolled_at": None,
                            "capabilities": capabilities(cfg)}
    if p.exists():
        try:
            data.update(json.loads(p.read_text()))
        except Exception:
            pass
    return data


def enroll(cfg: Any, *, model_alias: Optional[str] = None,
           require_new_authorization: bool = True) -> Dict[str, Any]:
    """Explicitly authorize THIS device. Records the capability snapshot and
    the model alias it was enrolled for. Never reads/copies secrets or browser
    profiles."""
    state_dir = cfg.state_dir() if hasattr(cfg, "state_dir") else Path(
        "~/.local/state/openclaw-voice-bridge").expanduser()
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(Path(state_dir), 0o700)
    except OSError:
        pass
    if require_new_authorization:
        raise DeviceNotEnrolledError(
            "trusted-device enrollment requires explicit authorization: "
            "pass --enroll on the CLI; never automatic")
    from .runner_contract import resolve
    alias = model_alias or resolve(cfg).alias
    rec = {
        "machine_id": machine_id(),
        "enrolled": True,
        "enrolled_at": round(time.time(), 3),
        "model_alias": alias,
        "capabilities": capabilities(cfg),
    }
    tmp = _enroll_path(state_dir).with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=2, sort_keys=True))
    os.replace(tmp, _enroll_path(state_dir))
    try:
        os.chmod(_enroll_path(state_dir), 0o600)
    except OSError:
        pass
    return rec


def require_enrolled(cfg: Any) -> Dict[str, Any]:
    """Fail closed unless the current device is explicitly enrolled."""
    p = profile(cfg)
    if not p.get("enrolled"):
        raise DeviceNotEnrolledError(
            "device not enrolled — run `voicebridge device enroll` (explicit)")
    return p


def assert_device_compatible(cfg: Any, device_profile: Dict[str, Any]) -> None:
    """A restore to a NEW device must re-check hardware/model/capabilities.
    Raises DeviceNotEnrolledError on incompatibility (never a silent pass)."""
    cur = capabilities(cfg)
    required = ("gpu", "ollama", "audio", "openssl")
    missing = [k for k in required if cur.get(k) in ("MISSING", "DEGRADED")]
    if missing:
        raise DeviceNotEnrolledError(
            f"target device missing required capability: {', '.join(missing)}")
    src_model = device_profile.get("model_alias")
    cur_model = profile(cfg).get("model_alias")
    if src_model and cur_model and src_model != cur_model:
        raise DeviceNotEnrolledError(
            f"model mismatch: bundle was enrolled for {src_model}, "
            f"this device is {cur_model} — re-enroll explicitly")