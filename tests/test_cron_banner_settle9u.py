"""Cron pause/resume/trigger settle through the pending-approvals banner.

The row actions stage via ``_cron_mutate`` (``payload={"id": job_id}``), and the
banner's id-only ``POST /api/ops/confirm`` must execute the real cron store
backend exactly once — not fail ``CONFIRM_UNSUPPORTED``. The dashboard cron
surface is backed by ``voicebridge.cron_bridge`` (the vendored ``cron.jobs``
JSON store the live scheduler fires from), not the legacy
``voicebridge.schedules`` SQLite store.

Hermetic: temp XDG/QUBIT_HOME only; no live service, network, or hardware.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

pytestmark = pytest.mark.unit


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        (tmp_path / var.lower()).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_compat_runner_has_cron_row_actions():
    from voicebridge import dashboard_ops as OPS

    for action in ("cron.pause", "cron.resume", "cron.run_due"):
        assert OPS._compat_runner(action) is not None, action


def _make_job(cfg):
    from voicebridge import qubit_compat as HC

    code, out = HC.cron_create(cfg, {},
                               {"name": "banner-settle-probe",
                                "prompt": "echo probe",
                                "when": "every 60 minutes",
                                "confirm": True}, {})
    assert code == 200, out
    job_id = out.get("id")
    assert job_id, out
    return job_id


def _paused(cfg, job_id) -> bool:
    from voicebridge import cron_bridge as CB

    job = CB.get_job(cfg, job_id)
    return bool(job) and not bool(job.get("enabled", True))


def test_staged_cron_pause_settles_through_banner(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge import dashboard_ops as OPS
    from voicebridge import qubit_compat as HC

    job_id = _make_job(cfg)
    assert _paused(cfg, job_id) is False

    code, staged = HC._cron_mutate("pause", cfg, {}, {}, {"id": str(job_id)})
    assert code == 409, staged
    assert staged.get("code") == "CONFIRM_REQUIRED", staged
    cid = staged.get("confirmation_id")
    assert cid, staged
    # nothing executed before confirmation
    assert _paused(cfg, job_id) is False

    out = OPS.confirm_op(cfg, cid, actor="dashboard:user",
                         session="dashboard")
    assert out["state"] == "executed", out
    assert _paused(cfg, job_id) is True

    # settle-once: a replay cannot double-apply
    again = OPS.confirm_op(cfg, cid, actor="dashboard:user",
                           session="dashboard")
    assert again["state"] == "failed", again


def test_staged_cron_resume_settles_through_banner(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge import cron_bridge as CB
    from voicebridge import dashboard_ops as OPS
    from voicebridge import qubit_compat as HC

    job_id = _make_job(cfg)
    CB.pause(cfg, job_id)
    assert _paused(cfg, job_id) is True

    code, staged = HC._cron_mutate("resume", cfg, {}, {}, {"id": str(job_id)})
    assert code == 409, staged
    cid = staged.get("confirmation_id")
    assert cid, staged

    out = OPS.confirm_op(cfg, cid, actor="dashboard:user",
                         session="dashboard")
    assert out["state"] == "executed", out
    assert _paused(cfg, job_id) is False
