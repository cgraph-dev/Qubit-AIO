"""Packet 5A evidence layer."""
from typing import Optional

from .models import (AdapterContract, EvidenceError, EvidencePartialError,
                     EvidencePermissionError, EvidenceRecord,
                     EvidenceUnavailableError, Scope, Sensitivity, Trust)
from .registry import EvidenceRegistry

__all__ = [
    "AdapterContract", "EvidenceError", "EvidencePartialError",
    "EvidencePermissionError", "EvidenceRecord", "EvidenceRegistry",
    "EvidenceUnavailableError", "Scope", "Sensitivity", "Trust",
]


def build_evidence_registry(config_root: Optional["Path"] = None) -> EvidenceRegistry:
    """Register every read-only adapter. All contracts are read-only by
    design in 5A; write/remember adapters arrive in 5B+ via gated paths."""
    from pathlib import Path
    from . import adapters as A

    reg = EvidenceRegistry()
    root = Path(config_root) if config_root is not None \
        else Path.cwd()

    files = A.FileAdapter(base=root)
    reg.register(AdapterContract(
        name="files", version="1.0.0", scope=Scope.FILES,
        sensitivity=Sensitivity.PERSONAL, permissions=frozenset({"read"}),
        read_only=True, freshness_ttl_s=None, trust=Trust.HIGH), files)

    reg.register(AdapterContract(
        name="repos", version="1.0.0", scope=Scope.REPOS,
        sensitivity=Sensitivity.INTERNAL, permissions=frozenset({"read"}),
        read_only=True, freshness_ttl_s=None, trust=Trust.MEDIUM),
        A.RepoAdapter())

    reg.register(AdapterContract(
        name="web", version="1.0.0", scope=Scope.WEB,
        sensitivity=Sensitivity.INTERNAL, permissions=frozenset({"read"}),
        read_only=True, freshness_ttl_s=600, trust=Trust.LOW),
        A.WebAdapter())

    reg.register(AdapterContract(
        name="reports", version="1.0.0", scope=Scope.REPORTS,
        sensitivity=Sensitivity.PERSONAL, permissions=frozenset({"read"}),
        read_only=True, freshness_ttl_s=None, trust=Trust.MEDIUM),
        A.ReportManifestAdapter())

    for decl in (("browser", Scope.BROWSER, Sensitivity.PERSONAL),
                 ("inbox", Scope.INBOX, Sensitivity.SECRET),
                 ("worker", Scope.WORKER, Sensitivity.INTERNAL),
                 ("memory", Scope.MEMORY, Sensitivity.PERSONAL)):
        name, scope, sens = decl
        reg.register(AdapterContract(
            name=name, version="1.0.0", scope=scope, sensitivity=sens,
            permissions=frozenset({"read"}), read_only=True,
            freshness_ttl_s=None, trust=Trust.MEDIUM),
            A.DeclarativeAdapter(name, "1.0.0", scope, sens))
    return reg