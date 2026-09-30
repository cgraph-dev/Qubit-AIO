"""Packet 8P controlled evaluation — promotion mixin.

Owns the owner+gate-gated promotion decision and the abort/hold lifecycle.
Promotion never auto-applies: it requires an explicit owner approval AND a
passing regression/security gate report, otherwise it is HOLD. Applying a
promoted candidate to production remains a manual, separate operation.
"""
from __future__ import annotations

from typing import Dict

from ._models import (ST_ABORTED, ST_HELD, ST_PROMOTED, ST_RUNNING,
                      ExperimentError, bound)
from ._persist import PersistMixin
from ._stats import (DEC_PROMOTE, DEFAULT_MC_SAMPLES,
                     DEFAULT_MIN_SESSIONS_PER_ARM, DEFAULT_PROMOTE_THRESHOLD,
                     promotion_decision)


class PromoteMixin(PersistMixin):
    """Adds evaluate/promote/abort/hold to the composed store."""

    def evaluate(self, experiment_id: str, *,
                 mc_samples: int = DEFAULT_MC_SAMPLES,
                 min_sessions_per_arm: int = DEFAULT_MIN_SESSIONS_PER_ARM,
                 promote_threshold: float = DEFAULT_PROMOTE_THRESHOLD,
                 seed: int = None) -> Dict:
        exp = self.experiment(experiment_id)
        if exp is None:
            raise ExperimentError(f"unknown experiment {experiment_id}")
        champ = self.scores_for_config(experiment_id, exp.champion_config_id)
        chal = self.scores_for_config(experiment_id, exp.challenger_config_id)
        dec = promotion_decision(champ, chal, mc_samples=mc_samples,
                                 min_sessions_per_arm=min_sessions_per_arm,
                                 promote_threshold=promote_threshold, seed=seed)
        return {
            "state": "OK", "experiment_id": experiment_id,
            "decision": dec.decision, "posterior": round(dec.posterior, 6),
            "sessions_each": dec.sessions_each, "required": dec.required,
            "champion_scores": champ, "challenger_scores": chal,
            "owner_approved": exp.owner_approved,
            "gate_report": exp.gate_report,
            "promotable": dec.decision == DEC_PROMOTE
                          and exp.owner_approved and bool(exp.gate_report),
        }

    def record_promotion(self, experiment_id: str, *, owner_approved: bool,
                         gate_report: str, requested_by: str) -> Dict:
        if requested_by != "user":
            return {"state": "denied",
                    "code": "CONFIRMATION_SOURCE_FORBIDDEN",
                    "detail": "only a human owner may approve a promotion"}
        gate_report = bound(gate_report, 500, "gate_report")
        exp = self.experiment(experiment_id)
        if exp is None:
            raise ExperimentError(f"unknown experiment {experiment_id}")
        if exp.status != ST_RUNNING:
            return {"state": "denied", "detail": "experiment not running"}
        champ = self.scores_for_config(experiment_id, exp.champion_config_id)
        chal = self.scores_for_config(experiment_id, exp.challenger_config_id)
        dec = promotion_decision(champ, chal)
        if dec.decision != DEC_PROMOTE:
            return {"state": "hold", "decision": dec.decision,
                    "posterior": round(dec.posterior, 6),
                    "detail": "evidence does not meet the promotion threshold"}
        if not owner_approved:
            return {"state": "hold", "decision": dec.decision,
                    "posterior": round(dec.posterior, 6),
                    "detail": "owner approval required (never auto-promote)"}
        if not gate_report:
            return {"state": "hold", "decision": dec.decision,
                    "posterior": round(dec.posterior, 6),
                    "detail": "a passing regression/security gate report is "
                              "required (never promote without one)"}
        with self._lock:
            exp.status = ST_PROMOTED
            exp.decided_at = self._clock()
            exp.decision_posterior = round(dec.posterior, 6)
            exp.owner_approved = True
            exp.gate_report = gate_report
            self._conn.execute(
                """UPDATE experiments SET status=?, decided_at=?,
                   decision_posterior=?, owner_approved=?, gate_report=?
                   WHERE id=?""",
                (ST_PROMOTED, exp.decided_at, exp.decision_posterior, 1,
                 gate_report, experiment_id))
            self._conn.commit()
            self._journal("promote", {"id": experiment_id,
                                      "posterior": exp.decision_posterior})
            self._log("promote", id=experiment_id,
                      posterior=exp.decision_posterior)
        return {"state": "OK", "decision": dec.decision,
                "posterior": exp.decision_posterior,
                "detail": "promoted decision recorded; applying to production "
                          "remains a manual, separate operation"}

    def abort(self, experiment_id: str, *, requested_by: str) -> Dict:
        if requested_by != "user":
            return {"state": "denied", "code": "CONFIRMATION_SOURCE_FORBIDDEN"}
        exp = self.experiment(experiment_id)
        if exp is None:
            raise ExperimentError(f"unknown experiment {experiment_id}")
        if exp.status != ST_RUNNING:
            return {"state": "denied", "detail": "experiment not running"}
        with self._lock:
            self._conn.execute(
                "UPDATE experiments SET status=?, decided_at=? WHERE id=?",
                (ST_ABORTED, self._clock(), experiment_id))
            self._conn.commit()
            self._journal("abort", {"id": experiment_id})
            self._log("abort", id=experiment_id)
        return {"state": "OK", "status": ST_ABORTED}

    def hold(self, experiment_id: str) -> Dict:
        exp = self.experiment(experiment_id)
        if exp is None:
            raise ExperimentError(f"unknown experiment {experiment_id}")
        if exp.status != ST_RUNNING:
            return {"state": "denied", "detail": "experiment not running"}
        with self._lock:
            self._conn.execute("UPDATE experiments SET status=? WHERE id=?",
                               (ST_HELD, experiment_id))
            self._conn.commit()
            self._journal("hold", {"id": experiment_id})
            self._log("hold", id=experiment_id)
        return {"state": "OK", "status": ST_HELD}
