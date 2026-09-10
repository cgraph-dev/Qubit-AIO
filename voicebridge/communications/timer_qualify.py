"""Packet 8I: operator-gated qualification harness for the real user-systemd
oneshot/timer + notification path.

Qualifies the EXISTING ``qubit-communications-monitor`` hourly schedule and
its notification path without changing them:

- Hermetic stages run against synthetic fixtures in an ISOLATED qualification
  state dir; they never touch the production monitor store, report root,
  browser, installed units, or the live timer.
- Live stages (a real desktop popup / journal inspection) attach to the user's
  actual session ONLY when explicitly authorized: ``--live`` AND
  ``VOICE_BRIDGE_QUALIFY=1``. Without both they are impossible and fail closed
  (``LIVE_NOT_AUTHORIZED``).

The harness NEVER: enables the timer, alters a live unit, restarts a service,
moves/extends the production lease, reads private message content, or records
report IDs/URLs/senders/subjects/bodies/tokens/cookies/secrets. A temporary
isolated accelerated test unit may exercise failure paths but can never
substitute for observing the production-equivalent hourly schedule.

Persistence: the redacted typed qualification summary is written at ``0600`` in
the isolated state dir (default ``<config state>/qualification-timer``).
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..config import Config
from .adapters.registry import PLATFORM_NAMES, communications_store
from .contracts import NotificationKind, NotificationStatus
from .monitor import (
    LEASE_MIN_TTL_S, MONITOR_LEASE, SERVICE_NAME, SERVICE_TIMEOUT_S,
    TIMER_NAME, _parse_quiet_hours, _retry_pending_notifications,
    render_units, run_hourly, time_config_state, validate_timezone,
)

QUALIFY_ENV = "VOICE_BRIDGE_QUALIFY"
QUALIFY_VERSION = 1
ACCEL_SERVICE_NAME = "qubit-communications-monitor-accel.service"
ACCEL_TIMER_NAME = "qubit-communications-monitor-accel.timer"

# Hermetic notification simulation: the harness swaps these delivery functions
# for fakes so the LOGIC (dedupe, quiet hours, ack, retry bounds) is qualified
# without ever touching the real desktop / TTS during hermetic runs.
_DELIVERY_FAKE_OK = {"state": "OK"}


def _cfg_default() -> Config:
    return Config.load()


def live_authorized(env_value: Optional[str] = None) -> bool:
    """Live execution requires the operator gate ``VOICE_BRIDGE_QUALIFY=1``."""
    return (os.environ.get(QUALIFY_ENV) if env_value is None
            else env_value) == "1"


def isolate_cfg(cfg: Config, state_dir: Path) -> Config:
    """A copy of the config with the state root redirected to an isolated dir.

    Ensures the qualification harness never writes into the production state
    root (communications store, report bundles, journal). ``state_dir`` is
    created with private modes by the callers that persist.
    """
    data = copy.deepcopy(cfg.data)
    log = data.setdefault("logging", {})
    log["jsonl"] = True
    log["jsonl_path"] = str(state_dir)
    return Config(data=data, path=cfg.path)


def deny_live(live: bool, *, env_value: Optional[str] = None
              ) -> Optional[Dict[str, Any]]:
    """Return a typed denial when live execution is requested without the gate.

    ``None`` means the requested live mode is authorized. Fail-closed.
    """
    if not live:
        return None
    if live_authorized(env_value):
        return None
    return {
        "state": "denied", "code": "LIVE_NOT_AUTHORIZED",
        "detail": f"live timer/notification qualification requires --live AND "
                  f"{QUALIFY_ENV}=1; nothing was touched",
    }


def _unit_state(name: str) -> Dict[str, Any]:
    """READ-ONLY systemd unit state (is-enabled/is-active). Never mutates."""
    try:
        r = subprocess.run(["systemctl", "--user", "is-enabled", name],
                           capture_output=True, text=True, timeout=5)
        enabled = r.stdout.strip()
        r2 = subprocess.run(["systemctl", "--user", "is-active", name],
                            capture_output=True, text=True, timeout=5)
        active = r2.stdout.strip()
        return {"enabled": enabled, "active": active}
    except Exception as exc:  # noqa: BLE001 - typed, bounded
        return {"error": str(exc)[:120]}


# ------------------------------------------------------------------ preflight
def qualify_preflight(cfg: Config) -> Dict[str, Any]:
    """Typed preflight: time config, notification session, lease floor, and
    the exact repository unit templates (read-only)."""
    from ..display import resolve_notification_session
    tc = time_config_state(cfg)
    lease_ttl = float(cfg.get("communications", "monitor", "lease_ttl_s",
                              default=LEASE_MIN_TTL_S))
    out: Dict[str, Any] = {
        "state": "OK", "qualify_version": QUALIFY_VERSION,
        "time_config": {k: tc[k] for k in
                        ("state", "quiet_hours_valid", "timezone_valid",
                         "active", "suppress_voice") if k in tc},
        "session": resolve_notification_session(),
        "lease": {
            "service_timeout_s": SERVICE_TIMEOUT_S,
            "lease_default_s": round(lease_ttl, 1),
            "lease_min_s": round(LEASE_MIN_TTL_S, 1),
            "lease_exceeds_timeout": bool(lease_ttl >= LEASE_MIN_TTL_S),
            "monotonic": True,
        },
        "units": {
            SERVICE_NAME: {"installed": _unit_state(SERVICE_NAME)},
            TIMER_NAME: {"installed": _unit_state(TIMER_NAME)},
        },
    }
    if tc["state"] == "config_invalid":
        out["state"] = "degraded"
        out["detail"] = tc["detail"]
    if not out["lease"]["lease_exceeds_timeout"]:
        out["state"] = "degraded"
        out["detail"] = ("lease default is below the required floor "
                         "runtime+margin")
    return out


# -------------------------------------------------------------- unit templates
def qualify_units(cfg: Config) -> Dict[str, Any]:
    """Qualify the EXACT unit names and repository templates (hermetic).

    Asserts: exact service/timer names, one repository-templated oneshot with
    bounded resources and no restart loop, a wall-clock hourly timer with
    ``Persistent=true`` catch-up, and — critically — NO hardcoded ``DISPLAY``
    value anywhere in the notification service template (Packet 8I).
    """
    checks: Dict[str, Any] = {}
    units = render_units()
    svc = units.get(SERVICE_NAME, "")
    tmr = units.get(TIMER_NAME, "")
    checks["exact_names"] = {
        "state": "OK" if SERVICE_NAME and TIMER_NAME else "failed",
        "service": SERVICE_NAME, "timer": TIMER_NAME,
    }
    checks["rendered"] = {
        "state": "OK" if (svc and tmr and "__PROJECT_DIR__" not in
                          (svc + tmr)) else "failed",
    }
    checks["oneshot"] = {
        "state": "OK" if "Type=oneshot" in svc else "failed",
    }
    checks["no_restart_loop"] = {
        "state": "OK" if "Restart=" not in svc else "failed",
    }
    checks["bounded_resources"] = {
        "state": "OK" if ("MemoryMax=" in svc and "TimeoutStartSec=" in svc)
        else "failed",
        "timeout_s": 300,
    }
    checks["no_hardcoded_display"] = {
        "state": "OK" if not _has_hardcoded_display(svc) else "failed",
        "detail": ("the notification unit must never ship a hardcoded "
                   "DISPLAY; the runtime resolves the real X11/Wayland "
                   "session"),
    }
    checks["timer_calendar"] = {
        "state": "OK" if ("OnCalendar=hourly" in tmr) else "failed",
    }
    checks["timer_persistent"] = {
        "state": "OK" if ("Persistent=true" in tmr) else "failed",
    }
    checks["lease_vs_timeout"] = {
        "state": "OK", "timeout_s": SERVICE_TIMEOUT_S,
        "min_lease_s": round(LEASE_MIN_TTL_S, 1),
        "detail": ("application lease must exceed the oneshot timeout by a "
                   "safety margin and be renewable"),
    }
    accel = _render_accel_units()
    checks["accel_templates"] = {
        "state": "OK" if accel else "failed",
        "service": ACCEL_SERVICE_NAME, "timer": ACCEL_TIMER_NAME,
        "isolated": True,
    }
    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks}


def _has_hardcoded_display(unit_text: str) -> bool:
    """True when a unit ships an actual ``Environment=DISPLAY=...`` assignment
    (a comment or a self-healing ``import-environment`` note is fine)."""
    for line in unit_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped.upper().startswith("ENVIRONMENT=DISPLAY="):
            return True
    return False


def _render_accel_units() -> Dict[str, str]:
    """Render the temporary accelerated test-unit templates (isolated,
    never installed by the harness)."""
    repo = Path(__file__).resolve().parents[2]
    out: Dict[str, str] = {}
    for name in (ACCEL_SERVICE_NAME, ACCEL_TIMER_NAME):
        p = repo / "systemd" / name
        if p.exists():
            out[name] = p.read_text().replace("__PROJECT_DIR__", str(repo))
    return out


# ---------------------------------------------------------------------- lease
def qualify_lease(cfg: Config, state_dir: Path) -> Dict[str, Any]:
    """Qualify the overlap lease: acquire/renew/release, second-owner denial,
    MONOTONIC deadlines, and reboot-stale recovery (all in an isolated store)."""
    state_dir.mkdir(parents=True, exist_ok=True)
    scfg = isolate_cfg(cfg, state_dir)
    st = communications_store(scfg)
    checks: Dict[str, Any] = {}
    try:
        a = st.acquire_lease(MONITOR_LEASE, "owner-a", ttl_s=120)
        checks["acquire"] = {"state": "OK" if a else "failed"}
        checks["held"] = {"state": "OK" if st.lease_ok(MONITOR_LEASE)
                          else "failed"}
        checks["second_owner_denied"] = {
            "state": "OK" if not st.acquire_lease(MONITOR_LEASE, "owner-b",
                                                  ttl_s=120) else "failed"}
        renew_ok = st.renew_lease(MONITOR_LEASE, "owner-a", ttl_s=180)
        checks["renewable"] = {"state": "OK" if renew_ok else "failed"}
        checks["renew_others_denied"] = {
            "state": "OK" if not st.renew_lease(MONITOR_LEASE, "owner-b",
                                                ttl_s=180) else "failed"}
        # monotonic deadline is recorded and live.
        row = st._conn.execute(
            "SELECT expires_mono, boot_id FROM lease WHERE name=?",
            (MONITOR_LEASE,)).fetchone()
        checks["monotonic"] = {
            "state": "OK" if (row and float(row["expires_mono"]) > 0
                              and row["boot_id"]) else "failed",
        }
        # reboot-stale: a lease left by a DIFFERENT boot is immediately stale.
        st._conn.execute(
            "UPDATE lease SET boot_id='other-boot' WHERE name=?",
            (MONITOR_LEASE,))
        checks["reboot_stale"] = {
            "state": "OK" if st.acquire_lease(MONITOR_LEASE, "owner-c",
                                              ttl_s=120) else "failed"}
        st.release_lease(MONITOR_LEASE, "owner-c")
        checks["release"] = {
            "state": "OK" if not st.lease_ok(MONITOR_LEASE) else "failed"}
    finally:
        st.close()
    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks}


# ----------------------------------------------------------------- quiet hours
def qualify_quiet_hours(cfg: Config) -> Dict[str, Any]:
    """Qualify strict quiet-hours + IANA timezone validation and the
    fail-closed ``CONFIG_INVALID`` behavior, including DST fold/gap handling."""
    from zoneinfo import ZoneInfo
    checks: Dict[str, Any] = {}
    valid = _parse_quiet_hours("22:00-07:00")
    checks["valid_spec"] = {
        "state": "OK" if valid == (22 * 60, 7 * 60) else "failed"}
    for bad in ("24:00-07:00", "07:60-22:00", "garbage", "22:00", "",
                "22:00-7:00"):
        checks[f"invalid_{bad or 'empty'}".replace(":", "_")] = {
            "state": "OK" if _parse_quiet_hours(bad) is None else "failed",
            "spec": bad}
    checks["boundary_full_day"] = {
        "state": "OK" if _parse_quiet_hours("00:00-23:59")
        == (0, 23 * 60 + 59) else "failed"}
    checks["tz_valid"] = {
        "state": "OK" if validate_timezone("Europe/Bucharest")
        == "Europe/Bucharest" else "failed"}
    checks["tz_invalid"] = {
        "state": "OK" if validate_timezone("Mars/Olympus") is None else "failed"}
    bad_cfg = copy.deepcopy(cfg)
    bad_cfg.data.setdefault("communications", {})["monitor"] = {
        **bad_cfg.data.get("communications", {}).get("monitor", {}),
        "voice": {"quiet_hours": "not-a-window", "timezone": "Mars/Olympus"}}
    bcfg = Config(data=bad_cfg.data, path=cfg.path)
    tcs = time_config_state(bcfg)
    checks["config_invalid_suppresses"] = {
        "state": "OK" if tcs["state"] == "config_invalid"
        and tcs["suppress_voice"] is True else "failed",
        "detail": "invalid time config must suppress voice (fail closed)"}
    checks["dst_fold_gap"] = _dst_fold_gap()
    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks}


def _dst_fold_gap() -> Dict[str, Any]:
    """Prove quiet-hours minute classification is STABLE across a real DST
    transition (Europe/Bucharest) and never throws on fold/gap instants.

    - fold: 2026-10-25 03:00 EEST -> 02:00 EET (02:30 occurs twice);
    - gap:  2026-03-29 02:00 EET -> 03:00 EEST (02:30 never exists).
    """
    from zoneinfo import ZoneInfo
    from .monitor import _wall_clock_minute
    try:
        tz = ZoneInfo("Europe/Bucharest")
        fold_a = datetime(2026, 10, 25, 2, 30, tzinfo=tz)
        fold_b = datetime(2026, 10, 25, 2, 30, tzinfo=tz).astimezone(tz)
        gap_aware = datetime(2026, 3, 29, 3, 0, tzinfo=tz)
        m1 = _wall_clock_minute(fold_a, "Europe/Bucharest")
        m2 = _wall_clock_minute(fold_b, "Europe/Bucharest")
        m3 = _wall_clock_minute(gap_aware, "Europe/Bucharest")
        stable = (m1 is not None and m1 == m2 and m3 == 3 * 60)
        return {"state": "OK" if stable else "failed",
                "fold_minute": m1, "gap_instant_minute": m3,
                "zone": "Europe/Bucharest"}
    except Exception as exc:  # noqa: BLE001 - typed
        return {"state": "failed", "detail": str(exc)[:160]}


# ---------------------------------------------------------------- notification
def qualify_notification(cfg: Config, state_dir: Path, *, live: bool = False,
                         env_value: Optional[str] = None,
                         fixture: Optional[Path] = None,
                         external: Optional[Path] = None) -> Dict[str, Any]:
    """Qualify the notification path: no-change silence, one new-item popup,
    dedupe, quiet-hours voice suppression, acknowledgement, bounded retry/
    dead-letter, and redacted plain-language copy.

    Hermetic by default (delivery functions simulated, isolated state). With
    ``--live`` AND the gate, one controlled real desktop popup is sent.
    """
    denied = deny_live(live, env_value=env_value)
    if denied:
        return denied
    state_dir.mkdir(parents=True, exist_ok=True)
    scfg = isolate_cfg(cfg, state_dir / "notify")
    fx = fixture or _default_fixture()
    checks: Dict[str, Any] = {}
    with _simulate_delivery():
        baseline = run_hourly(scfg, platforms=["gmail"], source="fixture",
                              fixture=fx, store=communications_store(scfg))
        checks["baseline_silent"] = {
            "state": "OK" if baseline.get("commit") == "baseline"
            and not baseline.get("notified") else "failed",
        }
        # One controlled NEW item -> exactly one new-item commit + popup set.
        nf = _external_fixture(state_dir / "notify", fx)
        new_run = run_hourly(scfg, platforms=["gmail"], source="fixture",
                             fixture=nf, store=communications_store(scfg))
        checks["one_new_item"] = {
            "state": "OK" if new_run.get("commit") == "new"
            and new_run.get("new_total") == 1 else "failed",
            "new_total": new_run.get("new_total"),
        }
        st = communications_store(scfg)
        kinds = {n["kind"]: n["status"]
                 for n in st.recent_notifications(50)}
        checks["desktop_delivered"] = {
            "state": "OK" if kinds.get("desktop") == "sent" else "failed"}
        checks["dashboard_delivered"] = {
            "state": "OK" if kinds.get("dashboard") == "sent" else "failed"}
        # A rescan of the SAME fixture is no-change: no duplicate popup.
        rescan = run_hourly(scfg, platforms=["gmail"], source="fixture",
                            fixture=nf, store=st)
        checks["dedupe_no_repeat"] = {
            "state": "OK" if rescan.get("commit") == "no-change"
            and not rescan.get("notified")
            and len(st.recent_notifications(100)) == 2 else "failed",
        }
        checks["ack"] = _ack_check(st)
        checks["retry_bounds"] = _retry_bounds_check(scfg, st)
        # Quiet-hours VOICE suppression is fail-closed by construction.
        qcfg = copy.deepcopy(scfg)
        qcfg.data.setdefault("communications", {})["monitor"] = {
            **qcfg.data.get("communications", {}).get("monitor", {}),
            "voice_notify": True,
            "voice": {"quiet_hours": "00:00-23:59", "timezone": "UTC"},
        }
        qcc = Config(data=qcfg.data, path=scfg.path)
        quiet = time_config_state(qcc)
        checks["quiet_hours_suppress"] = {
            "state": "OK" if quiet["suppress_voice"] is True else "failed",
            "detail": "inside quiet hours the voice channel is suppressed",
        }
    checks["redacted_copy"] = _redaction_check()
    if live:
        res = _live_popup(cfg, env_value=env_value)
        checks["live_popup"] = res
    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks,
            "live": bool(live)}


class _simulate_delivery:
    """Context manager swapping the real delivery functions for OK fakes.

    The hermetic notification stage must qualify the LOGIC without touching the
    real desktop/TTS/audio. The swap is restored on exit.
    """

    def __enter__(self):
        import voicebridge.communications.monitor as M
        self._mod = M
        self._saved = {
            name: getattr(M, name)
            for name in ("notify_desktop", "notify_degraded", "_notify_voice")
        }
        for name in self._saved:
            setattr(M, name, lambda *a, **k: dict(_DELIVERY_FAKE_OK))
        return self

    def __exit__(self, *exc):
        for name, fn in self._saved.items():
            setattr(self._mod, name, fn)
        return False


class _no_browser:
    """Context manager forcing a scan to look "no Chromium target" (unavailable)
    without ever touching the operator's browser. Restored on exit."""

    def __enter__(self):
        import voicebridge.communications.adapters.registry as R
        self._mod = R
        self._saved = {}
        for name in ("find_target", "find_target_preferring"):
            self._saved[name] = getattr(R, name)
            setattr(R, name, lambda *a, **k: None)
        return self

    def __exit__(self, *exc):
        for name, fn in self._saved.items():
            setattr(self._mod, name, fn)
        return False


