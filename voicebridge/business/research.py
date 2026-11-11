"""War Room research-provider seam.

A War Room intake records *material*; research is the step that turns that
material into labelled claims. This module owns the provider contract, a
deterministic offline implementation, and an explicit seam for a real provider
that is **disabled by default**.

Rules the module enforces rather than documents:

* **No invented facts.** A ``FACT`` claim must name at least one captured
  source digest. The local provider only ever calls the *capture* of supplied
  material a fact; everything about demand, fit or the future is
  ``INFERENCE``, ``ASSUMPTION`` or ``UNKNOWN``.
* **No hidden network.** :class:`LocalResearchProvider` is pure and
  deterministic. :class:`RemoteResearchProvider` refuses with a typed
  ``BUSINESS_RESEARCH_NOT_CONFIGURED`` until a caller injects both an explicit
  transport and ``enabled=True``; shipping code never does.
* **Typed refusals.** An unknown provider or source kind is
  ``BUSINESS_RESEARCH_UNAVAILABLE``; a disabled/unconfigured real seam is
  ``BUSINESS_RESEARCH_NOT_CONFIGURED``. Neither is an empty success.

The interface is deliberately small so a future YouTube/article/OCR provider
can be plugged in without changing the War Room service.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from .errors import (
    BAD_INPUT_CODE,
    RESEARCH_NOT_CONFIGURED_CODE,
    RESEARCH_UNAVAILABLE_CODE,
    BusinessError,
)
from .warroom import EVIDENCE_LABELS

#: The material shapes a research provider accepts. These are the War Room's
#: own source kinds (image/doc are extraction-only inputs, not research
#: inputs, until a provider exists that can reason about them).
RESEARCH_KINDS = ("url", "youtube", "note", "post")

LOCAL_PROVIDER_ID = "local-deterministic"
REMOTE_PROVIDER_ID = "remote"


def _clean_text(value: Any, label: str, *, max_len: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BusinessError(BAD_INPUT_CODE, f"{label} must be a non-empty string")
    clean = value.strip()
    if len(clean) > max_len:
        raise BusinessError(
            BAD_INPUT_CODE, f"{label} is longer than {max_len} characters"
        )
    return clean


def _clean_kind(value: Any) -> str:
    kind = _clean_text(value, "research input kind", max_len=20).lower()
    if kind not in RESEARCH_KINDS:
        raise BusinessError(
            RESEARCH_UNAVAILABLE_CODE,
            f"no research provider supports {kind!r}; expected one of "
            f"{', '.join(RESEARCH_KINDS)}",
        )
    return kind


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ResearchInput:
    """One piece of material handed to a provider."""

    kind: str
    locator: str
    title: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def normalised(self) -> "ResearchInput":
        return ResearchInput(
            kind=_clean_kind(self.kind),
            locator=_clean_text(self.locator, "research locator", max_len=2048),
            title=str(self.title or "").strip()[:300],
            metadata=dict(self.metadata or {}),
        )


@dataclass(frozen=True, slots=True)
class ResearchSource:
    """The provider's deterministic capture of one input."""

    kind: str
    locator: str
    title: str
    text: str
    digest: str
    adapter: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "locator": self.locator,
            "title": self.title,
            "digest": self.digest,
            "adapter": self.adapter,
            "chars": len(self.text),
        }


@dataclass(frozen=True, slots=True)
class ResearchClaim:
    """One labelled claim. A FACT must cite at least one source digest."""

    label: str
    claim: str
    source_digests: tuple[str, ...] = ()
    rationale: str = ""

    def __post_init__(self) -> None:
        label = str(self.label).upper()
        if label not in EVIDENCE_LABELS:
            raise BusinessError(
                BAD_INPUT_CODE,
                f"unknown research label {label!r}; expected one of "
                f"{', '.join(EVIDENCE_LABELS)}",
            )
        if label == "FACT" and not self.source_digests:
            raise BusinessError(
                "BUSINESS_WARROOM_UNVERIFIED_FACT",
                "a research FACT must cite at least one captured source",
            )
        if not str(self.claim).strip():
            raise BusinessError(BAD_INPUT_CODE, "research claim must be non-empty")

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "claim": self.claim,
            "source_digests": list(self.source_digests),
            "rationale": self.rationale,
        }


@dataclass(frozen=True, slots=True)
class ResearchResult:
    """What a provider returns: captured sources plus labelled claims."""

    provider: str
    inputs: tuple[ResearchInput, ...]
    sources: tuple[ResearchSource, ...]
    claims: tuple[ResearchClaim, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "inputs": [
                {"kind": item.kind, "locator": item.locator, "title": item.title}
                for item in self.inputs
            ],
            "sources": [item.as_dict() for item in self.sources],
            "claims": [item.as_dict() for item in self.claims],
            "labels": sorted({item.label for item in self.claims}),
        }


@runtime_checkable
class ResearchProvider(Protocol):
    """The contract a research backend implements."""

    provider_id: str

    def research(self, inputs: list[ResearchInput]) -> ResearchResult:
        ...  # pragma: no cover - protocol definition


