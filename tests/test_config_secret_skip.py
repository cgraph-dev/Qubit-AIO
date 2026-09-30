"""Focused repair tests: secret-safe config save + approvals cancel for
``qubit.config.*``.

Hermetic: temp XDG dirs + a real ``voicebridge.config.Config``; no network.
These cover the dashboard dead controls found by real-browser E2E:

* the Settings form PUTs the whole tree it read from ``GET /api/config``,
  which round-trips secret-shaped leaves the server served (``webui.token``,
  ``hotkey.hold_key``). Unchanged served leaves are dropped, changed/new
  secret leaves are still refused, and a body of only unchanged secret leaves
  is a typed ``CONFIG_NO_FIELDS`` (not a secret write).
* a staged ``qubit.config.write`` confirmation is cancellable through the
  pending-approvals ``cancel_op`` path (specific id and no-id aggregate).
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

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


# ---------------------------------------------------------------------------
# Secret-safe config save
# ---------------------------------------------------------------------------
def test_full_tree_save_skips_unchanged_served_secret_leaves(tmp_path,
                                                              monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, served = HC.dispatch(cfg, "GET", "/api/config", {}, {})
    assert code == 200, served
    # The served tree carries secret-shaped leaves, but never their raw value:
    # the read boundary redacts them to ``[REDACTED]`` (this is the shape that
    # made the whole-tree Save a dead control). The Save path treats the
    # placeholder as an unchanged served leaf and skips it, leaving the stored
    # value untouched.
    assert "webui" in served and "hotkey" in served, served
    assert served["hotkey"]["hold_key"] == "[REDACTED]", served.get("hotkey")

    body = {"config": served}
    body["config"]["vad"]["enabled"] = False
    code, out = HC.dispatch(cfg, "PUT", "/api/config", {},
                            {**body, "confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    # The real edit is reported and the unchanged secret leaves were skipped.
    assert out["config"]["vad.enabled"] is False, out
    assert not any(k.endswith("token") or k.endswith("_key")
                   for k in out["config"]), out

    code, live = HC.dispatch(cfg, "GET", "/api/config", {}, {})
    assert live["vad"]["enabled"] is False, live.get("vad")
    # The secret leaves are untouched: the read boundary still redacts them and
    # the stored value underneath was never overwritten by the echoed
    # placeholder.
    assert live["hotkey"]["hold_key"] == "[REDACTED]", live.get("hotkey")
    assert live["webui"]["token"] == served["webui"]["token"], live.get("webui")
    assert cfg.get("hotkey", "hold_key", default="") == "ctrl_r", \
        cfg.get("hotkey", "hold_key", default="")


def test_changed_served_secret_leaf_is_still_refused(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, served = HC.dispatch(cfg, "GET", "/api/config", {}, {})
    assert code == 200, served
    token = served["webui"]["token"]
    code, out = HC.dispatch(cfg, "PUT", "/api/config", {},
                            {"config": {"webui": {"token": token + "-changed"}},
                             "confirm": True})
    assert (code, out["code"]) == (403, "SECRET_WRITE_REFUSED"), (code, out)
    # Nothing leaked into the override store.
    code, live = HC.dispatch(cfg, "GET", "/api/config", {}, {})
    assert live["webui"]["token"] == token, live.get("webui")


def test_only_unchanged_secret_leaves_is_no_fields(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, served = HC.dispatch(cfg, "GET", "/api/config", {}, {})
    assert code == 200, served
    body = {"config": {"hotkey": {"hold_key": served["hotkey"]["hold_key"],
                                  "alt_hold_key":
                                      served["hotkey"]["alt_hold_key"]},
                       "webui": {"token": served["webui"]["token"]}},
            "confirm": True}
    code, out = HC.dispatch(cfg, "PUT", "/api/config", {}, body)
    assert code != 200 and out["ok"] is False, (code, out)
    assert out["code"] == "CONFIG_NO_FIELDS", out


def test_newly_provided_secret_leaf_is_refused(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "PUT", "/api/config", {},
                            {"config": {"terminal":
                                        {"docker_shared_container_key":
                                            "sk-new"}},
                             "confirm": True})
    assert (code, out["code"]) == (403, "SECRET_WRITE_REFUSED"), (code, out)


# ---------------------------------------------------------------------------
# Approvals cancel for staged qubit.config.write
# ---------------------------------------------------------------------------
def _stage_config_write(cfg):
    from voicebridge import qubit_compat as HC

    code, out = HC.dispatch(cfg, "PUT", "/api/config", {},
                            {"config": {"vad": {"enabled": False}}})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    cid = out.get("confirmation_id")
    assert cid, out
    return cid


def test_cancel_op_settles_staged_config_write_by_id(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    cid = _stage_config_write(cfg)
    out = OPS.cancel_op(cfg, cid)
    assert out["state"] == "executed", out
    assert out["receipt"]["cancelled"] == 1, out
    # The journal entry is denied, not left to expire.
    journal = OPS._journal(cfg)
    assert journal.get(cid).state == "denied", journal.get(cid)


def test_cancel_op_no_id_aggregate_cancels_config_write(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS

    cfg = _cfg(tmp_path, monkeypatch)
    cid = _stage_config_write(cfg)
    out = OPS.cancel_op(cfg, None)
    assert out["state"] == "executed", out
    assert out["receipt"]["cancelled"] >= 1, out
    assert OPS._journal(cfg).get(cid).state == "denied"


# ---------------------------------------------------------------------------
# Approvals confirm for staged qubit.config.write
# ---------------------------------------------------------------------------
def test_confirm_op_settles_staged_config_write_and_persists(tmp_path,
                                                             monkeypatch):
    """The banner's id-only Confirm settles AND applies the staged write.

    A staged ``qubit.config.write`` records its secret-filtered scalar payload
    on the journal entry, so ``confirm_op`` routes it to the real ``config_save``
    backend. The value persists, the entry is consumed exactly once, and a
    replay is a typed failure (never a double-apply).
    """
    from voicebridge import dashboard_ops as OPS
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    cid = _stage_config_write(cfg)
    # The stage recorded the real scalar set (no secrets) for id-only confirm.
    assert OPS._journal(cfg).get(cid).meta["payload"] == {"vad.enabled": False}

    out = OPS.confirm_op(cfg, cid)
    assert out["state"] == "executed", out
    assert out["receipt"]["confirmed"] == 1, out

    code, live = HC.dispatch(cfg, "GET", "/api/config", {}, {})
    assert live["vad"]["enabled"] is False, live.get("vad")

    journal = OPS._journal(cfg)
    settled = journal.get(cid)
    assert settled.state == "executing" and settled.decision == "approve", settled
    assert not any(a.id == cid for a in journal.pending()), journal.pending()

    replay = OPS.confirm_op(cfg, cid)
    assert replay["state"] == "failed" and replay["code"] == "NO_CONFIRMATION", \
        replay


def test_confirm_op_unknown_and_expired_ids_are_typed(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge.approvals import ApprovalJournal

    cfg = _cfg(tmp_path, monkeypatch)
    unknown = OPS.confirm_op(cfg, "does-not-exist")
    assert unknown["state"] == "failed", unknown
    assert unknown["code"] == "NO_CONFIRMATION", unknown

    # An entry that expired before confirm is gone, not silently approved.
    path = OPS._journal(cfg).path
    old = ApprovalJournal(path, clock=lambda: 1000.0, default_ttl_s=120.0)
    stale = old.create("qubit.config.write", "stale",
                       session="dashboard", action="config.write",
                       payload={"vad.enabled": False})
    expired = OPS.confirm_op(cfg, stale.id)
    assert expired["state"] == "failed", expired
    assert expired["code"] == "NO_CONFIRMATION", expired


def test_confirm_op_no_id_aggregate_confirms_config_write(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    cid = _stage_config_write(cfg)
    out = OPS.confirm_op(cfg, None)
    assert out["state"] == "executed", out
    assert out["receipt"]["confirmed"] >= 1, out
    assert OPS._journal(cfg).get(cid).decision == "approve"
    code, live = HC.dispatch(cfg, "GET", "/api/config", {}, {})
    assert live["vad"]["enabled"] is False, live.get("vad")
