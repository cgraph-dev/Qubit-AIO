"""Packet 8P controlled evaluation — data + validation (no I/O).

Holds the dataclasses (Candidate, Trial, Signal, Experiment) and the
bounds/secret helpers. A trial stores only a bounded redacted label plus a
stable fingerprint and outcome metrics — never raw private prompts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

MAX_LABEL = 200
MAX_DESCRIPTION = 500
MAX_NOTE = 500

EXPERIMENT_SCHEMA = 1

KIND_EXPLICIT = "explicit"
KIND_IMPLICIT = "implicit"
SIGNAL_KINDS = (KIND_EXPLICIT, KIND_IMPLICIT)

ST_RUNNING = "running"
ST_PROMOTED = "promoted"
ST_ABORTED = "aborted"
ST_HELD = "held"
EXPERIMENT_STATUSES = (ST_RUNNING, ST_PROMOTED, ST_ABORTED, ST_HELD)

VAR_CHAMPION = "champion"
VAR_CHALLENGER = "challenger"
VARIANTS = (VAR_CHAMPION, VAR_CHALLENGER)

_SECRET_RE = re.compile(
    r"(?i)(?:sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|AKIA[0-9A-Z]{16}|\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"
    r"|\b[a-z0-9._%+-]+:[^@\s]{6,}@|\b[0-9]{16}\b|"
    r"(?:api[_-]?key|apikey|token|secret|password|passwd|pwd|authorization|cookie)"
    r"\s*[=:]\s*\S+)")


class ExperimentError(Exception):
    """Typed experiment-store failure."""


class ExperimentStateError(ExperimentError):
    """Persisted state is corrupt, invalid, or could not be written durably."""


class SecretRejectedError(ExperimentError):
    """A stored field contains a secret-shaped value and was rejected."""


def looks_secret(text: str) -> bool:
    return bool(text) and bool(_SECRET_RE.search(text))


def bound(value: str, limit: int, name: str) -> str:
    if not isinstance(value, str):
        raise ExperimentError(f"{name} must be a string")
    if looks_secret(value):
        raise SecretRejectedError(f"{name} contains a secret-shaped value")
    return value[:limit]


@dataclass
class Candidate:
    id: str
    role: str
    label: str
    model: str
    description: str
    fingerprint: str
    created_at: float

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "role": self.role, "label": self.label,
                "model": self.model, "description": self.description,
                "fingerprint": self.fingerprint, "created_at": self.created_at}


@dataclass
class Trial:
    id: str
    experiment_id: str
    variant: str
    config_id: str
    started_at: float
    ended_at: Optional[float]
    outcome: str
    note: str

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "experiment_id": self.experiment_id,
                "variant": self.variant, "config_id": self.config_id,
                "started_at": self.started_at, "ended_at": self.ended_at,
                "outcome": self.outcome, "note": self.note}


@dataclass
class Signal:
    id: str
    trial_id: str
    kind: str
    source: str
    value: float
    recorded_at: float

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "trial_id": self.trial_id, "kind": self.kind,
                "source": self.source, "value": self.value,
                "recorded_at": self.recorded_at}


@dataclass
class Experiment:
    id: str
    champion_config_id: str
    challenger_config_id: str
    status: str
    started_at: float
    decided_at: Optional[float]
    decision_posterior: Optional[float]
    owner_approved: bool
    gate_report: str

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "champion_config_id": self.champion_config_id,
                "challenger_config_id": self.challenger_config_id,
                "status": self.status, "started_at": self.started_at,
                "decided_at": self.decided_at,
                "decision_posterior": self.decision_posterior,
                "owner_approved": self.owner_approved,
                "gate_report": self.gate_report}
