"""``qubit cron preview`` renders the real notice without sending or mutating anything.

The preview is a read-only surface: it resolves a real job, builds the same notice delivery
would build (from the last saved output artifact when one exists), and renders it for one
transport. These tests pin the no-send/no-mutation contract, the per-transport text, the
configured-URL-only link rule (and its escaping), and the unknown-input exit codes.
"""

from __future__ import annotations

import json
from argparse import Namespace
from unittest.mock import AsyncMock, patch

import pytest

import cron.scheduler_delivery as sd
from cron import jobs as cron_jobs
from cron.jobs import create_job
from qubit_cli import cron as cron_cli
from qubit_cli.cron import cron_preview


@pytest.fixture()
def tmp_cron_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("cron.jobs.CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", tmp_path / "cron" / "output")
    monkeypatch.setattr("qubit_constants.get_qubit_home", lambda: tmp_path)
    return tmp_path


def _job_with_artifact(tmp_cron_dir, body="Markets are up 2%.\nReady for review."):
    job = create_job(prompt="Daily report", schedule="every 1h", name="Preview job")
    out_dir = cron_jobs._job_output_dir(job["id"])
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact = out_dir / "2026-10-26_18-12-00.md"
    artifact.write_text(body, encoding="utf-8")
    return job, artifact


def _preview(job_ref, transport=None, as_json=False):
    return cron_preview(Namespace(job_id=job_ref, transport=transport, json=as_json))


def test_preview_renders_the_real_notice_without_sending_or_mutating(tmp_cron_dir, capsys):
    job, artifact = _job_with_artifact(tmp_cron_dir)
    jobs_file = tmp_cron_dir / "cron" / "jobs.json"
    before_bytes = jobs_file.read_bytes()
    before_job = dict(cron_jobs.get_job(job["id"]))
    before_files = sorted(p.name for p in artifact.parent.iterdir())

    sender = AsyncMock(side_effect=AssertionError("preview must never send"))
    with patch("qubit_cli.cron._preview_dashboard_url", return_value=""), \
         patch("tools.send_message_tool._send_to_platform", new=sender):
        assert _preview(job["id"], "plain") == 0

    out = capsys.readouterr().out
    assert "Preview job" in out
    assert "Markets are up 2%." in out
    assert "Saved: cron/output/" in out
    assert sender.await_count == 0
    assert jobs_file.read_bytes() == before_bytes
    assert dict(cron_jobs.get_job(job["id"])) == before_job
    assert sorted(p.name for p in artifact.parent.iterdir()) == before_files


def test_preview_accepts_a_job_name_and_defaults_to_the_delivery_transport(tmp_cron_dir, capsys):
    job, _ = _job_with_artifact(tmp_cron_dir)
    job["deliver"] = "whatsapp:222"
    cron_jobs.save_jobs([job])

    assert _preview("Preview job") == 0
    out = capsys.readouterr().out
    assert "*Job:*" in out and "**Job:**" not in out


@pytest.mark.parametrize(
    "transport,expected_bold,forbidden",
    [
        ("discord", "**Job:**", "\n*Job:*"),
        ("whatsapp", "*Job:*", "**Job:**"),
        ("plain", "Job:", "*Job:*"),
    ],
)
def test_per_transport_renders(tmp_cron_dir, capsys, transport, expected_bold, forbidden):
    job, _ = _job_with_artifact(tmp_cron_dir)
    with patch("qubit_cli.cron._preview_dashboard_url", return_value=""):
        assert _preview(job["id"], transport) == 0
    out = capsys.readouterr().out
    assert expected_bold in out
    assert forbidden not in out
    assert out.count("✅") == 1


