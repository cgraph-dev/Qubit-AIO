"""Local-first sync, conflict review, export and safe exit for Qubit Office (P6-06).

The office is local-first. Sync is not a cloud call: it compares the local
records with an incoming batch, classifies the difference, and **refuses to
merge a conflict until a human reviews it**. The same module exports a review
bundle, writes an atomic local snapshot and proves a safe close (no record is
lost).

Guarantees
----------
* **No network.** There is no HTTP, socket or client import in this module.
* **Review before merge.** A differing record with the same id is a conflict.
  :meth:`LocalSync.publish` raises ``SYNC_REVIEW_REQUIRED`` unless the caller
  passed ``reviewed=True`` and recorded a resolution for every conflict.
* **Typed refusals.** Every failure is a :class:`LocalSyncError` with a stable
  code; none is a silent overwrite.
* **No data loss on exit.** :meth:`LocalSync.close` writes the snapshot
  atomically and re-reads it, returning a receipt that proves the records were
  persisted.
* **No fabricated identity.** Records need an ``id``; the module never invents
  one and never derives a record from presentation state.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "SCHEMA",
    "VERSION",
    "RESOLUTIONS",
    "LocalSyncError",
    "SyncPlan",
    "SyncReceipt",
    "LocalSync",
    "detect_conflicts",
    "fingerprint_record",
    "export_bundle",
    "conflict_report",
]

SCHEMA = "qubit-office-local-sync/1"
VERSION = 1

RESOLUTIONS: Tuple[str, ...] = ("local", "incoming", "merged")

_META_KEYS = frozenset({"id", "record_id", "updated_at", "fingerprint",
                        "conflict"})


class LocalSyncError(ValueError):
    """A typed sync refusal. ``code`` is stable and testable."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


# ------------------------------------------------------------------ helpers
def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=True, default=str).encode("utf-8")
    ).hexdigest()


def _record_id(row: Mapping[str, Any]) -> str:
    return _text(row.get("id") or row.get("record_id"))


def _record_fields(row: Mapping[str, Any]) -> Dict[str, Any]:
    fields = row.get("fields")
    if fields is None:
        fields = {key: value for key, value in row.items()
                  if key not in _META_KEYS}
    if not isinstance(fields, Mapping):
        raise LocalSyncError(
            "SYNC_BAD_INPUT", "record fields must be a mapping")
    return dict(fields)


def _normalise(row: Any, *, where: str) -> Dict[str, Any]:
    if not isinstance(row, Mapping):
        raise LocalSyncError("SYNC_BAD_INPUT", f"{where} is not a mapping")
    rid = _record_id(row)
    if not rid:
        raise LocalSyncError("SYNC_BAD_INPUT", f"{where} has no id")
    fields = _record_fields(row)
    return {
        "id": rid,
        "fields": fields,
        "updated_at": _text(row.get("updated_at")),
        "fingerprint": fingerprint_record(fields),
    }


def _index(rows: Iterable[Any], *, where: str) -> Dict[str, Dict[str, Any]]:
    indexed: Dict[str, Dict[str, Any]] = {}
    for position, row in enumerate(rows):
        record = _normalise(row, where=f"{where}[{position}]")
        if record["id"] in indexed:
            raise LocalSyncError(
                "SYNC_BAD_INPUT", f"duplicate record id {record['id']!r}")
        indexed[record["id"]] = record
    return indexed


def fingerprint_record(fields: Mapping[str, Any]) -> str:
    """Stable fingerprint of the meaningful record fields (not metadata)."""
    return _digest(dict(fields))


# --------------------------------------------------------------------- plan
@dataclass(frozen=True)
class SyncPlan:
    local_count: int
    incoming_count: int
    added: Tuple[str, ...]
    unchanged: Tuple[str, ...]
    conflicts: Tuple[Dict[str, Any], ...]
    local_only: Tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.conflicts

    def to_dict(self) -> Dict[str, Any]:
        return {
            "local_count": self.local_count,
            "incoming_count": self.incoming_count,
            "added": list(self.added),
            "unchanged": list(self.unchanged),
            "conflicts": [dict(c) for c in self.conflicts],
            "local_only": list(self.local_only),
            "ok": self.ok,
        }


