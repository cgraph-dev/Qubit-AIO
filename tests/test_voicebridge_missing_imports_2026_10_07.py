"""Regression tests for Qubit-owned symbols imported/called but never defined (2026-10-07).

Both were live `NameError`s found by the undefined-global probe: a read-only adapter
whose deny path raised a name that was never imported, and the ghost action resolver
whose fallback called `action_spec` without importing it.
"""
from __future__ import annotations

import pytest


def test_readonly_adapter_deny_raises_evidence_permission_error():
    from voicebridge.evidence.adapters import ReadOnlyAdapter
    from voicebridge.evidence.models import EvidencePermissionError

    adapter = ReadOnlyAdapter()
    with pytest.raises(EvidencePermissionError):
        adapter._deny("write")


def test_ghost_spec_falls_back_to_the_shared_action_registry():
    from voicebridge import ghost

    # A non-ghost id must resolve through the shared registry (None when unregistered),
    # never NameError on the missing `action_spec` import.
    assert ghost.ghost_spec("__not_a_ghost_action__") is None
