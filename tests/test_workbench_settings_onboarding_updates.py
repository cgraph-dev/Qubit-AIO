"""Behavioural proof for the settings / onboarding / updates family.

Each test drives the REAL gateway (``h_wb_gateway``) with the same
``(cfg, query, body, params)`` tuple the listener builds, against the temp
``QUBIT_HOME`` the session fixture installs. Nothing here mocks the handler under
test; what IS injected is only the thing under suspicion (the HTTP probe, the
update manager) so the test can prove the handler reads the real answer rather
than a plausible one.

WHAT THIS PINS, AND WHY EACH ONE WOULD FAIL WITHOUT THE WORK
---------------------------------------------------------------------------
* ``model/auxiliary`` serves the live ``auxiliary`` config, and the whole reply
  carries no credential field — the security rule for this family, asserted on
  the actual bytes.
* ``provider/cost-history`` sums Qubit's OWN recorded spend and returns NO
  snapshot for an empty day (the panel's "not enough data" branch keys on that).
* ``provider/quota`` and ``models/refresh`` are REFUSALS with the reason and a
  counterpart: the first because no quota state exists, the second because
  answering it means moving a provider secret into this listener.
* ``providers/self-hosted`` refuses the write and says so louder when the body
  carried an ``api_key``.
* ``onboarding/probe`` reduces the provider's answer to ``id``/``label`` and
  reports an unreachable endpoint as ``ok: false`` with the reason (the
  wizard's own branch), not a 500.
* ``onboarding/setup`` refuses an ``api_key`` and writes NOTHING.
* ``onboarding/complete`` refuses when a turn is not possible and stamps the
  acknowledgement only when it is.
* ``system/health`` reports the three metrics the panel paints.
* ``gateway/status`` reports the daemon and marks the lifecycle read-only, and
  the lifecycle verbs (``gateway/start`` …) plus ``health/restart`` are refusals
  naming ``qubit gateway``.
* ``git-info`` reports real repo facts for a registered workspace and hides
  itself for a path outside the roots.
* ``updates/check`` reports QUBIT's update source (the manager's answer), not a
  hardcoded "no update", and the four updater WRITES are named refusals.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from voicebridge import workbench_ui_routes as R
from voicebridge.workbench_ui import handlers_settings as HS


# --------------------------------------------------------------------- helpers
def call(path: str, *, method: str = "GET", query=None, body=None, cfg=None):
    """Drive the single gateway row exactly as the listener does.

    ``cfg`` defaults to a real ``Config`` (the listener always passes one). A
    ``None`` cfg is still accepted so a test can prove a handler degrades rather
    than raising, but anything touching the project registry — the confinement
    every workspace read goes through — needs the real object.
    """
    sub = path.lstrip("/")
    params = {"sub": sub, "method": method.upper(), "rest": sub}
    if "/" in sub:
        params.setdefault("action", sub.rsplit("/", 1)[-1])
    return R.h_wb_gateway(cfg if cfg is not None else _cfg(), dict(query or {}),
                          dict(body or {}), params)


def _cfg():
    from voicebridge.config import Config

    return Config.load()


def _runtime_home() -> Path:
    from qubit_cli.config import get_config_path  # type: ignore

    return Path(get_config_path()).parent


SECRET_VALUE = "sk-do-not-leak-1234567890"


# -------------------------------------------------------------- route set
@pytest.mark.parametrize("path", [
    "model/auxiliary", "providers/self-hosted", "provider/cost-history",
    "provider/quota", "models/refresh", "onboarding/probe",
    "onboarding/setup", "onboarding/complete", "onboarding/oauth/start",
    "onboarding/oauth/poll", "onboarding/oauth/cancel", "system/health",
    "health/restart", "gateway/status", "gateway/start", "gateway/stop",
    "gateway/restart", "git-info", "updates/apply", "updates/force",
    "updates/clear_lock", "updates/summary",
])
def test_every_family_path_resolves_to_a_registered_handler(path):
    """A PORT_SET row naming a missing handler answers ``WB_NOT_IN_PORT_SET``
    even though the work was done — this is the receipt that it was not."""
    handler = R._resolve_handler(path, "GET")
    assert handler is not None, f"{path} resolves to no handler"
    assert R._HANDLERS.get(handler.__name__) is handler, (
        f"{path} -> {handler.__name__}, which is not in _HANDLERS")


def test_the_settings_family_is_no_longer_gap():
    """The five settings-group endpoints the wave answered left the GAP set."""
    from voicebridge.workbench_ui import _endpoint_table as ET

    port, _existing, gap, _eps = ET.verdicts()
    for path in ("/api/gateway/status", "/api/git-info", "/api/system/health",
                 "/api/model/auxiliary", "/api/provider/quota",
                 "/api/providers/self-hosted", "/api/models/refresh"):
        assert path not in gap, f"{path} is still counted GAP"
        assert path in port, f"{path} is answered but not counted PORT"


# ------------------------------------------------------------- no secrets
def _walk_strings(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_strings(v)
    elif isinstance(obj, str):
        yield obj


def test_model_auxiliary_serves_real_tasks_and_never_a_credential():
    code, out = call("model/auxiliary")
    assert code == 200, out
    assert out["ok"] is True
    assert isinstance(out["tasks"], list) and out["tasks"], (
        "the vendored auxiliary slot list is never empty")
    for row in out["tasks"]:
        assert row["task"], row
        assert "provider" in row and "model" in row
        # The security rule: no key, no reference, no token in any row.
        for value in _walk_strings(row):
            assert "sk-" not in value
        assert "api_key" not in row and "key_ref" not in row
    assert "main" in out


# --------------------------------------------------------------- cost / quota
def test_cost_history_sums_the_recorded_spend_and_leaves_empty_days_out(
        monkeypatch):
    db = _runtime_home() / "sessions.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessions (id TEXT, started_at REAL, "
        "estimated_cost_usd REAL, actual_cost_usd REAL)")
    conn.execute("DELETE FROM sessions")
    import time

    now = time.time()
    conn.execute("INSERT INTO sessions VALUES (?,?,?,?)", ("a", now, 0.25, 0.0))
    conn.execute("INSERT INTO sessions VALUES (?,?,?,?)", ("b", now, 0.10, 0.0))
    conn.execute("INSERT INTO sessions VALUES (?,?,?,?)",
                 ("old", now - 40 * 86400, 99.0, 0.0))
    conn.commit()
    conn.close()

    code, out = call("provider/cost-history")
    assert code == 200, out
    snaps = out["snapshots"]
    # ONE row for today: the 40-day-old session is outside the window.
    assert len(snaps) == 1, snaps
    assert snaps[0]["delta"] == pytest.approx(0.35), snaps
    # No budget field exists, so the panel hides its controls on null.
    assert out["monthly_budget"] is None


def test_cost_history_with_no_database_answers_an_empty_window():
    code, out = call("provider/cost-history")
    assert code == 200, out
    assert out["snapshots"] == []
    assert "no session database" in out["detail"]


def test_provider_quota_is_a_named_refusal_with_a_counterpart():
    code, out = call("provider/quota")
    assert code == 501, out
    assert out["code"] == "WB_CAPABILITY_UNBOUND"
    assert "quota" in out["detail"].lower()
    assert out["qubit_counterpart"], "a refusal must point somewhere real"


def test_models_refresh_refuses_because_it_would_need_the_stored_credential():
    code, out = call("models/refresh", method="POST", body={"provider": "ollama"})
    assert code == 403, out
    assert out["code"] == "WB_CREDENTIALS_NOT_IN_CONFIG"
    assert "credential" in out["detail"].lower()
    assert "api/models" in out["detail"]


# ------------------------------------------------------------------ providers
def test_self_hosted_write_is_refused_and_reports_the_typed_key():
    code, out = call("providers/self-hosted", method="POST",
                     body={"provider": "ollama", "base_url": "http://x:1/v1",
                           "model": "m", "api_key": SECRET_VALUE})
    assert code == 403, out
    assert out["code"] == "WB_CREDENTIALS_NOT_IN_CONFIG"
    assert "api_key" in out["detail"], "the carried key must be acknowledged"
    assert SECRET_VALUE not in json.dumps(out), "the refusal echoed the key"


# ----------------------------------------------------------------- onboarding
def test_onboarding_probe_reduces_the_endpoint_answer_to_id_and_label(
        monkeypatch):
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=-1):
            return json.dumps({"data": [{"id": "llama3", "object": "model",
                                         "owned_by": "x"},
                                        {"id": ""}]}).encode()

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        return _Resp()

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    code, out = call("onboarding/probe", method="POST",
                     body={"provider": "ollama",
                           "base_url": "http://127.0.0.1:11434/v1",
                           "api_key": SECRET_VALUE})
    assert code == 200, out
    assert seen["url"] == "http://127.0.0.1:11434/v1/models"
    # The request's OWN key rides the probe; nothing is read from the keys store.
    assert seen["auth"] == f"Bearer {SECRET_VALUE}"
    assert out["ok"] is True
    assert out["models"] == [{"id": "llama3", "label": "llama3"}]
    assert SECRET_VALUE not in json.dumps(out), "the probe echoed the key"


def test_onboarding_probe_reports_an_unreachable_endpoint_as_the_wizards_error():
    code, out = call("onboarding/probe", method="POST",
                     body={"base_url": "http://127.0.0.1:9/v1"})
    assert code == 200, out
    assert out["ok"] is False
    assert out["error"] == "unreachable"
    assert out["models"] == []


def test_onboarding_probe_needs_a_base_url():
    code, out = call("onboarding/probe", method="POST", body={})
    assert code == 400, out
    assert out["code"] == "WB_BAD_INPUT"


def test_onboarding_setup_refuses_a_key_and_writes_no_model(monkeypatch):
    """The key must not be silently dropped: nothing is half-configured."""
    written = []
    from voicebridge.providers import endpoints as EP

    monkeypatch.setattr(EP, "select_model",
                        lambda *a, **k: written.append((a, k)) or {"ok": True})
    code, out = call("onboarding/setup", method="POST",
                     body={"provider": "ollama", "model": "llama3",
                           "api_key": SECRET_VALUE})
    assert code == 403, out
    assert out["code"] == "WB_CREDENTIALS_NOT_IN_CONFIG"
    assert written == [], "a refused setup still called the model writer"
    assert SECRET_VALUE not in json.dumps(out)


def test_onboarding_complete_refuses_when_no_turn_is_possible(monkeypatch):
    from voicebridge.workbench_ui import handlers_extra as HE

    monkeypatch.setattr(HE, "_agent_ready",
                        lambda cfg: (False, "no chat model is configured"))
    code, out = call("onboarding/complete", method="POST", body={})
    assert code == 409, out
    assert "no chat model is configured" in out["detail"]
    assert not (_runtime_home() / "workbench_setup.json").exists(), (
        "a refused completion wrote the marker anyway")


def test_onboarding_complete_stamps_the_acknowledgement_when_a_turn_is_possible(
        monkeypatch):
    from voicebridge.workbench_ui import handlers_extra as HE

    monkeypatch.setattr(HE, "_agent_ready", lambda cfg: (True, "turns run"))
    code, out = call("onboarding/complete", method="POST", body={})
    assert code == 200, out
    assert out["acknowledged"] is True
    marker = _runtime_home() / "workbench_setup.json"
    assert marker.exists()
    assert HS.SETUP_ACK_KEY in json.loads(marker.read_text())


def test_onboarding_oauth_poll_without_a_flow_is_a_bad_request():
    code, out = call("onboarding/oauth/poll")
    assert code == 400, out
    assert out["code"] == "WB_BAD_INPUT"


def test_onboarding_oauth_cancel_of_an_unknown_flow_reports_it_gone():
    code, out = call("onboarding/oauth/cancel", method="POST",
                     body={"flow_id": "does-not-exist"})
    assert code == 200, out
    assert out["cancelled"] is False


# ---------------------------------------------------------------- system health
def test_system_health_reports_the_three_metrics_the_panel_paints():
    code, out = call("system/health")
    assert code == 200, out
    assert out["ok"] is True and out["available"] is True
    assert out["status"] in ("live", "partial")
    for name in ("cpu", "memory", "disk"):
        metric = out[name]
        assert metric is None or "percent" in metric, (name, metric)


# --------------------------------------------------------------------- gateway
def test_gateway_status_is_read_only_and_reports_real_daemon_state():
    code, out = call("gateway/status")
    assert code == 200, out
    assert out["ok"] is True
    assert isinstance(out["running"], bool)
    assert isinstance(out["platforms"], list)
    # Qubit's gateway is an operator daemon: the card must not offer to control it.
    assert out["lifecycle"]["managed"] is False
    assert out["lifecycle"]["reason"] == "operator_daemon"


@pytest.mark.parametrize("path", ["gateway/start", "gateway/stop",
                                  "gateway/restart", "health/restart"])
def test_gateway_lifecycle_verbs_refuse_and_name_the_cli(path):
    code, out = call(path, method="POST", body={})
    assert code == 403, (path, out)
    assert out["code"] == "WB_BROKERED_WRITE"
    assert "qubit gateway" in out["qubit_counterpart"]
    assert out["endpoint"].startswith("gateway/"), out


# --------------------------------------------------------------------- git-info
@pytest.fixture()
def registered_repo(tmp_path):
    """A real repo registered as a workspace root (the confinement's authority)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for argv in (["init", "-q", "."], ["config", "user.email", "t@t"],
                 ["config", "user.name", "t"]):
        subprocess.run(["git", *argv], cwd=repo, check=True)
    (repo / "a.txt").write_text("hi\n")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    (repo / "a.txt").write_text("hi\nmore\n")

    from voicebridge import projects as PJ

    cfg = _cfg()
    slug = PJ.create(cfg, "wb-git-info", [str(repo)], confirmed=True)[
        "project"]["slug"]
    yield repo
    PJ.archive(cfg, slug, confirmed=True)


