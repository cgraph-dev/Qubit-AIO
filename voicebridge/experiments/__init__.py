"""Packet 8P: controlled offline evaluation (optional, disabled by default).

Public package surface. Re-exports constants, dataclasses, the pure
statistics functions, the store, and the factory so callers use
``from voicebridge.experiments import ...``. All logic lives in clearly owned
sub-components:

- ``_stats``    pure aggregate/posterior/promotion statistics (Evolve-adapted)
- ``_models``   dataclasses + bounds + secret rejection
- ``_persist``  SQLite schema + row helpers
- ``_run``      candidate/experiment/trial/signal + owner+gate promotion
- ``_transfer`` snapshot/restore
- ``store``     the composed ``ExperimentStore``
"""
from __future__ import annotations

from ._models import (EXPERIMENT_SCHEMA, EXPERIMENT_STATUSES, KIND_EXPLICIT,
                      KIND_IMPLICIT, SIGNAL_KINDS, ST_ABORTED, ST_HELD,
                      ST_PROMOTED, ST_RUNNING, VAR_CHALLENGER, VAR_CHAMPION,
                      VARIANTS, Candidate, Experiment, ExperimentError,
                      ExperimentStateError, SecretRejectedError, Signal,
                      Trial, bound, looks_secret)
from ._stats import (DEC_HOLD, DEC_NEED_MORE_DATA, DEC_PROMOTE,
                     DEFAULT_EXPLICIT_WEIGHT, DEFAULT_IMPLICIT_WEIGHT,
                     DEFAULT_MC_SAMPLES, DEFAULT_MIN_SESSIONS_PER_ARM,
                     DEFAULT_PROMOTE_THRESHOLD, PromotionDecision,
                     aggregate_scores, config_fingerprint,
                     posterior_probability, promotion_decision, wins_losses)
from .store import ExperimentStore, experiments_store

__all__ = [
    "EXPERIMENT_SCHEMA", "EXPERIMENT_STATUSES", "ST_RUNNING", "ST_PROMOTED",
    "ST_ABORTED", "ST_HELD", "KIND_EXPLICIT", "KIND_IMPLICIT", "SIGNAL_KINDS",
    "VAR_CHAMPION", "VAR_CHALLENGER", "VARIANTS",
    "DEC_NEED_MORE_DATA", "DEC_PROMOTE", "DEC_HOLD",
    "DEFAULT_EXPLICIT_WEIGHT", "DEFAULT_IMPLICIT_WEIGHT",
    "DEFAULT_MC_SAMPLES", "DEFAULT_MIN_SESSIONS_PER_ARM",
    "DEFAULT_PROMOTE_THRESHOLD",
    "ExperimentError", "ExperimentStateError", "SecretRejectedError",
    "Candidate", "Experiment", "Trial", "Signal", "PromotionDecision",
    "ExperimentStore", "experiments_store",
    "aggregate_scores", "wins_losses", "posterior_probability",
    "promotion_decision", "config_fingerprint", "bound", "looks_secret",
]
