"""Qubit/OpenAI function schemas for the browser-companion tools.

Ported from the companion plugin of ``hermes-browser-extension``
(https://github.com/abundantbeing/hermes-browser-extension) at pin
16d42430ff45d5833cb6176b401db867b591c2f2, MIT, Copyright (c) 2026 Jon Komet.
See PROVENANCE.json beside this file. Local divergence: ``SCHEMA_EVENT_LOG``,
``SCHEMA_JOURNAL`` and ``SCHEMA_CONTROL_STATUS`` are not carried — those tools
are dropped in this port (``docs/EXT_COLLISION_NOTE.md``).
"""

_EMPTY_PARAMETERS = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

SCHEMA_STATUS = {
    "name": "browser_context_status",
    "description": "Check whether browser context is currently cached and available.",
    "parameters": _EMPTY_PARAMETERS,
}

SCHEMA_GET_CONTEXT = {
    "name": "browser_get_context",
    "description": "Consume the current turn's browser context once using its opaque context capability ID.",
    "parameters": {
        "type": "object",
        "properties": {
            "context_id": {
                "type": "string",
                "description": "Opaque browser context capability ID supplied by the companion context notice.",
                "minLength": 32,
                "maxLength": 32,
                "pattern": "^[a-f0-9]{32}$",
            },
        },
        "required": ["context_id"],
        "additionalProperties": False,
    },
}

SCHEMA_CLEAR_CONTEXT = {
    "name": "browser_clear_context",
    "description": "Clear the cached browser context. The next extension prompt will re-populate it.",
    "parameters": _EMPTY_PARAMETERS,
}
