"""Wave 3 — exhaustive product-facing branding parity + Qubit visual identity.

Complements the wave-1 dashboard rebrand scan and the 8T-F scans by walking
*every* normal product-facing surface for unreplaced upstream product names
and by validating the Qubit overlay / vendor provenance:

1. CLI help tree (all subcommands/flags) + shipped shell completions;
2. voice / terminal / orchestrator / report runtime strings (AST constants,
   docstrings excluded);
3. installer output;
4. dashboard shell title/nav + served SPA + the built bundle;
5. the overlay theme + plugin manifests/bundles;
6. the minimal Qubit HTML pages and setup/support docs;
7. ``docs/HERMES_SOURCE_COMPARISON.json`` vendor + overlay hashes;
8. the served Qubit theme carries the full visual identity (colors, fonts,
   spacing, responsive layout, accessibility, motion, and the loading /
   empty / degraded / approval / cancellation / recovery states).

Upstream names may survive only in legal/attribution/provenance/
source-comparison/migration/compat/developer records. Every permitted
survivor is enumerated in ``ALLOWED_SURFACE_PHRASES`` and classified with a
file:line in ``docs/BRANDING_AUDIT_WAVE3.md``.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

OVERLAY = REPO_ROOT / "frontend" / "hermes-qubit"
THEME = OVERLAY / "qubit-theme.json"
COMPLETIONS = REPO_ROOT / "voicebridge" / "_data" / "completions"
DIST = REPO_ROOT / "frontend" / "hermes-dist"
MANIFEST = REPO_ROOT / "docs" / "HERMES_SOURCE_COMPARISON.json"

_UPSTREAM = re.compile(r"\bHermes\b|\bOpenClaw\b")

# Substrings that legitimately keep an upstream name. Each is a
# migration/compat/developer record, not normal product identity:
#   * "OpenClaw migration ..."      -> the confirm-gated `qubit claw` family
#   * "never dispatch to OpenClaw"  -> the --dry-run help on the migration path
#   * "Qubit-owned Hermes <cmd>"    -> fork-forward help (the runtime is named
#                                      in the developer/compat help text)
#   * "OpenClaw Voice Bridge"       -> legacy service/unit names in the
#                                      migration find/replace table
#   * "voice bridge for OpenClaw"   -> legacy description in the same table
#   * "no Hermes gateway process" / "Hermes WebSocket dialect ... not served"
#                                   -> typed compat refusal details
#   * "in-process Hermes backend"   -> egress-guard developer diagnostics
ALLOWED_SURFACE_PHRASES = [
    "X-Hermes-Session-Token",
    "never dispatch to OpenClaw; only speak the plan",
    "arguments forwarded to the Qubit-owned Hermes",
    "OpenClaw migration tools (confirm-gated; no silent secret import)",
    "OpenClaw migration is an attended CLI/dashboard operation "
    "and never runs from a spoken command",
    "OpenClaw Voice Bridge",
    "voice bridge for OpenClaw",
    "no Hermes gateway process",
    "Hermes WebSocket dialect is not served",
    "Hermes tui_gateway WS dialect is not served",
    "in-process Hermes backend",
]

# Bundle identifiers/globals that are wire protocol or developer API, not
# product-facing text (kept for compatibility).
PROTOCOL_TOKENS = [
    "X-Hermes-Session-Token",
    "__HERMES_SESSION_TOKEN__",
    "__HERMES_BASE_PATH__",
]

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"(?<!:)//[^\n]*")


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _strip_js_comments(text: str) -> str:
    text = _BLOCK_COMMENT.sub("", text)
    return _LINE_COMMENT.sub("", text)


def _residual(text: str) -> list[str]:
    """Upstream tokens left after removing every permitted survivor."""
    for phrase in ALLOWED_SURFACE_PHRASES:
        text = text.replace(phrase, "")
    return [m.group(0) for m in _UPSTREAM.finditer(text)]


def _py_runtime_strings(path: Path):
    """Yield (lineno, value) for non-docstring string literals."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                docstrings.add(id(node.body[0].value))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstrings):
            yield node.lineno, node.value