def detect_conflicts(
    local: Sequence[Any], incoming: Sequence[Any],
) -> SyncPlan:
    """Classify an incoming batch against the local records.

    A same-id record with a different field fingerprint is a conflict — never a
    silent overwrite. ``local_only`` records are untouched.
    """
    local_by = _index(local, where="local")
    incoming_by = _index(incoming, where="incoming")
    added: List[str] = []
    unchanged: List[str] = []
    conflicts: List[Dict[str, Any]] = []
    for rid, record in incoming_by.items():
        current = local_by.get(rid)
        if current is None:
            added.append(rid)
        elif current["fingerprint"] == record["fingerprint"]:
            unchanged.append(rid)
        else:
            conflicts.append({
                "id": rid,
                "local": current,
                "incoming": record,
            })
    local_only = [rid for rid in local_by if rid not in incoming_by]
    return SyncPlan(
        local_count=len(local_by),
        incoming_count=len(incoming_by),
        added=tuple(sorted(added)),
        unchanged=tuple(sorted(unchanged)),
        conflicts=tuple(sorted(conflicts, key=lambda c: c["id"])),
        local_only=tuple(sorted(local_only)),
    )


def conflict_report(plan: SyncPlan) -> Dict[str, Any]:
    """A reviewable conflict report (no merge, no side effect)."""
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "ok": plan.ok,
        "review_required": not plan.ok,
        "conflicts": [
            {
                "id": conflict["id"],
                "local_fingerprint": conflict["local"]["fingerprint"],
                "incoming_fingerprint": conflict["incoming"]["fingerprint"],
                "local_fields": conflict["local"]["fields"],
                "incoming_fields": conflict["incoming"]["fields"],
            }
            for conflict in plan.conflicts
        ],
    }


@dataclass(frozen=True)
class SyncReceipt:
    applied: Tuple[str, ...]
    kept: Tuple[str, ...]
    resolved: Tuple[str, ...]
    conflicts: int
    reviewed: bool
    digest: str
    records: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "applied": list(self.applied),
            "kept": list(self.kept),
            "resolved": list(self.resolved),
            "conflicts": int(self.conflicts),
            "reviewed": bool(self.reviewed),
            "digest": self.digest,
            "records": int(self.records),
        }


# --------------------------------------------------------------------- sync
def _refuse_remote(path: Path | str) -> Path:
    raw = str(path)
    if "://" in raw or raw.startswith(("//", "\\\\")):
        raise LocalSyncError(
            "SYNC_REMOTE_REFUSED",
            "sync and export are local-only; no network or share path")
    return Path(raw)


