"""The Qubit Office page DEPLOYMENT CONTRACT, tested against the SERVED truth.

WHY THIS FILE EXISTS
--------------------
The office page is assembled from pieces that live in four different places and
are stitched together at *serve* time, not at *build* time. Every one of the
failure modes below has bitten this project, and every one of them is SILENT:
the page still returns 200, the tab still opens, and the office is simply
absent or wrong. A rebuild or a restart can break any of them with no tracked
diff, because the piece that broke was an untracked artifact.

The four known silent-breakage modes this file pins:

1. **The entry is declared in THREE places** — the plugin's own manifest, the
   SERVED ``fleet.json``, and the SPA's ``public/fleet.json`` — and the loader
   merges them with the API list winning. Editing the wrong copy leaves the page
   loading the OLD entry while the disk says otherwise. (We test the SERVED
   list as the authority and require the other sites to AGREE with it.)

2. **``frontend/qubit-dist/`` is UNTRACKED** and a dashboard rebuild
   (``scripts/build_dashboard.sh``, which runs ``--emptyOutDir``) can WIPE it,
   breaking the tab with NO tracked diff.

3. **A full-height page needs its path in ``FULL_HEIGHT_ROUTE_PATHS``**, which
   lives in a BUILT artifact (``frontend/qubit-web/src/lib/chat-routes.ts``), so
   a source edit does nothing until the dashboard is rebuilt. And the predicate
   must NOT be merged into ``CHAT_ROUTE_PATHS``: they answer different questions
   and merging them silently restyles the game.

4. **Early sampling reports a false negative.** ``render_office_screenshot.py``
   sampling too early reports "Connecting... / 0 non-background pixels" — the
   office takes ~0.3 s to connect and the iframe longer to attach. So the
   browser checks here WAIT for the iframe to attach and the canvas to paint
   before asserting, instead of taking one reading.

WHAT IS TESTED
--------------
The SERVED truth, over HTTP against the running dashboard, not the filesystem.
Where a check needs the browser it drives headless Chrome over CDP. When the
dashboard is genuinely not running the HTTP/browser checks SKIP WITH A REASON
(never a silent pass — a green run that proves nothing is worse than a red one).

Marks
-----
``contract``  — the served-truth HTTP/route checks (need the dashboard up).
``live``      — the headless-Chrome browser checks (need Chrome), opt-in.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

import pytest

REPO = Path(__file__).resolve().parents[1]

#: The running dashboard. Overridable so a subpath deployment or a second
#: instance can be probed without editing the file.
BASE_URL = os.environ.get("QUBIT_OFFICE_BASE_URL", "http://127.0.0.1:8890")

#: Where the plugin's own source manifest lives (declaration site 1).
APP_MANIFEST = REPO / "frontend/qubit-app/plugins/qubit-office/manifest.json"
#: The SERVED fleet list (declaration site 2) — the authority the loader uses.
DIST_FLEET = REPO / "frontend/qubit-dist/plugins/fleet.json"
#: The SPA's bundled fleet list (declaration site 3).
PUBLIC_FLEET = REPO / "frontend/qubit-web/public/plugins/fleet.json"
#: The route predicates live here, and this is a BUILT artifact.
CHAT_ROUTES_TS = REPO / "frontend/qubit-web/src/lib/chat-routes.ts"
#: The office page module whose iframe src is the contract.
OFFICE_APP_SRC = REPO / "frontend/qubit-app/plugins/qubit-office/game/app/office-app.ts"

OFFICE_PLUGIN = "qubit-office"
CHROME = os.environ.get("QUBIT_CHROME", "/usr/bin/google-chrome")

#: The office's own painted-pixel floor. The canvas is ~1300x780 here; the live
#: office paints well over a million non-background pixels, so 100k is a floor
#: that catches "the canvas is blank / never connected" without being flaky
#: against a sparse floor plan.
MIN_PAINTED_PIXELS = 100_000
#: ``#world`` must be wider than this or the iframe never sized (the classic
#: zero-width iframe bug: it attaches but never lays out).
MIN_CANVAS_WIDTH = 200


# --------------------------------------------------------------------------- #
# Serving helpers
# --------------------------------------------------------------------------- #
def _token() -> str | None:
    """The dashboard session token, read the same way ``webui`` injects it."""
    try:
        import yaml  # noqa: PLC0415
    except Exception:
        return os.environ.get("QUBIT_SESSION_TOKEN") or None
    cfg = Path("~/.config/qubit-voicebridge/voice.yaml").expanduser()
    if not cfg.is_file():
        return os.environ.get("QUBIT_SESSION_TOKEN") or None
    try:
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        return (data.get("webui") or {}).get("token")
    except Exception:
        return os.environ.get("QUBIT_SESSION_TOKEN") or None


def dashboard_up() -> bool:
    """True when the dashboard answers on ``BASE_URL``.

    A cheap probe against a route that needs no token, so "not running" is
    distinguishable from "running but unauthenticated".
    """
    try:
        with urlrequest.urlopen(f"{BASE_URL}/plugins/fleet.json", timeout=3) as r:
            return r.status == 200
    except (urlerror.URLError, OSError, ValueError):
        return False


requires_dashboard = pytest.mark.skipif(
    not dashboard_up(),
    reason=(
        f"the dashboard is not answering at {BASE_URL} — the served-truth "
        "office deployment contract cannot be checked; start the bridge "
        "(or set QUBIT_OFFICE_BASE_URL) and re-run, do NOT read this skip as "
        "coverage."
    ),
)

requires_chrome = pytest.mark.skipif(
    not (Path(CHROME).exists() or shutil.which(os.path.basename(CHROME))),
    reason=(
        f"headless Chrome not found at {CHROME!r} (set QUBIT_CHROME) — the "
        "iframe/canvas/stream contract cannot be exercised; this is a HARNESS "
        "gap, not a passing check."
    ),
)


def http(url: str, *, method: str = "GET", headers: dict | None = None,
         timeout: float = 10.0) -> tuple[int, dict, bytes]:
    """Return ``(status, headers, body)`` for a request, never raising on 4xx/5xx."""
    req = urlrequest.Request(url, method=method, headers=headers or {})
    try:
        with urlrequest.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urlerror.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def served_fleet() -> list[dict]:
    """The SERVED fleet list — the authority the plugin loader merges."""
    status, _h, body = http(f"{BASE_URL}/plugins/fleet.json")
    assert status == 200, f"GET /plugins/fleet.json -> {status}"
    data = json.loads(body)
    assert isinstance(data, list), "the served fleet manifest is not a JSON array"
    return data


def served_office_entry() -> dict:
    """The ``qubit-office`` row from the SERVED fleet list."""
    rows = [r for r in served_fleet() if r.get("name") == OFFICE_PLUGIN]
    assert rows, "the SERVED fleet.json does not list qubit-office at all"
    return rows[0]


def _read_json(path: Path) -> list[dict]:
    assert path.is_file(), f"declaration site missing on disk: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def _pick(rows: list[dict]) -> dict:
    picked = [r for r in rows if r.get("name") == OFFICE_PLUGIN]
    assert picked, f"declaration site does not list {OFFICE_PLUGIN}: {rows!r}"
    return picked[0]


# --------------------------------------------------------------------------- #
# 1. ENTRY AGREEMENT — the three-way declaration
# --------------------------------------------------------------------------- #
@pytest.mark.contract
@requires_dashboard
def test_the_three_entry_declaration_sites_agree_with_the_served_truth():
    """The entry is named in three places and the loader merges them; all must agree.

    The failure this catches: editing one copy (usually the tracked app
    manifest) while the SERVED copy still names the OLD entry. The disk then
    says one thing and the page loads another, with no error anywhere.
    """
    served = served_office_entry()
    served_entry = served.get("entry")
    assert served_entry, f"the SERVED qubit-office row declares no entry: {served!r}"

    # The two fleet lists are arrays of rows; compare their office row.
    #
    # ``frontend/qubit-dist/`` is GITIGNORED, so a fresh ``git worktree`` (the
    # workflow this repo mandates) does not have it on disk. Reading it as a hard
    # requirement made every worktree run RED for a file that is legitimately
    # absent from the checkout. The SERVED fleet truth (read over HTTP above) is
    # already the authority for that site, so an absent dist copy is SKIPPED with
    # a note naming the reason rather than asserted. The SPA copy and the app
    # manifest ARE tracked and stay hard requirements.
    sites = [("frontend/qubit-web/public/plugins/fleet.json", PUBLIC_FLEET)]
    if DIST_FLEET.is_file():
        sites.append(("frontend/qubit-dist/plugins/fleet.json", DIST_FLEET))
    else:
        print(
            f"note: {DIST_FLEET} is absent (gitignored — not present in a fresh "
            "worktree); checking the SERVED fleet truth and the SPA copy instead"
        )
    for label, path in sites:
        row = _pick(_read_json(path))
        assert row.get("entry") == served_entry, (
            f"{label} names entry {row.get('entry')!r} but the SERVED fleet "
            f"declares {served_entry!r}; the loader will serve a different file "
            "than this site claims"
        )

    # The app manifest is a single object, not a fleet row.
    app = json.loads(APP_MANIFEST.read_text(encoding="utf-8"))
    assert app.get("entry") == served_entry, (
        f"the plugin's own manifest names {app.get('entry')!r} but the SERVED "
        f"fleet declares {served_entry!r}"
    )


@pytest.mark.contract
@requires_dashboard
def test_the_named_entry_is_actually_served_with_a_js_content_type():
    """A declaration is worthless if the named artifact 404s.

    The loader builds ``assetRoot + "/" + manifest.entry``. ``assetBase`` is
    absent today, so the root is ``/dashboard-plugins/qubit-office``. A 404 here
    is a blank tab with no console error reported by the loader itself.
    """
    row = served_office_entry()
    base = row.get("assetBase") or f"/dashboard-plugins/{OFFICE_PLUGIN}"
    url = f"{BASE_URL}{base}/{row['entry']}"
    status, headers, body = http(url)
    assert status == 200, f"the declared entry {url} -> {status} (expected 200)"
    ctype = (headers.get("Content-Type") or headers.get("content-type") or "")
    assert "javascript" in ctype or "ecmascript" in ctype, (
        f"the entry {url} served Content-Type {ctype!r}; a browser refuses to "
        "execute a module without a JS content type"
    )
    assert len(body) > 0, f"the entry {url} served an empty body"


# --------------------------------------------------------------------------- #
# 2. ASSET ROUTES — both known-serving routes
# --------------------------------------------------------------------------- #
@pytest.mark.contract
@requires_dashboard
def test_the_entry_and_an_image_are_served_on_their_respective_routes():
    """Both known-serving routes must work, because they serve DIFFERENT roots.

    * ``/dashboard-plugins/qubit-office/<entry>`` — the plugin's code, served
      from the app source (``frontend/qubit-app/...``).
    * ``/plugins/qubit-office/<asset>`` — the plugin's assets (sprites, the
      asset MANIFEST), served from the SPA's ``public/`` tree.

    An image under the WRONG root 404s with NO console error — the office
    renders with missing sprites and nobody sees a failure. So prove BOTH: the
    JS route answers JS, and an image route answers a real image.
    """
    row = served_office_entry()

    # (a) code route
    js_url = f"{BASE_URL}/dashboard-plugins/{OFFICE_PLUGIN}/{row['entry']}"
    js_status, js_h, js_body = http(js_url)
    assert js_status == 200, f"code route {js_url} -> {js_status}"
    assert "javascript" in (js_h.get("Content-Type") or "").lower(), (
        f"code route {js_url} Content-Type {(js_h.get('Content-Type'))!r}"
    )
    assert b"plugin-apps/qubit-office/habblaud" in js_body, (
        "the served entry does not reference the vendored Habblaud app path; "
        "this is the OLD bundle (or a stub), not the promoted entry"
    )

    # (b) image route — pick a real sprite from the asset manifest so the test
    # does not hardcode a filename that vendor regeneration may rename. The
    # manifest's own `asset_base_url` is the authority for where its files are
    # reachable; its `files[].path` are relative to that base's `assets/` dir.
    manifest_url = f"{BASE_URL}/plugins/{OFFICE_PLUGIN}/assets/MANIFEST.json"
    m_status, _mh, m_body = http(manifest_url)
    assert m_status == 200, (
        f"asset route {manifest_url} -> {m_status}; images served under "
        f"/plugins/{OFFICE_PLUGIN}/... are broken (they 404 with no console error)"
    )
    manifest = json.loads(m_body)
    sprite_url = _first_sprite_url(manifest)
    assert sprite_url, "the asset MANIFEST declares no sprite file to probe"
    img_status, img_h, img_body = http(sprite_url)
    assert img_status == 200, (
        f"image route {sprite_url} -> {img_status}; the office will render with "
        "missing sprites and NO console error"
    )
    assert "image/" in (img_h.get("Content-Type") or "").lower(), (
        f"image route {sprite_url} Content-Type {(img_h.get('Content-Type'))!r}"
    )
    assert img_body[:8] == b"\x89PNG\r\n\x1a\n" or img_body[:3] == b"\xff\xd8\xff", (
        f"image route {sprite_url} did not return a real encoded image"
    )


def _first_sprite_url(manifest: object, base_url: str = BASE_URL) -> str | None:
    """Build the URL of one real sprite file declared by the asset manifest.

    The manifest is ``qubit-office-assets/1``: ``asset_base_url`` is where its
    tree is reachable (``/plugins/qubit-office``) and ``files[].path`` are
    relative to that base's ``assets/`` directory. Its exact nesting has changed
    before, so walk it for the first ``.png`` file path rather than assuming a
    key; return the fully-qualified URL so a 200/404 is unambiguous.
    """
    asset_base = base_url
    found: list[str] = []

    def walk(node: object) -> None:
        nonlocal asset_base
        if len(found) >= 1:
            return
        if isinstance(node, str):
            if node.endswith(".png") and "/" in node:
                found.append(node)
        elif isinstance(node, dict):
            if isinstance(node.get("asset_base_url"), str):
                asset_base = base_url + node["asset_base_url"]
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(manifest)
    if not found:
        return None
    return f"{asset_base}/assets/{found[0].lstrip('/')}"


# --------------------------------------------------------------------------- #
# 3. THE ROUTE PREDICATE — full-height, NOT chat
# --------------------------------------------------------------------------- #
@pytest.mark.contract
def test_the_office_route_is_full_height_and_not_a_chat_route():
    """``/qubit-office`` must be in ``FULL_HEIGHT_ROUTE_PATHS`` and out of ``CHAT_ROUTE_PATHS``.

    They answer different questions (see the file's own comment): full-height
    only controls the layout chain in ``App.tsx``; chat routes own the terminal
    surface. A page with a fixed footer (the office's ``height: 100%`` iframe)
    that is NOT full-height expands to its content height — the documented
    104,305px bug. Merging the two predicates silently restyles the game.
    """
    text = CHAT_ROUTES_TS.read_text(encoding="utf-8")

    full = re.search(r"FULL_HEIGHT_ROUTE_PATHS\s*=\s*\[([^\]]*)\]", text, re.S)
    assert full, "FULL_HEIGHT_ROUTE_PATHS is not defined as this contract expects"
    assert '"/qubit-office"' in full.group(1), (
        "/qubit-office is not in FULL_HEIGHT_ROUTE_PATHS; the office's "
        "height:100% iframe resolves against an auto-height ancestor and "
        "expands to its content instead of filling the viewport"
    )

    chat = re.search(r"CHAT_ROUTE_PATHS\s*=\s*\[([^\]]*)\]", text, re.S)
    assert chat, "CHAT_ROUTE_PATHS is not defined as this contract expects"
    assert "/qubit-office" not in chat.group(1), (
        "/qubit-office was added to CHAT_ROUTE_PATHS — full-height and chat "
        "parse different questions; merging them restyles the office as a chat "
        "surface"
    )


@pytest.mark.contract
@requires_dashboard
def test_the_SERVED_bundle_still_carries_the_office_in_the_full_height_list():
    """The predicate is a BUILT artifact — assert the SERVED bytes, not the source.

    A source edit to ``chat-routes.ts`` does nothing until the dashboard is
    rebuilt. The source test above can be green while the served bundle still
    omits ``/qubit-office``. This reads the actual served index bundle and
    requires the office to be in the full-height array literal.
    """
    # The served index HTML names the built entry chunk.
    html_status, _h, html = http(f"{BASE_URL}/qubit-office")
    assert html_status == 200, f"GET /qubit-office -> {html_status}"
    entry = re.search(rb'src="(/assets/[^"]+\.js)"', html)
    assert entry, "the served /qubit-office HTML names no entry chunk"
    chunk_url = BASE_URL + entry.group(1).decode()

    status, headers, body = http(chunk_url)
    assert status == 200, f"the served entry chunk {chunk_url} -> {status}"
    assert "javascript" in (headers.get("Content-Type") or "").lower()
    text = body.decode("utf-8", "replace")

    # The minifier emits the full-height array as the concatenation of the chat
    # list plus the literal full-height routes:
    #   `var jt=[...Dt,`/webchat`,`/qubit-bot`,`/qubit-office`,`/qubit-office2`,`/qubit-office3`]`
    # and the chat list is built from VARIABLES (`Tt=/chat`,`Et=/qubit-chat`),
    # so pin the full-height literal and require `/qubit-office` to be one of
    # its members. It is NOT required to be the LAST member: the page family
    # grows (`/qubit-office2`, `/qubit-office3` are appended after it), so a
    # "last member" assertion goes red for a legitimate addition and reports a
    # deployment break that does not exist.
    m = re.search(
        r"\[\.\.\.[A-Za-z_$][\w$]*,\s*`/webchat`\s*,\s*`/qubit-bot`\s*,"
        r"([^\]]*)\]",
        text)
    assert m, (
        "the SERVED dashboard bundle does not carry the `/webchat`,`/qubit-bot` "
        "full-height route literal at all; the full-height predicate it should "
        "extend is gone from the served bytes"
    )
    members = re.findall(r"`(/qubit-[\w-]+|/webchat)`", m.group(1))
    assert "/qubit-office" in members, (
        "the SERVED dashboard bundle does not carry /qubit-office in the "
        f"full-height route list (saw {members}); a source edit landed but the "
        "dashboard was never rebuilt, so the live page still has the auto-height "
        "bug"
    )
    # And the chat list must NOT have gained the office (the two predicates are
    # separate); the served chat literal is `/chat`,`/qubit-chat` via variables,
    # so assert the office is not one of the chat-route definitions.
    assert not re.search(r"`/qubit-office`\s*,\s*[A-Za-z_$][\w$]*\s*=\s*`?/chat", text), (
        "the office appears inside the served chat-route definition; full-height "
        "and chat parse different questions and must not be merged"
    )


# --------------------------------------------------------------------------- #
# 4. THE IFRAME + STREAM — live browser proof, waiting properly
# --------------------------------------------------------------------------- #
class _CDP:
    """A tiny Chrome DevTools Protocol client (WebSocket JSON-RPC).

    Extracted from the proven scratch client
    (``runtime/cache/scratch/diag_office_sse.py``) so the suite owns no external
    file. ``navigate`` is FIRE-AND-FORGET: ``Page.navigate``'s ack can hang when
    the page holds a live SSE stream open, so we send it and poll for state.
    """

    def __init__(self, ws_url: str):
        import websocket  # noqa: PLC0415  (optional dep; guarded by callers)

        self.ws = websocket.create_connection(ws_url, timeout=20, suppress_origin=True)
        self._id = 0
        self.events: list[dict] = []

    def call(self, method: str, params: dict | None = None, timeout: float = 25.0):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.ws.settimeout(max(0.1, deadline - time.time()))
            try:
                raw = self.ws.recv()
            except Exception:
                break
            if not raw:
                continue
            msg = json.loads(raw)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)
        raise RuntimeError(f"{method} timed out")

    def navigate(self, url: str) -> None:
        """Fire-and-forget navigation (see class docstring)."""
        self._id += 1
        self.ws.send(json.dumps(
            {"id": self._id, "method": "Page.navigate", "params": {"url": url}}))

    def ev(self, expr: str, timeout: float = 20.0):
        r = self.call("Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "awaitPromise": True,
        }, timeout=timeout)
        if r.get("exceptionDetails"):
            return {"__exc": r["exceptionDetails"].get("text")}
        return r.get("result", {}).get("value")

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _chrome_target(port: int, proc: subprocess.Popen, deadline: float) -> str:
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"chrome exited {proc.returncode}")
        try:
            with urlrequest.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as r:
                for t in json.loads(r.read()):
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.15)
    raise RuntimeError("no CDP page target")


class _Browser:
    """Headless Chrome over CDP, with the ``/tmp`` requirements baked in.

    Chrome MUST get ``--user-data-dir`` under ``/tmp`` and ``TMPDIR=/tmp`` or it
    cannot create its profile (the Qubit scratch dir is on a filesystem it will
    not use).
    """

    def __init__(self):
        self.port = _free_port()
        self.profile = tempfile.mkdtemp(prefix="office-contract-", dir="/tmp")
        env = dict(os.environ)
        env["TMPDIR"] = "/tmp"
        self.proc = subprocess.Popen(
            [CHROME, "--headless=new", "--no-sandbox", "--disable-gpu",
             "--disable-dev-shm-usage", "--hide-scrollbars",
             "--window-size=1600,1000", "--remote-allow-origins=*",
             f"--remote-debugging-port={self.port}",
             f"--user-data-dir={self.profile}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        self.cdp: _CDP | None = None

    def __enter__(self) -> "_Browser":
        self.cdp = _CDP(_chrome_target(self.port, self.proc, time.time() + 30))
        for m in ("Page.enable", "Runtime.enable", "Network.enable", "Log.enable"):
            self.cdp.call(m)
        return self

    def __exit__(self, *exc) -> None:
        if self.cdp:
            self.cdp.close()
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)

    def console_errors(self) -> list[str]:
        out: list[str] = []
        for e in self.cdp.events:  # type: ignore[union-attr]
            m, p = e.get("method"), e.get("params", {})
            if m == "Runtime.exceptionThrown":
                d = p.get("exceptionDetails", {})
                out.append(str((d.get("exception") or {}).get("description") or d.get("text")))
            elif m == "Runtime.consoleAPICalled" and p.get("type") == "error":
                out.append(" ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", [])))
            elif m == "Log.entryAdded" and p.get("entry", {}).get("level") == "error":
                out.append(str(p["entry"].get("text", "")))
        return out


#: A JS probe for a canvas painted with more than a trivial amount of ink.
#: Waits are done in Python by polling this; we never take a single early read.
_PROBE_TOP = r"""
(() => {
  const f = document.querySelector('iframe[data-testid="office-app-frame"]');
  if (!f) return {iframe: false};
  let cv = null, px = null;
  try {
    const d = f.contentDocument;
    cv = d && d.querySelector('#world');
    if (cv && cv.width > 200) {
      const ctx = cv.getContext('2d');
      const img = ctx.getImageData(0, 0, cv.width, cv.height).data;
      let n = 0;
      for (let i = 0; i < img.length; i += 4) {
        if (img[i] || img[i+1] || img[i+2] || img[i+3] > 10) n++;
      }
      px = n;
    }
  } catch (e) { /* cross-origin or not laid out yet */ }
  return {
    iframe: true,
    src: f.src,
    state: f.getAttribute('data-state'),
    canvas: !!cv,
    w: cv ? cv.width : 0,
    painted: px,
  };
})()
"""


def _wait_for(probe, predicate, timeout: float = 30.0, interval: float = 0.4):
    """Poll ``probe()`` until ``predicate`` holds; return the last value."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = probe()
        if last and predicate(last):
            return last
        time.sleep(interval)
    return last


@pytest.mark.live
@requires_dashboard
@requires_chrome
def test_the_office_iframe_mounts_paints_and_opens_the_stream():
    """The live page: iframe src, a painted ``#world`` canvas, and a 200 SSE.

    This is the check that catches "the tab opens but the office is blank". It
    WAITS for the iframe to attach and the canvas to paint (the office takes
    ~0.3 s to connect and the iframe longer) rather than sampling once and
    reporting a false 0.
    """
    pytest.importorskip("websocket", reason="the `websocket-client` package is required for the CDP browser checks")

    tok = _token()
    url = f"{BASE_URL}/qubit-office" + (f"?token={tok}" if tok else "")

    def stream_seen() -> bool:
        return any(
            e.get("method") == "Network.responseReceived"
            and "/api/stream" in e["params"]["response"]["url"]
            for e in b.cdp.events  # type: ignore[union-attr]
        )

    with _Browser() as b:
        b.cdp.navigate(url)  # type: ignore[union-attr]
        # Wait for the canvas to paint. Then keep ISSUING a cheap CDP call so
        # `cdp.events` keeps draining — events are only recorded while `call()`
        # is waiting for its own response, so a passive check would never see
        # the stream response and would flake to "no /api/stream observed".
        state = _wait_for(
            lambda: b.cdp.ev(_PROBE_TOP),  # type: ignore[union-attr]
            lambda s: s.get("canvas") and (s.get("painted") or 0) > MIN_PAINTED_PIXELS,
        )
        deadline = time.time() + 15.0
        while time.time() < deadline and not stream_seen():
            b.cdp.ev("1")  # type: ignore[union-attr]  # drain Network events
            time.sleep(0.25)

    assert state and state.get("iframe"), (
        "the office page mounted no iframe[data-testid=office-app-frame]; the "
        "plugin entry did not render the app (check the entry resolution)"
    )
    assert "/plugin-apps/qubit-office/habblaud/" in (state.get("src") or ""), (
        f"the office iframe src is {state.get('src')!r}, not the vendored "
        "Habblaud app directory"
    )
    assert state.get("state") == "ready", (
        f"the office iframe never reached data-state=ready (saw "
        f"{state.get('state')!r}); the vendored app did not load"
    )
    assert state.get("canvas"), "the office iframe has no #world canvas"
    assert (state.get("w") or 0) > MIN_CANVAS_WIDTH, (
        f"#world is {state.get('w')}px wide; the iframe attached but never laid out"
    )
    painted = state.get("painted") or 0
    assert painted > MIN_PAINTED_PIXELS, (
        f"#world has only {painted} non-background pixels (floor "
        f"{MIN_PAINTED_PIXELS}); the office never connected/painted — this is "
        "the early-sampling trap, but the wait above already ruled timing out"
    )

    # The SSE request itself must have returned 200 text/event-stream.
    sse = [
        e["params"]["response"]
        for e in b.cdp.events  # type: ignore[union-attr]
        if e.get("method") == "Network.responseReceived"
        and "/api/stream" in e["params"]["response"]["url"]
    ]
    assert sse, (
        "no /api/stream request was observed; the vendored app never opened its "
        "EventSource, so the office is frozen on its first frame"
    )
    ok = [r for r in sse if r.get("status") == 200
          and "text/event-stream" in (r.get("mimeType") or "")]
    assert ok, (
        f"the office SSE did not return 200 text/event-stream; saw "
        f"{[(r.get('status'), r.get('mimeType')) for r in sse]}. A 401/403 here "
        "means the stream token/referer policy drifted; a JSON 200 means the "
        "stream route stopped streaming"
    )


@pytest.mark.live
@requires_dashboard
@requires_chrome
def test_the_shell_rollback_renders_with_zero_console_errors():
    """``?shell=1`` is the rollback; it must still render its own painted canvas.

    The hand-built shell is deliberately kept one query param away so a
    regression in the vendored app can be escaped without a rebuild. If the
    rollback itself rots, the escape hatch is gone at exactly the moment it is
    needed. Its canvas is the top-level document's (not the iframe's), so this
    probes the host page directly.
    """
    pytest.importorskip("websocket", reason="the `websocket-client` package is required for the CDP browser checks")

    probe = r"""
    (() => {
      const cvs = [...document.querySelectorAll('canvas')];
      let painted = 0, best = null;
      for (const c of cvs) {
        if (c.width > 200) {
          try {
            const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
            let n = 0;
            for (let i = 0; i < d.length; i += 4) {
              if (d[i] || d[i+1] || d[i+2] || d[i+3] > 10) n++;
            }
            if (n > painted) { painted = n; best = {w: c.width, h: c.height}; }
          } catch (e) {}
        }
      }
      return {
        shell: !!document.querySelector('[data-testid="office-game-shell"]'),
        iframe: !!document.querySelector('iframe[data-testid="office-app-frame"]'),
        painted, best,
      };
    })()
    """
    tok = _token()
    url = f"{BASE_URL}/qubit-office?shell=1" + (f"&token={tok}" if tok else "")
    with _Browser() as b:
        b.cdp.navigate(url)  # type: ignore[union-attr]
        state = _wait_for(
            lambda: b.cdp.ev(probe),  # type: ignore[union-attr]
            lambda s: (s.get("painted") or 0) > MIN_PAINTED_PIXELS,
        )
        errors = b.console_errors()

    assert state and state.get("shell"), (
        "?shell=1 did not mount the hand-built office shell "
        "(no [data-testid=office-game-shell]); the rollback renderer is gone"
    )
    assert not state.get("iframe"), (
        "?shell=1 mounted the Habblaud iframe instead of the shell; the "
        "rollback switch no longer selects the hand-built renderer"
    )
    painted = (state or {}).get("painted") or 0
    assert painted > MIN_PAINTED_PIXELS, (
        f"the shell canvas painted only {painted} non-background pixels "
        f"(floor {MIN_PAINTED_PIXELS}); the rollback renders blank"
    )
    # Filter known-benign noise (favicon/net::ERR to a non-existent icon).
    real = [e for e in errors
            if "favicon" not in e.lower() and "net::err" not in e.lower()]
    assert not real, "?shell=1 produced console errors:\n  " + "\n  ".join(real[:10])
