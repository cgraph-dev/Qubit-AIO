"""Qubit Office — the org's accounting spine (M0).

The office is a corporation staffed by AI agents that spend real money and
have to earn real money back. This package is the *spine* every later
milestone stands on, and it is deliberately small:

* :mod:`voicebridge.office.store` — the one SQLite store, its schema, and the
  append-only triggers that make the ledger uncorrectable.
* :mod:`voicebridge.office.ledger` — recording rows and deriving every total
  from them. No number here is ever written by hand (C2).
* :mod:`voicebridge.office.org` — floors, departments, rooms, seats, and the
  chain of command (D34/C30).

Design rules that are not negotiable (see ``docs/game-office/95-CONTRACT.md``):

* **Money is integer cents.** Never a float.
* **The ledger is append-only, enforced by the database**, not by convention
  (C1). A trigger raises ``ABORT``; callers see ``sqlite3.IntegrityError``.
* **Every displayed total is derived** from the ledger by ``GROUP BY``; a
  stored rollup is a cache, never a source (C2).
* **Every seat has an accountable superior** except the CEO (C30).
* **One store.** No second database, bus, or loop (C3).

This module is M0: it creates the store, records money, derives P&L, and
maintains the org skeleton. It starts no agent, calls no provider, and spends
nothing. Nothing downstream is required for it to be useful.

Scope note: the full schema in ``docs/game-office/30-DATA-MODEL.md`` lands
incrementally. Tables arrive with the milestone that can exercise them — a
table nobody reads is dead weight, and this repo rejects speculative
infrastructure.
"""
from __future__ import annotations

from .errors import OfficeError

__all__ = ["OfficeError"]
