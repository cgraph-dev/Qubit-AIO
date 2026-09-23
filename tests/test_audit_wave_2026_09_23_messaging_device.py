"""Audit wave 2026-09-23 — pending-approvals settle for a staged channel
toggle and the CLI device-enroll audit receipt.

Two narrow fixes:

* ``dashboard_ops._compat_runner`` gains a ``messaging.platform_update`` entry
  so the pending-approvals banner's id-only Confirm (``POST /api/ops/confirm``)
  can execute a staged channel toggle through the SAME canonical
  ``qubit_compat.msg_update`` path the SPA PUT re-issue uses.
* ``cli._cmd_device`` emits the canonical ``device.enroll`` audit receipt on the
  one audit path, matching the dashboard handler
  (``webui._device_enroll_impl``), without changing the CLI's explicit
  authorization semantics.

Hermetic: no live service, network, credential, or hardware is touched.
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


# ---------------------------------------------------------------------------
# 1. messaging.platform_update is an executable compat runner
# ---------------------------------------------------------------------------
def test_compat_runner_has_messaging_platform_update():
    from voicebridge import dashboard_ops as OPS

    assert OPS._compat_runner("messaging.platform_update") is not None


def test_staged_channel_toggle_settles_through_canonical_path(tmp_path,
                                                              monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge import dashboard_ops as OPS
    from voicebridge import qubit_compat as HC

    entry = {"id": "telegram", "name": "Telegram", "description": "",
             "docs_url": "", "env_vars": (), "required_env": ()}
    monkeypatch.setattr(HC, "_platform_resolve",
                        lambda cfg, pid: ("catalog", dict(entry), {}))
    applied = []
    monkeypatch.setattr(HC, "_platform_store_update",
                        lambda cfg, pid, **kw: applied.append((pid, kw)))

    code, staged = HC.msg_update(cfg, {}, {"enabled": True},
                                 {"id": "telegram"})
    assert code == 409, staged
    assert staged.get("code") == "CONFIRM_REQUIRED", staged
    cid = staged.get("confirmation_id")
    assert cid, staged
    # nothing executed before confirmation
    assert applied == []

    # the pending-approvals banner settles with the id ONLY, via confirm_op
    out = OPS.confirm_op(cfg, cid, actor="dashboard:user",
                         session="dashboard")
    assert out["state"] == "executed", out
    assert applied and applied[0][0] == "telegram"
    assert applied[0][1].get("enabled") is True

    # settle-once: a replay cannot double-apply
    again = OPS.confirm_op(cfg, cid, actor="dashboard:user",
                           session="dashboard")
    assert again["state"] == "failed", again


def test_staged_channel_toggle_carries_reference_payload_only(tmp_path,
                                                              monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge import qubit_compat as HC

    entry = {"id": "telegram", "name": "Telegram", "description": "",
             "docs_url": "", "env_vars": ("TELEGRAM_BOT_TOKEN",),
             "required_env": ()}
    monkeypatch.setattr(HC, "_platform_resolve",
                        lambda cfg, pid: ("catalog", dict(entry), {}))
    code, staged = HC.msg_update(
        cfg, {}, {"enabled": True,
                  "env": {"TELEGRAM_BOT_TOKEN": "vault:tg"}},
        {"id": "telegram"})
    assert code == 409 and staged.get("confirmation_id"), staged
    # the staged payload is on the journal, never a raw value
    from voicebridge.dashboard_ops import _journal

    pending = [a for a in _journal(cfg).pending()
               if getattr(a, "capability", "") ==
               "qubit.messaging.platform_update"]
    assert len(pending) == 1, pending
    payload = (pending[0].meta or {}).get("payload") or {}
    assert payload.get("id") == "telegram"
    assert payload.get("env") == {"TELEGRAM_BOT_TOKEN": "vault:tg"}


# ---------------------------------------------------------------------------
# 2. CLI device enroll emits the canonical device.enroll audit receipt
# ---------------------------------------------------------------------------
def _state_cfg_path(tmp_path: Path) -> Path:
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(
        "logging:\n  jsonl_path: '" + str(state) + "'\n"
        "intent:\n  local_llm:\n    model: qubit-brain4\n")
    return p


def test_cli_device_enroll_emits_canonical_audit(tmp_path, monkeypatch,
                                                 capsys):
    from voicebridge import qubit_compat as HC
    from voicebridge.cli import main as cli

    calls = []
    real = HC.audit_event

    def _recorder(op, actor, detail):
        calls.append((op, actor, dict(detail)))
        return real(op, actor, detail)

    monkeypatch.setattr(HC, "audit_event", _recorder)

    cfg_path = _state_cfg_path(tmp_path)
    rc = cli(["device", "enroll", "--config", str(cfg_path)])
    assert rc == 0, capsys.readouterr().out
    audits = [c for c in calls if c[0] == "device.enroll"]
    assert audits, calls
    op, actor, detail = audits[0]
    assert actor == "cli:user"
    assert detail.get("via") == "cli"
    assert detail.get("machine_id")
    # enrollment itself still happened (explicit authorization semantics kept)
    assert (tmp_path / "state" / "device.json").exists()
