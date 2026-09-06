"""Packet 8P verified-skill memory — recall, dedup, retention mixin.

Owns the budgeted top-K recall (with a separate negatives slot), utility
tiering, and retention (archive/quarantine of stale or low-utility skills).
Recall is O(k) context-bounded and independent of library size.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ._models import (DEFAULT_HOT_CAP, DEFAULT_MAX_NEGATIVES,
                      DEFAULT_NEGATIVE_THRESHOLD, DEFAULT_TOKEN_BUDGET,
                      DEFAULT_TOP_K, KIND_NEGATIVE, KIND_POSITIVE, ST_ARCHIVED,
                      ST_COLD, ST_HOT, ST_QUARANTINED, ST_VERIFIED,
                      TIER_COLD, TIER_HOT, TIER_WARM, _EMBEDDER_VERSION,
                      cosine, digest, est_tokens, recency_decay,
                      recipe_embedding, utility_score)
from ._models import SkillRecord
from ._persist import PersistMixin


class RecallMixin(PersistMixin):
    """Adds recall + tiering + retention to the composed store."""

    def recall(self, query: str, *, top_k: int = DEFAULT_TOP_K,
               token_budget: int = DEFAULT_TOKEN_BUDGET,
               negative_threshold: float = DEFAULT_NEGATIVE_THRESHOLD,
               max_negatives: int = DEFAULT_MAX_NEGATIVES) -> Dict:
        top_k = max(1, int(top_k))
        token_budget = max(64, int(token_budget))
        max_negatives = max(0, int(max_negatives))
        q_emb = recipe_embedding(query, "")
        now = self._clock()
        positives = [r for r in self._query(
            "SELECT * FROM skills WHERE status=? AND kind=? AND tier=?",
            (ST_VERIFIED, KIND_POSITIVE, TIER_HOT))
            if r.embedding_version == _EMBEDDER_VERSION]
        scored = []
        for r in positives:
            sim = cosine(r.embedding, q_emb)
            if sim <= 0:
                continue
            score = (sim * (1.0 + 0.2 * _log1p(r.utility_score))
                     * recency_decay(r.created_at, now))
            scored.append((score, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        selected: List[Dict] = []
        cost = 0
        for score, r in scored[:top_k]:
            est = est_tokens(r.name + r.interface + r.implementation
                             + r.acceptance_test)
            if selected and cost + est > token_budget:
                continue
            cost += est
            from ..redaction import redact_text
            selected.append({
                "id": r.id, "name": r.name, "task": redact_text(r.task),
                "interface": redact_text(r.interface),
                "implementation": redact_text(r.implementation)[:800],
                "acceptance_test": redact_text(r.acceptance_test)[:400],
                "cost": r.cost, "trust": r.trust,
                "capabilities": r.capabilities, "score": round(score, 4),
            })
            with self._lock:
                self._conn.execute(
                    "INSERT OR REPLACE INTO retrievals(skill_id, task_hash,"
                    " retrieved_at) VALUES(?,?,?)",
                    (r.id, digest(query), now))
                self._conn.commit()
        negatives = []
        neg_cost = 0
        matched = []
        for r in self._query(
                "SELECT * FROM skills WHERE status=? AND kind=? ORDER BY"
                " created_at DESC LIMIT 200", (ST_VERIFIED, KIND_NEGATIVE)):
            if r.embedding_version != _EMBEDDER_VERSION:
                continue
            sim = cosine(r.embedding, q_emb)
            if sim > negative_threshold:
                matched.append((sim, r))
        matched.sort(key=lambda x: x[0], reverse=True)
        from ..redaction import redact_text
        for sim, r in matched[:max_negatives]:
            neg_cost += est_tokens(r.name + r.task + r.evidence)
            negatives.append({"id": r.id, "name": r.name,
                              "task": redact_text(r.task),
                              "approach": redact_text(r.name[9:]),
                              "evidence": redact_text(r.evidence)[:400],
                              "score": round(sim, 4)})
        return {"selected": selected, "negatives": negatives,
                "cost_estimate": cost + neg_cost,
                "retrieval_count": len(selected) + len(negatives)}

    def retier(self, hot_cap: int = DEFAULT_HOT_CAP) -> Dict:
        rows = self._query(
            "SELECT * FROM skills WHERE status=? AND kind=?",
            (ST_VERIFIED, KIND_POSITIVE))
        rows.sort(key=lambda r: (r.pinned, r.utility_score), reverse=True)
        for i, r in enumerate(rows):
            if r.pinned:
                tier = TIER_HOT
            elif i < hot_cap:
                tier = TIER_HOT
            elif i < hot_cap * 4:
                tier = TIER_WARM
            else:
                tier = TIER_COLD
            if r.tier != tier:
                r.tier = tier
                self._update_row(r)
        counts = {"hot": 0, "warm": 0, "cold": 0}
        for r in self._query(
                "SELECT * FROM skills WHERE status=? AND kind=?",
                (ST_VERIFIED, KIND_POSITIVE)):
            counts[r.tier] = counts.get(r.tier, 0) + 1
        return {"state": "OK", "hot_cap": hot_cap, "counts": counts}

    def sweep(self, *, expired_before: Optional[float] = None,
              evict_threshold: float = 0.1) -> Dict:
        now = self._clock()
        cutoff = expired_before if expired_before is not None else now
        archived = 0
        quarantined = 0
        for r in self._query(
                "SELECT * FROM skills WHERE status=? AND kind=?",
                (ST_VERIFIED, KIND_POSITIVE)):
            if r.pinned:
                continue
            if r.tier == TIER_COLD and r.utility_score < evict_threshold \
                    and r.updated_at < cutoff:
                r.status = ST_ARCHIVED
                r.updated_at = now
                self._update_row(r)
                self._journal("archive", {"id": r.id})
                archived += 1
        for r in self._query(
                "SELECT * FROM skills WHERE status=? AND kind=?",
                (ST_VERIFIED, KIND_POSITIVE)):
            if r.pinned:
                continue
            if r.uses == 0 and r.updated_at < cutoff - 86400 * 365:
                r.status = ST_QUARANTINED
                r.tier = ST_COLD
                r.updated_at = now
                self._update_row(r)
                self._journal("stale_quarantine", {"id": r.id})
                quarantined += 1
        self._log("sweep", archived=archived, quarantined=quarantined)
        return {"state": "OK", "archived": archived,
                "quarantined": quarantined}


def _log1p(x: float) -> float:
    import math
    return math.log(1.0 + x)
