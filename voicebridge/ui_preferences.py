"""Profile-local UI preferences for the Qubit compat route.

The Hermes reference serves ``/api/ui-preferences`` from a machine-level
store. Qubit keeps this store inside the active profile's state directory so
one profile can never read or write another profile's preferences. The wire
document stays compatible: ``{"exists": bool, "revision": int,
"values": {key: string}}``.

Values are opaque strings. The store never reads config or environment
secrets, and writes reject secret-shaped keys and private key material.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

STORE_NAME = "ui-preferences.json"
MAX_KEYS = 256
MAX_KEY_CHARS = 128
MAX_VALUE_CHARS = 64 * 1024
MAX_TOTAL_CHARS = 1024 * 1024

_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SECRET_KEY_RE = re.compile(
    r"(?:^|[._:-])"
    r"(?:api[_-]?key|auth|bearer|credentials?|passwd|password|"
    r"private[_-]?key|secret|token)"
    r"(?:$|[._:-])",
    re.IGNORECASE,
)
_LOCK = threading.RLock()


class PreferencesError(ValueError):
    """Base class for refused preference operations."""


class PreferencesInvalid(PreferencesError):
    """The request was malformed or outside the bounded contract."""


class PreferencesUnavailable(PreferencesError):
    """The profile's preference store could not be read or written."""


def store_path(cfg: Any) -> Path:
    """The store belongs to the active profile, never the machine root."""
    return Path(cfg.state_dir()) / STORE_NAME


def _read(path: Path) -> Tuple[dict, bool, bool, int, str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, False, False, 0, ""
    except UnicodeDecodeError:
        return {}, True, True, 0, ""
    except OSError as exc:
        raise PreferencesUnavailable(
            f"preferences store is unreadable: {exc}") from exc
    try:
        doc = json.loads(raw)
    except ValueError:
        return {}, True, True, 0, ""
    if not isinstance(doc, dict):
        return {}, True, True, 0, ""
    values = doc.get("values")
    clean = ({k: v for k, v in values.items()
              if isinstance(k, str) and isinstance(v, str)}
             if isinstance(values, dict) else {})
    revision = doc.get("revision")
    generation = doc.get("generation")
    return (clean, True, False,
            revision if isinstance(revision, int) and revision >= 0 else 0,
            generation if isinstance(generation, str) else "")


def _validate_key(key: Any) -> None:
    if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
        raise PreferencesInvalid(f"unsupported preference key: {key!r}")
    if _SECRET_KEY_RE.search(key):
        raise PreferencesInvalid(
            f"secret-like preference keys are not accepted: {key!r}")


def _validate_value(value: Any) -> None:
    if not isinstance(value, str):
        raise PreferencesInvalid("preference values must be strings or null")
    if len(value) > MAX_VALUE_CHARS:
        raise PreferencesInvalid("preference value is too large")
    if "\x00" in value:
        raise PreferencesInvalid("preference values may not contain NUL")
    if "-----BEGIN " in value and "PRIVATE KEY-----" in value:
        raise PreferencesInvalid("private key material is not accepted")
    if value.startswith("Bearer ") and len(value) > len("Bearer ") + 20:
        raise PreferencesInvalid("bearer tokens are not accepted")


def _keep_aside(path: Path) -> None:
    try:
        path.replace(path.with_name(f"{path.name}.corrupt-{int(time.time() * 1000)}"))
    except OSError:
        pass


def _atomic_write(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(doc, indent=2, sort_keys=True))
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read(cfg: Any) -> dict:
    path = store_path(cfg)
    with _LOCK:
        values, exists, corrupt, revision, generation = _read(path)
    if corrupt:
        return {"exists": False, "revision": 0, "values": {}}
    out = {"exists": exists, "revision": revision, "values": values}
    if generation:
        out["generation"] = generation
    return out


def write(cfg: Any, changes: Mapping[str, Optional[str]],
          seed: bool = False) -> dict:
    if not isinstance(changes, dict):
        raise PreferencesInvalid("values must be an object")
    path = store_path(cfg)
    with _LOCK:
        current, exists, corrupt, revision, generation = _read(path)
        values = dict(current)
        for key, value in changes.items():
            _validate_key(key)
            if value is None:
                if not seed:
                    values.pop(key, None)
                continue
            _validate_value(value)
            if seed:
                values.setdefault(key, value)
            else:
                values[key] = value
        if len(values) > MAX_KEYS:
            raise PreferencesInvalid("preferences store holds too many keys")
        if sum(len(k) + len(v) for k, v in values.items()) > MAX_TOTAL_CHARS:
            raise PreferencesInvalid("preferences store is too large")
        if values == current and (exists or not values):
            out = {"exists": exists, "revision": revision, "values": values}
            if generation:
                out["generation"] = generation
            return out
        if corrupt:
            _keep_aside(path)
        new_generation = generation if (exists and not corrupt and generation) \
            else secrets.token_hex(8)
        doc = {"version": 1, "generation": new_generation,
               "revision": revision + 1, "values": values}
        try:
            _atomic_write(path, doc)
        except OSError as exc:
            raise PreferencesUnavailable(
                f"preferences store is unwritable: {exc}") from exc
        return {"exists": True, "revision": doc["revision"], "values": values,
                "generation": new_generation}
