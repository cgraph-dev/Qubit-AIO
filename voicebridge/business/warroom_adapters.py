"""War Room source-extraction adapters.

A War Room intake records *material*, not a conversation: a YouTube link, a web
page, a post, a note, an image or a document. Extraction is the seam where that
material becomes text an agent can cite.

This module deliberately ships **deterministic stubs only**. There is no
network call, no browser, no transcription service and no filesystem read of an
untrusted path. A real adapter (YouTube captions, article extraction, OCR) is an
injected implementation of :class:`SourceAdapter`; the War Room never depends on
one being present, and an absent/failed adapter is a typed refusal.

The stubs are deterministic on ``(kind, locator)`` so a test or a replay sees
the same extracted text, and so a War Room cannot silently invent web research.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable

from .errors import (
    WARROOM_ADAPTER_FAILED_CODE,
    WARROOM_ADAPTER_UNAVAILABLE_CODE,
)

SOURCE_KINDS = ("youtube", "url", "post", "note", "image", "doc")


@dataclass(frozen=True, slots=True)
class Extraction:
    """One adapter's deterministic output for a source locator."""

    text: str
    title: str = ""
    media_type: str = "text/plain"
    metadata: dict[str, Any] = field(default_factory=dict)


class SourceAdapterError(Exception):
    """A typed adapter refusal or failure.

    ``code`` is stable API; the War Room maps it straight onto a
    :class:`~voicebridge.business.errors.BusinessError`.
    """

    code = WARROOM_ADAPTER_FAILED_CODE

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class SourceAdapterUnavailable(SourceAdapterError):
    """No adapter is installed for this source kind."""

    code = WARROOM_ADAPTER_UNAVAILABLE_CODE


@runtime_checkable
class SourceAdapter(Protocol):
    """The one method the War Room calls to turn a source into text."""

    kind: str

    def extract(
        self, locator: str, *, metadata: Mapping[str, Any] | None = None
    ) -> Extraction:  # pragma: no cover - protocol definition
        ...


class StubAdapter:
    """Deterministic, network-free extraction for tests and offline use."""

    def __init__(self, kind: str, label: str, *, media_type: str = "text/plain"):
        if kind not in SOURCE_KINDS:
            raise ValueError(f"unknown source kind: {kind!r}")
        self.kind = kind
        self.label = label
        self.media_type = media_type

    def extract(
        self, locator: str, *, metadata: Mapping[str, Any] | None = None
    ) -> Extraction:
        meta = dict(metadata or {})
        supplied = meta.get("text") or meta.get("transcript") or meta.get("alt_text")
        if self.kind == "note" and not supplied:
            text = locator
        elif supplied:
            text = str(supplied)
        else:
            digest = hashlib.sha256(
                f"{self.kind}\0{locator}".encode("utf-8")
            ).hexdigest()
            text = (
                f"[{self.label} extraction — deterministic stub, no network]\n"
                f"locator: {locator}\n"
                f"digest: {digest}\n"
                "summary: material captured for the War Room; no live fetch."
            )
        title = str(meta.get("title") or "").strip()
        if not title:
            title = text.strip().splitlines()[0][:120] if text.strip() else locator[:120]
        return Extraction(
            text=text,
            title=title,
            media_type=self.media_type,
            metadata={
                **meta,
                "stub": True,
                "adapter": f"stub:{self.kind}",
            },
        )


class MissingAdapter:
    """A deliberately unavailable adapter, used to exercise typed refusals."""

    def __init__(self, kind: str = "url") -> None:
        self.kind = kind

    def extract(
        self, locator: str, *, metadata: Mapping[str, Any] | None = None
    ) -> Extraction:
        raise SourceAdapterUnavailable(
            f"no extraction adapter is installed for {self.kind!r}"
        )


def default_registry() -> dict[str, SourceAdapter]:
    """The offline adapter set. Every source kind resolves to a stub."""
    return {
        "youtube": StubAdapter("youtube", "YouTube transcript"),
        "url": StubAdapter("url", "Web page text"),
        "post": StubAdapter("post", "Post text"),
        "note": StubAdapter("note", "Owner note"),
        "image": StubAdapter("image", "Image description"),
        "doc": StubAdapter("doc", "Document text"),
    }


__all__ = [
    "Extraction",
    "MissingAdapter",
    "SOURCE_KINDS",
    "SourceAdapter",
    "SourceAdapterError",
    "SourceAdapterUnavailable",
    "StubAdapter",
    "default_registry",
]
