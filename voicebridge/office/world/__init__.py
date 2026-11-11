"""Qubit Office world foundation: the data layer and room style system.

This package is the first-wave Lane C deliverable from
``docs/QUBIT-OFFICE-MASTER-PLAN.md`` §18.3/§18.4 and §25. It deliberately does
not render, load, rebrand, or run a business: it is the model those layers will
stand on.

* :mod:`voicebridge.office.world.model` — Campus/Wing/LogicalRoom/Cell/Desk/Seat,
  fixed 16x12 cells, cell clusters for 9/12/18/24+ desks, preserved
  ``legacyFloorId``, hierarchical portals, and ``validate_campus``.
* :mod:`voicebridge.office.world.active` — active/dormant lifecycle, active-set
  policy, rehydration snapshots, and the deterministic scheduler seam.
* :mod:`voicebridge.office.world.style` — asset catalog schema, palettes,
  archetypes, seeded recipes, and the walkability/capacity/collision/
  interaction/theme validator.
* :mod:`voicebridge.office.world.recipes` — the five sample archetype recipes
  (executive, support, finance, creative, control room) and a sample plan.
* :mod:`voicebridge.office.world.catalog` — a names-only LimeZu pack/theme-sorter
  extractor. It never opens, copies or commits art.
* :mod:`voicebridge.office.world.tower_plan` — the deterministic 100-floor,
  5-wing projection over the shipped templates (fixed cells, bounded payload).

Nothing here is ``VERIFIED``; the module is covered by focused unit tests.
"""
from __future__ import annotations

from . import active, catalog, model, recipes, style, tower_plan

__all__ = ["model", "active", "style", "recipes", "catalog", "tower_plan"]
