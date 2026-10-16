"""Cross-page SECURITY CONTRACT for every ``/plugin-apps/<name>/`` surface.

WHY THIS FILE EXISTS
--------------------
Serving a plugin app is generic: ``WebUIHandler._serve_plugin_app`` takes the
first path segment as the plugin directory name under
``resources.resource_path("frontend/qubit-app/plugins")``, applies a
``..``/confinement check, an extension ALLOWLIST, and a loopback-only session
token injection for ``.html`` (see ``voicebridge/webui.py`` ~2029-2169 and
``docs/OFFICE23_SPEC.md`` §2). That single code path is supposed to give every
office page the SAME guarantees — the point of this file is to PIN that claim
per-page rather than trust it:

* ``qubit-office`` sat in the tree first and is effectively the REFERENCE.
* ``qubit-office2`` (Agent-Quest concept) and ``qubit-office3`` (starnet design
  reimplementation) are added later. They MUST behave identically; a divergence
  here is a real regression, not a cosmetic one.

The third name is deliberately a CONTROL: it is parametrized exactly like the
other two, so a future reader cannot read a passing office/office2 pair as
"the contract holds" without office3 having been exercised too.

HARNESS LAYER
-------------
Handler-layer only — ``WebUIHandler.__new__`` with a fake ``server.app`` and a
captured ``_send``. ``_serve_plugin_app`` and ``_serve_app_html`` touch only
``self.app``, ``self.headers``, ``self.client_address`` and ``self._send``, so
this is faithful and needs no socket. (House a real HTTP client would normalize
``..`` out of the request line before it reaches the server, which is why the
handler layer is the layer that proves the server's own confinement; see
``tests/test_office_plugin_app.py`` for the same reasoning.)

METHOD FOR A MISSING PLUGIN DIR
-------------------------------
office2/office3 page files do not exist in every checkout (this branch adds
docs + this test, not the page dirs). The tests SKIP CLEANLY — with a reason
naming the missing path — instead of erroring at collection, so the file stays
green on a partial tree and becomes meaningful the moment the dirs land.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from voicebridge import webui as W  # noqa: E402

pytestmark = pytest.mark.unit

PREFIX = "/plugin-apps/"
PLUGINS = ["qubit-office", "qubit-office2", "qubit-office3"]
SAMPLE_HTML = b"<!doctype html><html><head></head><body>office</body></html>"
TOKEN = "s3cret-token"


# --------------------------------------------------------------------------
# Handler harness (no sockets)
# --------------------------------------------------------------------------

def _make_handler(*, path="", headers=None, peer="127.0.0.1", host="127.0.0.1",
                  token=TOKEN):
    """A bare ``WebUIHandler`` with ``_send`` captured into ``_captured``.

    ``host`` defaults to loopback because a real HTTP/1.1 request always carries
    a ``Host`` and ``_peer_is_loopback`` requires BOTH the peer and the Host to
    be loopback. A non-loopback caller is exercised by passing a non-loopback
    ``peer`` (and/or ``host``).
    """
    h = object.__new__(W.WebUIHandler)
    h.path = path
    h.headers = dict(headers or {})
    if host is not None and "Host" not in h.headers:
        h.headers["Host"] = host
    h.client_address = (peer, 51234)
    h._head_only = False

    class _FakeApp:
        api_token = token
        allowed_origins = ()

    h.server = type("S", (), {"app": _FakeApp()})()
    captured: dict = {}

    def _send(code, body, ctype="text/plain"):
        captured["code"] = code
        captured["body"] = body
        captured["ctype"] = ctype

    h._send = _send  # type: ignore[method-assign]
    h._captured = captured
    return h


@pytest.fixture()
def plugin_root(tmp_path, monkeypatch):
    """A hermetic ``frontend/qubit-app/plugins`` tree with all three apps.

    ``_serve_plugin_app`` resolves its serving root through
    ``resources.resource_path("frontend/qubit-app/plugins")``; redirecting that
    single resolution point makes the test independent of whether the checkout's
    real plugin dirs exist, and pins the ROOT the confinement check compares
    against. Each of the three names gets a byte-identical tree so a per-name
    result is genuinely comparable.
    """
    from voicebridge import resources

    root = tmp_path / "plugins"
    for name in PLUGINS:
        app = root / name
        (app / "bundle").mkdir(parents=True)
        (app / "index.html").write_bytes(SAMPLE_HTML)
        (app / "bundle" / "index-abc123.js").write_text("console.log(1)")
        (app / "style.css").write_text("body{}")
        (app / "server.py").write_bytes(b"SECRET = 1\n")
        (app / "data.bin").write_bytes(b"\x00\x01")
    real = resources.resource_path
    monkeypatch.setattr(
        resources, "resource_path",
        lambda name: root if name == "frontend/qubit-app/plugins" else real(name))
    return root


def _require_real_dir(name: str) -> Path:
    """The REAL plugin dir for ``name``, or a clean SKIP naming the path.

    Used by the "the shipped page dir actually exists" checks, which assert a
    property of the real checkout rather than the hermetic fixture.
    """
    from voicebridge import resources
    d = Path(resources.resource_path("frontend/qubit-app/plugins")) / name
    if not d.is_dir():
        pytest.skip(f"plugin dir not present in this worktree: {d}")
    return d


# --------------------------------------------------------------------------
# 1. A known file inside the plugin dir is served with the correct content type
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", PLUGINS)
def test_known_file_served_with_correct_content_type(plugin_root, name):
    """[handler] ``<name>/index.html`` serves 200 as ``text/html``.

    Content type is asserted EXACTLY (charset included), because a wrong
    content-type is how an HTML document becomes a script context or vice versa.
    """
    sub = f"{name}/index.html"
    h = _make_handler(path=PREFIX + sub)
    h._serve_plugin_app(sub)
    assert h._captured["code"] == 200, (name, h._captured)
    assert h._captured["ctype"] == "text/html; charset=utf-8", (name, h._captured)
    # The loopback path injects its <script> INSIDE <head>, so the sample is not
    # a contiguous run — assert the document survives around the injection
    # (everything before <head> and everything from </head> onward is intact)
    # rather than byte-equality, which would contradict §4 below.
    body = h._captured["body"]
    assert body.startswith(b"<!doctype html><html><head>"), (name, h._captured)
    assert body.endswith(b"</head><body>office</body></html>"), (name, h._captured)


@pytest.mark.parametrize("name", PLUGINS)
@pytest.mark.parametrize("rel,ctype", [
    ("bundle/index-abc123.js", "application/javascript"),
    ("style.css", "text/css"),
])
def test_known_asset_served_with_correct_content_type(plugin_root, name, rel,
                                                      ctype):
    """[handler] Every page's bundled assets serve with their declared type."""
    sub = f"{name}/{rel}"
    h = _make_handler(path=PREFIX + sub)
    h._serve_plugin_app(sub)
    assert h._captured["code"] == 200, (name, rel, h._captured)
    assert h._captured["ctype"] == ctype, (name, rel, h._captured)