# ---------------------------------------------------------------------------
# 1. CLI help tree + completions
# ---------------------------------------------------------------------------
def _walk_parser(parser):
    seen = set()
    for action in parser._actions:
        for text in (action.help, getattr(action, "description", None),
                     getattr(action, "epilog", None), action.dest,
                     getattr(action, "metavar", None)):
            if isinstance(text, str):
                yield parser.prog, text
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            for sub in choices.values():
                if hasattr(sub, "_actions") and id(sub) not in seen:
                    seen.add(id(sub))
                    yield from _walk_parser(sub)


def test_cli_help_tree_has_no_unrebranded_product_names():
    from voicebridge.cli import build_parser

    hits = []
    for prog, text in _walk_parser(build_parser()):
        residual = _residual(text)
        if residual:
            hits.append(f"{prog}: {residual} in {text[:100]!r}")
    assert not hits, "product-facing CLI help still exposes upstream names: " \
        + "; ".join(hits[:10])


def test_shell_completions_are_qubit_only():
    assert COMPLETIONS.is_dir(), COMPLETIONS
    files = sorted(COMPLETIONS.iterdir())
    assert files, "no shipped completion files"
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert "Qubit" in text, f"{path.name}: not Qubit-branded"
        assert "qubit" in text.lower(), f"{path.name}: no qubit command"
        for token in ("hermes", "openclaw"):
            assert token not in text.lower(), \
                f"{path.name}: upstream identity token {token!r}"


# ---------------------------------------------------------------------------
# 2. Voice / terminal / report runtime strings
# ---------------------------------------------------------------------------
def test_voice_runtime_strings_have_no_unrebranded_product_names():
    hits = []
    for path in sorted((REPO_ROOT / "voicebridge").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for lineno, text in _py_runtime_strings(path):
            residual = _residual(text)
            if residual:
                rel = path.relative_to(REPO_ROOT).as_posix()
                hits.append(f"{rel}:{lineno}: {residual} in {text[:90]!r}")
    assert not hits, "runtime/voice strings still expose upstream names: " \
        + "; ".join(hits[:10])


# ---------------------------------------------------------------------------
# 3. Installer output
# ---------------------------------------------------------------------------
def test_installer_output_is_qubit_branded():
    text = (REPO_ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    echoed = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("echo ", "printf ")):
            echoed.append(stripped)
    assert echoed, "install.sh has no user-facing output lines"
    hits = [line for line in echoed if _residual(line)]
    assert not hits, f"installer output names upstream: {hits[:5]}"


# ---------------------------------------------------------------------------
# 4. Dashboard shell / served SPA / built bundle
# ---------------------------------------------------------------------------
def test_overlay_theme_and_plugins_are_qubit_branded():
    theme = json.loads(THEME.read_text(encoding="utf-8"))
    assert theme["name"] == "qubit", theme["name"]
    assert not _residual(json.dumps(theme)), \
        "overlay theme still names an upstream product"
    for path in sorted(OVERLAY.rglob("*.js")):
        stripped = _strip_js_comments(path.read_text(encoding="utf-8"))
        residual = _residual(stripped)
        assert not residual, \
            f"{path.relative_to(REPO_ROOT)}: overlay bundle names upstream " \
            f"{residual}"
    for path in sorted((OVERLAY / "plugins").glob("*/manifest.json")):
        man = json.loads(path.read_text(encoding="utf-8"))
        blob = f'{man.get("label", "")} {man.get("description", "")}'
        assert not _residual(blob), \
            f"{path.relative_to(REPO_ROOT)}: manifest names upstream"


def test_minimal_frontend_pages_are_qubit_branded():
    hits = []
    for path in sorted((REPO_ROOT / "frontend").glob("*.html")) + \
            sorted((REPO_ROOT / "frontend").glob("*.js")):
        residual = _residual(path.read_text(encoding="utf-8",
                                             errors="replace"))
        if residual:
            hits.append(f"{path.name}: {residual}")
    assert not hits, f"Qubit shell pages still name upstream: {hits}"


def test_support_docs_are_qubit_branded():
    for rel in ("README.md", "docs/AUDIO_SETUP.md",
                "docs/VOICE_BRIDGE_SYSTEM.md", "docs/HowTo.md"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        residual = _residual(text)
        assert not residual, f"{rel}: support doc names upstream {residual}"


def _start_webui(tmp_path):
    from voicebridge.config import Config
    from voicebridge.webui import WebUI

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    cfg_file = tmp_path / "voice.yaml"
    cfg_file.write_text(
        f"logging:\n  jsonl_path: '{state}'\nwebui:\n  token: test-token\n")
    cfg = Config.load(cfg_file)

    class _FakeOrch:
        _route = "local"
        _web_active = 0
        task = type("T", (), {"active": False})()

        def __init__(self):
            self.cfg = cfg
            self._t0 = time.time()

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    return ui, ui._server.server_address[1]


def _get(port, path):
    import urllib.request

    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path), method="GET")
    req.add_header("X-Hermes-Session-Token", "test-token")
    with urllib.request.urlopen(req, timeout=8) as resp:
        return resp.status, resp.headers.get("Content-Type", ""), resp.read()


