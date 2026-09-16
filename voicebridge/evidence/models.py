"""Packet 5A: normalized read-only evidence + adapter contracts.

One EvidenceRecord shape for every source (files, repos, browser, inbox,
reports, web, worker/agent, memory), and an AdapterContract that declares
permissions (read/write/remember), scope, sensitivity, trust, freshness and
citation rules. Read-only adapters are enforced structurally: they expose NO
write/remember surface, and the registry refuses to register a read-only
adapter that declares write/remember intent (fail closed).
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Sensitivity(Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    PERSONAL = "personal"
    SECRET = "secret"


class Trust(Enum):
    LOW = "low"        # untrusted external (web pages, email text, reports)
    MEDIUM = "medium"  # semi-trusted (repo docs, file contents)
    HIGH = "high"      # verified local (user-edited config, first-party)


class Scope(Enum):
    FILES = "files"
    REPOS = "repos"
    BROWSER = "browser"
    INBOX = "inbox"
    REPORTS = "reports"
    WEB = "web"
    WORKER = "worker"          # Qubit worker lane
    MEMORY = "memory"


class EvidenceError(Exception):
    """Typed failure base for the evidence layer."""


class EvidencePermissionError(EvidenceError):
    """A read-only adapter was asked to do something mutating."""


class EvidencePartialError(EvidenceError):
    """Adapter returned partial results; carries typed reason."""


class EvidenceUnavailableError(EvidenceError):
    """Source unavailable (network down, missing file, auth required)."""


@dataclass(frozen=True)
class EvidenceRecord:
    """Stable normalized evidence record. Immutable once created."""
    evidence_id: str
    adapter: str
    adapter_version: str
    source_ref: str
    scope: Scope
    sensitivity: Sensitivity
    trust: Trust
    captured_at_epoch: float
    freshness_ttl_s: Optional[float]
    permission: str = "read"
    content_hash: str = ""
    redacted_excerpt: str = ""
    confidence: float = 0.0
    citations: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, *, adapter: str, adapter_version: str, source_ref: str,
               scope: Scope, sensitivity: Sensitivity, trust: Trust,
               excerpt: str, citations: Optional[List[str]] = None,
               freshness_ttl_s: Optional[float] = None,
               confidence: float = 0.0,
               meta: Optional[Dict[str, Any]] = None,
               raw_text: str = "",
               secret_patterns: Optional[List[str]] = None,
               captured_at_epoch: Optional[float] = None,
               ) -> "EvidenceRecord":
        """Build a record with a STABLE deterministic ID + content hash.

        evidence_id is derived from adapter|scope|source_ref|hash so the same
        source material always yields the same ID (idempotent, replay-safe).
        Redaction applies secret_patterns to the stored excerpt only; the
        raw_text is never stored.
        """
        raw_text = raw_text if raw_text else excerpt
        content_hash = hashlib.sha256(raw_text.encode("utf-8", "replace")).hexdigest()
        id_source = f"{adapter}|{scope.value}|{source_ref}|{content_hash}"
        evidence_id = hashlib.sha256(id_source.encode()).hexdigest()[:32]
        redacted = excerpt
        for pat in secret_patterns or []:
            redacted = redacted.replace(pat, "[REDACTED]")
        captured = captured_at_epoch if captured_at_epoch is not None \
            else time.time()
        return cls(
            evidence_id=evidence_id, adapter=adapter,
            adapter_version=adapter_version, source_ref=source_ref,
            scope=scope, sensitivity=sensitivity, trust=trust,
            captured_at_epoch=captured,
            freshness_ttl_s=freshness_ttl_s,
            content_hash=content_hash, redacted_excerpt=redacted[:2000],
            confidence=round(float(confidence), 4),
            citations=list(citations or []),
            meta=dict(meta or {}))

    def is_fresh(self, now_epoch: Optional[float] = None) -> bool:
        if self.freshness_ttl_s is None:
            return True
        return (now_epoch if now_epoch is not None else time.time()) - \
            self.captured_at_epoch <= self.freshness_ttl_s


@dataclass(frozen=True)
class AdapterContract:
    """Declared contract every adapter must satisfy."""
    name: str
    version: str
    scope: Scope
    sensitivity: Sensitivity
    permissions: frozenset        # subset of {"read", "write", "remember"}
    read_only: bool
    freshness_ttl_s: Optional[float]
    trust: Trust
    citation_policy: str = "cite source_ref on every record"
    capabilities: tuple = ()     # declared read-only operations

    def __post_init__(self):
        if not self.permissions:
            object.__setattr__(self, "permissions", frozenset({"read"}))
        allowed = {"read", "write", "remember"}
        if not self.permissions <= allowed:
            raise EvidenceError(
                f"{self.name}: undeclared permission "
                f"{sorted(self.permissions - allowed)}")
        if self.read_only and (self.permissions - {"read"}):
            raise EvidenceError(
                f"{self.name}: read_only adapter cannot declare "
                f"write/remember ({sorted(self.permissions)})")