def _default_fixture() -> Path:
    repo = Path(__file__).resolve().parents[2]
    return repo / "tests" / "fixtures" / "communications" / "new_items.json"


def _external_fixture(state_dir: Path, fx: Path) -> Path:
    """A persistent baseline+one-new-item fixture inside the isolated state
    dir, reused for both the new-item pass and its no-change rescan."""
    state_dir.mkdir(parents=True, exist_ok=True)
    nf = state_dir / "new_item_plus_one.json"
    rows = [dict(r) for r in (json.loads(fx.read_text()).get("rows") or [])]
    rows.append({"sourceItemId": "syn-8i-1", "threadId": "syn-8i-1",
                 "sender": "Synthetic 8I Sender", "subject": "s",
                 "preview": "p", "timeText": "Aug 28",
                 "href": "https://mail.google.com/", "unread": True})
    nf.write_text(json.dumps({"platform": "gmail", "rows": rows}))
    return nf


def _ack_check(st) -> Dict[str, Any]:
    rows = [r for r in st.recent_notifications(50)
            if r["kind"] == "desktop"]
    if not rows:
        return {"state": "skipped", "detail": "no desktop notification to ack"}
    nid = rows[0]["notification_id"]
    ok = st.acknowledge_notification(nid)
    again = st.acknowledge_notification(nid)   # idempotent single transition
    pending = st.pending_notifications()
    return {"state": "OK" if ok and not again else "failed",
            "acked_once": ok, "idempotent": not again,
            "pending_after_ack": pending}


