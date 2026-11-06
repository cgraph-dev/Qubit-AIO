"""Jev decision provider — an OPTIONAL typed-decision capability for Qubit Agent.

Derived from hermes-jev-skills (MIT, Copyright (c) 2026 Steve Darlow);
see ``NOTICE`` and ``PROVENANCE.json`` beside this file, and
``docs/JEV_INTEGRATION_NOTE.md`` for the contract analysis.

A decision model answers typed questions (yes/no, one-of-N, a level on a rubric) with
calibrated numbers and never writes text. The wire is **bespoke**, so this plugin speaks it
directly instead of translating an OpenAI chat call into a fake confidence: see
``client.py`` and the note for the evidence.

Optional means optional. With no decision-model key configured, ``jev_decide`` is hidden by
its ``check_fn`` and, if called anyway, reports ``available: false`` with the reason — a turn
that never needed a decision is byte-identical to one without this plugin.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def register(ctx: Any) -> None:
    """Register the ``jev_decide`` tool. Called once by the plugin loader."""
    from plugins.jev import tools

    try:
        tools.register_tools(ctx)
    except Exception:  # noqa: BLE001 - an optional capability must never break plugin loading
        logger.warning("jev: could not register jev_decide", exc_info=True)
        return
    logger.debug("jev: jev_decide registered (available=%s)", tools.check_available(ctx))


__all__ = ["register"]