def test_telegram_preview_is_markdownv2_and_escapes_links(tmp_cron_dir, capsys):
    job, _ = _job_with_artifact(tmp_cron_dir)
    with patch("qubit_cli.cron._preview_dashboard_url",
               return_value="https://dash.example.com/a(b)c"):
        assert _preview(job["id"], "telegram") == 0
    out = capsys.readouterr().out
    assert "*Job:*" in out and "**Job:**" not in out
    assert "https://dash.example.com/a%28b%29c" in out
    assert "a\\(b\\)c" not in out
    assert "Open dashboard" in out


def test_telegram_link_escaping_never_leaves_a_stray_backslash():
    from cron.notice_format import CronNotice, render_notice
    from plugins.platforms.telegram.adapter import TelegramAdapter

    notice = CronNotice(kind="success", job_name="Job", link="https://example.com/a(b)c")
    converted = TelegramAdapter.format_message(None, render_notice(notice, "telegram"))
    assert "https://example.com/a%28b%29c" in converted
    assert "\\(" not in converted and "\\)" not in converted


def test_preview_never_invents_a_url_when_none_is_configured(tmp_cron_dir, capsys):
    job, _ = _job_with_artifact(tmp_cron_dir)
    with patch("qubit_cli.cron._preview_dashboard_url", return_value=""):
        assert _preview(job["id"], "telegram") == 0
    out = capsys.readouterr().out
    assert "http" not in out


def test_preview_json_reports_the_rendered_text_and_no_side_effects(tmp_cron_dir, capsys):
    job, _ = _job_with_artifact(tmp_cron_dir)
    with patch("qubit_cli.cron._preview_dashboard_url",
               return_value="https://dash.example.com/hermes"):
        assert _preview(job["id"], "telegram", as_json=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["job_id"] == job["id"]
    assert payload["transport"] == "telegram"
    assert payload["kind"] == "success"
    assert payload["sent"] is False and payload["mutated"] is False
    assert payload["link"] == "https://dash.example.com/hermes"
    assert payload["link_preview"] is True
    assert "**Job:**" not in payload["text"]


def test_preview_unknown_job_and_transport_exit_2(tmp_cron_dir, capsys):
    assert _preview("does-not-exist", "telegram") == 2
    assert "not found" in capsys.readouterr().out.lower()

    job, _ = _job_with_artifact(tmp_cron_dir)
    assert _preview(job["id"], "email") == 2
    assert "unknown transport" in capsys.readouterr().out.lower()


def test_preview_paused_job_uses_the_paused_kind(tmp_cron_dir, capsys):
    job = create_job(prompt="Daily report", schedule="every 1h", name="Paused job")
    job["enabled"] = False
    job["state"] = "paused"
    cron_jobs.save_jobs([job])
    with patch("qubit_cli.cron._preview_dashboard_url", return_value=""):
        assert _preview(job["id"], "plain") == 0
    out = capsys.readouterr().out
    assert out.startswith("⏸️ Paused job — paused")
    assert out.count("⏸️") == 1


class _Native:
    async def send(self, chat_id, content, metadata=None):
        return {"success": True, "message_id": "m1"}

    async def send_for_platform(self, *args, **kwargs):  # pragma: no cover - not used
        return await self.send(*args, **kwargs)


def _target():
    from gateway.config import GatewayConfig, Platform

    return sd._prepare_target_delivery(
        {"id": "job-1"}, {"platform": "telegram", "chat_id": "555"},
        adapters={Platform.TELEGRAM: _Native()}, loop=None,
        config=GatewayConfig(), notify_delivery=True, mirror_enabled=False,
        mirror_text="", delivery_errors=[])


def test_link_preview_is_enabled_only_when_the_payload_has_a_link():
    target = _target()
    assert target is not None

    _, with_link, _ = sd._live_route_metadata(
        target, text="[Open dashboard](https://dash.example.com/hermes)")
    assert with_link["disable_link_preview"] is False

    _, bare, _ = sd._live_route_metadata(target, text="✅ Job — completed")
    assert bare["disable_link_preview"] is True

    _, default, _ = sd._live_route_metadata(target)
    assert default["disable_link_preview"] is True