def test_git_info_reports_real_repo_facts_for_a_registered_workspace(registered_repo):
    code, out = call("git-info", query={"path": str(registered_repo)})
    assert code == 200, out
    assert out.get("detail") != "the workspace is outside the registered roots, so no repository is reported", (
        f"the fixture repo was not confined to a registered root: {out}")
    git = out["git"]
    assert git["is_git"] is True, out
    assert git["branch"], git
    assert git["dirty"] == 1, git          # one unstaged edit
    assert git["ahead"] == 0 and git["behind"] == 0


def test_git_info_hides_the_badge_outside_the_registered_roots():
    code, out = call("git-info", query={"path": "/etc"})
    assert code == 200, out
    assert out["git"]["is_git"] is False
    assert "outside the registered roots" in out["detail"]


# --------------------------------------------------------------------- updates
def test_updates_check_reports_qubits_own_update_manager(monkeypatch):
    from voicebridge import update_manager as UM

    monkeypatch.setattr(UM, "check", lambda cfg, **kw: {
        "state": "OK", "current_version": "9.9.9", "current_revision": "abc",
        "remote_revision": "def", "behind": 2, "update_available": True,
        "can_apply": True, "install_method": "git-checkout", "channel": "stable",
        "message": None, "update_command": "git pull --ff-only"})
    code, out = call("updates/check", method="POST", body={"force": True})
    assert code == 200, out
    assert out["webui"]["behind"] == 2
    assert out["webui"]["current"] == "9.9.9"
    assert out["webui"]["channel"] == "stable"
    assert out["update_available"] is True
    assert "update_manager" in out["detail"], (
        "the reply must say WHOSE update source answered")


def test_updates_check_carries_the_real_reason_instead_of_a_false_up_to_date(
        monkeypatch):
    from voicebridge import update_manager as UM

    monkeypatch.setattr(UM, "check", lambda cfg, **kw: {
        "state": "DEGRADED", "behind": None, "update_available": False,
        "message": "git is not available on this host", "install_method": "package"})
    code, out = call("updates/check", method="POST", body={})
    assert code == 200, out
    assert out["webui"]["behind"] is None
    assert "git is not available" in out["webui"]["error"]


@pytest.mark.parametrize("action", ["apply", "force", "clear_lock", "summary"])
def test_updater_writes_refuse_and_name_the_qubit_updater(action):
    code, out = call(f"updates/{action}", method="POST", body={"target": "agent"})
    assert code == 501, (action, out)
    assert out["code"] == "WB_CAPABILITY_UNBOUND"
    assert "api/qubit/update" in out["detail"]
    assert out["qubit_counterpart"], "a refusal must point somewhere real"
