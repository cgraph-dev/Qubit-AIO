"""Packet 8Q — versioned provider/model catalog.

Catalogs are snapshots with source + timestamp. Stale or unknown entries fail
closed. No hardcoded model list is treated as permanent; the local default is
always available without a catalog.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

CATALOG_TTL_S = 24 * 3600.0
CATALOG_VERSION = "8q/1"


@dataclass(frozen=True)
class ModelEntry:
    provider: str
    model: str
    context_tokens: int = 8192
    max_output_tokens: int = 1024
    supports_tools: bool = False
    supports_structured: bool = False
    supports_streaming: bool = True
    price_in_per_1k: float = 0.0
    price_out_per_1k: float = 0.0
    terms_note: str = ""  # retention/terms pointer, never a promise


@dataclass
class ProviderCatalog:
    version: str = CATALOG_VERSION
    source: str = "local-default"
    fetched_at: float = 0.0
    models: Dict[str, ModelEntry] = field(default_factory=dict)  # "provider/model"

    def key(self, provider: str, model: str) -> str:
        return f"{provider}/{model}"

    def lookup(self, provider: str, model: str) -> Optional[ModelEntry]:
        return self.models.get(self.key(provider, model))

    def is_fresh(self, now: Optional[float] = None) -> bool:
        if not self.models:
            return False
        now = time.time() if now is None else now
        return (now - self.fetched_at) <= CATALOG_TTL_S

    def validate(self, provider: str, model: str,
                 now: Optional[float] = None) -> tuple[bool, str]:
        """Returns (ok, code). Unknown -> UNKNOWN_MODEL, stale -> STALE_CATALOG."""
        if self.lookup(provider, model) is None:
            return False, "UNKNOWN_MODEL"
        if not self.is_fresh(now=now):
            return False, "STALE_CATALOG"
        return True, ""

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "source": self.source,
            "fetched_at": self.fetched_at,
            "models": [
                {"provider": m.provider, "model": m.model,
                 "context_tokens": m.context_tokens,
                 "max_output_tokens": m.max_output_tokens,
                 "supports_tools": m.supports_tools,
                 "supports_structured": m.supports_structured,
                 "supports_streaming": m.supports_streaming}
                for m in self.models.values()
            ],
        }


def empty_catalog(source: str = "local-default") -> ProviderCatalog:
    return ProviderCatalog(source=source, fetched_at=time.time(), models={})


def build_catalog(entries: List[ModelEntry], source: str,
                  fetched_at: Optional[float] = None) -> ProviderCatalog:
    cat = ProviderCatalog(source=source,
                          fetched_at=time.time() if fetched_at is None else fetched_at)
    for e in entries:
        cat.models[cat.key(e.provider, e.model)] = e
    return cat


def catalog_from_allowlist(allowlist: List[str]) -> ProviderCatalog:
    """Build a fresh operator-allowlist snapshot for plain-chat routing.

    Entries carry conservative neutral capabilities (no tools/structured
    claims) so capability-gated requests still fail closed unless a richer
    catalog is supplied. Source and timestamp are recorded; staleness rules
    apply exactly as for any other snapshot.
    """
    entries = []
    for item in allowlist or []:
        if "/" not in str(item):
            continue
        provider, model = str(item).split("/", 1)
        provider, model = provider.strip(), model.strip()
        if provider and model:
            entries.append(ModelEntry(provider=provider, model=model))
    return build_catalog(entries, source="operator-allowlist")
