"""Packet 8R: supervised OpenCode coding workflow adapter (optional).

See voicebridge.opencode (__init__) for the contract.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from .policy import DEFAULT_BINARY

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


def _resolve_binary(ocfg: Dict[str, Any]) -> str:
    raw = str(ocfg.get("binary") or DEFAULT_BINARY)
    return os.path.expanduser(raw)


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

