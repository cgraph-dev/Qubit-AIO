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


def test_settings_uses_escaping_chokepoint() -> None:
    text = _read("settings.html")
    assert 'function show(id,html)' in text and 'innerHTML' in text
    assert 'esc(' in text
    assert '&quot;' in text and '&#39;' in text, \
        "settings esc() must escape quotes for attribute contexts"


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