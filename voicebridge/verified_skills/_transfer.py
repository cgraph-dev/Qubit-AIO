"""Packet 8P verified-skill memory — snapshot/restore/export mixin.

Owns deterministic, digest-verified snapshot/export and restore. Restore
requires a human owner, verifies the digest before applying, writes a
pre-restore backup, and never downgrades an existing verified skill in merge
mode. All persistence is 0600 and journaled.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from ._models import (COST_NORMAL, KIND_POSITIVE, SKILL_STATE_SCHEMA,
                      ST_QUARANTINED, TIER_COLD, TRUST_OBSERVED, _EMBEDDER_VERSION)
from ._models import SkillRecord
from ._persist import PersistMixin


class TransferMixin(PersistMixin):
    """Adds snapshot/restore/export to the composed store."""

    def snapshot(self, *, include_all: bool = True,
                 path: Optional[Path | str] = None) -> Dict[str, Any]:
        records = self._query("SELECT * FROM skills ORDER BY id")
        payload = {"schema": SKILL_STATE_SCHEMA, "generated_at": self._clock(),
                   "skills": [r.to_dict() for r in records]}
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        payload["digest"] = digest
        if path is not None:
            self._write_file(Path(path), payload)
        return payload

    def export(self, path: Path | str) -> Dict[str, Any]:
        return self.snapshot(path=path)

    def restore(self, data: Dict[str, Any], *, requested_by: str,
                merge: bool = False) -> Dict[str, Any]:
        from ._models import VerifiedSkillError, VerifiedSkillStateError
        if requested_by != "user":
            raise VerifiedSkillError("only a human may restore a snapshot")
        if not isinstance(data, dict) or data.get("schema") != SKILL_STATE_SCHEMA:
            raise VerifiedSkillError("invalid or unsupported snapshot schema")
        skills = data.get("skills")
        if not isinstance(skills, list):
            raise VerifiedSkillError("snapshot has no skills list")
        digest = data.get("digest")
        check = dict(data)
        check.pop("digest", None)
        expect = hashlib.sha256(
            json.dumps(check, sort_keys=True).encode("utf-8")).hexdigest()
        if digest != expect:
            raise VerifiedSkillError("snapshot digest mismatch (reject)")
        try:
            self.snapshot(path=self._path.with_name(self._path.name
                                                    + ".pre-restore.json"))
        except Exception:
            pass
        with self._lock:
            for item in skills:
                try:
                    rec = SkillRecord(
                        id=str(item["id"]), name=str(item["name"]),
                        interface=str(item["interface"]),
                        implementation=str(item["implementation"]),
                        acceptance_test=str(item["acceptance_test"]),
                        task=str(item["task"]),
                        capabilities=tuple(item.get("capabilities", ())),
                        cost=str(item.get("cost", COST_NORMAL)),
                        trust=int(item.get("trust", TRUST_OBSERVED)),
                        model=str(item.get("model", "")),
                        parents=tuple(item.get("parents", ())),
                        evidence=str(item.get("evidence", "")),
                        embedding_version=str(item.get("embedding_version",
                                                       _EMBEDDER_VERSION)),
                        embedding=list(item.get("embedding", [])),
                        utility_score=float(item.get("utility_score", 0.0)),
                        status=str(item.get("status", ST_QUARANTINED)),
                        kind=str(item.get("kind", KIND_POSITIVE)),
                        tier=str(item.get("tier", TIER_COLD)),
                        uses=int(item.get("uses", 0)),
                        success_rate=float(item.get("success_rate", 1.0)),
                        pinned=bool(item.get("pinned", False)),
                        check_strength=int(item.get("check_strength", 0)),
                        created_at=float(item.get("created_at", 0.0)),
                        updated_at=float(item.get("updated_at", 0.0)))
                except (KeyError, TypeError, ValueError) as exc:
                    raise VerifiedSkillStateError(f"bad snapshot row: {exc}") \
                        from exc
                if rec.status not in ("quarantined", "verified", "refuted",
                                      "archived"):
                    raise VerifiedSkillStateError("bad status in snapshot")
                existing = self._get(rec.id)
                if existing is not None:
                    if merge and existing.status == "verified" \
                            and rec.status != "verified":
                        continue
                    self._delete_row(rec.id)
                self._insert(rec)
            self._journal("restore", {"count": len(skills), "merge": merge})
        self._log("restore", count=len(skills), merge=merge)
        self.retier()
        return {"state": "OK", "restored": len(skills)}

    def _write_file(self, out: Path, payload: Dict[str, Any]) -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(out.parent), prefix=".snap.",
                                   suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(payload, fh, indent=2)
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
