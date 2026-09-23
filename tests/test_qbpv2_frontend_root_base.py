"""Public rebrand gate — root base path and the `/chat` route.

The public dashboard must be served at the Qubit-owned root: no `/hermes/`
base path, root-relative assets (`/assets/...`, `/favicon.ico`), and chat at
`/chat` (never `/hermes/chat`). The vendored SPA addresses its API through the
Qubit-owned `window.__QUBIT_API_PREFIX__` (`/agent`); the literal `/api/...`
call sites stay unchanged and are rewritten centrally in `src/lib/api.ts`.

These checks are static/artifact-level (no browser, no server). The live
served-at-root proof lives in the Playwright suites.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DIST = REPO_ROOT / "frontend" / "qubit-dist"
SRC = REPO_ROOT / "frontend" / "qubit-web" / "src"
DIST_BUILT = (DIST / "index.html").is_file()


def _index_html() -> str:
    return (DIST / "index.html").read_text(encoding="utf-8")


def _all_dist_text() -> str:
    out = []
    for path in sorted(DIST.rglob("*")):
        if path.is_file() and path.suffix in (".js", ".html", ".json"):
            out.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Source contract
# ---------------------------------------------------------------------------
def test_vite_builds_at_the_root_base():
    cfg = (REPO_ROOT / "frontend" / "qubit-web" / "vite.config.ts").read_text(
        encoding="utf-8")
    assert re.search(r'base:\s*"/"', cfg), "vite base must be the root"
    assert "../qubit-dist" in cfg, "build must emit frontend/qubit-dist"


def test_spa_reads_the_qubit_owned_api_prefix():
    api = (SRC / "lib" / "api.ts").read_text(encoding="utf-8")
    assert "__QUBIT_API_PREFIX__" in api
    assert 'return "/agent"' in api, "default Qubit API prefix"
    assert "withApiPrefix" in api
    # the vendored literal call sites are untouched; the helper maps them
    assert "/api/" in api
    assert re.search(
        r'fetch\(`\$\{BASE\}\$\{withApiPrefix\(url\)\}`', api), \
        "fetchJSON/authedFetch must apply withApiPrefix"
    assert "withApiPrefix(path)" in api, "buildWsUrl must prefix WS paths"


def test_chat_is_the_real_client_route():
    app = (SRC / "App.tsx").read_text(encoding="utf-8")
    assert 'path: "/chat"' in app
    # /chat is the Qubit-branded Hermes terminal page; /qubit-chat keeps the
    # native Qubit ChatPage host. Both are real client routes.
    assert '"/chat": TerminalChatPage' in app
    assert '"/qubit-chat": ChatRouteSink' in app
    assert "/hermes/chat" not in app


# ---------------------------------------------------------------------------
# Built artifact contract
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not DIST_BUILT, reason="hermes-dist not built")
def test_built_shell_has_root_asset_paths_and_no_qubit_base():
    html = _index_html()
    assert "<title>Qubit Agent" in html
    assert "/hermes/" not in html
    for ref in re.findall(r'(?:src|href)="([^"]+)"', html):
        assert ref.startswith("/") and not ref.startswith("/hermes"), ref
        assert "/hermes/" not in ref, ref
    # every script/style is a root asset or the root favicon
    assert re.search(r'src="/assets/[^"]+\.js"', html)
    assert 'href="/favicon.ico"' in html


@pytest.mark.skipif(not DIST_BUILT, reason="hermes-dist not built")
def test_built_assets_use_no_qubit_asset_or_base_path():
    text = _all_dist_text()
    # No asset/base/util URL under a /hermes/ prefix (plugins, fonts, public).
    for banned in ("/hermes/assets", "/hermes/favicon", "/hermes/fonts",
                   "/hermes/plugins", "/hermes/dashboard-plugins",
                   '"/hermes/', "'/hermes/"):
        assert banned not in text, f"built bundle still uses {banned!r}"
    # Any residual /hermes/ token must be a compat API endpoint literal only
    # (the vendored `/api/hermes/update` call sites are intentionally kept).
    for match in re.finditer(r"/hermes/[A-Za-z0-9_./-]*", text):
        assert match.group(0) in {"/hermes/update", "/hermes/update/check"}, \
            match.group(0)


@pytest.mark.skipif(not DIST_BUILT, reason="hermes-dist not built")
def test_built_bundle_has_chat_route_and_api_prefix():
    text = _all_dist_text()
    assert '"/chat"' in text, "built SPA missing the /chat route"
    assert '"/hermes/chat"' not in text, "built SPA still exposes /hermes/chat"
    assert "__QUBIT_API_PREFIX__" in text, \
        "built SPA does not consume the Qubit API prefix"
    assert "/agent" in text, "built SPA missing the Qubit API prefix"


@pytest.mark.skipif(not DIST_BUILT, reason="hermes-dist not built")
def test_built_plugin_manifests_live_at_root():
    fleet = json.loads(
        (DIST / "plugins" / "fleet.json").read_text(encoding="utf-8"))
    assert {m["name"] for m in fleet} == {"hermes-achievements", "kanban"}
    for entry in fleet:
        base = entry["assetBase"]
        assert base.startswith("/") and "/hermes/" not in base, base
