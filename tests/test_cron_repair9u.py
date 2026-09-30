"""Cron repair (2026-09-29 audit) — dashboard schedule modes, in-place edit,
and delivery preference.

Defects fixed here (audit ``qa-cron-mem-boards``):
  * Daily/Weekly/Monthly/Once cron + ISO schedule strings were rejected by
    ``schedules.parse_when`` (only natural-language forms were accepted).
  * The edit modal's "Save changes" was dead: the PUT handler only accepted an
    ``enabled`` transition. ``schedules.set_paused`` now delegates a body that
    carries editable fields to ``schedules.update_job``.
  * The dashboard ``deliver`` choice was silently dropped; the store now keeps
    the display preference while never fabricating a transport target.

Hermetic: temp XDG/state only; no live service, network, or hardware.
"""
from __future__ import annotations

from datetime import datetime

import pytest

pytestmark = pytest.mark.unit


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


# ------------------------------------------------ defect 1: schedule parsing
def test_parse_when_accepts_dashboard_cron_and_iso():
    from voicebridge import schedules as SC

    assert SC.parse_when("0 9 * * *") == {
        "kind": "cron", "cron_expr": "0 9 * * *"}
    assert SC.parse_when("30 14 * * 1,3,5") == {
        "kind": "cron", "cron_expr": "30 14 * * 1,3,5"}
    assert SC.parse_when("0 9 1 * *") == {
        "kind": "cron", "cron_expr": "0 9 1 * *"}
    once = SC.parse_when("2026-12-31T12:00:00")
    assert once["kind"] == "once"
    assert once["at_ts"] == datetime.fromisoformat(
        "2026-12-31T12:00:00").timestamp()
    # The natural-language forms the voice path uses are unchanged.
    assert SC.parse_when("every 30 minutes")["interval_min"] == 30
    assert SC.parse_when("daily 9am")["hour"] == 9


def test_parse_when_rejects_malformed_cron_and_iso():
    from voicebridge import schedules as SC

    for bad in (
        "0 9 * *",          # 4 fields
        "0 9 * * * *",      # 6 fields
        "0 25 * * *",       # hour out of range
        "60 9 * * *",       # minute out of range
        "0 9 0 * *",        # day-of-month out of range
        "0 9 * 13 *",       # month out of range
        "every 2 minutes",  # existing interval floor
        "sometime",
    ):
        with pytest.raises(SC.SchedulesError) as exc:
            SC.parse_when(bad)
        assert exc.value.code == SC.BAD_INPUT_CODE, bad


def test_cron_next_run_projects_honest_wall_clock(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    base = 1790640000.0  # 2026-09-28T21:20:00Z
    weekly = SC.parse_when("30 14 * * 1,3,5")
    job = {"kind": "cron", "cron_expr": weekly["cron_expr"], "enabled": True}
    nxt = SC.next_run_at(job, now=base)
    assert nxt is not None and nxt > base
    local = datetime.fromtimestamp(nxt)
    assert (local.hour, local.minute) == (14, 30)
    assert local.weekday() in (0, 2, 4)  # Mon/Wed/Fri

    monthly = SC.parse_when("0 9 1 * *")
    mjob = {"kind": "cron", "cron_expr": monthly["cron_expr"], "enabled": True}
    mnxt = SC.next_run_at(mjob, now=base)
    assert mnxt is not None
    mlocal = datetime.fromtimestamp(mnxt)
    assert (mlocal.day, mlocal.hour, mlocal.minute) == (1, 9, 0)


def test_cron_due_is_coalesced_and_one_shot(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    job_id = SC.create(cfg, "every minute", "t", "* * * * *",
                       confirmed=True)["job"]["id"]
    now = 1790640000.0
    row = SC.get_job(cfg, job_id)["job"]
    assert row["kind"] == "cron" and row["cron_expr"] == "* * * * *"
    assert SC._due(row, now, cfg) is True
    row["last_run_at"] = now
    assert SC._due(row, now, cfg) is False


# ------------------------------------------------- defect 2: in-place edit
def test_edit_body_through_set_paused_applies_fields(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    job_id = SC.create(cfg, "before", "old task", "every 30 minutes",
                       confirmed=True)["job"]["id"]

    body = {"name": "after", "prompt": "new task",
            "schedule": "0 9 1 * *", "when": "0 9 1 * *",
            "deliver": "local", "enabled": True, "confirm": True}
    out = SC.set_paused(cfg, job_id, False, requested_by="dashboard:user",
                        confirmed=True, body=body, query={})
    job = out["job"]
    assert job["name"] == "after"
    assert job["task_text"] == "new task"
    assert job["kind"] == "cron" and job["cron_expr"] == "0 9 1 * *"
    assert job["enabled"] is True

    # Persisted, not just echoed.
    reread = SC.get_job(cfg, job_id)["job"]
    assert reread["name"] == "after" and reread["cron_expr"] == "0 9 1 * *"


def test_bare_confirm_still_only_toggles_enabled(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    job_id = SC.create(cfg, "toggle", "task", "every 30 minutes",
                       confirmed=True)["job"]["id"]
    SC.set_paused(cfg, job_id, True, confirmed=True,
                  body={"confirm": True}, query={})
    assert SC.get_job(cfg, job_id)["job"]["enabled"] is False
    assert SC.get_job(cfg, job_id)["job"]["name"] == "toggle"


# --------------------------------------------- defect 4: delivery preference
def test_deliver_preference_persisted_without_fabricated_transport(tmp_path,
                                                                   monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    local_id = SC.create(cfg, "local", "t", "every 30 minutes",
                         deliver_to="local", confirmed=True)["job"]["id"]
    local = SC.get_job(cfg, local_id)["job"]
    assert local["deliver_pref"] == "local"
    # "local" means save-only: no transport spec is invented.
    assert local["deliver_to"] == ""

    platform_id = SC.create(cfg, "platform", "t", "every 30 minutes",
                            deliver_to="telegram", confirmed=True)["job"]["id"]
    platform = SC.get_job(cfg, platform_id)["job"]
    assert platform["deliver_pref"] == "telegram"
    assert platform["deliver_to"] == ""


def test_banner_runner_executes_cron_create_from_staged_payload(tmp_path,
                                                                monkeypatch):
    """The pending-approvals banner can settle a staged cron create."""
    from voicebridge import dashboard_ops as OPS
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    for action in ("cron.create", "cron.update", "cron.delete"):
        assert OPS._compat_runner(action) is not None, action

    runner = OPS._compat_runner("cron.create")
    code, out = runner(cfg, {"name": "banner-created", "task_text": "t",
                             "when": "0 9 * * *", "deliver": "local"})
    assert code == 200, out
    name = out.get("name") or out.get("job", {}).get("name")
    assert name == "banner-created", out
    assert SC.list_jobs(cfg)["jobs"], "job not persisted by banner runner"


def test_blueprint_schedule_template_is_accepted_by_store():
    """The dashboard sends only slot values; the resolved cron must parse."""
    from voicebridge import qubit_runtime, schedules as SC

    qubit_runtime.ensure_path()
    try:
        from cron.blueprint_catalog import fill_blueprint, get_blueprint
    except Exception:  # pragma: no cover - vendored catalog always present
        pytest.skip("vendored blueprint catalog unavailable")

    bp = get_blueprint("morning-brief")
    assert bp is not None
    spec = fill_blueprint(bp, {"time": "08:00", "deliver": "local"})
    assert spec["schedule"] == "0 8 * * *"
    assert SC.parse_when(spec["schedule"]) == {
        "kind": "cron", "cron_expr": "0 8 * * *"}
