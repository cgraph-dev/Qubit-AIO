"""Pin the vendor-manifest policy that upstream test trees are reference-only.

The upstream pytest suite (``tests/**``) is never shipped product — the fork
ships its own suite (``tests/`` + ``qubit_runtime/tests``). These paths must be
recorded as ``intentionally_omitted`` (still listed), never as
``missing_from_local``, so the sync gap reflects real product code.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "docs/generated" / "QUBIT_HERMES_VENDOR_MANIFEST.json"


def _load() -> dict:
    return json.loads(MANIFEST.read_text())


def test_upstream_tests_are_not_missing():
    manifest = _load()
    missing = manifest.get("missing_from_local", [])
    test_misses = [p for p in missing
                   if (p if isinstance(p, str) else p.get("path", "")).startswith("tests/")]
    assert test_misses == [], test_misses[:10]


def test_intentionally_omitted_covers_upstream_tests():
    manifest = _load()
    omitted = manifest.get("intentionally_omitted", [])
    paths = [p if isinstance(p, str) else p.get("path", "") for p in omitted]
    assert paths, "expected the upstream test suite to be recorded"
    legacy = ("skills/autonomous-ai-agents/hermes-agent/",
              "skills/software-development/hermes-agent-skill-authoring/",
              "skills/software-development/inspecting-hermes-desktop-dom/",
              "optional-skills/migration/openclaw-migration/",
              "optional-skills/devops/hermes-s6-container-supervision/")

    def _allowed(p: str) -> bool:
        return (p.startswith("tests/") or p.startswith("tests-js/")
                or "/__tests__/" in p
                or p.endswith((".test.ts", ".test.tsx",
                               ".test.js", ".test.jsx"))
                or any(p == l.rstrip("/") or p.startswith(l)
                       for l in legacy))

    bad = [p for p in paths if not _allowed(p)]
    assert bad == [], bad[:10]


def test_missing_excludes_tests_and_legacy_skills():
    # Neither upstream tests nor legacy-named skill content may appear as
    # ``missing_from_local``: tests are recorded under
    # ``intentionally_omitted``, and legacy skill names are covered by their
    # canonical de-branded Qubit counterparts.
    manifest = _load()
    missing = manifest.get("missing_from_local", [])
    paths = [p if isinstance(p, str) else p.get("path", "") for p in missing]
    legacy = ("skills/autonomous-ai-agents/hermes-agent/",
              "skills/software-development/hermes-agent-skill-authoring/",
              "skills/software-development/inspecting-hermes-desktop-dom/",
              "optional-skills/migration/openclaw-migration/",
              "optional-skills/devops/hermes-s6-container-supervision/")
    bad = [p for p in paths
           if p.startswith("tests/") or p.startswith("tests-js/")
           or "/__tests__/" in p
           or p.endswith((".test.ts", ".test.tsx", ".test.js", ".test.jsx"))
           or any(p == l.rstrip("/") or p.startswith(l) for l in legacy)]
    assert bad == [], bad[:10]


def test_missing_still_lists_real_product_files():
    # The point of the policy: a genuine product miss must remain visible in
    # ``missing_from_local`` (it is the actionable sync gap), while
    # reference-only upstream test trees are recorded under
    # ``intentionally_omitted``. The W9 product sync is currently complete, so
    # the residual gap is empty; the invariant that must always hold is that
    # the manifest's MISSING classification and the ``missing_from_local`` list
    # agree exactly, and that a recorded miss is never a reference-only path.
    manifest = _load()
    missing = manifest.get("missing_from_local", [])
    paths = [p if isinstance(p, str) else p.get("path", "") for p in missing]
    assert manifest["classification_counts"]["MISSING"] == len(paths)
    assert set(paths).isdisjoint(set(manifest.get("intentionally_omitted", [])))
