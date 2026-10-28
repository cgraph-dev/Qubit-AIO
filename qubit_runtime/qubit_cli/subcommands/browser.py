"""``qubit browser`` subcommand parser."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request


def _cdp_version(port: int) -> str:
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{int(port)}/json/version", timeout=1.0) as resp:
            import json
            data = json.loads(resp.read(4096).decode("utf-8", "replace"))
        return str(data.get("Browser") or data.get("webSocketDebuggerUrl") or "ready")
    except Exception:
        return ""


def _cdp_port(cfg: dict, fallback: int = 9222) -> int:
    url = ""
    try:
        url = str((cfg.get("browser") or {}).get("cdp_url") or "")
    except Exception:
        url = ""
    for token in url.split(":")[::-1]:
        if token.isdigit():
            return int(token)
    return int(fallback)


def build_browser_parser(subparsers) -> None:
    """Attach the ``browser`` subcommand to ``subparsers``."""
    browser_parser = subparsers.add_parser(
        "browser", help="Real-profile browsing helpers (close a browser locking its profile)",
        description="Helpers for real-profile browsing (browser.use_real_profile). "
            "close-profile terminates the browser process tree holding your "
            "default profile so Qubit can copy it — DESTRUCTIVE (unsaved tabs "
            "in that browser are lost). The agent runs this only after you "
            "approve closing the browser.")
    browser_subparsers = browser_parser.add_subparsers(dest="browser_action")
    browser_close = browser_subparsers.add_parser(
        "close-profile",
        help="Close the browser locking your real profile (asks nothing — "
             "run only with the user's explicit OK; loses unsaved tabs)")
    browser_close.add_argument(
        "--browser",
        help="Override detected default browser (chrome/edge/brave/brave-origin/chromium)")

    browser_open = browser_subparsers.add_parser(
        "open", help="Open the real Chromium profile with CDP for Qubit")
    browser_open.add_argument("--port", type=int, default=0,
                              help="CDP port (default: browser.cdp_url or 9222)")
    browser_open.add_argument("--profile-dir", default="",
                              help="Chromium user-data-dir (defaults to browser.profile_dir or the detected profile)")
    browser_open.add_argument("--browser", default="",
                              help="Override detected Chromium (chromium/chrome/brave/edge)")
    browser_open.add_argument("--url", default="", help="Optional URL to open")

    browser_status = browser_subparsers.add_parser(
        "status", help="Show whether the Qubit CDP browser is reachable")
    browser_status.add_argument("--port", type=int, default=0)

    def _dispatch_browser(_args):
        from qubit_cli.browser_connect import (
            UNSUPPORTED_CHANNEL, chromium_executable, close_browser_holding_profile,
            detect_default_chromium, real_profile_data_dir)
        from qubit_cli.config import load_config

        action = getattr(_args, "browser_action", None)
        _cfg = load_config()
        cfg = _cfg if isinstance(_cfg, dict) else {}
        port = int(getattr(_args, "port", 0) or 0) or _cdp_port(cfg)

        if action == "status":
            version = _cdp_version(port)
            if version:
                print(f"✓ CDP ready on http://127.0.0.1:{port} ({version})")
                return 0
            print(f"✗ No CDP browser on http://127.0.0.1:{port}")
            return 1

        if action == "open":
            if _cdp_version(port):
                print(f"✓ CDP already ready on http://127.0.0.1:{port} — Qubit can use it.")
                return 0
            browser = (getattr(_args, "browser", "") or detect_default_chromium())
            if not browser or browser == UNSUPPORTED_CHANNEL:
                print("✗ No supported Chromium browser detected.", file=sys.stderr)
                return 1
            profile = (getattr(_args, "profile_dir", "")
                       or str((cfg.get("browser") or {}).get("profile_dir") or "")
                       or real_profile_data_dir(browser))
            exe = chromium_executable(browser)
            if not exe:
                print(f"✗ Could not find the {browser} executable.", file=sys.stderr)
                return 1
            if not profile or not os.path.isdir(profile):
                print(f"✗ Chromium profile not found: {profile}", file=sys.stderr)
                return 1
            argv = [exe, f"--user-data-dir={profile}",
                    f"--remote-debugging-port={port}", "--no-first-run",
                    "--no-default-browser-check"]
            if getattr(_args, "url", ""):
                argv.append(str(_args.url))
            try:
                subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            except Exception as exc:
                print(f"✗ Could not launch {exe}: {exc}", file=sys.stderr)
                return 1
            deadline = time.time() + 20.0
            while time.time() < deadline:
                version = _cdp_version(port)
                if version:
                    print(f"✓ Chromium CDP ready on http://127.0.0.1:{port}")
                    print(f"  profile: {profile}")
                    print("  Qubit browser.cdp_url should point at this port.")
                    return 0
                time.sleep(0.25)
            print("✗ Chromium did not expose CDP within 20s.", file=sys.stderr)
            print("  If Chromium is already running on this profile, close it and retry;", file=sys.stderr)
            print("  or pass --profile-dir with the active profile.", file=sys.stderr)
            return 1

        if action != "close-profile":
            browser_parser.print_help()
            return 2
        browser = getattr(_args, "browser", None) or detect_default_chromium()
        if not browser or browser == UNSUPPORTED_CHANNEL:
            print("✗ No supported Chromium default browser detected.", file=sys.stderr)
            return 1
        src = real_profile_data_dir(browser)
        if not src:
            print(f"✗ Could not resolve the {browser} profile directory.", file=sys.stderr)
            return 1
        closed, msg = close_browser_holding_profile(src)
        if closed:
            print(f"✓ {msg}")
            return 0
        print(f"✗ {msg}", file=sys.stderr)
        return 1

    browser_parser.set_defaults(func=_dispatch_browser)
