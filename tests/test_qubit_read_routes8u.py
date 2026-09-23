"""Small missing Hermes read routes (audit §1 small readers + gap #15).

Dispatches the audit's "small reader" routes through the real compat backend
against real Qubit modules and asserts they answer 200 (or a typed gate) with
the upstream wire keys -- never a 404/500. Also spot-checks that adding the
routes did not shadow pre-existing ones.

* ``GET /api/health``                    -> ``h_health`` (lifecycle health + status)
* ``GET /api/cron/jobs/{id}/runs``       -> ``h_cron_job_runs`` (schedules.history)
* ``GET /api/model/recommended-default`` -> ``h_model_recommended_default``
* ``GET /api/egress/status``             -> ``h_egress_status`` (proxy + frontier policy)
* ``GET /api/ssh/ownership``             -> typed gate (no SSH ownership module)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


# Route -> handler expectations for the small readers added here.
NEW_ROUTES = [
    ("GET", "/api/health", "h_health"),
    ("GET", "/api/cron/jobs/1/runs", "h_cron_job_runs"),
    ("GET", "/api/model/recommended-default", "h_model_recommended_default"),
    ("GET", "/api/egress/status", "h_egress_status"),
]


def test_new_read_routes_resolve_to_handlers():
    """Every new reader is matched by the real route table (no 404 path)."""
    from voicebridge import qubit_compat as HC

    for method, path, handler in NEW_ROUTES:
        route, _ = HC.match(method, path)
        assert route is not None, (method, path)
        assert route["disposition"] == "implemented", (method, path)
        assert route["handler"] == handler, (method, path, route["handler"])


def test_all_new_routes_dispatch_typed_never_404_500(tmp_path, monkeypatch):
    """Each new route answers 200 or a typed gate -- never 404/500."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    for method, path, _handler in NEW_ROUTES:
        code, out = HC.dispatch(cfg, method, path, {}, {})
        assert code not in (404, 500), (method, path, code, str(out)[:200])
        assert code == 200, (method, path, code, str(out)[:200])
        assert out.get("ok") is True, (method, path, out)

    code, out = HC.dispatch(cfg, "GET", "/api/ssh/ownership", {}, {})
    assert code not in (404, 500), (code, out)
    assert code == 403, (code, out)


def test_health_route_shape(tmp_path, monkeypatch):
    """GET /api/health -> live liveness with health state, never fabricated."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/health", {}, {})
    assert code == 200, (code, out)
    for key in ("ok", "version", "auth_required", "state", "detail"):
        assert key in out, (key, out)
    assert out["auth_required"] is False
    assert isinstance(out["version"], str) and out["version"]
    assert isinstance(out["state"], str) and out["state"]


def test_health_degrades_instead_of_failing(tmp_path, monkeypatch):
    """A broken health inventory degrades the state, never a 500."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    import voicebridge.lifecycle as LC

    def _boom(*_a, **_k):
        raise RuntimeError("inventory unavailable")

    monkeypatch.setattr(LC, "health_inventory", _boom)
    code, out = HC.dispatch(cfg, "GET", "/api/health", {}, {})
    assert code == 200, (code, out)
    assert out["state"] == "DEGRADED", out
    assert "inventory unavailable" in out["detail"]


def test_cron_runs_route_shape_and_real_history(tmp_path, monkeypatch):
    """GET /api/cron/jobs/{id}/runs reflects the real schedules run log."""
    from voicebridge import qubit_compat as HC
    from voicebridge import schedules as S

    cfg = _cfg(tmp_path, monkeypatch)
    created = S.create(cfg, "audit-read", "summarize notes",
                       "once in 10 minutes", requested_by="test",
                       confirmed=True)
    job_id = int(created["job"]["id"])
    fired = S.run_due(
        cfg, requested_by="test",
        executor=lambda *_a, **_k: {"state": "OK", "code": "OK",
                                    "detail": "done"},
        now=time.time() + 3600, estop_engaged=lambda: False)
    assert fired["ran"] == 1, fired

    code, out = HC.dispatch(cfg, "GET", f"/api/cron/jobs/{job_id}/runs",
                            {}, {})
    assert code == 200, (code, out)
    for key in ("ok", "runs", "limit", "count"):
        assert key in out, (key, out)
    assert out["count"] == 1 and len(out["runs"]) == 1, out
    run = out["runs"][0]
    for key in ("id", "job_id", "started_at", "finished_at", "ended_at",
                "state", "code", "detail", "is_active", "archived"):
        assert key in run, (key, run)
    assert run["job_id"] == job_id
    assert run["state"] == "OK" and run["code"] == "OK"


def test_cron_runs_non_numeric_id_is_not_500(tmp_path, monkeypatch):
    """A non-numeric upstream job id reports no runs instead of scanning."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/cron/jobs/not-a-number/runs",
                            {}, {})
    assert code == 200, (code, out)
    assert out["runs"] == [] and out["count"] == 0


def test_model_recommended_default_shape(tmp_path, monkeypatch):
    """GET /api/model/recommended-default resolves the configured brain."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/model/recommended-default",
                            {}, {})
    assert code == 200, (code, out)
    for key in ("ok", "provider", "model", "free_tier"):
        assert key in out, (key, out)
    assert out["model"], out
    assert out["free_tier"] is None

    # A requested cloud provider is echoed honestly, never silently faked.
    code, out = HC.dispatch(cfg, "GET", "/api/model/recommended-default",
                            {"provider": "nous"}, {})
    assert code == 200, (code, out)
    assert out["provider"] == "nous" and out["model"] == ""


def test_egress_status_shape(tmp_path, monkeypatch):
    """GET /api/egress/status -> real proxy + frontier policy state."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/egress/status", {}, {})
    assert code == 200, (code, out)
    for key in ("ok", "text", "proxy", "frontier"):
        assert key in out, (key, out)
    assert isinstance(out["text"], str) and out["text"]
    assert out["proxy"]["state"] == "NOT_RUNNING", out["proxy"]
    assert "enabled" in out["frontier"], out["frontier"]


def test_ssh_ownership_is_typed_gate(tmp_path, monkeypatch):
    """GET /api/ssh/ownership has no backing module -> typed gate."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    route, _ = HC.match("GET", "/api/ssh/ownership")
    assert route is not None and route["disposition"] == "gated", route
    code, out = HC.dispatch(cfg, "GET", "/api/ssh/ownership", {}, {})
    assert code == 403, (code, out)
    assert out["ok"] is False
    assert out["state"] == "UNAVAILABLE"
    assert out["code"] == "SSH_OWNERSHIP_OPERATOR"
    assert out["qubit_counterpart"]
    assert route["gate"]["code"] == out["code"]
    assert route["gate"]["counterpart"] == out["qubit_counterpart"]


def test_pre_existing_routes_not_shadowed(tmp_path, monkeypatch):
    """Spot-check that the new routes did not shadow existing ones."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    expected = {
        "/api/status": "h_status",
        "/api/sessions": "h_sessions_list",
        "/api/ops": "h_ops_catalog",
    }
    for path, handler in expected.items():
        route, _ = HC.match("GET", path)
        assert route is not None, path
        assert route["handler"] == handler, (path, route["handler"])
        code, out = HC.dispatch(cfg, "GET", path, {}, {})
        assert code == 200, (path, code, str(out)[:200])
        assert out.get("ok") is True, (path, out)
