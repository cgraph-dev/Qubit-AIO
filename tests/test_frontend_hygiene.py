"""Packet 8L static frontend-hygiene checks.

Hermetic (no server, no browser): these tests read the repository frontend
files and enforce the 8L contract:

- every JSON API call goes through the shared authenticated typed helper
  (`frontend/vb.js` vbApi) or the shared stream helper (`frontend/ui.js`
  vbStream); no page performs a raw `fetch`, `XMLHttpRequest`, or `EventSource`;
- the migrated pages (activity/approvals/reports) render with safe DOM APIs and
  contain no raw dynamic `innerHTML`;
- index.html renders message/backend text via `textContent` only;
- settings.html uses the escaping choke-point (`show()` + quote-escaping `esc`);
- every page has the shared auth helper, shared stylesheet, and the structural
  WCAG 2.2 AA scaffolding (lang, main landmark, skip link, viewport, nav
  aria-current, labels, aria-live status);
- `app.css` ships the required accessibility primitives.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND = REPO_ROOT / "frontend"

PAGES = ["activity.html", "approvals.html", "index.html",
         "reports.html", "settings.html", "token-setup.html"]

# Pages that were migrated to safe DOM rendering in Packet 8L.
DOM_RENDERED = ["activity.html", "approvals.html", "reports.html"]


def _read(name: str) -> str:
    text = (FRONTEND / name).read_text(encoding="utf-8")
    # strip HTML comments so explanatory prose does not trip content checks
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def test_all_pages_link_shared_auth_helper_and_stylesheet() -> None:
    for page in PAGES:
        text = _read(page)
        assert '/static/vb.js' in text, f"{page}: missing shared auth helper"
        assert '/static/app.css' in text, f"{page}: missing shared stylesheet"
        assert 'id="main-content"' in text, f"{page}: missing main landmark target"
        assert 'class="skip-link"' in text and 'href="#main-content"' in text, \
            f"{page}: missing skip link"


def test_migrated_pages_link_shared_ui_primitives() -> None:
    for page in DOM_RENDERED + ["index.html"]:
        text = _read(page)
        assert '/static/ui.js' in text, f"{page}: missing shared ui.js"


def test_no_raw_api_fetch_in_any_page() -> None:
    for page in PAGES:
        text = _read(page)
        assert 'fetch(' not in text, f"{page}: raw fetch (must use vbApi/vbStream)"
        assert 'XMLHttpRequest' not in text, f"{page}: raw XHR"
        assert 'EventSource(' not in text, f"{page}: EventSource (header token impossible)"


def test_real_main_landmark_on_every_page() -> None:
    """Every route must use a real semantic <main> landmark (not a div/h1)
    carrying id=main-content + tabindex=-1, with the skip link pointing at it."""
    for page in PAGES:
        text = _read(page)
        mains = re.findall(r'<main\b[^>]*>', text)
        assert len(mains) == 1, f"{page}: expected exactly one <main>, got {len(mains)}"
        tag = mains[0]
        assert 'id="main-content"' in tag, f"{page}: <main> lacks id=main-content"
        assert 'tabindex="-1"' in tag, f"{page}: <main> lacks tabindex=-1"
        assert not re.search(r'<div[^>]*\bid="main-content"', text), \
            f"{page}: a div still carries the main-content landmark"
        assert not re.search(r'<h1[^>]*\bid="main-content"', text), \
            f"{page}: an h1 still carries the main-content landmark"
        assert 'class="skip-link"' in text and 'href="#main-content"' in text, \
            f"{page}: skip link must target the main landmark"


def test_no_dynamic_html_in_migrated_pages() -> None:
    for page in DOM_RENDERED:
        text = _read(page)
        assert not re.search(r"\.\s*innerHTML\s*=", text), \
            f"{page}: innerHTML assignment (use vbBuild/textContent)"
        assert 'insertAdjacentHTML' not in text, f"{page}: insertAdjacentHTML"
        assert not re.search(r"\.\s*outerHTML\s*=", text), f"{page}: outerHTML"
        assert 'document.write' not in text, f"{page}: document.write"


def test_index_renders_with_textcontent_only() -> None:
    text = _read("index.html")
    assert not re.search(r"\.\s*innerHTML\s*=", text)
    assert 'vbApi(' in text
    assert 'vbStream(' in text


def test_settings_uses_audited_safe_renderer() -> None:
    """Packet 8L XSS repair: Settings has NO innerHTML assignments and NO
    inline event attribute carrying dynamic/backend data. All dynamic content
    is rendered through the shared audited sanitizer (vbSafeHTML), DOM
    construction (vbBuild/fillOptions), textContent, data attributes, and
    addEventListener."""
    text = _read("settings.html")
    assert "innerHTML" not in text, "settings.html must not use innerHTML"
    assert "vbSafeHTML" in text and "fillOptions" in text and "vbBuild" in text
    assert "addEventListener" in text
    assert 'esc(' in text  # text-context escaping still used for status copy
    # no backend-controlled value may reach an inline event attribute:
    # no onclick/onchange whose value interpolates esc() or a template/JS concat
    assert not re.search(r'on(click|change|keydown)="[^"]*esc\(', text), \
        "settings inline handler interpolates escaped dynamic data"
    assert not re.search(r'on(click|change|keydown)="[^"]*\$', text), \
        "settings inline handler uses a template literal"
    assert 'onclick="' + "'" not in text and "onclick=\"'" not in text, \
        "settings builds inline handlers via string concatenation"
    # no option-list / row HTML built by string interpolation
    assert not re.search(r"<\s*option[^>]*>\s*['\"]\s*\+", text)


def test_no_query_string_api_override_anywhere() -> None:
    """Packet 8L API-origin repair: no page may honor an attacker-controlled
    ?api= query string, and the shared helpers must not read it."""
    for page in PAGES:
        text = _read(page)
        assert "get('api')" not in text and 'get("api")' not in text, page
    ui = (FRONTEND / "ui.js").read_text(encoding="utf-8")
    vb = (FRONTEND / "vb.js").read_text(encoding="utf-8")
    assert "URLSearchParams" not in ui, "ui.js must not read ?api="
    assert "location.search" not in ui and "location.search" not in vb


def test_shared_authenticated_binary_helper_contract() -> None:
    """Packet 8L report-operations repair: the shared helper exposes
    authenticated binary/download/open helpers using the same header token, and
    no page creates an unauthenticated report URL (index Inbox AND the
    standalone /reports page)."""
    vb = (FRONTEND / "vb.js").read_text(encoding="utf-8")
    for name in ["vbFetchBinary", "vbDownload", "vbOpenBinary", "vbApiTest",
                 "vbValidateApiOrigin"]:
        assert name in vb, f"vb.js missing shared helper {name}"
    assert "X-Api-Token" in vb and "vbHeaders" in vb
    index = _read("index.html")
    assert "vbFetchBinary('/api/reports/" in index, \
        "index.html must fetch report assets/export/html via vbFetchBinary"
    assert "window.open(BASE" not in index and "BASE + '/api/reports" not in index, \
        "index.html must not open unauthenticated report URLs"
    reports = _read("reports.html")
    assert "openReportHtml" in reports and "vbOpenBinary" in reports, \
        "reports.html must open report HTML through the authenticated Blob flow"
    assert "vbFetchBinary('/api/reports/'" in reports
    assert not re.search(r"href:\s*['\"]/api/reports", reports), \
        "reports.html must not build an unauthenticated <a href> report URL"
    assert not re.search(r"\.html['\"]\s*,\s*target", reports), \
        "reports.html must not open the HTML endpoint as a target link"


def test_pair_hint_built_with_dom_nodes() -> None:
    """Packet 8L safety: vbPairHint() must build the pairing hint with DOM
    nodes/textContent — no innerHTML anywhere in the shared auth helper."""
    vb = (FRONTEND / "vb.js").read_text(encoding="utf-8")
    assert "innerHTML" not in vb, "vb.js must not use innerHTML"
    assert "createTextNode" in vb and "textContent" in vb
    assert "vbPairHint" in vb


def test_pairing_token_tested_before_save() -> None:
    """Packet 8L pairing repair: token-setup tests the candidate token via the
    request header BEFORE persisting it to localStorage."""
    text = _read("token-setup.html")
    assert "vbApiTest" in text
    assert "localStorage.setItem('vdb_token'" in text
    assert text.index("vbApiTest") < text.index("localStorage.setItem('vdb_token'"), \
        "token must be tested before it is saved"


def test_shared_helpers_never_parse_unchecked_json() -> None:
    vb = (FRONTEND / "vb.js").read_text(encoding="utf-8")
    assert "res.headers.get('Content-Type')" in vb
    assert 'indexOf(\'json\')' in vb
    assert 'vbPairHint' in vb
    ui = (FRONTEND / "ui.js").read_text(encoding="utf-8")
    assert 'vbStream' in ui and 'vbBuild' in ui and 'vbStatus' in ui
    assert 'textContent' in ui


def test_wcag_structure_per_page() -> None:
    for page in PAGES:
        text = _read(page)
        assert re.search(r'<html[^>]*\blang="en"', text), f"{page}: lang=en"
        assert re.search(
            r'<meta[^>]*name="viewport"[^>]*width=device-width', text), \
            f"{page}: responsive viewport"
        assert 'aria-label="Primary"' in text, f"{page}: nav labelled"
        assert 'aria-current="page"' in text, f"{page}: nav active marker"


def test_form_controls_have_labels() -> None:
    for page in PAGES:
        text = _read(page)
        for m in re.finditer(r'<(input|select)\b[^>]*\bid="([^"]+)"[^>]*>', text):
            cid = m.group(2)
            tag = m.group(0)
            if 'aria-label=' in tag or 'aria-labelledby=' in tag:
                continue
            if re.search(r'<label[^>]*for="%s"' % re.escape(cid), text):
                continue
            # wrapping label: <label>...<control id=...>...</label>
            if re.search(r'<label[^>]*>(?:(?!</label>).)*?id="%s"' % re.escape(cid),
                         text, flags=re.S):
                continue
            raise AssertionError(f"{page}: control #{cid} has no label")


def test_status_regions_are_live() -> None:
    for page in PAGES:
        text = _read(page)
        for m in re.finditer(r'<span[^>]*\bid="(conn|st|msg)"[^>]*>', text):
            tag = m.group(0)
            assert 'aria-live' in tag or 'role="status"' in tag, \
                f"{page}: status #{m.group(1)} not announced"


def test_app_css_accessibility_primitives() -> None:
    css = (FRONTEND / "app.css").read_text(encoding="utf-8")
    for needle in [":focus-visible", "prefers-reduced-motion", "sr-only",
                   "min-height: 44px", "forced-colors", "skip-link",
                   "overflow-wrap", "min-width: 320px"]:
        assert needle in css, f"app.css missing: {needle}"


def test_no_script_eval_or_dynamic_function() -> None:
    for page in PAGES + ["vb.js", "ui.js"]:
        text = _read(page)
        assert 'eval(' not in text, f"{page}: eval()"
        assert 'new Function(' not in text, f"{page}: new Function"


# ---------------------------------------------------------------------------
# Packet 8L polish regression: consistent Qubit VoiceBridge branding, the
# Talk-page onboarding panel, canonical pairing guidance, and a single shared
# visual palette (near-black + restrained lime). These are additive checks —
# they never weaken the existing Packet 8L contract tests above.
# ---------------------------------------------------------------------------

def test_branding_title_on_every_page() -> None:
    """Every dashboard page carries the product name in a consistent
    ``<title>`` ("Qubit VoiceBridge — <Section>")."""
    for page in PAGES:
        text = _read(page)
        title = re.search(r"<title>(.*?)</title>", text, flags=re.S)
        assert title, f"{page}: missing <title>"
        assert title.group(1).strip().startswith("Qubit VoiceBridge"), \
            f"{page}: title must start with the product name, got {title.group(1)!r}"


def test_talk_page_has_onboarding_help_panel() -> None:
    """Packet 8L polish: the Talk page must include a first-run onboarding/help
    panel that explains the safe workflow (pair -> talk -> activity -> reports)
    and names the canonical token location. Links inside the panel are real
    44px tap targets."""
    text = _read("index.html")
    assert 'class="help-panel"' in text, "Talk page missing onboarding help panel"
    assert "How Qubit works" in text, "Talk help panel missing its heading"
    for needle in ["Pair device", "Activity", "Reports", "Settings"]:
        assert needle in text, f"Talk onboarding must mention the {needle} step"
    assert '~/.config/qubit-voicebridge/voice.yaml' in text, \
        "Talk onboarding must name the canonical token location"
    assert "webui.token" in text
    # every onboarding link (inside the help panel only) must be a >=44px
    # tap target (WCAG 2.2 / 8L)
    panel = re.search(
        r'<details\b[^>]*class="help-panel"[^>]*>(.*?)</details>',
        text, flags=re.S)
    assert panel, "Talk page missing the help-panel details block"
    panel_html = panel.group(1)
    links = re.findall(r'<a\b[^>]*>', panel_html)
    assert links, "Talk onboarding must link the workflow steps"
    for tag in links:
        assert 'tap-target' in tag, \
            f"onboarding link {tag!r} must be a 44px tap target"


def test_token_setup_uses_canonical_qubit_path() -> None:
    """Packet 8L polish: the pairing page must reference the canonical
    ``qubit-voicebridge`` config path (never the legacy
    ``openclaw-voice-bridge``/``qubit-voice-agent`` names) and the real
    ``webui.token`` key."""
    text = _read("token-setup.html")
    assert "openclaw-voice-bridge" not in text, \
        "token-setup must not name the legacy config path"
    assert "qubit-voice-agent/voice.yaml" not in text, \
        "token-setup must not name the 7D intermediate path"
    assert "webui.token" in text and "voice.yaml" in text, \
        "token-setup must point at webui.token in voice.yaml"
    assert "qubit-voicebridge" in text, "token-setup must use the canonical product path"
    assert "vbApiTest" in text  # candidate token is tested before saving


def test_no_legacy_blue_palette_in_page_styles() -> None:
    """Packet 8L polish: every page's inline <style> block must use the shared
    design tokens (near-black surfaces + lime accent). The legacy GitHub-blue
    palette hexes are not allowed anywhere in the frontend — they would fight
    the shared app.css foundation."""
    legacy = re.compile(
        r"#(?:0b0e14|dbe2ef|22304a|131a26|7f8ca3|6db3ff|1f7a4d|8a6d1f|7a1f2d|"
        r"ff8080|58a6ff|1f6feb|30363d|0d1117|161b22|21262d|3fb950|d29922|f85149|"
        r"238636|da3633|5ad19b|bc8cff|a371f7|8b949e|e6edf3|33455f|1f5a7a)\b",
        re.IGNORECASE)
    for page in PAGES + ["ui.js", "vb.js"]:
        text = _read(page)
        matches = legacy.findall(text)
        assert not matches, f"{page}: legacy palette still present: {sorted(set(matches))}"


def test_shared_tokens_defined_in_app_css() -> None:
    """The design tokens the pages reference must be defined once in app.css."""
    css = (FRONTEND / "app.css").read_text(encoding="utf-8")
    for token in ["--bg", "--surface", "--surface-raised", "--surface-soft",
                  "--text", "--muted", "--faint", "--line", "--line-strong",
                  "--primary", "--primary-strong", "--success", "--warning",
                  "--danger", "--focus", "--radius", "--shadow"]:
        assert token in css, f"app.css missing token {token}"