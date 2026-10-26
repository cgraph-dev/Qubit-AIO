"""Deterministic section results for recipes.

Sections never invent data. A section is either ``available`` with bounded
facts, or ``unavailable`` with a stable reason code and a short, redacted
detail. The runner renders both, so one missing connector degrades only that
section instead of failing the whole recipe.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Mapping, Sequence

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


class SectionUnavailable(Exception):
    """A collector's typed, non-fatal refusal."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(detail or reason)


def redact_text(value: object) -> str:
    """Best-effort secret redaction; never raises into a collector."""
    try:
        from agent.redact import redact_for_egress

        return redact_for_egress(str(value or ""))
    except Exception:
        return str(value or "")


def result_or_unavailable(section_id: str, title: str, collector: Callable,
                          now: datetime) -> SectionResult:
    """Run one collector and normalize its outcome into a SectionResult."""
    try:
        result = collector(now)
    except SectionUnavailable as exc:
        return unavailable(section_id, title, exc.reason, redact_text(exc.detail))
    except Exception as exc:  # noqa: BLE001 - one bad collector must not fail the recipe
        return unavailable(
            section_id, title, REASON_TOOL_ERROR,
            redact_text(f"{type(exc).__name__}: {exc}"))
    if not isinstance(result, SectionResult):
        return unavailable(
            section_id, title, REASON_TOOL_ERROR,
            f"collector returned {type(result).__name__}, not SectionResult")
    if result.id != section_id:
        return unavailable(
            section_id, title, REASON_TOOL_ERROR,
            f"collector returned section {result.id!r}")
    return result


def collect_sections(
    collectors: Mapping[str, Callable[[datetime], SectionResult]] | None = None,
    *,
    now: datetime | None = None,
    sections: Sequence[str],
    titles: Mapping[str, str] | None = None,
    defaults: Mapping[str, Callable[[datetime], SectionResult]] | None = None,
) -> tuple[SectionResult, ...]:
    """Collect *sections* in order; each failure degrades only its own section."""
    moment = now or datetime.now(timezone.utc)
    active = dict(defaults or {})
    active.update(collectors or {})
    labels = titles or {}
    results = []
    for section_id in sections:
        collector = active.get(section_id)
        if collector is None:
            results.append(unavailable(
                section_id, labels.get(section_id, section_id),
                REASON_MISSING_CONNECTOR, f"no collector for {section_id!r}"))
            continue
        results.append(result_or_unavailable(
            section_id, labels.get(section_id, section_id), collector, moment))
    return tuple(results)


def render_facts(sections: Sequence[SectionResult]) -> str:
    parts: list[str] = []
    for section in sections:
        parts.append(f"## {section.title}")
        if section.available:
            if section.facts:
                parts.extend(f"- {redact_text(fact)}" for fact in section.facts)
            else:
                parts.append("- (no items)")
        else:
            detail = redact_text(section.detail)
            parts.append(f"- unavailable ({section.reason}): {detail or 'no detail'}")
    return "\n".join(parts)


def render_brief(spec, sections: Sequence[SectionResult]) -> str:
    return f"# {spec.title}\n\n{render_facts(sections)}"
