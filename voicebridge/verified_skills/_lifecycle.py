"""Packet 8P verified-skill memory — lifecycle mixin.

Implements verified-before-remember: a candidate is staged QUARANTINED and
only becomes VERIFIED through an explicit OWNER verdict (there is no
self-verification path). Also negative (observed-failure) records, dedup on
verify (a verified near-duplicate reinforces), reinforcement, quarantine on
failure/revocation, and pin/unpin.

Owner-gated operations (verdict/pin/unpin/remove/reinforce-failure) require a
canonical confirmed-human principal (``HUMAN_REQUESTERS``): the CLI passes
``"user"`` and the voice/dashboard backends pass ``"<channel>:user"``
(``"voice:user"`` / ``"dashboard:user"``). Tool, agent, and model principals
are refused with ``CONFIRMATION_SOURCE_FORBIDDEN`` — there is no
self-verification path.
"""
from __future__ import annotations

import re
import uuid
from typing import Dict, Optional, Tuple

from ._models import (COST_CHEAP, KIND_NEGATIVE, KIND_POSITIVE,
                      MAX_CAPABILITIES, MIN_ACCEPTANCE_STRENGTH, ST_ARCHIVED,
                      ST_COLD, ST_HOT, ST_QUARANTINED,
                      ST_REFUTED, ST_VERIFIED, TIER_HOT, TRUST_OBSERVED,
                      TRUST_TOOL, TRUSTS, VerifiedSkillError, _EMBEDDER_VERSION,
                      acceptance_strength, bound, cosine, digest,
                      is_human_requester, recipe_embedding, utility_score)
from ._models import SkillProposal, SkillRecord
from ._persist import PersistMixin


