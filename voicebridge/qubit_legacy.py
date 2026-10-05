"""Narrow legacy-name migration adapter (owner amendment 2026-09-19).

The deep source rebrand renamed the shipped runtime's environment namespace
(``HERMES_HOME`` -> ``QUBIT_HOME``), package directory
(``qubit_hermes`` -> ``qubit_runtime``) and Qubit-owned modules. This module is
the ONE place that still knows the old names. It exists so an existing install
can be migrated without the legacy name becoming the active runtime identity.

Rules:

* ``QUBIT_*`` is the documented runtime namespace. The adapter mirrors it to
  the legacy ``HERMES_*`` name the vendored runtime still reads internally, so
  a caller that sets ``QUBIT_*`` works without a rename of the whole module
  graph;
* the reverse mirror (``HERMES_*`` -> ``QUBIT_*``) lets an existing install's
  old config migrate, but only when the Qubit name is unset -- a value is never
  overwritten and the legacy name is never advertised;
* the external upstream home (``~/.hermes``) is never adopted as the active
  ``QUBIT_HOME``; it stays a read-only migration source (the fail-closed
  isolation guard in :mod:`voicebridge.qubit_runtime` enforces this);
* databases, API/CLI shapes and wire schemas are unchanged, so no data
  migration is needed for compatibility beyond the home pointer.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, Optional

#: Legacy environment names -> canonical Qubit names. Kept exhaustive and
#: small on purpose; only names the rebrand actually renamed may appear here.
LEGACY_ENV_MAP = {
    "HERMES_HOME": "QUBIT_HOME",
}

#: The generic namespace prefixes the adapter mirrors. Only whole env names
#: matching these (``QUBIT_<SUFFIX>`` / ``HERMES_<SUFFIX>``) are considered.
_QUBIT_PREFIX = "QUBIT_"
_LEGACY_PREFIX = "HERMES_"
_ENV_NAME = re.compile(r"^(QUBIT|HERMES)_[A-Z0-9_]+$")
#: Names the adapter must never mirror (handled explicitly, or wire-preserved).
_MIRROR_EXCLUDE = frozenset({"HERMES_HOME", "QUBIT_HOME"})

#: The platform default upstream home. It is a migration source only; adopting
#: it would re-introduce the external-install split authority the product
#: forbids, so it is never copied into the Qubit namespace.
_EXTERNAL_DEFAULT_HOME = Path.home() / ".hermes"
_INSTALLED = False
_ADOPTED: Dict[str, str] = {}


def _is_external_default(value: str) -> bool:
    try:
        return Path(value).expanduser().resolve(strict=False) == \
            _EXTERNAL_DEFAULT_HOME.resolve(strict=False)
    except OSError:
        return False


def _mirror(env: Dict[str, str], adopted: Dict[str, str]) -> None:
    """Mirror ``QUBIT_*`` <-> ``HERMES_*`` for unset counterparts only."""
    for key in list(env):
        if key in _MIRROR_EXCLUDE or not _ENV_NAME.match(key):
            continue
        value = (env.get(key) or "").strip()
        if not value:
            continue
        if key.startswith(_QUBIT_PREFIX):
            counterpart = _LEGACY_PREFIX + key[len(_QUBIT_PREFIX):]
        else:
            counterpart = _QUBIT_PREFIX + key[len(_LEGACY_PREFIX):]
        if counterpart in env and (env.get(counterpart) or "").strip():
            continue
        # Never let the adapter surface the external upstream home under either
        # name; the home is handled by the explicit map below.
        if counterpart in _MIRROR_EXCLUDE and _is_external_default(value):
            continue
        env[counterpart] = value
        adopted[key] = counterpart


def install(environ: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Adopt/mirror env names for this process only (idempotent).

    Returns the mapping of source name -> counterpart name that was adopted.
    The caller keeps legacy values out of help, docs and persisted identity.
    """
    global _INSTALLED
    env = os.environ if environ is None else environ
    adopted: Dict[str, str] = {}
    # 1. Explicit home migration (legacy HERMES_HOME -> QUBIT_HOME), refusing
    #    the external upstream home.
    for legacy, canonical in LEGACY_ENV_MAP.items():
        legacy_value = (env.get(legacy) or "").strip()
        if not legacy_value:
            continue
        if (env.get(canonical) or "").strip():
            continue
        if _is_external_default(legacy_value):
            continue
        env[canonical] = legacy_value
        adopted[legacy] = canonical
    # 2. Namespace mirror so QUBIT_* is an accepted runtime name and legacy
    #    HERMES_* config migrates. Never overwrites an existing value.
    _mirror(env, adopted)
    if environ is None:
        _ADOPTED.update(adopted)
        _INSTALLED = True
    return adopted


def adopted() -> Dict[str, str]:
    """Return the process-wide source->counterpart adoptions (redacted)."""
    return dict(_ADOPTED)


__all__ = ["LEGACY_ENV_MAP", "install", "adopted"]
