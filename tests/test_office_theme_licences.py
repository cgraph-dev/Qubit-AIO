"""The office art licence record must not call the paid LimeZu packs CC-BY.

The paid Modern Interiors / Modern User Interface / generator packs forbid
resale and redistribution, which a CC-BY licence would not. Only Serene Village
is CC-BY 4.0, and it is not used by the Office v1 theme. These tests are prose
guards over ``THIRD_PARTY_NOTICES.md`` so a future edit cannot quietly restore
the wrong licence statement.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
NOTICES = REPO / "THIRD_PARTY_NOTICES.md"


def _paragraphs() -> list[str]:
    return re.split(r"\n\s*\n", NOTICES.read_text(encoding="utf-8"))


def test_paid_limezu_packs_are_not_described_as_cc_by():
    paid = [p for p in _paragraphs() if "paid-art licence" in p]
    assert paid, "the paid-pack licence paragraph is missing"
    for paragraph in paid:
        assert "CC-BY" not in paragraph, (
            "the paid LimeZu packs must not be described as CC-BY")
    flat = " ".join(paid[0].split())
    assert "commercial" in flat.lower()
    assert "credit" in flat.lower()
    assert "resell" in flat.lower() or "redistribut" in flat.lower()


def test_cc_by_is_scoped_to_the_unused_serene_village_pack():
    serene = [p for p in _paragraphs()
              if "Serene Village" in p and "CC-BY" in p
              and not p.lstrip().startswith("|")]
    assert serene, "Serene Village's CC-BY scope is not stated"
    for paragraph in serene:
        flat = " ".join(paragraph.split()).lower()
        assert "cc-by 4.0" in flat
        assert "not used" in flat
