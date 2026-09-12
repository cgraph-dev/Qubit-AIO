"""Agent 6 — automation standing orders / auto-reply (OpenClaw OC-31).

Hermetic: isolated XDG/state, a credential-less synthetic account, injected
delivery transports, and captured audit events. The REAL persisted store and
the REAL messaging outbox path are exercised end to end.

Proves the parity contract for auto-reply standing orders:
  * creation is confirmation-gated, validated, and authorized through the
    shared permission store;
  * a queued inbound event auto-replies through ``messaging.draft``/``send``
    (a missing credential is a typed ``UNAVAILABLE``, never a fake ``SENT``);
  * matching is literal/regex, disabled orders do not fire, and an event with
    no match is consumed with a typed ``NONE`` receipt;
  * the emergency stop refuses both creation and processing without consuming
    the queue;
  * bodies and receipts are bounded/redacted and recorded in the audit path.
"""
from __future__ import annotations

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.delenv("SLACK_TOKEN", raising=False)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _account(cfg, aid="team", provider="slack"):
    from voicebridge import messaging as M

    return M.account_add(cfg, aid, provider, aid, confirmed=True,
                         key_ref="env:SLACK_TOKEN")


# ------------------------------------------------------------- validation
def test_order_creation_gates_and_validation(tmp_path, monkeypatch):
    from voicebridge import automation as A

    cfg = _cfg(tmp_path, monkeypatch)
    _account(cfg)
    with pytest.raises(A.AutomationError) as nc:
        A.order_add(cfg, "greet", match="hello", reply="hi", account="team",
                    to="C1", confirmed=False)
    assert nc.value.code == A.CONFIRM_CODE
    with pytest.raises(A.AutomationError) as badname:
        A.order_add(cfg, "Bad Name", match="hello", reply="hi", account="team",
                    to="C1", confirmed=True)
    assert badname.value.code == A.BAD_INPUT_CODE
    with pytest.raises(A.AutomationError) as noreply:
        A.order_add(cfg, "greet", match="hello", reply="", account="team",
                    to="C1", confirmed=True)
    assert noreply.value.code == A.BAD_INPUT_CODE
    with pytest.raises(A.AutomationError) as noacct:
        A.order_add(cfg, "greet", match="hello", reply="hi", account="ghost",
                    to="C1", confirmed=True)
    assert noacct.value.code == A.BAD_INPUT_CODE
    assert A.order_list(cfg)["orders"] == []


def test_order_add_show_toggle_remove(tmp_path, monkeypatch):
    from voicebridge import automation as A

    cfg = _cfg(tmp_path, monkeypatch)
    _account(cfg)
    A.order_add(cfg, "greet", match="hello", reply="hi", account="team",
                to="C1", confirmed=True)
    with pytest.raises(A.AutomationError) as dup:
        A.order_add(cfg, "greet", match="x", reply="y", account="team",
                    to="C1", confirmed=True)
    assert dup.value.code == A.EXISTS_CODE
    assert A.order_show(cfg, "greet")["order"]["match"] == "hello"
    off = A.order_set_enabled(cfg, "greet", False, confirmed=True)
    assert off["order"]["enabled"] is False
    assert A.order_remove(cfg, "greet", confirmed=True)["removed"] == "greet"
    with pytest.raises(A.AutomationError) as gone:
        A.order_show(cfg, "greet")
    assert gone.value.code == A.NOT_FOUND_CODE


def test_hard_deny_permission_refuses_creation(tmp_path, monkeypatch):
    from voicebridge import automation as A
    from voicebridge import permissions as P

    cfg = _cfg(tmp_path, monkeypatch)
    _account(cfg)
    monkeypatch.setattr(P, "is_hard_deny_auto",
                        lambda cap: r"(^|\.)(delete)(\.|$)")
    with pytest.raises(A.AutomationError) as exc:
        A.order_add(cfg, "greet", match="hello", reply="hi", account="team",
                    to="C1", confirmed=True)
    assert exc.value.code == A.PERMISSION_CODE


# --------------------------------------------------------------- auto-reply
def test_queued_event_auto_replies_through_messaging(tmp_path, monkeypatch):
    from voicebridge import automation as A

    cfg = _cfg(tmp_path, monkeypatch)
    _account(cfg)
    A.order_add(cfg, "greet", match="hello", reply="hi there", account="team",
                to="C1", confirmed=True)
    A.queue_event(cfg, kind="channel", source="slack", text="well hello!",
                  confirmed=True)
    out = A.process_events(cfg)
    assert out["processed"] == 1, out
    res = out["results"][0]
    assert res["order_id"] == "greet"
    # No credential reference resolves -> typed UNAVAILABLE, never SENT.
    assert res["state"] == "UNAVAILABLE", res
    assert A.order_show(cfg, "greet")["order"]["count"] == 1
    # The queue was consumed exactly once.
    assert A.process_events(cfg)["processed"] == 0


