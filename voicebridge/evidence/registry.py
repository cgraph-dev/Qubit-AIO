"""Packet 5A: adapter registry with strict read-only enforcement.

A read-only adapter is a Python object whose ONLY declared callable surface
is read operations; write/remember is structurally absent. The registry
validates each adapter's contract at registration and refuses any read-only
adapter that exposes write-like methods or declares write/remember intent.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .models import (EvidenceError, EvidencePermissionError, EvidenceRecord,
                     AdapterContract)


class EvidenceRegistry:
    def __init__(self) -> None:
        self._adapters: Dict[str, Tuple[AdapterContract, Any]] = {}

    def register(self, contract: AdapterContract, adapter: Any) -> None:
        if contract.name in self._adapters:
            raise EvidenceError(f"adapter already registered: {contract.name}")
        if contract.read_only:
            self._assert_no_write_surface(contract, adapter)
        self._adapters[contract.name] = (contract, adapter)

    @staticmethod
    def _assert_no_write_surface(contract: AdapterContract, adapter: Any) -> None:
        """Fail closed: a read-only adapter must not expose write/remember."""
        forbidden = ("write", "send", "submit", "post", "put", "delete",
                     "remove", "create", "update", "navigate", "click",
                     "upload", "download_to", "remember", "promote",
                     "mutate", "archive", "mark_read")
        for name in dir(adapter):
            if name.startswith("_"):
                continue
            low = name.lower()
            if any(f in low for f in forbidden):
                raise EvidenceError(
                    f"{contract.name}: read_only adapter exposes mutating "
                    f"member {name!r}")

    def get(self, name: str) -> Tuple[AdapterContract, Any]:
        try:
            return self._adapters[name]
        except KeyError as exc:
            raise EvidenceError(f"unknown adapter: {name}") from exc

    def list(self) -> List[str]:
        return sorted(self._adapters)

    def fetch(self, name: str, *args, **kwargs) -> EvidenceRecord:
        """The ONLY read path. Any attempt to reach a non-read surface fails
        closed inside each adapter (see ReadOnlyAdapter mixin)."""
        contract, adapter = self.get(name)
        if not contract.read_only:
            raise EvidencePermissionError(
                f"{name} is not a read-only adapter; use a gated write path")
        if not hasattr(adapter, "fetch"):
            raise EvidenceError(f"{name}: read adapter missing fetch()")
        return adapter.fetch(*args, **kwargs)