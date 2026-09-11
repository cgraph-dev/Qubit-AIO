"""Packet 8D: authorized hourly inbox monitor.

Runs a bounded hourly scan of the configured platforms over the user's
already-logged-in real Chromium (loopback CDP), persists real screenshot bytes
into report bundles, and produces deduplicated desktop/dashboard (and opt-in
quiet-hours voice) notifications. It never sends/replies/marks-read, never
focuses/closes/navigates a user tab, never starts a competing browser/profile,
and never enables the live systemd timer on its own.

Explicit live-activation contract:

- ``monitor install --dry-run`` renders the service/timer templates and shows
  the diff WITHOUT installing or enabling anything;
- ``monitor install --enable`` (explicit user authorization) installs the units
  atomically with a backup and enables ONLY the timer after the rendered diff
  is inspected;
- ``monitor disable`` / ``monitor rollback`` restore the prior state and keep
  all cursors, reports, memory, and evidence;
- live canaries are opt-in (``--canary``) and are reported separately — a
  skipped or degraded canary is never hidden as a pass.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from ..events import EventJournal
from .adapters.base import CDP_LOOPBACK, browser_ws_url
from .adapters.registry import PLATFORM_NAMES, communications_store, run_scan
from .contracts import NotificationKind, NotificationRecord, NotificationStatus
from .reports import ReportStore

MONITOR_LEASE = "monitor:hourly"
SERVICE_NAME = "qubit-communications-monitor.service"
TIMER_NAME = "qubit-communications-monitor.timer"
# Packet 8I: the oneshot's TimeoutStartSec (the maximum permitted service
# runtime) and the default lease TTL. The lease MUST exceed the timeout by a
# safety margin so a timer run can never outlive its own overlap lease. The
# lease is ALSO renewable (store.renew_lease) for runs that approach the cap.
SERVICE_TIMEOUT_S = 300
LEASE_SAFETY_MARGIN_S = 180
LEASE_MIN_TTL_S = float(SERVICE_TIMEOUT_S + LEASE_SAFETY_MARGIN_S)
_VOICE_NOTIFY_TIMEOUT_S = 30.0   # bounded: a hung synth/playback cannot block
                                 # the hourly oneshot forever.

# Strict quiet-hours grammar: HH:MM-HH:MM with 00-23 hours and 00-59 minutes.
# Anything else (24:00, 07:60, free text) is CONFIG_INVALID, never a silent
# "no quiet hours" fallback (Packet 8I).
_QUIET_HOURS_RE = re.compile(
    r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$")


# --------------------------------------------------------------- notifications
def _parse_quiet_hours(spec: Any) -> Optional[Tuple[int, int]]:
    """Strictly parse ``"HH:MM-HH:MM"`` -> (start_min, end_min).

    Returns None on malformed or out-of-range input — the caller MUST treat
    None as ``CONFIG_INVALID`` and fail closed (suppress voice), never as
    "quiet hours disabled".
    """
    if not isinstance(spec, str):
        return None
    m = _QUIET_HOURS_RE.match(spec.strip())
    if not m:
        return None
    sh, sm, eh, em = (int(g) for g in m.groups())
    return sh * 60 + sm, eh * 60 + em


def validate_timezone(name: Any) -> Optional[str]:
    """Return the validated IANA timezone name, or None when invalid.

    Validation uses the IANA tz database via ``zoneinfo``; a valid name is the
    exact one the caller configured (no host-local fallback anywhere).
    """
    if not isinstance(name, str) or not name.strip():
        return None
    try:
        ZoneInfo(name.strip())
        return name.strip()
    except Exception:
        return None


def time_config_state(cfg, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Typed, fail-closed time configuration state (Packet 8I).

    - ``state=ok``: quiet hours + IANA timezone are valid; ``active`` is the
      wall-clock window membership in the configured zone.
    - ``state=config_invalid``: quiet-hours syntax/range or timezone is
      invalid. ``suppress_voice`` is always True (voice is suppressed until the
      config is repaired) and the window is NEVER evaluated in host-local time.
    """
    spec = cfg.get("communications", "monitor", "voice", "quiet_hours",
                   default="22:00-07:00")
    tz_name = cfg.get("communications", "monitor", "voice", "timezone",
                      default="")
    qh = _parse_quiet_hours(spec)
    tz_ok = validate_timezone(tz_name)
    if qh is None or tz_ok is None:
        problems = []
        if qh is None:
            problems.append(
                f"quiet_hours {spec!r} invalid (expected HH:MM-HH:MM with "
                "00-23 hours, 00-59 minutes)")
        if tz_ok is None:
            problems.append(
                f"timezone {tz_name!r} is not a valid IANA timezone")
        return {"state": "config_invalid",
                "quiet_hours_valid": qh is not None,
                "timezone_valid": tz_ok is not None,
                "detail": "; ".join(problems),
                "suppress_voice": True, "active": True}
    now = now or datetime.now()
    cur = _wall_clock_minute(now, tz_ok)
    start, end = qh
    if start < end:
        active = start <= cur < end
    else:
        active = cur >= start or cur < end   # overnight window
    return {"state": "ok", "quiet_hours": qh, "timezone": tz_ok,
            "spec": spec, "active": active, "suppress_voice": active}


