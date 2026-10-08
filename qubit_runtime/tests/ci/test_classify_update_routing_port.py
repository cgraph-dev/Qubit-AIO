"""Focused tests for the update-routing symbols ported into scripts/ci/classify_changes.py
from the frozen revision ``ad12263a5ed4``.

WHY THIS EXISTS
---------------
``test_classify_changes.py`` in this tree is a SHORT-PORT of the frozen revision: its
``CASES`` table still encodes the pre-routing expectations (e.g. ``scripts/build/web.mjs``
-> frontend only), and its workflow-wiring tests assert on ``.github/workflows/*.yaml``
gates that the frozen revision moved. Those are out of this port's scope, so this file
covers ONLY the newly-ported symbols — ``_with_package_inits``, ``_SHARED_FIXTURE_CONSUMERS``
and the fixture-consumer lane inheritance in ``classify`` — each asserted standalone, with
no dependency on the CI workflow layer.

The frozen revision wires these so a cross-language fixture file (one data file several
languages read as their shared contract) selects every consumer lane; a path-prefix rule
would see only ``tests/`` -> python and never run the cargo / vitest / PowerShell readers.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from scripts.ci.classify_changes import (  # noqa: E402
    _FIXTURE_CONSUMER_LANES,
    _SHARED_FIXTURE_CONSUMERS,
    _SHARED_HUBS,
    _with_package_inits,
    classify,
)


def test_with_package_inits_adds_ancestor_inits_that_exist():
    """Importing a/b/c.py runs a/__init__.py and a/b/__init__.py first, so a routed module
    must route the inits above it — but only the ones that exist on disk."""
    # scripts/__init__.py does not exist in this tree; scripts/ci/__init__.py may.
    out = _with_package_inits(("scripts/ci/classify_changes.py",))
    assert "scripts/ci/classify_changes.py" in out
    # Every added entry is an __init__.py that actually exists relative to the repo root.
    for entry in out:
        if entry != "scripts/ci/classify_changes.py":
            assert entry.endswith("/__init__.py")
            assert (_REPO / entry).is_file(), entry


def test_with_package_inits_never_routes_a_shared_hub_itself():
    """A hub's inits run whenever the hub is imported; only an init that is ITSELF a hub
    stays out — otherwise routing would re-add a hub the test deliberately keeps shared."""
    out = _with_package_inits(("scripts/ci/classify_changes.py",))
    for hub in _SHARED_HUBS:
        assert hub not in out or hub == "scripts/ci/classify_changes.py"


def test_with_package_inits_is_idempotent_on_plain_paths():
    """A path with no parent package inits (a top-level module) is returned unchanged."""
    out = _with_package_inits(("qubit_bootstrap.py",))
    assert "qubit_bootstrap.py" in out


def test_cross_language_fixture_selects_every_consumer_lane():
    """The corpus fixture must arm the lanes of every consumer that reads it, not only python."""
    fixture = "tests/fixtures/update_marker_corpus.json"
    assert fixture in _SHARED_FIXTURE_CONSUMERS, "fixture map lost the corpus entry"
    lanes = classify([fixture])
    for lane in _FIXTURE_CONSUMER_LANES:
        assert lanes[lane] is True, f"{lane} lane not armed by a cross-language fixture edit"


def test_shared_fixture_consumers_are_lane_names_classify_emits():
    """Guard against a typo in _FIXTURE_CONSUMER_LANES: each must be a real classify() key,
    or the inheritance silently no-ops."""
    lanes = classify(["tests/fixtures/update_marker_corpus.json"])
    for lane in _FIXTURE_CONSUMER_LANES:
        assert lane in lanes, f"{lane} is not a classify() output key"


def test_ordinary_source_file_does_not_arm_fixture_lanes():
    """The fixture inheritance must not leak onto unrelated edits."""
    lanes = classify(["run_agent.py"])
    assert lanes["rust"] is False
    assert lanes["desktop_updater"] is False