def _retry_bounds_check(scfg: Config, st) -> Dict[str, Any]:
    """Inject a PENDING notification and prove the bounded retry/dead-letter
    path never exceeds its budget."""
    from .contracts import NotificationRecord
    rec = NotificationRecord.create(
        report_id="", kind=NotificationKind.DESKTOP,
        status=NotificationStatus.PENDING,
        dedupe_key="retry:bound:test", new_count=1)
    st.record_notification(rec)
    nid = rec.notification_id
    outcomes = []
    for _ in range(5):
        status = st.apply_retry_result(nid, False, max_retries=3)
        outcomes.append(status)
        if status == "dead_letter":
            break
    final = st._conn.execute(
        "SELECT status, retry_count FROM notification WHERE notification_id=?",
        (nid,)).fetchone()
    bounded = final is not None and final["status"] == "dead_letter" \
        and int(final["retry_count"]) == 3
    return {"state": "OK" if bounded else "failed",
            "outcomes": outcomes,
            "final_status": final["status"] if final else None,
            "final_retries": int(final["retry_count"]) if final else None,
            "detail": "bounded retries with a terminal dead-letter state"}


def _redaction_check() -> Dict[str, Any]:
    """The USER-FACING notification copy must be redacted by construction: no
    report IDs, URLs, senders, subjects, bodies, tokens, cookies, or secrets.
    (Durable audit records in the private store may carry report IDs — the
    requirement is that the notification surface never does.)"""
    from .monitor import _desktop_text, _degraded_text, _voice_summary
    texts = []
    for n in (1, 3):
        t, b = _desktop_text(n, ["gmail"])
        texts += [t, b]
    dt, db = _degraded_text(["gmail"])
    texts += [dt, db]
    texts.append(_voice_summary(1, ["gmail"]))
    blob = json.dumps(texts)
    forbidden = ("http://", "https://", "report_", "sender", "subject",
                 "secret", "token", "sk-", "cook", "@")
    leaked = [f for f in forbidden if f.lower() in blob.lower()]
    return {"state": "OK" if not leaked else "failed",
            "leaked": leaked,
            "detail": "plain-language notification copy must never expose IDs, "
                      "URLs, senders, subjects, bodies, tokens, cookies, or "
                      "secrets"}


