"""Focused contracts for the shared cron-notice formatter.

These tests are intentionally behavioural: they pin the rendered text a user sees on each
transport, the truncation boundary, and the redaction chokepoint — not internal helper shapes.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cron import notice_format as nf

EET = timezone(timedelta(hours=2), "EET")
NOW = datetime(2026, 10, 26, 18, 12, tzinfo=EET)


@pytest.fixture(autouse=True)
def _fixed_zone(monkeypatch):
    monkeypatch.setattr(nf, "get_timezone", lambda: EET)
    monkeypatch.setattr(nf, "_qubit_now", lambda: NOW)


def _job(**extra):
    job = {
        "id": "ab12cd34",
        "name": "Morning brief",
        "schedule": {"kind": "interval", "minutes": 5, "display": "every 5m"},
        "next_run_at": None,
        "execution_id": "exec-9",
    }
    job.update(extra)
    return job


def test_success_notice_is_compact_structured_and_transport_neutral():
    notice = nf.build_run_notice(
        _job(),
        "Markets are up 2%.\nReady for review.",
        success=True,
        duration_seconds=64.2,
        dashboard_url="https://dash.example.com/hermes",
        now=NOW,
    )
    text = nf.render_notice(notice, "telegram")

    assert text.splitlines()[0] == "✅ **Morning brief** — completed"
    assert "**Job:** Morning brief (ab12cd34)" in text
    assert "**Schedule:** every 5m" in text
    assert "**Duration:** 1m 04s" in text
    assert "**Next run:** in 5m (2026-10-26 18:17 EET)" in text
    assert "**Run id:** exec-9" in text
    assert "[Open dashboard](https://dash.example.com/hermes)" in text
    assert "Markets are up 2%." in text
    assert len(text) <= nf.DEFAULT_MAX_CHARS
    assert "Cronjob Response:" not in text
    assert "-------------" not in text


def test_failure_notice_has_one_status_icon_and_keeps_the_action():
    notice = nf.build_run_notice(
        _job(),
        "⚠️ Cron 'Morning brief' failed: Error code: 401 - Unauthorized. "
        "Sign in again with `/login`. Run log: `qubit cron runs ab12cd34`.",
        success=False,
        now=NOW,
    )
    text = nf.render_notice(notice, "telegram")

    assert text.startswith("❌ **Morning brief** — failed")
    assert text.count("❌") == 1
    assert "⚠️" not in text
    assert "Cron 'Morning brief' failed" not in text
    assert "`/login`" in text
    assert "`qubit cron runs ab12cd34`" in text

    watchdog = nf.build_run_notice(
        _job(), "⚠ Cron watchdog 'Morning brief' script failed\n\nboom", success=False, now=NOW)
    watchdog_text = nf.render_notice(watchdog, "telegram")
    assert watchdog_text.startswith("❌ **Morning brief** — failed")
    assert "⚠" not in watchdog_text
    assert "boom" in watchdog_text


@pytest.mark.parametrize(
    "kind,icon,label",
    [
        ("scheduled", "⏰", "scheduled"),
        ("started", "▶️", "started"),
        ("success", "✅", "completed"),
        ("partial", "⚠️", "partial"),
        ("failed", "❌", "failed"),
        ("paused", "⏸️", "paused"),
        ("retry", "🔁", "retrying"),
        ("receipt", "🧾", "receipt"),
    ],
)
def test_status_icon_set_is_consistent(kind, icon, label):
    notice = nf.build_simple_notice(_job(), kind, summary="detail", now=NOW)
    text = nf.render_notice(notice, "telegram")
    assert text.splitlines()[0] == f"{icon} **Morning brief** — {label}"
    assert text.count(icon) == 1


def test_partial_and_reminder_use_their_own_kinds():
    partial = nf.build_run_notice(_job(), "3 of 4 items fetched.", success=True, partial=True, now=NOW)
    reminder = nf.build_simple_notice(_job(), "scheduled", summary="Standup in 5 minutes.", now=NOW)
    assert nf.render_notice(partial, "telegram").startswith("⚠️ **Morning brief** — partial")
    assert nf.render_notice(reminder, "telegram").startswith("⏰ **Morning brief** — scheduled")


def test_user_text_cannot_inject_extra_status_icons_or_break_the_headline():
    job = _job(name="a*b_c`d\nsecond line")
    notice = nf.build_run_notice(job, "✅ done\n✅ shipped\n✅ checked", success=True, now=NOW)
    text = nf.render_notice(notice, "telegram")

    assert text.count("✅") == 1
    assert "\nsecond line" not in text
    assert "**a∗b-c'd second line**" in text
    assert "done" in text and "shipped" in text


def test_truncation_never_splits_an_emoji_sequence_or_markdown_construct():
    family = "👨‍👩‍👧‍👦"
    text = "x" * 319 + family + "tail"
    truncated, cut = nf.safe_truncate(text, 322)
    assert cut is True
    assert truncated.endswith("…")
    assert len(truncated) <= 322
    assert "\u200d" not in truncated

    bold = "a" * 318 + "**bold tail"
    truncated, _ = nf.safe_truncate(bold, 322)
    assert truncated.count("**") % 2 == 0

    slash = "a" * 320 + "\\" + "b" * 10
    truncated, _ = nf.safe_truncate(slash, 323)
    assert not truncated.endswith("\\…")


def test_rendered_notice_has_a_hard_cap_and_a_marker():
    notice = nf.CronNotice(
        kind="failed",
        job_name="Huge",
        job_id="j1",
        summary="s" * 200,
        details=(("Note", "x" * 3000),),
    )
    text = nf.render_notice(notice, "telegram")
    assert len(text) <= nf.DEFAULT_MAX_CHARS
    assert text.endswith("…")


def test_plain_fallback_strips_markdown_and_keeps_readable_labels():
    notice = nf.build_run_notice(
        _job(),
        "**Bold** and `code` and [docs](https://example.com/docs).",
        success=True,
        now=NOW,
    )
    text = nf.render_notice(notice, "signal")

    assert "**" not in text
    assert "`" not in text
    assert "](" not in text
    assert "Job: Morning brief (ab12cd34)" in text
    assert "Bold and code and docs (https://example.com/docs)." in text


def test_per_transport_output_matches_the_transport():
    notice = nf.build_run_notice(_job(), "Report body.", success=True, now=NOW)
    telegram = nf.render_notice(notice, "telegram")
    discord = nf.render_notice(notice, "discord")
    slack = nf.render_notice(notice, "slack")
    whatsapp = nf.render_notice(notice, "whatsapp")
    plain = nf.render_notice(notice, "sms")

    assert telegram == discord == slack
    assert "**Job:**" in telegram
    assert "*Job:*" in whatsapp and "**Job:**" not in whatsapp
    assert "Job:" in plain and "*Job:*" not in plain


def test_no_url_is_invented_and_configured_urls_drop_credentials():
    no_link = nf.build_run_notice(_job(), "Report.", success=True, now=NOW)
    assert "http" not in nf.render_notice(no_link, "telegram")

    bad = nf.build_run_notice(_job(), "Report.", success=True,
                              dashboard_url="javascript:alert(1)", now=NOW)
    assert "javascript" not in nf.render_notice(bad, "telegram")

    with_creds = nf.build_run_notice(
        _job(), "Report.", success=True,
        dashboard_url="https://user:secret@dash.example.com/hermes", now=NOW)
    rendered = nf.render_notice(with_creds, "telegram")
    assert "secret" not in rendered
    assert "https://dash.example.com/hermes" in rendered


def test_deliver_result_renders_each_target_in_its_transport_style():
    from cron.scheduler_delivery import _deliver_result
    from gateway.config import GatewayConfig, Platform, PlatformConfig

    sent: list[tuple[str, str]] = []

    async def sender(platform, pconfig, chat_id, content, **_kwargs):
        sent.append((platform.value, content))
        return {"success": True, "message_id": "m1"}

    cfg = GatewayConfig()
    cfg.platforms[Platform.TELEGRAM] = PlatformConfig(enabled=True)
    cfg.platforms[Platform.WHATSAPP] = PlatformConfig(enabled=True)
    job = {
        "id": "j1",
        "name": "Brief",
        "deliver": "telegram:111,whatsapp:222",
        "schedule": {"kind": "interval", "minutes": 5, "display": "every 5m"},
    }
    with patch("gateway.config.load_gateway_config", return_value=cfg),          patch("cron.scheduler.load_config", return_value={}),          patch("tools.send_message_tool._send_to_platform", new=sender),          patch("sys.is_finalizing", return_value=False):
        assert _deliver_result(job, "Body text.") is None

    rendered = dict(sent)
    assert "**Job:**" in rendered["telegram"]
    assert "**Job:**" not in rendered["whatsapp"]
    assert "*Job:*" in rendered["whatsapp"]


def test_telegram_conversion_escapes_user_text_and_keeps_links():
    from plugins.platforms.telegram.adapter import TelegramAdapter

    notice = nf.build_run_notice(
        _job(),
        "Price (up) 2%!",
        success=True,
        dashboard_url="https://dash.example.com/hermes",
        now=NOW,
    )
    rendered = nf.render_notice(notice, "telegram")
    converted = TelegramAdapter.format_message(None, rendered)

    assert "\\(" in converted and "\\)" in converted
    assert "\\!" in converted
    assert "*Job:*" in converted
    assert "[Open dashboard](https://dash.example.com/hermes)" in converted


def test_duration_is_humanised_at_each_scale():
    assert nf.humanize_duration(0.4) == "<1s"
    assert nf.humanize_duration(5) == "5s"
    assert nf.humanize_duration(64.2) == "1m 04s"
    assert nf.humanize_duration(3660) == "1h 01m"


def test_next_run_is_relative_and_absolute():
    assert nf.format_next_run(_job(), now=NOW) == "in 5m (2026-10-26 18:17 EET)"
    overdue = _job(schedule=None, next_run_at=(NOW - timedelta(minutes=10)).isoformat())
    assert nf.format_next_run(overdue, now=NOW) == "overdue by 10m (2026-10-26 18:02 EET)"
    assert nf.format_next_run({"id": "x", "schedule": {"kind": "once"}}, now=NOW) == "no further runs"
    completed_once = {"id": "x", "schedule": {"kind": "once"},
                      "next_run_at": (NOW - timedelta(seconds=1)).isoformat()}
    assert nf.format_next_run(completed_once, now=NOW) == "no further runs"


def test_legacy_wrapper_is_stripped_but_new_notices_pass_through():
    legacy = (
        "Cronjob Response: Morning brief\n"
        "(job_id: ab12cd34)\n"
        "-------------\n\n"
        "The report.\n\n"
        'To stop or manage this job, send me a new message (e.g. "stop reminder Morning brief").'
    )
    assert nf.strip_legacy_cron_wrapper(legacy) == "The report."
    notice = nf.build_run_notice(_job(), "The report.", success=True, now=NOW)
    rendered = nf.render_notice(notice, "telegram")
    assert nf.strip_legacy_cron_wrapper(rendered) == rendered


def test_delivery_redaction_happens_before_the_excerpt_is_bounded():
    from cron.scheduler_delivery import _deliver_result

    secret = "sk-" + "A" * 32
    send = AsyncMock(return_value={"success": True})
    pconfig = MagicMock()
    pconfig.enabled = True
    cfg = MagicMock()
    from gateway.config import Platform

    cfg.platforms = {Platform.TELEGRAM: pconfig}
    job = {
        "id": "secret-job",
        "name": f"rotate {secret} daily",
        "deliver": "telegram:123",
    }
    with patch("gateway.config.load_gateway_config", return_value=cfg), \
         patch("cron.scheduler.load_config", return_value={}), \
         patch("tools.send_message_tool._send_to_platform", new=send), \
         patch("sys.is_finalizing", return_value=False):
        _deliver_result(job, f"Token {secret} " + "x" * 600)

    delivered = " ".join(str(a) for a in send.call_args.args) + " " + " ".join(
        str(v) for v in send.call_args.kwargs.values())
    assert secret not in delivered
    assert "✅" in delivered
    assert "x" * 100 in delivered
