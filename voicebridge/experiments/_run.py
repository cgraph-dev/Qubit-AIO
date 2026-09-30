"""Packet 8P controlled evaluation — run mixin.

Owns candidate registration, the one-running-experiment invariant, trials,
signals, and per-config score aggregation. The owner+gate-gated promotion
decision lives in ``_promote.py``; this module only records trials/signals.
"""
from __future__ import annotations

import uuid
from typing import List

from ._models import (ST_RUNNING, VAR_CHAMPION, VARIANTS,
                      Candidate, Experiment, ExperimentError, Signal, Trial,
                      bound)
from ._persist import PersistMixin
from ._stats import aggregate_scores, config_fingerprint


class RunMixin(PersistMixin):
    """Adds the candidate/experiment/trial/signal methods to the composed store."""

    def _new_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:16]}"

    # ------------------------------------------------------------ candidates
    def register_candidate(self, *, role: str, label: str, model: str,
                           description: str) -> Candidate:
        if role not in ("champion", "challenger", "historical"):
            raise ExperimentError(f"bad role {role!r}")
        label = bound(label, 200, "label")
        model = bound(model, 120, "model")
        description = bound(description, 500, "description")
        if not label.strip():
            raise ExperimentError("label required")
        fp = config_fingerprint(role, label, model, description)
        c = Candidate(id=self._new_id("cfg"), role=role, label=label,
                      model=model, description=description, fingerprint=fp,
                      created_at=self._clock())
        with self._lock:
            self._conn.execute(
                """INSERT INTO candidates(id,role,label,model,description,
                   fingerprint,created_at) VALUES(?,?,?,?,?,?,?)""",
                (c.id, c.role, c.label, c.model, c.description,
                 c.fingerprint, c.created_at))
            self._conn.commit()
            self._journal("candidate", {"id": c.id, "role": c.role,
                                        "fingerprint": c.fingerprint})
        self._log("candidate", id=c.id, role=c.role, fingerprint=c.fingerprint)
        return c

    def candidate(self, config_id: str) -> Candidate:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM candidates WHERE id=?",
                                     (config_id,))
            row = cur.fetchone()
            return self._row_candidate(row) if row else None

    def candidates(self) -> List[Candidate]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM candidates ORDER BY created_at")
            return [self._row_candidate(r) for r in cur.fetchall()]

    # ------------------------------------------------------------ experiments
    def start_experiment(self, *, champion_config_id: str,
                         challenger_config_id: str) -> Experiment:
        champ = self.candidate(champion_config_id)
        chal = self.candidate(challenger_config_id)
        if champ is None or chal is None:
            raise ExperimentError("champion/challenger candidate missing")
        if champ.fingerprint == chal.fingerprint:
            raise ExperimentError("champion and challenger are identical "
                                  "(same fingerprint)")
        with self._lock:
            cur = self._conn.execute(
                "SELECT id FROM experiments WHERE status=? "
                "AND champion_config_id=? AND challenger_config_id=? LIMIT 1",
                (ST_RUNNING, champion_config_id, challenger_config_id))
            if cur.fetchone() is not None:
                raise ExperimentError(
                    "a running experiment already exists for this pair")
            exp = Experiment(id=self._new_id("exp"),
                              champion_config_id=champion_config_id,
                              challenger_config_id=challenger_config_id,
                              status=ST_RUNNING, started_at=self._clock(),
                              decided_at=None, decision_posterior=None,
                              owner_approved=False, gate_report="")
            self._conn.execute(
                """INSERT INTO experiments(id,champion_config_id,
                   challenger_config_id,status,started_at,decided_at,
                   decision_posterior,owner_approved,gate_report)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (exp.id, exp.champion_config_id, exp.challenger_config_id,
                 exp.status, exp.started_at, exp.decided_at,
                 exp.decision_posterior, 0, ""))
            self._conn.commit()
            self._journal("experiment_start",
                          {"id": exp.id, "champion": champion_config_id,
                           "challenger": challenger_config_id})
            self._log("start", id=exp.id)
            return exp

    def experiment(self, experiment_id: str) -> Experiment:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM experiments WHERE id=?",
                                     (experiment_id,))
            row = cur.fetchone()
            return self._row_experiment(row) if row else None

    def experiments(self, *, status: str = None) -> List[Experiment]:
        sql = "SELECT * FROM experiments"
        params: List[str] = []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY started_at DESC"
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            return [self._row_experiment(r) for r in cur.fetchall()]

    # ------------------------------------------------------------ trials
    def start_trial(self, experiment_id: str, *, variant: str) -> Trial:
        if variant not in VARIANTS:
            raise ExperimentError(f"bad variant {variant!r}")
        exp = self.experiment(experiment_id)
        if exp is None:
            raise ExperimentError(f"unknown experiment {experiment_id}")
        if exp.status != ST_RUNNING:
            raise ExperimentError(
                f"experiment {experiment_id} is not running ({exp.status})")
        config_id = (exp.champion_config_id if variant == VAR_CHAMPION
                     else exp.challenger_config_id)
        t = Trial(id=self._new_id("trial"), experiment_id=experiment_id,
                  variant=variant, config_id=config_id,
                  started_at=self._clock(), ended_at=None, outcome="completed",
                  note="")
        with self._lock:
            self._conn.execute(
                """INSERT INTO trials(id,experiment_id,variant,config_id,
                   started_at,ended_at,outcome,note) VALUES(?,?,?,?,?,?,?,?)""",
                (t.id, t.experiment_id, t.variant, t.config_id, t.started_at,
                 t.ended_at, t.outcome, ""))
            self._conn.commit()
            self._journal("trial_start", {"id": t.id,
                                          "experiment": experiment_id,
                                          "variant": variant})
            self._log("trial_start", id=t.id, variant=variant)
        return t

    def trial(self, trial_id: str) -> Trial:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM trials WHERE id=?",
                                     (trial_id,))
            row = cur.fetchone()
            return self._row_trial(row) if row else None

    def finish_trial(self, trial_id: str, *, outcome: str,
                     note: str = "") -> Trial:
        if outcome not in ("completed", "failed", "cancelled"):
            raise ExperimentError(f"bad outcome {outcome!r}")
        note = bound(note, 500, "note")
        with self._lock:
            t = self.trial(trial_id)
            if t is None:
                raise ExperimentError(f"unknown trial {trial_id}")
            if t.ended_at is not None:
                raise ExperimentError(f"trial {trial_id} already finished")
            self._conn.execute(
                "UPDATE trials SET ended_at=?, outcome=?, note=? WHERE id=?",
                (self._clock(), outcome, note, trial_id))
            self._conn.commit()
            self._journal("trial_finish", {"id": trial_id, "outcome": outcome})
            self._log("trial_finish", id=trial_id, outcome=outcome)
        return self.trial(trial_id)

    # ------------------------------------------------------------ signals
    def record_signal(self, trial_id: str, *, kind: str, source: str,
                      value: float) -> Signal:
        if kind not in ("explicit", "implicit"):
            raise ExperimentError(f"bad signal kind {kind!r}")
        value = float(value)
        if not (0.0 <= value <= 1.0):
            raise ExperimentError("signal value must be in [0,1]")
        source = bound(source, 60, "source")
        t = self.trial(trial_id)
        if t is None:
            raise ExperimentError(f"unknown trial {trial_id}")
        if t.ended_at is None:
            raise ExperimentError(
                "trial is not finished; finish it before recording outcome "
                "signals")
        if t.outcome != "completed":
            raise ExperimentError("only completed trials may carry outcome signals")
        s = Signal(id=self._new_id("sig"), trial_id=trial_id, kind=kind,
                   source=source, value=value, recorded_at=self._clock())
        with self._lock:
            self._conn.execute(
                """INSERT INTO signals(id,trial_id,kind,source,value,
                   recorded_at) VALUES(?,?,?,?,?,?)""",
                (s.id, s.trial_id, s.kind, s.source, s.value, s.recorded_at))
            self._conn.commit()
            self._journal("signal", {"id": s.id, "trial": trial_id,
                                     "kind": kind, "value": value})
            self._log("signal", id=s.id, kind=kind, value=value)
        return s

    def signals_for_trial(self, trial_id: str) -> List[Signal]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM signals WHERE trial_id=? ORDER BY recorded_at",
                (trial_id,))
            return [self._row_signal(r) for r in cur.fetchall()]

    # ------------------------------------------------------------ scores
    def _completed_trials(self, experiment_id: str, config_id: str) -> List[Trial]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM trials WHERE experiment_id=? AND config_id=? "
                "AND outcome='completed' AND ended_at IS NOT NULL "
                "ORDER BY started_at",
                (experiment_id, config_id))
            return [self._row_trial(r) for r in cur.fetchall()]

    def scores_for_config(self, experiment_id: str, config_id: str) -> List[float]:
        scores = []
        for t in self._completed_trials(experiment_id, config_id):
            sigs = [(s.kind, s.value) for s in self.signals_for_trial(t.id)]
            scores.append(aggregate_scores(sigs))
        return scores