# --------------------------------------------------------------------------
# 2. A ``..`` traversal attempt is refused (404)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", PLUGINS)
@pytest.mark.parametrize("sub", [
    "{name}/../../../etc/passwd",
    "{name}/bundle/../../../../etc/passwd",
    "{name}/bundle/../index.html",           # resolves BACK INSIDE the root
    "{name}/..%2f..%2f..%2fetc%2fpasswd",
])
def test_dotdot_traversal_is_refused(plugin_root, name, sub):
    """[handler] A ``..`` in the path 404s for every page prefix.

    ``<name>/bundle/../index.html`` resolves to ``<name>/index.html`` — a REAL
    file inside the plugin root — so only the explicit ``".." in parts`` check
    refuses it; the resolved-path confinement re-check would serve it. Pinned so
    that check cannot be dropped for any of the three names.
    """
    sub = sub.format(name=name)
    h = _make_handler(path=PREFIX + sub)
    h._serve_plugin_app(sub)
    assert h._captured["code"] == 404, (name, sub, h._captured)
    # And no traversal variant ever leaks file content.
    assert b"root:" not in (h._captured.get("body") or b""), (name, sub)


# --------------------------------------------------------------------------
# 3. A disallowed extension is refused (404)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", PLUGINS)
@pytest.mark.parametrize("rel", ["server.py", "data.bin", "noext"])
def test_disallowed_extension_is_refused(plugin_root, name, rel):
    """[handler] An unlisted suffix 404s — never served with a guessed type.

    The route uses an ALLOWLIST, so the failure mode this pins is an unknown
    suffix being handed a guessed ``application/octet-stream`` and streamed.
    """
    sub = f"{name}/{rel}"
    h = _make_handler(path=PREFIX + sub)
    h._serve_plugin_app(sub)
    assert h._captured["code"] == 404, (name, rel, h._captured)
    assert h._captured.get("ctype") != "application/octet-stream", (name, rel)