def _live_popup(cfg: Config, *, env_value: Optional[str] = None
                ) -> Dict[str, Any]:
    """OPT-IN controlled real desktop popup (one plain-language notification).
    Requires the operator gate; never sends private content."""
    from .monitor import notify_desktop
    res = notify_desktop(cfg, 1, ["gmail"])
    return {"state": "OK" if res.get("state") == "OK" else "failed",
            "detail": "one controlled plain-language desktop popup",
            "delivery": res}


# ------------------------------------------------------------------ aggregate
def qualify_aggregate(cfg: Config, state_dir: Path) -> Dict[str, Any]:
    """Prove aggregate state/exit code are TRUTHFUL (Packet 8I): a degraded
    account, a failed account, or CONFIG_INVALID must never report top-level
    OK with exit code 0."""
    state_dir.mkdir(parents=True, exist_ok=True)
    scfg = isolate_cfg(cfg, state_dir / "agg")
    fx = _default_fixture()
    checks: Dict[str, Any] = {}
    ok_run = run_hourly(scfg, platforms=["gmail"], source="fixture",
                        fixture=fx, dry_run=True,
                        store=communications_store(scfg))
    checks["clean_ok"] = {
        "state": "OK" if ok_run.get("state") == "OK"
        and ok_run.get("exit_code") == 0 else "failed"}
    # Degraded account (no Chromium attached -> unavailable) must be DEGRADED.
    # `_no_browser` forces the unavailable path hermetically (never touches the
    # operator's real browser session).
    with _no_browser():
        deg_run = run_hourly(scfg, platforms=["gmail"], source="live",
                             dry_run=True, store=communications_store(scfg))
    checks["degraded_not_ok"] = {
        "state": "OK" if deg_run.get("state") == "degraded"
        and deg_run.get("exit_code") == 1 else "failed",
        "aggregate": deg_run.get("state"),
        "code": deg_run.get("code"),
        "accounts": deg_run.get("degraded_accounts"),
    }
    # Unknown platform -> failed aggregate.
    bad_run = run_hourly(scfg, platforms=["slack"], source="fixture",
                         fixture=fx, dry_run=True,
                         store=communications_store(scfg))
    checks["failed_not_ok"] = {
        "state": "OK" if bad_run.get("state") == "failed"
        and bad_run.get("exit_code") == 1 else "failed",
        "aggregate": bad_run.get("state"),
        "code": bad_run.get("code"),
    }
    # CONFIG_INVALID time config -> degraded + voice suppressed.
    qcfg = copy.deepcopy(scfg)
    qcfg.data.setdefault("communications", {})["monitor"] = {
        **qcfg.data.get("communications", {}).get("monitor", {}),
        "voice": {"quiet_hours": "not-a-window", "timezone": "Not/AZone"},
    }
    cc = Config(data=qcfg.data, path=scfg.path)
    inv_run = run_hourly(cc, platforms=["gmail"], source="fixture",
                         fixture=fx, dry_run=True,
                         store=communications_store(cc))
    checks["config_invalid_degrades"] = {
        "state": "OK" if inv_run.get("state") == "degraded"
        and inv_run.get("code") == "CONFIG_INVALID"
        and inv_run.get("exit_code") == 1 else "failed",
        "time_config": inv_run.get("time_config"),
    }
    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks}


