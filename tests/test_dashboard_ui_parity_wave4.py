"""Wave 4 — dashboard UI parity + placeholder/dead-control removal (agent F).

Proves, statically (no browser/Playwright) and against the production compat
dispatcher, that the Qubit overlay and the vendored dashboard expose real
operations and honest, typed states rather than fake boxes, status-only tiles,
wrong labels, dead controls, or placeholder pages:

* the eleven overlay plugin bundles still register their components and still call
  only real backend routes (the same ``/api/ops`` broker, ``/api/qubit/chat``,
  ``/api/logs``... as CLI and voice);
* the Runtime tab is no longer a raw-JSON status box (it renders the real
  pinned-runtime inventory as a typed table with loading/empty/degraded states);
* the Operations console no longer mislabels a linked voice entry as a
  ``voice: refusal`` and emits typed ``data-state`` values;
* the brand plugin's docs notice no longer links to itself (dead control);
* every data-bearing overlay page emits typed ``data-state`` markers the Qubit
  theme styles (loading / empty / degraded / pending / cancelled / recovering);
* the theme keeps the mobile top-bar wordmark (no blank brand) while still
  hiding the duplicate sidebar wordmark, and keeps its responsive /
  accessibility / reduced-motion / contrast contracts;
* the pinned Achievements and Kanban tabs ship Qubit-branded, localized
  bundles that read the real plugin backend;
* no placeholder tokens remain in the overlay or the vendored dashboard source.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

OVERLAY = REPO_ROOT / "frontend" / "qubit-app"
PLUGDIR = OVERLAY / "plugins"
THEME = OVERLAY / "qubit-theme.json"
WEB_SRC = REPO_ROOT / "frontend" / "qubit-web" / "src"
PUBLIC_PLUGINS = REPO_ROOT / "frontend" / "qubit-web" / "public" / "plugins"

EXPECTED_PLUGINS = [
    "qubit-approvals", "qubit-boards", "qubit-brand",
    "qubit-devices", "qubit-gateway", "qubit-memory", "qubit-models",
    "qubit-ops", "qubit-reports", "qubit-runtime",
]

# Real backend literals each overlay page is allowed to call. Kept as a guard
# so a future edit cannot swap a real route for a made-up one.
KNOWN_ENDPOINTS = {
    "/api/ops", "/api/ops/run", "/api/ops/confirm", "/api/ops/cancel",
    "/api/ops/approvals", "/api/ops/runtime", "/api/ops/boards",
    "/api/ops/kanban", "/api/ops/msg-accounts", "/api/ops/gateways",
    "/api/ops/gateway-link", "/api/ops/boards-create",
    "/api/ops/cards-create", "/api/ops/cards-move",
    "/api/ops/cards-complete", "/api/ops/msg-account-add",
    "/api/ops/gateway-add", "/api/ops/gateway-remove",
    "/api/qubit/chat", "/api/logs", "/api/analytics", "/api/memory",
    "/api/curator",
}

_ENDPOINT_RE = re.compile(r"""["'](/api/[A-Za-z0-9_./-]+)["']""")
_PRODUCT_NAME = re.compile(r"\bHermes\b|\bOpenClaw\b")
_PLACEHOLDERS = re.compile(
    r"coming soon|under construction|lorem ipsum|not yet implemented|"
    r"placeholder page|placeholder-page",
    re.IGNORECASE,
)


def _manifest(name: str) -> dict:
    return json.loads((PLUGDIR / name / "manifest.json").read_text())


def _plugin_text(name: str) -> str:
    man = _manifest(name)
    return (PLUGDIR / name / man["entry"]).read_text(encoding="utf-8")


def _theme() -> dict:
    return json.loads(THEME.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Theme: mobile brand + responsive/a11y contract
# ---------------------------------------------------------------------------
def test_theme_keeps_mobile_topbar_wordmark_and_hides_only_sidebar_duplicate():
    css = _theme()["customCSS"]
    # The Wave 3 rule blanked the lg:hidden top-bar wordmark too; Wave 4 keeps
    # the sidebar-scoped hide (the brand plugin renders the mark there) and
    # drops the unscoped `header span` rule.
    assert "header span.text-midground.font-bold{display:none" not in css, \
        "mobile top-bar wordmark is still hidden"
    assert "div.flex.h-14.shrink-0 span.text-midground.font-bold{display:none" \
        in css, "sidebar duplicate wordmark must stay hidden"
    # the branding test's required substrings survive
    assert "display:none" in css and "text-midground.font-bold" in css


def test_theme_carries_responsive_and_accessibility_contract():
    css = _theme()["customCSS"]
    for needle in (
            "focus-visible",
            "prefers-reduced-motion",
            "prefers-contrast",
            "forced-colors",
            "@media (max-width:768px)",
            "@media (pointer:coarse)",
            "min-height:44px",
            'data-state="loading"',
            'data-state="empty"',
            'data-state="degraded"',
            'data-state="pending"',
            'data-state="cancelled"',
            'data-state="recovering"',
    ):
        assert needle in css, f"theme customCSS missing {needle!r}"
    assert len(css.encode("utf-8")) < 32 * 1024


# ---------------------------------------------------------------------------
# Overlay plugin inventory + real routes
# ---------------------------------------------------------------------------
def test_all_ten_overlay_plugins_still_register_and_are_qubit_labelled():
    manifests = sorted(PLUGDIR.glob("*/manifest.json"))
    assert sorted(p.parent.name for p in manifests) == EXPECTED_PLUGINS
    for mf in manifests:
        man = json.loads(mf.read_text())
        text = _plugin_text(man["name"])
        assert f'register("{man["name"]}"' in text, man["name"]
        blob = (man["label"] + " " + man["description"]).lower()
        assert "hermes" not in blob and "openclaw" not in blob, man["name"]


def test_every_overlay_backend_literal_is_real_or_known():
    """Each /api literal is either a known Qubit route or resolves in compat."""
    from voicebridge import qubit_compat as HC

    known_patterns = [r["pattern"] for r in HC.ROUTES]
    seen = set()
    for name in EXPECTED_PLUGINS:
        for lit in _ENDPOINT_RE.findall(_plugin_text(name)):
            lit = lit.rstrip("/")
            if lit in KNOWN_ENDPOINTS:
                seen.add(lit)
                continue
            assert any(re.fullmatch(p, lit) for p in known_patterns), \
                f"{name}: unmapped endpoint {lit}"
            seen.add(lit)
    assert len(seen) >= 15, sorted(seen)


def test_ops_console_uses_real_broker_and_typed_states():
    text = _plugin_text("qubit-ops")
    for needle in ('"/api/ops/run"', '"/api/ops/"', '"confirm"', '"cancel"'):
        assert needle in text, needle
    # wrong label fixed
    assert "voice: refusal" not in text
    assert "voice: linked" in text
    for state in ("loading", "degraded", "pending", "empty"):
        assert f'"data-state": "{state}"' in text, state


def test_runtime_tab_is_a_real_inventory_not_a_json_status_box():
    text = _plugin_text("qubit-runtime")
    assert '"/api/ops/runtime"' in text
    assert 'e("table"' in text, "runtime must render the real inventory table"
    for state in ("loading", "empty", "degraded"):
        assert f'"data-state": "{state}"' in text, state
    assert "Raw backend payload" in text  # raw payload is disclosure-only


def test_every_visible_data_page_emits_typed_states():
    for name in EXPECTED_PLUGINS:
        if _manifest(name)["tab"].get("hidden"):
            continue  # hidden brand/approval slots have no data surface
        text = _plugin_text(name)
        assert "data-state" in text, f"{name} emits no typed states"


def test_approvals_and_boards_expose_pending_and_cancellation():
    for name in ("qubit-approvals", "qubit-boards", "qubit-gateway", "qubit-ops"):
        text = _plugin_text(name)
        assert '"data-state": "pending"' in text, name
        assert '"/api/ops/"' in text, name  # confirm/cancel journal lives here


def test_brand_notice_has_no_self_referential_dead_link():
    text = _plugin_text("qubit-brand")
    assert 'href: "/docs"' not in text, \
        "docs:top notice must not link to /docs (it renders there)"
    assert "Open the Qubit operator guide" not in text


# ---------------------------------------------------------------------------
# Pinned Achievements + Kanban tabs: real backend, Qubit-branded, localized
# ---------------------------------------------------------------------------
def test_achievements_tab_is_qubit_branded_and_calls_real_backend():
    bundle = PUBLIC_PLUGINS / "hermes-achievements" / "dist" / "index.js"
    text = bundle.read_text(encoding="utf-8")
    assert 'register("hermes-achievements"' in text
    assert "/api/plugins/hermes-achievements" in text
    assert "Qubit Achievements" in text
    assert "tx(" in text, "achievements must resolve host translations"
    clean = text.replace("X-Hermes-Session-Token", "")
    assert not _PRODUCT_NAME.search(clean), "achievements exposes upstream name"


def test_kanban_tab_is_qubit_branded_and_calls_real_backend():
    bundle = PUBLIC_PLUGINS / "kanban" / "dist" / "index.js"
    text = bundle.read_text(encoding="utf-8")
    assert 'register("kanban"' in text
    assert "/api/plugins/kanban/" in text
    assert "Qubit Kanban" in text
    assert "tx(" in text, "kanban must resolve host translations"
    assert not _PRODUCT_NAME.search(text), "kanban bundle exposes upstream name"


def test_kanban_and_achievements_have_host_localization_namespaces():
    en = (WEB_SRC / "i18n" / "en.ts").read_text(encoding="utf-8")
    types = (WEB_SRC / "i18n" / "types.ts").read_text(encoding="utf-8")
    for ns in ("achievements:", "kanban:"):
        assert ns in en, ns
        assert ns in types, ns


# ---------------------------------------------------------------------------
# Visible tabs point at real routes (no dead nav targets)
# ---------------------------------------------------------------------------
def test_visible_overlay_tabs_target_real_routes():
    from voicebridge import qubit_compat as HC

    app = (REPO_ROOT / "frontend" / "qubit-web" / "src" / "App.tsx").read_text()
    core = set(re.findall(r'"([^"]+)":', re.search(
        r"const BUILTIN_ROUTES_CORE[^{]*\{(.*?)\n\};", app, re.S).group(1)))
    served = {m["tab"]["path"] for m in HC.plugins_response()["plugins"]}
    all_paths = core | served | {"/chat"}
    for m in HC.plugins_response()["plugins"]:
        tab = m["tab"]
        pos = tab.get("position")
        if not pos or tab.get("hidden"):
            continue
        assert pos.startswith(("after:", "before:")), (m["name"], pos)
        target = "/" + pos.split(":", 1)[1]
        assert target in all_paths, (m["name"], pos, "dead nav target")


# ---------------------------------------------------------------------------
# No placeholders in the overlay or vendored dashboard source
# ---------------------------------------------------------------------------
def test_no_placeholder_tokens_in_overlay_or_vendored_source():
    hits = []
    roots = [OVERLAY, WEB_SRC]
    for root in roots:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in (".js", ".ts", ".tsx"):
                continue
            if ".test." in path.name:
                continue
            for m in _PLACEHOLDERS.finditer(
                    path.read_text(encoding="utf-8", errors="replace")):
                hits.append(f"{path.relative_to(REPO_ROOT)}:{m.group(0)}")
    assert not hits, hits


def test_vendored_dashboard_has_no_tiny_placeholder_page():
    """Every route page is a substantial real workspace — DocsPage included.

    S6 replaced the old docs iframe wrapper with a live OpenAPI reference, so
    DocsPage must now stand on its own: it fetches the canonical Qubit
    ``/openapi.json``, flattens real operations, groups them, renders
    parameters/responses, and reports typed loading/error/empty states. The
    tiny placeholder wrapper (``<iframe``) must not return.
    """
    pages = sorted((WEB_SRC / "pages").glob("*.tsx"))
    real = [p for p in pages if not p.name.endswith(".test.tsx")]
    # 21 pages: the 19 preserved dashboard pages plus the Qubit-added
    # TerminalChatPage (`/chat` real terminal; ChatPage owns `/qubit-chat`)
    # and AgentsPage (`/agents` canonical roster).
    assert len(real) == 21, [p.name for p in real]
    for p in real:
        assert len(p.read_text(encoding="utf-8").splitlines()) >= 100, p.name
    docs = (WEB_SRC / "pages" / "DocsPage.tsx").read_text(encoding="utf-8")
    # a real OAS reference, not a wrapper: canonical doc fetch + parser
    for needle in (
            "/openapi.json",
            "flattenOperations",
            "groupKey",
            "operations",
            "groups",
            "parameters",
            "responses",
            "deprecated",
            'data-state="loading"',
            'data-state="error"',
            'data-state="empty"',
            'PluginSlot name="docs:top"',
            'PluginSlot name="docs:bottom"',
    ):
        assert needle in docs, f"DocsPage missing real OAS reference marker {needle!r}"
    assert "<iframe" not in docs, \
        "DocsPage must be a real OAS reference, not the placeholder iframe"
