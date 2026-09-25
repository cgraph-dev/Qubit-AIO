# scripts/merge_v0215/merge.py
"""3-way merge driver for the v0.21.5 1:1 rebase (Task 5).

Ruling R8 fixes the merge base: ``base = engine(v0.21.4)``, ``ours =
engine(v0.21.5)`` (the candidate), ``theirs = current Qubit tree``.  A path is
resolved by :func:`merge_file`:

* present only in ``ours``  -> the candidate bytes (new upstream file);
* present only in ``theirs`` -> the current bytes (fork-only file is kept);
* present in both and byte-identical -> that content;
* present in both but different -> run ``git merge-file -p ours base theirs``
  (the theirs-vs-base change applied onto ours); a conflict raises
  :class:`MergeConflict` with the conflicted merge for review.

The helper is pure — it reads and returns bytes and never writes a product
tree.  The bucket loop that consumes it lives in the Task 6 driver; this module
exposes only the resolution primitive plus the bucket -> action rules.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional, Union

PathLike = Union[str, Path]


class MergeConflict(RuntimeError):
    """A genuine 3-way conflict; ``merged`` holds the conflicted merge bytes."""

    def __init__(self, path: str, merged: bytes, returncode: int) -> None:
        super().__init__(f"3-way conflict in {path!r} (git merge-file rc={returncode})")
        self.path = path
        self.merged = merged
        self.returncode = returncode


def _read(path: Path) -> Optional[bytes]:
    if not path.is_file():
        return None
    return path.read_bytes()


def _safe_join(root: Path, rel: str) -> Path:
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts:
        raise ValueError(f"refusing non-relative merge path: {rel!r}")
    return root / p


def merge_file(
    base: PathLike,
    ours: PathLike,
    theirs: PathLike,
    path: str,
) -> bytes:
    """Resolve one relative *path* across the three merge trees.

    Returns the merged bytes.  Raises :class:`MergeConflict` on overlapping
    edits, :class:`ValueError` when the path is absent from both ours and
    theirs or when it escapes the tree root.
    """
    base_root, ours_root, theirs_root = Path(base), Path(ours), Path(theirs)

    ours_p = _safe_join(ours_root, path)
    theirs_p = _safe_join(theirs_root, path)
    base_p = _safe_join(base_root, path)

    ours_b = _read(ours_p)
    theirs_b = _read(theirs_p)
    base_b = _read(base_p)

    if ours_b is None and theirs_b is None:
        raise ValueError(f"path {path!r} is absent from both ours and theirs")
    if ours_b is None:
        return theirs_b  # only_current -> keep the fork file
    if theirs_b is None:
        return ours_b  # only_candidate -> take the new upstream file
    if ours_b == theirs_b:
        return ours_b  # identical -> no work

    # differing -> apply the theirs-vs-base change onto ours (R8).
    return _three_way(base_p, ours_p, theirs_p, path, base_b, ours_b, theirs_b)


def _three_way(
    base_p: Path,
    ours_p: Path,
    theirs_p: Path,
    path: str,
    base_b: bytes,
    ours_b: bytes,
    theirs_b: bytes,
) -> bytes:
    """Run ``git merge-file -p ours base theirs`` over the three blobs.

    When the base blob is absent the path was independently added on both
    sides; ``git merge-file`` then reports a conflict, which is the honest
    answer (there is no common ancestor to merge from).
    """
    # ``git merge-file`` needs all three files on disk to name in the conflict
    # markers.  Use temp files seeded from the trees (base may be absent,
    # which git tolerates as an empty side and then reports a conflict).
    import tempfile

    with tempfile.TemporaryDirectory(prefix="merge_v0215-") as tmp:
        tmp_p = Path(tmp)
        (tmp_p / "ours").write_bytes(ours_b)
        (tmp_p / "theirs").write_bytes(theirs_b)
        if base_b is not None:
            (tmp_p / "base").write_bytes(base_b)
        else:
            (tmp_p / "base").write_bytes(b"")
        res = subprocess.run(
            [
                "git", "merge-file", "-p",
                str(tmp_p / "ours"), str(tmp_p / "base"), str(tmp_p / "theirs"),
            ],
            capture_output=True,
        )
    merged = res.stdout
    if res.returncode > 0:
        raise MergeConflict(path, merged, res.returncode)
    if res.returncode < 0:
        raise RuntimeError(
            f"git merge-file failed for {path!r} rc={res.returncode}: "
            f"{res.stderr.decode('utf-8', 'replace').strip()}"
        )
    return merged
