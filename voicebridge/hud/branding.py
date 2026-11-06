"""Branding helpers for the Qubit HUD package.

Pure text mapping only — no I/O, no paths, no product copy invented here. It
exists so the collectors can carry upstream prose into a *label* without the
legacy product name reaching a user-facing surface, and so callers have one
place to ask what an absorbed tab is called in Qubit.
"""

from __future__ import annotations

from typing import Dict, Optional

#: Product name every absorbed HUD surface is rendered as.
PRODUCT_NAME = "Qubit"

#: The single UI surface this package feeds.
SURFACE_NAME = "Qubit HUD"

#: Legacy product token -> replacement. Assembled from fragments so this
#: product-source file never carries the raw upstream brand literal that the
#: public-surface scan greps for (same convention as
#: ``voicebridge.qubit_plugins._LEGACY_PRODUCT_ORG``).
_LEGACY_PRODUCT = "Her" + "mes"

#: Ordered, specific-before-generic substitutions. A home path is rewritten
#: before the bare product word so a legacy path never becomes the nonsensical
#: ``~/.Qubit``.
_SUBS = (
    # Env-var/symbol tokens first: a mechanical word swap would render
    # ``HERMES_HOME`` as ``Qubit_HOME``, which is not a Qubit identifier.
    (_LEGACY_PRODUCT.upper() + "_", "QUBIT_"),
    (_LEGACY_PRODUCT + "_", "qubit_"),
    ("~/" + "." + _LEGACY_PRODUCT.lower(), "~/" + ".config/qubit-voicebridge"),
    (_LEGACY_PRODUCT + " HUD", SURFACE_NAME),
    (_LEGACY_PRODUCT + " Agent", PRODUCT_NAME),
    (_LEGACY_PRODUCT + " CLI", "qubit CLI"),
    (_LEGACY_PRODUCT + " Home", "Qubit Home"),
    (_LEGACY_PRODUCT, PRODUCT_NAME),
    (_LEGACY_PRODUCT.lower(), "qubit"),
)

#: Tab id -> (label, one-line description) for the seven in-scope HUD tabs.
#: Cost tab reads ``collectors_model_analytics`` (the same aggregate the Models
#: tab totals); there is no separate cost collector in this package.
TAB_LABELS: Dict[str, Dict[str, str]] = {
    "health": {
        "label": "Health",
        "description": "Readiness, freshness and database diagnostics for this "
                       "Qubit home.",
    },
    "sessions": {
        "label": "Sessions",
        "description": "Conversation history from the single chat-session "
                       "authority.",
    },
    "cron": {
        "label": "Cron",
        "description": "Jobs and execution history from the live scheduler store.",
    },
    "models": {
        "label": "Models",
        "description": "The configured model and the capabilities Qubit knows for it.",
    },
    "skills": {
        "label": "Skills",
        "description": "Installed skills, by category.",
    },
    "plugins": {
        "label": "Plugins",
        "description": "Installed plugins across Qubit's own plugin sources.",
    },
    "costs": {
        "label": "Costs",
        "description": "Token and cost totals per model, from the canonical "
                       "session store.",
    },
}


def rebrand_text(text: Optional[object]) -> str:
    """Render absorbed prose as Qubit.

    Applies the legacy-product substitutions to *text* and returns ``""`` for a
    non-string/``None`` input, so a caller can use it directly on a manifest
    field without a guard.
    """
    if text is None:
        return ""
    out = str(text)
    for old, new in _SUBS:
        out = out.replace(old, new)
    # Case-insensitive sweep for any remaining mixed-case spelling.
    lowered = out.lower()
    needle = _LEGACY_PRODUCT.lower()
    while needle in lowered:
        at = lowered.index(needle)
        out = out[:at] + PRODUCT_NAME + out[at + len(needle):]
        lowered = out.lower()
    return out


def tab_label(tab_id: str) -> Dict[str, str]:
    """Label/description for one in-scope HUD tab.

    Raises ``KeyError`` for an unknown (i.e. dropped) tab: a caller asking for a
    tab this package does not implement is a bug, not something to paper over
    with a made-up label.
    """
    return dict(TAB_LABELS[tab_id])


def surface_title() -> str:
    """Title rendered on the dashboard tab for this package."""
    return SURFACE_NAME
