"""Per-agent character customization for the vendored Habblaud office.

The vendored editor calls ``PUT /api/agents/<id>/character`` with
``{name, seed, parts}`` and ``DELETE`` to return to the seed. This module is the
one durable store for those edits: a small JSON document next to the office
database, written atomically and read by the tower fold. It holds customization
metadata only (name/seed/parts) -- never art.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from . import store

SCHEMA = "qubit-office-characters/1"
FILENAME = "characters.json"
MAX_ENTRIES = 512
NAME_MAX = 24

_LOCK = threading.RLock()
_CONTROL = re.compile(r"[\x00-\x1f\x7f\u2028\u2029\u200e\u200f\u202a-\u202e\u2066-\u2069]")
_COLOR = re.compile(r"^#[0-9a-f]{6}$", re.I)
_STYLES: Dict[str, set[str]] = {
    "hairStyle": {"short", "buzz", "spiky", "side_part", "curly", "afro", "bob",
                  "long", "ponytail", "bun", "pigtails", "mohawk", "bald", "wavy"},
    "topStyle": {"tshirt", "hoodie", "shirt_tie", "sweater", "jacket", "blouse", "polo"},
    "bottomStyle": {"pants", "shorts", "skirt"},
    "accessory": {"none", "glasses", "sunglasses", "headphones", "cap", "beanie",
                  "earrings", "bow"},
    "facialHair": {"none", "stubble", "beard", "mustache", "goatee"},
}
_COLORS = {"skin", "hair", "eyes", "top", "topAccent", "bottom", "shoes", "accessoryColor"}
_PART_KEYS = {
    "skin", "hair", "hairStyle", "eyes", "top", "topAccent", "topStyle",
    "bottom", "bottomStyle", "shoes", "accessory", "accessoryColor", "facialHair",
}


class CharacterError(ValueError):
    code = "OFFICE_CHARACTER_INVALID"
    status = 400

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


def path(cfg: Any) -> Path:
    return store.db_path(cfg).parent / FILENAME


def _read(cfg: Any) -> Dict[str, Dict[str, Any]]:
    p = path(cfg)
    if not p.is_file():
        return {}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        return {}
    rows = doc.get("characters")
    return dict(rows) if isinstance(rows, dict) else {}


def _write(cfg: Any, rows: Mapping[str, Mapping[str, Any]]) -> None:
    p = path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p.parent, 0o700)
    except OSError:
        pass
    doc = {"schema": SCHEMA, "characters": dict(rows)}
    data = (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=".characters-", dir=str(p.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def _name(raw: Any) -> str:
    if not isinstance(raw, str):
        raise CharacterError("name must be a string")
    name = " ".join(raw.split()).strip()
    if not name:
        raise CharacterError("name is required")
    if _CONTROL.search(name):
        raise CharacterError("name contains control characters")
    if len(name) > NAME_MAX:
        raise CharacterError(f"name is longer than {NAME_MAX} characters")
    return name


def _seed(raw: Any) -> int:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise CharacterError("seed must be an integer")
    if isinstance(raw, float) and not raw.is_integer():
        raise CharacterError("seed must be an integer")
    value = int(raw)
    if value < 0 or value > 0xFFFFFFFF:
        raise CharacterError("seed must be an unsigned 32-bit integer")
    return value


def _parts(raw: Any) -> Dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise CharacterError("parts must be an object")
    if len(raw) > len(_PART_KEYS):
        raise CharacterError("parts has too many keys")
    out: Dict[str, str] = {}
    for key, value in raw.items():
        if key not in _PART_KEYS:
            raise CharacterError(f"unknown part {key!r}")
        if not isinstance(value, str) or len(value) > 32:
            raise CharacterError(f"part {key!r} must be a short string")
        if key in _STYLES:
            if value not in _STYLES[key]:
                raise CharacterError(f"part {key!r} has an unknown style")
        elif key in _COLORS:
            if not _COLOR.fullmatch(value):
                raise CharacterError(f"part {key!r} must be #rrggbb")
        out[key] = value.lower() if key in _COLORS else value
    return out


def load(cfg: Any) -> Dict[str, Dict[str, Any]]:
    """Every valid saved character, keyed by agent id."""
    return _read(cfg)


def save(cfg: Any, agent_id: str, body: Mapping[str, Any]) -> Dict[str, Any]:
    aid = str(agent_id or "").strip()
    if not aid or not aid.isdigit() or len(aid) > 32:
        raise CharacterError("agent id must be numeric")
    if not isinstance(body, Mapping):
        raise CharacterError("body must be an object")
    row = {
        "name": _name(body.get("name")),
        "seed": _seed(body.get("seed")),
        "parts": _parts(body.get("parts")),
    }
    with _LOCK:
        rows = _read(cfg)
        if aid not in rows and len(rows) >= MAX_ENTRIES:
            raise CharacterError("character store is full")
        rows[aid] = row
        _write(cfg, rows)
    return row


def reset(cfg: Any, agent_id: str) -> bool:
    aid = str(agent_id or "").strip()
    if not aid or not aid.isdigit() or len(aid) > 32:
        raise CharacterError("agent id must be numeric")
    with _LOCK:
        rows = _read(cfg)
        if aid not in rows:
            return False
        rows.pop(aid, None)
        _write(cfg, rows)
    return True
