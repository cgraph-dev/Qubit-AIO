"""Packet 8P controlled evaluation — SQLite persistence mixin.

Owns the versioned schema and low-level row helpers for the experiment store.
A newer-than-supported schema is a typed fail-closed error.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import List, Optional

from ._models import (EXPERIMENT_SCHEMA, Candidate, Experiment,
                      ExperimentStateError, Signal, Trial)


class PersistMixin:
    _conn: sqlite3.Connection
    _lock: threading.RLock

    def _open(self, path: Path | str) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(self._path.parent, 0o700)
        except OSError:
            pass
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    @property
    def path(self) -> Path:
        return self._path

    def _migrate(self) -> None:
        cur = self._conn.execute("PRAGMA user_version")
        ver = int(cur.fetchone()[0])
        if ver > EXPERIMENT_SCHEMA:
            raise ExperimentStateError(
                f"state schema {ver} is newer than supported "
                f"{EXPERIMENT_SCHEMA} (fail closed; do not downgrade)")
        if ver < 1:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS candidates(
                    id TEXT PRIMARY KEY,
                    role TEXT NOT NULL,
                    label TEXT NOT NULL,
                    model TEXT NOT NULL,
                    description TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS experiments(
                    id TEXT PRIMARY KEY,
                    champion_config_id TEXT NOT NULL,
                    challenger_config_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    decided_at REAL,
                    decision_posterior REAL,
                    owner_approved INTEGER NOT NULL DEFAULT 0,
                    gate_report TEXT NOT NULL,
                    CHECK(status IN ('running','promoted','aborted','held'))
                );
                CREATE TABLE IF NOT EXISTS trials(
                    id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    variant TEXT NOT NULL,
                    config_id TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    ended_at REAL,
                    outcome TEXT NOT NULL,
                    note TEXT NOT NULL,
                    CHECK(variant IN ('champion','challenger')),
                    CHECK(outcome IN ('completed','failed','cancelled'))
                );
                CREATE TABLE IF NOT EXISTS signals(
                    id TEXT PRIMARY KEY,
                    trial_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    source TEXT NOT NULL,
                    value REAL NOT NULL,
                    recorded_at REAL NOT NULL,
                    CHECK(kind IN ('explicit','implicit')),
                    CHECK(value >= 0.0 AND value <= 1.0)
                );
                CREATE INDEX IF NOT EXISTS idx_experiments_status
                    ON experiments(status);
                CREATE INDEX IF NOT EXISTS idx_trials_experiment
                    ON trials(experiment_id);
                CREATE INDEX IF NOT EXISTS idx_signals_trial
                    ON signals(trial_id);
                """
            )
            self._conn.execute(f"PRAGMA user_version={EXPERIMENT_SCHEMA}")
        self._conn.commit()

    def _row_candidate(self, row: sqlite3.Row) -> Candidate:
        return Candidate(id=str(row["id"]), role=str(row["role"]),
                         label=str(row["label"]), model=str(row["model"]),
                         description=str(row["description"]),
                         fingerprint=str(row["fingerprint"]),
                         created_at=float(row["created_at"]))

    def _row_experiment(self, row: sqlite3.Row) -> Experiment:
        return Experiment(id=str(row["id"]),
                          champion_config_id=str(row["champion_config_id"]),
                          challenger_config_id=str(row["challenger_config_id"]),
                          status=str(row["status"]),
                          started_at=float(row["started_at"]),
                          decided_at=row["decided_at"],
                          decision_posterior=row["decision_posterior"],
                          owner_approved=bool(row["owner_approved"]),
                          gate_report=str(row["gate_report"]))

    def _row_trial(self, row: sqlite3.Row) -> Trial:
        return Trial(id=str(row["id"]), experiment_id=str(row["experiment_id"]),
                     variant=str(row["variant"]), config_id=str(row["config_id"]),
                     started_at=float(row["started_at"]),
                     ended_at=row["ended_at"], outcome=str(row["outcome"]),
                     note=str(row["note"]))

    def _row_signal(self, row: sqlite3.Row) -> Signal:
        return Signal(id=str(row["id"]), trial_id=str(row["trial_id"]),
                      kind=str(row["kind"]), source=str(row["source"]),
                      value=float(row["value"]),
                      recorded_at=float(row["recorded_at"]))

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