# --------------------------------------------------------------------------
# 4. The session token is injected for LOOPBACK only
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", PLUGINS)
def test_html_from_loopback_peer_gets_session_token_injected(plugin_root, name):
    """[handler] An .html request from a loopback peer carries the token.

    Asserted on the ACTUAL injection the page needs:
    ``window.__QUBIT_SESSION_TOKEN__``. The app's ``fetch('/api/access')`` has no
    credential without it.
    """
    sub = f"{name}/index.html"
    h = _make_handler(path=PREFIX + sub, peer="127.0.0.1",
                      host="127.0.0.1:8890")
    h._serve_plugin_app(sub)
    assert h._captured["code"] == 200, (name, h._captured)
    body = h._captured["body"].decode("utf-8")
    assert "window.__QUBIT_SESSION_TOKEN__" in body, (name, body)


@pytest.mark.parametrize("name", PLUGINS)
@pytest.mark.parametrize("peer,host", [
    ("192.168.1.10", "192.168.1.10:8890"),   # off-box peer
    ("10.0.0.5", "127.0.0.1:8890"),           # off-box peer, spoofed Host
    ("127.0.0.1", "evil.example.com:8890"),   # loopback peer, non-loopback Host
])
def test_html_from_non_loopback_peer_gets_no_token(plugin_root, name, peer, host):
    """[handler] The SAME .html from a non-loopback caller has NO token.

    The page is still served (a static asset, the office stays viewable), but
    WITHOUT the credential — the measured-token-leak guard. Any of the three
    pages handing a remote peer the token is a real security regression, which
    is why this is asserted per-page and per-bypass-shape.
    """
    sub = f"{name}/index.html"
    h = _make_handler(path=PREFIX + sub, peer=peer, host=host)
    h._serve_plugin_app(sub)
    assert h._captured["code"] == 200, (name, peer, host, h._captured)
    body = h._captured["body"].decode("utf-8")
    assert "window.__QUBIT_SESSION_TOKEN__" not in body, (name, peer, host, body)


# --------------------------------------------------------------------------
# 5. The shipped page dir exists (skips cleanly when it does not yet)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", PLUGINS)
def test_real_plugin_dir_exists_and_ships_what_its_manifest_declares(name):
    """The REAL checkout ships ``<name>/`` with the entry its manifest declares.

    WHY THIS DOES NOT ASSERT AN .html. An earlier version of this test required
    every plugin dir to contain an ``.html``, and failed on office2/office3. That
    rule was generalised from ``qubit-office``, which is the ABNORMAL case: it
    embeds a vendored standalone app (``habblaud/index.html``) inside an iframe.
    The other TEN plugin dirs in this tree (qubit-approvals, qubit-boards,
    qubit-brand, qubit-devices, qubit-gateway, qubit-memory, qubit-models,
    qubit-ops, qubit-reports, qubit-runtime) ship manifest + entry JS and NO
    html; the SPA renders them through ``PluginPage``/``__QUBIT_PLUGINS__``. So
    ``.html`` is the exception, not the contract, and asserting it would forbid
    the normal plugin shape.

    What is actually universal: the dir exists and the file its manifest's
    ``entry`` names is present. For ``qubit-office`` that entry is the game
    bundle; for office2/office3 it is the panel script. Skips CLEANLY — naming
    the missing path — when a page dir has not landed yet.
    """
    d = _require_real_dir(name)
    mf = d / "manifest.json"
    assert mf.is_file(), f"no manifest.json under {d}"
    manifest = json.loads(mf.read_text(encoding="utf-8"))
    entry = manifest.get("entry")
    assert entry, f"{name}/manifest.json declares no entry"
    assert (d / entry).is_file(), f"{name}: manifest entry {entry!r} is missing on disk"

