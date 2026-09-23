"""Canonical Qubit environment reference registry (PRB-02).

ONE store behind every environment/config-reference surface. The dashboard
``GET/PUT/DELETE /api/env`` handlers, the CLI ``qubit auth status`` projection,
and the voice ``auth_status`` intent all read and write this single JSON store
(``<state_dir>/qubit_env_refs.json``). A value must be a vault reference
(``vault:``/``ref:``); a raw secret is never accepted, so the store never holds
secret material itself.

This module deliberately has no dependency on :mod:`voicebridge.qubit_compat`
so :mod:`voicebridge.auth_status` can read it without an import cycle.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

STORE_NAME = "qubit_env_refs.json"
#: pre-rename name; read once when only it exists, never written again.
LEGACY_STORE_NAME = "hermes_env_refs.json"
STORE_SCHEMA = "qubit-env-refs/1"
REF_RE = re.compile(r"^(vault|ref):")
#: accepted reference prefixes (the only values the store will hold)
REF_KINDS: Tuple[str, ...] = ("vault", "ref")


def _base(cfg: Any) -> Path:
    try:
        return Path(cfg.state_dir())
    except Exception:  # noqa: BLE001 - a config-less caller gets a safe temp
        return Path("/tmp")


def store_path(cfg: Any) -> Path:
    """The canonical env-reference store path (the write target) for *cfg*."""
    return _base(cfg) / STORE_NAME


def read_path(cfg: Any) -> Path:
    """The read source: canonical when present, else the legacy-named store.

    Non-destructive migration: when only the pre-rename file exists it is read
    once so no reference is lost, and the next save writes the canonical name.
    """
    new = store_path(cfg)
    if new.exists():
        return new
    legacy = _base(cfg) / LEGACY_STORE_NAME
    return legacy if legacy.exists() else new


def load(cfg: Any) -> Dict[str, str]:
    """Read the registry (values are reference strings only)."""
    try:
        raw = json.loads(read_path(cfg).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def names(cfg: Any) -> List[str]:
    """Sorted reference names (never values)."""
    return sorted(load(cfg).keys())


def runtime_env_names(cfg: Any) -> set:
    """Names present in the canonical runtime ``.env`` (never the values).

    The Keys page's ``is_set`` must reflect whether a key is actually present
    in Qubit's own runtime environment, not only in the ``vault:``/``ref:``
    registry. This reads the canonical ``<state_dir>/runtime/.env`` once and
    returns the dotenv-style variable NAMES only; values are never read out,
    cached, or returned. Any read error yields the empty set (fail closed).
    """
    try:
        from . import config as _config
        path = Path(_config.canonical_runtime_env_path())
    except Exception:  # noqa: BLE001 - unresolvable home -> no names
        return set()
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return set()
    found: set = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, sep, _value = line.partition("=")
        key = key.strip()
        if sep and key:
            found.add(key)
    return found


def present_names(cfg: Any) -> set:
    """Union of reference names and runtime-``.env`` names (names only)."""
    return set(load(cfg).keys()) | runtime_env_names(cfg)


def is_present(cfg: Any, name: str) -> bool:
    """True when *name* is a registry reference or a runtime ``.env`` key."""
    return str(name or "").strip() in present_names(cfg)


def _save(cfg: Any, data: Dict[str, str]) -> None:
    path = store_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    if path.exists():
        try:
            path.with_suffix(path.suffix + ".bak").write_bytes(path.read_bytes())
        except OSError:
            pass
    os.replace(tmp, path)


def is_reference(value: str) -> bool:
    """True when *value* is a vault/ref reference, never a raw secret."""
    return bool(REF_RE.match(str(value or "")))


def set_ref(cfg: Any, name: str, ref: str) -> Dict[str, Any]:
    """Persist one reference. Raises ``ValueError`` for a raw secret."""
    clean = str(name or "").strip()
    if not clean:
        raise ValueError("env reference name required")
    value = str(ref or "")
    if not is_reference(value):
        raise ValueError(
            "env writes accept vault:/ref: references only, never secret values")
    data = load(cfg)
    data[clean] = value
    _save(cfg, data)
    return {"name": clean, "count": len(data)}


def remove_ref(cfg: Any, name: str) -> Dict[str, Any]:
    """Remove one reference; returns whether it existed."""
    clean = str(name or "").strip()
    if not clean:
        raise ValueError("env reference name required")
    data = load(cfg)
    existed = clean in data
    data.pop(clean, None)
    if existed:
        _save(cfg, data)
    return {"name": clean, "removed": existed, "count": len(data)}