class LifecycleMixin(PersistMixin):
    """Adds the state-transition methods to the composed store."""

    def _new_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:16]}"

    def _validate_proposal(self, p: SkillProposal) -> Dict:
        name = bound(p.name, 120, "name")
        if not name.strip():
            raise VerifiedSkillError("name required")
        interface = bound(p.interface, 2000, "interface")
        implementation = bound(p.implementation, 20000, "implementation")
        acceptance_test = bound(p.acceptance_test, 20000, "acceptance_test")
        task = bound(p.task, 500, "task")
        if not task.strip():
            raise VerifiedSkillError("task required")
        evidence = bound(p.evidence, 1000, "evidence")
        if p.cost not in ("cheap", "normal", "expensive"):
            raise VerifiedSkillError(f"bad cost {p.cost!r}")
        if int(p.trust) not in TRUSTS:
            raise VerifiedSkillError(f"bad trust {p.trust!r}")
        caps = tuple(str(c) for c in p.capabilities)[:MAX_CAPABILITIES]
        for c in caps:
            if not re.match(r"^[a-z][a-z0-9_.-]{2,63}$", c):
                raise VerifiedSkillError(f"bad capability name {c!r}")
        parents = tuple(str(x) for x in p.parents)[:8]
        return {"name": name, "interface": interface,
                "implementation": implementation,
                "acceptance_test": acceptance_test, "task": task,
                "capabilities": caps, "cost": p.cost, "trust": int(p.trust),
                "model": bound(p.model, 120, "model"), "parents": parents,
                "evidence": evidence}

    def submit(self, proposal: SkillProposal) -> SkillRecord:
        v = self._validate_proposal(proposal)
        now = self._clock()
        strength = acceptance_strength(v["acceptance_test"])
        rec = SkillRecord(
            id=self._new_id("skill"), name=v["name"], interface=v["interface"],
            implementation=v["implementation"],
            acceptance_test=v["acceptance_test"], task=v["task"],
            capabilities=v["capabilities"], cost=v["cost"], trust=v["trust"],
            model=v["model"], parents=v["parents"], evidence=v["evidence"],
            embedding_version=_EMBEDDER_VERSION,
            embedding=recipe_embedding(v["task"], v["interface"]),
            utility_score=0.0, status=ST_QUARANTINED, kind=KIND_POSITIVE,
            tier=ST_COLD, uses=0, success_rate=1.0, pinned=False,
            check_strength=strength, created_at=now, updated_at=now)
        with self._lock:
            self._insert(rec)
            self._journal("submit", {"id": rec.id, "name": rec.name,
                                     "task_hash": digest(rec.task),
                                     "check_strength": rec.check_strength})
        self._log("submit", id=rec.id, name=rec.name,
                  check_strength=rec.check_strength)
        return rec

    def _find_duplicate(self, rec: SkillRecord) -> Optional[SkillRecord]:
        # Praxis dedup: only a VERIFIED near-duplicate reinforces on verify.
        # A quarantined near-duplicate must never absorb a verified candidate.
        best: Optional[SkillRecord] = None
        best_sim = 0.92
        for other in self._query(
                "SELECT * FROM skills WHERE kind=? AND status=?",
                (KIND_POSITIVE, ST_VERIFIED)):
            if other.id == rec.id:
                continue
            if other.embedding_version != _EMBEDDER_VERSION:
                continue
            if other.interface.strip() != rec.interface.strip():
                continue
            sim = cosine(other.embedding, rec.embedding)
            if sim >= 0.92 and sim > best_sim:
                best = other
                best_sim = sim
        return best

    def _reinforce_hit(self, rec: SkillRecord) -> SkillRecord:
        new_uses = rec.uses + 1
        rec.success_rate = (rec.success_rate * rec.uses + 1.0) / new_uses
        rec.uses = new_uses
        rec.utility_score = utility_score(rec,
                                          self._distinct_retrieval_tasks(rec.id),
                                          self._clock())
        rec.updated_at = self._clock()
        self._update_row(rec)
        return rec

    def apply_verdict(self, skill_id: str, *, decision: str,
                      requested_by: str, reason: str = "") -> SkillRecord:
        if not is_human_requester(requested_by):
            raise VerifiedSkillError(
                "only a human owner may issue a verify/refute verdict "
                "(CONFIRMATION_SOURCE_FORBIDDEN)")
        if decision not in (ST_VERIFIED, ST_REFUTED):
            raise VerifiedSkillError(f"invalid decision {decision!r}")
        rec = self._get(skill_id)
        if rec is None:
            raise VerifiedSkillError(f"unknown skill {skill_id}")
        if rec.status == ST_ARCHIVED:
            raise VerifiedSkillError(f"skill {skill_id} is archived")
        if rec.status == ST_REFUTED:
            raise VerifiedSkillError(f"skill {skill_id} is already refuted")
        reason_b = bound(reason, 2000, "reason")
        if decision == ST_VERIFIED:
            if rec.kind != KIND_POSITIVE:
                raise VerifiedSkillError("only positive skills can be verified")
            if rec.check_strength < MIN_ACCEPTANCE_STRENGTH:
                raise VerifiedSkillError(
                    "acceptance test has no concrete expectation; cannot verify")
            existing = self._find_duplicate(rec)
            if existing is not None:
                existing = self._reinforce_hit(existing)
                with self._lock:
                    self._journal("verify_reinforce",
                                  {"id": existing.id, "reinforced": True,
                                   "candidate": rec.id, "reason": reason_b})
                    self._delete_row(rec.id)
                self._log("verify_reinforce", id=existing.id,
                          candidate=rec.id)
                return self._get(existing.id)
            rec.status = ST_VERIFIED
            rec.trust = max(rec.trust, TRUST_TOOL)
            rec.evidence = rec.evidence or reason_b
            rec.tier = TIER_HOT
            rec.utility_score = utility_score(rec,
                                              self._distinct_retrieval_tasks(
                                                  rec.id), self._clock())
            with self._lock:
                self._update_row(rec)
                self._journal("verify", {"id": rec.id, "reason": reason_b})
            self._log("verify", id=rec.id, name=rec.name)
            self.retier()
        else:
            rec.status = ST_REFUTED
            rec.evidence = rec.evidence or reason_b
            with self._lock:
                self._update_row(rec)
                self._journal("refute", {"id": rec.id, "reason": reason_b})
            self._log("refute", id=rec.id, name=rec.name)
        return self._get(rec.id)

    def record_failure(self, task: str, approach: str, reason: str) -> SkillRecord:
        task = bound(task, 500, "task")
        approach = bound(approach, 2000, "approach")
        reason = bound(reason, 2000, "reason")
        if not task.strip() or not approach.strip():
            raise VerifiedSkillError("task and approach required")
        now = self._clock()
        rec = SkillRecord(
            id=self._new_id("neg"), name=f"failure: {approach[:80]}",
            interface="", implementation="", acceptance_test="", task=task,
            capabilities=(), cost=COST_CHEAP, trust=TRUST_OBSERVED,
            model="observed", parents=(), evidence=reason,
            embedding_version=_EMBEDDER_VERSION,
            embedding=recipe_embedding(task, approach), utility_score=0.0,
            status=ST_VERIFIED, kind=KIND_NEGATIVE, tier=ST_HOT, uses=0,
            success_rate=1.0, pinned=False, check_strength=0,
            created_at=now, updated_at=now)
        with self._lock:
            self._insert(rec)
            self._journal("record_failure",
                          {"id": rec.id, "task_hash": digest(task),
                           "evidence": reason})
        self._log("record_failure", id=rec.id, task=task)
        return rec

    def reinforce(self, skill_id: str, *, outcome: str,
                  requested_by: str) -> SkillRecord:
        if outcome not in ("success", "failure"):
            raise VerifiedSkillError(f"bad outcome {outcome!r}")
        rec = self._get(skill_id)
        if rec is None:
            raise VerifiedSkillError(f"unknown skill {skill_id}")
        if rec.status != ST_VERIFIED:
            raise VerifiedSkillError("only verified skills can be reinforced")
        if outcome == "failure":
            if not is_human_requester(requested_by):
                raise VerifiedSkillError(
                    "only a human may confirm a failure (no self-verification)")
            rec.status = ST_QUARANTINED
            rec.tier = ST_COLD
            rec.updated_at = self._clock()
            with self._lock:
                self._update_row(rec)
                self._journal("reinforce_failure_quarantine", {"id": rec.id})
            self._log("reinforce_failure", id=rec.id)
            self.retier()
            return rec
        rec.uses += 1
        rec.success_rate = min(1.0, rec.success_rate + 0.05)
        rec.updated_at = self._clock()
        with self._lock:
            self._update_row(rec)
            self._journal("reinforce_success", {"id": rec.id})
        self._log("reinforce_success", id=rec.id)
        return rec

    def quarantine_revoked(self, skill_id: str) -> SkillRecord:
        rec = self._get(skill_id)
        if rec is None:
            raise VerifiedSkillError(f"unknown skill {skill_id}")
        rec.status = ST_QUARANTINED
        rec.tier = ST_COLD
        rec.pinned = False
        rec.updated_at = self._clock()
        with self._lock:
            self._update_row(rec)
            self._journal("quarantine_revoked", {"id": rec.id})
        self._log("quarantine_revoked", id=rec.id)
        self.retier()
        return rec

    def pin(self, skill_id: str, *, requested_by: str) -> SkillRecord:
        if not is_human_requester(requested_by):
            raise VerifiedSkillError("only a human may pin a skill")
        rec = self._get(skill_id)
        if rec is None:
            raise VerifiedSkillError(f"unknown skill {skill_id}")
        if rec.status != ST_VERIFIED or rec.kind != KIND_POSITIVE:
            raise VerifiedSkillError("only verified positive skills can be pinned")
        rec.pinned = True
        rec.tier = TIER_HOT
        rec.updated_at = self._clock()
        with self._lock:
            self._update_row(rec)
            self._journal("pin", {"id": rec.id})
        self._log("pin", id=rec.id)
        return rec

    def unpin(self, skill_id: str, *, requested_by: str) -> SkillRecord:
        if not is_human_requester(requested_by):
            raise VerifiedSkillError("only a human may unpin a skill")
        rec = self._get(skill_id)
        if rec is None:
            raise VerifiedSkillError(f"unknown skill {skill_id}")
        rec.pinned = False
        rec.updated_at = self._clock()
        with self._lock:
            self._update_row(rec)
            self._journal("unpin", {"id": rec.id})
        self._log("unpin", id=rec.id)
        return rec

    def remove(self, skill_id: str, *, requested_by: str) -> SkillRecord:
        if not is_human_requester(requested_by):
            raise VerifiedSkillError("only a human may remove a skill")
        rec = self._get(skill_id)
        if rec is None:
            raise VerifiedSkillError(f"unknown skill {skill_id}")
        with self._lock:
            self._journal("remove", {"id": rec.id})
            self._delete_row(rec.id)
        self._log("remove", id=rec.id)
        return rec
