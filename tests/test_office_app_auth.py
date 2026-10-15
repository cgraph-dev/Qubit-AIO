"""The office iframe's authentication: the bridge must carry the session token.

WHY THIS FILE EXISTS
--------------------
The Qubit Office page renders the vendored Habblaud client inside a same-origin
iframe. That client was written against a server with NO auth: it fetches
``/api/access`` with ``credentials: 'same-origin'`` and no credential of any
kind. Qubit gates every office route behind the session token, so the request
came back **401**, and the client — whose own contract is "a 401 means go to
/login" — ran ``location.assign('/login')``. The iframe landed on ``/sessions``,
so ``/qubit-office`` displayed the Sessions dashboard while every text assertion
still called the page "ready". The blank-office bug was an auth bug.

Two layers, both pinned here because the failure needs both to be right:

1. **The bridge attaches the token** (``api-bridge.ts``). It is the one place
   that knows both the rewrite (``/api/<x>`` -> ``/agent/api/office/habblaud/<x>``)
   and the host session. A request that already carries a token — or a token on
   the URL — keeps it; the bridge only fills the gap.
2. **The app document is served WITH the token** (``webui._serve_plugin_app`` ->
   ``_serve_app_html``). The host shell injects ``window.__QUBIT_SESSION_TOKEN__``
   into the DASHBOARD document, never into the iframe's own document, so without
   the injection the bridge has nothing to read. And the gate itself
   (``_office_app_caller_ok``) must accept the credential the browser actually
   sends: a header token, the loopback+same-origin+own-app Referer triple for the
   bundle's first probe (which runs before the bridge installs), and nothing else.

METHOD: the TS half is compiled with the project's own ``tsc`` and driven under
``node`` (the established ``_office_toolchain``/``_office_node`` pattern), so the
assertion is on BEHAVIOUR — does the outgoing request carry the header — not on
source text. The Python half uses the handler-layer harness from
``tests/test_office_stream_auth.py``.

Hermetic: tmp dirs only; no network, no live service.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _office_node import run_node  # noqa: E402
from _office_toolchain import (  # noqa: E402
    ensure_tsc, node_path, require_toolchain_or_fail)

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP_DIR = REPO_ROOT / "frontend/qubit-app/plugins/qubit-office/game/app"
NODE = node_path()
TSC = ensure_tsc()

pytestmark = pytest.mark.unit

#: Loud, not a silent skip — a vanished compiler is a broken environment.
require_toolchain_or_fail()

from voicebridge import webui as W  # noqa: E402


# --------------------------------------------------------------------------
# 1. The bridge attaches the session token (compiled TS, driven under node)
# --------------------------------------------------------------------------
def _run_bridge(tmp_path: Path, js: str) -> object:
    """Compile ``api-bridge.ts`` with tsc and run a driver against it.

    The driver is given the compiled module and the shared ``Request``/
    ``Response`` globals node 22 provides, so ``installOfficeApiBridge`` runs for
    real: the fake window's ``fetch`` records the outgoing request and the test
    reads ITS headers — the same object the server would see.
    """
    staged = tmp_path / "src"
    staged.mkdir(exist_ok=True)
    shutil.copy(APP_DIR / "api-bridge.ts", staged / "api-bridge.ts")
    subprocess.run(
        [NODE, str(TSC), "--module", "commonjs", "--target", "es2020",
         "--moduleResolution", "node", "--ignoreDeprecations", "6.0",
         "--outDir", str(tmp_path / "js"), "--skipLibCheck",
         "--noEmitOnError", "false", "--lib", "es2020,dom",
         str(staged / "api-bridge.ts")],
        capture_output=True, text=True, timeout=240, cwd=str(staged))
    compiled = tmp_path / "js" / "api-bridge.js"
    if not compiled.is_file():
        pytest.fail("tsc could not compile api-bridge.ts for the node driver; "
                    "the token attachment is unchecked without this")
    program = (
        'const B = require(%s);\n'
        '(async () => {\n'
        'const out = await (async () => { %s })();\n'
        'process.stdout.write(JSON.stringify(out));\n'
        '})();\n'
    ) % (json.dumps(str(compiled)), js)
    proc = run_node(program, timeout=90)
    assert proc.returncode == 0, (
        f"node exited {proc.returncode}\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}")
    return json.loads(proc.stdout)


#: Records every outgoing request and answers 200. Shared by the drivers.
_FAKE_WINDOW = """
const makeWindow = (token, parentToken) => {
  const seen = [];
  const win = {
    location: { href: "http://localhost:8890/plugin-apps/qubit-office/habblaud/",
                origin: "http://localhost:8890" },
    fetch: async (input, init) => {
      const req = new Request(input, init);
      seen.push({ url: req.url, method: req.method,
                  token: req.headers.get("X-Qubit-Session-Token") });
      return new Response("{}", { status: 200 });
    },
  };
  if (token !== undefined) win.__QUBIT_SESSION_TOKEN__ = token;
  win.parent = parentToken === undefined ? win : { __QUBIT_SESSION_TOKEN__: parentToken };
  return { win, seen };
};
"""


def test_bridge_attaches_the_session_token_to_a_rewritten_request(tmp_path):
    """THE regression: without the token the rewritten request 401s and the
    iframe navigates to /sessions. The bridge must add the header."""
    got = _run_bridge(tmp_path, _FAKE_WINDOW + """
      const { win, seen } = makeWindow("sess-token-abc");
      const restore = B.installOfficeApiBridge(win);
      await win.fetch("http://localhost:8890/api/access");
      return seen;
    """)
    assert len(got) == 1, f"expected one rewritten request, saw {got}"
    assert got[0]["url"].endswith("/agent/api/office/habblaud/access"), got[0]
    assert got[0]["token"] == "sess-token-abc", (
        "the bridge did NOT attach X-Qubit-Session-Token — the office route "
        f"answers 401 and the iframe lands on /sessions: {got[0]}")


def test_bridge_reads_the_parent_document_token_when_the_frame_has_none(tmp_path):
    """The host shell injects the token into the PARENT document; the app is a
    same-origin child. The bridge must still find it."""
    got = _run_bridge(tmp_path, _FAKE_WINDOW + """
      const { win, seen } = makeWindow(undefined, "parent-token-xyz");
      const restore = B.installOfficeApiBridge(win);
      await win.fetch("http://localhost:8890/api/access");
      return seen;
    """)
    assert got[0]["token"] == "parent-token-xyz", (
        f"the bridge did not fall back to the parent document's token: {got[0]}")


def test_bridge_preserves_a_token_already_on_the_request(tmp_path):
    """A caller that authenticated itself keeps its own credential — the bridge
    fills a gap, it does not overwrite."""
    got = _run_bridge(tmp_path, _FAKE_WINDOW + """
      const { win, seen } = makeWindow("doc-token");
      const restore = B.installOfficeApiBridge(win);
      await win.fetch("http://localhost:8890/api/access", { headers: { "X-Qubit-Session-Token": "caller-token" } });
      return seen;
    """)
    assert got[0]["token"] == "caller-token", (
        "the bridge clobbered a credential the caller had already set: "
        f"{got[0]}")


def test_bridge_preserves_a_token_already_on_the_url(tmp_path):
    """The rewrite must not strip the caller's query string (an existing
    ``?token=`` included)."""
    got = _run_bridge(tmp_path, _FAKE_WINDOW + """
      const { win, seen } = makeWindow("doc-token");
      const restore = B.installOfficeApiBridge(win);
      await win.fetch("http://localhost:8890/api/access?token=url-token");
      return seen;
    """)
    assert "token=url-token" in got[0]["url"], (
        f"the rewrite dropped the caller's query params: {got[0]['url']}")


# --------------------------------------------------------------------------
# 2. The app document is served WITH the token (server side)
# --------------------------------------------------------------------------
def _app_handler(*, token="secret", peer="127.0.0.1"):
    """A handler whose ``_send`` is captured, serving a real HTML file."""
    from voicebridge import resources

    h = object.__new__(W.WebUIHandler)
    h.path = "/plugin-apps/qubit-office/habblaud/index.html"
    h.headers = {}
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
    return h, resources


@pytest.fixture()
def plugin_root(tmp_path, monkeypatch):
    """A hermetic plugin tree whose app document is a real file."""
    from voicebridge import resources

    root = tmp_path / "plugins"
    app = root / "qubit-office" / "habblaud"
    app.mkdir(parents=True)
    (app / "index.html").write_text(
        "<!doctype html><html><head><title>App</title></head>"
        "<body><canvas id=\"world\"></canvas></body></html>")
    (app / "bundle").mkdir()
    (app / "bundle" / "main.js").write_text("console.log(1)")

    real = resources.resource_path
    monkeypatch.setattr(
        resources, "resource_path",
        lambda name: root if name == "frontend/qubit-app/plugins" else real(name))
    return root


def test_app_html_carries_the_session_token_the_bridge_reads(plugin_root):
    """Without this injection the bridge has NO token to attach, so the 401 is
    unavoidable however correct the bridge is."""
    h, _ = _app_handler(token="tok-123")
    h._serve_plugin_app("qubit-office/habblaud/index.html")
    assert h._captured["code"] == 200, h._captured
    body = h._captured["body"].decode("utf-8")
    assert "__QUBIT_SESSION_TOKEN__" in body, (
        "the served office app document does not inject the session token, so "
        "the bridge has nothing to attach and /api/access still 401s")
    assert "tok-123" in body, "the injected token is not the configured token"


def test_app_html_injection_is_html_only_not_every_asset(plugin_root):
    """The token must not leak into JS/CSS bodies served from the same route."""
    h, _ = _app_handler(token="tok-123")
    h._serve_plugin_app("qubit-office/habblaud/bundle/main.js")
    body = h._captured["body"].decode("utf-8")
    assert "__QUBIT_SESSION_TOKEN__" not in body, (
        "the token was injected into a non-HTML asset — it belongs only in the "
        "document's own <head>")


# --------------------------------------------------------------------------
# 3. The gate accepts the credential the browser actually sends
# --------------------------------------------------------------------------
def _gate_handler(*, peer="127.0.0.1", host="127.0.0.1:8890", referer=None,
                  path="/api/access", token="secret", header_token=None):
    h = object.__new__(W.WebUIHandler)
    h.path = path
    h.client_address = (peer, 1234)
    h.headers = {"Host": host}  # type: ignore[assignment]
    if referer:
        h.headers["Referer"] = referer
    if header_token:
        h.headers["X-Qubit-Session-Token"] = header_token
    h.server = type("S", (), {"app": type("A", (), {"api_token": token})()})()
    h._api_token_ok = lambda: False
    h._qubit_token_ok = (
        (lambda: header_token == token) if header_token else (lambda: False))
    return h


OFFICE_REFERER = "http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/"


def test_gate_accepts_the_loopback_own_app_probe_with_a_token_configured():
    """THE gate fix: the bundle's first ``/api/access`` runs before the bridge
    installs, so it can carry no header. On loopback, from this server's own
    Host, with the app's own Referer, it must be allowed as a blind probe."""
    h = _gate_handler(referer=OFFICE_REFERER, token="secret")
    assert h._office_app_caller_ok(blind_probe=True) is True, (
        "the office app's own pre-bridge probe is refused even though the peer "
        "is loopback, the Host is this server, and the Referer is the app — "
        "the exact triple the predicate documents")


