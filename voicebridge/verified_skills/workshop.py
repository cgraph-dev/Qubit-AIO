"""Qubit Skill Workshop — agent-owned collection lifecycle (OC-41, OC-D29).

The Qubit Skill Workshop is an *agent-owned* collection with explicit
backup/restore/rollback, curator status, and collection contracts. Qubit maps
that onto the existing verified-skill store so the safety properties are
preserved and the workshop adds only the authoring/collection layer:

* **author** — an agent (or human) submits a proposal; it lands
  ``quarantined`` exactly like ``verified-skills submit`` and can never
  self-verify (the store's owner-only verdict gate is untouched);
* **backup** — a digest-signed, 0600 JSON snapshot of the whole collection in
  ``<workshop>/backups``; bounded and never executed;
* **restore**/**rollback** — human-only (``requested_by`` must be a canonical
  confirmed-human principal: ``user`` or the channel-qualified
  ``cli:user``/``voice:user``/``dashboard:user``; agent/tool/model actors are
  refused), the snapshot digest is verified before applying, and the store
  writes its own pre-restore backup first; rollback restores the most recent
  (or a named) backup;
* **curator_status** — honest counts, journal integrity, and contract
  coverage; **contracts** — the collection rules Qubit enforces.

No code in this module imports, evaluates, or executes model/skill text, and
the package's no-unauthorized-runtime-surface guard still holds.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ._models import VerifiedSkillError, is_human_requester

WORKSHOP_SCHEMA = "qubit-skill-workshop/1"
BACKUP_SCHEMA = "qubit-skill-workshop-backup/1"
MAX_BACKUPS = 50
MAX_BACKUP_BYTES = 8_000_000

# Collection contracts the workshop enforces (surfaced by ``contracts``).
COLLECTION_CONTRACTS = (
    {"id": "verified-before-active",
     "description": "a proposed skill stays quarantined until a human owner "
                    "verifies it; no agent self-verification",
     "enforced": True},
    {"id": "digest-verified-restore",
     "description": "every restore/rollback verifies the snapshot digest "
                    "before applying and writes a pre-restore backup",
     "enforced": True},
    {"id": "human-authorized-recovery",
     "description": "only a human owner (requested_by=user) may restore or "
                    "roll back the collection",
     "enforced": True},
    {"id": "bounded-private-backups",
     "description": "backups are bounded, 0600, and capped per collection",
     "enforced": True},
)

CURATOR_ROLES = ("owner", "curator", "author", "observer")


class WorkshopError(VerifiedSkillError):
    """Typed Skill Workshop failure (never a traceback on the CLI)."""


def _digest(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=repr).encode("utf-8")
    ).hexdigest()


class SkillWorkshop:
    """Agent-owned collection layer over a :class:`VerifiedSkillStore`."""

    def __init__(self, store: Any, backup_dir: Path | str) -> None:
        self._store = store
        self.backup_dir = Path(backup_dir)

    # ------------------------------------------------------------- contracts
    def contracts(self) -> Dict[str, Any]:
        return {"state": "OK", "schema": WORKSHOP_SCHEMA,
                "contracts": [dict(c) for c in COLLECTION_CONTRACTS],
                "curator_roles": list(CURATOR_ROLES)}

    # ------------------------------------------------------------ collection
    def collection(self, *, status: Optional[str] = None) -> Dict[str, Any]:
        records = self._store.list(status=status)
        return {"state": "OK", "schema": WORKSHOP_SCHEMA,
                "skills": [r.to_dict() for r in records],
                "count": len(records), "status": status or "all"}

    def author(self, proposal: Any, *, author_kind: str = "agent",
               requested_by: str = "agent") -> Dict[str, Any]:
        """Submit a proposal into the collection (stays quarantined)."""
        kind = str(author_kind or "agent").strip().lower()
        if kind not in ("agent", "user", "tool"):
            raise WorkshopError(f"unknown author_kind {author_kind!r}")
        rec = self._store.submit(proposal)
        self._store._log("workshop_author", id=rec.id, author_kind=kind,
                         requested_by=requested_by)
        return {"state": "OK", "schema": WORKSHOP_SCHEMA, "skill": rec.to_dict(),
                "author_kind": kind,
                "contract": "quarantined until a human owner verifies"}

    # ---------------------------------------------------------------- backup
    def backup(self, *, label: str = "") -> Dict[str, Any]:
        snap = self._store.snapshot()
        payload = {"schema": BACKUP_SCHEMA, "label": str(label or "")[:64],
                   "created_at": time.time(), "snapshot": snap}
        payload["digest"] = _digest({k: v for k, v in payload.items()
                                     if k != "digest"})
        bid = f"collection-{int(time.time() * 1000)}-{payload['digest'][:10]}"
        path = self.backup_dir / f"{bid}.json"
        self._write_backup(path, payload)
        self._prune()
        return {"state": "OK", "schema": WORKSHOP_SCHEMA, "backup_id": bid,
                "path": str(path), "digest": payload["digest"],
                "skills": len(snap.get("skills", [])), "label": payload["label"]}

    def backups(self) -> Dict[str, Any]:
        rows: List[Dict[str, Any]] = []
        if self.backup_dir.is_dir():
            for path in sorted(self.backup_dir.glob("collection-*.json")):
                try:
                    payload = self._load_backup(path)
                except WorkshopError:
                    rows.append({"backup_id": path.stem, "state": "CORRUPT"})
                    continue
                rows.append({"backup_id": path.stem, "state": "OK",
                             "digest": payload.get("digest", "")[:16],
                             "skills": len(payload.get("snapshot", {})
                                           .get("skills", [])),
                             "created_at": payload.get("created_at"),
                             "label": payload.get("label", "")})
        rows.sort(key=lambda r: r["backup_id"])
        return {"state": "OK", "schema": WORKSHOP_SCHEMA, "backups": rows,
                "count": len(rows)}

    def restore(self, backup_id: str, *, requested_by: str) -> Dict[str, Any]:
        if not is_human_requester(requested_by):
            raise WorkshopError(
                "only a human owner may restore the skill collection")
        path = self._resolve_backup(backup_id)
        payload = self._load_backup(path)
        out = self._store.restore(payload["snapshot"], requested_by=requested_by)
        return {"state": "OK", "schema": WORKSHOP_SCHEMA,
                "restored_from": path.stem, "digest": payload["digest"],
                "restored": out.get("restored", 0)}

    def rollback(self, backup_id: str = "", *,
                 requested_by: str) -> Dict[str, Any]:
        """Restore the most recent (or named) backup, human-only."""
        if not is_human_requester(requested_by):
            raise WorkshopError(
                "only a human owner may roll back the skill collection")
        if not backup_id:
            rows = self.backups()["backups"]
            good = [r for r in rows if r.get("state") == "OK"]
            if not good:
                raise WorkshopError("no workshop backup to roll back to")
            backup_id = good[-1]["backup_id"]
        return self.restore(backup_id, requested_by=requested_by)

    # ------------------------------------------------------------ curator
    def curator_status(self) -> Dict[str, Any]:
        stats = self._store.stats()
        backups = self.backups()
        journal = stats.get("journal", {}) if isinstance(stats, dict) else {}
        return {"state": "OK", "schema": WORKSHOP_SCHEMA,
                "curator": "QUBIT-OWNED",
                "by_status": stats.get("by_status", {}),
                "pinned": stats.get("pinned", 0),
                "backups": backups["count"],
                "journal": journal.get("state", "unknown"),
                "contracts_enforced": len(COLLECTION_CONTRACTS)}

    # ------------------------------------------------------------- internals
    def _write_backup(self, out: Path, payload: Dict[str, Any]) -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(out.parent), prefix=".ws.",
                                   suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(payload, fh, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, out)
            os.chmod(out, 0o600)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _load_backup(self, path: Path) -> Dict[str, Any]:
        try:
            if path.stat().st_size > MAX_BACKUP_BYTES:
                raise WorkshopError("workshop backup too large")
            payload = json.loads(path.read_text(encoding="utf-8"))
        except WorkshopError:
            raise
        except (OSError, ValueError) as exc:
            raise WorkshopError(f"workshop backup unreadable: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("schema") != BACKUP_SCHEMA:
            raise WorkshopError("workshop backup has an unknown schema")
        digest = payload.get("digest")
        check = {k: v for k, v in payload.items() if k != "digest"}
        if digest != _digest(check):
            raise WorkshopError("workshop backup digest mismatch")
        if not isinstance(payload.get("snapshot"), dict):
            raise WorkshopError("workshop backup has no snapshot")
        return payload

    def _resolve_backup(self, backup_id: str) -> Path:
        clean = str(backup_id or "").strip()
        if not clean or "/" in clean or "\\" in clean or ".." in clean:
            raise WorkshopError("invalid backup id")
        path = self.backup_dir / f"{clean}.json"
        if not path.is_file():
            raise WorkshopError(f"no workshop backup {backup_id!r}")
        return path

    def _prune(self) -> None:
        paths = sorted(self.backup_dir.glob("collection-*.json"))
        while len(paths) > MAX_BACKUPS:
            try:
                paths.pop(0).unlink()
            except OSError:
                break


def verified_skill_workshop(cfg: Any, *, store: Any = None,
                            backup_dir: Path | str | None = None) -> SkillWorkshop:
    """Open the Skill Workshop over the configured verified-skill store."""
    if store is None:
        from .store import verified_skills_store

        store = verified_skills_store(cfg)
    if backup_dir is None:
        backup_dir = (Path(cfg.state_dir()) / "verified_skills" / "workshop"
                      / "backups")
    return SkillWorkshop(store, Path(backup_dir))
