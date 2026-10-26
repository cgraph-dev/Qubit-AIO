"""Deterministic section results for recipes.

Sections never invent data. A section is either ``available`` with bounded
facts, or ``unavailable`` with a stable reason code and a short, redacted
detail. The runner renders both, so one missing connector degrades only that
section instead of failing the whole recipe.
"""
from __future__ import annotations

from dataclasses import dataclass, field

AVAILABLE = "available"
UNAVAILABLE = "unavailable"
STATUSES = (AVAILABLE, UNAVAILABLE)

# Stable machine-readable reasons. A UI/voice path may show them verbatim
# because they name the gap, not a secret.
REASON_MISSING_CONNECTOR = "missing_connector"
REASON_MISSING_CREDENTIAL = "missing_credential"
REASON_CONNECTOR_NOT_CONFIGURED = "connector_not_configured"
REASON_TOOL_ERROR = "tool_error"
REASON_NOT_SUPPORTED = "not_supported"

_MAX_FACT_CHARS = 500
_MAX_FACTS = 20
_MAX_DETAIL_CHARS = 400


@dataclass(frozen=True)
class SectionResult:
    id: str
    title: str
    status: str
    facts: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""
    detail: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"invalid section status {self.status!r}")
        if self.status == UNAVAILABLE and not self.reason:
            raise ValueError(f"unavailable section {self.id!r} needs a reason code")
        if self.status == AVAILABLE and self.reason:
            raise ValueError(f"available section {self.id!r} cannot carry a reason code")
        if len(self.facts) > _MAX_FACTS:
            raise ValueError(f"section {self.id!r} has too many facts")
        for fact in self.facts:
            if len(str(fact)) > _MAX_FACT_CHARS:
                raise ValueError(f"section {self.id!r} fact exceeds {_MAX_FACT_CHARS} chars")
        if len(str(self.detail)) > _MAX_DETAIL_CHARS:
            raise ValueError(f"section {self.id!r} detail exceeds {_MAX_DETAIL_CHARS} chars")

    @property
    def available(self) -> bool:
        return self.status == AVAILABLE

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "facts": list(self.facts),
            "reason": self.reason,
            "detail": self.detail,
            "source": self.source,
        }


def available(section_id: str, title: str, facts: list[str] | tuple[str, ...] = (),
              *, detail: str = "", source: str = "") -> SectionResult:
    return SectionResult(
        id=section_id, title=title, status=AVAILABLE,
        facts=tuple(facts), detail=detail, source=source)


def unavailable(section_id: str, title: str, reason: str, detail: str = "",
                *, source: str = "") -> SectionResult:
    return SectionResult(
        id=section_id, title=title, status=UNAVAILABLE,
        reason=reason, detail=detail, source=source)
