"""Turn-time dead-model record (Lane 1 fix).

A model the registry lists but upstream rejects at TURN time (static probe
resolves, real turns fail ``ModelProtocolUnsupported`` / unavailable /
not-found) must never be (re-)persisted into the runtime ``config.yaml``
model slot or the ``terminal.profile`` pin: each activation re-stamps both
slots and every default turn then fails until a hand repair.

The record lives in ``<state_dir>/dead_models.json`` (same authority dir as
``profiles.json``), keyed ``provider/model`` (lowercased). Writers: the
canonical turn path (``providers/gateway.py``) on a
``MODEL_UNAVAILABLE_TURN`` result. Readers: ``_gate_remote_liveness``
(refuses activation, slot byte-unchanged) and ``_terminal_follow_main_model``
(keeps the pin). A later successful turn clears the record (auto-recovery
when upstream restores the model).

All functions never raise. ``is_dead`` fails OPEN (``None``) on any error
so a broken record can never block activation (today's behavior). Only
identity strings are stored — never prompts, keys, or bodies.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

FILE_NAME = "dead_models.json"
SCHEMA = "qubit-dead-models/1"


def _path(cfg: Any) -> Optional[Path]:
    try:
        state = cfg.state_dir() if hasattr(cfg, "state_dir") else None
    except Exception:
        return None
    if state is None:
        return None
    try:
        return Path(state) / FILE_NAME
    except Exception:
        return None


def _key(provider: str, model: str) -> str:
    return f"{str(provider or '').strip().lower()}/{str(model or '').strip().lower()}"


def _read(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def record_dead(cfg: Any, provider: str, model: str, reason: str) -> bool:
    """Record a turn-time-dead provider/model. Best-effort, never raises."""
    path = _path(cfg)
    key = _key(provider, model)
    if path is None or key == "/":
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = _read(path)
        models = data.get("models")
        if not isinstance(models, dict):
            models = {}
        models[key] = {
            "provider": str(provider or "").strip(),
            "model": str(model or "").strip(),
            "reason": str(reason or "")[:200],
            "recorded_at": time.time(),
        }
        data["schema"] = SCHEMA
        data["models"] = models
        tmp = path.with_name(path.name + ".qubit-tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True),
                       encoding="utf-8")
        tmp.replace(path)
        return True
    except Exception:
        return False


def is_dead(cfg: Any, provider: str, model: str) -> Optional[Dict[str, Any]]:
    """The dead record for provider/model, or ``None`` (fail-open)."""
    path = _path(cfg)
    key = _key(provider, model)
    if path is None or key == "/":
        return None
    try:
        models = _read(path).get("models")
        if not isinstance(models, dict):
            return None
        rec = models.get(key)
        return dict(rec) if isinstance(rec, dict) else None
    except Exception:
        return None


def clear_dead(cfg: Any, provider: str, model: str) -> bool:
    """Drop the dead record (recovery). Best-effort, never raises."""
    path = _path(cfg)
    key = _key(provider, model)
    if path is None or key == "/":
        return False
    try:
        data = _read(path)
        models = data.get("models")
        if not isinstance(models, dict) or key not in models:
            return key not in (models if isinstance(models, dict) else {})
        models.pop(key, None)
        data["models"] = models
        tmp = path.with_name(path.name + ".qubit-tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True),
                       encoding="utf-8")
        tmp.replace(path)
        return True
    except Exception:
        return False