def export_bundle(
    records: Sequence[Mapping[str, Any]],
    path: Path | str,
    *,
    tenant_id: str,
    extra: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Write a deterministic local review/export bundle, atomically."""
    target = _refuse_remote(path)
    indexed = _index(records, where="records")
    ordered = [indexed[rid] for rid in sorted(indexed)]
    payload: Dict[str, Any] = {
        "schema": SCHEMA,
        "version": VERSION,
        "tenant_id": _text(tenant_id),
        "records": ordered,
        "counts": {"records": len(ordered)},
    }
    if extra:
        payload["extra"] = dict(extra)
    payload["digest"] = _digest(
        {k: v for k, v in payload.items() if k != "digest"})
    encoded = json.dumps(payload, indent=2, sort_keys=True,
                         ensure_ascii=True) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(target.parent),
            prefix=f".{target.name}.", suffix=".tmp", delete=False,
        ) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            tmp_path = Path(handle.name)
        os.replace(tmp_path, target)
    except OSError as exc:
        raise LocalSyncError(
            "SYNC_EXPORT_FAILED", f"cannot write {target}: {exc}") from exc
    return {
        "schema": SCHEMA,
        "path": str(target),
        "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "digest": payload["digest"],
        "records": len(ordered),
    }


class LocalSync:
    """Local-first record store with conflict review, export and safe close."""

    def __init__(
        self,
        *,
        tenant_id: str,
        store_path: Path | str,
        records: Optional[Iterable[Mapping[str, Any]]] = None,
    ) -> None:
        self.tenant_id = _text(tenant_id)
        if not self.tenant_id:
            raise LocalSyncError("SYNC_BAD_INPUT", "tenant_id is required")
        self.store_path = _refuse_remote(store_path)
        initial = list(records or ())
        self._records: Dict[str, Dict[str, Any]] = _index(
            initial, where="local")
        self._decisions: Dict[str, Dict[str, Any]] = {}
        self._dirty = False

    # -- reads ----------------------------------------------------------
    @property
    def records(self) -> List[Dict[str, Any]]:
        return [dict(self._records[rid]) for rid in sorted(self._records)]

    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        record = self._records.get(record_id)
        return dict(record) if record else None

    def diff(self, incoming: Sequence[Any]) -> SyncPlan:
        return detect_conflicts(self.records, incoming)

    # -- review ---------------------------------------------------------
    def review(self, conflict_id: str, resolution: str,
               merged: Optional[Mapping[str, Any]] = None) -> None:
        """Record a human resolution for one conflict. No merge happens yet."""
        if resolution not in RESOLUTIONS:
            raise LocalSyncError(
                "SYNC_BAD_INPUT",
                f"resolution must be one of {RESOLUTIONS}")
        if not _text(conflict_id):
            raise LocalSyncError("SYNC_BAD_INPUT", "conflict id is required")
        if resolution == "merged":
            if merged is None or not isinstance(merged, Mapping):
                raise LocalSyncError(
                    "SYNC_BAD_INPUT",
                    "a merged resolution requires merged fields")
            fields = dict(merged)
        else:
            fields = {}
        self._decisions[conflict_id] = {
            "resolution": resolution,
            "merged": fields,
        }

    def clear_review(self) -> None:
        self._decisions.clear()

    # -- merge ----------------------------------------------------------
    def publish(self, incoming: Sequence[Any], *,
                reviewed: bool = False) -> SyncReceipt:
        """Apply an incoming batch, refusing unreviewed conflicts."""
        plan = self.diff(incoming)
        if plan.conflicts and not reviewed:
            raise LocalSyncError(
                "SYNC_REVIEW_REQUIRED",
                f"{len(plan.conflicts)} conflict(s) must be reviewed before"
                f" merge")
        if plan.conflicts:
            missing = [c["id"] for c in plan.conflicts
                       if c["id"] not in self._decisions]
            if missing:
                raise LocalSyncError(
                    "SYNC_CONFLICT_UNRESOLVED",
                    f"no review decision for {missing}")
        incoming_by = _index(incoming, where="incoming")
        applied: List[str] = []
        kept: List[str] = []
        resolved: List[str] = []
        for rid in plan.added:
            self._records[rid] = dict(incoming_by[rid])
            applied.append(rid)
        for rid in plan.unchanged:
            self._records[rid] = dict(incoming_by[rid])
        for conflict in plan.conflicts:
            rid = conflict["id"]
            decision = self._decisions[rid]
            resolution = decision["resolution"]
            if resolution == "incoming":
                self._records[rid] = dict(incoming_by[rid])
                applied.append(rid)
            elif resolution == "merged":
                fields = dict(decision["merged"])
                self._records[rid] = {
                    "id": rid,
                    "fields": fields,
                    "updated_at": incoming_by[rid]["updated_at"],
                    "fingerprint": fingerprint_record(fields),
                }
                applied.append(rid)
            else:  # local wins
                kept.append(rid)
            resolved.append(rid)
        self._dirty = True
        self._decisions.clear()
        return SyncReceipt(
            applied=tuple(sorted(applied)),
            kept=tuple(sorted(kept)),
            resolved=tuple(sorted(resolved)),
            conflicts=len(plan.conflicts),
            reviewed=bool(reviewed),
            digest=self.digest(),
            records=len(self._records),
        )

    # -- export / close -------------------------------------------------
    def digest(self) -> str:
        return _digest(self.records)

    def export(self, path: Path | str) -> Dict[str, Any]:
        return export_bundle(self.records, path, tenant_id=self.tenant_id)

    def save(self) -> Dict[str, Any]:
        receipt = export_bundle(
            self.records, self.store_path, tenant_id=self.tenant_id)
        self._dirty = False
        return receipt

    def close(self) -> Dict[str, Any]:
        """Write and verify the local snapshot; prove no record was lost."""
        receipt = self.save()
        try:
            raw = json.loads(self.store_path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            raise LocalSyncError(
                "SYNC_EXPORT_FAILED",
                f"cannot re-read the snapshot: {exc}") from exc
        persisted = _index(raw.get("records") or (), where="persisted")
        if set(persisted) != set(self._records):
            raise LocalSyncError(
                "SYNC_DATA_LOSS",
                "the persisted snapshot does not hold every local record")
        for rid, record in self._records.items():
            if persisted[rid]["fingerprint"] != record["fingerprint"]:
                raise LocalSyncError(
                    "SYNC_DATA_LOSS",
                    f"record {rid!r} changed during the snapshot write")
        return {
            "ok": True,
            "path": str(self.store_path),
            "sha256": receipt["sha256"],
            "digest": receipt["digest"],
            "records": len(persisted),
        }