def test_served_spa_shell_and_plugins_are_qubit_branded(tmp_path):
    ui, port = _start_webui(tmp_path)
    try:
        status, ctype, body = _get(port, "/hermes/")
        assert status == 200 and "text/html" in ctype
        html = body.decode("utf-8")
        assert "<title>Qubit Agent" in html, html[:200]
        for token in PROTOCOL_TOKENS:
            html = html.replace(token, "")
        assert not _residual(html), "served shell leaks an upstream product name"

        status, ctype, body = _get(port, "/hermes/api/dashboard/plugins")
        assert status == 200 and "application/json" in ctype, (status, body[:200])
        # The endpoint returns {"plugins": [...]} when driven through the
        # served HTTP boundary (compat dispatch returns the bare list).
        plugins = json.loads(body)
        if isinstance(plugins, dict):
            plugins = plugins.get("plugins", [])
        assert len(plugins) == 10, plugins
        for man in plugins:
            blob = f'{man.get("label", "")} {man.get("description", "")}'
            assert not _residual(blob), (man.get("name"), blob)
    finally:
        ui.stop()


def test_built_spa_bundle_is_qubit_branded():
    index = DIST / "index.html"
    if not index.is_file():
        pytest.skip("frontend/hermes-dist not built in this checkout")
    html = index.read_text(encoding="utf-8")
    assert "<title>Qubit Agent" in html, html[:200]

    bundle = []
    for path in sorted((DIST / "assets").glob("*.js")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for token in PROTOCOL_TOKENS:
            text = text.replace(token, "")
        residual = _residual(text)
        if residual:
            bundle.append(f"{path.name}: {residual}")
    assert not bundle, f"built bundle still exposes upstream names: {bundle[:5]}"

    # The rebrand must not have shrunk the baseline route map.
    blob = "".join(p.read_text(encoding="utf-8", errors="replace")
                   for p in sorted((DIST / "assets").glob("*.js")))
    for route in ("/sessions", "/chat", "/files", "/models", "/logs",
                  "/cron", "/skills", "/plugins", "/mcp", "/channels",
                  "/webhooks", "/pairing", "/profiles", "/config", "/system"):
        assert f'"{route}"' in blob, f"built route map missing {route}"


def test_spa_shipped_fleet_bundles_are_qubit_branded():
    """The pinned Achievements/Kanban plugin bundles ship inside the served
    SPA (`public/plugins` -> `dist/plugins`), so their rendered text is a
    normal product-facing surface. Comments and wire/cookie identifiers are
    not product text; everything else must read as Qubit."""
    roots = [REPO_ROOT / "frontend" / "hermes-web" / "public" / "plugins",
             DIST / "plugins"]
    seen = 0
    hits = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.js")):
            seen += 1
            text = _strip_js_comments(
                path.read_text(encoding="utf-8", errors="replace"))
            for token in PROTOCOL_TOKENS:
                text = text.replace(token, "")
            for match in _UPSTREAM.finditer(text):
                rel = path.relative_to(REPO_ROOT).as_posix()
                start = max(0, match.start() - 60)
                hits.append(f"{rel}: ...{text[start:match.end() + 60]!r}...")
    assert seen, "no SPA-shipped fleet bundles found"
    assert not hits, (
        "SPA-shipped plugin bundles still render upstream product names "
        f"({len(hits)} occurrences): " + "; ".join(hits[:6]))


# ---------------------------------------------------------------------------
# 5. Provenance manifests
# ---------------------------------------------------------------------------
def test_vendor_manifest_hashes_are_valid():
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = man["rows"]
    checked = 0
    for row in rows:
        assert row.get("reason"), row
        patch = row.get("patch") or ""
        assert "MISMATCH" not in patch and "MISSING" not in patch, row
        qubit = row.get("qubit")
        if not qubit:
            continue
        path = REPO_ROOT / qubit
        assert path.is_file(), f"manifest path missing: {qubit}"
        assert _sha(path) == row["qubit_sha256"], \
            f"manifest qubit hash stale: {qubit}"
        checked += 1
    assert checked >= 200, checked
    assert "wave3" in man, "wave 3 overlay provenance block missing"
    assert man["wave3"]["vendor_patches"] == [], \
        "wave 3 declares vendored patches; record exact patch + reason"


def test_overlay_manifest_hashes_are_valid():
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    wave3 = man["wave3"]
    for row in wave3.get("added", []):
        path = REPO_ROOT / row["qubit"]
        assert path.is_file(), row
        assert _sha(path) == row["qubit_sha256"], \
            f"wave3.added hash stale: {row['qubit']}"
        assert row.get("role"), row
    listed = {}
    for row in wave3["overlay"]:
        path = REPO_ROOT / row["qubit"]
        assert path.is_file(), row
        assert _sha(path) == row["qubit_sha256"], \
            f"overlay hash stale: {row['qubit']}"
        assert row.get("role"), row
        listed[row["qubit"]] = True
    disk = {
        p.relative_to(REPO_ROOT).as_posix()
        for p in OVERLAY.rglob("*") if p.is_file()
    }
    assert disk == set(listed), \
        f"overlay manifest drift missing={disk - set(listed)} " \
        f"extra={set(listed) - disk}"
    assert listed["frontend/hermes-qubit/qubit-theme.json"]


# ---------------------------------------------------------------------------
# 6. Served Qubit theme = full visual identity
# ---------------------------------------------------------------------------
def test_served_theme_carries_full_visual_identity(tmp_path):
    from voicebridge import hermes_compat as HC
    from voicebridge.config import Config

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    cfg_file = tmp_path / "voice.yaml"
    cfg_file.write_text(f"logging:\n  jsonl_path: '{state}'\n")
    cfg = Config.load(cfg_file)

    status, payload = HC.dispatch(cfg, "GET", "/api/dashboard/themes", {}, {})
    assert status == 200 and payload["active"] == "qubit", payload
    theme = payload["themes"][0]["definition"]
    assert theme == json.loads(THEME.read_text(encoding="utf-8")), \
        "served theme diverges from the recorded overlay file"

    # identity: graphite/navy + signal-lime and electric-cyan
    assert theme["palette"]["background"]["hex"] == "#10141c"
    assert theme["colorOverrides"]["accent"] and \
        theme["colorOverrides"]["ring"] == "#a3e635"
    assert theme["seriesColors"]["inputTokenAccent"] == "#22d3ee"
    assert theme["seriesColors"]["outputTokenAccent"] == "#a3e635"

    # typography: proportional sans + terminal monospace, readable scale
    assert "Inter" in theme["typography"]["fontSans"]
    assert "JetBrains Mono" in theme["typography"]["fontMono"]
    assert theme["typography"]["baseSize"].endswith("px")

    # spacing / radius / responsive layout
    assert theme["layout"]["radius"] and theme["layout"]["density"] in (
        "compact", "comfortable", "spacious")
    assert theme["layoutVariant"] in ("standard", "cockpit", "tiled")

    # accessibility, motion, and the required UI states live in customCSS
    css = theme["customCSS"]
    for needle in (
            "display:none",                      # vendored wordmark hidden
            "text-midground.font-bold",          # wordmark selector
            "focus-visible",                     # keyboard focus ring
            "prefers-reduced-motion",            # reduced motion
            "prefers-contrast",                  # high contrast
            "forced-colors",                     # forced-colors
            "pointer:coarse",                    # 44px touch targets
            "min-height:44px",                   # 44px touch targets
            "@media (max-width:768px)",          # responsive narrow layout
            "data-state=\"loading\"",            # loading
            "data-state=\"empty\"",              # empty
            "data-state=\"degraded\"",           # degraded
            "data-state=\"pending\"",            # approval / pending
            "data-state=\"cancelled\"",          # cancellation
            "data-state=\"recovering\"",         # recovery
            "@keyframes qubit-pulse",            # motion
    ):
        assert needle in css, f"theme customCSS missing {needle!r}"
    assert len(css.encode("utf-8")) < 32 * 1024, "customCSS exceeds theme cap"
