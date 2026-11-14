"""P6-05 — the reusable a11y module's public surface and the shell wiring.

These are static contracts, not a substitute for the vitest jsdom behaviour
tests (which run in the Office app lane). They make a refactor that silently
drops a helper, or a shell change that removes a landmark, fail here first.
"""
from __future__ import annotations

import re

from conftest import A11Y_TS, GAME_CSS, SHELL, STYLES


def test_a11y_module_exports_the_required_helpers():
    source = A11Y_TS.read_text(encoding="utf-8")
    for name in (
        "focusableElements", "focusFirst", "captureFocus", "restoreFocus",
        "trapFocus", "clampIndex", "nextRovingIndex", "rovingTabIndexes",
        "statusAria", "officeAnnouncement", "announce", "prefersReducedMotion",
        "prefersHighContrast", "onReducedMotionChange", "onMediaQueryChange",
        "installEscapeToShell", "meetsTargetSize", "undersizedTargets",
    ):
        assert re.search(rf"export function {name}\b", source), name
    assert "MIN_TARGET_PX = 24" in source


def test_shell_uses_the_shared_helpers_not_a_second_copy():
    source = SHELL.read_text(encoding="utf-8")
    assert 'from "./a11y"' in source
    for name in ("captureFocus", "focusFirst", "installEscapeToShell",
                 "officeAnnouncement", "prefersReducedMotion",
                 "prefersHighContrast", "restoreFocus"):
        assert name in source, name
    # Nothing re-implements a trap or a live region locally.
    assert "aria-live\": \"polite\"" in source
    assert "role: \"application\"" not in source


def test_shell_renders_semantic_landmarks_and_restores_focus():
    source = SHELL.read_text(encoding="utf-8")
    assert 'href: "#office-game-main"' in source
    assert 'href: "#office-game-controls"' in source
    assert 'id: "office-game-main"' in source
    assert 'role: "toolbar"' in source
    assert "openDockRef" in source and "focusReturnRef" in source
    assert "if (!restoreFocus(target, doc))" in source
    assert "data-office-reduced-motion" in source
    assert "data-office-contrast" in source


def test_stylesheet_keeps_focus_reduced_motion_and_target_size():
    css = GAME_CSS.read_text(encoding="utf-8")
    assert ".office-game__skipLink:focus" in css
    assert ".office-game__main" in css
    assert ".office-game__toolbar" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert '[data-office-reduced-motion="1"]' in css
    assert "min-height: 24px" in css
    assert "min-width: 24px" in css
    # The old colour-only focus removal is gone.
    assert "outline: none;" not in css


def test_tokens_declare_the_preference_switches():
    tokens = (STYLES / "tokens.css").read_text(encoding="utf-8")
    assert '[data-office-reduced-motion="1"]' in tokens
    assert '[data-office-contrast="high"]' in tokens
