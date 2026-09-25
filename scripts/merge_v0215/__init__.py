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

from .checkpoint import Checkpoint, CheckpointError, DirtyWorktreeError
from .driver import (
    BUCKET_ORDER,
    RES_ADDED,
    RES_AUTO_OURS,
    RES_DELETED,
    RES_KEEP,
    RES_KEEP_CONFLICT,
    RES_MERGE_FILE,
    apply_record,
    bucket_of,
    build_audit,
    classify,
    iter_files,
    resolve,
    run_bucket,
    run_merge,
    write_audit,
)
from .freeze import PIN, build_freeze, dirty_count, write_freeze
from .merge import MergeConflict, merge_file
from .rules import BUCKET_ACTIONS, WRITE_ORDER, action_for_path

__all__ = [
    "BUCKET_ACTIONS",
    "BUCKET_ORDER",
    "WRITE_ORDER",
    "RES_ADDED",
    "RES_AUTO_OURS",
    "RES_DELETED",
    "RES_KEEP",
    "RES_KEEP_CONFLICT",
    "RES_MERGE_FILE",
    "Checkpoint",
    "CheckpointError",
    "DirtyWorktreeError",
    "MergeConflict",
    "PIN",
    "action_for_path",
    "apply_record",
    "bucket_of",
    "build_audit",
    "build_freeze",
    "classify",
    "dirty_count",
    "iter_files",
    "merge_file",
    "resolve",
    "run_bucket",
    "run_merge",
    "write_audit",
    "write_freeze",
]
