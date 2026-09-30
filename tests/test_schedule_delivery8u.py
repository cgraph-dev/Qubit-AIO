"""Scheduled-job delivery: bounded, redacted run results reach configured
channel/webhook targets through the existing transports.

Hermetic: isolated XDG/state, frozen timestamps, real schedules SQLite store
and the real messaging outbox, with the network seam monkeypatched
(``urllib.request.urlopen``) and a delegated webhook transport. No test
touches the network and no state leaves ``tmp_path``.

Proves the delivery contract:
  * a configured channel target delivers exactly once and records a durable
    delivery receipt keyed by the run;
  * no target means no delivery and no receipt;
  * a missing credential or absent transport adapter is a typed UNAVAILABLE
    (never a fabricated SENT) and leaves the run receipt intact;
  * a webhook with no outbound adapter is a typed UNAVAILABLE, while a
    delegated transport delivers exactly once;
  * a latched emergency stop skips delivery (and refuses an autofire tick);
  * secrets never appear in the delivered body, delivery receipt, or audit;
  * a restart never duplicates a delivery.
"""
from __future__ import annotations

import json
import time
import urllib.request

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _ok(task_text, *, session, estop_engaged):
    return {"state": "OK", "code": "OK", "detail": "did " + task_text[:40]}


def _slack_account(cfg, aid="ops", key_ref="env:SLACK_TOKEN"):
    from voicebridge import messaging as M

    return M.account_add(cfg, aid, "slack", "Ops", key_ref=key_ref,
                         confirmed=True)


class _Resp:
    def __init__(self, payload, status=200):
        self._body = json.dumps(payload).encode()
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _capture_urlopen(monkeypatch, calls):
    """Monkeypatch the network seam the messaging transport uses."""

    def _fake(req, timeout=None):
        calls.append(req)
        return _Resp({"ok": True, "ts": "1718000000.0001"})

    monkeypatch.setattr(urllib.request, "urlopen", _fake)


