"""Writer/reader agreement for the dashboard's bootstrapped ``window.__QUBIT_*__`` globals.

The server (``qubit_cli.web_server_dashboard``) writes these globals into the served
``index.html``; the SPA and the Electron shell read them. A name split on either side is
invisible to unit tests that stub ``window`` themselves (``api.test.ts`` does exactly
that), so it ships silently and breaks auth at runtime: the desktop cannot extract the
session token, so every dashboard API read 401s.

These tests assert the *relationship* between the two sides — each reader's name is
scraped from the reader's own source, then checked against real server output. They do
not pin a snapshot, so renaming the globals consistently on both sides keeps them green
while a one-sided rename (the bug this file exists for) fails.
"""

import re
from pathlib import Path

import pytest

import qubit_cli.web_server as ws
import qubit_cli.web_server_dashboard as _dashboard

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WEB_SRC = _REPO_ROOT / "qubit_runtime" / "web" / "src"
_ELECTRON = _REPO_ROOT / "qubit_runtime" / "apps" / "desktop" / "electron"

#: Every global the dashboard bootstrap script writes. Scraped *from the writer source*
#: by :func:`_writer_globals` rather than hardcoded, so a newly-added writer global that
#: no reader knows about still shows up in the agreement check below.
_GLOBAL_REF = re.compile(r"window\.(__QUBIT_[A-Z_]+__)")


def _js_regex_to_python(js_body: str) -> str:
    """Translate a JS regex literal body to an equivalent Python pattern.

    Only the escapes this contract uses are meaningful: ``\\.``/``\\s``/``\\\\``. A JS
    regex source has the *extra* backslash level of a JS string literal only when it
    was embedded in a string; a literal like ``/window\\.__X__/`` already holds one
    backslash per escape, so passing it through verbatim is correct once surrounding
    delimiters are stripped.
    """
    return js_body


def _writer_globals() -> set[str]:
    """Global names the dashboard server actually emits into index.html."""
    source = Path(_dashboard.__file__).read_text(encoding="utf-8")
    # Only the bootstrap-script region writes globals; comment prose mentions them too,
    # so keep the f-string/statement forms that assign a value.
    names = set()
    for match in _GLOBAL_REF.finditer(source):
        name = match.group(1)
        # An assignment writes the global; a bare mention is documentation.
        tail = source[match.end():match.end() + 3].lstrip()
        if tail.startswith("="):
            names.add(name)
    return names


def _reader_source(*relative: str) -> str:
    path = Path(*relative)
    assert path.is_file(), f"reader source missing: {path}"
    return path.read_text(encoding="utf-8")


@pytest.fixture
def served_index(tmp_path, monkeypatch):
    """The real ``index.html`` body the dashboard serves to a browser/desktop client."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    dist = tmp_path / "web_dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        "<html><head></head><body>SPA</body></html>", encoding="utf-8"
    )
    monkeypatch.setattr(ws, "WEB_DIST", dist)
    monkeypatch.delenv("QUBIT_SERVE_HEADLESS", raising=False)

    app = FastAPI()
    app.state.auth_required = False
    _dashboard.mount_spa(app)
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200, resp.text
    return resp.text


def test_desktop_extractor_finds_the_token_the_server_serves(tmp_path, monkeypatch):
    """The Electron shell extracts the token with a regex from its own source.

    Apply *that* regex to the served HTML. If the writer and the desktop disagree on the
    global's name, the extractor returns ``None`` and desktop auth 401s everywhere.
    """
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    monkeypatch.setattr(ws, "_SESSION_TOKEN", "desktop-handshake-token")
    dist = tmp_path / "web_dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        "<html><head></head><body>SPA</body></html>", encoding="utf-8"
    )
    monkeypatch.setattr(ws, "WEB_DIST", dist)
    monkeypatch.delenv("QUBIT_SERVE_HEADLESS", raising=False)
    app = FastAPI()
    app.state.auth_required = False
    _dashboard.mount_spa(app)
    served_index = TestClient(app).get("/").text
    assert "desktop-handshake-token" in served_index, served_index[:400]

    tsc = _reader_source(_ELECTRON, "dashboard-token.ts")
    literal = re.search(r"const match = (/.*?/)\.exec\(", tsc, re.S)
    assert literal, "could not locate the desktop's extraction regex in dashboard-token.ts"
    pattern = literal.group(1)
    # Render the JS regex literal as a Python one: strip delimiters, translate escapes.
    body = pattern[1:pattern.rindex("/")]
    py_pattern = _js_regex_to_python(body)

    match = re.search(py_pattern, served_index)
    assert match, (
        "the desktop's token regex does not match the served index.html; the server "
        f"writes {sorted(_writer_globals())}\n--- served head ---\n{served_index[:400]}"
    )
    assert match.group(1) == '"desktop-handshake-token"'


def test_spa_readers_agree_with_writer_globals(served_index):
    """Every ``window.__QUBIT_*__`` the SPA reads must be written by the server."""
    spa_files = [
        _WEB_SRC / "lib" / "api.ts",
        _WEB_SRC / "lib" / "profile-bootstrap.ts",
        _WEB_SRC / "lib" / "dashboard-flags.ts",
    ]
    read_names: set[str] = set()
    for path in spa_files:
        read_names |= set(_GLOBAL_REF.findall(path.read_text(encoding="utf-8")))

    assert read_names, "no SPA global reads discovered — the scraper is stale"
    written = _writer_globals()
    missing = read_names - written
    assert not missing, (
        f"SPA reads {sorted(missing)} but the server never writes them "
        f"(writes {sorted(written)})"
    )

    # And the served document actually carries each one.
    for name in read_names:
        assert f"window.{name}=" in served_index, f"{name} absent from served index.html"


def test_spa_does_not_read_legacy_hermes_globals():
    """The SPA's dashboard-global reads are QUBIT-branded; the legacy name is a half-rename."""
    offenders: list[str] = []
    for path in (_WEB_SRC / "lib").glob("*.ts"):
        if path.name.endswith(".test.ts"):
            continue
        text = path.read_text(encoding="utf-8")
        for name in ("SESSION_TOKEN", "AUTH_REQUIRED", "BASE_PATH", "INITIAL_PROFILE",
                     "DASHBOARD_PROFILE", "DASHBOARD_EMBEDDED_CHAT"):
            if f"window.__HERMES_{name}__" in text:
                offenders.append(f"{path.name}: __HERMES_{name}__")
    assert not offenders, f"SPA still reads legacy globals: {offenders}"


def test_theme_bootstrap_style_id_matches_the_client_contract():
    """The inlined theme ``<style>`` id is part of the served markup contract."""
    source = Path(_dashboard.__file__).read_text(encoding="utf-8")
    assert 'hermes-theme-bootstrap' not in source
    assert 'id="qubit-theme-bootstrap"' in source
