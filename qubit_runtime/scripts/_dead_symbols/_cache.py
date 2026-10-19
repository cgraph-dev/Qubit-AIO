"""Per-file AST fact cache: makes a re-run seconds instead of a quarter hour.

WHY
---
The fact pass is ~0.85s per 1,000 AST nodes on this host (measured: a 2,841-node
file costs 11ms through `parse_tree`+`FileFacts.visit`, ~4.6ms for `ast.parse` +
`ast.walk` alone), so a full sweep of ~9,100 owned + ~9,500 upstream files takes
about six and a half minutes. The *answer* is deterministic -- same source bytes
plus the same detector version always yield the same facts -- so re-running the
gate must not pay that cost again. The cache keys on the content hash and the
`_facts` implementation, and lives under the repo's gitignored scratch dir.

CORRECTNESS RULES (a cache that can lie is worse than no cache)
--------------------------------------------------------------
  * Key = sha256(source bytes) + DIGEST (a hash of `_facts.py` + `_consts.py`).
    Editing the classifier changes DIGEST, so stale entries can never be served;
    that is checked, not assumed.
  * Concurrent writers are safe: entries are written to a unique temp file and
    `os.replace`d into place, so a reader sees the old or the new file, never a
    half-written one (two agents share this checkout).
  * A corrupt/unreadable entry is treated as a MISS and recomputed, never as a
    fact with missing fields.
  * `--no-cache` bypasses and refreshes; `--rebuild-cache` ignores existing
    entries. The value is always recomputed identically either way, so a cached
    run and a cold run must print the same numbers.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent


def _digest() -> str:
    h = hashlib.sha256()
    for name in ("_facts.py", "_consts.py"):
        try:
            h.update((_PKG_DIR / name).read_bytes())
        except OSError:
            h.update(name.encode())
    return h.hexdigest()[:16]


DIGEST = _digest()


def default_dir(root: Path | str | None) -> Path:
    """Where the cache lives: the repo's scratch dir when there is one."""
    if root is not None:
        base = Path(root).resolve()
        if (base / ".git").exists() or (base / "qubit_runtime").is_dir():
            return base / ".dead_symbols_cache"
    return Path(tempfile.gettempdir()) / "dead_symbols_cache"


class FactCache:
    """Content-addressed store for one file's fact payload.

    `payload` is an opaque JSON-able dict produced by the caller; this class only
    guarantees it is keyed by content correctly.
    """

    def __init__(self, directory: Path, enabled: bool = True,
                 read_only: bool = False) -> None:
        self.dir = Path(directory)
        self.enabled = enabled
        self.read_only = read_only
        self.hits = 0
        self.misses = 0
        if enabled and not read_only:
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                self.enabled = False

    def _path(self, src: bytes) -> Path:
        key = hashlib.sha256(src).hexdigest()
        return self.dir / DIGEST / key[:2] / (key[2:] + ".json")

    def get(self, src: bytes) -> dict | None:
        if not self.enabled:
            return None
        path = self._path(src)
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            self.misses += 1
            return None
        self.hits += 1
        return data

    def put(self, src: bytes, payload: dict) -> None:
        if not self.enabled or self.read_only:
            return
        path = self._path(src)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
            with os.fdopen(fd, "w") as fh:
                json.dump(payload, fh, sort_keys=True)
            os.replace(tmp, path)
        except OSError:
            pass
