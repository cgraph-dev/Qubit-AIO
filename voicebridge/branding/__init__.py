"""Qubit branding overlays applied at Qubit-owned boundaries.

The vendored runtime and pinned frontend inputs stay byte-faithful as the
provenance record; product identity is rewritten on the rendered/served surface
instead. This package holds the CLI help overlay; the dashboard normalizers
live beside their served-boundary callers (``qubit_plugins._rebrand_product``,
``webui._rebrand_proxied_json``).
"""
from . import help_overlay

__all__ = ["help_overlay"]
