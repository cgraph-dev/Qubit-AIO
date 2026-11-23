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


def test_cron_profile_scope_and_exact_approval_replay(tmp_path, monkeypatch):
    from voicebridge import cron_bridge as CB
    from voicebridge import dashboard_ops as OPS
    from voicebridge import qubit_compat as HC

    home = tmp_path / "qubit"
    profile = home / "profiles" / "work"
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_text("{}\n")
    monkeypatch.setenv("QUBIT_HOME", str(home))
    cfg = _cfg(tmp_path, monkeypatch)
    route = "/api/cron/jobs"
    default = {"profile": "default"}
    work = {"profile": "work"}

    def request(method, query, body=None, suffix=""):
        return HC.dispatch(cfg, method, route + suffix, query, body or {})

    fields = {"name": "work report", "prompt": "Summarize notes",
              "schedule": "every 30m", "repeat": 3, "skills": ["fixture"],
              "context_from": ["self"], "enabled_toolsets": []}
    status, staged = request("POST", work, fields)
    assert status == 409, staged
    approval = staged["confirmation_id"]

    # An approval belongs to the original job settings and profile.
    status, _ = request("POST", default, {**fields, "confirmation_id": approval})
    assert status == 409
    status, _ = request("POST", work, {**fields, "repeat": 5, "confirmation_id": approval})
    assert status == 409
    assert request("GET", default) == (200, [])
    assert request("GET", work) == (200, [])

    result = OPS.confirm_op(cfg, approval, actor="dashboard:user", session="dashboard")
    assert result["state"] == "executed", result
    status, rows = request("GET", work)
    assert status == 200 and len(rows) == 1, rows
    job = rows[0]
    assert job["profile"] == "work"
    assert job["repeat"]["times"] == 3
    assert job["skills"] == ["fixture"]
    assert job["context_from"] == ["self"]
    assert job["enabled_toolsets"] == []
    suffix = f"/{job['id']}"

    # A copied job ID is valid in both stores. Each row retains its owner.
    with CB.profile_scope(cfg, "default"):
        J = CB._store(cfg)
        J.save_jobs([{**job, "name": "Default report", "prompt": "Default notes"}])
    status, rows = request("GET", {"profile": "all"})
    assert status == 200
    assert {(j["profile"], j["id"]) for j in rows} == {
        ("default", job["id"]), ("work", job["id"])}

    with CB.profile_scope(cfg, "work"):
        E = CB._executions(cfg)
        run = E.create_execution(job["id"], source="manual")
        E.finish_execution(run["id"], success=True)
    assert request("GET", work, suffix=suffix + "/runs")[1]["count"] == 1
    assert request("GET", default, suffix=suffix + "/runs")[1]["count"] == 0

    original_next = job["next_run_at"]
    status, staged = request("PUT", work, {"name": "edited", "schedule": "every 30m",
                                          "enabled_toolsets": ["web"]}, suffix)
    assert status == 409, staged
    assert OPS.confirm_op(cfg, staged["confirmation_id"], actor="dashboard:user",
                          session="dashboard")["state"] == "executed"
    status, edited = request("GET", work, suffix=suffix)
    assert status == 200 and edited["name"] == "edited"
    assert edited["next_run_at"] == original_next
    assert edited["enabled_toolsets"] == ["web"]
    assert request("GET", default, suffix=suffix)[1]["name"] == "Default report"

    status, staged = request("POST", work, suffix=suffix + "/pause")
    assert status == 409, staged
    assert OPS.confirm_op(cfg, staged["confirmation_id"], actor="dashboard:user",
                          session="dashboard")["state"] == "executed"
    assert request("GET", work, suffix=suffix)[1]["state"] == "paused"
    assert request("GET", default, suffix=suffix)[1]["state"] == "scheduled"

    status, edited = request("PUT", work, {"confirm": True, "name": "still paused"}, suffix)
    assert status == 200 and edited["state"] == "paused"
    status, queued = request("POST", work, suffix=suffix + "/trigger")
    assert status == 200 and queued["result"]["profile"] == "work"
    status, _ = request("DELETE", work, {"confirm": True}, suffix)
    assert status == 200
    assert request("GET", work, suffix=suffix)[0] == 404
    assert request("GET", default, suffix=suffix)[0] == 200
    assert request("GET", {"profile": "missing"})[0] == 404
    assert request("GET", {"profile": "../invalid"})[0] == 400


def test_blueprint_approval_id_creates_job_in_selected_profile(tmp_path, monkeypatch):
    from voicebridge import cron_bridge as CB
    from voicebridge import qubit_compat as HC

    home = tmp_path / "qubit"
    profile = home / "profiles" / "work"
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_text("{}\n")
    monkeypatch.setenv("QUBIT_HOME", str(home))
    cfg = _cfg(tmp_path, monkeypatch)
    route = "/api/cron/blueprints/instantiate"
    query = {"profile": "work"}
    body = {"blueprint": "custom-reminder", "values": {"what": "check fixtures", "time": "09:30"}}
    status, staged = HC.dispatch(cfg, "POST", route, query, body)
    assert status == 409, staged
    confirmed = {**body, "confirmation_id": staged["confirmation_id"]}
    status, job = HC.dispatch(cfg, "POST", route, query, confirmed)
    assert status == 200 and job["profile"] == "work", job
    assert "check fixtures" in job["prompt"]
    assert CB.list_jobs(cfg, profile="default") == []
    assert len(CB.list_jobs(cfg, profile="work")) == 1
    status, _ = HC.dispatch(cfg, "POST", route, query, confirmed)
    assert status == 409
    assert len(CB.list_jobs(cfg, profile="work")) == 1


def test_plugin_blueprints_follow_the_requested_cron_profile(tmp_path, monkeypatch):
    from dataclasses import replace
    from voicebridge import qubit_compat as HC
    from voicebridge import qubit_runtime

    qubit_runtime.ensure_path()
    from cron import blueprint_catalog, blueprint_plugins
    from qubit_constants import get_qubit_home
    root = tmp_path / "qubit"
    profile = root / "profiles" / "work"
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_text("{}\n")
    monkeypatch.setenv("QUBIT_HOME", str(root))
    cfg = _cfg(tmp_path, monkeypatch)

    blueprint = replace(blueprint_catalog.get_blueprint("custom-reminder"), key="fixture:reminder")
    monkeypatch.setattr(blueprint_plugins, "plugin_blueprints",
                        lambda: [blueprint] if get_qubit_home() == profile else [])
    # Channel discovery is tested separately. This fixture has no configured delivery.
    from voicebridge import cron_bridge
    monkeypatch.setattr(cron_bridge, "delivery_targets", lambda *_: [])
    route = "/api/cron/blueprints"
    for scope, expected in (("default", False), ("work", True)):
        status, result = HC.dispatch(cfg, "GET", route, {"profile": scope})
        assert status == 200, result
        assert ("fixture:reminder" in {r["key"] for r in result["blueprints"]}) is expected

    status, result = HC.dispatch(cfg, "POST", route + "/instantiate", {"profile": "work"}, {
        "blueprint": "fixture:reminder", "values": {"what": "summarize", "time": "09:30"},
        "confirm": True,
    })
    assert status == 200 and result["profile"] == "work", result