def test_gate_keeps_regular_app_reads_strict_when_a_token_is_configured():
    """Regular app reads remain strict; the stream handler opts into its own-app path."""
    h = _gate_handler(path="/api/stream", referer=OFFICE_REFERER, token="secret")
    assert h._office_app_caller_ok() is False, (
        "the stream rode the blind-probe exemption; it must keep requiring a "
        "credential when one is configured")


def test_gate_still_refuses_a_non_loopback_peer():
    """The ceiling that must never move: off-loopback callers get nothing."""
    h = _gate_handler(peer="10.0.0.5", referer=OFFICE_REFERER, token="secret")
    assert h._office_app_caller_ok() is False, (
        "a non-loopback peer passed the office exemption — the network exposure "
        "was widened")


def test_stream_handler_uses_the_restricted_app_referer_path():
    """Only a valid query token or configured header authorizes EventSource."""
    h = _gate_handler(path="/api/stream", referer=OFFICE_REFERER, token="secret")
    assert h._office_app_caller_ok() is False
    h.path = "/api/stream?token=secret"
    assert h._office_app_caller_ok() is True


def test_gate_still_refuses_a_foreign_origin_host():
    h = _gate_handler(host="evil.example.com", referer=OFFICE_REFERER)
    assert h._office_app_caller_ok() is False


