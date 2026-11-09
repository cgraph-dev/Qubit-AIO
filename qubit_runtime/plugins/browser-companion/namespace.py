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

#: The extension's own name for the plugin, kept as upstream's wire value.
#:
#: The ported extension's ``lib/companion-plugin-status.mjs`` VERIFIES this field:
#: ``parseCompanionStatusPayload`` rejects the whole payload unless
#: ``payload.plugin`` equals its own ``COMPANION_PLUGIN_NAME``, which is
#: ``browser-companion`` — the same value as Qubit's plugin id. Upstream's older
#: spelling is the one the extension treats as a PREVIOUS copy
#: (``COMPANION_LEGACY_PLUGIN_NAME``) and reports as outdated.
#:
#: It was ``"qubit-browser-extension"`` here, so the route served at the right path
#: with a correct ``object``/``version``/``tools`` and was still discarded: the
#: extension classified a perfectly healthy plugin as ``outdated``
#: (``companion-no-route``) and fell back to ``/v1/toolsets``. Measured live —
#: ``probeCompanionPlugin`` returned state=outdated, tools=27, routes=0.
EXTENSION_PLUGIN_NAME = PLUGIN_NAME

#: Base path of every route this plugin wires. The extension calls exactly this.
BASE_PATH = f"/api/plugins/{PLUGIN_NAME}"

#: Toolset every ported tool registers under.
TOOLSET = PLUGIN_NAME
