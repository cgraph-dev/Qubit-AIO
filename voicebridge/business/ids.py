"""Stable, sortable, tenant-scoped identifiers and request fingerprints.

IDs are ULID-shaped: a 48-bit millisecond timestamp followed by 80 random bits,
encoded in Crockford base32 and prefixed by resource kind (``biz_...``). They
are lexicographically sortable by creation time and need no coordination.

A *fingerprint* is the canonical JSON hash of a request body. It is stored
beside an idempotency key so replaying a key with a different request is a
typed conflict, never a silent second effect.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_PREFIX_RE = re.compile(r"^[a-z][a-z0-9]{1,11}$")


def new_id(prefix: str) -> str:
    """Return ``<prefix>_<26-char ULID>``.

    ``prefix`` is a short lowercase resource tag (``ten``, ``biz``, ``job``…).
    """
    if not _PREFIX_RE.match(prefix):
        raise ValueError(f"invalid id prefix: {prefix!r}")
    millis = int(time.time() * 1000)
    value = (millis << 80) | int.from_bytes(os.urandom(10), "big")
    chars: list[str] = []
    for _ in range(26):
        chars.append(_ALPHABET[value & 31])
        value >>= 5
    return f"{prefix}_{''.join(reversed(chars))}"


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