def quiet_hours_active(cfg, now: Optional[datetime] = None) -> bool:
    """True when voice announcements should be suppressed.

    Fail-closed (Packet 8I): malformed quiet-hours or an invalid IANA timezone
    returns True (suppress voice) with the typed ``CONFIG_INVALID`` state
    surfaced through ``time_config_state`` — it never disables quiet hours and
    never falls back to host-local time.
    """
    return bool(time_config_state(cfg, now)["suppress_voice"])


def _wall_clock_minute(now: datetime, tz_name: str) -> Optional[int]:
    """Wall-clock minute of ``now`` expressed in the given IANA zone.

    ``tz_name`` MUST already be validated (``validate_timezone``). A naive
    ``now`` is treated as the host's local wall clock and converted. Returns
    None only for an invalid zone — NEVER a host-local fallback.
    """
    try:
        if now.tzinfo is None:
            now = now.replace(tzinfo=datetime.now().astimezone().tzinfo)
        now = now.astimezone(ZoneInfo(tz_name))
        return now.hour * 60 + now.minute
    except Exception:
        return None


_PLATFORM_DISPLAY = {
    "gmail": "Gmail",
    "instagram": "Instagram",
    "messenger": "Messenger",
}


def _platform_display(platform: str) -> str:
    """User-facing platform name (never the raw internal key)."""
    return _PLATFORM_DISPLAY.get(str(platform).lower(), str(platform))


def _voice_summary(new_count: int, platforms: List[str]) -> str:
    count = max(1, min(int(new_count), 99))
    names = sorted({_platform_display(p) for p in platforms})
    noun = "message" if count == 1 else "messages"
    if len(names) == 1:
        return f"{count} new {noun} in {names[0]}."
    return f"{count} new {noun} in {', '.join(names)}."


def _desktop_text(new_count: int, platforms: List[str]) -> tuple:
    """Plain-language, user-facing title/body for a new-item notification.

    Never includes report IDs, account IDs, URLs, senders, subjects, message
    bodies, cookies, or secrets — only a friendly count + platform names.
    """
    count = max(1, int(new_count))
    names = sorted({_platform_display(p) for p in platforms})
    if len(names) == 1:
        label = names[0]
        noun = "message" if count == 1 else "messages"
        body = ("Open Qubit to review it." if count == 1
                else "Open Qubit to review them.")
        return f"Qubit: {count} new {label} {noun}", body
    return ("Qubit: New messages found",
            f"{count} new messages were found. "
            "Open Qubit to review them.")


def _degraded_text(platforms: List[str]) -> tuple:
    """Plain-language, user-facing title/body for a degraded/auth-required
    alert. Redacted: never exposes exceptions, URLs, account/report IDs,
    message text, cookies, or secrets."""
    names = sorted({_platform_display(p) for p in platforms})
    if len(names) == 1:
        body = f"Qubit could not check {names[0]}. Open Qubit for details."
    else:
        body = "Qubit could not check some accounts. Open Qubit for details."
    return "Qubit needs attention", body


def _send_notify_send(title: str, body: str) -> Dict[str, Any]:
    """Run notify-send with a typed result (OK only on exit 0)."""
    if not shutil.which("notify-send"):
        return {"state": "unavailable", "reason": "notify-send missing"}
    # Packet 8I: resolve the real X11/Wayland notification session at runtime
    # (validated, never the hardcoded unit DISPLAY). X11 sets DISPLAY to the
    # reachable session; Wayland relies on the user D-Bus + XDG_RUNTIME_DIR.
    from ..display import resolve_notification_session
    session = resolve_notification_session()
    if session.get("session_type") == "x11" and session.get("display"):
        os.environ["DISPLAY"] = session["display"]
    try:
        proc = subprocess.run(["notify-send", title, body],
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=5)
    except subprocess.TimeoutExpired:
        return {"state": "failed", "reason": "notify-send timed out"}
    except Exception as exc:
        return {"state": "failed", "reason": str(exc)[:120]}
    if proc.returncode != 0:
        return {"state": "failed",
                "reason": f"notify-send exited with code {proc.returncode}"}
    return {"state": "OK"}


def notify_desktop(cfg, new_count: int, platforms: List[str]) -> Dict[str, Any]:
    """Desktop notification for genuinely-new items (plain-language copy).

    Returns a TYPED result:
    - ``OK`` only when ``notify-send`` exits with code 0;
    - ``failed`` for non-zero exit codes or timeout (with a short reason, never
      stdout/stderr content);
    - ``unavailable`` when the binary is missing.

    The payload is plain-language count/platform copy only — never message
    text, secrets, paths, or report IDs. stdout/stderr are discarded.
    """
    title, body = _desktop_text(new_count, platforms)
    return _send_notify_send(title, body)


