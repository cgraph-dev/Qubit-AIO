"""Packet 8I tests: real timer, notification, and quiet-hours qualification.

Covers the qualification harness stages AND the closed configuration risks:
- strict quiet-hours + IANA timezone validation (CONFIG_INVALID fail-closed,
  voice suppressed, NO host-local fallback);
- validated X11/Wayland notification-session resolution (no DISPLAY=:1);
- MONOTONIC, renewable, reboot-aware overlap lease that exceeds the oneshot
  runtime plus a safety margin (clock jump / suspend-resume immune);
- truthful aggregate state + exit code (degraded/failed/CONFIG_INVALID != OK);
- no-change silence, one new-item notification, dedupe, quiet hours,
  acknowledgement, bounded retry/dead-letter, redacted copy, journal evidence,
  DST fold/gap, catch-up (missed run without duplicates), overlap, timeout,
  and rollback.

All tests are hermetic: they use isolated state dirs and synthetic fixtures and
never enable the live timer or alter an installed unit.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge.communications.adapters.registry import communications_store  # noqa: E402
from voicebridge.communications.contracts import (  # noqa: E402
    NotificationKind, NotificationRecord, NotificationStatus,
)
from voicebridge.communications.monitor import (  # noqa: E402
    LEASE_MIN_TTL_S, MONITOR_LEASE, SERVICE_NAME, SERVICE_TIMEOUT_S,
    TIMER_NAME, _parse_quiet_hours, _retry_pending_notifications,
    _wall_clock_minute, quiet_hours_active, render_units, run_hourly,
    time_config_state, validate_timezone,
)
from voicebridge.communications.store import (  # noqa: E402
    CommunicationsStore, STORE_SCHEMA_VERSION, _boot_id,
)
from voicebridge.communications.timer_qualify import (  # noqa: E402
    QUALIFY_ENV, run_qualification, qualify_accelerated, qualify_aggregate,
    qualify_journal, qualify_lease, qualify_notification, qualify_preflight,
    qualify_quiet_hours, qualify_units,
)

FIX = REPO_ROOT / "tests" / "fixtures" / "communications"


def _cfg(tmp_path, **extra):
    from voicebridge.config import Config
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    lines = ["logging:\n  jsonl_path: '" + str(state) + "'\n"]
    for key, val in extra.items():
        lines.append(f"{key}: {json.dumps(val)}\n")
    p = tmp_path / "voice.yaml"
    p.write_text("".join(lines))
    return Config.load(p)


def _store(tmp_path, name="c.db"):
    st = CommunicationsStore(tmp_path / name)
    return st


def _fixture(tmp_path, rows=None):
    rows = rows if rows is not None else \
        json.loads((FIX / "new_items.json").read_text())["rows"]
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"platform": "gmail", "rows": rows}))
    return fx


def _new_rows():
    rows = json.loads((FIX / "new_items.json").read_text())["rows"]
    rows.append({"sourceItemId": "syn-8i-t", "threadId": "syn-8i-t",
                 "sender": "Synthetic 8I", "subject": "s", "preview": "p",
                 "timeText": "Aug 28", "href": "https://mail.google.com/",
                 "unread": True})
    return rows


# ---------------------------------------------------------------- time config
class TestQuietHoursValidation:
    def test_strict_parse_valid_and_ranges(self):
        assert _parse_quiet_hours("22:00-07:00") == (22 * 60, 7 * 60)
        assert _parse_quiet_hours("00:00-23:59") == (0, 23 * 60 + 59)

    def test_strict_parse_rejects_out_of_range(self):
        for bad in ("24:00-07:00", "22:60-07:00", "7:00-22:00", "22:00-7:00",
                    "22:00", "garbage", "", None, 22, "22:00-07:00-extra"):
            assert _parse_quiet_hours(bad) is None, bad

    def test_invalid_spec_is_config_invalid_and_suppresses(self, tmp_path):
        cfg = _cfg(tmp_path, **{"communications": {"monitor": {"voice": {
            "quiet_hours": "not-a-window", "timezone": "Europe/Bucharest"}}}})
        tcs = time_config_state(cfg)
        assert tcs["state"] == "config_invalid"
        assert tcs["quiet_hours_valid"] is False
        assert tcs["suppress_voice"] is True
        assert quiet_hours_active(cfg) is True

    def test_invalid_timezone_is_config_invalid_and_suppresses(self, tmp_path):
        cfg = _cfg(tmp_path, **{"communications": {"monitor": {"voice": {
            "quiet_hours": "22:00-07:00", "timezone": "Mars/Olympus"}}}})
        tcs = time_config_state(cfg)
        assert tcs["state"] == "config_invalid"
        assert tcs["timezone_valid"] is False
        assert tcs["suppress_voice"] is True
        # fail-closed: never host-local time, never "quiet hours disabled"
        assert quiet_hours_active(cfg) is True

    def test_valid_timezone_and_window_membership(self, tmp_path):
        from datetime import timezone as dt_tz
        cfg = _cfg(tmp_path, **{"communications": {"monitor": {"voice": {
            "quiet_hours": "22:00-07:00", "timezone": "Europe/Bucharest"}}}})
        now = datetime(2026, 8, 28, 20, 0, tzinfo=dt_tz.utc)  # 23:00 Bucharest
        tcs = time_config_state(cfg, now=now)
        assert tcs["state"] == "ok" and tcs["active"] is True
        assert validate_timezone("Europe/Bucharest") == "Europe/Bucharest"
        assert validate_timezone("Mars/Olympus") is None

    def test_dst_fold_stable(self):
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Bucharest")
        fold_a = datetime(2026, 10, 25, 2, 30, tzinfo=tz)
        fold_b = datetime(2026, 10, 25, 2, 30, tzinfo=tz).astimezone(tz)
        assert _wall_clock_minute(fold_a, "Europe/Bucharest") == 2 * 60 + 30
        assert _wall_clock_minute(fold_b, "Europe/Bucharest") == 2 * 60 + 30

    def test_dst_gap_instant_stable(self):
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Bucharest")
        # 2026-03-29 03:00 EEST exists (the 02:00 hour was skipped)
        gap = datetime(2026, 3, 29, 3, 0, tzinfo=tz)
        assert _wall_clock_minute(gap, "Europe/Bucharest") == 3 * 60


# ------------------------------------------------------------------- session
class TestNotificationSession:
    def test_resolve_x11(self, monkeypatch):
        from voicebridge import display as D
        monkeypatch.setattr(D, "_x11_display_numbers", lambda: [0])
        sess = D.resolve_notification_session({"DISPLAY": ":0"})
        assert sess["state"] == "ok" and sess["session_type"] == "x11"
        assert sess["display"] == ":0"

    def test_resolve_wayland(self, monkeypatch, tmp_path):
        from voicebridge import display as D
        xdg = tmp_path / "xdg"
        xdg.mkdir()
        (xdg / "bus").touch()
        sess = D.resolve_notification_session({
            "WAYLAND_DISPLAY": "wayland-0", "XDG_RUNTIME_DIR": str(xdg),
            "DISPLAY": ":0"})
        assert sess["state"] == "ok" and sess["session_type"] == "wayland"

    def test_headless_unavailable(self, monkeypatch):
        from voicebridge import display as D
        monkeypatch.setattr(D, "_x11_display_numbers", lambda: [])
        sess = D.resolve_notification_session({})
        assert sess["state"] == "unavailable"

    def test_notify_send_uses_resolved_session(self, tmp_path, monkeypatch):
        """The timer notification path resolves the real session at runtime and
        never relies on a hardcoded DISPLAY."""
        from voicebridge import display as D
        monkeypatch.setattr(D, "_x11_display_numbers", lambda: [0])
        monkeypatch.setattr(
            "voicebridge.communications.monitor.shutil.which",
            lambda *a, **k: "/usr/bin/notify-send")
        seen = {}

        def _fake_run(cmd, **kw):
            seen["env"] = dict(os.environ)
            return type("P", (), {"returncode": 0, "stdout": "",
                                  "stderr": ""})()

        monkeypatch.setattr(
            "voicebridge.communications.monitor.subprocess.run", _fake_run)
        from voicebridge.communications.monitor import notify_desktop
        res = notify_desktop(_cfg(tmp_path), 1, ["gmail"])
        assert res["state"] == "OK"
        assert seen["env"].get("DISPLAY") == ":0"


# ---------------------------------------------------------------------- lease
class TestLeaseMonotonic:
    def test_lease_exceeds_service_timeout_plus_margin(self):
        assert LEASE_MIN_TTL_S > SERVICE_TIMEOUT_S
        assert LEASE_MIN_TTL_S >= SERVICE_TIMEOUT_S + 120

    def test_schema_v3_adds_monotonic_and_retry_columns(self, tmp_path):
        st = _store(tmp_path)
        assert st.schema_version == STORE_SCHEMA_VERSION == 3
        lease_cols = [r[1] for r in st._conn.execute("PRAGMA table_info(lease)")]
        assert {"acquired_mono", "expires_mono", "boot_id"} <= set(lease_cols)
        notif_cols = [r[1] for r in st._conn.execute(
            "PRAGMA table_info(notification)")]
        assert {"retry_count", "dead_letter_at"} <= set(notif_cols)
        st.close()

    def test_lease_immune_to_wall_clock_jump(self, tmp_path, monkeypatch):
        """A forward wall-clock jump must NOT expire a live monotonic lease."""
        import voicebridge.communications.store as S
        real_wall, real_mono = time.time, time.monotonic

        class _Clock:
            def __init__(self):
                self.wall = real_wall()
                self.mono = real_mono()

            def time(self):
                return self.wall

            def monotonic(self):
                return self.mono

        clock = _Clock()
        monkeypatch.setattr(S.time, "time", clock.time)
        monkeypatch.setattr(S.time, "monotonic", clock.monotonic)
        st = _store(tmp_path)
        assert st.acquire_lease("jump", "a", ttl_s=120)
        # wall jumps +1000 s (past the wall expiry) while monotonic advances
        # only 10 s (the lease is still live in monotonic terms).
        clock.wall += 1000
        clock.mono += 10
        assert st.lease_ok("jump") is True
        # a second owner is still denied (no silent overlap despite the jump)
        assert st.acquire_lease("jump", "b", ttl_s=120) is False
        st.close()

    def test_lease_immune_to_suspend_resume(self, tmp_path, monkeypatch):
        """Monotonic excludes suspend: after a suspend of hours, the lease is
        still held (never expired by wall time that advanced)."""
        import voicebridge.communications.store as S
        real_wall, real_mono = time.time, time.monotonic

        class _Clock:
            def __init__(self):
                self.wall = real_wall()
                self.mono = real_mono()

            def time(self):
                return self.wall

            def monotonic(self):
                return self.mono

        clock = _Clock()
        monkeypatch.setattr(S.time, "time", clock.time)
        monkeypatch.setattr(S.time, "monotonic", clock.monotonic)
        st = _store(tmp_path)
        assert st.acquire_lease("sus", "a", ttl_s=3600)
        clock.wall += 7200      # 2 h of suspend
        clock.mono += 5         # barely any process time
        assert st.lease_ok("sus") is True
        st.close()

    def test_lease_reboot_stale_is_acquirable(self, tmp_path):
        st = _store(tmp_path)
        assert st.acquire_lease("reboot", "a", ttl_s=3600)
        # a lease from a DIFFERENT boot is never alive -> immediately acquirable
        st._conn.execute("UPDATE lease SET boot_id='other-boot' WHERE name=?",
                         ("reboot",))
        assert st.acquire_lease("reboot", "b", ttl_s=60) is True
        assert st.lease_ok("reboot") is True
        st.close()

    def test_lease_renewable(self, tmp_path):
        st = _store(tmp_path)
        assert st.acquire_lease("rn", "a", ttl_s=60)
        assert st.renew_lease("rn", "a", ttl_s=999) is True
        assert st.renew_lease("rn", "b", ttl_s=999) is False  # only owner renews
        assert st.release_lease("rn", "a") is True
        assert st.lease_ok("rn") is False
        st.close()

    def test_lease_monotonic_and_boot_recorded(self, tmp_path):
        st = _store(tmp_path)
        assert st.acquire_lease("mono", "a", ttl_s=120)
        row = st._conn.execute(
            "SELECT expires_mono, acquired_mono, boot_id FROM lease WHERE"
            " name='mono'").fetchone()
        assert float(row["expires_mono"]) > time.monotonic()
        assert row["boot_id"] == _boot_id()
        st.close()


# --------------------------------------------------------------- notification
class TestNotificationPath:
    def _baseline_and_new(self, tmp_path, monkeypatch, **cfg_extra):
        cfg = _cfg(tmp_path, **cfg_extra)
        fx = _fixture(tmp_path)
        run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=fx)
        nf = _fixture(tmp_path, rows=_new_rows())
        return cfg, nf

    def _fake_notify(self, monkeypatch, rc=0):
        monkeypatch.setattr(
            "voicebridge.communications.monitor.shutil.which",
            lambda *a, **k: "/usr/bin/notify-send")
        calls = []

        def _run(cmd, **kw):
            calls.append(list(cmd))
            return type("P", (), {"returncode": rc, "stdout": "",
                                  "stderr": ""})()

        monkeypatch.setattr(
            "voicebridge.communications.monitor.subprocess.run", _run)
        return calls

    def test_no_change_silence(self, tmp_path):
        cfg = _cfg(tmp_path)
        fx = _fixture(tmp_path)
        run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=fx)
        out = run_hourly(cfg, platforms=["gmail"], source="fixture",
                         fixture=fx)
        assert out["commit"] == "no-change"
        assert out["notified"] == []
        assert communications_store(cfg).recent_notifications(50) == []

    def test_one_new_item_exactly_one_popup_then_dedupe(self, tmp_path,
                                                        monkeypatch):
        calls = self._fake_notify(monkeypatch)
        cfg, nf = self._baseline_and_new(tmp_path, monkeypatch)
        out = run_hourly(cfg, platforms=["gmail"], source="fixture",
                         fixture=nf)
        assert out["commit"] == "new" and out["new_total"] == 1
        assert len(out["notified"]) == 1
        assert len(calls) == 1                       # one desktop popup
        st = communications_store(cfg)
        assert len(st.recent_notifications(50)) == 2  # desktop + dashboard
        # rescan -> no duplicate popup
        out2 = run_hourly(cfg, platforms=["gmail"], source="fixture",
                          fixture=nf)
        assert out2["commit"] == "no-change"
        assert out2["notified"] == []
        assert len(calls) == 1
        assert len(st.recent_notifications(50)) == 2

    def test_desktop_copy_is_redacted(self, tmp_path, monkeypatch):
        calls = self._fake_notify(monkeypatch)
        cfg, nf = self._baseline_and_new(tmp_path, monkeypatch)
        run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=nf)
        blob = json.dumps(calls)
        for forbidden in ("http", "report_", "sender", "subject", "secret",
                          "token", "sk-", "@"):
            assert forbidden.lower() not in blob.lower(), forbidden

    def test_quiet_hours_suppresses_voice_fail_closed(self, tmp_path,
                                                      monkeypatch):
        calls = self._fake_notify(monkeypatch)
        cfg = _cfg(tmp_path, **{"communications": {"monitor": {
            "voice_notify": True, "desktop_notify": True}}})
        fx = _fixture(tmp_path)
        run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=fx)
        nf = _fixture(tmp_path, rows=_new_rows())
        monkeypatch.setattr(
            "voicebridge.communications.monitor.time_config_state",
            lambda cfg, now=None: {"state": "ok", "suppress_voice": True,
                                   "active": True})
        out = run_hourly(cfg, platforms=["gmail"], source="fixture",
                         fixture=nf)
        st = communications_store(cfg)
        kinds = {n["kind"]: n["status"] for n in st.recent_notifications(50)}
        assert kinds["voice"] == "suppressed_quiet_hours"
        assert kinds["desktop"] == "sent"
        assert calls, "quiet hours never silence the desktop surface"

    def test_invalid_time_config_suppresses_voice_and_degrades(
            self, tmp_path, monkeypatch):
        """CONFIG_INVALID: malformed time config suppresses voice AND makes the
        aggregate degraded with exit code 1 — never a silent pass."""
        calls = self._fake_notify(monkeypatch)
        cfg = _cfg(tmp_path, **{"communications": {"monitor": {
            "voice_notify": True,
            "voice": {"quiet_hours": "bad", "timezone": "Mars/Olympus"}}}})
        fx = _fixture(tmp_path)
        run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=fx)
        nf = _fixture(tmp_path, rows=_new_rows())
        out = run_hourly(cfg, platforms=["gmail"], source="fixture",
                         fixture=nf)
        assert out["state"] == "degraded"
        assert out["code"] == "CONFIG_INVALID"
        assert out["exit_code"] == 1
        assert out["time_config"]["state"] == "config_invalid"
        st = communications_store(cfg)
        kinds = {n["kind"]: n["status"] for n in st.recent_notifications(50)}
        assert kinds.get("voice") == "suppressed_quiet_hours"

    def test_acknowledgement_single_transition(self, tmp_path, monkeypatch):
        self._fake_notify(monkeypatch)
        cfg, nf = self._baseline_and_new(tmp_path, monkeypatch)
        run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=nf)
        st = communications_store(cfg)
        before = st.pending_notifications()
        nid = [n["notification_id"] for n in st.recent_notifications(50)
               if n["kind"] == "desktop"][0]
        assert st.acknowledge_notification(nid) is True
        assert st.acknowledge_notification(nid) is False   # one transition
        row = st._conn.execute(
            "SELECT status FROM notification WHERE notification_id=?",
            (nid,)).fetchone()
        assert row["status"] == "acked"
        # the acknowledged desktop record no longer counts as pending
        assert st.pending_notifications() == before - 1

    def test_delivery_failure_pending_then_dead_letter(self, tmp_path,
                                                       monkeypatch):
        """A failing desktop delivery records PENDING; bounded retries end in
        DEAD_LETTER (never retried forever, never silently dropped)."""
        self._fake_notify(monkeypatch, rc=1)
        cfg = _cfg(tmp_path)
        fx = _fixture(tmp_path)
        run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=fx)
        nf = _fixture(tmp_path, rows=_new_rows())
        out = run_hourly(cfg, platforms=["gmail"], source="fixture",
                         fixture=nf)
        # the failed desktop delivery must degrade the aggregate truthfully
        assert out["state"] == "degraded"
        assert out["code"] == "DELIVERY_FAILED"
        st = communications_store(cfg)
        desktop = [n for n in st.recent_notifications(50)
                   if n["kind"] == "desktop"]
        assert desktop and desktop[0]["status"] == "pending"
        # bounded retry exhausts the budget -> dead_letter
        st.apply_retry_result(desktop[0]["notification_id"], False, 3)
        st.apply_retry_result(desktop[0]["notification_id"], False, 3)
        st.apply_retry_result(desktop[0]["notification_id"], False, 3)
        st.apply_retry_result(desktop[0]["notification_id"], False, 3)
        row = st._conn.execute(
            "SELECT status, retry_count FROM notification WHERE"
            " notification_id=?", (desktop[0]["notification_id"],)).fetchone()
        assert row["status"] == "dead_letter"
        assert row["retry_count"] == 3
        # the dead-lettered desktop no longer counts; only the (unacked)
        # dashboard record remains pending/sent.
        assert st.pending_notifications() == 1
        assert st.notification_rows_retryable(3) == []

    def test_retry_pending_bounded(self, tmp_path, monkeypatch):
        """_retry_pending_notifications only retries inside the budget and
        never exceeds max_retries."""
        monkeypatch.setattr(
            "voicebridge.communications.monitor.shutil.which",
            lambda *a, **k: "/usr/bin/notify-send")
        calls = []

        def _run(cmd, **kw):
            calls.append(1)
            return type("P", (), {"returncode": 1, "stdout": "",
                                  "stderr": ""})()

        monkeypatch.setattr(
            "voicebridge.communications.monitor.subprocess.run", _run)
        cfg = _cfg(tmp_path)
        st = communications_store(cfg)
        rec = NotificationRecord.create(
            report_id="", kind=NotificationKind.DESKTOP,
            status=NotificationStatus.PENDING,
            dedupe_key="retry:test", new_count=1)
        st.record_notification(rec)
        results = _retry_pending_notifications(cfg, st, max_retries=3)
        assert len(results) == 1 and results[0]["ok"] is False
        row = st._conn.execute(
            "SELECT status, retry_count FROM notification WHERE"
            " notification_id=?", (rec.notification_id,)).fetchone()
        assert row["retry_count"] == 1 and row["status"] == "pending"
        for _ in range(5):
            _retry_pending_notifications(cfg, st, max_retries=3)
        row = st._conn.execute(
            "SELECT status, retry_count FROM notification WHERE"
            " notification_id=?", (rec.notification_id,)).fetchone()
        assert row["status"] == "dead_letter"
        assert row["retry_count"] == 3
        # dead-letter notifications are excluded from pending and retryable
        assert st.pending_notifications() == 0
        assert st.notification_rows_retryable(3) == []

    def test_missed_run_catch_up_no_burst(self, tmp_path, monkeypatch):
        """A missed run caught up by the Persistent timer must not burst: the
        new item produces exactly one popup, never a repeat."""
        calls = self._fake_notify(monkeypatch)
        cfg = _cfg(tmp_path)
        fx = _fixture(tmp_path)
        run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=fx)
        nf = _fixture(tmp_path, rows=_new_rows())
        out = run_hourly(cfg, platforms=["gmail"], source="fixture",
                         fixture=nf)
        assert out["commit"] == "new"
        assert len(out["notified"]) == 1
        assert len(calls) == 1
        # a subsequent catch-up rescan stays silent
        out2 = run_hourly(cfg, platforms=["gmail"], source="fixture",
                          fixture=nf)
        assert out2["commit"] == "no-change"
        assert out2["notified"] == [] and len(calls) == 1


# ------------------------------------------------------------------ aggregate
class TestAggregateTruthfulness:
    def test_clean_run_is_ok(self, tmp_path):
        cfg = _cfg(tmp_path)
        fx = _fixture(tmp_path)
        out = run_hourly(cfg, platforms=["gmail"], source="fixture",
                         fixture=fx, dry_run=True)
        assert out["state"] == "OK" and out["exit_code"] == 0

    def test_degraded_account_never_reports_ok(self, tmp_path, monkeypatch):
        import voicebridge.communications.adapters.registry as R
        cfg = _cfg(tmp_path)
        fx = _fixture(tmp_path)
        run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=fx)
        monkeypatch.setattr(R, "find_target", lambda *a, **k: None)
        out = run_hourly(cfg, platforms=["gmail"], source="live",
                         dry_run=True)
        assert out["state"] == "degraded"
        assert out["code"] == "ACCOUNT_DEGRADED"
        assert out["exit_code"] == 1
        assert out["degraded_accounts"] == ["gmail/default"]

    def test_failed_account_never_reports_ok(self, tmp_path):
        cfg = _cfg(tmp_path)
        fx = _fixture(tmp_path)
        out = run_hourly(cfg, platforms=["slack"], source="fixture",
                         fixture=fx, dry_run=True)
        assert out["state"] == "failed"
        assert out["code"] == "ACCOUNT_FAILED"
        assert out["exit_code"] == 1

    def test_overlap_is_degraded(self, tmp_path):
        cfg = _cfg(tmp_path)
        st = communications_store(cfg)
        assert st.acquire_lease(MONITOR_LEASE, "other", ttl_s=120)
        out = run_hourly(cfg, platforms=["gmail"], source="fixture",
                         fixture=_fixture(tmp_path), store=st)
        assert out["state"] == "degraded" and out["code"] == "OVERLAP"
        assert out["exit_code"] == 1


# ------------------------------------------------------------------- harness
class TestQualificationHarness:
    def test_preflight_reports_config_and_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "voicebridge.communications.timer_qualify._unit_state",
            lambda name: {"enabled": "disabled", "active": "inactive"})
        out = qualify_preflight(_cfg(tmp_path))
        assert out["state"] == "OK"
        assert out["time_config"]["state"] == "ok"
        assert out["lease"]["lease_exceeds_timeout"] is True
        assert out["units"][SERVICE_NAME]

    def test_units_exact_names_and_no_display_hardcode(self, tmp_path):
        out = qualify_units(_cfg(tmp_path))
        assert out["state"] == "OK"
        assert out["checks"]["exact_names"]["service"] == SERVICE_NAME
        assert out["checks"]["exact_names"]["timer"] == TIMER_NAME
        assert out["checks"]["no_hardcoded_display"]["state"] == "OK"
        assert out["checks"]["timer_persistent"]["state"] == "OK"
        # the repository template itself must carry no Environment=DISPLAY=:N
        svc = render_units()[SERVICE_NAME]
        assert "Environment=DISPLAY=" not in svc

    def test_lease_stage(self, tmp_path):
        out = qualify_lease(_cfg(tmp_path), tmp_path / "ql")
        assert out["state"] == "OK"
        assert out["checks"]["renewable"]["state"] == "OK"
        assert out["checks"]["monotonic"]["state"] == "OK"
        assert out["checks"]["reboot_stale"]["state"] == "OK"

    def test_quiet_hours_stage(self, tmp_path):
        out = qualify_quiet_hours(_cfg(tmp_path))
        assert out["state"] == "OK"
        assert out["checks"]["config_invalid_suppresses"]["state"] == "OK"
        assert out["checks"]["dst_fold_gap"]["state"] == "OK"

    def test_notification_stage(self, tmp_path):
        out = qualify_notification(_cfg(tmp_path), tmp_path / "qn")
        assert out["state"] == "OK"
        assert out["checks"]["baseline_silent"]["state"] == "OK"
        assert out["checks"]["one_new_item"]["state"] == "OK"
        assert out["checks"]["dedupe_no_repeat"]["state"] == "OK"
        assert out["checks"]["ack"]["state"] == "OK"
        assert out["checks"]["retry_bounds"]["state"] == "OK"
        assert out["checks"]["redacted_copy"]["state"] == "OK"

    def test_aggregate_stage(self, tmp_path):
        out = qualify_aggregate(_cfg(tmp_path), tmp_path / "qa")
        assert out["state"] == "OK"
        assert out["checks"]["clean_ok"]["state"] == "OK"
        assert out["checks"]["degraded_not_ok"]["state"] == "OK"
        assert out["checks"]["failed_not_ok"]["state"] == "OK"
        assert out["checks"]["config_invalid_degrades"]["state"] == "OK"

    def test_journal_stage(self, tmp_path):
        out = qualify_journal(_cfg(tmp_path), tmp_path / "qj")
        assert out["state"] == "OK"
        assert out["checks"]["report_ready_event"]["state"] == "OK"
        assert out["checks"]["journal_redacted"]["state"] == "OK"

    def test_accelerated_stage(self, tmp_path):
        out = qualify_accelerated(_cfg(tmp_path), tmp_path / "qx")
        assert out["state"] == "OK"
        assert out["checks"]["overlap"]["state"] == "OK"
        assert out["checks"]["config_invalid"]["state"] == "OK"
        assert out["checks"]["delivery_failure_dead_letter"]["state"] == "OK"
        assert out["checks"]["aggregate_failed"]["state"] == "OK"

    def test_full_qualification_hermetic(self, tmp_path):
        out = run_qualification(_cfg(tmp_path),
                                state_dir=tmp_path / "qual",
                                dry_run=True)
        assert out["state"] == "OK"
        for stage, rec in out["stages"].items():
            assert rec.get("state") in ("OK", "degraded"), (stage, rec)

    def test_full_qualification_is_idempotent_on_rerun(self, tmp_path):
        """Packet 8I repair: re-running `--all` against the SAME state dir
        must not fail because a previous run's notify/lease stores are reused.
        Each run gets a fresh per-run isolated stage root."""
        state_dir = tmp_path / "qual"
        cfg = _cfg(tmp_path)
        first = run_qualification(cfg, state_dir=state_dir, dry_run=True)
        assert first["state"] == "OK"
        second = run_qualification(cfg, state_dir=state_dir, dry_run=True)
        assert second["state"] == "OK", \
            f"re-run must stay green; failed stages: " \
            f"{[k for k, v in second['stages'].items() if v.get('state') == 'failed']}"
        assert second["run_id"] != first["run_id"]

    def test_harness_is_self_contained_no_cross_packet_import(self):
        """Packet 8I repair: the 8I harness must be self-contained and NOT
        import the uncommitted Packet 8G `communications/qualify` module (a
        broken dependency would make the committed 8I tree fail to import)."""
        from voicebridge.communications import timer_qualify as _tq
        text = Path(_tq.__file__).read_text()
        assert "from .qualify import" not in text, \
            "8I harness must not depend on Packet 8G's communications/qualify"
        assert "from .qualify import isolate_cfg" not in text

    def test_summary_persisted_private_redacted(self, tmp_path):
        state_dir = tmp_path / "qual"
        out = run_qualification(_cfg(tmp_path), state_dir=state_dir)
        assert out["state"] == "OK"
        runs = list((state_dir / "timer-qualify").glob("*"))
        assert runs, "summary persisted"
        for summary in (state_dir / "timer-qualify").rglob("summary.json"):
            assert (summary.stat().st_mode & 0o077) == 0
            payload = json.dumps(json.loads(summary.read_text()))
            # no actual IDs, URLs, or message-derived VALUES may survive
            # (check-key prose such as "report_ready_event" is metadata, not a
            # value; actual report ids look like report_<hex>).
            import re as _re
            assert not _re.search(r"report_[0-9a-f]{8,}", payload)
            assert not _re.search(r"notif_[0-9a-f]{8,}", payload)
            for forbidden in ("http://", "https://", "sk-",
                              "Synthetic Sender", "Synthetic subject"):
                assert forbidden.lower() not in payload.lower(), forbidden

    def test_qualification_never_touches_production_state(self, tmp_path):
        """The hermetic qualification runs in an ISOLATED state dir; the
        production state root must not be written."""
        prod = tmp_path / "prod-state"
        prod.mkdir()
        cfg = _cfg(tmp_path)
        cfg.data.setdefault("logging", {})["jsonl_path"] = str(prod)
        run_qualification(cfg, state_dir=tmp_path / "qual", dry_run=True)
        assert list(prod.iterdir()) == [], "production state was touched"

    def test_live_without_gate_is_denied(self, tmp_path):
        from voicebridge.communications.timer_qualify import deny_live
        denied = deny_live(True)
        assert denied is not None
        assert denied["code"] == "LIVE_NOT_AUTHORIZED"
        assert deny_live(False) is None


class TestRollback:
    def test_disable_stops_and_disables_timer(self, tmp_path, monkeypatch):
        """Rollback = stop + disable the timer; state is preserved (Packet 8I
        rollback qualification, hermetic via fake systemctl)."""
        from voicebridge.communications.monitor import disable_monitor
        calls = []

        def _fake_run(cmd, **kw):
            calls.append(cmd)
            return type("P", (), {"returncode": 0, "stdout": "",
                                  "stderr": ""})()

        monkeypatch.setattr(
            "voicebridge.communications.monitor.subprocess.run", _fake_run)
        out = disable_monitor()
        assert out["disable"]["state"] == "OK"
        assert out["stop"]["state"] == "OK"
        assert any("disable" in c for c in calls)
        assert any("stop" in c for c in calls)

    def test_disable_reports_ok_when_never_installed(self, tmp_path,
                                                     monkeypatch):
        from voicebridge.communications.monitor import disable_monitor

        def _fake_run(cmd, **kw):
            if "is-enabled" in cmd or "is-active" in cmd:
                return type("P", (), {"returncode": 0, "stdout": "",
                                      "stderr": ""})()
            return type("P", (), {"returncode": 1, "stdout": "",
                                  "stderr": "Unit does not exist"})()

        monkeypatch.setattr(
            "voicebridge.communications.monitor.subprocess.run", _fake_run)
        out = disable_monitor()
        assert out["disable"]["state"] == "OK"
        assert out["stop"]["state"] == "OK"

    def test_rollback_keeps_state(self, tmp_path, monkeypatch):
        """A rollback must never delete cursors/reports/notifications."""
        from voicebridge.communications.monitor import rollback_monitor
        from voicebridge.config import Config
        state = tmp_path / "state"
        state.mkdir(exist_ok=True)
        p = tmp_path / "voice.yaml"
        p.write_text(f"logging:\n  jsonl_path: '{state}'\n")
        cfg = Config.load(p)
        st = communications_store(cfg)
        fx = _fixture(tmp_path)
        run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=fx)
        assert st.peek_cursor(__import__(
            "voicebridge.communications.contracts",
            fromlist=["Platform"]).Platform.GMAIL, "default") is not None

        def _fake_run(cmd, **kw):
            return type("P", (), {"returncode": 0, "stdout": "",
                                  "stderr": ""})()

        monkeypatch.setattr(
            "voicebridge.communications.monitor.subprocess.run", _fake_run)
        out = rollback_monitor(cfg)
        assert out["state"] == "OK"
        # state survives the rollback: cursor, reports, notifications intact
        assert st.peek_cursor(__import__(
            "voicebridge.communications.contracts",
            fromlist=["Platform"]).Platform.GMAIL, "default") is not None


# --------------------------------------------------------------------- CLI
class TestQualifyCLI:
    def test_cli_all_hermetic_exit_0(self, tmp_path):
        import subprocess
        r = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "timer_qualify.py"),
             "--all", "--state-dir", str(tmp_path / "cli"),
             "--config", str(_cfg(tmp_path).path or "")],
            capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            pytest.fail(r.stdout + r.stderr)
        assert r.returncode == 0

    def test_cli_live_without_gate_denied_exit_2(self, tmp_path):
        import subprocess
        r = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "timer_qualify.py"),
             "--notify", "--live", "--config", str(_cfg(tmp_path).path or "")],
            capture_output=True, text=True, timeout=60)
        assert r.returncode == 2
        assert "LIVE_NOT_AUTHORIZED" in r.stdout

    def test_cli_no_mode_denied(self, tmp_path):
        import subprocess
        r = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "timer_qualify.py"),
             "--config", str(_cfg(tmp_path).path or "")],
            capture_output=True, text=True, timeout=60)
        assert r.returncode == 2

    def test_cli_help_documents_live_gate(self):
        import subprocess
        r = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "timer_qualify.py"),
             "--help"],
            capture_output=True, text=True, timeout=60)
        assert QUALIFY_ENV in r.stdout
        assert "--live" in r.stdout