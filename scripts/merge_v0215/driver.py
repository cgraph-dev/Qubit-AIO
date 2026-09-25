# scripts/merge_v0215/driver.py
"""Task 6 bucket merge driver for the v0.21.5 1:1 rebase.

Consumes the Task 5 primitive (:func:`merge_v0215.merge.merge_file`) and the
Task 5 triage (``prep/TASK6_TRIAGE.json``) to mutate ``qubit_runtime/**``
bucket by bucket.

Ruling R8 fixes the merge base: ``base = engine(v0.21.4)``, ``ours =
engine(v0.21.5)`` (candidate), ``theirs = current Qubit tree``.  The mandatory
bucket rule is:

* ``TAKE_OURS``      -> copy ``ours`` (upstream-only change/add/delete);
* ``REAPPLY_THEIRS`` -> ``merge_file(ours, base, theirs)``; on conflict markers
  keep ``theirs`` and record;
* ``CONFLICT``       -> keep ``theirs`` (never lose Qubit behavior) and record
  for analyst resolution;
* only in candidate -> add; only in current -> keep.

The module is pure except for :func:`apply_record`, which is the single writer.
It never touches the overlay trees (``voicebridge/``, ``systemd/``,
``config/``, ``frontend/``, ``skills/``): every path it writes is under the
``qubit_runtime`` root it is handed.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Optional

from .merge import MergeConflict, merge_file

#: Buckets in mandatory merge order (the last is the catch-all remainder).
BUCKET_ORDER: tuple[str, ...] = (
    "qubit_state*",
    "agent",
    "gateway",
    "providers",
    "tools",
    "qubit_cli",
    "tui_gateway",
    "plugins",
    "remaining",
)

#: Named subsystem buckets (everything else is ``remaining``).
_NAMED = ("agent", "gateway", "providers", "tools", "qubit_cli", "tui_gateway",
          "plugins")

#: Directories that are never part of the merge surface.
_SKIP_PARTS = frozenset({".git", "__pycache__", ".pytest_cache", ".mypy_cache",
                         ".ruff_cache"})

#: Resolution vocabulary reported in the audit artifact.
RES_AUTO_OURS = "AUTO_OURS"
RES_ADDED = "ADDED"
RES_MERGE_FILE = "MERGE_FILE"
RES_KEEP_CONFLICT = "KEEP_THEIRS_CONFLICT"
RES_KEEP = "KEEP_THEIRS"
RES_DELETED = "DELETED"
RES_SKIP = "SKIP_IDENTICAL"


def bucket_of(rel: str) -> str:
    """Return the bucket name for a ``qubit_runtime``-relative POSIX path."""
    if "/" not in rel and rel.startswith("qubit_state") and rel.endswith(".py"):
        return "qubit_state*"
    for name in _NAMED:
        if rel.startswith(name + "/"):
            return name
    return "remaining"


def _skip(rel: str) -> bool:
    return bool(_SKIP_PARTS.intersection(Path(rel).parts))


def _read(root: Path, rel: str) -> Optional[bytes]:
    p = root / rel
    try:
        if p.is_file():
            return p.read_bytes()
    except OSError:
        return None
    return None


def _present(root: Path, rel: str) -> bool:
    return (root / rel).is_file()


def iter_files(root: Path) -> set[str]:
    """Every merge-relevant relative path under *root* (skips VCS/caches)."""
    out: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_PARTS]
        dp = Path(dirpath)
        for fn in filenames:
            rel = (dp / fn).relative_to(root).as_posix()
            if not _skip(rel):
                out.add(rel)
    return out


def classify(base: Path, ours: Path, theirs: Path, rel: str) -> str:
    """Classify one path with the Task 5 triage rules (byte-identical match)."""
    bb = _read(base, rel)
    ob = _read(ours, rel)
    tb = _read(theirs, rel)
    pb, po, pt = bb is not None, ob is not None, tb is not None
    if pb and po and pt:
        if bb == ob == tb:
            return "IDENTICAL"
        if bb == tb:
            return "TAKE_OURS"
        if bb == ob:
            return "REAPPLY_THEIRS"
        if ob == tb:
            return "SAME_CHANGE"
        return "CONFLICT"
    if not pb and po and pt:
        return "ADDED_IDENTICAL" if ob == tb else "CONFLICT"
    if pb and not po and not pt:
        return "BOTH_DELETED"
    if not pb and po and not pt:
        return "TAKE_OURS"          # new upstream file
    if not pb and not po and pt:
        return "REAPPLY_THEIRS"     # fork-only file
    if pb and not po and pt:
        return "TAKE_OURS" if bb == tb else "CONFLICT"      # delete-upstream
    if pb and po and not pt:
        return "REAPPLY_THEIRS" if bb == ob else "CONFLICT"  # delete-fork
    raise AssertionError(f"unclassifiable path: {rel!r}")


def resolve(base: Path, ours: Path, theirs: Path, rel: str,
            cls: str) -> dict:
    """Resolve one path to ``(resolution, action, data, conflict)``.

    ``action`` is ``write`` / ``delete`` / ``noop``; ``data`` is the bytes to
    write for ``write`` (else ``None``); ``conflict`` marks a record handed to
    the analysts.
    """
    ob = _read(ours, rel)
    tb = _read(theirs, rel)
    rec = {"path": rel, "class": cls, "conflict": False}

    if cls in ("IDENTICAL", "ADDED_IDENTICAL", "SAME_CHANGE", "BOTH_DELETED"):
        rec.update(resolution=RES_SKIP, action="noop", data=None,
                   merge_markers=False)
        return rec

    if cls == "TAKE_OURS":
        if ob is None:
            rec.update(resolution=RES_DELETED, action="delete", data=None)
        else:
            resolution = RES_ADDED if _read(base, rel) is None else RES_AUTO_OURS
            rec.update(resolution=resolution, action="write", data=ob)
        rec["merge_markers"] = False
        return rec

    if cls == "REAPPLY_THEIRS":
        try:
            merged = merge_file(base, ours, theirs, rel)
        except MergeConflict:
            merged = tb
            rec.update(resolution=RES_KEEP_CONFLICT, action=_keep_action(tb),
                       data=tb, conflict=True, merge_markers=True)
            return rec
        rec.update(resolution=RES_MERGE_FILE,
                   action=_content_action(merged, tb), data=merged,
                   merge_markers=False)
        return rec

    # CONFLICT -> keep theirs by default (no merge attempted).
    rec.update(resolution=RES_KEEP_CONFLICT, action=_keep_action(tb), data=tb,
               conflict=True, merge_markers=False)
    return rec


def _keep_action(tb: Optional[bytes]) -> str:
    return "noop" if tb is not None else "delete"


def _content_action(merged: bytes, tb: Optional[bytes]) -> str:
    # theirs absent (fork deleted the file) -> the merge restores ``ours``;
    # otherwise a merge that reproduces theirs is a no-op against the target.
    if tb is not None and merged == tb:
        return "noop"
    return "write"


def _sha(data: Optional[bytes]) -> Optional[str]:
    return None if data is None else hashlib.sha256(data).hexdigest()


def apply_record(root: Path, rec: dict, ours_root: Optional[Path] = None) -> None:
    """Single writer: apply one resolved record under *root*."""
    rel = rec["path"]
    path = root / rel
    action = rec["action"]
    if action == "noop":
        return
    if action == "delete":
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    data = rec["data"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    src = (ours_root / rel) if ours_root is not None else None
    if src is not None and src.is_file():
        try:
            os.chmod(path, stat.S_IMODE(src.stat().st_mode))
        except OSError:
            pass


def run_bucket(target: Path, base: Path, ours: Path, theirs: Path,
               bucket: str, apply: bool = True) -> list[dict]:
    """Merge one bucket; return the per-file audit records (changed only)."""
    all_rel = iter_files(base) | iter_files(ours) | iter_files(theirs)
    target_rels = [r for r in all_rel if bucket_of(r) == bucket]
    records: list[dict] = []
    for rel in sorted(target_rels):
        cls = classify(base, ours, theirs, rel)
        if cls == "IDENTICAL":
            continue
        rec = resolve(base, ours, theirs, rel, cls)
        rec["bucket"] = bucket
        if apply:
            apply_record(target, rec, ours)
        rec["base_sha256"] = _sha(_read(base, rel))
        rec["ours_sha256"] = _sha(_read(ours, rel))
        rec["theirs_sha256"] = _sha(_read(theirs, rel))
        rec["result_sha256"] = (
            _sha(_read(target, rel)) if apply else None
        )
        # The raw merged bytes are only needed by the writer; never serialize.
        rec.pop("data", None)
        records.append(rec)
    return records


def run_merge(target: Path, base: Path, ours: Path, theirs: Path,
              apply: bool = True) -> dict:
    """Merge every bucket in order; return the full audit document."""
    all_records: list[dict] = []
    for bucket in BUCKET_ORDER:
        recs = run_bucket(target, base, ours, theirs, bucket, apply=apply)
        all_records.extend(recs)
    return build_audit(all_records, base, ours, theirs)


#: Resolution -> totals bucket name.
_RES_BUCKETS = {
    RES_AUTO_OURS: "AUTO_OURS",
    RES_ADDED: "ADDED",
    RES_MERGE_FILE: "MERGE_FILE",
    RES_KEEP_CONFLICT: "KEEP_THEIRS_CONFLICT",
    RES_KEEP: "KEEP_THEIRS",
    RES_DELETED: "DELETED",
    RES_SKIP: "SKIP_IDENTICAL",
}


def build_audit(records: list[dict], base: Path, ours: Path,
                theirs: Path) -> dict:
    from collections import Counter

    by_res = Counter(r["resolution"] for r in records)
    by_class = Counter(r["class"] for r in records)
    by_bucket = Counter(r["bucket"] for r in records)
    by_bucket_res: dict[str, Counter] = {}
    for r in records:
        by_bucket_res.setdefault(r["bucket"], Counter())[r["resolution"]] += 1
    conflicts = [
        {"path": r["path"], "bucket": r["bucket"],
         "kind": "MERGE_MARKERS" if r.get("merge_markers")
                 else "TRIAGE_CONFLICT",
         "action": r["action"]}
        for r in records if r["resolution"] == RES_KEEP_CONFLICT
    ]
    written = sum(1 for r in records if r["action"] == "write")
    deleted = sum(1 for r in records if r["action"] == "delete")
    return {
        "schema": "qubit-v0215-task6-merge/1",
        "base_root": str(base),
        "ours_root": str(ours),
        "theirs_root": str(theirs),
        "merge_rule": "TAKE_OURS->ours; REAPPLY_THEIRS->merge_file; "
                      "CONFLICT->keep theirs; add candidate-only",
        "totals": {
            "changed": len(records),
            "written": written,
            "deleted": deleted,
            "conflicts": len(conflicts),
            "by_resolution": {k: by_res.get(k, 0) for k in _RES_BUCKETS},
            "by_class": dict(sorted(by_class.items())),
            "by_bucket": dict(sorted(by_bucket.items())),
            "by_bucket_resolution": {
                b: dict(sorted(c.items())) for b, c in sorted(by_bucket_res.items())
            },
        },
        "conflicts": conflicts,
        "files": records,
    }


def write_audit(audit: dict, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(audit, indent=1, sort_keys=False) + "\n",
                   encoding="utf-8")
    return out