def notify_degraded(cfg, platforms: List[str]) -> Dict[str, Any]:
    """Desktop alert for degraded/auth-required accounts (plain-language copy).

    Redacted: only platform names appear; no exceptions, URLs, account/report
    IDs, message text, cookies, or secrets. Typed result identical to
    ``notify_desktop``.
    """
    title, body = _degraded_text(platforms)
    return _send_notify_send(title, body)


def emit_report_ready(cfg, report_id: str, new_count: int,
                      platforms: List[str]) -> Optional[Dict[str, Any]]:
    """Append a typed ``communications.report.ready`` event to the durable
    journal the dashboard consumes (Packet 2B typed stream). Redacted: no
    message text, no account identities."""
    try:
        journal = EventJournal(cfg.state_dir() / "typed-events.jsonl")
        return journal.append(
            "CUSTOM", name="communications.report.ready",
            value={"report_id": report_id, "new_count": int(new_count),
                   "platforms": sorted(set(platforms))})
    except Exception:
        return None


# ------------------------------------------------------------ CDP precondition
def cdp_precondition(cfg) -> Dict[str, Any]:
    """Chromium-CDP deployment precondition (plan task 17).

    Returns a typed state:
    - OK: loopback CDP answers and a Chromium binary answers;
    - BROWSER_RESTART_REQUIRED: Chromium is running but CDP is not reachable —
      the user must restart it with a debugging port; we never kill/restart it;
    - UNAVAILABLE: no Chromium/CDP available (scan would degrade).
    """
    try:
        ws = browser_ws_url(CDP_LOOPBACK)
    except Exception:
        ws = None
    if ws:
        return {"state": "OK", "cdp": True}
    # Chromium running but no CDP -> explicit restart-required (never kill).
    if _chromium_running():
        return {"state": "BROWSER_RESTART_REQUIRED", "cdp": False,
                "detail": "Chromium is running without loopback CDP; restart "
                          "it with --remote-debugging-port=9222. The monitor "
                          "never kills or restarts the browser."}
    return {"state": "UNAVAILABLE", "cdp": False,
            "detail": "no Chromium/CDP available"}


_CHROMIUM_PROC_NAMES = ("chrome", "chromium", "chromium-browser",
                        "google-chrome", "google-chrome-stable")


def _chromium_running() -> bool:
    """True when a REAL Chromium/Chrome browser process is running.

    Matches exact process names only — never the broad ``pgrep -f chrom``
    substring (that falsely matches VS Code's ``chrome_crashpad_handler`` and
    other Electron apps, which would wrongly report BROWSER_RESTART_REQUIRED
    when the monitored browser is not running at all).
    """
    for name in _CHROMIUM_PROC_NAMES:
        try:
            out = subprocess.run(["pgrep", "-x", name, "-a"],
                                 capture_output=True, text=True, timeout=5)
        except Exception:
            continue
        for line in out.stdout.splitlines():
            if "crashpad" in line or "vscode" in line or "/code/" in line:
                continue
            if line.strip():
                return True
    return False


def optional_launch(cfg) -> Dict[str, Any]:
    """OPTIONAL approved Chromium launch integration.

    Only runs when ``communications.monitor.cdp_launch`` is explicitly true.
    Reuses the user's existing Chromium executable + saved profile, bound to
    loopback only; detects an existing process/profile lock and never starts a
    competing instance; never kills or restarts an existing browser.
    """
    enabled = cfg.get("communications", "monitor", "cdp_launch", default=False)
    if not enabled:
        return {"state": "not_enabled", "detail": "cdp_launch is off"}
    pre = cdp_precondition(cfg)
    if pre["state"] == "OK":
        return {"state": "OK", "detail": "CDP already available"}
    if pre["state"] == "BROWSER_RESTART_REQUIRED":
        return {"state": "refused",
                "detail": "Chromium running without CDP; refusing to launch a "
                          "competing instance (user must restart the browser)"}
    try:
        from scripts.browser_auto import BINARY, PROFILE, _cdp_alive, _launch
    except Exception:
        return {"state": "failed", "detail": "launch helper unavailable"}
    if not os.path.exists(BINARY) or not os.path.exists(PROFILE):
        return {"state": "failed",
                "detail": "snap chromium executable/profile missing"}
    try:
        _launch()
        return {"state": "OK" if _cdp_alive() else "degraded",
                "detail": "launched on loopback CDP" if _cdp_alive()
                else "launch did not open CDP"}
    except Exception as exc:
        return {"state": "failed", "detail": str(exc)[:120]}