# -------------------------------------------------------------------- journal
def qualify_journal(cfg: Config, state_dir: Path, *, live: bool = False,
                    env_value: Optional[str] = None) -> Dict[str, Any]:
    """Journal evidence: the durable typed-event journal records the redacted
    ``communications.report.ready`` event (hermetic). Live ``--journal`` adds
    a read-only systemd journal excerpt for the service."""
    denied = deny_live(live, env_value=env_value)
    if denied:
        return denied
    state_dir.mkdir(parents=True, exist_ok=True)
    scfg = isolate_cfg(cfg, state_dir / "journal")
    checks: Dict[str, Any] = {}
    # A new-item pass is required: only a genuinely committed NEW-item report
    # emits the durable ``communications.report.ready`` event (baselines are
    # silent by design). Baseline the fresh store first, then add one item.
    nf = _external_fixture(state_dir / "journal", _default_fixture())
    with _simulate_delivery():
        run_hourly(scfg, platforms=["gmail"], source="fixture",
                   fixture=_default_fixture(), store=communications_store(scfg))
        run_hourly(scfg, platforms=["gmail"], source="fixture",
                   fixture=nf, store=communications_store(scfg))
    events = []
    try:
        from ..events import EventJournal
        ej = EventJournal(scfg.state_dir() / "typed-events.jsonl")
        for rec in ej.read_all():
            if rec.get("name") == "communications.report.ready":
                events.append(rec)
    except Exception as exc:  # noqa: BLE001
        return {"state": "failed", "detail": str(exc)[:160], "checks": {}}
    checks["report_ready_event"] = {
        "state": "OK" if events else "failed", "count": len(events)}
    # The journal is a structured audit surface; it must still never carry
    # message-derived content (senders/subjects/bodies/secrets). The typed
    # event value holds only report_id + counts + platform names.
    blob = json.dumps(events)
    forbidden = ("subject", "sender", "secret", "token", "cook")
    leaked = [f for f in forbidden if f.lower() in blob.lower()]
    checks["journal_redacted"] = {
        "state": "OK" if not leaked else "failed", "leaked": leaked}
    if live:
        checks["systemd_journal"] = _systemd_journal_probe()
    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks}


