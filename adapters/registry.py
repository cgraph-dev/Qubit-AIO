"""Loader/validator for the ``adapters/`` registry.

Groundwork only: this reads ``registry.yaml`` and returns the adapter entries.
It is deliberately NOT wired into the runtime -- the five discovery seams it
documents (skills/plugins/mcps/channels/tools) already exist and are called
directly by their owning modules. This module is the machine-readable index of
those seams, meant to be consumed by docs, audits, and future adapters.

Usage::

    from adapters.registry import load_adapters
    for a in load_adapters():
        print(a.kind, a.seam)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

try:
    import yaml
except ImportError as e:  # pragma: no cover - yaml ships with Qubit's deps
    raise ImportError("adapters/registry.py requires PyYAML") from e

REGISTRY_PATH = Path(__file__).resolve().parent / "registry.yaml"

VALID_KINDS = ("skills", "plugins", "mcps", "channels", "tools")
VALID_STATUSES = ("shipped", "planned")

_REQUIRED_FIELDS = ("name", "kind", "status", "source", "seam", "symbol")


@dataclass(frozen=True)
class Adapter:
    """One adapter entry: a kind, its source, and the discovery seam it uses."""

    name: str
    kind: str
    status: str
    source: str
    seam: str
    symbol: str

    @property
    def seam_path(self) -> str:
        """The ``path`` half of ``seam`` (``path/file.py:LINE`` -> ``path/file.py``)."""
        return self.seam.rsplit(":", 1)[0]

    @property
    def seam_line(self) -> int:
        """The ``LINE`` half of ``seam``."""
        return int(self.seam.rsplit(":", 1)[1])


def load_registry_document(path: Path | str = REGISTRY_PATH) -> Dict[str, Any]:
    """Parse registry.yaml and return the raw document. Raises on malformed YAML."""
    with open(path, "r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    return doc


def load_adapters(path: Path | str = REGISTRY_PATH) -> List[Adapter]:
    """Return the validated adapter entries from the registry.

    Validates the schema on the way through: unknown kinds/statuses and missing
    required fields raise, so a bad edit fails loud rather than silently shipping.
    """
    doc = load_registry_document(path)
    entries = doc.get("adapters")
    if not isinstance(entries, list):
        raise ValueError(f"{path}: 'adapters' must be a list")

    out: List[Adapter] = []
    for i, raw in enumerate(entries):
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: adapters[{i}] must be a mapping")
        missing = [f for f in _REQUIRED_FIELDS if f not in raw]
        if missing:
            raise ValueError(f"{path}: adapters[{i}] missing fields {missing}")
        kind = str(raw["kind"])
        status = str(raw["status"])
        if kind not in VALID_KINDS:
            raise ValueError(f"{path}: adapters[{i}] bad kind {kind!r}; want {VALID_KINDS}")
        if status not in VALID_STATUSES:
            raise ValueError(f"{path}: adapters[{i}] bad status {status!r}; want {VALID_STATUSES}")
        out.append(Adapter(
            name=str(raw["name"]), kind=kind, status=status,
            source=str(raw["source"]), seam=str(raw["seam"]), symbol=str(raw["symbol"]),
        ))
    return out


def kind_is_registered(kind: str, path: Path | str = REGISTRY_PATH) -> bool:
    """True when *kind* has at least one entry in the registry."""
    return any(a.kind == kind for a in load_adapters(path))


def adapters_for(kind: str, path: Path | str = REGISTRY_PATH) -> List[Adapter]:
    """Return shipped adapters for *kind*, preserving registry order.

    Discovery callers should use this small API rather than parsing YAML or
    assuming that the builtin adapter is the only provider.  Unknown kinds are
    treated like an empty capability set, which keeps optional integrations
    discoverable without making the UX path fail noisily.
    """
    return [a for a in load_adapters(path) if a.kind == kind and a.status == "shipped"]


def adapter_names(kind: str | None = None, path: Path | str = REGISTRY_PATH) -> List[str]:
    """Return unique shipped adapter identifiers for capability discovery."""
    adapters = load_adapters(path)
    if kind is not None:
        adapters = [a for a in adapters if a.kind == kind]
    return list(dict.fromkeys(a.name for a in adapters if a.status == "shipped"))
