"""Packet 8D tests: authorized hourly monitor, byte-persistence hook, and the
8C screenshot-byte dependency.

Covers:
- the LIVE screenshot-byte persistence hook: a live-style scan commits real
  .webp bytes (not metadata only) into a durable store AND a rendered report
  bundle that serves the exact bytes with a matching hash;
- hourly run driver: global overlap lease, baseline/new/no-change commits,
  notification dedupe, quiet hours, retention dry-run, typed degraded states;
- systemd template rendering + dry-run install (nothing enabled implicitly);
- health/rollback commands and the CDP precondition;
- notification records and the dashboard /api/notifications surface.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from pathlib import Path

from voicebridge.communications.adapters.registry import (  # noqa: E402
    communications_store, run_scan,
)
from voicebridge.communications.contracts import (  # noqa: E402
    NotificationKind, NotificationStatus, Platform,
)
from voicebridge.communications.monitor import (  # noqa: E402
    MONITOR_LEASE, apply_retention, cdp_precondition,
    emit_report_ready, install_monitor, monitor_health, notify_degraded,
    notify_desktop, optional_launch, quiet_hours_active, render_units,
    rollback_monitor, run_hourly, _notify_voice, _parse_quiet_hours,
)
from voicebridge.communications.reports import ReportStore  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FIX = REPO_ROOT / "tests" / "fixtures" / "communications"


class _SyncThread:
    """Replacement for threading.Thread that runs synchronously (tests)."""

    def __init__(self, target, name="", daemon=False):
        self._target = target
        self.daemon = daemon

    def start(self):
        self._target()

    def join(self, timeout=None):
        return None

    def is_alive(self):
        return False


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


def _rows_with_screenshot():
    rows = json.loads((FIX / "new_items.json").read_text())["rows"]
    rows[0]["screenshot"] = base64.b64encode(b"\x00" * 1024).decode()
    return rows


def _fixture(tmp_path, rows=None):
    rows = rows if rows is not None else _rows_with_screenshot()
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"platform": "gmail", "rows": rows}))
    return fx


# ------------------------------------------------------------ byte persistence
def test_live_scan_persists_real_screenshot_bytes(tmp_path):
    """The 8C dependency: a live-style scan with screenshot bytes must persist
    the ACTUAL bytes (durably + into the bundle), never metadata-only."""
    cfg = _cfg(tmp_path)
    fx = _fixture(tmp_path)
    r = run_scan(cfg, "gmail", account_scope="byte1", source="fixture",
                 fixture=fx)
    assert r["commit"] == "baseline"
    assert r["bundle"] is not None
    rid = r["report_id"]
    # durable bytes in the store
    st = communications_store(cfg)
    stored = st.asset_bytes_for_report(rid)
    assert len(stored) == 1          # one AVAILABLE asset
    aid, data = next(iter(stored.items()))
    assert data == b"\x00" * 1024
    # the published bundle serves the exact bytes with a matching hash
    rstore = ReportStore(cfg.state_dir() / "reports")
    det = rstore.detail(rid)
    declared = next(a for a in det["assets"] if a["asset_id"] == aid)
    assert declared["redaction_status"] == "available"
    assert declared["byte_count"] == 1024
    served = rstore.asset(rid, aid)
    assert served is not None
    blob, mime = served
    assert mime == "image/webp"
    assert blob == data
    assert hashlib.sha256(blob).hexdigest() == declared["sha256"]
    assert (rstore.bundle_dir(rid) / "assets" / f"{aid}.webp").is_file()


def test_live_scan_missing_evidence_keeps_typed_reason(tmp_path):
    """Items without screenshot bytes keep an explicit NOT_AVAILABLE reason and
    no byte entry — the text item is never erased."""
    cfg = _cfg(tmp_path)
    fx = _fixture(tmp_path, rows=None)   # fixture WITHOUT any screenshot payload
    # strip screenshot from the shared fixture rows
    rows = json.loads((FIX / "new_items.json").read_text())["rows"]
    for row in rows:
        row.pop("screenshot", None)
    fx = _fixture(tmp_path, rows=rows)
    r = run_scan(cfg, "gmail", account_scope="byte2", source="fixture",
                 fixture=fx)
    assert r["commit"] == "baseline"
    rid = r["report_id"]
    st = communications_store(cfg)
    assert st.asset_bytes_for_report(rid) == {}
    det = ReportStore(cfg.state_dir() / "reports").detail(rid)
    statuses = {a["redaction_status"] for a in det["assets"]}
    assert statuses == {"not_available"}


def test_asset_bytes_survive_restart_and_regen(tmp_path):
    """Durable bytes survive a store reopen and regen re-render (rollback)."""
    cfg = _cfg(tmp_path)
    fx = _fixture(tmp_path)
    r = run_scan(cfg, "gmail", account_scope="byte3", source="fixture",
                 fixture=fx)
    rid = r["report_id"]
    st = communications_store(cfg)
    aid, data = next(iter(st.asset_bytes_for_report(rid).items()))
    st.close()
    # reopen
    st2 = communications_store(cfg)
    assert st2.asset_bytes_for_report(rid)[aid] == data
    rstore = ReportStore(cfg.state_dir() / "reports")
    regen = rstore.render_from_store(st2, rid)
    assert regen is not None
    served = rstore.asset(rid, aid)
    assert served is not None and served[0] == data


def test_store_schema_v2_migrates_v1_db_in_place(tmp_path):
    """A v1 store (no data column) opens as the current version (v3) with NULL
    bytes and the Packet 8I lease/retry columns preserved."""
    from voicebridge.communications.store import (
        CommunicationsStore, STORE_SCHEMA_VERSION,
    )
    db = tmp_path / "v1.db"
    conn = __import__("sqlite3").connect(db)
    conn.executescript(__import__(
        "voicebridge.communications.store", fromlist=["_SCHEMA_V1"]).
        _SCHEMA_V1)
    conn.execute("PRAGMA user_version=1")
    conn.commit()
    conn.close()
    st = CommunicationsStore(db)
    assert st.schema_version == STORE_SCHEMA_VERSION == 3
    cols = [r[1] for r in st._conn.execute(
        "PRAGMA table_info(evidence_asset)")]
    assert "data" in cols
    lease_cols = [r[1] for r in st._conn.execute("PRAGMA table_info(lease)")]
    assert {"acquired_mono", "expires_mono", "boot_id"} <= set(lease_cols)
    notif_cols = [r[1] for r in st._conn.execute(
        "PRAGMA table_info(notification)")]
    assert {"retry_count", "dead_letter_at"} <= set(notif_cols)
    # old rows keep NULL bytes -> no fabricated evidence
    assert st.asset_bytes_for_report("nonexistent") == {}
    st.close()


# ------------------------------------------------------------- hourly driver
def test_hourly_baseline_then_new_then_no_change(tmp_path):
    cfg = _cfg(tmp_path)
    fx = _fixture(tmp_path)
    out1 = run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=fx)
    assert out1["state"] == "OK"
    assert out1["commit"] == "baseline"
    # baseline commits a report but must NOT notify (new_count == 0)
    assert out1["reports"] and out1["notified"] == []
    st = communications_store(cfg)
    assert st.recent_notifications(50) == []
    # same scan again -> no-change, no new report, no notification spam
    out2 = run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=fx)
    assert out2["commit"] == "no-change"
    assert out2["reports"] == [] and out2["notified"] == []
    assert st.recent_notifications(50) == []
    # a NEW item -> new commit + a fresh report + exactly one notification set
    rows = _rows_with_screenshot()
    rows.append({"sourceItemId": "syn-8d1", "threadId": "syn-8d1",
                 "sender": "Synthetic Sender EightD",
                 "subject": "Synthetic subject 8d", "preview": "p",
                 "timeText": "Aug 28", "href": "https://mail.google.com/",
                 "unread": True})
    nf = _fixture(tmp_path, rows=rows)
    out3 = run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=nf)
    assert out3["commit"] == "new"
    assert out3["new_total"] == 1
    assert len(out3["reports"]) == 1
    assert out3["notified"] == out3["reports"]


def test_hourly_overlap_lease_degraded(tmp_path):
    cfg = _cfg(tmp_path)
    st = communications_store(cfg)
    assert st.acquire_lease(MONITOR_LEASE, "other", ttl_s=120)
    fx = _fixture(tmp_path)
    out = run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=fx,
                     store=st)
    assert out["state"] == "degraded" and out.get("code") == "OVERLAP"


def test_hourly_dry_run_persists_nothing(tmp_path):
    cfg = _cfg(tmp_path)
    fx = _fixture(tmp_path)
    out = run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=fx,
                     dry_run=True)
    assert out["state"] == "OK" and out["commit"] == "preview"
    st = communications_store(cfg)
    assert st.peek_cursor(Platform.GMAIL, "default") is None
    assert st.latest_scan(Platform.GMAIL, "default") is None


def test_hourly_auth_required_never_advances_and_degrades(tmp_path, monkeypatch):
    import voicebridge.communications.adapters.registry as R
    cfg = _cfg(tmp_path)
    fx = _fixture(tmp_path)
    run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=fx)  # baseline
    monkeypatch.setattr(R, "find_target", lambda *a, **k: None)
    out = run_hourly(cfg, platforms=["gmail"], source="live", dry_run=True)
    # Packet 8I aggregate truthfulness: a degraded account must NEVER report a
    # top-level OK; the typed state AND exit code must agree.
    assert out["state"] == "degraded"
    assert out.get("code") == "ACCOUNT_DEGRADED"
    assert out.get("exit_code") == 1
    assert out["results"][0]["health"] == "unavailable"
    assert out["degraded_accounts"] == ["gmail/default"]
    st = communications_store(cfg)
    assert st.peek_cursor(Platform.GMAIL, "default") is not None  # preserved


# -------------------------------------------------------------- notifications
def test_quiet_hours_parse_and_active():
    from datetime import datetime
    assert _parse_quiet_hours("22:00-07:00") == (22 * 60, 7 * 60)
    assert _parse_quiet_hours("garbage") is None
    cfg = _cfg(Path("/tmp"), **{"dummy": True})
    assert quiet_hours_active(cfg, now=datetime(2026, 8, 28, 23, 30)) is True
    assert quiet_hours_active(cfg, now=datetime(2026, 8, 28, 12, 0)) is False


def test_quiet_hours_respects_configured_timezone(tmp_path):
    """Regression: quiet hours are evaluated in the configured
    ``communications.monitor.voice.timezone`` (default Europe/Bucharest), not
    the host's local zone. The same instant is quiet in one zone and not in
    another."""
    from datetime import datetime, timezone as dt_tz
    cfg = _cfg(tmp_path, **{"communications": {"monitor": {"voice": {
        "quiet_hours": "22:00-07:00", "timezone": "Europe/Bucharest"}}}})
    cfg_utc = _cfg(tmp_path, **{"communications": {"monitor": {"voice": {
        "quiet_hours": "22:00-07:00", "timezone": "UTC"}}}})
    # 2026-08-28 20:00 UTC == 23:00 Europe/Bucharest -> inside quiet hours
    now = datetime(2026, 8, 28, 20, 0, tzinfo=dt_tz.utc)
    assert quiet_hours_active(cfg, now=now) is True
    # the same instant in UTC is 20:00 -> NOT inside 22:00-07:00 UTC
    assert quiet_hours_active(cfg_utc, now=now) is False
    # 2026-08-28 12:00 UTC == 15:00 Europe/Bucharest -> outside quiet hours
    now2 = datetime(2026, 8, 28, 12, 0, tzinfo=dt_tz.utc)
    assert quiet_hours_active(cfg, now=now2) is False
    # Packet 8I fail-closed: an invalid IANA zone is CONFIG_INVALID and
    # suppresses voice; it NEVER falls back to host-local time.
    cfg_bad = _cfg(tmp_path, **{"communications": {"monitor": {"voice": {
        "quiet_hours": "22:00-07:00", "timezone": "Mars/Olympus"}}}})
    from voicebridge.communications.monitor import time_config_state
    tcs = time_config_state(cfg_bad, now=now)
    assert tcs["state"] == "config_invalid"
    assert tcs["timezone_valid"] is False and tcs["suppress_voice"] is True
    assert quiet_hours_active(cfg_bad, now=now) is True   # voice suppressed


def test_notification_dedupe_and_voice_suppression(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    fx = _fixture(tmp_path)
    out1 = run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=fx,
                      dry_run=True)
    assert out1["state"] == "OK"
    # voice notifications are suppressed inside quiet hours
    monkeypatch.setattr("voicebridge.communications.monitor._parse_quiet_hours",
                        lambda s: (0, 1440))   # force quiet all day
    st = communications_store(cfg)
    st.record_notification(__import__("voicebridge.communications.contracts",
                                      fromlist=["NotificationRecord"]).
                           NotificationRecord.create(
        report_id="report_x", kind=NotificationKind.VOICE,
        status=NotificationStatus.SUPPRESSED_QUIET_HOURS,
        dedupe_key="voice:report:report_x", new_count=1, quiet_hours=True))
    assert st.notification_exists("voice:report:report_x",
                                  NotificationKind.VOICE)
    assert st.pending_notifications() == 0


def test_notify_desktop_missing_bin_typed(tmp_path, monkeypatch):
    monkeypatch.setattr("voicebridge.communications.monitor.shutil.which",
                        lambda *a, **k: None)
    res = notify_desktop(_cfg(tmp_path), 2, ["gmail"])
    assert res["state"] == "unavailable"


def test_notify_desktop_nonzero_exit_is_failed(tmp_path, monkeypatch):
    """Truthfulness: notify-send returning non-zero must be `failed`, never OK."""
    monkeypatch.setattr("voicebridge.communications.monitor.shutil.which",
                        lambda *a, **k: "/usr/bin/notify-send")
    called = {}

    def _fake_run(cmd, **kw):
        called["cmd"] = list(cmd)
        return type("P", (), {"returncode": 3, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("voicebridge.communications.monitor.subprocess.run",
                        _fake_run)
    res = notify_desktop(_cfg(tmp_path), 5, ["gmail"])
    assert res["state"] == "failed"
    assert "code 3" in res["reason"]
    # redacted: never includes message text or stdout/stderr
    assert "stdout" not in json.dumps(res)
    assert called["cmd"][0] == "notify-send"
    assert all("secret" not in part.lower() for part in called["cmd"])


def test_notify_desktop_zero_exit_is_ok(tmp_path, monkeypatch):
    monkeypatch.setattr("voicebridge.communications.monitor.shutil.which",
                        lambda *a, **k: "/usr/bin/notify-send")
    monkeypatch.setattr("voicebridge.communications.monitor.subprocess.run",
                        lambda cmd, **kw: type("P", (), {
                            "returncode": 0, "stdout": "", "stderr": ""})())
    res = notify_desktop(_cfg(tmp_path), 3, ["gmail", "instagram"])
    assert res["state"] == "OK"


def test_notify_desktop_timeout_is_failed(tmp_path, monkeypatch):
    import subprocess as sp
    monkeypatch.setattr("voicebridge.communications.monitor.shutil.which",
                        lambda *a, **k: "/usr/bin/notify-send")
    monkeypatch.setattr("voicebridge.communications.monitor.subprocess.run",
                        lambda cmd, **kw: (_ for _ in ()).throw(
                            sp.TimeoutExpired("notify-send", 5)))
    res = notify_desktop(_cfg(tmp_path), 1, ["gmail"])
    assert res["state"] == "failed"
    assert "timed out" in res["reason"]


# ---------------------------------------------- notification UX (plain copy)
def test_desktop_copy_single_platform_no_identifiers(tmp_path, monkeypatch):
    """Plain-language single-platform copy: friendly title/body, no report ID,
    no account ID, no URL, no message text."""
    monkeypatch.setattr("voicebridge.communications.monitor.shutil.which",
                        lambda *a, **k: "/usr/bin/notify-send")
    called = {}

    def _fake_run(cmd, **kw):
        called["cmd"] = list(cmd)
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("voicebridge.communications.monitor.subprocess.run",
                        _fake_run)
    res = notify_desktop(_cfg(tmp_path), 1, ["gmail"])
    assert res["state"] == "OK"
    title, body = called["cmd"][1], called["cmd"][2]
    assert title == "Qubit: 1 new Gmail message"
    assert body == "Open Qubit to review it."
    blob = json.dumps(called["cmd"])
    for forbidden in ("report_", "report:", "account", "http", "sender",
                      "subject", "secret", "token", "sk-"):
        assert forbidden not in blob, f"leaked {forbidden}"


def test_desktop_copy_single_platform_plural(tmp_path, monkeypatch):
    monkeypatch.setattr("voicebridge.communications.monitor.shutil.which",
                        lambda *a, **k: "/usr/bin/notify-send")
    called = {}
    monkeypatch.setattr("voicebridge.communications.monitor.subprocess.run",
                        lambda cmd, **kw: (called.setdefault("cmd",
                                             list(cmd)),
                                           type("P", (), {"returncode": 0,
                                                          "stdout": "",
                                                          "stderr": ""})())[1])
    res = notify_desktop(_cfg(tmp_path), 5, ["messenger"])
    assert res["state"] == "OK"
    title, body = called["cmd"][1], called["cmd"][2]
    assert title == "Qubit: 5 new Messenger messages"
    assert body == "Open Qubit to review them."


def test_desktop_copy_multiple_platforms(tmp_path, monkeypatch):
    monkeypatch.setattr("voicebridge.communications.monitor.shutil.which",
                        lambda *a, **k: "/usr/bin/notify-send")
    called = {}
    monkeypatch.setattr("voicebridge.communications.monitor.subprocess.run",
                        lambda cmd, **kw: (called.setdefault("cmd",
                                             list(cmd)),
                                           type("P", (), {"returncode": 0,
                                                          "stdout": "",
                                                          "stderr": ""})())[1])
    res = notify_desktop(_cfg(tmp_path), 3, ["gmail", "instagram"])
    assert res["state"] == "OK"
    title, body = called["cmd"][1], called["cmd"][2]
    assert title == "Qubit: New messages found"
    assert body == "3 new messages were found. Open Qubit to review them."


def test_degraded_copy_redacted_and_understandable(tmp_path, monkeypatch):
    """Degraded/auth-required copy is plain language and never leaks raw
    exceptions, URLs, account IDs, report IDs, or message content."""
    monkeypatch.setattr("voicebridge.communications.monitor.shutil.which",
                        lambda *a, **k: "/usr/bin/notify-send")
    called = {}

    def _fake_run(cmd, **kw):
        called["cmd"] = list(cmd)
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("voicebridge.communications.monitor.subprocess.run",
                        _fake_run)
    res = notify_degraded(_cfg(tmp_path), ["gmail"])
    assert res["state"] == "OK"
    title, body = called["cmd"][1], called["cmd"][2]
    assert title == "Qubit needs attention"
    assert body == "Qubit could not check Gmail. Open Qubit for details."
    blob = json.dumps(called["cmd"])
    for forbidden in ("report_", "report:", "account", "http", "sender",
                      "subject", "secret", "token", "sk-", "Traceback",
                      "exception"):
        assert forbidden.lower() not in blob.lower(), f"leaked {forbidden}"


def test_hourly_baseline_no_notification(tmp_path):
    """Requirement: baseline (initial) scan must never notify."""
    cfg = _cfg(tmp_path)
    fx = _fixture(tmp_path)
    out = run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=fx)
    assert out["commit"] == "baseline"
    assert out["notified"] == [] and out["degraded_notified"] == []
    assert communications_store(cfg).recent_notifications(50) == []


def test_hourly_zero_new_no_notification(tmp_path):
    """Requirement: a scan yielding zero new items must never notify."""
    cfg = _cfg(tmp_path)
    fx = _fixture(tmp_path)
    run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=fx)  # baseline
    out = run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=fx)
    assert out["commit"] == "no-change"
    assert out["new_total"] == 0
    assert out["notified"] == []
    assert communications_store(cfg).recent_notifications(50) == []


def test_hourly_one_new_item_one_notification_deduped(tmp_path):
    """Requirement: one real new item -> exactly one notification set, and
    repeated scans never duplicate it."""
    cfg = _cfg(tmp_path)
    fx = _fixture(tmp_path)
    run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=fx)  # baseline
    rows = _rows_with_screenshot()
    rows.append({"sourceItemId": "syn-ux1", "threadId": "syn-ux1",
                 "sender": "Synthetic Sender UX", "subject": "s", "preview": "p",
                 "timeText": "Aug 28", "href": "https://mail.google.com/",
                 "unread": True})
    nf = _fixture(tmp_path, rows=rows)
    out = run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=nf)
    assert out["commit"] == "new"
    assert out["new_total"] == 1
    assert len(out["notified"]) == 1
    st = communications_store(cfg)
    all_n = st.recent_notifications(50)
    assert len(all_n) == 2                       # desktop + dashboard only
    assert {n["kind"] for n in all_n} == {"desktop", "dashboard"}
    # repeated scan (no-change now) -> no duplicate notification
    out2 = run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=nf)
    assert out2["commit"] == "no-change"
    assert out2["notified"] == []
    assert len(st.recent_notifications(50)) == 2
    # user-facing text recorded in notifications never carries identifiers
    blob = json.dumps(all_n)
    for forbidden in ("sender", "subject", "secret", "token", "sk-", "http"):
        assert forbidden.lower() not in blob.lower(), f"leaked {forbidden}"


def test_hourly_no_notify_flag_suppresses_delivery(tmp_path):
    """--no-notify (notify=False) must suppress all notification delivery even
    for a genuinely-new report."""
    cfg = _cfg(tmp_path)
    fx = _fixture(tmp_path)
    run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=fx)  # baseline
    rows = _rows_with_screenshot()
    rows.append({"sourceItemId": "syn-nn1", "threadId": "syn-nn1",
                 "sender": "Synthetic Sender NN", "subject": "s", "preview": "p",
                 "timeText": "Aug 28", "href": "https://mail.google.com/",
                 "unread": True})
    nf = _fixture(tmp_path, rows=rows)
    out = run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=nf,
                     notify=False)
    assert out["commit"] == "new"
    assert out["notified"] == []
    assert out["degraded_notified"] == []
    assert communications_store(cfg).recent_notifications(50) == []


def test_hourly_voice_notify_quiet_suppresses_voice_only(tmp_path, monkeypatch):
    """Regression: with voice notifications enabled inside quiet hours, the
    VOICE channel is suppressed (SUPPRESSED_QUIET_HOURS) while the desktop and
    dashboard channels still record as sent — quiet hours never silence the
    desktop/dashboard surface for a genuinely-new report."""
    monkeypatch.setattr("voicebridge.communications.monitor._parse_quiet_hours",
                        lambda s: (0, 1440))   # force quiet all day
    monkeypatch.setattr("voicebridge.communications.monitor.shutil.which",
                        lambda *a, **k: "/usr/bin/notify-send")
    called: list = []

    def _fake_run(cmd, **kw):
        called.append(list(cmd))
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("voicebridge.communications.monitor.subprocess.run",
                        _fake_run)
    cfg = _cfg(tmp_path, **{"communications": {"monitor": {
        "voice_notify": True, "desktop_notify": True}}})
    fx = _fixture(tmp_path)
    run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=fx)  # baseline
    rows = _rows_with_screenshot()
    rows.append({"sourceItemId": "syn-vq1", "threadId": "syn-vq1",
                 "sender": "Synthetic Sender VQ", "subject": "s", "preview": "p",
                 "timeText": "Aug 28", "href": "https://mail.google.com/",
                 "unread": True})
    nf = _fixture(tmp_path, rows=rows)
    out = run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=nf)
    assert out["commit"] == "new"
    assert out["quiet_hours_active"] is True
    st = communications_store(cfg)
    kinds = {n["kind"]: n["status"] for n in st.recent_notifications(50)}
    assert kinds["desktop"] == "sent"
    assert kinds["dashboard"] == "sent"
    assert kinds["voice"] == "suppressed_quiet_hours"
    assert called, "desktop notify-send must still run inside quiet hours"


def test_hourly_voice_notify_outside_quiet_records_sent(tmp_path, monkeypatch):
    """Regression: outside quiet hours a genuinely-new report with voice
    notifications enabled records VOICE=SENT (synthesis+playback both OK).

    Deterministic: a fixed 12:00 (non-quiet) clock is injected so the test does
    not depend on the real time of day (it must pass during 22:00–07:00 too)."""
    from datetime import datetime as _real_dt

    class _FixedNoon(_real_dt):
        @classmethod
        def now(cls, tz=None):
            # fixed 12:00 local — outside the 22:00–07:00 quiet window
            return cls(2026, 8, 28, 12, 0, 0)

    monkeypatch.setattr("voicebridge.communications.monitor.datetime",
                        _FixedNoon)
    class _FakeTTS:
        def synthesize(self, text, out):
            out.write_bytes(b"\x00" * 512)

    class _FakePlayback:
        def play(self, wav, name="", interruptible=True,
                 on_start=None, on_finish=None):
            return True

    monkeypatch.setattr("voicebridge.communications.monitor._build_local_tts",
                        lambda cfg: _FakeTTS())
    monkeypatch.setattr(
        "voicebridge.communications.monitor.threading.Thread",
        lambda target, name="", daemon=False: _SyncThread(target))
    monkeypatch.setattr("voicebridge.audio.playback.Playback",
                        lambda *a, **k: _FakePlayback())
    cfg = _cfg(tmp_path, **{"communications": {"monitor": {
        "voice_notify": True, "desktop_notify": False}}})
    fx = _fixture(tmp_path)
    run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=fx)  # baseline
    rows = _rows_with_screenshot()
    rows.append({"sourceItemId": "syn-vq2", "threadId": "syn-vq2",
                 "sender": "Synthetic Sender VQ2", "subject": "s", "preview": "p",
                 "timeText": "Aug 28", "href": "https://mail.google.com/",
                 "unread": True})
    nf = _fixture(tmp_path, rows=rows)
    out = run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=nf)
    assert out["commit"] == "new"
    assert out["quiet_hours_active"] is False
    st = communications_store(cfg)
    kinds = {n["kind"]: n["status"] for n in st.recent_notifications(50)}
    assert kinds["voice"] == "sent"
    assert kinds["dashboard"] == "sent"


def test_hourly_degraded_notifies_once_plain(tmp_path, monkeypatch):
    """Requirement: degraded/auth-required accounts get ONE deduplicated
    plain-language alert, never a raw technical dump."""
    import voicebridge.communications.adapters.registry as R
    cfg = _cfg(tmp_path)
    fx = _fixture(tmp_path)
    run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=fx)  # baseline
    monkeypatch.setattr(R, "find_target", lambda *a, **k: None)
    monkeypatch.setattr("voicebridge.communications.monitor.shutil.which",
                        lambda *a, **k: "/usr/bin/notify-send")
    called = {}
    monkeypatch.setattr("voicebridge.communications.monitor.subprocess.run",
                        lambda cmd, **kw: (called.setdefault("cmds",
                                             []).append(list(cmd)),
                                           type("P", (), {"returncode": 0,
                                                          "stdout": "",
                                                          "stderr": ""})())[1])
    out = run_hourly(cfg, platforms=["gmail"], source="live")
    assert out["degraded_accounts"] == ["gmail/default"]
    assert out["degraded_notified"]
    st = communications_store(cfg)
    # exactly one degraded desktop alert recorded, dedupe key is redacted from
    # the dashboard view but present in the store
    assert st.notification_exists("degraded:gmail", NotificationKind.DESKTOP)
    blob = json.dumps(called.get("cmds", []))
    assert "Qubit needs attention" in blob
    assert "could not check Gmail" in blob
    for forbidden in ("report_", "http", "sender", "subject", "secret",
                      "token", "Traceback"):
        assert forbidden.lower() not in blob.lower(), f"leaked {forbidden}"
    # second degraded run is deduplicated (no second desktop alert)
    out2 = run_hourly(cfg, platforms=["gmail"], source="live")
    assert "deduped" in out2["degraded_notified"][0]
    assert len(called.get("cmds", [])) == 1


def test_notify_voice_unavailable_when_no_tts(tmp_path, monkeypatch):
    """No local voice -> unavailable, never a fake success."""
    monkeypatch.setattr("voicebridge.communications.monitor._build_local_tts",
                        lambda cfg: None)
    res = _notify_voice(_cfg(tmp_path), 2, ["gmail"])
    assert res["state"] == "unavailable"


def test_notify_voice_synthesis_failure_is_failed(tmp_path, monkeypatch):
    """Synthesis failure must not be reported as success."""
    class _FakeTTS:
        def synthesize(self, text, out):
            raise RuntimeError("synth boom")

    monkeypatch.setattr("voicebridge.communications.monitor._build_local_tts",
                        lambda cfg: _FakeTTS())
    res = _notify_voice(_cfg(tmp_path), 2, ["gmail"])
    assert res["state"] == "failed"
    assert "synth" in res["reason"]


def test_notify_voice_playback_failure_is_failed(tmp_path, monkeypatch):
    """Playback failure must not be reported as success."""
    class _FakeTTS:
        def synthesize(self, text, out):
            out.write_bytes(b"\x00" * 512)

    class _FakePlayback:
        def play(self, wav, name="", interruptible=True,
                 on_start=None, on_finish=None):
            return False

    monkeypatch.setattr("voicebridge.communications.monitor._build_local_tts",
                        lambda cfg: _FakeTTS())
    monkeypatch.setattr(
        "voicebridge.communications.monitor.threading.Thread",
        lambda target, name="", daemon=False: _SyncThread(target))
    monkeypatch.setattr("voicebridge.audio.playback.Playback",
                        lambda *a, **k: _FakePlayback())
    res = _notify_voice(_cfg(tmp_path), 2, ["gmail"])
    assert res["state"] == "failed"
    assert "playback" in res["reason"]


def test_notify_voice_success(tmp_path, monkeypatch):
    """Only synthesis+playback both succeeding is OK."""
    class _FakeTTS:
        def synthesize(self, text, out):
            out.write_bytes(b"\x00" * 512)

    class _FakePlayback:
        def play(self, wav, name="", interruptible=True,
                 on_start=None, on_finish=None):
            return True

    monkeypatch.setattr("voicebridge.communications.monitor._build_local_tts",
                        lambda cfg: _FakeTTS())
    monkeypatch.setattr(
        "voicebridge.communications.monitor.threading.Thread",
        lambda target, name="", daemon=False: _SyncThread(target))
    monkeypatch.setattr("voicebridge.audio.playback.Playback",
                        lambda *a, **k: _FakePlayback())
    res = _notify_voice(_cfg(tmp_path), 2, ["gmail"])
    assert res["state"] == "OK"


def test_notify_voice_cleans_temp_wav(tmp_path, monkeypatch):
    """Temporary notification audio is removed after the call."""
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl_path: '" + str(state) + "'\n")
    from voicebridge.config import Config
    cfg = Config.load(p)

    class _FakeTTS:
        def synthesize(self, text, out):
            out.write_bytes(b"\x00" * 512)

    class _FakePlayback:
        def play(self, wav, name="", interruptible=True,
                 on_start=None, on_finish=None):
            return True

    monkeypatch.setattr("voicebridge.communications.monitor._build_local_tts",
                        lambda cfg: _FakeTTS())
    monkeypatch.setattr(
        "voicebridge.communications.monitor.threading.Thread",
        lambda target, name="", daemon=False: _SyncThread(target))
    monkeypatch.setattr("voicebridge.audio.playback.Playback",
                        lambda *a, **k: _FakePlayback())
    res = _notify_voice(cfg, 1, ["gmail"])
    assert res["state"] == "OK"
    notify_dir = state / "notify"
    wavs = list(notify_dir.glob("*.wav")) if notify_dir.exists() else []
    assert wavs == [], "temporary notification audio must be cleaned up"


def test_emit_report_ready_writes_durable_event(tmp_path):
    cfg = _cfg(tmp_path)
    rec = emit_report_ready(cfg, "report_ready1", 3, ["gmail"])
    assert rec is not None
    journal_path = cfg.state_dir() / "typed-events.jsonl"
    assert journal_path.exists()
    last = json.loads(journal_path.read_text().strip().splitlines()[-1])
    assert last["type"] == "CUSTOM"
    assert last.get("name") == "communications.report.ready"
    assert last["value"]["report_id"] == "report_ready1"
    # no message text / account identities in the event
    assert "sender" not in json.dumps(last)
    assert "subject" not in json.dumps(last)


# ------------------------------------------------------------ CDP / health
def test_cdp_base_is_loopback_only():
    """Regression: the monitor's Chromium-CDP endpoint is loopback-only; an
    any-interface/remote binding would expose a debugging endpoint to the
    network. Every monitor/CDP entry point derives from this constant."""
    from voicebridge.communications.adapters.base import CDP_LOOPBACK
    assert CDP_LOOPBACK.startswith("http://127.0.0.1:")
    assert "0.0.0.0" not in CDP_LOOPBACK
    assert "9222" in CDP_LOOPBACK


def test_cdp_precondition_typed(tmp_path):
    cfg = _cfg(tmp_path)
    res = cdp_precondition(cfg)
    assert res["state"] in ("OK", "BROWSER_RESTART_REQUIRED", "UNAVAILABLE")
    assert "cdp" in res


def test_cdp_precondition_no_false_restart_for_other_chrom_procs(tmp_path,
                                                                 monkeypatch):
    """Regression: VS Code / Electron crashpad handlers (process names that
    merely CONTAIN 'chrom') must never make the monitor claim Chromium is
    running without CDP. Only a real browser process may -> RESTART_REQUIRED."""
    import subprocess as _sp
    import voicebridge.communications.monitor as M
    cfg = _cfg(tmp_path)
    # no CDP available
    monkeypatch.setattr(M, "browser_ws_url", lambda *a, **k: None)
    # a VS Code crashpad handler is running but no real browser -> UNAVAILABLE
    monkeypatch.setattr(
        _sp, "run",
        lambda cmd, **kw: type("R", (), {
            "returncode": 0,
            "stdout": "/snap/code/usr/share/code/chrome_crashpad_handler "
                      "--monitor-self-annotation=crashpad\n",
            "stderr": ""})())
    assert M._chromium_running() is False
    res = cdp_precondition(cfg)
    assert res["state"] == "UNAVAILABLE"
    # a real chromium binary running without CDP -> BROWSER_RESTART_REQUIRED
    monkeypatch.setattr(
        _sp, "run",
        lambda cmd, **kw: type("R", (), {
            "returncode": 0,
            "stdout": "/usr/bin/chromium --remote-debugging-port=0\n",
            "stderr": ""})())
    assert M._chromium_running() is True
    res2 = cdp_precondition(cfg)
    assert res2["state"] == "BROWSER_RESTART_REQUIRED"
    assert "never kills or restarts" in res2["detail"]


def test_optional_launch_default_off(tmp_path):
    cfg = _cfg(tmp_path)
    res = optional_launch(cfg)
    assert res["state"] == "not_enabled"


def test_monitor_health_reports_typed_fields(tmp_path):
    cfg = _cfg(tmp_path)
    fx = _fixture(tmp_path)
    run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=fx)
    h = monitor_health(cfg)
    assert "cdp" in h and "lease_held" in h
    assert "pending_notifications" in h
    assert "gmail" in h["reports"]


# ------------------------------------------------------------- install/rollback
def test_render_units_has_project_dir_injected():
    units = render_units()
    assert "openclaw-communications-monitor.service" in units
    assert "openclaw-communications-monitor.timer" in units
    assert "__PROJECT_DIR__" not in units["openclaw-communications-monitor.service"]
    assert "__PROJECT_DIR__" not in units["openclaw-communications-monitor.timer"]
    assert "__PROJECT_DIR__" not in units["openclaw-communications-monitor.service"]
    svc = units["openclaw-communications-monitor.service"]
    assert "Type=oneshot" in svc
    assert "communications_monitor.py --hourly" in svc
    tmr = units["openclaw-communications-monitor.timer"]
    assert "OnCalendar=hourly" in tmr and "Persistent=true" in tmr


def test_install_monitor_dry_run_writes_nothing(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr("voicebridge.communications.monitor.Path.home",
                        lambda: tmp_path / "home")
    out = install_monitor(cfg, dry_run=True, enable=False)
    assert out["state"] == "dry-run"
    unit_dir = tmp_path / "home" / ".config" / "systemd" / "user"
    assert not (unit_dir / "openclaw-communications-monitor.service").exists()
    assert not (unit_dir / "openclaw-communications-monitor.timer").exists()


def test_install_monitor_dry_run_never_creates_unit_dir(tmp_path, monkeypatch):
    """Regression: a dry-run install writes NOTHING — not even the systemd user
    unit directory (the documented ``--install`` dry-run contract)."""
    cfg = _cfg(tmp_path)
    monkeypatch.setattr("voicebridge.communications.monitor.Path.home",
                        lambda: tmp_path / "home")
    out = install_monitor(cfg, dry_run=True, enable=False)
    assert out["state"] == "dry-run"
    unit_dir = tmp_path / "home" / ".config" / "systemd" / "user"
    assert not unit_dir.exists(), \
        "dry-run must not create ~/.config/systemd/user"
    # the real (non-dry-run) install DOES create the directory
    out2 = install_monitor(cfg, dry_run=False, enable=False)
    assert out2["state"] == "installed"
    assert unit_dir.exists()


def test_install_monitor_never_enables_implicitly(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr("voicebridge.communications.monitor.Path.home",
                        lambda: tmp_path / "home")
    calls: list = []

    def _fake_run(cmd, **kw):
        calls.append(list(cmd))
        return type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr("voicebridge.communications.monitor.subprocess.run",
                        _fake_run)
    out = install_monitor(cfg, dry_run=False, enable=False)
    assert out["enable"]["state"] == "not_enabled"
    # units written atomically with a private mode
    unit_dir = tmp_path / "home" / ".config" / "systemd" / "user"
    svc = unit_dir / "openclaw-communications-monitor.service"
    tmr = unit_dir / "openclaw-communications-monitor.timer"
    assert svc.exists() and tmr.exists()
    assert oct(svc.stat().st_mode & 0o777) == "0o600"
    # implicit run: daemon-reload may run, but the TIMER is never enabled
    assert not any("enable" in c for c in calls)
    # explicit enable path invokes systemctl --user enable for the TIMER only
    calls.clear()
    out2 = install_monitor(cfg, dry_run=False, enable=True)
    assert out2["enable"]["state"] == "OK"
    enabled = [c for c in calls if "enable" in c]
    assert enabled, "explicit enable must invoke systemctl enable"
    assert any("openclaw-communications-monitor.timer" in c for c in enabled)
    assert not any("openclaw-communications-monitor.service" in c and "enable" in c
                   for c in calls)


def test_install_monitor_enable_starts_timer(tmp_path, monkeypatch):
    """Regression: `--install --enable` must START the timer (not just enable
    it). An enabled-but-unstarted timer is inactive/dead with Trigger=n/a in an
    already-running session and never fires until the next login — so live
    activation would silently not activate. Only the TIMER is started."""
    cfg = _cfg(tmp_path)
    monkeypatch.setattr("voicebridge.communications.monitor.Path.home",
                        lambda: tmp_path / "home")
    calls: list = []

    def _fake_run(cmd, **kw):
        calls.append(list(cmd))
        return type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr("voicebridge.communications.monitor.subprocess.run",
                        _fake_run)
    out = install_monitor(cfg, dry_run=False, enable=True)
    assert out["enable"]["state"] == "OK"
    starts = [c for c in calls if "start" in c]
    assert starts, "live enable must invoke systemctl start for the timer"
    assert any("openclaw-communications-monitor.timer" in c for c in starts)
    # the SERVICE unit is never started or enabled by live activation
    assert not any("openclaw-communications-monitor.service" in c
                   and ("start" in c or "enable" in c) for c in calls)
    # a failed start is reported as a failed enable (truthful return code)
    calls.clear()

    def _fail_start(cmd, **kw):
        calls.append(list(cmd))
        return type("R", (), {"returncode": 1 if "start" in cmd else 0,
                              "stderr": "start failed", "stdout": ""})()

    monkeypatch.setattr("voicebridge.communications.monitor.subprocess.run",
                        _fail_start)
    out2 = install_monitor(cfg, dry_run=False, enable=True)
    assert out2["enable"]["state"] == "failed"
    assert "start failed" in out2["enable"]["detail"]


def test_disable_and_rollback_keep_state(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr("voicebridge.communications.monitor.subprocess.run",
                        lambda *a, **k: type("R", (), {"returncode": 0,
                                                       "stderr": ""})())
    out = rollback_monitor(cfg)
    assert out["state"] == "OK" and "disable" in out


def test_disable_monitor_reports_ok_when_timer_never_installed(tmp_path,
                                                               monkeypatch):
    """Regression: disabling a timer that was never installed must report
    SUCCESS (idempotent rollback — the desired state already holds), never a
    failure; a genuine systemctl failure is still reported as failed."""
    import voicebridge.communications.monitor as M
    monkeypatch.setattr(
        M.subprocess, "run",
        lambda cmd, **kw: type("R", (), {
            "returncode": 1,
            "stderr": "Failed to disable unit: Unit file "
                      "openclaw-communications-monitor.timer does not exist.\n",
            "stdout": ""})())
    out = M.disable_monitor()
    assert out["disable"]["state"] == "OK"
    assert "nothing to disable" in out["disable"]["detail"]
    # a real systemctl failure must still be reported as failed
    monkeypatch.setattr(
        M.subprocess, "run",
        lambda cmd, **kw: type("R", (), {
            "returncode": 1, "stderr": "Operation refused", "stdout": ""})())
    out2 = M.disable_monitor()
    assert out2["disable"]["state"] == "failed"
    assert "Operation refused" in out2["disable"]["detail"]


def test_rollback_stops_and_disables_live_timer(tmp_path, monkeypatch):
    """Regression: a true rollback must STOP an already-running timer (`disable`
    alone leaves it active/waiting so it keeps firing) AND disable it — while
    never touching the service unit or any state."""
    import voicebridge.communications.monitor as M
    calls: list = []

    def _fake(cmd, **kw):
        calls.append(list(cmd))
        return type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(M.subprocess, "run", _fake)
    out = M.disable_monitor()
    assert out["disable"]["state"] == "OK"
    assert out["stop"]["state"] == "OK"
    cmds = [c for c in calls if any(x in c for x in ("disable", "stop"))]
    assert any("disable" in c and "openclaw-communications-monitor.timer" in c
               for c in cmds)
    assert any("stop" in c and "openclaw-communications-monitor.timer" in c
               for c in cmds)
    # the SERVICE unit is never disabled/stopped by a monitor rollback
    assert not any("openclaw-communications-monitor.service" in c for c in cmds)


def test_rollback_preserves_cursors_reports_evidence_notifications(tmp_path,
                                                                   monkeypatch):
    """Rollback must NOT touch cursors, reports, evidence bytes, memory, or
    notification records — only disable the timer."""
    cfg = _cfg(tmp_path)
    fx = _fixture(tmp_path)
    out = run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=fx)
    assert out["reports"]
    st = communications_store(cfg)
    before_cur = st.peek_cursor(Platform.GMAIL, "default")
    before_scan = st.latest_scan(Platform.GMAIL, "default")
    before_notifs = st.recent_notifications(50)
    rid = out["reports"][0]
    rstore = ReportStore(cfg.state_dir() / "reports")
    assert rstore.detail(rid) is not None
    before_assets = st.asset_bytes_for_report(rid)
    assert before_assets          # real screenshot bytes committed
    # rollback (systemctl calls stubbed to success)
    monkeypatch.setattr("voicebridge.communications.monitor.subprocess.run",
                        lambda *a, **k: type("R", (), {"returncode": 0,
                                                       "stderr": ""})())
    r = rollback_monitor(cfg)
    assert r["state"] == "OK"
    # everything preserved
    assert st.peek_cursor(Platform.GMAIL, "default") == before_cur
    assert st.latest_scan(Platform.GMAIL, "default") == before_scan
    assert st.recent_notifications(50) == before_notifs
    assert rstore.detail(rid) is not None          # bundle still on disk
    assert st.asset_bytes_for_report(rid) == before_assets


def test_apply_retention_dry_run_typed(tmp_path):
    cfg = _cfg(tmp_path)
    fx = _fixture(tmp_path)
    run_hourly(cfg, platforms=["gmail"], source="fixture", fixture=fx)
    st = communications_store(cfg)
    out = apply_retention(cfg, st, dry_run=True)
    assert out["state"] == "OK" and out["dry_run"] is True
    cats = {c["category"] for c in out["db"]}
    assert {"scan_health", "evidence_asset", "report_manifest",
            "notification"} <= cats
    assert out["bundles"]["state"] in ("OK", "BLOCKED", "DEGRADED")


# ------------------------------------------------------------- CLI smoke
def test_monitor_cli_hourly_denies_without_authorization(tmp_path):
    import subprocess as sp
    script = REPO_ROOT / "scripts" / "communications_monitor.py"
    # default invocation (no --hourly/--once) is refused
    r = sp.run([sys.executable, str(script)],
               capture_output=True, text=True, timeout=60)
    assert r.returncode == 2
    out = json.loads(r.stdout)
    assert out["state"] == "denied"
    # install dry-run via the script does not enable anything (hermetic: use a
    # FAKE HOME so a genuinely-installed timer on the real host never makes the
    # dry-run assertion depend on machine state)
    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "fakehome")
    env.pop("VOICE_BRIDGE_MONITOR_ENABLE", None)
    r2 = sp.run([sys.executable, str(script), "--install",
                 "--config", str(tmp_path / "voice.yaml")],
                capture_output=True, text=True, timeout=60, env=env)
    assert r2.returncode == 0
    out2 = json.loads(r2.stdout)
    assert out2["dry_run"] is True
    assert out2["files"]["openclaw-communications-monitor.timer"][
        "exists"] is False


def test_cli_enable_without_install_rejected(tmp_path):
    """--enable requires --install (and is rejected on its own)."""
    import subprocess as sp
    script = REPO_ROOT / "scripts" / "communications_monitor.py"
    r = sp.run([sys.executable, str(script), "--enable",
                "--config", str(tmp_path / "voice.yaml")],
               capture_output=True, text=True, timeout=60)
    assert r.returncode == 2
    out = json.loads(r.stdout)
    assert out["state"] == "denied"
    assert "--install" in out["detail"]


def test_cli_install_enable_without_env_denied(tmp_path):
    """--install --enable WITHOUT VOICE_BRIDGE_MONITOR_ENABLE=1 is denied and
    writes/enables nothing (verified against a fake HOME systemd dir)."""
    import subprocess as sp
    script = REPO_ROOT / "scripts" / "communications_monitor.py"
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    env = dict(os.environ)
    env["HOME"] = str(fake_home)
    env.pop("VOICE_BRIDGE_MONITOR_ENABLE", None)
    r = sp.run([sys.executable, str(script), "--install", "--enable",
                "--config", str(tmp_path / "voice.yaml")],
               capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 1
    out = json.loads(r.stdout)
    assert out["dry_run"] is True
    unit_dir = fake_home / ".config" / "systemd" / "user"
    assert not (unit_dir / "openclaw-communications-monitor.service").exists()
    assert not (unit_dir / "openclaw-communications-monitor.timer").exists()


def test_cli_install_enable_with_env_enables_timer_only(tmp_path):
    """With the env authorization, --install --enable installs atomically and
    enables ONLY the timer (never the service)."""
    import subprocess as sp
    script = REPO_ROOT / "scripts" / "communications_monitor.py"
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    env = dict(os.environ)
    env["HOME"] = str(fake_home)
    env["VOICE_BRIDGE_MONITOR_ENABLE"] = "1"
    # We must NOT actually run systemctl. Monkeypatching subprocess.run inside
    # the subprocess is not possible from here, so instead we verify the install
    # contract at the function level (install_monitor) and the CLI authorization
    # gate separately. This subprocess run installs real unit files into the
    # FAKE home dir and would invoke systemctl -> so run it via a config that
    # routes systemctl to a stub through PATH.
    stub = tmp_path / "stubbin"
    stub.mkdir()
    (stub / "systemctl").write_text(
        "#!/bin/sh\nprintf 'enabled\\n'\nexit 0\n")
    (stub / "systemctl").chmod(0o755)
    env["PATH"] = str(stub) + os.pathsep + env.get("PATH", "")
    r = sp.run([sys.executable, str(script), "--install", "--enable",
                "--config", str(tmp_path / "voice.yaml")],
               capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, r.stderr[-400:]
    out = json.loads(r.stdout)
    assert out["state"] == "installed" and out["enabled"] is True
    assert out["enable"]["state"] == "OK"
    unit_dir = fake_home / ".config" / "systemd" / "user"
    svc = unit_dir / "openclaw-communications-monitor.service"
    tmr = unit_dir / "openclaw-communications-monitor.timer"
    assert svc.exists() and tmr.exists()
    assert oct(svc.stat().st_mode & 0o777) == "0o600"
    assert oct(tmr.stat().st_mode & 0o777) == "0o600"
    # backups are created when re-installing over existing units
    env["HOME"] = str(fake_home)
    r2 = sp.run([sys.executable, str(script), "--install", "--enable",
                 "--config", str(tmp_path / "voice.yaml")],
                capture_output=True, text=True, timeout=60, env=env)
    out2 = json.loads(r2.stdout)
    assert out2["files"]["openclaw-communications-monitor.timer"]["backup"]
    backups = list(unit_dir.glob("*.bak.*"))
    assert backups


def test_cli_help_and_docs_syntax_identical(tmp_path):
    """Every documented install command is executable exactly as written:
    --install alone is dry-run; --enable requires the env gate."""
    import subprocess as sp
    script = REPO_ROOT / "scripts" / "communications_monitor.py"
    r = sp.run([sys.executable, str(script), "--help"],
               capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    help_text = r.stdout
    assert "--enable" in help_text
    assert "VOICE_BRIDGE_MONITOR_ENABLE" in help_text
    # the invalid documented syntax is gone from the repo
    assert "--dry-run=false" not in help_text
    docs = (REPO_ROOT / "docs" / "OPERATIONS_8D.md").read_text()
    assert "--dry-run=false" not in docs
    assert "VOICE_BRIDGE_MONITOR_ENABLE=1" in docs
    assert "--install --enable" in docs


# ---------------------------------------------------------- webui surface
def test_webui_notifications_route_requires_auth(tmp_path):
    from tests.test_webui_reports import _start, _http
    ui, port = _start(tmp_path)
    try:
        status, _, _ = _http(port, "GET", "/api/notifications", token="")
        assert status == 401
        status, _, body = _http(port, "GET", "/api/notifications")
        assert status == 200
        data = json.loads(body)
        assert data["state"] == "OK" and "notifications" in data
    finally:
        ui.stop()


def test_webui_stop_releases_listening_socket(tmp_path):
    """Regression: WebUI.stop() must release the listening socket (server_close
    after shutdown), otherwise every start/stop leaks an fd (ResourceWarning in
    the 8D /api/notifications webui test)."""
    from tests.test_webui_reports import _start
    ui, port = _start(tmp_path)
    ui.stop()
    sock = ui._server.socket
    assert sock.fileno() == -1, "listening socket must be closed by stop()"