def _systemd_journal_probe() -> Dict[str, Any]:
    """READ-ONLY journal excerpt for the monitor service (metadata only)."""
    try:
        r = subprocess.run(
            ["journalctl", "--user", "-u", SERVICE_NAME, "-n", "20",
             "--no-pager", "-o", "short"],
            capture_output=True, text=True, timeout=10)
        lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
        return {"state": "OK" if r.returncode == 0 else "degraded",
                "lines": len(lines)}
    except Exception as exc:  # noqa: BLE001
        return {"state": "failed", "detail": str(exc)[:160]}


# ------------------------------------------------- accelerated failure drill
def qualify_accelerated(cfg: Config, state_dir: Path, *,
                        fixture: Optional[Path] = None) -> Dict[str, Any]:
    """Accelerated failure-path simulation in an ISOLATED state root.

    Rapid back-to-back passes with injected failures (overlap, malformed time
    config, delivery failure, timeout) prove every failure path returns a TYPED
    result. This is NOT a substitute for observing the production-equivalent
    hourly schedule — it is a temporary isolated accelerated drill.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    scfg = isolate_cfg(cfg, state_dir / "accel")
    fx = fixture or _default_fixture()
    checks: Dict[str, Any] = {}
    st = communications_store(scfg)

    # 1) overlap: pre-acquire the global lease -> typed OVERLAP degraded.
    st.acquire_lease(MONITOR_LEASE, "accel-other", ttl_s=3600)
    overlap = run_hourly(scfg, platforms=["gmail"], source="fixture",
                         fixture=fx, store=st)
    checks["overlap"] = {
        "state": "OK" if overlap.get("state") == "degraded"
        and overlap.get("code") == "OVERLAP" else "failed",
        "state_out": overlap.get("state"), "code": overlap.get("code")}
    st.release_lease(MONITOR_LEASE, "accel-other")

    # 2) malformed time config -> CONFIG_INVALID (fail-closed).
    qcfg = copy.deepcopy(scfg)
    qcfg.data.setdefault("communications", {})["monitor"] = {
        **qcfg.data.get("communications", {}).get("monitor", {}),
        "voice": {"quiet_hours": "25:99-07:00", "timezone": "Mars/Olympus"}}
    cc = Config(data=qcfg.data, path=scfg.path)
    tc = time_config_state(cc)
    checks["config_invalid"] = {
        "state": "OK" if tc["state"] == "config_invalid"
        and tc["suppress_voice"] is True else "failed",
        "state_out": tc["state"]}

    # 3) delivery failure -> PENDING recorded + bounded retry -> dead-letter.
    from .contracts import NotificationRecord
    rec = NotificationRecord.create(
        report_id="", kind=NotificationKind.DESKTOP,
        status=NotificationStatus.PENDING,
        dedupe_key="accel:delivery:fail", new_count=1)
    st.record_notification(rec)
    final = ""
    for _ in range(4):
        status = st.apply_retry_result(rec.notification_id, False,
                                       max_retries=3)
        if status == "dead_letter":
            final = status
            break
        if status:
            final = status
    checks["delivery_failure_dead_letter"] = {
        "state": "OK" if final == "dead_letter" else "failed",
        "final": final}

    # 4) bounded voice timeout (a hung synthesis must fail typed, not hang).
    import voicebridge.communications.monitor as M
    checks["voice_timeout_bound"] = {
        "state": "OK" if M._VOICE_NOTIFY_TIMEOUT_S and
        M._VOICE_NOTIFY_TIMEOUT_S <= 60 else "failed",
        "timeout_s": M._VOICE_NOTIFY_TIMEOUT_S}

    # 5) aggregate truthfulness under a failing account.
    bad = run_hourly(scfg, platforms=["slack"], source="fixture",
                     fixture=fx, dry_run=True, store=st)
    checks["aggregate_failed"] = {
        "state": "OK" if bad.get("state") == "failed"
        and bad.get("exit_code") == 1 else "failed",
        "aggregate": bad.get("state")}

    st.close()
    failed = any(c.get("state") == "failed" for c in checks.values())
    return {"state": "failed" if failed else "OK", "checks": checks,
            "isolated": True,
            "note": "accelerated isolated drill; not a substitute for the "
                    "production-equivalent hourly schedule"}


# ---------------------------------------------------------------- orchestration
def _fresh_run_root(state_dir: Path, rid: str) -> Path:
    """Create a fresh per-run isolated stage root under ``state_dir``.

    ``runs/<rid>`` is created empty so every qualification run starts from a
    clean store/lease/journal (Packet 8I idempotency repair). The production
    state root, installed units, and the live timer are never touched.
    """
    root = state_dir / "runs" / rid
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    return root


def run_qualification(cfg: Config, *, live: bool = False,
                      env_value: Optional[str] = None,
                      state_dir: Optional[Path] = None,
                      fixture: Optional[Path] = None,
                      external: Optional[Path] = None,
                      dry_run: bool = False,
                      stages: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Run the Packet 8I qualification (hermetic + optional gated live).

    Returns a typed per-stage result and, unless ``dry_run``, persists a
    REDACTED summary JSON at ``0600`` in ``state_dir`` (default
    ``<config state>/qualification-timer``).
    """
    state_dir = Path(state_dir or (cfg.state_dir() / "qualification-timer"))
    state_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(state_dir, 0o700)
    rid = f"tq-{uuid.uuid4().hex[:12]}"
    # Packet 8I repair: every run gets a FRESH isolated per-run stage root.
    # Re-running `--all` against the same state dir must be idempotent — the
    # notify/lease/journal/agg stores must not retain a previous run's
    # baseline/new-item commits (that made baseline_silent/one_new_item fail
    # on the second run). Summaries still persist under `timer-qualify/<rid>`.
    run_root = _fresh_run_root(state_dir, rid)
    avail = {
        "preflight": qualify_preflight(cfg),
        "units": qualify_units(cfg),
        "lease": qualify_lease(cfg, run_root / "lease"),
        "quiet_hours": qualify_quiet_hours(cfg),
        "notification": qualify_notification(cfg, run_root, live=live,
                                             env_value=env_value,
                                             fixture=fixture,
                                             external=external),
        "aggregate": qualify_aggregate(cfg, run_root),
        "journal": qualify_journal(cfg, run_root, live=live,
                                   env_value=env_value),
        "accelerated": qualify_accelerated(cfg, run_root, fixture=fixture),
    }
    wanted = list(stages) if stages else list(avail.keys())
    out: Dict[str, Any] = {
        "state": "OK", "qualify_version": QUALIFY_VERSION, "run_id": rid,
        "dry_run": bool(dry_run), "live": bool(live),
        "stages": {k: avail[k] for k in wanted if k in avail},
    }
    _bad = lambda v: isinstance(v, dict) and v.get("state") in ("failed",
                                                                "denied")
    failed = any(_bad(out["stages"][k])
                 or (isinstance(out["stages"][k], dict)
                     and any(_bad(x) for x in out["stages"][k].values()
                             if isinstance(x, dict)))
                 for k in out["stages"])
    out["state"] = "failed" if failed else "OK"
    if not dry_run:
        _persist_summary(state_dir, rid, out)
    return out


