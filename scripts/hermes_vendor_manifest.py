#!/usr/bin/env python3
"""Generate the per-file provenance manifests for the Qubit-owned Hermes fork.

The Qubit product executes a Qubit-owned fork of the pinned Hermes source
(see docs/archive/2026-09-21/QUBIT_OWNER_AMENDMENT_2026-09-11_HERMES_RUNTIME.md). This script
records, for every file in the local fork, the pinned upstream revision, the
upstream git blob hash, the upstream blob sha256, the local sha256, and a
six-way classification (UNCHANGED / QUBIT_PATCHED / OPENCLAW_OVERLAY /
QUBIT_ADDED / MISSING / INTENTIONALLY_OMITTED) plus EOL-normalization notes.
It also regenerates the vendored-frontend comparison ledger
(``docs/archive/2026-09-21/HERMES_SOURCE_COMPARISON.json``). It is deterministic and re-runnable;
no timestamps are embedded.

Usage:
    scripts/hermes_vendor_manifest.py
    scripts/hermes_vendor_manifest.py \
        --upstream /tmp/opencode/pinned/hermes-agent \
        --local qubit_runtime \
        --out docs/archive/2026-09-21/QUBIT_HERMES_VENDOR_MANIFEST.json \
        --openclaw /tmp/opencode/pinned/openclaw \
        --comparison-out docs/archive/2026-09-21/HERMES_SOURCE_COMPARISON.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIN = "d337b736aa1e8ebecfab043842d13e4a2d2f48a3"
UPSTREAM_TAG = "v0.21.4"
UPSTREAM_REPO = "https://github.com/NousResearch/hermes-agent"
UPSTREAM_LICENSE = "MIT License, Copyright (c) 2025 Nous Research"
OPENCLAW_PIN = "3928bad9badfcb6c7d140530435e806fb8092190"
OPENCLAW_TAG = "v2026.9.2"
OPENCLAW_REPO = "https://github.com/openclaw/openclaw"
OPENCLAW_LICENSE = "MIT (Copyright (c) 2026 OpenClaw Foundation)"

_SKIP_DIR_PARTS = {"__pycache__", ".git", ".pytest_cache", ".pytest-cache"}
_SKIP_SUFFIX = (".pyc", ".pyo")
_UPSTREAM_SKIP_DIRS = _SKIP_DIR_PARTS
# Disposable dependency/build/cache trees. A path under one of these is skipped
# from the local snapshot ONLY when it is untracked (gitignored build output /
# installed packages); upstream-tracked files such as the plugin dashboard
# ``dist/*`` bundles are real vendored source and stay in the manifest.
_DISPOSABLE_DIR_PARTS = {"node_modules", "dist", "build", ".cache", ".venv",
                         "venv"}
_EXCLUDE_COMPARE_DIRS = {"node_modules", "dist", "build", ".git", "__pycache__"}

UNCHANGED = "UNCHANGED"
QUBIT_PATCHED = "QUBIT_PATCHED"
OPENCLAW_OVERLAY = "OPENCLAW_OVERLAY"
QUBIT_ADDED = "QUBIT_ADDED"
MISSING = "MISSING"
INTENTIONALLY_OMITTED = "INTENTIONALLY_OMITTED"
EOL_NORMALIZED = "EOL_NORMALIZED"
UNEXPLAINED = "UNEXPLAINED"

_CLASSIFICATIONS = (
    UNCHANGED, QUBIT_PATCHED, OPENCLAW_OVERLAY, QUBIT_ADDED,
    MISSING, INTENTIONALLY_OMITTED, EOL_NORMALIZED,
)

# Documented Qubit patches (or intentional divergence) in the vendored fork.
#
# The shipped Qubit runtime is a *maintained source-level rebrand* of the pinned
# upstream (owner amendment 2026-09-18). Pristine upstream byte identity is
# proven against the read-only baseline clone; the fork itself carries recorded,
# owned patches. Every patched file MUST appear here with a real reason, so the
# verification model is "pristine baseline byte-identical + deterministic patch
# manifest", never "patched == 0".
_REBRAND_PATCH = {
    "patch": "Qubit source-level rebrand: replace the upstream product name / "
             "labels with Qubit in product-facing strings (owner amendment "
             "2026-09-18 source-rebrand provenance).",
    "reason": "Owner directive: the shipped Qubit runtime is a maintained "
              "source-level rebrand of upstream, not a byte-identical mirror. "
              "String/comment-only change; no route, logic, API, or wire-shape "
              "change. Behavioural parity is proven against the baseline.",
    "affected_regression_test": "tests/test_qubit_runtime_vendor8u.py",
}

_REBRAND_PATCHED_FILES = (
    "qubit_cli/commands.py",
    "qubit_cli/skin_engine.py",
    "ui-tui/src/app/createGatewayEventHandler.ts",
    "ui-tui/src/app/slash/commands/core.ts",
    "ui-tui/src/app/slash/commands/session.ts",
    "ui-tui/src/app/slash/commands/setup.ts",
    "ui-tui/src/app/slash/commands/subscription.ts",
    "ui-tui/src/app/slash/commands/topup.ts",
    "ui-tui/src/app/slash/commands/wake.ts",
    "ui-tui/src/app/wakeState.ts",
    "ui-tui/src/banner.ts",
    "ui-tui/src/components/billingOverlay.tsx",
    "ui-tui/src/components/branding.tsx",
    "ui-tui/src/components/gridStreamsDemo.tsx",
    "ui-tui/src/components/helpHint.tsx",
    "ui-tui/src/components/journey.tsx",
    "ui-tui/src/components/modelPicker.tsx",
    "ui-tui/src/components/subscriptionOverlay.tsx",
    "ui-tui/src/lib/billingDialog.test.ts",
    "ui-tui/src/lib/billingDialog.ts",
    "ui-tui/src/lib/terminalParity.ts",
    "ui-tui/src/theme.ts",
)
_QUBIT_PATCH_SPEC: dict[str, dict] = {
    rel: dict(_REBRAND_PATCH) for rel in _REBRAND_PATCHED_FILES
}
# OpenClaw source is behaviourally adapted, never vendored into qubit_runtime/.
_OPENCLAW_OVERLAY: set[str] = set()
# Upstream files deliberately not vendored into the fork (none at present).
_INTENTIONAL_OMISSIONS: dict[str, str] = {}

# ---------------------------------------------------------------------------
# Deep source rebrand path renames (owner amendment 2026-09-19)
#
# The shipped fork renamed upstream modules/dirs/files. A renamed file is NOT a
# missing file: this map translates an upstream path to its shipped Qubit path
# so the comparison is path-aware. Content changes on renamed files are owned by
# the deep-rebrand patch reason below.
# ---------------------------------------------------------------------------
_UPSTREAM_TO_LOCAL: dict[str, str] = {
    "hermes_cli": "qubit_cli",
    "hermes_bootstrap": "qubit_bootstrap",
    "hermes_constants": "qubit_constants",
    "hermes_constants_scratch": "qubit_constants_scratch",
    "hermes_logging": "qubit_logging",
    "hermes_platform": "qubit_platform",
    "hermes_startup_watchdog": "qubit_startup_watchdog",
    "hermes_time": "qubit_time",
    "hermes_state": "qubit_state",
    "hermes_state_common": "qubit_state_common",
    "hermes_state_compression": "qubit_state_compression",
    "hermes_state_dbfile": "qubit_state_dbfile",
    "hermes_state_errors": "qubit_state_errors",
    "hermes_state_fts": "qubit_state_fts",
    "hermes_state_gateway": "qubit_state_gateway",
    "hermes_state_guard": "qubit_state_guard",
    "hermes_state_holders": "qubit_state_holders",
    "hermes_state_ids": "qubit_state_ids",
    "hermes_state_lockguard": "qubit_state_lockguard",
    "hermes_state_lockowners": "qubit_state_lockowners",
    "hermes_state_maintenance": "qubit_state_maintenance",
    "hermes_state_messages": "qubit_state_messages",
    "hermes_state_portability": "qubit_state_portability",
    "hermes_state_profile_repair": "qubit_state_profile_repair",
    "hermes_state_readpool": "qubit_state_readpool",
    "hermes_state_registry": "qubit_state_registry",
    "hermes_state_repair": "qubit_state_repair",
    "hermes_state_rewind": "qubit_state_rewind",
    "hermes_state_schema": "qubit_state_schema",
    "hermes_state_search": "qubit_state_search",
    "hermes_state_sessions": "qubit_state_sessions",
    "hermes_state_telegram": "qubit_state_telegram",
    "hermes_state_timeline": "qubit_state_timeline",
    "hermes_state_titles": "qubit_state_titles",
    "hermes_state_usage": "qubit_state_usage",
    "hermes_state_user_copy": "qubit_state_user_copy",
    "hermes_state_wal": "qubit_state_wal",
    "hermes_tools": "qubit_tools",
    "hermes_tools_mcp_server": "qubit_tools_mcp_server",
    "hermes_subprocess_env": "qubit_subprocess_env",
    "hermes_home_key": "qubit_home_key",
    "hermes_client_tag": "qubit_client_tag",
    "hermes_xai_user_agent": "qubit_xai_user_agent",
    "hermes_xai_default_headers": "qubit_xai_default_headers",
    "hermes_undo": "qubit_undo",
}
#: Root entries whose basename (not a Python module) was renamed.
_EXTRA_NAME_RENAMES: dict[str, str] = {
    "hermes": "qubit",
    "setup-hermes.sh": "setup-qubit.sh",
}
#: Exact full-path renames that are not module basenames.
_EXTRA_PATH_RENAMES: dict[str, str] = {
    "docker/hermes-exec-shim.sh": "docker/qubit-exec-shim.sh",
    "nix/hermes-agent.nix": "nix/qubit-runtime.nix",
    "ui-tui/src/types/hermes-ink.d.ts": "ui-tui/src/types/qubit-ink.d.ts",
    "web/src/components/HermesConsoleModal.tsx":
        "web/src/components/QubitConsoleModal.tsx",
}
#: Directory-prefix renames (upstream prefix -> shipped prefix).
_EXTRA_PREFIX_RENAMES: tuple[tuple[str, str], ...] = (
    ("ui-tui/packages/hermes-ink/", "ui-tui/packages/qubit-ink/"),
)
#: Generic segment map mirroring ``scripts/qubit_identifier_rename.py`` so the
#: RR2 module-graph file renames (fork tests, skill shims, TS components) are
#: seen as renames, not missing/extra. Hyphenated plugin ids are preserved.
_GS = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")


def _map_stem(stem: str) -> str:
    low = stem.lower()
    # Mirror scripts/qubit_identifier_rename.py::map_name: preserve
    # external-compat (nous/openclaw) and hyphenated plugin ids.
    if ("hermes" not in low or "-" in stem
            or "nous" in low or "openclaw" in low):
        return stem

    def _case(seg: str) -> str:
        if seg.isupper():
            return "QUBIT"
        if seg[:1].isupper():
            return "Qubit"
        return "qubit"

    return _GS.sub(
        lambda m: _case(m.group(0)) if m.group(0).lower() == "hermes"
        else m.group(0), stem)
#: Owned reason for content changes introduced by the deep source rebrand
#: (product-facing strings / branding in the shipped fork).
_DEEP_REBRAND_PATCH: dict[str, str] = {
    "patch": "Qubit deep source rebrand: product-facing branding strings, "
             "module namespace, and/or path renamed to Qubit in the shipped "
             "runtime (owner amendment 2026-09-19 deep rebrand; engine "
             "scripts/qubit_rebrand.py + scripts/qubit_debrand.py).",
    "reason": "Owner directive: the shipped runtime is a source-level Qubit "
              "rebrand, not a byte-identical mirror. Rename/branding-only "
              "change; behavioural/API/CLI/voice parity is proven against the "
              "pristine baseline.",
    "affected_regression_test": "tests/test_qubit_runtime_vendor8u.py",
}


def _is_deep_rebrand_path(rel: str) -> bool:
    """True when *rel* is a fork path the deep rebrand engines own.

    The structural rename (``scripts/qubit_rebrand.py``) rewrote module tokens
    across the whole fork, including its ``tests/``, ``apps/`` and ``website/``
    trees, so a content change in any fork path is attributable to the
    owner-authorized deep rebrand. Genuinely unexplained divergence is detected
    at the pinned-baseline comparison, not by path.
    """
    return True


def _local_from_upstream(rel: str) -> str:
    """Translate one upstream path to its shipped Qubit path (identity if none).

    Every path segment is considered: the structural rename moved both
    top-level modules and nested names such as ``tests/hermes_cli/`` and
    ``agent/transports/hermes_tools_mcp_server.py``.
    """
    if rel in _EXTRA_PATH_RENAMES:
        return _EXTRA_PATH_RENAMES[rel]
    for old_prefix, new_prefix in _EXTRA_PREFIX_RENAMES:
        if rel.startswith(old_prefix):
            return new_prefix + rel[len(old_prefix):]
    parts = rel.split("/")
    out: list[str] = []
    for index, seg in enumerate(parts):
        stem, ext = os.path.splitext(seg)
        if stem in _UPSTREAM_TO_LOCAL:
            out.append(_UPSTREAM_TO_LOCAL[stem] + ext)
        elif index == 0 and seg in _EXTRA_NAME_RENAMES:
            out.append(_EXTRA_NAME_RENAMES[seg])
        elif ext in (".py", ".pyi"):
            # Only Python module files were renamed by the RR2 generic pass;
            # TS/asset basenames are handled explicitly above.
            mapped = _map_stem(stem)
            out.append(mapped + ext if mapped != stem else seg)
        else:
            out.append(seg)
    return "/".join(out)

# ---------------------------------------------------------------------------
# Vendored-frontend comparison ledger (docs/archive/2026-09-21/HERMES_SOURCE_COMPARISON.json)
# ---------------------------------------------------------------------------

_GENERIC_REBRAND = {
    "patch": "Qubit product rebrand: replace upstream product name/labels in "
             "product-facing dashboard strings",
    "reason": "Qubit rebrand of product-facing dashboard strings (owner "
              "directive 2026-09-12); no logic, route, or backend-route change.",
    "affected_regression_test": "tests/test_dashboard_branding_wave1.py",
}

_REBRAND_PATHS = [
    "frontend/qubit-web/src/App.tsx",
    "frontend/qubit-web/src/components/QubitConsoleModal.tsx",
    "frontend/qubit-web/src/i18n/af.ts",
    "frontend/qubit-web/src/i18n/ar.ts",
    "frontend/qubit-web/src/i18n/de.ts",
    "frontend/qubit-web/src/i18n/es.ts",
    "frontend/qubit-web/src/i18n/fr.ts",
    "frontend/qubit-web/src/i18n/ga.ts",
    "frontend/qubit-web/src/i18n/hu.ts",
    "frontend/qubit-web/src/i18n/it.ts",
    "frontend/qubit-web/src/i18n/ja.ts",
    "frontend/qubit-web/src/i18n/ko.ts",
    "frontend/qubit-web/src/i18n/pt.ts",
    "frontend/qubit-web/src/i18n/ru.ts",
    "frontend/qubit-web/src/i18n/tr.ts",
    "frontend/qubit-web/src/i18n/uk.ts",
    "frontend/qubit-web/src/i18n/zh-hant.ts",
    "frontend/qubit-web/src/i18n/zh.ts",
    "frontend/qubit-web/src/lib/gatewayClient.ts",
    "frontend/qubit-web/src/pages/ChannelsPage.tsx",
    "frontend/qubit-web/src/pages/ChatPage.tsx",
    "frontend/qubit-web/src/pages/ConfigPage.tsx",
    "frontend/qubit-web/src/pages/EnvPage.tsx",
    "frontend/qubit-web/src/pages/McpPage.tsx",
    "frontend/qubit-web/src/pages/PluginsPage.tsx",
    "frontend/qubit-web/src/pages/SkillsPage.tsx",
    "frontend/qubit-web/src/pages/SystemPage.tsx",
    "frontend/qubit-web/src/themes/presets.ts",
]

_PATCH_SPEC: dict[str, dict] = {
    p: dict(_GENERIC_REBRAND) for p in _REBRAND_PATHS
}
_PATCH_SPEC.update({
    "frontend/qubit-web/src/contexts/SystemActions.tsx": {
        "patch": "Qubit typed-refusal classification for system actions "
                 "(self-update / process supervision): parse the typed "
                 "403/denied envelope and surface the honest Qubit counterpart "
                 "reason instead of a raw action error toast.",
        "reason": "Qubit policy-refusal UX (owner directive 2026-09-15): the "
                  "backend answers operator-owned self-update/supervision with "
                  "a typed refusal, so the dashboard action must present honest "
                  "guidance rather than a fake success or a raw error. No "
                  "backend route, permission, or upstream behavior change.",
        "affected_regression_test": "tests/test_dashboard_control_fixes.py",
    },
    "frontend/qubit-web/src/components/OAuthLoginModal.tsx": {
        "patch": "route OAuth start/poll/submit failures through the shared "
                 "Qubit `refusalOrError` helper so an operator-owned typed "
                 "refusal renders its Qubit counterpart instead of a raw error.",
        "reason": "Qubit policy-refusal UX (owner directive 2026-09-15): the "
                  "backend refuses live OAuth with a typed manual-handoff "
                  "envelope, so the dashboard modal must present honest Qubit "
                  "guidance. No backend route, permission, or upstream behavior "
                  "change.",
        "affected_regression_test": "tests/test_dashboard_control_fixes.py",
    },
    "frontend/qubit-web/src/components/OAuthProvidersCard.tsx": {
        "patch": "route OAuth provider load/disconnect failures through the "
                 "shared Qubit `refusalOrError` helper so a typed refusal renders "
                 "its Qubit counterpart instead of a raw error.",
        "reason": "Qubit policy-refusal UX (owner directive 2026-09-15): the "
                  "backend refuses live OAuth with a typed manual-handoff "
                  "envelope, so the dashboard card must present honest Qubit "
                  "guidance. No backend route, permission, or upstream behavior "
                  "change.",
        "affected_regression_test": "tests/test_dashboard_control_fixes.py",
    },
    "frontend/qubit-web/src/lib/resolve-page-title.test.ts": {
        "patch": "assert the Qubit route-title map for the profile-create path",
        "reason": "test-only assertion for the Qubit route-title overlay; no "
                  "vendor runtime behavior change.",
        "affected_regression_test": "tests/test_dashboard_control_fixes.py",
    },
    "frontend/qubit-web/src/App.tsx": {
        "patch": "Qubit product rebrand plus the owner chat mapping: `/chat` "
                 "and `/qubit-chat` are registered as the two built-in routes "
                 "that both render the single persistent ChatPage host, and a "
                 "`/qubit-chat` sidebar entry is added. No second chat "
                 "implementation, no redirect.",
        "reason": "Owner chat mapping (2026-09-17): `/qubit-chat` must directly "
                  "render the same Hermes-style chat UI as `/chat`, sharing the "
                  "session store, provider resolver, streaming, policy, audit, "
                  "e-stop, and VoiceBridge. The upstream file is patched because "
                  "the vendored SPA has no `/qubit-chat` route of its own.",
        "affected_regression_test":
            "tests/test_dashboard_page_closure_wave2.py",
    },
    "frontend/qubit-web/src/contexts/PageHeaderProvider.tsx": {
        "patch": "derive `isChatRoute` from the canonical `isChatPath()` helper "
                 "so the header layout is identical on `/chat` and "
                 "`/qubit-chat`.",
        "reason": "Owner chat mapping (2026-09-17): both chat routes share one "
                  "page host and must share its header layout. No backend route "
                  "or upstream behavior change.",
        "affected_regression_test":
            "tests/test_dashboard_page_closure_wave2.py",
    },
    "frontend/qubit-web/src/lib/resolve-page-title.ts": {
        "patch": "add the `/qubit-chat` -> 'Qubit Chat' route-title entry so the "
                 "built-in chat route renders a proper page title instead of the "
                 "naive path fallback.",
        "reason": "Owner chat mapping (2026-09-17): `/qubit-chat` is a built-in "
                  "route and needs an explicit title. No backend route or "
                  "upstream behavior change.",
        "affected_regression_test":
            "tests/test_dashboard_page_closure_wave2.py",
    },
    "frontend/qubit-web/index.html": {
        "patch": "Qubit product rebrand: document title -> 'Qubit Agent - "
                 "Command Center'",
        "reason": "Qubit rebrand of product-facing dashboard strings (owner "
                  "directive 2026-09-12); no logic, route, or backend-route "
                  "change.",
        "affected_regression_test": "tests/test_dashboard_branding_wave1.py",
    },
    "frontend/qubit-web/package.json": {
        "patch": "declare dashboard build/lint/test devDependencies (jsdom + "
                 "eslint toolchain) missing from the vendored web/ package.json",
        "reason": "Qubit build-dependency patch (owner directive 2026-09-11): "
                  "the Hermes monorepo declares the eslint toolchain at the "
                  "root workspace, which is not vendored; declaring it here "
                  "makes `npm install` + `npm run lint/test/build` work from a "
                  "clean checkout. No runtime dependency or Hermes behavior "
                  "changes.",
        "affected_regression_test": "tests/test_release_gates8u.py",
    },
    "frontend/qubit-web/src/i18n/en.ts": {
        "patch": "product-string rebrand: 'Update Hermes'/'Updating "
                 "Hermes'/'Update Hermes?' -> Qubit; confirm text "
                 "hermes->qubit (owner rebrand)",
        "reason": "Qubit rebrand of product-facing English dashboard strings; "
                  "translation keys unchanged; no logic or route change.",
        "affected_regression_test": "tests/test_dashboard_branding_wave1.py",
    },
    "frontend/qubit-web/src/pages/SessionsPage.tsx": {
        "patch": "hermes_flow source label 'Hermes Flow' -> 'Qubit Flow' "
                 "(owner rebrand; no logic change)",
        "reason": "Qubit rebrand of a product-facing session source label; the "
                  "source id hermes_flow is unchanged for compatibility.",
        "affected_regression_test": "tests/test_dashboard_branding_wave1.py",
    },
    "frontend/qubit-web/src/pages/ChatPage.test.tsx": {
        "patch": "wave1/chat-safe-transport test rewrite",
        "reason": "Qubit-safe ChatPage contract: the page drives the native "
                  "agent turn at POST /api/qubit/chat instead of a raw "
                  "/api/pty WebSocket (Qubit policy refuses PTY with "
                  "PTY_NO_SHELL). The vendored page test is rewritten to pin "
                  "the safe transport (real send/receive, typed error+retry, "
                  "voice fallback, transcript persistence, no WebSocket). "
                  "Test-only; no runtime dependency or backend-route change.",
        "affected_regression_test": "tests/test_branding_parity_wave3.py",
    },
    "frontend/qubit-web/src/plugins/index.ts": {
        "patch": "wave3/plugins-fleet-exports",
        "reason": "Re-export the new fleet discovery helpers from the plugin "
                  "barrel. Regression: tests/test_dashboard_plugin_fleet.py.",
        "affected_regression_test": "tests/test_dashboard_plugin_fleet.py",
    },
    "frontend/qubit-web/src/components/AutoField.tsx": {
        "patch": "wave2/frontend-rebrand-normalizer",
        "reason": "Apply the Qubit product-identity normalizer to schema "
                  "field descriptions at the render boundary so backend "
                  "free-text never exposes an upstream product name. "
                  "Presentation-only; no logic or backend-route change. "
                  "Regression: tests/test_qubit_frontend_inventory8u.py.",
        "affected_regression_test": "tests/test_qubit_frontend_inventory8u.py",
    },
    "frontend/qubit-web/src/components/QubitConsoleModal.tsx": {
        "patch": "wave2/frontend-rebrand-console-prompt",
        "reason": "Rebrand the refused raw-console prompt fallback from a "
                  "Qubit product perspective. Presentation-only; the raw "
                  "console/PTY remains a typed refusal. Regression: "
                  "tests/test_qubit_playwright8tf.py.",
        "affected_regression_test": "tests/test_qubit_playwright8tf.py",
    },
    "frontend/qubit-web/src/pages/ChannelsPage.tsx": {
        "patch": "wave2/frontend-rebrand-normalizer",
        "reason": "Apply the Qubit product-identity normalizer to channel "
                  "platform descriptions/help text at the render boundary. "
                  "Presentation-only; no logic or backend-route change. "
                  "Regression: tests/test_qubit_frontend_inventory8u.py.",
        "affected_regression_test": "tests/test_qubit_frontend_inventory8u.py",
    },
    "frontend/qubit-web/src/lib/api.ts": {
        "patch": "wave2/public-rebrand-api-prefix",
        "reason": "Qubit-owned public API prefix: the vendored SPA's API, WS, "
                  "and export URLs are mapped from /api/<x> to /agent/api/<x> "
                  "via window.__QUBIT_API_PREFIX__; logout/update call the "
                  "Qubit paths. No vendor business logic changed. Regression: "
                  "tests/test_qbpv2_rebrand_public_surface.py.",
        "affected_regression_test": "tests/test_qbpv2_rebrand_public_surface.py",
    },
    "frontend/qubit-web/src/lib/api.test.ts": {
        "patch": "wave2/public-rebrand-api-prefix-tests",
        "reason": "Update the vendored api tests to the Qubit-owned /agent API "
                  "prefix. Test-only. Regression: "
                  "tests/test_qbpv2_rebrand_public_surface.py.",
        "affected_regression_test": "tests/test_qbpv2_rebrand_public_surface.py",
    },
    "frontend/qubit-web/vite.config.ts": {
        "patch": "wave2/public-rebrand-root-base",
        "reason": "Serve the SPA at the Qubit root base ('/') instead of the "
                  "upstream-named prefix; dev proxy for /agent. Build-config "
                  "only; no vendor runtime logic changed. Regression: "
                  "tests/test_qbpv2_frontend_root_base.py.",
        "affected_regression_test": "tests/test_qbpv2_frontend_root_base.py",
    },
    "frontend/qubit-web/src/lib/gatewayClient.ts": {
        "patch": "wave2/public-rebrand-api-prefix",
        "reason": "Gateway WebSocket path routed through the Qubit-owned "
                  "/agent API prefix. Regression: "
                  "tests/test_qbpv2_rebrand_public_surface.py.",
        "affected_regression_test": "tests/test_qbpv2_rebrand_public_surface.py",
    },
    "frontend/qubit-web/src/plugins/types.ts": {
        "patch": "wave3/plugins-manifest-assetBase",
        "reason": "Add the optional PluginManifest.assetBase field consumed by "
                  "usePlugins for static fleet bundles; when absent the host "
                  "keeps the upstream overlay asset root. Regression: "
                  "tests/test_dashboard_plugin_fleet.py.",
        "affected_regression_test": "tests/test_dashboard_plugin_fleet.py",
    },
    "frontend/qubit-web/src/plugins/usePlugins.ts": {
        "patch": "wave3/usePlugins-static-fleet",
        "reason": "Merge the SPA-shipped static plugin fleet (pinned "
                  "achievements + kanban manifests) with the compat overlay "
                  "manifests the Qubit backend advertises, and resolve each "
                  "manifest's entry/css bundles against its assetBase (fleet "
                  "bundles ship under <base>/plugins/<name>; overlay bundles "
                  "keep <base>/dashboard-plugins/<name>). Backend + auth "
                  "behaviour unchanged (SDK fetch/ws helpers still apply the "
                  "base path and session token). Regression: "
                  "tests/test_dashboard_plugin_fleet.py.",
        "affected_regression_test": "tests/test_dashboard_plugin_fleet.py",
    },
    "frontend/qubit-shared/src/json-rpc-gateway.ts": {
        "patch": "@@ -450,7 +450,7 @@\n       if (frame.error) {\n         "
                 "call.reject(\n-          new JsonRpcGatewayError(frame.error."
                 "message || 'Hermes RPC failed', {\n+          new "
                 "JsonRpcGatewayError(frame.error.message || 'Qubit RPC "
                 "failed', {\n             code: typeof frame.error.code === "
                 "'number' ? frame.error.code : undefined,\n             data: "
                 "frame.error.data",
        "reason": "Owner-approved Qubit rebrand: the only user-visible gateway "
                  "error string is relabelled 'Qubit RPC failed'; no behavior "
                  "change.",
        "affected_regression_test": "tests/test_dashboard_branding_wave1.py",
    },
    "frontend/qubit-shared/src/websocket-url.ts": {
        "patch": "@@ -42,7 +42,7 @@\n   if (conn.authMode === 'oauth') {\n     "
                 "if (!mint) {\n-      throw new Error('This Desktop build "
                 "cannot refresh OAuth WebSocket tickets. Update Hermes "
                 "Desktop and try again.')\n+      throw new Error('This Desktop "
                 "build cannot refresh OAuth WebSocket tickets. Update Qubit "
                 "Desktop and try again.')\n     }",
        "reason": "Owner-approved Qubit rebrand: the desktop OAuth-ticket "
                  "error is relabelled 'Qubit Desktop'; no behavior change.",
        "affected_regression_test": "tests/test_dashboard_branding_wave1.py",
    },
    "frontend/qubit-web/public/plugins/hermes-achievements/dist/index.js": {
        "patch": "wave3/QUBIT_ADDED:byte-identical [wave3 rebrand] Qubit "
                 "product naming applied; [A28] share trigger shown on all "
                 "non-secret cards and typed SHARE_UNAVAILABLE refusals added.",
        "reason": "Owner-approved Qubit rebrand of the vendored achievements "
                  "dashboard bundle: product-facing copy relabelled Qubit "
                  "(Qubit Achievements / Qubit Agent / Qubit session history); "
                  "protocol identifiers (X-Hermes-Session-Token, HERMES_PLUGINS, "
                  "~/.hermes) and module ids preserved. A28: restored the Hermes "
                  "Share actions (trigger, dialog, Share on X / Copy image / "
                  "Download PNG) with typed refusals when canvas/toBlob are "
                  "unavailable.",
        "affected_regression_test": "tests/test_dashboard_plugin_fleet.py",
    },
    "frontend/qubit-web/public/plugins/hermes-achievements/dist/style.css": {
        "patch": "wave3/QUBIT_ADDED:byte-identical",
        "reason": "Vendored pinned achievements stylesheet copied "
                  "byte-identical into the SPA static root.",
        "affected_regression_test": "tests/test_dashboard_plugin_fleet.py",
    },
    "frontend/qubit-web/public/plugins/kanban/dist/index.js": {
        "patch": "wave3/QUBIT_ADDED:byte-identical [wave3 rebrand] Qubit "
                 "product naming applied; [wave17 branding audit] task-id "
                 "tooltip no longer tells the user to run `hermes kanban show` "
                 "(now `qubit kanban show`).",
        "reason": "Owner-approved Qubit rebrand of the vendored kanban "
                  "dashboard bundle: product-facing copy relabelled Qubit "
                  "(Qubit Kanban / Qubit profile); protocol identifiers and "
                  "storage paths preserved. The task-id tooltip is "
                  "browser-visible product text, so its command hint was "
                  "rebranded to the Qubit CLI (the only byte difference from the "
                  "wave-3 bundle).",
        "affected_regression_test": "tests/test_dashboard_plugin_fleet.py",
    },
    "frontend/qubit-web/public/plugins/kanban/dist/style.css": {
        "patch": "wave3/QUBIT_ADDED [wave3 rebrand] Qubit product naming "
                 "applied to the vendored kanban stylesheet.",
        "reason": "Owner-approved Qubit rebrand of the vendored kanban "
                  "dashboard bundle: the stylesheet header comment is "
                  "relabelled 'Hermes Kanban' -> 'Qubit Kanban' (the only byte "
                  "difference from the pinned bundle). Protocol identifiers, "
                  "selectors, and layout are preserved.",
        "affected_regression_test": "tests/test_dashboard_plugin_fleet.py",
    },
    "frontend/qubit-web/eslint.config.js": {
        "patch": "build-lint: ignore the vendored dashboard plugin dist bundles",
        "reason": "Qubit build-tooling patch: add `**/dist/**` and "
                  "`../qubit-dist/**` to `globalIgnores` so the pinned "
                  "`public/plugins/<name>/dist` bundles (which carry their own "
                  "inline eslint directives) are not linted as project source. "
                  "No runtime or Hermes behavior change.",
        "affected_regression_test": "tests/test_release_gates8u.py",
    },
    "frontend/qubit-web/tsconfig.app.json": {
        "patch": "build-config: exclude shared test files from the app build",
        "reason": "Qubit build-tooling patch: add an `exclude` for "
                  "`../qubit-shared/src/**/*.test.ts(x)` so the dashboard app "
                  "`tsc` build does not typecheck the vendored shared-lib test "
                  "files. No runtime or Hermes behavior change.",
        "affected_regression_test": "tests/test_release_gates8u.py",
    },
    "frontend/qubit-web/vitest.config.ts": {
        "patch": "build-config: spread the rolldown filter instead of mutating it",
        "reason": "Qubit build-tooling patch: set the react code filter with "
                  "`{ ...preset.rolldown.filter, code: ... }` instead of "
                  "mutating a possibly-frozen preset object, so the vitest "
                  "config loads under the pinned toolchain. Test-config only; "
                  "no runtime or Hermes behavior change.",
        "affected_regression_test": "tests/test_release_gates8u.py",
    },
    "frontend/qubit-web/src/components/ChatSessionList.tsx": {
        "patch": "wave1/chat-safe-transport: native chat-session store",
        "reason": "Qubit-safe Chat contract: the sidebar lists sessions from "
                  "the Qubit-native chat-session store "
                  "(`GET /api/qubit/chat/sessions`, "
                  "`voicebridge.chat_transport`) via `api.listChatSessions` "
                  "instead of the receipts store / PTY session list, and its "
                  "copy describes the persisted SSE transcript rather than a "
                  "terminal child (Qubit policy refuses PTY with "
                  "PTY_NO_SHELL). Regression: "
                  "tests/test_branding_parity_wave3.py.",
        "affected_regression_test": "tests/test_branding_parity_wave3.py",
    },
    "frontend/qubit-web/src/pages/LogsPage.tsx": {
        "patch": "wave2/frontend-rebrand-normalizer",
        "reason": "Apply the Qubit product-identity normalizer (`rebrand`) to "
                  "rendered log lines at the render boundary so "
                  "backend/upstream free-text never exposes an upstream product "
                  "name. Presentation-only; no logic or backend-route change. "
                  "Regression: tests/test_qubit_frontend_inventory8u.py.",
        "affected_regression_test": "tests/test_qubit_frontend_inventory8u.py",
    },
})

# Documented intentional Qubit patches to the vendored frontend that were not
# captured by the wave-specific spec above. Each carries an exact patch + reason
# so the provenance gate accepts the divergence instead of flagging it as
# undocumented drift.
_PATCH_SPEC.update({
    "frontend/qubit-web/src/components/SidebarFooter.tsx": {
        "patch": "Qubit rebrand: render the org label as plain text and remove "
                 "the external upstream vendor (nousresearch.com) link from the "
                 "sidebar footer",
        "reason": "Qubit identity promise: a normal dashboard surface must not "
                  "present an upstream vendor link as Qubit. Presentation-only; "
                  "no logic, route, or backend-route change; one hunk.",
        "affected_regression_test": "tests/test_branding_parity_wave3.py",
    },
    "frontend/qubit-web/src/pages/CronPage.tsx": {
        "patch": "Qubit fix: null-safe cron delivery-target handling "
                 "(`available ?? []`, `selected ?? []`, `deliveryTargets ?? []`)",
        "reason": "Qubit runtime fix so the vendored cron page does not crash "
                  "when a partially loaded cron response omits a list; behavior "
                  "is unchanged when the fields are present.",
        "affected_regression_test": "tests/test_qubit_frontend_inventory8u.py",
    },
    "frontend/qubit-web/src/pages/DocsPage.tsx": {
        "patch": "Qubit rebrand: label the vendored docs iframe/link as upstream "
                 "vendor documentation, keep the real provenance URL, and stop "
                 "presenting it as Qubit's own docs",
        "reason": "Qubit identity promise (owner directive 2026-09-15): normal "
                  "surfaces render Qubit while the genuine upstream docs URL is "
                  "retained for attribution. Presentation-only; no logic or "
                  "backend-route change.",
        "affected_regression_test": "tests/test_branding_leaks_fixes.py",
    },
    "frontend/qubit-web/src/components/ToolsetConfigDrawer.tsx": {
        "patch": "Qubit rebrand: the provider auth badge reads 'Qubit Labs' "
                 "instead of the upstream company/portal name",
        "reason": "Qubit identity promise (owner directive 2026-09-19): no "
                  "normal user-facing surface may render the upstream company "
                  "or portal name as product branding. The wire field "
                  "`requires_nous_auth` is unchanged for compatibility.",
        "affected_regression_test": "tests/test_branding_leaks_fixes.py",
    },
    "frontend/qubit-web/src/pages/ConfigPage.tsx": {
        "patch": "Qubit settings write: handle the backend CONFIRM_REQUIRED "
                 "staging on Save (confirm, then re-PUT with confirm=true) so "
                 "the Settings Save control is not a dead 409",
        "reason": "Qubit contract: settings writes are confirm-gated and "
                  "audited; the vendored page must complete the shared "
                  "approval flow instead of leaving an unreachable control.",
        "affected_regression_test": "tests/test_qubit_config_fixes.py",
    },
    "frontend/qubit-web/src/lib/api.ts": {
        "patch": "wave2/public-rebrand-api-prefix + Qubit config confirm flag",
        "reason": "Qubit-owned public API prefix (/api/<x> -> /agent/api/<x>) "
                  "plus a confirm flag on saveConfig so the Settings page can "
                  "complete the confirm-gated settings write. No vendor "
                  "business logic changed.",
        "affected_regression_test": "tests/test_qbpv2_rebrand_public_surface.py",
    },
    "frontend/qubit-web/src/App.tsx": {
        "patch": "Qubit rebrand of product strings + route-identity "
                 "disambiguation: resolve overlay plugin nav labels through the "
                 "shared ROUTE_TITLES map and keep hidden plugin tabs in the "
                 "page-header title metadata",
        "reason": "Dashboard control fixes D8/D9/D10/D13 (owner directive "
                  "2026-09-15): the Qubit overlay must be distinguishable from "
                  "the vendored duplicate routes in the sidebar and page header, "
                  "and hidden routes (/qubit-approvals, /qubit-brand) must "
                  "resolve a real title. Presentation/routing metadata only; no "
                  "route deleted, no backend-route change.",
        "affected_regression_test": "tests/test_dashboard_control_fixes.py",
    },
    "frontend/qubit-web/src/lib/resolve-page-title.ts": {
        "patch": "Qubit route-identity map (ROUTE_TITLES) applied before the "
                 "plugin-tab lookup so duplicate overlay routes get distinct "
                 "page titles and hidden routes no longer fall through to the "
                 "mangled capitalize fallback",
        "reason": "Dashboard control fixes D13 (mangled titles for "
                  "/qubit-approvals, /qubit-brand) and D8/D9/D10 (Qubit Boards "
                  "vs /kanban, Runtime Inventory vs /devices): the page header "
                  "must render a distinct Qubit identity instead of a generic "
                  "manifest label or raw path. Presentation-only; no route or "
                  "backend change.",
        "affected_regression_test": "tests/test_dashboard_control_fixes.py",
    },
})

# Wave-17 dashboard-control fixes (drift A19-A23) and the sidebar latency
# readout: documented Qubit patches of diverging vendored files.
_PATCH_SPEC.update({
    "frontend/qubit-web/src/pages/ModelsPage.tsx": {
        "patch": "wave17/models-use-as: add the Provider profiles panel whose "
                 "per-profile 'Use as' action calls the canonical profile "
                 "set-active op (POST /agent/api/profiles/active)",
        "reason": "Dashboard parity A20: the pinned /models page's 'USE AS' "
                  "provider-selection action is re-anchored on the canonical "
                  "Qubit brain profiles. The panel reads api.getProfiles() / "
                  "api.getActiveProfile() and routes selection through "
                  "api.setActiveProfile, the same shared profile op the CLI and "
                  "voice paths use (canonical provider/profile resolver); no "
                  "backend route removed and no vendor logic changed.",
        "affected_regression_test":
            "frontend/qubit-web/src/pages/ModelsPage.test.tsx",
    },
    "frontend/qubit-web/src/components/SidebarStatusStrip.tsx": {
        "patch": "wave17/sidebar-latency: render the sampled gateway "
                 "round-trip (formatLatency) in the System sidebar status block "
                 "when a latency sample is available",
        "reason": "Dashboard parity: the Qubit shell samples the gateway "
                  "round-trip and surfaces it in the existing status strip; the "
                  "optional latencyMs prop leaves the upstream render path "
                  "unchanged when absent. Presentation-only; no logic, route, "
                  "or backend-route change.",
        "affected_regression_test":
            "frontend/qubit-web/src/lib/latency-sampler.test.ts",
    },
    "frontend/qubit-web/src/pages/SessionsPage.test.tsx": {
        "patch": "wave17/sessions-tests: pin the Chats/Automation/All and "
                 "Overview/History tab families and the Escape-dismissal of the "
                 "'Session source' popover",
        "reason": "Dashboard parity A19 + the live2 a11y finding: the vendored "
                  "sessions test is extended to exercise the real Overview panel "
                  "and the source-popover dismissal contract. Test-only; no "
                  "runtime dependency or backend-route change.",
        "affected_regression_test":
            "frontend/qubit-web/src/pages/SessionsPage.test.tsx",
    },
})

# Wave-17 chat-feed migration: the vendored ChatSidebar moves from the retired
# WebSocket transports to the Qubit-native authenticated SSE feed.
_PATCH_SPEC.update({
    "frontend/qubit-web/src/components/ChatSidebar.tsx": {
        "patch": "wave17/chat-sse-feed: replace the two WebSocket transports "
                 "(`/api/ws` JSON-RPC sidecar + `/api/events` subscriber) with "
                 "the authenticated native SSE reader (`@/lib/events-sse`, "
                 "`GET /api/events/stream`); resume from the durable journal via "
                 "`?last_seq=`, treat 401/403 as terminal, and never open a "
                 "socket.",
        "reason": "Qubit keeps exactly one event path and the upstream "
                  "WebSockets (`/api/ws`, `/api/events`) answer HTTP 426 "
                  "`WS_SINGLE_PATH`, so the vendored sidebar had no live feed. "
                  "It now consumes the Qubit-native authenticated SSE stream "
                  "served by `voicebridge/webui.py:_events_stream` (fetch + a "
                  "stream frame parser, because `EventSource` cannot attach the "
                  "dashboard auth header); the model badge/capabilities still "
                  "come from REST. No backend route removed and no vendor "
                  "business logic changed.",
        "affected_regression_test":
            "frontend/qubit-web/src/components/ChatSidebar.test.tsx",
    },
    "frontend/qubit-web/src/components/ChatSidebar.test.tsx": {
        "patch": "wave17/chat-sse-feed-tests: rewrite the vendored WebSocket "
                 "transport tests to pin the SSE contract — authenticated fetch "
                 "to `/api/events/stream`, id/data frame dispatch, `?last_seq=` "
                 "resume on reconnect, terminal auth rejection, and abort on "
                 "unmount; asserts no WebSocket is opened.",
        "reason": "Test-only companion to the ChatSidebar SSE migration; the "
                  "vendored test exercised the retired `/api/events` WebSocket, "
                  "which Qubit refuses with 426 `WS_SINGLE_PATH`. No runtime "
                  "dependency or backend-route change.",
        "affected_regression_test":
            "frontend/qubit-web/src/components/ChatSidebar.test.tsx",
    },
})

# Scope config for the vendored-frontend comparison ledger.
#: Vendored-frontend files the RR2 identifier rename moved (upstream-derived
#: local path -> shipped Qubit path). The comparison reads the shipped path.
_LOCAL_PATH_RENAMES: dict[str, str] = {
    "frontend/qubit-web/src/components/HermesConsoleModal.tsx":
        "frontend/qubit-web/src/components/QubitConsoleModal.tsx",
}

_COMPARISON_SCOPES = [
    {
        "scope": "hermes-agent/web",
        "category": "dashboard",
        "rev": PIN,
        "upstream_dir": "web",
        "upstream_root": "hermes",
        "local_dir": "frontend/qubit-web",
        "default_reason": "Hermes React dashboard source; vendored "
                          "byte-identical as the Qubit baseline.",
        "default_test": "tests/test_qubit_vendor8tf.py",
    },
    {
        "scope": "hermes-agent/shared",
        "category": "dashboard-lib",
        "rev": PIN,
        "upstream_dir": "apps/shared",
        "upstream_root": "hermes",
        "local_dir": "frontend/qubit-shared",
        "default_reason": "Shared @qubit/shared lib imported by web/; vendored "
                          "byte-identical.",
        "default_test": "tests/test_qubit_vendor8tf.py",
    },
    {
        "scope": "hermes-agent/apps/desktop",
        "category": "desktop-shell",
        "rev": PIN,
        "upstream_dir": "apps/desktop",
        "upstream_root": "hermes",
        "omit_reason": "Electron desktop shell hosting the same web UI; not "
                       "shipped in this packet \u2014 Qubit serves the identical "
                       "web UI from its own backend (frontend/qubit-web "
                       "build). Desktop packaging is a follow-up build target; "
                       "counterpart: Qubit distribution + web UI.",
    },
    {
        "scope": "hermes-agent/ui-tui",
        "category": "terminal-ui",
        "rev": PIN,
        "upstream_dir": "ui-tui",
        "upstream_root": "hermes",
        "omit_reason": "Ink terminal UI; not shipped \u2014 counterpart is the "
                       "Qubit-native TUI live board (voicebridge/terminal.py, "
                       "CLI `qubit tui`) on the same backend.",
    },
    {
        "scope": "hermes-agent/tui_gateway",
        "category": "gateway-backend",
        "rev": PIN,
        "upstream_dir": "tui_gateway",
        "upstream_root": "hermes",
        "omit_reason": "Python JSON-RPC gateway backend for the TUI/WS "
                       "dialect; not shipped \u2014 counterpart is the Qubit "
                       "broker + event stream + WS/app protocol in voicebridge "
                       "(single authority).",
    },
    {
        "scope": "hermes-agent/website",
        "category": "marketing-site",
        "rev": PIN,
        "upstream_dir": "website",
        "upstream_root": "hermes",
        "omit_reason": "Docusaurus marketing/docs site (tutorials, "
                       "screenshots); not product UI and not shipped \u2014 "
                       "counterpart: Qubit docs/ + dashboard Docs page.",
    },
    {
        "scope": "openclaw/ui",
        "category": "openclaw-dashboard",
        "rev": OPENCLAW_PIN,
        "upstream_dir": "ui",
        "upstream_root": "openclaw",
        "omit_reason": "openclaw-dashboard",
    },
    {
        "scope": "hermes-agent/plugins",
        "category": "dashboard",
        "rev": PIN,
        "upstream_dir": "plugins",
        "upstream_root": "hermes",
        "local_dir": "frontend/qubit-web/public/plugins",
        "default_reason": "Vendored pinned dashboard plugin bundle copied into "
                          "the SPA static root.",
        "default_test": "tests/test_dashboard_plugin_fleet.py",
        "select": [
            "hermes-achievements/dashboard/dist/index.js",
            "hermes-achievements/dashboard/dist/style.css",
            "kanban/dashboard/dist/index.js",
            "kanban/dashboard/dist/style.css",
        ],
    },
]

_WAVE3_NOTE = (
    "Wave 3 vendored-frontend entries from agent C's "
    "docs/archive/2026-09-21/HERMES_SOURCE_COMPARISON.wave3.diff are merged: the three patched "
    "src/plugins rows and the four byte-identical SPA-shipped plugin bundles "
    "(scope hermes-agent/plugins) are in rows[] with exact patch + reason + "
    "qubit_sha256; every Qubit-added file that has no upstream row is recorded "
    "under wave3.added with an on-disk affected_regression_test — the static "
    "fleet (src/plugins/fleet.ts, public/plugins/fleet.json), the render/refusal "
    "helpers (src/lib/rebrand.ts, src/lib/policy-refusal.ts) with their tests, "
    "the Qubit-native chat-session sidebar test, the wave-17 latency sampler "
    "(src/lib/latency-sampler.ts/.test.ts), the authenticated chat-feed SSE "
    "reader (src/lib/events-sse.ts/.test.ts), and the wave-17 page contract "
    "tests (DocsPage/EnvPage/ModelsPage/SkillsPage .test.tsx). Qubit "
    "visual-identity "
    "work is overlay-only (frontend/qubit-app/**). All hashes are validated "
    "by tests/test_branding_parity_wave3.py. NOTE: the pinned "
    "Achievements/Kanban bundles are Qubit-rebranded (wave-3 rebrand + wave-17 "
    "branding audit/A28 share actions); see docs/BRANDING_AUDIT_WAVE3.md."
)

_WAVE3_ADDED = [
    {
        "qubit": "frontend/qubit-web/src/pages/ChatPage.voice9u.test.tsx",
        "role": "Qubit-added contract test for the /qubit-chat voice-status "
                "strip (active engine, Right-Ctrl state, privacy/fallback "
                "reason); no upstream counterpart.",
        "affected_regression_test": "tests/test_branding_parity_wave3.py",
    },
    {
        "qubit": "frontend/qubit-web/src/lib/wsTicket.test.ts",
        "role": "Qubit-added contract test for the Qubit-owned WS-ticket "
                "client (`POST /agent/api/auth/ws-ticket`) that the /chat "
                "terminal upgrade uses; no upstream counterpart.",
        "affected_regression_test": "tests/test_branding_parity_wave3.py",
    },
    {
        "qubit": "frontend/qubit-web/src/components/ChatSessionArtifacts.tsx",
        "role": "Qubit-added /qubit-chat artifacts panel: renders the "
                "canonical session comments, structured CI-detail rows, and "
                "per-session drafts from the Qubit chat-transport authority; "
                "no upstream counterpart.",
        "affected_regression_test": "tests/test_qbpv2_agent3_workflows.py",
    },
    {
        "qubit": "frontend/qubit-web/src/pages/AgentsPage.tsx",
        "role": "Qubit-added /agents workspace: renders the canonical "
                "GET /api/qubit/agents roster (worker lane + runtime "
                "adapters, read-only); no upstream counterpart.",
        "affected_regression_test": "tests/test_dashboard_agent3_wiring.py",
    },
    {
        "qubit": "frontend/qubit-web/src/pages/AgentsPage.test.tsx",
        "role": "Qubit-added contract test for the /agents workspace "
                "(roster render + refresh), with no upstream counterpart.",
        "affected_regression_test": "tests/test_dashboard_agent3_wiring.py",
    },
    {
        "qubit": "frontend/qubit-web/src/plugins/fleet.ts",
        "role": "Qubit-added static plugin-fleet discovery: fetch "
                "<base>/plugins/fleet.json (SPA-shipped manifests) and resolve "
                "each manifest's asset root. Keeps the vendored compatibility "
                "backend as the operation authority while surfacing the real "
                "pinned plugin tabs.",
        "affected_regression_test": "tests/test_dashboard_plugin_fleet.py",
    },
    {
        "qubit": "frontend/qubit-web/public/plugins/fleet.json",
        "role": "Qubit-authored static fleet manifest aggregate: mirrors the "
                "two pinned dashboard manifests (tab paths/positions, entry, "
                "css) with a Qubit-neutral description; verified by "
                "tests/test_dashboard_plugin_fleet.py::"
                "test_static_fleet_manifest_mirrors_pinned_plugins.",
        "affected_regression_test": "tests/test_dashboard_plugin_fleet.py",
    },
    {
        "qubit": "frontend/qubit-web/src/lib/rebrand.ts",
        "role": "Qubit-added product-identity normalizer applied at the render "
                "boundary to backend free-text (MCP/Channels/Config) so served "
                "pages never expose an upstream product name; upstream names "
                "remain only in vendored source/provenance records.",
        "affected_regression_test": "tests/test_qubit_frontend_inventory8u.py",
    },
    {
        "qubit": "frontend/qubit-web/src/pages/SystemPage.test.tsx",
        "role": "Qubit-added contract test for the System page power/e-stop "
                "control (live latch badge, confirm-dialog latch -> "
                "powerAction('emergency-stop'), latched reset state); no "
                "upstream counterpart.",
        "affected_regression_test": "tests/test_branding_parity_wave3.py",
    },
    {
        "qubit": "frontend/qubit-web/src/components/ChatSessionList.test.tsx",
        "role": "Qubit-added contract test for the Qubit-native chat-session "
                "store sidebar (ChatSessionList.tsx): pins "
                "`api.listChatSessions`, the rendered rows, refresh, and the "
                "`?resume=<id>` selection driving the persisted SSE transcript "
                "(no upstream counterpart; the vendored component test is "
                "absent).",
        "affected_regression_test": "tests/test_branding_parity_wave3.py",
    },
    {
        "qubit": "frontend/qubit-web/src/lib/policy-refusal.ts",
        "role": "Qubit-added typed policy-refusal helper: parses the backend's "
                "typed denied envelope (code, detail, qubit_counterpart) so "
                "dashboard actions render honest guidance instead of a raw "
                "error (no upstream counterpart).",
        "affected_regression_test": "tests/test_dashboard_control_fixes.py",
    },
    {
        "qubit": "frontend/qubit-web/src/lib/policy-refusal.test.ts",
        "role": "Qubit-added unit test for the typed policy-refusal helper.",
        "affected_regression_test": "tests/test_dashboard_control_fixes.py",
    },
    {
        "qubit": "frontend/qubit-web/src/lib/latency-sampler.ts",
        "role": "Qubit-added connection-latency sampler for the dashboard "
                "shell: EWMA-smoothed gateway round-trip plus a shared "
                "human-readable formatter. Plain value object (no timers/DOM); "
                "the shell owns the sampling cadence.",
        "affected_regression_test":
            "frontend/qubit-web/src/lib/latency-sampler.test.ts",
    },
    {
        "qubit": "frontend/qubit-web/src/lib/latency-sampler.test.ts",
        "role": "Qubit-added unit test for the latency sampler (EWMA smoothing, "
                "rejected invalid/negative samples, reset, and formatting).",
        "affected_regression_test":
            "frontend/qubit-web/src/lib/latency-sampler.test.ts",
    },
    {
        "qubit": "frontend/qubit-web/src/pages/DocsPage.test.tsx",
        "role": "Qubit-added contract test for the /docs OpenAPI reference "
                "(drift A23): renders the served OAS operations grouped by "
                "namespace and narrowed by the header search input.",
        "affected_regression_test":
            "frontend/qubit-web/src/pages/DocsPage.test.tsx",
    },
    {
        "qubit": "frontend/qubit-web/src/pages/EnvPage.test.tsx",
        "role": "Qubit-added contract test for the /env six-tab surface "
                "(drift A22): every section is structural with an empty "
                "catalog and each header tab jumps to its card.",
        "affected_regression_test":
            "frontend/qubit-web/src/pages/EnvPage.test.tsx",
    },
    {
        "qubit": "frontend/qubit-web/src/pages/ModelsPage.test.tsx",
        "role": "Qubit-added contract test for the /models provider-profile "
                "'Use as' actions (drift A20): one action per profile, "
                "aria-pressed on the active one, and a click routed through the "
                "canonical setActiveProfile op.",
        "affected_regression_test":
            "frontend/qubit-web/src/pages/ModelsPage.test.tsx",
    },
    {
        "qubit": "frontend/qubit-web/src/pages/SkillsPage.test.tsx",
        "role": "Qubit-added contract test for the /skills category filter and "
                "live header search (drift A21), including the typed empty "
                "state when nothing matches.",
        "affected_regression_test":
            "frontend/qubit-web/src/pages/SkillsPage.test.tsx",
    },
    {
        "qubit": "frontend/qubit-web/src/lib/events-sse.ts",
        "role": "Qubit-added authenticated SSE reader for the single Qubit "
                "event path (`GET /api/events/stream`, served by "
                "`voicebridge/webui.py:_events_stream`): fetch + "
                "ReadableStream/TextDecoder frame parser (because `EventSource` "
                "cannot attach the dashboard auth header), `?last_seq=` journal "
                "replay cursor, id/event/data frames, comment heartbeats, and "
                "the `resync` overflow sentinel. No upstream counterpart: the "
                "vendored sidebar used WebSockets, which Qubit refuses with "
                "HTTP 426 `WS_SINGLE_PATH`.",
        "affected_regression_test":
            "frontend/qubit-web/src/lib/events-sse.test.ts",
    },
    {
        "qubit": "frontend/qubit-web/src/lib/events-sse.test.ts",
        "role": "Qubit-added unit test for the SSE frame parser and replay-URL "
                "builder (partial-tail buffering, heartbeat/retry-block "
                "filtering, multi-line data joins, the resync sentinel, and "
                "base-path/cursor handling).",
        "affected_regression_test":
            "frontend/qubit-web/src/lib/events-sse.test.ts",
    },
    {
        "qubit": "frontend/qubit-web/src/lib/chat-routes.ts",
        "role": "Qubit-added canonical chat-route predicate (owner chat "
                "mapping): `/chat` and `/qubit-chat` are the two equivalent "
                "chat routes rendering the single shared ChatPage host. Keeps "
                "host visibility, header layout, and nav highlighting on one "
                "source of truth so the routes cannot drift into a second "
                "implementation. No upstream counterpart.",
        "affected_regression_test":
            "frontend/qubit-web/src/lib/chat-routes.test.ts",
    },
    {
        "qubit": "frontend/qubit-web/src/lib/chat-routes.test.ts",
        "role": "Qubit-added unit test for the canonical chat-route predicate "
                "(both routes match, trailing slash tolerated, prefix "
                "collisions rejected).",
        "affected_regression_test":
            "frontend/qubit-web/src/lib/chat-routes.test.ts",
    },
    {
        "qubit": "frontend/qubit-web/src/pages/TerminalChatPage.tsx",
        "role": "Qubit-added `/chat` terminal frontend: mounts the vendored "
                "xterm terminal and streams the real vendor PTY over the single "
                "Qubit WebSocket upgrade path (`/agent/api/pty`) with a "
                "short-lived single-use scoped ticket. `/qubit-chat` keeps the "
                "Qubit-native ChatPage (owner decision 2026-09-17).",
        "affected_regression_test": "tests/test_pty_ws8u.py",
    },
]

# Qubit-authored overlay files that were explicitly REMOVED by a later owner
# directive. Recorded (not silently dropped) so historical handoff diffs that
# reference them remain accounted for while the current tree no longer ships
# them. Owner chat mapping 2026-09-17: the card-only `/qubit-chat` overlay
# plugin was removed in favor of the built-in `/chat` + `/qubit-chat` routes
# that render one shared ChatPage.
_WAVE3_REMOVED = [
    {
        "qubit": "frontend/qubit-app/plugins/qubit-chat/qubit-chat.js",
        "reason": "Removed by the 2026-09-17 owner chat mapping: `/qubit-chat` "
                  "is no longer a card-only overlay page; it is a built-in SPA "
                  "route rendering the same ChatPage as `/chat`.",
        "affected_regression_test":
            "tests/test_dashboard_page_closure_wave2.py",
    },
    {
        "qubit": "frontend/qubit-app/plugins/qubit-chat/manifest.json",
        "reason": "Removed with the card-only `/qubit-chat` overlay plugin "
                  "(owner chat mapping 2026-09-17).",
        "affected_regression_test":
            "tests/test_dashboard_page_closure_wave2.py",
    },
]

_OVERLAY_README_ROLE = "Overlay integration notes / plugin authoring rules."
_OVERLAY_PLUGIN_ROLE = "Qubit overlay plugin asset (manifest/bundle)."
_OVERLAY_THEME_ROLE = ("Qubit visual identity theme served by "
                       "GET /api/dashboard/themes (active).")

_PROVENANCE_SCHEMA = {
    "purpose": "Per-file provenance for every vendored Hermes/OpenClaw file. "
               "Unmodified source stays byte-identical; every divergence is a "
               "documented Qubit patch with a regression test.",
    "fields": {
        "upstream": "pinned upstream path at upstream_rev",
        "upstream_blob_sha256": "sha256 of the pinned upstream blob content; "
                                "identical to upstream_sha256 (kept for the "
                                "dashboard-rebuild contract wording). null when "
                                "the file has no upstream counterpart.",
        "qubit": "vendored local path (or null when intentionally omitted)",
        "qubit_sha256": "sha256 of the on-disk vendored file",
        "patch": "exact patch id / rebrand description; null when the vendored "
                 "file is byte-identical to upstream",
        "reason": "why the patch/add exists and what behaviour it affects",
        "affected_regression_test": "on-disk test that fails if the divergence "
                                    "or provenance record regresses",
    },
    "added_files": "Qubit-authored files with no upstream counterpart live "
                   "under wave3.added / wave4.added, each with qubit_sha256, "
                   "role, and affected_regression_test.",
}


def _git(args, cwd):
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True, capture_output=True, text=True,
    ).stdout


def _git_bytes(args, cwd):
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True, capture_output=True,
    ).stdout


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel(path: Path) -> str:
    try:
        return path.relative_to(REPO).as_posix()
    except ValueError:
        return str(path)


def _is_skipped(rel: str) -> bool:
    parts = set(Path(rel).parts)
    if parts & _SKIP_DIR_PARTS:
        return True
    return rel.endswith(_SKIP_SUFFIX)


def _git_tracked_modes(local_label: str, repo: Path) -> dict[str, str]:
    """rel -> git mode for every blob tracked under ``local_label`` in the index.

    Uses the local repository's index (``git ls-files``), so a file that exists
    on disk but is excluded by the vendored upstream ``.gitignore`` is reported
    as *not tracked* (it would be absent from a fresh clone) until it is
    force-added. Once force-added it is in the index and counts as tracked, so
    the manifest reports zero gaps.
    """
    if not local_label or Path(local_label).is_absolute():
        return {}
    try:
        out = _git(["ls-files", "--stage", "--", local_label], repo)
    except subprocess.CalledProcessError:
        return {}
    prefix = local_label.rstrip("/") + "/"
    modes: dict[str, str] = {}
    for line in out.splitlines():
        meta, path = line.split("\t", 1)
        mode = meta.split()[0]
        if path.startswith(prefix):
            modes[path[len(prefix):]] = mode
    return modes


def _check_ignore(local_label: str, rel: str, repo: Path) -> str:
    """The ``.gitignore`` source line that excludes the path, or ''."""
    try:
        return _git(["check-ignore", "-v", f"{local_label}/{rel}"], repo).strip()
    except subprocess.CalledProcessError:
        return ""


def _check_revision(upstream: Path, expected: str, label: str) -> str:
    if not upstream.is_dir():
        raise SystemExit(f"{label} pin not found: {upstream}")
    rev = _git(["rev-parse", "HEAD"], upstream).strip()
    if rev != expected:
        raise SystemExit(f"REFUSING: {label} revision {rev} != pinned {expected}")
    return rev


def _ls_tree(upstream: Path, rev: str) -> dict[str, tuple[str, str]]:
    """Return rel -> (mode, blob_hash) for tracked blobs at ``rev``."""
    out: dict[str, tuple[str, str]] = {}
    for line in _git(["ls-tree", "-r", rev], upstream).splitlines():
        meta, path = line.split("\t", 1)
        mode, otype, blob = meta.split()
        if otype != "blob" or _is_skipped(path):
            continue
        out[path] = (mode, blob)
    return out


def _eol_map(upstream: Path) -> dict[str, tuple[str, str]]:
    """rel -> (index_eol, worktree_eol) from ``git ls-files --eol``.

    A worktree eol differing from the index eol (e.g. upstream
    ``.gitattributes`` sets ``*.ps1 text eol=crlf``) means the checked-out
    bytes differ from the committed blob by line endings only.
    """
    out: dict[str, tuple[str, str]] = {}
    for line in _git(["ls-files", "--eol"], upstream).splitlines():
        meta, _, path = line.partition("\t")
        fields = meta.split()
        if len(fields) >= 2:
            out[path] = (fields[0], fields[1])
    return out


def build_vendor_manifest(upstream: Path, local: Path, local_label: str) -> dict:
    rev = _check_revision(upstream, PIN, "upstream")
    tree = _ls_tree(upstream, rev)
    eol = _eol_map(upstream)

    files: dict[str, dict] = {}
    counts = {c: 0 for c in _CLASSIFICATIONS}
    counts[UNEXPLAINED] = 0
    eol_normalized: list[dict] = []
    blockers: list[str] = []
    patched: list[str] = []
    added: list[str] = []
    missing: list[str] = []
    omitted: list[str] = []

    # Local git-index modes must be known before enumeration so an untracked
    # disposable artifact (installed dependency / build output) is skipped while
    # a tracked upstream file under the same directory name is kept.
    local_git_modes = _git_tracked_modes(local_label, REPO)

    local_rels: list[str] = []
    for path in sorted(local.rglob("*")):
        if not path.is_file() and not path.is_symlink():
            continue
        rel = path.relative_to(local).as_posix()
        if _is_skipped(rel):
            continue
        if (_DISPOSABLE_DIR_PARTS & set(Path(rel).parts)
                and rel not in local_git_modes):
            continue
        local_rels.append(rel)
    local_set = set(local_rels)

    # Path-rename awareness: upstream rel -> shipped Qubit rel. A renamed file
    # is compared against its upstream original, never reported missing/extra.
    upstream_local = {_local_from_upstream(rel): rel for rel in tree}
    renamed: list[dict] = []

    def _blob_sha(blob_hash: str) -> str:
        return _sha_bytes(_git_bytes(["cat-file", "blob", blob_hash], upstream))

    for rel in sorted(local_rels):
        path = local / rel
        is_symlink = path.is_symlink()
        data = path.read_bytes() if path.is_file() else path.readlink().as_posix().encode()
        local_sha = _sha_bytes(data)
        local_fs_mode = ("120000" if is_symlink
                         else "100755" if os.access(path, os.X_OK)
                         else "100644")
        local_git_mode = local_git_modes.get(rel)
        up_rel = upstream_local.get(rel)
        up = tree.get(up_rel) if up_rel is not None else None
        if up is None:
            classification = QUBIT_ADDED
            added.append(rel)
            counts[QUBIT_ADDED] += 1
            files[rel] = {
                "sha256": local_sha,
                "upstream_blob": None,
                "upstream_sha256": None,
                "upstream_blob_sha256": None,
                "upstream_mode": None,
                "local_git_mode": local_git_mode,
                "local_fs_mode": local_fs_mode,
                "symlink": is_symlink,
                "git_tracked": local_git_mode is not None,
                "patched": False,
                "classification": classification,
            }
            continue
        mode, blob_hash = up
        blob_sha = _blob_sha(blob_hash)
        up_file = upstream / up_rel
        up_worktree_sha = _sha_file(up_file) if up_file.is_file() else None
        blob_equals_local = (not is_symlink) and local_sha == blob_sha
        worktree_equals_local = up_worktree_sha is not None and local_sha == up_worktree_sha
        index_eol, worktree_eol = eol.get(up_rel, (None, None))
        is_renamed = up_rel != rel
        if is_renamed:
            renamed.append({"from": up_rel, "to": rel})

        if blob_equals_local:
            classification = UNCHANGED
        elif rel in _QUBIT_PATCH_SPEC:
            classification = QUBIT_PATCHED
            patched.append(rel)
        elif rel in _OPENCLAW_OVERLAY:
            classification = OPENCLAW_OVERLAY
        elif worktree_equals_local and index_eol != worktree_eol:
            classification = EOL_NORMALIZED
            eol_normalized.append({
                "path": rel,
                "upstream_blob": blob_hash,
                "blob_sha256": blob_sha,
                "upstream_worktree_sha256": up_worktree_sha,
                "local_sha256": local_sha,
                "reason": "Upstream .gitattributes pins this file to eol=crlf, "
                          "so the pinned index blob stores LF while the "
                          "checkout writes CRLF. The fork is byte-identical to "
                          "the pinned checkout and differs from the committed "
                          "blob only by line endings.",
                "provenance": f"{UPSTREAM_REPO}@{PIN} ({UPSTREAM_LICENSE})",
                "affected_regression_test":
                    "tests/test_surface_reachability8u.py",
            })
        elif is_renamed or _is_deep_rebrand_path(rel):
            # Deep source rebrand content change (path rename and/or
            # product-facing branding strings), owned by the 2026-09-19
            # amendment and recorded per file in the patch manifest.
            classification = QUBIT_PATCHED
            patched.append(rel)
        else:
            classification = UNEXPLAINED
            blockers.append(rel)
        counts[classification] += 1
        files[rel] = {
            "sha256": local_sha,
            "upstream_blob": blob_hash,
            "upstream_sha256": up_worktree_sha if up_worktree_sha else blob_sha,
            "upstream_blob_sha256": blob_sha,
            "upstream_mode": mode,
            "local_git_mode": local_git_mode,
            "local_fs_mode": local_fs_mode,
            "symlink": is_symlink,
            "git_tracked": local_git_mode is not None,
            "patched": classification == QUBIT_PATCHED,
            "classification": classification,
        }

    covered_upstream = {upstream_local[r] for r in local_set
                        if r in upstream_local}
    for rel in sorted(set(tree) - covered_upstream):
        reason = _INTENTIONAL_OMISSIONS.get(rel)
        if reason is None:
            classification = MISSING
            missing.append(rel)
            blockers.append(rel)
        else:
            classification = INTENTIONALLY_OMITTED
            omitted.append(rel)
        counts[classification] += 1

    # --- mode / symlink comparison against the pinned upstream tree ---------
    mode_divergences: list[dict] = []
    symlink_divergences: list[dict] = []
    git_index_gaps: list[dict] = []
    for rel in sorted(local_set):
        up_rel = upstream_local.get(rel)
        if up_rel is None:
            continue
        up_mode = tree[up_rel][0]
        entry = files[rel]
        git_mode = entry["local_git_mode"]
        if git_mode is not None and git_mode != up_mode:
            mode_divergences.append({
                "path": rel,
                "upstream_mode": up_mode,
                "local_git_mode": git_mode,
                "local_fs_mode": entry["local_fs_mode"],
                "reason": "Local git index records a different file mode than "
                          "the pinned upstream blob; the executable bit must be "
                          "committed to stay byte/mode faithful.",
                "affected_regression_test": "tests/test_surface_reachability8u.py",
            })
        if (up_mode == "120000") != entry["symlink"]:
            symlink_divergences.append({
                "path": rel,
                "upstream_mode": up_mode,
                "local_is_symlink": entry["symlink"],
                "reason": "Symlink status differs from the pinned upstream blob.",
                "affected_regression_test": "tests/test_surface_reachability8u.py",
            })
        if git_mode is None:
            git_index_gaps.append({
                "path": rel,
                "upstream_mode": up_mode,
                "upstream_blob": tree[up_rel][1],
                "on_disk_sha256": entry["sha256"],
                "ignored_by": _check_ignore(local_label, rel, REPO) or None,
                "reason": "Upstream tracks this file but the local git index "
                          "does not: the vendored upstream .gitignore excludes "
                          "it and it was not force-added, so a fresh clone "
                          "would be missing a byte-identical upstream file.",
                "affected_regression_test": "tests/test_surface_reachability8u.py",
            })

    def _mode_counts(getter) -> dict[str, int]:
        out: dict[str, int] = {}
        for entry in files.values():
            key = getter(entry)
            if key is None:
                continue
            out[key] = out.get(key, 0) + 1
        return dict(sorted(out.items()))

    tracking_blockers = sorted(
        {d["path"] for d in mode_divergences}
        | {d["path"] for d in symlink_divergences}
        | {d["path"] for d in git_index_gaps})

    manifest = {
        "upstream_repo": UPSTREAM_REPO,
        "upstream_revision": rev,
        "upstream_tag": UPSTREAM_TAG,
        "upstream_license": UPSTREAM_LICENSE,
        "local_root": local_label,
        "file_count": len(files),
        "upstream_file_count": len(tree),
        "local_git_file_count": len(local_git_modes),
        "identical": counts[UNCHANGED] + counts[EOL_NORMALIZED],
        "byte_identical_to_blob": counts[UNCHANGED],
        "eol_normalized_count": counts[EOL_NORMALIZED],
        "patched": sorted(patched),
        "patch_manifest": {
            rel: dict(_QUBIT_PATCH_SPEC.get(rel, _DEEP_REBRAND_PATCH))
            for rel in sorted(patched)
        },
        "path_renames": sorted(renamed, key=lambda r: r["from"]),
        "renamed_count": len(renamed),
        "extra_local": sorted(added),
        "missing_from_local": sorted(missing),
        "intentionally_omitted": sorted(omitted),
        "classification_counts": counts,
        "eol_normalized": eol_normalized,
        "upstream_mode_counts": _mode_counts(lambda e: e["upstream_mode"]),
        "local_git_mode_counts": _mode_counts(lambda e: e["local_git_mode"]),
        "local_fs_mode_counts": _mode_counts(lambda e: e["local_fs_mode"]),
        "symlink_count_upstream": sum(
            1 for r in tree if tree[r][0] == "120000"),
        "symlink_count_local": sum(1 for e in files.values() if e["symlink"]),
        "mode_divergences": mode_divergences,
        "symlink_divergences": symlink_divergences,
        "git_index_gaps": git_index_gaps,
        "tracking_blockers": tracking_blockers,
        "blockers": sorted(set(blockers)),
        "files": files,
    }
    return manifest


def _iter_upstream_files(root: Path) -> list[str]:
    out = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part in _EXCLUDE_COMPARE_DIRS for part in p.parts):
            continue
        out.append(p.relative_to(root).as_posix())
    return out


def build_comparison(upstream: Path, openclaw: Path) -> dict:
    _check_revision(upstream, PIN, "upstream")
    _check_revision(openclaw, OPENCLAW_PIN, "openclaw")
    roots = {"hermes": upstream, "openclaw": openclaw}
    rows: list[dict] = []
    for spec in _COMPARISON_SCOPES:
        up_root = roots[spec["upstream_root"]] / spec["upstream_dir"]
        if not up_root.is_dir():
            raise SystemExit(f"comparison upstream root missing: {up_root}")
        rels = spec.get("select") or _iter_upstream_files(up_root)
        if spec.get("select"):
            rels = sorted(spec["select"])
        for rel in rels:
            up_path = up_root / rel
            # The comparison rows key ``upstream`` by the scope-relative path
            # (``shared/...``, ``apps/desktop/...``, ``web/...``, ``ui/...``),
            # which is what the vendor guard enumerates on disk.
            upstream_field = f"{spec['scope'].split('/', 1)[1]}/{rel}"
            upstream_sha = _sha_file(up_path)
            row = {
                "scope": spec["scope"],
                "category": spec["category"],
                "upstream": upstream_field,
                "upstream_rev": spec["rev"],
                "upstream_sha256": upstream_sha,
            }
            local_dir = spec.get("local_dir")
            if local_dir is None:
                row.update({
                    "qubit": None,
                    "qubit_sha256": None,
                    "patch": None,
                    "reason": spec["omit_reason"],
                })
                rows.append(row)
                continue
            if spec["scope"] == "hermes-agent/plugins":
                local_rel = rel.replace("/dashboard/", "/", 1)
            else:
                local_rel = rel
            qubit = f"{local_dir}/{local_rel}"
            # RR2 identifier rename moved a few vendored frontend files; map
            # the upstream rel to its shipped local path.
            qubit = _LOCAL_PATH_RENAMES.get(qubit, qubit)
            qpath = REPO / qubit
            if not qpath.is_file():
                raise SystemExit(f"vendored comparison file missing: {qubit}")
            patch = _PATCH_SPEC.get(qubit)
            # Deep source rebrand: any content change in a vendored frontend
            # file is a documented, owned Qubit patch (never silent drift).
            if patch is None and _sha_file(qpath) != upstream_sha:
                patch = _GENERIC_REBRAND
            row.update({
                "qubit": qubit,
                "qubit_sha256": _sha_file(qpath),
                "patch": patch["patch"] if patch else None,
                "reason": patch["reason"] if patch else spec["default_reason"],
                "upstream_blob_sha256": upstream_sha,
                "affected_regression_test":
                    patch["affected_regression_test"] if patch
                    else spec["default_test"],
            })
            rows.append(row)

    added = []
    for entry in _WAVE3_ADDED:
        p = REPO / entry["qubit"]
        if not p.is_file():
            raise SystemExit(f"wave3 added file missing: {entry['qubit']}")
        added.append({
            "qubit": entry["qubit"],
            "qubit_sha256": _sha_file(p),
            "role": entry["role"],
            "affected_regression_test": entry["affected_regression_test"],
        })

    overlay = []
    overlay_root = REPO / "frontend/qubit-app"
    for p in sorted(overlay_root.rglob("*")):
        if not p.is_file():
            continue
        qubit = p.relative_to(REPO).as_posix()
        if qubit.endswith("README.md"):
            role = _OVERLAY_README_ROLE
        elif qubit.endswith("qubit-theme.json"):
            role = _OVERLAY_THEME_ROLE
        else:
            role = _OVERLAY_PLUGIN_ROLE
        overlay.append({
            "qubit": qubit,
            "qubit_sha256": _sha_file(p),
            "role": role,
        })

    return {
        "hermes": {
            "repo": UPSTREAM_REPO,
            "rev": PIN,
            "tag": UPSTREAM_TAG,
            "license": "MIT (Copyright (c) 2025 Nous Research)",
        },
        "openclaw": {
            "repo": OPENCLAW_REPO,
            "rev": OPENCLAW_PIN,
            "tag": OPENCLAW_TAG,
            "license": OPENCLAW_LICENSE,
        },
        "amendment": "docs/archive/2026-09-21/QUBIT_OWNER_AMENDMENT_2026-09-11.md",
        "wave3": {
            "date": "2026-09-12",
            "agent": "F (Qubit rebrand + visual/UI parity)",
            "vendor_patches": [],
            "added": added,
            "removed": list(_WAVE3_REMOVED),
            "handoff_merged": "docs/archive/2026-09-21/HERMES_SOURCE_COMPARISON.wave3.diff; "
                              "docs/archive/2026-09-21/HERMES_SOURCE_COMPARISON.wave4.diff",
            "note": _WAVE3_NOTE,
            "overlay": overlay,
        },
        "rows": rows,
        "provenance_schema": _PROVENANCE_SCHEMA,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--upstream", default="/tmp/opencode/pinned/hermes-agent")
    ap.add_argument("--local", default="qubit_runtime")
    ap.add_argument("--out", default="docs/archive/2026-09-21/QUBIT_HERMES_VENDOR_MANIFEST.json")
    # Default to the contractual OpenClaw baseline clone that matches
    # OPENCLAW_PIN (v2026.9.2); the newer v2026.9.4 clone lives at
    # /tmp/opencode/pinned/openclaw and must be passed explicitly because the
    # recorded comparison baseline is the owner-approved v2026.9.2 revision.
    ap.add_argument("--openclaw",
                    default=f"/tmp/opencode/pinned/openclaw-{OPENCLAW_TAG}")
    ap.add_argument("--comparison-out",
                    default="docs/archive/2026-09-21/HERMES_SOURCE_COMPARISON.json")
    args = ap.parse_args(argv)

    upstream = Path(args.upstream)
    local = (REPO / args.local).resolve()
    out = (REPO / args.out).resolve()

    if not local.is_dir():
        print(f"local fork not found: {local}", file=sys.stderr)
        return 2

    manifest = build_vendor_manifest(upstream, local, args.local)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n",
                   encoding="utf-8")
    counts = manifest["classification_counts"]
    print(f"wrote {_rel(out)}")
    print(f"  total={manifest['file_count']} identical={manifest['identical']} "
          f"eol_normalized={counts[EOL_NORMALIZED]} "
          f"patched={len(manifest['patched'])} "
          f"extra={len(manifest['extra_local'])} "
          f"missing={len(manifest['missing_from_local'])} "
          f"omitted={len(manifest['intentionally_omitted'])} "
          f"unexplained={counts[UNEXPLAINED]}")
    print(f"  upstream_mode={manifest['upstream_mode_counts']} "
          f"local_git_mode={manifest['local_git_mode_counts']} "
          f"symlinks_upstream={manifest['symlink_count_upstream']} "
          f"symlinks_local={manifest['symlink_count_local']}")
    print(f"  git_tracked={manifest['local_git_file_count']}/"
          f"{manifest['upstream_file_count']} "
          f"mode_divergences={len(manifest['mode_divergences'])} "
          f"symlink_divergences={len(manifest['symlink_divergences'])} "
          f"git_index_gaps={len(manifest['git_index_gaps'])}")

    if args.comparison_out:
        comparison = build_comparison(upstream, Path(args.openclaw))
        comp_out = (REPO / args.comparison_out).resolve()
        comp_out.parent.mkdir(parents=True, exist_ok=True)
        comp_out.write_text(
            json.dumps(comparison, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {_rel(comp_out)}")
        print(f"  rows={len(comparison['rows'])} "
              f"wave3.added={len(comparison['wave3']['added'])} "
              f"wave3.overlay={len(comparison['wave3']['overlay'])}")

    if manifest["blockers"]:
        print(f"BLOCKERS: {len(manifest['blockers'])} unexplained difference(s)",
              file=sys.stderr)
        for rel in manifest["blockers"][:20]:
            print(f"  {rel}", file=sys.stderr)
        return 1
    if manifest["tracking_blockers"]:
        print(f"TRACKING GAPS: {len(manifest['tracking_blockers'])} file(s) "
              f"not reproducible from the git index "
              f"(mode/symlink divergence or ignored-but-upstream-tracked)",
              file=sys.stderr)
        for rel in manifest["tracking_blockers"][:30]:
            print(f"  {rel}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
