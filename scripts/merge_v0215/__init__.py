# scripts/merge_v0215/__init__.py
"""v0.21.5 1:1 rebase merge harness (Task 5).

Public interface consumed by Task 6's bucket merge::

    merge_file(base, ours, theirs, path) -> bytes
    Checkpoint(repo).checkpoint(name) / .rollback([name]) / .list()
    action_for_path(path, delta) -> Optional[str]

``base``/``ours``/``theirs`` are the engine(v0.21.4) / engine(v0.21.5) /
current-Qubit trees (ruling R8).  Nothing here writes a product tree; the
caller owns the destination and the live-tree guard.
"""
from __future__ import annotations

from .checkpoint import Checkpoint, CheckpointError
from .freeze import PIN, build_freeze, dirty_count, write_freeze
from .merge import MergeConflict, merge_file
from .rules import BUCKET_ACTIONS, WRITE_ORDER, action_for_path

__all__ = [
    "BUCKET_ACTIONS",
    "WRITE_ORDER",
    "Checkpoint",
    "CheckpointError",
    "MergeConflict",
    "PIN",
    "action_for_path",
    "build_freeze",
    "dirty_count",
    "merge_file",
    "write_freeze",
]