# ------------------------------------------------- deliveries exactly once
def test_configured_channel_target_delivers_once_and_records_receipt(
        tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("SLACK_TOKEN", "xoxb-test-token")
    calls = []
    _capture_urlopen(monkeypatch, calls)
    _slack_account(cfg)
    created = SC.create(cfg, "digest", "summarize inbox", "every 5 minutes",
                        deliver_to="channel:ops:#alerts", confirmed=True)
    assert created["job"]["deliver_to"] == "channel:ops:#alerts"

    out = SC.run_due(cfg, executor=_ok, now=time.time() + 3600)
    assert out["ran"] == 1
    result = out["results"][0]
    assert result["state"] == "OK"
    assert result["delivery"]["state"] == "SENT"
    assert result["delivery"]["delivered"] == 1
    assert len(calls) == 1, "exactly one transport attempt"

    hist = SC.delivery_history(cfg)["deliveries"]
    assert len(hist) == 1, "exactly one durable delivery receipt"
    assert hist[0]["run_id"] == 1 and hist[0]["job_id"] == 1
    assert hist[0]["target"] == "channel:ops:#alerts"
    assert hist[0]["state"] == "SENT"
    assert hist[0]["code"] == SC.DELIVERY_SENT_CODE
    assert hist[0]["attempted_at"] > 0
    # The run receipt survives alongside the delivery receipt.
    assert SC.history(cfg)["runs"][0]["state"] == "OK"


def test_no_target_means_no_delivery(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("SLACK_TOKEN", "x")
    calls = []
    _capture_urlopen(monkeypatch, calls)
    SC.create(cfg, "plain", "just work", "every 5 minutes", confirmed=True)

    out = SC.run_due(cfg, executor=_ok, now=time.time() + 3600)
    assert out["results"][0]["delivery"]["state"] == "NONE"
    assert calls == []
    assert SC.delivery_history(cfg)["deliveries"] == []


# ----------------------------------------------- typed unavailable states
def test_missing_credential_typed_unavailable_run_receipt_intact(
        tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.delenv("SLACK_TOKEN", raising=False)
    calls = []
    _capture_urlopen(monkeypatch, calls)
    _slack_account(cfg)
    SC.create(cfg, "digest", "work", "every 5 minutes",
              deliver_to="channel:ops:#alerts", confirmed=True)

    out = SC.run_due(cfg, executor=_ok, now=time.time() + 3600)
    delivery = out["results"][0]["delivery"]
    assert delivery["state"] == "UNAVAILABLE"
    assert delivery["delivered"] == 0
    assert delivery["targets"][0]["code"] == "MSG_NO_CREDENTIAL"
    assert calls == [], "no transport attempted without a credential"
    # The run receipt is the executor's, not the delivery's.
    run = SC.history(cfg)["runs"][0]
    assert run["state"] == "OK" and run["code"] == "OK"
    assert SC.show(cfg, 1)["job"]["last_state"] == "OK"
    receipt = SC.delivery_history(cfg)["deliveries"][0]
    assert receipt["state"] == "UNAVAILABLE"
    assert receipt["code"] == "MSG_NO_CREDENTIAL"


def test_no_transport_adapter_is_typed_unavailable(tmp_path, monkeypatch):
    from voicebridge import messaging as M
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("DISCORD_TOKEN", "x")
    calls = []
    _capture_urlopen(monkeypatch, calls)
    M.account_add(cfg, "chat", "discord", "Chat",
                  key_ref="env:DISCORD_TOKEN", confirmed=True)
    SC.create(cfg, "digest", "work", "every 5 minutes",
              deliver_to="channel:chat:#general", confirmed=True)

    out = SC.run_due(cfg, executor=_ok, now=time.time() + 3600)
    delivery = out["results"][0]["delivery"]
    assert delivery["state"] == "UNAVAILABLE"
    assert delivery["targets"][0]["code"] == "MSG_TRANSPORT_UNAVAILABLE"
    assert calls == [], "the adapter refuses before any network"
    assert SC.history(cfg)["runs"][0]["state"] == "OK"


def test_webhook_target_without_transport_is_typed_unavailable(
        tmp_path, monkeypatch):
    from voicebridge import schedules as SC
    from voicebridge import webhooks as W

    cfg = _cfg(tmp_path, monkeypatch)
    W.add(cfg, "deploy", "http://127.0.0.1:9999/hook", ["deploy"],
          "env:WH_SECRET", confirmed=True)
    SC.create(cfg, "hook", "notify", "every 5 minutes",
              deliver_to="webhook:deploy", confirmed=True)

    out = SC.run_due(cfg, executor=_ok, now=time.time() + 3600)
    delivery = out["results"][0]["delivery"]
    assert delivery["state"] == "UNAVAILABLE"
    assert delivery["targets"][0]["code"] == SC.DELIVERY_UNAVAILABLE_CODE
    receipt = SC.delivery_history(cfg)["deliveries"][0]
    assert receipt["code"] == SC.DELIVERY_UNAVAILABLE_CODE
    assert SC.history(cfg)["runs"][0]["state"] == "OK"


def test_webhook_delivers_once_through_delegated_transport(
        tmp_path, monkeypatch):
    from voicebridge import schedules as SC
    from voicebridge import webhooks as W

    cfg = _cfg(tmp_path, monkeypatch)
    W.add(cfg, "deploy", "http://127.0.0.1:9999/hook", ["deploy"],
          "env:WH_SECRET", confirmed=True)
    seen = []

    def _transport(cfg, subscription, payload):
        seen.append((subscription["id"], payload))
        return {"state": "OK"}

    monkeypatch.setattr(SC, "_WEBHOOK_DELIVER", _transport)
    SC.create(cfg, "hook", "notify", "every 5 minutes",
              deliver_to="webhook:deploy", confirmed=True)
    out = SC.run_due(cfg, executor=_ok, now=time.time() + 3600)
    assert out["results"][0]["delivery"]["state"] == "SENT"
    assert len(seen) == 1
    assert seen[0][0] == "deploy"
    assert seen[0][1]["job_id"] == 1 and seen[0][1]["run_id"] == 1
    assert SC.delivery_history(cfg)["deliveries"][0]["state"] == "SENT"


def test_multiple_targets_partial_when_one_unavailable(tmp_path, monkeypatch):
    from voicebridge import schedules as SC
    from voicebridge import webhooks as W

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("SLACK_TOKEN", "x")
    calls = []
    _capture_urlopen(monkeypatch, calls)
    _slack_account(cfg)
    W.add(cfg, "hook", "http://127.0.0.1:9999/h", ["e"], "env:WH",
          confirmed=True)
    SC.create(cfg, "multi", "work", "every 5 minutes",
              deliver_to="channel:ops:#a,webhook:hook", confirmed=True)

    out = SC.run_due(cfg, executor=_ok, now=time.time() + 3600)
    delivery = out["results"][0]["delivery"]
    assert delivery["state"] == "PARTIAL"
    assert delivery["delivered"] == 1
    assert len(calls) == 1
    assert len(SC.delivery_history(cfg)["deliveries"]) == 2


# -------------------------------------------------------------- e-stop
def test_estop_blocks_delivery_and_spares_run_receipt(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("SLACK_TOKEN", "x")
    calls = []
    _capture_urlopen(monkeypatch, calls)
    _slack_account(cfg)
    SC.create(cfg, "digest", "work", "every 5 minutes",
              deliver_to="channel:ops:#alerts", confirmed=True)

    out = SC.run_due(cfg, executor=_ok, now=time.time() + 3600,
                     estop_engaged=lambda: True)
    delivery = out["results"][0]["delivery"]
    assert delivery["state"] == "UNAVAILABLE"
    assert delivery["targets"][0]["code"] == SC.DELIVERY_ESTOP_CODE
    assert calls == []
    assert SC.history(cfg)["runs"][0]["state"] == "OK"


def test_autofire_estop_refuses_tick_and_delivers_nothing(tmp_path,
                                                         monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("SLACK_TOKEN", "x")
    calls = []
    _capture_urlopen(monkeypatch, calls)
    _slack_account(cfg)
    SC.create(cfg, "digest", "work", "every 5 minutes",
              deliver_to="channel:ops:#alerts", confirmed=True)

    out = SC.autofire(cfg, executor=_ok, now=time.time() + 3600,
                      estop_engaged=lambda: True)
    assert out["state"] == "denied" and out["ran"] == 0
    assert calls == []
    assert SC.delivery_history(cfg)["deliveries"] == []


# ------------------------------------------------------------- redaction
def test_secret_never_in_delivered_body_receipt_or_audit(tmp_path,
                                                         monkeypatch):
    from voicebridge import logging_setup as LOG
    from voicebridge import schedules as SC

    events = []
    monkeypatch.setattr(LOG, "log_event",
                        lambda ev, **f: events.append((ev, f)))
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("SLACK_TOKEN", "xoxb-test-token")
    calls = []
    _capture_urlopen(monkeypatch, calls)
    _slack_account(cfg)
    secret = "sk-abcdefghijklmnopqrstuvwx"

    def _exec(task_text, *, session, estop_engaged):
        return {"state": "OK", "code": "OK",
                "detail": f"deployed with api_key={secret}"}

    SC.create(cfg, "digest", "work", "every 5 minutes",
              deliver_to="channel:ops:#alerts", confirmed=True)
    out = SC.run_due(cfg, executor=_exec, now=time.time() + 3600)
    assert out["results"][0]["delivery"]["state"] == "SENT"

    body = calls[0].data.decode("utf-8")
    assert secret not in body, "delivered body must be redacted"
    assert "[REDACTED]" in body
    assert secret not in json.dumps(SC.delivery_history(cfg))
    assert secret not in json.dumps(events)
    assert "schedules.deliver" in [ev for ev, _ in events]


# ------------------------------------------------- config-time validation
def test_configure_unknown_target_refused_and_nothing_stored(tmp_path,
                                                             monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(SC.SchedulesError) as exc:
        SC.create(cfg, "x", "t", "every 5 minutes",
                  deliver_to="channel:ghost:#a", confirmed=True)
    assert exc.value.code == SC.BAD_INPUT_CODE
    with pytest.raises(SC.SchedulesError) as exc:
        SC.create(cfg, "x", "t", "every 5 minutes",
                  deliver_to="webhook:nope", confirmed=True)
    assert exc.value.code == SC.BAD_INPUT_CODE
    assert SC.list_jobs(cfg)["jobs"] == []


def test_parse_delivery_spec_formats_and_bounds(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    assert SC.parse_delivery_spec("") == []
    got = SC.parse_delivery_spec("channel:a:#1, webhook:b")
    assert [t["kind"] for t in got] == ["channel", "webhook"]
    assert got[0] == {"kind": "channel", "account": "a", "to": "#1"}
    assert got[1] == {"kind": "webhook", "sub": "b"}
    for bad in ("channel:no_recipient", "email:a@b", "webhook:",
                "channel::#x"):
        with pytest.raises(SC.SchedulesError):
            SC.parse_delivery_spec(bad)


# -------------------------------------------------- restart idempotency
def test_restart_does_not_duplicate_delivery(tmp_path, monkeypatch):
    from voicebridge import schedules as SC

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("SLACK_TOKEN", "x")
    calls = []
    _capture_urlopen(monkeypatch, calls)
    _slack_account(cfg)
    SC.create(cfg, "digest", "work", "every 5 minutes",
              deliver_to="channel:ops:#alerts", confirmed=True)
    now = time.time() + 3600
    assert SC.autofire(cfg, executor=_ok, now=now)["ran"] == 1
    assert len(calls) == 1

    cfg2 = _cfg(tmp_path, monkeypatch)
    assert cfg2 is not cfg
    out = SC.autofire(cfg2, executor=_ok, now=now)
    assert out["ran"] == 0, "the durable lease blocks a second run"
    assert len(calls) == 1, "delivery never repeats"
    assert len(SC.delivery_history(cfg2)["deliveries"]) == 1
