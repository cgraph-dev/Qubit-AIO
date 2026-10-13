#!/usr/bin/env python3
"""Headless-Chrome screenshot + render probe for the Qubit Office page.

WHY THIS EXISTS
---------------
The Office renderer has been changed many times while every *text* assertion
kept passing -- the HTML contained the right strings, the request returned
200, the components mounted -- and yet the page rendered a mostly-empty
canvas: a black box, a collapsed grid, a WebGL context that silently failed to
draw. Nothing short of looking at the actual pixels ever caught those bugs.

So the rule is: text passing is not evidence that the UI renders. The only
trustworthy check is to load the page in a real browser and count the
non-background pixels. This script is that check, extracted from the throwaway
code that had been re-typed (and re-debugged) every time.

WHAT IT DOES
------------
1. Reads the dashboard token from ``~/.config/qubit-voicebridge/voice.yaml``
   (``webui.token``). The token value is NEVER printed -- only its length.
2. Launches headless Chrome with a free CDP port and a throwaway profile.
3. Drives CDP over the websocket: seeds the session token into localStorage,
   navigates to the Office route.
4. Polls (with a deadline, never a blind sleep) until the page reports
   ``data-state="ready"`` AND at least one canvas wider than 200px exists.
5. Captures the screenshot plus a JSON report: ``data-state``, canvas sizes,
   non-background pixel count and top colour buckets for the largest canvas,
   visible page text, and console errors.
6. Exits non-zero -- with a clear message -- if Chrome fails to start, the
   dashboard is unreachable, or the page never reaches ready. A tool that
   silently returns a blank canvas is worse than no tool.

USAGE
-----
    .venv/bin/python scripts/render_office_screenshot.py
    .venv/bin/python scripts/render_office_screenshot.py \
        --url http://127.0.0.1:8890/qubit-office \
        --out /tmp/office.png --timeout 30 --json

``--json`` prints only the machine-readable report (plus the stage lines) so
the run can be consumed by other scripts; without it the report is pretty
printed.

Requires only what is already installed in the venv: ``websocket-client`` and
``PyYAML``.

See also: tests/test_office_render.py if present, and docs/ for the Office
route contract.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

import yaml

try:
    import websocket  # websocket-client
except ImportError:  # pragma: no cover - environment guard
    sys.stderr.write(
        "ERROR: websocket-client is required (pip install websocket-client)\n"
    )
    raise SystemExit(3)

CHROME = "/usr/bin/google-chrome"

# localStorage keys the SPA has used over time to hold the session token.
# We seed all of them so whichever one this build reads is present.
TOKEN_KEYS = [
    "qubit-session-token",
    "hermes-session-token",
    "session-token",
    "qubit_token",
]

# The Office page paints a near-black background (~#0b1016). A pixel is
# "background" when all three channels are below this threshold.
BG_CHANNEL_MAX = 30

DEFAULT_TOKEN_PATH = "~/.config/qubit-voicebridge/voice.yaml"
DEFAULT_BASE = "http://127.0.0.1:8890"


class RenderProbeError(RuntimeError):
    """Raised for any condition the tool must surface as a hard failure."""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def read_token(path: str) -> str:
    """Return ``webui.token`` from the voice config. Never logs the value."""
    p = Path(os.path.expanduser(path))
    if not p.is_file():
        raise RenderProbeError(f"config not found: {p}")
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    token = (data.get("webui") or {}).get("token")
    if not token:
        raise RenderProbeError(f"webui.token missing in {p}")
    return str(token)


def free_port() -> int:
    """Ask the OS for an unused TCP port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def http_status(url: str, timeout: float = 5.0) -> int | None:
    """Cheap reachability check via the devtools HTTP endpoint."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.getcode()
    except urllib.error.HTTPError as exc:  # server answered, just not 200
        return exc.code
    except Exception:
        return None


def wait_for_endpoint(url: str, proc: subprocess.Popen, deadline: float) -> str:
    """Poll the CDP HTTP endpoint until a *page* target exists.

    ``/json/version`` gives the browser-level socket, which cannot be driven
    with Page/Runtime domains. We need the page target's websocket from
    ``/json/list``.
    """
    import urllib.request

    list_url = url.rstrip("/") + "/json/list"
    last_err = None
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RenderProbeError(
                f"Chrome exited early (code {proc.returncode}) before CDP was ready"
            )
        try:
            with urllib.request.urlopen(list_url, timeout=2) as resp:
                targets = json.loads(resp.read().decode("utf-8"))
            for t in targets:
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                    return t["webSocketDebuggerUrl"]
            last_err = f"no page target yet ({len(targets)} targets)"
        except Exception as exc:  # not up yet
            last_err = str(exc)
        time.sleep(0.15)
    raise RenderProbeError(
        f"CDP page target never became ready within timeout ({last_err})"
    )


class CDP:
    """Minimal Chrome DevTools Protocol client over a websocket."""

    def __init__(self, ws_url: str) -> None:
        # origin, suppressed, timeout...
        self.ws = websocket.create_connection(
            ws_url, timeout=15, suppress_origin=True
        )
        self._id = 0
        self._events: list[dict] = []

    def call(self, method: str, params: dict | None = None, timeout: float = 20.0):
        self._id += 1
        msg_id = self._id
        self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.ws.settimeout(max(0.1, deadline - time.time()))
            try:
                raw = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                break
            if not raw:
                continue
            msg = json.loads(raw)
            if msg.get("id") == msg_id:
                if "error" in msg:
                    raise RenderProbeError(
                        f"CDP {method} failed: {msg['error']}"
                    )
                return msg.get("result", {})
            if "method" in msg:
                self._events.append(msg)
        raise RenderProbeError(f"CDP {method} timed out after {timeout}s")

    def eval(self, expression: str, timeout: float = 20.0):
        """Evaluate JS, returning the deserialized value (awaitPromise)."""
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
            timeout=timeout,
        )
        if result.get("exceptionDetails"):
            raise RenderProbeError(
                f"JS raised: {result['exceptionDetails'].get('text')}"
            )
        return result.get("result", {}).get("value")

    def drain_events(self, method: str | None = None) -> list[dict]:
        out = self._events
        self._events = []
        if method:
            return [e for e in out if e.get("method") == method]
        return out

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# the JS used to inspect the page
# --------------------------------------------------------------------------- #
_PROBE_JS = r"""
(() => {
  const canvases = Array.from(document.querySelectorAll('canvas'));
  const body = document.body ? document.body.innerText : '';
  // data-state lives on an inner container, not always document.body -- read
  // wherever it exists.
  const stateEl = document.querySelector('[data-state]');
  const dataState = stateEl ? stateEl.getAttribute('data-state') : null;
  return {
    dataState: dataState,
    dataStateTag: stateEl ? stateEl.tagName : null,
    canvasCount: canvases.length,
    canvases: canvases.map(c => ({ width: c.width, height: c.height,
                                   cssW: c.clientWidth, cssH: c.clientHeight })),
    text: body,
  };
})()
"""

# Counts non-background pixels and colour buckets on the largest canvas.
_PIXEL_JS = r"""
(() => {
  const canvases = Array.from(document.querySelectorAll('canvas'));
  if (!canvases.length) return { error: 'no canvas' };
  // largest by pixel area
  let big = canvases[0], area = 0;
  for (const c of canvases) {
    const a = c.width * c.height;
    if (a > area) { area = a; big = c; }
  }
  let ctx = big.getContext('2d');
  let w = big.width, h = big.height;
  let imageData = null;
  try {
    imageData = ctx.getImageData(0, 0, w, h);
  } catch (e) {
    // WebGL canvas: read via readPixels on the GL context if available
    const gl = big.getContext('webgl2') || big.getContext('webgl');
    if (gl) {
      const px = new Uint8Array(w * h * 4);
      gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, px);
      return { width: w, height: h, mode: 'webgl', data: Array.from(px) };
    }
    return { error: 'cannot read pixels: ' + e };
  }
  const d = imageData.data;
  return { width: w, height: h, mode: '2d', data: Array.from(d) };
})()
"""


def _bucket(rgb: tuple[int, int, int]) -> str:
    """Coarse colour bucket, quantised to steps of 16 per channel."""
    r, g, b = ((min(255, v) // 16) * 16 for v in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def analyse_pixels(payload: dict) -> dict:
    """Turn raw RGBA into non-background count + top colour buckets."""
    if payload.get("error"):
        return {"error": payload["error"]}
    data = payload.get("data") or []
    nonbg = 0
    buckets: Counter[str] = Counter()
    for i in range(0, len(data) - 3, 4):
        r, g, b = data[i], data[i + 1], data[i + 2]
        if r < BG_CHANNEL_MAX and g < BG_CHANNEL_MAX and b < BG_CHANNEL_MAX:
            continue
        nonbg += 1
        buckets[_bucket((r, g, b))] += 1
    total = payload["width"] * payload["height"]
    return {
        "width": payload["width"],
        "height": payload["height"],
        "mode": payload.get("mode", "2d"),
        "total_pixels": total,
        "non_background_pixels": nonbg,
        "non_background_pct": round(100.0 * nonbg / total, 3) if total else 0.0,
        "top_buckets": [
            {"color": c, "count": n} for c, n in buckets.most_common(10)
        ],
    }


# --------------------------------------------------------------------------- #
# main flow
# --------------------------------------------------------------------------- #
def run(args) -> int:
    out_path = Path(os.path.expanduser(args.out)).resolve()

    # -- token ---------------------------------------------------------------
    try:
        token = read_token(args.config)
    except RenderProbeError as exc:
        sys.stderr.write(f"FAIL: {exc}\n")
        return 4
    print(f"token: read from {os.path.expanduser(args.config)} "
          f"(length={len(token)}, value hidden by policy)")

    base = args.url.rstrip("/")
    origin = "/".join(base.split("/")[:3])  # scheme://host:port

    # -- dashboard reachability ---------------------------------------------
    code = http_status(origin + "/")
    if code is None:
        sys.stderr.write(
            f"FAIL: dashboard unreachable at {origin}/ "
            "(is qubit-voicebridge.service running?)\n"
        )
        return 2
    office_code = http_status(base)
    print(f"dashboard: {origin}/ -> HTTP {code}; route {base} -> HTTP {office_code}")

    # -- launch chrome -------------------------------------------------------
    port = free_port()
    profile = tempfile.mkdtemp(prefix="qubit-shot-profile-")
    cmd = [
        CHROME,
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--hide-scrollbars",
        "--window-size=1600,1000",
        # Required: without this the CDP websocket handshake 403s.
        "--remote-allow-origins=*",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "about:blank",
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    deadline = time.time() + args.timeout
    cdp = None
    try:
        ws_url = wait_for_endpoint(f"http://127.0.0.1:{port}", proc, deadline)
        cdp = CDP(ws_url)
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Log.enable")
        print(f"chrome: headless on cdp port {port}")

        # -- seed localStorage on the origin, then go to the Office route ---
        cdp.call("Page.navigate", {"url": origin + "/"})
        _wait_ready_basic(cdp, deadline)
        seed = {k: token for k in TOKEN_KEYS}
        cdp.eval(
            "(() => { const t = %s; for (const k of %s) "
            "localStorage.setItem(k, t); return true; })()"
            % (json.dumps(token), json.dumps(TOKEN_KEYS))
        )
        print(f"localStorage: seeded {len(TOKEN_KEYS)} token keys")

        cdp.call("Page.navigate", {"url": base})

        # -- poll for readiness (deadline, not sleep) ------------------------
        state = None
        big_canvas = 0
        polls = 0
        while time.time() < deadline:
            polls += 1
            try:
                info = cdp.eval(_PROBE_JS)
            except RenderProbeError:
                time.sleep(0.2)
                continue
            state = info.get("dataState")
            widths = [c["width"] for c in info.get("canvases", [])]
            big_canvas = max(widths) if widths else 0
            if state == "ready" and big_canvas > 200:
                break
            time.sleep(0.25)

        if state != "ready" or big_canvas <= 200:
            sys.stderr.write(
                "FAIL: page never reached data-state=ready with a canvas "
                f">200px (last state={state!r}, largest canvas width={big_canvas}, "
                f"polls={polls}).\n"
            )
            _dump_console(cdp)
            return 1

        print(f"ready: data-state=ready after {polls} polls "
              f"(largest canvas {big_canvas}px wide)")

        # -- gather the report ----------------------------------------------
        info = cdp.eval(_PROBE_JS)
        pixels = analyse_pixels(cdp.eval(_PIXEL_JS))
        console_errors = _console_errors(cdp)

        shot = cdp.call(
            "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
        )
        png = base64.b64decode(shot["data"])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(png)

        report = {
            "url": base,
            "data_state": info.get("dataState"),
            "canvas_count": info.get("canvasCount"),
            "canvases": info.get("canvases"),
            "pixels_largest_canvas": pixels,
            "visible_text": info.get("text", ""),
            "console_errors": console_errors,
            "screenshot": str(out_path),
            "screenshot_bytes": len(png),
        }
        print(json.dumps(report, indent=2) if not args.json else json.dumps(report))
        print(f"PNG: {out_path} ({len(png)} bytes)")

        # A render that "reached ready" but painted nothing is still a failure.
        nb = (pixels or {}).get("non_background_pixels", 0)
        if pixels.get("error") or nb == 0:
            sys.stderr.write(
                "FAIL: page reported ready but the largest canvas painted "
                f"no non-background pixels ({pixels}).\n"
            )
            return 1
        return 0
    except RenderProbeError as exc:
        sys.stderr.write(f"FAIL: {exc}\n")
        if cdp is not None:
            _dump_console(cdp)
        return 5
    finally:
        if cdp is not None:
            cdp.close()
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        import shutil

        shutil.rmtree(profile, ignore_errors=True)


def _wait_ready_basic(cdp: CDP, deadline: float) -> None:
    """Wait for document.readyState==='complete' on the seed navigation."""
    while time.time() < deadline:
        try:
            if cdp.eval("document.readyState") == "complete":
                return
        except RenderProbeError:
            pass
        time.sleep(0.1)


def _console_errors(cdp: CDP) -> list[str]:
    """Collect console errors that arrived while driving CDP."""
    errors = []
    for ev in cdp.drain_events():
        method = ev.get("method")
        params = ev.get("params", {})
        if method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails", {})
            text = details.get("exception", {}).get("description") or details.get(
                "text", ""
            )
            errors.append(f"exception: {text}")
        elif method == "Runtime.consoleAPICalled" and params.get("type") == "error":
            args = params.get("args", [])
            errors.append(
                "console.error: "
                + " ".join(str(a.get("value", a.get("description", ""))) for a in args)
            )
        elif method == "Log.entryAdded" and params.get("entry", {}).get(
            "level"
        ) == "error":
            errors.append("log.error: " + str(params["entry"].get("text", "")))
    return errors


def _dump_console(cdp: CDP) -> None:
    errs = _console_errors(cdp)
    if errs:
        sys.stderr.write("console errors:\n")
        for e in errs[-15:]:
            sys.stderr.write(f"  - {e}\n")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Headless-Chrome screenshot + pixel probe for the Qubit Office page.",
    )
    ap.add_argument(
        "--url",
        default=DEFAULT_BASE + "/qubit-office",
        help="Office route to capture (default: %(default)s)",
    )
    ap.add_argument(
        "--out",
        default=os.path.join(
            tempfile.gettempdir(), "qubit-office.png"
        ),
        help="PNG output path (default: %(default)s)",
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Seconds to wait for Chrome + page ready (default: %(default)s)",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="Emit the report as a single JSON line (machine-readable)",
    )
    ap.add_argument(
        "--config",
        default=DEFAULT_TOKEN_PATH,
        help="Path to voice.yaml holding webui.token (default: %(default)s)",
    )
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
