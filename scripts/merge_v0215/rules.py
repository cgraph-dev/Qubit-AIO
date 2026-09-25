# scripts/merge_v0215/rules.py
"""Bucket -> merge-action rules for the v0.21.5 1:1 rebase (Task 5).

The delta manifest (``scripts/rebase_v0215.sh``) classifies every path in
``qubit_runtime/`` into four buckets.  Those buckets are the merge rules:

===============  ==============  ============================================
bucket           action          meaning for the merge
===============  ==============  ============================================
only_candidate   candidate       path exists only in engine(v0.21.5) -> take ours
only_current     current         path exists only in the fork -> take theirs
differing        merge           apply the theirs-vs-base change onto ours
identical        keep            byte-identical -> keep as-is
===============  ==============  ============================================

Only the first three require file work; ``identical`` files are skipped by the
merge driver.  This module is intentionally dependency-free so both the merge
driver and its tests import it cheaply.
"""
from __future__ import annotations

from typing import Mapping, Optional, Sequence

#: bucket name -> action name
BUCKET_ACTIONS: dict[str, str] = {
    "only_candidate": "candidate",
    "only_current": "current",
    "differing": "merge",
    "identical": "keep",
}

#: The buckets that produce a file write, in a deterministic order.
WRITE_ORDER: tuple[str, ...] = ("only_candidate", "only_current", "differing")


def action_for_path(path: str, delta: Mapping[str, Sequence[str]]) -> Optional[str]:
    """Return the merge action for *path* per a delta manifest, or ``None``.

    ``None`` means the path is not part of the merge surface (unlisted, or in
    the ``identical`` bucket where no work is needed).
    """
    for bucket in WRITE_ORDER:
        if path in delta.get(bucket, ()):
            return BUCKET_ACTIONS[bucket]
    if path in delta.get("identical", ()):
        return BUCKET_ACTIONS["identical"]
    return None