class LocalResearchProvider:
    """Deterministic, offline, network-free research.

    It captures the supplied material (a note's text, a transcript supplied in
    metadata, or a deterministic locator digest) and separates what is known
    from what is merely assumed. It never fetches the locator.
    """

    provider_id = LOCAL_PROVIDER_ID

    def research(self, inputs: list[ResearchInput]) -> ResearchResult:
        clean_inputs = tuple(item.normalised() for item in inputs)
        sources: list[ResearchSource] = []
        claims: list[ResearchClaim] = []
        for item in clean_inputs:
            text = _capture_text(item)
            digest = _digest(text)
            title = item.title or _first_line(text) or item.locator[:120]
            sources.append(
                ResearchSource(
                    kind=item.kind,
                    locator=item.locator,
                    title=title,
                    text=text,
                    digest=digest,
                    adapter=LOCAL_PROVIDER_ID,
                )
            )
            claims.extend(_claims_for(item, digest))
        return ResearchResult(
            provider=self.provider_id,
            inputs=clean_inputs,
            sources=tuple(sources),
            claims=tuple(claims),
        )


class RemoteResearchProvider:
    """The optional real-provider seam, disabled by default.

    Constructing this provider performs no I/O. ``research`` refuses with
    ``BUSINESS_RESEARCH_NOT_CONFIGURED`` unless the caller explicitly passes
    ``enabled=True`` *and* a transport callable. The transport is injected so
    no product code path and no test can reach the network implicitly.
    """

    provider_id = REMOTE_PROVIDER_ID

    def __init__(
        self,
        *,
        enabled: bool = False,
        transport: Callable[[list[ResearchInput]], ResearchResult] | None = None,
        endpoint: str = "",
    ) -> None:
        self.enabled = bool(enabled)
        self._transport = transport
        self.endpoint = endpoint

    def research(self, inputs: list[ResearchInput]) -> ResearchResult:
        clean_inputs = [item.normalised() for item in inputs]
        if not self.enabled or self._transport is None:
            raise BusinessError(
                RESEARCH_NOT_CONFIGURED_CODE,
                "the remote research provider is disabled by default; configure "
                "an explicit transport and enable it before use",
            )
        try:
            result = self._transport(clean_inputs)
        except BusinessError:
            raise
        except Exception as exc:  # noqa: BLE001 - external seam
            raise BusinessError(
                RESEARCH_UNAVAILABLE_CODE,
                f"the remote research provider failed: "
                f"{type(exc).__name__}: {str(exc)[:200]}",
            ) from exc
        return result


class UnavailableResearchProvider:
    """A provider that is deliberately absent, for typed-refusal paths."""

    provider_id = "unavailable"

    def research(self, inputs: list[ResearchInput]) -> ResearchResult:
        raise BusinessError(
            RESEARCH_UNAVAILABLE_CODE,
            "no research provider is installed for this War Room",
        )


def resolve_provider(name: str | None = None) -> ResearchProvider:
    """Resolve a provider by name. The local deterministic adapter is default.

    ``remote`` resolves to the disabled seam (a typed ``not_configured``
    refusal); anything unknown is a typed ``unavailable`` refusal. No name
    reads an environment variable or performs I/O.
    """
    requested = (name or LOCAL_PROVIDER_ID).strip().lower()
    if requested in ("", LOCAL_PROVIDER_ID, "local"):
        return LocalResearchProvider()
    if requested in (REMOTE_PROVIDER_ID, "real"):
        return RemoteResearchProvider()
    raise BusinessError(
        RESEARCH_UNAVAILABLE_CODE,
        f"unknown research provider {name!r}; expected "
        f"{LOCAL_PROVIDER_ID!r} or {REMOTE_PROVIDER_ID!r}",
    )


def _capture_text(item: ResearchInput) -> str:
    meta = dict(item.metadata or {})
    supplied = meta.get("text") or meta.get("transcript") or meta.get("alt_text")
    if supplied is not None and str(supplied).strip():
        return str(supplied).strip()
    if item.kind == "note":
        return item.locator
    digest = _digest(f"{item.kind}\0{item.locator}")
    return (
        f"[{item.kind} research capture — deterministic local adapter, no fetch]\n"
        f"locator: {item.locator}\n"
        f"digest: {digest}\n"
        "The locator was recorded but not fetched; its content is unknown."
    )


def _first_line(text: str) -> str:
    for line in text.splitlines():
        clean = line.strip()
        if clean:
            return clean[:120]
    return ""


def _claims_for(item: ResearchInput, digest: str) -> list[ResearchClaim]:
    subject = item.title or item.locator
    return [
        ResearchClaim(
            label="FACT",
            claim=(
                f"Captured {item.kind} material for {subject!r} "
                f"(sha256 {digest})."
            ),
            source_digests=(digest,),
            rationale="the capture itself is recorded in this session",
        ),
        ResearchClaim(
            label="INFERENCE",
            claim=(
                f"If the captured {item.kind} material is current, it may inform "
                "the session hypothesis; the local adapter does not verify it."
            ),
            source_digests=(digest,),
            rationale="derived from the capture, not independently checked",
        ),
        ResearchClaim(
            label="ASSUMPTION",
            claim=(
                f"The captured {item.kind} material is relevant, representative "
                "and current for the target customer."
            ),
            rationale="untested assumption; the owner must confirm it",
        ),
        ResearchClaim(
            label="UNKNOWN",
            claim=(
                "Whether this material generalises to the target market, and "
                "what it implies for demand, price or effort, is unverified."
            ),
            rationale="no live research was performed",
        ),
    ]


__all__ = [
    "LOCAL_PROVIDER_ID",
    "REMOTE_PROVIDER_ID",
    "RESEARCH_KINDS",
    "LocalResearchProvider",
    "RemoteResearchProvider",
    "ResearchClaim",
    "ResearchInput",
    "ResearchProvider",
    "ResearchResult",
    "ResearchSource",
    "UnavailableResearchProvider",
    "resolve_provider",
]