# -------------------------------------------------------------- monitor run
def run_hourly(cfg, *, platforms: Optional[List[str]] = None,
               accounts: Optional[List[str]] = None,
               source: str = "live", fixture: Optional[Path] = None,
               dry_run: bool = False,
               notify: bool = True, canary: bool = False,
               store=None) -> Dict[str, Any]:
    """Run one bounded hourly monitor pass.

    - one global overlap lease (timer + manual never overlap);
    - per platform/account a bounded read-only scan that persists screenshot
      bytes into a real report bundle (Packet 8D byte hook);
    - notifications fire ONLY for genuinely committed new-item reports
      (baseline, no-change, and zero-new-item runs are silent); each is
      deduplicated, uses plain-language copy, and auth/degraded accounts get
      one deduplicated plain-language alert;
    - returns typed per-account results; never advances a cursor on failure.

    ``source="live"`` attaches to the user's already-running Chromium on
    loopback CDP (read-only). ``source="fixture"`` (+ ``fixture`` path) is the
    hermetic path used by tests and dry-runs; it never touches a browser.
    """
    platforms = platforms or cfg.get("communications", "monitor", "platforms",
                                     default=["gmail"])
    accounts = accounts or cfg.get("communications", "monitor", "accounts",
                                   default=["default"])
    # Packet 8O: automation is a first-class channel on the ONE policy path.
    # Before any scan the monitor consults the shared DURABLE power-control
    # latch (the same <state_dir>/power-control.json every channel reads): a
    # latched emergency stop / global disable FAILS CLOSED with a typed denial
    # and no browser read ever happens. The monitor runs as its own process, so
    # it cannot rely on an in-memory singleton — it re-reads the durable latch.
    # If the durable control cannot be consulted at all, the scan is refused
    # (fail closed — never scan on unverifiable control state).
    try:
        from .. import power as PW
        _ctl = PW.power_control(cfg)
        _ctl.refresh()
        _pctl = _ctl.state
        if not _pctl.get("persisted"):
            return {"state": "denied", "code": "POWER_DISABLED",
                    "detail": "power-control state is not durable; monitor "
                              "scan refused (fail closed)",
                    "exit_code": 3}
        if _pctl.get("emergency_stop"):
            return {"state": "denied", "code": "EMERGENCY_STOP",
                    "detail": "global emergency stop is latched; monitor scan "
                              "refused (reset required)",
                    "exit_code": 3}
        if _pctl.get("global_disable"):
            return {"state": "denied", "code": "POWER_DISABLED",
                    "detail": "global power disable is set; monitor scan "
                              "refused",
                    "exit_code": 3}
    except Exception as exc:  # noqa: BLE001 — fail closed on any control error
        from ..logging_setup import log_event
        log_event("monitor.power.refused", error=str(exc)[:200])
        return {"state": "denied", "code": "POWER_DISABLED",
                "detail": "power-control unavailable; monitor scan refused",
                "exit_code": 3}
    # Autonomous scheduler parity (OpenClaw): fire due scheduled jobs through
    # the one bounded, idempotent entry point. Dry-runs never execute. Placed
    # before the monitor overlap lease so a long agent turn does not extend
    # it; the run_due DB lease already prevents double-fire across ticks.
    if not dry_run:
        try:
            from .. import schedules as _schedules
            _schedules.autofire(cfg)
        except Exception as exc:  # noqa: BLE001 — never fail the monitor pass
            log_event("monitor.autofire.error", error=str(exc)[:200])
    # Packet 8I: the overlap lease MUST exceed the maximum oneshot runtime
    # (unit TimeoutStartSec) by a safety margin. A configured value below the
    # floor is clamped up and the effective value reported truthfully.
    lease_ttl = float(cfg.get("communications", "monitor", "lease_ttl_s",
                              default=LEASE_MIN_TTL_S))
    lease_clamped = lease_ttl < LEASE_MIN_TTL_S
    if lease_clamped:
        lease_ttl = LEASE_MIN_TTL_S
    st = store or communications_store(cfg)
    owner = f"monitor-{uuid.uuid4().hex[:8]}"
    if not st.acquire_lease(MONITOR_LEASE, owner, ttl_s=lease_ttl):
        return {"state": "degraded", "code": "OVERLAP",
                "detail": "another monitor run holds the hourly lease",
                "exit_code": 1}
    try:
        results: List[Dict[str, Any]] = []
        new_total = 0
        ready_reports: List[str] = []
        all_platforms: List[str] = []
        degraded: List[str] = []
        failed_accounts: List[str] = []
        commits: List[str] = []
        new_commits: List[Dict[str, Any]] = []   # genuinely-new committed reports
        delivery_failures: List[str] = []
        for platform in platforms:
            pl = PLATFORM_NAMES.get(platform)
            if pl is None:
                results.append({"platform": platform, "state": "failed",
                                "detail": "unknown platform"})
                failed_accounts.append(f"{platform}/default")
                continue
            for acct in accounts:
                r = run_scan(cfg, platform, account_scope=acct,
                             source=source, fixture=fixture,
                             live=(source == "live"),
                             dry_run=dry_run, store=st)
                r["platform"] = platform
                r["account_scope"] = acct
                results.append(r)
                rc_new = int(r.get("new_count") or 0)
                new_total += rc_new
                if r.get("commit") in ("new", "baseline") and r.get("report_id"):
                    ready_reports.append(r["report_id"])
                    all_platforms.append(platform)
                    commits.append(r["commit"])
                # Only a genuinely committed NEW-item report may notify:
                # baseline (new_count 0) and no-change never notify.
                if r.get("commit") == "new" and r.get("report_id") and rc_new > 0:
                    new_commits.append({"report_id": r["report_id"],
                                        "platform": platform,
                                        "new_count": rc_new})
                if r.get("state") in ("degraded", "failed") or \
                        r.get("health") in ("degraded", "unavailable",
                                            "auth_required", "failed"):
                    degraded.append(f"{platform}/{acct}")
                if r.get("state") == "failed" or \
                        r.get("health") in ("failed",):
                    failed_accounts.append(f"{platform}/{acct}")
        # Packet 8I: truthful time-config state (fail-closed quiet hours).
        time_cfg = time_config_state(cfg)
        if dry_run:
            hourly_commit = "preview"
        elif commits:
            hourly_commit = "new" if "new" in commits else "baseline"
        else:
            hourly_commit = "no-change"
        out: Dict[str, Any] = {
            "state": "OK",
            "dry_run": dry_run,
            "new_total": new_total,
            "commit": hourly_commit,
            "reports": ready_reports,
            "results": results,
            "platforms": sorted(set(all_platforms)),
            "degraded_accounts": sorted(set(degraded)),
            "failed_accounts": sorted(set(failed_accounts)),
            "lease_ttl_s": round(lease_ttl, 1),
            "lease_clamped": lease_clamped,
            "time_config": {k: time_cfg[k] for k in
                            ("state", "quiet_hours_valid", "timezone_valid",
                             "active", "suppress_voice")
                            if k in time_cfg},
            "canary": bool(canary),
        }
        # Notifications fire ONLY for genuinely committed new-item reports.
        # Baseline, no-change, and zero-new-item runs stay silent. The
        # `notify` flag (from --no-notify) suppresses all delivery.
        notified: List[str] = []
        voice_notify = bool(cfg.get("communications", "monitor", "voice_notify",
                                    default=False))
        desktop_notify = bool(cfg.get("communications", "monitor",
                                      "desktop_notify", default=True))
        quiet = bool(time_cfg["suppress_voice"])
        if notify and new_commits:
            # Group per platform for the plain-language copy. The notification
            # is deduplicated by report id so repeated scans never duplicate it.
            for nc in new_commits:
                rid = nc["report_id"]
                dedupe = f"report:{rid}"
                if st.notification_exists(dedupe, NotificationKind.DESKTOP) and \
                        st.notification_exists(dedupe, NotificationKind.DASHBOARD):
                    notified.append(f"{rid}:deduped")
                    continue
                rc_new = int(nc["new_count"])
                platform_names = [nc["platform"]]
                recs: List[NotificationRecord] = []
                if desktop_notify:
                    res = notify_desktop(cfg, rc_new, platform_names)
                    if res.get("state") != "OK":
                        delivery_failures.append(f"desktop:{rid}")
                    recs.append(NotificationRecord.create(
                        report_id=rid, kind=NotificationKind.DESKTOP,
                        status=NotificationStatus.SENT
                        if res.get("state") == "OK" else NotificationStatus.PENDING,
                        dedupe_key=dedupe, new_count=rc_new,
                        quiet_hours=False, sent_at=time.time()
                        if res.get("state") == "OK" else None))
                recs.append(NotificationRecord.create(
                    report_id=rid, kind=NotificationKind.DASHBOARD,
                    status=NotificationStatus.SENT, dedupe_key=dedupe,
                    new_count=rc_new))
                if voice_notify:
                    vd = f"voice:report:{rid}"
                    if quiet:
                        recs.append(NotificationRecord.create(
                            report_id=rid, kind=NotificationKind.VOICE,
                            status=NotificationStatus.SUPPRESSED_QUIET_HOURS,
                            dedupe_key=vd, new_count=rc_new,
                            quiet_hours=True))
                    else:
                        res = _notify_voice(cfg, rc_new, platform_names)
                        if res.get("state") != "OK":
                            delivery_failures.append(f"voice:{rid}")
                        recs.append(NotificationRecord.create(
                            report_id=rid, kind=NotificationKind.VOICE,
                            status=NotificationStatus.SENT
                            if res.get("state") == "OK"
                            else NotificationStatus.PENDING,
                            dedupe_key=vd, new_count=rc_new,
                            sent_at=time.time()
                            if res.get("state") == "OK" else None))
                for rec in recs:
                    st.record_notification(rec)
                emit_report_ready(cfg, rid, rc_new, platform_names)
                notified.append(rid)
        # Degraded/auth-required accounts: one deduplicated plain-language
        # desktop alert per platform set.
        degraded_notified: List[str] = []
        if notify and degraded and desktop_notify:
            deg_platforms = sorted({d.split("/")[0] for d in degraded})
            dkey = "degraded:" + ",".join(deg_platforms)
            if not st.notification_exists(dkey, NotificationKind.DESKTOP):
                res = notify_degraded(cfg, deg_platforms)
                if res.get("state") != "OK":
                    delivery_failures.append(f"desktop-degraded:{dkey}")
                st.record_notification(NotificationRecord.create(
                    report_id="", kind=NotificationKind.DESKTOP,
                    status=NotificationStatus.SENT
                    if res.get("state") == "OK" else NotificationStatus.PENDING,
                    dedupe_key=dkey, new_count=0, quiet_hours=False,
                    sent_at=time.time()
                    if res.get("state") == "OK" else None))
                degraded_notified.append(dkey)
            else:
                degraded_notified.append(f"{dkey}:deduped")
        # Packet 8I: truthful aggregate state + exit code. An account or a
        # required delivery degrading is never hidden behind a top-level OK.
        if failed_accounts:
            out["state"] = "failed"
            out["code"] = "ACCOUNT_FAILED"
        elif degraded:
            out["state"] = "degraded"
            out["code"] = "ACCOUNT_DEGRADED"
        elif delivery_failures:
            out["state"] = "degraded"
            out["code"] = "DELIVERY_FAILED"
        elif time_cfg["state"] == "config_invalid":
            out["state"] = "degraded"
            out["code"] = "CONFIG_INVALID"
        else:
            out["state"] = "OK"
        out["exit_code"] = 0 if out["state"] == "OK" else 1
        out["notified"] = notified
        out["degraded_notified"] = degraded_notified
        out["quiet_hours_active"] = quiet
        out["delivery_failures"] = sorted(set(delivery_failures))
        if not dry_run:
            # Packet 8I: bounded retry of PENDING notifications before the run
            # finishes; exhausted retries become DEAD_LETTER (never infinite).
            out["retries"] = _retry_pending_notifications(cfg, st)
            # Bounded retention after a successful pass (dry-run default).
            out["retention"] = apply_retention(cfg, st, dry_run=True)
        return out
    finally:
        st.release_lease(MONITOR_LEASE, owner)


