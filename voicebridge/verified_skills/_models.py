"""Packet 8P verified-skill memory — pure data + helpers (no I/O).

Adapted from the smallest tested concepts of Praxis (MIT (c) 2026 Kristian
Baer / Northtek; docs/archive/2026-09-21/UPSTREAM_REUSE.md R26). Holds the constants, the
`SkillProposal`/`SkillRecord` dataclasses, and the deterministic embedding,
syntactic acceptance-strength, secret detection, and utility helpers. No store
or journal code lives here.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

ST_QUARANTINED = "quarantined"
ST_VERIFIED = "verified"
ST_REFUTED = "refuted"
ST_ARCHIVED = "archived"
SKILL_STATUSES = (ST_QUARANTINED, ST_VERIFIED, ST_REFUTED, ST_ARCHIVED)

KIND_POSITIVE = "positive"
KIND_NEGATIVE = "negative"
SKILL_KINDS = (KIND_POSITIVE, KIND_NEGATIVE)

TIER_HOT = "hot"
TIER_WARM = "warm"
TIER_COLD = "cold"
SKILL_TIERS = (TIER_HOT, TIER_WARM, TIER_COLD)

# Short aliases used by lifecycle code.
ST_HOT = TIER_HOT
ST_COLD = TIER_COLD

COST_CHEAP = "cheap"
COST_NORMAL = "normal"
COST_EXPENSIVE = "expensive"
SKILL_COSTS = (COST_CHEAP, COST_NORMAL, COST_EXPENSIVE)

TRUST_USER = 3
TRUST_TOOL = 2
TRUST_OBSERVED = 1
TRUSTS = (TRUST_USER, TRUST_TOOL, TRUST_OBSERVED)

# Canonical confirmed-human principals allowed to perform owner-only
# verified-skill actions (verdict/pin/unpin/remove/restore). The Qubit CLI
# passes ``"user"``; the voice command backend and the dashboard dispatcher
# pass the channel-qualified ``"<channel>:user"`` forms. Only these exact
# strings are accepted: ``tool``/``agent``/``model``/``core:*`` actors are
# intentionally absent, so no self-verification path exists.
HUMAN_REQUESTERS = ("user", "cli:user", "voice:user", "dashboard:user",
                    "tui:user", "web:user")


def is_human_requester(requested_by: str) -> bool:
    """True only for the canonical confirmed-human actor identities."""
    return str(requested_by or "") in HUMAN_REQUESTERS

SKILL_STATE_SCHEMA = 1

DEFAULT_TOP_K = 5
DEFAULT_TOKEN_BUDGET = 6000
DEFAULT_NEGATIVE_THRESHOLD = 0.7
DEFAULT_MAX_NEGATIVES = 1
DEFAULT_HOT_CAP = 200

DEDUP_THRESHOLD = 0.92
MIN_ACCEPTANCE_STRENGTH = 1

MAX_TASK = 500
MAX_APPROACH = 2000
MAX_REASON = 2000
MAX_EVIDENCE = 1000
MAX_NAME = 120
MAX_INTERFACE = 2000
MAX_IMPLEMENTATION = 20000
MAX_ACCEPTANCE = 20000
MAX_CAPABILITIES = 16

_EMBEDDER_VERSION = "blake2b-256-v1"

_SECRET_RE = re.compile(
    r"(?i)(?:sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|AKIA[0-9A-Z]{16}|\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"
    r"|\b[a-z0-9._%+-]+:[^@\s]{6,}@|\b[0-9]{16}\b|"
    r"(?:api[_-]?key|apikey|token|secret|password|passwd|pwd|authorization|cookie)"
    r"\s*[=:]\s*\S+)")
_WORD_RE = re.compile(r"[a-z0-9]+")


class VerifiedSkillError(Exception):
    """Typed store failure."""


class VerifiedSkillStateError(VerifiedSkillError):
    """Persisted state is corrupt, invalid, or could not be written durably."""


class SecretRejectedError(VerifiedSkillError):
    """The proposed recipe contains a secret-shaped value and was rejected."""


def looks_secret(text: str) -> bool:
    return bool(text) and bool(_SECRET_RE.search(text))


def bound(value: str, limit: int, name: str) -> str:
    if not isinstance(value, str):
        raise VerifiedSkillError(f"{name} must be a string")
    if looks_secret(value):
        raise SecretRejectedError(f"{name} contains a secret-shaped value")
    return value[:limit]


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_words(text: str) -> List[str]:
    return _WORD_RE.findall((text or "").lower())


def recipe_embedding(task: str, interface: str) -> List[float]:
    """Deterministic local embedding (BLAKE2b 256-dim projection). Same
    embedder version is required before any cosine comparison."""
    words = normalize_words(f"{task} {interface}")
    vec = [0.0] * 256
    for w in words:
        d = hashlib.blake2b(w.encode("utf-8"), digest_size=32).digest()
        for i in range(32):
            vec[(d[i] + i) % 256] += 1.0
    norm = (sum(v * v for v in vec) ** 0.5) or 1.0
    return [v / norm for v in vec]


def cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def acceptance_strength(acceptance_test: str) -> int:
    """Syntactic check strength (Praxis strength.ts): counts concrete
    run(...) comparison assertions. No concrete expectation -> 0."""
    if not acceptance_test or not isinstance(acceptance_test, str):
        return 0
    score = 0
    for m in re.finditer(r"run\s*\([^)]*\)", acceptance_test):
        snippet = acceptance_test[max(0, m.start() - 80):m.end() + 80]
        if re.search(r"(==|!=|<=|>=|<|>|in|is)\s*(['\"]|\d|True|False|None|null"
                     r"|\[|\{)", snippet):
            score += 1
    if re.search(r"run\s*\([^)]*\)\s*[!=]=\s*run\s*\(", acceptance_test):
        score = max(score - 1, 0)
    return score


def recency_decay(created_at: float, now: float, half_life_days: float = 30.0
                  ) -> float:
    days = max(0.0, (now - created_at) / 86400.0)
    return math.exp(-math.log(2) * days / half_life_days)


def est_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def utility_score(record: "SkillRecord", distinct_tasks: int, now: float) -> float:
    """Utility score (Praxis utility.ts weights)."""
    rec = recency_decay(record.created_at, now)
    gen = min(float(distinct_tasks) / 5.0, 1.0)
    return (0.3 * math.log(1.0 + float(record.uses))
            + 0.35 * record.success_rate + 0.2 * rec + 0.15 * gen)


@dataclass
class SkillProposal:
    name: str
    interface: str
    implementation: str
    acceptance_test: str
    task: str
    capabilities: Tuple[str, ...] = ()
    cost: str = COST_NORMAL
    trust: int = TRUST_OBSERVED
    model: str = ""
    parents: Tuple[str, ...] = ()
    evidence: str = ""


@dataclass
class SkillRecord:
    id: str
    name: str
    interface: str
    implementation: str
    acceptance_test: str
    task: str
    capabilities: Tuple[str, ...]
    cost: str
    trust: int
    model: str
    parents: Tuple[str, ...]
    evidence: str
    embedding_version: str
    embedding: List[float]
    utility_score: float
    status: str
    kind: str
    tier: str
    uses: int
    success_rate: float
    pinned: bool
    check_strength: int
    created_at: float
    updated_at: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "interface": self.interface,
            "implementation": self.implementation,
            "acceptance_test": self.acceptance_test, "task": self.task,
            "capabilities": sorted(self.capabilities), "cost": self.cost,
            "trust": self.trust, "model": self.model,
            "parents": list(self.parents), "evidence": self.evidence,
            "embedding_version": self.embedding_version,
            "embedding": self.embedding, "utility_score": self.utility_score,
            "status": self.status, "kind": self.kind, "tier": self.tier,
            "uses": self.uses, "success_rate": self.success_rate,
            "pinned": self.pinned, "check_strength": self.check_strength,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }
