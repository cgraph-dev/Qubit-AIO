"""Slice-6 CDP supervisor: watchdog for the debug-port Chromium.

Keeps ONE Chromium alive with ``--remote-debugging-port=9222`` on the owner's
 existing profile (same session: Gmail logged in, Perplexity Pro), pins the
Slice-2 browser-Pro tabs (Perplexity/Gmail), and exposes health the Slice-2
pre-flight (``chat_transport._cdp_healthy``) can read via ``read_health``.

SAFETY (non-negotiable): additive/watchdog ONLY. The supervisor never
terminates or signals any browser process — if the owner's Chromium runs
WITHOUT the debug port it reports ``browser_without_cdp`` (degraded) and
owner relaunches with the flag. ``launch()`` refuses while ANY chromium
process exists (``SUPERVISOR_REFUSES_RUNNING_INSTANCE``).

Run: ``python -m voicebridge.cdp_supervisor --watch`` (writes the health
file every round) or ``--once`` for a single reconcile.
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

CDP_PORT = 9222
CDP_VERSION_URL = f"http://127.0.0.1:{CDP_PORT}/json/version"
CDP_TABS_URL = f"http://127.0.0.1:{CDP_PORT}/json"
CDP_NEW_TAB_URL = f"http://127.0.0.1:{CDP_PORT}/json/new"

#: Pinned tabs for the Slice-2 browser-Pro layer (match by prefix).
PINNED_TABS = (
    "https://www.perplexity.ai",
    "https://mail.google.com",
)

DEFAULT_HEALTH_PATH = "/tmp/qubit-voicebridge/cdp_health.json"
_HEALTH_TTL_S = 30.0

_CHROMIUM_BINS = ("chromium", "chromium-browser", "google-chrome",
                  "google-chrome-stable")
_CHROMIUM_PROC_TOKENS = ("chrome", "chromium")


def _pgrep_chromium() -> List[Dict[str, Any]]:
    """List running chromium processes (pid + cmd). Never signals anything."""
    try:
        p = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True,
                           text=True, timeout=8, check=False)
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for line in (p.stdout or "").splitlines()[1:]:
        low = line.lower()
        if not any(t in low for t in _CHROMIUM_PROC_TOKENS):
            continue
        if "crashpad" in low or "zygote" in low:
            continue
        parts = line.split(None, 1)
        try:
            out.append({"pid": int(parts[0]),
                        "cmd": (parts[1] if len(parts) > 1 else "")[:200]})
        except ValueError:
            continue
    return out


def chromium_processes() -> List[Dict[str, Any]]:
    return _pgrep_chromium()


def cdp_tabs(timeout: float = 2.0) -> Optional[List[Dict[str, Any]]]:
    """Read-only ``GET /json`` tab list; None when CDP is unreachable."""
    try:
        with urllib.request.urlopen(CDP_TABS_URL,
                                    timeout=float(timeout)) as resp:
            return json.loads(resp.read(1 << 20).decode("utf-8") or "[]")
    except Exception:
        return None


def _pinned_missing(tabs: List[Dict[str, Any]]) -> List[str]:
    urls = [str(t.get("url") or "") for t in tabs if isinstance(t, dict)]
    return [pin for pin in PINNED_TABS
            if not any(u.startswith(pin) for u in urls)]


def health(timeout: float = 2.0) -> Dict[str, Any]:
    """Supervisor health verdict (pure probe, no side effects)."""
    procs = chromium_processes()
    tabs = cdp_tabs(timeout=timeout)
    cdp_up = tabs is not None
    running = bool(procs)
    if cdp_up:
        missing = _pinned_missing(tabs or [])
        verdict = "healthy" if not missing else "tabs_missing"
    elif running:
        missing = list(PINNED_TABS)
        verdict = "browser_without_cdp"
    else:
        missing = list(PINNED_TABS)
        verdict = "down_no_browser"
    return {
        "schema": "qubit-cdp-health/1",
        "ts": time.time(),
        "cdp_up": cdp_up,
        "chromium_running": running,
        "chromium_pids": [p["pid"] for p in procs],
        "tab_count": len(tabs) if tabs is not None else 0,
        "pinned_missing": missing,
        "verdict": verdict,
    }


def write_health(path: str = DEFAULT_HEALTH_PATH,
                 timeout: float = 2.0) -> Dict[str, Any]:
    """Probe and persist the health file (the Slice-2 hook reads this)."""
    h = health(timeout=timeout)
    try:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        tmp.write_text(json.dumps(h), encoding="utf-8")
        tmp.replace(dest)
    except OSError:
        pass
    return h


def read_health(path: str = DEFAULT_HEALTH_PATH,
                max_age_s: float = _HEALTH_TTL_S) -> Optional[Dict[str, Any]]:
    """Read a cached health verdict; None when absent/stale/unparseable."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
        h = json.loads(raw)
    except (OSError, ValueError):
        return None
    if not isinstance(h, dict):
        return None
    try:
        age = time.time() - float(h.get("ts") or 0)
    except (TypeError, ValueError):
        return None
    if age < 0 or age > float(max_age_s):
        return None
    return h


def _spawn(argv: List[str]) -> int:
    p = subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         start_new_session=True)
    return p.pid


def launch(user_data_dir: str = "/home/trick/snap/chromium/common/chromium",
           profile_directory: str = "Default",
           tabs: List[str] = list(PINNED_TABS)) -> Dict[str, Any]:
    """Start Chromium with the debug port on the owner's profile.

    REFUSES while any chromium process runs (never orphans/strands the
    owner's session, never double-locks the profile). Returns a typed dict.
    """
    running = chromium_processes()
    if running:
        return {"ok": False, "code": "SUPERVISOR_REFUSES_RUNNING_INSTANCE",
                "detail": "a Chromium instance is already running "
                          f"(pids {[p['pid'] for p in running]}); refusing to "
                          "launch a second one — relaunch the existing "
                          "instance with --remote-debugging-port=9222",
                "pids": [p["pid"] for p in running]}
    binary = next((b for b in _CHROMIUM_BINS if _which(b)), None)
    if binary is None:
        return {"ok": False, "code": "SUPERVISOR_NO_BROWSER",
                "detail": "no chromium binary on PATH"}
    argv = [binary, f"--user-data-dir={user_data_dir}",
            f"--profile-directory={profile_directory}",
            f"--remote-debugging-port={CDP_PORT}",
            "--remote-allow-origins=*", *tabs]
    try:
        pid = _spawn(argv)
    except Exception as exc:
        return {"ok": False, "code": "SUPERVISOR_LAUNCH_FAILED",
                "detail": str(exc)[:160]}
    return {"ok": True, "pid": pid, "argv": argv}


def _which(binary: str) -> bool:
    import shutil
    return shutil.which(binary) is not None


def open_pinned_tab(url: str, timeout: float = 5.0) -> bool:
    """Open one URL via CDP ``/json/new`` (only when CDP is already up)."""
    try:
        req = urllib.request.Request(
            f"{CDP_NEW_TAB_URL}?{urllib.parse.urlencode({'url': url})}")
        with urllib.request.urlopen(req, timeout=float(timeout)):
            return True
    except Exception:
        return False


def reconcile(health_path: str = DEFAULT_HEALTH_PATH,
              timeout: float = 2.0) -> Dict[str, Any]:
    """One watchdog round: launch-if-absent, pin tabs, persist health."""
    h = health(timeout=timeout)
    action = "none"
    if h["verdict"] == "down_no_browser":
        res = launch()
        action = ("launched" if res.get("ok")
                  else f"launch_refused:{res.get('code')}")
        h["action"] = action
        h["launch"] = res
    elif h["verdict"] == "tabs_missing":
        opened = [u for u in h["pinned_missing"] if open_pinned_tab(u)]
        action = f"pinned_opened:{len(opened)}/{len(h['pinned_missing'])}"
        h["action"] = action
    else:
        h["action"] = action
    try:
        dest = Path(health_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        tmp.write_text(json.dumps(h), encoding="utf-8")
        tmp.replace(dest)
    except OSError:
        pass
    return h


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="qubit-cdp-supervisor")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=float, default=30.0)
    ap.add_argument("--health-path", default=DEFAULT_HEALTH_PATH)
    args = ap.parse_args(argv)
    if args.watch:
        while True:
            reconcile(health_path=args.health_path)
            time.sleep(max(5.0, args.interval))
        return 0
    h = reconcile(health_path=args.health_path)
    print(json.dumps(h, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["CDP_PORT", "PINNED_TABS", "DEFAULT_HEALTH_PATH",
           "chromium_processes", "cdp_tabs", "health", "write_health",
           "read_health", "launch", "open_pinned_tab", "reconcile", "main"]