def test_gate_still_refuses_a_foreign_referer():
    h = _gate_handler(referer="http://127.0.0.1:8890/somewhere/else/")
    assert h._office_app_caller_ok() is False, (
        "an arbitrary local page rode the office exemption")


def test_gate_still_refuses_a_supplied_but_wrong_query_token():
    """A bad token must not fall through to the exemption."""
    h = _gate_handler(path="/api/stream?token=wrong", referer=OFFICE_REFERER)
    assert h._office_app_caller_ok() is False


def test_gate_accepts_a_valid_header_token_off_the_exemption_path():
    h = _gate_handler(header_token="secret", referer=None, peer="127.0.0.1")
    assert h._office_app_caller_ok() is True


# --------------------------------------------------------------------------
# 4. The compat route the bridge rewrites to actually exists
# --------------------------------------------------------------------------
def test_bridge_target_route_is_registered():
    """The rewritten path must resolve to a real handler, not the bare
    ``no compat route`` 404."""
    from voicebridge import qubit_compat as HC

    route, _ = HC.match("GET", "/api/office/habblaud/access")
    assert route is not None, (
        "the bridge rewrites /api/access to /api/office/habblaud/access, which "
        "has no registered compat route — the office 404s even with a token")
    assert route["handler"] == "h_office_habblaud_access"
