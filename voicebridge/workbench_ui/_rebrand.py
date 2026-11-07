#!/usr/bin/env python3
"""Rebrand the ported hermes-webui tree — the §4 rule list, applied as code.

WHY A SCRIPT AND NOT A SED
---------------------------------------------------------------------------
This rebrand has writer↔reader pairs: a global written in ``index.html`` and
read in ``boot.js``; a storage key written in a pre-paint inline script and read
by deferred JS; a canvas that posts to a ``BroadcastChannel`` another function
listens on. A hand edit is how a half-rename leaves a dead screen — the reader
keeps waiting for a name the writer stopped using. So every rule below is
declared once, applied to EVERY file, and then ``_check_pairs.py`` proves that
no read lacks a write.

It is idempotent: running it twice changes nothing the second time.

WHAT IS NOT TOUCHED
---------------------------------------------------------------------------
* ``LICENSE`` and ``vendor/VENDOR_RECORD.md`` — the MIT notice and the
  provenance record must keep the upstream name (legal attribution is the one
  required exception to the zero-legacy-brand rule).
* Absolute URLs into the upstream product's own site (``get-hermes.ai``,
  ``hermes-agent.nousresearch.com``) — they are a third party's address, not
  our identifier. They are instead REMOVED or repointed where the UI links to
  them, because linking a user to another product's docs is a product bug.
* ``hermes_cli`` / ``hermes_constants`` / ``hermes_time`` / ``mnemosyne-hermes``
  — comments naming UPSTREAM MODULES that Qubit does not have. They stay as
  provenance in a comment, and the rebrand rule rewrites them to
  ``upstream-hermes`` so the zero-token grep can stay strict while the
  provenance is still readable.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "static"

#: Files whose whole point is the upstream name.
SKIP_FILES = {"LICENSE", "VENDOR_RECORD.md"}

#: Third-party addresses. Handled explicitly BEFORE the generic rules so the
#: generic rule cannot mangle a hostname into an unreachable one.
THIRD_PARTY = [
    # (pattern, replacement) — a link to another product's site becomes Qubit's
    # own docs route; leaving it would send a Qubit user to hermes-webui docs.
    ("https://get-hermes.ai/", "https://github.com/cgraph-dev/Qubit/tree/production/docs"),
    # A status payload's docs_url fallback that points at the upstream product.
    ("https://hermes-agent.nousresearch.com/docs/user-guide/skills/bundled/research/research-llm-wiki",
     "https://github.com/cgraph-dev/Qubit/tree/production/docs"),
]

#: Provenance comments naming upstream MODULES Qubit does not have. Rewritten to
#: a form that keeps the meaning and drops the product token.
PROVENANCE_MODULES = [
    ("hermes_cli/write_approval_commands.py", "the upstream CLI's write_approval_commands.py"),
    ("hermes_constants.VALID_REASONING_EFFORTS", "the upstream constants' VALID_REASONING_EFFORTS"),
    ("hermes_time._resolve_timezone_name", "the upstream time helper's _resolve_timezone_name"),
]

#: The ordered rule list. Every rule applies to every non-skipped file.
#: Order matters: the most specific (a full identifier) precedes the general.
RULES = [
    # --- R1 globals -------------------------------------------------------
    ("__HERMES_CONFIG__", "__QUBIT_CONFIG__"),
    ("__HERMES_WEBUI_BUNDLE_VERSION__", "__QUBIT_WB_BUNDLE_VERSION__"),
    ("__HERMES_WEBUI_DESKTOP_COMPANION_STATUS__", "__QUBIT_DESKTOP_COMPANION_STATUS__"),
    ("__HERMES_EXTENSION_CONFIG__", "__QUBIT_EXTENSION_CONFIG__"),
    # --- R2 published namespaces -----------------------------------------
    ("HermesAssistantTurnAnchors", "QubitTurnAnchors"),
    ("HermesExtensionSettings", "QubitExtensionSettings"),
    ("HermesPWA", "QubitPWA"),
    ("openHermesDashboard", "openQubitDashboard"),
    ("registerHermesTtsEngine", "registerQubitTtsEngine"),
    ("registerHermesSkin", "registerQubitSkin"),
    ("registerHermesSessionOpenHandler", "registerQubitSessionOpenHandler"),
    ("hermesExt", "qubitWbExt"),
    ("_syncHermesPanelSessionActions", "_syncQubitPanelSessionActions"),
    ("_settingsHermesDefaultModelOnOpen", "_settingsQubitDefaultModelOnOpen"),
    ("_settingsHermesDefaultModelProviderOnOpen", "_settingsQubitDefaultModelProviderOnOpen"),
    # --- R3 storage keys / channels --------------------------------------
    # A storage key and the BroadcastChannel name are both prefixes we own.
    ("hermes-webui", "qubit-wb"),
    # The rest are short keys with the product as their stem; a single stem rule
    # covers every one of them because each is either `hermes-<x>` or `hermes_<x>`.
    ("hermes-", "qubit-wb-"),
    ("hermes_", "qubit_wb_"),
    # remaining camelCase storage/config stems keyed off the product name
    ("_hermesTts", "_qubitWbTts"),
    ("_hermesNotify", "_qubitWbNotify"),
    ("_hermesAddTtsOption", "_qubitWbAddTtsOption"),
    ("_hermesExtension", "_qubitWbExtension"),
    ("_hermesSession", "_qubitWbSession"),
    ("_hermesExternalRefresh", "_qubitWbExternalRefresh"),
    ("_hermesSidebarSse", "_qubitWbSidebarSse"),
    ("_hermesGatewaySSE", "_qubitWbGatewaySSE"),
    ("_hermesHandlePetSlashCommand", "_qubitWbHandlePetSlashCommand"),
    ("_hermesDisplayOnlyIndexState", "_qubitWbDisplayOnlyIndexState"),
    ("_hermesDateGroup", "_qubitWbDateGroup"),
    ("_hermesSessionStream", "_qubitWbSessionStream"),
    ("_hermesSetBackgrounded", "_qubitWbSetBackgrounded"),
    ("_bindHermesExtensionMessageActions", "_bindQubitExtensionMessageActions"),
    # --- R5 css class families -------------------------------------------
    ("hermes-kanban-md", "qubit-wb-kanban-md"),
    ("hermes-kanban-", "qubit-wb-kanban-"),
    ("hermes-action-grid", "qubit-wb-action-grid"),
    ("hermesTheme", "qubitWbTheme"),
    ("hermesNotify", "qubitWbNotify"),
    ("hermesOk", "qubitWbOk"),
    ("hermesSessionMeta", "qubitWbSessionMeta"),
    ("hermesExtensionSkinStyles", "qubitWbExtensionSkinStyles"),
    ("hermesDeferredInstallPrompt", "qubitWbDeferredInstallPrompt"),
    # --- products / misc identifiers --------------------------------------
    ("hermes-mark", "qubit-mark"),
    ("__hermes_dashboard__", "__qubit_dashboard__"),
    ("system.hermes_found", "system.qubit_found"),
    # --- R7 visible product name -----------------------------------------
    # Only after the identifiers above, so this catches prose: 'Hermes',
    # 'Hermes WebUI', 'Hermes Dashboard', 'Hermes-Profile', 'Hermes agents'…
    ("Hermes WebUI", "Qubit Workbench"),
    ("Hermes-WebUI", "Qubit-Workbench"),
    ("Hermes Web UI", "Qubit Workbench"),
    ("Hermes", "Qubit"),
    ("hermes", "qubit"),
    # --- uppercase forms --------------------------------------------------
    # Env-var names, feature flags and the upper-case module-scope locals in
    # boot.js. Listed explicitly (never a blanket case-insensitive sweep: that
    # would also rewrite the MIT notice and third-party package names).
    ("_HERMES_TTS_", "_QUBIT_WB_TTS_"),
    ("_HERMES_SESSION_OPEN_HANDLERS", "_QUBIT_WB_SESSION_OPEN_HANDLERS"),
    ("HERMES_WEBUI_PASSWORD", "QUBIT_WB_PASSWORD"),
    ("HERMES_WEBUI_PASSKEY", "QUBIT_WB_PASSKEY"),
    ("HERMES_WEBUI_CSP_IMG_EXTRA", "QUBIT_WB_CSP_IMG_EXTRA"),
    ("HERMES_WEBUI_GATEWAY_BASE_URL", "QUBIT_WB_GATEWAY_BASE_URL"),
    ("HERMES_GATEWAY_HEALTH_URL", "QUBIT_WB_GATEWAY_HEALTH_URL"),
    ("HERMES_TIMEZONE", "QUBIT_WB_TIMEZONE"),
    ("HERMES_API_URL", "QUBIT_WB_API_URL"),
    ("HERMES", "QUBIT"),
]

#: After the generic rules, repair anything they over-reached. These are the
#: ONLY occurrences where the mechanical rule produced a wrong token.
REPAIRS = [
    # `mnemosyne-hermes` is a third-party memory PROVIDER's package name; the
    # generic rule would rename it and the user would be told to install a
    # package that does not exist.
    ("mnemosyne-qubit", "mnemosyne-hermes"),
    ("Hermese", "Hermes"),  # none expected; kept as a guard
]


def rebrand_text(text: str) -> str:
    for old, new in THIRD_PARTY:
        text = text.replace(old, new)
    for old, new in PROVENANCE_MODULES:
        text = text.replace(old, new)
    # Provenance module rewrites already dropped the token; the generic rules
    # would now touch nothing there. Apply the ordered list.
    for old, new in RULES:
        text = text.replace(old, new)
    for old, new in REPAIRS:
        text = text.replace(old, new)
    return text


def main(argv: list[str]) -> int:
    check = "--check" in argv
    changed = 0
    for dirpath, _dirs, files in os.walk(ROOT):
        for name in files:
            if name in SKIP_FILES:
                continue
            if not name.endswith((".js", ".html", ".css", ".json", ".svg")):
                continue
            path = Path(dirpath) / name
            try:
                before = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            after = rebrand_text(before)
            if after == before:
                continue
            changed += 1
            if check:
                print(f"WOULD CHANGE: {path.relative_to(ROOT)}")
            else:
                path.write_text(after, encoding="utf-8")
    print(f"{'would change' if check else 'changed'} {changed} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
