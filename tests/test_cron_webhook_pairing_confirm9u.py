"""Dashboard audit fix — cron/webhook/pairing mutations require confirmation.

Every one of these mutations previously executed without the shared confirm
contract (e-stop + single-use journal approval) or bypassed the addressed job:

- cron pause/resume called the store with ``confirmed=True`` directly;
- cron trigger ignored the path job id and ran every due job;
- webhook create and enable/disable used ``confirm`` defaults that executed;
- pairing mutations bypassed the e-stop and flattened ``PairingError`` to 500.

Each is now a typed 409 with a real ``confirmation_id`` when unconfirmed, and
executes exactly once when settled. The canonical stores (schedules, webhooks,
pairing) perform and audit the write.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    home = tmp_path / "home" / ".hermes"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QUBIT_HOME", str(home))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


# --------------------------------------------------------------------- cron
def _cron_job(cfg, name):
    from voicebridge import schedules as S

    return int(S.create(cfg, name, "task", "every 60 minutes",
                        requested_by="t", confirmed=True)["job"]["id"])


def test_cron_pause_requires_confirmation_then_persists(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import schedules as S

    cfg = _cfg(tmp_path, monkeypatch)
    job_id = _cron_job(cfg, "pause-me")

    code, staged = HC.dispatch(cfg, "POST",
                               f"/api/cron/jobs/{job_id}/pause", {}, {})
    assert code == 409 and staged["code"] == "CONFIRM_REQUIRED", staged
    assert staged["confirmation_id"], staged
    assert S.show(cfg, job_id)["job"]["enabled"] is True, "pause bypassed"

    code, done = HC.dispatch(cfg, "POST", f"/api/cron/jobs/{job_id}/pause",
                             {}, {"confirmation_id": staged["confirmation_id"]})
    assert code == 200 and done["ok"] is True, (code, done)
    assert S.show(cfg, job_id)["job"]["enabled"] is False

    code, staged = HC.dispatch(cfg, "POST",
                               f"/api/cron/jobs/{job_id}/resume", {}, {})
    assert code == 409 and staged["confirmation_id"], staged
    code, done = HC.dispatch(cfg, "POST", f"/api/cron/jobs/{job_id}/resume",
                             {}, {"confirm": True})
    assert code == 200, (code, done)
    assert S.show(cfg, job_id)["job"]["enabled"] is True


def test_cron_trigger_addresses_only_the_path_job(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import schedules as S

    cfg = _cfg(tmp_path, monkeypatch)
    job_a = _cron_job(cfg, "job-a")
    job_b = _cron_job(cfg, "job-b")

    code, out = HC.dispatch(cfg, "POST",
                            f"/api/cron/jobs/{job_b}/trigger", {}, {})
    assert code == 200, (code, out)
    ran = [r.get("job_id") for r in out["result"].get("results", [])]
    assert ran == [job_b], ran
    assert S.history(cfg, job_a)["runs"] == [], "job A was triggered"
    assert [r["job_id"] for r in S.history(cfg, job_b)["runs"]] == [job_b]


# ----------------------------------------------------------------- webhooks
def _spa_webhook(name="wh-confirm"):
    return {"name": name, "url": "http://127.0.0.1:9/hook",
            "events": ["task.completed"], "secret_ref": "env:WH_CONFIRM"}


def test_webhook_create_requires_confirmation_and_audits(tmp_path,
                                                         monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import webhooks as W

    cfg = _cfg(tmp_path, monkeypatch)
    code, staged = HC.dispatch(cfg, "POST", "/api/webhooks", {},
                               _spa_webhook())
    assert code == 409 and staged["code"] == "CONFIRM_REQUIRED", staged
    assert staged["confirmation_id"], staged
    assert W.list_subs(cfg)["subscriptions"] == [], "create bypassed confirm"

    audits = []
    monkeypatch.setattr(W, "_audit",
                        lambda op, actor, detail: audits.append(op))
    code, created = HC.dispatch(cfg, "POST", "/api/webhooks", {},
                                {**_spa_webhook(), "confirm": True})
    assert code == 200 and created["ok"] is True, (code, created)
    assert [s["id"] for s in W.list_subs(cfg)["subscriptions"]] == \
        ["wh-confirm"]
    assert "add" in audits, audits


def test_webhook_enable_and_sub_toggle_require_confirmation(tmp_path,
                                                            monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import webhooks as W

    cfg = _cfg(tmp_path, monkeypatch)
    code, _ = HC.dispatch(cfg, "POST", "/api/webhooks", {},
                          {**_spa_webhook("wh-toggle"), "confirm": True})
    assert code == 200

    # Store-wide delivery toggle: unconfirmed must not execute.
    code, staged = HC.dispatch(cfg, "POST", "/api/webhooks/enable", {},
                               {"enabled": False})
    assert code == 409 and staged["confirmation_id"], staged
    assert W.is_enabled(cfg) is True, "enable toggle bypassed confirm"
    code, done = HC.dispatch(cfg, "POST", "/api/webhooks/enable", {},
                             {"enabled": False, "confirm": True})
    assert code == 200 and done["enabled"] is False, (code, done)

    # Per-subscription toggle: unconfirmed must not execute.
    code, staged = HC.dispatch(cfg, "PUT",
                               "/api/webhooks/wh-toggle/enabled", {},
                               {"enabled": False})
    assert code == 409 and staged["confirmation_id"], staged
    assert W.show(cfg, "wh-toggle")["subscription"]["enabled"] is True
    code, done = HC.dispatch(cfg, "PUT",
                             "/api/webhooks/wh-toggle/enabled", {},
                             {"enabled": False, "confirm": True})
    assert code == 200, (code, done)
    assert W.show(cfg, "wh-toggle")["subscription"]["enabled"] is False


# ------------------------------------------------------------------ pairing
def test_pairing_create_list_and_mutations_are_confirmed_and_typed(
        tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)

    # Create is confirm-gated; nothing is issued unconfirmed.
    code, staged = HC.dispatch(cfg, "POST", "/api/pairing", {},
                               {"label": "dev", "scopes": ["chat"]})
    assert code == 409 and staged["confirmation_id"], staged
    code, created = HC.dispatch(cfg, "POST", "/api/pairing", {},
                                {"label": "dev", "scopes": ["chat"],
                                 "confirm": True})
    assert code == 200 and created["result"]["state"] == "OK", created
    record = created["result"]

    # The list must emit a usable request_id (non-empty, resolvable id).
    code, listed = HC.dispatch(cfg, "GET", "/api/pairing", {}, {})
    assert code == 200, (code, listed)
    pending = listed["pending"]
    assert pending and pending[0]["request_id"], listed
    assert pending[0]["request_id"] == pending[0]["user_id"]
    assert pending[0]["request_id"] == record["code_id"]

    # Approve accepts the real code; an unknown code is a typed 4xx (not 500).
    code, out = HC.dispatch(cfg, "POST", "/api/pairing/approve", {},
                            {"code": "ZZZZZZZZ", "confirm": True})
    assert code == 400 and out["code"] == "PAIRING_BAD_CODE", (code, out)
    code, out = HC.dispatch(cfg, "POST", "/api/pairing/approve", {},
                            {"code": record["code"], "confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)

    # Revoke accepts the ``user_id`` alias the SPA sends.
    code, out = HC.dispatch(cfg, "POST", "/api/pairing/revoke", {},
                            {"user_id": record["code_id"], "confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    code, out = HC.dispatch(cfg, "POST", "/api/pairing/revoke", {},
                            {"user_id": "pair_missing", "confirm": True})
    assert code == 404 and out["code"] == "PAIRING_NOT_FOUND", (code, out)


def test_pairing_mutations_refuse_while_estop_latched(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(HC, "_estop", lambda c: True)
    code, out = HC.dispatch(cfg, "POST", "/api/pairing", {},
                            {"label": "dev", "scopes": ["chat"],
                             "confirm": True})
    assert code == 403 and out["code"] == "EMERGENCY_STOP", (code, out)
