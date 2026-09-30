"""Packet 8P verified-skill memory — composed store.

Composes the clearly owned persistence, lifecycle, recall, and transfer
components into the public ``VerifiedSkillStore``. This file carries only the
composition and the journal/log/clock bridge; all logic lives in the mixins.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..logging_setup import log_event
from ..state_journal import HashChainJournal
from ._lifecycle import LifecycleMixin
from ._models import SKILL_KINDS, SKILL_STATUSES, VerifiedSkillError, SkillRecord
from ._recall import RecallMixin
from ._transfer import TransferMixin


class VerifiedSkillStore(LifecycleMixin, RecallMixin, TransferMixin):
    """Persistent verified-before-remember skill memory.

    Advisory recipe memory only. Never grants a capability and never executes
    model/page/tool-proposed code. The authoritative fact MemoryStore is
    untouched.
    """

    def __init__(self, path: Path | str, *,
                 clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._open(path)
        self._journal_obj = HashChainJournal(
            Path(path).with_name(Path(path).name + ".journal.jsonl"),
            event_prefix="verified_skills", clock=clock)
        if not self._journal_obj.path.exists():
            try:
                self._journal("init", {"version": 1})
            except Exception:
                pass

    # --- bridge methods used by the mixins ---
    def _journal(self, action: str, payload: Dict[str, Any]) -> None:
        self._journal_obj.append(action, payload)

    def _log(self, event: str, **fields: Any) -> None:
        log_event(f"verified_skills.{event}", **fields)

    @property
    def journal_path(self) -> Path:
        return self._journal_obj.path

    def journal_verify(self) -> Dict[str, Any]:
        return self._journal_obj.verify()

    def get(self, skill_id: str) -> Optional[SkillRecord]:
        return self._get(skill_id)

    def list(self, *, status: Optional[str] = None,
             kind: Optional[str] = None) -> List[SkillRecord]:
        if status is not None and status not in SKILL_STATUSES:
            raise VerifiedSkillError(f"bad status {status!r}")
        if kind is not None and kind not in SKILL_KINDS:
            raise VerifiedSkillError(f"bad kind {kind!r}")
        sql = "SELECT * FROM skills"
        where = []
        params: List[Any] = []
        if status:
            where.append("status=?")
            params.append(status)
        if kind:
            where.append("kind=?")
            params.append(kind)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC"
        return self._query(sql, tuple(params))

    def stats(self) -> Dict[str, Any]:
        cur = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM skills GROUP BY status")
        by_status = {r["status"]: int(r["n"]) for r in cur.fetchall()}
        cur = self._conn.execute(
            "SELECT COUNT(*) AS n FROM skills WHERE pinned=1")
        pinned = int(cur.fetchone()["n"])
        return {"by_status": by_status, "pinned": pinned,
                "bundled": len(self.bundled()),
                "journal": self.journal_verify()}

    # --- bundled Qubit skills (read-only; see _bundled.py) -----------------
    def bundled(self) -> List[Dict[str, Any]]:
        """Validated, Qubit-branded bundled skill manifests (no network)."""
        from ._bundled import bundled_skill_manifests

        return bundled_skill_manifests()

    def bundled_skill(self, name: str) -> Optional[Dict[str, Any]]:
        from ._bundled import get_bundled_skill

        return get_bundled_skill(name)

    def register_bundled_skills(self) -> Dict[str, Any]:
        from ._bundled import register_bundled_skills

        return register_bundled_skills(self)


def verified_skills_store(cfg, *, path: Optional[Path | str] = None) -> VerifiedSkillStore:
    """Open (or create) the verified-skill store. Disabled-by-default is a
    caller-level gate; the store is inert until used."""
    if path is None:
        path = cfg.get("verified_skills", "state_path", default="") or \
            (cfg.state_dir() / "verified_skills" / "skills.db")
    return VerifiedSkillStore(Path(path).expanduser())