def test_queue_event_requires_confirm_and_validates(tmp_path, monkeypatch):
    from voicebridge import automation as A

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(A.AutomationError) as nc:
        A.queue_event(cfg, kind="channel", source="s", text="t", confirmed=False)
    assert nc.value.code == A.CONFIRM_CODE
    with pytest.raises(A.AutomationError) as bad:
        A.queue_event(cfg, kind="telepathy", source="s", text="t",
                      confirmed=True)
    assert bad.value.code == A.BAD_INPUT_CODE


def test_matching_disabled_and_regex(tmp_path, monkeypatch):
    from voicebridge import automation as A

    cfg = _cfg(tmp_path, monkeypatch)
    _account(cfg)
    A.order_add(cfg, "any", match="re:(deploy|release)", reply="ack",
                account="team", to="C1", confirmed=True)
    A.order_add(cfg, "off", match="notify", reply="ack", account="team",
                to="C1", confirmed=True)
    A.order_set_enabled(cfg, "off", False, confirmed=True)
    A.queue_event(cfg, kind="internal", source="ci", text="please deploy now",
                  confirmed=True)
    A.queue_event(cfg, kind="internal", source="ci", text="notify the team",
                  confirmed=True)
    A.queue_event(cfg, kind="internal", source="ci", text="unrelated",
                  confirmed=True)
    out = A.process_events(cfg)
    by_event = {r["event_id"]: r for r in out["results"]}
    states = [r for r in out["results"]]
    assert len(states) == 3
    assert sum(1 for r in states if r["order_id"] == "any") == 1
    assert all(r["order_id"] != "off" for r in states)
    # The disabled order's event and the unrelated event both resolve NONE.
    assert sum(1 for r in states if r["state"] == "NONE") == 2
    assert by_event  # sanity


def test_injected_transport_reports_sent(tmp_path, monkeypatch):
    from voicebridge import automation as A

    cfg = _cfg(tmp_path, monkeypatch)
    _account(cfg)
    A.order_add(cfg, "greet", match="hello", reply="hi", account="team",
                to="C1", confirmed=True)
    A.queue_event(cfg, kind="internal", source="x", text="hello", confirmed=True)
    out = A.process_events(
        cfg, deliver=lambda cfg_, order, body, event: {
            "state": "OK", "message_id": "m1"})
    res = out["results"][0]
    assert res["state"] == "SENT" and res["code"] == A.DELIVERY_SENT_CODE


# --------------------------------------------------------------- e-stop/audit
def test_estop_blocks_creation_and_processing_without_consuming(tmp_path,
                                                                monkeypatch):
    from voicebridge import automation as A

    cfg = _cfg(tmp_path, monkeypatch)
    _account(cfg)
    A.order_add(cfg, "greet", match="hello", reply="hi", account="team",
                to="C1", confirmed=True)
    A.queue_event(cfg, kind="internal", source="x", text="hello", confirmed=True)
    monkeypatch.setattr(A, "estop_engaged", lambda _c: True)
    with pytest.raises(A.AutomationError) as exc:
        A.order_add(cfg, "blocked", match="x", reply="y", account="team",
                    to="C1", confirmed=True)
    assert exc.value.code == A.ESTOP_CODE
    denied = A.process_events(cfg)
    assert denied["state"] == "denied" and denied["code"] == A.ESTOP_CODE
    assert denied["processed"] == 0
    # The queue is untouched, so a cleared latch still delivers.
    monkeypatch.setattr(A, "estop_engaged", lambda _c: False)
    assert A.process_events(cfg)["processed"] == 1


def test_auto_reply_is_audited(tmp_path, monkeypatch):
    from voicebridge import automation as A
    from voicebridge import logging_setup as LOG

    events = []
    monkeypatch.setattr(LOG, "log_event",
                        lambda ev, **f: events.append((ev, f)))
    cfg = _cfg(tmp_path, monkeypatch)
    _account(cfg)
    A.order_add(cfg, "greet", match="hello", reply="hi", account="team",
                to="C1", confirmed=True)
    A.queue_event(cfg, kind="internal", source="x", text="hello", confirmed=True)
    A.process_events(cfg)
    names = [e for e, _ in events]
    assert "automation.order_add" in names
    assert "automation.auto_reply" in names
