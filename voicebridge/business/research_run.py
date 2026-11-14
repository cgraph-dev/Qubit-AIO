"""Run a research provider against an existing War Room session.

This is the *action* seam the War Room UI calls. It owns no provider contract
(that lives in :mod:`voicebridge.business.research`) and no business rule (those
live in :mod:`voicebridge.business.warroom`): it composes the two.

The rules it enforces:

* **Owner intent is the caller's job.** The HTTP bridge refuses a request
  without ``owner: true`` before this module runs.
* **Intake/research only.** Sources may only be attached while the session is
  in ``intake`` or ``research``; a later state is a typed ``bad_state``.
* **Idempotent per session + query.** A completed run stamps every appended
  evidence row with a deterministic query digest. Repeating the same query
  returns the recorded rows without calling the provider again.
* **No side effects beyond sources/evidence.** It never records a decision,
  creates a task, requests an approval, publishes, spends or contacts anyone.
* **A FACT is only recorded when a returned source supports it.** A provider
  claim labelled ``FACT`` whose digest does not map to a captured source is
  refused before any evidence is written.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from . import models, warroom
from .errors import (
    BAD_INPUT_CODE,
    BAD_STATE_CODE,
    WARROOM_UNVERIFIED_FACT_CODE,
    BusinessError,
)
from .research import (
    PERPLEXITY_PROVIDER_ID,
    ResearchInput,
    ResearchProvider,
    ResearchResult,
    ResearchSource,
    resolve_provider,
)
from .warroom_adapters import SOURCE_KINDS

#: Sources can only be attached while the session is still material-gathering.
RESEARCH_ALLOWED_STATES = ("intake", "research")

_MARKER = "research_query_digest"


@dataclass(frozen=True, slots=True)
class ResearchRunResult:
    """What a run recorded (or replayed)."""

    provider: str
    query: str
    query_digest: str
    created: bool
    sources: tuple[models.WarRoomSource, ...]
    evidence: tuple[models.WarRoomEvidence, ...]

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(item.source_id for item in self.sources)

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(item.evidence_id for item in self.evidence)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "query": self.query,
            "query_digest": self.query_digest,
            "created": self.created,
            "source_ids": list(self.source_ids),
            "evidence_ids": list(self.evidence_ids),
            "sources": self.sources,
            "evidence": self.evidence,
        }


def _clean_query(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise BusinessError(BAD_INPUT_CODE, "query must be a string")
    clean = value.strip()
    if len(clean) > 2000:
        raise BusinessError(
            BAD_INPUT_CODE, "query is longer than 2000 characters"
        )
    return clean


def _normalise_inputs(raw: Any) -> list[ResearchInput]:
    if raw is None:
        return []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise BusinessError(BAD_INPUT_CODE, "inputs must be a list")
    inputs: list[ResearchInput] = []
    for item in raw:
        if isinstance(item, str):
            inputs.append(ResearchInput(kind="note", locator=item))
            continue
        if not isinstance(item, Mapping):
            raise BusinessError(
                BAD_INPUT_CODE, "each input must be a string or a mapping"
            )
        metadata = item.get("metadata")
        if metadata is not None and not isinstance(metadata, Mapping):
            raise BusinessError(
                BAD_INPUT_CODE, "input metadata must be a mapping"
            )
        inputs.append(
            ResearchInput(
                kind=str(item.get("kind") or "note"),
                locator=str(item.get("locator") or ""),
                title=str(item.get("title") or ""),
                metadata=dict(metadata or {}),
            )
        )
    return inputs


def query_digest(
    session_id: str, query: str, inputs: Sequence[ResearchInput]
) -> str:
    material = {
        "session_id": session_id,
        "query": query,
        "inputs": [
            [item.kind, item.locator, item.title] for item in inputs
        ],
    }
    encoded = json.dumps(
        material, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _existing(
    rows: Sequence[models.WarRoomEvidence], digest: str, provider: str
) -> bool:
    for row in rows:
        meta = row.metadata or {}
        if (
            meta.get(_MARKER) == digest
            and str(meta.get("research_provider") or "") == provider
        ):
            return True
    return False


def _rows_for_run(
    store: Any, tenant_id: str, session_id: str, digest: str
) -> tuple[tuple[models.WarRoomSource, ...], tuple[models.WarRoomEvidence, ...]]:
    sources = tuple(
        item
        for item in warroom.list_sources(store, tenant_id, session_id)
        if (item.metadata or {}).get(_MARKER) == digest
    )
    evidence = tuple(
        item
        for item in warroom.list_evidence(store, tenant_id, session_id)
        if (item.metadata or {}).get(_MARKER) == digest
    )
    return sources, evidence


def _validate_claims(result: ResearchResult) -> dict[str, ResearchSource]:
    """Refuse a FACT the provider did not back with a returned source."""
    by_digest = {source.digest: source for source in result.sources}
    for claim in result.claims:
        if claim.label != "FACT":
            continue
        if not any(digest in by_digest for digest in claim.source_digests):
            raise BusinessError(
                WARROOM_UNVERIFIED_FACT_CODE,
                "the research provider labelled a claim FACT without a"
                " returned source URL to support it",
            )
    return by_digest


def run_research(
    store: Any,
    *,
    tenant_id: str,
    session_id: str,
    query: str = "",
    inputs: Any = None,
    provider: ResearchProvider | str | None = None,
    cfg: Any = None,
    transport: Any = None,
    environ: Mapping[str, str] | None = None,
) -> ResearchRunResult:
    """Run research and append real sources/evidence to one session.

    ``provider`` may be a provider instance (service/test use) or a name
    (``perplexity`` by default when the caller names one). No provider is
    called twice for the same ``(session, query, inputs)`` digest.
    """
    session = warroom.get_session(store, tenant_id, session_id)
    if session.state not in RESEARCH_ALLOWED_STATES:
        raise BusinessError(
            BAD_STATE_CODE,
            "research runs during intake or research"
            f" (state={session.state!r})",
        )

    clean_query = _clean_query(query) or session.title
    clean_inputs = _normalise_inputs(inputs)
    if not clean_inputs:
        clean_inputs = [
            ResearchInput(
                kind="note",
                locator=clean_query,
                title=session.title,
                metadata={"query": True, "text": clean_query},
            )
        ]
    elif clean_query and not any(
        dict(item.metadata or {}).get("query") for item in clean_inputs
    ):
        clean_inputs.insert(
            0,
            ResearchInput(
                kind="note",
                locator=clean_query,
                title="Research question",
                metadata={"query": True, "text": clean_query},
            ),
        )

    if isinstance(provider, str) or provider is None:
        resolved = resolve_provider(
            provider or PERPLEXITY_PROVIDER_ID,
            cfg=cfg,
            transport=transport,
            environ=environ,
        )
    else:
        resolved = provider
    provider_id = str(getattr(resolved, "provider_id", "") or "unknown")

    digest = query_digest(session_id, clean_query, clean_inputs)
    existing = warroom.list_evidence(store, tenant_id, session_id)
    if _existing(existing, digest, provider_id):
        sources, evidence = _rows_for_run(
            store, tenant_id, session_id, digest
        )
        return ResearchRunResult(
            provider=provider_id,
            query=clean_query,
            query_digest=digest,
            created=False,
            sources=sources,
            evidence=evidence,
        )

    result = resolved.research(clean_inputs)
    _validate_claims(result)

    digest_to_source: dict[str, str] = {}
    for source in result.sources:
        kind = source.kind if source.kind in SOURCE_KINDS else "url"
        row, _created = warroom.add_source(
            store,
            tenant_id=tenant_id,
            session_id=session_id,
            kind=kind,
            locator=source.locator,
            title=source.title,
            metadata={
                "text": source.text,
                "research_provider": provider_id,
                "research_query": clean_query[:1000],
                _MARKER: digest,
                "source_digest": source.digest,
            },
        )
        if row.status != "extracted":
            row = warroom.extract_source(
                store,
                tenant_id=tenant_id,
                session_id=session_id,
                source_id=row.source_id,
            )
        digest_to_source[source.digest] = row.source_id

    for claim in result.claims:
        source_id = next(
            (
                digest_to_source[digest]
                for digest in claim.source_digests
                if digest in digest_to_source
            ),
            None,
        )
        if claim.label == "FACT" and source_id is None:
            raise BusinessError(
                WARROOM_UNVERIFIED_FACT_CODE,
                "a FACT claim has no captured source in this session",
            )
        row, _created = warroom.add_evidence(
            store,
            tenant_id=tenant_id,
            session_id=session_id,
            label=claim.label,
            claim=claim.claim,
            source_id=source_id,
            metadata={
                "research_provider": provider_id,
                "research_query": clean_query[:1000],
                _MARKER: digest,
                "source_digests": list(claim.source_digests),
                "rationale": claim.rationale,
            },
        )

    sources, evidence = _rows_for_run(store, tenant_id, session_id, digest)
    return ResearchRunResult(
        provider=provider_id,
        query=clean_query,
        query_digest=digest,
        created=True,
        sources=sources,
        evidence=evidence,
    )


__all__ = [
    "RESEARCH_ALLOWED_STATES",
    "ResearchRunResult",
    "query_digest",
    "run_research",
]