def _retry_pending_notifications(cfg, st, *, max_retries: Optional[int] = None,
                                 now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Packet 8I: bounded notification retry with a dead-letter bound.

    Re-attempts ONLY PENDING notifications still inside ``max_retries``. A
    success records ``sent``; an exhausted failure becomes ``dead_letter``.
    Platforms for the retry copy are derived from the store (never a hidden
    hardcode). Returns redacted typed per-attempt records.
    """
    max_retries = int(max_retries if max_retries is not None else
                      cfg.get("communications", "monitor",
                              "notification_max_retries", default=3))
    out: List[Dict[str, Any]] = []
    for row in st.notification_rows_retryable(max_retries, limit=20):
        nid = row.get("notification_id")
        kind = row.get("kind")
        if kind not in (NotificationKind.DESKTOP.value,
                        NotificationKind.VOICE.value):
            continue
        platforms = _retry_platforms(st, row)
        count = int(row.get("new_count") or 0)
        if kind == NotificationKind.DESKTOP.value:
            res = notify_desktop(cfg, count, platforms)
        else:
            res = _notify_voice(cfg, count, platforms)
        status = st.apply_retry_result(nid, res.get("state") == "OK",
                                       max_retries, now=now)
        out.append({"notification_id": nid, "kind": kind,
                    "state": status or "terminal",
                    "ok": res.get("state") == "OK"})
    return out


def _retry_platforms(st, row: Dict[str, Any]) -> List[str]:
    """Platform keys for a retry attempt, derived from durable records.

    Degraded-alert dedupe keys are ``degraded:<p1>,<p2>``; item notifications
    look the report up in the item table. Never a silent hardcoded fallback.
    """
    dkey = str(row.get("dedupe_key") or "")
    if dkey.startswith("degraded:"):
        return [p for p in dkey.split(":", 1)[1].split(",") if p]
    report_id = str(row.get("report_id") or "")
    if report_id:
        try:
            plats = st.report_platforms(report_id)
            if plats:
                return plats
        except Exception:
            pass
    return []


def _notify_voice(cfg, new_count: int, platforms: List[str]) -> Dict[str, Any]:
    """Opt-in quiet-hours-aware voice summary (short, no message text).

    Uses the repository TTS/playback contract: synthesizes the short redacted
    count/platform summary through the configured LOCAL Piper voice (CPU, 0
    VRAM — never a second resident generative model) into a temporary WAV, then
    plays it through the repository ``Playback`` device. Returns:
    - ``OK`` only when synthesis AND playback both succeed;
    - ``unavailable`` when the local voice model / audio device is missing;
    - ``failed`` (typed, bounded reason) on synthesis or playback failure.
    Temporary audio is cleaned up; a bounded timeout prevents hangs.
    """
    summary = _voice_summary(new_count, platforms)
    tts = _build_local_tts(cfg)
    if tts is None:
        return {"state": "unavailable", "reason": "local TTS voice unavailable"}
    out_dir = cfg.state_dir() / "notify"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    wav_path = out_dir / f"notify-{uuid.uuid4().hex[:8]}.wav"
    result: Dict[str, Any] = {}

    def _work() -> None:
        try:
            tts.synthesize(summary, wav_path)
            if not wav_path.exists() or wav_path.stat().st_size == 0:
                result["state"] = "failed"
                result["reason"] = "synthesis produced no audio"
                return
            from ..audio.playback import Playback
            playback = Playback()
            ok = playback.play(wav_path, name="communications-notify")
            if not ok:
                result["state"] = "failed"
                result["reason"] = "playback failed or interrupted"
            else:
                result["state"] = "OK"
        except Exception as exc:  # noqa: BLE001 - typed, bounded reason
            result["state"] = "failed"
            result["reason"] = f"voice notification failed: {str(exc)[:100]}"

    worker = threading.Thread(target=_work, name="notify-voice",
                              daemon=True)
    worker.start()
    worker.join(timeout=_VOICE_NOTIFY_TIMEOUT_S)
    if worker.is_alive():
        return {"state": "failed", "reason": "voice notification timed out"}
    try:
        wav_path.unlink(missing_ok=True)
    except OSError:
        pass
    return result or {"state": "failed",
                      "reason": "voice notification did not complete"}


def _build_local_tts(cfg):
    """Construct the configured LOCAL CPU Piper TTS (0 VRAM, never a second
    resident generative model). Returns None when the voice is unavailable."""
    try:
        from ..tts.piper import PiperTTS
        tts = PiperTTS(
            model_dir=cfg.model_dir(),
            voice=cfg.get("tts", "voice", default="en_US-amy-medium"))
        if not tts.available():
            return None
        return tts
    except Exception:
        return None


# ---------------------------------------------------------------- retention
def apply_retention(cfg, store, *, dry_run: bool = True,
                    retention_days: Optional[int] = None,
                    screenshot_days: Optional[int] = None) -> Dict[str, Any]:
    """Run store + report-bundle retention fail-closed (never deletes until
    backup/index checks pass). Returns a typed result."""
    days = retention_days or int(cfg.get("communications", "monitor",
                                         "retention_days", default=90))
    shot_days = screenshot_days or int(cfg.get("communications", "monitor",
                                               "screenshot_days", default=30))
    out: Dict[str, Any] = {"state": "OK", "dry_run": dry_run}
    try:
        db = store.apply_retention(dry_run=dry_run, retention_seconds={
            "scan_health": days * 86400,
            "item": days * 86400,
            "report_manifest": days * 86400,
            "evidence_asset": shot_days * 86400,
            "notification": days * 86400,
        })
        out["db"] = db
    except Exception as exc:
        out["state"] = "degraded"
        out["db_error"] = str(exc)[:160]
    try:
        rstore = ReportStore(cfg.state_dir() / "reports")
        out["bundles"] = rstore.retention(dry_run=dry_run,
                                          retention_days=days)
    except Exception as exc:
        out["state"] = "degraded"
        out["bundle_error"] = str(exc)[:160]
    return out


# ------------------------------------------------------------------ health
def monitor_health(cfg, store=None) -> Dict[str, Any]:
    """Typed monitor health: CDP precondition, timer/service state, last scan,
    notification backlog, report/evidence counts."""
    st = store or communications_store(cfg)
    out: Dict[str, Any] = {
        "cdp": cdp_precondition(cfg),
        "lease_held": st.lease_ok(MONITOR_LEASE),
        "pending_notifications": st.pending_notifications(),
        "reports": {},
    }
    try:
        from .contracts import Platform
        for platform in Platform:
            latest = st.latest_scan(platform, "default")
            if latest is not None:
                out["reports"][platform.value] = {
                    "status": latest.status.value,
                    "captured_at": latest.captured_at,
                    "report_id": latest.report_id,
                }
    except Exception:
        pass
    # systemd timer/service state (read-only inspection).
    try:
        out["timer"] = _unit_state(TIMER_NAME)
        out["service"] = _unit_state(SERVICE_NAME)
    except Exception:
        pass
    try:
        rstore = ReportStore(cfg.state_dir() / "reports")
        idx = rstore.index(limit=10)
        out["bundle_count"] = len(idx["reports"])
        out["legacy_count"] = len(idx["legacy"])
    except Exception:
        pass
    return out


def _unit_state(name: str) -> Dict[str, Any]:
    try:
        r = subprocess.run(["systemctl", "--user", "is-enabled", name],
                           capture_output=True, text=True, timeout=5)
        enabled = r.stdout.strip()
        r2 = subprocess.run(["systemctl", "--user", "is-active", name],
                            capture_output=True, text=True, timeout=5)
        active = r2.stdout.strip()
        return {"enabled": enabled, "active": active}
    except Exception as exc:
        return {"error": str(exc)[:120]}


# ---------------------------------------------------------- install/rollback
def render_units(repo: Optional[Path] = None) -> Dict[str, str]:
    """Render the service+timer templates with the repository path injected.

    No installation side effect — used by ``monitor install --dry-run`` and the
    verification agent to inspect the exact unit files BEFORE anything is
    enabled.
    """
    repo = repo or Path(__file__).resolve().parents[2]
    if not repo:
        raise ValueError("cannot resolve repository root")
    svc = (Path(repo) / "systemd" / SERVICE_NAME).read_text() if \
        (Path(repo) / "systemd" / SERVICE_NAME).exists() else ""
    tmr = (Path(repo) / "systemd" / TIMER_NAME).read_text() if \
        (Path(repo) / "systemd" / TIMER_NAME).exists() else ""
    if not svc or not tmr:
        raise FileNotFoundError("monitor systemd templates missing")
    return {
        SERVICE_NAME: svc.replace("__PROJECT_DIR__", str(repo)),
        TIMER_NAME: tmr.replace("__PROJECT_DIR__", str(repo)),
    }


def install_monitor(cfg, *, dry_run: bool = True,
                    enable: bool = False) -> Dict[str, Any]:
    """Install the repository-templated oneshot service + hourly timer.

    - ``dry_run`` (default): render + show the diff, write NOTHING;
    - explicit ``enable=True`` (live authorization): atomically install with a
      backup and enable ONLY the timer. Never enables implicitly.
    """
    repo = Path(__file__).resolve().parents[2]
    units = render_units(repo)
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    # A dry-run install writes NOTHING — not even the systemd user unit dir.
    # The directory is only created on a real (non-dry-run) install.
    if not dry_run:
        unit_dir.mkdir(parents=True, exist_ok=True)
    out: Dict[str, Any] = {"state": "dry-run" if dry_run else "installed",
                           "dry_run": dry_run, "enabled": bool(enable),
                           "files": {}}
    for name, content in units.items():
        target = unit_dir / name
        existing = target.read_text() if target.exists() else ""
        out["files"][name] = {
            "path": str(target),
            "exists": target.exists(),
            "diff": existing != content,
            "content": content if dry_run else None,
        }
        if dry_run:
            continue
        backup = None
        if target.exists():
            backup = Path(f"{target}.bak.{int(time.time())}")
            shutil.copy2(target, backup)
        _atomic_write(target, content)
        out["files"][name]["backup"] = str(backup) if backup else None
    if not dry_run:
        if enable:
            r = subprocess.run(["systemctl", "--user", "daemon-reload"],
                               capture_output=True, text=True, timeout=10)
            r2 = subprocess.run(["systemctl", "--user", "enable", TIMER_NAME],
                                capture_output=True, text=True, timeout=10)
            # A freshly enabled timer is NOT running in an already-active user
            # session; start it so the hourly schedule actually engages (without
            # this, the unit stays inactive/dead with Trigger=n/a until the
            # next login). Only the TIMER is started, never the service.
            r3 = subprocess.run(["systemctl", "--user", "start", TIMER_NAME],
                                capture_output=True, text=True, timeout=10)
            out["enable"] = {
                "state": "OK" if (r2.returncode == 0 and r3.returncode == 0)
                else "failed",
                "detail": (r.stderr or r2.stderr or r3.stderr or "")[:160]}
        else:
            out["enable"] = {"state": "not_enabled",
                             "detail": "timer left disabled (explicit --enable "
                                       "required to activate)"}
    return out


def disable_monitor() -> Dict[str, Any]:
    """Stop + disable the timer WITHOUT deleting units/cursors/reports/evidence."""
    out: Dict[str, Any] = {}
    try:
        r = subprocess.run(["systemctl", "--user", "disable", TIMER_NAME],
                           capture_output=True, text=True, timeout=10)
        err = (r.stderr or "")
        # A timer that was never installed is ALREADY disabled: report the
        # rollback as success (idempotent, truthful) rather than a failure.
        if r.returncode != 0 and "does not exist" in err:
            out["disable"] = {"state": "OK",
                              "detail": "timer not installed; nothing to disable"}
        else:
            out["disable"] = {"state": "OK" if r.returncode == 0 else "failed",
                              "detail": err[:160]}
    except Exception as exc:
        out["disable"] = {"state": "failed", "detail": str(exc)[:120]}
    # `disable` alone leaves an ALREADY-RUNNING timer active (it keeps firing);
    # a true rollback must also STOP it so the hourly schedule actually halts.
    try:
        r2 = subprocess.run(["systemctl", "--user", "stop", TIMER_NAME],
                            capture_output=True, text=True, timeout=10)
        err2 = (r2.stderr or "")
        if r2.returncode != 0 and "does not exist" in err2:
            out["stop"] = {"state": "OK",
                           "detail": "timer not installed; nothing to stop"}
        else:
            out["stop"] = {"state": "OK" if r2.returncode == 0 else "failed",
                           "detail": err2[:160]}
    except Exception as exc:
        out["stop"] = {"state": "failed", "detail": str(exc)[:120]}
    return out


def rollback_monitor(cfg) -> Dict[str, Any]:
    """Rollback: stop + disable the timer, keep all state (cursors, reports,
    memory, evidence). Restoration of prior unit files is documented (backup
    paths)."""
    return {"state": "OK", "disable": disable_monitor()}


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp.{uuid.uuid4().hex[:8]}")
    tmp.write_text(content)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


__all__ = [
    "LEASE_MIN_TTL_S", "LEASE_SAFETY_MARGIN_S", "MONITOR_LEASE", "SERVICE_NAME",
    "SERVICE_TIMEOUT_S", "TIMER_NAME", "apply_retention", "cdp_precondition",
    "disable_monitor", "emit_report_ready", "install_monitor",
    "monitor_health", "notify_degraded", "notify_desktop", "optional_launch",
    "quiet_hours_active", "render_units", "rollback_monitor", "run_hourly",
    "time_config_state", "validate_timezone",
]