"""Bounded, invalidatable in-process cache for expensive read-only payloads.

The dashboard polls several inventory reads (runtime status, skills, MCP,
model options, terminal profiles, the office tower) that each re-walk files or
probe a subprocess on every request. Those reads are expensive but change
slowly, so a short-lived snapshot is enough to answer a poll without doing the
work twice.

Rules enforced here:

* **Read-only only.** Callers cache a computed payload, never a mutation.
* **Never across homes/profiles.** :func:`cfg_key` always folds in the
  resolved runtime home, the config's state dir and the config file's
  identity, so two profiles can never share an entry.
* **Invalidatable.** A mutation path calls :func:`invalidate` with its
  namespace; TTL and the caller-supplied mtime stamps cover the cross-process
  case (another CLI/server writing the same files).
* **Bounded.** A long-lived dashboard process keeps at most ``MAX_ENTRIES``
  entries; expired entries are dropped first, then the oldest.

``QUBIT_READ_CACHE=0`` (or ``off``/``false``/``no``) disables the cache
entirely, and ``QUBIT_READ_CACHE_TTL_<NAMESPACE>`` overrides one namespace's
TTL (seconds, ``0`` disables just that namespace). Tests use these to pin the
enabled/disabled behaviour.
"""
from __future__ import annotations

import copy
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

MAX_ENTRIES = 128

_LOCK = threading.RLock()
_CACHE: Dict[Any, Tuple[float, Any]] = {}
_STATS: Dict[str, int] = {"hits": 0, "misses": 0, "stores": 0, "invalidations": 0}

_FALSE = {"0", "off", "false", "no", "disabled"}


def enabled() -> bool:
    raw = os.environ.get("QUBIT_READ_CACHE")
    if raw is not None:
        return str(raw).strip().lower() not in _FALSE
    # Under pytest the ambient process is not a long-lived dashboard: every
    # read is one-shot, and a retained snapshot would perturb the strict
    # ResourceWarning/unraisable checks many suites rely on. Focused cache
    # tests opt in explicitly with ``QUBIT_READ_CACHE=1``.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return True


def ttl_seconds(namespace: str, default: float) -> float:
    """TTL for ``namespace``: the caller's default, env-overridable.

    ``QUBIT_READ_CACHE_TTL_<NAMESPACE>=0`` disables just this namespace.
    A cache hit always wins; a TTL of 0 means every call recomputes.
    """
    raw = os.environ.get(f"QUBIT_READ_CACHE_TTL_{namespace.upper()}")
    if raw is None:
        return max(0.0, float(default))
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return max(0.0, float(default))


def file_stamp(path: Any) -> Tuple[int, int]:
    """``(mtime_ns, size)`` for a path; ``(0, 0)`` when it cannot be read."""
    try:
        st = Path(str(path)).stat()
        return (int(st.st_mtime_ns), int(st.st_size))
    except (OSError, ValueError, TypeError):
        return (0, 0)


def stamp_of(*paths: Any) -> Tuple[Tuple[int, int], ...]:
    return tuple(file_stamp(p) for p in paths if p)


def cfg_key(cfg: Any, namespace: str, *parts: Any) -> Any:
    """Cache identity for one config: home + state dir + config file identity.

    Every caller that caches per profile MUST build its key through this (or
    include all of these parts itself) so an entry can never leak between
    homes or profiles.
    """
    state_dir = ""
    try:
        state_dir = str(cfg.state_dir())
    except Exception:  # noqa: BLE001 - a config-less read still needs a key
        pass
    cfg_path = str(getattr(cfg, "path", "") or "")
    home = ""
    try:
        from . import qubit_runtime as QR

        home = str(QR.runtime_home_for_cfg(cfg))
    except Exception:  # noqa: BLE001 - fall back to the state dir identity
        pass
    return (namespace, home, state_dir, cfg_path,
            file_stamp(cfg_path) if cfg_path else (0, 0), *parts)


def get(key: Any) -> Optional[Any]:
    """Return a deep copy of a live entry, or ``None`` on miss/expiry."""
    if not enabled():
        return None
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit is None:
            _STATS["misses"] += 1
            return None
        expires, value = hit
        if expires <= now:
            _CACHE.pop(key, None)
            _STATS["misses"] += 1
            return None
        _STATS["hits"] += 1
    return copy.deepcopy(value)


def put(key: Any, value: Any, ttl: float) -> None:
    """Store a deep copy of ``value`` for ``ttl`` seconds (0 stores nothing)."""
    if not enabled() or ttl <= 0:
        return
    now = time.monotonic()
    with _LOCK:
        if len(_CACHE) >= MAX_ENTRIES:
            expired = [k for k, (exp, _) in _CACHE.items() if exp <= now]
            for stale in expired:
                _CACHE.pop(stale, None)
            while len(_CACHE) >= MAX_ENTRIES:
                oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
                _CACHE.pop(oldest, None)
        _CACHE[key] = (now + ttl, copy.deepcopy(value))
        _STATS["stores"] += 1


def get_or_compute(key: Any, compute: Callable[[], Any], ttl: float) -> Any:
    hit = get(key)
    if hit is not None:
        return hit
    value = compute()
    put(key, value, ttl)
    return value


def invalidate(namespace: Optional[str] = None) -> int:
    """Drop every entry for ``namespace`` (all entries when ``None``)."""
    with _LOCK:
        if namespace is None:
            removed = len(_CACHE)
            _CACHE.clear()
        else:
            doomed = [k for k in _CACHE
                      if isinstance(k, tuple) and k and k[0] == namespace]
            for key in doomed:
                _CACHE.pop(key, None)
            removed = len(doomed)
        _STATS["invalidations"] += removed
    return removed


def stats() -> Dict[str, int]:
    with _LOCK:
        return dict(_STATS)


def reset_for_tests() -> None:
    """Clear the cache and its counters (focused tests only)."""
    with _LOCK:
        _CACHE.clear()
        for key in _STATS:
            _STATS[key] = 0