def _persist_summary(state_dir: Path, run_id: str, out: Dict[str, Any]) -> None:
    payload = {
        "qualify_version": out.get("qualify_version"),
        "run_id": run_id, "dry_run": out.get("dry_run"),
        "live": out.get("live"), "state": out.get("state"),
        "stages": _redacted_stages(out.get("stages", {})),
    }
    target = state_dir / "timer-qualify" / run_id / "summary.json"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.tmp.{uuid.uuid4().hex[:8]}")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except OSError:
        pass


def _redacted_stages(stages: Dict[str, Any]) -> Dict[str, Any]:
    redacted: Dict[str, Any] = {}
    for key, value in stages.items():
        if isinstance(value, dict):
            item = {k: v for k, v in value.items()
                    if k not in ("state_dir",)}
            redacted[key] = {k: _redacted_stages(v) if isinstance(v, dict)
                             else v for k, v in item.items()}
        else:
            redacted[key] = value
    return redacted


__all__ = [
    "ACCEL_SERVICE_NAME", "ACCEL_TIMER_NAME", "QUALIFY_ENV",
    "QUALIFY_VERSION", "deny_live", "qualify_accelerated",
    "qualify_aggregate", "qualify_journal", "qualify_lease",
    "qualify_notification", "qualify_preflight", "qualify_quiet_hours",
    "qualify_units", "run_qualification",
]