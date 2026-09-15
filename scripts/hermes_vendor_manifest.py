#!/usr/bin/env python3
"""Generate the per-file provenance manifests for the Qubit-owned Hermes fork.

The Qubit product executes a Qubit-owned fork of the pinned Hermes source
(see docs/QUBIT_OWNER_AMENDMENT_2026-09-11_HERMES_RUNTIME.md). This script
records, for every file in the local fork, the pinned upstream revision, the
upstream git blob hash, the upstream blob sha256, the local sha256, and a
six-way classification (UNCHANGED / QUBIT_PATCHED / OPENCLAW_OVERLAY /
QUBIT_ADDED / MISSING / INTENTIONALLY_OMITTED) plus EOL-normalization notes.
It also regenerates the vendored-frontend comparison ledger
(``docs/HERMES_SOURCE_COMPARISON.json``). It is deterministic and re-runnable;
no timestamps are embedded.

Usage:
    scripts/hermes_vendor_manifest.py
    scripts/hermes_vendor_manifest.py \
        --upstream /tmp/opencode/pinned/hermes-agent \
        --local qubit_hermes \
        --out docs/QUBIT_HERMES_VENDOR_MANIFEST.json \
        --openclaw /tmp/opencode/pinned/openclaw \
        --comparison-out docs/HERMES_SOURCE_COMPARISON.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIN = "2237be355906fbe6065ce1815711eee52b2d646e"
UPSTREAM_TAG = "v2026.9.7"
UPSTREAM_REPO = "https://github.com/NousResearch/hermes-agent"
UPSTREAM_LICENSE = "MIT License, Copyright (c) 2025 Nous Research"
OPENCLAW_PIN = "3928bad9badfcb6c7d140530435e806fb8092190"
OPENCLAW_TAG = "v2026.9.2"
OPENCLAW_REPO = "https://github.com/openclaw/openclaw"
OPENCLAW_LICENSE = "MIT (Copyright (c) 2026 OpenClaw Foundation)"

_SKIP_DIR_PARTS = {"__pycache__", ".git", ".pytest_cache"}
_SKIP_SUFFIX = (".pyc", ".pyo")
_UPSTREAM_SKIP_DIRS = _SKIP_DIR_PARTS
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
# Currently empty: the fork is a byte-faithful copy of the pinned checkout and
# all Qubit behaviour lives in overlay modules under voicebridge/.
_QUBIT_PATCH_SPEC: dict[str, dict] = {}
# OpenClaw source is behaviourally adapted, never vendored into qubit_hermes/.
_OPENCLAW_OVERLAY: set[str] = set()
# Upstream files deliberately not vendored into the fork (none at present).
_INTENTIONAL_OMISSIONS: dict[str, str] = {}

# ---------------------------------------------------------------------------
# Vendored-frontend comparison ledger (docs/HERMES_SOURCE_COMPARISON.json)
# ---------------------------------------------------------------------------

_GENERIC_REBRAND = {
    "patch": "Qubit product rebrand: replace upstream product name/labels in "
             "product-facing dashboard strings",
    "reason": "Qubit rebrand of product-facing dashboard strings (owner "
              "directive 2026-09-12); no logic, route, or backend-route change.",
    "affected_regression_test": "tests/test_dashboard_branding_wave1.py",
}

_REBRAND_PATHS = [
    "frontend/hermes-web/src/App.tsx",
    "frontend/hermes-web/src/components/HermesConsoleModal.tsx",
    "frontend/hermes-web/src/i18n/af.ts",
    "frontend/hermes-web/src/i18n/ar.ts",
    "frontend/hermes-web/src/i18n/de.ts",
    "frontend/hermes-web/src/i18n/es.ts",
    "frontend/hermes-web/src/i18n/fr.ts",
    "frontend/hermes-web/src/i18n/ga.ts",
    "frontend/hermes-web/src/i18n/hu.ts",
    "frontend/hermes-web/src/i18n/it.ts",
    "frontend/hermes-web/src/i18n/ja.ts",
    "frontend/hermes-web/src/i18n/ko.ts",
    "frontend/hermes-web/src/i18n/pt.ts",
    "frontend/hermes-web/src/i18n/ru.ts",
    "frontend/hermes-web/src/i18n/tr.ts",
    "frontend/hermes-web/src/i18n/uk.ts",
    "frontend/hermes-web/src/i18n/zh-hant.ts",
    "frontend/hermes-web/src/i18n/zh.ts",
    "frontend/hermes-web/src/lib/gatewayClient.ts",
    "frontend/hermes-web/src/pages/ChannelsPage.tsx",
    "frontend/hermes-web/src/pages/ChatPage.tsx",
    "frontend/hermes-web/src/pages/ConfigPage.tsx",
    "frontend/hermes-web/src/pages/EnvPage.tsx",
    "frontend/hermes-web/src/pages/McpPage.tsx",
    "frontend/hermes-web/src/pages/PluginsPage.tsx",
    "frontend/hermes-web/src/pages/SkillsPage.tsx",
    "frontend/hermes-web/src/pages/SystemPage.tsx",
    "frontend/hermes-web/src/themes/presets.ts",
]

_PATCH_SPEC: dict[str, dict] = {
    p: dict(_GENERIC_REBRAND) for p in _REBRAND_PATHS
}
_PATCH_SPEC.update({
    "frontend/hermes-web/src/contexts/SystemActions.tsx": {
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
    "frontend/hermes-web/src/lib/resolve-page-title.test.ts": {
        "patch": "assert the Qubit route-title map for the profile-create path",
        "reason": "test-only assertion for the Qubit route-title overlay; no "
                  "vendor runtime behavior change.",
        "affected_regression_test": "tests/test_dashboard_control_fixes.py",
    },
    "frontend/hermes-web/index.html": {
        "patch": "Qubit product rebrand: document title -> 'Qubit Agent - "
                 "Command Center'",
        "reason": "Qubit rebrand of product-facing dashboard strings (owner "
                  "directive 2026-09-12); no logic, route, or backend-route "
                  "change.",
        "affected_regression_test": "tests/test_dashboard_branding_wave1.py",
    },
    "frontend/hermes-web/package.json": {
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
    "frontend/hermes-web/src/i18n/en.ts": {
        "patch": "product-string rebrand: 'Update Hermes'/'Updating "
                 "Hermes'/'Update Hermes?' -> Qubit; confirm text "
                 "hermes->qubit (owner rebrand)",
        "reason": "Qubit rebrand of product-facing English dashboard strings; "
                  "translation keys unchanged; no logic or route change.",
        "affected_regression_test": "tests/test_dashboard_branding_wave1.py",
    },
    "frontend/hermes-web/src/pages/SessionsPage.tsx": {
        "patch": "hermes_flow source label 'Hermes Flow' -> 'Qubit Flow' "
                 "(owner rebrand; no logic change)",
        "reason": "Qubit rebrand of a product-facing session source label; the "
                  "source id hermes_flow is unchanged for compatibility.",
        "affected_regression_test": "tests/test_dashboard_branding_wave1.py",
    },
    "frontend/hermes-web/src/pages/ChatPage.test.tsx": {
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
    "frontend/hermes-web/src/plugins/index.ts": {
        "patch": "wave3/plugins-fleet-exports",
        "reason": "Re-export the new fleet discovery helpers from the plugin "
                  "barrel. Regression: tests/test_dashboard_plugin_fleet.py.",
        "affected_regression_test": "tests/test_dashboard_plugin_fleet.py",
    },
    "frontend/hermes-web/src/components/AutoField.tsx": {
        "patch": "wave2/frontend-rebrand-normalizer",
        "reason": "Apply the Qubit product-identity normalizer to schema "
                  "field descriptions at the render boundary so backend "
                  "free-text never exposes an upstream product name. "
                  "Presentation-only; no logic or backend-route change. "
                  "Regression: tests/test_hermes_frontend_inventory8u.py.",
        "affected_regression_test": "tests/test_hermes_frontend_inventory8u.py",
    },
    "frontend/hermes-web/src/components/HermesConsoleModal.tsx": {
        "patch": "wave2/frontend-rebrand-console-prompt",
        "reason": "Rebrand the refused raw-console prompt fallback from a "
                  "Qubit product perspective. Presentation-only; the raw "
                  "console/PTY remains a typed refusal. Regression: "
                  "tests/test_hermes_playwright8tf.py.",
        "affected_regression_test": "tests/test_hermes_playwright8tf.py",
    },
    "frontend/hermes-web/src/pages/ChannelsPage.tsx": {
        "patch": "wave2/frontend-rebrand-normalizer",
        "reason": "Apply the Qubit product-identity normalizer to channel "
                  "platform descriptions/help text at the render boundary. "
                  "Presentation-only; no logic or backend-route change. "
                  "Regression: tests/test_hermes_frontend_inventory8u.py.",
        "affected_regression_test": "tests/test_hermes_frontend_inventory8u.py",
    },
    "frontend/hermes-web/src/lib/api.ts": {
        "patch": "wave2/public-rebrand-api-prefix",
        "reason": "Qubit-owned public API prefix: the vendored SPA's API, WS, "
                  "and export URLs are mapped from /api/<x> to /agent/api/<x> "
                  "via window.__QUBIT_API_PREFIX__; logout/update call the "
                  "Qubit paths. No vendor business logic changed. Regression: "
                  "tests/test_qbpv2_rebrand_public_surface.py.",
        "affected_regression_test": "tests/test_qbpv2_rebrand_public_surface.py",
    },
    "frontend/hermes-web/src/lib/api.test.ts": {
        "patch": "wave2/public-rebrand-api-prefix-tests",
        "reason": "Update the vendored api tests to the Qubit-owned /agent API "
                  "prefix. Test-only. Regression: "
                  "tests/test_qbpv2_rebrand_public_surface.py.",
        "affected_regression_test": "tests/test_qbpv2_rebrand_public_surface.py",
    },
    "frontend/hermes-web/vite.config.ts": {
        "patch": "wave2/public-rebrand-root-base",
        "reason": "Serve the SPA at the Qubit root base ('/') instead of the "
                  "upstream-named prefix; dev proxy for /agent. Build-config "
                  "only; no vendor runtime logic changed. Regression: "
                  "tests/test_qbpv2_frontend_root_base.py.",
        "affected_regression_test": "tests/test_qbpv2_frontend_root_base.py",
    },
    "frontend/hermes-web/src/lib/gatewayClient.ts": {
        "patch": "wave2/public-rebrand-api-prefix",
        "reason": "Gateway WebSocket path routed through the Qubit-owned "
                  "/agent API prefix. Regression: "
                  "tests/test_qbpv2_rebrand_public_surface.py.",
        "affected_regression_test": "tests/test_qbpv2_rebrand_public_surface.py",
    },
    "frontend/hermes-web/src/plugins/types.ts": {
        "patch": "wave3/plugins-manifest-assetBase",
        "reason": "Add the optional PluginManifest.assetBase field consumed by "
                  "usePlugins for static fleet bundles; when absent the host "
                  "keeps the upstream overlay asset root. Regression: "
                  "tests/test_dashboard_plugin_fleet.py.",
        "affected_regression_test": "tests/test_dashboard_plugin_fleet.py",
    },
    "frontend/hermes-web/src/plugins/usePlugins.ts": {
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
    "frontend/hermes-shared/src/json-rpc-gateway.ts": {
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
    "frontend/hermes-shared/src/websocket-url.ts": {
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
    "frontend/hermes-web/public/plugins/hermes-achievements/dist/index.js": {
        "patch": "wave3/QUBIT_ADDED:byte-identical [wave3 rebrand] Qubit "
                 "product naming applied.",
        "reason": "Owner-approved Qubit rebrand of the vendored achievements "
                  "dashboard bundle: product-facing copy relabelled Qubit "
                  "(Qubit Achievements / Qubit Agent / Qubit session history); "
                  "protocol identifiers (X-Hermes-Session-Token, HERMES_PLUGINS, "
                  "~/.hermes) and module ids preserved.",
        "affected_regression_test": "tests/test_dashboard_plugin_fleet.py",
    },
    "frontend/hermes-web/public/plugins/hermes-achievements/dist/style.css": {
        "patch": "wave3/QUBIT_ADDED:byte-identical",
        "reason": "Vendored pinned achievements stylesheet copied "
                  "byte-identical into the SPA static root.",
        "affected_regression_test": "tests/test_dashboard_plugin_fleet.py",
    },
    "frontend/hermes-web/public/plugins/kanban/dist/index.js": {
        "patch": "wave3/QUBIT_ADDED:byte-identical [wave3 rebrand] Qubit "
                 "product naming applied.",
        "reason": "Owner-approved Qubit rebrand of the vendored kanban "
                  "dashboard bundle: product-facing copy relabelled Qubit "
                  "(Qubit Kanban / Qubit profile); protocol identifiers and "
                  "storage paths preserved.",
        "affected_regression_test": "tests/test_dashboard_plugin_fleet.py",
    },
    "frontend/hermes-web/public/plugins/kanban/dist/style.css": {
        "patch": "wave3/QUBIT_ADDED [wave3 rebrand] Qubit product naming "
                 "applied to the vendored kanban stylesheet.",
        "reason": "Owner-approved Qubit rebrand of the vendored kanban "
                  "dashboard bundle: the stylesheet header comment is "
                  "relabelled 'Hermes Kanban' -> 'Qubit Kanban' (the only byte "
                  "difference from the pinned bundle). Protocol identifiers, "
                  "selectors, and layout are preserved.",
        "affected_regression_test": "tests/test_dashboard_plugin_fleet.py",
    },
    "frontend/hermes-web/eslint.config.js": {
        "patch": "build-lint: ignore the vendored dashboard plugin dist bundles",
        "reason": "Qubit build-tooling patch: add `**/dist/**` and "
                  "`../hermes-dist/**` to `globalIgnores` so the pinned "
                  "`public/plugins/<name>/dist` bundles (which carry their own "
                  "inline eslint directives) are not linted as project source. "
                  "No runtime or Hermes behavior change.",
        "affected_regression_test": "tests/test_release_gates8u.py",
    },
    "frontend/hermes-web/tsconfig.app.json": {
        "patch": "build-config: exclude shared test files from the app build",
        "reason": "Qubit build-tooling patch: add an `exclude` for "
                  "`../apps/shared/src/**/*.test.ts(x)` so the dashboard app "
                  "`tsc` build does not typecheck the vendored shared-lib test "
                  "files. No runtime or Hermes behavior change.",
        "affected_regression_test": "tests/test_release_gates8u.py",
    },
    "frontend/hermes-web/vitest.config.ts": {
        "patch": "build-config: spread the rolldown filter instead of mutating it",
        "reason": "Qubit build-tooling patch: set the react code filter with "
                  "`{ ...preset.rolldown.filter, code: ... }` instead of "
                  "mutating a possibly-frozen preset object, so the vitest "
                  "config loads under the pinned toolchain. Test-config only; "
                  "no runtime or Hermes behavior change.",
        "affected_regression_test": "tests/test_release_gates8u.py",
    },
    "frontend/hermes-web/src/components/ChatSessionList.tsx": {
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
    "frontend/hermes-web/src/pages/LogsPage.tsx": {
        "patch": "wave2/frontend-rebrand-normalizer",
        "reason": "Apply the Qubit product-identity normalizer (`rebrand`) to "
                  "rendered log lines at the render boundary so "
                  "backend/upstream free-text never exposes an upstream product "
                  "name. Presentation-only; no logic or backend-route change. "
                  "Regression: tests/test_hermes_frontend_inventory8u.py.",
        "affected_regression_test": "tests/test_hermes_frontend_inventory8u.py",
    },
})

# Documented intentional Qubit patches to the vendored frontend that were not
# captured by the wave-specific spec above. Each carries an exact patch + reason
# so the provenance gate accepts the divergence instead of flagging it as
# undocumented drift.
_PATCH_SPEC.update({
    "frontend/hermes-web/src/components/SidebarFooter.tsx": {
        "patch": "Qubit rebrand: render the org label as plain text and remove "
                 "the external upstream vendor (nousresearch.com) link from the "
                 "sidebar footer",
        "reason": "Qubit identity promise: a normal dashboard surface must not "
                  "present an upstream vendor link as Qubit. Presentation-only; "
                  "no logic, route, or backend-route change; one hunk.",
        "affected_regression_test": "tests/test_branding_parity_wave3.py",
    },
    "frontend/hermes-web/src/pages/CronPage.tsx": {
        "patch": "Qubit fix: null-safe cron delivery-target handling "
                 "(`available ?? []`, `selected ?? []`, `deliveryTargets ?? []`)",
        "reason": "Qubit runtime fix so the vendored cron page does not crash "
                  "when a partially loaded cron response omits a list; behavior "
                  "is unchanged when the fields are present.",
        "affected_regression_test": "tests/test_hermes_frontend_inventory8u.py",
    },
    "frontend/hermes-web/src/pages/DocsPage.tsx": {
        "patch": "Qubit rebrand: label the vendored docs iframe/link as upstream "
                 "vendor documentation, keep the real provenance URL, and stop "
                 "presenting it as Qubit's own docs",
        "reason": "Qubit identity promise (owner directive 2026-09-15): normal "
                  "surfaces render Qubit while the genuine upstream docs URL is "
                  "retained for attribution. Presentation-only; no logic or "
                  "backend-route change.",
        "affected_regression_test": "tests/test_branding_leaks_fixes.py",
    },
    "frontend/hermes-web/src/pages/ConfigPage.tsx": {
        "patch": "Qubit settings write: handle the backend CONFIRM_REQUIRED "
                 "staging on Save (confirm, then re-PUT with confirm=true) so "
                 "the Settings Save control is not a dead 409",
        "reason": "Qubit contract: settings writes are confirm-gated and "
                  "audited; the vendored page must complete the shared "
                  "approval flow instead of leaving an unreachable control.",
        "affected_regression_test": "tests/test_hermes_config_fixes.py",
    },
    "frontend/hermes-web/src/lib/api.ts": {
        "patch": "wave2/public-rebrand-api-prefix + Qubit config confirm flag",
        "reason": "Qubit-owned public API prefix (/api/<x> -> /agent/api/<x>) "
                  "plus a confirm flag on saveConfig so the Settings page can "
                  "complete the confirm-gated settings write. No vendor "
                  "business logic changed.",
        "affected_regression_test": "tests/test_qbpv2_rebrand_public_surface.py",
    },
    "frontend/hermes-web/src/App.tsx": {
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
    "frontend/hermes-web/src/lib/resolve-page-title.ts": {
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

# Scope config for the vendored-frontend comparison ledger.
_COMPARISON_SCOPES = [
    {
        "scope": "hermes-agent/web",
        "category": "dashboard",
        "rev": PIN,
        "upstream_dir": "web",
        "upstream_root": "hermes",
        "local_dir": "frontend/hermes-web",
        "default_reason": "Hermes React dashboard source; vendored "
                          "byte-identical as the Qubit baseline.",
        "default_test": "tests/test_hermes_vendor8tf.py",
    },
    {
        "scope": "hermes-agent/apps/shared",
        "category": "dashboard-lib",
        "rev": PIN,
        "upstream_dir": "apps/shared",
        "upstream_root": "hermes",
        "local_dir": "frontend/hermes-shared",
        "default_reason": "Shared @hermes/shared lib imported by web/; vendored "
                          "byte-identical.",
        "default_test": "tests/test_hermes_vendor8tf.py",
    },
    {
        "scope": "hermes-agent/apps/desktop",
        "category": "desktop-shell",
        "rev": PIN,
        "upstream_dir": "apps/desktop",
        "upstream_root": "hermes",
        "omit_reason": "Electron desktop shell hosting the same web UI; not "
                       "shipped in this packet \u2014 Qubit serves the identical "
                       "web UI from its own backend (frontend/hermes-web "
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
        "local_dir": "frontend/hermes-web/public/plugins",
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
    "docs/HERMES_SOURCE_COMPARISON.wave3.diff are merged: the three patched "
    "src/plugins rows and the four byte-identical SPA-shipped plugin bundles "
    "(scope hermes-agent/plugins) are in rows[] with exact patch + reason + "
    "qubit_sha256; the two Qubit-added files that have no upstream row "
    "(src/plugins/fleet.ts, public/plugins/fleet.json) are recorded under "
    "wave3.added. Qubit visual-identity work is overlay-only "
    "(frontend/hermes-qubit/**). All hashes are validated by "
    "tests/test_branding_parity_wave3.py. NOTE: the pinned Achievements/Kanban "
    "bundles carry product-facing upstream strings pending agent C rebrand; "
    "see docs/BRANDING_AUDIT_WAVE3.md."
)

_WAVE3_ADDED = [
    {
        "qubit": "frontend/hermes-web/src/plugins/fleet.ts",
        "role": "Qubit-added static plugin-fleet discovery: fetch "
                "<base>/plugins/fleet.json (SPA-shipped manifests) and resolve "
                "each manifest's asset root. Keeps the vendored compatibility "
                "backend as the operation authority while surfacing the real "
                "pinned plugin tabs.",
        "affected_regression_test": "tests/test_dashboard_plugin_fleet.py",
    },
    {
        "qubit": "frontend/hermes-web/public/plugins/fleet.json",
        "role": "Qubit-authored static fleet manifest aggregate: mirrors the "
                "two pinned dashboard manifests (tab paths/positions, entry, "
                "css) with a Qubit-neutral description; verified by "
                "tests/test_dashboard_plugin_fleet.py::"
                "test_static_fleet_manifest_mirrors_pinned_plugins.",
        "affected_regression_test": "tests/test_dashboard_plugin_fleet.py",
    },
    {
        "qubit": "frontend/hermes-web/src/lib/rebrand.ts",
        "role": "Qubit-added product-identity normalizer applied at the render "
                "boundary to backend free-text (MCP/Channels/Config) so served "
                "pages never expose an upstream product name; upstream names "
                "remain only in vendored source/provenance records.",
        "affected_regression_test": "tests/test_hermes_frontend_inventory8u.py",
    },
    {
        "qubit": "frontend/hermes-web/src/components/ChatSessionList.test.tsx",
        "role": "Qubit-added contract test for the Qubit-native chat-session "
                "store sidebar (ChatSessionList.tsx): pins "
                "`api.listChatSessions`, the rendered rows, refresh, and the "
                "`?resume=<id>` selection driving the persisted SSE transcript "
                "(no upstream counterpart; the vendored component test is "
                "absent).",
        "affected_regression_test": "tests/test_branding_parity_wave3.py",
    },
    {
        "qubit": "frontend/hermes-web/src/lib/policy-refusal.ts",
        "role": "Qubit-added typed policy-refusal helper: parses the backend's "
                "typed denied envelope (code, detail, qubit_counterpart) so "
                "dashboard actions render honest guidance instead of a raw "
                "error (no upstream counterpart).",
        "affected_regression_test": "tests/test_dashboard_control_fixes.py",
    },
    {
        "qubit": "frontend/hermes-web/src/lib/policy-refusal.test.ts",
        "role": "Qubit-added unit test for the typed policy-refusal helper.",
        "affected_regression_test": "tests/test_dashboard_control_fixes.py",
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

    local_rels: list[str] = []
    for path in sorted(local.rglob("*")):
        if not path.is_file() and not path.is_symlink():
            continue
        rel = path.relative_to(local).as_posix()
        if _is_skipped(rel):
            continue
        local_rels.append(rel)
    local_set = set(local_rels)

    # Local git-index (HEAD) modes: a file present on disk but absent here is
    # excluded from a fresh clone by the vendored upstream .gitignore, even
    # though upstream tracked it. That is a provenance/tracking gap distinct
    # from a content mismatch; both are recorded.
    local_git_modes = _git_tracked_modes(local_label, REPO)

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
        up = tree.get(rel)
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
        up_file = upstream / rel
        up_worktree_sha = _sha_file(up_file) if up_file.is_file() else None
        blob_equals_local = (not is_symlink) and local_sha == blob_sha
        worktree_equals_local = up_worktree_sha is not None and local_sha == up_worktree_sha
        index_eol, worktree_eol = eol.get(rel, (None, None))

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

    for rel in sorted(set(tree) - local_set):
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
    for rel in sorted(local_set & set(tree)):
        up_mode = tree[rel][0]
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
                "upstream_blob": tree[rel][1],
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
            upstream_field = f"{spec['upstream_dir']}/{rel}"
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
            qpath = REPO / qubit
            if not qpath.is_file():
                raise SystemExit(f"vendored comparison file missing: {qubit}")
            patch = _PATCH_SPEC.get(qubit)
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
    overlay_root = REPO / "frontend/hermes-qubit"
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
        "amendment": "docs/QUBIT_OWNER_AMENDMENT_2026-09-11.md",
        "wave3": {
            "date": "2026-09-12",
            "agent": "F (Qubit rebrand + visual/UI parity)",
            "vendor_patches": [],
            "added": added,
            "handoff_merged": "docs/HERMES_SOURCE_COMPARISON.wave3.diff; "
                              "docs/HERMES_SOURCE_COMPARISON.wave4.diff",
            "note": _WAVE3_NOTE,
            "overlay": overlay,
        },
        "rows": rows,
        "provenance_schema": _PROVENANCE_SCHEMA,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--upstream", default="/tmp/opencode/pinned/hermes-agent")
    ap.add_argument("--local", default="qubit_hermes")
    ap.add_argument("--out", default="docs/QUBIT_HERMES_VENDOR_MANIFEST.json")
    # Default to the contractual OpenClaw baseline clone that matches
    # OPENCLAW_PIN (v2026.9.2); the newer v2026.9.4 clone lives at
    # /tmp/opencode/pinned/openclaw and must be passed explicitly because the
    # recorded comparison baseline is the owner-approved v2026.9.2 revision.
    ap.add_argument("--openclaw",
                    default=f"/tmp/opencode/pinned/openclaw-{OPENCLAW_TAG}")
    ap.add_argument("--comparison-out",
                    default="docs/HERMES_SOURCE_COMPARISON.json")
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
