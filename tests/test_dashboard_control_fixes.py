"""F4 — final-wave dashboard control fixes (D7 / D13 / D14).

Four confirmed dashboard defects from ``docs/`` / the live Playwright sweep are
pinned here, statically and by evaluating the shipped overlay plugin bundles:

* D14 — ``qubit-brand.js`` registered a hidden route body that returned
  ``null`` (a dead path). It must register a real component;
* D13 — ``/qubit-approvals`` and ``/qubit-brand`` resolved a mangled title
  ("Qubit-approvals" / "Qubit-brand") from the naive capitalize fallback;
* D8/D9/D10 — the Qubit overlay duplicates vendored surfaces
  (``/qubit-boards`` vs ``/kanban``, ``/qubit-runtime`` vs ``/devices``); the
  page titles and sidebar labels must be distinct identities, not duplicates;
* D7 — ``/reports`` rendered token/cost totals even when
  ``dashboard.show_token_analytics`` was false. It must use the same policy
  gate as the built-in ``/analytics`` page: never call the analytics endpoint
  and render a typed-empty state when the flag is off.

The bundle-evaluation tests run the dependency-free IIFE with a minimal
React/hooks/SDK stub under ``node`` and inspect the registered component's
rendered element tree; they skip if ``node`` is unavailable.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

PLUGINS = REPO_ROOT / "frontend/hermes-qubit/plugins"
BRAND = PLUGINS / "qubit-brand/qubit-brand.js"
REPORTS = PLUGINS / "qubit-reports/qubit-reports.js"
SRC = REPO_ROOT / "frontend/hermes-web/src"
RESOLVE = SRC / "lib/resolve-page-title.ts"
APP = SRC / "App.tsx"
POLICY_REFUSAL = SRC / "lib/policy-refusal.ts"
SYSTEM_ACTIONS = SRC / "contexts/SystemActions.tsx"
SYSTEM_PAGE = SRC / "pages/SystemPage.tsx"

_NODE = shutil.which("node")


# ---------------------------------------------------------------------------
# D14 — qubit-brand route body is a real component, not null
# ---------------------------------------------------------------------------
def _brand_text() -> str:
    return BRAND.read_text(encoding="utf-8")


def test_d14_brand_route_registers_real_component():
    text = _brand_text()
    assert 'register("qubit-brand", Hidden)' not in text
    assert "function Hidden" not in text
    assert 'register("qubit-brand", QubitBrandPage)' in text
    # the registered body renders a title instead of returning null
    assert "function QubitBrandPage" in text
    assert 'e("h1"' in text and "Qubit Brand" in text


def test_d14_brand_bundle_renders_non_null_tree():
    tree = _eval_bundle(BRAND, "qubit-brand", {"dashboard": {}})
    assert tree["registered"] is True
    assert tree["isNull"] is False
    assert "Qubit Brand" in tree["text"]


# ---------------------------------------------------------------------------
# D13 + D8/D9/D10 — distinct route titles / sidebar identities
# ---------------------------------------------------------------------------
def _route_titles() -> dict[str, str]:
    text = RESOLVE.read_text(encoding="utf-8")
    block = re.search(
        r"export const ROUTE_TITLES[^{]*\{(.*?)\}", text, re.S).group(1)
    return dict(re.findall(r'"([^"]+)":\s*"([^"]+)"', block))


def test_d13_hidden_routes_have_real_titles_not_mangled_paths():
    titles = _route_titles()
    assert titles["/qubit-approvals"] == "Qubit Approvals"
    assert titles["/qubit-brand"] == "Qubit Brand"
    # the map is consulted before the naive capitalize fallback can run
    text = RESOLVE.read_text(encoding="utf-8")
    assert text.index("ROUTE_TITLES[normalized]") < \
        text.index("const segment = normalized.slice(1)")
    # the fallback still exists for genuinely unknown routes
    assert 'segment.charAt(0).toUpperCase() + segment.slice(1)' in text


def test_d8_d9_d10_duplicate_routes_have_distinct_identities():
    titles = _route_titles()
    assert titles["/qubit-boards"] == "Qubit Boards"
    assert titles["/qubit-runtime"] == "Runtime Inventory"
    # the vendored counterparts keep their own (non-Qubit) titles
    assert "/kanban" not in titles and "/devices" not in titles
    app = APP.read_text(encoding="utf-8")
    # sidebar labels resolve through the same map, so nav and header agree
    assert "ROUTE_TITLES[manifest.tab.path]" in app
    assert "ROUTE_TITLES[path]" in app
    # hidden tabs stay in the title metadata so their header is not mangled
    assert ".filter((m) => !m.tab.hidden)" not in app


# ---------------------------------------------------------------------------
# D15 — the expected dashboard self-update policy refusal is handled, not
# surfaced as a raw error. The backend advertises can_update_hermes=true but
# answers /api/qubit/update[/check] with a typed 403 UPDATE_OPERATOR_OWNED;
# the sidebar/pages must render that as honest guidance.
# ---------------------------------------------------------------------------
def test_d15_policy_refusal_helper_parses_the_typed_envelope():
    helper = POLICY_REFUSAL.read_text(encoding="utf-8")
    assert "export interface PolicyRefusal" in helper
    assert "export function parsePolicyRefusal" in helper
    assert "export function policyRefusalMessage" in helper
    assert "qubit_counterpart" in helper
    # the fetchJSON error formatter is "<status>: <body>"
    assert 'message.indexOf("{")' in helper


def test_d15_update_controls_use_the_refusal_helper():
    actions = SYSTEM_ACTIONS.read_text(encoding="utf-8")
    assert "parsePolicyRefusal" in actions
    assert "policyRefusalMessage" in actions
    # the raw `actionFailed` toast remains only for genuine failures
    assert "actionFailed" in actions
    # every action classifies a typed refusal before the generic error path
    assert "const refusal = parsePolicyRefusal(err);" in actions

    system = SYSTEM_PAGE.read_text(encoding="utf-8")
    # check + apply paths classify the refusal
    assert system.count("parsePolicyRefusal") >= 2
    assert system.count("policyRefusalMessage") >= 2
    # refused state renders a reason and hides the actionable controls
    assert 'data-state="unavailable"' in system
    assert "canUpdateHermes && !updateRefusal" in system
    # process supervision is discovered from GET /api/gateway and disabled
    assert "getGatewayStatus" in system
    assert "GATEWAY_NO_SUPERVISION" in system


def test_d15_sidebar_disables_refused_supervision_and_update():
    app = APP.read_text(encoding="utf-8")
    # both expected refusals are discovered up front
    assert "getGatewayStatus" in app
    assert "GATEWAY_NO_SUPERVISION" in app
    assert "disabledReason: gatewayRefusal" in app
    # a refused restart never opens the confirm dialog
    assert 'action === "restart" && gatewayRefusal' in app


def test_d15_sidebar_disables_the_refused_update_with_a_reason():
    app = APP.read_text(encoding="utf-8")
    assert "parsePolicyRefusal" in app
    assert "policyRefusalMessage" in app
    # an honestly-disabled action carries its operator-facing reason
    assert "disabledReason" in app
    assert "title={item.disabledReason || undefined}" in app
    # a refusal must never open the confirm dialog
    assert "updateConfirmOpen && !updateRefusal" in app


# ---------------------------------------------------------------------------
# D16 — /profiles/new resolves a real page title, not "Profiles/new"
# ---------------------------------------------------------------------------
def test_d16_profiles_new_route_has_a_real_title():
    resolve = RESOLVE.read_text(encoding="utf-8")
    assert '"/profiles/new": "New Profile"' in resolve
    # it stays out of the plugin-overlay title map (it is a builtin route)
    assert "/profiles/new" not in _route_titles()


# ---------------------------------------------------------------------------
# D17 — form controls carry accessible names. A live Playwright accessibility
# snapshot found unlabeled config switches/inputs (AutoField rendered a
# <Label> with no htmlFor and controls with no id/aria-label), two unlabeled
# /plugins install switches, and an unlabeled sessions search input.
# ---------------------------------------------------------------------------
AUTOFIELD = SRC / "components/AutoField.tsx"
PLUGINS_PAGE = SRC / "pages/PluginsPage.tsx"
SESSIONS_PAGE = SRC / "pages/SessionsPage.tsx"


def test_d17_autofield_fields_get_accessible_names():
    text = AUTOFIELD.read_text(encoding="utf-8")
    # every field derives a stable control id and the label points at it
    assert "const fieldId = `cfg-" in text
    assert text.count("htmlFor={fieldId}") >= 5
    assert text.count("id={fieldId}") >= 5
    # the boolean switch keeps an explicit aria-label
    assert "aria-label={label}" in text


def test_d17_plugin_install_switches_are_labelled():
    text = PLUGINS_PAGE.read_text(encoding="utf-8")
    assert "aria-label={t.pluginsPage.forceReinstall}" in text
    assert "aria-label={t.pluginsPage.enableAfterInstall}" in text


def test_d17_sessions_search_input_is_labelled():
    text = SESSIONS_PAGE.read_text(encoding="utf-8")
    assert "aria-label={t.sessions.searchPlaceholder}" in text


# ---------------------------------------------------------------------------
# D7 — /reports respects dashboard.show_token_analytics
# ---------------------------------------------------------------------------
def _reports_text() -> str:
    return REPORTS.read_text(encoding="utf-8")


def test_d7_reports_reads_gate_and_guards_analytics_fetch():
    text = _reports_text()
    assert '"/api/config"' in text
    assert "show_token_analytics" in text
    # the analytics endpoint is only reached behind the enabled guard
    assert text.index("show_token_analytics") < text.index('"/api/analytics"')
    guarded = text[text.index('"/api/config"'):text.index('"/api/analytics"')]
    assert "if (!enabled) return" in guarded
    # and the totals panel has a typed-empty disabled branch
    assert "showTokens === false" in text
    assert '"data-state"' in text


def test_d7_reports_hides_tokens_when_disabled_and_shows_when_enabled():
    disabled = _eval_bundle(
        REPORTS, "qubit-reports", {"dashboard": {"show_token_analytics": False}})
    assert disabled["analyticsCalls"] == 0, \
        "analytics endpoint called while show_token_analytics=false"
    assert "Token and cost analytics are disabled" in disabled["text"]
    for token in ("Input tokens", "Output tokens", "Cost"):
        assert token not in disabled["text"], (token, disabled["text"][:300])

    enabled = _eval_bundle(
        REPORTS, "qubit-reports", {"dashboard": {"show_token_analytics": True}})
    assert enabled["analyticsCalls"] >= 1
    assert "Cost" in enabled["text"]


# ---------------------------------------------------------------------------
# Production wiring: the served plugin set still includes the owned bundles
# ---------------------------------------------------------------------------
def test_owned_plugins_are_served_by_the_real_backend():
    from voicebridge import hermes_compat as HC

    served = {m["name"] for m in HC.plugins_response()["plugins"]}
    assert {"qubit-brand", "qubit-reports"} <= served
    for plug, entry in (("qubit-brand", BRAND), ("qubit-reports", REPORTS)):
        assert plug in served and entry.is_file()


# ---------------------------------------------------------------------------
# Bundle evaluation harness (node + minimal React/hooks/SDK stub)
# ---------------------------------------------------------------------------
_NODE_HARNESS = r"""
const fs = require("fs");
const [bundlePath, pluginName, configJson] = process.argv.slice(2);
const config = JSON.parse(configJson);
const registered = {};
const store = [];
const analyticsCalls = [];
let idx = 0;
let effects = [];
const React = {
  createElement(type, props) {
    return {
      type,
      props: props || {},
      children: Array.prototype.slice.call(arguments, 2),
    };
  },
};
const hooks = {
  useState(init) {
    const i = idx++;
    if (!(i in store)) store[i] = init;
    return [store[i], (v) => {
      store[i] = (typeof v === "function") ? v(store[i]) : v;
    }];
  },
  useEffect(fn) { effects.push(fn); },
};
const fetchJSON = (path) => {
  if (path === "/api/config") return Promise.resolve(config);
  if (path === "/api/analytics") {
    analyticsCalls.push(path);
    return Promise.resolve({totals: {total_sessions: 2, total_api_calls: 7,
      total_input: 11, total_output: 22, total_estimated_cost: 0.42}});
  }
  if (path === "/api/logs") return Promise.resolve({lines: ["hello"]});
  return Promise.resolve({});
};
const authedFetch = () => Promise.resolve({
  text: () => Promise.resolve("{}"),
});
global.window = {
  __HERMES_PLUGIN_SDK__: {React, hooks, fetchJSON, authedFetch},
  __HERMES_PLUGINS__: {
    register: (n, c) => { registered[n] = c; },
    registerSlot: () => {},
  },
};
eval(fs.readFileSync(bundlePath, "utf8"));
const Component = registered[pluginName];
function textOf(node) {
  if (node === null || node === undefined) return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join(" ");
  return (node.children || []).map(textOf).join(" ");
}
(async () => {
  let tree = null;
  for (let pass = 0; pass < 12; pass++) {
    idx = 0;
    effects = [];
    tree = Component();
    const fx = effects.slice();
    if (!fx.length) break;
    for (const fn of fx) fn();
    await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
  }
  console.log(JSON.stringify({
    registered: !!Component,
    isNull: tree === null || tree === undefined,
    text: textOf(tree),
    analyticsCalls: analyticsCalls.length,
  }));
})().catch((e) => { console.error(String(e)); process.exit(1); });
"""


def _eval_bundle(bundle: Path, plugin: str, config: dict) -> dict:
    if _NODE is None:
        import pytest

        pytest.skip("node unavailable to evaluate the plugin bundle")
    import os
    import tempfile

    fd, script = tempfile.mkstemp(suffix=".js", prefix="qubit-eval-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(_NODE_HARNESS)
        out = subprocess.run(
            [_NODE, script, str(bundle), plugin, json.dumps(config)],
            capture_output=True, text=True, timeout=60, cwd=str(REPO_ROOT))
    finally:
        os.unlink(script)
    assert out.returncode == 0, (out.returncode, out.stderr[-800:])
    return json.loads(out.stdout.strip().splitlines()[-1])
