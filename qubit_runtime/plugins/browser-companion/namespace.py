"""Qubit namespace for the ported browser companion.

Upstream ships this plugin as ``hermes-browser-extension``: that name is its
plugin id, its toolset, the base path of every HTTP route it wires
(``/api/plugins/hermes-browser-extension/...``) and the wire id its extension
half calls. Qubit ships it as ``browser-companion`` — the plugin directory name,
the ``qubit plugins`` id and the toolset every ported registration uses.

Keeping the single source of truth here means one token changes (or none):
the route module, the control module and the registration all read the same
constants, and ``wire_check.py`` asserts they equal what the ported extension
actually sends. A half-rename in any one of them is a dead channel, which is
exactly the failure this module exists to prevent.
"""

from __future__ import annotations

#: Qubit's plugin id and toolset for this plugin (``plugins/browser-companion``).
PLUGIN_NAME = "browser-companion"

#: The extension's own name for the plugin, kept as upstream's wire value: the
#: ported extension's ``lib/companion-plugin-status.mjs`` sends this string and
#: falls back to ``/v1/toolsets`` when the routes answer 404. It is a wire id,
#: not Qubit's plugin id, so the two are deliberately different values in one
#: place rather than silently diverging in several.
EXTENSION_PLUGIN_NAME = "qubit-browser-extension"

#: Base path of every route this plugin wires. The extension calls exactly this.
BASE_PATH = f"/api/plugins/{PLUGIN_NAME}"

#: Toolset every ported tool registers under.
TOOLSET = PLUGIN_NAME
